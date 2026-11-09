# /backend/app/services/media_trust.py
"""Which publishers count as press for a given country, learned once and stored.

A company's coverage lives where the company lives. YKK is Japanese, and the
international wire list returns a decade of US litigation filings about it while
missing Nikkei entirely.

Hardcoding a list per country does not scale: there are ~200 jurisdictions, the
titles change, and nobody on this project can vouch for the business desk of a
newspaper in a market they have never read. So the list is **generated**: the
model is asked which outlets are the established press for a country's business
community, the answer is validated, and it is stored on disk. The next brief on a
company in that country reads the stored list instead of paying for it again.

Two fixed rules survive generation, because they are not matters of local
knowledge:

1. **Some hosts are never press, anywhere.** Forums, social platforms,
   user-submitted aggregators and content farms are refused before anything else
   runs. Rumour is a claim about a claim, not a source.
2. **A generated list is validated, not trusted.** An answer naming a social
   platform, a job board or a host that is not plausible as a news domain is
   discarded rather than stored. A wrong list silently narrows every future brief
   in that country, so the failure mode has to be "store nothing", never "store
   something wrong".
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from app.services.logger_service import get_logger

logger = get_logger("media_trust")

# Where learned per-country lists are kept. Machine-local, not source: the list
# is generated per deployment and is not part of the product's identity.
STORE_PATH = Path(__file__).resolve().parents[2] / "data" / "media_trust.json"

# =============================================================================
# 1. Never press, anywhere
# =============================================================================

# Matched as host suffixes, so "reddit.com" refuses "old.reddit.com".
REFUSED_HOSTS: Tuple[str, ...] = (
    # forums and message boards
    "reddit.com", "quora.com", "stocktwits.com", "investorshub.advfn.com",
    "advfn.com", "boards.fool.com",
    # social platforms
    "facebook.com", "fb.com", "x.com", "twitter.com", "instagram.com",
    "tiktok.com", "youtube.com", "youtu.be", "linkedin.com", "pinterest.com",
    "telegram.me", "t.me", "discord.com", "threads.net", "mastodon.social",
    # user-generated and scraped aggregators
    "wikipedia.org", "wikidata.org", "fandom.com", "medium.com", "substack.com",
    "blogspot.com", "wordpress.com", "tumblr.com", "sites.google.com",
    "docs.google.com", "slideshare.net", "scribd.com", "issuu.com",
    "crunchbase.com", "glassdoor.com", "indeed.com", "ziprecruiter.com",
    "builtin.com", "zippia.com", "simplyhired.com", "careerbuilder.com",
    "monster.com", "rocketreach.co", "zoominfo.com", "apollo.io",
    "signalhire.com", "leadiq.com", "pitchbook.com", "tracxn.com",
    "similarweb.com", "semrush.com",
    # content farms and SEO aggregators
    "newsbreak.com", "flipboard.com", "newsnow.co.uk", "inkl.com", "msn.com",
    "barchart.com", "wallstreetzen.com", "simplywall.st", "marketbeat.com",
    "gurufocus.com", "moomoo.com", "zacks.com", "marketscreener.com",
    "valuengine.com", "stockanalysis.com", "tipranks.com", "benzinga.com",
    "insidermonkey.com", "24wallst.com", "nai500.com", "biggo.com",
    "whatsmode.com",
    # Job-board aggregators that are not the company's own careers page. A
    # Canadian run filed seven roles from one of these into the hard-fact lane,
    # because the page is prose-shaped and not on this list.
    #
    # Applicant-tracking systems are **not** on this list even though they read
    # like job boards. `canadagoose.wd3.myworkdayjobs.com` is Canada Goose's own
    # careers site — the company's openings, published by the company, reached
    # through a vendor's front end. Refusing it deleted the hiring lane for every
    # employer that uses Workday, which is most large ones: measured, a brief for
    # Canada Goose reported hiring=0 while its own board listed 96 roles. The
    # vendor host is read as a careers page (`is_careers_url`) and its listings
    # come from the vendor's JSON endpoint, never from reporter prose.
    "eluta.ca", "jobzmall.com", "talent.com", "neuvoo.com", "jooble.org",
    "workopolis.com", "careerjet.ca", "ca.indeed.com", "jobs.ca",
    # Paid company-profile databases: the visible page is a sales pitch, and
    # "Gain a 360-degree view … Learn more" was reaching the fact base.
    "globaldata.com", "dnb.com", "bisnode.com", "moodys.com",
    "refinitiv.com", "factset.com",
    # Wildlife and species pages. The name collides: a run for the outerwear
    # brand returned federal and state "Canada goose" management programmes.
    # These are refused by host so the collision never becomes evidence.
    "fws.gov", "ncwildlife.gov", "dec.ny.gov", "portal.ct.gov",
    "birdsoftheworld.org", "allaboutbirds.org", "audubon.org",
    "ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov",
    # Law-firm blogs and class-action solicitation pages. They restate a filing
    # this pipeline already reads directly, and they exist to collect claimants.
    "topclassactions.com", "edelson-law.com", "ahdootwolfson.com",
    "stollberne.com", "zlk.com",
)

# Filings, newswires and regulators: the company's own words. Trusted everywhere
# because they are not journalism, so a jurisdiction cannot change their tier.
PRIMARY_HOSTS: Tuple[str, ...] = (
    "sec.gov", "prnewswire.com", "globenewswire.com", "businesswire.com",
)

# International wires and business press. They report a large company well
# anywhere; they barely report a mid-sized one outside its own country.
GLOBAL_HOSTS: Tuple[str, ...] = (
    "reuters.com", "bloomberg.com", "wsj.com", "ft.com", "cnbc.com",
    "businessinsider.com", "markets.businessinsider.com", "economist.com",
    "apnews.com", "afp.com", "forbes.com",
)

# Ordered trust tiers. Lower index = read first.
TIER_PRIMARY = "primary"
TIER_NATIONAL = "national"
TIER_GLOBAL = "global"
TIER_FALLBACK = "fallback"
TIER_ORDER: Tuple[str, ...] = (TIER_PRIMARY, TIER_NATIONAL, TIER_GLOBAL, TIER_FALLBACK)

# =============================================================================
# Host helpers
# =============================================================================

def host_of_result(result: dict) -> str:
    """Return the bare host of a search result, lowercased."""
    return normalize_host(str(result.get("url") or ""))


def normalize_host(url_or_host: str) -> str:
    """Reduce a URL or host to a bare lowercase host."""
    text = (url_or_host or "").strip().lower()
    if "//" in text:
        text = text.split("//", 1)[1]
    host = text.split("/")[0].split("?")[0].split("#")[0]
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    host = host.split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def _matches(host: str, known: str) -> bool:
    known = known.lower()
    return host == known or host.endswith("." + known)


def is_refused_host(url_or_host: str) -> bool:
    """True when this host is never treated as press."""
    host = normalize_host(url_or_host)
    return any(_matches(host, refused) for refused in REFUSED_HOSTS)


# Column shapes that cover several companies in one page. A "small caps to watch"
# roundup reports a dozen firms' figures in sequence; every sentence is true and
# none of them can be attributed to the company being briefed without reading
# which paragraph belongs to whom. Measured: one such page contributed eight
# facts to a Canada Goose brief, seven of them about other companies (MDA Space's
# revenue, Savaria's price move, a table of six unrelated tickers), and the
# opening paragraphs — which is what the subject classifier reads — were about
# the index rather than about anyone in it.
#
# The page is not press misbehaving; it is a format whose numbers have no single
# owner. Refusing it at the URL is the only cheap way to keep them out.
ROUNDUP_PATH_MARKERS: Tuple[str, ...] = (
    "inside-the-market",
    "small-caps-to-watch",
    "stocks-to-watch",
    "market-movers",
    "what-to-watch",
    "morning-movers",
    "midday-movers",
    "closing-bell",
    "live-updates",
    "markets-wrap",
    "roundup",
)


def is_roundup_url(url_or_host: str) -> bool:
    """True when the URL is a multi-company column rather than one story."""
    text = (url_or_host or "").strip().lower()
    path = text.split("?", 1)[0]
    return any(marker in path for marker in ROUNDUP_PATH_MARKERS)


PUBLIC_SUFFIXES = (
    ".com", ".net", ".org", ".co", ".jp", ".de", ".fr", ".uk", ".cn", ".in",
    ".hk", ".sg", ".my", ".id", ".kr", ".tw", ".th", ".vn", ".au", ".nz",
    ".ca", ".br", ".mx", ".ru", ".it", ".es", ".nl", ".se", ".dk", ".fi",
    ".no", ".ch", ".at", ".be", ".ie", ".pt", ".pl", ".cz", ".gr", ".tr",
)


def looks_like_news_domain(host: str) -> bool:
    """True when a host is plausible as a publication rather than a platform.

    This is the validation that stops a generated list from storing nonsense: the
    model may know a country's press, but it can also return a social handle, a
    job board or a made-up string, and a wrong entry narrows every future brief
    in that country.
    """
    if not host or len(host) < 4:
        return False
    if is_refused_host(host):
        return False
    if not any(host.endswith(suffix) for suffix in PUBLIC_SUFFIXES):
        return False
    # A publication has a name before its suffix.
    local = host.rsplit(".", 1)[0]
    if not local or len(local) < 2:
        return False
    # Reject obvious placeholders and personal pages.
    if re.fullmatch(r"(www|blog|news\d+|user\d*)", local):
        return False
    if "/" in host or " " in host:
        return False
    # A local edition of an international wire is not that country's own press,
    # and listing it as national spends a national slot on a title the reader
    # already gets from the global tier.
    for international in GLOBAL_HOSTS:
        brand = international.rsplit(".", 1)[0]
        if local == brand or local.startswith(brand + "."):
            return False
    return True


# =============================================================================
# Learned per-country lists
# =============================================================================

JURISDICTION_COUNTRY: Dict[str, str] = {
    "JP": "Japan", "CN": "China", "HK": "Hong Kong", "SG": "Singapore",
    "MY": "Malaysia", "VN": "Vietnam", "ID": "Indonesia", "KR": "South Korea",
    "TW": "Taiwan", "TH": "Thailand", "IN": "India", "AU": "Australia",
    "NZ": "New Zealand", "PH": "Philippines",
    "DE": "Germany", "FR": "France", "NL": "the Netherlands", "BE": "Belgium",
    "IT": "Italy", "ES": "Spain", "SE": "Sweden", "DK": "Denmark",
    "FI": "Finland", "NO": "Norway", "CH": "Switzerland", "AT": "Austria",
    "IE": "Ireland", "PT": "Portugal", "PL": "Poland", "CZ": "Czechia",
    "GR": "Greece", "TR": "Turkey", "GB": "the United Kingdom",
    "US": "the United States", "CA": "Canada", "MX": "Mexico",
    "BR": "Brazil", "AR": "Argentina", "CL": "Chile",
    "AE": "the United Arab Emirates", "SA": "Saudi Arabia",
    "IL": "Israel", "ZA": "South Africa", "EG": "Egypt", "NG": "Nigeria",
}


MEDIA_LIST_PROMPT = """You name the established press of one country, and the language
those outlets publish in.

You receive a country. Return:
1. The news outlets a professional reader in that country's business community
   would consider serious reporting on companies, in descending order of
   authority. Only real, currently-published news organisations: no social
   platforms, no forums, no job boards, no company directories, no price-aggregator
   pages. Business dailies and the business desks of national newspapers first.
   Domains only, bare host, no scheme and no path: "nikkei.com".
2. The language those outlets actually publish in, as an ISO 639-1 code
   ("ja", "de", "fr", "zh", "en", ...). This is the language of the business
   press, not the country's every language.
3. Between 6 and 12 topic terms in that language, for company news: the words a
   local journalist would use for earnings, guidance, pricing, contract,
   capacity, layoffs, investment, regulation. These become search terms, so they
   must be the words that appear in local coverage, not translations of English
   phrasing.

If you do not know the country's press well, return FEWER entries and say so in
the note. A short accurate list beats a long guessed one.

Reply with JSON only:
{"country": str, "domains": [str], "language": str, "topic_terms": [str],
 "confidence": "high|medium|low", "note": "one short sentence"}"""


@dataclass
class MediaPolicy:
    """The trust order that applies to one company."""

    jurisdiction: Optional[str]
    national: Tuple[str, ...] = ()
    jurisdiction_known: bool = False
    # How the list was obtained: "stored", "generated", or "none".
    source: str = "none"
    confidence: str = "low"
    note: str = ""
    # The language those national outlets publish in, and topic terms in that
    # language. An English query on Japanese domains mostly returns the English
    # edition of one title; a query in the local language reaches the local desks.
    language: str = ""
    topic_terms: Tuple[str, ...] = ()

    def tier_of(self, url_or_host: str) -> str:
        """Return the trust tier for one host."""
        host = normalize_host(url_or_host)
        if is_refused_host(host):
            return "refused"

        for known in PRIMARY_HOSTS:
            if _matches(host, known):
                return TIER_PRIMARY

        if self.jurisdiction_known:
            for known in self.national:
                if _matches(host, known):
                    return TIER_NATIONAL

        for known in GLOBAL_HOSTS:
            if _matches(host, known):
                return TIER_GLOBAL

        return TIER_FALLBACK

    def rank(self, url_or_host: str) -> int:
        """Sort key: lower is more trusted. Refused hosts sort last."""
        tier = self.tier_of(url_or_host)
        if tier == "refused":
            return len(TIER_ORDER) + 1
        return TIER_ORDER.index(tier)

    @property
    def description(self) -> str:
        if not self.jurisdiction_known:
            return (
                "jurisdiction unknown or no press list available, so national "
                "press could not be ranked; international wires rank first and "
                "every other publication is read as unranked"
            )
        return (
            f"jurisdiction {self.jurisdiction}: {len(self.national)} national "
            f"titles ({self.source}, {self.confidence} confidence) rank above "
            f"international wires"
        )


class MediaListStore:
    """Country -> learned press list, persisted as JSON on disk."""

    def __init__(self, path: Path = STORE_PATH) -> None:
        self.path = path
        self._data: Dict[str, dict] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            if self.path.exists():
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    self._data = payload
        except Exception as exc:
            logger.warning("Media list store unreadable | path=%s | %s", self.path, exc)
            self._data = {}

    def get(self, jurisdiction: str) -> Optional[dict]:
        self._load()
        return self._data.get((jurisdiction or "").upper())

    def put(self, jurisdiction: str, entry: dict) -> None:
        self._load()
        self._data[(jurisdiction or "").upper()] = entry
        self._save()

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning("Media list store unwritable | path=%s | %s", self.path, exc)

    def jurisdictions(self) -> List[str]:
        self._load()
        return sorted(self._data.keys())


_STORE = MediaListStore()


def list_store() -> MediaListStore:
    """Return the process-wide store."""
    return _STORE


def believe_domains(raw: Iterable[str]) -> Tuple[str, ...]:
    """Validate a candidate list, keeping only plausible publications."""
    kept: List[str] = []
    rejected: List[str] = []

    for item in raw:
        host = normalize_host(str(item or ""))
        if not looks_like_news_domain(host):
            rejected.append(host or "(empty)")
            continue
        if host in kept:
            continue
        kept.append(host)

    if rejected:
        logger.info(
            "Media list entries rejected | count=%s | hosts=%s",
            len(rejected),
            rejected[:8],
        )
    return tuple(kept)


async def generate_media_list(jurisdiction: str) -> MediaPolicy:
    """Ask the model which outlets are press in this country, then store it.

    A jurisdiction with no country mapping yields no list rather than a guess:
    ranking the wrong country's press first narrows every brief on that company
    without anyone noticing.
    """
    code = (jurisdiction or "").strip().upper().split("-")[0]
    country = JURISDICTION_COUNTRY.get(code)

    if not country:
        logger.info("Media list skipped | jurisdiction=%r has no country mapping", code)
        return MediaPolicy(
            jurisdiction=code or None,
            jurisdiction_known=False,
            source="none",
            note=f"no country mapping for {code or 'unknown'}",
        )

    from app.services.llm_client import build_client

    try:
        client, model = build_client()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": MEDIA_LIST_PROMPT},
                {"role": "user", "content": f"COUNTRY: {country}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        content = response.choices[0].message.content or ""
        payload = json.loads(content)
    except Exception as exc:
        logger.warning("Media list generation failed | jurisdiction=%s | %s", code, exc)
        return MediaPolicy(
            jurisdiction=code,
            jurisdiction_known=False,
            source="none",
            note=f"generation failed: {type(exc).__name__}",
        )

    raw_domains = payload.get("domains") or []
    if not isinstance(raw_domains, (list, tuple)):
        raw_domains = []

    domains = believe_domains(raw_domains)
    confidence = str(payload.get("confidence") or "low").lower()
    note = str(payload.get("note") or "")[:200]

    # The language the local business press publishes in, and topic terms in that
    # language. An English query on Japanese domains mostly returns the English
    # edition of one title; a query in the local language reaches the local desks.
    language = str(payload.get("language") or "").strip().lower()
    if not re.fullmatch(r"[a-z]{2,3}", language):
        language = ""
    raw_terms = payload.get("topic_terms") or []
    topic_terms = tuple(
        term.strip()
        for term in (raw_terms if isinstance(raw_terms, (list, tuple)) else [])
        if isinstance(term, str) and term.strip() and len(term.strip()) <= 30
    )[:12]

    if not domains:
        logger.info("Media list empty after validation | jurisdiction=%s", code)
        return MediaPolicy(
            jurisdiction=code,
            jurisdiction_known=False,
            source="none",
            note="generated list had no plausible publication",
        )

    entry = {
        "country": country,
        "domains": list(domains),
        "language": language,
        "topic_terms": list(topic_terms),
        "confidence": confidence,
        "note": note,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    _STORE.put(code, entry)

    logger.info(
        "Media list generated and stored | jurisdiction=%s | country=%s | "
        "domains=%s | language=%s | terms=%s | confidence=%s",
        code, country, len(domains), language or "?", len(topic_terms), confidence,
    )
    return MediaPolicy(
        jurisdiction=code,
        national=domains,
        jurisdiction_known=True,
        source="generated",
        confidence=confidence,
        note=note,
        language=language,
        topic_terms=topic_terms,
    )


COUNTRY_PROMPT = """You decide which country a company is based in.

You receive a company name. Answer with the ISO 3166-1 alpha-2 country code of
the country where its headquarters and primary operations sit.

Rules:
- Answer from the company's own identity: where it is registered and run, not
  where its news happens to be reported.
- If the company is a subsidiary or a local arm of a larger group, answer the
  country of the group that the name refers to.
- If you genuinely do not know the company, answer "unknown". A wrong country
  aims every later search at the wrong press, which is worse than not aiming.

Reply with JSON only:
{"country": "JP|US|DE|...|unknown", "reason": "one short sentence"}"""


async def infer_jurisdiction(company: str) -> Optional[str]:
    """Ask the model which country a company is based in.

    Used only when the registry could not name a legal entity and the company's
    own domain does not carry a country suffix. A private firm like YKK has a
    `.com` address, so the domain says nothing; the model's own knowledge of where
    the company sits is the next cheapest source, and it is cheaper than a search.

    An unusable answer returns None: a guessed country aims every later search at
    the wrong press.
    """
    name = (company or "").strip()
    if not name:
        return None

    from app.services.llm_client import build_client

    try:
        client, model = build_client()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": COUNTRY_PROMPT},
                {"role": "user", "content": f"COMPANY: {name}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        payload = json.loads(response.choices[0].message.content or "")
    except Exception as exc:
        logger.warning("Country inference failed | company=%r | %s", name, exc)
        return None

    code = str(payload.get("country") or "").strip().upper()
    if not code or code == "UNKNOWN" or len(code) != 2:
        logger.info("Country inference returned nothing usable | company=%r", name)
        return None

    logger.info(
        "Country inferred | company=%r | country=%s | %s",
        name,
        code,
        str(payload.get("reason") or "")[:120],
    )
    return code


LOCAL_QUERY_PROMPT = """You write search queries in the local language for one company.

You receive a company name, its country, the language its business press publishes
in, and a list of topic terms in that language. Write queries that a local
journalist's own words would match.

Rules:
- Write in {language_name}. Do not translate English phrasing: use the words that
  appear in local coverage of companies.
- Each query combines the company name (as it is written locally, when you know
  it) with one or two topic terms.
- Between 2 and {max_queries} queries. Short, keyword-style, not sentences.
- Cover the topics that reveal business position: results, guidance, pricing,
  contract terms, capacity, layoffs, investment, regulation.
- If you do not know how the company is written locally, keep the name as given.

Reply with JSON only:
{{"local_name": str, "queries": [str]}}"""

LANGUAGE_NAMES = {
    "ja": "Japanese", "zh": "Chinese", "ko": "Korean", "de": "German",
    "fr": "French", "es": "Spanish", "it": "Italian", "pt": "Portuguese",
    "nl": "Dutch", "sv": "Swedish", "da": "Danish", "fi": "Finnish",
    "no": "Norwegian", "pl": "Polish", "cs": "Czech", "el": "Greek",
    "tr": "Turkish", "ru": "Russian", "ar": "Arabic", "he": "Hebrew",
    "th": "Thai", "vi": "Vietnamese", "id": "Indonesian", "ms": "Malay",
    "hi": "Hindi", "en": "English",
}


def _language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code or "the local language")


async def generate_local_queries(
    company: str,
    policy: MediaPolicy,
    max_queries: int = 3,
) -> List[str]:
    """Write search queries in the language the local business press publishes in.

    Measured: an English query restricted to Japanese domains returned the English
    edition of one title and little else, while a query in Japanese against the
    same domains returned nine results with usable bodies, including the national
    wire. The local language is not a nicety — it is what reaches the desks that
    actually report the company.

    Returns an empty list when the policy carries no language: an untranslated
    query is better than a bad translation, and the English plan still runs.
    """
    language = (policy.language or "").strip().lower()
    if not language or language == "en":
        return []

    from app.services.llm_client import build_client

    terms = ", ".join(policy.topic_terms[:10])
    user_message = (
        f"COMPANY: {company}\n"
        f"COUNTRY: {policy.jurisdiction or '?'}\n"
        f"LANGUAGE: {language} ({_language_name(language)})\n"
        f"TOPIC TERMS IN THAT LANGUAGE: {terms or '(none supplied)'}\n\n"
        "Write the queries."
    )

    try:
        client, model = build_client()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": LOCAL_QUERY_PROMPT.format(
                        language_name=_language_name(language),
                        max_queries=max_queries,
                    ),
                },
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        payload = json.loads(response.choices[0].message.content or "")
    except Exception as exc:
        logger.warning(
            "Local query generation failed | company=%r | language=%s | %s",
            company, language, exc,
        )
        return []

    if not isinstance(payload, dict):
        logger.info(
            "Local query reply was not an object | company=%r | type=%s",
            company, type(payload).__name__,
        )
        return []

    if not isinstance(payload, dict):
        return []

    raw_queries = payload.get("queries") or []
    queries: List[str] = []
    for item in raw_queries if isinstance(raw_queries, (list, tuple)) else []:
        text = str(item or "").strip()
        if not text or len(text) > 160:
            continue
        if text not in queries:
            queries.append(text)
        if len(queries) >= max_queries:
            break

    logger.info(
        "Local queries generated | company=%r | language=%s | local_name=%r | "
        "queries=%s",
        company,
        language,
        str(payload.get("local_name") or "")[:60],
        queries,
    )
    return queries


LANGUAGE_ONLY_PROMPT = """You name the language one country's business press
publishes in, and the topic terms a local journalist would use.

You receive a country and the outlets already validated as its national press.
Return:
1. The language those outlets publish in, as an ISO 639-1 code ("ja", "de", "fr",
   "zh", "en", ...). This is the language of the business press.
2. Between 6 and 12 topic terms in that language, for company news: the words a
   local journalist writes for earnings, guidance, pricing, contract, capacity,
   layoffs, investment, regulation. They become search terms, so they must be the
   words that appear in local coverage.

Reply with JSON only:
{"language": str, "topic_terms": [str]}"""


async def _enrich_language(
    code: str,
    stored: dict,
    domains: Tuple[str, ...],
) -> Optional[MediaPolicy]:
    """Add the language and local topic terms to an already-stored press list."""
    country = stored.get("country") or JURISDICTION_COUNTRY.get(code) or code

    from app.services.llm_client import build_client

    try:
        client, model = build_client()
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": LANGUAGE_ONLY_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"COUNTRY: {country}\n"
                        f"NATIONAL PRESS: {', '.join(domains[:10])}"
                    ),
                },
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        payload = json.loads(response.choices[0].message.content or "")
    except Exception as exc:
        logger.warning("Language enrichment failed | jurisdiction=%s | %s", code, exc)
        return None

    language = str(payload.get("language") or "").strip().lower()
    if not re.fullmatch(r"[a-z]{2,3}", language):
        return None

    raw_terms = payload.get("topic_terms") or []
    topic_terms = tuple(
        term.strip()
        for term in (raw_terms if isinstance(raw_terms, (list, tuple)) else [])
        if isinstance(term, str) and term.strip() and len(term.strip()) <= 30
    )[:12]

    updated = dict(stored)
    updated.update(
        {
            "domains": list(domains),
            "language": language,
            "topic_terms": list(topic_terms),
            "enriched_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    _STORE.put(code, updated)

    logger.info(
        "Media list upgraded with language | jurisdiction=%s | language=%s | terms=%s",
        code, language, len(topic_terms),
    )
    return MediaPolicy(
        jurisdiction=code,
        national=domains,
        jurisdiction_known=True,
        source="stored",
        confidence=str(stored.get("confidence") or "low"),
        note=str(stored.get("note") or ""),
        language=language,
        topic_terms=topic_terms,
    )


async def resolve_policy(jurisdiction: Optional[str]) -> MediaPolicy:
    """Return the trust order for a company, generating the list if needed.

    A stored list is reused; a missing one is generated once and kept. Generation
    costs one model call per country, which is why it is stored rather than
    repeated per request.

    A stored entry that predates the language field is **upgraded** rather than
    used as-is: the domains are still good, but without the language there are no
    local queries, and an English query on Japanese domains returns the English
    edition of one title.
    """
    code = (jurisdiction or "").strip().upper().split("-")[0]
    if not code:
        return MediaPolicy(
            jurisdiction=None, jurisdiction_known=False, source="none",
            note="no jurisdiction supplied",
        )

    stored = _STORE.get(code)
    if stored and stored.get("domains"):
        domains = believe_domains(stored["domains"])
        if domains:
            language = str(stored.get("language") or "").strip().lower()
            if not re.fullmatch(r"[a-z]{2,3}", language):
                language = ""
            raw_terms = stored.get("topic_terms") or []
            topic_terms = tuple(
                term.strip()
                for term in (raw_terms if isinstance(raw_terms, (list, tuple)) else [])
                if isinstance(term, str) and term.strip()
            )[:12]

            if not language:
                # Upgrade in place: keep the validated domains, ask only for the
                # language and the local topic terms.
                upgraded = await _enrich_language(code, stored, domains)
                if upgraded is not None:
                    return upgraded

            logger.info(
                "Media list loaded from store | jurisdiction=%s | domains=%s | "
                "language=%s | terms=%s",
                code, len(domains), language or "?", len(topic_terms),
            )
            return MediaPolicy(
                jurisdiction=code,
                national=domains,
                jurisdiction_known=True,
                source="stored",
                confidence=str(stored.get("confidence") or "low"),
                note=str(stored.get("note") or ""),
                language=language,
                topic_terms=topic_terms,
            )

    return await generate_media_list(code)


def order_by_trust(
    results: Sequence[dict],
    policy: MediaPolicy,
) -> List[dict]:
    """Return results ordered best-trusted first, refused hosts removed."""
    kept: List[dict] = []
    refused: List[str] = []

    for result in results:
        if not isinstance(result, dict):
            continue
        host = host_of_result(result)
        if is_refused_host(host):
            refused.append(host)
            continue
        kept.append(result)

    if refused:
        logger.info(
            "Refused hosts dropped | count=%s | hosts=%s",
            len(refused),
            sorted(set(refused))[:8],
        )

    return sorted(kept, key=lambda item: policy.rank(host_of_result(item)))


def refused_domains() -> List[str]:
    """Every host this product will never read. Published so it can be checked."""
    return sorted(REFUSED_HOSTS)
