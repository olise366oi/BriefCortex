from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, HttpUrl


class FactItem(BaseModel):
    """A single fact or opinion item.

    The content field must be an excerpt from the source text or a short
    sentence tightly bound to the source text.
    """

    content: str
    source_url: HttpUrl
    publish_date: Optional[datetime] = None


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


class FactBase(BaseModel):
    """Core output of the ingestion layer."""

    company_id: str
    last_refresh_date: datetime
    hard_facts: List[FactItem]
    analyst_opinions: List[FactItem]
    conflicts: List[dict] = []
    # Not evidence: a reading list for the human, isolated from the model path.
    incidental_facts: List[IncidentalFact] = []
