from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, HttpUrl


class FactItem(BaseModel):
    """A single fact or opinion item.

    The content field must be an excerpt from the source text or a short
    sentence tightly bound to the source text.

    A fact is stored once, however many outlets reported it. Every outlet that
    told it is kept in ``also_reported_by`` so a reader still gets all the
    links: how much coverage a claim received is not the claim's value, but the
    reader may still want every place it appeared.
    """

    content: str
    source_url: HttpUrl
    publish_date: Optional[datetime] = None
    # Additional sources that reported the same fact, so the entry keeps one
    # copy of the claim without losing the reader's other links.
    also_reported_by: List[str] = Field(default_factory=list)

    def all_sources(self) -> List[str]:
        """Return every URL that reported this fact, primary source first."""
        sources = [str(self.source_url)]
        for url in self.also_reported_by:
            if url and url not in sources:
                sources.append(url)
        return sources


class IncidentalFact(BaseModel):
    """A fact about our company found inside an article about someone else.

    These are kept apart from ``hard_facts`` on purpose: they are neither used
    as the model's evidence nor counted as full coverage. They are delivered as
    a trailing reference block so a human can follow up, and each carries a
    provenance state instead of an implied endorsement.
    """

    content: str
    source_url: HttpUrl
    publish_date: Optional[datetime] = None
    # Who the article was actually about, when the model could tell.
    article_subject: Optional[str] = None
    # "corroborated" when a targeted search found reporting that stands behind
    # this sentence, "unverified" when nothing did.
    provenance: str = "unverified"
    # The source found by the targeted follow-up search, when one exists.
    corroborating_url: Optional[str] = None

    def all_sources(self) -> List[str]:
        sources = [str(self.source_url)]
        if self.corroborating_url and self.corroborating_url not in sources:
            sources.append(self.corroborating_url)
        return sources


class CoverageRecord(BaseModel):
    """How much the retrieval actually reached, carried alongside the facts.

    Delivered to the reader as well as the model: a brief that says "thin
    coverage" is honest, while the same brief presented without that label
    reads as "nothing to worry about".
    """

    level: str = "none"
    total_facts: int = 0
    distinct_sources: int = 0
    can_support_leverage: bool = False
    lanes_missing: List[str] = []
    notes: List[str] = []
    checks_to_run: List[str] = []


class FactBase(BaseModel):
    """Core output of the ingestion layer."""

    company_id: str
    last_refresh_date: datetime
    hard_facts: List[FactItem]
    analyst_opinions: List[FactItem]
    # Job postings, kept apart from reported facts: they signal intent rather
    # than asserting an outcome, and the model reads them as a separate block.
    hiring: List[FactItem] = []
    conflicts: List[dict] = []
    # Not evidence: a reading list for the human, isolated from the model path.
    incidental_facts: List[IncidentalFact] = []
    # What retrieval did and did not reach, plus the checks left to the reader.
    coverage: CoverageRecord = CoverageRecord()
