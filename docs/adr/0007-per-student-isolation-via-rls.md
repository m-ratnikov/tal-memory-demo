# ADR-0007: Per-student isolation is enforced in the database via RLS and a non-owner app role

- Status: accepted
- Date: 2026-07-08
- Supersedes: none
- Source: [../architecture/system-design.md](../architecture/system-design.md), [../../README.md](../../README.md)

## Context

The stored data is students' psychological profiles, and the company is moving into FCA-regulated
territory. "Student A's memories must never surface in student B's session" is a hard requirement. If
isolation depends on every query carrying the right `WHERE student_id = ...`, one forgotten predicate in
one code path leaks sensitive data with no error. That is too fragile for regulated psychological data.

## Decision

We will enforce isolation in Postgres with row-level security. Every personal table has a policy
`student_id = current_setting('app.student_id')`. The application serves requests as a NON-OWNER role
(`tal_app`) for which RLS is live, setting `app.student_id` per request with `SET LOCAL` (dies with the
transaction, pooler-safe). Migration, ingestion, review, and seeding use the table-owner role, which
BYPASSES RLS and legitimately sees all students. The read path deliberately omits `WHERE student_id` - the
policy supplies it - to prove the enforcement lives in the database, not in remembered predicates.

## Consequences

Easier: isolation holds even when a query forgets the predicate; the guarantee is one policy per table, not
a discipline spread across every handler; a cross-student leakage test (`tests/test_isolation.py`) is a
cheap, deterministic CI gate. Harder: two connection roles to manage, and a sharp trap made explicit - if
the app ever served requests through the owner role, isolation would evaporate silently, so the role split
is load-bearing, not incidental. `SET LOCAL` (not `SET`) is mandatory behind a transaction-mode pooler.
Auth is out of demo scope; in production the authenticated session maps to `app.student_id`, with RLS as
the floor beneath it.
