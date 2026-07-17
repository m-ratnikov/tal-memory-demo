# ADR-0001: Personal memory and the knowledge base are two separate homes

- Status: accepted
- Date: 2026-07-10
- Supersedes: none
- Source: [../architecture/domain-model.md](../architecture/domain-model.md), [../../README.md](../../README.md)

## Context

TAL needs two very different kinds of recall. One is a student's own history - months of reports and
conversations, personal, dynamic, and sensitive. The other is shared course content - lectures and
modules, static and non-personal. Early work briefly merged them into one retrieval store. They pull in
opposite directions: 100 hours of a student's history cannot go in a prompt and must be distilled to a
few durable facts, while citing a specific lecture passage verbatim is a feature. One is per-student and
must be isolated; the other is shared and has no isolation need.

## Decision

We will keep two stores with two paradigms. **Personal memory** is a memory layer: extract each
student's reports and conversations into small typed FACTS (`memories`), per-student, RLS-isolated,
retrieved by meaning under a token budget. The **knowledge base** is classic RAG: chunk shared course
content into PASSAGES (`documents` / `document_chunks`), shared, no per-student isolation, retrieved and
cited as text. The rule that keeps them apart: distil personal, dynamic history into facts; retrieve
shared, static reference content as passages. Retrieving a student's own raw history is the anti-pattern;
retrieving shared lecture passages is normal RAG. The coach may later cite the knowledge base, but never
retrieves a student's raw history.

## Consequences

Easier: each store gets the mechanism it actually needs - versioning, provenance, and isolation for
personal memory; chunking, hybrid search, and reranking for the knowledge base - without compromising the
other. Isolation stays simple because only the personal store carries `student_id`. Harder: two stores to
build and reason about, and a future "answer from facts AND passages" flow must merge two retrievers. The
knowledge base is explicitly out of trial scope (it relates to the separate video-content bot), so it is
modeled as target architecture only; committing to the split now keeps that later work from contaminating
the personal-memory design.
