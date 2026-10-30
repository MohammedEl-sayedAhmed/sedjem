"""The website's share pictures, site/images/og-en.png and og-ar.png (1200 x 630): what LinkedIn, WhatsApp, X, Slack
and others show for a link to the English page or the Arabic one. Each is the page's hero in its own look and
language: the logo, the headline, the line of the page's title under it, the ear, and three lines of the made-up
sprint planning in docs/images/make/content.py, each speaker's name in their colour, right to left as the hero has
them. The Arabic picture is laid out right to left and names the speakers as the Arabic page does. Everything that
matters sits inside the middle 1100 x 560, as some apps crop the edges, and the text is sized to read on a
thumbnail 500 px wide. Run from anywhere, with Playwright and Pillow:
    python docs/images/make/og.py
site/build.py publishes them, and the pages name them in their og: and twitter: tags."""
import base64
import importlib.util
import io
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

R = Path(__file__).resolve().parent.parent.parent.parent
OUT = R / "site" / "images"
FONTS = R / "docs/video/fonts"
W, H = 1200, 630
SAFE = (50, 35, 1150, 595)  # left, top, right and bottom of the middle 1100 x 560
ROWS = slice(2, 5)  # RAW[2] to RAW[4]: Mona, Karim and Omar, a line that starts in English and two that start in Arabic

_spec = importlib.util.spec_from_file_location("site_build", R / "site" / "build.py")
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)  # the site's own words, names, lines and ear


def uri(path, kind):
    return f"data:{kind};base64," + base64.b64encode(path.read_bytes()).decode()


# The site's faces (site/styles.css), each limited to its own script, so Arabic and Latin each take their own face
LATIN = ("U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, U+2000-206F, "
         "U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD")
ARABIC = "U+0600-06FF, U+0750-077F, U+08A0-08FF, U+200C-200E, U+FB50-FDFF, U+FE70-FEFC"
FACES = [("IBM Plex Sans", "ibm-plex-sans-latin", w, LATIN) for w in (400, 600)] + \
        [("IBM Plex Sans Arabic", "ibm-plex-sans-arabic-arabic", w, ARABIC) for w in (400, 600)] + \
        [("IBM Plex Mono", "ibm-plex-mono-latin", 400, LATIN)]
faces = "\n".join(f"@font-face {{ font-family: '{family}'; font-weight: {w}; unicode-range: {r}; "
                  f"src: url('{uri(FONTS / f'{name}-{w}-normal.woff2', 'font/woff2')}'); }}" for family, name, w, r in FACES)
logo = uri(R / "docs/brand/sedjem-logo-colour.svg", "image/svg+xml")

# The site's light tokens (site/styles.css), its type a step larger so it reads on a thumbnail
STYLE = """
:root { --bg: #f4f5f7; --tray: #eef0f3; --card: #ffffff; --text: #18202b; --muted: #5f6b7a;
        --line: rgba(24, 32, 43, 0.12); --ear: #14a3a1;
        --s1: #2563eb; --s2: #b45309; --s3: #7c3aed; --s4: #0f766e;
        --font: "IBM Plex Sans", "IBM Plex Sans Arabic", sans-serif;
        --font-arabic: "IBM Plex Sans Arabic", "IBM Plex Sans", sans-serif; --mono: "IBM Plex Mono", monospace; }
html:lang(ar) { --font: var(--font-arabic); }
* { box-sizing: border-box; }
body { margin: 0; width: 1200px; height: 630px; overflow: hidden; padding: 40px 64px 0; background: var(--bg);
       color: var(--text); font: 400 27px/1.4 var(--font); -webkit-font-smoothing: antialiased; }
[lang="ar"] { font-family: var(--font-arabic); letter-spacing: 0; }
h1, p { margin: 0; }
.logo { display: block; height: 40px; }
.top { display: flex; justify-content: space-between; align-items: flex-start; gap: 36px; margin-top: 24px; }
h1 { font-size: 64px; font-weight: 600; line-height: 1.08; letter-spacing: -0.02em; white-space: nowrap; }
html:lang(ar) h1 { font-size: 60px; line-height: 1.25; letter-spacing: 0; }
.lead { margin-top: 16px; font-size: 26px; }
html:lang(ar) .lead { margin-top: 10px; font-size: 28px; line-height: 1.5; }
.ear { flex: none; height: 200px; fill: none; stroke: var(--ear); stroke-width: 3; stroke-linecap: round;
       stroke-linejoin: round; }
.ear path { vector-effect: non-scaling-stroke; }
.frame { margin-top: 28px; padding: 4px; border-radius: 8px; background: var(--tray); box-shadow: inset 0 0 0 1px var(--line); }
.log { margin: 0; padding: 6px 4px; list-style: none; border-radius: 4px; background: var(--card); }
.row { display: grid; grid-template-columns: 64px minmax(0, 1fr); column-gap: 16px; padding: 6px 20px; }
.ts { grid-row: 1 / span 2; padding-top: 1px; color: var(--muted); font: 400 19px/1.5 var(--mono); }
.who { color: var(--c); font-size: 22px; font-weight: 600; line-height: 1.25; }
html:lang(ar) .who { font-size: 23px; }
.say { margin: 0; font-size: 27px; line-height: 1.4; text-align: start; white-space: nowrap; }
.say-ltr { text-align: end; }
"""


def rows(m, lang):
    """The transcript's lines, as the hero writes them (site/build.py transcript), with the page's names."""
    out = []
    for (spk, _, text), x in zip(m["RAW"][ROWS], m["lines"]()[ROWS]):
        out.append(f'<li class="row" style="--c: var(--{build.SPEAKERS[int(spk) - 1]})"><span class="ts">{build.clock(x["start"])}'
                   f'</span><span class="who">{build.names(m, lang)[spk]}</span>{build.line(text)}</li>')
    return "\n".join(out)


def page(lang):
    words = build.copy(lang)
    # the headline in two lines, as the hero sets it on a desktop, its last two words on the second
    h1 = words["hero.h1"].split()
    h1 = " ".join(h1[:-2]) + "<br>" + " ".join(h1[-2:])
    lead = words["page.title"].split(": ", 1)[1] + "."  # the page's title after "Sedjem: "
    return f"""<!doctype html><html lang="{lang}" dir="{words['page.dir']}"><head><meta charset="utf-8"><style>
{faces}
{STYLE}</style></head><body>
<img class="logo" src="{logo}" alt="Sedjem">
<div class="top"><div class="words"><h1>{h1}</h1><p class="lead">{lead}</p></div>
{build.ear()}</div>
<div class="frame"><ol class="log" dir="rtl">
{rows(build.meeting(), lang)}
</ol></div>
</body></html>"""


OUT.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    try:
        b = p.chromium.launch(channel="chrome")
    except Exception:  # no Chrome installed: Playwright's own Chromium
        b = p.chromium.launch()
    pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
    for lang in build.LANGS:
        pg.set_content(page(lang))
        pg.evaluate("Promise.all([...document.fonts].map(f => f.load()))")  # every face, used or not yet
        pg.wait_for_function("() => document.fonts.status === 'loaded' && [...document.images].every(i => i.complete)")
        # nothing that matters outside the middle, and no line cut short
        astray = pg.evaluate("""([l, t, r, b]) => [...document.querySelectorAll('.logo, h1, .lead, .ear, .frame, .ts, .who, .say')]
            .filter(e => { const x = e.getBoundingClientRect();
                           return x.left < l || x.top < t || x.right > r || x.bottom > b || e.scrollWidth > e.clientWidth; })
            .map(e => e.outerHTML.slice(0, 80))""", SAFE)
        if astray:
            raise SystemExit(f"og-{lang}.png: outside the middle {SAFE} or cut short: {astray}")
        out = OUT / f"og-{lang}.png"
        # every pixel kept: Pillow only packs the file tighter
        Image.open(io.BytesIO(pg.screenshot())).convert("RGB").save(out, optimize=True)
        print("wrote", out, out.stat().st_size)
    b.close()
