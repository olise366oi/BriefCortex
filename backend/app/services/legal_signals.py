# /backend/app/services/legal_signals.py
"""Targeted searches for legal and regulatory exposure.

Regulator portals differ by country and most are unreachable or unusable from
outside their jurisdiction, so this does not integrate them one by one. It
searches the open web with jurisdiction-aware phrasing instead, which surfaces
the same events through coverage that quotes the filings, and marks everything
it finds as a lead rather than a fact.
"""
from __future__ import annotations

from typing import List, Optional

from app.services.company_identity import CompanyIdentity
from app.services.logger_service import get_logger

logger = get_logger("legal")

# Jurisdiction-specific regulator names. Naming the regulator in the query
# pulls the filing coverage to the front, which is the point of the search.
REGULATORS_BY_JURISDICTION: dict[str, tuple[str, ...]] = {
    "US": ("SEC", "DOJ", "FTC", "FDA"),
    "GB": ("CMA", "FCA", "Serious Fraud Office"),
    "DE": ("Bundeskartellamt", "BaFin"),
    "FR": ("Autorite de la concurrence", "AMF"),
    "IT": ("Autorita Garante della Concorrenza", "Consob"),
    "NL": ("Autoriteit Consument en Markt", "AFM"),
    "ES": ("CNMC", "CNMV"),
    "CH": ("Competition Commission", "FINMA"),
    "DK": ("Konkurrence- og Forbrugerstyrelsen", "Finanstilsynet"),
    "SE": ("Konkurrensverket", "Finansinspektionen"),
    "NO": ("Konkurransetilsynet", "Finanstilsynet"),
    "IN": ("Competition Commission of India", "SEBI", "MCA"),
    "JP": ("Japan Fair Trade Commission", "FSA"),
    "CN": ("SAMR", "CSRC"),
    "HK": ("Competition Commission", "SFC"),
    "SG": ("Competition and Consumer Commission", "MAS"),
    "AU": ("ACCC", "ASIC"),
    "CA": ("Competition Bureau", "OSC"),
    "KY": ("Cayman Monetary Authority",),
}

# The regulatory questions a deal team actually asks.
LEGAL_TOPICS = (
    "lawsuit litigation",
    "regulatory fine penalty settlement",
    "antitrust competition investigation",
    "regulator probe inquiry",
)

# Fallback regulators when the jurisdiction is unknown, so a search still runs.
GENERAL_REGULATORS = ("regulator", "antitrust authority")


def legal_queries(
    company: str,
    identity: Optional[CompanyIdentity] = None,
    *,
    max_queries: int = 4,
) -> List[str]:
    """Build searches for legal and regulatory exposure.

    The jurisdiction decides which regulator to name. When the registry could
    not identify the company confidently, the queries stay generic rather than
    guessing a country: a wrong regulator name returns another country's news.
    """
    name = company.strip()
    if not name:
        return []

    jurisdiction = None
    if identity is not None and identity.confident and identity.jurisdiction:
        jurisdiction = identity.jurisdiction.upper()

    regulators = REGULATORS_BY_JURISDICTION.get(jurisdiction, GENERAL_REGULATORS)

    queries: List[str] = []
    for topic in LEGAL_TOPICS:
        queries.append(f"{name} {topic}")
        if len(queries) >= max_queries:
            break

    # One regulator-anchored query, because filings coverage is the strongest
    # signal that something was actually decided rather than merely alleged.
    primary_regulator = regulators[0]
    queries.append(f"{name} {primary_regulator} investigation fine ruling")

    logger.info(
        "Legal queries | company=%r | jurisdiction=%s | regulators=%s",
        name,
        jurisdiction or "unknown",
        regulators[:2],
    )
    return queries[: max_queries + 1]
