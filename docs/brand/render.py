"""Renders the PNGs in png/ from the SVGs in this folder (transparent backgrounds), and the overview sheet
brand-sheet.png. Needs Playwright with Chrome:
    python docs/brand/render.py"""
import base64
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
OUT = HERE / "png"
OUT.mkdir(exist_ok=True)


def uri(svg):
    return "data:image/svg+xml;base64," + base64.b64encode(svg.read_bytes()).decode()


HEIGHTS = {"logo": (128, 512), "wordmark": (128, 512), "icon": (256, 1024)}


def page(browser, w, h, body, bg="transparent"):
    pg = browser.new_page(viewport={"width": w, "height": h})
    pg.set_content(f'<html><body style="margin:0;background:{bg}">{body}</body></html>')
    return pg


with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    for svg in sorted(HERE.glob("sedjem-*.svg")):
        kind = svg.stem.split("-")[1]
        text = svg.read_text()
        vw, vh = (float(v) for v in text.split('viewBox="')[1].split('"')[0].split()[2:])
        for h in HEIGHTS[kind]:
            w = round(vw * h / vh)
            pg = page(b, w, h, f'<img src="{uri(svg)}" style="display:block;width:{w}px;height:{h}px">')
            pg.screenshot(path=str(OUT / f"{svg.stem}-{h}.png"), omit_background=True)
            pg.close()
    cells = ""
    for name, bg in (("colour", "#ffffff"), ("colour-dark", "#0f1216"), ("mono-black", "#ffffff"), ("mono-white", "#0f1216")):
        cells += (f'<div style="background:{bg};border-radius:18px;padding:40px 44px;display:flex;align-items:center;gap:44px;'
                  f'border:1px solid #e3e6ea"><img src="{uri(HERE / f"sedjem-logo-{name}.svg")}" style="height:84px">'
                  f'<img src="{uri(HERE / f"sedjem-icon-{name.replace("-dark", "")}.svg")}" style="height:64px;margin-left:auto">'
                  f'<img src="{uri(HERE / f"sedjem-icon-{name.replace("-dark", "")}.svg")}" style="height:28px"></div>')
    pg = page(b, 1400, 400, f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;padding:28px">{cells}</div>', "#eef0f3")
    pg.screenshot(path=str(HERE / "brand-sheet.png"), full_page=True)
    b.close()
print("ok")
