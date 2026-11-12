# /backend/app/services/company_identity.py
"""Identify a company's jurisdiction and domicile before searching for it.

Tavily has no notion of where a company is registered, so a brief on an Indian
pharmaceutical group and one on a Danish drugmaker get the same publisher set
and the same queries. The Global Legal Entity Identifier registry does know:
it is free, needs no key, and returns the jurisdiction code and the legal form
for a registered entity.

The registry is a lookup, not an oracle: name matching is fuzzy and returns
affiliates and inactive shells. Candidates are therefore ranked by status and
name closeness, and a miss is reported honestly rather than guessed.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import List, Optional

from app.services.llm_client import build_client
from app.services.logger_service import get_logger

logger = get_logger("identity")

GLEIF_ENDPOINT = "https://api.gleif.org/api/v1/lei-records"
GLEIF_TIMEOUT_SECONDS = 15
GLEIF_HEADERS = {
    "User-Agent": "briefcortex-briefing/1.0",
    "Accept": "application/vnd.api+json",
}

# How close a registry name must be to the requested name to count as a match.
MIN_NAME_SIMILARITY = 0.72

# Legal-form suffixes that should not decide identity either way.
LEGAL_SUFFIXES = (
    "limited", "ltd", "inc", "incorporated", "corp", "corporation", "plc",
    "ag", "sa", "nv", "bv", "gmbh", "se", "spa", "s.p.a", "ab", "asa", "oy",
    "group", "holdings", "holding", "company", "co",
)


@dataclass(frozen=True)
class CompanyIdentity:
    """What the registry says about the entity we are briefing on."""

    name: str
    jurisdiction: Optional[str]
    legal_form: Optional[str]
    status: Optional[str]
    # True only when the match is strong enough to trust the jurisdiction.
    confident: bool

    @property
    def is_active(self) -> bool:
        return (self.status or "").upper() == "ACTIVE"


@dataclass(frozen=True)
class IdentityCandidate:
    """One registered entity that matched the requested name.

    A short name matches several companies: "AMD" is both the chipmaker and an
    unrelated French entity. Returning the candidates lets the caller pick
    instead of guessing, because a wrong jurisdiction sends the legal search to
    the wrong regulator.
    """

    name: str
    jurisdiction: Optional[str]
    legal_form: Optional[str]
    status: Optional[str]
    confidence: float

    @property
    def is_active(self) -> bool:
        return (self.status or "").upper() == "ACTIVE"


# How many candidates to surface. Beyond this the list stops being a choice.
MAX_CANDIDATES_RETURNED = 10

# Two candidates this close cannot be separated by score alone, so the caller
# is asked to choose rather than accepting whichever sorted first.
AMBIGUITY_MARGIN = 0.08

# A name this short can match unrelated entities exactly: "AMD" scores 1.0
# against both the chipmaker and an unrelated French company, so a perfect
# score on a short token proves nothing about which one was meant.
SHORT_NAME_CHARS = 6

# Any candidate at least this strong, when the wanted name is short, means the
# choice has to come from the caller.
STRONG_MATCH = 0.95

# Place names a caller may use to say where a company operates, mapped to ISO
# codes. Only used when the registry has no entry, and only for explicit names:
# a city or country the caller wrote, never an inference from the industry.
COUNTRY_HINTS = (
    ("san francisco", "US"),
    ("silicon valley", "US"),
    ("california", "US"),
    ("new york", "US"),
    ("united states", "US"),
    ("usa", "US"),
    ("london", "GB"),
    ("united kingdom", "GB"),
    ("berlin", "DE"),
    ("germany", "DE"),
    ("paris", "FR"),
    ("france", "FR"),
    ("amsterdam", "NL"),
    ("netherlands", "NL"),
    ("bangalore", "IN"),
    ("bengaluru", "IN"),
    ("mumbai", "IN"),
    ("india", "IN"),
    ("singapore", "SG"),
    ("tokyo", "JP"),
    ("japan", "JP"),
    ("shenzhen", "CN"),
    ("shanghai", "CN"),
    ("beijing", "CN"),
    ("china", "CN"),
)

DISAMBIGUATION_PROMPT = """You decide which registered entity a business user means.

You receive the company name as the caller wrote it, and the registered entities
whose names matched. Tickers and short names collide with unrelated companies:
"AMD" is the chipmaker, but a company literally named AMD is also registered.

Reply which candidate the caller means:
- If one candidate is clearly the well-known company behind that name, return its
  jurisdiction code.
- If the name is a ticker or abbreviation of a company that is NOT in the list,
  return "none".
- If you genuinely cannot tell, return "none" rather than guessing.

Reply with JSON only:
{"jurisdiction": "US|FR|DK|...|none", "reason": "one short sentence"}"""


def needs_caller_choice(
    company_name: str,
    candidates: List["IdentityCandidate"],
) -> bool:
    """True when the registry cannot settle which entity the caller meant.

    Two independent reasons:
    - the top two candidates are too close to separate by score;
    - the name is short enough (a ticker like "AMD") that a lone perfect match
      proves nothing. A three-letter token matches one registered company
      exactly while the entity the caller means is a different, better-known one
      that the registry spells differently.
    """
    if not candidates:
        return False

    if len(candidates) >= 2 and (
        candidates[0].confidence - candidates[1].confidence
    ) < AMBIGUITY_MARGIN:
        return True

    if len(_normalize_name(company_name).replace(" ", "")) <= SHORT_NAME_CHARS:
        strong = [c for c in candidates if c.confidence >= STRONG_MATCH]
        if len(strong) >= 2:
            return True
        # A short name with a single strong match is still uncertain: the ticker
        # "AMD" is registered exactly once, against an unrelated entity, while
        # the company the caller means is not in the registry under that spelling.
        return bool(strong)

    return False


async def disambiguate_with_model(
    company_name: str,
    candidates: List["IdentityCandidate"],
) -> Optional[str]:
    """Ask the model which candidate is the company the caller means.

    Used when the registry itself cannot decide. Returns the chosen
    jurisdiction, "none" when none of the candidates is right, or None when the
    model cannot be reached and the caller must decide.
    """
    if not candidates:
        return None

    listing = "\n".join(
        f"[{index}] {candidate.name} — jurisdiction {candidate.jurisdiction or '?'}, "
        f"status {candidate.status or '?'}"
        for index, candidate in enumerate(candidates)
    )
    try:
        client, model = build_client()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": DISAMBIGUATION_PROMPT},
                {
                    "role": "user",
                    "content": f"COMPANY AS THE CALLER WROTE IT: {company_name}\n\nCANDIDATES:\n{listing}",
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        content = response.choices[0].message.content or ""
        verdict = json.loads(content)
    except Exception as exc:
        logger.warning("Identity disambiguation failed | company=%r | %s", company_name, exc)
        return None

    chosen = verdict.get("jurisdiction")
    if not isinstance(chosen, str):
        return None
    if chosen.strip().lower() in ("none", "", "unknown"):
        return "none"
    return chosen.strip().upper()



def _normalize_name(value: str) -> str:
    """Lowercase and drop legal-form suffixes so names compare on substance."""
    text = "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in value.lower())
    tokens = [token for token in text.split() if token not in LEGAL_SUFFIXES]
    return " ".join(tokens)


def _name_similarity(candidate: str, wanted: str) -> float:
    """Score how likely the candidate is the entity the caller named.

    A candidate that merely starts with the wanted name is not a match:
    "Unicredit Futuro 2031" and "UNICREDIT SPA" both contain "unicredit", but
    only one is the bank. Extra words therefore cost score in proportion to how
    much longer the candidate is, so the shortest close match wins.
    """
    left = _normalize_name(candidate)
    right = _normalize_name(wanted)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0

    if right in left or left in right:
        shared = min(len(left), len(right))
        longer = max(len(left), len(right))
        # A short wanted name inside a long candidate scores lower than a near
        # exact match: a 25-character suffix is a different entity.
        return 0.95 * (shared / longer) ** 0.5

    return SequenceMatcher(None, left, right).ratio()


def _fetch_candidates(company_name: str) -> List[dict]:
    params = urllib.parse.urlencode(
        {"filter[entity.legalName]": company_name, "page[size]": 10}
    )
    request = urllib.request.Request(
        f"{GLEIF_ENDPOINT}?{params}", headers=GLEIF_HEADERS
    )
    try:
        with urllib.request.urlopen(request, timeout=GLEIF_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        logger.warning("GLEIF lookup rejected | company=%r | status=%s", company_name, exc.code)
        return []
    except Exception as exc:
        logger.warning("GLEIF lookup failed | company=%r | %s", company_name, exc)
        return []

    return payload.get("data", []) if isinstance(payload, dict) else []


def _candidate_from_item(item: dict, name: str) -> Optional[IdentityCandidate]:
    entity = item.get("attributes", {}).get("entity", {})
    legal_name = (entity.get("legalName") or {}).get("name") or ""
    if not legal_name:
        return None

    score = _name_similarity(legal_name, name)
    status = entity.get("status")
    # An inactive shell must never outrank an active entity of the same name.
    if (status or "").upper() != "ACTIVE":
        score -= 0.25

    return IdentityCandidate(
        name=legal_name,
        jurisdiction=entity.get("jurisdiction"),
        legal_form=(entity.get("legalForm") or {}).get("id"),
        status=status,
        confidence=round(max(score, 0.0), 3),
    )


def _rank(items: List[dict], name: str) -> List[IdentityCandidate]:
    ranked = [c for c in (_candidate_from_item(item, name) for item in items) if c]
    ranked.sort(key=lambda candidate: candidate.confidence, reverse=True)
    return ranked


def rank_candidates(company_name: str) -> List[IdentityCandidate]:
    """Return the registered entities matching this name, best first.

    The registry filter matches the words it is given, so a legal name that
    carries an extra word ("THE TORO COMPANY" for the caller's "Toro") may not
    appear in the first page at all: measured, "Toro" returned three French
    shells and a shipping company, and the model was then asked to choose among
    entities that did not include the company the caller meant. When the name is
    ambiguous, the common legal-name variants are queried too and merged before
    the model is asked, so the choice includes the real candidate.
    """
    name = company_name.strip()
    if not name:
        return []

    ranked = _rank(_fetch_candidates(name), name)
    if ranked and not needs_caller_choice(name, ranked):
        return ranked[:MAX_CANDIDATES_RETURNED]

    seen = {(candidate.name, candidate.jurisdiction) for candidate in ranked}
    for variant in (f"{name} Company", f"{name} Inc"):
        for item in _fetch_candidates(variant):
            candidate = _candidate_from_item(item, name)
            if candidate is None:
                continue
            key = (candidate.name, candidate.jurisdiction)
            if key in seen:
                continue
            seen.add(key)
            ranked.append(candidate)

    ranked.sort(key=lambda candidate: candidate.confidence, reverse=True)
    return ranked[:MAX_CANDIDATES_RETURNED]


def is_ambiguous(candidates: List[IdentityCandidate]) -> bool:
    """True when the top two candidates are too close to choose between.

    A clear winner is acted on; a near tie is surfaced, because picking one
    silently would aim the jurisdiction-specific searches at the wrong country.
    """
    if len(candidates) < 2:
        return False
    return (candidates[0].confidence - candidates[1].confidence) < AMBIGUITY_MARGIN


# Host suffixes that identify where an organisation is based. A company's own
# domain is the most reliable signal for a private firm with no registry entry:
# ykk.co.jp is a Japanese company, and its US litigation history is not a reason
# to read it through an American press list.
HOST_COUNTRY_SUFFIXES = (
    (".co.jp", "JP"),
    (".jp", "JP"),
    (".co.kr", "KR"),
    (".kr", "KR"),
    (".com.cn", "CN"),
    (".cn", "CN"),
    (".com.hk", "HK"),
    (".hk", "HK"),
    (".com.sg", "SG"),
    (".sg", "SG"),
    (".com.my", "MY"),
    (".my", "MY"),
    (".co.in", "IN"),
    (".in", "IN"),
    (".com.tw", "TW"),
    (".tw", "TW"),
    (".co.th", "TH"),
    (".th", "TH"),
    (".com.vn", "VN"),
    (".vn", "VN"),
    (".co.id", "ID"),
    (".id", "ID"),
    (".com.au", "AU"),
    (".au", "AU"),
    (".co.nz", "NZ"),
    (".co.uk", "GB"),
    (".uk", "GB"),
    (".de", "DE"),
    (".fr", "FR"),
    (".nl", "NL"),
    (".be", "BE"),
    (".it", "IT"),
    (".es", "ES"),
    (".se", "SE"),
    (".dk", "DK"),
    (".fi", "FI"),
    (".no", "NO"),
    (".ch", "CH"),
    (".at", "AT"),
    (".ie", "IE"),
    (".pt", "PT"),
    (".pl", "PL"),
    (".cz", "CZ"),
    (".gr", "GR"),
    (".tr", "TR"),
    (".ru", "RU"),
    (".br", "BR"),
    (".mx", "MX"),
    (".ca", "CA"),
)


def jurisdiction_from_url(url: str) -> Optional[str]:
    """Return the country a company's own web host implies, or None.

    A company's domain is its own statement of where it is based, which matters
    for a private firm that has no registry record: ykk.co.jp is Japanese even
    though the only English-language coverage about it is American litigation.

    A generic `.com` implies nothing and yields None. Guessing from it would aim
    retrieval at the wrong country's press, which is worse than not aiming it.
    """
    host = (url or "").strip().lower()
    if "//" in host:
        host = host.split("//", 1)[1]
    host = host.split("/")[0].split("?")[0].split(":")[0]

    for suffix, code in HOST_COUNTRY_SUFFIXES:
        if host.endswith(suffix):
            return code
    return None


def jurisdiction_from_description(description: str) -> Optional[str]:
    """Return a country code named in the caller's description, if any.

    A private startup has no registry entry, so the caller's own words are the
    only source for where it operates: "San Francisco AI assistant startup"
    means US. Only explicit place names are read; nothing is inferred from the
    industry, because aiming legal searches at a guessed country returns another
    country's news.

    Matching is on whole words, longest first. A plain substring test reads
    "Chinatown" as China and would aim the legal search at the wrong regulator.
    """
    text = (description or "").lower()
    if not text:
        return None

    # Longest phrase first, so "united states" wins over a shorter contained
    # hint and a multi-word place name is never split.
    for name, code in sorted(COUNTRY_HINTS, key=lambda pair: -len(pair[0])):
        pattern = rf"(?<![a-z0-9]){re.escape(name)}(?![a-z0-9])"
        if re.search(pattern, text):
            return code
    return None


def resolve_identity(
    company_name: str,
    *,
    preferred_jurisdiction: Optional[str] = None,
    allow_fallback: bool = True,
) -> Optional[CompanyIdentity]:
    """Look the company up and return the best-matching registered entity.

    Returns None when the registry has nothing close enough: a private company
    without an LEI is a normal case, and inventing a jurisdiction for it would
    route retrieval to the wrong country's sources.

    ``preferred_jurisdiction`` pins the answer to a country the caller chose, so
    a name shared by several entities resolves to the intended one.

    ``allow_fallback=False`` is what the caller's "none of these" answer means:
    a name is registered somewhere, but not for the company being briefed. The
    registry match is then withheld entirely rather than falling back to an
    unrelated entity that happens to share the name. That fallback was a live
    defect: a model verdict of "none" for a San Francisco startup still produced
    an Indian entity and aimed the legal searches at Indian regulators.
    """
    name = company_name.strip()
    if not name:
        return None

    candidates = rank_candidates(name)
    if not candidates:
        logger.info("Identity unresolved | company=%r | no registry match", name)
        return None

    chosen = candidates[0]
    if preferred_jurisdiction:
        wanted = preferred_jurisdiction.upper()
        matching = [
            candidate
            for candidate in candidates
            if (candidate.jurisdiction or "").upper() == wanted
        ]
        if matching:
            chosen = matching[0]
        else:
            # The caller named a country and no candidate is registered there.
            # Falling through to candidates[0] would answer with a different
            # country while looking like a confirmation, which is how a caller's
            # "US" still produced an Indian entity and Indian regulators.
            logger.info(
                "Identity withheld | company=%r | no candidate in %s (have %s)",
                name,
                wanted,
                sorted({(c.jurisdiction or "?") for c in candidates}),
            )
            return None

    if not allow_fallback:
        # The caller rejected this registry entity. A perfect name match is not
        # a counter-argument: the whole reason they were asked is that several
        # unrelated businesses share the name, so identity is withheld outright
        # and the description becomes the only source of jurisdiction.
        logger.info(
            "Identity withheld | company=%r | caller rejected all %s candidates "
            "(top match was %r in %s)",
            name,
            len(candidates),
            chosen.name,
            chosen.jurisdiction or "?",
        )
        return None

    identity = CompanyIdentity(
        name=chosen.name,
        jurisdiction=chosen.jurisdiction,
        legal_form=chosen.legal_form,
        status=chosen.status,
        confident=chosen.confidence >= MIN_NAME_SIMILARITY,
    )

    logger.info(
        "Identity resolved | company=%r | legal_name=%r | jurisdiction=%s | "
        "status=%s | confidence=%s | confident=%s | candidates=%s",
        name,
        identity.name,
        identity.jurisdiction,
        identity.status,
        chosen.confidence,
        identity.confident,
        len(candidates),
    )
    return identity
