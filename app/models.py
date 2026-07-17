"""Pydantic models - the DTO layer.

FastAPI uses these to validate requests and shape responses; LangChain uses
Fact/ExtractedFacts as the schema it forces the LLM to fill (structured
output) - invalid LLM output fails validation instead of reaching our code.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# A closed set of fact kinds, enforced at deserialization. Kind matters because
# reconciliation rules differ by kind (events are history and never superseded;
# traits/goals/struggles evolve).
FactKind = Literal["trait", "goal", "struggle", "event", "preference"]


class Fact(BaseModel):
    """One small, self-contained statement about a student."""

    kind: FactKind
    content: str = Field(
        description="One sentence, third person, self-contained "
        "(understandable without the surrounding report)."
    )
    importance: float = Field(
        ge=0.0, le=1.0,
        description="How central this fact is to coaching this student, 0..1.",
    )


class ExtractedFacts(BaseModel):
    """What the extractor LLM must return - the structured-output contract."""

    facts: list[Fact]


class StorePlan(BaseModel):
    """Reconciliation decision for one extracted fact (flows between graph nodes).

    Actions: insert (new fact), confirm (duplicate of a live fact - re-confirm),
    supersede (same topic, NEWER source - version the old fact out), archive
    (same topic, OLDER source - insert as already-superseded history so the
    timeline stays complete without disturbing the live fact), skip (duplicate
    of a SIBLING fact from the same batch - nothing stored, audit row only)."""

    action: Literal["insert", "confirm", "supersede", "archive", "skip"]
    fact: Fact
    embedding: list[float]
    existing_id: str | None = None  # the memory this duplicates or supersedes
    # WHY the action was chosen - carried through so the store node can write
    # the extraction_audit row (durable, human-reviewable; DEBUG logs are not).
    matched_content: str | None = None   # the matched fact as it read at decision time
    judge_relation: str | None = None    # same | evolved | unrelated; None = no candidates
    judge_reason: str | None = None
    nearest_distance: float | None = None


class MemoryOut(BaseModel):
    """A memory as returned by the API."""

    id: uuid.UUID
    kind: str
    content: str
    created_at: datetime
    superseded: bool = False
    # Present on /ask results. score = similarity x recency; the two factors
    # are surfaced separately so a low score is diagnosable (unrelated vs stale).
    similarity: float | None = None  # cosine sim of question vs fact (meaning)
    recency: float | None = None     # time-decay on last_confirmed_at
    score: float | None = None       # similarity x recency (the ranking key)


class AskResponse(BaseModel):
    answer: str
    memories_used: list[MemoryOut]  # provenance surfaced to the caller


class MigrateResult(BaseModel):
    reports_processed: int
    reports_skipped: int   # already migrated (ledger says so) - the idempotency proof
    facts_added: int
    facts_confirmed: int   # duplicates: existing memory re-confirmed, nothing inserted
    facts_superseded: int  # old memory marked superseded by a newer-source version
    facts_archived: int    # older-source facts inserted directly as superseded history
