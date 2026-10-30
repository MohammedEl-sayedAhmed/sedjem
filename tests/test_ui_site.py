"""Browser tests for the website (site/build.py): both pages, built into a temporary folder and served on a free
port, in a headless Chromium at a phone's width up to a desktop's. Nothing may scroll sideways, nothing may load
from another site, no script may fail, and the hero's lines must all arrive, or be there at once with less motion
or without scripts. The theme on the page's attribute wins over the system's, the reader's stored pick is on the
page before it is drawn, the theme button switches to the other theme, its icon the one on show, the pictures of the
app follow the pick and only the ones on show are fetched, and without scripts the page follows the system, the
phone's menu still opens and the download is Windows'. The pictures of the app are never drawn smaller than their
own size, from a 320 px phone to a desktop; the speakers' names are in their colours, which read on the card in both
themes; the commands fade at the edge where more of them is to the side, and the subtitles are left out on a phone.
The grid of numbers, built from tests/site_support.py's made-up stats and totals and again without any, fits every
width from a phone's to a desktop's on both pages, its dates never touching and never smaller, and is whole without
scripts; with them, a chart tells its points on hover and from the keyboard. The downloads are the made-up latest
release's files, Linux's on a Linux computer. These were checks by hand; tests/test_site.py has the ones that need
no browser.

The anonymous counts are checked on a second build that counts on 127.0.0.1, with a stand-in key: one page_view for
each page opened, with the right values, as the one request off this computer; a download for each download link;
nothing with Do Not Track or Global Privacy Control, nothing from the ordinary build on this computer, and no cookie
or storage. Nothing reaches Aptabase or any other site: every request off this computer is answered in the browser
(route), and Aptabase's names resolve to nowhere in this browser in case one ever got past.

The browser comes from tests/ui_support.py: the tests skip without Playwright or a browser, unless
SEDJEM_UI_REQUIRED=1 (as CI sets it).
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui_site.py" -v
"""
import functools
import importlib.util
import json
import re
import struct
import tempfile
import threading
import time
import unittest
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import site_support as fixture
from ui_support import REQUIRED, launch, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("site_build", ROOT / "site" / "build.py")
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)

PAGES = ("/", "/ar/")
WIDTHS = (320, 390, 768, 1024, 1440)
KEY = "sedjem-site-theme"  # where site.js keeps the reader's pick of theme
BACKGROUND = {"light": "rgb(244, 245, 247)", "dark": "rgb(15, 18, 22)"}  # the page's --bg in each theme
# The theme and colour scheme the page had when its body began, recorded by the time the body was inserted: both are
# on before the page can first be drawn
THEME_AT_BODY = """new MutationObserver(function (records, observer) {
  if (!document.body) return;
  var root = document.documentElement;
  window.themeAtBody = [root.getAttribute("data-theme"), root.style.colorScheme];
  observer.disconnect();
}).observe(document, {childList: true, subtree: true});"""
ENDPOINT = "https://eu.aptabase.com/api/v0/event"
STAND_IN_KEY = "A-EU-0000000000"  # the counting build's key here, not the website's
NO_APTABASE = "--host-resolver-rules=MAP aptabase.com ~NOTFOUND, MAP *.aptabase.com ~NOTFOUND"
HOLD = """document.addEventListener("click", function (e) {
  if (e.target.closest("a[href^='https:']")) e.preventDefault();
}, true);"""  # a link to another site stays on the page, so one page load can take several clicks
PRIVACY = {"Do Not Track": 'Object.defineProperty(navigator, "doNotTrack", {get: () => "1"});',
           "window.doNotTrack": 'window.doNotTrack = "1";',
           "Global Privacy Control": 'Object.defineProperty(navigator, "globalPrivacyControl", {get: () => true});'}
OS_NAMES = ("Windows", "macOS", "Linux", "Android", "iOS", "other")


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class SiteInBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            if REQUIRED:
                raise RuntimeError("Playwright is not installed: pip install -r requirements-dev.txt")
            raise unittest.SkipTest("Playwright is not installed (pip install -r requirements-dev.txt)")
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)  # with every number in the section of numbers
        cls.out = build.build(tmp / "site", stats=fixture.write_stats(tmp / "stats"), usage=fixture.write_usage(tmp / "usage.json"),
                              latest=fixture.write_release(tmp / "release"))
        # the same site counting on this computer's address, with a stand-in key
        cls.counting = build.build(tmp / "counting", counts_key=STAND_IN_KEY, counts_host="127.0.0.1")
        # and without numbers: no stats, and no totals yet
        cls.plain = build.build(tmp / "plain", usage=tmp / "no-usage.json")
        cls.servers = [ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(out)))
                       for out in (cls.out, cls.counting, cls.plain)]
        for server in cls.servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
        cls.base, cls.counting_base, cls.plain_base = (f"http://127.0.0.1:{server.server_address[1]}" for server in cls.servers)
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = launch(cls.playwright, args=[NO_APTABASE])
        except BaseException:
            cls.stop()
            raise

    @classmethod
    def stop(cls):
        cls.playwright.stop()
        for server in cls.servers:
            server.shutdown()
            server.server_close()
        cls.tmp.cleanup()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.stop()

    def open(self, path, width, motion="reduce", system="light", stored=None, scripts=True, platform=None, base=None,
             scripts_before=(), locale=None, referer=None):
        """A page at a width, with every request and error it causes recorded: the system's theme as given, the
        reader's pick of theme stored before the page loads, and the computer's system as the page asks for it;
        base is the counting build's address to open that one, and scripts_before run before the page's own. Every
        request off this computer is answered here: a count gets what Aptabase answers and is kept in page.sent,
        and anything else (a download link's page) an empty page."""
        base = base or self.base
        context = self.browser.new_context(viewport={"width": width, "height": 800}, reduced_motion=motion,
                                           color_scheme=system, java_script_enabled=scripts, locale=locale)
        self.addCleanup(context.close)
        if stored:
            context.add_init_script(f"localStorage.setItem({KEY!r}, {stored!r});")
        if platform:
            context.add_init_script(f"""Object.defineProperty(navigator, "userAgentData", {{get: () => ({{platform: {platform!r}}})}});
                Object.defineProperty(navigator, "platform", {{get: () => {platform!r}}});""")
        for script in scripts_before:
            context.add_init_script(script)
        context.add_init_script(THEME_AT_BODY)
        sent = []

        def elsewhere(route):
            request = route.request
            if request.url == ENDPOINT and request.method == "POST":
                sent.append((request.headers, json.loads(request.post_data)))
                route.fulfill(status=200, json={}, headers={"Access-Control-Allow-Origin": "*"})
            else:
                route.fulfill(status=200, content_type="text/html", body="<!doctype html><title>Elsewhere</title>")
        context.route(lambda url: urlsplit(url).hostname != "127.0.0.1", elsewhere)
        page = context.new_page()
        page.outside, page.errors, page.requests, page.sent = [], [], [], sent
        page.on("request", lambda r: page.requests.append(r.url) if r.url.startswith(base) else page.outside.append(r.url))
        page.on("pageerror", lambda e: page.errors.append(str(e)))
        page.on("console", lambda m: page.errors.append(m.text) if m.type == "error" else None)
        page.goto(base + path, wait_until="load", referer=referer)
        return page

    def counts(self, page, n):
        """The n events a page has sent, once it has sent them, and after a moment in which no more came."""
        deadline = time.monotonic() + 10
        while len(page.sent) < n and time.monotonic() < deadline:
            page.wait_for_timeout(50)
        page.wait_for_timeout(300)
        self.assertEqual(len(page.sent), n, [event for _, event in page.sent])
        return [event for _, event in page.sent]

    def test_nothing_scrolls_sideways_or_loads_from_another_site(self):
        for path in PAGES:
            for width in WIDTHS:
                with self.subTest(path, width=width):
                    page = self.open(path, width)
                    # the images below the first screen load too, so their requests are seen
                    page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
                    page.wait_for_load_state("networkidle")
                    self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                    self.assertEqual(page.outside, [])
                    self.assertEqual(page.errors, [])
        # the build that counts here sends one thing off this computer, its page view, and only to Aptabase's EU host
        for path in PAGES:
            with self.subTest(path, counting=True):
                page = self.open(path, 1440, base=self.counting_base)
                page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
                page.wait_for_load_state("networkidle")
                self.counts(page, 1)
                self.assertEqual(page.outside, [ENDPOINT])
                self.assertEqual(urlsplit(ENDPOINT).hostname, "eu.aptabase.com")
                self.assertEqual(page.errors, [])

    def test_the_ear_and_the_transcript_sit_on_the_grid(self):
        """At 1440 by 900 the ear's top is the headline's and its end edge the header's, on the start side of the
        Arabic page, and the transcript's top shows in the first screen, 470 to 550 px down. Below 1000 px the ear is
        smaller and over the headline: 80 to 120 px tall, its end edge the content's, clear of the header and the
        headline, the same gap above the headline at every width, and the headline as wide as the content."""
        box = """() => Object.fromEntries(['h1', '.ear', '.site-header', '.hero-grid', '.hero-figure .frame'].map(s => {
            const b = document.querySelector(s).getBoundingClientRect(); return [s, [b.left, b.top, b.right, b.bottom]]; }))"""
        for path in PAGES:
            end = 2 if path == "/" else 0  # the end edge: the right in English, the left in Arabic
            with self.subTest(path):
                page = self.open(path, 1440)
                b = {k: [round(v) for v in edges] for k, edges in page.evaluate(box).items()}
                self.assertEqual(b[".ear"][1], b["h1"][1])
                self.assertEqual(b[".ear"][end], b[".site-header"][end])
                self.assertTrue(470 <= b[".hero-figure .frame"][1] <= 550, b)
            gaps = []
            for width in (320, 390, 768, 999):
                with self.subTest(path, width=width):
                    page = self.open(path, width)
                    self.assertTrue(page.locator(".ear").is_visible())
                    b = page.evaluate(box)
                    ear, h1, header, content = b[".ear"], b["h1"], b[".site-header"], b[".hero-grid"]
                    self.assertTrue(80 <= ear[3] - ear[1] <= 120, ear)
                    self.assertAlmostEqual(ear[end], content[end], delta=0.5)
                    self.assertGreaterEqual(ear[1], header[3])  # under the header
                    self.assertLessEqual(ear[3], h1[1])  # and over the headline, clear of both
                    self.assertAlmostEqual(h1[2] - h1[0], content[2] - content[0], delta=0.5)
                    gaps.append(h1[1] - ear[3])
            self.assertLess(max(gaps) - min(gaps), 0.5, gaps)
            self.assertGreater(min(gaps), 0)

    def test_the_pictures_hold_their_place_before_they_load(self):
        """Every picture of the app keeps its box before it arrives (its width and height, or its phone form's, are in
        the markup), so the page doesn't move as they load."""
        boxes = """() => [document.documentElement.scrollHeight, ...Array.from(document.querySelectorAll('.frame img'))
            .filter(i => i.getClientRects().length).map(i => [Math.round(i.getBoundingClientRect().width), Math.round(i.getBoundingClientRect().height)])]"""
        for path in PAGES:
            for width in (390, 1440):
                with self.subTest(path, width=width):
                    page = self.open(path, width)
                    # the fonts first: the Arabic face has no stand-in drawn to its metrics, so its arrival may move
                    # the page by a few pixels, which isn't what this checks
                    page.evaluate("document.fonts.ready.then(() => true)")
                    before = page.evaluate(boxes)
                    self.assertEqual(len(before), 1 + 5)
                    self.assertTrue(all(w > 100 and h > 50 for w, h in before[1:]), before)
                    for img in page.locator(".frame img").all():  # each comes near, so each loads
                        if img.is_visible():
                            img.scroll_into_view_if_needed()
                    page.wait_for_function("Array.from(document.querySelectorAll('.frame img')).filter(i => i.getClientRects().length).every(i => i.complete && i.naturalWidth)")
                    self.assertEqual(page.evaluate(boxes), before)

    def test_the_lines_all_arrive_or_are_there_at_once(self):
        visible = """() => Array.from(document.querySelectorAll('[data-reveal] .row'))
            .filter(e => getComputedStyle(e).opacity === '1').length"""
        for path in PAGES:
            with self.subTest(path, motion="full"):
                page = self.open(path, 1440, motion="no-preference")
                page.wait_for_function("document.getAnimations().length === 0", timeout=10_000)
                self.assertEqual(page.evaluate(visible), 6)
                self.assertEqual(page.errors, [])
            # with less motion asked for, or without scripts, every line is there from the start
            for motion, scripts in (("reduce", True), ("no-preference", False)):
                with self.subTest(path, motion=motion, scripts=scripts):
                    page = self.open(path, 1440, motion=motion, scripts=scripts)
                    rows = page.locator("[data-reveal] .row")
                    self.assertEqual(rows.count(), 6)
                    self.assertEqual(rows.evaluate_all("rows => rows.filter(e => getComputedStyle(e).opacity === '1').length"), 6)
                    if scripts:
                        self.assertEqual(page.evaluate("document.getAnimations().length"), 0)

    def test_the_download_is_for_the_readers_system(self):
        for path in PAGES:
            for platform, scripts, shown in ((None, False, "windows"), ("Linux x86_64", True, "linux"),
                                             ("Windows", True, "windows"), ("macOS", True, "windows")):
                with self.subTest(path, platform=platform, scripts=scripts):
                    page = self.open(path, 1440, scripts=scripts, platform=platform)
                    labels = page.locator(".actions [data-os]")
                    on_show = labels.evaluate_all("ls => ls.filter(l => l.getClientRects().length).map(l => l.dataset.os)")
                    self.assertEqual(on_show, [shown, shown])

    def test_the_phone_menu_opens_without_scripts(self):
        for path in PAGES:
            with self.subTest(path):
                page = self.open(path, 390, scripts=False)
                menu = page.locator("details.menu")
                self.assertTrue(page.locator(".get-short").is_visible())
                self.assertFalse(menu.locator("nav a").first.is_visible())
                menu.locator("summary").click()
                self.assertEqual(menu.locator("nav a").evaluate_all("as => as.filter(a => a.getClientRects().length).length"), 3)
                self.assertFalse(menu.locator("[data-theme-switch]").is_visible())  # the theme needs the script
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), 390)

    def theme(self, page):
        """The theme on the page's attribute (None when it follows the system), the colour it is drawn in, and the
        logo on show."""
        return page.evaluate("""() => [document.documentElement.getAttribute("data-theme"),
            getComputedStyle(document.body).backgroundColor,
            Array.from(document.querySelectorAll(".brand img")).filter(i => getComputedStyle(i).display !== "none")
              .map(i => i.getAttribute("src").split("/").pop())]""")

    def test_the_attribute_wins_over_the_systems_theme(self):
        for system in ("light", "dark"):
            for theme in ("light", "dark"):
                with self.subTest(system=system, theme=theme):
                    page = self.open("/", 1024, system=system)
                    page.evaluate(f"document.documentElement.setAttribute('data-theme', {theme!r})")
                    logo = "sedjem-logo-colour-dark.svg" if theme == "dark" else "sedjem-logo-colour.svg"
                    self.assertEqual(self.theme(page), [theme, BACKGROUND[theme], [logo]])

    def test_the_stored_pick_is_on_before_the_page_is_drawn(self):
        for path in PAGES:
            for stored, system in (("light", "dark"), ("dark", "light")):
                with self.subTest(path, stored=stored, system=system):
                    page = self.open(path, 390, system=system, stored=stored)
                    self.assertEqual(page.evaluate("window.themeAtBody"), [stored, stored])
                    self.assertEqual(self.theme(page)[:2], [stored, BACKGROUND[stored]])
                    self.assertEqual(page.errors, [])

    def test_the_button_switches_to_the_other_theme(self):
        """With the system light and with it dark: the button's icon is the theme on show and its name says what a press
        does; a press switches the page and the icon to the other theme and keeps the pick; a press back to the
        system's theme clears it, so the page follows the system again; and a pick kept is on the page before it is
        drawn after a reload. In the header's row from 700 px, and in the phone's menu, where its name shows too."""
        for path in PAGES:
            for width in (1440, 390):
                for system in ("light", "dark"):
                    with self.subTest(path, width=width, system=system):
                        other = "dark" if system == "light" else "light"
                        page = self.open(path, width, system=system)
                        button = self.theme_button(page, width)
                        name = {to: button.get_attribute(f"data-to-{to}") for to in ("dark", "light")}
                        self.assertEqual(self.switch(page, button), [None, BACKGROUND[system], ["icon-" + other], name[other], None])
                        button.click()
                        self.assertEqual(self.switch(page, button), [other, BACKGROUND[other], ["icon-" + system], name[system], other])
                        button.click()  # back to the system's theme: the page follows the system again
                        self.assertEqual(self.switch(page, button), [None, BACKGROUND[system], ["icon-" + other], name[other], None])
                        button.click()
                        page.reload()
                        self.assertEqual(page.evaluate("window.themeAtBody"), [other, other])
                        button = self.theme_button(page, width)
                        self.assertEqual(self.switch(page, button), [other, BACKGROUND[other], ["icon-" + system], name[system], other])
                        self.assertEqual(page.errors, [])

    def test_the_button_follows_the_system_while_nothing_is_picked(self):
        """Following the system, the page, the button's icon and its name change with the system's theme while the page
        is open; a picked theme stays."""
        page = self.open("/", 1440, system="light")
        button = self.theme_button(page, 1440)
        name = {to: button.get_attribute(f"data-to-{to}") for to in ("dark", "light")}
        # the browser tells the page of a change of the system's theme when it next draws it
        drawn = "() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done)))"
        page.emulate_media(color_scheme="dark")
        page.evaluate(drawn)
        self.assertEqual(self.switch(page, button), [None, BACKGROUND["dark"], ["icon-light"], name["light"], None])
        button.click()  # to light, now not the system's: kept
        page.emulate_media(color_scheme="light")
        page.evaluate(drawn)
        self.assertEqual(self.switch(page, button), ["light", BACKGROUND["light"], ["icon-dark"], name["dark"], "light"])
        self.assertEqual(page.errors, [])

    def theme_button(self, page, width):
        """The theme button on show: in the header's row from 700 px, in the phone's menu, opened, below."""
        if width < 700:
            page.locator("details.menu summary").click()
        return page.locator(".theme-wide" if width >= 700 else ".theme-menu")

    def switch(self, page, button):
        """The theme on the page's attribute (None when it follows the system), the colour it is drawn in, the icon
        on show in the button, the button's name (its title, and in the menu its name on show, the same), and the pick
        kept."""
        name = button.get_attribute("aria-label")
        self.assertEqual(button.get_attribute("title"), name)
        if "theme-menu" in (button.get_attribute("class") or ""):
            self.assertEqual(button.locator(".theme-name").text_content(), name)
        return self.theme(page)[:2] + [
            button.evaluate("b => Array.from(b.querySelectorAll('svg')).filter(s => getComputedStyle(s).display !== 'none')"
                            ".map(s => s.getAttribute('class'))"),
            name, page.evaluate(f"localStorage.getItem({KEY!r})")]

    def test_the_pictures_follow_the_picked_theme(self):
        """Dark picked on a system in light, with the button or kept from before: the pictures of the app on show are
        the dark ones. Kept from before, the light ones are hidden from the start and never fetched (picked with the
        button, the light ones were on show until then, and may have been)."""
        for path in PAGES:
            for picked in ("button", "stored"):
                with self.subTest(path, picked=picked):
                    page = self.open(path, 1440, system="light", stored="dark" if picked == "stored" else None)
                    if picked == "button":
                        page.locator(".theme-wide").click()
                    self.assertEqual(self.theme(page)[0], "dark")
                    for img in page.locator(".frame img").all():  # each comes near, so each loads
                        if img.is_visible():
                            img.scroll_into_view_if_needed()
                    page.wait_for_load_state("networkidle")
                    shown = page.evaluate("""() => Array.from(document.querySelectorAll('.frame img'))
                        .filter(i => i.getClientRects().length).map(i => [i.currentSrc.split('/').pop(), i.naturalWidth > 0])""")
                    self.assertEqual(len(shown), 5)
                    self.assertTrue(all(name.endswith("-wide-dark.webp") and loaded for name, loaded in shown), shown)
                    if picked == "stored":
                        self.assertEqual([url for url in page.requests if re.search(r"-light\.(?:png|webp)$", url)], [])
                    self.assertEqual(page.errors, [])

    def test_the_pictures_are_never_drawn_smaller_than_their_own_size(self):
        """At a phone's width and a desktop's, and at the widths between where the layout changes, every picture of the
        app on show is drawn at no less than its own size: its pixels over the density it was taken at, one css pixel
        of the picture to one of the page or more, in both themes. The browser takes the WebP."""
        density = {"wide": 2, "narrow": 3}
        sizes = {p.name: struct.unpack(">II", p.read_bytes()[16:24]) for p in (self.out / "captures").glob("*.png")}
        drawn = """() => Array.from(document.querySelectorAll('.frame img')).filter(i => i.getClientRects().length)
            .map(i => [i.currentSrc.split('/').pop(), i.getBoundingClientRect().width, i.getBoundingClientRect().height])"""
        for path in PAGES:
            for width in (320, 390, 699, 700, 749, 750, 999, 1000, 1024, 1279, 1280, 1440):
                for system in ("light", "dark") if width in (320, 390, 1440) else ("light",):
                    with self.subTest(path, width=width, system=system):
                        page = self.open(path, width, system=system)
                        for img in page.locator(".frame img").all():
                            if img.is_visible():
                                img.scroll_into_view_if_needed()
                        page.wait_for_function("Array.from(document.querySelectorAll('.frame img')).filter(i => i.getClientRects().length).every(i => i.complete && i.naturalWidth)")
                        shown = page.evaluate(drawn)
                        self.assertEqual(len(shown), 5)
                        for name, w, h in shown:
                            form = "narrow" if "-narrow-" in name else "wide"
                            self.assertEqual(form, "narrow" if width < 750 else "wide", name)
                            self.assertIn(f"-{system}.webp", name)
                            pixels = sizes[name.replace(".webp", ".png")]
                            self.assertGreaterEqual(w, pixels[0] / density[form] - 0.01, name)
                            self.assertGreaterEqual(h, pixels[1] / density[form] - 0.01, name)
                        self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)

    def test_the_speakers_names_read_in_both_themes(self):
        """The hero's names are drawn in their speakers' colours, bold, with nothing before them, and each colour is
        4.5:1 or more on the transcript's card, in light and in dark, on both pages."""
        def luminance(rgb):
            r, g, b = [c / 255 for c in map(int, re.findall(r"\d+", rgb)[:3])]
            r, g, b = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
            return 0.2126 * r + 0.7152 * g + 0.0722 * b
        names = """() => Array.from(document.querySelectorAll('.log .row')).map(row => { const who = row.querySelector('.who'),
            css = getComputedStyle(who), probe = document.createElement('i');
          probe.style.color = getComputedStyle(row).getPropertyValue('--c'); row.append(probe);
          const c = getComputedStyle(probe).color; probe.remove();
          return [css.color, c, css.fontWeight, parseFloat(css.fontSize), getComputedStyle(who, '::before').content,
                  getComputedStyle(document.querySelector('.log')).backgroundColor, getComputedStyle(document.body).color]; })"""
        for path in PAGES:
            for system in ("light", "dark"):
                for width in (390, 1440):
                    with self.subTest(path, system=system, width=width):
                        page = self.open(path, width, system=system)
                        rows = page.evaluate(names)
                        self.assertEqual(len(rows), 6)
                        self.assertGreaterEqual(len({row[0] for row in rows}), 3)  # Mona, Karim and Omar apart
                        for colour, speaker, weight, size, before, card, ink in rows:
                            self.assertEqual(colour, speaker)
                            self.assertNotEqual(colour, ink)
                            self.assertEqual(weight, "600")
                            self.assertLess(size, 18.66)  # not large text, so 4.5:1 is the bar
                            self.assertEqual(before, "none")
                            high, low = sorted((luminance(colour), luminance(card)), reverse=True)
                            self.assertGreaterEqual((high + 0.05) / (low + 0.05), 4.5, colour)

    def test_the_commands_fade_where_they_scroll_and_the_subtitles_leave_the_phone(self):
        """On a phone the commands scroll sideways in a named box the keyboard can reach, faded at the end edge as the
        cue, with the focus ring on the box around them; at the end of the scroll the fade is over the padding only.
        The hero's subtitles, which would wrap raggedly there, are left out, and so are the caption's words on them."""
        for path in PAGES:
            with self.subTest(path):
                page = self.open(path, 390)
                code = page.locator("pre.code")
                self.assertEqual(code.get_attribute("tabindex"), "0")
                self.assertTrue(code.get_attribute("aria-label"))
                self.assertGreater(code.evaluate("e => e.scrollWidth - e.clientWidth"), 40)
                self.assertIn("linear-gradient", code.evaluate("e => getComputedStyle(e).maskImage || getComputedStyle(e).webkitMaskImage"))
                fade, padding = code.evaluate("e => [32, parseFloat(getComputedStyle(e).paddingRight)]")
                self.assertGreaterEqual(padding, fade)
                code.evaluate("e => e.closest('.way').querySelector('.docs a').focus()")
                page.keyboard.press("Shift+Tab")
                self.assertEqual(page.evaluate("document.activeElement.tagName"), "PRE")
                self.assertEqual(page.locator(".code-box").evaluate("e => getComputedStyle(e).outlineStyle"), "solid")
                self.assertFalse(page.locator(".srt").is_visible())
                self.assertFalse(page.locator(".with-srt").is_visible())
                page = self.open(path, 768)
                self.assertTrue(page.locator(".srt").is_visible())
                self.assertTrue(page.locator(".with-srt").is_visible())

    def test_without_scripts_the_page_follows_the_system(self):
        """Without scripts the page follows the system, and the theme button, which needs them, isn't shown, in the
        header's row or in the phone's menu."""
        for system in ("light", "dark"):
            for width in (390, 1440):
                with self.subTest(system=system, width=width):
                    page = self.open("/ar/", width, system=system, scripts=False)
                    self.assertEqual(page.locator("body").evaluate("b => getComputedStyle(b).backgroundColor"),
                                     BACKGROUND[system])
                    if width < 700:
                        page.locator("details.menu summary").click()
                    self.assertEqual(page.locator("[data-theme-switch]").evaluate_all(
                        "bs => bs.filter(b => b.getClientRects().length).length"), 0)

    def test_each_page_opened_sends_one_page_view(self):
        """One page_view each time a page is opened, to Aptabase's EU address with the key, from a fixed list of values
        but the referrer, which is the linking site's host without www.; with the browser's language, the system in
        one word, the version the page offers and a session number of the moment, new for each page opened."""
        version = build.version()
        for path, width, stored, platform, referer, expected in (
                ("/", 390, None, "Windows", None, {"page": "en", "device": "phone", "theme": "system", "referrer": "none"}),
                ("/ar/", 768, "dark", "macOS", "https://www.github.com/MohammedEl-sayedAhmed/sedjem",
                 {"page": "ar", "device": "tablet", "theme": "dark", "referrer": "github.com"}),
                ("/", 1440, "light", "Linux x86_64", "https://news.ycombinator.com/item?id=1",
                 {"page": "en", "device": "desktop", "theme": "light", "referrer": "news.ycombinator.com"}),
                ("/", 1099, None, None, None, {"page": "en", "device": "tablet", "theme": "system", "referrer": "none"})):
            with self.subTest(path, width=width, platform=platform):
                page = self.open(path, width, base=self.counting_base, stored=stored, platform=platform,
                                 locale="ar-EG", referer=referer)
                (event,) = self.counts(page, 1)
                headers = page.sent[0][0]
                self.assertEqual((headers.get("app-key"), headers.get("content-type")), (STAND_IN_KEY, "application/json"))
                self.assertNotIn("cookie", headers)
                self.assertNotIn("referer", headers)
                self.assertEqual(list(event), ["timestamp", "sessionId", "eventName", "systemProps", "props"])
                self.assertEqual(event["eventName"], "page_view")
                self.assertEqual(event["props"], expected)
                os_name = {"Windows": "Windows", "macOS": "macOS", "Linux x86_64": "Linux"}.get(platform)
                self.assertEqual(event["systemProps"], {"locale": "ar-EG", "appVersion": version, "isDebug": False,
                                                        "sdkVersion": "sedjem-site@1",
                                                        "osName": os_name or event["systemProps"]["osName"]})
                self.assertIn(event["systemProps"]["osName"], OS_NAMES)
                self.assertRegex(event["timestamp"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
                self.assertLess(abs(datetime.fromisoformat(event["timestamp"]).timestamp() - time.time()), 60)
                self.assertRegex(event["sessionId"], r"^\d{18}$")
                self.assertLess(abs(int(event["sessionId"][:10]) - time.time()), 60)
                self.assertEqual(page.outside, [ENDPOINT])
                self.assertEqual(page.errors, [])
        # opened again, or the other page from this one: a new session number, and a link from the site itself
        # counts as none
        page = self.open("/", 1440, base=self.counting_base)
        page.reload()
        page.locator(".nav-wide .other-lang").click()
        page.wait_for_url("**/ar/")
        first, again, other = self.counts(page, 3)
        self.assertEqual([e["props"]["page"] for e in (first, again, other)], ["en", "en", "ar"])
        self.assertEqual(other["props"]["referrer"], "none")
        self.assertEqual(len({e["sessionId"] for e in (first, again, other)}), 3)
        self.assertEqual(page.errors, [])

    def test_the_404_page_counts_too(self):
        """The page for a missing address sends its page_view as the others do, as page 404."""
        page = self.open("/", 1024, base=self.counting_base, stored="dark")
        # the 404 page finds the site's files under /sedjem/, as GitHub Pages serves them; here they are at the top
        page.route(self.counting_base + "/sedjem/**",
                   lambda route: route.continue_(url=route.request.url.replace("/sedjem/", "/", 1)))
        page.goto(self.counting_base + "/404.html", wait_until="load")
        view, missing = self.counts(page, 2)
        self.assertEqual(missing["props"], {"page": "404", "device": "tablet", "theme": "dark", "referrer": "none"})
        self.assertNotEqual(missing["sessionId"], view["sessionId"])
        self.assertEqual(page.errors, [])

    def test_a_download_click_sends_a_download(self):
        """Each download link sends one download with its platform and place, once a page load, and the session
        number of the page's page_view; the hero's is the system on its label. A download that leaves the page is
        still counted."""
        for path, platform, hero in (("/", "Windows", "windows"), ("/ar/", "Linux x86_64", "linux")):
            with self.subTest(path, platform=platform):
                page = self.open(path, 1440, base=self.counting_base, platform=platform, scripts_before=[HOLD])
                lang = "en" if path == "/" else "ar"
                for selector in (".actions [data-download]", ".actions [data-download]", '[data-download="windows"]',
                                 '[data-download="linux"]', '[data-download="source"]'):
                    page.locator(selector).click()
                view, *downloads = self.counts(page, 5)
                self.assertEqual(view["eventName"], "page_view")
                self.assertEqual([(e["eventName"], e["props"]) for e in downloads],
                                 [("download", {"platform": p, "place": where, "page": lang})
                                  for p, where in ((hero, "hero"), ("windows", "get"), ("linux", "get"), ("source", "get"))])
                self.assertEqual({e["sessionId"] for e in downloads}, {view["sessionId"]})
                self.assertEqual(page.errors, [])
        page = self.open("/", 1440, base=self.counting_base)
        page.locator('[data-download="linux"]').click()
        page.wait_for_url("https://github.com/**")
        view, download = self.counts(page, 2)
        self.assertEqual(download["props"], {"platform": "linux", "place": "get", "page": "en"})
        releases = "https://github.com/MohammedEl-sayedAhmed/sedjem/releases/latest"
        self.assertEqual(sorted(page.outside), sorted([ENDPOINT, ENDPOINT, releases]))

    def test_do_not_track_and_global_privacy_control_send_nothing(self):
        """With Do Not Track (navigator's or window's) or Global Privacy Control on, neither a page view nor a download
        is sent, and nothing at all leaves the page."""
        for name, script in PRIVACY.items():
            with self.subTest(name):
                page = self.open("/", 1440, base=self.counting_base, scripts_before=[script, HOLD])
                page.locator('[data-download="windows"]').click()
                self.counts(page, 0)
                self.assertEqual(page.outside, [])
                self.assertEqual(page.errors, [])
        # and Do Not Track set to 0 counts as usual
        page = self.open("/", 1440, base=self.counting_base,
                         scripts_before=['Object.defineProperty(navigator, "doNotTrack", {get: () => "0"});'])
        self.counts(page, 1)

    def test_the_ordinary_build_sends_nothing_here(self):
        """The site as published counts only on its own host, so opened on this computer it sends nothing, page view
        or download."""
        for path in PAGES:
            with self.subTest(path):
                page = self.open(path, 1440, scripts_before=[HOLD])
                self.assertEqual(page.evaluate("document.querySelector('script[data-key]').dataset.host"), build.COUNTS_HOST)
                page.locator('[data-download="windows"]').click()
                page.locator(".actions [data-download]").click()
                self.counts(page, 0)
                self.assertEqual(page.outside, [])
                self.assertEqual(page.errors, [])

    def test_the_grid_fits_from_a_phone_to_a_desktop(self):
        """The grid of numbers from 320 to 1440 px on both pages, with every number and without any, with and without
        scripts: nothing in it reaches past its card or the column, nothing overflows its own box, and the page doesn't
        scroll sideways, and every number is there without scripts. From 1000 px the figure cards stand three to a row,
        all of a width, and the counts' month across a row of its own; below, one under the other."""
        boxes = """() => { const sec = document.querySelector('section[aria-labelledby="numbers"]');
            const column = sec.querySelector('.wrap').getBoundingClientRect();
            const out = [];
            sec.querySelectorAll('.card').forEach(card => {
              const c = card.getBoundingClientRect();
              if (c.left < column.left - 0.5 || c.right > column.right + 0.5) out.push('card ' + card.id);
              card.querySelectorAll('h3, h4, p, span, li, data, svg, .split, .rank, .ticks').forEach(e => {
                if (!e.getClientRects().length || e.closest('.sr-only')) return;
                const b = e.getBoundingClientRect();
                if (b.left < c.left - 0.5 || b.right > c.right + 0.5) out.push(card.id + ' ' + e.tagName + ' ' + e.textContent.trim().slice(0, 30));
                if (e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflow !== 'visible') out.push('overflows ' + e.textContent.trim().slice(0, 30));
              });
              // the dates along a chart: none on another, and none smaller than the tick size
              card.querySelectorAll('.ticks').forEach(ticks => {
                const labels = Array.from(ticks.querySelectorAll('.tick')).filter(t => t.getClientRects().length && getComputedStyle(t).display !== 'none')
                  .map(t => t.firstElementChild.getBoundingClientRect()).sort((a, b) => a.left - b.left);
                labels.forEach((b, i) => { if (i && b.left < labels[i - 1].right + 4) out.push(card.id + ' dates touch'); });
                ticks.querySelectorAll('.tick').forEach(t => { if (parseFloat(getComputedStyle(t).fontSize) < 12) out.push('small date'); });
              });
            });
            const tops = Array.from(sec.querySelectorAll('.card')).map(c => [c.id, Math.round(c.getBoundingClientRect().top), Math.round(c.getBoundingClientRect().width)]);
            return {out, figures: sec.querySelectorAll('.card data').length, soon: sec.querySelectorAll('.soon').length, tops}; }"""
        for base, figures, soon in ((self.base, 12, 0), (self.plain_base, 0, 1)):
            for path in PAGES:
                for width in (320, 390, 768, 1024, 1280, 1440):
                    for scripts in (True, False) if width in (390, 1440) else (True,):
                        with self.subTest(path, width=width, numbers=bool(figures), scripts=scripts):
                            page = self.open(path, width, base=base, scripts=scripts)
                            page.locator('section[aria-labelledby="numbers"]').scroll_into_view_if_needed()
                            page.evaluate("document.fonts.ready.then(() => true)")
                            found = page.evaluate(boxes)
                            self.assertEqual(found["out"], [])
                            self.assertEqual((found["figures"], found["soon"]), (figures, soon))
                            self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth"), width)
                            if figures:
                                rows = {}
                                for key, top, card_width in found["tops"]:
                                    rows.setdefault(top, []).append((key, card_width))
                                if width >= 1000:
                                    self.assertEqual([[k for k, _ in row] for row in rows.values()],
                                                     [["numbers-downloads", "numbers-releases", "numbers-platforms"],
                                                      ["numbers-views", "numbers-stars"], ["numbers-counts"]])
                                    widths = {k: w for row in rows.values() for k, w in row}
                                    whole = widths.pop("numbers-counts")
                                    wide = widths.pop("numbers-stars")  # it fills the row before the counts
                                    self.assertEqual(len(set(widths.values())), 1, widths)
                                    self.assertAlmostEqual(wide, 2 * widths["numbers-views"] + 24, delta=2)
                                    self.assertAlmostEqual(whole, 3 * widths["numbers-views"] + 2 * 24, delta=2)
                                else:
                                    self.assertEqual(len(rows), 6)
                            self.assertEqual(page.errors, [])

    def test_a_chart_tells_its_points_on_hover_and_from_the_keyboard(self):
        """With scripts, a pointer over a small line shows the nearest day's value and date, the same as its table's
        row, with a hairline at the day; on the Arabic page, where time runs from the right, the nearest day is the one
        under the pointer. Once the line has the focus, the arrow keys move along it the way time runs. Without scripts
        there is nothing to show and nothing takes the focus."""
        tip = """() => { const t = document.querySelector('#numbers-views .tip');
            return t && !t.hidden ? [t.querySelector('strong').textContent, t.querySelector('span').textContent] : null; }"""
        row = "i => Array.from(document.querySelectorAll('#numbers-views tbody tr')[i].cells).map(c => c.textContent).reverse()"
        for path in PAGES:
            with self.subTest(path):
                page = self.open(path, 1440)
                chart = page.locator("#numbers-views .chart")
                chart.scroll_into_view_if_needed()
                box = chart.bounding_box()
                # the tenth day of thirty, the first at one end and the last at the other: from the left, or from the
                # right on the Arabic page
                across = (9 / 29) if path == "/" else 1 - (9 / 29)
                page.mouse.move(box["x"] + box["width"] * across, box["y"] + box["height"] / 2)
                self.assertEqual(page.evaluate(tip), page.evaluate(row, 9))
                cross = page.locator("#numbers-views .cross").bounding_box()
                self.assertAlmostEqual(cross["x"], box["x"] + box["width"] * across, delta=box["width"] / 58 + 1)
                page.mouse.move(0, 0)
                self.assertIsNone(page.evaluate(tip))
                page.locator("#numbers-views .plot").focus()
                self.assertEqual(page.evaluate(tip), page.evaluate(row, 29), "the focus starts at the newest day")
                page.keyboard.press("ArrowLeft" if path == "/" else "ArrowRight")
                self.assertEqual(page.evaluate(tip), page.evaluate(row, 28))
                page.keyboard.press("Home")
                self.assertEqual(page.evaluate(tip), page.evaluate(row, 0))
                self.assertEqual(page.locator("#numbers-views .plot").get_attribute("aria-label"),
                                 page.locator("#numbers-views h3").text_content())
                self.assertEqual(page.errors, [])
            with self.subTest(path, scripts=False):
                page = self.open(path, 1440, scripts=False)
                self.assertEqual(page.locator("section[aria-labelledby='numbers'] [tabindex]").count(), 0)
                self.assertEqual(page.locator("section[aria-labelledby='numbers'] .tip").count(), 0)

    def test_the_downloads_are_the_releases_files(self):
        """The hero's download is the latest release's Windows installer, and on a Linux computer its Linux archive
        with the label to match; each SHA256 is selected whole by one click."""
        for path in PAGES:
            for platform, scripts, system in ((None, False, "windows"), ("Linux x86_64", True, "linux"), ("Windows", True, "windows")):
                with self.subTest(path, platform=platform):
                    page = self.open(path, 1440, scripts=scripts, platform=platform)
                    name = fixture.FILES[system][0]
                    self.assertEqual(page.locator(".actions [data-download]").get_attribute("href"),
                                     f"https://github.com/MohammedEl-sayedAhmed/sedjem/releases/download/{fixture.TAG}/{name}")
            page = self.open(path, 1440)
            code = page.locator("#download .sum").first
            code.scroll_into_view_if_needed()
            code.click()
            self.assertEqual(page.evaluate("getSelection().toString()"), fixture.FILES["windows"][2])
            self.assertEqual(page.evaluate("getComputedStyle(document.querySelector('#download .sum')).fontFamily").split(",")[0].strip('"'),
                             "IBM Plex Mono")

    def test_the_counts_keep_nothing_in_the_browser(self):
        """After a page view and a download: no cookie, nothing in local or session storage, IndexedDB or the cache."""
        page = self.open("/", 390, base=self.counting_base, scripts_before=[HOLD])
        page.locator("[data-download='source']").click()
        self.counts(page, 2)
        self.assertEqual(page.context.cookies(), [])
        self.assertEqual(page.evaluate("""async () => [document.cookie, localStorage.length, sessionStorage.length,
            (await indexedDB.databases()).length, (await caches.keys()).length]"""), ["", 0, 0, 0, 0])
        self.assertEqual(page.errors, [])


if __name__ == "__main__":
    unittest.main()
