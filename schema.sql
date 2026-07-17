-- TAL-style memory layer: schema
-- Design goals: provenance, versioning (never overwrite), per-student isolation via RLS.
--
-- The COACH's personal memory = curated FACTS distilled from a student's reports
-- AND conversations. Raw conversation is the append-only source of truth; it is
-- EXTRACTED into facts (same write path as reports), never retrieved raw into a
-- prompt. That is the memory-layer thesis. Classic chunk-and-retrieve RAG belongs
-- to the separate shared KNOWLEDGE BASE (course content), not here.

CREATE EXTENSION IF NOT EXISTS vector;

-- external_id: the record's identity in the SOURCE SYSTEM (file fixture id,
-- legacy DB key, CRM id...). UNIQUE makes bulk import idempotent by structure:
-- re-importing the same source is ON CONFLICT DO NOTHING, not duplication.
-- NULL for rows born here (demo seed) rather than imported.
CREATE TABLE students (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id text UNIQUE,
    name        text NOT NULL
);

-- Legacy source: personality reports. Migrated into facts.
CREATE TABLE raw_reports (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id text UNIQUE,
    student_id  uuid NOT NULL REFERENCES students(id),
    content     text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    migrated_at timestamptz   -- ledger: NULL = not yet migrated (idempotency proof)
);

-- Raw conversation log (append-only source of truth). Ingested into facts via
-- the SAME extraction path as reports - this is how the semantic memory GROWS
-- from talking, which is the product's whole premise.
CREATE TABLE conversations (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id text UNIQUE,
    student_id  uuid NOT NULL REFERENCES students(id),
    started_at  timestamptz NOT NULL DEFAULT now(),
    ingested_at timestamptz   -- ledger: NULL = not yet distilled into facts
);

CREATE TABLE messages (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL REFERENCES conversations(id),
    student_id      uuid NOT NULL REFERENCES students(id),  -- denormalized for RLS
    role            text NOT NULL,                          -- 'student' | 'coach'
    content         text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- The memory layer: extracted, typed, dated facts. Provenance points to the
-- source a fact came from - EITHER a report OR a conversation (exactly one set).
CREATE TABLE memories (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id             uuid NOT NULL REFERENCES students(id),
    kind                   text NOT NULL,  -- trait | goal | struggle | event | preference
    content                text NOT NULL,
    embedding              vector(1536) NOT NULL,          -- text-embedding-3-small
    importance             real NOT NULL DEFAULT 0.5,
    source_report_id       uuid REFERENCES raw_reports(id),    -- provenance (report)
    source_conversation_id uuid REFERENCES conversations(id),  -- provenance (conversation)
    -- EVENT time: the source document's own date, NOT processing time. Supersede
    -- direction compares THIS - so re-ingesting an old source can never overturn
    -- facts from a newer one, regardless of processing order across the two
    -- write paths (reports and conversations have independent ledgers).
    source_created_at      timestamptz NOT NULL DEFAULT now(),
    superseded_by          uuid REFERENCES memories(id),       -- versioning: never deleted
    created_at             timestamptz NOT NULL DEFAULT now(),
    last_confirmed_at      timestamptz NOT NULL DEFAULT now()
);

-- Human-review audit trail: one row per extracted fact per write-path run,
-- recording WHAT reconcile decided and WHY (the judge's verdict), durable -
-- unlike the DEBUG log lines, which die with the process. This is (a) the
-- substrate for the /review UI where a human approves or flags each decision
-- during migration, and (b) the FCA-style audit answer to "why does the
-- system believe X about this student". Review verdicts double as human
-- LABELS: flagged rows feed the golden set and judge validation.
CREATE TABLE extraction_audit (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id             uuid NOT NULL REFERENCES students(id),
    source_report_id       uuid REFERENCES raw_reports(id),
    source_conversation_id uuid REFERENCES conversations(id),
    fact_kind              text NOT NULL,
    fact_content           text NOT NULL,   -- what the extractor produced (kept even
                                            -- when the action stored nothing, e.g. confirm)
    action                 text NOT NULL,   -- insert | confirm | supersede | archive
    memory_id              uuid REFERENCES memories(id),  -- row written/confirmed
    matched_memory_id      uuid REFERENCES memories(id),  -- live fact it was judged against
    matched_content        text,            -- denormalized: the matched fact AS IT READ at
                                            -- decision time (it may be superseded later)
    judge_relation         text,            -- same | evolved | unrelated; NULL = no candidates
    judge_reason           text,
    nearest_distance       real,            -- nearest candidate's cosine distance
    created_at             timestamptz NOT NULL DEFAULT now(),
    review_status          text NOT NULL DEFAULT 'pending',  -- pending | approved | flagged
    review_note            text,
    reviewed_at            timestamptz
);

-- Read-path audit: what the coach SAID and which memories it leaned on.
-- The write audit answers "why does the system believe X"; this answers
-- "why did it say Y to the student last Tuesday" - the user-facing action,
-- which is what an incident review or a regulator asks about first.
-- `retrieved` is a RANKING SNAPSHOT (memory id, kind, content, score, rank):
-- rankings drift as the store evolves, so yesterday's top-5 cannot be
-- reconstructed from today's store - it must be captured at answer time.
-- Volume note: writes are per-request (unlike the batch write audit) - in
-- production this table gets a retention/archival policy and review works by
-- sampling; the LOG itself stays complete (a partial audit is not an audit).
CREATE TABLE retrieval_audit (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id  uuid NOT NULL REFERENCES students(id),
    question    text NOT NULL,
    answer      text NOT NULL,
    retrieved   jsonb NOT NULL,   -- [{memory_id, kind, content, score, rank}]
    model       text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    -- FLAG-ONLY review (no approve): an endless stream never "completes", so
    -- there is no pending/approved state - operators just mark bad answers
    -- they spot. A flagged answer + its snapshot + the note is 90% of an
    -- eval case; flags feed /review/labels like the write-path ones.
    flagged_at  timestamptz,
    review_note text
);

CREATE INDEX idx_memories_student ON memories (student_id);
CREATE INDEX idx_messages_conversation ON messages (conversation_id);
CREATE INDEX idx_audit_review ON extraction_audit (review_status, created_at);
CREATE INDEX idx_retrieval_audit_student ON retrieval_audit (student_id, created_at);
-- Deliberately NO ANN (HNSW) index: within one student's rows an exact scan
-- beats ANN. HNSW appears later only for the shared knowledge base (millions of
-- vectors), never for per-student memory.

-- ---------------------------------------------------------------------------
-- Row-Level Security: one student's data can never surface in another's session.
-- ---------------------------------------------------------------------------
ALTER TABLE raw_reports      ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations    ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages         ENABLE ROW LEVEL SECURITY;
ALTER TABLE memories         ENABLE ROW LEVEL SECURITY;
-- The audit table holds student data too - RLS on for defense in depth, even
-- though only the operator (owner role) reads it today. No tal_app grant:
-- review is an operator tool, students never see it.
ALTER TABLE extraction_audit ENABLE ROW LEVEL SECURITY;
CREATE POLICY per_student_audit ON extraction_audit
    USING (student_id = current_setting('app.student_id')::uuid);
-- The read-path audit is WRITTEN inside the request's student-scoped
-- transaction (tal_app role). With no WITH CHECK clause, Postgres applies
-- the USING expression to INSERTs too - so a request can only ever audit
-- rows for the student its transaction is scoped to. Isolation holds for
-- audit data exactly as it does for memories.
ALTER TABLE retrieval_audit ENABLE ROW LEVEL SECURITY;
CREATE POLICY per_student_retrieval_audit ON retrieval_audit
    USING (student_id = current_setting('app.student_id')::uuid);

CREATE POLICY per_student_reports ON raw_reports
    USING (student_id = current_setting('app.student_id')::uuid);
CREATE POLICY per_student_conversations ON conversations
    USING (student_id = current_setting('app.student_id')::uuid);
CREATE POLICY per_student_messages ON messages
    USING (student_id = current_setting('app.student_id')::uuid);
CREATE POLICY per_student_memories ON memories
    USING (student_id = current_setting('app.student_id')::uuid);

-- CRITICAL: the table OWNER (postgres) bypasses RLS. The app connects as a
-- non-owner role, or the whole isolation story silently evaporates.
CREATE ROLE tal_app LOGIN PASSWORD 'tal_app';
GRANT USAGE ON SCHEMA public TO tal_app;
GRANT SELECT ON students TO tal_app;
GRANT SELECT ON conversations, messages TO tal_app;
GRANT SELECT, INSERT, UPDATE ON raw_reports, memories TO tal_app;
GRANT INSERT ON retrieval_audit TO tal_app;  -- append-only from the app's side:
-- no SELECT/UPDATE/DELETE - requests write the ledger, only operators read it.

-- ---------------------------------------------------------------------------
-- Seed: two students with reports (fixed UUIDs so demo queries are copy-pasteable).
-- ---------------------------------------------------------------------------
INSERT INTO students (id, name) VALUES
    ('11111111-1111-1111-1111-111111111111', 'Alice'),
    ('22222222-2222-2222-2222-222222222222', 'Bob');

-- Explicit dates: /migrate processes reports oldest-first so a later report's
-- facts supersede an earlier one, never the reverse. Bob has TWO reports - the
-- follow-up demonstrates supersede ("panics on losses" -> "no longer panics").
INSERT INTO raw_reports (student_id, content, created_at) VALUES
    ('11111111-1111-1111-1111-111111111111',
     'Personality assessment: Alice is on the Quick Win program with Simon Pullen, '
     'working through the M&W module. She is highly disciplined and follows her trading plan. '
     'She is risk-averse and tends to exit winning positions too early. '
     'Goal: build confidence to let winners run. Scores: discipline 9/10, risk tolerance 3/10.',
     now() - interval '120 days'),
    ('22222222-2222-2222-2222-222222222222',
     'Personality assessment: Bob is studying the Legacy program with Sid Naiman and '
     'backtesting the Reversal Method. He is impulsive under pressure and panics on losses, '
     'often revenge-trading after a losing day. Strong analytical skills. '
     'Goal: develop emotional control and a stop-loss routine. Scores: discipline 4/10, risk tolerance 8/10.',
     now() - interval '120 days'),
    ('22222222-2222-2222-2222-222222222222',
     'Follow-up assessment: Bob has built a consistent stop-loss routine and no longer '
     'panics when a trade goes against him. Still impulsive when overtired. '
     'Goal: hold the routine through a full losing week. Scores: discipline 7/10, risk tolerance 7/10.',
     now() - interval '10 days');

-- Alice conversation: contains specifics (an NVDA trade, the anxiety mechanism,
-- a trailing-stop idea) NOT in her static report. /ingest-conversations distills
-- these into NEW facts - proving the memory grows from talking, not just the quiz.
INSERT INTO conversations (id, student_id, started_at) VALUES
    ('aaaa1111-0000-0000-0000-000000000001',
     '11111111-1111-1111-1111-111111111111',
     now() - interval '30 days');

INSERT INTO messages (conversation_id, student_id, role, content, created_at) VALUES
    ('aaaa1111-0000-0000-0000-000000000001','11111111-1111-1111-1111-111111111111','student',
     'I sold my NVDA position for a small gain this morning, then watched it run another 6% without me. Gutted.',
     now() - interval '30 days' + interval '0 minute'),
    ('aaaa1111-0000-0000-0000-000000000001','11111111-1111-1111-1111-111111111111','coach',
     'That sounds frustrating. What was going through your mind right before you sold?',
     now() - interval '30 days' + interval '1 minute'),
    ('aaaa1111-0000-0000-0000-000000000001','11111111-1111-1111-1111-111111111111','student',
     'I got anxious the gain would vanish. I would rather lock in something small than risk giving it back.',
     now() - interval '30 days' + interval '2 minute'),
    ('aaaa1111-0000-0000-0000-000000000001','11111111-1111-1111-1111-111111111111','coach',
     'So fear of giving back a profit pulled you out early. Is that a pattern you notice?',
     now() - interval '30 days' + interval '3 minute'),
    ('aaaa1111-0000-0000-0000-000000000001','11111111-1111-1111-1111-111111111111','student',
     'Yeah. I almost always sell winners too soon. My losers I hold too long, hoping they come back.',
     now() - interval '30 days' + interval '4 minute'),
    ('aaaa1111-0000-0000-0000-000000000001','11111111-1111-1111-1111-111111111111','coach',
     'That asymmetry - cutting winners early, holding losers - is worth working on. What would letting a winner run look like next time?',
     now() - interval '30 days' + interval '5 minute'),
    ('aaaa1111-0000-0000-0000-000000000001','11111111-1111-1111-1111-111111111111','student',
     'Maybe a trailing stop instead of a fixed target, so I do not have to decide in the moment.',
     now() - interval '30 days' + interval '6 minute'),
    ('aaaa1111-0000-0000-0000-000000000001','11111111-1111-1111-1111-111111111111','coach',
     'A trailing stop takes the in-the-moment anxiety out of it. Let us try that on your next trade and review how it felt.',
     now() - interval '30 days' + interval '7 minute');

-- Bob ADVERSARIAL conversation: dated BETWEEN his two reports. It describes the
-- OLD Bob (panics, pulls his stops) but is ingested AFTER report migration -
-- the out-of-order case that leaked stale facts live on 2026-07-10 under the
-- pure-cosine reconcile. Correct behavior: reconcile judges these facts as
-- EVOLVED-and-OLDER than the follow-up report's live facts and ARCHIVES them
-- (born superseded). evals/store_invariants.py is the regression gate: it must
-- report zero contradicting live facts after the full migrate + ingest run.
INSERT INTO conversations (id, student_id, started_at) VALUES
    ('bbbb2222-0000-0000-0000-000000000001',
     '22222222-2222-2222-2222-222222222222',
     now() - interval '60 days');

INSERT INTO messages (conversation_id, student_id, role, content, created_at) VALUES
    ('bbbb2222-0000-0000-0000-000000000001','22222222-2222-2222-2222-222222222222','student',
     'Rough day. My trade went against me right after entry and I froze, then panic-sold at the worst point.',
     now() - interval '60 days' + interval '0 minute'),
    ('bbbb2222-0000-0000-0000-000000000001','22222222-2222-2222-2222-222222222222','coach',
     'What happens for you in that moment when a trade turns against you?',
     now() - interval '60 days' + interval '1 minute'),
    ('bbbb2222-0000-0000-0000-000000000001','22222222-2222-2222-2222-222222222222','student',
     'My heart races and I stop thinking. After I sell I get angry and jump straight into another trade to win it back.',
     now() - interval '60 days' + interval '2 minute'),
    ('bbbb2222-0000-0000-0000-000000000001','22222222-2222-2222-2222-222222222222','coach',
     'That is the revenge cycle. Do you set a stop-loss when you enter?',
     now() - interval '60 days' + interval '3 minute'),
    ('bbbb2222-0000-0000-0000-000000000001','22222222-2222-2222-2222-222222222222','student',
     'I set one, but I keep pulling it mid-trade because I do not trust it.',
     now() - interval '60 days' + interval '4 minute'),
    ('bbbb2222-0000-0000-0000-000000000001','22222222-2222-2222-2222-222222222222','coach',
     'Then the routine is the work: set the stop at entry and leave it alone. We will review it next session.',
     now() - interval '60 days' + interval '5 minute');
