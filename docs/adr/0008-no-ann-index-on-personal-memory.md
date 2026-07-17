# ADR-0008: No ANN (HNSW) index on personal memory - an exact per-student scan

- Status: accepted
- Date: 2026-07-08
- Supersedes: none
- Source: [../architecture/system-design.md](../architecture/system-design.md), [../../README.md](../../README.md)

## Context

pgvector offers approximate-nearest-neighbour indexes (HNSW/IVFFlat) to make vector search fast at scale.
The reflex is to add one to the `memories` table. But every personal-memory search is already scoped by RLS
to ONE student's rows - a few hundred at most - not millions. ANN indexes trade exactness for speed on large
sets; on a few hundred rows they add build/maintenance cost and approximation error while saving nothing.

## Decision

We will NOT build an ANN index on personal memory. Retrieval does an exact scan over the current student's
live facts, ranked by `similarity x recency` under a small top-k. HNSW is reserved for the shared knowledge
base (millions of passages), where approximate search actually pays off - and that store is out of trial
scope ([ADR-0001](0001-two-homes-memory-vs-knowledge-base.md)).

## Consequences

Easier: exact results (no ANN recall loss), no index to build, tune, or keep warm, and simpler operations.
The RLS scoping that isolation requires ([ADR-0007](0007-per-student-isolation-via-rls.md)) is the same
thing that keeps the scan cheap, so the two decisions reinforce each other. Harder: if per-student memory
ever grew to tens of thousands of live facts, the exact scan would need revisiting - a future decision, not
a present cost. The point is deliberate: knowing when NOT to add infrastructure is part of the design.
