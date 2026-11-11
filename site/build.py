"""Build Sedjem's website, which GitHub Pages serves at https://mohammedel-sayedahmed.github.io/sedjem/: the English
page at the top and the Arabic page at ar/.

Both pages come from the one template, site/index.html, filled in with the words of copy/en.toml or copy/ar.toml.
The transcript in the hero, its first lines as SRT and the speakers' shares come from the made-up sprint planning
in docs/images/make/content.py, the meeting the README screenshots show, and the version from app/__init__.py; the
Arabic page gives the speakers Arabic names. The ear beside the hero is the brand icon's, read from docs/brand/. The
pictures of the app are the captures in site/captures/, made by
docs/images/make/screenshots.py (its "site" shot) and shrink.py, each in a desktop and a phone form, light and dark,
as lossless WebP with a PNG for browsers without WebP. Next to the pages go the stylesheet, the scripts, the 404 page and only the files they use: the logo from docs/brand/, the
captures, each page's share picture from site/images/ (made by docs/images/make/og.py), the font files the
stylesheet names from docs/video/fonts/ with their licence, and the app's icon. Each page counts its visits anonymously with counts.js, which sends to Aptabase only
from the published host (COUNTS_HOST), so a local preview sends nothing; without a key (COUNTS_KEY) the build
leaves it out. Standard library only.

"Benchmarks", after the facts, is a grid of the same figure cards over small charts of bars: the word error rate and
the English terms kept on Perle, the speaker labels on ArzEn and AMI, and the private meetings' difference from
ElevenLabs, speed and speaker labels. Its numbers are the docs', read with docs/charts.py's own sources
(results/summary.json, results/*-diarization.json and docs/charts-data.json) and rounded as the docs round them, so the
page and the docs can't drift; those files are the repository's, and one that can't be read stops the build.

"Sedjem in numbers", near the end, is a grid of charts drawn here as SVG: GitHub's downloads, stars and page views
from the files the Repository stats workflow keeps on the stats branch, read from a folder given with --stats, and
the anonymous counts' totals from docs/usage.json (tools/usage_totals.py). The downloads lead, a wide line with each
stable release tagged by its version in mono; the page views and the stars are smaller. In one bar the part the
bar is about is the brand's teal and the other parts are greys, so one accent leads. The counts' card, "The anonymous
counts", shows the newest finished month and, beside it, the headline numbers of the month so far when
docs/usage.json has one ("so far, to 9 November"), then the month so far's visits on a map and a calendar, then a
census of the newest finished month in four groups, each breakdown in the form that fits its data:
the installs as a row of the brand's ears, a quiet list of names and shares (largest first, "other" last, a hairline
under each row) for the countries, regions, systems, graphics cards, memory, versions, models, languages, lengths,
sites and link tags, the devices as three line glyphs (Lucide, ISC) with their shares, local or hosted as one small
ring, the website's visits by country on a choropleth world map drawn from site/worldmap.json (made by
docs/images/make/worldmap.py from Natural Earth's public-domain data; countries are not folded there) and by day in a
calendar. Stacked bars stay for a whole split in two or three parts (the releases, the systems). One accent leads in
each picture, the brand's teal, and the rest is grey. A value with fewer than 3 installs, transcriptions or visitors
is added to "other", a breakdown that would show one group is left out, and so is a group with none. The downloads link the
latest release's files, with their names, sizes and SHA256, read from a folder given with --release. The build never
fetches anything: pages.yml downloads the files into the folders with the API before it builds. Without the stats
folder GitHub's charts are left out, without docs/usage.json the page says when the first totals come, and without
the release folder the downloads link the release's page with their general wording. A file that is missing or
can't be read leaves its part out, with a line in the log, and never stops the build.

    python site/build.py [out_dir] [--stats DIR] [--release DIR]   # out_dir defaults to _site/ in the repository
    python -m http.server 8000 -d _site     # then open http://127.0.0.1:8000/ and http://127.0.0.1:8000/ar/

To try the numbers and the downloads locally, download the files without checking the stats branch out (a checkout
counts as a clone) and without downloading a release's files (a download adds to their count):
    for f in downloads.csv repo.csv views.csv; do
      gh api -H "Accept: application/vnd.github.raw" "repos/MohammedEl-sayedAhmed/sedjem/contents/$f?ref=stats" > stats/$f
    done
    gh api repos/MohammedEl-sayedAhmed/sedjem/releases/latest > release/release.json

The 404 page links its files from the site's path (/sedjem/), as GitHub Pages serves it at any address, so it
shows unstyled in a local preview. site/DESIGN.md has the design notes, and tests/test_site.py checks the result;
.github/workflows/pages.yml builds and publishes it.
"""
import argparse
import ast
import calendar
import csv
import html
import json
import math
import os
import re
import shutil
import struct
import sys
import tomllib
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
CONTENT = ROOT / "docs" / "images" / "make" / "content.py"
CAPTURES = SITE / "captures"  # docs/images/make/screenshots.py site, then shrink.py site
# Each capture's pixels per css pixel: the desktop form was taken at twice the size, the phone form at three times
DENSITY = {"wide": 2, "narrow": 3}
# Below this width the page shows each capture's phone form: the desktop form is up to 656 px wide, and from here
# the column holds it at its own size, a scroll bar included
NARROW = "(max-width: 749px)"
ICON = ROOT / "docs" / "brand" / "sedjem-icon-colour.svg"  # the hero's ear, and the logo tile in the 404 page
URL = "https://mohammedel-sayedahmed.github.io/sedjem/"
BASE = urlsplit(URL).path  # /sedjem/, where the 404 page finds the site's files

# The anonymous counts (counts.js; docs/privacy.md, "The website"): the website's own Aptabase app, whose key is public
# as it is in the page, and the one host the pages send from. An empty key leaves the counting out of the build.
COUNTS_KEY = "A-EU-3941212028"
COUNTS_HOST = urlsplit(URL).hostname
COUNTS_KEY_FORMAT = re.compile(r"A-EU-[A-Za-z0-9]{1,64}")  # a key of Aptabase's EU region, the one the counts go to
COUNTS_ENDPOINT = "https://eu.aptabase.com/api/v0/event"  # one event a request

# The numbers (docs/13-usage-counts.md, "Public totals"): the stats branch's files the page reads, with the columns
# each must have (.github/scripts/repo_stats.py writes them), the totals of the anonymous counts, and where the
# section's last line sends the reader for the charts and for how the totals are made
STATS = {"downloads.csv": ("date", "release", "asset", "downloads"), "repo.csv": ("date", "stars"),
         "views.csv": ("date", "views")}
USAGE = ROOT / "docs" / "usage.json"
CHARTS = "https://github.com/MohammedEl-sayedAhmed/sedjem/tree/stats#readme"
METHOD = "https://github.com/MohammedEl-sayedAhmed/sedjem/blob/main/docs/13-usage-counts.md#public-totals"
VIEWS_DAYS = 30  # the page views of the repository: the days added up, up to the last one recorded
STABLE = re.compile(r"v?(\d+(?:\.\d+)*)")  # a release's tag; one with more after it (-preview.20261029) is a preview
ENGINES = ("cohere", "whisper_medium", "whisper_large", "other_local", "hosted")  # the models, as docs/usage.json names them
LEAST = 3  # the installs, transcriptions or visitors a group needs to be named; the smaller ones are added up as "other"
MAP = SITE / "worldmap.json"  # the countries' outlines, made by docs/images/make/worldmap.py from Natural Earth
SMALL = 40  # the height of the page views' and the stars' lines
LEAD = 170  # the height of the lead chart, the downloads, in pixels
EARS = 24  # the most ears in the installs' unit chart; past it one ear stands for EACH's first number that fits
EACH = (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 5000, 10000)
REST = ("other", "previews")  # the parts that add up the rest, in the lightest grey
# What the census draws, in its order: each group with its breakdowns (the name in docs/usage.json's counts, how a value
# is named, and what is counted: installs, transcriptions, or the website's page views)
CENSUS = (("where", (("countries", "country", "installs"), ("regions", "place", "installs"),
                    ("wcountries", "country", "views"))),
          ("computers", (("systems", "text", "installs"), ("gpu", "text", "installs"), ("ram", "gb", "installs"),
                         ("kind", "text", "installs"), ("versions", "text", "installs"))),
          ("how", (("models", "model", "transcriptions"), ("hosted", "text", "transcriptions"),
                   ("languages", "text", "transcriptions"), ("lengths", "min", "transcriptions"))),
          ("visitors", (("devices", "text", "views"), ("wsystems", "text", "views"),
                        ("referrers", "text", "views"), ("sources", "text", "views"))))
# The latest release's files, which the download links point at (pages.yml downloads the release's details into a
# folder given with --release): each system's file by its name, where GitHub serves them, and the form of a SHA256
LATEST = "https://github.com/MohammedEl-sayedAhmed/sedjem/releases/latest"  # without the details, the release's page
RELEASE_FILES = {"windows": re.compile(r"Sedjem-[\w.-]+-setup\.exe"), "linux": re.compile(r"Sedjem-[\w.-]+-linux-x64\.tar\.gz")}
DOWNLOADS = "https://github.com/MohammedEl-sayedAhmed/sedjem/releases/download/"
SHA256 = re.compile(r"[0-9a-f]{64}")
DAILY = 35  # the most days a running total is drawn by the day; a longer one is drawn by the week
# How each page writes a number: the thousands separator and the decimal point. Both pages write Western digits, as
# the Arabic copy does everywhere, grouped as Egypt groups them.
NUMERALS = {"en": (",", "."), "ar": (",", ".")}

# The benchmarks (docs/03-results.md and docs/04-speaker-labels.md): docs/charts.py, whose sources give each number of
# the docs' charts as the docs round it; the docs the section's last line links; and the public test sets, each where
# it is downloaded, with the licence the docs give (copy/*.toml names them)
BENCH = ROOT / "docs" / "charts.py"
DOCS = "https://github.com/MohammedEl-sayedAhmed/sedjem/blob/main/docs/"
TEST_SETS = {"perle": ("Perle", "https://huggingface.co/datasets/Perle-ai/ASR_Code_Switch"),
             "arzen": ("ArzEn", "https://sites.google.com/view/arzen-corpus/resources"),
             "ami": ("AMI", "https://groups.inf.ed.ac.uk/ami/corpus/")}
# The models on the bars: the name on a bar and the one in its table. Sedjem runs the first five, three to transcribe
# and two to tell the speakers apart; the others are what they were measured against.
MODELS = {"cohere": ("Cohere", "Cohere Transcribe Arabic"), "medium": ("whisper-medium", "whisper-medium code-switching"),
          "large": ("Whisper large-v3", "Whisper large-v3"), "titanet": ("TitaNet-small", "TitaNet-small"),
          "nemotron": ("Nemotron", "Nemotron-3-Diarization"), "audar": ("Audar", "Audar-ASR-V1-Turbo"),
          "r2t2": ("R2T2", "R2T2"), "qwen": ("Qwen3-ASR", "Qwen3-ASR-1.7B"), "elevenlabs": ("ElevenLabs", "ElevenLabs")}
IN_SEDJEM = ("cohere", "medium", "large", "titanet", "nemotron")
# Each model's run on Perle at 16 kHz in results/summary.json, the rows of docs/03-results.md's Perle tables: Sedjem's
# three as it runs them, then the three that were also tried, which aren't used
PERLE = {"cohere": ("cohere-transcribe-arabic-07-2026-Q4_K_M", "ar"),
         "medium": ("whisper-whisper-medium-arabic-codeswitched-ct2", "ar"), "large": ("whisper-large-v3", "ar-p3c2e92"),
         "audar": ("Audar-ASR-V1-Turbo-Q4_K_M", "ar-prompt-clip"), "r2t2": ("Confucius4-R2T2-Q8_0", "ar-clip"),
         "qwen": ("Qwen3-ASR-1.7B-Q8_0", "ar-clip")}
SPEAKER_SYSTEMS = {"titanet": "TitaNet-small", "nemotron": "Nemotron, as the app"}  # in results/*-diarization.json
# The cards in the page's order: the public test sets on the first row, the private meetings on the second
BENCHMARKS = ("wer", "english", "nemotron", "meetings", "speed", "speakers")

# Each language's page: its folder in the site, and the fonts its first view needs first
LANGS = {
    "en": {"folder": "", "preload": ("ibm-plex-sans-latin-600", "ibm-plex-sans-latin-400", "ibm-plex-sans-arabic-arabic-400")},
    "ar": {"folder": "ar/", "preload": ("ibm-plex-sans-arabic-arabic-600", "ibm-plex-sans-arabic-arabic-400", "ibm-plex-sans-latin-400")},
}
PAGES = ("index.html", "ar/index.html", "404.html")
COPIED = ("styles.css", "site.js")
COUNTER = "counts.js"  # copied too when the pages count
# Every other file the site publishes: its path in the site, and where it comes from in the repository. The font
# files are added from the stylesheet's @font-face rules, so only the weights it uses are published.
FILES = {
    "brand/sedjem-logo-colour.svg": "docs/brand/sedjem-logo-colour.svg",
    "brand/sedjem-logo-colour-dark.svg": "docs/brand/sedjem-logo-colour-dark.svg",
    "icon.svg": "app/static/icon.svg",
    "icon-192.png": "app/static/icon-192.png",
    "images/og-en.png": "site/images/og-en.png",  # each page's share picture, made by docs/images/make/og.py
    "images/og-ar.png": "site/images/og-ar.png",
    "fonts/OFL-IBM-Plex.txt": "docs/video/fonts/OFL-IBM-Plex.txt",
}
FONTS = "docs/video/fonts"

HERO = slice(0, 6)            # RAW[0] to RAW[5]: the lines of the hero's transcript
SRT = slice(0, 3)             # and the first three, as the app exports them
SPEAKERS = ("s1", "s2", "s3", "s4")
NAMES_AR = {"1": "منى", "2": "كريم", "3": "عمر", "4": "نور"}  # content.py's Mona, Karim, Omar and Nour
# The pictures of the app, in the order the page shows them; their alt text is the copy key given
SHOTS = {"speakers": "voices.panel_alt", "local": "local.row_alt", "reading": "reading.shot_alt",
         "review": "fixing.shot_alt", "compare": "comparing.shot_alt"}

# Runs of the other script inside a line: English words in an Arabic line, Arabic after English in the other
LATIN = re.compile(r"[A-Za-z][\w'’-]*(?:[ ,.?!]+[A-Za-z][\w'’-]*)*", re.ASCII)
ARABIC = re.compile(r"[\u0600-\u06ff][^A-Za-z]*")
IN_ENGLISH = '<span lang="en">'
IN_ARABIC = '<span lang="ar" dir="rtl">'

# A run of Latin words in the Arabic page's copy: a word (AGPL-3.0, i5-1245U and Sedjem/Sedjem included), with a
# number before it when it is the number's unit (778 MB), then more words or numbers joined by spaces, slashes or an
# arrow (Windows 10, Python 3.12, Settings → Models)
NBSP = "\N{NO-BREAK SPACE}"
LATIN_WORDS = re.compile(rf"(?:\d+(?:\.\d+)?[ {NBSP}](?=[A-Za-z]))?[A-Za-z][\w'’-]*(?:\.\w+)*"
                         rf"(?:(?:[ {NBSP}/]+|\s*→\s*)[A-Za-z0-9][\w'’-]*(?:\.\w+)*)*", re.ASCII)
MARKUP = re.compile(r"(<[^>]*>|&\w+;|\{\{[\w.]+\}\})")  # tags, entities and placeholders, left as they are
IN_LATIN = '<span lang="en" dir="ltr" translate="no">'
NAME = '<span translate="no">'  # a product's name on the English page, as the copy marks one
PLACEHOLDER = re.compile(r"\{\{([\w.]+)\}\}")


def meeting():
    """docs/images/make/content.py's names, read without importing it (so nothing is written next to it)."""
    names = {}
    exec(compile(CONTENT.read_text(encoding="utf-8"), str(CONTENT), "exec"), names)
    return names


def version():
    """__version__ from app/__init__.py, read without importing the app."""
    for node in ast.parse((ROOT / "app" / "__init__.py").read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "__version__" for t in node.targets):
            return node.value.value
    raise SystemExit("app/__init__.py has no __version__")


def half_up(x):
    """JavaScript's Math.round, which the app uses for the shares it shows."""
    return math.floor(x + 0.5)


def clock(seconds):
    """A time as the app writes it in a transcript: m:ss."""
    seconds = math.floor(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def starts_in_latin(text):
    """Whether a line's first letter is Latin, which is what dir="auto" goes by."""
    for ch in text:
        if ch.isalpha():
            return ch.isascii()
    return True


def runs(text, pattern, open_tag):
    """The text, escaped, with each run of the other script in its own element."""
    out, at = [], 0
    for m in pattern.finditer(text):
        out += [html.escape(text[at:m.start()], quote=False), open_tag, html.escape(m.group(), quote=False), "</span>"]
        at = m.end()
    return "".join(out + [html.escape(text[at:], quote=False)])


def line(text):
    """One spoken line in its own direction: an Arabic line right to left with its English words marked as
    English, and a line that starts in English left to right (dir="auto") with its Arabic marked as Arabic; that
    one still hangs from the transcript's right edge (say-ltr). The language is set on each line, so it holds on
    both pages."""
    if starts_in_latin(text):
        return f'<p class="say say-ltr" lang="en" dir="auto">{runs(text, ARABIC, IN_ARABIC)}</p>'
    return f'<p class="say" lang="ar" dir="rtl">{runs(text, LATIN, IN_ENGLISH)}</p>'


def names(m, lang):
    """The speakers' names: content.py's on the English page, the same names in Arabic on the Arabic one."""
    return NAMES_AR if lang == "ar" else m["NAMES"]


def transcript(m, lang):
    """The hero's rows: time, speaker and line, for RAW[0] to RAW[5]."""
    rows = []
    for (spk, _, text), x in zip(m["RAW"][HERO], m["lines"]()[HERO]):
        rows.append(f'            <li class="row" style="--c: var(--{SPEAKERS[int(spk) - 1]})">'
                    f'<time class="ts" datetime="PT{math.floor(x["start"])}S">{clock(x["start"])}</time>'
                    f'<span class="who">{html.escape(names(m, lang)[spk])}</span>{line(text)}</li>')
    return "\n".join(rows)


def stamp(t):
    """A time as an SRT file writes it, as app/transcript.py does: 00:00:05,300."""
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def srt(m, lang):
    """The first lines of the transcript as the app exports them as SRT (app/transcript.py to_srt): a number, the
    times, and the speaker's name before the line, which reads right to left where it starts in Arabic, as the
    app's right-to-left mark makes a player show it."""
    cues = []
    for i, ((spk, _, text), x) in enumerate(zip(m["RAW"][SRT], m["lines"]()[SRT]), 1):
        name = names(m, lang)[spk]
        if starts_in_latin(text):
            say = f'<span lang="en" dir="ltr">{runs(name, ARABIC, IN_ARABIC)}: {runs(text, ARABIC, IN_ARABIC)}</span>'
        else:
            say = f'<span lang="ar" dir="rtl">{runs(name, LATIN, IN_ENGLISH)}: {runs(text, LATIN, IN_ENGLISH)}</span>'
        cues.append(f"{i}\n{stamp(x['start'])} --> {stamp(x['end'])}\n{say}")
    return "\n\n".join(cues)


ICONS = {  # the theme button's two faces, the theme on show: a sun on a light page, the app's moon on a dark one
    "light": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2'
             'M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/>',
    "dark": '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
}


def theme_button(words, where):
    """The theme button, in the header's row from 700 px or in the phone's menu, where its name shows too. The
    stylesheet shows the icon of the theme on show, and the name says what a press does: here the light page's, which
    site.js changes on a dark one."""
    name = words["theme.to_dark"]
    icons = "".join(f'<svg class="icon-{k}" viewBox="0 0 24 24" aria-hidden="true" focusable="false">{v}</svg>'
                    for k, v in ICONS.items())
    shown = f'<span class="theme-name" aria-hidden="true">{name}</span>' if where == "menu" else ""
    indent = "      " if where == "menu" else "  "
    return (f'{indent}<button class="theme theme-{where}" type="button" data-theme-switch data-to-dark="{name}" '
            f'data-to-light="{words["theme.to_light"]}" aria-label="{name}" title="{name}">{icons}{shown}</button>')


def png_size(path):
    """A PNG file's width and height in pixels, from its header."""
    return struct.unpack(">II", path.read_bytes()[16:24])


def shot(name, alt, root):
    """One picture of the app, light and dark, each a desktop capture with its phone form on a phone, as lossless
    WebP with the same pixels in PNG for a browser without WebP. Each is set at its own size, its pixels over the
    density it was taken at, and the stylesheet never draws it smaller."""
    out = []
    for theme in ("light", "dark"):
        sizes = {}
        for form, density in DENSITY.items():
            w, h = png_size(CAPTURES / f"{name}-{form}-{theme}.png")
            sizes[form] = f'width="{w // density}" height="{h // density}"'
        src = lambda form, ext: f"{root}captures/{name}-{form}-{theme}.{ext}"
        srcset = lambda form, ext: f'srcset="{src(form, ext)} {DENSITY[form]}x" {sizes[form]}'
        out.append(f'          <picture class="view-{theme}">'
                   f'<source media="{NARROW}" type="image/webp" {srcset("narrow", "webp")}>'
                   f'<source media="{NARROW}" {srcset("narrow", "png")}>'
                   f'<source type="image/webp" {srcset("wide", "webp")}>'
                   f'<img src="{src("wide", "png")}" {srcset("wide", "png")} alt="{alt}" loading="lazy" decoding="async">'
                   f'</picture>')
    return "\n".join(out)


def shares(m):
    """Each speaker's share of the talking in the whole made-up meeting, in per cent, worked out as the app's
    sidebar does (app/static/app.js speakerStats): the sum of their lines' lengths over everyone's."""
    talk = {}
    for x in m["lines"]():
        talk[x["speaker"]] = talk.get(x["speaker"], 0) + max(0, x["end"] - x["start"])
    total = sum(talk.values())
    return [(sid, m["NAMES"][sid], talk[sid] / total * 100) for sid in sorted(talk, key=int)]


def shares_phrase(m, lang):
    """The speakers and their shares as the Speakers panel shows them, for its alt text: "Mona 38%, Karim 22%,
    Omar 22% and Nour 17%", or the same joined the Arabic way."""
    parts = [f"{name} {half_up(share)}%" for _, name, share in shares(m)]
    if lang == "ar":
        return "، و".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def ear():
    """The ear of the brand icon (docs/brand/sedjem-icon-colour.svg), its outline and inner curve exactly as the
    icon draws them, for the hero to show as a line drawing: the logo's own shape, taken at build time rather than
    redrawn. The stylesheet strokes it in the brand's teal."""
    outline, curve = re.findall(r'<path d="([^"]+)"', ICON.read_text(encoding="utf-8"))
    return (f'      <svg class="ear" viewBox="17 10.5 33.6 45" aria-hidden="true" focusable="false">'
            f'<path d="{outline}"/><path d="{curve}"/></svg>')


def icon():
    """The brand's logo tile (docs/brand/sedjem-icon-colour.svg) as it is, set in the page, for the 404 page, which
    can't count on finding the site's files."""
    svg = ICON.read_text(encoding="utf-8").strip()
    return svg.replace('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">',
                       '<svg class="mark" viewBox="0 0 64 64" role="img" aria-label="Sedjem">', 1)


def counts(src, page, key, host, offered=None):
    """The script that counts a page, with what it needs on its element: the page, the key, the address, the host it
    may send from and the version the page offers (the latest release's, or the app's without it). Nothing without
    a key."""
    if not key:
        return ""
    if not COUNTS_KEY_FORMAT.fullmatch(key):
        raise SystemExit(f"{key!r} is not a key of Aptabase's EU region")
    return (f'  <script src="{src}" defer data-page="{page}" data-key="{key}" data-endpoint="{COUNTS_ENDPOINT}" '
            f'data-host="{html.escape(host)}" data-version="{html.escape(offered or version())}"></script>')


def note(message):
    """A line in the build's log about numbers left out: a warning in a GitHub Actions run."""
    print(f"{'::warning::' if os.environ.get('GITHUB_ACTIONS') == 'true' else ''}numbers: {message}", file=sys.stderr)


def stats_rows(folder, name):
    """A stats file's rows with the columns it must have: the date as a date, the release and the asset as text and the
    rest as whole numbers. None, with a line in the log, when the file is missing or isn't what repo_stats.py
    writes."""
    def value(key, text):
        if key == "date":
            return date.fromisoformat(text)
        if key in ("release", "asset"):
            if not text:
                raise ValueError(f"no {key}")
            return text
        if not text.isdigit():
            raise ValueError(f"{key} {text!r} isn't a count")
        return int(text)
    try:
        with open(Path(folder) / name, newline="", encoding="utf-8") as f:
            rows = [{key: value(key, r[key] or "") for key in STATS[name]} for r in csv.DictReader(f)]
        if not rows:
            raise ValueError("it has no rows")
        return rows
    except (OSError, UnicodeDecodeError, csv.Error, KeyError, ValueError) as e:
        note(f"{name} can't be read ({e!r}); its numbers are left out")
        return None


def version_key(tag):
    """A release's version as numbers, to find the newest by; None for a preview."""
    m = STABLE.fullmatch(tag)
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def release(folder):
    """The latest release from folder: its version and, for Windows and Linux, its file's name, size, address and
    SHA256, from release.json (gh api repos/OWNER/NAME/releases/latest). The SHA256 is the one SHA256SUMS.txt gives,
    when the folder has it, or else GitHub's digest of the file; a file whose two differ shows neither. None without a
    folder, and None with a line in the log when release.json is missing or isn't a release's."""
    if folder is None:
        return None
    try:
        data = json.loads((Path(folder) / "release.json").read_text(encoding="utf-8"))
        tag = str(data["tag_name"])
        stable = re.fullmatch(r"v(\d+(?:\.\d+)*)", tag)
        if not stable or data.get("prerelease") or data.get("draft"):
            raise ValueError(f"{tag!r} isn't a release")
        sums = {}
        listed = Path(folder) / "SHA256SUMS.txt"
        if listed.is_file():
            for row in listed.read_text(encoding="utf-8").splitlines():
                parts = row.split()
                if len(parts) == 2 and SHA256.fullmatch(parts[0].lower()):
                    sums[parts[1].lstrip("*")] = parts[0].lower()
        files = {}
        for asset in data["assets"]:
            name = str(asset["name"])
            system = next((k for k, form in RELEASE_FILES.items() if form.fullmatch(name)), None)
            if system is None or system in files:
                continue
            if asset["browser_download_url"] != f"{DOWNLOADS}{tag}/{name}":
                raise ValueError(f"{name} is served from {asset['browser_download_url']!r}")
            digest = str(asset.get("digest") or "").removeprefix("sha256:")
            sha = sums.get(name) or digest
            if sums.get(name) and digest and digest != sums[name]:
                note(f"{name}: SHA256SUMS.txt and GitHub's digest differ, so its SHA256 is left out")
                sha = None
            files[system] = {"name": name, "size": count(asset["size"]), "url": f"{DOWNLOADS}{tag}/{name}",
                             "sha256": sha if SHA256.fullmatch(sha or "") else None}
    except FileNotFoundError:
        note("no release.json, so the downloads keep their general wording and link the release's page")
        return None
    except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError, AttributeError) as e:
        note(f"release.json can't be read ({e!r}); the downloads keep their general wording and link the release's page")
        return None
    return {"version": stable.group(1), "files": files}


def size(n, lang):
    """A file's size as GitHub's release page gives it, in megabytes of 1,048,576 bytes, with the bytes in its data
    element: 87.7 MB."""
    text = f"{n / 2 ** 20:,.1f} MB"
    return f'<data value="{n}">{text if lang == "en" else IN_LATIN + text + "</span>"}</data>'


def release_file(rel, system, words, lang):
    """What a download section's way says of its file under its button: its name and size, and its SHA256 in mono,
    selected whole by one click; nothing without the release's details."""
    f = (rel or {}).get("files", {}).get(system)
    if not f:
        return ""
    name = f'<span class="file-name" lang="en" dir="ltr" translate="no">{html.escape(f["name"])}</span>'
    rows = [f'<div><dt>{words["get.file"]}</dt><dd>{name} {size(f["size"], lang)}</dd></div>']
    if f["sha256"]:
        rows.append(f'<div><dt>{words["get.sha256"]}</dt><dd><code class="sum" lang="en" dir="ltr" translate="no">'
                    f'{f["sha256"]}</code></dd></div>')
    return f'<dl class="file">{"".join(rows)}</dl>'


def weekly(series):
    """A running total of more than DAILY days by the week, so a chart and its table grow by a point a week: the first
    day, the last, and every seventh day back from the last."""
    if len(series) <= DAILY:
        return series
    last = series[-1][0]
    return [p for i, p in enumerate(series) if i == 0 or (last - p[0]).days % 7 == 0]


def platform_of(asset):
    """Which system a release file is for, by its name: the installer is Windows', the archive Linux's."""
    name = asset.lower()
    return "windows" if name.endswith((".exe", ".msi")) else "linux" if "linux" in name else "other"


def github(folder):
    """GitHub's numbers from the stats files in folder, as many as could be read. From downloads.csv, whose rows are
    a snapshot of every release file's running total each day: the day they are of, the app's downloads in all on each
    day (by the week past DAILY days; its files, not the checksums; previews included, as the README's badge counts
    them), each release's on the
    newest day with the previews added up apart, the downloads by system, and the day each release that isn't a
    preview was first counted, when that is after the first day. From repo.csv the stars each day (by the week past
    DAILY days), and from views.csv
    the page views of each day in the last VIEWS_DAYS days recorded, or from the first day recorded when that is
    later (views_whole False). None without a folder."""
    if folder is None:
        return None
    gh = {}
    downloads = stats_rows(folder, "downloads.csv")
    if downloads:
        app = [r for r in downloads if not r["asset"].lower().endswith(".txt")]
        days = sorted({r["date"] for r in downloads})
        newest = [r for r in app if r["date"] == days[-1]]
        by_release = {}
        for r in newest:
            by_release[r["release"]] = by_release.get(r["release"], 0) + r["downloads"]
        stable = sorted((tag for tag in by_release if version_key(tag)), key=version_key, reverse=True)
        first_seen = {}
        for r in sorted(app, key=lambda r: r["date"]):
            first_seen.setdefault(r["release"], r["date"])
        marks = {}
        for tag in sorted(stable, key=version_key):  # the newest of a day's releases names it
            if first_seen[tag] > days[0]:
                marks[first_seen[tag]] = STABLE.fullmatch(tag).group(1)
        platforms = {}
        for r in newest:
            platforms[platform_of(r["asset"])] = platforms.get(platform_of(r["asset"]), 0) + r["downloads"]
        gh.update(day=days[-1], downloads=weekly([(d, sum(r["downloads"] for r in app if r["date"] == d)) for d in days]),
                  releases=[(STABLE.fullmatch(tag).group(1), by_release[tag]) for tag in stable],
                  previews=sum(n for tag, n in by_release.items() if not version_key(tag)),
                  marks=sorted(marks.items()),
                  platforms=[(p, platforms[p]) for p in ("windows", "linux", "other") if platforms.get(p)])
    repo = stats_rows(folder, "repo.csv")
    if repo:
        stars = {r["date"]: r["stars"] for r in sorted(repo, key=lambda r: r["date"])}
        gh.update(day=max(gh.get("day", max(stars)), max(stars)), stars=weekly(sorted(stars.items())))
    views = stats_rows(folder, "views.csv")
    if views:
        per_day = {r["date"]: r["views"] for r in views}
        last, first = max(per_day), min(per_day)
        start = max(last - timedelta(VIEWS_DAYS - 1), first)
        gh.update(views=[(start + timedelta(i), per_day.get(start + timedelta(i), 0)) for i in range((last - start).days + 1)],
                  views_whole=first <= last - timedelta(VIEWS_DAYS - 1))
        gh.setdefault("day", last)
    if not gh:
        note("no stats file could be read, so GitHub's numbers are left out")
    return gh or None


def count(value):
    """A whole number from 0 up as docs/usage.json has it, or ValueError."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{value!r} isn't a count")
    return value


def tally(source, *path):
    """The counts at path in source as {name: count}, or {} when they are missing or aren't counts."""
    for key in path:
        if not isinstance(source, dict) or key not in source:
            return {}
        source = source[key]
    try:
        return {str(k): count(v) for k, v in source.items()}
    except (AttributeError, ValueError):
        return {}


def folded(counts):
    """Counts as [(name, count)] with the groups under LEAST (and "unknown") added up as "other", the largest first
    and "other" last."""
    named = {k: n for k, n in counts.items() if n >= LEAST and k not in ("other", "unknown")}
    rest = sum(counts.values()) - sum(named.values())
    return sorted(named.items(), key=lambda kv: (-kv[1], kv[0])) + ([("other", rest)] if rest else [])


def linked(w, key, listed):
    """The website's page views with no link (or no tag), then those by site (or tag): {"none": n, ...}."""
    none = w.get(key, {}).get("none") if isinstance(w, dict) and isinstance(w.get(key), dict) else None
    return ({"none": none} if isinstance(none, int) and not isinstance(none, bool) and none >= 0 else {}) \
        | tally(w, key, listed)


def census_counts(t, w):
    """Every breakdown the census may draw, as {name: {value: count}}, from the app's totals t and the website's w
    of one period (either may be None). A breakdown the file doesn't have is empty."""
    models = tally(t, "runs", "models")
    return {"countries": tally(t, "installs", "countries", "installs"), "regions": tally(t, "installs", "regions"),
            "systems": tally(t, "installs", "systems"), "gpu": tally(t, "hardware", "gpu"),
            "ram": tally(t, "hardware", "ram"), "kind": tally(t, "hardware", "kind"),
            "versions": tally(t, "installs", "versions"), "models": models,
            "hosted": {"local": sum(n for k, n in models.items() if k != "hosted"), "hosted": models.get("hosted", 0)}
            if models else {},
            "languages": tally(t, "runs", "languages"), "lengths": tally(t, "runs", "lengths"),
            "wcountries": tally(w, "countries", "page_views"), "devices": tally(w, "devices"),
            "wsystems": tally(w, "systems"), "referrers": linked(w, "referrers", "sites"),
            "sources": linked(w, "sources", "tags")}


def visit_totals(w):
    """The website's visits as docs/usage.json has them, totals only: {"total", "countries": {code: visits}, "days":
    [(date, visits)]}, or None when there are none or they can't be read."""
    try:
        v = w["visits"]
        countries = {str(k): count(n) for k, n in v["countries"].items() if re.fullmatch(r"[A-Z]{2}|other", str(k))}
        days = sorted((date.fromisoformat(d), count(n)) for d, n in v["days"].items())
        total = count(v["total"])
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    return {"total": total, "countries": countries, "days": days} if total else None


def census(t, w, month_, through, own_visits=True):
    """The census of one period: {"month", "through" (the last day it covers; None for a finished month), "groups": [(group,
    [(breakdown, [(value, count)])])] with the small groups folded into "other", a breakdown left out when it would
    show only one group, and a group left out when it has none, "visits": the website's visits as totals (their number, by country and by day), or None,
    "map": whether it draws the world map}. Without its own visits (own_visits=False: the month so far shows its own)
    it has no map, no visits and so no calendar."""
    counts, visits = census_counts(t, w), visit_totals(w) if own_visits else None
    groups = []
    for group, breakdowns in CENSUS:
        # the website's countries stay while there are visits: the map and its list count those, not the folded page views
        shown = [(key, rows) for key, *_ in breakdowns
                 if len(rows := folded(counts[key])) >= 2 or (key == "wcountries" and visits and visits["countries"])]
        if shown:
            groups.append((group, shown))
    return {"month": month_, "through": through, "groups": groups, "visits": visits, "map": own_visits}


def weeks_ended(month_, through):
    """The ISO weeks a month counts (those whose Thursday is in it) that had ended before through (a date, its last
    day): a week's runs are reported after it ends. ["2026-W45"], oldest first."""
    first = date.fromisoformat(f"{month_}-01")
    monday = first + timedelta(days=(3 - first.weekday()) % 7 - 3)  # the Monday of the week of the month's first Thursday
    out = []
    while monday + timedelta(days=6) < through and (monday + timedelta(days=3)).strftime("%Y-%m") == month_:
        year, number, _ = monday.isocalendar()
        out.append(f"{year}-W{number:02d}")
        monday += timedelta(days=7)
    return out


def app_part(t, through=None):
    """One period's app totals as the page reads them: the installs, their countries, the transcriptions, the hours
    of audio and the most used model (None when none was used, or the totals don't say: a month so far has no
    breakdown). The weeks whose runs are counted are the month's weekly ones, or, for a month so far (through, its
    last day), those that had ended by then."""
    models = {e: count((t["runs"].get("models") or {}).get(e, 0)) for e in ENGINES}
    hours = t["runs"]["hours"]
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 0 <= hours < 10 ** 9:
        raise ValueError(f"{hours!r} isn't a number of hours")
    weeks = weeks_ended(t["month"], through) if through else \
        sorted(k for k in (t.get("weekly", {}).get("active_installs") or {}) if re.fullmatch(r"\d{4}-W\d\d", str(k)))
    return {"month": t["month"], "weeks": len(weeks), "last_week": weeks[-1] if weeks else "",
            "installs": count(t["installs"]["active"]),
            "countries": count(t["installs"]["countries"]["count"]),
            "transcriptions": count(t["runs"]["transcriptions"]), "hours": round(float(hours), 1),
            "model": max(ENGINES, key=lambda e: (models[e], -ENGINES.index(e))) if any(models.values()) else None}


def website_part(w):
    """One period's website totals as the page reads them: page views, countries and download clicks."""
    return {"page_views": count(w["page_views"]), "countries": count(w["countries"]["count"]),
            "clicks": count(w["downloads"]["clicks"])}


def monthly(path):
    """The anonymous counts' totals in docs/usage.json (tools/usage_totals.py): {"app": the newest finished month's
    installs, countries, transcriptions, hours of audio and most used model, "website": its page views, countries and
    download clicks (None when it has none), "so_far": the month so far, {"month", "through", "app", "website"}, or
    None, "census": the breakdowns of the newest finished month as census() has them (a month so far holds headline
    numbers only, tools/usage_totals.py), "visits": the month so far's website visits, {"month", "through", "visits"},
    or None (then the census has the newest month's own)}. {} while the file isn't there, before the first month is
    exported; None, with a line in the log, when it can't be read. A part that can't be read is left out alone."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError, ValueError) as e:
        note(f"{path} can't be read ({e}); the counts are left out")
        return None
    try:
        months = {k: v for k, v in data["months"].items() if re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", k)}
        if not months:
            return {}
        newest = max(months)
        app = app_part(months[newest])
    except (AttributeError, KeyError, TypeError, ValueError) as e:
        note(f"{path} doesn't hold the totals tools/usage_totals.py writes ({e!r}); the counts are left out")
        return None
    site = raw_site = None
    if newest in (data.get("website") or {}):
        try:
            raw_site = data["website"][newest]
            site = website_part(raw_site)
        except (AttributeError, KeyError, TypeError, ValueError) as e:
            raw_site = None
            note(f"the website's totals for {newest} in {path} can't be read ({e!r}); they are left out")
    so_far = seen = None
    raw = data.get("so_far")
    if isinstance(raw, dict):
        try:
            month_, through = raw["month"], raw["through"]
            if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month_) or date.fromisoformat(through).strftime("%Y-%m") != month_:
                raise ValueError(f"{month_!r} through {through!r}")
            if month_ > newest:
                parts_ = {}
                for part, make in (("app", lambda t: app_part(t, date.fromisoformat(through))), ("website", website_part)):
                    try:
                        parts_[part] = make(raw[part]) if isinstance(raw.get(part), dict) else None
                    except (AttributeError, KeyError, TypeError, ValueError) as e:
                        parts_[part] = None
                        note(f"the month so far's {part} totals in {path} can't be read ({e!r}); they are left out")
                if parts_["app"] or parts_["website"]:
                    so_far = {"month": month_, "through": through, **parts_}
                    visits = visit_totals(raw["website"]) if parts_["website"] else None
                    seen = {"month": month_, "through": through, "visits": visits} if visits else None
        except (KeyError, TypeError, ValueError) as e:
            note(f"the month so far in {path} can't be read ({e!r}); it is left out")
    return {"app": app, "website": site, "so_far": so_far, "visits": seen,
            "census": census(months[newest], raw_site, newest, None, own_visits=not seen)}


# ---- the section of numbers: a grid of cards, each a figure as a dashboard shows one, drawn here from the numbers ---
#
# A figure card is its number, large, beside its title and one line about it, and a small picture across the card
# under them: a line with no axes and no dates, or a thin bar of parts with a legend. The line is SVG, placed across in
# per cent of the card's width and down in pixels, drawn in a nested SVG that stretches across only, with strokes that
# keep their width; the Arabic page mirrors it with the stylesheet, so time runs from the right as its words do. Each
# picture has a table of the same numbers for screen readers, which site.js also reads for its tooltip.

def figure(n, lang, tenths=False):
    """A number as the page writes it, in a data element that holds its plain value: 1,234 or 7.8, the tenths only
    when there are some, or always (tenths: hours, 5.0) as docs/usage.json has them."""
    if isinstance(n, float) and n.is_integer() and not tenths:
        n = int(n)
    group, point = NUMERALS[lang]
    text = f"{n:,}" if isinstance(n, int) else f"{n:,.1f}"
    return f'<data value="{n if isinstance(n, int) else f"{n:.1f}"}">{"".join(group if c == "," else point if c == "." else c for c in text)}</data>'


def plain(n, lang):
    """A number as the page writes it, without its data element (for the tables and the tooltips)."""
    return re.sub(r"<[^>]+>", "", figure(n, lang))


def day(d, words, year=True):
    """A day in a time element, in the page's language: 29 October 2026, or 28 October without the year."""
    months = words["numbers.months"].split()
    return f'<time datetime="{d.isoformat()}">{d.day} {months[d.month - 1]}{f" {d.year}" if year else ""}</time>'


def month(m, words):
    """A month, 2026-11, in a time element, in the page's language: November 2026."""
    year, number = m.split("-")
    return f'<time datetime="{m}">{words["numbers.months"].split()[int(number) - 1]} {year}</time>'


def pct(x):
    """A position across a plot, in per cent, as short as it can be written."""
    return f"{x:.3f}".rstrip("0").rstrip(".") + "%"


def places(points):
    """Where each point of a chart is, for site.js's tooltip, which reads its value and day from the chart's table,
    row by row: its place across in per cent and its height."""
    return " ".join(f"{x:.2f},{y:g}" for x, y in points)


def tangents(xs, ys):
    """The slopes of a monotone cubic through the points (Fritsch and Carlson), so the curve never overshoots a value:
    a running total never seems to dip, and a day with no views never seems below nothing."""
    d = [(ys[i + 1] - ys[i]) / (xs[i + 1] - xs[i]) for i in range(len(xs) - 1)]
    m = [d[0]] + [(d[i - 1] + d[i]) / 2 if d[i - 1] * d[i] > 0 else 0.0 for i in range(1, len(d))] + [d[-1]]
    for i, slope in enumerate(d):
        if slope == 0:
            m[i] = m[i + 1] = 0.0
            continue
        a, b = m[i] / slope, m[i + 1] / slope
        if a * a + b * b > 9:
            t = 3 / math.sqrt(a * a + b * b)
            m[i], m[i + 1] = t * a * slope, t * b * slope
    return m


def curve(xs, ys, m, first, last):
    """The cubic Bezier pieces of the monotone curve from point first to point last."""
    pieces = []
    for i in range(first, last):
        third = (xs[i + 1] - xs[i]) / 3
        pieces.append(f"C{xs[i] + third:.1f},{ys[i] + m[i] * third:.1f} {xs[i + 1] - third:.1f},"
                      f"{ys[i + 1] - m[i + 1] * third:.1f} {xs[i + 1]:.1f},{ys[i + 1]:.1f}")
    return f"M{xs[first]:.1f},{ys[first]:.1f} " + " ".join(pieces)


def spark(series, key, floor=None, marks=(), height=56, tags=False, top=6, wash=True):
    """A line under a card's number, across the card: a smooth curve over a wash that fades downwards (wash), the last
    stretch dashed (the day still being counted) to a dot, and a ring at each day in marks (a release), which tags
    name with a small mono version above the line (tags; the tags of days close together are staggered). No axes and
    no dates: the tooltip and the table under the card give each day and its value. floor is the value at the bottom,
    0 for counts of a day, the lowest value for a running total, which then fills the height as it grows; top is the
    room above the highest value, in pixels, for the tags."""
    days, values = [d for d, _ in series], [v for _, v in series]
    span = max((days[-1] - days[0]).days, 1)
    lo = min(values) if floor is None else floor
    rise = (max(values) - lo) or 1
    xs = [(d - days[0]).days / span * 1000 if len(days) > 1 else 1000 for d in days]  # the nested SVG is 1000 wide
    ys = [round(top + (1 - (v - lo) / rise) * (height - top - 4), 1) for v in values]
    whole = f"M{xs[0]:.1f},{ys[-1]:.1f}" if len(xs) == 1 else None
    if len(xs) > 1:
        m = tangents(xs, ys)
        tail = len(xs) > 2
        line = curve(xs, ys, m, 0, len(xs) - 2 if tail else len(xs) - 1)
        dashed = f'<path class="line line-tail" d="{curve(xs, ys, m, len(xs) - 2, len(xs) - 1)}"/>' if tail else ""
        whole = curve(xs, ys, m, 0, len(xs) - 1)
    else:
        line, dashed = whole, ""
    area = (f'<path class="wash" d="{whole} L{xs[-1]:.1f},{height} L{xs[0]:.1f},{height} Z" style="fill: url(#wash-{key})"/>'
            if wash else "")
    gradient = (f'<defs><linearGradient id="wash-{key}" x1="0" y1="0" x2="0" y2="1">'
                f'<stop offset="0" style="stop-color: var(--series-1); stop-opacity: 0.28"/>'
                f'<stop offset="1" style="stop-color: var(--series-1); stop-opacity: 0"/></linearGradient></defs>'
                if wash else "")
    named = dict(marks)
    rings = "".join(f'<circle class="mark" cx="{pct(xs[i] / 10)}" cy="{ys[i]}" r="3.5"/>'
                    for i, d in enumerate(days) if d in named and i < len(days) - 1)
    labels, near, level = "", None, 0
    for i, d in enumerate(days):
        if tags and d in named:
            level = 1 - level if near is not None and (xs[i] - near) / 1000 < 0.09 else 0
            near = xs[i]
            edge = " end" if xs[i] > 900 else " start" if xs[i] < 100 else ""  # at an edge the tag stays inside the card
            labels += (f'<span class="tag{edge}" style="--x: {pct(xs[i] / 10)}; --y: {ys[i] - 10 - 20 * level:g}px; '
                       f'--stem: {10 + 20 * level}px">{named[d]}</span>')
    svg = (f'<svg class="chart" height="{height}" aria-hidden="true" focusable="false">{gradient}'
           f'<svg width="100%" height="{height}" viewBox="0 0 1000 {height}" preserveAspectRatio="none">'
           f'{area}<path class="line" d="{line}"/>{dashed}</svg>'
           f'{rings}<circle class="dot" cx="{pct(xs[-1] / 10)}" cy="{ys[-1]}" r="4"/></svg>')
    return (f'<div class="plot spark" style="--h: {height}px" data-points="{places([(x / 10, y) for x, y in zip(xs, ys)])}">'
            f"{svg}{labels}</div>")


def roles(rows, lead=None):
    """The colour role of each part of a bar: the part the picture is about (lead, a key; else the first) in the
    brand's teal, the part that adds up the rest in the lightest grey, and the others in steps of grey."""
    lead = rows[0][0] if lead is None and rows else lead
    steps = iter(range(1, 99))
    return [("is-lead" if k == lead else "is-rest" if k in REST else f"is-g{min(next(steps), 3)}") for k, *_ in rows]


def share_of(n, total):
    """A part's share of its whole in per cent, "<1%" for a part under half of one."""
    return f"{round(n / total * 100)}%" if n / total >= 0.005 else "<1%"


def bare(text):
    """Words as plain text, for an attribute: without their tags and entities."""
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def tip(first, second="", accent=False):
    """The data attributes site.js's tooltip shows for an item of a picture: its first line, its second and, for the
    accent, a small teal key. They say what the table under the picture says."""
    return (f' data-tip-a="{html.escape(bare(first))}"' + (f' data-tip-b="{html.escape(bare(second))}"' if second else "")
            + (" data-tip-key" if accent else ""))


def parts(rows, total, lead=None):
    """One whole as a thin bar of parts 2 px apart, each part's class from its key and its role (the one in teal, the
    others in grey), with a legend of names and shares under it."""
    given = roles(rows, lead)
    blocks = "".join(f'<span class="part part-{k} {role}" style="--v: {n}"{tip(name, f"{plain(n, chr(101) + chr(110))} · {share_of(n, total)}", role == "is-lead")}></span>'
                     for (k, name, n), role in zip(rows, given))
    legend = "".join(f'<li><span class="key part-{k} {role}"></span>{name} <span class="share">{share_of(n, total)}</span></li>'
                     for (k, name, n), role in zip(rows, given))
    return (f'<div class="parts"><div class="split" aria-hidden="true" data-tip-group>{blocks}</div>'
            f'<ul class="legend" aria-hidden="true">{legend}</ul></div>')


def table(caption, heads, rows, boxed=False):
    """The chart's numbers as a table for screen readers: its caption, its column heads and its rows. A table is never
    narrower than its words, so one with a long caption goes in a hidden box instead (boxed), which keeps them from
    widening the page."""
    head = "".join(f'<th scope="col">{h}</th>' for h in heads)
    body = "".join("<tr>" + "".join(f'<th scope="row">{r[0]}</th>' + "".join(f"<td>{c}</td>" for c in r[1:])) + "</tr>"
                   for r in rows)
    whole = f"<caption>{caption}</caption><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    return f'<div class="sr-only"><table>{whole}</div>' if boxed else f'<table class="sr-only">{whole}'


def stat(n, label, lang, size="", tenths=False):
    """A number, large, over its label (the anonymous counts' card)."""
    return f'<p class="stat{size}">{figure(n, lang, tenths)}<span>{label}</span></p>'


def quiet(rows, total, lead=None, hairline=True):
    """A quiet list: each row its name and its share in Plex Mono, the largest first and "other" last (as rows come),
    and under it a hairline as long as its share, teal for the row the list is about, grey for the others."""
    given = roles(rows, lead)
    items = "".join(
        f'<li class="quiet-row {role}"{tip(name, f"{n:,} · {share_of(n, total)}", role == "is-lead")}><span class="quiet-name">{name}</span>'
        f'<span class="quiet-share">{share_of(n, total)}</span>'
        + (f'<span class="quiet-line" style="--v: {n / total * 100:.1f}"></span>' if hairline else "") + "</li>"
        for (_, name, n), role in zip(rows, given))
    return f'<ol class="quiet" aria-hidden="true" data-tip-group>{items}</ol>'


def ring(rows, total, say):
    """One share as a small ring (a whole split in two, such as local or hosted): the first part's arc in teal on a
    track of grey, its name and share beside it, and the other's."""
    share = rows[0][2] / total
    length = 2 * math.pi * 14
    svg = (f'<svg class="ring" viewBox="0 0 36 36" aria-hidden="true" focusable="false"><circle class="ring-track" cx="18" cy="18" r="14"/>'
           f'<circle class="ring-arc" cx="18" cy="18" r="14" stroke-dasharray="{share * length:.1f} {length:.1f}"/></svg>')
    return f'<div class="ring-row">{svg}{quiet(rows, total, hairline=False)}</div>'


# The three glyphs of the devices, Lucide's smartphone, tablet and monitor (ISC licence, in NOTICE): 24 px, one stroke
LUCIDE = {
    "phone": '<rect width="14" height="20" x="5" y="2" rx="2" ry="2"/><path d="M12 18h.01"/>',
    "tablet": '<rect width="16" height="20" x="4" y="2" rx="2" ry="2"/><line x1="12" x2="12.01" y1="18" y2="18"/>',
    "desktop": '<rect width="20" height="14" x="2" y="3" rx="2"/><line x1="8" x2="16" y1="21" y2="21"/>'
               '<line x1="12" x2="12" y1="17" y2="21"/>'}


def glyphs(rows, total):
    """The devices as line glyphs in the text colour, each with its share large under it and its name under that; the
    largest has a teal hairline under it, the one accent. A value with no glyph (other) is its name and share only."""
    given = roles(rows, rows[0][0] if rows else None)
    items = "".join(
        f'<li class="glyph {role}"{tip(name, f"{n:,} · {share_of(n, total)}", role == "is-lead")}>'
        + (f'<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">{LUCIDE[key]}</svg>' if key in LUCIDE else
           '<span class="glyph-none"></span>')
        + f'<span class="glyph-share">{share_of(n, total)}</span><span class="glyph-name">{name}</span></li>'
        for (key, name, n), role in zip(rows, given))
    return f'<ul class="glyphs" aria-hidden="true" data-tip-group>{items}</ul>'


def ear_paths():
    """The two paths of the brand icon's ear, as ear() draws it."""
    return re.findall(r'<path d="([^"]+)"', ICON.read_text(encoding="utf-8"))[:2]


def ears(n, say, period=""):
    """A unit chart of the installs: one small ear of the brand's icon for each install, and past EARS installs one ear
    for each N of them, said under the ears. Nothing for no installs."""
    if not n:
        return ""
    each = next((e for e in EACH if -(-n // e) <= EARS), EACH[-1])
    count = n if each == 1 else max(round(n / each), 1)
    outline, curve = ear_paths()
    one = (f'<svg class="unit" viewBox="17 10.5 33.6 45" aria-hidden="true" focusable="false">'
           f'<path d="{outline}"/><path class="unit-in" d="{curve}"/></svg>')
    note_ = f'<span class="ears-each">{say("ear_each", n=plain(each, "en"))}</span>' if each > 1 else ""
    return (f'<div class="ears" aria-hidden="true" data-tip-group><span class="ears-row"{tip(f"{n:,} " + say("installs_one" if n == 1 else "installs"), period, True)}>'
            f'{one * count}</span>{note_}</div>')


def calendar_grid(days, month_, say, words, lang):
    """The visits of each day of a month as a calendar: seven columns, Monday to Sunday, one row a week, the month's
    name over it; each day ([(date, visits)], to the period's last day) shows its number small and, when it had visits,
    their count, with a tint of teal by its share of the busiest day (one hue); a day with none is a quiet step of the
    card, the days before the first are blank and the days after the last covered, to the end of its week, are empty
    outlines; the weeks after it aren't drawn. Each day has
    its tooltip, and the table under it, for screen readers, has the numbers."""
    year, number = int(month_[:4]), int(month_[5:])
    names = words["numbers.months"].split()
    per = {d.day: n for d, n in days if (d.year, d.month) == (year, number)}
    last = max(per, default=0)
    top = max(per.values(), default=0) or 1
    cells = ['<span class="cell off"></span>'] * date(year, number, 1).weekday()
    for d in range(1, calendar.monthrange(year, number)[1] + 1):
        if d > last:  # a day still to come is an empty outline
            cells.append('<span class="cell future"></span>')
            continue
        n = per.get(d, 0)
        said = tip(f"{d} {names[number - 1]}", say("visit_count_one" if n == 1 else "visit_count", n=str(n)), n == top and n > 0)
        cells.append(f'<span class="cell{" lit" if n else ""}"' + (f' style="--a: {0.25 + 0.75 * n / top:.2f}"' if n else "")
                     + f'{said}><span class="n">{d}</span>' + (f'<span class="v">{n}</span>' if n else "") + "</span>")
    cells = cells[:-(-(date(year, number, 1).weekday() + last) // 7) * 7]  # up to the week of the last covered day
    heads = "".join(f"<span>{w}</span>" for w in say("weekdays").split())
    ramp = (f'<span class="key-row calendar-key"><span>{say("legend_fewer")}</span><span class="sw cell-low"></span>'
            f'<span class="sw cell-high"></span><span>{say("legend_more")}</span></span>')
    return (f'<div class="calendar" aria-hidden="true" data-tip-group><p class="calendar-month">{month(month_, words)}</p>'
            f'<div class="weekdays">{heads}</div><div class="days">{"".join(cells)}</div>{ramp}</div>')


def card(key, title, body, span):
    """One card of the grid, its title over what it shows."""
    return (f'      <div class="card span-{span}" id="numbers-{key}">\n        <h3>{title}</h3>\n'
            f"        {body}\n      </div>")


def figure_card(key, number, title, line, picture, span=1, section="numbers"):
    """One card as a dashboard shows a figure: the number large, beside it its title and one line about it, and the
    picture (a small line, a bar of parts or bars to compare) across the card under them."""
    return (f'      <div class="card figure-card span-{span}" id="{section}-{key}">\n'
            f'        <div class="kpi"><p class="kpi-figure">{number}</p>'
            f'<div class="kpi-text"><h3>{title}</h3><p>{line}</p></div></div>\n'
            f"        {picture}\n      </div>")


def counted(n, key, say, lang, size="", tenths=False):
    """A number over its label, in the singular when it is one (hours: 1.0 too)."""
    return stat(n, say(f"{key}_one" if n == 1 else key), lang, size, tenths)


def label_of(kind, key, value, say, words):
    """The name of a value of a breakdown, in the page's language: a country by the copy's names, a range with its
    unit, a value the copy names (a kind of install, a model), else the value as it is sent, as a Latin name."""
    latin = lambda text: f"{IN_LATIN}{html.escape(text)}</span>"  # noqa: E731
    if value == "other":
        return say("other")
    if kind == "country":
        return words.get(f"countries.{value}") or latin(value)
    if kind == "place":
        return latin(value.split(": ", 1)[-1])
    if kind == "model":
        return words[f"numbers.model_{value}"]
    unit = {"gb": "gb", "min": "min"}.get(kind)
    if unit:
        m = re.fullmatch(r"(under|over) (\d+)", value) or re.fullmatch(r"(\d+)-(\d+)", value)
        if m:
            number = say(m[1], v=m[2]) if m[1] in ("under", "over") else f"<bdi dir=\"ltr\">{m[1]}–{m[2]}</bdi>"
            return say(unit, v=number)
    named = words.get(f"numbers.v_{key}_{value.replace('-', '_')}")
    return named or latin(value)


STEPS = 5  # the steps of the map's ramp of teal, light to strong


def step_of(n, top):
    """The step, 1 to STEPS, of a count on the ramp: its share of the largest count, rounded up."""
    return min(STEPS, max(1, -(-n * STEPS // top))) if top else 1


def world_map(shade, total, unit, say, words):
    """The world as an SVG, a choropleth: the land in a quiet step of the card's own greys with hairline borders, no sea
    and no Antarctica, and each country in shade ({code: count}) in the brand's teal, one of STEPS steps from light to
    strong by its count against the largest (the map isn't folded: a country with one visit is shaded). A code the map
    doesn't have is ignored. A shaded country has the data of its tooltip: its name, its count (unit, a copy key such
    as visit_count) and its share of total. The outlines are read from MAP, which docs/images/make/worldmap.py writes;
    without it there is no map. Returns the SVG with the lowest and the highest count it shades, for the legend, which
    so never names a count that no drawn country has ("other" and the codes without an outline aren't drawn)."""
    try:
        asset = json.loads(MAP.read_text(encoding="utf-8"))
        outlines = {str(k): str(v) for k, v in asset["countries"].items()}
        box = re.fullmatch(r"0 0 \d+ \d+", asset["viewBox"]) and asset["viewBox"]
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        box = None
    if not box:
        note(f"{MAP} can't be read; the map is left out")
        return "", 0, 0
    lit = {code: n for code, n in shade.items() if code in outlines and n > 0}
    top = max(lit.values(), default=0)
    land = "".join(f'<path d="{d}"/>' for code, d in outlines.items() if code not in lit)
    name_of = lambda code: bare(words.get(f"countries.{code}") or code)  # noqa: E731
    teal = "".join(f'<path class="s{step_of(n, top)}" d="{outlines[code]}"'
                   + tip(name_of(code), say(f"{unit}_one" if n == 1 else unit, n=f"{n:,}") + f" · {share_of(n, total or 1)}", True) + "/>"
                   for code, n in sorted(lit.items()))
    return (f'<svg class="map" viewBox="{box}" aria-hidden="true" focusable="false" data-tip-group>'
            f'<g class="land">{land}</g><g class="lit">{teal}</g></svg>', min(lit.values(), default=0), top)


def ramp_key(label, low, high):
    """The map's legend: a word, the lowest count, the ramp of its steps drawn as they are on the map, the highest."""
    steps = "".join(f'<i class="s{i}"></i>' for i in range(1, STEPS + 1))
    return (f'<div class="ramp-key" aria-hidden="true"><span>{label}</span> <span class="mono">{plain(low, "en")}</span> '
            f'<span class="steps">{steps}</span> <span class="mono">{plain(high, "en")}</span></div>')


def key_row(items):
    """A row of legend items, each a swatch drawn like the thing it explains and a few words: [(swatch class, words)]."""
    return ('<ul class="key-row" aria-hidden="true">'
            + "".join(f'<li><span class="sw {swatch}"></span>{text}</li>' for swatch, text in items) + "</ul>")


def crowd(key, title, rows, kind, unit, say, words, lang, visits=None, map_=True):
    """One breakdown of the census, in the form that fits it: a quiet list of names and shares (most of them), the
    devices as three glyphs with their shares, local or hosted as one small ring, and the website's countries as a
    list under the world map, which shades the countries by their visits (all of them, unfolded, and the list counts
    the same visits; without the visits, both use the page views of those that pass the fold) under a legend of its
    ramp, or as the list alone when map_ is False (the month so far draws the map); each with a table for screen
    readers."""
    total = sum(n for _, n in rows)
    named = [("other" if v == "other" else f"c{i}", label_of(kind, key, v, say, words), n) for i, (v, n) in enumerate(rows)]
    lead = next((k for k, _, _ in named if k != "other"), None)
    drawn = spoken = ""
    if key == "devices":
        picture = glyphs([(v if v in LUCIDE else "other", name, n) for (v, _), (_, name, n) in zip(rows, named)], total)
    elif key == "hosted" and len(rows) == 2:
        picture = ring(named, total, say)
    elif key == "wcountries":
        if visits:  # the list under the map counts what the map shades: the visits of every country, unfolded
            rows = sorted(visits["countries"].items(), key=lambda kv: (kv[0] == "other", -kv[1], kv[0]))
            named = [("other" if v == "other" else f"c{i}", label_of(kind, key, v, say, words), n)
                     for i, (v, n) in enumerate(rows)]
            total, unit = visits["total"], "visits"
            lead = next((k for k, _, _ in named if k != "other"), None)
        picture = quiet(named, total, lead)
        if visits:
            shade, whole, unit_key, label = visits["countries"], visits["total"], "visit_count", say("legend_visits")
        else:
            shade, whole, unit_key, label = {v: n for v, n in rows if v != "other"}, total, "view_count", say("legend_views")
        drawn, low, high = world_map(shade, whole, unit_key, say, words) if map_ else ("", 0, 0)
        if drawn:  # the legend over the map, which spans the card, and the list of countries under it
            picture = f'{ramp_key(label, low, high)}<div class="map-row">{drawn}</div>{picture}'
    else:
        picture = quiet(named, total, lead)
    return (f'<div class="crowd{" crowd-map" if drawn else ""}"><p class="crowd-title">{title}</p>{picture}'
            + table(title, [say("value"), say(f"unit_{unit}"), say("share")],
                    [(name, plain(n, lang), share_of(n, total)) for _, name, n in named], boxed=True) + spoken + "</div>")


def daily_html(visits, month_, say, words, lang):
    """The website's visits of each day of a period, as a calendar with its table; nothing without days."""
    days = visits["days"] if visits else None
    if not days:
        return ""
    return (f'<div class="crowd chart-box"><p class="crowd-title">{say("daily")}</p>'
            f'{calendar_grid(days, month_, say, words, lang)}'
            + table(say("daily"), [say("day"), say("unit_visits")], [(day(d, words), plain(n, lang)) for d, n in days], boxed=True)
            + "</div>")


def census_html(c, say, words, lang):
    """The census of the newest month: its groups (where, computers, how it is used, website visitors), each with its
    breakdowns, and the website's visits of each day, as a calendar, under the visitors."""
    chosen = {g: dict(items) for g, items in c["groups"]}
    out = []
    for group, breakdowns in CENSUS:
        inner = "".join(crowd(key, say(f"t_{key}"), chosen[group][key], kind, unit, say, words, lang, c["visits"], c["map"])
                        for key, kind, unit in breakdowns if key in chosen.get(group, {}))
        if group == "visitors":
            inner += daily_html(c["visits"], c["month"], say, words, lang)
        if inner:
            out.append(f'<div class="group group-{group}"><h4>{say(f"g_{group}")}</h4>{inner}</div>')
    return "".join(out)


def visits_html(v, title, say, words, lang):
    """The month so far's website visits, under its title: the world map with the list of countries under it, and the
    calendar of each day; totals only, as the census draws a finished month's."""
    inner = (crowd("wcountries", say("t_wcountries"), [], "country", "views", say, words, lang, v["visits"])
             + daily_html(v["visits"], v["month"], say, words, lang))
    return (f'<div class="census census-visits"><h4 class="census-title">{title}</h4>'
            f'<div class="groups"><div class="group group-visits">{inner}</div></div></div>')


def week_of(week, words):
    """An ISO week ("2026-W45") in plain words: the day it begins on, as "2 November"."""
    year, number = (int(x) for x in re.fullmatch(r"(\d{4})-W(\d\d)", week).groups())
    return day(date.fromisocalendar(year, number, 1), words, year=False)


def period_html(app, site, title, say, lang, so_far=False, words=None):
    """One period's numbers: its title, the installs and their countries large with the installs as a row of ears, the
    transcriptions and hours smaller, the most used model, and the website's page views, countries and download
    clicks in one line."""
    stats = ""
    if app:
        stats = (counted(app["installs"], "installs", say, lang) + counted(app["countries"], "countries", say, lang)
                 + ears(app["installs"], say, title))
        if not so_far:
            stats += (counted(app["transcriptions"], "transcriptions", say, lang, " stat-small")
                      + counted(app["hours"], "hours", say, lang, " stat-small", True))
        elif app["weeks"]:  # the runs of a month so far are those of the weeks reported, which come after a week ends
            stats += (stat(app["transcriptions"], say("transcriptions_weeks"), lang, " stat-small")
                      + stat(app["hours"], say("hours_weeks"), lang, " stat-small", True)
                      + f'<p class="model">{say("weeks_last", week=week_of(app["last_week"], words))}</p>')
        if app["model"] and (not so_far or app["weeks"]):
            stats += f'<p class="model">{say("model")} <span>{say("model_" + app["model"])}</span></p>'
    web = ""
    if site:
        web = (f'<div class="website"><h4>{say("website")}</h4>'
               + counted(site["page_views"], "page_views", say, lang, " stat-small")
               + counted(site["countries"], "countries", say, lang, " stat-small")
               + counted(site["clicks"], "clicks", say, lang, " stat-small") + "</div>")
    return f'<div class="period"><h4>{title}</h4><div class="stats">{stats}</div>{web}</div>'


def numbers(words, lang, gh, totals):
    """The section of numbers: a grid of cards, three to a row from 1000 px. Five figure cards of GitHub's numbers
    from the stats branch (the downloads with those of the last seven days, the latest release's, the larger
    system's share, the repository's page views of the last 30 days and the stars), then the anonymous counts' newest
    month across a row, or a card saying when the first totals come; under it, a line saying where they come from.
    Nothing when there is nothing to show."""
    say = lambda key, **values: fill(words[f"numbers.{key}"], values)  # noqa: E731
    cards, caption = [], []
    if gh and gh.get("downloads"):
        series, marks = gh["downloads"], dict(gh["marks"])
        total, last = series[-1][1], series[-1][0]
        before = [n for d, n in series if d <= last - timedelta(7)]
        if before:
            week = total - before[-1]
            line = say("week_more", n=plain(week, lang)) if week else say("week_none")
        else:  # a history shorter than a week: what came since its first day
            grew = total - series[0][1]
            since = day(series[0][0], words, year=False)
            line = say("more_since", n=plain(grew, lang), day=since) if grew else say("since_day", day=since)
        rows = [(day(d, words) + (f" ({say('release_out', release=marks[d])})" if d in marks else ""), plain(n, lang))
                for d, n in series]
        cards.append(figure_card("downloads", figure(total, lang), say("downloads"), line,
                                 spark(series, "downloads", marks=gh["marks"], height=LEAD, tags=True, top=46)
                                 + table(say("downloads_title"), [say("day"), say("downloads_title")], rows), span=3))
        if gh["releases"]:
            latest, n = gh["releases"][0]
            out = next((d for d, r in gh["marks"] if r == latest), None)
            older = sum(m for _, m in gh["releases"][1:])
            bar = [("latest", latest, n)] + ([("older", say("older"), older)] if older else []) \
                + ([("previews", say("previews"), gh["previews"])] if gh["previews"] else [])
            whole = sum(m for *_, m in bar)
            cards.append(figure_card("releases", figure(n, lang), say("latest_title", release=latest),
                                     say("latest_out", day=day(out, words, year=False)) if out else say("latest_only"),
                                     parts(bar, whole, "latest")
                                     + table(say("releases_title"), [say("release"), say("downloads_title")],
                                             [(v, plain(m, lang)) for v, m in gh["releases"]]
                                             + ([(say("previews"), plain(gh["previews"], lang))] if gh["previews"] else []))))
        shares = gh["platforms"]
        if shares:
            whole = sum(m for _, m in shares)
            big = max(range(len(shares)), key=lambda i: shares[i][1])
            share = round(shares[big][1] / whole * 100)
            rest = [say("platform_rest", platform=say(p), share=str(round(m / whole * 100)))
                    for i, (p, m) in enumerate(shares) if i != big]
            cards.append(figure_card("platforms", f'<data value="{share}">{share}%</data>',
                                     say("platform_share", platform=say(shares[big][0])), " ".join(rest),
                                     parts([(p, say(p), m) for p, m in shares], whole, shares[big][0])
                                     + table(say("platforms_title"), [say("platform"), say("downloads_title"), say("share")],
                                             [(say(p), plain(m, lang), f"{round(m / whole * 100)}%") for p, m in shares])))
    small = []  # the page views and the stars, smaller and simpler than the lead: a plain line, stacked in one column
    if gh and gh.get("views"):
        views, first, last = gh["views"], gh["views"][0][0], gh["views"][-1][0]
        seen = sum(n for _, n in views)
        title = say("views_30") if gh["views_whole"] else say("views_since", first=day(first, words, year=False))
        small.append(figure_card("views", figure(seen, lang), title,
                                 say("views_average", n=plain(round(seen / len(views), 1), lang)),
                                 spark(views, "views", floor=0, height=SMALL, wash=False)
                                 + table(say("views_title"), [say("day"), say("page_views")],
                                         [(day(d, words), plain(n, lang)) for d, n in views])))
    if gh and gh.get("stars"):
        stars = gh["stars"]
        grew = stars[-1][1] - stars[0][1]
        since = day(stars[0][0], words, year=False)
        small.append(figure_card("stars", figure(stars[-1][1], lang), say("stars"),
                                 say("more_since", n=plain(grew, lang), day=since) if grew else say("since_day", day=since),
                                 spark(stars, "stars", height=SMALL, wash=False)
                                 + table(say("stars_title"), [say("day"), say("stars_title")],
                                         [(day(d, words), plain(n, lang)) for d, n in stars])))
    if small:
        cards.append('      <div class="stack">\n' + "\n".join(small) + "\n      </div>")
    if gh:
        caption.append(say("caption_github", day=day(gh["day"], words), charts=CHARTS))
    if totals == {}:
        cards.append(card("counts", say("counts"), f'<p class="soon">{say("not_yet")}</p>', 1))
    elif totals:
        app, site, sofar, picture = totals["app"], totals["website"], totals["so_far"], totals["census"]
        periods = period_html(app, site, month(app["month"], words), say, lang)
        seen = ""
        if sofar:
            named = say("so_far", month=month(sofar["month"], words), day=day(date.fromisoformat(sofar["through"]), words, year=False))
            periods += period_html(sofar["app"], sofar["website"], named, say, lang, True, words)
            if totals["visits"]:  # the month so far's map and calendar, under the two periods
                seen = visits_html(totals["visits"], say("visits_title", period=named), say, words, lang)
        groups = census_html(picture, say, words, lang)
        body = (f'<div class="periods">{periods}</div>{seen}'
                + (f'<div class="census"><h4 class="census-title">{say("census", period=month(picture["month"], words))}</h4>'
                   f'<div class="groups">{groups}</div></div>' if groups else ""))
        cards.append(card("counts", say("counts"), body, 3))
    if totals is not None:
        caption.append(say("caption_counts", method=METHOD))
    if not cards:
        return ""
    return "\n".join([
        '  <section class="sec numbers" aria-labelledby="numbers">',
        '    <div class="wrap sec-grid">',
        '      <div class="claim">',
        f'        <h2 id="numbers">{say("h2")}</h2>',
        f'        <p class="lede">{say("lede")}</p>',
        "      </div>",
        '      <div class="bento">',
        *cards,
        "      </div>",
        f'      <p class="numbers-source">{" ".join(caption)}</p>',
        "    </div>",
        "  </section>"])


# ---- the benchmarks: the same figure cards, each over bars to compare, from the numbers of the docs' charts --------
#
# A card's figure is the best of Sedjem's own bars (the speaker labels' card: the default, TitaNet-small), its model and
# test set named in the line under its title. Under them
# a bar for each model from one zero line, Sedjem's in the brand's teal and the rest in grey, each named, with its value
# at its end; a heading over a group (the models also tried, or a test set); and which way is better. A share of the
# words and the minutes for an hour run to the whole, 100% or an hour, drawn as the bar's track; the speaker labels'
# shares, all small, to the largest of them. Each chart has a table of its numbers for screen readers.

def bench_sources():
    """docs/charts.py's Sources, read without importing it (so nothing is written next to it): the numbers the docs'
    charts draw, from results/summary.json, results/*-diarization.json and docs/charts-data.json, each with the value
    and the text the docs give it."""
    names = {"__name__": "sedjem_charts", "__file__": str(BENCH)}
    exec(compile(BENCH.read_text(encoding="utf-8"), str(BENCH), "exec"), names)
    return names["Sources"]()


def measured(src):
    """Each card's numbers: which way is better, the note under its chart, the value its bars run to (None for the
    largest), its unit, and its bars in order, each (model, the group heading over it or None, value, text), the value
    the text as the docs round it, so a bar is drawn to the number at its end. Also the number of Perle clips, which
    every run on Perle shares."""
    def bar(model, n, group=None):
        return (model, group, float(n.shown), n.shown)

    def perle(model, metric):
        return bar(model, src.perle("wav16k", *PERLE[model], metric, None), None if model in IN_SEDJEM else "tried")

    def minutes(model, engine):  # the time for an hour of the meetings, from their speed: processing time / audio length
        n = src.published("meeting_speed", f"{engine}_mean")
        m = (Decimal(n.shown) * 60).quantize(Decimal(1), ROUND_HALF_UP)
        return (model, None, float(m), str(m))

    def speaker(model, corpus):
        return bar(model, src.speaker(corpus, SPEAKER_SYSTEMS[model], "wrong_on_speech", "Wrong speaker on speech"), corpus)

    clips = {src.summary[("perle", "wav16k", *run)]["clips"] for run in PERLE.values()}
    if len(clips) != 1:
        raise SystemExit(f"the Perle runs in results/summary.json have different numbers of clips: {sorted(clips)}")
    two_or_three = "meetings_two_or_three_people"
    cards = {
        "wer": dict(better="lower", note="lower", top=100, unit="%", bars=[perle(m, "wer") for m in PERLE]),
        "english": dict(better="higher", note="higher", top=100, unit="%", bars=[perle(m, "en_kept") for m in PERLE]),
        # led by the default speaker model, TitaNet-small (docs/adr/0009), though Nemotron's share is lower
        "nemotron": dict(better="lower", note="lower", top=None, unit="%", lead="titanet",
                         bars=[speaker(m, corpus) for corpus in ("arzen", "ami") for m in ("titanet", "nemotron")]),
        "meetings": dict(better="lower", note="closer", top=100, unit="%",
                         bars=[bar("cohere", src.published("meetings", "pooled", "cohere_wer")),
                               bar("medium", src.published("meetings", "pooled", "whisper_wer"))]),
        "speed": dict(better="lower", note="faster", top=60, unit="min",
                      bars=[minutes("cohere", "cohere"), minutes("medium", "whisper")]),
        "speakers": dict(better="lower", note="lower", top=None, unit="%",
                         bars=[bar("titanet", src.published(two_or_three, "whisper_wrong_speaker")),  # TitaNet-small's labels
                               bar("elevenlabs", src.published(two_or_three, "elevenlabs_wrong_speaker"))]),
    }
    return cards, clips.pop()


def lead(card):
    """The bar a card's figure is: the first of the model the card names in "lead", else the best of Sedjem's own, the
    first of them on a tie."""
    if card.get("lead"):
        return next(b for b in card["bars"] if b[0] == card["lead"])
    ours = [b for b in card["bars"] if b[0] in IN_SEDJEM]
    return (min if card["better"] == "lower" else max)(ours, key=lambda b: b[2])


def benchmarks(words, lang, cards, clips):
    """The benchmarks: the claim, a grid of six figure cards, three to a row from 1000 px, and under them the test sets,
    each linked to its source with its licence, and the docs that hold every number."""
    tell = lambda key, **values: fill(words[f"benchmarks.{key}"], values)  # noqa: E731
    # a name stays in Latin and is never translated; on the Arabic page it is its own left-to-right run, marked English
    name = lambda text: f'{IN_LATIN if lang == "ar" else NAME}{html.escape(text)}</span>'  # noqa: E731

    def words_of(text):
        """A long name in a card's line, which may break between its words but never at a hyphen: each word kept
        whole, and on the Arabic page the words one left-to-right run, so they keep their order."""
        kept = " ".join(f"{NAME}{html.escape(w)}</span>" for w in text.split(" "))
        return f'<bdi dir="ltr" lang="en">{kept}</bdi>' if lang == "ar" else kept

    def called(model, heading):
        """A model's name in a table: in full, with the hint it ran with, or as one also tried."""
        full = name(MODELS[model][1])
        if model == "large":
            return tell("hint", model=full)
        if model == "elevenlabs":
            return tell("elevenlabs")
        return tell("tried_row", model=full) if heading == "tried" else full

    out = []
    for key in BENCHMARKS:
        card = cards[key]
        if card["unit"] == "%":
            unit = lambda text: f"{text}%"  # noqa: E731
            figure = lambda text: f'<data value="{text}">{text}%</data>'  # noqa: E731
        else:
            unit = lambda text: f'{text}&nbsp;{tell("minute")}'  # noqa: E731
            figure = lambda text: f'<data value="{text}">{text}<span class="kpi-unit">&nbsp;{tell("minute")}</span></data>'  # noqa: E731
        first = lead(card)
        top = card["top"] or max(b[2] for b in card["bars"])
        rows, group = [], None
        for b in card["bars"]:
            model, heading, value, text = b
            if heading != group and heading:
                rows.append(f'<li class="bar-head">{tell("tried") if heading == "tried" else name(TEST_SETS[heading][0])}</li>')
            group = heading
            rows.append(f'<li class="bar-{"sedjem" if model in IN_SEDJEM else "other"}{" bar-lead" if b is first else ""}">'
                        f'<span class="bar-name">{name(MODELS[model][0])}</span>'
                        f'<span class="bar-track"><span class="bar-fill" style="--v: {value / top * 100:.1f}"></span></span>'
                        f'<span class="bar-value">{unit(text)}</span></li>')
        chart = (f'<div class="bars{" bars-whole" if card["top"] else ""}" aria-hidden="true">'
                 f'<ol class="bar-list">{"".join(rows)}</ol><p class="bar-note">{tell(card["note"])}</p></div>')

        sets = list(dict.fromkeys(b[1] for b in card["bars"] if b[1] in TEST_SETS))
        if sets:  # a row for each test set, a column for each model
            models = list(dict.fromkeys(b[0] for b in card["bars"]))
            numbers = {(b[1], b[0]): unit(b[3]) for b in card["bars"]}
            grid = table(tell(f"{key}_table"), [tell("test_set")] + [called(m, None) for m in models],
                         [[name(TEST_SETS[s][0])] + [numbers[s, m] for m in models] for s in sets], boxed=True)
        else:
            grid = table(tell(f"{key}_table"), [tell("model"), tell(key)], [(called(b[0], b[1]), unit(b[3])) for b in card["bars"]],
                         boxed=True)
        line = tell(f"{key}_line", model=words_of(MODELS[first[0]][1]),
                    set=name(TEST_SETS[first[1]][0]) if first[1] in TEST_SETS else "")
        out.append(figure_card(key, figure(first[3]), tell(key), line, chart + grid, section="benchmarks"))
    links = {k: url for k, (_, url) in TEST_SETS.items()}
    sources = (tell("sets", clips=str(clips), **links) + " "
               + tell("more", results=DOCS + "03-results.md", speakers=DOCS + "04-speaker-labels.md", tables=DOCS + "results-tables.md"))
    return "\n".join([
        '  <section class="sec benchmarks" aria-labelledby="benchmarks">',
        '    <div class="wrap sec-grid">',
        '      <div class="claim">',
        f'        <h2 id="benchmarks">{tell("h2")}</h2>',
        f'        <p class="lede">{tell("lede")}</p>',
        f'        <p>{tell("draft")}</p>',
        "      </div>",
        '      <div class="bento">',
        *out,
        "      </div>",
        f'      <p class="sources">{sources}</p>',
        "    </div>",
        "  </section>"])


def latin_runs(text):
    """Arabic copy with each run of Latin words in its own left-to-right span, marked as English and not to be
    translated, so a name, a term or a path keeps its own order inside the right-to-left line."""
    text = text.replace("&nbsp;", NBSP)  # so a number and its unit can be one run
    marked = "".join(part if MARKUP.fullmatch(part) else LATIN_WORDS.sub(lambda m: f"{IN_LATIN}{m.group()}</span>", part)
                     for part in MARKUP.split(text))
    return marked.replace(NBSP, "&nbsp;")


def copy(lang):
    """A page's words from copy/<lang>.toml, as placeholders: "hero.lead" and so on. The [page] table and the keys
    ending in _alt are attribute text, so they are escaped; the rest is HTML, and on the Arabic page its Latin
    runs are marked."""
    with open(SITE / "copy" / f"{lang}.toml", "rb") as f:
        tables = tomllib.load(f)
    words = {}
    for table, values in tables.items():
        for key, value in values.items():
            if table == "page" or key.endswith("_alt"):
                value = html.escape(value)
            elif lang == "ar":
                value = latin_runs(value)
            words[f"{table}.{key}"] = value
    return words


def fill(template, values):
    """The template with every {{name}} filled in, including those inside the values themselves."""
    def value(m):
        if m.group(1) not in values:
            raise SystemExit(f"nothing to fill {{{{{m.group(1)}}}}} with")
        return PLACEHOLDER.sub(value, values[m.group(1)])
    return PLACEHOLDER.sub(value, template)


def fonts(css):
    """The font files the stylesheet's @font-face rules name."""
    return sorted(set(re.findall(r'url\("(fonts/[^"]+\.woff2)"\)', css)))


def preloads(names, root):
    return "\n".join(f'  <link rel="preload" href="{root}fonts/{name}-normal.woff2" as="font" type="font/woff2" crossorigin>'
                     for name in names)


def prepare(out):
    """An empty output folder. An existing one is emptied only if it holds an earlier build, and never if it is
    the repository or site/ itself."""
    out = out.resolve()
    if out in (ROOT, SITE) or out in ROOT.parents or SITE in out.parents:
        raise SystemExit(f"won't build into {out}")
    if out.exists() and any(out.iterdir()):
        if not ((out / "index.html").is_file() and (out / "fonts" / "OFL-IBM-Plex.txt").is_file()):
            raise SystemExit(f"{out} is not empty and doesn't hold an earlier build; choose another folder")
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    return out


def build(out=ROOT / "_site", counts_key=COUNTS_KEY, counts_host=COUNTS_HOST, stats=None, usage=USAGE, latest=None):
    """Build the site into out and return it. counts_host is the one host whose pages send their counts (the tests
    build pages that count on 127.0.0.1), and an empty counts_key leaves the counting out. stats is the folder with
    the stats branch's files, None to leave GitHub's numbers out, usage the totals of the anonymous counts, and latest
    the folder with the latest release's details, None for the downloads' general wording and the release's page."""
    out = prepare(Path(out))
    m = meeting()
    cards, clips = measured(bench_sources())
    gh, totals, rel = github(stats), monthly(usage), release(latest)
    offered = rel["version"] if rel else version()
    href = {k: (rel or {}).get("files", {}).get(k, {}).get("url", LATEST) for k in RELEASE_FILES}
    css = (SITE / "styles.css").read_text(encoding="utf-8")
    template = (SITE / "index.html").read_text(encoding="utf-8")
    words = {lang: copy(lang) for lang in LANGS}
    shared = {"version": offered, "href_windows": href["windows"], "href_linux": href["linux"], "ear": ear(), "url_en": URL + LANGS["en"]["folder"], "url_ar": URL + LANGS["ar"]["folder"]}
    for lang, page in LANGS.items():
        other = next(o for o in LANGS if o != lang)
        root = "../" * page["folder"].count("/")
        values = dict(shared, **words[lang], url=URL + page["folder"], root=root, shares=shares_phrase(m, lang),
                      transcript=transcript(m, lang), srt=srt(m, lang),
                      theme_wide=theme_button(words[lang], "wide"), theme_menu=theme_button(words[lang], "menu"),
                      preloads=preloads(page["preload"], root), other_lang=other,
                      other_href=root + LANGS[other]["folder"], other_dir=words[other]["page.dir"],
                      other_locale=words[other]["page.locale"],
                      counts=counts(root + COUNTER, lang, counts_key, counts_host, offered),
                      benchmarks=benchmarks(words[lang], lang, cards, clips), numbers=numbers(words[lang], lang, gh, totals),
                      file_windows=release_file(rel, "windows", words[lang], lang),
                      file_linux=release_file(rel, "linux", words[lang], lang))
        values.update({f"shot_{name}": shot(name, words[lang][alt], root) for name, alt in SHOTS.items()})
        (out / page["folder"]).mkdir(parents=True, exist_ok=True)
        (out / page["folder"] / "index.html").write_text(fill(template, values), encoding="utf-8", newline="\n")
    not_found = {"base": BASE, "icon": icon(), "counts": counts(BASE + COUNTER, "404", counts_key, counts_host, offered)}
    (out / "404.html").write_text(fill((SITE / "404.html").read_text(encoding="utf-8"), not_found),
                                  encoding="utf-8", newline="\n")
    for name in COPIED + ((COUNTER,) if counts_key else ()):
        shutil.copyfile(SITE / name, out / name)
    files = dict(FILES, **{f: f"{FONTS}/{Path(f).name}" for f in fonts(css)},
                 **{f"captures/{p.name}": p.relative_to(ROOT).as_posix() for p in sorted(CAPTURES.glob("*.*"))})
    for target, source in files.items():
        (out / target).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source, out / target)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("out", nargs="?", type=Path, default=ROOT / "_site", help="the folder to build into (default _site/)")
    ap.add_argument("--stats", type=Path, help="a folder with the stats branch's downloads.csv, repo.csv and views.csv; "
                                               "without it GitHub's numbers are left out")
    ap.add_argument("--release", type=Path, help="a folder with the latest release's release.json (and SHA256SUMS.txt "
                                                 "for a release without digests); without it the downloads link the "
                                                 "release's page")
    args = ap.parse_args()
    print(f"built {build(args.out, stats=args.stats, latest=args.release)}")
