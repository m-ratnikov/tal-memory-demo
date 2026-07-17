"""The write path: report text -> typed facts -> reconciled -> stored.

A LangGraph pipeline of three nodes. State is a typed dict flowing through;
each node returns a partial update that the runtime merges. Edges define order.

For a linear 3-step pipeline the graph is close to overkill, but it earns its
place when steps need retries, branching, mid-flow persistence (checkpointer)
or per-step observability. Used here because the pipeline's shape - extract,
reconcile, store - is the design, and the graph makes that shape explicit and
testable.
"""

import logging
from datetime import datetime
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from pgvector import Vector
from pydantic import BaseModel, Field

from app.ai import embedder, llm, reconcile_judge_llm
from app.db import owner_conn
from app.models import ExtractedFacts, Fact, StorePlan

# Per-step observability is the graph's main payoff over three bare function
# calls: these log lines show what each node decided and why.
log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Reconciliation = wide net + JOINT batch judge (evolved twice, empirically):
# 1) 2026-07-11: cosine stopped being the decision-maker. The pure-cosine
#    heuristic (nearest same-KIND fact, 0.40 cutoff) let an opposite-polarity
#    fact walk into the live store ("panics" vs "no longer panics" = 0.49;
#    cosine is blind to negation). Cosine now only shortlists CANDIDATES.
# 2) 2026-07-11 (later): per-fact judging replaced by ONE JOINT call per
#    source. Per-fact decisions are made against the pre-batch store snapshot,
#    so two sibling facts matching the same live fact produced a LOST UPDATE:
#    one superseded it while the other confirmed it - the confirmed claim
#    ended up with no live representation. Industry-standard shape (Mem0's
#    joint ADD/UPDATE/DELETE/NOOP call; Graphiti's new-vs-new dedupe before
#    new-vs-graph resolution): the judge sees ALL new facts + ALL candidate
#    live facts together and decides the SET coherently, including sibling
#    duplicates. A deterministic plan validator (validate_plan) then enforces
#    write-write coherence in code - LLM output is still just output.
# --------------------------------------------------------------------------
CANDIDATE_DISTANCE = 0.60  # wide net: anything nearer is WORTH A LOOK by the judge
CANDIDATE_LIMIT = 5        # judge sees at most this many nearest live facts
# There is deliberately NO "close enough = confirm" shortcut. Tried one at
# distance 0.10; it silently re-confirmed "discipline score of 4/10" when the
# follow-up said 7/10 - the two sentences differ by ONE DIGIT and cosine
# scored them 0.025 apart (8/10 vs 7/10 measured 0.012). Embeddings barely
# see numbers, so no distance threshold is safe as an auto-decision. Every
# fact with candidates goes to the judge; only judges decide.
NEVER_SUPERSEDE = {"event"}  # events are history; two events never contradict


class ExtractionState(TypedDict):
    """The state carried between nodes. Everything in it is serializable -
    that is what would let a checkpointer persist/resume this graph."""

    student_id: str
    student_name: str                   # goes into the extract prompt: facts say
                                        # "Bob...", never "The student..." - consistent
                                        # phrasing is what lets facts MATCH across sources
    source_text: str                    # a report OR a conversation transcript
    source_report_id: str | None        # provenance if the source is a report
    source_conversation_id: str | None  # provenance if the source is a conversation
    source_date: datetime               # the source's OWN date (event time) - decides
                                        # supersede direction, never processing order
    extracted: list[Fact]    # written by node 1
    plan: list[StorePlan]    # written by node 2
    added: int               # written by node 3
    confirmed: int
    superseded: int
    archived: int


EXTRACT_PROMPT = """You extract durable facts about a student from a coaching \
or personality report or a conversation transcript.

Rules:
- Each fact is ONE sentence stating ONE claim, third person, self-contained.
  Split compound statements ("built a routine and no longer panics") into
  separate facts.
- Refer to the student BY NAME (given with the source), never as "the student".
  Facts from different sources must phrase the subject the same way, or they
  will not match up when reconciled.
- kind is one of: trait (stable characteristic), goal (what they work toward),
  struggle (current difficulty), event (a ONE-TIME occurrence: a specific
  trade, a session, an incident - a change in the student's ongoing state is
  a trait/struggle, NOT an event), preference (how they like to interact or
  learn).
- importance reflects how central the fact is to coaching this student (0..1).
- Extract only what the source states. Do not infer or invent."""


def extract(state: ExtractionState) -> dict:
    """Node 1: LLM turns prose into typed facts.

    with_structured_output(ExtractedFacts) sends the Pydantic schema to the
    model as its output contract and validates the response against it; a
    response that does not fit fails validation and cannot reach our code.
    """
    extractor = llm.with_structured_output(ExtractedFacts)
    result = extractor.invoke(
        [
            ("system", EXTRACT_PROMPT),
            ("user",
             f"Student name: {state['student_name']}\n\n{state['source_text']}"),
        ]
    )
    source = state["source_report_id"] or state["source_conversation_id"]
    log.info(
        "extract: source=%s student=%s -> %d facts",
        source, state["student_id"], len(result.facts),
    )
    return {"extracted": result.facts}  # a delta: only the key this node owns


class FactDecision(BaseModel):
    """The judge's decision for ONE new fact, inside the joint batch verdict."""

    fact: int = Field(description="1-based number of the NEW fact this decides.")
    relation: Literal["new", "duplicate_of_existing", "evolution_of_existing",
                      "duplicate_of_sibling"]
    existing: int = Field(
        0, description="1-based number of the EXISTING fact, when the relation "
                       "targets one; 0 otherwise.")
    sibling: int = Field(
        0, description="1-based number of the sibling NEW fact that already "
                       "carries this claim; 0 unless duplicate_of_sibling.")
    reason: str = Field(description="One short sentence explaining the decision.")


class BatchReconcileVerdict(BaseModel):
    """Joint output: exactly one decision per new fact, decided as a SET."""

    decisions: list[FactDecision]


RECONCILE_PROMPT = """You reconcile ALL NEW facts extracted from ONE source \
against a student's EXISTING live memory facts. The decisions are applied \
together - decide the whole set COHERENTLY.

Per NEW fact, the relation is one of:
- new: nothing existing and no sibling covers this claim.
- duplicate_of_existing: an existing fact states the SAME claim (paraphrase;
  wording differs).
- evolution_of_existing: an existing fact covers the same aspect of the
  student but the statement CHANGED - an update or a contradiction, e.g.
  "panics when trades go against him" vs "no longer panics when a trade goes
  against him". A changed number or score on the SAME metric is an evolution
  ("discipline score of 4/10" vs "discipline score of 7/10").
- duplicate_of_sibling: another NEW fact in this batch already carries this
  claim. Mark the narrower/redundant one as the duplicate, keep the fuller one.

Coherence rules (the set is applied as one transaction):
- At most ONE new fact may be the evolution of a given existing fact. If
  several new facts touch the same existing fact, pick the best evolution;
  each other one is either duplicate_of_sibling or its own new claim.
- Never mark a fact duplicate_of_existing when a sibling EVOLVES that same
  existing fact: the evolution replaces the old fact, so this claim must
  survive on its own (new) or in the sibling (duplicate_of_sibling).

Judgment rules:
- A NEW fact that CONTRADICTS an existing fact - opposite polarity on the
  same specific claim ("panics when trades go against him" vs "no longer
  panics"; "pulls his stop-loss mid-trade" vs "has a consistent stop-loss
  routine") - is ALWAYS evolution_of_existing, never new, even when the new
  fact adds extra detail. The pipeline resolves which side wins by source
  dates; calling it new would leave both sides live and contradicting.
- The student's REACTION TO THE SAME TRIGGER is one aspect: panicking,
  freezing, or panic-selling when a trade goes against him are all his
  reaction-to-adverse-trade state. If an existing fact says that reaction
  changed ("no longer panics when a trade goes against him"), a new fact
  describing any form of the old reaction is an evolution of it, not new.
- DIFFERENT AXES are unrelated: a score/metric vs a behavior, or two distinct
  weaknesses with different triggers, do not evolve each other.
- A goal about a topic does not evolve a struggle on that topic - they coexist.
- Events are one-time history; an event never evolves anything.
- When unsure between new and a merge, say new: an extra insert is visible
  and recoverable, while a wrong merge silently corrupts the store. But a
  CONTRADICTION is never "new" - see the first rule."""


def judge_batch(facts: list[Fact], existing: list[tuple]) -> list[FactDecision]:
    """ONE joint judge call per source: all new facts + all candidate live
    facts in a single prompt (the Mem0-style coherent-set decision).

    Returns exactly one decision per fact, in fact order - the model's output
    is normalized here: a missing decision defaults to "new" (fail-safe: an
    extra insert is visible; a wrong merge is silent), duplicates are dropped."""
    new_listing = "\n".join(
        f"{i}. [{f.kind}] {f.content}" for i, f in enumerate(facts, 1)
    )
    existing_listing = "\n".join(
        f"{i}. [{kind}] (source dated {date:%Y-%m-%d}) {content}"
        for i, (_id, kind, content, date, _dist) in enumerate(existing, 1)
    ) or "(none)"
    verdict = reconcile_judge_llm.with_structured_output(BatchReconcileVerdict).invoke(
        [
            ("system", RECONCILE_PROMPT),
            ("user",
             f"NEW facts:\n{new_listing}\n\n"
             f"EXISTING live facts:\n{existing_listing}"),
        ]
    )
    assert isinstance(verdict, BatchReconcileVerdict)

    by_fact: dict[int, FactDecision] = {}
    for d in verdict.decisions:
        if 1 <= d.fact <= len(facts) and d.fact not in by_fact:
            by_fact[d.fact] = d
    missing = [i for i in range(1, len(facts) + 1) if i not in by_fact]
    if missing:
        log.warning("reconcile judge: no decision for fact(s) %s -> new", missing)
    return [
        by_fact.get(i)
        or FactDecision(fact=i, relation="new", reason="judge omitted -> new")
        for i in range(1, len(facts) + 1)
    ]


def decide(decision: FactDecision, fact: Fact, facts: list[Fact],
           decisions: list[FactDecision], existing: list[tuple],
           source_date: datetime) -> dict:
    """Map one judge decision to StorePlan kwargs (action, existing_id + the
    WHY fields that land in extraction_audit). Pure function - unit-testable
    without a DB or an LLM. Any decision pointing outside its list is a judge
    glitch and fails SAFE to insert."""
    base = {"judge_relation": decision.relation, "judge_reason": decision.reason}

    if decision.relation == "duplicate_of_sibling":
        s = decision.sibling
        valid = 1 <= s <= len(facts) and s != decision.fact \
            and decisions[s - 1].relation != "duplicate_of_sibling"  # no chains
        if not valid:
            return {"action": "insert", **base}
        # Nothing stored - the claim lives in the sibling. Audit still records it.
        return {"action": "skip", "matched_content": facts[s - 1].content, **base}

    if decision.relation in ("duplicate_of_existing", "evolution_of_existing"):
        if not (1 <= decision.existing <= len(existing)):
            return {"action": "insert", **base}
        row = existing[decision.existing - 1]
        base["matched_content"] = row[2]
        if decision.relation == "duplicate_of_existing":
            return {"action": "confirm", "existing_id": str(row[0]), **base}

        # evolution: events are history on EITHER side - never versioned.
        if fact.kind in NEVER_SUPERSEDE or row[1] in NEVER_SUPERSEDE:
            return {"action": "insert", **base}
        # Direction = SOURCE dates, never processing order: only a strictly
        # newer source may overturn the live fact; an older source's fact is
        # archived (born superseded), so re-ingests never resurrect stale claims.
        if source_date > row[3]:
            return {"action": "supersede", "existing_id": str(row[0]), **base}
        return {"action": "archive", "existing_id": str(row[0]), **base}

    return {"action": "insert", **base}


def validate_plan(plan: list[StorePlan]) -> list[StorePlan]:
    """DB-style write-write conflict check over the finished plan.

    The judge is ASKED to be coherent, but LLM output is still just output -
    coherence is enforced here, deterministically. Two invariants:
      1. A live fact may be superseded at most once per batch - a second
         supersede of the same target becomes an insert (both claims are from
         the same source; they coexist as live siblings).
      2. A confirm must not target a fact superseded in the same batch - the
         confirm's premise ("the claim already lives in the store") dies with
         its target, so the claim is inserted instead. This is the exact
         lost-update found live 2026-07-11: "sells winners too soon" confirmed
         a fact its sibling superseded, and the claim vanished from the live
         store. Pure function - unit-tested in tests/test_plan_coherence.py."""
    superseded = {p.existing_id for p in plan if p.action == "supersede"}
    seen: set[str] = set()
    out: list[StorePlan] = []
    for p in plan:
        repair = None
        if p.action == "supersede" and p.existing_id in seen:
            repair = "second supersede of the same target"
        elif p.action == "supersede":
            seen.add(p.existing_id)
        elif p.action == "confirm" and p.existing_id in superseded:
            repair = "confirm of a fact superseded in this batch"
        if repair:
            log.warning("plan coherence: %s -> insert %r", repair, p.fact.content)
            p = p.model_copy(update={
                "action": "insert", "existing_id": None,
                "judge_reason": f"{p.judge_reason} (plan-coherence repair: "
                                f"{repair} -> insert)",
            })
        out.append(p)
    return out


def reconcile(state: ExtractionState) -> dict:
    """Node 2: decide, for the WHOLE batch, insert / confirm / supersede /
    archive / skip - coherently.

    Embeds all facts in ONE batched API call; a generous cosine net shortlists
    each fact's nearest live facts ACROSS KINDS (cosine is blind to negation
    and numbers, so it never decides - it only nominates); then ONE joint
    judge call sees all new facts + the union of candidates and decides the
    set together (siblings see each other - no intra-batch lost updates);
    finally validate_plan enforces write-write coherence deterministically.
    Pure decision-making: nothing is written yet (that separation is what
    makes this node testable against a fake plan, and retryable without
    side effects).
    """
    facts = state["extracted"]
    if not facts:
        return {"plan": []}

    vectors = embedder.embed_documents([f.content for f in facts])

    # Wide net per fact, then UNION the candidates for the joint judge call.
    # A dict keyed by id dedupes while keeping first-seen order.
    per_fact_candidates: list[list[tuple]] = []
    union: dict[str, tuple] = {}
    with owner_conn() as conn:  # migration context -> owner role on purpose
        for vec in vectors:
            # Deliberately NOT filtered by kind (the first leak got in through
            # kind-scoping: a struggle was invisible to a trait saying the
            # opposite). `<=>` is pgvector's cosine-distance operator.
            rows = conn.execute(
                """
                SELECT id, kind, content, source_created_at,
                       embedding <=> %s AS distance
                FROM memories
                WHERE student_id = %s AND superseded_by IS NULL
                  AND (embedding <=> %s) < %s
                ORDER BY distance
                LIMIT %s
                """,
                (Vector(vec), state["student_id"], Vector(vec),
                 CANDIDATE_DISTANCE, CANDIDATE_LIMIT),
            ).fetchall()
            per_fact_candidates.append(rows)
            for row in rows:
                union.setdefault(str(row[0]), row)
    existing = list(union.values())

    # One joint judge call per source. Even with NO existing candidates the
    # judge runs when the batch has siblings - new-vs-new duplicates are a
    # conflict class of their own (Graphiti dedupes episodes the same way).
    if not existing and len(facts) == 1:
        decisions = [FactDecision(fact=1, relation="new",
                                  reason="no existing candidates")]
    else:
        decisions = judge_batch(facts, existing)

    plan: list[StorePlan] = []
    for fact, vec, cands, decision in zip(facts, vectors,
                                          per_fact_candidates, decisions):
        kwargs = decide(decision, fact, facts, decisions, existing,
                        state["source_date"])
        nearest = float(cands[0][4]) if cands else None
        # Per-fact decision at DEBUG (set LOG_LEVEL=DEBUG to see it) - the
        # durable version of this trail is the extraction_audit row the
        # store node writes for every planned fact.
        log.debug(
            "reconcile: %-9s kind=%-9s nearest_dist=%s %r - %s",
            kwargs["action"], fact.kind,
            f"{nearest:.3f}" if nearest is not None else "none",
            fact.content, decision.reason,
        )
        plan.append(StorePlan(fact=fact, embedding=vec,
                              nearest_distance=nearest, **kwargs))

    plan = validate_plan(plan)

    # Summary counts at INFO.
    log.info(
        "reconcile: %d insert, %d supersede, %d archive, %d confirm, "
        "%d skip (of %d facts)",
        sum(1 for p in plan if p.action == "insert"),
        sum(1 for p in plan if p.action == "supersede"),
        sum(1 for p in plan if p.action == "archive"),
        sum(1 for p in plan if p.action == "confirm"),
        sum(1 for p in plan if p.action == "skip"),
        len(plan),
    )
    return {"plan": plan}


def store(state: ExtractionState) -> dict:
    """Node 3: apply the plan. The only node that writes memories.

    Every insert carries provenance (source_report_id OR source_conversation_id)
    - the anti-hallucination rule ("only state what you retrieved") depends on it.
    Supersede = write the new fact AND point the old one at it. Never delete:
    the superseded chain IS the "notice patterns over time" data.
    """
    added = confirmed = superseded = archived = skipped = 0
    with owner_conn() as conn:
        for p in state["plan"]:
            if p.action == "skip":
                # Duplicate of a sibling in this same batch: nothing stored -
                # the claim lives in the sibling's row. The audit row below
                # still records the decision for human review.
                skipped += 1
                memory_id = None
            elif p.action == "confirm":
                # GREATEST keeps last_confirmed_at at EVENT time: confirming
                # from an older source must not bump the fact's recency.
                conn.execute(
                    "UPDATE memories SET last_confirmed_at = "
                    "GREATEST(last_confirmed_at, %s) WHERE id = %s",
                    (state["source_date"], p.existing_id),
                )
                confirmed += 1
                memory_id = p.existing_id  # nothing inserted; audit points at it
            else:
                # An "archive" row is born already superseded (points at the live
                # fact it lost to) - history stays complete, the live fact untouched.
                born_superseded = p.existing_id if p.action == "archive" else None
                memory_id = conn.execute(
                    """
                    INSERT INTO memories
                        (student_id, kind, content, embedding, importance,
                         source_report_id, source_conversation_id,
                         source_created_at, last_confirmed_at, superseded_by)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        state["student_id"],
                        p.fact.kind,
                        p.fact.content,
                        Vector(p.embedding),
                        p.fact.importance,
                        state["source_report_id"],
                        state["source_conversation_id"],
                        state["source_date"],  # event time, drives supersede + recency
                        state["source_date"],
                        born_superseded,
                    ),
                ).fetchone()[0]

                if p.action == "archive":
                    archived += 1
                else:
                    added += 1

                if p.action == "supersede":
                    conn.execute(
                        "UPDATE memories SET superseded_by = %s WHERE id = %s",
                        (memory_id, p.existing_id),
                    )
                    superseded += 1

            # Durable audit row, SAME transaction as the action it records -
            # an action can never commit without its audit trail, and vice
            # versa. This is what the /review UI reads; a human approves or
            # flags each decision, and flags become golden-set labels.
            conn.execute(
                """
                INSERT INTO extraction_audit
                    (student_id, source_report_id, source_conversation_id,
                     fact_kind, fact_content, action, memory_id,
                     matched_memory_id, matched_content,
                     judge_relation, judge_reason, nearest_distance)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    state["student_id"],
                    state["source_report_id"],
                    state["source_conversation_id"],
                    p.fact.kind,
                    p.fact.content,
                    p.action,
                    memory_id,
                    p.existing_id,
                    p.matched_content,
                    p.judge_relation,
                    p.judge_reason,
                    p.nearest_distance,
                ),
            )

    log.info("store: added=%d confirmed=%d superseded=%d archived=%d skipped=%d",
             added, confirmed, superseded, archived, skipped)
    return {"added": added, "confirmed": confirmed,
            "superseded": superseded, "archived": archived}


# --------------------------------------------------------------------------
# Graph assembly. Wire the transitions once at import time, then reuse the
# compiled graph for every run.
# --------------------------------------------------------------------------
_builder = StateGraph(ExtractionState)
_builder.add_node("extract", extract)
_builder.add_node("reconcile", reconcile)
_builder.add_node("store", store)
_builder.add_edge(START, "extract")
_builder.add_edge("extract", "reconcile")
_builder.add_edge("reconcile", "store")
_builder.add_edge("store", END)

extraction_graph = _builder.compile()


class ExtractionOutcome(TypedDict):
    """What callers get back - deliberately NARROWER than ExtractionState.

    invoke() returns the entire final graph state, including source_text and
    the full plan with every embedding in it. Callers need only the counters.
    Returning the narrow type keeps the graph's internals private to this
    module."""

    added: int
    confirmed: int
    superseded: int
    archived: int


def run_extraction(
    student_id: str,
    source_text: str,
    source_date: datetime,
    source_report_id: str | None = None,
    source_conversation_id: str | None = None,
) -> ExtractionOutcome:
    """Run one source (a report OR a conversation transcript) through the graph.
    Exactly one of the source ids should be set (that becomes provenance).
    source_date is the source's OWN date - it decides supersede direction."""
    # The student's name feeds the extract prompt so every source phrases its
    # facts identically ("Bob...", never "The student..."). Found empirically:
    # transcripts extracted subject-less facts, and the phrasing drift weakened
    # reconcile matching.
    with owner_conn() as conn:
        student_name = conn.execute(
            "SELECT name FROM students WHERE id = %s", (student_id,)
        ).fetchone()[0]

    final = extraction_graph.invoke(
        {
            "student_id": student_id,
            "student_name": student_name,
            "source_text": source_text,
            "source_date": source_date,
            "source_report_id": source_report_id,
            "source_conversation_id": source_conversation_id,
        }
    )
    # Explicit re-packing, not cast(): cast() is a runtime no-op, whereas this
    # fails HERE if a key goes missing rather than three files away.
    return ExtractionOutcome(
        added=final["added"],
        confirmed=final["confirmed"],
        superseded=final["superseded"],
        archived=final["archived"],
    )
