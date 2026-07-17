# ADR-0009: LangGraph on the write path only; the read path stays a linear function

- Status: accepted
- Date: 2026-07-09
- Supersedes: none
- Source: [../architecture/system-design.md](../architecture/system-design.md), [../../README.md](../../README.md) section 4

## Context

The stack lists agent frameworks, and it is tempting to model everything as a graph. But the two paths have
different shapes. The write path is a small state machine - extract, then reconcile (a wide net, a joint
judge, a validator), then store - with real intermediate state, room for branching (e.g. low-confidence to
human review), and value in per-step observability. The read path is one request: embed the question, run
one query, make one LLM call, return. It has no multi-step state to carry, nothing to branch on, nothing to
resume.

## Decision

We will use LangGraph for the WRITE path only, where its nodes/edges/state make the pipeline explicit,
testable, and traceable per step. The READ path stays a plain linear function (embed -> retrieve -> ground
-> answer); adding a graph there would be ceremony without payoff. In a production voice product the
stateful thing is the CONVERSATION LOOP (long sessions, dropped calls, turn accumulation), and that is where
a checkpointer-backed graph earns its place - not in the one-shot answer.

## Consequences

Easier: the write path gets structure and observability where the judgment lives; the read path stays as
simple and fast as it is (no framework overhead on the latency-sensitive path, which matters for voice).
Saying "a graph for three linear steps is overkill" out loud is the senior call. Harder: two different
styles in one codebase (a graph and a plain function), which must be understood as a deliberate altitude
choice, not an inconsistency. When the conversation loop is built, its stateful graph is a new decision,
not an extension of this one.
