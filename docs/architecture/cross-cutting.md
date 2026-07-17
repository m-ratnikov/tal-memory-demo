# Cross-cutting concerns

System-wide concerns at the L1 boundary - where each lives and which decision fixes it. Technology
mechanisms (the logging library, the vector column, the job thread) are L2 choices; this file names
the concern and points to the deciding ADR.

Related: [system-context.md](system-context.md), [system-design.md](system-design.md),
[domain-model.md](domain-model.md). Governing decisions:
[ADR-0002](../adr/0002-versioning-by-supersede.md), [ADR-0003](../adr/0003-provenance-on-every-fact.md),
[ADR-0004](../adr/0004-supersede-direction-by-source-date.md),
[ADR-0005](../adr/0005-reconcile-by-joint-judge.md),
[ADR-0007](../adr/0007-per-student-isolation-via-rls.md),
[ADR-0008](../adr/0008-no-ann-index-on-personal-memory.md). Re-sliced by scope from
[../../README.md](../../README.md).

- **Isolation (trust boundary)**: per-student separation is enforced in Postgres by RLS, not in application code. The app connects as `tal_app` (RLS live, `SET LOCAL app.student_id` per request); migration, ingestion, review, and seeding connect as the owner (bypasses RLS, legitimately sees all). `SET LOCAL` (not `SET`) so the scope dies with the transaction - pooler-safe. Routing a request through the owner role would void isolation with no error, which is why the split is a decision, not a convention ([ADR-0007](../adr/0007-per-student-isolation-via-rls.md)).

- **Provenance**: every fact links to exactly one source (report or conversation) and that source's event date. This is what makes the anti-hallucination rule ("state only retrieved facts, each traceable") enforceable, and it is the write-path audit's spine ([ADR-0003](../adr/0003-provenance-on-every-fact.md)).

- **Versioning**: facts are superseded (`superseded_by`), never updated or deleted - history is the "notice patterns over time" data. Supersede DIRECTION is decided by source event time (`source_created_at`), never processing order: only a strictly newer source overturns a live fact, and an older source's fact is archived (born superseded). This makes the two independent write-path ledgers safe to process in any order ([ADR-0002](../adr/0002-versioning-by-supersede.md), [ADR-0004](../adr/0004-supersede-direction-by-source-date.md)).

- **Data sensitivity**: the stored data is students' psychological profiles - sensitive, and moving into FCA-regulated territory. PII concentrates in `raw_reports`, `messages`, and the derived `memories` (plus the `matched_content` / `judge_reason` strings in `extraction_audit` and the answer snapshots in `retrieval_audit`). It crosses the boundary to the LLM provider on every extraction, embedding, and answer - the main data-processor exposure. Retention and a one-click per-student delete are named production requirements; the demo does not yet implement them.

- **Observability**: per-node structured logging (`INFO` summaries; `DEBUG` shows the per-fact reconcile decision and the nearest-candidate distance that drove it), plus optional LangSmith tracing via env vars with zero app-code coupling - every graph node, LLM call, and embedding as a nested trace. Traces are the raw material for error analysis. Data-residency caveat: LangSmith's default endpoint is hosted and traces carry psychological facts, so for FCA data self-host the tracer/judge (Langfuse/Phoenix or a self-hosted endpoint); app code stays vendor-neutral, enabling tracing is env-only.

- **Write-path audit and human review** (`extraction_audit` + `/review`): every reconcile decision persists durably - the fact, the action, the live fact it was judged against, the judge's verdict and reason - in the SAME transaction as the action, so an action can never commit without its audit trail. This is the FCA answer to "why does the system believe X about this student". The `/review` UI shows each source next to its decisions for approve/flag; flagged rows export as eval-case candidates. Migration review policy: 100% of the pilot batch, then all supersede/archive plus a sample of inserts.

- **Read-path audit** (`retrieval_audit` + `/review/answers`): every answer is ledgered with its exact ranking snapshot (memory ids, scores, ranks - captured at answer time, since rankings drift and cannot be reconstructed later). Written by `tal_app` inside the student-scoped transaction (RLS `WITH CHECK` = a request audits only its own student), INSERT-only for the app role. This is where real-traffic eval data accumulates: a flagged answer plus its snapshot is most of a golden case.

- **Quality gates (evals + monitor)**: a golden set of independent ground-truth cases scores recall and supersede (NLI-style entailment judge, robust to paraphrase and negation) and groundedness (LLM-as-judge; a correct refusal counts as grounded). The free, deterministic CI gate is the cross-student leakage suite (`tests/test_isolation.py`) plus the plan-coherence suite; the paid judged evals run on demand behind `--fail-under`. Separately, the store-invariant monitor judges the STORE not answers - no student may hold two contradicting live facts - catching latent corruption that stayed below top-k while every answer looked correct (the exact failure observed live before the joint-judge fix, [ADR-0005](../adr/0005-reconcile-by-joint-judge.md)).

- **Configuration and secrets**: runtime config and the `OPENAI_API_KEY` / DSNs / `LANGSMITH_*` vars are read once from the environment (`app/config.py`); secrets stay server-side and never reach a client. The reconcile judge model is a separate, stronger-model env knob than the workhorse.

- **Failure handling and idempotency**: the write path is idempotent and resumable by structure - import upserts by `external_id` (`ON CONFLICT DO NOTHING`), and the migrate/ingest ledgers are stamped only after facts commit, so a crash mid-distill leaves the source Pending and a re-run resumes exactly where it stopped ([ADR-0006](../adr/0006-idempotency-by-ledger.md)). A read-path LLM error surfaces to the caller; a refusal (nothing retrieved) is a correct, audited outcome, not an error.

- **Scaling / capacity**: personal memory is searched per-student with an exact scan and NO ANN/HNSW index - within one student's few-hundred rows an exact scan beats an approximate one and stays exact; HNSW is reserved for the shared knowledge base's millions of passages, never per-student memory ([ADR-0008](../adr/0008-no-ann-index-on-personal-memory.md)). The binding constraint is the paid, rate-limited LLM: extraction and judged evals are the cost driver, run offline and batched. The demo runs write-path jobs in-process on a thread; the named production path is a task queue, and conversation ingestion moves from a manual batch endpoint to per-finished-conversation triggering - neither changes the memory-layer contract.

- **Integration points (brownfield)**: the memory layer plugs into components that already exist - LiveKit voice (preload the session's facts at connect; retrieval must never block the speech loop), auth/logins (the session maps to the `app.student_id` RLS reads), and the deploy/monitoring stack. Migration runs against a COPY of production, never live data.
