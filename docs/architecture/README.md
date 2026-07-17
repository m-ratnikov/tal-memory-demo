# Architecture (living canon)

The current, always-up-to-date picture of how the TAL memory system is built. Read this folder to
answer "how is the system built today?"; read the ADRs in [`../adr/`](../adr/) to answer "why?".

These docs apply the `spec-driven-architecture` canon format (the same one used in wisery-crm): a
coherent set of C4-leveled views, one authoritative copy of each system-wide concern, with durable
decisions distilled into immutable ADRs. This canon is the authoritative architecture reference for the
repo; [`../../README.md`](../../README.md) is the friendly narrative intro and
[`../../ARCHITECTURE.md`](../../ARCHITECTURE.md) now points here.

## Layout

- `glossary.md` - the ubiquitous language. ONE per bounded context (we have one, the personal-memory core).
- `domain-model.md` - the entity model (ERD), the fact lifecycle, and the domain events. Top-level and
  flat while there is one implicit area; moves into `areas/<area>/` if the model ever splits.
- `system-context.md` - C4 level 1 (the system, its actors, and every external system). System-wide, one file.
- `system-design.md` - C4 levels 2 and 3 (containers, key runtime flows, and the component view inside
  the one app process). System-wide while there is one implicit area.
- `cross-cutting.md` - isolation, provenance, versioning, observability, data sensitivity, evals, scaling.
  System-wide, one file.
- `../adr/` - the Architecture Decision Records (Nygard format), numbered and immutable once accepted.
  The view docs above are living and revised; an ADR is frozen.
- `deployment.md` - deployment topology. Present only once where-things-run is decided (deferred; VPS is
  a later step).

## Rules

1. Three levels: a bounded CONTEXT (one model, one language - here the personal-memory core) contains
   AREAS/subdomains (the `areas/` folders, none yet), which contain finer capabilities. The folder axis
   is the AREA.
2. System-wide content has exactly ONE copy (glossary, system-context, cross-cutting). Never duplicate
   it per area.
3. A change UPDATES the homes it touches; it never dumps new ad-hoc files.
4. Start flat: while there is one implicit area, keep `domain-model.md` / `system-design.md` at the top
   of this folder. Split into `areas/<name>/` only when the single model grows or a second area emerges.
5. ADRs are immutable once accepted. To change a decision, write a NEW ADR that supersedes the old one;
   the prior ADR's contents stay frozen as history.

## Canon integrity

The canon has a machine-readable contract and a deterministic check - the discipline is verify, don't
mechanize: a human authors the views, the check judges the result.

- `canon.manifest.json` - the contract: each canon doc and the section headings it MUST contain, plus
  the named artifacts (e.g. the boundary runtime flow) that canon prose may reference only if they
  resolve to a heading within canon.
- `tests/test_canon_integrity.py` (run via `uv run pytest tests/test_canon_integrity.py`) enforces it:
  required sections present; every relative Markdown link across the canon and the ADRs resolves to a
  real file; intra-canon anchors resolve to real headings; named artifacts resolve to a canon heading;
  and no `<!-- v:... -->` verification anchors are left in the promoted views.

A red check means a doc dropped a required section, a link rotted, or an anchor points at nothing - fix
the canon, not the test.
