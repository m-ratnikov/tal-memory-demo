"""Canon integrity - the deterministic post-condition on the architecture canon.

The Python analog of wisery-crm's tests/canon-integrity.test.ts. The canon
(docs/architecture/) is human-authored in the spec-driven-architecture format;
canon.manifest.json is its machine-readable contract and this test is the check.
Principle: verify, don't mechanize - a human writes the views, the check judges
the result. A red here means a required section was dropped, a link rotted, an
anchor points at nothing, or a verification anchor leaked into the promoted view.

Runs free (no DB, no network, no LLM), so it belongs in CI beside the leakage and
plan-coherence suites. Run:  uv run pytest tests/test_canon_integrity.py
"""

import json
import re
from pathlib import Path

import pytest

ARCH = Path(__file__).resolve().parent.parent / "docs" / "architecture"
MANIFEST = json.loads((ARCH / "canon.manifest.json").read_text(encoding="utf-8"))
ADR_DIR = (ARCH / MANIFEST["adrDir"]).resolve()

_FENCE = re.compile(r"```.*?```", re.DOTALL)
_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*$", re.MULTILINE)
_NUM_PREFIX = re.compile(r"^\d+(\.\d+)*\.?\s+")


def _strip_code(text: str) -> str:
    """Drop fenced code/mermaid blocks so their contents are not parsed as
    Markdown links or headings."""
    return _FENCE.sub("", text)


def _headings(text: str) -> list[str]:
    return _HEADING.findall(_strip_code(text))


def _norm(section: str) -> str:
    """Normalize a heading/section for comparison: drop a leading 'N.' number
    prefix, collapse whitespace, lowercase (matches the manifest's $paths note)."""
    return _NUM_PREFIX.sub("", section).strip().lower()


def _slug(heading: str) -> str:
    """GitHub-style anchor slug for a heading."""
    s = _norm(heading)
    s = re.sub(r"[^\w\s-]", "", s)
    return re.sub(r"\s+", "-", s)


def _canon_docs() -> list[Path]:
    return [ARCH / d["path"] for d in MANIFEST["docs"]]


def _all_docs() -> list[Path]:
    """Every doc the check scans: the canon views plus every ADR."""
    return _canon_docs() + sorted(ADR_DIR.glob("*.md"))


def test_manifest_docs_exist():
    for doc in _canon_docs():
        assert doc.is_file(), f"manifest lists a missing canon doc: {doc.name}"


@pytest.mark.parametrize("entry", MANIFEST["docs"], ids=lambda e: e["path"])
def test_required_sections_present(entry):
    text = (ARCH / entry["path"]).read_text(encoding="utf-8")
    have = {_norm(h) for h in _headings(text)}
    for section in entry["requiredSections"]:
        assert _norm(section) in have, (
            f"{entry['path']} is missing required section '{section}'")


def test_named_artifacts_resolve_to_a_canon_heading():
    all_headings = []
    for doc in _canon_docs():
        all_headings += _headings(doc.read_text(encoding="utf-8"))
    normalized = [_norm(h) for h in all_headings]
    for artifact in MANIFEST["namedArtifacts"]:
        a = _norm(artifact)
        assert any(a in h for h in normalized), (
            f"named artifact '{artifact}' resolves to no canon heading")


def test_all_relative_links_and_anchors_resolve():
    problems: list[str] = []
    for doc in _all_docs():
        text = doc.read_text(encoding="utf-8")
        for target in _LINK.findall(_strip_code(text)):
            target = target.strip()
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            path_part, _, anchor = target.partition("#")
            if path_part:
                dest = (doc.parent / path_part).resolve()
                if not dest.exists():  # a file OR a directory (e.g. ../adr/)
                    problems.append(f"{doc.name}: dead link -> {target}")
                    continue
            else:
                dest = doc  # pure in-page anchor
            if anchor and dest.suffix == ".md":
                slugs = {_slug(h) for h in _headings(dest.read_text(encoding="utf-8"))}
                if anchor not in slugs:
                    problems.append(f"{doc.name}: dead anchor -> {target}")
    assert not problems, "unresolved links/anchors:\n  " + "\n  ".join(problems)


def test_no_verification_anchors_leaked():
    # The five promoted VIEW docs must be clean. README.md is the canon's own
    # contract narration - it legitimately quotes the anchor syntax when
    # describing this very check, so it is not a promoted view and is exempt.
    for doc in _canon_docs():
        if doc.name == "README.md":
            continue
        assert "<!-- v:" not in doc.read_text(encoding="utf-8"), (
            f"{doc.name} still contains a <!-- v:... --> verification anchor "
            "(these must be stripped from the promoted canon view)")
