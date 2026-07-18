"""Human-review UI over extraction_audit - the operator's quality gate.

WHY THIS EXISTS: during a real migration (TAL: ~thousands of reports) a human
must be able to see, per source, WHAT the pipeline extracted and WHAT reconcile
did to the store - and approve or flag each decision. Tracing tools (LangSmith)
review individual LLM calls; this reviews STORE OUTCOMES, which live across
calls in our own DB. Flagged rows are human LABELS: they feed the golden set
and judge validation later - the review tool is how the eval corpus gets built.

Deliberately boring tech: server-rendered HTML from FastAPI, zero JS framework,
inline CSS. The Hamel-school point is that a custom data viewer is an hour of
work and the highest-ROI tool in an AI pipeline - so keep it an hour of work.

Operator tool: runs on the OWNER connection (cross-student on purpose - the
reviewer audits everyone). Students never see these pages; in production this
sits behind operator auth.

WHERE REVIEW DATA GOES (the lifecycle, also explained on the queue page):
  1. Audit rows are permanent - reviewed or not, they stay as the audit trail.
  2. Flagged rows are LABELS: /review/labels exports them as eval-case
     candidates (JSON) to be turned into golden-set cases (evals/golden_set.py)
     and judge-validation examples. The fix for whatever was flagged happens in
     the pipeline (prompt/judge/threshold), then re-runs are judged against
     those cases - the error-analysis loop.
  3. Approved rows are evidence: "a human checked N% of this batch" is what
     lets a migration report claim a quality level.

Routes:
  GET  /review                        - queue: sources with pending counts
  GET  /review/{source_type}/{id}     - source text next to every decision
  POST /review/{audit_id}/verdict     - approve/flag/undo one decision (form post)
  GET  /review/labels                 - flagged rows as eval-case candidates (JSON)
"""

import uuid

from fastapi import APIRouter, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app import theme
from app.db import owner_conn

router = APIRouter(prefix="/review")

# The look now lives in app.theme (one brand system for the whole demo). This
# alias is kept so any code that still imports _STYLE keeps working.
_STYLE = theme.STYLE


# These pages are POST/redirect/GET: a plain redirect jumps to the top, so on
# a long review list you lose your place and the button gives no feedback.
# This tiny script (capture-phase, so it runs before navigation) saves the
# scroll position and marks the clicked button on ANY submit, then restores
# the position on the next load. No framework, progressive enhancement - forms
# still work with JS off, just with the jump.
_SCROLL_JS = """<script>
(function () {
  var y = sessionStorage.getItem('_rev_y');
  if (y !== null) { window.scrollTo(0, parseInt(y, 10));
                    sessionStorage.removeItem('_rev_y'); }
  document.addEventListener('submit', function (e) {
    sessionStorage.setItem('_rev_y', window.scrollY);
    var b = e.target.querySelector('button');
    if (b) { b.disabled = true; b.textContent = '\\u2026'; }
  }, true);
})();
</script>"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(theme.shell(
        title, f"<h1>{theme.esc(title)}</h1>{body}",
        active="/under-hood", scripts=_SCROLL_JS,
    ))


@router.get("", response_class=HTMLResponse)
def review_queue() -> HTMLResponse:
    """The queue, newest batch first: sources ordered by when they were last
    processed, so a just-run migration/ingestion lands at the top (and is
    badged 'new'). max(created_at) per source is the processing time."""
    with owner_conn() as conn:
        rows = conn.execute(
            """
            SELECT COALESCE(a.source_report_id, a.source_conversation_id),
                   CASE WHEN a.source_report_id IS NOT NULL
                        THEN 'report' ELSE 'conversation' END,
                   s.name,
                   count(*) FILTER (WHERE a.review_status = 'pending'),
                   count(*) FILTER (WHERE a.review_status = 'approved'),
                   count(*) FILTER (WHERE a.review_status = 'flagged'),
                   count(*),
                   max(a.created_at) AS last_processed,
                   max(a.created_at) > now() - interval '15 minutes' AS is_recent
            FROM extraction_audit a
            JOIN students s ON s.id = a.student_id
            GROUP BY 1, 2, 3
            ORDER BY last_processed DESC
            """
        ).fetchall()

    body_rows = "".join(
        f"<tr class='{'recent' if is_recent else ''}'>"
        f"<td>{theme.esc(name)}"
        + (" <span class='badge new'>new</span>" if is_recent else "")
        + f"</td><td>{theme.esc(stype)}</td>"
        f"<td><span class='badge pending'>{pending} pending</span> "
        f"<span class='badge approved'>{approved} ok</span> "
        f"<span class='badge flagged'>{flagged} flagged</span></td>"
        f"<td>{total} facts</td>"
        f"<td class='why'>{last_processed:%b %d, %H:%M}</td>"
        f"<td><a href='/review/{stype}/{sid}'>review</a></td></tr>"
        for sid, stype, name, pending, approved, flagged, total,
        last_processed, is_recent in rows
    )
    with owner_conn() as conn:
        flagged_total = conn.execute(
            "SELECT count(*) FROM extraction_audit WHERE review_status = 'flagged'"
        ).fetchone()[0]

    return _page(
        "Extraction review queue",
        "<p class='why'>Newest batch first - a source you just migrated or "
        "ingested appears at the top, marked <span class='badge new'>new</span>."
        "</p>"
        "<table><tr><th>Student</th><th>Source</th><th>Status</th>"
        f"<th>Facts</th><th>Processed</th><th></th></tr>{body_rows}</table>"
        "<div class='explain'><b>What happens with your review:</b><ul>"
        "<li><b>Approve</b> = the pipeline's decision was right. Approved rows "
        "are the evidence behind &ldquo;a human checked this batch&rdquo; in a "
        "migration report.</li>"
        "<li><b>Flag</b> = wrong or doubtful (say why in the note). Flagged rows "
        "are labels: they get exported as eval-case candidates, turned into "
        "golden-set cases and judge-validation examples, and the underlying "
        "fix lands in the pipeline (prompt / judge / threshold).</li>"
        "<li>Every decision stays in <code>extraction_audit</code> permanently, "
        "reviewed or not - that table is the audit trail.</li></ul>"
        f"<p><a href='/review/labels'><b>{flagged_total} flagged decision(s)"
        "</b> ready to export as eval-case candidates (JSON)</a> &middot; "
        "<a href='/review/answers'>answer audit (read path)</a></p></div>",
    )


# NOTE: registered BEFORE the dynamic /{source_type}/{source_id} route below -
# routes match in declaration order, so a literal path must come first or
# "labels" would be captured as a source_type.
@router.get("/labels")
def export_labels() -> JSONResponse:
    """Flagged decisions as eval-case CANDIDATES - the reviewer's output.

    This is where review data GOES: each flagged row carries the source, what
    the pipeline decided, why the judge thought so, and why the human
    disagreed. From here they become (a) golden-set cases in
    evals/golden_set.py, (b) judge-validation examples (human label vs judge
    verdict), (c) error-analysis input for the next prompt/threshold fix.
    """
    with owner_conn() as conn:
        rows = conn.execute(
            """
            SELECT s.name,
                   CASE WHEN a.source_report_id IS NOT NULL
                        THEN 'report' ELSE 'conversation' END,
                   COALESCE(a.source_report_id, a.source_conversation_id),
                   a.fact_kind, a.fact_content, a.action, a.matched_content,
                   a.judge_relation, a.judge_reason, a.review_note, a.reviewed_at
            FROM extraction_audit a
            JOIN students s ON s.id = a.student_id
            WHERE a.review_status = 'flagged'
            ORDER BY a.reviewed_at
            """
        ).fetchall()

    with owner_conn() as conn:
        answer_rows = conn.execute(
            """
            SELECT s.name, a.question, a.answer, a.retrieved,
                   a.review_note, a.flagged_at
            FROM retrieval_audit a
            JOIN students s ON s.id = a.student_id
            WHERE a.flagged_at IS NOT NULL
            ORDER BY a.flagged_at
            """
        ).fetchall()

    return JSONResponse({
        "purpose": "eval-case candidates from human review - feed into "
                   "evals/golden_set.py and judge validation",
        "flagged": [
            {
                "student": name, "source_type": stype, "source_id": str(sid),
                "fact_kind": kind, "fact_content": content,
                "pipeline_action": action, "matched_content": matched,
                "judge_relation": relation, "judge_reason": reason,
                "human_note": note,
                "reviewed_at": reviewed.isoformat() if reviewed else None,
            }
            for (name, stype, sid, kind, content, action, matched,
                 relation, reason, note, reviewed) in rows
        ],
        # A flagged ANSWER is a golden case waiting to happen: the question,
        # the ranking snapshot it saw, and a human saying what went wrong.
        "flagged_answers": [
            {
                "student": name, "question": question, "answer": answer,
                "retrieved": retrieved, "human_note": note,
                "flagged_at": flagged.isoformat(),
            }
            for (name, question, answer, retrieved, note, flagged) in answer_rows
        ],
    })


# Also literal - registered before the dynamic route, same reason as /labels.
@router.get("/answers", response_class=HTMLResponse)
def review_answers(limit: int = 50) -> HTMLResponse:
    """The READ-PATH ledger, newest first: what each student asked, what the
    coach answered, and the exact ranking snapshot it used. Read-only for now -
    answer-quality review (approve/flag, sampled) is the annotation-queue job;
    this page is the audit view an incident review starts from."""
    with owner_conn() as conn:
        rows = conn.execute(
            """
            SELECT a.id, s.name, a.question, a.answer, a.retrieved, a.model,
                   a.created_at, a.flagged_at, a.review_note
            FROM retrieval_audit a
            JOIN students s ON s.id = a.student_id
            ORDER BY a.created_at DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()

    cards = []
    for (aid, name, question, answer, retrieved, model,
         created, flagged_at, note) in rows:
        facts = "".join(
            f"<tr><td>#{f['rank']}</td><td>{f.get('similarity', '')}</td>"
            f"<td>{f.get('recency', '')}</td><td>{f['score']}</td>"
            f"<td>[{theme.esc(f['kind'])}]</td><td>{theme.esc(f['content'])}</td></tr>"
            for f in retrieved
        ) or "<tr><td colspan='6'>(nothing retrieved - refusal path)</td></tr>"
        # Flag-only review: no Approve on a stream (it never completes) - just
        # mark the bad ones you spot. Flagged answers feed /review/labels.
        if flagged_at:
            verdict = (
                f"<span class='badge flagged'>flagged</span> "
                + (f"<span class='note'>note: {theme.esc(note)}</span> " if note else "")
                + f"<form class='verdict' method='post' "
                  f"action='/review/answers/{aid}/flag'>"
                  f"<input type='hidden' name='action' value='unflag'>"
                  f"<button class='undo'>Undo</button></form>"
            )
        else:
            verdict = (
                f"<form class='verdict' method='post' "
                f"action='/review/answers/{aid}/flag'>"
                f"<input type='hidden' name='action' value='flag'>"
                f"<input name='note' placeholder='what is wrong with this answer?'> "
                f"<button class='flag'>Flag</button></form>"
            )
        cards.append(
            f"<div class='card'>"
            f"<div><b>{theme.esc(name)}</b> asked at {created:%Y-%m-%d %H:%M} "
            f"<span class='why'>({theme.esc(model)})</span></div>"
            f"<div class='why'>Q: {theme.esc(question)}</div>"
            f"<div>A: {theme.esc(answer)}</div>"
            f"<details><summary class='why'>retrieved snapshot "
            f"({len(retrieved)} facts)</summary>"
            f"<table>{facts}</table></details>"
            f"<div>{verdict}</div></div>"
        )

    return _page(
        "Answer audit (read path)",
        "<p><a href='/review'>&larr; back to queue</a></p>"
        f"{''.join(cards) or '<p>No answers audited yet - hit /ask first.</p>'}",
    )


@router.post("/answers/{audit_id}/flag")
def flag_answer(
    audit_id: uuid.UUID,
    action: str = Form(...),
    note: str = Form(""),
) -> RedirectResponse:
    """Flag or unflag one answer. Flag-only by design: the stream has no
    'approved' state - unreviewed is the default and stays so."""
    with owner_conn() as conn:
        if action == "unflag":
            conn.execute(
                "UPDATE retrieval_audit SET flagged_at = NULL, "
                "review_note = NULL WHERE id = %s",
                (audit_id,),
            )
        else:
            conn.execute(
                "UPDATE retrieval_audit SET flagged_at = now(), "
                "review_note = %s WHERE id = %s",
                (note or None, audit_id),
            )
    return RedirectResponse("/review/answers", status_code=303)


def _load_source(source_type: str, source_id: uuid.UUID) -> str:
    """The reviewed source's own text as SAFE HTML - a report body, or a rebuilt
    transcript with the speaker label bolded. Content is always escaped; the only
    literal markup is the <b> we add around each speaker (so the caller renders
    this without re-escaping)."""
    with owner_conn() as conn:
        if source_type == "report":
            row = conn.execute(
                "SELECT content FROM raw_reports WHERE id = %s", (source_id,)
            ).fetchone()
            return theme.esc(row[0]) if row else "(source not found)"
        rows = conn.execute(
            "SELECT role, content FROM messages "
            "WHERE conversation_id = %s ORDER BY created_at",
            (source_id,),
        ).fetchall()
        return "\n".join(
            f"<b>{theme.esc(role)}:</b> {theme.esc(content)}" for role, content in rows
        ) or "(source not found)"


@router.get("/{source_type}/{source_id}", response_class=HTMLResponse)
def review_source(source_type: str, source_id: uuid.UUID) -> HTMLResponse:
    """One source, side by side: what it said vs what the pipeline decided."""
    source_col = "source_report_id" if source_type == "report" \
        else "source_conversation_id"
    with owner_conn() as conn:
        audits = conn.execute(
            f"""
            SELECT id, fact_kind, fact_content, action, matched_content,
                   judge_relation, judge_reason, nearest_distance,
                   review_status, review_note
            FROM extraction_audit
            WHERE {source_col} = %s
            ORDER BY created_at, id
            """,
            (source_id,),
        ).fetchall()

    cards = []
    for (aid, kind, content, action, matched, relation, reason, dist,
         status, note) in audits:
        why = ""
        if relation:
            # dist is NULL when the wide net found no candidates at all (a
            # student's first facts) - the judge still relates them as "new".
            dist_txt = (f"nearest dist {dist:.3f}" if dist is not None
                        else "no candidates in net")
            why = (f"<div class='why'>judge: <b>{theme.esc(relation)}</b> "
                   f"({dist_txt}) - {theme.esc(reason)}</div>")
            if matched:
                why += f"<div class='matched'>vs: &ldquo;{theme.esc(matched)}&rdquo;</div>"
        verdict_note = f"<div class='note'>note: {theme.esc(note)}</div>" if note else ""

        def _form(label: str, new_status: str, css: str = "",
                  note_input: bool = False) -> str:
            return (
                f"<form class='verdict' method='post' action='/review/{aid}/verdict'>"
                f"<input type='hidden' name='source_type' value='{theme.esc(source_type)}'>"
                f"<input type='hidden' name='source_id' value='{source_id}'>"
                f"<input type='hidden' name='status' value='{new_status}'>"
                + ("<input name='note' placeholder='why is this wrong?'> "
                   if note_input else "")
                + f"<button class='{css}'>{label}</button></form>"
            )

        if status == "pending":
            buttons = _form("Approve", "approved") + " " \
                + _form("Flag", "flagged", css="flag", note_input=True)
        else:
            # Undo = back to pending; the handler clears note + reviewed_at.
            buttons = _form("Undo", "pending", css="undo")
        cards.append(
            f"<div class='card'>"
            f"<span class='badge {status}'>{status}</span> "
            f"<span class='action {action}'>{action.upper()}</span> "
            f"[{theme.esc(kind)}] {theme.esc(content)}"
            f"{why}{verdict_note}<div>{buttons}</div></div>"
        )

    return _page(
        f"Review: {source_type} {source_id}",
        "<p><a href='/review'>&larr; back to queue</a></p>"
        "<div class='cols'>"
        f"<div><h2>Source text</h2>"
        f"<div class='source'>{_load_source(source_type, source_id)}</div></div>"
        f"<div><h2>Extraction &amp; reconcile decisions</h2>{''.join(cards)}</div>"
        "</div>",
    )


@router.post("/{audit_id}/verdict")
def review_verdict(
    audit_id: uuid.UUID,
    status: str = Form(...),
    note: str = Form(""),
    source_type: str = Form(...),
    source_id: str = Form(...),
) -> RedirectResponse:
    """Record one human verdict - or undo it. Form(...) binds urlencoded form
    fields, which is why python-multipart is a dependency."""
    if status not in ("approved", "flagged", "pending"):
        status = "flagged"
    with owner_conn() as conn:
        if status == "pending":
            # Undo: back to the unreviewed state, note and timestamp cleared.
            conn.execute(
                "UPDATE extraction_audit SET review_status = 'pending', "
                "review_note = NULL, reviewed_at = NULL WHERE id = %s",
                (audit_id,),
            )
        else:
            conn.execute(
                "UPDATE extraction_audit SET review_status = %s, review_note = %s, "
                "reviewed_at = now() WHERE id = %s",
                (status, note or None, audit_id),
            )
    # 303 = "See Other": the POST is done, GET the page again (the classic
    # post/redirect/get pattern so a browser refresh cannot double-submit).
    return RedirectResponse(f"/review/{source_type}/{source_id}", status_code=303)
