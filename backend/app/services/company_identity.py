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
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import List, Optional

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


def resolve_identity(company_name: str) -> Optional[CompanyIdentity]:
    """Look the company up and return the best-matching registered entity.

    Returns None when the registry has nothing close enough: a private company
    without an LEI is a normal case, and inventing a jurisdiction for it would
    route retrieval to the wrong country's sources.
    """
    name = company_name.strip()
    if not name:
        return None

    best: Optional[CompanyIdentity] = None
    best_score = 0.0

    for item in _fetch_candidates(name):
        entity = item.get("attributes", {}).get("entity", {})
        legal_name = (entity.get("legalName") or {}).get("name") or ""
        if not legal_name:
            continue

        score = _name_similarity(legal_name, name)
        status = entity.get("status")
        # An inactive shell must never win against an active entity of the same
        # name; a stale registration points at a defunct subsidiary.
        if (status or "").upper() != "ACTIVE":
            score -= 0.25

        if score > best_score:
            best_score = score
            best = CompanyIdentity(
                name=legal_name,
                jurisdiction=entity.get("jurisdiction"),
                legal_form=(entity.get("legalForm") or {}).get("id"),
                status=status,
                confident=score >= MIN_NAME_SIMILARITY,
            )

    if best is None:
        logger.info("Identity unresolved | company=%r | no registry match", name)
        return None

    logger.info(
        "Identity resolved | company=%r | legal_name=%r | jurisdiction=%s | "
        "status=%s | confident=%s",
        name,
        best.name,
        best.jurisdiction,
        best.status,
        best.confident,
    )
    return best
