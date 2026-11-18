"""Browser tests of the default model (#6) in Settings → Models: the list shows the model a new transcription starts
with, choosing another saves it at once and keeps it after a reload, the new transcription page then starts with it,
and a model that isn't ready says so while the page starts with a ready one.
Headless Chromium against a Sedjem server (see tests/ui_support.py). ElevenLabs gets a stand-in key, so it is ready;
Speechmatics has none.
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui_default_model.py" -v
"""
import unittest

from playwright.sync_api import expect
from ui_support import UiTestCase


class DefaultModel(UiTestCase):
    def setUp(self):
        super().setUp()
        self.app.cfg.save_settings(default_model=None)
        self.app.cfg.save_key("elevenlabs", "stand-in-key")

    def choice(self):
        return self.page.locator("#settings select[data-setting='default_model']")

    def test_choosing_it_in_settings(self):
        self.open()
        self.open_settings("models")
        expect(self.choice()).to_have_value("whisper-medium")  # the config's, until another is chosen
        expect(self.choice()).to_have_accessible_name("Default model")
        self.choice().select_option("elevenlabs")
        self.page.wait_for_selector(".toast >> text=Saved")
        self.assertEqual(self.app.cfg.saved_settings()["default_model"], "elevenlabs")
        self.assertEqual(self.page.evaluate("S.status.defaults.model"), "elevenlabs")

        self.page.goto(f"{self.base}/#/new")
        self.page.reload()  # a new start of the page: its form is new too
        self.page.wait_for_function("() => typeof S !== 'undefined' && S.status")
        self.page.wait_for_selector(".model-card[data-model='elevenlabs']")
        self.assertEqual(self.page.evaluate("S.form.model"), "elevenlabs")
        expect(self.page.locator(".model-card[data-model='elevenlabs']")).to_have_attribute("aria-checked", "true")
        self.open_settings("models")
        expect(self.choice()).to_have_value("elevenlabs")

    def test_a_model_that_isnt_ready(self):
        self.open()
        self.open_settings("models")
        expect(self.choice().locator("option[value='speechmatics']")).to_contain_text("(not ready)")
        self.choice().select_option("speechmatics")
        self.page.wait_for_selector(".toast >> text=Saved")
        expect(self.page.locator("#defModelHint")).to_contain_text("isn't ready yet, so the first ready model is selected")
        self.page.goto(f"{self.base}/#/new")
        self.page.reload()
        self.page.wait_for_function("() => typeof S !== 'undefined' && S.status")
        self.page.wait_for_selector(".model-card")
        picked = self.page.evaluate("S.form.model")
        self.assertNotEqual(picked, "speechmatics")
        self.assertTrue(self.page.evaluate("(id) => S.status.models.find((m) => m.id === id).ready", picked))


if __name__ == "__main__":
    unittest.main()
