# Architecture

The architecture now lives as a **living canon** in [`docs/architecture/`](docs/architecture/) - a
set of C4-leveled views (system context, system design, domain model, cross-cutting) plus immutable
Architecture Decision Records in [`docs/adr/`](docs/adr/). That canon is authoritative; this file is a
pointer so there is a single source of truth (no two architecture docs to drift apart).

Start here:

- [`docs/architecture/README.md`](docs/architecture/README.md) - how the canon is laid out and the rules it follows.
- [`docs/architecture/system-context.md`](docs/architecture/system-context.md) - C4 L1: the system, its actors, external dependencies.
- [`docs/architecture/system-design.md`](docs/architecture/system-design.md) - C4 L2 + L3: containers, runtime flows, components.
- [`docs/architecture/domain-model.md`](docs/architecture/domain-model.md) - entities (ERD), the fact lifecycle, domain events.
- [`docs/architecture/cross-cutting.md`](docs/architecture/cross-cutting.md) - isolation, provenance, versioning, observability, data sensitivity, scaling.
- [`docs/architecture/glossary.md`](docs/architecture/glossary.md) - the ubiquitous language.
- [`docs/adr/`](docs/adr/) - the numbered decisions (Nygard format), immutable once accepted.

For the friendly, narrative walk-through of the design, see [`README.md`](README.md). In the running
demo, the same canon is browsable in-brand at **`/architecture`**.
