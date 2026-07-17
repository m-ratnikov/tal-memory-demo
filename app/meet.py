"""/meet - the read path told as a human story (the demo's one "wow").

A non-technical visitor picks a student and sees, side by side, how the coach
first knew them versus how it knows them today - the retired memories greyed
out, the current ones vivid. Then they can ask the student's coach a question
live and watch it answer from the current memories only, refusing to invent.

Everything on the left (the then/now timeline) is a plain database read, so it
renders instantly and works even if the LLM is slow or offline. The ask box on
the right is the live cherry - it calls the existing GET /students/{id}/ask
JSON endpoint, the same one /chat uses. No new answer-path logic here.

Featured students are the two seeded ones with FIXED uuids (schema.sql), so
this page can hard-reference them. Bob is the hero: his "panics on losses" ->
"no longer panics" is the clearest evolution story in the corpus.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app import theme
from app.db import owner_conn

router = APIRouter(prefix="/meet")

# The two seeded students, with a little academy context (real instructors,
# programs, modules and strategies from The Trading Academy) so each profile
# reads like it belongs to their world, not a generic sandbox.
BOB = "22222222-2222-2222-2222-222222222222"
ALICE = "11111111-1111-1111-1111-111111111111"

FEATURED = {
    BOB: {
        "name": "Bob",
        "context": "Legacy program with Sid Naiman &middot; backtesting the "
                   "Reversal Method",
        "hook": "Four months ago, Bob froze and panic-sold on every trade that "
                "went against him. His coach remembers who he was - and who he "
                "has become.",
        "examples": [
            "Do I still panic when a trade goes against me?",
            "What should I work on next?",
            "What is my favourite programming language?",
        ],
    },
    ALICE: {
        "name": "Alice",
        "context": "Quick Win program with Simon Pullen &middot; working the "
                   "M&amp;W module",
        "hook": "Alice keeps cutting her winners short. Her coach remembers "
                "exactly how that fear shows up for her.",
        "examples": [
            "How should I handle my exits?",
            "What am I working on right now?",
            "What is my star sign?",
        ],
    },
}
DEFAULT = BOB

_MEET_STYLE = """
.hero-hook { font-size: 1.2rem; color: #454b59; max-width: 40rem; }
.switch { display: flex; gap: .5rem; margin: 1rem 0 1.6rem; }
.switch a { padding: .35rem .9rem; border-radius: 999px; font-weight: 600;
            border: 1.5px solid var(--violet); color: var(--violet); }
.switch a.on { background: var(--violet); color: #fff; }
.split { display: grid; grid-template-columns: 1.1fr .9fr; gap: 2rem;
         align-items: start; }
.then-now { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
.tn-col h3 { margin: 0 0 .6rem; font-size: 1.05rem;
             font-family: 'DM Sans', sans-serif; font-weight: 700; }
.tn-col .when { text-transform: uppercase; letter-spacing: .06em;
                font-size: .72rem; font-weight: 700; color: var(--muted); }
.mem { background: #fff; border: 1px solid var(--line); border-radius: 10px;
       padding: .6rem .75rem; margin-bottom: .6rem; font-size: .95rem; }
.mem .k { display: inline-block; font-size: .68rem; font-weight: 700;
          text-transform: uppercase; letter-spacing: .03em; color: var(--violet);
          background: #f3f0fb; padding: .05rem .45rem; border-radius: 999px;
          margin-bottom: .25rem; }
.mem.retired { background: #faf9f7; color: #9b9488; border-style: dashed; }
.mem.retired .body { text-decoration: line-through; text-decoration-color: #cbc4b8; }
.mem.retired .k { color: #a79e8f; background: #efece7; }
.mem.retired .tag { display: block; text-decoration: none; font-size: .72rem;
                    color: #a79e8f; margin-top: .25rem; font-style: italic; }
.askbox { background: #fff; border: 1px solid var(--line); border-radius: 14px;
          padding: 1.2rem; position: sticky; top: 5rem; }
.askbox h3 { margin-top: 0; }
#ex button { display: block; width: 100%; text-align: left; margin: 0 0 .4rem;
             font-size: .9rem; }
#meet-q { width: 100%; margin-bottom: .5rem; }
.answer { margin-top: 1rem; }
.answer .a { background: #f3f0fb; border-radius: 10px; padding: .8rem 1rem;
             white-space: pre-wrap; }
.leaned { font-size: .85rem; color: var(--muted); margin-top: .6rem; }
.leaned li { margin: .2rem 0; }
.callout { background: linear-gradient(180deg,#fff,#faf9ff);
           border: 1px solid var(--line); border-left: 4px solid var(--violet);
           border-radius: 12px; padding: .9rem 1.1rem; margin: 1.4rem 0; }
@media (max-width: 820px) {
  .split { grid-template-columns: 1fr; }
  .askbox { position: static; }
}
"""

_SCRIPT = """<script>
(function () {
  var input = document.getElementById('meet-q');
  var out = document.getElementById('meet-answer');
  var sid = document.body.getAttribute('data-sid');

  function esc(s) { var d = document.createElement('div');
                    d.textContent = s == null ? '' : s; return d.innerHTML; }

  function ask(q) {
    if (!q.trim()) return;
    out.innerHTML = '<div class="a"><span style="color:#7a72a8">'
      + 'TAL is thinking&hellip;</span></div>';
    fetch('/students/' + sid + '/ask?q=' + encodeURIComponent(q))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var mem = d.memories_used || [];
        var lean;
        if (mem.length === 0) {
          lean = '<p class="leaned"><b>Nothing on file for that.</b> So TAL '
            + 'said so instead of guessing - that is the no-made-up-memories '
            + 'rule doing its job.</p>';
        } else {
          var items = mem.map(function (m) {
            return '<li>&ldquo;' + esc(m.content) + '&rdquo;</li>';
          }).join('');
          lean = '<p class="leaned">TAL answered using '
            + mem.length + ' remembered fact' + (mem.length > 1 ? 's' : '')
            + ':</p><ul class="leaned">' + items + '</ul>';
        }
        out.innerHTML = '<div class="answer"><div class="a">'
          + esc(d.answer) + '</div>' + lean + '</div>';
      })
      .catch(function (e) {
        out.innerHTML = '<p class="leaned">Could not reach the coach ('
          + esc(e.message) + '). Is the server running with an OpenAI key?</p>';
      });
  }

  document.getElementById('meet-form').addEventListener('submit', function (e) {
    e.preventDefault(); ask(input.value);
  });
  [].forEach.call(document.querySelectorAll('#ex button'), function (b) {
    b.addEventListener('click', function () { input.value = b.textContent; ask(b.textContent); });
  });
})();
</script>"""


def _memories(student_id: str) -> list[tuple]:
    """One student's facts, oldest source first, retired flag included.

    Owner connection on purpose: this is an operator/demo view that shows the
    full versioned chain (live AND superseded). RLS-scoped reads hide the
    retired ones - which are exactly the 'then' half of the story here.
    """
    with owner_conn() as conn:
        return conn.execute(
            """
            SELECT kind, content, superseded_by IS NOT NULL AS retired
            FROM memories
            WHERE student_id = %s
            ORDER BY source_created_at, created_at
            """,
            (student_id,),
        ).fetchall()


def _mem_card(kind: str, content: str, retired: bool) -> str:
    if retired:
        return (f"<div class='mem retired'><span class='k'>{theme.esc(kind)}</span>"
                f"<div class='body'>{theme.esc(content)}</div>"
                f"<span class='tag'>remembered, no longer true</span></div>")
    return (f"<div class='mem'><span class='k'>{theme.esc(kind)}</span>"
            f"<div class='body'>{theme.esc(content)}</div></div>")


@router.get("/{student_id}", response_class=HTMLResponse)
@router.get("", response_class=HTMLResponse)
def meet(student_id: str = DEFAULT) -> HTMLResponse:
    if student_id not in FEATURED:
        student_id = DEFAULT
    info = FEATURED[student_id]

    rows = _memories(student_id)
    retired = [r for r in rows if r[2]]
    live = [r for r in rows if not r[2]]

    # No memories yet = the demo has not been seeded/distilled. Guide, do not
    # crash - the page still has to look intentional to a founder clicking cold.
    if not rows:
        body = (
            f"<p class='eyebrow'>Meet {theme.esc(info['name'])}</p>"
            f"<h1>{theme.esc(info['name'])}&rsquo;s memory is empty for now.</h1>"
            "<p class='lede'>The demo store has not been populated yet. Run the "
            "one-time setup, then this page fills with the then-and-now story.</p>"
            "<p><a class='btn' href='/under-hood'>Open the wizard &rarr;</a></p>")
        return HTMLResponse(theme.shell(f"Meet {info['name']}", body,
                                        active="/meet", extra_style=_MEET_STYLE))

    switch = "<div class='switch'>" + "".join(
        f"<a href='/meet/{sid}' class=\"{'on' if sid == student_id else ''}\">"
        f"{theme.esc(FEATURED[sid]['name'])}</a>"
        for sid in FEATURED
    ) + "</div>"

    then_col = (
        "<div class='tn-col'><div class='when'>When they started</div>"
        "<h3>How TAL first knew them</h3>"
        + ("".join(_mem_card(*r) for r in retired) if retired else
           "<div class='mem' style='color:var(--muted);border-style:dashed;'>"
           "Nothing has been retired yet - their early picture still holds.</div>")
        + "</div>"
    )
    now_col = (
        "<div class='tn-col'><div class='when'>Today</div>"
        "<h3>How TAL knows them now</h3>"
        + "".join(_mem_card(*r) for r in live)
        + "</div>"
    )

    callout = (
        "<div class='callout'>TAL keeps <b>both</b> columns. It answers from who "
        "they are today, but it never deletes the past - that history is how it "
        "notices a student growing over time.</div>"
    ) if retired else ""

    examples = "".join(f"<button>{theme.esc(q)}</button>" for q in info["examples"])
    askbox = (
        "<div class='askbox'><h3>Ask "
        f"{theme.esc(info['name'])}&rsquo;s coach</h3>"
        "<p class='why'>Live. It answers from the memories on the left, and "
        "declines to guess about anything it has not been told.</p>"
        f"<div id='ex'>{examples}</div>"
        "<form id='meet-form'><input id='meet-q' autocomplete='off' "
        "placeholder='Ask something&hellip;'>"
        "<button class='btn' type='submit'>Ask</button></form>"
        "<div id='meet-answer'></div></div>"
    )

    body = (
        f"<p class='eyebrow'>Meet {theme.esc(info['name'])} &middot; "
        f"{info['context']}</p>"
        f"<h1>{theme.esc(info['name'])}</h1>"
        f"<p class='hero-hook'>{info['hook']}</p>"
        f"{switch}"
        "<div class='split'><div>"
        f"<div class='then-now'>{then_col}{now_col}</div>{callout}</div>"
        f"{askbox}</div>"
    )
    # data-sid lets the ask script target the right student without inlining it.
    scripts = f"<script>document.body.setAttribute('data-sid','{student_id}');</script>{_SCRIPT}"
    return HTMLResponse(theme.shell(
        f"Meet {info['name']} - Memory Layer demo", body,
        active="/meet", extra_style=_MEET_STYLE, scripts=scripts))
