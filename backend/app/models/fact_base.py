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


class FactBase(BaseModel):
    """Core output of the ingestion layer."""

    company_id: str
    last_refresh_date: datetime
    hard_facts: List[FactItem]
    analyst_opinions: List[FactItem]
    conflicts: List[dict] = []
