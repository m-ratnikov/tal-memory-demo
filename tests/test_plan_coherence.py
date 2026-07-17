"""Regression tests for the intra-batch lost-update bug (found 2026-07-11).

The scenario, as it happened live: one conversation extracted two facts that
both matched the same live fact "Alice tends to exit winning positions too
early" - the anxiety fact SUPERSEDED it while "almost always sells winners too
soon" CONFIRMED it. Both decisions were correct against the pre-batch store
snapshot; applied together, the confirmed claim ended up with no live
representation (a classic write-write conflict / lost update).

validate_plan is the deterministic guard: pure function, no DB, no LLM - the
kind of free test that belongs in CI next to the RLS leakage suite.
"""

from app.extraction import validate_plan
from app.models import Fact, StorePlan


def _plan(action: str, existing_id: str | None = None) -> StorePlan:
    return StorePlan(
        action=action,
        fact=Fact(kind="struggle", content="claim", importance=0.5),
        embedding=[0.0],
        existing_id=existing_id,
        judge_reason="test",
    )


def test_confirm_of_superseded_target_becomes_insert():
    """THE live bug: confirm targets a fact a sibling supersedes in the same
    batch -> the confirmed claim must survive as its own live insert."""
    plan = [_plan("supersede", "F"), _plan("confirm", "F")]
    out = validate_plan(plan)
    assert out[0].action == "supersede"
    assert out[1].action == "insert"
    assert out[1].existing_id is None
    assert "plan-coherence repair" in out[1].judge_reason


def test_second_supersede_of_same_target_becomes_insert():
    """A live fact may be superseded at most once per batch - the second
    claimant becomes a live sibling insert instead."""
    plan = [_plan("supersede", "F"), _plan("supersede", "F")]
    out = validate_plan(plan)
    assert [p.action for p in out] == ["supersede", "insert"]


def test_coherent_plan_passes_untouched():
    """Distinct targets and archives coexist with a supersede - no repairs.
    (An archive born-superseded may point at a fact that dies in the same
    batch: the history chain stays valid.)"""
    plan = [_plan("supersede", "F"), _plan("archive", "F"),
            _plan("confirm", "G"), _plan("insert"), _plan("skip")]
    out = validate_plan(plan)
    assert [p.action for p in out] == \
        ["supersede", "archive", "confirm", "insert", "skip"]
