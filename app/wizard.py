"""The migration wizard - the trial task as a guided product flow.

One page, four steps, mirroring how an operator (not an engineer) would move
their students into the memory layer and decide whether to trust the result:

  1. IMPORT   land raw data from a source (idempotent - re-runs skip)
  2. DISTILL  extract facts from everything pending (background job; progress
              is read from the LEDGERS - migrated_at / ingested_at - so the
              page needs no hooks into the pipeline and survives restarts)
  3. REVIEW   human sign-off on the write-path decisions (/review pages)
  4. ASSESS   migration-quality panel: what was written, review coverage,
              and a SCOPED store-invariant sweep over chosen students
              (cheap, minutes -> seconds, instead of the full-store nightly)

Server-rendered like the review UI; while a job runs the page re-polls via
<meta http-equiv=refresh> - zero JavaScript. Steps are safe to re-run in any
order because every underlying operation is idempotent by structure.
"""

import html
import logging

from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from app import jobs, theme
from app.db import owner_conn
from app.importer.files import FileDataSource
from app.importer.service import run_import
from app.ingestion import ingest_conversations
from app.migration import run_migration
from app.review import _esc  # shared escaper
from evals.store_invariants import run_sweep

log = logging.getLogger(__name__)

router = APIRouter(prefix="/wizard")

# The wave-2 bundles the wizard imports. Fixed on the server: a deployed demo has
# no filesystem the viewer can point at, so there is no path to type - the wizard
# shows the actual records instead (see _incoming).
WAVE2_PATH = "data/students-wave2"

# The wizard's own bits of style, appended after the shared brand sheet.
_WSTYLE = """
.step { background: #fff; border: 1px solid var(--line); border-radius: 14px;
        padding: 1.1rem 1.3rem; margin-bottom: 1rem; }
.step h2 { margin: 0 0 .5rem 0; font-size: 1.3rem; }
.stat { display: inline-block; background: var(--cream2); border-radius: 999px;
        padding: .2rem .7rem; margin: 0 .3rem .3rem 0; font-size: .88rem; }
.verdict-green { background: #d8f2dd; color: var(--green); padding: .5rem .8rem;
                 border-radius: 8px; font-weight: 600; display: inline-block; }
.verdict-red   { background: #fadbd8; color: var(--red); padding: .5rem .8rem;
                 border-radius: 8px; font-weight: 600; display: inline-block; }
.running { color: var(--violet); font-weight: 600; }
.incoming { border: 1px solid var(--line); border-radius: 10px;
            padding: .55rem .8rem; margin: .45rem 0; background: #fff; }
.incoming details { margin-top: .4rem; }
.rec { font-size: .88rem; white-space: pre-wrap; background: var(--cream);
       border: 1px solid var(--line); border-radius: 6px; padding: .5rem .65rem;
       margin: .4rem 0; }
.reset-row { text-align: right; margin-top: 1.8rem; }
.reset { opacity: .4; font-size: .8rem; border-color: transparent;
         color: var(--muted); }
.reset:hover { opacity: 1; border-color: #d99; color: var(--red); }
"""

# Last import result, shown inline on the page (module state, demo-grade).
_last_import: dict = {}

# Minimal vanilla JS (no framework, no build step) that makes the long-running
# steps feel alive: instant spinner on click, live progress polling against
# /wizard/status, and scroll preserved across the one reload done on
# completion - a plain server redirect jumps to the top of the page, which is
# why the button looked like it did nothing. Plain string (NOT an f-string) so
# the JS braces need no escaping.
_SCRIPT = """<script>
(function () {
  var y = sessionStorage.getItem('wiz_scroll');
  if (y !== null) { window.scrollTo(0, parseInt(y, 10));
                    sessionStorage.removeItem('wiz_scroll'); }

  function poll(job) {
    fetch('/wizard/status').then(function (r) { return r.json(); })
      .then(function (s) {
        var box = document.getElementById(job + '-status');
        if (s[job] && s[job].status === 'running') {
          if (box && job === 'distill') {
            box.innerHTML = '<p class="running">Distilling&hellip; '
              + s.pending + ' source(s) left</p>';
          }
          setTimeout(function () { poll(job); }, 1500);
        } else {
          sessionStorage.setItem('wiz_scroll', window.scrollY);
          location.reload();
        }
      });
  }

  function spin(form, statusId, text) {
    var box = document.getElementById(statusId);
    if (box) box.innerHTML = '<p class="running">' + text + '</p>';
    var btn = form.querySelector('button');
    if (btn) { btn.disabled = true; btn.textContent = 'Working\\u2026'; }
    sessionStorage.setItem('wiz_scroll', window.scrollY);
  }

  // Async JOB forms (distill, assess): kick off, then poll until done.
  function hook(formId, job, text) {
    var form = document.getElementById(formId);
    if (!form) return;
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      spin(form, job + '-status', text);
      fetch(form.action, { method: 'POST', body: new FormData(form) })
        .then(function () { poll(job); });
    });
  }

  // SYNC forms (import): fast server-side, no job - just feedback then reload.
  function hookSync(formId, statusId, text) {
    var form = document.getElementById(formId);
    if (!form) return;
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      spin(form, statusId, text);
      fetch(form.action, { method: 'POST', body: new FormData(form) })
        .then(function () { location.reload(); });
    });
  }

  hookSync('import-form', 'import-status', 'Importing&hellip;');
  hook('distill-form', 'distill', 'Distilling&hellip; starting');
  hook('assess-form', 'assess',
       'Sweeping for contradictions&hellip; (one judge call per candidate pair)');

  fetch('/wizard/status').then(function (r) { return r.json(); })
    .then(function (s) {
      if (s.distill && s.distill.status === 'running') poll('distill');
      if (s.assess && s.assess.status === 'running') poll('assess');
    });
})();
</script>"""


def _counts() -> dict:
    """Everything the page needs, read fresh each render - progress comes
    from the ledgers, not from the job (survives restarts, needs no hooks)."""
    with owner_conn() as conn:
        row = conn.execute(
            """
            SELECT
              (SELECT count(*) FROM raw_reports WHERE migrated_at IS NULL),
              (SELECT count(*) FROM raw_reports),
              (SELECT count(*) FROM conversations WHERE ingested_at IS NULL),
              (SELECT count(*) FROM conversations),
              (SELECT count(*) FROM memories WHERE superseded_by IS NULL),
              (SELECT count(*) FROM memories WHERE superseded_by IS NOT NULL),
              (SELECT count(*) FROM extraction_audit WHERE review_status='pending'),
              (SELECT count(*) FROM extraction_audit WHERE review_status='approved'),
              (SELECT count(*) FROM extraction_audit WHERE review_status='flagged')
            """
        ).fetchone()
        students = conn.execute(
            """
            SELECT s.id, s.name, s.external_id, count(m.id)
            FROM students s LEFT JOIN memories m
              ON m.student_id = s.id AND m.superseded_by IS NULL
            GROUP BY s.id, s.name, s.external_id ORDER BY s.name
            """
        ).fetchall()
    return {
        "reports_pending": row[0], "reports_total": row[1],
        "convos_pending": row[2], "convos_total": row[3],
        "facts_live": row[4], "facts_superseded": row[5],
        "review_pending": row[6], "review_approved": row[7],
        "review_flagged": row[8], "students": students,
    }


def _incoming() -> tuple[str, int]:
    """Preview the student bundles waiting to be imported - so the operator and
    the audience can SEE what is coming in (the records themselves), instead of a
    server file path they cannot inspect. Read straight from the file adapter."""
    try:
        bundles = list(FileDataSource(WAVE2_PATH).read())
    except Exception as exc:  # a missing/broken fixture should not 500 the page
        return (f"<p class='note'>Could not read incoming records: {_esc(exc)}</p>", 0)
    if not bundles:
        return ("<p class='why'>No incoming records found.</p>", 0)

    cards = []
    for b in bundles:
        recs = []
        for r in b.reports:
            recs.append(
                f"<div class='rec'><b>Report</b> "
                f"<span class='why'>{r.created_at:%b %Y}</span><br>{_esc(r.content)}</div>")
        for c in b.conversations:
            lines = "<br>".join(
                f"<b>{_esc(m.role)}:</b> {_esc(m.content)}" for m in c.messages)
            recs.append(
                f"<div class='rec'><b>Conversation</b> "
                f"<span class='why'>{c.started_at:%b %Y}</span><br>{lines}</div>")
        cards.append(
            f"<div class='incoming'><b>{_esc(b.name)}</b> "
            f"<span class='why'>({len(b.reports)} report(s), "
            f"{len(b.conversations)} conversation(s))</span>"
            f"<details><summary class='why'>see the raw records</summary>"
            f"{''.join(recs)}</details></div>")
    return ("".join(cards), len(bundles))


def _distill_job() -> dict:
    migrated = run_migration()
    ingested = ingest_conversations()
    return {"reports": migrated.reports_processed,
            "conversations": ingested["conversations_ingested"],
            "facts_added": migrated.facts_added + ingested["facts_added"],
            "superseded": migrated.facts_superseded + ingested["facts_superseded"],
            "archived": migrated.facts_archived + ingested["facts_archived"]}


@router.get("", response_class=HTMLResponse)
def wizard() -> HTMLResponse:
    c = _counts()
    distill = jobs.status("distill")
    assess = jobs.status("assess")

    # ---- Step 1: import -------------------------------------------------
    imp = ""
    if _last_import:
        r = _last_import
        imp = (f"<p><span class='stat'>students +{r['students_imported']}"
               f"/={r['students_skipped']}</span>"
               f"<span class='stat'>reports +{r['reports_imported']}"
               f"/={r['reports_skipped']}</span>"
               f"<span class='stat'>conversations +{r['conversations_imported']}"
               f"/={r['conversations_skipped']}</span> "
               f"<span class='why'>(+imported / =skipped: re-imports skip "
               f"by external_id - idempotent by structure)</span></p>")
    incoming_html, n_incoming = _incoming()
    btn_label = f"Import these {n_incoming} students" if n_incoming else "Import"
    step1 = (
        "<div class='step'><h2>1. Import raw data</h2>"
        "<p class='why'>These student records are waiting to come into the memory "
        "layer - the same shape your real export would take. Each is one bundle: a "
        "student, their reports, and their conversations. Import lands them in the "
        "raw tables (idempotent by id); nothing is distilled into memory yet - that "
        "is the next step. A file adapter feeds them here; your real export becomes "
        "a second adapter behind the same interface.</p>"
        f"{incoming_html}"
        "<form id='import-form' method='post' action='/wizard/import'>"
        f"<button>{_esc(btn_label)}</button></form>"
        f"<div id='import-status'>{imp}</div></div>"
    )

    # ---- Step 2: distill ------------------------------------------------
    pending = c["reports_pending"] + c["convos_pending"]
    if distill["status"] == "running":
        body = (f"<p class='running'>Distilling&hellip; {pending} "
                f"source(s) left</p>")
    elif distill["status"] == "error":
        body = f"<p class='note'>Distill failed: {_esc(distill['error'])}</p>"
    elif pending:
        body = (f"<p>{c['reports_pending']} report(s) and "
                f"{c['convos_pending']} conversation(s) pending "
                f"(ledger: migrated_at / ingested_at is NULL).</p>"
                "<form id='distill-form' method='post' action='/wizard/distill'>"
                "<button>Distill into memory</button></form>")
    else:
        done = distill.get("result") or {}
        summary = (f" Last run: +{done.get('facts_added', 0)} facts, "
                   f"{done.get('superseded', 0)} superseded, "
                   f"{done.get('archived', 0)} archived.") if done else ""
        body = f"<p>Nothing pending - all sources distilled.{summary}</p>"
    step2 = (
        "<div class='step'><h2>2. Distill into memory</h2>"
        "<p class='why'>Each pending source runs the extraction graph: typed "
        "facts, reconciled against existing memory by an LLM judge, every "
        "decision audited. Runs in the background; safe to interrupt - the "
        "ledgers make it resumable.</p>"
        f"<p><span class='stat'>{c['facts_live']} live facts</span>"
        f"<span class='stat'>{c['facts_superseded']} superseded "
        f"(history, never deleted)</span></p>"
        f"<div id='distill-status'>{body}</div></div>"
    )

    # ---- Step 3: review -------------------------------------------------
    step3 = (
        "<div class='step'><h2>3. Review the decisions</h2>"
        "<p class='why'>A human signs off the pilot batch: every extraction "
        "and reconcile decision, next to its source, with the judge's "
        "reasoning. Flags become eval cases.</p>"
        f"<p><span class='badge pending'>{c['review_pending']} pending</span> "
        f"<span class='badge approved'>{c['review_approved']} approved</span> "
        f"<span class='badge flagged'>{c['review_flagged']} flagged</span></p>"
        "<p><a href='/review'>Open the review queue &rarr;</a></p></div>"
    )

    # ---- Step 4: assess -------------------------------------------------
    if assess["status"] == "running":
        body = ("<p class='running'>Sweeping for contradictions&hellip; "
                "(one judge call per candidate pair)</p>")
    elif assess["status"] == "error":
        body = f"<p class='note'>Assessment failed: {_esc(assess['error'])}</p>"
    elif assess["status"] == "done":
        r = assess["result"]
        if r["violations"]:
            rows = "".join(
                f"<div class='card'><b>{_esc(v['student'])}</b> "
                f"(dist {v['distance']:.3f})<br>{_esc(v['a'])}<br>"
                f"{_esc(v['b'])}<div class='why'>{_esc(v['reason'])}</div></div>"
                for v in r["violations"])
            body = (f"<p class='verdict-red'>{len(r['violations'])} "
                    f"contradicting live pair(s) across "
                    f"{r['students_checked']} student(s) - do not sign off "
                    f"until resolved.</p>{rows}")
        else:
            body = (f"<p class='verdict-green'>Store invariants hold: no "
                    f"contradicting live facts ({r['students_checked']} "
                    f"student(s) checked).</p>")
    else:
        body = ""
    boxes = "".join(
        f"<label><input type='checkbox' name='student_id' value='{sid}'> "
        f"{_esc(name)} <span class='why'>({n_facts} live facts)</span>"
        f"</label><br>"
        for sid, name, _ext, n_facts in c["students"]
    )
    step4 = (
        "<div class='step'><h2>4. Assess migration quality</h2>"
        "<p class='why'>The state check behavioral tests miss: no student may "
        "hold two live facts that contradict each other. Scoped to the "
        "students you just migrated - the full store gets this nightly.</p>"
        f"<form id='assess-form' method='post' action='/wizard/assess'>{boxes}"
        "<button>Run invariant sweep</button></form>"
        f"<div id='assess-status'>{body}</div>"
        "<p class='why'>Deeper quality gates run from the CLI: "
        "<code>uv run python -m evals.run --split test</code> (golden-set "
        "scorecard with per-stage failure localization) and "
        "<code>uv run pytest</code> (isolation + plan coherence).</p></div>"
    )

    body = (
        "<h1>Memory-layer migration wizard</h1>"
        "<p class='why'>The trial task as a product: move student data into "
        "the memory layer, then decide whether to trust it. Every step is "
        "idempotent - re-run anything, in any order. "
        "<a href='/chat'>Chat with a student's memory &rarr;</a></p>"
        f"{step1}{step2}{step3}{step4}"
        "<div class='reset-row'><form method='post' action='/wizard/reset' "
        "onsubmit=\"return confirm('Reset the demo to its starting state? This "
        "removes the imported students and everything distilled from them. Alice "
        "and Bob are untouched.')\">"
        "<button class='reset'>&#8635; Reset demo to initial state</button>"
        "</form></div>"
    )
    return HTMLResponse(theme.shell(
        "Memory-layer migration wizard", body,
        active="/under-hood", extra_style=_WSTYLE, scripts=_SCRIPT,
    ))


@router.get("/status")
def wizard_status() -> dict:
    """Job state for the page's polling script - the counts come from the
    ledgers so 'sources left' ticks down live during a distill run."""
    c = _counts()
    return {
        "distill": jobs.status("distill"),
        "assess": jobs.status("assess"),
        "pending": c["reports_pending"] + c["convos_pending"],
    }


@router.post("/import")
def wizard_import() -> RedirectResponse:
    """Import the fixed wave-2 bundles. No path is taken from the request: a
    deployed demo must not let a caller point the importer at arbitrary server
    files, and there is nothing for a viewer to type anyway."""
    global _last_import
    _last_import = run_import(FileDataSource(WAVE2_PATH)).model_dump()
    return RedirectResponse("/wizard", status_code=303)


@router.post("/reset")
def wizard_reset() -> RedirectResponse:
    """Discreet operator control: undo a wizard run - remove the imported wave-2
    students and everything distilled, reviewed, or asked about them - back to the
    starting state. Safe to click when nothing was imported (a no-op). Alice and
    Bob (and any main-corpus students) are never touched. Removal order respects
    the FKs; superseded_by is nulled before the memories rows are deleted."""
    global _last_import
    try:
        ext_ids = [b.external_id for b in FileDataSource(WAVE2_PATH).read()]
    except Exception:
        ext_ids = []
    with owner_conn() as conn:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM students WHERE external_id = ANY(%s)", (ext_ids,)
        ).fetchall()] if ext_ids else []
        if ids:
            conn.execute("DELETE FROM extraction_audit WHERE student_id = ANY(%s)", (ids,))
            conn.execute("DELETE FROM retrieval_audit  WHERE student_id = ANY(%s)", (ids,))
            conn.execute("UPDATE memories SET superseded_by = NULL WHERE student_id = ANY(%s)", (ids,))
            conn.execute("DELETE FROM memories     WHERE student_id = ANY(%s)", (ids,))
            conn.execute("DELETE FROM messages      WHERE student_id = ANY(%s)", (ids,))
            conn.execute("DELETE FROM conversations WHERE student_id = ANY(%s)", (ids,))
            conn.execute("DELETE FROM raw_reports   WHERE student_id = ANY(%s)", (ids,))
            conn.execute("DELETE FROM students      WHERE id = ANY(%s)", (ids,))
    _last_import = {}
    jobs.clear("distill")
    jobs.clear("assess")
    return RedirectResponse("/wizard", status_code=303)


@router.post("/distill")
def wizard_distill() -> RedirectResponse:
    jobs.start("distill", _distill_job)
    return RedirectResponse("/wizard", status_code=303)


@router.post("/assess")
def wizard_assess(student_id: list[str] = Form([])) -> RedirectResponse:
    ids = list(student_id)
    jobs.start("assess", lambda: run_sweep(ids or None))
    return RedirectResponse("/wizard", status_code=303)
