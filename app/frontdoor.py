"""The front door - the pages a non-technical founder reads without narration.

Three server-rendered pages, all in The Trading Cafe brand:
  /            the story: the problem, the fix, why it matters (plain English)
  /vision      where this goes: the memory as a moat, and the hedge-fund surface
  /under-hood  the engineer's index into the operator tools (for the CEO)

No database, no LLM - these are static prose. The one interactive page that
touches the store is /meet (app.meet). Keeping the marketing surface DB-free
means the front door renders even before the demo has been seeded.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app import theme

router = APIRouter()


# --------------------------------------------------------------------------- #
#  /  - the landing page                                                       #
# --------------------------------------------------------------------------- #
@router.get("/", response_class=HTMLResponse)
def landing() -> HTMLResponse:
    tiles = (
        "<div class='grid'>"
        "<div class='tile'><div class='num'>1</div>"
        "<h3>Remembers what matters</h3>"
        "<p>It picks out the meaningful things a student shares - their fears, "
        "their goals, the habits they are fighting - and keeps them. So the "
        "coach can bring them up months later, like a mentor who was paying "
        "attention the whole time.</p></div>"
        "<div class='tile'><div class='num'>2</div>"
        "<h3>Keeps up as they grow</h3>"
        "<p>When a student changes - “I used to panic on losses, now I "
        "hold my plan” - the coach updates. It answers from who they are "
        "today, and it never throws the old version of a person at the new one."
        "</p></div>"
        "<div class='tile'><div class='num'>3</div>"
        "<h3>Never makes things up</h3>"
        "<p>Every memory traces back to something the student actually said, or "
        "a report you already have. The coach can only speak from real memories, "
        "not invented ones. That matters a lot once you are regulated.</p></div>"
        "</div>"
    )

    body = (
        "<p class='eyebrow'>For The Trading Academy</p>"
        "<h1>An AI coach that actually<br>remembers your students.</h1>"
        "<p class='lede'>Right now your coach meets every student like a "
        "stranger, every time. Someone who has talked to it for six months gets "
        "the same generic start as someone on day one. This is a working demo of "
        "the fix: a memory that grows with each student and keeps up as they "
        "change.</p>"
        "<div class='cta-row'>"
        "<a class='btn' href='/meet'>Meet a student &rarr;</a>"
        "<a class='btn ghost' href='/vision'>See where this goes</a>"
        "</div>"
        "<p class='why'>Prefer the engineer's view? "
        "<a href='/architecture'>Browse the architecture</a> or "
        "<a href='/under-hood'>look under the hood</a>.</p>"
        "<h2>What it does, in plain words</h2>"
        f"{tiles}"
    )
    return HTMLResponse(theme.shell("The Trading Academy - Memory Layer demo",
                                    body, active="/"))


# --------------------------------------------------------------------------- #
#  /vision                                                                     #
# --------------------------------------------------------------------------- #
@router.get("/vision", response_class=HTMLResponse)
def vision() -> HTMLResponse:
    roadmap = (
        "<div class='grid'>"
        "<div class='tile'><h3>Memory that grows from every session</h3>"
        "<p>Today the coach learns from reports. Next it learns from every "
        "conversation, voice included - each session quietly adds to what it "
        "knows, so the relationship deepens instead of resetting.</p></div>"
        "<div class='tile'><h3>A coach that notices patterns</h3>"
        "<p>Not just recall - noticing. “You cut three winners short this "
        "week, same as March.” The memory is what makes that possible, "
        "because it can see a student across months, not just this chat.</p></div>"
        "<div class='tile'><h3>Course content as a second brain</h3>"
        "<p>Your lessons and modules become a searchable knowledge base the coach "
        "can draw on - kept separate from personal memory, so a student’s "
        "private profile never mixes with shared teaching material.</p></div>"
        "<div class='tile'><h3>The same memory feeds the fund</h3>"
        "<p>When a student converts to live capital, the fund already understands "
        "their psychology and risk habits. The memory you build for coaching is "
        "the same asset that de-risks allocation. One system, two surfaces.</p>"
        "</div></div>"
    )

    ideas = (
        "<div class='grid'>"
        "<div class='tile'><h3>Clear the coaching bottleneck</h3>"
        "<p>In Zack's walkthrough, the human coaches are the constraint - "
        "the waiting list grows because onboarding needs scarce human time. An AI "
        "screening coach can run the first pass: the intake, the initial read, and "
        "it builds the student's memory from day one. A human coach then picks up a "
        "warm, fully briefed student, only where human judgment is really needed. "
        "More students onboarded without more coaches.</p></div>"
        "<div class='tile'><h3>Coaching where the work happens</h3>"
        "<p>Not another chat window the student has to open - small, timely hints "
        "right where they are looking. As a student studies a chart, a hint near "
        "the cursor: “you tend to cut winners early - this looks like your "
        "March setup.” This is the shape that is winning. Cursor, an AI coding "
        "tool now valued around $29B (in talks at over $50B), proved in-context "
        "help beats a bolted-on chatbot; Cluely, backed by a16z, proved a "
        "screen-aware overlay that surfaces hints as you work is a real product. "
        "Grounded in the student's memory, the hints are personal, not generic - "
        "the moat again.</p></div>"
        "<div class='tile'><h3>A copilot for your coaches</h3>"
        "<p>Point the same memory at the humans. Before each session, one short "
        "brief: where the student is, what changed since last time, the one thing "
        "to work on. Every human coaching hour goes further - the same bottleneck, "
        "from the other side. The AI serves your coaches, it does not replace "
        "them.</p></div>"
        "<div class='tile'><h3>Nudges, not just answers</h3>"
        "<p>Memory that notices, not only recalls. Three winners cut short this "
        "week, the same as in March - so the coach nudges at the right moment "
        "instead of waiting to be asked. And the same signal becomes a readiness "
        "read: who is ready for live capital, who is at risk - feeding both "
        "retention and the fund.</p></div>"
        "</div>"
    )

    body = (
        "<p class='eyebrow'>The vision</p>"
        "<h1>The memory is the moat.</h1>"
        "<p class='lede'>Most AI in education is a chatbot bolted onto a course. "
        "Anyone can add that in a weekend. What is hard to copy is a coach that "
        "has grown a real relationship with each student over months. That is "
        "what your data makes possible, and it is worth building deliberately.</p>"
        "<h2>Where this goes</h2>"
        f"{roadmap}"
        "<h2>Ideas I want to explore with you</h2>"
        "<p style='max-width:44rem;'>I watched Zack's video walking through the "
        "product. A few things jumped out, and here is where I would take "
        "them.</p>"
        f"{ideas}"
        "<div class='cta-row'>"
        "<a class='btn ghost' href='/'>Back to the start</a></div>"
    )
    return HTMLResponse(theme.shell("Vision - The Trading Academy Memory Layer",
                                    body, active="/vision"))


# --------------------------------------------------------------------------- #
#  /under-hood                                                                 #
# --------------------------------------------------------------------------- #
@router.get("/under-hood", response_class=HTMLResponse)
def under_hood() -> HTMLResponse:
    def link(href: str, title: str, desc: str) -> str:
        return (f"<a class='tile' href='{href}' style='display:block;'>"
                f"<h3>{theme.esc(title)} &rarr;</h3>"
                f"<p style='color:var(--slate);'>{desc}</p></a>")

    tools = (
        "<div class='grid'>"
        + link("/architecture", "Architecture canon",
               "The C4-leveled design - system context, containers and components, "
               "data model, cross-cutting concerns - plus the ADRs, rendered in-brand "
               "with live diagrams and checked by a canon-integrity test.")
        + link("/wizard", "The migration wizard",
               "The trial task as a guided flow: import student data, distill it "
               "into memory, review the decisions, then run a quality check. Every "
               "step is idempotent and safe to re-run.")
        + link("/meet", "Meet a student",
               "The read path in human terms: one student’s memory then and "
               "now, plus a live box to ask their coach a question.")
        + link("/chat", "Retrieval bench",
               "Ask any student’s coach and watch ranking decide what it "
               "sees. Similarity times recency, top facts only, superseded ones "
               "left out - the scores are shown so nothing is a black box.")
        + link("/review", "Review queue",
               "Human sign-off on every extraction and reconcile decision, next "
               "to the source it came from and the judge’s reasoning. Flags "
               "become future test cases.")
        + link("/review/answers", "Answer audit",
               "The read-path ledger: every question, the answer, and the exact "
               "memories it leaned on. The record a regulator or an incident "
               "review starts from.")
        + link("/docs", "API docs",
               "The auto-generated API surface (FastAPI). The endpoints behind "
               "all of the above.")
        + "</div>"
    )

    body = (
        "<p class='eyebrow'>Under the hood</p>"
        "<h1>The engineer’s view.</h1>"
        "<p class='lede'>This is a working miniature of your trial task, built "
        "the way I would build it in production: a real memory layer on Postgres, "
        "LLM extraction with a judge, per-student isolation enforced in the "
        "database, evals, and audit trails on both the write and read paths.</p>"
        f"{tools}"
        "<p class='why' style='margin-top:1.5rem;'>The full architecture write-up "
        "lives in the repo’s <code>README.md</code> and "
        "<code>ARCHITECTURE.md</code>. Happy to walk any of it live.</p>"
    )
    return HTMLResponse(theme.shell("Under the hood - Memory Layer demo",
                                    body, active="/under-hood"))
