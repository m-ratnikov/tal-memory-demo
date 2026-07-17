# ADR-0004: Supersede direction is decided by source event date, not processing order

- Status: accepted
- Date: 2026-07-10
- Supersedes: none
- Source: [../architecture/system-design.md](../architecture/system-design.md), [../../README.md](../../README.md)

## Context

Facts evolve, and two facts on the same aspect must resolve to one live winner ([ADR-0002](0002-versioning-by-supersede.md)).
There are two independent write paths - reports (`migrated_at`) and conversations (`ingested_at`) - with
separate ledgers that can run in any order, including re-runs and backfills. If "the fact processed later
wins", then re-ingesting an old conversation after a newer report would resurrect a stale claim. This
actually happened in validation: a re-run resurrected "panics on losses" over the follow-up report's "no
longer panics".

## Decision

We will decide supersede direction by comparing `source_created_at` (the source document's own event
date), never processing order. Reconcile only lets a STRICTLY NEWER source overturn a live fact
(supersede); a fact from an OLDER source than the live fact is ARCHIVED - inserted already-superseded
(born dead) so the timeline stays complete without disturbing current truth. Confirmation from an older
source may not bump `last_confirmed_at` past a newer source's date. The LLM judge decides only that two
facts are the same evolving aspect; which side wins is pure code comparing dates.

## Consequences

Easier: any processing order across the two ledgers is safe - re-runs, backfills, and late-arriving old
documents can never overturn newer knowledge. The rule is deterministic and testable, independent of the
LLM. Harder: every source must carry a trustworthy event date (import must preserve it), and "archive"
is an extra store action to implement and audit. Clock skew or a wrong source date would mis-order facts,
so source dates are treated as event-time truth, not ingestion time.
