# System design (C4 L2 + L3) - containers, runtime flows, and components

The container view of the TAL memory system (C4 L2) plus the component decomposition inside the app
process (C4 L3): the separately runnable units, the protocols between them, the highest-judgment
runtime flows, and the components that make up the one Python process. The L1 system context is in
[system-context.md](system-context.md); the data model in [domain-model.md](domain-model.md);
cross-cutting concerns in [cross-cutting.md](cross-cutting.md). Governing decisions:
[ADR-0004](../adr/0004-supersede-direction-by-source-date.md) (supersede by source date),
[ADR-0005](../adr/0005-reconcile-by-joint-judge.md) (joint-judge reconcile + validator),
[ADR-0006](../adr/0006-idempotency-by-ledger.md) (ledgers),
[ADR-0007](../adr/0007-per-student-isolation-via-rls.md) (RLS + two roles),
[ADR-0008](../adr/0008-no-ann-index-on-personal-memory.md) (no ANN index),
[ADR-0009](../adr/0009-langgraph-write-path-only.md) (LangGraph on the write path only). Re-sliced by
scope from [../../README.md](../../README.md).

## Containers

The separately runnable and deployable units inside (and at the edge of) the TAL boundary, and the
protocols between them. The test for a container here is "something that has to be running for the
system to work" - a process or a datastore - not a code grouping inside one of them (that is L3).

The web API, the operator UIs, and the write-path jobs are three ROLES of a SINGLE Python process:
the demo runs the distill/assess jobs in-process behind a thread-backed jobs facade, so they are ONE
container, annotated with all three responsibilities. Internal units are blue; external systems we
depend on but do not control are grey; the optional tracing edge is dashed. A legend follows.

```mermaid
flowchart TB
    student["Student - chat / voice<br/>[Container: client]"]
    operator["Operator - browser / CLI<br/>[Container: client]"]

    app["TAL memory app<br/>[Container: Python 3.12 / FastAPI on uvicorn]<br/>serves the read-path API (/ask) + operator UIs (/wizard, /review, /chat) + front door<br/>AND runs the write-path jobs in-process (migrate, ingest, assess) via a thread-backed jobs facade<br/>connects to Postgres under TWO roles: owner (jobs, bypasses RLS) and tal_app (requests, RLS)"]

    db[("Postgres 17 + pgvector<br/>[Container: datastore]<br/>raw log + curated facts + audit ledgers<br/>per-student RLS lives here")]
    llm["LLM provider<br/>[External System]<br/>chat + embeddings, e.g. OpenAI"]
    trace["LangSmith<br/>[External System - optional]"]

    student -->|"HTTPS - ask"| app
    operator -->|"HTTPS - import / migrate / ingest / review / evals"| app
    app -->|"app reads/writes (tal_app role, RLS, Postgres wire)"| db
    app -->|"migration reads/writes (owner role, bypasses RLS)"| db
    app -->|"extract / embed / ground (HTTPS, carries PII)"| llm
    app -.->|"per-node traces (HTTPS, when LANGSMITH_* set)"| trace

    classDef internal fill:#cfe3ff,stroke:#4a78b5,color:#10243e;
    classDef external fill:#ececec,stroke:#9a9a9a,color:#1f1f1f;
    classDef optional stroke-dasharray: 5 5;
    class student,operator,app,db internal;
    class llm,trace external;
    class trace optional;
```

**Legend.**

```mermaid
flowchart LR
    Li["Internal container"]
    Lx["External system"]
    Ld[("Our datastore")]
    Ra[" "] -->|"required"| Rb[" "]
    Oa[" "] -.->|"optional"| Ob[" "]
    classDef internal fill:#cfe3ff,stroke:#4a78b5,color:#10243e;
    classDef external fill:#ececec,stroke:#9a9a9a,color:#1f1f1f;
    class Li,Ld internal;
    class Lx external;
```

A **solid** arrow is an always-present relationship; a **dashed** arrow is optional (tracing).
**Rectangle** = process/container, **cylinder** = datastore. **Blue** = internal (including our own
managed datastore), **grey** = external system we integrate with but do not own. Arrows point from
caller to dependency; the response is implied.

Container by container, and why each qualifies:
- **TAL memory app**: one Python process (`uvicorn app.main:app`). It is a single container because the read-path requests, the operator UIs, and the write-path jobs all run in the same process; the jobs facade (`app/jobs.py`) runs distill/assess on a background thread, not a separate process. **Peel-safety**: the no-rewrite peel of the write-path jobs onto their own process (or a task queue) holds only while requests and jobs share state solely through Postgres - the demo's jobs facade is a thread today, `Production = task queue` is named in the code. The two DB roles are the trust boundary drawn INTO this one container: `owner_conn()` (bypasses RLS, migration/ingestion/review only) and `student_conn()` (RLS enforced, request-scoped) - routing a request through the owner role would silently void isolation ([ADR-0007](../adr/0007-per-student-isolation-via-rls.md)).
- **Postgres + pgvector**: the datastore container; one instance holds the raw log, the curated `memories`, and both audit ledgers. It is internal (we own the schema), managed-hosted in production. Per-student RLS is enforced HERE, in the database, not in application code - so a forgotten `WHERE student_id = ...` cannot leak data ([ADR-0007](../adr/0007-per-student-isolation-via-rls.md)). Deliberately NO ANN/HNSW index on personal memory: within one student's few-hundred rows an exact scan is faster and exact ([ADR-0008](../adr/0008-no-ann-index-on-personal-memory.md)).
- **LLM provider** (external): chat + embeddings. Every fact extraction, every embedding, and every grounded answer crosses this edge carrying student PII. The reconcile judge runs on a STRONGER model than the extraction/answer workhorse - the highest-stakes call, offline, one per source ([ADR-0005](../adr/0005-reconcile-by-joint-judge.md)). The natural seam is an `LLMProvider` port; today it is the `app/ai.py` client.
- **LangSmith** (external, optional): captures every graph node and LLM call as a nested trace when env-enabled, with no app-code change. For FCA data this endpoint is a residency decision (self-host) - [cross-cutting.md](cross-cutting.md).

## Key runtime flows

The highest-judgment flows, all consistent with the [L1 boundary flow](system-context.md#boundary-runtime-flow)
and the [fact lifecycle](domain-model.md#fact-lifecycle-the-core-entity). Because the web and jobs are
one container, the app appears once and a note marks when it is acting in its in-process job capacity.

### Write path - distill a source into facts (migrate / ingest)

Both sources share one extraction graph. Idempotent via the ledgers: a second run skips already-distilled
sources ([ADR-0006](../adr/0006-idempotency-by-ledger.md)).

```mermaid
sequenceDiagram
    actor Op as Operator
    participant App as TAL app
    participant G as Extraction graph (LangGraph)
    participant LLM as LLM provider
    participant DB as Postgres (owner role)

    Op->>App: POST /migrate (or /ingest-conversations)
    App->>DB: select sources WHERE ledger IS NULL
    loop each pending source
        App->>G: run_extraction(source text, provenance)
        G->>LLM: extract typed facts (structured output, name injected)
        G->>LLM: embed all facts (one batched call)
        Note over G,DB: reconcile - wide cosine net, ONE joint judge call, plan validator (below)
        G->>DB: apply plan - insert / confirm / supersede / archive, write audit rows
        App->>DB: stamp the ledger (migrated_at / ingested_at)
    end
    App-->>Op: facts added / confirmed / superseded / archived
```

### Reconcile internals - from N facts to a coherent plan

Where most of the write-path judgment lives. Cosine nominates candidates; a single joint judge decides
the set; code enforces write-write coherence ([ADR-0005](../adr/0005-reconcile-by-joint-judge.md)).

```mermaid
flowchart TB
    F["N extracted facts"] --> E["embed all N (one batched call)"]
    E --> W["wide cosine net PER fact<br/>top-5 live facts under distance 0.60, across ALL kinds"]
    W --> U["UNION of candidates (dedup by id)"]
    U --> J["ONE joint judge call (stronger model)<br/>all N new facts + all candidates, decide the SET"]
    J --> M["map each relation to an action<br/>new -> insert, dup-existing -> confirm,<br/>evolution -> supersede or archive by date, dup-sibling -> skip"]
    M --> V["validate_plan (deterministic code)<br/>at most one supersede per target,<br/>no confirm of a fact superseded this batch"]
    V --> P["final plan -> store node"]
```

Cosine is blind to negation ("panics" vs "no longer panics" = 0.49 apart) and to numbers ("score 8/10"
vs "7/10" = 0.012 apart), both measured live - so the net only shortlists; the judge decides relations,
and dates decide the supersede DIRECTION ([ADR-0004](../adr/0004-supersede-direction-by-source-date.md)).
The judge call is JOINT (all new facts + all candidates at once) because per-fact judging against the
pre-batch snapshot produced a live lost update - two sibling facts matching the same live fact, one
superseding it and one confirming the now-dead fact; the confirmed claim vanished from live memory. The
same industry fix as Mem0's joint ADD/UPDATE/DELETE/NOOP call and Graphiti's new-vs-new dedupe, plus
`validate_plan` as a deterministic safety net (unit-tested in `tests/test_plan_coherence.py`).

### Read path - answer a question from memory

Linear, stateless, request-scoped - deliberately NOT a graph ([ADR-0009](../adr/0009-langgraph-write-path-only.md)).
RLS scopes the transaction to one student.

```mermaid
sequenceDiagram
    actor St as Student
    participant App as TAL app
    participant EMB as Embeddings
    participant DB as Postgres (tal_app role, RLS)
    participant LLM as LLM provider

    St->>App: GET /students/{id}/ask?q=...
    App->>EMB: embed the question
    App->>DB: BEGIN, SET LOCAL app.student_id
    App->>DB: SELECT live facts ORDER BY similarity x recency LIMIT k
    Note over DB: RLS makes only this student's rows visible - no WHERE needed
    DB-->>App: top-k live facts (superseded excluded)
    App->>LLM: prompt = facts + question, rule: use only these facts
    LLM-->>App: grounded answer (or an honest "I don't know that yet")
    App->>DB: write retrieval_audit (question, answer, ranking snapshot)
    App-->>St: answer + memories used (provenance)
```

## Components (C4 L3)

The component decomposition inside the single app process. Everything blue is a component of the one
container; grey is external. Components are grouped by their stable seam. A solid arrow is an in-process
dependency; a dashed arrow is an adapter implementing a port (the dependency-inversion direction); an
edge crossing to an external system carries its wire protocol. All cores read config via `config` and
emit logs/traces via `obs`; those ubiquitous edges are stated once here rather than drawn.

```mermaid
flowchart TB
    subgraph app["TAL memory app - one Python process"]
        direction TB

        subgraph web["HTTP surface - app.main + routers"]
            api["read-path API<br/>/ask, /memories, /health"]
            ops["operator UIs<br/>/wizard, /review, /chat"]
            front["front door<br/>/, /meet, /vision, /architecture"]
        end

        jobsf["jobs facade<br/>app/jobs.py - background thread (Production = task queue)"]

        subgraph writecores["Write-path cores"]
            imp["import service<br/>app/importer - DataSource protocol"]
            mig["migration<br/>app/migration.py"]
            ing["ingestion<br/>app/ingestion.py"]
            ext["extraction graph<br/>app/extraction.py - extract / reconcile / store"]
        end

        subgraph readcore["Read-path core"]
            ret["retrieval<br/>app/retrieval.py - rank + ground + audit"]
        end

        subgraph seams["Ports and adapters"]
            dsport["DataSource port"]
            dsfile["FileDataSource adapter"]
            ai["LLM + embeddings client<br/>app/ai.py (LLMProvider seam)"]
        end

        subgraph platform["Platform facades - shared"]
            db["db<br/>owner_conn / student_conn"]
            cfg["config<br/>app/config.py"]
            mdl["models (DTOs)<br/>app/models.py"]
            obs["obs<br/>logging + LangSmith"]
        end

        subgraph qa["Quality"]
            ev["evals<br/>golden set, recall/supersede/groundedness"]
            inv["store-invariant monitor<br/>evals/store_invariants.py"]
        end
    end

    PG[("Postgres + pgvector")]
    LLMX["LLM provider"]
    LS["LangSmith"]

    api --> ret
    ops --> jobsf
    ops --> db
    front --> ret
    front --> db
    jobsf --> mig
    jobsf --> ing
    mig --> ext
    ing --> ext
    imp --> dsport
    dsfile -.->|implements| dsport
    imp --> db
    ext --> ai
    ext --> db
    ret --> ai
    ret --> db
    ev --> ret
    inv --> db
    ai -->|HTTPS| LLMX
    db -->|"SQL, Postgres wire"| PG
    obs -.->|traces| LS

    classDef internal fill:#cfe3ff,stroke:#4a78b5,color:#10243e;
    classDef external fill:#ececec,stroke:#9a9a9a,color:#1f1f1f;
    classDef port fill:#d6f5e0,stroke:#3f9d6a,color:#10243e;
    classDef adapter fill:#fff0cc,stroke:#c79a3a,color:#3a2e10;
    class api,ops,front,jobsf,imp,mig,ing,ext,ret,ai,db,cfg,mdl,obs,ev,inv internal;
    class dsport port;
    class dsfile adapter;
    class PG,LLMX,LS external;
```

**Legend.**

```mermaid
flowchart LR
    Li["Internal component"]
    Lp["Port / seam"]
    La["Adapter"]
    Lx["External system"]
    A1[" "] -->|"in-process dependency"| A2[" "]
    B1[" "] -.->|"implements (DIP)"| B2[" "]
    classDef internal fill:#cfe3ff,stroke:#4a78b5,color:#10243e;
    classDef port fill:#d6f5e0,stroke:#3f9d6a,color:#10243e;
    classDef adapter fill:#fff0cc,stroke:#c79a3a,color:#3a2e10;
    classDef external fill:#ececec,stroke:#9a9a9a,color:#1f1f1f;
    class Li internal;
    class Lp port;
    class La adapter;
    class Lx external;
```

### Component catalog

| Component | Responsibility | Seam / port | Module |
|---|---|---|---|
| read-path API | `/ask` grounded answer + provenance; `/memories`; `/health` | reads via retrieval | `app/main.py` |
| operator UIs | Migration wizard, human review of write-path decisions, retrieval bench | owner role | `app/wizard.py`, `app/review.py`, `app/chat.py` |
| front door | Non-technical landing, the memory-evolution story, vision, this architecture view | reads via retrieval / db | `app/frontdoor.py`, `app/meet.py`, `app/architecture.py` |
| jobs facade | Run distill/assess on a background thread; progress read from the ledgers so a crash resumes | - | `app/jobs.py` |
| import service | Land raw student data from any `DataSource` into the raw tables, idempotent by `external_id`, one tx per student | `DataSource` port | `app/importer/` |
| migration | Reports -> facts; idempotent via `migrated_at` | calls extraction | `app/migration.py` |
| ingestion | Conversations -> facts (same extraction path); idempotent via `ingested_at` | calls extraction | `app/ingestion.py` |
| extraction graph | extract (LLM structured output) -> reconcile (cosine net + joint judge + validator) -> store (apply plan + audit) | `LLMProvider` seam | `app/extraction.py` |
| retrieval | Rank live facts by similarity x recency under top-k, ground the answer, write the read-path audit | `LLMProvider` seam | `app/retrieval.py` |
| DataSource port + FileDataSource | The import contract; the file adapter today, the client's export a second adapter with zero service change | port + adapter | `app/importer/` |
| LLM + embeddings client | The one place that talks to the LLM/embeddings vendor - the `LLMProvider` seam | seam | `app/ai.py` |
| db | Two connection factories, two trust levels: `owner_conn` (bypasses RLS) and `student_conn` (RLS, `SET LOCAL`) | - | `app/db.py` |
| config / models / obs | Env config; Pydantic DTOs incl. the extractor's structured-output schema; logging + LangSmith | - | `app/config.py`, `app/models.py`, `app/obs.py` |
| evals | Golden-set scorecard: recall / supersede (NLI-style judge) and groundedness | reads via retrieval | `evals/` |
| store-invariant monitor | Judge the STORE, not answers: no student holds two contradicting live facts | reads db | `evals/store_invariants.py` |

### Ports and adapters direction

The dependency-inversion seam: cores and ports MUST NOT import adapters; an adapter implements a port
and is bound to a source shape only at the composition edge. This is why the client's real export
becomes a NEW `DataSource` adapter file, not a change to the import service, the migration pipeline, or
any core. The `DataSource` protocol carries this today; the `LLMProvider` seam (`app/ai.py`) is the
same shape for the vendor and is the natural next port when a second provider lands.

### Two roles inside one process (the isolation boundary)

The trust boundary is drawn INTO the single container: `owner_conn()` bypasses RLS and is used only by
migration, ingestion, review, and seeding; `student_conn()` runs every request under the `tal_app` role
with `SET LOCAL app.student_id`, so RLS makes only that student's rows visible and the setting dies with
the transaction (pooler-safe). The read path deliberately has NO `WHERE student_id = ...` - the policy
supplies it, which is the point: isolation cannot depend on every query remembering the predicate
([ADR-0007](../adr/0007-per-student-isolation-via-rls.md)). `tests/test_isolation.py` asks one
student's question in another's session and asserts nothing leaks.
