"""Browser tests of Settings while a model downloads: the page checks the status every 1.5 s then and
redraws Settings, which used to throw the panel back to the top (scrolling seemed to hang) and wipe a
field being typed in. Headless Chromium against a Sedjem server (see tests/ui_support.py); the download
is reported by rewriting the status replies, so nothing is downloaded.
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui*.py" -v
"""
import unittest

from ui_support import UiTestCase


class SettingsWhileDownloading(UiTestCase):
    def setUp(self):
        super().setUp()
        self.page.set_viewport_size({"width": 1280, "height": 620})  # short enough for the panels to scroll

        def downloading(route):
            r = route.fetch()
            st = r.json()
            m = next(m for m in st["models"] if m["kind"] == "local" and m.get("download"))
            m["download"] = {**m["download"], "state": "downloading", "done": 300_000_000, "total": 1_600_000_000}
            route.fulfill(response=r, json=st)
        self.page.route("**/api/status", downloading)

    def tearDown(self):
        self.page.unroute_all(behavior="ignoreErrors")
        super().tearDown()

    def wait_for_redraws(self, n=2):
        """Until Settings has been rebuilt n times by the download checks."""
        self.page.evaluate("() => { window.__panel = document.querySelector('#set-panel'); window.__redraws = 0;"
                           " new MutationObserver(() => { if (document.querySelector('#set-panel') !== window.__panel)"
                           " { window.__panel = document.querySelector('#set-panel'); window.__redraws++; } })"
                           ".observe(document.querySelector('#settingsBody'), { childList: true }); }")
        self.page.wait_for_function(f"() => window.__redraws >= {n}", timeout=8000)

    def test_the_panel_keeps_its_scroll_position(self):
        self.open()
        self.open_settings("add")
        panel = "#set-panel"
        self.assertGreater(self.page.eval_on_selector(panel, "p => p.scrollHeight - p.clientHeight"), 150, "the panel scrolls")
        self.page.eval_on_selector(panel, "p => { p.scrollTop = 150; }")
        self.wait_for_redraws()
        self.assertEqual(self.page.eval_on_selector(panel, "p => p.scrollTop"), 150)

    def test_another_tab_starts_at_the_top(self):
        self.open()
        self.open_settings("add")
        self.page.eval_on_selector("#set-panel", "p => { p.scrollTop = 150; }")
        self.page.click("#settings [data-set-tab='models']")
        self.page.wait_for_selector("#settings #tab-models[aria-selected='true']")
        self.assertEqual(self.page.eval_on_selector("#set-panel", "p => p.scrollTop"), 0)

    def test_a_field_being_typed_in_is_not_wiped(self):
        self.open()
        self.open_settings("hosted")
        self.page.click("#key-deepgram [data-key-toggle]")
        field = self.page.locator("#key-deepgram input[data-key]")
        field.click()
        field.press_sequentially("dg-key-being-typed")
        self.page.wait_for_timeout(3500)  # two status checks while typing
        self.assertEqual(field.input_value(), "dg-key-being-typed")
        self.assertTrue(field.evaluate("e => e === document.activeElement"), "still the field being typed in")
        self.page.click("#settings .dlg-head")  # leaving the field lets the progress redraw again
        self.wait_for_redraws(1)


if __name__ == "__main__":
    unittest.main()
