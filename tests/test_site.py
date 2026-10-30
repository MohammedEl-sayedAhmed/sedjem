"""Tests for the website (site/build.py), which has an English page at the top and an Arabic one at ar/. The site
is built into a temporary folder and checked as GitHub Pages serves it, every check on each page: the pages parse,
every file they link or load is in the build and nothing is loaded from another site, every colour in the
stylesheet is one of the palette's, images have alt text and their real proportions, Arabic is marked as Arabic and
the Arabic page reads right to left, the copy keeps the repository's style, the transcript and the speakers' shares
come from docs/images/make/content.py and the version from app/__init__.py, the two pages carry the same sections, numbers and images and point to each other, each page shares its own picture
of 1200 x 630 described in its language, the theme button is named in each
language and the dark theme is one set of tokens, the transcript, the download and the menu are whole without
JavaScript, the speakers' names are in colours that read on the card, the pictures of the app keep every pixel and
are set at their own size, and the first view stays small. The anonymous counts go only to Aptabase, only from the
published site, never with Do Not Track or Global Privacy Control, and hold only the values the privacy policy lists.
The section of numbers, built from made-up stats files and totals (tests/site_support.py), is a grid of charts whose
every number is its source's, each with a table of the same numbers; it is dated right, the same on both pages, says
when the first totals come before there are any, leaves out what a missing or broken file would have given, and is
all in the HTML: the build reads files and fetches nothing. The benchmarks are six figure cards over bars to compare,
every number the one the docs publish, read through docs/charts.py from the files the bench scripts write and
checked here against the docs' tables and sentences; the bars start at zero and say which way is better, each chart has
a table of its numbers, the test sets are linked with their licences, the private meetings are only ever a private set
with pooled numbers, and no other section says one of their numbers again. The downloads are the latest release's files, with their
names, sizes and SHA256 as the release gives them, or the release's page and the general wording without its
details. Offline, standard library only.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import ast
import contextlib
import fnmatch
import importlib.util
import io
import json
import math
import posixpath
import re
import struct
import sys
import tempfile
import tomllib
import unittest
from collections import Counter
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import site_support as fixture  # noqa: E402

_spec = importlib.util.spec_from_file_location("site_build", ROOT / "site" / "build.py")
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)

HOMES = {"en": "index.html", "ar": "ar/index.html"}  # each language's page in the build
# The only colours the stylesheet may use, with where each comes from (rgba() of one of them is allowed too)
PALETTE = {
    "#f4f5f7": "app.css light --bg", "#ffffff": "app.css light --surface and --accent-text",
    "#f7f8fa": "app.css light --surface-2", "#eef0f3": "app.css light --sunken", "#e2e5ea": "app.css light --border",
    "#cfd4dc": "app.css light --border-strong", "#18202b": "app.css light --text", "#5f6b7a": "app.css light --muted",
    "#8a95a3": "app.css light --faint", "#0e7c7b": "app.css light --accent", "#0a6362": "app.css light --accent-strong",
    "#e2f3f2": "app.css light --accent-soft", "#fff6d9": "app.css light --current", "#ffe28a": "app.css light --mark",
    "#2563eb": "app.css light --s1", "#d9480f": "app.css light --s2", "#7c3aed": "app.css light --s3",
    "#0f766e": "app.css light --s4", "#b45309": "app.css light --s7, the darker orange of Karim's name on the card",
    "#0f1216": "app.css dark --bg", "#161a20": "app.css dark --surface", "#1b2027": "app.css dark --surface-2",
    "#11151a": "app.css dark --sunken", "#262c35": "app.css dark --border", "#353d48": "app.css dark --border-strong",
    "#e6eaf0": "app.css dark --text", "#9aa5b3": "app.css dark --muted", "#6f7a88": "app.css dark --faint",
    "#2bb3b0": "app.css dark --accent", "#3cc9c6": "app.css dark --accent-strong", "#123332": "app.css dark --accent-soft",
    "#06201f": "app.css dark --accent-text", "#2b2715": "app.css dark --current", "#6b5a12": "app.css dark --mark",
    "#6ea0ff": "app.css dark --s1", "#ff8f5c": "app.css dark --s2", "#b794ff": "app.css dark --s3",
    "#3fcfbf": "app.css dark --s4",
    "#14a3a1": "docs/brand: the ear and cursor on light", "#0b5f5e": "docs/brand: the icon gradient's dark end",
    "#2dd4bf": "docs/brand: the ear and cursor on dark", "#111111": "docs/brand: the wordmark's letters on light",
    "#6dbeba": "site: the numbers' light teal, a whole's second part on the light card",
    "#2f7f7c": "site: the numbers' darker teal, a whole's second part on the dark card",
}
NAMED_COLOURS = re.compile(
    r"\b(?:white|black|red|green|blue|gr[ae]y|silver|orange|yellow|purple|pink|teal|navy|maroon|olive|lime|aqua|"
    r"fuchsia|cyan|magenta|brown|gold|indigo|violet|tomato|coral|salmon|khaki|beige|ivory|crimson)\b", re.I)
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
ARABIC = re.compile("[" + chr(0x600) + "-" + chr(0x6FF) + "]")
LATIN_WORD = re.compile(r"[A-Za-z]{2,}")
NBSP = chr(0xA0)
# bytes: a page, its stylesheet and script, every font file and the header's two logos. The section of numbers adds
# about 18 kB to a page, its charts and the tables that say their numbers, and up to 10 kB more as the running totals
# grow by a point a week (DAILY)
FIRST_VIEW_BUDGET = 385_000
# The two lines of the registers, and the words marked in each
REGISTERS = [
    ("صباح الخير يا جماعة، خلونا نبدأ السبرينت بلانينج بسرعة عشان عندنا ديمو الساعة اتنين",
     ["السبرينت بلانينج", "ديمو"]),
    ("صباح الخير يا جماعة، خلونا نبدأ ال sprint planning بسرعة عشان عندنا demo الساعة اتنين",
     ["sprint planning", "demo"]),
]
# How each page writes the version in the hero's small print
VERSION_IN = {"en": "Version {}.", "ar": "الإصدار {}."}
# The anonymous counts (site/counts.js; docs/privacy.md, "The website"): each event with its props and their values
# (None for the referrer, a host name), the system props, and the one address they go to
COUNTED = {"page_view": {"page": ["en", "ar", "404"], "device": ["phone", "tablet", "desktop"],
                         "theme": ["light", "dark", "system"], "referrer": None},
           "download": {"platform": ["windows", "linux", "source"], "place": ["hero", "get"], "page": ["en", "ar", "404"]}}
SYSTEM_PROPS = ["locale", "appVersion", "isDebug", "sdkVersion", "osName"]
ENDPOINT = "https://eu.aptabase.com/api/v0/event"
PAGE_OF = {"index.html": "en", "ar/index.html": "ar", "404.html": "404"}  # each page's name in the counts
# The files the benchmarks are read from: docs/charts.py and the data its sources read
BENCH_FILES = ["docs/charts.py", "docs/charts-data.json", "results/summary.json", "results/arzen-diarization.json",
               "results/ami-diarization.json"]


class Node:
    def __init__(self, tag, attrs, parent):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs), parent, []

    def iter(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.iter()

    def find(self, test):
        return [n for n in self.iter() if test(n)]

    def text(self):
        return "".join(c if isinstance(c, str) else c.text() for c in self.children)

    def classes(self):
        return self.attrs.get("class", "").split()

    def nearest(self, attr):
        node = self
        while node is not None and attr not in node.attrs:
            node = node.parent
        return node.attrs[attr] if node is not None else None

    def inside(self, test):
        node = self
        while node is not None:
            if test(node):
                return True
            node = node.parent
        return False


class Strict(HTMLParser):
    """A tree of the page that records every end tag that doesn't close the element it should."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.at = Node("#document", [], None)
        self.errors = []

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.at)
        self.at.children.append(node)
        if tag not in VOID:
            self.at = node

    def handle_startendtag(self, tag, attrs):
        self.at.children.append(Node(tag, attrs, self.at))

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if self.at.tag != tag:
            self.errors.append(f"</{tag}> where <{self.at.tag}> is open")
            while self.at.parent is not None and self.at.tag != tag:
                self.at = self.at.parent
        if self.at.parent is not None:
            self.at = self.at.parent

    def handle_data(self, data):
        self.at.children.append(data)


def parse(text):
    parser = Strict()
    parser.feed(text)
    parser.close()
    if parser.at is not parser.root:
        parser.errors.append(f"<{parser.at.tag}> is never closed")
    return parser.root, parser.errors


def visible_text(node):
    """The text a reader sees: everything but scripts and styles."""
    if node.tag in ("script", "style"):
        return ""
    return "".join(c if isinstance(c, str) else visible_text(c) for c in node.children)


def squash(text):
    return " ".join(text.replace(NBSP, " ").split())


def clock(seconds):
    seconds = math.floor(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def webp_chunks(data):
    """The chunks of a WebP file, (name, body) in order."""
    at, chunks = 12, []
    while at + 8 <= len(data):
        name, size = data[at:at + 4], struct.unpack("<I", data[at + 4:at + 8])[0]
        chunks.append((name, data[at + 8:at + 8 + size]))
        at += 8 + size + size % 2
    return chunks


def image_size(path):
    """(width, height) of a PNG, a lossless WebP, or an SVG's viewBox."""
    data = path.read_bytes()
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", data[16:24])
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        (body,) = [body for name, body in webp_chunks(data) if name == b"VP8L"]
        bits = int.from_bytes(body[1:5], "little")  # after the signature byte: 14 bits each, less one
        return (bits & 0x3FFF) + 1, (bits >> 14 & 0x3FFF) + 1
    box = re.search(r'viewBox="\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)\s*"', data.decode("utf-8"))
    return float(box.group(1)), float(box.group(2))


def hex_of(colour):
    """A CSS colour as #rrggbb, or None if it isn't a hex, rgb() or rgba() colour."""
    colour = colour.lower()
    if colour.startswith("#"):
        digits = colour[1:]
        if len(digits) in (3, 4):
            digits = "".join(c * 2 for c in digits)
        return "#" + digits[:6]
    m = re.fullmatch(r"rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)(?:\s*[,/]\s*[\d.]+%?)?\s*\)", colour)
    return "#%02x%02x%02x" % tuple(int(v) for v in m.groups()) if m else None


class SiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)  # with every number, so each check covers the section of numbers too
        cls.out = build.build(tmp / "site", stats=fixture.write_stats(tmp / "stats"),
                              usage=fixture.write_usage(tmp / "usage.json"), latest=fixture.write_release(tmp / "release"))
        cls.meeting = build.meeting()
        cls.pages = {page: parse((cls.out / page).read_text(encoding="utf-8")) for page in build.PAGES}
        cls.homes = {lang: cls.pages[page][0] for lang, page in HOMES.items()}
        cls.css = (cls.out / "styles.css").read_text(encoding="utf-8")
        # every stylesheet, with the page its addresses are read from: the site's, and the 404 page's own
        cls.styles = [("styles.css", cls.css)] + [
            (page, n.text()) for page, (root, _) in cls.pages.items() for n in root.find(lambda n: n.tag == "style")]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def local(self, page, url):
        """The built file a page's local address points to, or None for another site or an anchor."""
        parts = urlsplit(url)
        if parts.scheme or parts.netloc or not parts.path:
            return None
        path = parts.path
        if path.startswith("/"):
            self.assertTrue(path.startswith(build.BASE), f"{page}: {url} is outside the site's path")
            path = path[len(build.BASE):]
        else:
            path = posixpath.normpath(posixpath.join(posixpath.dirname(page), path)) + ("/" if path.endswith("/") else "")
        target = self.out / (path.lstrip("./") if path not in (".", "./") else "")
        return target / "index.html" if path.endswith("/") or target.is_dir() else target

    def resources(self, root):
        """(element, address) for everything a page loads: stylesheets, fonts, icons, scripts and images."""
        for node in root.iter():
            rel = node.attrs.get("rel", "")
            if node.tag == "link" and rel in ("stylesheet", "preload", "icon", "apple-touch-icon", "manifest"):
                yield node, node.attrs["href"]
            if node.tag in ("script", "img", "iframe", "embed", "video", "audio") and "src" in node.attrs:
                yield node, node.attrs["src"]
            for attr in ("srcset", "poster", "data"):
                if attr in node.attrs:
                    for candidate in node.attrs[attr].split(","):
                        yield node, candidate.split()[0]

    def images(self, page):
        """The built files of every picture a page shows."""
        return sorted(self.local(page, url).relative_to(self.out).as_posix() for node, url in self.resources(self.pages[page][0])
                      if node.tag in ("img", "source"))

    def test_pages_parse(self):
        for page, (root, errors) in self.pages.items():
            with self.subTest(page):
                self.assertEqual(errors, [])
                (html,) = root.find(lambda n: n.tag == "html")
                self.assertIn(html.attrs.get("lang"), ("en", "ar"))
                self.assertEqual(len(root.find(lambda n: n.tag == "h1")), 1)
                levels = [int(n.tag[1]) for n in root.iter() if re.fullmatch(r"h[1-6]", n.tag)]
                self.assertTrue(all(b <= a + 1 for a, b in zip(levels, levels[1:])), f"a skipped level: {levels}")
                ids = [n.attrs["id"] for n in root.iter() if "id" in n.attrs]
                self.assertEqual(len(ids), len(set(ids)), "an id used twice")
                for n in root.find(lambda n: n.tag == "a" and n.attrs.get("href", "").startswith("#")):
                    self.assertIn(n.attrs["href"][1:], ids, f"no element for {n.attrs['href']}")
                self.assertTrue(root.find(lambda n: n.tag == "main"))
                if page in HOMES.values():  # the 404 page has nothing before its main to skip
                    self.assertTrue(root.find(lambda n: n.tag == "a" and "skip" in n.classes()))
                self.assertNotIn("{{", (self.out / page).read_text(encoding="utf-8"))

    def test_every_local_address_is_in_the_build(self):
        checked = 0
        for page, (root, _) in self.pages.items():
            for node in root.iter():
                urls = [node.attrs[a] for a in ("href", "src") if a in node.attrs]
                urls += [c.split()[0] for c in node.attrs.get("srcset", "").split(",") if c.strip()]
                for url in urls:
                    target = self.local(page, url)
                    if target is not None:
                        with self.subTest(page, url=url):
                            self.assertTrue(target.is_file())
                        checked += 1
        for where, css in self.styles:
            for url in re.findall(r'url\(\s*["\']?([^"\')]+)', css):
                target = self.local(where, url)
                with self.subTest(where, url=url):
                    self.assertIsNotNone(target, "a stylesheet loads something from another site")
                    self.assertTrue(target.is_file())
                checked += 1
        self.assertGreater(checked, 40)

    def test_nothing_loads_from_another_site(self):
        for page, (root, _) in self.pages.items():
            self.assertFalse(root.find(lambda n: n.tag in ("iframe", "embed", "object", "video", "audio")))
            for node, url in self.resources(root):
                with self.subTest(page, url=url):
                    parts = urlsplit(url)
                    self.assertFalse(parts.scheme or parts.netloc, f"<{node.tag}> loads from another site")
            # absolute addresses outside links are only the site's own: canonical, alternates and the cards
            for node in root.find(lambda n: n.tag in ("link", "meta")):
                url = node.attrs.get("href") or node.attrs.get("content", "")
                if re.match(r"(?:[a-z]+:)?//", url):
                    self.assertTrue(url.startswith(build.URL), url)
        for where, css in self.styles:
            self.assertNotIn("@import", css)
            for url in re.findall(r'url\(\s*["\']?([^"\')]+)', css):
                self.assertFalse(urlsplit(url).scheme or urlsplit(url).netloc, f"{where}: {url}")
        # The pages send one thing elsewhere, the counts: one fetch in counts.js, to the address on its element, which
        # is the one absolute address in the pages outside their links and cards
        self.assertNotRegex((self.out / "site.js").read_text(encoding="utf-8"), r"https?://|fetch\(|XMLHttpRequest")
        counter = (self.out / build.COUNTER).read_text(encoding="utf-8")
        self.assertEqual(counter.count("fetch("), 1)
        self.assertNotRegex(counter, r"https?://|XMLHttpRequest|sendBeacon|WebSocket|EventSource|import\(|\.src\b|new Image")
        for page, (root, _) in self.pages.items():
            elsewhere = {value for node in root.iter() if node.tag not in ("a", "link", "meta")
                         for value in node.attrs.values() if value and re.match(r"\s*(?:[a-z]+:)?//", value)}
            self.assertEqual(elsewhere, {ENDPOINT}, page)
            self.assertEqual(urlsplit(ENDPOINT).hostname, "eu.aptabase.com")
            for script in root.find(lambda n: n.tag == "script" and "src" not in n.attrs):
                self.assertNotRegex(script.text(), r"//|fetch\(|XMLHttpRequest|sendBeacon", page)

    def test_each_page_counts_once_with_the_websites_key(self):
        """Each page, the 404 page too, has one counting script, deferred, from the site itself, with the website's own
        Aptabase key, the EU address, the published host, the page and the version on its element. Every script is
        the site's own."""
        self.assertRegex(build.COUNTS_KEY, r"^A-EU-\d{10}$")
        self.assertEqual(build.COUNTS_HOST, "mohammedel-sayedahmed.github.io")
        self.assertEqual(build.COUNTS_ENDPOINT, ENDPOINT)
        for page, (root, _) in self.pages.items():
            scripts = root.find(lambda n: n.tag == "script")
            with self.subTest(page):
                for script in scripts:
                    if "src" in script.attrs:
                        self.assertIsNotNone(self.local(page, script.attrs["src"]), "a script from another site")
                (counter,) = [n for n in scripts if "data-key" in n.attrs]
                self.assertEqual(self.local(page, counter.attrs["src"]), self.out / build.COUNTER)
                self.assertIn("defer", counter.attrs)
                self.assertEqual({k: v for k, v in counter.attrs.items() if k.startswith("data-")},
                                 {"data-page": PAGE_OF[page], "data-key": build.COUNTS_KEY, "data-endpoint": ENDPOINT,
                                  "data-host": build.COUNTS_HOST, "data-version": fixture.RELEASE})

    def test_the_counts_check_before_they_send_and_hold_only_the_listed_values(self):
        """counts.js stops before anything is sent when Do Not Track or Global Privacy Control is on or the page isn't
        on the published host; keeps nothing in the browser; and sends only the two events, with the props, values and
        system props the privacy policy lists, checked again on the way out. The download links name what they are
        from the same lists."""
        js = (self.out / build.COUNTER).read_text(encoding="utf-8")
        code = re.sub(r"/\*.*?\*/|//[^\n]*", "", js, flags=re.S)
        before = code[:code.index("function send(")]
        self.assertRegex(before, r'\n  if \(nav\.doNotTrack === "1" \|\| window\.doNotTrack === "1" \|\| '
                                 r'nav\.globalPrivacyControl === true\) return;')
        self.assertRegex(before, r'\n  if \(!script \|\| !window\.fetch \|\| location\.hostname !== script\.getAttribute\("data-host"\)\) return;')
        self.assertNotRegex(code, r"(?i)localStorage|sessionStorage|indexedDB|cookie|caches|openDatabase")
        self.assertIn('credentials: "omit"', code)
        self.assertIn("keepalive: true", code)
        # the events and their props, and the system props, exactly
        self.assertEqual(json.loads(re.search(r"var EVENTS = (\{.*?\});", code, re.S).group(1)), COUNTED)
        system = re.search(r"var SYSTEM = \{(.*?)\};", code, re.S).group(1)
        self.assertEqual(re.findall(r"(?:^|,)\s*(\w+):", system), SYSTEM_PROPS)
        self.assertIn('sdkVersion: "sedjem-site@1"', system)
        self.assertIn("isDebug: false", system)
        self.assertEqual({name: re.findall(r"(\w+):", props) for name, props in re.findall(r'send\("(\w+)", \{([^}]*)\}\)', code)},
                         {name: list(props) for name, props in COUNTED.items()})
        body = re.search(r"body: JSON\.stringify\(\{(.*?)\}\)", code, re.S).group(1)
        self.assertEqual(re.findall(r"(\w+):", body), ["timestamp", "sessionId", "eventName", "systemProps", "props"])
        self.assertIn("if (!checked(name, props)) return;", code)
        # the download links: the hero's platform is its label on show, and the others name theirs
        for lang, home in self.homes.items():
            with self.subTest(lang):
                links = home.find(lambda n: n.tag == "a" and "data-download" in n.attrs)
                self.assertEqual([(n.attrs["data-download"], n.attrs["data-place"]) for n in links],
                                 [(None, "hero"), ("windows", "get"), ("linux", "get"), ("source", "get")])
                self.assertEqual([n.attrs["data-os"] for n in links[0].find(lambda n: "data-os" in n.attrs)], ["windows", "linux"])
                for link in links:
                    self.assertEqual(link.attrs["href"].split("/")[2], "github.com")
        # and the privacy policy names every event and value
        policy = (ROOT / "docs" / "privacy.md").read_text(encoding="utf-8").split("## The website")[1].split("\n## ")[0]
        for name, props in COUNTED.items():
            self.assertIn(f"`{name}`", policy)
            for values in props.values():
                for value in values or ():
                    self.assertIn(f"`{value}`", policy)
        self.assertIn("https://aptabase.com/legal/privacy", policy)
        self.assertIn("Global Privacy Control", policy)

    def test_without_a_key_nothing_counts(self):
        """An empty key leaves the counting out: no script on any page and no counts.js. A key of another kind is
        refused."""
        with tempfile.TemporaryDirectory() as tmp:
            out = build.build(Path(tmp) / "site", counts_key="")
            self.assertFalse((out / build.COUNTER).exists())
            for page in build.PAGES:
                root, errors = parse((out / page).read_text(encoding="utf-8"))
                self.assertEqual(errors, [])
                self.assertFalse(root.find(lambda n: n.tag == "script" and "data-key" in n.attrs), page)
                self.assertNotIn("aptabase", (out / page).read_text(encoding="utf-8"))
        for key in ("A-US-3941212028", "A-SH-1234567890", "A-EU-", "eu-3941212028"):
            with self.subTest(key), self.assertRaises(SystemExit):
                build.counts(build.COUNTER, "en", key, build.COUNTS_HOST)

    def test_every_colour_is_from_the_palette(self):
        found = 0
        for where, css in self.styles:
            css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
            for colour in re.findall(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|color|color-mix)\([^)]*\)", css):
                with self.subTest(where, colour=colour):
                    self.assertIn(hex_of(colour), PALETTE, "not a palette colour (hex, rgb() or rgba() only)")
                found += 1
            for value in re.findall(r":\s*([^;{}]+)[;}]", css):
                self.assertNotRegex(value, NAMED_COLOURS, f"{where}: a named colour")
        self.assertGreater(found, 40)
        # inline styles and the theme colours in the pages
        for page, (root, _) in self.pages.items():
            for node in root.iter():
                style = node.attrs.get("style", "")
                self.assertNotRegex(style, r"#[0-9a-fA-F]{3}|rgb|hsl", f"{page}: a colour in a style attribute")
                if node.attrs.get("name") == "theme-color":
                    self.assertIn(node.attrs["content"], ("#f4f5f7", "#0f1216"))

    def test_images_have_alt_text_and_their_real_size(self):
        images = 0
        for page, (root, _) in self.pages.items():
            for img in root.find(lambda n: n.tag == "img"):
                with self.subTest(page, src=img.attrs.get("src")):
                    self.assertGreaterEqual(len(img.attrs.get("alt", "").split()), 1)
                    width, height = int(img.attrs["width"]), int(img.attrs["height"])
                    real = image_size(self.local(page, img.attrs["src"]))
                    self.assertAlmostEqual(width / height, real[0] / real[1], delta=0.02 * real[0] / real[1])
                    images += 1
        self.assertGreaterEqual(images, 12)
        # the pictures of the app load when they come near, the logo straight away. Each is set at its own size, its
        # pixels over the density it was taken at (twice the size on a desktop, three times on a phone), as WebP with
        # the same pixels in PNG after it, and the phone form below the width where the desktop one fits
        for lang, home in self.homes.items():
            for img in home.find(lambda n: n.tag == "img" and "captures/" in n.attrs["src"]):
                with self.subTest(lang, src=img.attrs["src"]):
                    self.assertEqual(img.attrs.get("loading"), "lazy")
                    self.assertGreaterEqual(len(img.attrs["alt"].split()), 10)
                    sources = img.parent.find(lambda n: n.tag == "source")
                    self.assertEqual([(n.attrs.get("media"), n.attrs.get("type")) for n in sources],
                                     [(build.NARROW, "image/webp"), (build.NARROW, None), (None, "image/webp")])
                    for node in sources + [img]:
                        url, density = node.attrs["srcset"].split()
                        form = "narrow" if "-narrow-" in url else "wide"
                        self.assertEqual(density, f"{build.DENSITY[form]}x")
                        real = image_size(self.local(HOMES[lang], url))
                        self.assertEqual((int(node.attrs["width"]), int(node.attrs["height"])),
                                         (real[0] // build.DENSITY[form], real[1] // build.DENSITY[form]))
                        self.assertEqual(real[0] % build.DENSITY[form], 0, url)
                    self.assertEqual(img.attrs["src"], img.attrs["srcset"].split()[0])
                    self.assertEqual([n.attrs["srcset"].split()[0].rsplit(".", 1)[1] for n in sources + [img]],
                                     ["webp", "png", "webp", "png"])
        # and the phone form is used just below the width where the desktop form fits: the widest desktop form
        # with its frame inside the column of a 750 px window with a scroll bar
        widest = max(image_size(p)[0] // 2 for p in build.CAPTURES.glob("*-wide-*.png"))
        self.assertLessEqual(widest + 8, 750 - 17 - 2 * 32)
        self.assertEqual(build.NARROW, "(max-width: 749px)")
        self.assertLessEqual(max(image_size(p)[0] // 3 for p in build.CAPTURES.glob("*-narrow-*.png")) + 8, 320 - 2 * 16)

    def test_no_picture_of_the_app_is_quantized(self):
        """The captures keep every colour they were taken with: a PNG in full colour (RGB or RGBA, 8 bits, not a
        palette of 256), and a WebP compressed losslessly (VP8L, not the lossy VP8), with the same pixels."""
        files = sorted(build.CAPTURES.iterdir())
        self.assertEqual(len(files), 2 * 2 * 2 * len(build.SHOTS))
        for path in files:
            data = path.read_bytes()
            with self.subTest(path.name):
                if path.suffix == ".png":
                    self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
                    bit_depth, colour_type = data[24], data[25]
                    self.assertEqual(bit_depth, 8)
                    self.assertIn(colour_type, (2, 6), "a palette or grey PNG")
                    self.assertNotIn(b"PLTE", data[:data.index(b"IDAT")])
                else:
                    self.assertEqual(path.suffix, ".webp")
                    self.assertEqual((data[:4], data[8:12]), (b"RIFF", b"WEBP"))
                    names = [name for name, _ in webp_chunks(data)]
                    self.assertIn(b"VP8L", names)
                    self.assertNotIn(b"VP8 ", names)
                    self.assertEqual(image_size(path), image_size(path.with_suffix(".png")))

    def test_the_speakers_names_are_in_their_colours(self):
        """The hero's names are in their speakers' colours, the app's, and bold, without the dot they once had; each
        colour reads on the transcript's card at 4.5:1 or more in both themes, as names of this size need."""
        css = re.sub(r"/\*.*?\*/", "", self.css, flags=re.S)
        (who,) = re.findall(r"(?:^|\n)\.who\s*\{([^}]*)\}", css)
        self.assertRegex(who, r"color:\s*var\(--c\)")
        self.assertRegex(who, r"font-size:\s*var\(--fs-small\)")
        self.assertRegex(who, r"font-weight:\s*600")
        self.assertNotRegex(css, r"\.who::before")
        light = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-f]{6})", re.search(r":root\s*\{([^}]*)\}", css).group(1)))
        dark = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-f]{6})", re.search(r':root\[data-theme="dark"\]\s*\{([^}]*)\}', css).group(1)))

        def luminance(colour):
            channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            r, g, b = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        def contrast(a, b):
            high, low = sorted((luminance(a), luminance(b)), reverse=True)
            return (high + 0.05) / (low + 0.05)

        for theme, tokens in (("light", light), ("dark", dark)):
            for speaker in build.SPEAKERS:
                with self.subTest(theme, speaker=speaker):
                    self.assertIn(tokens[f"--{speaker}"], PALETTE)
                    self.assertTrue(PALETTE[tokens[f"--{speaker}"]].startswith(f"app.css {theme} --s"))
                    self.assertGreaterEqual(contrast(tokens[f"--{speaker}"], tokens["--card"]), 4.5)
        self.assertEqual([light[f"--{s}"] for s in build.SPEAKERS], ["#2563eb", "#b45309", "#7c3aed", "#0f766e"])
        self.assertEqual([dark[f"--{s}"] for s in build.SPEAKERS], ["#6ea0ff", "#ff8f5c", "#b794ff", "#3fcfbf"])

    def test_arabic_is_marked_as_arabic(self):
        checked = 0
        for page, (root, _) in self.pages.items():
            for node in root.iter():
                for child in node.children:
                    if isinstance(child, str) and ARABIC.search(child):
                        with self.subTest(page, text=child.strip()[:40]):
                            self.assertEqual(node.nearest("lang"), "ar")
                            self.assertEqual(node.nearest("dir"), "rtl")
                        checked += 1
        self.assertGreater(checked, 60)
        # an Arabic line keeps its English words marked as English, so a screen reader switches voice
        for lang, home in self.homes.items():
            for say in home.find(lambda n: "say" in n.classes() and n.attrs.get("lang") == "ar"):
                english = [n.text() for n in say.iter() if n.attrs.get("lang") == "en"]
                self.assertEqual(" ".join(english), " ".join(re.findall(r"[A-Za-z][A-Za-z ]*[A-Za-z]|[A-Za-z]", say.text())))

    def test_the_arabic_page_reads_right_to_left(self):
        root = self.homes["ar"]
        (html,) = root.find(lambda n: n.tag == "html")
        self.assertEqual((html.attrs.get("lang"), html.attrs.get("dir")), ("ar", "rtl"))
        # Every run of Latin words is marked as English, apart from the speakers' names, which the app writes in
        # Latin letters too. In the page's own words each run also keeps its own direction, and so do the commands;
        # the transcript and the registers are the English page's, and the link to it is a word on its own.
        (pre,) = root.find(lambda n: n.tag == "pre" and "code" in n.classes())
        self.assertEqual((pre.attrs.get("dir"), pre.attrs.get("lang")), ("ltr", "en"))
        shared = lambda n: n.tag in ("ol", "dd") or "other-lang" in n.classes() or "srt" in n.classes()
        checked = 0
        for node in root.iter():
            if node.tag in ("script", "style", "title") or node.inside(lambda n: "who" in n.classes() or "name" in n.classes()):
                continue
            for child in node.children:
                if isinstance(child, str) and LATIN_WORD.search(child):
                    with self.subTest(text=child.strip()[:40]):
                        self.assertEqual(node.nearest("lang"), "en")
                        if not node.inside(shared):
                            self.assertEqual(node.nearest("dir"), "ltr")
                    checked += 1
        self.assertGreater(checked, 50)
        # no physical left or right in the stylesheet, so the layout mirrors through logical properties
        css = re.sub(r"/\*.*?\*/", "", self.css, flags=re.S)
        self.assertNotRegex(css, r"(?:margin|padding|border)-(?:left|right)\s*:|[{;\s](?:left|right)\s*:|text-align\s*:\s*(?:left|right)|float\s*:")

    def test_the_two_pages_carry_the_same_sections_numbers_and_images(self):
        en, ar = self.homes["en"], self.homes["ar"]
        ids = lambda root: [n.attrs["id"] for n in root.iter() if "id" in n.attrs]
        self.assertEqual(ids(en), ids(ar))
        numbers = {lang: Counter(re.findall(r"\d+(?:\.\d+)*", squash(visible_text(root)))) for lang, root in self.homes.items()}
        self.assertEqual(numbers["en"], numbers["ar"])
        self.assertEqual(self.images(HOMES["en"]), self.images(HOMES["ar"]))
        # the same transcript lines and times and register lines, character for character; only the speakers'
        # names are in each page's script
        for test in (lambda n: "say" in n.classes() or "ts" in n.classes(), lambda n: n.tag == "dd" and "register" in n.parent.classes()):
            self.assertEqual([n.text() for n in en.find(test)], [n.text() for n in ar.find(test)])
        shots = lambda root: [n.attrs.get("srcset") or n.attrs["src"] for n in root.find(lambda n: n.tag in ("source", "img") and "captures/" in (n.attrs.get("srcset") or n.attrs.get("src", "")))]
        self.assertEqual([s.removeprefix("../") for s in shots(ar)], shots(en))
        self.assertEqual(len(shots(en)), 2 * 4 * len(build.SHOTS))  # each theme: three sources and the image

    def test_each_page_points_to_the_other(self):
        urls = {"en": build.URL, "ar": build.URL + "ar/"}
        for lang, page in HOMES.items():
            root, other = self.homes[lang], "ar" if lang == "en" else "en"
            with self.subTest(lang):
                links = {n.attrs.get("rel"): n.attrs["href"] for n in root.find(lambda n: n.tag == "link" and n.attrs.get("rel") == "canonical")}
                self.assertEqual(links, {"canonical": urls[lang]})
                alternates = {n.attrs["hreflang"]: n.attrs["href"] for n in root.find(
                    lambda n: n.tag == "link" and n.attrs.get("rel") == "alternate")}
                self.assertEqual(alternates, {"en": urls["en"], "ar": urls["ar"], "x-default": urls["en"]})
                switches = root.find(lambda n: n.tag == "a" and "other-lang" in n.classes())
                self.assertEqual(len(switches), 2)  # in the header's row, and in the phone's menu
                for switch in switches:
                    self.assertEqual((switch.attrs["lang"], switch.attrs["hreflang"]), (other, other))
                    self.assertEqual(self.local(page, switch.attrs["href"]), self.out / HOMES[other])
                    self.assertEqual(switch.text(), {"en": "English", "ar": "العربية"}[other])
                    self.assertEqual(switch.children[0].attrs.get("dir"), {"en": "ltr", "ar": "rtl"}[other])
                    self.assertTrue(switch.inside(lambda n: n.tag == "header"))
        # one 404 page for both, with a way back to each
        root = self.pages["404.html"][0]
        homes = {self.local("404.html", n.attrs["href"]) for n in root.find(lambda n: n.tag == "a" and n.parent.tag == "p")}
        self.assertEqual(homes, {self.out / page for page in HOMES.values()})
        self.assertTrue(root.find(lambda n: n.attrs.get("lang") == "ar" and n.attrs.get("dir") == "rtl"))

    def test_the_copy_files_have_the_same_words(self):
        tables = {}
        for lang in HOMES:
            with open(ROOT / "site" / "copy" / f"{lang}.toml", "rb") as f:
                tables[lang] = {f"{t}.{k}" for t, values in tomllib.load(f).items() for k in values}
        self.assertEqual(tables["en"], tables["ar"])
        # and every one of them is used: by the template, or by build.py for the theme buttons, the pictures and the
        # numbers (a model's name by the name docs/usage.json gives it)
        template = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        used = set(re.findall(r"\{\{(\w+\.\w+)\}\}", template)) | {"theme.to_dark", "theme.to_light"}
        source = (ROOT / "site" / "build.py").read_text(encoding="utf-8")
        numbers = {f"numbers.{key}" for key in re.findall(r'say\(["\'](\w+)["\'][,)]', source)}
        numbers |= {"numbers.months"} | {f"numbers.model_{engine}" for engine in build.ENGINES}
        numbers |= {f"numbers.{system}" for system in ("windows", "linux", "other")}
        numbers |= set(re.findall(r'words\["(get\.\w+)"\]', source))
        numbers |= {f"benchmarks.{key}" for key in re.findall(r'tell\(["\'](\w+)["\'][,)]', source)}
        cards, _ = build.measured(build.bench_sources())
        numbers |= {f"benchmarks.{key}{end}" for key in build.BENCHMARKS for end in ("", "_line", "_table")}
        numbers |= {f"benchmarks.{card['note']}" for card in cards.values()}
        numbers |= {key for key in tables["en"] if key.startswith("countries.")}
        self.assertEqual(used | set(build.SHOTS.values()) | numbers, tables["en"])
        self.assertTrue(all(re.fullmatch(r"countries\.[A-Z]{2}", key) for key in tables["en"] if key.startswith("countries.")))

    def test_copy_follows_the_house_style(self):
        for name in ("index.html", "ar/index.html", "404.html", "styles.css", "site.js", build.COUNTER):
            self.assertNotIn("—", (self.out / name).read_text(encoding="utf-8"), name)
        for name in ("build.py", "DESIGN.md", "copy/en.toml", "copy/ar.toml"):
            self.assertNotIn("—", (ROOT / "site" / name).read_text(encoding="utf-8"), name)
        self.assertNotRegex(self.css, r"text-transform\s*:\s*uppercase")
        for page, (root, _) in self.pages.items():
            self.assertFalse(root.find(lambda n: n.tag in ("b", "strong")), f"{page}: bold in the copy")
            text = squash(visible_text(root))
            self.assertNotIn(" · ", text)
            # an arrow only in a menu path, such as Settings → Models
            for m in re.finditer("→", text):
                self.assertRegex(text[max(0, m.start() - 2):m.end() + 2], r"\w →\s\w", f"{page}: a stray arrow")
            for node in root.find(lambda n: n.tag in ("a", "button")):
                self.assertFalse(squash(node.text()).endswith("→"), squash(node.text()))

    def test_title_description_and_cards(self):
        for lang, root in self.homes.items():
            with self.subTest(lang):
                url = build.URL + ("" if lang == "en" else "ar/")
                meta = {n.attrs.get("name") or n.attrs.get("property"): n.attrs.get("content")
                        for n in root.find(lambda n: n.tag == "meta")}
                (title,) = root.find(lambda n: n.tag == "title")
                self.assertIn("Sedjem", title.text())
                for key in ("description", "og:title", "og:description", "og:image", "og:image:alt", "og:url",
                            "og:type", "og:locale", "og:locale:alternate", "twitter:card", "twitter:title",
                            "twitter:description", "twitter:image", "twitter:image:alt"):
                    self.assertTrue(meta.get(key), key)
                self.assertEqual(meta["og:url"], url)
                self.assertEqual((meta["og:locale"], meta["og:locale:alternate"]),
                                 ("en_US", "ar_EG") if lang == "en" else ("ar_EG", "en_US"))
                self.assertEqual(meta["twitter:card"], "summary_large_image")
                for key in ("og:image", "twitter:image"):
                    image = self.out / meta[key][len(build.URL):]
                    self.assertTrue(meta[key].startswith(build.URL) and image.is_file(), meta[key])
                    self.assertEqual(image_size(image), (int(meta["og:image:width"]), int(meta["og:image:height"])))
                themes = [n.attrs["content"] for n in root.find(lambda n: n.attrs.get("name") == "theme-color")]
                self.assertEqual(themes, ["#f4f5f7", "#0f1216"])
                self.assertTrue(root.find(lambda n: n.tag == "link" and n.attrs.get("rel") == "icon"
                                          and n.attrs.get("type") == "image/svg+xml"))
                if lang == "ar":
                    self.assertTrue(ARABIC.search(title.text()) and ARABIC.search(meta["description"]))

    def test_each_page_shares_its_own_picture(self):
        """A link to a page shows that page's own picture: og: and twitter: tags with the same absolute https
        address, a PNG in the build of exactly the size they give, 1200 x 630, and its description in the page's
        language."""
        shared = set()
        for lang, root in self.homes.items():
            with self.subTest(lang):
                meta = {n.attrs.get("name") or n.attrs.get("property"): n.attrs.get("content")
                        for n in root.find(lambda n: n.tag == "meta")}
                url = f"{build.URL}images/og-{lang}.png"
                self.assertTrue(url.startswith("https://"))
                self.assertEqual((meta["og:image"], meta["twitter:image"]), (url, url))
                self.assertEqual(image_size(self.out / url[len(build.URL):]), (1200, 630))
                self.assertEqual((meta["og:image:width"], meta["og:image:height"], meta["og:image:type"]),
                                 ("1200", "630", "image/png"))
                self.assertEqual(meta["twitter:card"], "summary_large_image")
                self.assertEqual((meta["twitter:title"], meta["twitter:description"]), (meta["og:title"], meta["og:description"]))
                alt = meta["og:image:alt"]
                self.assertEqual(meta["twitter:image:alt"], alt)
                self.assertGreaterEqual(len(alt.split()), 10)
                arabic, latin = len(ARABIC.findall(alt)), len(re.findall("[A-Za-z]", alt))
                if lang == "ar":
                    self.assertGreater(arabic, 2 * latin, alt)
                else:
                    self.assertEqual(arabic, 0, alt)
                shared.add(url)
        self.assertEqual(len(shared), len(HOMES))

    def test_the_share_pictures_come_from_their_generator(self):
        """The share pictures are made by docs/images/make/og.py at 1200 x 630, kept inside the middle 1100 x 560
        that some apps crop to, and saved in full colour, losslessly packed, small enough for the apps that fetch
        them."""
        generator = (ROOT / "docs" / "images" / "make" / "og.py").read_text(encoding="utf-8")
        for line in ("W, H = 1200, 630", "SAFE = (50, 35, 1150, 595)", 'OUT / f"og-{lang}.png"', "optimize=True"):
            self.assertIn(line, generator)
        self.assertNotIn("quantize", generator)
        self.assertEqual(sorted(p.name for p in (ROOT / "site" / "images").iterdir()), ["og-ar.png", "og-en.png"])
        for lang in HOMES:
            with self.subTest(lang):
                self.assertEqual(build.FILES[f"images/og-{lang}.png"], f"site/images/og-{lang}.png")
                data = (ROOT / "site" / "images" / f"og-{lang}.png").read_bytes()
                self.assertEqual((data[24], data[25]), (8, 2))  # 8 bits, RGB: every colour, not a palette
                self.assertLess(len(data), 300_000)

    def test_the_transcript_comes_from_content_py(self):
        m = self.meeting
        raw, timed = m["RAW"][:6], m["lines"]()[:6]  # RAW[0] to RAW[5], the meeting's opening
        for lang, home in self.homes.items():
            (log,) = home.find(lambda n: n.tag == "ol" and "data-reveal" in n.attrs)
            rows = log.find(lambda n: "row" in n.classes())
            self.assertEqual(len(rows), 6)  # every line is in the page, whatever the script does
            for row, (spk, _, text), x in zip(rows, raw, timed):
                with self.subTest(lang, text=text[:30]):
                    (ts,) = row.find(lambda n: "ts" in n.classes())
                    (who,) = row.find(lambda n: "who" in n.classes())
                    (say,) = row.find(lambda n: "say" in n.classes())
                    self.assertEqual(ts.text(), clock(x["start"]))
                    self.assertEqual(who.text(), (build.NAMES_AR if lang == "ar" else m["NAMES"])[spk])
                    self.assertEqual(say.text(), text)
                    self.assertIn(f"var(--s{spk})", row.attrs["style"])
                    expected = {"lang": "en", "dir": "auto"} if text[0].isascii() else {"lang": "ar", "dir": "rtl"}
                    self.assertEqual({k: say.attrs.get(k) for k in expected}, expected)
            # its first three lines as the app exports them to SRT, beside it in the same frame
            (srt,) = home.find(lambda n: n.tag == "pre" and "srt" in n.classes())
            self.assertTrue(srt.inside(lambda n: "frame" in n.classes()))
            names = build.NAMES_AR if lang == "ar" else m["NAMES"]
            cues = [[str(i + 1), f"{build.stamp(x['start'])} --> {build.stamp(x['end'])}", f"{names[spk]}: {text}"]
                    for i, ((spk, _, text), x) in enumerate(zip(raw[:3], timed[:3]))]
            self.assertEqual(srt.text().splitlines()[1:], [line for cue in cues for line in cue + [""]][:-1])
        self.assertEqual(build.stamp(5.3), "00:00:05,300")
        caption = squash(self.homes["en"].find(lambda n: "hero-figure" in n.classes())[0].text())
        self.assertIn("A made-up sprint planning as Sedjem writes it", caption)

    def test_the_arrival_is_one_short_motion_with_one_switch(self):
        """The lines arrive one after another once, in about two seconds, timed by the stylesheet's tokens, which
        site.js reads; the reader's reduced motion turns the one switch, --motion, to 0. Until the script marks the
        page, nothing hides a line, so without JavaScript every line is simply there."""
        tokens = {}
        for name, value in re.findall(r"--(motion(?:-\w+)?):\s*([\d.]+);", self.css):
            tokens.setdefault(name, float(value))  # the first, :root's; the reduced-motion rule comes after
        self.assertEqual(tokens["motion"], 1)
        total = tokens["motion-start"] + 5 * tokens["motion-step"] + tokens["motion-row"]
        self.assertTrue(1500 <= total <= 2500, total)
        self.assertRegex(self.css, r"@media \(prefers-reduced-motion: reduce\) \{\s*:root \{ --motion: 0; \}\s*\}")
        script = (self.out / "site.js").read_text(encoding="utf-8")
        for name in ("--motion", "--motion-start", "--motion-step", "--motion-row"):
            self.assertIn(f'token("{name}")', script)
        css = re.sub(r"/\*.*?\*/", "", self.css, flags=re.S)
        hiding = [rule for rule in re.findall(r"([^{}]+)\{([^}]*)\}", css) if re.search(r"(?<![-\w])opacity:\s*0(?![.\d%])|visibility:\s*hidden", rule[1])]
        self.assertEqual([selector.strip() for selector, _ in hiding], [".will-reveal .log .row"])
        self.assertIn('root.classList.add("will-reveal")', script)

    def test_the_registers_and_their_marks(self):
        for lang, home in self.homes.items():
            lines = home.find(lambda n: n.tag == "dd" and n.parent.tag == "div" and "register" in n.parent.classes())
            self.assertEqual(len(lines), 2)
            for dd, (text, marked) in zip(lines, REGISTERS):
                with self.subTest(lang, text=text[:30]):
                    self.assertEqual(dd.text(), text)
                    self.assertEqual([n.text() for n in dd.find(lambda n: n.tag == "mark")], marked)
                    self.assertEqual((dd.attrs.get("lang"), dd.attrs.get("dir")), ("ar", "rtl"))

    def test_the_speakers_shares_come_from_content_py(self):
        m = self.meeting
        talk = {}
        for x in m["lines"]():
            talk[x["speaker"]] = talk.get(x["speaker"], 0) + x["end"] - x["start"]
        total = sum(talk.values())
        expected = [(m["NAMES"][s], f"{math.floor(talk[s] / total * 100 + 0.5)}%") for s in sorted(talk, key=int)]
        self.assertEqual(expected, [("Mona", "38%"), ("Karim", "22%"), ("Omar", "22%"), ("Nour", "17%")])
        # the Speakers panel is the app's own, captured in each theme; its alt text names the shares, worked out at
        # build time, in the order and with the rounding the panel shows
        for lang, home in self.homes.items():
            images = home.find(lambda n: n.tag == "img" and "/speakers-" in n.attrs["src"])
            self.assertEqual([img.attrs["src"].rsplit("/", 1)[-1] for img in images], ["speakers-wide-light.png", "speakers-wide-dark.png"])
            for img in images:
                self.assertEqual(re.findall(r"([A-Z][a-z]+) (\d+%)", img.attrs["alt"]), expected, lang)

    def test_the_ear_is_the_brand_icons(self):
        """The hero's ear is drawn from the brand icon's own paths, once on each page and nowhere else on it, and
        never filled or recoloured by the page: the stylesheet strokes it with the brand's teal. The 404 page sets the
        logo tile itself."""
        icon = (ROOT / "docs" / "brand" / "sedjem-icon-colour.svg").read_text(encoding="utf-8")
        paths = re.findall(r'<path d="([^"]+)"', icon)
        for lang, home in self.homes.items():
            with self.subTest(lang):
                (ear,) = home.find(lambda n: n.tag == "svg" and "ear" in n.classes())
                self.assertEqual([n.attrs["d"] for n in ear.find(lambda n: n.tag == "path")], paths)
                self.assertEqual(ear.attrs.get("aria-hidden"), "true")
                self.assertTrue(ear.inside(lambda n: "hero" in n.classes()))
        rules = re.findall(r"\.ear\s*\{([^}]*)\}", re.sub(r"/\*.*?\*/", "", self.css, flags=re.S))
        drawn = [rule for rule in rules if "stroke" in rule]
        self.assertTrue(drawn)
        for rule in drawn:
            self.assertRegex(rule, r"fill:\s*none")
            self.assertRegex(rule, r"stroke:\s*var\(--ear\)")
        page = (self.out / "404.html").read_text(encoding="utf-8")
        for path in paths:
            self.assertIn(f'<path d="{path}"', page)

    def test_nothing_blinks_or_loops(self):
        """One motion only, and it ends: no endless or blinking animation anywhere, the cursor included."""
        scripts = [(name, (self.out / name).read_text(encoding="utf-8")) for name in ("site.js", build.COUNTER)]
        for where, text in [*self.styles, *scripts]:
            with self.subTest(where):
                self.assertNotRegex(text, r"infinite|Infinity|@keyframes|setInterval|blink")

    def test_the_tokens_are_the_ones_design_md_declares(self):
        """Every colour, font, font size and radius the stylesheets use is in DESIGN.md's frontmatter, which is where
        design tools read a project's tokens."""
        design = (ROOT / "site" / "DESIGN.md").read_text(encoding="utf-8")
        front = design.split("---", 2)[1]
        colors = {c.lower() for c in re.findall(r'"(#[0-9a-fA-F]{6})"', front)}
        self.assertEqual(colors, set(PALETTE))  # the palette, no more and no less
        fonts = {f.strip() for stack in re.findall(r'fontFamily: "([^"]+)"', front) for f in stack.split(",")}
        sizes = {float(v) for v in re.findall(r'(?:fontSize|[\w-]+): "(\d+(?:\.\d+)?)px"', front.split("rounded:")[0])}
        radii = {float(v) for v in re.findall(r'"(\d+)px"', front.split("rounded:")[1].split("spacing:")[0])}
        px = lambda value: float(value[:-3]) * 16 if value.endswith("rem") else float(value[:-2])
        for where, css in self.styles:
            css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
            with self.subTest(where):
                for colour in re.findall(r"#[0-9a-fA-F]{6}\b", css):
                    self.assertIn(colour.lower(), colors)
                for stack in re.findall(r"(?:font-family|--font-\w+|--mono)\s*:\s*([^;}]+)", css):
                    if "var(" not in stack:
                        self.assertIn(stack.split(",")[0].strip().strip('"'), fonts, stack)
                for value in re.findall(r"(?:font-size|--fs-\w+)\s*:\s*(\d+(?:\.\d+)?(?:px|rem))\b", css):
                    self.assertIn(px(value), sizes, value)
                for value in re.findall(r"font:\s*\d{3}\s+(\d+(?:\.\d+)?px)", css):
                    self.assertIn(px(value), sizes, value)
                for value in re.findall(r"border-radius\s*:\s*(\d+px)", css):
                    self.assertIn(px(value), radii, value)

    def test_the_theme_button(self):
        """The header's theme button is named for what a press does, switching to the dark theme or to the light one,
        in the page's language, with the same title and, in the phone's menu, the same name on show; its icons are a
        sun and a moon, hidden from screen readers, and the stylesheet shows the one a press switches to, from the
        tokens that show each theme's views. site.js and the 404 page keep the reader's pick under the same key."""
        names = {}
        for lang, home in self.homes.items():
            buttons = home.find(lambda n: n.tag == "button" and "data-theme-switch" in n.attrs)
            self.assertEqual([b.classes() for b in buttons], [["theme", "theme-wide"], ["theme", "theme-menu"]])
            for button in buttons:
                with self.subTest(lang, where=button.classes()[1]):
                    self.assertTrue(button.inside(lambda n: n.tag == "header"))
                    self.assertEqual(button.attrs.get("type"), "button")
                    names[lang] = [button.attrs["data-to-dark"], button.attrs["data-to-light"]]
                    self.assertEqual(len(set(names[lang])), 2)
                    # a light page as far as the build knows; site.js names it for the theme on show
                    self.assertEqual((button.attrs["aria-label"], button.attrs["title"]), (names[lang][0], names[lang][0]))
                    icons = button.find(lambda n: n.tag == "svg")
                    self.assertEqual([icon.classes() for icon in icons], [["icon-light"], ["icon-dark"]])
                    self.assertEqual({icon.attrs.get("aria-hidden") for icon in icons}, {"true"})
                    shown = button.find(lambda n: "theme-name" in n.classes())
                    self.assertEqual([n.text() for n in shown], [names[lang][0]] if "theme-menu" in button.classes() else [])
            self.assertTrue(buttons[1].inside(lambda n: n.tag == "details"))
        self.assertEqual(names["en"], ["Switch to the dark theme", "Switch to the light theme"])
        self.assertTrue(all(ARABIC.search(name) for name in names["ar"]))
        css = re.sub(r"/\*.*?\*/", "", self.css, flags=re.S)
        self.assertRegex(css, r"\n\.theme \.icon-light \{ display: var\(--view-dark\); \}\n\.theme \.icon-dark \{ display: var\(--view-light\); \}")
        self.assertNotIn("icon-system", css)
        script = (self.out / "site.js").read_text(encoding="utf-8")
        self.assertIn('button.getAttribute("data-to-" + other())', script)
        key = re.search(r'var KEY = "([^"]+)";', script).group(1)
        self.assertIn(f'localStorage.getItem("{key}")', (self.out / "404.html").read_text(encoding="utf-8"))

    def test_the_dark_theme_is_one_set_of_tokens(self):
        """Each stylesheet writes its dark tokens twice, for the system's setting and for the reader's pick, as a
        media query and an attribute can't share a block; the two must say the same."""
        for where, css in self.styles:
            css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
            blocks = [{name: value.strip() for name, value in re.findall(r"([\w-]+)\s*:\s*([^;]+);", match.group(1))}
                      for match in (re.search(r':root:not\(\[data-theme="light"\]\)\s*\{([^}]*)\}', css),
                                    re.search(r':root\[data-theme="dark"\]\s*\{([^}]*)\}', css))]
            with self.subTest(where):
                self.assertEqual(blocks[0], blocks[1])
                self.assertEqual(blocks[0]["color-scheme"], "dark")
                self.assertIn("--bg", blocks[0])

    def test_the_download_and_the_menu_work_without_javascript(self):
        """Without JavaScript the hero's button is the Windows download and the link beside it reads Linux and the
        source (site.js swaps them on a Linux computer), and the phone's menu is a disclosure that opens by itself,
        named, holding the docs, GitHub and the other language."""
        for lang, home in self.homes.items():
            with self.subTest(lang):
                (actions,) = home.find(lambda n: "actions" in n.classes())
                for link in actions.find(lambda n: n.tag == "a"):
                    labels = {n.attrs["data-os"]: n for n in link.find(lambda n: "data-os" in n.attrs)}
                    self.assertEqual(set(labels), {"windows", "linux"})
                    self.assertNotIn("hidden", labels["windows"].attrs)
                    self.assertIn("hidden", labels["linux"].attrs)
                primary, others = actions.find(lambda n: n.tag == "a")
                self.assertIn("Windows", primary.find(lambda n: n.attrs.get("data-os") == "windows")[0].text())
                self.assertIn("Linux", others.find(lambda n: n.attrs.get("data-os") == "windows")[0].text())
                self.assertEqual(others.attrs["href"], "#download")
                (menu,) = home.find(lambda n: n.tag == "details" and "menu" in n.classes())
                (summary,) = menu.find(lambda n: n.tag == "summary")
                self.assertTrue(squash(summary.text()))
                hrefs = [a.attrs["href"] for a in menu.find(lambda n: n.tag == "a")]
                self.assertEqual(len(hrefs), 3)
                self.assertTrue(any("docs" in h for h in hrefs) and any(h.endswith("/sedjem") for h in hrefs))
        script = (self.out / "site.js").read_text(encoding="utf-8")
        self.assertIn('label.getAttribute("data-os") !== "linux"', script)

    def test_the_pictures_of_the_app_come_from_its_generator(self):
        """Every picture of the app is a capture made by docs/images/make/screenshots.py's "site" shot, in a
        desktop and a phone form, light and dark, at the densities the page sets them at, and made into WebP and
        PNG in site/captures/ by shrink.py."""
        make = ROOT / "docs" / "images" / "make"
        generator = (make / "screenshots.py").read_text(encoding="utf-8")
        self.assertIn('want("site")', generator)
        self.assertIn('SITE = OUT / "site"', generator)
        self.assertIn("scale=2 if wide else 3", generator)
        self.assertEqual(build.DENSITY, {"wide": 2, "narrow": 3})
        shrink = (make / "shrink.py").read_text(encoding="utf-8")
        self.assertIn('"site" / "captures"', shrink)
        self.assertIn("lossless=True", shrink)
        names = sorted(p.name for p in build.CAPTURES.iterdir())
        self.assertEqual(names, sorted(f"{shot}-{form}-{theme}.{ext}" for shot in build.SHOTS for form in ("wide", "narrow")
                                       for theme in ("light", "dark") for ext in ("png", "webp")))
        for name in names:
            self.assertIn(f'"{name.split("-")[0]}"', generator)

    def test_privacy_is_linked_from_the_footer_and_the_privacy_section(self):
        """The footer of each page links the privacy page, and the section that says what the app sends ends with the
        same link."""
        privacy = "https://github.com/MohammedEl-sayedAhmed/sedjem/blob/main/docs/privacy.md"
        for lang, home in self.homes.items():
            with self.subTest(lang):
                (footer,) = home.find(lambda n: n.tag == "footer")
                self.assertEqual(len(footer.find(lambda n: n.tag == "a" and n.attrs.get("href") == privacy)), 1)
                (section,) = home.find(lambda n: n.tag == "section" and n.attrs.get("aria-labelledby") == "local")
                (claim,) = section.find(lambda n: "claim" in n.classes())
                last = [n for n in claim.children if not isinstance(n, str)][-1]
                self.assertEqual([a.attrs["href"] for a in last.find(lambda n: n.tag == "a")], [privacy])
            self.assertNotRegex((self.out / HOMES[lang]).read_text(encoding="utf-8").lower(), r"code.?signing")

    def test_the_version_is_the_latest_releases_or_the_apps(self):
        """The downloads' small print names the latest release's version, or the app's without the release's details."""
        source = (ROOT / "app" / "__init__.py").read_text(encoding="utf-8")
        self.assertEqual(build.version(), re.search(r'__version__ = "([^"]+)"', source).group(1))
        with tempfile.TemporaryDirectory() as tmp:
            plain = build.build(Path(tmp) / "site")
            for version, out in ((fixture.RELEASE, self.out), (build.version(), plain)):
                for lang, page in HOMES.items():
                    (fine,) = parse((out / page).read_text(encoding="utf-8"))[0].find(lambda n: "fine" in n.classes())
                    self.assertTrue(squash(fine.text()).endswith(VERSION_IN[lang].format(version)), fine.text())

    def test_the_first_view_stays_small(self):
        fonts = sorted(set(re.findall(r'url\("(fonts/[^"]+\.woff2)"\)', self.css)))
        for page in HOMES.values():
            files = [page, "styles.css", "site.js", build.COUNTER, "brand/sedjem-logo-colour.svg", "brand/sedjem-logo-colour-dark.svg", *fonts]
            size = sum((self.out / f).stat().st_size for f in files)
            self.assertLessEqual(size, FIRST_VIEW_BUDGET, f"{page}: {size} bytes")
        # only the faces DESIGN.md names, and only the weights the stylesheet uses
        for font in fonts:
            self.assertRegex(font, r"fonts/ibm-plex-(sans-latin-[4-7]00|sans-arabic-arabic-[4-7]00|mono-latin-[45]00)-normal\.woff2")
        self.assertEqual(sorted(p.relative_to(self.out).as_posix() for p in (self.out / "fonts").iterdir()),
                         sorted(fonts + ["fonts/OFL-IBM-Plex.txt"]))

    def test_the_build_publishes_only_what_the_pages_use(self):
        published = sorted(p.relative_to(self.out).as_posix() for p in self.out.rglob("*") if p.is_file())
        fonts = sorted(set(re.findall(r'url\("(fonts/[^"]+\.woff2)"\)', self.css)))
        captures = [f"captures/{p.name}" for p in build.CAPTURES.iterdir()]
        self.assertEqual(published, sorted([*build.PAGES, *build.COPIED, build.COUNTER, *build.FILES, *fonts, *captures]))
        # the logo, the icon and the screenshots are the repository's files, unchanged
        for target, source in build.FILES.items():
            self.assertEqual((self.out / target).read_bytes(), (ROOT / source).read_bytes(), target)

    def test_the_build_refuses_folders_that_arent_its_own(self):
        for folder in (ROOT, ROOT / "site", ROOT.parent):
            with self.subTest(folder=folder.name), self.assertRaises(SystemExit):
                build.prepare(folder)
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "notes.txt").write_text("keep me", encoding="utf-8")
            with self.assertRaises(SystemExit):
                build.prepare(Path(tmp))
            self.assertTrue((Path(tmp) / "notes.txt").is_file())

    def test_the_workflow_rebuilds_when_a_source_changes(self):
        workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
        paths = re.search(r"\n    paths:\n((?:      - .+\n)+)", workflow).group(1)
        patterns = [p.strip()[2:].strip('"') for p in paths.splitlines()]
        sources = ["site/index.html", "site/styles.css", "site/counts.js", "site/build.py", "site/copy/ar.toml", "docs/images/make/content.py",
                   "app/__init__.py", *build.FILES.values(), *BENCH_FILES,
                   *(f"{build.FONTS}/{Path(f).name}" for f in re.findall(r'url\("(fonts/[^"]+)"\)', self.css))]
        for source in sources:
            with self.subTest(source):
                self.assertTrue(any(fnmatch.fnmatch(source, p) for p in patterns))
        for line in ("pages: write", "id-token: write", "group: pages", "workflow_dispatch:"):
            self.assertIn(line, workflow)
        # and the totals of the counts, which the section of numbers reads
        self.assertTrue(any(fnmatch.fnmatch("docs/usage.json", p) for p in patterns))
        self.assertEqual(build.USAGE, ROOT / "docs" / "usage.json")

    def test_the_numbers_refresh_after_the_stats_without_a_checkout_of_them(self):
        """Pages rebuilds after each successful run of the Repository stats workflow on main, as well as on a push and
        by hand, so the numbers are the day's. It downloads the stats files through the API into a folder and builds
        from it; it never checks out or fetches the stats branch, which GitHub would count as a clone, and a file it
        can't download is left out with a warning rather than stopping the publication."""
        workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
        names = json.loads("[" + re.search(r'\n  workflow_run:\n    workflows: \[(.+)\]\n    types: \[completed\]\n', workflow).group(1) + "]")
        self.assertEqual(names, ["Repository stats", "Desktop build"])
        for name, path in zip(names, ("stats.yml", "desktop.yml")):
            self.assertIn(f"\nname: {name}\n", (ROOT / ".github" / "workflows" / path).read_text(encoding="utf-8"))
        self.assertIn("\n  push:\n", workflow)
        self.assertIn("github.event.workflow_run.conclusion == 'success' &&\n"
                      "       github.event.workflow_run.head_repository.full_name == github.repository &&\n"
                      "       (github.event.workflow_run.head_branch == 'main' || github.event.workflow_run.event == 'push'))", workflow)
        (download,) = re.findall(r"\n      - name: Download the stats.*?\n(?=\n      - )", workflow, re.S)
        for name in build.STATS:
            self.assertIn(name, download.split("for file in ")[1].split(";")[0])
        self.assertIn('gh api -H "Accept: application/vnd.github.raw" "repos/$GITHUB_REPOSITORY/contents/$file?ref=stats"', download)
        self.assertIn("::warning::", download)
        self.assertIn('python site/build.py _site --stats "$RUNNER_TEMP/stats" --release "$RUNNER_TEMP/release"', workflow)
        self.assertNotRegex(workflow, r"ref:\s*stats|git (?:fetch|clone|checkout|pull)|fetch-depth: 0")
        self.assertEqual(workflow.count("actions/checkout@"), 1)  # main's, which the build reads

    def test_a_release_reaches_the_site(self):
        """A release is on the site as soon as its files exist: the Desktop build's success after a pushed tag starts
        a build (that workflow publishes with its own token, which starts no workflow), and a release published by a
        person starts a run that asks for one on main, the one branch the github-pages environment deploys from;
        pre-releases leave the downloads alone. The build reads the latest release through the API, and its
        SHA256SUMS.txt only when GitHub has no digest of a file, as each download adds to its count; a release that
        can't be read leaves a warning and the general wording."""
        workflow = (ROOT / ".github" / "workflows" / "pages.yml").read_text(encoding="utf-8")
        desktop = (ROOT / ".github" / "workflows" / "desktop.yml").read_text(encoding="utf-8")
        self.assertIn("\n  release:\n    types: [published]\n", workflow)
        self.assertIn('tags: ["v*"]', desktop)
        self.assertIn('gh release create "$tag"', desktop)
        (job,) = re.findall(r"\n  on-main:\n.*?(?=\n\n  \w)", workflow, re.S)
        self.assertIn("if: github.event_name == 'release' && !github.event.release.prerelease", job)
        self.assertIn("actions: write", job)
        self.assertIn('gh workflow run pages.yml --ref main --repo "$GITHUB_REPOSITORY"', job)
        self.assertNotIn("actions: write", workflow.replace(job, ""))
        self.assertNotIn("github.event_name == 'release'", workflow.split("\n  build:\n")[1].split("\n    steps:")[0])
        (step,) = re.findall(r"\n      - name: Download the latest release.*?\n(?=\n      - )", workflow, re.S)
        self.assertIn('gh api "repos/$GITHUB_REPOSITORY/releases/latest" > "$folder/release.json"', step)
        self.assertIn("""jq -e '[.assets[] | select(.name != "SHA256SUMS.txt") | .digest] | all'""", step)
        self.assertIn('"repos/$GITHUB_REPOSITORY/releases/assets/$id"', step)
        self.assertEqual(step.count("::warning::"), 2)
        self.assertNotRegex(step, r"gh release download|curl|wget")

    def test_readme_links_the_site_near_the_top(self):
        top = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()[:25]
        self.assertTrue(any(build.URL in line for line in top))

    def test_build_uses_only_the_standard_library(self):
        tree = ast.parse((ROOT / "site" / "build.py").read_text(encoding="utf-8"))
        modules = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        modules |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertLessEqual({m.split(".")[0] for m in modules}, set(sys.stdlib_module_names))
        # and never fetches: the numbers come from files
        self.assertFalse({m for m in modules if m.split(".")[0] in ("http", "socket", "ssl", "subprocess", "ftplib")
                          or m in ("urllib", "urllib.request")}, modules)


MONTHS = "January February March April May June July August September October November December".split()


def long_day(d):
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


class NumbersTest(unittest.TestCase):
    """The section of numbers, built from the made-up stats files and totals of tests/site_support.py: a grid of figure
    cards, each its number beside its title and one line, over a small picture drawn at build time."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.stats = fixture.write_stats(cls.dir / "stats")
        cls.usage = fixture.write_usage(cls.dir / "usage.json")
        cls.full = cls.sections(build.build(cls.dir / "full", stats=cls.stats, usage=cls.usage))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @staticmethod
    def sections(out):
        """Each page's section of numbers, or None when it has none."""
        found = {}
        for lang, page in HOMES.items():
            root, errors = parse((out / page).read_text(encoding="utf-8"))
            assert not errors, errors
            sections = root.find(lambda n: n.tag == "section" and n.attrs.get("aria-labelledby") == "numbers")
            found[lang] = sections[0] if sections else None
        return found

    def build(self, name, **kwargs):
        """A build with other numbers, and what it said in the log."""
        log = io.StringIO()
        with contextlib.redirect_stderr(log), contextlib.redirect_stdout(io.StringIO()):
            out = build.build(self.dir / name, **kwargs)
        return self.sections(out), log.getvalue()

    @staticmethod
    def cards(section):
        """Each card by its key: its width in the grid, its title, its figure (value, as shown) and its line, the points
        of its line ((across, down) as drawn), its bar of parts ((part, value)) and legend ((name, share)), its table's
        rows, the counts' numbers ((value, label)) and its line saying when the first totals come."""
        out = {}
        for card in section.find(lambda n: "card" in n.classes()):
            first = lambda node, test: (node.find(test) or [None])[0]  # noqa: E731
            figure = first(card, lambda n: "kpi-figure" in n.classes())
            text = first(card, lambda n: "kpi-text" in n.classes())
            plot = first(card, lambda n: "plot" in n.classes())
            data = figure and first(figure, lambda n: n.tag == "data")
            out[card.attrs["id"].removeprefix("numbers-")] = {
                "span": next(c for c in card.classes() if c.startswith("span-")),
                "title": squash(first(card, lambda n: n.tag == "h3").text()),
                "figure": (data.attrs["value"], squash(data.text())) if data else None,
                "line": squash(first(text, lambda n: n.tag == "p").text()) if text else None,
                "points": [tuple(float(v) for v in p.split(",")) for p in plot.attrs["data-points"].split()] if plot else [],
                "parts": [(p.classes()[1], int(p.attrs["style"].removeprefix("--v: "))) for p in card.find(
                    lambda n: "part" in n.classes() and n.parent.attrs.get("class") == "split")],
                "legend": [(squash("".join(c if isinstance(c, str) else "" if "share" in c.classes() else c.text() for c in li.children)),
                            squash(first(li, lambda n: "share" in n.classes()).text()))
                           for li in card.find(lambda n: n.tag == "li" and "legend" in n.parent.classes())],
                "rank": [(squash(first(li, lambda n: "rank-name" in n.classes()).text()),
                          squash(first(li, lambda n: "rank-value" in n.classes()).text()))
                         for li in card.find(lambda n: n.tag == "li" and n.parent.tag == "ol")],
                "table": [[squash(c.text()) for c in tr.children if not isinstance(c, str)]
                          for tr in card.find(lambda n: n.tag == "tr" and n.parent.tag == "tbody")],
                "stats": [(first(p, lambda n: n.tag == "data").attrs["value"], squash(first(p, lambda n: n.tag == "span").text()))
                          for p in card.find(lambda n: "stat" in n.classes())],
                "soon": [squash(p.text()) for p in card.find(lambda n: "soon" in n.classes())],
            }
        return out

    @staticmethod
    def expected():
        """The numbers each card must show, worked out here again from tests/site_support.py's files."""
        days = sorted({date.fromisoformat(d) for d, *_ in fixture.DOWNLOADS})
        app = [(date.fromisoformat(d), r, a, n) for d, r, a, n in fixture.DOWNLOADS if not a.endswith(".txt")]
        per_day = [(d, sum(n for dd, _, _, n in app if dd == d)) for d in days]
        first_seen = {}
        for d, r, _, _ in sorted(app):
            first_seen.setdefault(r, d)
        newest = [(r, a, n) for d, r, a, n in app if d == days[-1]]
        releases = {}
        for r, _, n in newest:
            releases[r] = releases.get(r, 0) + n
        week_ago = [n for d, n in per_day if d <= days[-1] - timedelta(7)]
        return {"per_day": per_day, "first_seen": first_seen, "releases": releases,
                "week": per_day[-1][1] - (week_ago[-1] if week_ago else per_day[0][1]),
                "windows": sum(n for _, a, n in newest if a.endswith(".exe")),
                "linux": sum(n for _, a, n in newest if "linux" in a), "views": sorted(fixture.VIEWS)[-30:],
                "stars": [(fixture.DAY - timedelta(1), fixture.STARS - 4), (fixture.DAY, fixture.STARS)],
                "app": fixture.USAGE["months"]["2026-11"], "website": fixture.USAGE["website"]["2026-11"]}

    def test_every_number_on_every_card_is_its_sources(self):
        """Each card's figure, its line, its bar of parts, its legend and its table are the files' numbers, worked out
        again here: the downloads in all each day (the app's files of every release, previews included, not the
        checksums) and those of the last seven days, the newest release that isn't a preview against the older ones
        and the previews, Windows' and Linux's shares, the page views of the last 30 days recorded and their daily
        average, the stars and how many came since the first day, and the newest month of the counts with its
        countries, the five with the most installs by name and the rest as other."""
        e = self.expected()
        fmt = lambda n: f"{n:,}"  # noqa: E731
        share = lambda n, whole: f"{round(n / whole * 100)}%"  # noqa: E731
        self.assertEqual(e["per_day"][-1][1], fixture.TOTAL)
        self.assertEqual(e["releases"], {"v0.10.0": fixture.LATEST, "v0.9.2": 463, "v0.10.1-preview.20261201": 7})
        self.assertEqual(sum(n for _, n, _ in e["views"]), fixture.VIEWS_30)
        cards = self.cards(self.full["en"])
        self.assertEqual(list(cards), ["downloads", "releases", "platforms", "views", "stars", "counts"])
        c = cards["downloads"]
        self.assertEqual((c["figure"], c["title"], c["line"]),
                         ((str(fixture.TOTAL), fmt(fixture.TOTAL)), "Downloads, all releases", f"+{fmt(e['week'])} since {e['per_day'][0][0].day} {e['per_day'][0][0]:%B}"))
        self.assertEqual(e["week"], fixture.TOTAL - 380, "the history is shorter than a week, so it counts from its first day")
        out = {d: r.removeprefix("v") for r, d in e["first_seen"].items() if d > e["per_day"][0][0] and not "-" in r}
        self.assertEqual(out, {fixture.DAY: "0.10.0"})
        self.assertEqual(c["table"], [[long_day(d) + (f" ({out[d]} out)" if d in out else ""), fmt(n)] for d, n in e["per_day"]])
        c = cards["releases"]
        older = sum(n for r, n in e["releases"].items() if r != "v0.10.0" and "-" not in r)
        whole = fixture.LATEST + older + 7
        self.assertEqual((c["figure"], c["title"], c["line"]),
                         ((str(fixture.LATEST), fmt(fixture.LATEST)), "Downloads of 0.10.0", "the latest release, out on 2 December"))
        self.assertEqual(c["parts"], [("part-latest", fixture.LATEST), ("part-older", older), ("part-previews", 7)])
        self.assertEqual(c["legend"], [("0.10.0", share(fixture.LATEST, whole)), ("Older releases", share(older, whole)),
                                       ("Previews", share(7, whole))])
        self.assertEqual(c["table"], [["0.10.0", fmt(fixture.LATEST)], ["0.9.2", "463"], ["Previews", "7"]])
        c = cards["platforms"]
        whole = e["windows"] + e["linux"]
        self.assertEqual((e["windows"], e["linux"], whole), (1589, 157, fixture.TOTAL))
        self.assertEqual((c["figure"], c["title"], c["line"]),
                         ((share(e["windows"], whole)[:-1], share(e["windows"], whole)), "of downloads are for Windows",
                          f"Linux: {share(e['linux'], whole)}"))
        self.assertEqual(c["parts"], [("part-windows", e["windows"]), ("part-linux", e["linux"])])
        self.assertEqual(c["legend"], [("Windows", share(e["windows"], whole)), ("Linux", share(e["linux"], whole))])
        self.assertEqual(c["table"], [["Windows", fmt(e["windows"]), share(e["windows"], whole)],
                                      ["Linux", fmt(e["linux"]), share(e["linux"], whole)]])
        c = cards["views"]
        self.assertEqual((c["figure"], c["title"], c["line"]),
                         ((str(fixture.VIEWS_30), fmt(fixture.VIEWS_30)), "Repository page views, last 30 days",
                          f"about {round(fixture.VIEWS_30 / 30, 1)} a day"))
        self.assertEqual(c["table"], [[long_day(d), fmt(n)] for d, n, _ in e["views"]])
        c = cards["stars"]
        self.assertEqual((c["figure"], c["title"], c["line"]),
                         ((str(fixture.STARS), fmt(fixture.STARS)), "Stars on GitHub", "+4 since 1 December"))
        self.assertEqual(c["table"], [[long_day(d), fmt(n)] for d, n in e["stars"]])
        c, u, w = cards["counts"], e["app"], e["website"]
        self.assertEqual((c["title"], c["span"]), ("In the app, November 2026", "span-3"))
        self.assertEqual(c["stats"], [(str(u["installs"]["active"]), "installs ran Sedjem"),
                                      (str(u["installs"]["countries"]["count"]), "countries"),
                                      (str(u["runs"]["transcriptions"]), "transcriptions"), (str(u["runs"]["hours"]), "hours of audio"),
                                      (str(w["page_views"]), "page views"), (str(w["countries"]["count"]), "countries"),
                                      (str(w["downloads"]["clicks"]), "download clicks")])
        where = fixture.WHERE
        top = sorted((k for k in where if k != "other"), key=lambda k: -where[k])[:5]
        names = {"EG": "Egypt", "SA": "Saudi Arabia", "AE": "Emirates", "US": "United States", "DE": "Germany"}
        rows = [(names[k], fmt(where[k])) for k in top] + [("Other countries", fmt(sum(where.values()) - sum(where[k] for k in top)))]
        self.assertEqual((c["rank"], [tuple(r) for r in c["table"]]), (rows, rows))
        self.assertEqual(rows[-1], ("Other countries", "133"), "Jordan (30) is added to the other countries")
        (model,) = self.full["en"].find(lambda n: "model" in n.classes())
        self.assertEqual(squash(model.text()), "Most used model: whisper-medium code-switching")

    def test_the_lines_are_drawn_to_the_numbers(self):
        """Each small line is its numbers: a point a row of the table, at its day across the card and at its value down
        a 56 px picture, from the lowest value (or 0 for the page views) at the bottom to the highest at the top; a
        smooth curve through every point that never overshoots one; the last stretch dashed to the dot on the last
        day; and a ring on each release day before it."""
        e = self.expected()
        section = self.full["en"]
        down = lambda v, lo, hi: round(6 + (1 - (v - lo) / ((hi - lo) or 1)) * 46, 1)  # noqa: E731
        span = lambda series: (series[-1][0] - series[0][0]).days  # noqa: E731
        cards = self.cards(section)
        for key, series, floor in (("downloads", e["per_day"], None), ("views", [(d, n) for d, n, _ in e["views"]], 0),
                                   ("stars", e["stars"], None)):
            with self.subTest(key):
                lo = min(n for _, n in series) if floor is None else floor
                hi = max(n for _, n in series)
                self.assertEqual(cards[key]["points"], [(round((d - series[0][0]).days / span(series) * 100, 2), down(n, lo, hi))
                                                        for d, n in series])
                self.assertEqual(len(cards[key]["points"]), len(cards[key]["table"]))
                (card,) = section.find(lambda n: n.attrs.get("id") == f"numbers-{key}")
                paths = card.find(lambda n: n.tag == "path" and "line" in n.classes())
                want = [(round(x * 10, 1), y) for x, y in cards[key]["points"]]
                pieces = list(self.pieces(paths))
                self.assertEqual(sorted({p[3] for p in pieces} | {p[2] for p in pieces}), sorted(set(want)),
                                 "the curve passes through every point")
                for c1, c2, end, start in pieces:
                    low, high = sorted((start[1], end[1]))
                    self.assertTrue(low - 0.05 <= c1[1] <= high + 0.05 and low - 0.05 <= c2[1] <= high + 0.05,
                                    "a piece's handles stay between its ends, so the curve never overshoots")
                tails = [p for p in paths if "line-tail" in p.classes()]
                if len(want) > 2:  # the last stretch, dashed: the day still being counted
                    self.assertEqual([t.attrs["d"].split(" C")[0] for t in tails], [f"M{want[-2][0]:.1f},{want[-2][1]:.1f}"])
                else:
                    self.assertEqual(tails, [])
                (dot,) = card.find(lambda n: n.tag == "circle" and "dot" in n.classes())
                self.assertEqual((dot.attrs["cx"], float(dot.attrs["cy"])), ("100%", want[-1][1]))
                (stop,) = card.find(lambda n: n.tag == "linearGradient".lower() or n.tag == "lineargradient")
                self.assertEqual(stop.attrs["id"], f"wash-{key}")
        # a release first counted before the last day is a ring on the line; on the last day, the dot stands for it
        downloads = list(fixture.DOWNLOADS) + [(d, "v0.9.5", "Sedjem-0.9.5-setup.exe", n) for d, n in
                                               (("2026-11-30", 3), ("2026-12-01", 4), ("2026-12-02", 5))]
        sections, _ = self.build("ring", stats=fixture.write_stats(self.dir / "ring-stats", downloads=downloads), usage=self.usage)
        (card,) = sections["en"].find(lambda n: n.attrs.get("id") == "numbers-downloads")
        rings = [r.attrs["cx"] for r in card.find(lambda n: n.tag == "circle" and "mark" in n.classes())]
        self.assertEqual(rings, ["50%"], "0.9.5, first counted on the third of five days")
        self.assertIn("30 November 2026 (0.9.5 out)", str(self.cards(sections["en"])["downloads"]["table"]))
        self.assertFalse(self.full["en"].find(lambda n: n.tag == "circle" and "mark" in n.classes()))

    def test_a_long_history_is_drawn_by_the_week(self):
        """A running total of up to DAILY days has a point a day; a longer one a point a week, the first day, the last
        and every seventh back from it, so a picture and its table grow by a row a week."""
        start = date(2026, 1, 1)
        self.assertEqual(build.weekly([(start + timedelta(i), i) for i in range(build.DAILY)]),
                         [(start + timedelta(i), i) for i in range(build.DAILY)])
        thinned = build.weekly([(start + timedelta(i), i) for i in range(50)])
        self.assertEqual([n for _, n in thinned], [0, 7, 14, 21, 28, 35, 42, 49])
        thinned = build.weekly([(start + timedelta(i), i) for i in range(52)])
        self.assertEqual([n for _, n in thinned], [0, 2, 9, 16, 23, 30, 37, 44, 51])
        self.assertEqual(build.DAILY, 35)

    @staticmethod
    def pieces(paths):
        """Each cubic piece of the paths: its two handles, its end and its start."""
        for p in paths:
            d = p.attrs["d"]
            start = tuple(float(v) for v in re.match(r"M([\d.]+),([\d.]+)", d).groups())
            for c in re.findall(r"C([\d.]+,[\d.]+) ([\d.]+,[\d.]+) ([\d.]+,[\d.]+)", d):
                c1, c2, end = (tuple(float(v) for v in xy.split(",")) for xy in c)
                yield c1, c2, end, start
                start = end

    def test_each_picture_has_its_text_alternative(self):
        """Every picture is hidden from screen readers and none can take the focus without site.js; each card with a
        picture has one table of the same numbers, captioned, with a row for each point of its line; the figure, its
        title and its line are text."""
        for lang, section in self.full.items():
            for card in section.find(lambda n: "card" in n.classes()):
                pictures = card.find(lambda n: n.tag == "svg" and "chart" in n.classes()) + card.find(
                    lambda n: n.tag == "ol" or "split" in n.classes() or "legend" in n.classes())
                tables = card.find(lambda n: n.tag == "table")
                with self.subTest(lang, card=card.attrs["id"]):
                    if not pictures:
                        continue
                    self.assertEqual(len(tables), 1)
                    self.assertIn("sr-only", tables[0].classes())
                    self.assertTrue(squash(tables[0].find(lambda n: n.tag == "caption")[0].text()))
                    for picture in pictures:
                        self.assertEqual(picture.nearest("aria-hidden"), "true", picture.tag)
                    self.assertFalse(card.find(lambda n: "tabindex" in n.attrs))
                    rows = tables[0].find(lambda n: n.tag == "tr" and n.parent.tag == "tbody")
                    for plot in card.find(lambda n: "plot" in n.classes()):  # site.js shows a point from its row
                        self.assertEqual(len(plot.attrs["data-points"].split()), len(rows))
                    for svg in card.find(lambda n: n.tag == "svg" and "chart" in n.classes()):
                        self.assertEqual((svg.attrs.get("aria-hidden"), svg.attrs.get("focusable")), ("true", "false"))

    def test_the_cards_take_their_places_in_the_grid(self):
        """Five figure cards of GitHub's numbers, one column each of the three, then the counts: one card more before
        the first month, so two full rows, and with a month a row of its own, the stars filling the row before it. A card whose file can't be read is left
        out and the others keep their order."""
        self.assertEqual({k: c["span"] for k, c in self.cards(self.full["en"]).items()},
                         {"downloads": "span-1", "releases": "span-1", "platforms": "span-1", "views": "span-1",
                          "stars": "span-2", "counts": "span-3"})
        sections, _ = self.build("grid-not-yet", stats=self.stats, usage=self.dir / "nowhere.json")
        self.assertEqual([c["span"] for c in self.cards(sections["en"]).values()], ["span-1"] * 6)
        broken = fixture.write_stats(self.dir / "no-views")
        (broken / "views.csv").unlink()
        sections, log = self.build("no-views-site", stats=broken, usage=self.usage)
        self.assertEqual(list(self.cards(sections["en"])), ["downloads", "releases", "platforms", "stars", "counts"])
        self.assertIn("views.csv can't be read", log)

    def test_the_dates_are_the_numbers_own(self):
        """GitHub's numbers are as of the newest day in its daily snapshots, said under the grid; the latest release is
        out on the day it was first counted, the stars count from their first day, the page views run to the last day
        recorded, and the counts are of the newest month, each named in the page's language and in a time element."""
        for lang, section in self.full.items():
            (source,) = section.find(lambda n: "numbers-source" in n.classes())
            self.assertEqual([(t.attrs["datetime"], squash(t.text())) for t in source.find(lambda n: n.tag == "time")],
                             [("2026-12-02", {"en": "2 December 2026", "ar": "2 ديسمبر 2026"}[lang])])
            times = lambda key: [(t.attrs["datetime"], squash(t.text())) for t in section.find(  # noqa: E731
                lambda n: n.attrs.get("id") == f"numbers-{key}")[0].find(lambda n: n.tag == "time" and not n.inside(lambda m: m.tag == "table"))]
            self.assertEqual(times("counts"), [("2026-11", {"en": "November 2026", "ar": "نوفمبر 2026"}[lang])])
            self.assertEqual(times("releases"), [("2026-12-02", {"en": "2 December", "ar": "2 ديسمبر"}[lang])])
            self.assertEqual(times("stars"), [("2026-12-01", {"en": "1 December", "ar": "1 ديسمبر"}[lang])])
            self.assertEqual(times("views"), [])
        # with a shorter history the page views say from when, and the average is over the days recorded
        short = fixture.VIEWS[-10:]
        sections, _ = self.build("short", stats=fixture.write_stats(self.dir / "short-stats", views=short), usage=self.usage)
        views = self.cards(sections["en"])["views"]
        self.assertEqual((views["title"], views["line"]),
                         ("Repository page views since 22 November", f"about {round(sum(n for _, n, _ in short) / 10, 1)} a day"))

    def test_before_the_first_month_the_page_says_when_it_comes(self):
        """Without docs/usage.json the counts' card is a calm hatched one with a line saying the first totals,
        November's, come out in early December, and no number standing in for them; without a stats folder GitHub's
        cards are left out."""
        sections, log = self.build("not-yet", usage=self.dir / "nowhere.json")
        self.assertEqual(log, "")
        for lang, words in (("en", "their first monthly totals, for November 2026, come out in early December"),
                            ("ar", "وأول مجموع شهري ليهم، بتاع نوفمبر 2026، هيطلع في أوائل ديسمبر")):
            with self.subTest(lang):
                cards = self.cards(sections[lang])
                self.assertEqual(list(cards), ["counts"])
                self.assertIn(words, cards["counts"]["soon"][0])
                self.assertEqual((cards["counts"]["stats"], cards["counts"]["span"]), ([], "span-1"))
                self.assertFalse(sections[lang].find(lambda n: n.tag in ("data", "svg", "ol", "table")))
                self.assertNotIn(build.CHARTS, str([a.attrs["href"] for a in sections[lang].find(lambda n: n.tag == "a")]))
        css = re.sub(r"/\*.*?\*/", "", (ROOT / "site" / "styles.css").read_text(encoding="utf-8"), flags=re.S)
        self.assertRegex(css, r"#numbers-counts:has\(\.soon\) \{[^}]*repeating-linear-gradient\(135deg, var\(--line\)")
        sections, _ = self.build("not-yet-github", stats=self.stats, usage=self.dir / "nowhere.json")
        self.assertEqual(list(self.cards(sections["en"])), ["downloads", "releases", "platforms", "views", "stars", "counts"])

    def test_a_missing_or_broken_file_leaves_its_card_out(self):
        """A stats file that is missing, empty or not what repo_stats.py writes leaves its cards out, with a line in the
        log, and the build goes on; unreadable totals leave the counts' card out, with no promise of totals either.
        The website's totals missing for the newest month leave only them out, and nothing at all, no section."""
        broken = fixture.write_stats(self.dir / "broken")
        (broken / "repo.csv").unlink()
        (broken / "views.csv").write_text("date,views,unique\n2026-12-01,many,2\n", encoding="utf-8")
        sections, log = self.build("broken-site", stats=broken, usage=self.usage)
        self.assertEqual(list(self.cards(sections["en"])), ["downloads", "releases", "platforms", "counts"])
        self.assertIn("repo.csv can't be read", log)
        self.assertIn("views.csv can't be read", log)
        for name, text in (("empty", ""), ("header", "date,release,asset,downloads\n"), ("wrong", "a,b\n1,2\n"),
                           ("negative", "date,release,asset,downloads\n2026-12-02,v1.0.0,x.exe,-4\n")):
            with self.subTest(name):
                folder = fixture.write_stats(self.dir / f"stats-{name}")
                (folder / "downloads.csv").write_text(text, encoding="utf-8")
                sections, log = self.build(f"site-{name}", stats=folder, usage=self.usage)
                self.assertEqual(list(self.cards(sections["en"])), ["views", "stars", "counts"])
                self.assertIn("downloads.csv can't be read", log)
        empty = self.dir / "empty-stats"
        empty.mkdir()
        sections, log = self.build("no-stats", stats=empty, usage=self.usage)
        self.assertEqual(list(self.cards(sections["en"])), ["counts"])
        self.assertIn("GitHub's numbers are left out", log)
        for name, text in (("json", "{not json"), ("shape", '{"months": {"2026-11": {"installs": 3}}}'),
                           ("negative", json.dumps({"months": {"2026-11": {**fixture.USAGE["months"]["2026-11"],
                                                                           "installs": {"active": -1, "countries": {"count": 1, "installs": {}}}}}}))):
            with self.subTest(name):
                path = self.dir / f"usage-{name}.json"
                path.write_text(text, encoding="utf-8")
                sections, log = self.build(f"usage-site-{name}", stats=self.stats, usage=path)
                self.assertNotIn("counts", self.cards(sections["en"]))
                self.assertIn("the counts are left out", log)
                self.assertNotIn("first monthly totals", squash(visible_text(sections["en"])))
        sections, _ = self.build("both-gone", stats=empty, usage=self.dir / "usage-json.json")
        self.assertEqual(sections, {"en": None, "ar": None}, "nothing to show, so no section")
        data = json.loads(json.dumps(fixture.USAGE))
        del data["website"]["2026-11"]
        sections, log = self.build("no-website", stats=self.stats, usage=fixture.write_usage(self.dir / "no-website.json", data))
        self.assertFalse(sections["en"].find(lambda n: "website" in n.classes()))
        self.assertIn("installs ran Sedjem", str(self.cards(sections["en"])["counts"]["stats"]))
        self.assertEqual(log, "")

    def test_the_arabic_page_has_the_same_numbers(self):
        """The same numbers in the same order on both pages, in the figures, the pictures, the legends and the tables,
        written alike: Western digits and the same separators, as the Arabic copy writes every number."""
        values = {lang: [(d.attrs["value"], d.text()) for d in section.find(lambda n: n.tag == "data")]
                  for lang, section in self.full.items()}
        self.assertEqual(len(values["en"]), 5 + 7)  # the figure of each of GitHub's cards, and seven of the counts'
        self.assertEqual(values["en"], values["ar"])
        points = {lang: [plot.attrs["data-points"] for plot in section.find(lambda n: "plot" in n.classes())]
                  for lang, section in self.full.items()}
        self.assertEqual(points["en"], points["ar"])
        digits = {lang: re.findall(r"\d+(?:[,.]\d+)*", squash(visible_text(section))) for lang, section in self.full.items()}
        self.assertEqual(Counter(digits["en"]), Counter(digits["ar"]))
        self.assertFalse(re.search("[٠-٩۰-۹]", visible_text(self.full["ar"])), "Arabic-Indic digits")
        self.assertEqual(build.NUMERALS, {"en": (",", "."), "ar": (",", ".")})
        ar = self.cards(self.full["ar"])
        self.assertEqual(ar["counts"]["rank"][0], ("مصر", "640"))
        self.assertEqual(ar["platforms"]["table"][0][0], "Windows")

    def test_the_numbers_are_in_the_page_and_nothing_is_fetched(self):
        """Every number is in the HTML, so the section needs no script: it has none, loads nothing, its pictures link
        nothing outside the page (their washes use gradients of their own, by id), the points site.js may show are
        plain numbers, and its links are the stats branch's charts and the method. The build reads the files it is
        given and nothing else. The section sits after the facts and before the downloads, under its own title."""
        for lang, section in self.full.items():
            self.assertFalse(section.find(lambda n: n.tag in ("script", "img", "iframe", "object", "noscript", "image", "use", "foreignobject")))
            self.assertFalse([n for n in section.iter() if any(k.startswith("on") or k in ("src", "href", "xlink:href") for k in n.attrs) and n.tag != "a"])
            self.assertEqual([a.attrs["href"] for a in section.find(lambda n: n.tag == "a")], [build.CHARTS, build.METHOD])
            for node in section.find(lambda n: "url(" in n.attrs.get("style", "")):
                self.assertRegex(node.attrs["style"], r"^fill: url\(#wash-\w+\)$")
            for plot in section.find(lambda n: "plot" in n.classes()):
                self.assertRegex(plot.attrs["data-points"], r"^[\d.]+,[\d.]+(?: [\d.]+,[\d.]+)*$")
            (h2,) = section.find(lambda n: n.tag == "h2")
            self.assertEqual((h2.attrs["id"], squash(h2.text())), ("numbers", {"en": "Sedjem in numbers", "ar": "Sedjem بالأرقام"}[lang]))
            self.assertEqual(h2.find(lambda n: n.attrs.get("translate") == "no")[0].text(), "Sedjem")
        self.assertEqual(build.CHARTS, "https://github.com/MohammedEl-sayedAhmed/sedjem/tree/stats#readme")
        self.assertTrue(build.METHOD.endswith("/blob/main/docs/13-usage-counts.md#public-totals"))
        self.assertIn("## Public totals", (ROOT / "docs" / "13-usage-counts.md").read_text(encoding="utf-8"))
        self.assertIsNone(build.github(None))
        page = (self.dir / "full" / "index.html").read_text(encoding="utf-8")
        self.assertLess(page.index('aria-labelledby="facts"'), page.index('aria-labelledby="numbers"'))
        self.assertLess(page.index('aria-labelledby="numbers"'), page.index('aria-labelledby="get"'))
        self.assertNotIn("Counted in the open", page + (ROOT / "site" / "DESIGN.md").read_text(encoding="utf-8"))

    def test_the_palette_is_the_one_design_md_records(self):
        """The pictures' colours are the brand's teal for a series, a light teal for a whole's second part (a darker
        one on the dark card) and a grey for the rest, as DESIGN.md records them with what the dataviz validator said
        of them; the marks take them from the stylesheet, never from the page, and text never wears them."""
        css = re.sub(r"/\*.*?\*/", "", (ROOT / "site" / "styles.css").read_text(encoding="utf-8"), flags=re.S)
        root = re.search(r":root\s*\{([^}]*)\}", css).group(1)
        self.assertEqual(re.findall(r"--series-(\w+): (#[0-9a-f]{6});", root), [("1", "#14a3a1"), ("2", "#6dbeba"), ("rest", "#8a95a3")])
        for block in (r':root:not\(\[data-theme="light"\]\)\s*\{([^}]*)\}', r':root\[data-theme="dark"\]\s*\{([^}]*)\}'):
            self.assertEqual(sorted(re.findall(r"--series-(\w+): (#[0-9a-f]{6});", re.search(block, css).group(1))),
                             [("2", "#2f7f7c"), ("rest", "#6f7a88")])
        for rule in re.findall(r"([^{}]+)\{([^}]*var\(--series-[^}]*)\}", css):
            self.assertNotRegex(rule[1], r"(?<![-\w])color:\s*var\(--series", rule[0])
        design = (ROOT / "site" / "DESIGN.md").read_text(encoding="utf-8").split("## The numbers")[1].split("\n## ")[0]
        for words in ("#14a3a1", "#6dbeba", "#2f7f7c", "validate_palette.js"):
            self.assertIn(words, design)


def docs_row(path, heading, first):
    """The cells of the row that starts with first in the table under heading (## or ###) of a docs file."""
    text = (ROOT / path).read_text(encoding="utf-8")
    part = re.split(r"\n#{2,3} " + re.escape(heading) + "\n", text)[1]
    (line,) = [x for x in re.split(r"\n#{2,3} ", part)[0].splitlines() if x.startswith("| " + first)]
    return [c.strip() for c in line.strip().strip("|").split("|")]


def docs_text(path):
    return squash((ROOT / path).read_text(encoding="utf-8"))


def text_outside(node, skip):
    """The text a reader sees outside the elements skip picks."""
    if node.tag in ("script", "style") or skip(node):
        return ""
    return "".join(c if isinstance(c, str) else text_outside(c, skip) for c in node.children)


class BenchmarksTest(unittest.TestCase):
    """The benchmarks: six figure cards, each over bars to compare, every number the one the docs publish. The build
    reads them through docs/charts.py from results/summary.json, results/*-diarization.json and docs/charts-data.json;
    this checks them against the docs' own tables and sentences."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = build.build(Path(cls.tmp.name) / "site")
        cls.roots, cls.sections = {}, {}
        for lang, page in HOMES.items():
            root, errors = parse((out / page).read_text(encoding="utf-8"))
            assert not errors, errors
            cls.roots[lang] = root
            (cls.sections[lang],) = root.find(lambda n: n.tag == "section" and n.attrs.get("aria-labelledby") == "benchmarks")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @staticmethod
    def cards(section):
        """Each card by its key: its figure (value, as shown), title and line, its chart's rows, each a heading's text or
        (classes, name, value as shown, --v), the note under the chart, and its table's caption and rows."""
        out = {}
        for card in section.find(lambda n: "card" in n.classes()):
            first = lambda node, test: node.find(test)[0]  # noqa: E731
            data = first(first(card, lambda n: "kpi-figure" in n.classes()), lambda n: n.tag == "data")
            rows = []
            for li in card.find(lambda n: n.tag == "li" and "bar-list" in n.parent.classes()):
                if "bar-head" in li.classes():
                    rows.append(squash(li.text()))
                    continue
                fill = first(li, lambda n: "bar-fill" in n.classes())
                rows.append((li.classes(), squash(first(li, lambda n: "bar-name" in n.classes()).text()),
                             squash(first(li, lambda n: "bar-value" in n.classes()).text()),
                             float(fill.attrs["style"].removeprefix("--v: "))))
            table = first(card, lambda n: n.tag == "table")
            out[card.attrs["id"].removeprefix("benchmarks-")] = {
                "figure": (data.attrs["value"], squash(data.text())),
                "title": squash(first(card, lambda n: n.tag == "h3").text()),
                "line": squash(first(card, lambda n: "kpi-text" in n.classes()).find(lambda n: n.tag == "p")[0].text()),
                "rows": rows, "note": squash(first(card, lambda n: "bar-note" in n.classes()).text()),
                "caption": squash(first(table, lambda n: n.tag == "caption").text()),
                "table": [[squash(c.text()) for c in tr.children if not isinstance(c, str)]
                          for tr in table.find(lambda n: n.tag == "tr" and n.parent.tag == "tbody")],
                "hidden": [n.nearest("aria-hidden") for n in card.find(lambda n: "bars" in n.classes())],
                "boxed": table.parent.classes(),
            }
        return out

    @staticmethod
    def expected():
        """Each card's bars as the docs publish them, in order: (heading over it or None, name on the bar, the name in
        full, value as shown), and which way is better."""
        results, speakers = "docs/03-results.md", "docs/04-speaker-labels.md"
        perle = {"Cohere": ("Cohere Transcribe Arabic", docs_row(results, "Public benchmark: Perle at 16 kHz", "Cohere Transcribe Arabic (")),
                 "whisper-medium": ("whisper-medium code-switching", docs_row(results, "Public benchmark: Perle at 16 kHz", "whisper-medium")),
                 "Whisper large-v3": ("Whisper large-v3", docs_row(results, "Public benchmark: Perle at 16 kHz", "Whisper large-v3 + style hint")),
                 "Audar": ("Audar-ASR-V1-Turbo", docs_row(results, "Also tried", "Audar-ASR-V1-Turbo")),
                 "R2T2": ("R2T2", docs_row(results, "Also tried", "R2T2")),
                 "Qwen3-ASR": ("Qwen3-ASR-1.7B", docs_row(results, "Also tried", "Qwen3-ASR-1.7B"))}
        tried = lambda name: "Also tried" if name in ("Audar", "R2T2", "Qwen3-ASR") else None  # noqa: E731
        pooled = docs_row(results, "Real meetings", "pooled")
        meetings = re.search(r"Other pooled numbers.*?speed with speaker labels [\d.]+–[\d.]+ times real time \(mean ([\d.]+)\) "
                             r"and [\d.]+–[\d.]+ \(mean ([\d.]+)\)", docs_text(results)).groups()
        minutes = [str(int((Decimal(m) * 60).quantize(Decimal(1), ROUND_HALF_UP))) for m in meetings]
        two_or_three = re.search(r"and on ([\d.]+)% over the five 2–3-person excerpts, where TitaNet-small gets ([\d.]+)%",
                                 docs_text(speakers)).groups()
        on_speech = lambda heading, first: docs_row(speakers, heading, first)[1]  # noqa: E731
        sets = {"ArzEn": "ArzEn: interviews in Egyptian Arabic and English", "AMI": "AMI: English meetings with much overlap"}
        return {
            "wer": ("lower", [(tried(k), k, full, cells[1].split()[0]) for k, (full, cells) in perle.items()]),
            "english": ("higher", [(tried(k), k, full, cells[5]) for k, (full, cells) in perle.items()]),
            "nemotron": ("lower", [(s, short, full, on_speech(heading, row)) for s, heading in sets.items()
                                   for short, full, row in (("TitaNet-small", "TitaNet-small", "TitaNet-small voiceprints (default)"),
                                                            ("Nemotron", "Nemotron-3-Diarization", "Nemotron, one speaker at a time"))]),
            "meetings": ("lower", [(None, "Cohere", "Cohere Transcribe Arabic", pooled[2].split()[0]),
                                   (None, "whisper-medium", "whisper-medium code-switching", pooled[3].split()[0])]),
            "speed": ("lower", [(None, "Cohere", "Cohere Transcribe Arabic", f"{minutes[0]} min"),
                                (None, "whisper-medium", "whisper-medium code-switching", f"{minutes[1]} min")]),
            "speakers": ("lower", [(None, "TitaNet-small", "TitaNet-small", f"{two_or_three[1]}%"),
                                   (None, "ElevenLabs", "ElevenLabs", f"{two_or_three[0]}%")]),
        }

    def test_every_number_is_the_docs(self):
        """Each bar is the number the docs publish, in the docs' order: the word error rate and the English kept on
        Perle of the three models Sedjem runs and the three also tried, the speech TitaNet-small and Nemotron give to
        the wrong speaker on ArzEn and AMI, the recorded meetings' difference from ElevenLabs and their time for an hour
        (their mean speed, times 60 minutes), and the words TitaNet-small and ElevenLabs give to the wrong speaker on
        meetings of two or three people. A card's figure is the best of Sedjem's own bars, and its line names that
        model, and the test set on the speaker labels' card."""
        e = self.expected()
        self.assertEqual((e["wer"][1][0][3], e["english"][1][1][3], e["speakers"][1][0][3]), ("13.4%", "90%", "9.0%"))
        for lang, section in self.sections.items():
            cards = self.cards(section)
            self.assertEqual(list(cards), list(build.BENCHMARKS))
            self.assertEqual(list(cards), ["wer", "english", "nemotron", "meetings", "speed", "speakers"])
            for key, (better, bars) in e.items():
                with self.subTest(lang, card=key):
                    c, unit = cards[key], (lambda text: text.replace(" min", {"en": " min", "ar": " دقيقة"}[lang]))
                    shown, heading = [], None
                    for head, short, _, value in bars:
                        if head and head != heading:
                            shown.append({"Also tried": {"en": "Also tried", "ar": "اتجرّبوا كمان"}[lang]}.get(head, head))
                        heading = head
                        shown.append((short, unit(value)))
                    self.assertEqual([r if isinstance(r, str) else (r[1], r[2]) for r in c["rows"]], shown)
                    ours = [b for b in bars if b[1] not in ("Audar", "R2T2", "Qwen3-ASR", "ElevenLabs")]
                    number = lambda b: float(b[3].split()[0].rstrip("%"))  # noqa: E731
                    lead = (min if better == "lower" else max)(ours, key=number)
                    value = lead[3].split()[0].rstrip("%")
                    self.assertEqual(c["figure"], (value, unit(lead[3]) if "min" in lead[3] else lead[3]))
                    self.assertIn(lead[2], c["line"])
                    if key == "nemotron":
                        self.assertIn(lead[0], c["line"])
                    leads = [r[1] for r in c["rows"] if not isinstance(r, str) and "bar-lead" in r[0]]
                    self.assertEqual(leads, [lead[1]])

    def test_the_bars_start_at_zero_and_say_which_way_is_better(self):
        """Every bar starts at zero and is as long as its number over the chart's whole: a share of the words over
        100%, the minutes over an hour, and the speaker labels' small shares over the largest of them. Under each
        chart a note says which way is better, and the figure is the best bar that way. Sedjem's own bars are the
        brand's teal and the others grey, through their classes."""
        notes = {"en": {"lower": "Lower is better", "higher": "Higher is better", "closer": "Lower is closer",
                        "faster": "Lower is faster"},
                 "ar": {"lower": "الأقل أحسن", "higher": "الأعلى أحسن", "closer": "الأقل أقرب", "faster": "الأقل أسرع"}}
        wholes = {"wer": 100, "english": 100, "meetings": 100, "speed": 60, "nemotron": None, "speakers": None}
        directions = {"wer": "lower", "english": "higher", "nemotron": "lower", "meetings": "closer", "speed": "faster",
                      "speakers": "lower"}
        for lang, section in self.sections.items():
            for key, c in self.cards(section).items():
                with self.subTest(lang, card=key):
                    bars = [r for r in c["rows"] if not isinstance(r, str)]
                    values = [float(r[2].split()[0].replace("%", "").replace(NBSP, "")) for r in bars]
                    whole = wholes[key] or max(values)
                    self.assertEqual([r[3] for r in bars], [float(f"{v / whole * 100:.1f}") for v in values])
                    self.assertEqual(c["note"], notes[lang][directions[key]])
                    for classes, name, _, _ in bars:
                        ours = name in ("Cohere", "whisper-medium", "Whisper large-v3", "TitaNet-small", "Nemotron")
                        self.assertIn("bar-sedjem" if ours else "bar-other", classes)
        css = re.sub(r"/\*.*?\*/", "", (ROOT / "site" / "styles.css").read_text(encoding="utf-8"), flags=re.S)
        self.assertRegex(css, r"\.bar-list \{[^}]*grid-template-columns: max-content minmax\(0, 1fr\) max-content;")
        self.assertRegex(css, r"\.bar-list li \{ display: contents; \}")  # one grid, so every bar starts on one line
        fill = re.search(r"\.bar-fill \{([^}]*)\}", css).group(1)
        for rule in ("inset-inline-start: 0;", "width: calc(var(--v) * 1%);", "background: var(--series-1);"):
            self.assertIn(rule, fill)
        self.assertRegex(css, r"\.bar-other \.bar-fill \{ background: var\(--series-rest\); \}")

    def test_each_chart_has_a_table_of_its_numbers(self):
        """The bars are a picture, hidden from screen readers; each card has a table of the same numbers instead, with a
        caption that says what they are and which way is better, in a hidden box so its long caption never widens the
        page: a row for each bar with the model's full name, or for the speaker labels a row for each test set and a
        column for each model."""
        e = self.expected()
        for lang, section in self.sections.items():
            for key, c in self.cards(section).items():
                with self.subTest(lang, card=key):
                    self.assertEqual(c["hidden"], ["true"])
                    self.assertEqual(c["boxed"], ["sr-only"])
                    self.assertTrue(c["caption"])
                    bars = [(r[1], r[2]) for r in c["rows"] if not isinstance(r, str)]
                    if key == "nemotron":
                        self.assertEqual(c["table"], [["ArzEn", bars[0][1], bars[1][1]], ["AMI", bars[2][1], bars[3][1]]])
                        continue
                    self.assertEqual([row[1] for row in c["table"]], [value for _, value in bars])
                    for row, (_, _, full, _) in zip(c["table"], e[key][1]):
                        self.assertIn(full, row[0])
            self.assertFalse(section.find(lambda n: "tabindex" in n.attrs or n.tag in ("script", "svg", "img")))

    def test_the_test_sets_and_the_docs_are_linked(self):
        """Under the grid, each public test set is linked where it is downloaded, with the licence the docs give it next
        to the same address, and the number of Perle clips the docs give; then the results, the speaker labels and the
        tables in the repository."""
        docs = build.DOCS
        self.assertEqual(docs, "https://github.com/MohammedEl-sayedAhmed/sedjem/blob/main/docs/")
        for lang, section in self.sections.items():
            with self.subTest(lang):
                (sources,) = section.find(lambda n: "sources" in n.classes())
                links = [(a.attrs["href"], squash(a.text())) for a in sources.find(lambda n: n.tag == "a")]
                self.assertEqual(links[:3], [(url, name) for name, url in build.TEST_SETS.values()])
                self.assertEqual([href for href, _ in links[3:]], [docs + "03-results.md", docs + "04-speaker-labels.md",
                                                                  docs + "results-tables.md"])
                text = squash(sources.text())
                for licence in ("MIT", "CC BY-SA 4.0", "CC BY 4.0"):
                    self.assertIn(licence, text)
                self.assertIn("40", re.findall(r"\d+", text))
        self.assertTrue(docs_row("docs/03-results.md", "Test sets", "Perle")[2].startswith("40 random"))
        self.assertIn("(https://huggingface.co/datasets/Perle-ai/ASR_Code_Switch), MIT", docs_text("docs/research/market-research-report.md"))
        speakers = docs_text("docs/04-speaker-labels.md")
        self.assertIn("(https://sites.google.com/view/arzen-corpus/resources), CC BY-SA 4.0", speakers)
        self.assertIn("(https://groups.inf.ed.ac.uk/ami/corpus/), CC BY 4.0", speakers)
        for path in ("docs/03-results.md", "docs/04-speaker-labels.md", "docs/results-tables.md"):
            self.assertTrue((ROOT / path).is_file())

    def test_the_private_meetings_are_only_pooled_numbers(self):
        """The recorded meetings are private: the section calls them a private set of recorded meetings and names no
        meeting, excerpt or file, and the build reads only their pooled numbers from docs/charts-data.json."""
        src = build.bench_sources()
        read, published = [], src.published
        src.published = lambda block, row, column=None: read.append((block, row, column)) or published(block, row, column)
        build.measured(src)
        self.assertEqual(sorted({block for block, *_ in read}), ["meeting_speed", "meetings", "meetings_two_or_three_people"])
        self.assertEqual({row for block, row, _ in read if block == "meetings"}, {"pooled"})
        for lang, words in (("en", "a private set of recorded meetings"), ("ar", "مجموعة خاصة من اجتماعات متسجّلة")):
            text = squash(visible_text(self.sections[lang]))
            self.assertIn(words, text)
            self.assertNotRegex(text, r"\bm[1-6]\b|excerpt|\.(?:wav|json|txt|srt)\b")

    def test_no_number_is_said_twice(self):
        """The benchmarks hold the page's measurements and say each once: no other section has a percentage or a time
        for an hour, the old table's and sentences' ranges are gone, and the sections that had them point to the
        benchmarks. In the section each bar is a different model on its card and test set, so the only number shown
        twice is a card's figure, beside the bar it leads in the same card."""
        for lang, root in self.roots.items():
            with self.subTest(lang):
                outside = squash(text_outside(root, lambda n: n.tag == "section" and n.attrs.get("aria-labelledby") in ("benchmarks", "numbers")))
                self.assertNotRegex(outside, r"\d\s*%|\d\s*(?:min\b|minutes|دقيقة|دقايق)")
                page = squash(visible_text(root))
                for gone in ("8–25%", "77–90%", "8–9%", "30–45"):
                    self.assertNotIn(gone, page)
                for key in ("voices", "facts"):
                    (sec,) = root.find(lambda n: n.tag == "section" and n.attrs.get("aria-labelledby") == key)
                    self.assertEqual([a.attrs["href"] for a in sec.find(lambda n: n.tag == "a" and n.attrs["href"].startswith("#"))],
                                     ["#benchmarks"])
                self.assertFalse(root.find(lambda n: n.attrs.get("id") == "measured"))
                cards = self.cards(self.sections[lang])
                named = []
                for key, c in cards.items():
                    heading = None
                    for r in c["rows"]:
                        if isinstance(r, str):
                            heading = r
                        else:
                            named.append((key, heading, r[1]))
                self.assertEqual(len(named), len(set(named)))

    def test_both_pages_have_the_same_numbers(self):
        """The same figures and bars on both pages, in Western digits; only the words differ."""
        cards = {lang: self.cards(section) for lang, section in self.sections.items()}
        digits = lambda text: re.findall(r"\d+(?:\.\d+)?", text)  # noqa: E731
        for key in build.BENCHMARKS:
            en, ar = cards["en"][key], cards["ar"][key]
            self.assertEqual(en["figure"][0], ar["figure"][0])
            self.assertEqual([(r[1], digits(r[2]), r[3]) for r in en["rows"] if not isinstance(r, str)],
                             [(r[1], digits(r[2]), r[3]) for r in ar["rows"] if not isinstance(r, str)])
        self.assertFalse(re.search("[٠-٩۰-۹]", visible_text(self.sections["ar"])), "Arabic-Indic digits")

    def test_the_section_follows_the_features_it_measures(self):
        """The benchmarks come after the facts, which point to them, and before the numbers and the downloads, with
        their own title; the build reads them through docs/charts.py, the same sources as the docs' charts."""
        for lang, root in self.roots.items():
            order = [n.attrs.get("aria-labelledby") for n in root.find(lambda n: n.tag == "section")]
            self.assertEqual([s for s in order[order.index("facts"):] if s != "numbers"], ["facts", "benchmarks", "get"])
            if "numbers" in order:
                self.assertEqual(order.index("numbers"), order.index("benchmarks") + 1)
            self.assertLess(order.index("voices"), order.index("benchmarks"))
            (h2,) = self.sections[lang].find(lambda n: n.tag == "h2")
            self.assertEqual((h2.attrs["id"], squash(h2.text())), ("benchmarks", {"en": "Benchmarks", "ar": "نتايج الاختبارات"}[lang]))
        self.assertEqual(build.BENCH, ROOT / "docs" / "charts.py")
        self.assertIn("class Sources:", build.BENCH.read_text(encoding="utf-8"))


class ReleaseTest(unittest.TestCase):
    """The downloads: the latest release's files, from tests/site_support.py's made-up release."""

    def build(self, release=None, sums=None):
        """Each page's tree, built with release.json (and SHA256SUMS.txt) or none, and what the build said in the log."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = fixture.write_release(Path(tmp.name) / "release", release, sums) if release is not False else None
        log = io.StringIO()
        with contextlib.redirect_stderr(log), contextlib.redirect_stdout(io.StringIO()):
            out = build.build(Path(tmp.name) / "site", latest=folder)
        return {lang: parse((out / page).read_text(encoding="utf-8"))[0] for lang, page in HOMES.items()}, log.getvalue()

    @staticmethod
    def downloads(root):
        """The download links' addresses: the hero's (with its Linux one) and the downloads section's, and each way's
        file as (name, size in bytes, size shown, SHA256), or None."""
        (hero,) = root.find(lambda n: n.attrs.get("data-place") == "hero")
        out = {"hero": (hero.attrs["href"], hero.attrs["data-href-linux"])}
        for way in root.find(lambda n: "way" in n.classes() and n.find(lambda c: c.attrs.get("data-place") == "get" and c.tag == "a" and c.attrs.get("data-download") in ("windows", "linux"))):
            (link,) = way.find(lambda n: n.tag == "a" and n.attrs.get("data-download") in ("windows", "linux"))
            files = way.find(lambda n: "file" in n.classes())
            file = None
            if files:
                (name,) = files[0].find(lambda n: "file-name" in n.classes())
                (size,) = files[0].find(lambda n: n.tag == "data")
                sums = files[0].find(lambda n: n.tag == "code")
                file = (name.text(), int(size.attrs["value"]), squash(size.text()), sums[0].text() if sums else None)
            out[link.attrs["data-download"]] = (link.attrs["href"], file)
        return out

    def test_the_downloads_are_the_latest_releases_files(self):
        """Each download links the latest release's file for its system, the hero's Windows' with Linux's for site.js;
        under each, the file's name, its size as GitHub's release page gives it (MB of 1,048,576 bytes) with the bytes
        in its data element, and its SHA256 in mono, the same on both pages; the version is the release's."""
        pages, log = self.build()
        self.assertEqual(log, "")
        url = lambda name: f"https://github.com/MohammedEl-sayedAhmed/sedjem/releases/download/{fixture.TAG}/{name}"  # noqa: E731
        (win, win_size, win_sha), (lin, lin_size, lin_sha) = fixture.FILES["windows"], fixture.FILES["linux"]
        expected = {"hero": (url(win), url(lin)),
                    "windows": (url(win), (win, win_size, "87.7 MB", win_sha)),
                    "linux": (url(lin), (lin, lin_size, "190.2 MB", lin_sha))}
        for lang, root in pages.items():
            with self.subTest(lang):
                self.assertEqual(self.downloads(root), expected)
                for code in root.find(lambda n: n.tag == "code" and "sum" in n.classes()):
                    self.assertEqual((code.attrs.get("lang"), code.attrs.get("dir"), code.attrs.get("translate")), ("en", "ltr", "no"))
        self.assertEqual((f"{win_size / 2 ** 20:.1f}", f"{lin_size / 2 ** 20:.1f}"), ("87.7", "190.2"))
        css = re.sub(r"/\*.*?\*/", "", (ROOT / "site" / "styles.css").read_text(encoding="utf-8"), flags=re.S)
        (rule,) = re.findall(r"\n\.sum \{([^}]*)\}", css)
        self.assertIn("var(--mono)", rule)
        self.assertIn("user-select: all", rule)
        script = (ROOT / "site" / "site.js").read_text(encoding="utf-8")
        self.assertIn('link.setAttribute("href", link.getAttribute("data-href-linux"))', script)

    def test_the_sums_come_from_the_release(self):
        """SHA256SUMS.txt gives each file's SHA256 when the folder has it, and GitHub's digest otherwise; a file whose
        two differ shows neither, with a line in the log, and one without either shows its name and size alone."""
        (win, _, win_sha), (lin, _, lin_sha) = fixture.FILES["windows"], fixture.FILES["linux"]
        sums = lambda pages: {k: v[1][3] for k, v in self.downloads(pages["en"]).items() if k != "hero"}  # noqa: E731
        pages, log = self.build(fixture.release_json(digests=False), fixture.SUMS)
        self.assertEqual((sums(pages), log), ({"windows": win_sha, "linux": lin_sha}, ""))
        pages, log = self.build(fixture.release_json(), fixture.SUMS)
        self.assertEqual((sums(pages), log), ({"windows": win_sha, "linux": lin_sha}, ""))
        pages, log = self.build(fixture.release_json(), fixture.SUMS.replace(win_sha, "0" * 64))
        self.assertEqual(sums(pages), {"windows": None, "linux": lin_sha})
        self.assertIn(f"{win}: SHA256SUMS.txt and GitHub's digest differ", log)
        pages, _ = self.build(fixture.release_json(digests=False))
        self.assertEqual(sums(pages), {"windows": None, "linux": None})
        self.assertEqual(self.downloads(pages["en"])["linux"][1][:3], (lin, fixture.FILES["linux"][1], "190.2 MB"))

    def test_without_the_release_the_general_wording(self):
        """Without the release's details (no folder, no release.json, a release.json that isn't a release's, a
        pre-release, or a file served from elsewhere) every download links the release's page, with the general
        wording, no file and no SHA256, and the version is the app's; the build says why in its log and goes on."""
        latest = "https://github.com/MohammedEl-sayedAhmed/sedjem/releases/latest"
        general = {"hero": (latest, latest), "windows": (latest, None), "linux": (latest, None)}
        foreign = fixture.release_json()
        foreign["assets"][0]["browser_download_url"] = "https://example.com/Sedjem-0.10.0-setup.exe"
        for name, release, words in (("no folder", False, ""), ("not json", "{oops", "release.json can't be read"),
                                     ("a pre-release", fixture.release_json(prerelease=True), "isn't a release"),
                                     ("a preview's tag", fixture.release_json(tag="v0.10.1-preview.20261201"), "isn't a release"),
                                     ("elsewhere", foreign, "is served from")):
            with self.subTest(name):
                if release == "{oops":
                    tmp = tempfile.TemporaryDirectory()
                    self.addCleanup(tmp.cleanup)
                    (Path(tmp.name) / "release.json").write_text(release, encoding="utf-8")
                    log = io.StringIO()
                    with contextlib.redirect_stderr(log), contextlib.redirect_stdout(io.StringIO()):
                        out = build.build(Path(tmp.name) / "site", latest=Path(tmp.name))
                    pages, log = {lang: parse((out / page).read_text(encoding="utf-8"))[0] for lang, page in HOMES.items()}, log.getvalue()
                else:
                    pages, log = self.build(release)
                for lang, root in pages.items():
                    self.assertEqual(self.downloads(root), general)
                    (fine,) = root.find(lambda n: "fine" in n.classes())
                    self.assertTrue(squash(fine.text()).endswith(VERSION_IN[lang].format(build.version())))
                    self.assertFalse(root.find(lambda n: n.tag == "code" and "sum" in n.classes()))
                self.assertIn(words, log)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log = io.StringIO()
        with contextlib.redirect_stderr(log), contextlib.redirect_stdout(io.StringIO()):
            build.build(Path(tmp.name) / "site", latest=Path(tmp.name))
        self.assertIn("no release.json", log.getvalue())
        # the general wording is today's: the installer's size and SmartScreen
        with open(ROOT / "site" / "copy" / "en.toml", "rb") as f:
            self.assertIn("The installer is about 90&nbsp;MB", tomllib.load(f)["get"]["windows_text"])


if __name__ == "__main__":
    unittest.main()
