"""Migration orchestration: legacy reports -> the memory layer.

Separated from the HTTP endpoint on purpose: this runs with no web server
(evals call it, tests call it, a CLI could call it), and it is unit-testable
directly.

Idempotency is STRUCTURAL, via the raw_reports.migrated_at ledger, not a
similarity heuristic: a report whose migrated_at is set is skipped outright.
The ledger is stamped only AFTER a report's facts
commit, so a crash mid-run leaves that report NULL and the next run retries it -
resumable backfill, in one column.
"""

import logging

from app.db import owner_conn
from app.extraction import run_extraction
from app.models import MigrateResult

log = logging.getLogger(__name__)


def run_migration() -> MigrateResult:
    """Process every not-yet-migrated report oldest-first. Safe to run twice:
    the second run finds nothing to do and reports everything as skipped."""
    with owner_conn() as conn:
        # WHERE migrated_at IS NULL is the ledger check. Oldest-first is a
        # nicety, not a correctness requirement: supersede direction is decided
        # by source dates in reconcile, so any processing order is safe.
        reports = conn.execute(
            "SELECT id, student_id, content, created_at FROM raw_reports "
            "WHERE migrated_at IS NULL ORDER BY created_at"
        ).fetchall()
        skipped = conn.execute(
            "SELECT count(*) FROM raw_reports WHERE migrated_at IS NOT NULL"
        ).fetchone()[0]

    log.info("migrate: %d reports to process, %d already migrated (skipped)",
             len(reports), skipped)

    totals = {"added": 0, "confirmed": 0, "superseded": 0, "archived": 0}
    for report_id, student_id, content, created_at in reports:
        result = run_extraction(str(student_id), content, created_at,
                                source_report_id=str(report_id))
        for key in totals:
            totals[key] += result[key]
        # Stamp the ledger only after the facts above committed. Its own tiny
        # transaction, so it commits independently of the extraction graph.
        with owner_conn() as conn:
            conn.execute(
                "UPDATE raw_reports SET migrated_at = now() WHERE id = %s",
                (report_id,),
            )

    log.info("migrate: done - added=%d confirmed=%d superseded=%d archived=%d",
             totals["added"], totals["confirmed"], totals["superseded"],
             totals["archived"])

    return MigrateResult(
        reports_processed=len(reports),
        reports_skipped=skipped,
        facts_added=totals["added"],
        facts_confirmed=totals["confirmed"],
        facts_superseded=totals["superseded"],
        facts_archived=totals["archived"],
    )
