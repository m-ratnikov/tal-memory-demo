"""Eval runner: scores the memory layer against the golden set.

Run:  uv run python -m evals.run                  (report: print the scorecard)
      uv run python -m evals.run --fail-under 0.8 (gate: exit 1 if a rate < 0.8)
      uv run python -m evals.run --no-judge       (dry run: answers only, no calls)

Three metrics, matching the interview eval plan:
  - Recall       does a retrieved fact ENTAIL the claim a human marked relevant?
  - Supersede    does NO retrieved fact CONTRADICT it (stale/negated fact leaked)?
  - Groundedness did the answer use ONLY retrieved facts, or invent? (refusal
                 counts as grounded)

WHY RECALL IS A JUDGE, NOT A SUBSTRING CHECK: the stored fact is LLM-generated,
so its wording varies. Exact-substring recall is brittle - a paraphrase
("closes winners too soon" vs "exits winning trades too early") false-fails even
when retrieval was correct (the classic SQuAD "France" vs "French" -> 0 problem).
A cosine-similarity check fixes paraphrase but is BLIND TO NEGATION ("panics" vs
"no longer panics" score ~identical). So the match decision is an
entailment/contradiction (NLI) judgment by an LLM, which reads meaning AND
polarity. Cost: an LLM call per case, non-deterministic. At scale you'd put a
cheap cosine filter in front to shortlist candidates before judging; omitted
here because each student has a handful of facts - a pre-filter buys nothing.

WHY A SCRIPT, NOT A CI TEST: judged metrics hit paid APIs and aren't
deterministic. The free, deterministic gate that belongs in CI is
tests/test_isolation.py (the cross-student leakage suite). This runner is the
on-demand pass you run when a prompt or the retrieval ranking changes.

TWO MODES: by default this REPORTS a scorecard (metrics to track, exit 0). With
--fail-under it ASSERTS - any metric below the bar exits non-zero, so CI can
block a regression. Same run, two modes, exactly like coverage's --fail-under.
Gating is on the aggregate RATE, not per-case, because the system is
non-deterministic: one unlucky sample shouldn't red the build; a rate drop should.
"""

import argparse
import logging
import sys
import uuid
from collections import Counter

from pydantic import BaseModel, Field

from app import obs
from app.ai import llm
from app.db import owner_conn
from app.migration import run_migration
from app.models import AskResponse
from app.retrieval import answer_question
from evals.golden_set import GoldenCase, load_golden_cases

log = logging.getLogger(__name__)


class GroundednessVerdict(BaseModel):
    """The judge's structured output - a bool plus its reason (for the report)."""

    grounded: bool = Field(description="True if every claim about the student is "
                                       "supported by the facts, OR the answer refuses.")
    reason: str = Field(description="One short sentence explaining the verdict.")


JUDGE_PROMPT = """You are a strict evaluator of an AI coach's answer.

You are given the FACTS the coach was allowed to use, and the coach's ANSWER.
Decide if the ANSWER is grounded:
- grounded=true if every statement about the student is supported by the FACTS.
- grounded=true if the answer honestly refuses ("I don't know that about you
  yet") instead of inventing.
- grounded=false if the answer states anything about the student that the FACTS
  do not support (a hallucinated memory).

Judge only grounding, not tone or helpfulness."""


def judge_groundedness(resp: AskResponse) -> GroundednessVerdict:
    """One LLM call per case. with_structured_output forces the bool+reason
    shape - the judge cannot answer in prose that we would have to parse."""
    facts_block = "\n".join(f"- [{m.kind}] {m.content}" for m in resp.memories_used) \
        or "(no facts were available)"
    verdict = llm.with_structured_output(GroundednessVerdict).invoke(
        [
            ("system", JUDGE_PROMPT),
            ("user", f"FACTS:\n{facts_block}\n\nANSWER:\n{resp.answer}"),
        ]
    )
    assert isinstance(verdict, GroundednessVerdict)
    return verdict


class RecallVerdict(BaseModel):
    """The retrieval judge's output - two independent NLI-style booleans."""

    supported: bool = Field(description="True if at least one retrieved fact "
        "ENTAILS the expected claim (same claim; paraphrases count).")
    contradicted: bool = Field(description="True if at least one retrieved fact "
        "CONTRADICTS the expected claim (e.g. a stale, opposite-polarity fact "
        "leaked through).")
    reason: str = Field(description="One short sentence explaining the verdict.")


RECALL_JUDGE_PROMPT = """You evaluate whether a retrieval system fetched the
right memory for a question.

You are given an EXPECTED claim (what a human says should have been retrieved)
and the FACTS that were actually retrieved. Reading MEANING, not wording, and
paying close attention to NEGATION, decide two things:

- supported: does at least one retrieved fact state the SAME claim as EXPECTED?
  Paraphrases count: "exits winners early" == "closes winning trades too soon".
- contradicted: does at least one retrieved fact assert the OPPOSITE of EXPECTED?
  e.g. EXPECTED "no longer panics" vs a retrieved "panics on losses" is a
  contradiction -> contradicted=true.

This is an entailment / contradiction (NLI) judgment, not string matching."""


class StoreRecallVerdict(BaseModel):
    """Does the expected claim exist ANYWHERE in the student's live store?"""

    present: bool = Field(description="True if at least one live fact ENTAILS "
                                      "the expected claim (paraphrases count).")
    reason: str = Field(description="One short sentence explaining the verdict.")


def judge_store_recall(expected_fact: str, student_uuid: str) -> StoreRecallVerdict:
    """The WRITE-PATH half of the funnel: is the claim in the store at all,
    regardless of ranking? Judged against ALL of the student's live facts, not
    the top-k. recall(top-k) FAIL + present=True localizes the failure to
    RANKING; present=False localizes it to EXTRACTION/RECONCILE. Same NLI
    logic as the retrieval judge - only the candidate set differs."""
    with owner_conn() as conn:
        rows = conn.execute(
            "SELECT kind, content FROM memories "
            "WHERE student_id = %s AND superseded_by IS NULL",
            (student_uuid,),
        ).fetchall()
    facts_block = "\n".join(f"- [{k}] {c}" for k, c in rows) or "(store is empty)"
    verdict = llm.with_structured_output(StoreRecallVerdict).invoke(
        [
            ("system", RECALL_JUDGE_PROMPT),
            ("user", f"EXPECTED claim:\n{expected_fact}\n\n"
                     f"RETRIEVED facts:\n{facts_block}"),
        ]
    )
    assert isinstance(verdict, StoreRecallVerdict)
    return verdict


def judge_recall(expected_fact: str, resp: AskResponse) -> RecallVerdict:
    """One LLM call. Decides same-claim (recall) and opposite-claim (leak) in a
    single pass - a poor man's NLI classifier over the retrieved set."""
    facts_block = "\n".join(f"- [{m.kind}] {m.content}" for m in resp.memories_used) \
        or "(nothing was retrieved)"
    
    verdict = llm.with_structured_output(RecallVerdict).invoke(
        [
            ("system", RECALL_JUDGE_PROMPT),
            ("user", f"EXPECTED claim:\n{expected_fact}\n\nRETRIEVED facts:\n{facts_block}"),
        ]
    ) 
    assert isinstance(verdict, RecallVerdict)
    return verdict 


_student_ids: dict[str, str] = {}


def resolve_student(ref: str) -> str:
    """Golden cases reference students by UUID (seed) or external_id (imported
    corpus) - map either to the DB uuid, cached per run."""
    if ref in _student_ids:
        return _student_ids[ref]
    try:
        uuid.UUID(ref)
        resolved = ref
    except ValueError:
        with owner_conn() as conn:
            row = conn.execute(
                "SELECT id FROM students WHERE external_id = %s", (ref,)
            ).fetchone()
        if row is None:
            raise SystemExit(
                f"golden case references unknown student {ref!r} - "
                f"did you run POST /import for the corpus?")
        resolved = str(row[0])
    _student_ids[ref] = resolved
    return resolved


def score_case(case: GoldenCase, use_judge: bool) -> dict:
    """Run one golden case end to end and return its per-metric results.
    None for a metric means 'not applicable to this case'. All metrics here are
    judge-based, so --no-judge yields a dry run: answers only, no scoring."""
    student_uuid = resolve_student(case.student_id)
    resp = answer_question(student_uuid, case.question)

    recall = supersede = grounded = in_store = None
    if use_judge:
        # Recall + supersede come from ONE retrieval-judge call (NLI-style).
        if case.expected_fact is not None:
            v = judge_recall(case.expected_fact, resp)
            recall = v.supported
            if case.verify_supersede:
                supersede = not v.contradicted
            # FUNNEL: only when top-k recall fails does the store-wide check
            # matter (and cost a call) - it decides WHICH stage owns the bug.
            if not recall:
                in_store = judge_store_recall(case.expected_fact,
                                              student_uuid).present
        # Groundedness judges the generated answer, separately.
        grounded = judge_groundedness(resp).grounded

    return {"recall": recall, "supersede": supersede, "grounded": grounded,
            "in_store": in_store, "answer": resp.answer}


def _fraction(values: list[bool]) -> float | None:
    """Pass-rate as a 0..1 float, or None when the metric applied to no case."""
    return sum(values) / len(values) if values else None


def _rate(values: list[bool]) -> str:
    """Fraction as 'k/n (pp%)', or 'n/a' when the metric applied to no case."""
    if not values:
        return "n/a"
    passed = sum(values)
    return f"{passed}/{len(values)} ({100 * passed // len(values)}%)"


def _mark(v: bool | None) -> str:
    """PASS / FAIL / '-' (not applicable) for one metric cell."""
    return "  -  " if v is None else (" PASS" if v else " FAIL")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run memory-layer evals.")
    parser.add_argument("--no-judge", action="store_true",
                        help="dry run: print answers only, run no judges "
                             "(every metric here is judge-based)")
    parser.add_argument("--fail-under", type=float, default=None, metavar="FRACTION",
                        help="GATE MODE: exit non-zero if any metric's pass-rate "
                             "is below this (e.g. 0.8). Omit to just report. "
                             "Like coverage's --fail-under: same run, two modes.")
    parser.add_argument("--split", choices=["dev", "test"], default=None,
                        help="run only this split. dev = tune against it; "
                             "test = report only, NEVER tune on it. Omit for all.")
    args = parser.parse_args()
    use_judge = not args.no_judge

    obs.configure_logging()

    cases = load_golden_cases()
    if args.split:
        cases = [c for c in cases if c.split == args.split]
    if not cases:
        raise SystemExit(f"no golden cases in split {args.split!r}")

    # Idempotent: if reports are already migrated this is a no-op that just
    # reports them skipped. Makes the runner self-contained - no server needed.
    log.info("ensuring reports are migrated before evaluating...")
    run_migration()

    print("\n" + "=" * 72)
    print(f"  MEMORY-LAYER EVALS  ({len(cases)} cases, "
          f"split={args.split or 'all'}, judge={'on' if use_judge else 'OFF'})")
    print("=" * 72)

    recalls: list[bool] = []
    supersedes: list[bool] = []
    grounded: list[bool] = []
    failed_tags: Counter[str] = Counter()
    stages: Counter[str] = Counter()  # failure localization by pipeline stage

    for case in cases:
        r = score_case(case, use_judge)
        if r["recall"] is not None:
            recalls.append(r["recall"])
        if r["supersede"] is not None:
            supersedes.append(r["supersede"])
        if r["grounded"] is not None:
            grounded.append(r["grounded"])

        # Compact at this scale: one line per case; details only on failure.
        failed = any(v is False for v in
                     (r["recall"], r["supersede"], r["grounded"]))
        print(f"[{case.name:38s}] recall{_mark(r['recall'])}  "
              f"supersede{_mark(r['supersede'])}  grounded{_mark(r['grounded'])}")
        if failed:
            failed_tags.update(case.tags or ("untagged",))
            # THE FUNNEL: same FAIL, three different owners. in_store tells
            # which stage's lever fixes this case.
            if r["recall"] is False:
                stage = ("ranking (in store, not in top-k)"
                         if r["in_store"] else "extraction/reconcile (not in store)")
                stages[stage] += 1
                print(f"    stage: {stage}")
            elif r["grounded"] is False:
                stages["generation (retrieved, answer unsupported)"] += 1
            if r["supersede"] is False:
                stages["versioning (contradicting fact in top-k)"] += 1
            print(f"    tags: {', '.join(case.tags) or '-'}")
            print(f"    answer: {r['answer']}")

    print("\n" + "-" * 72)
    print(f"  Recall (entailment) : {_rate(recalls)}")
    print(f"  Supersede (no-contra): {_rate(supersedes)}")
    print(f"  Groundedness         : {_rate(grounded)}")
    if failed_tags:
        # The error-analysis view: WHERE do failures cluster? An aggregate
        # rate says "3 failed"; this says "all 3 were numeric supersedes".
        slices = ", ".join(f"{t}={n}" for t, n in failed_tags.most_common(8))
        print(f"  Failures by tag      : {slices}")
    if stages:
        print("  Failures by stage    :")
        for stage, n in stages.most_common():
            print(f"    {n} x {stage}")
    print("-" * 72 + "\n")

    # GATE MODE. Without --fail-under this whole block is skipped and the runner
    # is a pure reporter (exit 0). With it, the rates become assertions: any
    # metric below the bar fails the run (exit 1), so CI can block a regression.
    # We gate on the aggregate RATE, not per-case, because an LLM system is
    # non-deterministic - one unlucky sample shouldn't red the build; a rate
    # drop should. This is the "assertion" the scorecard alone was missing.
    if args.fail_under is not None:
        bar = args.fail_under
        # Skip metrics that applied to no case (fraction is None).
        metrics = [("recall", _fraction(recalls)),
                   ("supersede", _fraction(supersedes)),
                   ("groundedness", _fraction(grounded))]
        failures = [(name, frac) for name, frac in metrics
                    if frac is not None and frac < bar]
        if failures:
            for name, frac in failures:
                print(f"  GATE FAIL: {name} {frac:.2f} < {bar:.2f}")
            print()
            sys.exit(1)
        print(f"  GATE PASS: all metrics >= {bar:.2f}\n")


if __name__ == "__main__":
    main()
