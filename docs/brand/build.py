"""Builds the Sedjem logo SVGs in this folder: the lockup (icon and wordmark) and the wordmark alone, in colour for
light and dark backgrounds, mono black and mono white. The letters are outlines taken from JetBrains Mono ExtraBold
(SIL Open Font License 1.1), so the files need no font. The icons are copied from app/static, and the two colour
logos are copied back there for the app's header.

Needs fontTools and brotli (for the woff2 file):
    python docs/brand/build.py path/to/jetbrains-mono-800-normal.woff2
Then render.py makes the PNGs."""
import re
import sys
from pathlib import Path

from fontTools.pens.recordingPen import DecomposingRecordingPen, RecordingPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont

HERE = Path(__file__).parent
STATIC = HERE.parent.parent / "app/static"
EAR = "M22 14 C38 10 50 20 48 34 C47 43 40 46 37 50 C35 53 31 54 29 51 C27 48 30 45 30 41 C30 37 24 35 22 30 C19 24 18 16 22 14Z"
INNER = "M29 22 C35 21 40 26 38 32"
TEAL, BRIGHT = "#14a3a1", "#2dd4bf"
TRACKING = -20          # letter spacing, in font units (-0.02 em)
EAR_H, EAR_BOTTOM = 480, 560  # the ear as the dot of the j: height, and its lowest point above the baseline
ICON, GAP, ICON_MID = 1267, 333, 360  # icon size, gap to the text, and the icon's centre above the baseline

font = TTFont(sys.argv[1])
glyphs = font.getGlyphSet()
cmap = font.getBestCmap()
x_height = font["OS/2"].sxHeight


def contours(name):
    rec = DecomposingRecordingPen(glyphs)
    glyphs[name].draw(rec)
    out, cur = [], []
    for op in rec.value:
        cur.append(op)
        if op[0] in ("closePath", "endPath"):
            out.append(cur)
            cur = []
    return out


def ys(contour):
    return [pt[1] for _, args in contour for pt in args]


def xs(contour):
    return [pt[0] for _, args in contour for pt in args]


def path(contour_list):
    pen = SVGPathPen(glyphs)
    for c in contour_list:
        for op, args in c:
            getattr(pen, op)(*args)
    return pen.getCommands()


# Lay out "sedjem_" with the j's dot removed; the dot's centre is where the ear goes.
letters, x, ear_cx = [], 0, None
for ch in "sedjem_":
    name = cmap[ord(ch)]
    cs = contours(name)
    if ch == "j":  # the font builds j from a dotless j and a dot; keep the first, find the second's centre
        rec = RecordingPen()
        glyphs[name].draw(rec)
        (base_name, _), (dot_name, (*_, dx, _)) = (op[1] for op in rec.value)
        cs = contours(base_name)
        dot = contours(dot_name)[0]
        ear_cx = x + dx + (min(xs(dot)) + max(xs(dot))) / 2
    letters.append((ch, x, path(cs)))
    x += glyphs[name].width + TRACKING
text_w = x - TRACKING
descent = min(min(ys(c)) for ch in "j_" for c in contours(cmap[ord(ch)]))

ear_w = EAR_H * 31 / 45
ear_x, ear_top = ear_cx - ear_w / 2, EAR_BOTTOM + EAR_H


def icon_inner(file, prefix):
    """The icon's SVG body with its ids prefixed, to nest it in the lockup."""
    svg = (STATIC / file).read_text()
    body = svg[svg.index(">", svg.index("<svg")) + 1:svg.rindex("</svg>")].strip()
    return re.sub(r'(id="|url\(#)', lambda m: m.group(1) + prefix + "-", body)


def logo(text, accent, icon_file=None):
    """Text and accent colours; with icon_file, the full lockup."""
    left = ICON + GAP if icon_file else 0
    top = max(ear_top, ICON_MID + ICON / 2 if icon_file else 0)
    bottom = min(descent, ICON_MID - ICON / 2 if icon_file else 0)
    w, h = left + text_w, top - bottom
    base = top  # the baseline, measured down from the top of the file
    parts = []
    if icon_file:
        parts.append(f'<svg x="0" y="{base - ICON_MID - ICON / 2:g}" width="{ICON}" height="{ICON}" viewBox="0 0 64 64">'
                     f'{icon_inner(icon_file, "icon")}</svg>')
    g = []
    for ch, lx, d in letters:
        fill = accent if ch == "_" else text
        g.append(f'<path transform="translate({left + lx:g} {base:g}) scale(1 -1)" d="{d}" fill="{fill}"/>')
    parts.append("\n  ".join(g))
    ex, ey = left + ear_x, base - ear_top
    s = ear_w / 31
    parts.append(f'<g transform="translate({ex:.1f} {ey:.1f}) scale({s:.4f}) translate(-18 -10)">'
                 f'<mask id="ear-cut" maskUnits="userSpaceOnUse" x="18" y="10" width="31" height="45">'
                 f'<path d="{EAR}" fill="#fff"/><path d="{INNER}" fill="none" stroke="#000" stroke-width="4.2" stroke-linecap="round"/></mask>'
                 f'<rect x="18" y="10" width="31" height="45" fill="{accent}" mask="url(#ear-cut)"/></g>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:g} {h:g}" width="{w / 20:.0f}" height="{h / 20:.0f}">\n  '
            + "\n  ".join(parts) + "\n</svg>\n")


VERSIONS = {  # name: (text, accent, icon)
    "colour": ("#111111", TEAL, "icon.svg"),
    "colour-dark": ("#ffffff", BRIGHT, "icon.svg"),
    "mono-black": ("#000000", "#000000", "icon-mono-black.svg"),
    "mono-white": ("#ffffff", "#ffffff", "icon-mono-white.svg"),
}
for name, (text, accent, icon) in VERSIONS.items():
    (HERE / f"sedjem-logo-{name}.svg").write_text(logo(text, accent, icon))
    (HERE / f"sedjem-wordmark-{name}.svg").write_text(logo(text, accent))
for name in ("colour", "colour-dark"):  # the app's header shows the logo, switching with the theme
    (STATIC / f"logo-{name}.svg").write_text((HERE / f"sedjem-logo-{name}.svg").read_text())
for src, dst in (("icon.svg", "colour"), ("icon-mono-black.svg", "mono-black"), ("icon-mono-white.svg", "mono-white")):
    (HERE / f"sedjem-icon-{dst}.svg").write_text((STATIC / src).read_text())
print("ok")
