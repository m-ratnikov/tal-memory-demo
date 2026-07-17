"""The source-agnostic import contract.

WHY AN ABSTRACTION: the paid trial is "migrate our existing student reports
into the memory layer" - and we do not know their data's shape yet (legacy DB?
CSV export? CRM API?). What we DO know is our side of the boundary: whatever
the source is, it must yield students with their reports and conversations.
So the contract is defined by the CONSUMER (our import service), and each
source becomes one adapter. Today: files. Tomorrow: TheirLegacyDbSource,
implementing the same protocol, zero changes to the service or pipeline.

Structural typing via typing.Protocol: FileDataSource never declares
"implements DataSource"; it satisfies the protocol by having the right method
shape. Checked by mypy/pyright, duck-typed at runtime.

Design decisions (production posture, not demo shortcuts):
- The unit of transfer is one STUDENT BUNDLE (student + their sources), and
  read() returns an ITERATOR of bundles - a 7,000-student import must stream,
  not materialize, and per-student commits keep a crash resumable.
- Every record carries the SOURCE system's id (external_id). Identity is what
  makes import idempotent: re-running an import upserts nothing twice.
- Import lands data in the RAW tables only (the append-only log). It never
  extracts facts - the existing /migrate and /ingest-conversations pipelines
  pick up new rows via their own ledgers. One writer per concern.
"""

from datetime import datetime
from typing import Iterator, Protocol

from pydantic import BaseModel


class ImportMessage(BaseModel):
    role: str  # 'student' | 'coach'
    content: str
    at: datetime


class ImportConversation(BaseModel):
    external_id: str
    started_at: datetime
    messages: list[ImportMessage]


class ImportReport(BaseModel):
    external_id: str
    created_at: datetime
    content: str


class StudentBundle(BaseModel):
    """One student and everything the source knows about them."""

    external_id: str
    name: str
    reports: list[ImportReport] = []
    conversations: list[ImportConversation] = []


class DataSource(Protocol):
    """Anything that can yield student bundles is an import source."""

    def read(self) -> Iterator[StudentBundle]:
        """Yield bundles one at a time (stream, do not materialize)."""
        ...

    def describe(self) -> str:
        """Human-readable source identity for logs and the import result."""
        ...
