import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services import dedup  # noqa: E402  (path must be set first)


@pytest.fixture(autouse=True)
def _clear_dedup_verdicts():
    """Dedup verdicts are cached across runs; tests must not inherit them."""
    dedup.clear_verdict_cache()
    yield
    dedup.clear_verdict_cache()
