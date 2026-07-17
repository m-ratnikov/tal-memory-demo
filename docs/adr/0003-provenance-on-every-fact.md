# ADR-0003: Every fact carries provenance to exactly one source

- Status: accepted
- Date: 2026-07-08
- Supersedes: none
- Source: [../architecture/domain-model.md](../architecture/domain-model.md), [../../README.md](../../README.md)

## Context

The product must not hallucinate memories - inventing a "fact" about a student's psychology is worse than
saying "I don't know that yet", especially under FCA scrutiny. The anti-hallucination rule is "TAL may
only state things it actually retrieved". That rule is only enforceable and auditable if every stored fact
can be traced back to the exact source text it came from. A fact with no source is unverifiable and
un-removable.

## Decision

We will require every `memories` row to reference exactly one source: either `source_report_id` OR
`source_conversation_id` (never both, never neither), plus that source's own event date
(`source_created_at`). Provenance is written in the same store step that creates the fact. The read path
returns, with each answer, the memories it used - provenance surfaced to the caller and recorded in the
read-path audit.

## Consequences

Easier: the anti-hallucination rule becomes checkable (groundedness evals; "which memories did it use"),
the write-path audit has a spine, and a "delete everything derived from source X" operation is a
well-defined query. The event date on the fact is also what makes date-based supersede possible
([ADR-0004](0004-supersede-direction-by-source-date.md)). Harder: the one-of-two provenance is an invariant
to enforce (a CHECK constraint in the brownfield schema), and the extractor must always know which source
it is processing. Facts cannot be synthesized from thin air or merged across sources without choosing a
provenance - a deliberate constraint.
