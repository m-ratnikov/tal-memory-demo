"""HTTP surface (FastAPI).

Handlers here are plain `def` (sync): FastAPI runs them on a thread pool, which
keeps the demo code simple. Production version: `async def` + async DB driver,
same structure.

Run:  uv run uvicorn app.main:app --reload
Docs: http://localhost:8000/docs  (auto-generated)
"""

import base64
import logging
import os
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from app import obs
from app.db import student_conn
from app.importer.files import FileDataSource
from app.importer.service import ImportResult, run_import
from app.ingestion import ingest_conversations
from app.migration import run_migration
from app.models import AskResponse, MemoryOut, MigrateResult
from app.retrieval import answer_question
from app.architecture import router as architecture_router
from app.chat import router as chat_router
from app.frontdoor import router as frontdoor_router
from app.meet import router as meet_router
from app.review import router as review_router
from app.wizard import router as wizard_router

obs.configure_logging()  # one-time logging bootstrap at the composition root

app = FastAPI(title="TAL-style memory layer - demo")

# --- Login tracking (who is trying the demo) --------------------------------
# Every credential submission is logged, from every IP, success or failure, with
# no de-duplication. Basic auth resends the password on each request, so a single
# page view (its assets included) produces several OK lines - that is expected;
# "log all attempts" means all of them. The browser's first no-credentials probe
# (the one that draws the 401 challenge) is NOT a submission, so it is skipped.
_access_log = logging.getLogger("app.access")
# Appended to a file (default login.log in the workdir) AND emitted to stdout
# via the logger, so it shows up in `docker logs` too. On an ephemeral
# container filesystem the file resets on redeploy - point DEMO_LOGIN_LOG at a
# mounted volume to keep the history across deploys.
_LOGIN_LOG = os.getenv("DEMO_LOGIN_LOG", "login.log")


def _client_ip(request) -> str:
    """The real client IP behind the reverse proxy. Caddy/most proxies set
    X-Forwarded-For; the first hop is the original client. Fall back to the
    socket peer for a direct (no-proxy) connection."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _log_attempt(request, ok: bool, user: str) -> None:
    """Record one login attempt - every one, from every IP, no de-dup. `ok` is
    whether the submitted credentials matched; `user` is the submitted username."""
    ip = _client_ip(request)
    result = "OK" if ok else "FAIL"
    ua = request.headers.get("user-agent", "-")
    _access_log.info(
        "DEMO LOGIN %s ip=%s user=%s path=%s ua=%s",
        result, ip, user or "-", request.url.path, ua)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    line = f"{ts}\t{result}\t{ip}\t{user or '-'}\t{request.url.path}\t{ua}\n"
    try:
        with open(_LOGIN_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:  # a read-only FS should never take the demo down
        _access_log.warning("could not write %s: %s", _LOGIN_LOG, e)


@app.middleware("http")
async def _password_gate(request, call_next):
    """Optional shared-credential gate for a public deployment. When
    DEMO_PASSWORD is set (in the host's env), every path except the health check
    requires HTTP Basic auth; unset (local dev), the gate is a no-op. If DEMO_USER
    is also set, the username must match it too (a proper username/password pair);
    if DEMO_USER is empty, any username is accepted. Read from the environment per
    request so it needs no import-time ordering."""
    pw = os.getenv("DEMO_PASSWORD")
    if pw and request.url.path != "/health":
        want_user = os.getenv("DEMO_USER") or ""
        header = request.headers.get("authorization", "")
        got_user, got_pw = "", ""
        has_creds = header.startswith("Basic ")
        if has_creds:
            try:
                got_user, _, got_pw = base64.b64decode(
                    header[6:]).decode("utf-8").partition(":")
            except Exception:
                got_user, got_pw = "", ""
        ok_pw = secrets.compare_digest(got_pw, pw)
        ok_user = (not want_user) or secrets.compare_digest(got_user, want_user)
        ok = ok_pw and ok_user
        if has_creds:  # a credential was submitted - log the attempt (OK or FAIL)
            _log_attempt(request, ok, got_user)
        if not ok:
            return Response(
                "Authentication required.", status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="TAL demo"'})
    return await call_next(request)


# Vendored assets (mermaid.min.js) - the front door inlines its small favicon /
# logo, but mermaid is large enough to serve as a static file.
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"),
          name="static")
# Front door first: the branded, non-technical pages a founder reads without a
# walkthrough (/ , /vision, /under-hood) and the /meet story page.
app.include_router(frontdoor_router)
app.include_router(meet_router)     # /meet - the read path as a human story
app.include_router(architecture_router)  # /architecture - the living canon, in-brand
# The human-review pages (/review) live in their own module (an APIRouter is a
# mountable route group).
app.include_router(review_router)
app.include_router(wizard_router)  # /wizard - the migration flow as a product
app.include_router(chat_router)    # /chat - interactive read-path (retrieval) test


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/import", response_model=ImportResult)
def import_data(path: str = "data/students") -> ImportResult:
    """Land raw student data from a source into the raw tables (idempotent
    via external_id). Source-agnostic by design: the service takes any
    DataSource; this endpoint wires the file adapter. The client's real
    export format becomes a second adapter behind the same protocol.
    Import fills the raw log only - run /migrate and /ingest-conversations
    afterwards to distill the new sources into memory facts."""
    return run_import(FileDataSource(path))


@app.post("/migrate", response_model=MigrateResult)
def migrate() -> MigrateResult:
    """THE TRIAL TASK IN MINIATURE: move legacy reports into the memory layer.

    Thin handler - the orchestration lives in app.migration.run_migration so it
    is reusable (evals, tests) and server-independent. Idempotent via the
    migrated_at ledger: run it twice, the second run skips everything.
    """
    return run_migration()


@app.post("/ingest-conversations")
def ingest() -> dict:
    """Distill finished conversations into memory FACTS via the same extraction
    path as reports - the personal memory grows from talking. Raw conversation is
    never retrieved into the prompt; that is the knowledge base's job."""
    return ingest_conversations()


@app.get("/students/{student_id}/memories", response_model=list[MemoryOut])
def list_memories(student_id: uuid.UUID) -> list[MemoryOut]:
    """Inspect one student's memory, superseded facts included -
    the versioned chain is visible here (that IS the 'patterns over time' data)."""
    with student_conn(str(student_id)) as conn:
        rows = conn.execute(
            """
            SELECT id, kind, content, created_at, superseded_by IS NOT NULL
            FROM memories
            ORDER BY created_at
            """
        ).fetchall()
    return [
        MemoryOut(id=r[0], kind=r[1], content=r[2], created_at=r[3], superseded=r[4])
        for r in rows
    ]


@app.get("/students/{student_id}/ask", response_model=AskResponse)
def ask(student_id: uuid.UUID, q: str) -> AskResponse:
    """TAL answers using ONLY retrieved memories (RAG over the memory layer).

    `q` is a required query-string parameter; FastAPI returns 422 if missing.
    """
    return answer_question(str(student_id), q)
