"""Turns the twice-size screenshots in out/ into the README images in docs/images/: 2400 pixels wide,
256 colours, no dithering, which keeps the text sharp and the files small. Needs Pillow:
    python docs/images/make/shrink.py
    python docs/images/make/shrink.py site    # the website's captures, from out/site/ into site/captures/
The website's captures are shown at their own size, so they keep every pixel and colour: each becomes a lossless
WebP (method 6, the smallest) and an optimised PNG for a browser without WebP."""
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).parent
WIDTH = 2400
if sys.argv[1:] == ["site"]:
    site = HERE.parent.parent.parent / "site" / "captures"
    for p in sorted((HERE / "out" / "site").glob("*.png")):
        im = Image.open(p).convert("RGB")
        im.save(site / p.name, optimize=True)
        im.save(site / f"{p.stem}.webp", lossless=True, method=6, quality=100)
        print(f"{p.name}: {im.width}x{im.height} {(site / p.name).stat().st_size // 1024} KB png,"
              f" {(site / f'{p.stem}.webp').stat().st_size // 1024} KB webp")
    sys.exit()
for p in sorted((HERE / "out").glob("*.png")):
    im = Image.open(p).convert("RGB")
    h = round(im.height * WIDTH / im.width)
    im = im.resize((WIDTH, h), Image.LANCZOS).quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
    out = HERE.parent / p.name
    im.save(out, optimize=True)
    print(f"{p.name}: {WIDTH}x{h} {out.stat().st_size // 1024} KB")
