# ADR-0006: Idempotency and resumability are structural, via per-source ledgers

- Status: accepted
- Date: 2026-07-08
- Supersedes: none
- Source: [../architecture/system-design.md](../architecture/system-design.md), [../../README.md](../../README.md)

## Context

Migrating ~7,000 real reports and ingesting conversations is a long, interruptible job that will be
re-run - after a crash, a partial import, or a code fix. It must never double-count a source or double-write
its facts, and a re-run must resume rather than restart. Guessing "have I seen this before" by content
similarity is fragile and expensive. Import and distillation are also separate concerns - landing raw rows
versus extracting facts - and coupling them makes both harder to re-run.

## Decision

We will make idempotency structural. Import upserts raw rows by `external_id` with `ON CONFLICT DO NOTHING`,
one transaction per student, so a re-import skips everything already landed. Distillation is gated by a
per-source ledger column (`raw_reports.migrated_at`, `conversations.ingested_at`), stamped only AFTER the
extracted facts commit - so a source is either fully distilled or still Pending, never half-done. `/migrate`
and `/ingest-conversations` select `WHERE ledger IS NULL`, so re-running them processes only what is left.
Import never extracts facts (one writer per concern).

## Consequences

Easier: every write-path endpoint is safe to re-run in any order; a crash mid-run loses at most the
in-flight source and a re-run resumes from the ledger; progress is readable from the ledgers themselves,
so the wizard needs no hooks into the job and survives restarts. Harder: the ledger must be stamped in the
same logical unit as the fact commit (or a source could show distilled with no facts), and the two-phase
"land raw, then distill" flow is one more step than a single combined pass. The demo runs distillation on
an in-process thread; the same ledger design carries over unchanged to a production task queue.
