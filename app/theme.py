"""The demo's visual shell - one branded look for every page.

WHY THIS EXISTS: the operator pages (/wizard, /review, /chat) were built to be
narrated by an engineer. The front door (/, /meet, /vision) is built to be
clicked by a non-technical founder with nobody narrating. Both now share one
stylesheet and one page frame so the whole thing reads as a single product,
styled in The Trading Cafe's own brand (their fonts, colours, favicon, logo).

Self-contained on purpose: the favicon is inlined as a data URI and the logo as
inline SVG (both read from app/static/ once, at import). No StaticFiles mount, no
new dependency, works with the network off. Fonts load from Google Fonts with
plain serif/sans fallbacks, so a lost connection degrades gracefully instead of
breaking the layout.

Brand tokens (pulled from thetrading.cafe and their logo SVG):
  violet  #5539A1  primary accent (logo, links, CTAs)
  cyan    #6FDFE3  secondary accent (logo)
  slate   #2C3345  headings and body text
  cream   #F8F7F5 / #EEECEA   warm off-white backgrounds
  gold    #D4972A  tertiary highlight
Headlines: DM Serif Display. Body: DM Sans. (Their site's exact pairing.)
"""

import base64
import html
from pathlib import Path

_STATIC = Path(__file__).parent / "static"

# Inlined once at import - see module docstring for why (self-contained page).
_FAVICON_DATA_URI = "data:image/png;base64," + base64.b64encode(
    (_STATIC / "favicon.png").read_bytes()
).decode()
LOGO_SVG = (_STATIC / "logo.svg").read_text(encoding="utf-8")

# --- brand tokens (also referenced by page modules for inline bits) ----------
VIOLET = "#5539A1"
VIOLET_DK = "#3f2b7d"
CYAN = "#6FDFE3"
SLATE = "#2C3345"
CREAM = "#F8F7F5"
CREAM_2 = "#EEECEA"
GOLD = "#D4972A"
GREEN = "#1B6E2E"
RED = "#A4271A"


def esc(value) -> str:
    """html.escape that tolerates None (audit WHY-fields are nullable)."""
    return html.escape(str(value)) if value is not None else ""


# One stylesheet for every page. The first block is the brand system; the rest
# preserves every class name the operator pages (review/chat/wizard) already
# use, restyled to the brand - so reskinning them is just routing through here.
STYLE = f"""
:root {{
  --violet: {VIOLET}; --violet-dk: {VIOLET_DK}; --cyan: {CYAN};
  --slate: {SLATE}; --cream: {CREAM}; --cream2: {CREAM_2}; --gold: {GOLD};
  --line: #e3ded7; --muted: #6b7280;
}}
* {{ box-sizing: border-box; }}
html {{ -webkit-text-size-adjust: 100%; }}
body {{
  font-family: 'DM Sans', system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
  color: var(--slate); background: var(--cream); margin: 0;
  line-height: 1.6; font-size: 16px;
}}
h1, h2, h3 {{
  font-family: 'DM Serif Display', Georgia, 'Times New Roman', serif;
  font-weight: 400; color: var(--slate); line-height: 1.15; letter-spacing: -.01em;
}}
h1 {{ font-size: clamp(1.9rem, 4.5vw, 3rem); margin: 0 0 .4rem; }}
h2 {{ font-size: clamp(1.35rem, 3vw, 1.9rem); margin: 2.2rem 0 .6rem; }}
h3 {{ font-size: 1.2rem; margin: 1.4rem 0 .4rem; }}
p {{ margin: .6rem 0; }}
a {{ color: var(--violet); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
code {{ background: var(--cream2); padding: .1rem .35rem; border-radius: 4px;
        font-size: .88em; }}

/* --- page frame: top bar, main column, footer --- */
.topbar {{
  background: #fff; border-bottom: 1px solid var(--line);
  position: sticky; top: 0; z-index: 20;
}}
.topbar-inner {{
  max-width: 62rem; margin: 0 auto; padding: .7rem 1.5rem;
  display: flex; align-items: center; gap: 1.2rem; flex-wrap: wrap;
}}
.brand {{ display: flex; align-items: center; gap: .6rem; margin-right: auto; }}
.brand svg {{ height: 30px; width: auto; display: block; }}
.brand .tag {{ font-size: .72rem; color: var(--muted); font-weight: 600;
               letter-spacing: .04em; text-transform: uppercase;
               border-left: 1px solid var(--line); padding-left: .6rem; }}
.nav {{ display: flex; gap: .3rem; flex-wrap: wrap; font-size: .92rem; }}
.nav a {{ color: var(--slate); padding: .3rem .7rem; border-radius: 999px;
          font-weight: 500; }}
.nav a:hover {{ background: var(--cream2); text-decoration: none; }}
.nav a.active {{ background: var(--violet); color: #fff; }}
main {{ max-width: 62rem; margin: 0 auto; padding: 2.2rem 1.5rem 4rem; }}
.lede {{ font-size: 1.2rem; color: #454b59; max-width: 42rem; }}
.eyebrow {{ text-transform: uppercase; letter-spacing: .08em; font-size: .78rem;
            font-weight: 700; color: var(--violet); margin: 0 0 .4rem; }}
.footer {{ border-top: 1px solid var(--line); color: var(--muted);
           font-size: .85rem; padding: 1.5rem; text-align: center; }}

/* --- buttons + CTAs --- */
.btn {{ display: inline-block; background: var(--violet); color: #fff;
        padding: .6rem 1.2rem; border-radius: 999px; font-weight: 600;
        border: none; cursor: pointer; font-size: 1rem; }}
.btn:hover {{ background: var(--violet-dk); text-decoration: none; }}
.btn.ghost {{ background: transparent; color: var(--violet);
             box-shadow: inset 0 0 0 1.5px var(--violet); }}
.btn.ghost:hover {{ background: rgba(85,57,161,.06); }}
.cta-row {{ display: flex; gap: .7rem; flex-wrap: wrap; margin: 1.4rem 0; }}

/* --- generic card grid --- */
.grid {{ display: grid; gap: 1.1rem; grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr)); }}
.tile {{ background: #fff; border: 1px solid var(--line); border-radius: 14px;
         padding: 1.3rem 1.4rem; }}
.tile h3 {{ margin-top: 0; }}
.tile .num {{ font-family: 'DM Serif Display', serif; font-size: 1.8rem;
              color: var(--violet); line-height: 1; }}

/* ===================================================================== */
/* operator-page classes (review / chat / wizard) - same names, rebranded */
/* ===================================================================== */
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ text-align: left; padding: .45rem .6rem; border-bottom: 1px solid var(--line);
         vertical-align: top; }}
th {{ font-size: .82rem; text-transform: uppercase; letter-spacing: .03em;
     color: var(--muted); }}
.cols {{ display: flex; gap: 2rem; align-items: flex-start; flex-wrap: wrap; }}
.cols > div {{ flex: 1; min-width: 15rem; }}
.source {{ white-space: pre-wrap; background: #fff; padding: 1rem;
          border: 1px solid var(--line); border-radius: 10px; font-size: .92rem; }}
.card {{ background: #fff; border: 1px solid var(--line); border-radius: 12px;
        padding: .8rem 1rem; margin-bottom: .8rem; }}
.badge {{ display: inline-block; padding: .1rem .55rem; border-radius: 999px;
         font-size: .74rem; font-weight: 700; }}
.badge.pending  {{ background: var(--cream2); color: #555; }}
.badge.approved {{ background: #d8f2dd; color: var(--green); }}
.badge.flagged  {{ background: #fadbd8; color: var(--red); }}
.badge.new      {{ background: var(--violet); color: #fff; }}
tr.recent {{ background: #f3f0fb; }}
.action {{ font-weight: 700; }}
.action.insert    {{ color: var(--green); }}
.action.confirm   {{ color: #555; }}
.action.supersede {{ color: var(--violet); }}
.action.archive   {{ color: var(--gold); }}
.action.skip      {{ color: #777; }}
.why {{ color: var(--muted); font-size: .85rem; margin: .3rem 0; }}
.matched {{ color: var(--muted); font-size: .85rem; }}
form.verdict {{ display: inline; }}
button {{ padding: .3rem .8rem; border-radius: 999px; border: 1px solid #c9c2b6;
         background: #fff; cursor: pointer; font-family: inherit; font-size: .95rem; }}
button:hover {{ border-color: var(--violet); color: var(--violet); }}
button.flag {{ border-color: #d99; color: var(--red); }}
button.undo {{ border-color: #ccc; color: #555; font-size: .8rem; }}
input {{ font-family: inherit; font-size: 1rem; }}
input[type=text], input[name=note], input[name=path], #q {{
  padding: .4rem .55rem; border: 1px solid var(--line); border-radius: 8px;
  background: #fff; }}
.note {{ color: var(--red); font-size: .85rem; }}
.explain {{ background: #fff; border: 1px solid var(--line); border-radius: 12px;
           padding: .9rem 1.1rem; font-size: .9rem; max-width: 52rem; }}
.explain li {{ margin: .25rem 0; }}

@media (max-width: 640px) {{
  main {{ padding: 1.5rem 1.1rem 3rem; }}
  .topbar-inner {{ padding: .6rem 1.1rem; }}
  .brand .tag {{ display: none; }}
}}
"""

# Fonts + favicon for the <head>. preconnect keeps the font fetch off the
# critical path; the fallbacks in STYLE mean a blocked CDN just looks plainer.
_HEAD_LINKS = (
    f"<link rel='icon' type='image/png' href='{_FAVICON_DATA_URI}'>"
    "<link rel='preconnect' href='https://fonts.googleapis.com'>"
    "<link rel='preconnect' href='https://fonts.gstatic.com' crossorigin>"
    "<link href='https://fonts.googleapis.com/css2?"
    "family=DM+Sans:wght@400;500;600;700&"
    "family=DM+Serif+Display&display=swap' rel='stylesheet'>"
)

# Primary nav - the four doors a non-technical visitor should see. The operator
# tools live one level down, under /under-hood, so this stays uncluttered.
_NAV = [
    ("/", "Home"),
    ("/meet", "Meet a student"),
    ("/vision", "Vision"),
    ("/architecture", "Architecture"),
    ("/under-hood", "Under the hood"),
]


def _nav_html(active: str) -> str:
    links = "".join(
        f"<a href='{href}'{' class=\"active\"' if href == active else ''}>"
        f"{esc(label)}</a>"
        for href, label in _NAV
    )
    return (
        "<div class='topbar'><div class='topbar-inner'>"
        f"<a class='brand' href='/'>{LOGO_SVG}"
        "<span class='tag'>Memory Layer &middot; demo</span></a>"
        f"<nav class='nav'>{links}</nav>"
        "</div></div>"
    )


def shell(title: str, body: str, *, active: str = "",
          extra_style: str = "", scripts: str = "") -> str:
    """Wrap page BODY in the full branded document (head, nav, footer).

    active: the nav href to highlight. extra_style: page-specific CSS appended
    after the shared sheet. scripts: raw <script> markup injected before </body>.
    Returns an HTML string; callers wrap it in HTMLResponse.
    """
    style = STYLE + (extra_style or "")
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{esc(title)}</title>{_HEAD_LINKS}"
        f"<style>{style}</style></head><body>"
        f"{_nav_html(active)}<main>{body}</main>"
        "<div class='footer'>A working demo built by "
        "<a href='https://michaelratnikov.com' target='_blank' rel='noopener'>"
        "Michael Ratnikov</a> &middot; "
        "an AI coach that remembers a student as they grow.</div>"
        f"{scripts}</body></html>"
    )
