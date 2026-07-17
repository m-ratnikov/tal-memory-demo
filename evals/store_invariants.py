"""Store-invariant monitor: is the memory STORE itself healthy?

Run:  uv run python -m evals.store_invariants
Exit: 0 = invariants hold; 1 = violations found (CI-usable). Violations are
also logged at ERROR - in production that log line is what pages you (Sentry).

WHY THIS EXISTS (found empirically 2026-07-10): behavioral evals judge ANSWERS
(the read path). A stale fact that leaks past reconcile can sit LIVE in the
store while recency ranking keeps it out of top-k - so every behavioral eval
stays green while the store rots. Latent state corruption needs its own check:
this module sweeps the STATE, not the answers. Green dashboards != healthy store.

INVARIANT: no student may hold two LIVE facts that contradict each other.
(Superseded facts are history and exempt - contradiction over time is the
product; contradiction NOW is corruption.)

Detection is the wide-net + judge pattern:
  1. Cosine shortlist of candidate pairs - deliberately CROSS-KIND (the
     reconcile leak got in through kind-scoping; the monitor must not share
     that blind spot) and with a GENEROUS threshold (the leak sat at 0.49
     while reconcile's cutoff was 0.40 - the net here is 0.70).
  2. An LLM judge decides contradiction per candidate pair - negation-aware,
     which cosine is not.

Also reported (WARN, no gate): near-duplicate live pairs - same claim stored
twice. Redundancy, not corruption, but it means reconcile missed a confirm.

In production this runs on a schedule (nightly) and after any write-path
change, over all students or a rolling sample.
"""

import logging
import sys

from pydantic import BaseModel, Field

from app import obs
from app.ai import llm
from app.db import owner_conn

log = logging.getLogger(__name__)

# Wide net: catch everything remotely related, let the judge decide. The
# reconcile leak measured 0.49; reconcile's own cutoff was 0.40. Anything
# under 0.70 is worth a look. (Cost guard: it only feeds the judge, not users.)
CANDIDATE_DISTANCE = 0.70
# Under this, two live facts are probably the same claim stored twice.
DUPLICATE_DISTANCE = 0.25


class ContradictionVerdict(BaseModel):
    """The judge's structured output for one candidate pair."""

    contradicts: bool = Field(description="True only if the two facts cannot "
                                          "both be true of the student NOW.")
    reason: str = Field(description="One short sentence explaining the verdict.")


JUDGE_PROMPT = """You check a student-memory store for internal contradictions.

You get two facts about the SAME student, each with its kind and source date.
Decide: can both be true of the student RIGHT NOW?

- contradicts=true only for a real conflict of CURRENT state, e.g.
  "panics when trades go against him" vs "no longer panics when trades go
  against him".
- Paraphrases and overlapping claims are NOT contradictions.
- DIFFERENT AXES are not contradictions: a score/metric and a behavior, or two
  unrelated weaknesses, can coexist (high risk tolerance + revenge-trading is
  consistent, not contradictory).
- Distinct emotions and behaviors are distinct axes: impulsiveness is not
  panic, anger is not fear. "Impulsive under pressure" and "no longer panics
  when a trade goes against him" can both be true of the same person.
- A general strength and a specific weakness coexist: "highly disciplined and
  follows her plan" and "sells winners too soon" are both true of the same
  trader - assessments assert both at once.
- An ASPIRATION and the BEHAVIOR it fights coexist: a stated preference,
  plan, rule or intention ("prefers smaller positions per her risk plan",
  "has set predefined entry rules") does not contradict a struggle that
  violates it ("trades too large", "overthinks entries") - that gap is the
  whole premise of coaching. Flag only STATE vs STATE opposites.
- Different topics are NOT contradictions.
- The bar: contradicts=true means OPPOSITE POLARITY on the SAME SPECIFIC claim
  ("panics on X" vs "no longer panics on X"), not a tension you can argue.

Be strict: only flag pairs that genuinely cannot coexist. When unsure, say
contradicts=false - this monitor pages a human, and false alarms erode trust."""


def judge_pair(a: tuple, b: tuple) -> ContradictionVerdict:
    """One judge call per candidate pair. a/b = (kind, content, source_date)."""
    verdict = llm.with_structured_output(ContradictionVerdict).invoke(
        [
            ("system", JUDGE_PROMPT),
            ("user",
             f"FACT 1 [{a[0]}] (source dated {a[2]:%Y-%m-%d}):\n{a[1]}\n\n"
             f"FACT 2 [{b[0]}] (source dated {b[2]:%Y-%m-%d}):\n{b[1]}"),
        ]
    )
    assert isinstance(verdict, ContradictionVerdict)
    return verdict


def candidate_pairs(student_id: str) -> list[tuple]:
    """All LIVE current-state fact pairs for one student within the wide cosine
    net. CROSS-KIND among STATE kinds on purpose - the reconcile leak got in
    through kind-scoping. Goals and events are excluded: contradiction is only
    meaningful between CURRENT-STATE claims (trait/struggle/preference). A goal
    that targets a struggle is coaching, not contradiction; an event is history.
    (First run proved this empirically: 6 of 9 flags were goal/event pairs, all
    false alarms. Goal staleness is a separate future invariant.)
    a.id < b.id dedupes pairs."""
    with owner_conn() as conn:
        return conn.execute(
            """
            SELECT a.kind, a.content, a.source_created_at,
                   b.kind, b.content, b.source_created_at,
                   (a.embedding <=> b.embedding) AS distance
            FROM memories a
            JOIN memories b
              ON a.student_id = b.student_id AND a.id < b.id
            WHERE a.student_id = %s
              AND a.superseded_by IS NULL
              AND b.superseded_by IS NULL
              AND a.kind IN ('trait', 'struggle', 'preference')
              AND b.kind IN ('trait', 'struggle', 'preference')
              AND (a.embedding <=> b.embedding) < %s
            ORDER BY distance
            """,
            (student_id, CANDIDATE_DISTANCE),
        ).fetchall()


def check_student(student_id: str, name: str) -> list[dict]:
    """Check one student's live memory. Returns the contradictions found -
    structured, so callers (CLI below, the migration wizard) can render them."""
    pairs = candidate_pairs(student_id)
    log.info("invariants: student=%s (%s) - %d candidate pairs under %.2f",
             student_id, name, len(pairs), CANDIDATE_DISTANCE)

    violations: list[dict] = []
    for a_kind, a_content, a_date, b_kind, b_content, b_date, dist in pairs:
        # Near-duplicates: report, no judge needed, no gate. A missed confirm.
        if dist < DUPLICATE_DISTANCE:
            log.warning("NEAR-DUPLICATE (%.3f) [%s] %r ~ [%s] %r",
                        dist, a_kind, a_content, b_kind, b_content)
            continue
        v = judge_pair((a_kind, a_content, a_date), (b_kind, b_content, b_date))
        if v.contradicts:
            violations.append({
                "student": name, "distance": float(dist),
                "a": f"[{a_kind} {a_date:%Y-%m-%d}] {a_content}",
                "b": f"[{b_kind} {b_date:%Y-%m-%d}] {b_content}",
                "reason": v.reason,
            })
            # ERROR level on purpose: in production this line IS the alert.
            log.error(
                "CONTRADICTION (dist %.3f): [%s %s] %r  <->  [%s %s] %r - %s",
                dist, a_kind, f"{a_date:%Y-%m-%d}", a_content,
                b_kind, f"{b_date:%Y-%m-%d}", b_content, v.reason,
            )
    return violations


def run_sweep(student_ids: list[str] | None = None) -> dict:
    """The callable core: sweep some or all students, return the findings.
    student_ids=None sweeps every student with live facts (the nightly job);
    a scoped list is what the migration wizard uses to assess one import
    wave cheaply instead of paying for the full store."""
    with owner_conn() as conn:
        if student_ids:
            students = conn.execute(
                "SELECT id, name FROM students WHERE id = ANY(%s) ORDER BY name",
                (student_ids,),
            ).fetchall()
        else:
            students = conn.execute(
                """
                SELECT DISTINCT s.id, s.name FROM students s
                JOIN memories m ON m.student_id = s.id AND m.superseded_by IS NULL
                ORDER BY s.name
                """
            ).fetchall()

    violations: list[dict] = []
    for student_id, name in students:
        violations.extend(check_student(str(student_id), name))
    return {"students_checked": len(students), "violations": violations}


def main() -> None:
    obs.configure_logging()
    result = run_sweep()

    print()
    if result["violations"]:
        print(f"  STORE INVARIANT VIOLATED: {len(result['violations'])} "
              f"contradicting live pair(s). See ERROR log lines above.")
        sys.exit(1)
    print(f"  Store invariants hold: no contradicting live facts "
          f"({result['students_checked']} students checked).")


if __name__ == "__main__":
    main()
