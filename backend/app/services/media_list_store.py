# /backend/app/services/media_list_store.py
"""The on-disk store for learned per-country press lists.

Generation and validation live in `media_trust`; this module owns only the file
those results are kept in. Machine-local, not source: a list is learned per
deployment and can be regenerated.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from app.services.logger_service import get_logger

logger = get_logger("media_list_store")

STORE_PATH = Path(__file__).resolve().parents[2] / "data" / "media_trust.json"


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
