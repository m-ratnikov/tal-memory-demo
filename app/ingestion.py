"""Ingest finished conversations into the memory layer.

The SAME extraction path as reports (migration.py), just fed a conversation
transcript instead of a report. A finished conversation is distilled into typed
FACTS - this is how the semantic memory grows from talking. Raw conversation is
never chunked or retrieved into a prompt here; that is the knowledge base's job.

Idempotency is structural via conversations.ingested_at (like the report ledger):
a conversation already ingested is skipped; the ledger is stamped only after its
facts commit, so a crash mid-run retries it next time.
"""

import logging

from app.db import owner_conn
from app.extraction import run_extraction

log = logging.getLogger(__name__)


def ingest_conversations() -> dict:
    """Distill every not-yet-ingested conversation into facts. Safe to re-run:
    the second run finds nothing and reports everything skipped."""
    with owner_conn() as conn:
        # Oldest-first is a nicety, not a correctness requirement: supersede
        # direction is decided by source dates (started_at), so any processing
        # order - including relative to report migration - is safe.
        convos = conn.execute(
            "SELECT id, student_id, started_at FROM conversations "
            "WHERE ingested_at IS NULL ORDER BY started_at"
        ).fetchall()
        skipped = conn.execute(
            "SELECT count(*) FROM conversations WHERE ingested_at IS NOT NULL"
        ).fetchone()[0]

    log.info("ingest: %d conversations to process, %d already ingested (skipped)",
             len(convos), skipped)

    totals = {"added": 0, "confirmed": 0, "superseded": 0, "archived": 0}
    for convo_id, student_id, started_at in convos:
        with owner_conn() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages "
                "WHERE conversation_id = %s ORDER BY created_at",
                (convo_id,),
            ).fetchall()
        if not rows:
            continue

        # The whole transcript is the source text for extraction. The extractor
        # pulls durable facts from it, same as from a report. The conversation's
        # started_at is the source date (event time).
        transcript = "\n".join(f"{role}: {content}" for role, content in rows)
        result = run_extraction(
            str(student_id), transcript, started_at,
            source_conversation_id=str(convo_id),
        )
        for key in totals:
            totals[key] += result[key]

        with owner_conn() as conn:
            conn.execute(
                "UPDATE conversations SET ingested_at = now() WHERE id = %s",
                (convo_id,),
            )
        log.info("ingest: conversation=%s -> +%d facts", convo_id, result["added"])

    log.info("ingest: done - added=%d confirmed=%d superseded=%d archived=%d",
             totals["added"], totals["confirmed"], totals["superseded"],
             totals["archived"])
    return {"conversations_ingested": len(convos),
            "conversations_skipped": skipped,
            "facts_added": totals["added"],
            "facts_confirmed": totals["confirmed"],
            "facts_superseded": totals["superseded"],
            "facts_archived": totals["archived"]}
