"""/architecture - the living architecture canon, rendered in-brand.

The Markdown source of truth is docs/architecture/ (the C4 views + glossary) and
docs/adr/ (the decision records). This module RENDERS it; it never duplicates it.
The showcase page leads with the headline C4 diagrams (extracted live from the
canon, so they cannot drift) and the ADR table; each canon doc and ADR also
renders as its own branded page with live Mermaid.

Mermaid is vendored at /static/mermaid.min.js (v10 UMD, exposes window.mermaid),
so diagrams render offline. Markdown -> HTML via the `markdown` library; fenced
```mermaid blocks are rewritten to <pre class="mermaid"> and cross-doc .md links
are rewritten to the in-app routes below.
"""

import re
from pathlib import Path

import markdown as md
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app import theme

router = APIRouter(prefix="/architecture")

ROOT = Path(__file__).resolve().parent.parent      # tal-memory-demo/
ARCH = ROOT / "docs" / "architecture"
ADR = ROOT / "docs" / "adr"

# Canon view docs, in reading order (slug, title, one-line blurb).
VIEWS = [
    ("system-context", "System context", "C4 L1 - the system, its actors, and external systems"),
    ("system-design", "System design", "C4 L2 + L3 - containers, runtime flows, and components"),
    ("domain-model", "Domain model", "Entities (ERD), the fact lifecycle, and domain events"),
    ("cross-cutting", "Cross-cutting", "Isolation, provenance, versioning, observability, scaling"),
    ("glossary", "Glossary", "The ubiquitous language"),
]
_CANON_STEMS = {slug for slug, _, _ in VIEWS} | {"readme"}

# Headline diagrams for the showcase: (canon file, block index, caption). Pulled
# live from the canon so the showcase can never disagree with the docs.
_HEADLINE = [
    ("system-context.md", 0, "C4 L1 - system context"),
    ("system-design.md", 0, "C4 L2 - containers"),
    ("domain-model.md", 0, "Data model (ERD)"),
    ("system-design.md", 2, "Write path - distil a source into facts"),
]

_MERMAID_JS = """<script src="/static/mermaid.min.js"></script>
<script>
  window.addEventListener('DOMContentLoaded', function () {
    if (!window.mermaid) return;
    mermaid.initialize({
      startOnLoad: true, securityLevel: 'loose', theme: 'base',
      themeVariables: {
        primaryColor: '#eef0ff', primaryBorderColor: '#5539A1',
        primaryTextColor: '#2C3345', lineColor: '#8a83c0',
        secondaryColor: '#e6faf7', tertiaryColor: '#faf7ff',
        fontFamily: 'DM Sans, system-ui, sans-serif'
      }
    });
  });
</script>
<script>
  // Diagram lightbox with pan + zoom: click any rendered diagram to open it
  // near-full-screen, then wheel/buttons to zoom and drag to pan. Delegation on
  // document, so it works no matter when Mermaid finishes drawing.
  (function () {
    var S = { s: 1, tx: 0, ty: 0, min: 1, max: 8 };  // zoom multiplier + pan
    var BASE = { w: 0, h: 0 };                         // fitted pixel size (s = 1)
    var drag = { on: false, x: 0, y: 0, moved: false };

    function stage() { return document.getElementById('mm-stage'); }
    function svg() { var st = stage(); return st ? st.querySelector('svg') : null; }
    // Scale by setting the SVG's pixel width/height (it re-rasterizes its vector
    // at that size - crisp at any zoom), and pan with a translate only. A CSS
    // scale() would cache one bitmap and go blurry when magnified.
    function apply() {
      var s = svg();
      if (!s) return;
      s.style.width = (BASE.w * S.s) + 'px';
      s.style.height = (BASE.h * S.s) + 'px';
      s.style.transform = 'translate(' + S.tx + 'px,' + S.ty + 'px)';
    }
    function fit() {
      var r = stage().getBoundingClientRect();
      S.s = 1; S.tx = (r.width - BASE.w) / 2; S.ty = (r.height - BASE.h) / 2;
      apply();
    }
    function zoomAt(cx, cy, factor) {
      var ns = Math.min(S.max, Math.max(S.min, S.s * factor));
      if (ns === S.s) return;
      S.tx = cx - (cx - S.tx) * (ns / S.s);   // keep the point under (cx,cy) fixed
      S.ty = cy - (cy - S.ty) * (ns / S.s);
      S.s = ns; apply();
    }
    function centerZoom(factor) {
      var r = stage().getBoundingClientRect();
      zoomAt(r.width / 2, r.height / 2, factor);
    }

    function build() {
      var o = document.getElementById('mm-lightbox');
      if (o) return o;
      o = document.createElement('div');
      o.id = 'mm-lightbox';
      o.innerHTML =
        '<button id="mm-close" aria-label="Close (Esc)">&times;</button>'
        + '<div id="mm-controls">'
        + '<button data-z="out" aria-label="Zoom out">&minus;</button>'
        + '<button data-z="fit" aria-label="Fit">Fit</button>'
        + '<button data-z="in" aria-label="Zoom in">+</button>'
        + '</div><div id="mm-stage" class="grab"></div>';
      document.body.appendChild(o);

      o.addEventListener('click', function (e) {
        if ((e.target === o || e.target.id === 'mm-close') && !drag.moved) close();
      });
      o.querySelector('#mm-controls').addEventListener('click', function (e) {
        var b = e.target.closest('button'); if (!b) return;
        var z = b.getAttribute('data-z');
        if (z === 'in') centerZoom(1.4);
        else if (z === 'out') centerZoom(1 / 1.4);
        else fit();
      });
      var st = o.querySelector('#mm-stage');
      st.addEventListener('wheel', function (e) {
        e.preventDefault();
        var r = st.getBoundingClientRect();
        zoomAt(e.clientX - r.left, e.clientY - r.top, e.deltaY < 0 ? 1.15 : 1 / 1.15);
      }, { passive: false });
      st.addEventListener('dblclick', function (e) {
        var r = st.getBoundingClientRect();
        zoomAt(e.clientX - r.left, e.clientY - r.top, 1.6);
      });
      st.addEventListener('mousedown', function (e) {
        drag.on = true; drag.moved = false; drag.x = e.clientX; drag.y = e.clientY;
        st.classList.remove('grab'); st.classList.add('grabbing'); e.preventDefault();
      });
      window.addEventListener('mousemove', function (e) {
        if (!drag.on) return;
        var dx = e.clientX - drag.x, dy = e.clientY - drag.y;
        drag.x = e.clientX; drag.y = e.clientY;
        if (Math.abs(dx) + Math.abs(dy) > 2) drag.moved = true;
        S.tx += dx; S.ty += dy; apply();
      });
      window.addEventListener('mouseup', function () {
        if (!drag.on) return;
        drag.on = false; st.classList.remove('grabbing'); st.classList.add('grab');
        setTimeout(function () { drag.moved = false; }, 0);
      });
      return o;
    }

    function close() {
      var o = document.getElementById('mm-lightbox');
      if (!o) return;
      o.classList.remove('open');
      stage().innerHTML = '';
      document.body.style.overflow = '';
    }
    function open(src) {
      var o = build();
      var clone = src.cloneNode(true);
      // Intrinsic size from the viewBox drives the fitted pixel size below.
      var vb = clone.viewBox && clone.viewBox.baseVal;
      var W = (vb && vb.width) || clone.clientWidth || 800;
      var H = (vb && vb.height) || clone.clientHeight || 600;
      clone.removeAttribute('width'); clone.removeAttribute('height');
      clone.style.maxWidth = 'none'; clone.style.maxHeight = 'none';
      var st = stage(); st.innerHTML = ''; st.appendChild(clone);
      o.classList.add('open'); document.body.style.overflow = 'hidden';
      var r = st.getBoundingClientRect();          // measured after .open (visible)
      var scale = Math.min(r.width / W, r.height / H);
      BASE.w = W * scale; BASE.h = H * scale;
      fit();
    }

    document.addEventListener('click', function (e) {
      if (!e.target.closest) return;
      var host = e.target.closest('pre.mermaid');
      if (!host) return;
      var s = host.querySelector('svg');
      if (s) open(s);
    });
    document.addEventListener('keydown', function (e) {
      var o = document.getElementById('mm-lightbox');
      if (!o || !o.classList.contains('open')) return;
      if (e.key === 'Escape') close();
      else if (e.key === '+' || e.key === '=') centerZoom(1.4);
      else if (e.key === '-' || e.key === '_') centerZoom(1 / 1.4);
      else if (e.key === '0') fit();
    });
  })();
</script>"""

_DOC_STYLE = """
.doc { max-width: 54rem; }
.doc h1 { font-size: clamp(1.7rem, 3.6vw, 2.4rem); }
.doc h2 { border-top: 1px solid var(--line); padding-top: 1.1rem; margin-top: 2rem; }
.doc table { display: block; overflow-x: auto; }
.doc pre.mermaid { background: #fff; border: 1px solid var(--line);
  border-radius: 12px; padding: 1rem; margin: 1.2rem 0; text-align: center;
  overflow-x: auto; }
.doc pre:not(.mermaid) { background: var(--cream2); border-radius: 8px;
  padding: .8rem 1rem; overflow-x: auto; }
.doc blockquote { border-left: 3px solid var(--violet); margin: 1rem 0;
  padding: .2rem 1rem; color: #454b59; }
.subnav { font-size: .9rem; margin-bottom: 1rem; }
.mm-figure { margin: 0 0 1.4rem; }
.mm-figure figcaption { font-size: .85rem; color: var(--muted); margin-top: .3rem;
  text-align: center; }
.diagram-grid { display: grid; gap: 1.2rem; grid-template-columns: 1fr 1fr; }
.diagram-grid pre.mermaid { background: #fff; border: 1px solid var(--line);
  border-radius: 12px; padding: 1rem; overflow-x: auto; text-align: center; }
@media (max-width: 820px) { .diagram-grid { grid-template-columns: 1fr; } }

/* Diagrams are clickable - open near-full-screen in the lightbox. */
pre.mermaid { cursor: zoom-in; position: relative; transition: box-shadow .12s ease; }
pre.mermaid:hover { box-shadow: 0 0 0 2px var(--violet); }
pre.mermaid[data-processed]::after { content: "\\2922  click to enlarge";
  position: absolute; top: .5rem; right: .6rem; font-family: 'DM Sans', sans-serif;
  font-size: .72rem; color: var(--muted); background: var(--cream);
  border: 1px solid var(--line); border-radius: 999px; padding: .05rem .5rem;
  opacity: 0; transition: opacity .12s ease; pointer-events: none; }
pre.mermaid:hover::after { opacity: 1; }

#mm-lightbox { position: fixed; inset: 0; z-index: 100; display: none;
  align-items: center; justify-content: center; padding: 3vh 3vw;
  background: rgba(24, 20, 38, .82); }
#mm-lightbox.open { display: flex; }
#mm-stage { position: relative; width: 94vw; height: 90vh; background: #fff;
  border-radius: 14px; box-shadow: 0 24px 70px rgba(0,0,0,.45); overflow: hidden; }
#mm-stage.grab { cursor: grab; }
#mm-stage.grabbing { cursor: grabbing; }
#mm-stage svg { position: absolute; top: 0; left: 0; transform-origin: 0 0; }
#mm-close { position: fixed; top: 1.1rem; right: 1.4rem; z-index: 101;
  width: 2.5rem; height: 2.5rem; border: none; border-radius: 999px;
  background: #fff; color: var(--slate); font-size: 1.5rem; line-height: 1;
  cursor: pointer; box-shadow: 0 3px 12px rgba(0,0,0,.35); }
#mm-close:hover { background: var(--cream2); }
#mm-controls { position: fixed; bottom: 1.4rem; left: 50%;
  transform: translateX(-50%); z-index: 101; display: flex; gap: .25rem;
  background: #fff; border-radius: 999px; padding: .3rem;
  box-shadow: 0 3px 14px rgba(0,0,0,.35); }
#mm-controls button { border: none; background: transparent; color: var(--slate);
  width: 2.2rem; height: 2.2rem; border-radius: 999px; font-size: 1.2rem;
  line-height: 1; cursor: pointer; font-family: 'DM Sans', sans-serif; }
#mm-controls button[data-z="fit"] { width: auto; padding: 0 .8rem; font-size: .9rem;
  font-weight: 600; }
#mm-controls button:hover { background: var(--cream2); }
"""

_MERMAID_FENCE = re.compile(
    r'<pre><code class="language-mermaid">(.*?)</code></pre>', re.DOTALL)
_MERMAID_BLOCK = re.compile(r"```mermaid\n(.*?)```", re.DOTALL)
_HREF = re.compile(r'href="([^"]+)"')


def _mermaid_blocks(filename: str) -> list[str]:
    """Raw ```mermaid sources from a canon doc, in order."""
    return _MERMAID_BLOCK.findall((ARCH / filename).read_text(encoding="utf-8"))


def _mermaid_figure(source: str, caption: str = "") -> str:
    # Escape so the browser stores the source as text (mermaid reads textContent
    # and decodes entities); a raw <br/> would otherwise become a real element.
    fig = f"<pre class='mermaid'>{theme.esc(source.strip())}</pre>"
    if caption:
        fig = f"<figure class='mm-figure'>{fig}<figcaption>{theme.esc(caption)}</figcaption></figure>"
    return fig


def _route_for(href: str, base: Path) -> str:
    """Rewrite a canon-relative link to its in-app route."""
    if href.startswith(("http://", "https://", "mailto:", "/", "#")):
        return href
    path, _, anchor = href.partition("#")
    anchor = f"#{anchor}" if anchor else ""
    target = (base / path).resolve()
    if ADR in target.parents or target == ADR:
        m = re.match(r"(\d{4})", target.name)
        if m:
            return f"/architecture/adr/{m.group(1)}{anchor}"
        return "/architecture"
    if ARCH in target.parents or target == ARCH:
        if target.suffix == ".md" and target.stem.lower() in _CANON_STEMS:
            slug = "index" if target.stem.lower() == "readme" else target.stem
            return f"/architecture/doc/{slug}{anchor}"
        return "/architecture"
    # README.md / ARCHITECTURE.md at the repo root, or anything else off-canon.
    return "/architecture"


def _render(text: str, base: Path) -> str:
    html = md.markdown(text, extensions=["fenced_code", "tables", "toc"])
    html = _MERMAID_FENCE.sub(
        lambda m: f"<pre class='mermaid'>{m.group(1)}</pre>", html)
    html = _HREF.sub(lambda m: f'href="{_route_for(m.group(1), base)}"', html)
    return html


def _adr_rows() -> list[tuple[str, str, str]]:
    """(number, short title, first-paragraph gist) for every ADR, sorted."""
    rows = []
    for f in sorted(ADR.glob("[0-9]*.md")):
        num = f.name[:4]
        lines = f.read_text(encoding="utf-8").splitlines()
        title = lines[0].lstrip("# ").strip()
        title = re.sub(r"^ADR-\d+:\s*", "", title)
        rows.append((num, title, f.name))
    return rows


@router.get("", response_class=HTMLResponse)
def hub() -> HTMLResponse:
    diagrams = "".join(
        _mermaid_figure(_mermaid_blocks(fn)[i], cap)
        for fn, i, cap in _HEADLINE
        if len(_mermaid_blocks(fn)) > i
    )

    view_cards = "".join(
        f"<a class='tile' href='/architecture/doc/{slug}' style='display:block'>"
        f"<h3>{theme.esc(title)} &rarr;</h3>"
        f"<p style='color:var(--slate)'>{theme.esc(blurb)}</p></a>"
        for slug, title, blurb in VIEWS
    )

    adr_rows = "".join(
        f"<tr><td><a href='/architecture/adr/{num}'>ADR-{num}</a></td>"
        f"<td>{theme.esc(title)}</td></tr>"
        for num, title, _ in _adr_rows()
    )

    body = (
        "<p class='eyebrow'>Architecture &middot; living canon</p>"
        "<h1>How the memory system is built.</h1>"
        "<p class='lede'>The design as a C4-leveled canon: one system-context view, "
        "the container and component design, the data model, and the cross-cutting "
        "concerns - with every durable decision recorded as an ADR. Authored with "
        "the same spec-driven-architecture method I use in production, and checked "
        "by a canon-integrity test so the docs cannot rot.</p>"
        "<h2>The system at a glance</h2>"
        f"<div class='diagram-grid'>{diagrams}</div>"
        "<p class='why'>These four are pulled live from the canon docs below - the "
        "full runtime flows, the L3 component view, and the read path are in "
        "<a href='/architecture/doc/system-design'>System design</a>.</p>"
        "<h2>The views</h2>"
        f"<div class='grid'>{view_cards}</div>"
        "<p class='why'>Plus the <a href='/architecture/doc/glossary'>glossary</a> "
        "and the <a href='/architecture/doc/index'>canon README</a> (layout, rules, "
        "and the integrity contract).</p>"
        "<h2>Decisions (ADRs)</h2>"
        "<p class='why'>Immutable once accepted - a decision is changed by a new "
        "ADR that supersedes the old one, never by editing it.</p>"
        f"<table><tr><th>#</th><th>Decision</th></tr>{adr_rows}</table>"
    )
    return HTMLResponse(theme.shell(
        "Architecture - TAL memory system", body,
        active="/architecture", extra_style=_DOC_STYLE, scripts=_MERMAID_JS))


@router.get("/doc/{slug}", response_class=HTMLResponse)
def canon_doc(slug: str) -> HTMLResponse:
    name = "README" if slug == "index" else slug
    if name.lower() not in _CANON_STEMS:
        return _not_found()
    f = ARCH / f"{name}.md"
    if not f.is_file():
        return _not_found()
    body = (
        "<p class='subnav'><a href='/architecture'>&larr; architecture</a></p>"
        f"<article class='doc'>{_render(f.read_text(encoding='utf-8'), ARCH)}</article>"
    )
    return HTMLResponse(theme.shell(
        f"{name} - architecture", body,
        active="/architecture", extra_style=_DOC_STYLE, scripts=_MERMAID_JS))


@router.get("/adr/{num}", response_class=HTMLResponse)
def adr_doc(num: str) -> HTMLResponse:
    try:
        stem = f"{int(num):04d}"
    except ValueError:
        return _not_found()
    matches = sorted(ADR.glob(f"{stem}-*.md"))
    if not matches:
        return _not_found()
    body = (
        "<p class='subnav'><a href='/architecture'>&larr; architecture</a> "
        "&middot; <span class='why'>ADRs are immutable once accepted</span></p>"
        f"<article class='doc'>{_render(matches[0].read_text(encoding='utf-8'), ADR)}</article>"
    )
    return HTMLResponse(theme.shell(
        f"ADR-{stem} - architecture", body,
        active="/architecture", extra_style=_DOC_STYLE, scripts=_MERMAID_JS))


def _not_found() -> HTMLResponse:
    body = ("<h1>Not found</h1><p>That architecture document does not exist. "
            "<a href='/architecture'>Back to the canon &rarr;</a></p>")
    return HTMLResponse(theme.shell("Not found - architecture", body,
                                    active="/architecture"), status_code=404)
