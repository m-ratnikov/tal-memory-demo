"""Chat UI - an interactive test bench for the READ path (retrieval).

The /ask endpoint returns JSON; this page makes it feel-able. You pick a
student, ask questions, and see the answer NEXT TO the exact facts retrieval
pulled and their scores - so it is a retrieval TEST tool, not a chatbot. The
scores are the point: you watch similarity x recency decide what the coach
sees, and you watch a superseded fact stay out of the answer.

Honesty in the UI copy: each question retrieves FRESH and independently -
there is no conversation threading here (that is the voice/checkpointer work
in the roadmap, deliberately absent). Every question also writes to
retrieval_audit, so /review/answers fills up as you chat.

Pure frontend over the existing GET /students/{id}/ask?q= JSON endpoint - the
page is a thin client, no new server logic on the answer path.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app import theme
from app.db import owner_conn

router = APIRouter(prefix="/chat")

# Page-specific styling appended after the shared brand sheet (theme.STYLE).
_CHAT_STYLE = """
.running { color: var(--violet); font-weight: 600; }
.turn { max-width: 46rem; }
.turn.you { background: #f3f0fb; }
.turn b { font-size: .8rem; color: var(--muted); }
.msg { margin: .2rem 0; white-space: pre-wrap; }
#examples button { margin: 0 .3rem .3rem 0; font-size: .85rem; }
#q { width: 32rem; max-width: 90%; }
select { padding: .35rem .5rem; border: 1px solid var(--line);
         border-radius: 8px; background: #fff; }
.subnav { margin-bottom: 1rem; font-size: .9rem; }
"""

# Example questions - clickable so a live demo needs no typing. The last one is
# a REFUSAL probe (no source states it) - it shows the anti-hallucination rule.
_EXAMPLES = [
    "What do I struggle with?",
    "What should I work on next?",
    "Am I a disciplined trader?",
    "Do I still panic when a trade goes against me?",
    "What is my favourite programming language?",
]

# Plain (non-f-string) JS so its braces need no escaping. Talks to the existing
# /students/{id}/ask JSON endpoint and renders answer + retrieved-fact scores.
_SCRIPT = """<script>
(function () {
  var student = document.getElementById('student');
  var transcript = document.getElementById('transcript');
  var input = document.getElementById('q');

  function esc(s) { var d = document.createElement('div');
                    d.textContent = s == null ? '' : s; return d.innerHTML; }

  function addTurn(who, msg) {
    var div = document.createElement('div');
    div.className = 'card turn ' + who;
    div.innerHTML = '<b>' + (who === 'you' ? 'You' : 'TAL') + '</b>'
      + '<div class="msg">' + msg + '</div><div class="facts"></div>';
    transcript.appendChild(div);
    div.scrollIntoView({ behavior: 'smooth', block: 'end' });
    return div;
  }

  function ask(q) {
    if (!q.trim()) return;
    var sid = student.value;
    addTurn('you', esc(q));
    var turn = addTurn('coach', '<span class="running">thinking&hellip;</span>');
    fetch('/students/' + sid + '/ask?q=' + encodeURIComponent(q))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var mem = d.memories_used || [];
        function num(x) { return x == null ? '' : x; }
        var rows = mem.map(function (m, i) {
          return '<tr><td>#' + (i + 1) + '</td><td>' + num(m.similarity)
            + '</td><td>' + num(m.recency) + '</td><td><b>' + num(m.score)
            + '</b></td><td>[' + esc(m.kind) + ']</td><td>' + esc(m.content)
            + '</td></tr>';
        }).join('') || '<tr><td colspan="6">(nothing retrieved - honest refusal)</td></tr>';
        turn.querySelector('.msg').innerHTML = esc(d.answer);
        turn.querySelector('.facts').innerHTML =
          '<details><summary class="why">retrieved ' + mem.length
          + ' fact(s) - similarity &times; recency = score (low similarity = '
          + 'unrelated; low recency = stale)</summary>'
          + '<table><tr><th>rank</th><th>similarity</th><th>recency</th>'
          + '<th>score</th><th>kind</th><th>fact</th></tr>'
          + rows + '</table></details>';
      })
      .catch(function (e) {
        turn.querySelector('.msg').innerHTML =
          '<span class="note">error: ' + esc(e.message) + '</span>';
      });
  }

  document.getElementById('chat-form').addEventListener('submit', function (e) {
    e.preventDefault(); ask(input.value); input.value = ''; input.focus();
  });
  [].forEach.call(document.querySelectorAll('#examples button'), function (b) {
    b.addEventListener('click', function () { ask(b.textContent); });
  });
  // Switching student clears the transcript - retrieval is per-student, so a
  // mixed transcript would be misleading.
  student.addEventListener('change', function () { transcript.innerHTML = ''; });
})();
</script>"""


@router.get("", response_class=HTMLResponse)
def chat() -> HTMLResponse:
    with owner_conn() as conn:
        students = conn.execute(
            "SELECT id, name FROM students ORDER BY name"
        ).fetchall()

    options = "".join(f"<option value='{sid}'>{theme.esc(name)}</option>"
                      for sid, name in students)
    examples = "".join(f"<button>{theme.esc(q)}</button>" for q in _EXAMPLES)

    body = (
        "<div class='subnav'><a href='/under-hood'>&larr; under the hood</a> "
        "&middot; <a href='/wizard'>wizard</a> &middot; "
        "<a href='/review'>review</a> &middot; "
        "<a href='/review/answers'>answer audit</a></div>"
        "<p class='why'>Ask a student's coach and watch retrieval work: each "
        "answer shows the exact facts it pulled and their similarity&times;"
        "recency scores. A test bench, not a chatbot - questions are answered "
        "independently (no conversation threading; that's the voice/checkpointer "
        "work), and each one is logged to the read-path audit.</p>"
        f"<p><label>Student: <select id='student'>{options}</select></label></p>"
        f"<p id='examples'>{examples}</p>"
        "<form id='chat-form'><input id='q' autocomplete='off' "
        "placeholder='Ask this student&rsquo;s coach&hellip;'> "
        "<button>Ask</button></form>"
        "<div id='transcript'></div>"
    )
    return HTMLResponse(theme.shell(
        "Chat - test retrieval",
        f"<h1>Chat - test the memory retrieval</h1>{body}",
        active="/under-hood", extra_style=_CHAT_STYLE, scripts=_SCRIPT,
    ))
