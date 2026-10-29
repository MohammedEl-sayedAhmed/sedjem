"""Browser tests of the anonymous usage counts in the app (app/static/usage.js, and the first-start dialog in
app/static/updates.js): on first start the dialog asks about updates and says that the counts are on, with their
switch; after an update that brought them it says only that, once; put away with Escape it comes back at the next
start, unless its switch was turned; the switch in the dialog is saved as soon as it is turned; Settings → About has
the switch, with a line on what is sent and a link to the docs; DO_NOT_TRACK turns it off and disables it; a copy
without a key shows neither the notice nor the switch; and the dialog fits a narrow window.
The server is the real one, with a stand-in key for a self-hosted Aptabase on 127.0.0.1 that nothing listens on;
nothing would be sent anyway, since the tests never send (app/usage.py).
Headless Chromium against a Sedjem server with a demo transcript (see tests/ui_support.py).
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui_usage.py" -v
"""
import json
import os
import unittest
from unittest import mock

from ui_support import UiTestCase

DOC = "https://github.com/MohammedEl-sayedAhmed/sedjem/blob/main/docs/13-usage-counts.md"
WHAT = ("A few numbers a month, such as the version, the system and how many transcriptions with which models; "
        "never recordings, transcripts or names.")


class UsageCounts(UiTestCase):
    jobs = ({"jid": "20261020-100000-a001", "title": "Sprint planning", "created": "2026-10-20T10:00:00+03:00"},)
    config = '[usage]\napp_key = "A-SH-1234567890"\nhost = "http://127.0.0.1:9"\n'

    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ)  # the server runs in this process: its environment is this one
        env.start()
        self.addCleanup(env.stop)
        for var in ("DO_NOT_TRACK", "SEDJEM_NO_USAGE"):
            os.environ.pop(var, None)
        self.file = self.home / "app_data" / "settings.json"

    def tearDown(self):
        try:
            super().tearDown()
        finally:
            self.settings(update_check=False)  # as the other tests of this class expect to find it

    def settings(self, **values):
        """settings.json as the test needs it; the server reads it again for every request."""
        self.file.write_text(json.dumps(values), encoding="utf-8")

    def saved(self):
        return json.loads(self.file.read_text(encoding="utf-8"))

    def text(self, selector):
        return " ".join(self.page.inner_text(selector).split())

    def texts(self, selector):
        return self.page.eval_on_selector_all(selector, "els => els.map(e => e.innerText.replace(/\\s+/g, ' ').trim())")

    def toast(self, words):
        self.page.wait_for_selector(f".toast >> text={words}")

    def no_dialog(self):
        self.page.evaluate("() => refreshStatus()")  # another status check doesn't bring it either
        self.assertFalse(self.page.is_visible("#updAsk"))

    # ---- the first-start dialog ------------------------------------------------------------------------
    def test_first_start_asks_about_updates_and_says_the_counts_are_on(self):
        self.settings()
        self.open()
        self.page.wait_for_selector("#updAsk[open]")
        self.assertEqual(self.page.inner_text("#updAskTitle"), "Check for updates automatically?")
        self.assertIn("Once a day, Sedjem can ask GitHub", self.text("#updAskText"))
        self.assertEqual(self.text("#updAsk .usage-ask .set-text b"), "Send anonymous usage counts")
        notice = self.text("#usageAskText")
        self.assertEqual(notice, f"On unless you turn it off, here or later in Settings → About. {WHAT} What is sent")
        link = self.page.locator("#usageAskText a")
        self.assertEqual((link.get_attribute("href"), link.get_attribute("target")), (DOC, "_blank"))
        self.assertTrue(self.page.is_checked("#usageAsk"), "on by default")
        self.assertEqual(self.texts("#updAsk .dlg-foot button"), ["No", "Yes, check daily"])
        self.assertEqual(self.page.get_attribute("#updAsk", "aria-describedby"), "updAskText")
        self.assertEqual(self.page.evaluate("() => document.activeElement.id"), "updAskNo", "not the link to the docs")

        self.page.click("#updAskYes")
        self.page.wait_for_selector("#updAsk", state="hidden")
        self.toast("Sedjem checks for updates once a day")
        self.assertEqual(self.saved(), {"update_check": True, "usage_counts": True})
        self.page.reload()
        self.page.wait_for_function("() => S.status")
        self.no_dialog()

    def test_turning_the_counts_off_in_the_dialog(self):
        self.settings()
        self.open()
        self.page.wait_for_selector("#updAsk[open]")
        self.page.click("#usageAsk")
        self.toast("No usage counts. Settings → About can turn them on again")
        self.assertEqual(self.saved(), {"usage_counts": False}, "saved as soon as it is turned")
        self.page.click("#updAskNo")
        self.page.wait_for_selector("#updAsk", state="hidden")
        self.assertEqual(self.saved(), {"usage_counts": False, "update_check": False})
        self.page.reload()
        self.page.wait_for_function("() => S.status")
        self.no_dialog()
        self.open_settings("about")
        self.assertFalse(self.page.is_checked("#usageSwitch"))

    def test_after_an_update_the_notice_alone_once(self):
        self.settings(update_check=False)  # answered before the counts came
        self.open()
        self.page.wait_for_selector("#updAsk[open]")
        self.assertEqual(self.page.inner_text("#updAskTitle"), "Anonymous usage counts")
        self.assertEqual(self.page.locator("#updAskText").count(), 0, "no question about updates")
        self.assertEqual(self.page.get_attribute("#updAsk", "aria-describedby"), "usageAskText")
        self.assertTrue(self.page.is_checked("#usageAsk"))
        self.assertEqual(self.texts("#updAsk .dlg-foot button"), ["OK"])
        self.assertEqual(self.page.evaluate("() => document.activeElement.id"), "updAskOk")
        self.page.click("#updAskOk")
        self.page.wait_for_selector("#updAsk", state="hidden")
        self.assertEqual(self.saved(), {"update_check": False, "usage_counts": True})
        self.page.reload()
        self.page.wait_for_function("() => S.status")
        self.no_dialog()

    def test_put_away_it_comes_back_at_the_next_start(self):
        self.settings(update_check=False)
        self.open()
        self.page.wait_for_selector("#updAsk[open]")
        self.page.keyboard.press("Escape")
        self.page.wait_for_selector("#updAsk", state="hidden")
        self.no_dialog()
        self.assertEqual(self.saved(), {"update_check": False}, "not an answer")
        self.page.reload()
        self.page.wait_for_function("() => S.status")
        self.page.wait_for_selector("#updAsk[open]")
        self.page.click("#usageAsk")  # turned off, then put away: the switch was the answer
        self.toast("No usage counts")
        self.page.keyboard.press("Escape")
        self.page.wait_for_selector("#updAsk", state="hidden")
        self.page.reload()
        self.page.wait_for_function("() => S.status")
        self.no_dialog()
        self.assertEqual(self.saved(), {"update_check": False, "usage_counts": False})

    # ---- Settings → About ------------------------------------------------------------------------------
    def test_the_switch_in_settings_about(self):
        self.settings(update_check=False, usage_counts=True)
        self.open()
        self.no_dialog()
        self.open_settings("about")
        row = "#set-panel .set-row:has(#usageSwitch)"
        self.assertEqual(self.text(f"{row} .set-text b"), "Send anonymous usage counts")
        self.assertEqual(self.text(f"{row} .hint"), f"{WHAT} What is sent")
        self.assertEqual(self.page.get_attribute(f"{row} .hint a", "href"), DOC)
        self.assertTrue(self.page.is_checked("#usageSwitch"))
        self.page.click("#usageSwitch")
        self.toast("Saved")
        self.assertIs(self.saved()["usage_counts"], False)
        self.assertFalse(self.page.is_checked("#usageSwitch"), "drawn again, off")
        self.page.click("#usageSwitch")
        self.page.wait_for_function("() => S.status.usage.on")
        self.assertIs(self.saved()["usage_counts"], True)
        self.assertEqual(self.page.evaluate("() => document.activeElement.id"), "usageSwitch", "the focus stays")

    def test_do_not_track_turns_it_off(self):
        os.environ["DO_NOT_TRACK"] = "1"
        self.settings(update_check=False)  # the counts never said: still no notice
        self.open()
        self.no_dialog()
        self.open_settings("about")
        self.assertFalse(self.page.is_checked("#usageSwitch"))
        self.assertTrue(self.page.is_disabled("#usageSwitch"))
        self.assertEqual(self.text("#set-panel .set-row:has(#usageSwitch) .hint"),
                         "Off: DO_NOT_TRACK is set in the environment Sedjem was started from. What is sent")

    def test_a_copy_without_a_key_shows_neither(self):
        with mock.patch.object(self.app.usage, "key", ""):
            self.settings(update_check=False)
            self.open()
            self.no_dialog()
            self.open_settings("about")
            self.assertEqual(self.page.locator("#usageSwitch").count(), 0)
            self.assertIn("Your data", self.text("#set-panel .set-list"))
            self.page.keyboard.press("Escape")
            self.settings()  # a first start: the question about updates alone
            self.page.reload()
            self.page.wait_for_function("() => S.status")
            self.page.wait_for_selector("#updAsk[open]")
            self.assertEqual(self.page.locator("#usageAsk").count(), 0)
            self.page.click("#updAskNo")
            self.page.wait_for_selector("#updAsk", state="hidden")
            self.assertEqual(self.saved(), {"update_check": False})

    def test_the_dialog_in_a_narrow_window(self):
        self.page.set_viewport_size({"width": 360, "height": 640})
        self.settings()
        self.open()
        self.page.wait_for_selector("#updAsk[open]")
        box = self.page.locator("#updAsk").bounding_box()
        self.assertLessEqual(box["x"] + box["width"], 360)
        self.assertGreaterEqual(box["x"], 0)
        sw = self.page.locator("#usageAsk").bounding_box()
        self.assertLessEqual(sw["x"] + sw["width"], box["x"] + box["width"], "the switch is inside the dialog")
        self.assertFalse(self.page.evaluate("() => document.documentElement.scrollWidth > innerWidth"))


if __name__ == "__main__":
    unittest.main()
