# /backend/app/services/own_profile.py
"""The caller's own company profile, used only where it is legitimate.

A briefing is read by someone who is themselves in a position: a supplier, a
customer, a partner, an investor. What that position is changes what advice makes
sense — a company with no alternative supplier should not be told to walk away.

It must not change what the evidence is said to contain. A profile is therefore
readable at exactly one stage, the recommendation, and nowhere else:

- The findings stage never sees it, so the caller's own situation cannot make an
  article look like it says something it does not.
- The analysis stage never sees it, so the assessment of the other company stays
  independent of who is reading.
- The recommendation stage sees it, because that is the point where "what should
  we do" legitimately depends on "where do we stand".

Profiles are stored by name so a caller can keep several and choose one per
request, which is how a team with several business lines uses the product.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from app.services.logger_service import get_logger

logger = get_logger("own_profile")

# A profile is a few sentences, not a document. Beyond this it is truncated rather
# than rejected, because a long paste should not fail a request.
MAX_PROFILE_CHARS = 1200


@dataclass(frozen=True)
class OwnCompanyProfile:
    """The caller's own side: who they are and what position they are in."""

    name: str
    description: str
    # Free-text facts that shape what is realistic: scale, alternatives, deadline,
    # budget, regulatory constraints.
    leverage_notes: str = ""

    @property
    def is_usable(self) -> bool:
        return bool(self.name.strip() or self.description.strip())

    def to_prompt_block(self) -> str:
        """Render for the recommendation stage only."""
        parts: List[str] = []
        if self.name.strip():
            parts.append(f"OUR COMPANY: {self.name.strip()}")
        if self.description.strip():
            parts.append(self.description.strip()[:MAX_PROFILE_CHARS])
        if self.leverage_notes.strip():
            parts.append(f"OUR POSITION: {self.leverage_notes.strip()[:MAX_PROFILE_CHARS]}")
        return "\n".join(parts)


class ProfileStore:
    """Named profiles, held in memory for one process.

    Kept deliberately simple: these are small text blobs bound to a request, and
    persisting them is the database's job when the API exposes them. The store
    exists so a profile can be resolved by name without threading it through every
    call.
    """

    def __init__(self) -> None:
        self._profiles: Dict[str, OwnCompanyProfile] = {}

    def add(self, profile: OwnCompanyProfile) -> None:
        key = _key(profile.name)
        if not key:
            raise ValueError("a profile needs a name")
        self._profiles[key] = profile
        logger.info("Own profile stored | name=%r | total=%s", profile.name, len(self._profiles))

    def get(self, name: str) -> Optional[OwnCompanyProfile]:
        return self._profiles.get(_key(name))

    def list(self) -> List[OwnCompanyProfile]:
        return list(self._profiles.values())

    def remove(self, name: str) -> bool:
        return bool(self._profiles.pop(_key(name), None))


def _key(name: str) -> str:
    return " ".join((name or "").lower().split())


def resolve_profile(
    store: ProfileStore,
    name: str,
    inline: Optional[str] = None,
) -> Optional[OwnCompanyProfile]:
    """Return the profile to use, or None.

    A name resolves to a stored profile; no name means no profile, which is a
    normal case (a caller who does not want their position to shape the advice).
    An inline description is accepted for a one-off, because forcing a save before
    the first request would be friction for no gain.
    """
    if name:
        stored = store.get(name)
        if stored is not None:
            return stored
        logger.info("Own profile not found | name=%r", name)

    if inline and inline.strip():
        return OwnCompanyProfile(name=name or "inline", description=inline.strip())

    return None
