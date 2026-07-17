"""The read path: question -> relevant memories -> grounded answer.

Deliberately NOT a LangGraph graph: this is a linear, stateless,
request-scoped flow (embed, one query, one LLM call, return). A graph is
justified by state, branching or resumability - not by the presence of an
LLM call.
"""

import logging

from pgvector import Vector
from psycopg.types.json import Jsonb

from app import config
from app.ai import embedder, llm
from app.db import student_conn
from app.models import AskResponse, MemoryOut

log = logging.getLogger(__name__)

TOP_K = 5  # the hard budget: TAL sees at most this many memories per question

TAL_SYSTEM_PROMPT = """You are TAL, a supportive trading-psychology coach.
Answer the student's question in 2-4 sentences, warm and direct.

HARD RULE: base your answer ONLY on the facts provided about this student.
If the facts do not cover the question, say plainly that you do not know
that about them yet. Never invent a memory."""


def retrieve_memories(student_id: str, question: str, k: int = TOP_K) -> list[MemoryOut]:
    """Rank = similarity x recency, top-k, superseded excluded.

    Note what is ABSENT: no `WHERE student_id = ...`. The RLS policy supplies
    it - student_conn() has already scoped this transaction to one student.
    (In production you would add the WHERE anyway, as defense in depth; it is
    left out here to prove where the enforcement actually lives.)

    Also absent: any vector index. Within one student's few hundred rows an
    exact scan is faster and simpler than ANN - a deliberate non-decision.
    """
    qvec = embedder.embed_query(question)

    with student_conn(student_id) as conn:
        # similarity and recency are surfaced SEPARATELY, not just their
        # product: a low score can mean "unrelated" (low similarity) or
        # "related but stale" (low recency) - two different diagnoses. The
        # /chat and /review/answers tables show both so you can tell which.
        rows = conn.execute(
            """
            SELECT id, kind, content, created_at,
                   similarity, recency, similarity * recency AS score
            FROM (
                SELECT id, kind, content, created_at,
                       1 - (embedding <=> %s) AS similarity,
                       1.0 / (1.0 + EXTRACT(EPOCH FROM (now() - last_confirmed_at))
                                    / 86400.0 / 30.0) AS recency
                FROM memories
                WHERE superseded_by IS NULL
            ) ranked
            ORDER BY score DESC
            LIMIT %s
            """,
            (Vector(qvec), k),
        ).fetchall()

    out = [
        MemoryOut(id=r[0], kind=r[1], content=r[2], created_at=r[3],
                  similarity=round(r[4], 4), recency=round(r[5], 4),
                  score=round(r[6], 4))
        for r in rows
    ]
    # top_score is the retrieval-quality signal a recall eval later checks
    # against a golden set. Logging it per request makes "why did TAL feel
    # dumb here" answerable from the logs, not a guess.
    top_score = out[0].score if out else 0.0
    log.info("retrieve: student=%s k=%d -> %d memories (top_score=%.3f)",
             student_id, k, len(out), top_score)
    return out


def _audit_answer(student_id: str, question: str, answer: str,
                  memories: list[MemoryOut]) -> None:
    """The read-path ledger: what was asked, what was retrieved (with scores
    and ranks - a RANKING SNAPSHOT that cannot be reconstructed later, since
    rankings drift as the store evolves), and what the coach answered.

    Written as tal_app inside a student-scoped transaction - the RLS policy's
    WITH CHECK means a request can only audit its own student. Runs after the
    response is composed; in production this would share the request's
    transaction (or go async) rather than open its own."""
    snapshot = [
        {"memory_id": str(m.id), "kind": m.kind, "content": m.content,
         "similarity": m.similarity, "recency": m.recency,
         "score": m.score, "rank": rank}
        for rank, m in enumerate(memories, 1)
    ]
    with student_conn(student_id) as conn:
        conn.execute(
            "INSERT INTO retrieval_audit "
            "(student_id, question, answer, retrieved, model) "
            "VALUES (%s, %s, %s, %s, %s)",
            (student_id, question, answer, Jsonb(snapshot), config.OPENAI_MODEL),
        )


def answer_question(student_id: str, question: str) -> AskResponse:
    """RAG over the memory layer: retrieved curated FACTS go into the prompt,
    nothing else. Raw conversation is never retrieved here - it is distilled into
    facts by the extraction write path. That is the memory-layer thesis; classic
    chunk-and-retrieve lives in the separate knowledge base."""
    memories = retrieve_memories(student_id, question)

    if not memories:
        # Not an error - a correct "I don't know that about you yet". Logged at
        # WARNING because a spike of these is the signal that migration/ingestion
        # under-populated memory, which is exactly what a QA pass wants to catch.
        log.warning("ask: no memories for student=%s, question=%r", student_id, question)
        answer = "I don't have any memories about you yet - let's talk and I'll learn."
        # Refusals are audited too - a spike of empty retrievals IS a finding.
        _audit_answer(student_id, question, answer, [])
        return AskResponse(answer=answer, memories_used=[])

    facts_block = "\n".join(
        f"- [{m.kind}] {m.content} (recorded {m.created_at:%Y-%m-%d})" for m in memories
    )
    answer = str(llm.invoke(
        [
            ("system", TAL_SYSTEM_PROMPT),
            ("user", f"Facts about this student:\n{facts_block}\n\nStudent's question: {question}"),
        ]
    ).content)

    _audit_answer(student_id, question, answer, memories)
    return AskResponse(answer=answer, memories_used=memories)
