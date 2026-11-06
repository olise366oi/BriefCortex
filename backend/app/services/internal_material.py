# /backend/app/services/internal_material.py
"""Internal material supplied by the caller, instead of market retrieval.

A company that would rather not have its own position read from public coverage
can hand its material in directly: an internal strategy note, a board pack, a
sales target. This is an **intake path only** — the content is stored and made
available to the recommendation, and nothing here parses, scores or judges it.

Two limits are deliberate:

* It never becomes evidence about the counterparty. Internal material describes
  us; it cannot be cited as something they did.
* It is capped in size. A pasted board pack would otherwise crowd out the
  assessment, and the recommendation reasons over an assessment of a few
  paragraphs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.services.logger_service import get_logger

logger = get_logger("internal_material")

# A board pack is longer than this, but the recommendation does not need all of
# it, and an unbounded paste would drown the assessment.
MAX_MATERIAL_CHARS = 4000

MAX_MATERIALS_PER_COMPANY = 5


@dataclass(frozen=True)
class InternalMaterial:
    """One piece of caller-supplied material."""

    company: str
    title: str
    body: str
    supplied_at: datetime

    @property
    def usable(self) -> bool:
        return bool(self.body.strip())

    def to_prompt_block(self) -> str:
        """Render for the recommendation stage."""
        if not self.usable:
            return ""
        title = self.title.strip() or "internal note"
        return f"[{title}]\n{self.body.strip()[:MAX_MATERIAL_CHARS]}"


class MaterialStore:
    """Materials per company, held for this process.

    Keyed by the normalised company name, because the whole point is that the
    caller's own company — "Canada Goose" today and "Canada Goose Holdings"
    tomorrow — resolves to the same body of internal context.
    """

    def __init__(self) -> None:
        self._by_company: Dict[str, List[InternalMaterial]] = {}

    def add(self, material: InternalMaterial) -> None:
        key = _key(material.company)
        if not key:
            raise ValueError("material needs a company name")
        bucket = self._by_company.setdefault(key, [])
        bucket.append(material)

        # Keep the most recent ones: an older board pack is superseded, not
        # additive, and the cap is what stops a store from growing unbounded.
        if len(bucket) > MAX_MATERIALS_PER_COMPANY:
            del bucket[: len(bucket) - MAX_MATERIALS_PER_COMPANY]
        logger.info(
            "Internal material stored | company=%r | title=%r | held=%s",
            material.company,
            material.title,
            len(bucket),
        )

    def list(self, company: str) -> List[InternalMaterial]:
        return list(self._by_company.get(_key(company), []))

    def clear(self, company: str) -> int:
        return len(self._by_company.pop(_key(company), []))


def _key(name: str) -> str:
    return " ".join((name or "").lower().split())


# One store per process, so the API's intake endpoint and the briefing pipeline
# share the same material without either importing the other.
_SHARED_STORE: Optional[MaterialStore] = None


def shared_store() -> MaterialStore:
    """Return the process-wide material store."""
    global _SHARED_STORE
    if _SHARED_STORE is None:
        _SHARED_STORE = MaterialStore()
    return _SHARED_STORE


def render_materials(materials: List[InternalMaterial]) -> str:
    """Render every usable material into one prompt block."""
    blocks = [item.to_prompt_block() for item in materials if item.usable]
    return "\n\n".join(blocks)


def build_material(
    company: str,
    title: str,
    body: str,
) -> Optional[InternalMaterial]:
    """Validate and normalise one submission, or None when it carries nothing."""
    name = (company or "").strip()
    text = re.sub(r"\s+", " ", body or "").strip()
    if not name or not text:
        return None

    return InternalMaterial(
        company=name,
        title=" ".join((title or "").split())[:120],
        body=text,
        supplied_at=datetime.now(timezone.utc),
    )
