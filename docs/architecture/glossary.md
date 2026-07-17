# Glossary (ubiquitous language)

The canonical noun for each domain concept - one term per concept. ONE glossary per bounded
context (we have one, the coach's personal-memory core). The other views and the code use these
terms.

Related: [domain-model.md](domain-model.md), [system-design.md](system-design.md),
[system-context.md](system-context.md). Governing decisions:
[ADR-0001](../adr/0001-two-homes-memory-vs-knowledge-base.md) (memory layer vs knowledge base),
[ADR-0002](../adr/0002-versioning-by-supersede.md) (supersede, never delete),
[ADR-0003](../adr/0003-provenance-on-every-fact.md) (provenance). Re-sliced by scope from the trial
design in [../../README.md](../../README.md) and [../../ARCHITECTURE.md](../../ARCHITECTURE.md).

- **TAL**: the AI trading-psychology coach the product ships. Stateless per call - its knowledge of a student is exactly what the app puts in the prompt, which is why the memory layer exists.
- **Student**: a person TAL coaches. The subject of one isolated memory; identified inside the system by a `uuid`, and in a source system by an `external_id`.
- **Operator**: the human running migration, ingestion, review, and evals. Uses the owner-role tools (`/wizard`, `/review`); never a student. Trusted principal, distinct from a student.
- **Raw log**: the append-only source of truth - a student's `raw_reports` and `conversations`/`messages`. Never deleted, never retrieved wholesale into a prompt. Facts are distilled FROM it ([ADR-0001](../adr/0001-two-homes-memory-vs-knowledge-base.md)).
- **Report**: a legacy personality/assessment document about a student (`raw_reports`). A write-path source; migrated into facts.
- **Conversation**: a coaching-session transcript (`conversations` + `messages`). The other write-path source; ingested into facts by the same extraction path as reports - this is how memory grows from talking.
- **Memory layer**: the curated store of typed facts about one student (`memories`). The product's core: small dated facts with provenance and versioning, searched by meaning under a token budget. Distinct from the knowledge base.
- **Fact (memory)**: one small, self-contained, typed statement about a student ("exits winning positions too early"), with an embedding, an importance, provenance to its source, and a version chain. The retrieval unit - never a raw passage.
- **Kind**: the closed type of a fact - `trait | goal | struggle | event | preference`. Reconciliation rules differ by kind (an `event` is one-time history, never versioned in or out; traits/goals/struggles/preferences evolve).
- **Importance**: a 0..1 weight the extractor assigns a fact - how central it is to coaching this student.
- **Live fact**: a fact whose `superseded_by` is null - the current truth, and the only kind retrieval returns.
- **Superseded fact**: a fact versioned out by a newer-source fact on the same aspect (`superseded_by` set). Kept forever as history; excluded from retrieval. The "notice patterns over time" data ([ADR-0002](../adr/0002-versioning-by-supersede.md)).
- **Provenance**: the link from a fact to the exact source it came from (`source_report_id` OR `source_conversation_id`) and that source's event date (`source_created_at`). What makes the anti-hallucination rule enforceable and the audit trail real ([ADR-0003](../adr/0003-provenance-on-every-fact.md)).
- **Extraction**: the write-path step that turns one source's text into typed facts via a structured-output LLM call. The student's name is injected so every source phrases facts identically.
- **Reconcile**: the write-path step that decides, for a batch of newly extracted facts, how each relates to the existing store - insert, confirm, supersede, archive, or skip. A wide cosine net nominates candidates; a joint LLM judge decides the set; a deterministic validator enforces write-write coherence ([ADR-0005](../adr/0005-reconcile-by-joint-judge.md)).
- **Judge (reconcile)**: the stronger-model LLM call that, seeing all new facts plus all candidates at once, assigns each a relation (`new | duplicate_of_existing | evolution_of_existing | duplicate_of_sibling`). It decides relations, never the supersede direction - dates decide that ([ADR-0004](../adr/0004-supersede-direction-by-source-date.md)).
- **Supersede**: version a live fact out because a strictly NEWER source asserts an evolved claim on the same aspect. The new fact goes live; the old one is kept, linked by `superseded_by`.
- **Archive**: insert a fact already superseded (born dead) because it comes from an OLDER source than the live fact on that aspect. Keeps the timeline complete without disturbing current truth ([ADR-0004](../adr/0004-supersede-direction-by-source-date.md)).
- **Confirm**: re-affirm an existing live fact a new source duplicates - bump its `last_confirmed_at` (never past an older source's date). No new row.
- **Skip**: store nothing because a fact duplicates a SIBLING fact in the same batch; an audit row is still written.
- **Similarity**: cosine similarity of a question's embedding to a fact's embedding - the "by meaning" axis of retrieval. Blind to negation and to numbers, so it filters candidates but never decides ([ADR-0005](../adr/0005-reconcile-by-joint-judge.md)).
- **Recency**: a time-decay factor on a fact's `last_confirmed_at` - the "how current" axis of retrieval.
- **Score (retrieval)**: `similarity x recency`, the ranking key for the read path. Surfaced with its two factors so a low score is diagnosable (unrelated vs stale).
- **Top-k budget**: the hard cap on how many facts TAL sees per question (`TOP_K = 5`). What kills context bloat.
- **Ledger**: a nullable timestamp column that records whether a source has been distilled - `raw_reports.migrated_at`, `conversations.ingested_at`. Stamped only after facts commit, so the write path is idempotent and resumable ([ADR-0006](../adr/0006-idempotency-by-ledger.md)).
- **DataSource adapter**: an implementation of the import `DataSource` protocol that yields per-student bundles from one source shape (today `FileDataSource`; the client's real export becomes a second adapter). Import lands raw data only - one writer per concern.
- **external_id**: a record's identity in the SOURCE system (`UNIQUE`, nullable). Makes bulk import idempotent by structure (`ON CONFLICT DO NOTHING`).
- **RLS (row-level security)**: the per-student visibility policy enforced inside Postgres. A student's rows are invisible to any transaction not scoped to that student ([ADR-0007](../adr/0007-per-student-isolation-via-rls.md)).
- **Owner role vs app role**: two DB roles with two trust levels. The owner (`postgres`) bypasses RLS - migration/ingestion/review only. The app role (`tal_app`) is subject to RLS - all request-scoped work. Connecting requests through the owner would silently void isolation ([ADR-0007](../adr/0007-per-student-isolation-via-rls.md)).
- **extraction_audit**: the write-path ledger - one durable row per extracted fact recording what reconcile decided, the matched fact, the judge's verdict and reason, and the human review status. The FCA-grade answer to "why does the system believe X about this student".
- **retrieval_audit**: the read-path ledger - one row per answer with the question, the answer, and the exact ranking snapshot it used (captured at answer time, since rankings drift). The answer to "why did it say Y".
- **Store-invariant monitor**: an offline check that judges the STORE, not answers - no student may hold two live current-state facts that contradict. Catches latent corruption behavioral evals miss.
- **Golden set**: independent ground-truth eval cases (a human's claims, not the model's own output) used to score recall, supersede, and groundedness. Derived mechanically from a generated world model so the pipeline never sees its own answer key.
- **Knowledge base (planned)**: classic RAG over shared course content (`documents` / `document_chunks`) - chunked passages, not per-student facts, no RLS. The second "home", deliberately separate from personal memory and out of trial scope ([ADR-0001](../adr/0001-two-homes-memory-vs-knowledge-base.md)).
- **Embedding**: text turned into a ~1536-float vector so texts with similar meaning land near each other. The pgvector column type backs meaning-based search.

Config-and-source entities: **raw_reports** (report), **conversations** + **messages** (conversation).
Curated store: **memories** (facts).
Audit ledgers: **extraction_audit** (write path), **retrieval_audit** (read path).
Planned (knowledge base, out of trial scope): **documents**, **document_chunks**.
