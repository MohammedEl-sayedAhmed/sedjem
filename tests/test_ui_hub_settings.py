"""Browser tests of adding models in Settings: from a Hugging Face link, and from the Recommended list (Settings →
Add models), then finding them under Models. In headless Chromium against a Sedjem server whose Hugging Face is
the local stand-in of tests/test_hub.py (see HubTestCase), so nothing comes from the internet.
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui*.py" -v
"""
import unittest

from test_ui_hub import GGUF, HubTestCase  # first: it puts the project on sys.path

from app import hub  # noqa: E402

COHERE = "handy-computer/cohere-transcribe-arabic-07-2026-gguf"  # the catalog's higher-precision Cohere
REFUSED = "Failed to load resource: the server responded with a status of 400"  # how the browser logs a refusal


def row(title):
    """A model's row in Settings → Models."""
    return f"#settingsBody .set-item:has(.set-item-text b:text-is('{title}'))"


class HubInSettings(HubTestCase):
    def forget_later(self, model_id):
        self.addCleanup(self.forget, model_id)

    def open_hub(self):
        self.open_settings("add")
        self.page.click("[data-set-add='hub']")
        self.page.wait_for_selector("#hubUrl")

    def check(self, link):
        self.page.fill("#hubUrl", link)
        self.page.press("#hubUrl", "Enter")

    def test_check_a_link_and_pick_a_file(self):
        self.open()
        self.open_hub()
        self.check("https://huggingface.co/org/asr-gguf")
        found = self.page.locator(".hub-found")
        found.locator("#hubAdd").wait_for()
        text = found.inner_text()
        for words in ("org/asr-gguf", "GGUF for transcribe.cpp (NVIDIA Parakeet)", "parakeet", "Apache-2.0", "1234567"):
            self.assertIn(words, text)
        self.assertEqual(self.page.input_value("#hubFile"), "asr-Q4_K_M.gguf", "Q4_K_M first")
        self.assertEqual(found.locator("#hubFile option").all_inner_texts(),
                         ["asr-Q4_K_M.gguf (372 B)", "asr-Q8_0.gguf (572 B)", "asr-F16.gguf (972 B)"], "no mmproj")
        self.assertEqual(found.locator("#hubAdd").inner_text().strip(), "Add and download (372 B)")

        self.page.select_option("#hubFile", "asr-Q8_0.gguf")  # checks that file instead
        self.page.locator("#hubAdd:has-text('572 B')").wait_for()
        self.assertEqual(self.page.input_value("#hubFile"), "asr-Q8_0.gguf")
        self.assertEqual(self.page.input_value("#hubUrl"), "https://huggingface.co/org/asr-gguf")
        self.assertNotIn(GGUF, self.app.cfg.models, "a check saves nothing")

    def test_the_link_and_the_result_survive_a_rerender(self):
        self.open()
        self.open_hub()
        self.page.click("#hubUrl")
        self.page.keyboard.type("org/asr")
        self.page.evaluate("() => renderSettings()")  # what every status poll does during a download
        self.assertEqual(self.page.input_value("#hubUrl"), "org/asr")
        self.page.keyboard.type("-gguf")  # still focused, the caret where it was
        self.assertEqual(self.page.input_value("#hubUrl"), "org/asr-gguf")
        self.page.keyboard.press("Enter")
        self.page.locator("#hubAdd").wait_for()
        self.page.evaluate("() => renderSettings()")
        self.assertIn("NVIDIA Parakeet", self.page.locator(".hub-found").inner_text())

    def test_add_and_download_gives_a_model_card(self):
        self.forget_later(GGUF)
        self.open()
        self.open_hub()
        self.check("org/asr-gguf")
        self.page.select_option("#hubFile", "asr-Q8_0.gguf")
        self.page.locator("#hubAdd:has-text('572 B')").wait_for()
        self.page.click("#hubAdd")
        self.page.locator(".toast:has-text('Added org/asr-gguf')").wait_for()
        self.assertEqual(self.page.input_value("#hubUrl"), "", "the field is cleared for the next one")
        self.page.click("[data-set-tab='models']")  # it is listed with the models on this computer
        self.page.locator(f"{row('asr-gguf')} .status.ok:text-is('Downloaded')").wait_for()
        self.assertIn("from org/asr-gguf · GGUF for transcribe.cpp (NVIDIA Parakeet)", self.page.locator(row("asr-gguf")).inner_text())
        self.assertTrue((self.home / "models/hf/org--asr-gguf/asr-Q8_0.gguf").is_file())

        self.page.click("#settings .dlg-foot [data-close]")
        card = self.page.locator(f".model-card[data-model='{GGUF}']:not(.unavailable)")
        card.wait_for()
        self.assertEqual(card.locator(".mc-badges .pill").all_inner_texts()[1:], ["From Hugging Face"])
        self.assertEqual(card.locator(".mc-title").inner_text(), "asr-gguf")
        self.assertEqual(card.locator(".mc-tag").inner_text(), "NVIDIA Parakeet model")
        self.assertEqual(card.locator("li").all_inner_texts(),
                         ["org/asr-gguf", "GGUF, parakeet: asr-Q8_0.gguf", "572 B download", "Licence: Apache-2.0"])
        self.assertIn("the recording's length on this computer", card.locator(".mc-foot").inner_text())

    def test_remove_from_the_app(self):
        self.forget_later(GGUF)
        self.open()
        self.assertEqual(self.api("POST", "/api/hub/add", {"url": "org/asr-gguf"})["status"], 200)
        self.page.reload()  # the page learns of the new model at its next status
        self.page.wait_for_function("() => typeof S !== 'undefined' && S.status")
        self.open_settings("models")
        self.page.locator(f"{row('asr-gguf')} .status.ok:text-is('Downloaded')").wait_for()
        self.page.once("dialog", lambda d: d.accept())
        self.page.click(f"[data-forget-model='{GGUF}']")
        self.page.locator(".toast:has-text('Removed')").wait_for()
        self.page.locator(row("asr-gguf")).wait_for(state="detached")
        self.assertEqual(self.page.locator(f".model-card[data-model='{GGUF}']").count(), 0)
        self.assertNotIn(GGUF, [m["id"] for m in self.api("GET", "/api/status")["json"]["models"]])
        self.assertFalse((self.home / "models/hf/org--asr-gguf").exists(), "its files are deleted")

    def test_a_refused_repository_says_why(self):
        self.open()
        self.open_hub()
        # a refusal comes as HTTP 400, which the browser also logs as a failed request: expected here
        refused = lambda m: m.text.startswith(REFUSED)  # noqa: E731
        with self.page.expect_console_message(refused):
            self.check("org/llm-gguf")
        error = self.page.locator(".hub-found.error")
        error.wait_for()
        self.assertIn("“llama”, which transcribe.cpp can't run", error.inner_text())
        self.assertEqual(self.page.locator("#hubAdd").count(), 0)
        with self.page.expect_console_message(refused):
            self.check("https://example.com/org/name")
        self.page.locator(".hub-found.error:has-text(\"isn't a Hugging Face link\")").wait_for()
        self.assertEqual(len([e for e in self.errors if e.startswith(REFUSED)]), 2)
        self.errors = [e for e in self.errors if not e.startswith(REFUSED)]

    def test_a_transformers_model_that_cant_be_converted_here(self):
        self.open()
        self.open_hub()
        self.check("org/whisper-hf")
        found = self.page.locator(".hub-found")
        found.locator(".mc-missing").wait_for()
        self.assertIn("Whisper in Transformers format, converted after the download", found.inner_text())
        self.assertEqual(found.locator(".mc-missing").inner_text(), hub.CANT_CONVERT)
        self.assertEqual(self.page.locator("#hubAdd").count(), 0)

    def test_add_from_the_recommended_list(self):
        mid = hub.model_id(self.app.cfg, COHERE)
        self.forget_later(mid)
        self.open()
        self.open_settings("add")  # the Recommended list is what Add models shows first
        catalog = self.page.locator("#catalog")
        catalog.wait_for()
        self.assertEqual(catalog.locator(".status:text-is('Built in')").count(), 3)
        whisper_small = catalog.locator(".set-item:has-text('Egyptian code-switching whisper-small')")
        self.assertEqual(whisper_small.locator(".mc-missing").inner_text(), hub.NEEDS_SOURCE)
        self.assertEqual(whisper_small.locator("[data-catalog-add]").count(), 0)

        entry = catalog.locator(".set-item:has-text('Cohere Transcribe Arabic, higher precision')")
        entry.locator("select").select_option("cohere-transcribe-arabic-07-2026-Q6_K.gguf")
        entry.locator("[data-catalog-add]:has-text('Add (2.0 GB)')").wait_for()
        entry.locator("[data-catalog-add]").click()
        self.page.locator(".toast:has-text('Added Cohere Transcribe Arabic, higher precision')").wait_for()
        entry.locator(".status.ok:text-is('Added: Q6_K')").wait_for()
        self.page.click("[data-set-tab='models']")
        self.page.locator(f"{row('cohere-transcribe-arabic-07-2026-gguf')} .status.ok:text-is('Downloaded')").wait_for()
        model = next(m for m in self.api("GET", "/api/status")["json"]["models"] if m["id"] == mid)
        self.assertEqual((model["hub"]["file"], model["hub"]["revision"], model["engine"]),
                         ("cohere-transcribe-arabic-07-2026-Q6_K.gguf", next(c["revision"] for c in hub.recommended()
                                                                           if c.get("repo") == COHERE), "gguf"))


if __name__ == "__main__":
    unittest.main()
