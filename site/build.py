"""Build Sedjem's website, which GitHub Pages serves at https://mohammedel-sayedahmed.github.io/sedjem/: the English
page at the top and the Arabic page at ar/.

Both pages come from the one template, site/index.html, filled in with the words of copy/en.toml or copy/ar.toml.
The transcript in the hero, its first lines as SRT and the speakers' shares come from the made-up sprint planning
in docs/images/make/content.py, the meeting the README screenshots show, and the version from app/__init__.py; the
Arabic page gives the speakers Arabic names. The ear beside the hero is the brand icon's, read from docs/brand/. The
pictures of the app are the captures in site/captures/, made by
docs/images/make/screenshots.py (its "site" shot) and shrink.py, each in a desktop and a phone form, light and dark,
as lossless WebP with a PNG for browsers without WebP. Next to the pages go the stylesheet, the scripts, the 404 page and only the files they use: the logo from docs/brand/, the
captures, the social card from docs/images/, the font files the stylesheet names from docs/video/fonts/ with their
licence, and the app's icon. Each page counts its visits anonymously with counts.js, which sends to Aptabase only
from the published host (COUNTS_HOST), so a local preview sends nothing; without a key (COUNTS_KEY) the build
leaves it out. Standard library only.

The section of numbers near the end shows GitHub's (downloads, stars, page views) from the files the Repository stats
workflow keeps on the stats branch, read from a folder given with --stats, and the newest month of the anonymous
counts' totals from docs/usage.json (tools/usage_totals.py). The build never fetches anything: pages.yml downloads
the files into the folder with the API before it builds. Without the folder GitHub's numbers are left out, and
without docs/usage.json the page says when the first totals come. A file that is missing or can't be read leaves its
numbers out, with a line in the log, and never stops the build.

    python site/build.py [out_dir] [--stats DIR]   # out_dir defaults to _site/ at the top of the repository
    python -m http.server 8000 -d _site            # then open http://127.0.0.1:8000/ and http://127.0.0.1:8000/ar/

To try the numbers locally, download the stats files without checking the branch out (a checkout counts as a clone):
    for f in downloads.csv repo.csv views.csv; do
      gh api -H "Accept: application/vnd.github.raw" "repos/MohammedEl-sayedAhmed/sedjem/contents/$f?ref=stats" > stats/$f
    done

The 404 page links its files from the site's path (/sedjem/), as GitHub Pages serves it at any address, so it
shows unstyled in a local preview. site/DESIGN.md has the design notes, and tests/test_site.py checks the result;
.github/workflows/pages.yml builds and publishes it.
"""
import argparse
import ast
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
# How each page writes a number: the thousands separator and the decimal point. Both pages write Western digits, as
# the Arabic copy does everywhere, grouped as Egypt groups them.
NUMERALS = {"en": (",", "."), "ar": (",", ".")}

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
    "images/social-preview.png": "docs/images/social-preview.png",
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


def counts(src, page, key, host):
    """The script that counts a page, with what it needs on its element: the page, the key, the address, the host it
    may send from and the version the page offers. Nothing without a key."""
    if not key:
        return ""
    if not COUNTS_KEY_FORMAT.fullmatch(key):
        raise SystemExit(f"{key!r} is not a key of Aptabase's EU region")
    return (f'  <script src="{src}" defer data-page="{page}" data-key="{key}" data-endpoint="{COUNTS_ENDPOINT}" '
            f'data-host="{html.escape(host)}" data-version="{html.escape(version())}"></script>')


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


def github(folder):
    """GitHub's numbers from the stats files in folder, as many as could be read: the day they are of (the newest in
    the daily snapshots), the downloads of the app (its files, not the checksums) from every release and from the
    newest that isn't a preview, the stars, and the page views of the repository in the last VIEWS_DAYS days
    recorded, or from the first day recorded when that is later (views_whole False). None without a folder."""
    if folder is None:
        return None
    gh = {}
    downloads = stats_rows(folder, "downloads.csv")
    if downloads:
        newest = max(r["date"] for r in downloads)
        app = [r for r in downloads if r["date"] == newest and not r["asset"].lower().endswith(".txt")]
        gh.update(day=newest, downloads=sum(r["downloads"] for r in app))
        stable = [r["release"] for r in app if version_key(r["release"])]
        if stable:
            release = max(stable, key=version_key)
            gh.update(release=STABLE.fullmatch(release).group(1),
                      latest=sum(r["downloads"] for r in app if r["release"] == release))
    repo = stats_rows(folder, "repo.csv")
    if repo:
        last = max(repo, key=lambda r: r["date"])
        gh.update(day=max(gh.get("day", last["date"]), last["date"]), stars=last["stars"])
    views = stats_rows(folder, "views.csv")
    if views:
        last, first = max(r["date"] for r in views), min(r["date"] for r in views)
        start = max(last - timedelta(VIEWS_DAYS - 1), first)
        gh.update(views=sum(r["views"] for r in views if r["date"] >= start), views_from=start, views_to=last,
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


def monthly(path):
    """The newest month of the anonymous counts' totals in docs/usage.json (tools/usage_totals.py): {"app": the
    installs, their countries, the transcriptions, the hours of audio and the most used model (None when none was
    used), "website": its page views, their countries and the download clicks that month, or None when it has none}.
    {} while the file isn't there, before the first month is exported; None, with a line in the log, when it can't be
    read. Website totals that can't be read are left out alone."""
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
        t = months[newest]
        models = {e: count(t["runs"]["models"].get(e, 0)) for e in ENGINES}
        hours = t["runs"]["hours"]
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not 0 <= hours < 10 ** 9:
            raise ValueError(f"{hours!r} isn't a number of hours")
        app = {"month": newest, "installs": count(t["installs"]["active"]),
               "countries": count(t["installs"]["countries"]["count"]),
               "transcriptions": count(t["runs"]["transcriptions"]), "hours": round(float(hours), 1),
               "model": max(ENGINES, key=lambda e: (models[e], -ENGINES.index(e))) if any(models.values()) else None}
    except (AttributeError, KeyError, TypeError, ValueError) as e:
        note(f"{path} doesn't hold the totals tools/usage_totals.py writes ({e!r}); the counts are left out")
        return None
    site = None
    if newest in (data.get("website") or {}):
        try:
            w = data["website"][newest]
            site = {"page_views": count(w["page_views"]), "countries": count(w["countries"]["count"]),
                    "clicks": count(w["downloads"]["clicks"])}
        except (AttributeError, KeyError, TypeError, ValueError) as e:
            note(f"the website's totals for {newest} in {path} can't be read ({e!r}); they are left out")
    return {"app": app, "website": site}


def figure(n, lang):
    """A number as the page writes it, in a data element that holds its plain value: 1,234 or 7.8, the tenths only
    when there are some."""
    if isinstance(n, float) and n.is_integer():
        n = int(n)
    group, point = NUMERALS[lang]
    text = f"{n:,}" if isinstance(n, int) else f"{n:,.1f}"
    return f'<data value="{n}">{"".join(group if c == "," else point if c == "." else c for c in text)}</data>'


def day(d, words, year=True):
    """A day in a time element, in the page's language: 29 October 2026, or 28 October without the year."""
    months = words["numbers.months"].split()
    return f'<time datetime="{d.isoformat()}">{d.day} {months[d.month - 1]}{f" {d.year}" if year else ""}</time>'


def month(m, words):
    """A month, 2026-11, in a time element, in the page's language: November 2026."""
    year, number = m.split("-")
    return f'<time datetime="{m}">{words["numbers.months"].split()[int(number) - 1]} {year}</time>'


def numbers(words, lang, gh, totals):
    """The section of numbers: on a sheet, GitHub's numbers beside the app's newest month and the website's for the
    same month across the foot, or a line saying when the first totals come; under it, a line saying where they come
    from. Nothing when there is nothing to show."""
    say = lambda key, **values: fill(words[f"numbers.{key}"], values)  # noqa: E731
    item = lambda key, value, **values: f"              <div><dt>{say(key, **values)}</dt><dd>{value}</dd></div>"  # noqa: E731

    def group(heading, items, wide=False):
        start = '          <div class="spec-wide">' if wide else "          <div>"
        return "\n".join([start, f"            <h3>{heading}</h3>", "            <dl>", *items, "            </dl>",
                          "          </div>"])

    groups, caption = [], []
    if gh:
        items = []
        if "downloads" in gh:
            items.append(item("downloads", figure(gh["downloads"], lang)))
        if "latest" in gh:
            items.append(item("latest", figure(gh["latest"], lang), release=html.escape(gh["release"])))
        if "stars" in gh:
            items.append(item("stars", figure(gh["stars"], lang)))
        if "views" in gh:
            items.append(item("views", figure(gh["views"], lang), day=day(gh["views_to"], words, year=False))
                         if gh["views_whole"] else
                         item("views_between", figure(gh["views"], lang), first=day(gh["views_from"], words, year=False),
                              last=day(gh["views_to"], words, year=False)))
        groups.append(group(say("github", day=day(gh["day"], words)), items))
        caption.append(say("caption_github", charts=CHARTS))
    if totals == {}:
        groups.append("\n".join(["          <div>", f'            <h3>{say("counts")}</h3>',
                                 f'            <p>{say("not_yet")}</p>', "          </div>"]))
    elif totals:
        app, site = totals["app"], totals["website"]
        items = [item("installs", figure(app["installs"], lang)), item("countries", figure(app["countries"], lang)),
                 item("transcriptions", figure(app["transcriptions"], lang)), item("hours", figure(app["hours"], lang))]
        if app["model"]:
            items.append(item("model", say(f"model_{app['model']}")))
        groups.append(group(say("app", month=month(app["month"], words)), items))
        if site:
            groups.append(group(say("website", month=month(app["month"], words)),
                                [item("page_views", figure(site["page_views"], lang)),
                                 item("countries", figure(site["countries"], lang)),
                                 item("clicks", figure(site["clicks"], lang))], wide=True))
    if totals is not None:
        caption.append(say("caption_counts", method=METHOD))
    if not groups:
        return ""
    return "\n".join([
        '  <section class="sec" aria-labelledby="numbers">',
        '    <div class="wrap sec-grid">',
        '      <div class="claim">',
        f'        <h2 id="numbers">{say("h2")}</h2>',
        f'        <p class="lede">{say("lede")}</p>',
        "      </div>",
        '      <figure class="proof">',
        '        <div class="spec">',
        *groups,
        "        </div>",
        f"        <figcaption>{' '.join(caption)}</figcaption>",
        "      </figure>",
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


def build(out=ROOT / "_site", counts_key=COUNTS_KEY, counts_host=COUNTS_HOST, stats=None, usage=USAGE):
    """Build the site into out and return it. counts_host is the one host whose pages send their counts (the tests
    build pages that count on 127.0.0.1), and an empty counts_key leaves the counting out. stats is the folder with
    the stats branch's files, None to leave GitHub's numbers out, and usage the totals of the anonymous counts."""
    out = prepare(Path(out))
    m = meeting()
    gh, totals = github(stats), monthly(usage)
    css = (SITE / "styles.css").read_text(encoding="utf-8")
    template = (SITE / "index.html").read_text(encoding="utf-8")
    words = {lang: copy(lang) for lang in LANGS}
    shared = {"version": version(), "ear": ear(), "url_en": URL + LANGS["en"]["folder"], "url_ar": URL + LANGS["ar"]["folder"]}
    for lang, page in LANGS.items():
        other = next(o for o in LANGS if o != lang)
        root = "../" * page["folder"].count("/")
        values = dict(shared, **words[lang], url=URL + page["folder"], root=root, shares=shares_phrase(m, lang),
                      transcript=transcript(m, lang), srt=srt(m, lang),
                      theme_wide=theme_button(words[lang], "wide"), theme_menu=theme_button(words[lang], "menu"),
                      preloads=preloads(page["preload"], root), other_lang=other,
                      other_href=root + LANGS[other]["folder"], other_dir=words[other]["page.dir"],
                      other_locale=words[other]["page.locale"],
                      counts=counts(root + COUNTER, lang, counts_key, counts_host),
                      numbers=numbers(words[lang], lang, gh, totals))
        values.update({f"shot_{name}": shot(name, words[lang][alt], root) for name, alt in SHOTS.items()})
        (out / page["folder"]).mkdir(parents=True, exist_ok=True)
        (out / page["folder"] / "index.html").write_text(fill(template, values), encoding="utf-8", newline="\n")
    not_found = {"base": BASE, "icon": icon(), "counts": counts(BASE + COUNTER, "404", counts_key, counts_host)}
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
    args = ap.parse_args()
    print(f"built {build(args.out, stats=args.stats)}")
