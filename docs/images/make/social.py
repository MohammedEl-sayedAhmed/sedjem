"""The GitHub social preview card, docs/images/social-preview.png (1280 x 640): the logo for dark backgrounds,
the tagline, three facts and the transcript screenshot. Run from anywhere, with Playwright and Chrome:
    python docs/images/make/social.py
The card is uploaded by hand in the repository's settings (Social preview)."""
import base64
from pathlib import Path

from playwright.sync_api import sync_playwright

R = Path(__file__).resolve().parent.parent.parent.parent
OUT = R / "docs/images/social-preview.png"
FONTS = R / "docs/video/fonts"


def uri(path, kind):
    return f"data:{kind};base64," + base64.b64encode(path.read_bytes()).decode()


faces = "\n".join(f"@font-face {{ font-family: Plex; src: url('{uri(FONTS / f'ibm-plex-sans-latin-{w}-normal.woff2', 'font/woff2')}'); "
                  f"font-weight: {w}; }}" for w in (400, 600))
logo = uri(R / "docs/brand/sedjem-logo-colour-dark.svg", "image/svg+xml")
shot = uri(R / "docs/images/transcript.png", "image/png")
html = f"""<html><head><meta charset="utf-8"><style>
{faces}
body {{ margin: 0; width: 1280px; height: 640px; overflow: hidden; font-family: Plex, sans-serif; color: #fff;
       background: radial-gradient(760px 560px at 12% 0%, rgba(20, 163, 161, .42) 0%, transparent 62%),
                   radial-gradient(700px 520px at 100% 100%, rgba(20, 163, 161, .24) 0%, transparent 60%), #0f1216; }}
.left {{ position: absolute; left: 72px; top: 96px; width: 560px; }}
.logo {{ height: 92px; display: block; }}
h1 {{ font-size: 34px; font-weight: 600; line-height: 1.3; margin: 44px 0 26px; }}
ul {{ list-style: none; padding: 0; margin: 0; font-size: 24px; line-height: 1.7; color: #d6dde4; }}
li::before {{ content: ""; display: inline-block; width: 9px; height: 9px; border-radius: 50%; background: #2dd4bf; margin: 0 16px 4px 2px; }}
.url {{ position: absolute; left: 72px; bottom: 44px; font-size: 22px; color: #9aa5b1; }}
.shot {{ position: absolute; left: 678px; top: 74px; width: 700px; height: 478px; border-radius: 16px; overflow: hidden;
        box-shadow: 0 24px 60px rgba(0,0,0,.5), 0 0 0 1px rgba(255,255,255,.14); background: #fff; }}
.shot img {{ width: 1020px; display: block; }}
</style></head><body>
<div class="left">
  <img class="logo" src="{logo}" alt="Sedjem">
  <h1>Transcripts of Egyptian Arabic–English meetings, made on your own computer</h1>
  <ul><li>Speakers told apart by voice</li><li>English terms kept in English</li>
      <li>Windows and Linux · free software, AGPL-3.0</li></ul>
</div>
<div class="shot"><img src="{shot}"></div>
<div class="url">github.com/MohammedEl-sayedAhmed/sedjem</div>
</body></html>"""
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    pg = b.new_page(viewport={"width": 1280, "height": 640}, device_scale_factor=1)
    pg.set_content(html)
    pg.wait_for_function("() => document.fonts.status === 'loaded' && [...document.images].every(i => i.complete)")
    pg.screenshot(path=str(OUT))
    b.close()
print("wrote", OUT, OUT.stat().st_size)
