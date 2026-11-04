# /backend/app/services/urls.py
"""URL parsing shared by filtering, freshness and source ranking."""
from __future__ import annotations


def _strip_scheme_and_query(url: str) -> str:
    """Lowercase a URL and drop its query, fragment and scheme."""
    lowered = url.strip().lower()
    for separator in ("?", "#"):
        if separator in lowered:
            lowered = lowered.split(separator, 1)[0]
    if "//" in lowered:
        lowered = lowered.split("//", 1)[1]
    return lowered


def host_of(url: str) -> str:
    """Return the lowercased host, without www, query or fragment."""
    host = _strip_scheme_and_query(url).split("/", 1)[0]
    if host.startswith("www."):
        host = host[len("www."):]
    return host


def url_path(url: str) -> str:
    """Return the path portion of a URL, lowercased, without query or fragment."""
    stripped = _strip_scheme_and_query(url)
    slash = stripped.find("/")
    return stripped[slash:] if slash != -1 else "/"


def url_key(url: str) -> str:
    """Return a comparison key for a URL: no scheme, no trailing slash."""
    return url.strip().rstrip("/")
