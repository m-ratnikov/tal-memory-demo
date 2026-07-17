# ADR-0002: Facts are versioned by supersede, never updated or deleted

- Status: accepted
- Date: 2026-07-08
- Supersedes: none
- Source: [../architecture/domain-model.md](../architecture/domain-model.md), [../../README.md](../../README.md)

## Context

Students change: "panics on losses" in January becomes "no longer panics" in June. A naive store would
overwrite the old fact with the new one. But the CHANGE is the product - "notice patterns over time" is a
stated requirement, and a coach that can see the arc of a student is the whole value. Overwriting destroys
that arc. It also destroys the audit trail a regulated system needs: "what did the system believe about
this student, and when".

## Decision

We will never update or delete a fact. When a new source asserts an evolved claim on the same aspect, we
write a NEW fact and set the old fact's `superseded_by` to point at it. Retrieval returns only live facts
(`superseded_by IS NULL`); the superseded chain is kept forever as history and is visible in the inspect
and review views. Confirmation of an unchanged fact bumps `last_confirmed_at` rather than writing a row.

## Consequences

Easier: the version chain IS the pattern-over-time data and the audit history, for free; the store-invariant
monitor can assert "no contradicting LIVE facts" over just the live set; nothing is ever lost. Harder: the
store grows monotonically (superseded rows accumulate), so a future consolidation/pruning job is needed at
scale, gated by evals and run offline. Every read must filter `superseded_by IS NULL`, and the supersede
DIRECTION becomes a real decision - resolved separately in [ADR-0004](0004-supersede-direction-by-source-date.md).
