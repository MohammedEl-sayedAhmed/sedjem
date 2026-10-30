"""Browser tests for the website (site/build.py): both pages, built into a temporary folder and served on a free
port, in a headless Chromium at a phone's width up to a desktop's. Nothing may scroll sideways, nothing may load
from another site, no script may fail, and the hero's lines must all arrive, or be there at once with less motion
or without scripts. The theme on the page's attribute wins over the system's, the reader's stored pick is on the
page before it is drawn, the theme button goes through its three states and remembers the last, the pictures of the
app follow the pick and only the ones on show are fetched, and without scripts the page follows the system, the
phone's menu still opens and the download is Windows'. The pictures of the app are never drawn smaller than their
own size, from a 320 px phone to a desktop; the speakers' names are in their colours, which read on the card in both
themes; the commands fade at the edge where more of them is to the side, and the subtitles are left out on a phone.
These were checks by hand; tests/test_site.py has the ones that need no browser.

The browser comes from tests/ui_support.py: the tests skip without Playwright or a browser, unless
SEDJEM_UI_REQUIRED=1 (as CI sets it).
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui_site.py" -v
"""
import functools
import importlib.util
import re
import struct
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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
        cls.out = out = build.build(Path(cls.tmp.name) / "site")
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(out)))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = launch(cls.playwright)
        except BaseException:
            cls.stop()
            raise

    @classmethod
    def stop(cls):
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.stop()

    def open(self, path, width, motion="reduce", system="light", stored=None, scripts=True, platform=None):
        """A page at a width, with every request and error it causes recorded: the system's theme as given, the
        reader's pick of theme stored before the page loads, and the computer's system as the page asks for it."""
        context = self.browser.new_context(viewport={"width": width, "height": 800}, reduced_motion=motion,
                                           color_scheme=system, java_script_enabled=scripts)
        self.addCleanup(context.close)
        if stored:
            context.add_init_script(f"localStorage.setItem({KEY!r}, {stored!r});")
        if platform:
            context.add_init_script(f"""Object.defineProperty(navigator, "userAgentData", {{get: () => ({{platform: {platform!r}}})}});
                Object.defineProperty(navigator, "platform", {{get: () => {platform!r}}});""")
        context.add_init_script(THEME_AT_BODY)
        page = context.new_page()
        page.outside, page.errors, page.requests = [], [], []
        page.on("request", lambda r: page.requests.append(r.url) if r.url.startswith(self.base) else page.outside.append(r.url))
        page.on("pageerror", lambda e: page.errors.append(str(e)))
        page.on("console", lambda m: page.errors.append(m.text) if m.type == "error" else None)
        page.goto(self.base + path, wait_until="load")
        return page

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

    def test_the_ear_and_the_transcript_sit_on_the_grid(self):
        """At 1440 by 900 the ear's top is the headline's and its end edge the header's, on the start side of the
        Arabic page, and the transcript's top shows in the first screen, 470 to 550 px down. Below 1000 px the ear
        isn't drawn."""
        box = """() => Object.fromEntries(['h1', '.ear', '.site-header', '.hero-figure .frame'].map(s => {
            const b = document.querySelector(s).getBoundingClientRect(); return [s, [b.left, b.top, b.right, b.bottom].map(Math.round)]; }))"""
        for path in PAGES:
            with self.subTest(path):
                page = self.open(path, 1440)
                b = page.evaluate(box)
                self.assertEqual(b[".ear"][1], b["h1"][1])
                end = 2 if path == "/" else 0  # the end edge: the right in English, the left in Arabic
                self.assertEqual(b[".ear"][end], b[".site-header"][end])
                self.assertTrue(470 <= b[".hero-figure .frame"][1] <= 550, b)
                page = self.open(path, 999)
                self.assertFalse(page.locator(".ear").is_visible())

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

    def test_the_button_goes_through_the_three_themes_and_remembers_the_last(self):
        # in the header's row from 700 px, and in the phone's menu, where it shows its name too
        for path, width, system, other in (("/", 1440, "dark", "light"), ("/ar/", 1440, "light", "dark"),
                                           ("/", 390, "light", "dark"), ("/ar/", 390, "dark", "light")):
            with self.subTest(path, width=width, system=system):
                page = self.open(path, width, system=system)
                if width < 700:
                    page.locator("details.menu summary").click()
                button = page.locator(".theme-wide" if width >= 700 else ".theme-menu")
                name = {state: button.get_attribute(f"data-{state}") for state in ("system", "light", "dark")}
                self.assertEqual(button.get_attribute("aria-label"), name["system"])
                # the first press always changes what the page looks like
                for theme, drawn in ((other, other), (system, system), (None, system)):
                    button.click()
                    self.assertEqual(self.theme(page)[:2], [theme, BACKGROUND[drawn]])
                    self.assertEqual(button.get_attribute("aria-label"), name[theme or "system"])
                    if width < 700:
                        self.assertEqual(button.locator(".theme-name").text_content(), name[theme or "system"])
                    self.assertEqual(page.evaluate(f"localStorage.getItem({KEY!r})"), theme)
                button.click()
                page.reload()
                self.assertEqual(self.theme(page)[:2], [other, BACKGROUND[other]])
                self.assertEqual(page.locator(".theme-wide").get_attribute("aria-label"), name[other])
                self.assertEqual(page.errors, [])

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
        for system in ("light", "dark"):
            with self.subTest(system=system):
                page = self.open("/ar/", 390, system=system, scripts=False)
                self.assertEqual(page.locator("body").evaluate("b => getComputedStyle(b).backgroundColor"),
                                 BACKGROUND[system])
                self.assertFalse(page.locator(".theme-wide").is_visible())


if __name__ == "__main__":
    unittest.main()
