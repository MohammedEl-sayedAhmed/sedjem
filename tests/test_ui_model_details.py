"""Browser tests of the model details in Settings: the line of key facts on each row of Models, Hosted services and
Recommended, and everything known about a model when its row is opened. In headless Chromium against a Sedjem
server whose Hugging Face is the local stand-in of tests/test_hub.py (see HubTestCase), so nothing comes from the
internet.
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui*.py" -v
"""
import unittest

from test_ui_hub import GGUF, HubTestCase  # first: it puts the project on sys.path

from playwright.sync_api import expect  # noqa: E402

from app import hub  # noqa: E402

COHERE = "handy-computer/cohere-transcribe-arabic-07-2026-gguf"  # the catalog's higher-precision Cohere


def row(title):
    """A row in the open Settings tab, by the name it shows."""
    return f"#settingsBody .set-item:has(.set-item-text b:text-is('{title}'))"


class ModelDetails(HubTestCase):
    def facts(self, title):
        return self.page.locator(f"{row(title)} .set-facts").get_attribute("title")

    def details(self, title):
        return self.page.locator(f"{row(title)} .set-details")

    def button(self, title):
        return self.page.locator(f"{row(title)} [data-details]")

    def cfg(self, model_id):
        return self.app.cfg.models[model_id]

    def test_each_row_has_a_line_of_key_facts(self):
        self.open()
        self.open_settings("models")
        self.assertEqual(self.facts("whisper-medium code-switching"),
                         "OpenAI Whisper, fine-tuned by Seif-Eldeen-Sameh · faster-whisper · MIT · NVIDIA graphics only")
        self.assertEqual(self.facts("Cohere Transcribe Arabic"), "Cohere · transcribe.cpp · Apache-2.0 · Any graphics card")
        self.assertEqual(self.page.locator(f"{row('Cohere Transcribe Arabic')} .set-facts i").count(), 3, "a dot between facts")
        self.page.click("#settings [data-set-tab=hosted]")
        deepgram = self.cfg("deepgram")
        self.assertEqual(self.facts(deepgram["title"]), f"Deepgram · nova-3 · $0.26/h · {deepgram['facts'][-1]}")
        self.assertEqual(self.page.locator(f"{row(deepgram['title'])} .set-item-text span").inner_text(), deepgram["tagline"])
        self.assertEqual(self.facts("Azure AI Speech"), "Microsoft Azure · $0.36/h · Doesn't store your audio or transcript",
                         "no API model in the config: left out of the line")
        self.assertEqual(self.page.locator("#settings .set-details").count(), 0, "all rows start closed")

    def test_open_with_a_click_and_with_the_keyboard(self):
        self.open()
        self.open_settings("models")
        title = "Cohere Transcribe Arabic"
        expect(self.button(title)).to_have_attribute("aria-expanded", "false")
        self.page.click(f"{row(title)} .set-item-text b")  # anywhere on the row
        expect(self.details(title)).to_be_visible()
        expect(self.button(title)).to_have_attribute("aria-expanded", "true")
        self.assertEqual(self.button(title).get_attribute("aria-controls"), self.details(title).get_attribute("id"))
        self.page.click(f"{row(title)} .set-item-text b")
        expect(self.details(title)).to_have_count(0)

        self.button(title).focus()
        self.page.keyboard.press("Enter")
        expect(self.details(title)).to_be_visible()
        expect(self.button(title)).to_be_focused()  # focus stays on the button after the rows are redrawn
        self.page.keyboard.press("Space")
        expect(self.details(title)).to_have_count(0)
        expect(self.button(title)).to_have_attribute("aria-expanded", "false")
        expect(self.button(title)).to_be_focused()

    def test_one_open_at_a_time(self):
        self.open()
        self.open_settings("models")
        self.button("whisper-medium code-switching").click()
        expect(self.details("whisper-medium code-switching")).to_be_visible()
        self.button("Cohere Transcribe Arabic").click()
        expect(self.details("Cohere Transcribe Arabic")).to_be_visible()
        expect(self.page.locator("#settings .set-details")).to_have_count(1)
        expect(self.button("whisper-medium code-switching")).to_have_attribute("aria-expanded", "false")
        self.page.evaluate("() => renderSettings()")  # what every status poll does during a download
        expect(self.details("Cohere Transcribe Arabic")).to_be_visible()

    def test_a_built_in_models_details(self):
        self.open()
        self.open_settings("models")
        self.button("Cohere Transcribe Arabic").click()
        box = self.details("Cohere Transcribe Arabic")
        text = box.inner_text()
        wer = self.cfg("cohere")["facts"][0].split()[0]  # "13.4% WER on the public Egyptian set"
        for words in ("Cohere", "Apache-2.0", "transcribe.cpp", "cohere-transcribe-arabic-07-2026-Q4_K_M.gguf", "Q4_K_M",
                      "In the project's tests".upper(), f"{wer} (Perle's 40 Egyptian clips)", "77% of English terms on Perle",
                      "the test laptop's processor", "Intel Iris Xe Graphics", "1.6 GB", "Not downloaded",
                      self.cfg("cohere")["tagline"]):
            self.assertIn(words, text)
        revision = "5e6b33c211458ac69347d297abb6d47a250c328f"
        self.assertEqual(box.locator("a").evaluate_all("els => els.map(a => a.href)"),
                         [f"https://huggingface.co/{COHERE}", f"https://huggingface.co/{COHERE}/tree/{revision}"])
        self.button("whisper-medium code-switching").click()  # Whisper needs an NVIDIA card, which the demo laptop lacks
        text = self.details("whisper-medium code-switching").inner_text()
        self.assertIn("faster-whisper (CTranslate2)", text)
        self.assertIn("No NVIDIA graphics card was found on this computer", text)
        self.assertIn("Word timestamps", text, "the facts that aren't results follow")

    def test_a_hosted_services_details(self):
        self.open()
        self.open_settings("hosted")
        deepgram = self.cfg("deepgram")
        self.button(deepgram["title"]).click()
        text = self.details(deepgram["title"]).inner_text()
        for words in ("Deepgram", "nova-3", "ar-EG", "$200 free credit, then $0.26/h", deepgram["privacy"],
                      deepgram["speakers_hint"], "No key yet", "console.deepgram.com"):
            self.assertIn(words, text)
        self.button("Azure AI Speech").click()
        box = self.details("Azure AI Speech")
        self.assertIn("westeurope", box.inner_text())
        expect(box.locator("dt:text-is('API model') + dd")).to_have_text("Not stated")

    def test_the_actions_still_work_while_open(self):
        self.open()
        self.add("org/asr-gguf")
        self.open_settings("models")
        self.page.locator(f"{row('asr-gguf')} .status.ok:text-is('Downloaded')").wait_for()
        self.button("asr-gguf").click()
        expect(self.details("asr-gguf")).to_contain_text("On disk")
        self.page.once("dialog", lambda d: d.accept())
        self.page.click(f"{row('asr-gguf')} [data-dl-remove='{GGUF}']")  # Delete
        self.page.locator(".toast:has-text('Deleted')").wait_for()
        expect(self.page.locator(f"{row('asr-gguf')} .status")).to_have_text("Not downloaded")
        expect(self.details("asr-gguf")).to_be_visible()  # still open
        self.page.click(f"{row('asr-gguf')} [data-dl='{GGUF}']")  # Download
        self.page.locator(f"{row('asr-gguf')} .status.ok:text-is('Downloaded')").wait_for()
        expect(self.details("asr-gguf")).to_be_visible()

        self.addCleanup(self.app.cfg.save_key, "gemini", "")
        self.page.click("#settings [data-set-tab=hosted]")
        self.button("Google Gemini").click()
        expect(self.details("Google Gemini")).to_be_visible()
        self.page.click("[data-key-toggle='gemini']")  # the key form takes the place of the details
        expect(self.details("Google Gemini")).to_have_count(0)
        self.page.fill("[data-key='gemini']", "test-key-123")
        self.page.click("[data-key-form='gemini'] button[type=submit]")
        self.page.wait_for_selector(".toast >> text=Key saved")
        self.button("Google Gemini").click()
        expect(self.details("Google Gemini")).to_contain_text("Saved in the app")
        self.page.click("[data-key-toggle='gemini']")
        expect(self.page.locator("[data-key='gemini']")).to_be_focused()

    def test_a_model_added_from_hugging_face_shows_its_repository_and_revision(self):
        self.open()
        model_id = self.add("org/asr-gguf", "asr-Q8_0.gguf")
        revision = self.app.cfg.models[model_id]["hub"]["revision"]
        self.open_settings("models")
        self.assertEqual(self.facts("asr-gguf"), "transcribe.cpp · Apache-2.0 · Any graphics card")
        self.button("asr-gguf").click()
        box = self.details("asr-gguf")
        self.assertEqual(box.locator("a").evaluate_all("els => els.map(a => a.href)"),
                         ["https://huggingface.co/org/asr-gguf", f"https://huggingface.co/org/asr-gguf/tree/{revision}"])
        text = box.inner_text()
        for words in ("org, on Hugging Face", f"pinned to revision {revision[:7]}", "GGUF for transcribe.cpp (NVIDIA Parakeet)",
                      "asr-Q8_0.gguf", "Apache-2.0", "NVIDIA Parakeet model"):
            self.assertIn(words, text)
        expect(box.locator("dt:text-is('Results') + dd")).to_have_text("Not stated")  # no catalog entry, no figures

    def test_a_recommended_rows_details_and_add(self):
        mid = hub.model_id(self.app.cfg, COHERE)
        self.addCleanup(self.forget, mid)
        self.open()
        self.open_settings("add")
        entry = next(c for c in hub.recommended() if c.get("repo") == COHERE)
        title = entry["name"]
        self.assertEqual(self.facts(title), "handy-computer · transcribe.cpp · 2.4 GB · Apache-2.0 · Any graphics card")
        self.page.click(f"{row(title)} .set-item-text b")
        box = self.details(title)
        for words in (entry["evidence"], "cohere-transcribe-arabic-07-2026-Q6_K.gguf (2.0 GB)", "GGUF for transcribe.cpp (cohere_asr)",
                      f"pinned to revision {entry['revision'][:7]}"):
            self.assertIn(words, box.inner_text())
        self.page.locator(f"{row(title)} select").select_option("cohere-transcribe-arabic-07-2026-Q6_K.gguf")
        expect(box).to_be_visible()
        self.page.click(f"{row(title)} [data-catalog-add]:has-text('Add (2.0 GB)')")
        self.page.locator(".toast:has-text('Added Cohere Transcribe Arabic, higher precision')").wait_for()
        self.page.locator(f"{row(title)} .status.ok:text-is('Added: Q6_K')").wait_for()
        self.button("Cohere Transcribe Arabic").click()  # a built-in one: where its files come from
        self.assertIn("Built in, runs with transcribe.cpp", self.details("Cohere Transcribe Arabic").inner_text())
        expect(self.page.locator("#settings .set-details")).to_have_count(1)


if __name__ == "__main__":
    unittest.main()
