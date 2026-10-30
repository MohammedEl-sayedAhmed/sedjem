"""Tests for the website (site/build.py), which has an English page at the top and an Arabic one at ar/. The site
is built into a temporary folder and checked as GitHub Pages serves it, every check on each page: the pages parse,
every file they link or load is in the build and nothing is loaded from another site, every colour in the
stylesheet is one of the palette's, images have alt text and their real proportions, Arabic is marked as Arabic and
the Arabic page reads right to left, the copy keeps the repository's style, the transcript and the speakers' shares
come from docs/images/make/content.py and the version from app/__init__.py, the measured numbers are the docs',
the two pages carry the same sections, numbers and images and point to each other, the theme button is named in each
language and the dark theme is one set of tokens, the transcript, the download and the menu are whole without
JavaScript, the speakers' names are in colours that read on the card, the pictures of the app keep every pixel and
are set at their own size, and the first view stays small. Offline, standard library only.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import ast
import fnmatch
import importlib.util
import math
import posixpath
import re
import struct
import sys
import tempfile
import tomllib
import unittest
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
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
}
NAMED_COLOURS = re.compile(
    r"\b(?:white|black|red|green|blue|gr[ae]y|silver|orange|yellow|purple|pink|teal|navy|maroon|olive|lime|aqua|"
    r"fuchsia|cyan|magenta|brown|gold|indigo|violet|tomato|coral|salmon|khaki|beige|ivory|crimson)\b", re.I)
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
ARABIC = re.compile("[" + chr(0x600) + "-" + chr(0x6FF) + "]")
LATIN_WORD = re.compile(r"[A-Za-z]{2,}")
NBSP = chr(0xA0)
FIRST_VIEW_BUDGET = 360_000  # bytes: a page, its stylesheet and script, every font file and the header's two logos
# The two lines of the registers, and the words marked in each
REGISTERS = [
    ("صباح الخير يا جماعة، خلونا نبدأ السبرينت بلانينج بسرعة عشان عندنا ديمو الساعة اتنين",
     ["السبرينت بلانينج", "ديمو"]),
    ("صباح الخير يا جماعة، خلونا نبدأ ال sprint planning بسرعة عشان عندنا demo الساعة اتنين",
     ["sprint planning", "demo"]),
]
# How each page writes the version in the hero's small print
VERSION_IN = {"en": "Version {}.", "ar": "الإصدار {}."}
# Numbers the Arabic copy spells out: "about 2 h" is حوالي ساعتين, two hours in the dual, and "1 h takes" is ساعة صوت
# بتاخد, an hour of audio takes
SPELLED = {"ساعتين": "2", "ساعة صوت بتاخد": "1"}


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
        cls.out = build.build(Path(cls.tmp.name) / "site")
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
        self.assertNotRegex((self.out / "site.js").read_text(encoding="utf-8"), r"https?://|fetch\(|XMLHttpRequest")

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
        text_ar = visible_text(ar)
        for word, number in SPELLED.items():
            numbers["ar"][number] += text_ar.count(word)
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
        # and every one of them is used: by the template, or by build.py for the theme buttons and the pictures
        template = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        used = set(re.findall(r"\{\{(\w+\.\w+)\}\}", template)) | {"theme.system", "theme.light", "theme.dark"}
        self.assertEqual(used | set(build.SHOTS.values()), tables["en"])

    def test_copy_follows_the_house_style(self):
        for name in ("index.html", "ar/index.html", "404.html", "styles.css", "site.js"):
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
        hiding = [rule for rule in re.findall(r"([^{}]+)\{([^}]*)\}", css) if re.search(r"opacity:\s*0\b|visibility:\s*hidden", rule[1])]
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
        script = (self.out / "site.js").read_text(encoding="utf-8")
        for where, text in [*self.styles, ("site.js", script)]:
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
        """The header's theme button has a name in the page's language for each of its three states, its icons are
        hidden from screen readers, and site.js and the 404 page keep the reader's pick under the same key."""
        names = {}
        for lang, home in self.homes.items():
            buttons = home.find(lambda n: n.tag == "button" and "data-theme-switch" in n.attrs)
            self.assertEqual([b.classes() for b in buttons], [["theme", "theme-wide"], ["theme", "theme-menu"]])
            for button in buttons:
                with self.subTest(lang, where=button.classes()[1]):
                    self.assertTrue(button.inside(lambda n: n.tag == "header"))
                    self.assertEqual(button.attrs.get("type"), "button")
                    names[lang] = [button.attrs[f"data-{state}"] for state in ("system", "light", "dark")]
                    self.assertEqual(len(set(names[lang])), 3)
                    self.assertEqual(button.attrs["aria-label"], names[lang][0])
                    icons = button.find(lambda n: n.tag == "svg")
                    self.assertEqual([icon.classes() for icon in icons], [["icon-system"], ["icon-light"], ["icon-dark"]])
                    self.assertEqual({icon.attrs.get("aria-hidden") for icon in icons}, {"true"})
            self.assertTrue(buttons[1].inside(lambda n: n.tag == "details"))
        self.assertTrue(all(ARABIC.search(name) for name in names["ar"]))
        self.assertFalse(any(ARABIC.search(name) for name in names["en"]))
        key = re.search(r'var KEY = "([^"]+)";', (self.out / "site.js").read_text(encoding="utf-8")).group(1)
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

    def test_the_numbers_are_the_docs(self):
        """The table's word error rates and English kept are docs/03-results.md's, model by model."""
        docs = (ROOT / "docs" / "03-results.md").read_text(encoding="utf-8").split("## Public benchmark: Perle at 16 kHz")[1].split("\n## ")[0]
        rows = {"Cohere Transcribe Arabic": "Cohere Transcribe Arabic (", "whisper-medium code-switching": "whisper-medium code-switching fine-tune",
                "Whisper large-v3": "Whisper large-v3 + style hint"}
        for lang, home in self.homes.items():
            for tr in home.find(lambda n: n.tag == "tr" and n.find(lambda c: "model" in c.classes())):
                (model,) = tr.find(lambda n: "model" in n.classes())
                wer, kept = (n.text() for n in tr.find(lambda n: "fig" in n.classes()))
                with self.subTest(lang, model=model.text()):
                    (line,) = [x for x in docs.splitlines() if x.startswith("| " + rows[model.text()])]
                    cells = [c.strip() for c in line.strip("|").split("|")]
                    self.assertEqual(cells[1].split()[0], wer)
                    self.assertEqual(cells[5], kept)
                    (bar,) = tr.find(lambda n: "bar" in n.classes())
                    self.assertEqual(bar.attrs["style"], f"--v: {kept[:-1]}")

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

    def test_the_version_is_the_apps(self):
        source = (ROOT / "app" / "__init__.py").read_text(encoding="utf-8")
        version = re.search(r'__version__ = "([^"]+)"', source).group(1)
        for lang, home in self.homes.items():
            (fine,) = home.find(lambda n: "fine" in n.classes())
            self.assertTrue(squash(fine.text()).endswith(VERSION_IN[lang].format(version)), fine.text())

    def test_the_first_view_stays_small(self):
        fonts = sorted(set(re.findall(r'url\("(fonts/[^"]+\.woff2)"\)', self.css)))
        for page in HOMES.values():
            files = [page, "styles.css", "site.js", "brand/sedjem-logo-colour.svg", "brand/sedjem-logo-colour-dark.svg", *fonts]
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
        self.assertEqual(published, sorted([*build.PAGES, *build.COPIED, *build.FILES, *fonts, *captures]))
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
        sources = ["site/index.html", "site/styles.css", "site/build.py", "site/copy/ar.toml", "docs/images/make/content.py",
                   "app/__init__.py", *build.FILES.values(),
                   *(f"{build.FONTS}/{Path(f).name}" for f in re.findall(r'url\("(fonts/[^"]+)"\)', self.css))]
        for source in sources:
            with self.subTest(source):
                self.assertTrue(any(fnmatch.fnmatch(source, p) for p in patterns))
        for line in ("pages: write", "id-token: write", "group: pages", "workflow_dispatch:"):
            self.assertIn(line, workflow)

    def test_readme_links_the_site_near_the_top(self):
        top = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()[:25]
        self.assertTrue(any(build.URL in line for line in top))

    def test_build_uses_only_the_standard_library(self):
        tree = ast.parse((ROOT / "site" / "build.py").read_text(encoding="utf-8"))
        modules = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        modules |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertLessEqual({m.split(".")[0] for m in modules}, set(sys.stdlib_module_names))


if __name__ == "__main__":
    unittest.main()
