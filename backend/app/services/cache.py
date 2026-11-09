# /backend/app/services/cache.py
"""How long a finished brief stays servable.

The request-level lookup itself lives in `BriefingRepository.find_cached`: the
record already stores company, meeting purpose and meeting time, so matching
those three columns is both correct and inspectable. A hashed key string was
written for this and never called — the repository compares fields instead — so
only the TTL that both share is kept here.

The cache deliberately stores nothing that could be stale beyond its window.
Entries expire, and the underlying evidence window is anchored to the meeting,
so a hit can only serve a request that would have produced the same brief.
"""
from __future__ import annotations

# A finished brief stays valid for this long. Production news moves daily, so
# anything older is re-run rather than served.
DEFAULT_TTL_HOURS = 6
