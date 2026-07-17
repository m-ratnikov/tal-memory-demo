"""The import service - source-agnostic landing of raw data.

Takes ANY DataSource and lands its bundles in the raw tables (students,
raw_reports, conversations, messages). Facts are NOT extracted here: import
fills the append-only log; the /migrate and /ingest-conversations pipelines
then pick up the new rows through their own ledgers (migrated_at /
ingested_at are NULL on fresh rows). One writer per concern - import can be
re-run, reordered, or partially crash without ever touching the memory layer.

Idempotency is structural, via external_id UNIQUE + ON CONFLICT DO NOTHING:
re-importing the same source skips everything already landed, row by row.
Each student bundle commits in its own transaction, so a crash mid-import
loses at most one student and a re-run resumes exactly where it stopped -
the same resumability design as the migration ledger.
"""

import logging

from pydantic import BaseModel

from app.db import owner_conn
from app.importer.base import DataSource, StudentBundle

log = logging.getLogger(__name__)


class ImportResult(BaseModel):
    source: str
    students_imported: int = 0
    students_skipped: int = 0   # external_id already present - idempotent re-run
    reports_imported: int = 0
    reports_skipped: int = 0
    conversations_imported: int = 0
    conversations_skipped: int = 0
    messages_imported: int = 0


def run_import(source: DataSource) -> ImportResult:
    """Land every bundle from the source. Safe to re-run: the second run
    reports everything as skipped."""
    result = ImportResult(source=source.describe())
    for bundle in source.read():
        _import_bundle(bundle, result)
    log.info(
        "import done (%s): students +%d/=%d, reports +%d/=%d, "
        "conversations +%d/=%d, messages +%d",
        result.source,
        result.students_imported, result.students_skipped,
        result.reports_imported, result.reports_skipped,
        result.conversations_imported, result.conversations_skipped,
        result.messages_imported,
    )
    return result


def _import_bundle(bundle: StudentBundle, result: ImportResult) -> None:
    """One student, one transaction (owner_conn commits on clean exit)."""
    with owner_conn() as conn:
        # Upsert the student by SOURCE identity. ON CONFLICT DO NOTHING
        # returns no row when the student already exists - fetch the id then.
        row = conn.execute(
            "INSERT INTO students (external_id, name) VALUES (%s, %s) "
            "ON CONFLICT (external_id) DO NOTHING RETURNING id",
            (bundle.external_id, bundle.name),
        ).fetchone()
        if row:
            result.students_imported += 1
            student_id = row[0]
        else:
            result.students_skipped += 1
            student_id = conn.execute(
                "SELECT id FROM students WHERE external_id = %s",
                (bundle.external_id,),
            ).fetchone()[0]

        for report in bundle.reports:
            inserted = conn.execute(
                "INSERT INTO raw_reports (external_id, student_id, content, created_at) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (external_id) DO NOTHING RETURNING id",
                (report.external_id, student_id, report.content, report.created_at),
            ).fetchone()
            result.reports_imported += 1 if inserted else 0
            result.reports_skipped += 0 if inserted else 1

        for convo in bundle.conversations:
            inserted = conn.execute(
                "INSERT INTO conversations (external_id, student_id, started_at) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (external_id) DO NOTHING RETURNING id",
                (convo.external_id, student_id, convo.started_at),
            ).fetchone()
            if not inserted:
                result.conversations_skipped += 1
                continue  # messages were landed with the conversation last time
            result.conversations_imported += 1
            for msg in convo.messages:
                conn.execute(
                    "INSERT INTO messages (conversation_id, student_id, role, "
                    "content, created_at) VALUES (%s, %s, %s, %s, %s)",
                    (inserted[0], student_id, msg.role, msg.content, msg.at),
                )
                result.messages_imported += 1

    log.info("import: student %s (%s) landed", bundle.external_id, bundle.name)
