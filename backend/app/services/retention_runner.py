"""Apply the evidence retention policy to the configured database."""
from __future__ import annotations

from datetime import date

from app.core.database import SessionLocal
from app.repositories.briefing_repository import EvidenceRepository
from app.services.logger_service import configure_logging, get_logger
from app.services.retention import build_retention_plan

logger = get_logger("retention")


def run_retention(plan=None) -> dict[str, int]:
    """Delete expired fact bodies and decay stale URL identity.

    Reads are unaffected: this only removes what the briefing window can no
    longer reach, plus URL rows nobody has touched in months.
    """
    plan = plan or build_retention_plan(date.today())
    db = SessionLocal()
    try:
        result = EvidenceRepository(db).apply_retention(plan)
    finally:
        db.close()

    logger.info(
        "Retention applied | fact_cutoff=%s | facts_deleted=%s | urls_purged=%s "
        "| urls_dormant=%s",
        plan.fact_cutoff,
        result["facts_deleted"],
        result["urls_purged"],
        result["urls_dormant"],
    )
    return result


if __name__ == "__main__":
    configure_logging(force=True)
    print(run_retention())
