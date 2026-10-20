"""Browser tests of the interface: what was checked by hand while building it, run for real in headless
Chromium against a Sedjem server with demo transcripts (see tests/ui_support.py).
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui*.py" -v
"""
import os
import unittest
from unittest import mock

from ui_support import LINES, UiTestCase

JOB, MERGE, LONG = "20260925-230000-a1b2", "20260925-230100-b2c3", "20260925-230200-c3d4"
# a two-hour meeting: 400 lines
LONG_LINES = [{**LINES[i % 5], "start": i * 18.0, "end": i * 18.0 + 17} for i in range(400)]


class TranscriptPage(UiTestCase):
    jobs = ({"jid": JOB}, {"jid": MERGE, "title": "Two speakers"},
            {"jid": LONG, "title": "Long meeting", "lines": LONG_LINES, "seconds": 20.0})

    def job_page(self, jid=JOB):
        self.open(f"#/job/{jid}")
        self.page.wait_for_selector(".line .text")

    def test_mixed_lines_take_their_direction_from_their_words(self):
        self.job_page()
        dirs = self.page.eval_on_selector_all(".line .text", "els => els.map(e => [e.dir, getComputedStyle(e).direction])")
        # lines 1 and 2 open with an English word but are Arabic sentences; 3 and 5 are English; 6 is a time
        self.assertEqual([d for d, _ in dirs], ["rtl", "rtl", "ltr", "rtl", "ltr", "auto"])
        self.assertEqual([c for _, c in dirs][:5], ["rtl", "rtl", "ltr", "rtl", "ltr"])

    def test_direction_follows_what_is_typed(self):
        self.job_page()
        self.page.click("#editBtn")
        line = self.page.locator(".line .text").nth(4)
        line.click()
        self.page.keyboard.press("ControlOrMeta+A")
        self.page.keyboard.type("تمام نبدأ ال sprint planning دلوقتي")
        self.assertEqual(line.get_attribute("dir"), "rtl")
        self.page.keyboard.press("ControlOrMeta+A")
        self.page.keyboard.type("OK, let us start")
        self.assertEqual(line.get_attribute("dir"), "ltr")

    def test_merge_menu_stays_in_view_and_merges(self):
        self.job_page(MERGE)
        self.page.click(".merge-btn")
        menu = self.page.locator(".merge-menu.open")
        box = menu.bounding_box()
        self.assertGreaterEqual(box["x"], 0)
        self.assertLessEqual(box["x"] + box["width"], 1280)
        self.assertFalse(self.page.evaluate("() => document.documentElement.scrollWidth > innerWidth"))
        self.page.once("dialog", lambda d: d.accept())
        menu.locator("button").first.click()
        self.page.wait_for_selector(".toast >> text=Speakers merged")
        job = self.api("GET", f"/api/jobs/{MERGE}")["json"]
        self.assertEqual({x["speaker"] for x in job["lines"]}, {"2"})

    def test_export_icons_are_small_and_export_asks_where_to_save(self):
        # stands in for the browser's Save dialog (showSaveFilePicker) and keeps what is written
        self.page.add_init_script("""
            window.showSaveFilePicker = async (opts) => ({ name: opts.suggestedName, createWritable: async () => {
                const parts = [];
                return { write: async (b) => parts.push(b),
                         close: async () => { window.__saved = { name: opts.suggestedName, text: await new Blob(parts).text() }; } };
            } });""")
        self.job_page()
        self.page.click("[data-menu='exportMenu']")
        width = self.page.locator("#exportMenu a svg").first.bounding_box()["width"]
        self.assertLessEqual(width, 18)
        self.page.click("#exportMenu a[data-fmt='txt']")
        saved = self.page.wait_for_function("() => window.__saved").json_value()
        self.assertEqual(saved["name"], "Sprint-review.txt")
        self.assertIn("ال API معتمد", saved["text"])

    def test_details_show_where_it_ran_with_logos(self):
        self.job_page()
        hero = self.page.locator(".job-details .det-hero")
        self.assertIn("Intel Iris Xe Graphics", hero.inner_text())
        self.assertIn("0.15×", hero.inner_text())
        # a logo is a Simple Icons glyph (svg with a label) or a brand's own file (img with alt text)
        logos = self.page.eval_on_selector_all(".job-details .logo-tile svg[aria-label], .job-details .logo-tile img.on-light",
                                               "els => els.map(e => e.getAttribute('aria-label') || e.alt)")
        self.assertEqual(logos[:1], ["Intel"])
        self.assertIn("Lenovo", logos)
        text = self.page.locator(".job-details").inner_text()
        self.assertIn("Lenovo ThinkPad P16 Gen 1", text)  # the maker's own casing, not "LENOVO"
        self.assertIn("M4A", text)

    def test_details_can_be_reached_at_the_top_of_a_long_transcript(self):
        self.job_page(LONG)
        self.page.evaluate("() => document.getElementById('main').scrollTo(0, 600)")  # a little way into it
        side = self.page.locator(".side-col")
        box = side.bounding_box()
        self.assertGreaterEqual(box["y"], 0)
        self.assertLessEqual(box["y"] + box["height"], 800, "the side column fits in the window")
        top = self.page.evaluate("([x, y]) => !!document.elementFromPoint(x, y).closest('.side-col')",
                                 [box["x"] + box["width"] / 2, box["y"] + 4])
        self.assertTrue(top, "nothing covers the top of the side column")
        side.evaluate("el => el.scrollTo(0, el.scrollHeight)")  # scrolled on its own
        copy = self.page.locator("#copyDetails").bounding_box()
        player = self.page.locator("#player").bounding_box()
        self.assertLessEqual(copy["y"] + copy["height"], player["y"], "the player bar doesn't cover the details")
        main = self.page.evaluate("() => { const m = document.getElementById('main'); return [m.scrollTop, m.scrollHeight - m.clientHeight]; }")
        self.assertLess(main[0], main[1] / 2, "the transcript stays where it was, far from its end")

    def test_playback_speed_list_is_styled_and_works(self):
        self.job_page()
        if not self.page.evaluate("() => CSS.supports('appearance', 'base-select')"):
            self.skipTest("this Chromium has no customizable select")
        self.assertEqual(self.page.evaluate("() => getComputedStyle(document.getElementById('rate')).appearance"), "base-select")
        self.page.select_option("#rate", "1.5")
        self.assertEqual(self.page.evaluate("() => document.getElementById('audio').playbackRate"), 1.5)


class SettingsDialog(UiTestCase):
    def test_toasts_show_above_the_open_dialog(self):
        self.open()
        before = self.api("GET", "/api/status")["json"]["settings"]["threads"]
        self.open_settings("speed")
        threads = self.page.locator("input[data-setting='threads']")
        threads.fill("2")
        threads.dispatch_event("change")
        toast = self.page.wait_for_selector(".toast >> text=Saved")
        self.assertTrue(toast.is_visible())
        # In the top layer (a popover) and shown after the dialog opened, so drawn above it and its blurred
        # backdrop. (While a modal dialog is open everything outside it is inert, so hit-testing the toast
        # with elementFromPoint can't show this.)
        state = self.page.evaluate("""() => ({ dialog: document.getElementById('settings').open,
                                              popover: document.getElementById('toasts').matches(':popover-open') })""")
        self.assertEqual(state, {"dialog": True, "popover": True})
        # back as it was, within what this machine allows (the default, 10, can be more than a CI runner has)
        self.api("POST", "/api/settings", {"threads": min(before, os.cpu_count() or 1)})

    def test_switches_are_saved(self):
        self.open()
        self.open_settings("speed")
        self.page.locator("input[data-setting='device']").uncheck()
        self.page.wait_for_selector(".toast >> text=Saved")
        self.assertEqual(self.api("GET", "/api/status")["json"]["settings"]["device"], "cpu")
        self.page.reload()
        self.page.wait_for_function("() => typeof S !== 'undefined' && S.status")
        self.open_settings("speed")
        self.assertFalse(self.page.locator("input[data-setting='device']").is_checked())
        self.api("POST", "/api/settings", {"device": "auto"})

    def test_about_shows_the_author_and_the_licence(self):
        self.open()
        self.open_settings("about")
        about = self.page.locator(".about-text")
        self.assertIn("By Mohammed El-sayed Ahmed", about.inner_text())
        self.assertIn("AGPL-3.0", about.inner_text())
        links = about.locator("a").evaluate_all("els => els.map(a => a.href)")
        self.assertIn("https://github.com/MohammedEl-sayedAhmed/sedjem", links)

    def test_one_tab_at_a_time_and_the_keyboard_moves_between_them(self):
        self.open()
        self.open_settings()
        self.assertEqual(self.page.locator("#settings [role=tab][aria-selected=true]").inner_text().split("\n")[0].strip(), "Models")
        self.assertEqual(self.page.locator("#settings .set-panel").count(), 1)
        self.page.focus("#tab-models")
        self.page.keyboard.press("ArrowDown")
        self.assertEqual(self.page.evaluate("() => document.activeElement.id"), "tab-add")
        self.assertTrue(self.page.locator("#tab-add[aria-selected=true]").is_visible())
        # the panel fits the dialog: no scrolling through every section
        height = self.page.evaluate("() => document.getElementById('settingsBody').scrollHeight")
        self.assertLessEqual(height, 700)

    def test_model_states_look_different(self):
        self.open()
        self.open_settings()
        states = self.page.eval_on_selector_all("#settings .set-item .status",
                                                "els => els.map(e => [e.className, e.textContent.trim(), getComputedStyle(e).borderStyle])")
        kinds = {cls.split()[1]: text for cls, text, _ in states}
        self.assertEqual(kinds.get("none"), "Not downloaded")  # nothing is downloaded in the test's home
        # each state has its own look, not only its word: here the dashed outline of "Not downloaded"
        self.assertIn("dashed", {style for cls, _, style in states if "none" in cls})

    def test_key_rows_stay_closed_until_one_is_opened(self):
        self.open()
        self.open_settings("hosted")
        self.assertEqual(self.page.locator("#settings [data-key]").count(), 0, "no key box is open at first")
        self.page.click("[data-key-toggle='deepgram']")
        self.assertEqual(self.page.evaluate("() => document.activeElement.dataset.key"), "deepgram")
        self.page.click("[data-key-toggle='gemini']")
        self.assertEqual(self.page.locator("#settings [data-key]").count(), 1, "only one open at a time")
        self.page.fill("[data-key='gemini']", "test-key-123")
        self.page.click("[data-key-form='gemini'] button[type=submit]")
        self.page.wait_for_selector(".toast >> text=Key saved")
        self.assertEqual(self.page.locator("#settings [data-key]").count(), 0, "saving closes it")
        self.assertIn("Key saved", self.page.locator("#key-gemini .status").inner_text())
        self.assertIn("1 of", self.page.locator("#tab-hosted small").inner_text())
        self.api("POST", "/api/keys", {"model": "gemini", "key": ""})

    def test_the_add_it_link_opens_that_services_key(self):
        self.open()
        card = self.page.locator(".model-card[data-model='deepgram']")
        card.locator("[data-open-settings]").click()
        self.page.wait_for_selector("#settings[open] #tab-hosted[aria-selected=true]")
        self.page.wait_for_function("() => document.activeElement && document.activeElement.dataset.key === 'deepgram'")

    def test_logos_of_models_and_services(self):
        self.open()
        self.page.wait_for_selector(".model-card[data-model='groq'] .mc-head .logo-tile")
        labels = self.page.eval_on_selector_all(".model-card .mc-head .logo-tile",
                                                "els => els.map(e => e.getAttribute('title') || e.textContent.trim())")
        self.assertIn("Hugging Face", labels)  # a model downloaded from there, with no logo of its own
        self.assertIn("Cohere", labels)
        self.assertIn("ElevenLabs", labels)
        self.assertIn("Google Gemini", labels)
        self.assertIn("Speechmatics", labels)
        self.assertIn("Groq", labels)  # its mark, from LobeHub's set (see app/static/logos/SOURCES.md)
        self.assertEqual(self.page.locator(".model-card[data-model='groq'] .logo-tile.mono").count(), 0)

    def test_each_hosted_service_shows_its_own_logo(self):
        self.open()
        self.page.wait_for_selector(".model-card[data-model='mistral'] .mc-head .logo-tile")
        for theme in ("light", "dark"):
            self.page.evaluate("t => document.documentElement.dataset.theme = t", theme)
            self.page.wait_for_function("""() => [...document.querySelectorAll('.model-card .mc-head .logo-tile img')]
                .every(i => i.complete)""")
            shown = self.page.evaluate("""() => Object.fromEntries([...document.querySelectorAll('.model-card')].map(card => {
                const img = [...card.querySelectorAll('.mc-head .logo-tile img')].find(i => getComputedStyle(i).display !== 'none');
                return [card.dataset.model, img ? [img.alt, img.naturalWidth, img.getBoundingClientRect().width] : null];
            }))""")
            for model, name in (("elevenlabs", "ElevenLabs"), ("gemini", "Google Gemini"), ("deepgram", "Deepgram"),
                                ("assemblyai", "AssemblyAI"), ("azure", "Azure AI Speech"),
                                ("speechmatics", "Speechmatics"), ("openai", "OpenAI"), ("mistral", "Mistral AI"),
                                ("groq", "Groq")):
                with self.subTest(model=model, theme=theme):
                    alt, natural, width = shown[model]
                    self.assertEqual(alt, name)
                    self.assertGreater(natural, 0)  # the file loaded
                    self.assertGreater(width, 0)
        # the version for dark backgrounds, in the dark theme: Groq's mark in white
        for model, file in (("groq", "groq-dark.svg"),):
            src = self.page.eval_on_selector(f".model-card[data-model='{model}'] .logo-tile img.on-dark", "i => i.src")
            self.assertTrue(src.endswith(f"/static/logos/{file}"), src)

    def test_gpu_chip(self):
        self.open()
        self.assertIn("GPU: Iris Xe", self.page.locator("#topStatus").inner_text())



class PageErrors(unittest.TestCase):
    """What the browser tests count as the page's errors (no browser needed)."""

    def test_only_the_machine_running_out_of_socket_buffers_is_let_through(self):
        page = type("Page", (), {"errors": []})()
        for text in ("Failed to load resource: net::ERR_NO_BUFFER_SPACE",
                     "Failed to load resource: the server responded with a status of 404 (Not Found)",
                     "Failed to load resource: net::ERR_CONNECTION_REFUSED", "Uncaught TypeError: x is undefined"):
            with mock.patch("sys.stderr"):
                UiTestCase.console_error(page, text)
        self.assertEqual(page.errors, ["Failed to load resource: the server responded with a status of 404 (Not Found)",
                                       "Failed to load resource: net::ERR_CONNECTION_REFUSED",
                                       "Uncaught TypeError: x is undefined"])

if __name__ == "__main__":
    unittest.main()
