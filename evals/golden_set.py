"""The golden set: labelled cases a human agrees are correct.

This is the cheapest, most honest eval asset there is - the "right answers",
written down. In the real system this comes from the ~7,000 existing student
Q&A pairs (free, already labelled by reality). Here it is hand-built from the
two seeded students in schema.sql, so the demo's evals are self-contained.

KEY DESIGN RULE - the golden must be INDEPENDENT ground truth. `expected_fact`
is a claim a HUMAN wrote, phrased how a correct system *should* phrase it. It is
NOT the raw report prose, and NOT the model's actual extracted output. Building
the golden from the system's own output would be circular: the eval could then
only prove the system equals itself, never that it is right (a dropped field
would pass, because the golden dropped it too).

Each case pins one behaviour the memory layer must get right:
  - RECALL:     a retrieved fact ENTAILS the expected claim (same claim)
  - SUPERSEDE:  no retrieved fact CONTRADICTS it (a stale/negated fact must not
                leak) - scored as an entailment/contradiction (NLI) judgment,
                which is negation-aware where a substring or cosine check is not
  - REFUSAL:    when nothing relevant exists, TAL must say so, not invent
"""

import json
from dataclasses import dataclass
from pathlib import Path

ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"


@dataclass(frozen=True)
class GoldenCase:
    name: str
    # A student REFERENCE: either a UUID (seeded students) or an external_id
    # ("stu-004", imported corpus) - the runner resolves it against the DB.
    student_id: str
    question: str
    # Human-authored gold claim (independent of the system's output). Recall =
    # at least one retrieved fact entails this. None => a refusal case.
    expected_fact: str | None = None
    # Also assert NO retrieved fact contradicts expected_fact. Set for cases that
    # exercise versioning: a superseded, opposite-polarity fact must not surface.
    verify_supersede: bool = False
    # True => the correct behaviour is an honest "I don't know that about you yet".
    expect_refusal: bool = False
    # Dimension tags (case type, fact kind, timeline shape, failure-mode flags):
    # results slice by these - aggregate scores hide WHERE a metric breaks.
    tags: tuple[str, ...] = ()
    # dev = tune prompts/thresholds against it freely. test = touch NOTHING
    # based on it; it exists to catch overfitting to dev. The 5 hand-written
    # cases below are dev BY DEFINITION - we tuned on them for days.
    split: str = "dev"


def load_golden_cases() -> list[GoldenCase]:
    """Hand-written cases + the generated corpus (data/golden/cases.json,
    produced by scripts/generate_dataset.py from world-model ground truth)."""
    cases = list(GOLDEN_SET)
    path = Path("data/golden/cases.json")
    if path.exists():
        for item in json.loads(path.read_text(encoding="utf-8")):
            cases.append(GoldenCase(
                name=item["name"],
                student_id=item["student_external_id"],
                question=item["question"],
                expected_fact=item["expected_fact"],
                verify_supersede=item["verify_supersede"],
                expect_refusal=item["expect_refusal"],
                tags=tuple(item["tags"]),
                split=item["split"],
            ))
    return cases


GOLDEN_SET: list[GoldenCase] = [
    GoldenCase(
        name="alice-exits-recall",
        student_id=ALICE,
        question="How should I handle exiting my winning trades?",
        expected_fact="Alice tends to exit her winning trades too early.",
    ),
    GoldenCase(
        name="alice-discipline-recall",
        student_id=ALICE,
        question="Am I a disciplined trader?",
        expected_fact="Alice is a disciplined trader who follows her trading plan.",
    ),
    GoldenCase(
        name="bob-supersede-current-fact",
        student_id=BOB,
        question="Do I still panic when a trade goes against me?",
        # The CURRENT (follow-up) claim must be retrieved...
        expected_fact="Bob no longer panics when a trade goes against him.",
        # ...and the OLD "panics on losses" fact - its polarity opposite - must be
        # superseded and therefore absent. A judge reading meaning catches the
        # negation; a substring/cosine check would miss it.
        verify_supersede=True,
    ),
    GoldenCase(
        name="bob-next-goal-recall",
        student_id=BOB,
        question="What should I work on next?",
        expected_fact="Bob's current goal is to hold his stop-loss routine "
                      "through a full losing week.",
    ),
    GoldenCase(
        name="alice-refusal-unknown",
        student_id=ALICE,
        # Nothing in Alice's report touches this - TAL must refuse, not invent.
        question="What is my favourite programming language?",
        expect_refusal=True,
    ),
]
