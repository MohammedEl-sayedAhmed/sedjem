"""Browser tests of the models added from Hugging Face: what was checked by hand while building it, in headless
Chromium against a Sedjem server (tests/ui_support.py) whose Hugging Face is the local stand-in of
tests/test_hub.py, so nothing comes from the internet. The model files are a few hundred bytes.
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui*.py" -v
"""
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

from ui_support import UiTestCase  # first: it puts the project on sys.path

from app import hub  # noqa: E402
from test_hub import REPOS, FakeHub, stand_in  # noqa: E402

GGUF = "hf-org--asr-gguf"
WHISPER_HF = "hf-org--whisper-hf"


class HubTestCase(UiTestCase):
    """A Sedjem server whose Hugging Face is the stand-in (the catalog's repositories included), without the
    speaker models' downloads (TitaNet-small's would come from GitHub), and where Transformers checkpoints can't
    be converted, as in the desktop build and in CI."""

    @classmethod
    def setUpClass(cls):
        cls.fake = ThreadingHTTPServer(("127.0.0.1", 0), FakeHub)
        threading.Thread(target=cls.fake.serve_forever, daemon=True).start()
        cls.patches = [
            mock.patch.object(hub, "BASE", f"http://127.0.0.1:{cls.fake.server_address[1]}"),
            mock.patch.dict(REPOS, {c["repo"]: stand_in(c) for c in hub.recommended() if not c.get("builtin")}),
            mock.patch.object(hub, "can_convert", return_value=False),
        ]
        for p in cls.patches:
            p.start()
        try:
            super().setUpClass()
        except BaseException:
            cls.stop_fake()
            raise
        cls.app.cfg.local = {**cls.app.cfg.local, "speaker_models": []}

    @classmethod
    def stop_fake(cls):
        for p in reversed(cls.patches):
            p.stop()
        cls.fake.shutdown()
        cls.fake.server_close()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.stop_fake()

    def add(self, link, file=None):
        """Add a model through the API (which starts its download), and reload so the page knows of it."""
        r = self.api("POST", "/api/hub/add", {"url": link, "file": file})
        self.assertEqual(r["status"], 200, r["json"])
        model_id = r["json"]["models"][-1]["id"]
        self.addCleanup(self.forget, model_id)
        self.page.reload()
        self.page.wait_for_function("() => typeof S !== 'undefined' && S.status")
        return model_id

    def forget(self, model_id):
        """Remove an added model after a test, whatever happened in it."""
        if model_id in self.app.cfg.models:
            self.app.downloads.remove(model_id)
            hub.forget(self.app.cfg, model_id)

    def card(self, model_id, ready=True):
        return self.page.locator(f".model-card[data-model='{model_id}']" + (":not(.unavailable)" if ready else ""))


class AddedModelCards(HubTestCase):
    """New transcription: the cards of models added from Hugging Face."""

    def test_a_card_with_its_pill_and_facts(self):
        self.open()
        self.add("org/asr-gguf", "asr-Q8_0.gguf")
        card = self.card(GGUF)
        card.wait_for()
        self.assertEqual(card.locator(".mc-badges .pill").all_inner_texts(), ["On this computer", "From Hugging Face"])
        self.assertEqual(card.locator(".mc-title").inner_text(), "asr-gguf")
        self.assertEqual(card.locator(".mc-tag").inner_text(), "NVIDIA Parakeet model")
        self.assertEqual(card.locator("li").all_inner_texts(),
                         ["org/asr-gguf", "GGUF, parakeet: asr-Q8_0.gguf", "572 B download", "Licence: Apache-2.0"])
        self.assertIn("the recording's length on this computer", card.locator(".mc-foot").inner_text())
        card.click()
        self.page.locator(f".model-card[data-model='{GGUF}'][aria-checked='true']").wait_for()
        # a GGUF model runs through transcribe.cpp, so on the demo laptop's Intel graphics
        self.assertIn("on this computer's graphics card", self.page.locator(".estimate").inner_text())
        self.assertEqual(self.page.locator(".model-card[data-model='whisper-medium'] .pill:text-is('From Hugging Face')").count(), 0)

    def test_download_from_the_card(self):
        self.open()
        self.add("org/asr-gguf")
        self.card(GGUF).wait_for()
        self.assertEqual(self.api("DELETE", f"/api/downloads/{GGUF}")["status"], 200)  # the files, not the model
        self.page.reload()
        self.page.wait_for_function("() => typeof S !== 'undefined' && S.status")
        card = self.card(GGUF, ready=False)
        self.assertEqual(card.locator(".mc-missing").first.inner_text(), "Not downloaded yet")
        self.assertEqual(card.locator(f"[data-dl='{GGUF}']").inner_text().strip(), "Download (372 B)")
        card.locator(f"[data-dl='{GGUF}']").click()
        self.card(GGUF).wait_for()
        self.assertTrue((self.home / "models/hf/org--asr-gguf/asr-Q4_K_M.gguf").is_file())

    def test_a_failed_conversion_says_why_and_offers_to_convert(self):
        failed = RuntimeError("the conversion failed: out of memory")
        with mock.patch.object(hub, "can_convert", return_value=True), mock.patch.object(hub, "convert", side_effect=failed):
            self.open()
            self.add("org/whisper-hf")
            card = self.card(WHISPER_HF, ready=False)
            card.locator(".mc-missing:text-is('the conversion failed: out of memory')").wait_for()
            self.assertEqual(card.locator(".mc-missing").first.inner_text(), "Downloaded, not converted yet")
            self.assertEqual(card.locator(f"[data-dl='{WHISPER_HF}']").inner_text().strip(), "Convert")
            self.assertEqual(card.locator(".mc-badges .pill").all_inner_texts()[1:], ["From Hugging Face"])


if __name__ == "__main__":
    unittest.main()
