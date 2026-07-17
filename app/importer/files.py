"""File-based DataSource - the demo's (and the synthetic corpus's) adapter.

Layout: one JSON file per student bundle in a directory (default `data/students/`).
The file IS the StudentBundle schema - pydantic validates on load, so a
malformed fixture fails loudly at read time with a precise error, not deep
inside the DB write.

This is deliberately the THIN part: all real logic (identity, idempotency,
transactions) lives in service.py and works for any source. When the client's
actual export format arrives, the new adapter is this file's size.
"""

import logging
from pathlib import Path
from typing import Iterator

from app.importer.base import StudentBundle

log = logging.getLogger(__name__)


class FileDataSource:
    """Reads `*.json` student bundles from a directory, sorted by filename
    (deterministic import order - nice for reproducible demos, not required
    for correctness: source dates decide reconciliation, never import order)."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def describe(self) -> str:
        return f"files:{self.root}"

    def read(self) -> Iterator[StudentBundle]:
        paths = sorted(self.root.glob("*.json"))
        log.info("import source %s: %d bundle file(s)", self.describe(), len(paths))
        for path in paths:
            yield StudentBundle.model_validate_json(
                path.read_text(encoding="utf-8")
            )
