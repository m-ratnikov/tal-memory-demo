# ADR-0005: Reconcile by a joint per-source LLM judge plus a deterministic validator, not a cosine threshold

- Status: accepted
- Date: 2026-07-11
- Supersedes: none
- Source: [../architecture/system-design.md](../architecture/system-design.md), [../../README.md](../../README.md)

## Context

When new facts arrive, reconcile must decide for each whether it is new, a duplicate, or an evolution of
an existing fact. A cosine-distance threshold is the obvious mechanism, but embeddings are blind to the
two distinctions that matter most here: negation ("panics" vs "no longer panics" sit 0.49 apart) and
numbers ("score 8/10" vs "7/10" sit 0.012 apart) - both measured live in this repo. A distance cutoff
therefore cannot be trusted to decide, in either direction. A separate failure appeared when facts were
judged one at a time against the pre-batch store snapshot: two sibling facts matching the same live fact
produced a lost update - one superseded it, one confirmed the now-dead fact, and a true current claim
vanished from live memory.

## Decision

We will reconcile with a wide cosine net that only NOMINATES candidates (top-5 per fact under distance
0.60, across all kinds), then ONE JOINT judge call per source - a stronger model than the extraction/answer
workhorse - that sees all new facts plus the union of candidates and decides the whole SET coherently
(new / duplicate-of-existing / evolution-of-existing / duplicate-of-sibling). A deterministic
`validate_plan` step then enforces write-write coherence in code (at most one supersede per target; no
confirm of a fact superseded in the same batch). Cosine nominates, the judge decides relations, dates
decide direction ([ADR-0004](0004-supersede-direction-by-source-date.md)), and code guarantees coherence.

## Consequences

Easier: negation- and number-sensitive reconciliation the threshold could never do; sibling facts see each
other so the lost update cannot recur; the judge's reasoning is captured per decision in the audit. This is
the same shape as Mem0's joint ADD/UPDATE/DELETE/NOOP call and Graphiti's new-vs-new dedupe, so it is a
known-good pattern, not a bespoke bet. Harder: a stronger, pricier model on the highest-stakes call (offline,
one call per source, so acceptable); a joint prompt that grows with batch size (bounded by the cosine net
keeping it small); and a deterministic validator to maintain and unit-test (`tests/test_plan_coherence.py`).
