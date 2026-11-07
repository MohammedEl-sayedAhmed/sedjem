"""Browser tests of the speaker model choice in Settings → Models: the saved choice is shown, choosing another saves it,
with the mouse or the keyboard, the chosen row says what labels the speakers while it isn't downloaded, and each
speaker model downloads and deletes like the other models. Headless Chromium against a Sedjem server (see
tests/ui_support.py) whose speaker models are small stand-ins from a local server, so nothing comes from the internet
and no model runs.
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui*.py" -v
"""
import hashlib
import re
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from ui_support import UiTestCase  # first: it puts the project on sys.path

from playwright.sync_api import expect  # noqa: E402

IDS = ["titanet-small", "titanet-large", "campplus", "nemotron-3-diarization"]
NOTE = "Not downloaded: TitaNet-small is used until it is."


class Files(BaseHTTPRequestHandler):
    """Serves the stand-ins' bytes by path."""
    payloads = {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = self.payloads.get(self.path)
        self.send_response(200 if body is not None else 404)
        self.send_header("Content-Length", str(len(body or b"")))
        self.end_headers()
        self.wfile.write(body or b"")


class SpeakerModels(UiTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.files = ThreadingHTTPServer(("127.0.0.1", 0), Files)
        threading.Thread(target=cls.files.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{cls.files.server_address[1]}"
        models = []
        for m in cls.app.cfg.local["speaker_models"]:  # the config's, with small files of their own
            files = []
            for i, f in enumerate(m["files"]):
                body = f"{m['id']} {i} ".encode() * 40
                Files.payloads[f"/{m['id']}/{i}"] = body
                files.append({**f, "url": f"{url}/{m['id']}/{i}", "size": len(body), "sha256": hashlib.sha256(body).hexdigest()})
            models.append({**m, "files": files})
        cls.app.cfg.local = {**cls.app.cfg.local, "speaker_models": models}

    @classmethod
    def tearDownClass(cls):
        cls.files.shutdown()
        cls.files.server_close()
        super().tearDownClass()

    def setUp(self):
        super().setUp()  # each test starts with TitaNet-small downloaded and chosen, and nothing else
        cfg = self.app.cfg
        for m in cfg.speaker_models().values():
            self.app.downloads.remove(m["item"])
        self.put("titanet-small")
        cfg.save_settings(speaker_model="titanet-small")

    def put(self, model_id):
        """A speaker model's files, as its download leaves them."""
        for i, f in enumerate(self.app.cfg.speaker_models()[model_id]["files"]):
            p = self.app.cfg.path(f["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(Files.payloads[f"/{model_id}/{i}"])

    def radio(self, model_id):
        return self.page.locator(f"#spk-{model_id}")

    def row(self, model_id):
        return self.page.locator(f"#settings .spk-models .set-item:has(#spk-{model_id})")

    def status(self):
        return self.api("GET", "/api/status")["json"]["speaker_models"]

    def open_models(self):
        self.open()
        self.open_settings("models")
        self.page.locator("#spk-head").scroll_into_view_if_needed()

    def test_the_saved_choice_is_shown(self):
        self.app.cfg.save_settings(speaker_model="campplus")
        self.open_models()
        group = self.page.get_by_role("radiogroup", name="Speaker labels")
        expect(group.get_by_role("radio")).to_have_count(4)
        self.assertEqual(group.get_by_role("radio").evaluate_all("rs => rs.map(r => r.value)"), IDS)
        expect(group.get_by_role("radio", name="3D-Speaker CAM++", exact=True)).to_be_checked()
        for name in ("TitaNet-small", "TitaNet-large", "Nemotron-3-Diarization"):
            expect(group.get_by_role("radio", name=name, exact=True)).not_to_be_checked()
        # the chosen one isn't downloaded: its row says what labels the speakers meanwhile, and only its row
        expect(self.row("campplus").locator(".spk-note")).to_have_text(NOTE)
        expect(self.page.locator("#settings .spk-note")).to_have_count(1)
        expect(self.radio("campplus")).to_have_accessible_description(re.compile(re.escape(NOTE)))
        expect(self.radio("titanet-small")).to_have_accessible_description(re.compile(r"the default · .* on this computer"))
        expect(self.radio("nemotron-3-diarization")).to_have_accessible_description(re.compile(r"up to 8 speakers · .* to download"))
        expect(self.row("titanet-small").locator(".status")).to_have_text("Downloaded")
        expect(self.row("campplus").locator(".status")).to_have_text("Not downloaded")
        expect(self.row("campplus").locator("[data-dl]")).to_have_attribute("data-dl", "campplus")
        expect(self.row("titanet-small").locator("[data-dl-remove]")).to_have_attribute("data-dl-remove", "voiceprints")

    def test_choosing_another_saves_it(self):
        self.open_models()
        expect(self.radio("titanet-small")).to_be_checked()
        self.row("titanet-large").locator(".set-item-text b").click()  # the name is the radio button's label
        self.page.wait_for_selector(".toast >> text=Saved")
        expect(self.radio("titanet-large")).to_be_checked()
        expect(self.radio("titanet-small")).not_to_be_checked()
        self.assertEqual(self.app.cfg.saved_settings()["speaker_model"], "titanet-large")
        self.assertEqual((self.status()["chosen"], self.status()["used"]), ("titanet-large", "titanet-small"))
        expect(self.row("titanet-large").locator(".spk-note")).to_have_text(NOTE)
        self.page.reload()
        self.page.wait_for_function("() => typeof S !== 'undefined' && S.status")
        self.open_settings("models")
        expect(self.radio("titanet-large")).to_be_checked()

    def test_the_keyboard(self):
        self.open_models()
        self.radio("titanet-small").focus()
        self.page.keyboard.press("ArrowDown")  # moves to the next one and chooses it, as radio buttons do
        self.page.wait_for_function("() => S.status.speaker_models.chosen === 'titanet-large'")
        expect(self.radio("titanet-large")).to_be_checked()
        expect(self.radio("titanet-large")).to_be_focused()  # still there once Settings is drawn again
        self.page.keyboard.press("ArrowDown")
        self.page.wait_for_function("() => S.status.speaker_models.chosen === 'campplus'")
        expect(self.radio("campplus")).to_be_focused()
        self.page.keyboard.press("ArrowUp")
        self.page.wait_for_function("() => S.status.speaker_models.chosen === 'titanet-large'")
        expect(self.radio("titanet-large")).to_be_checked()
        expect(self.radio("titanet-large")).to_be_focused()
        self.assertEqual(self.app.cfg.saved_settings()["speaker_model"], "titanet-large")
        # Tab leaves the group at the chosen row's own button; Shift+Tab comes back to the chosen radio button
        self.page.keyboard.press("Tab")
        expect(self.row("titanet-large").locator("[data-dl]")).to_be_focused()
        self.page.keyboard.press("Shift+Tab")
        expect(self.radio("titanet-large")).to_be_focused()

    def test_download_choose_and_delete(self):
        self.open_models()
        nemotron = self.row("nemotron-3-diarization")
        nemotron.locator("[data-dl]").click()
        expect(nemotron.locator(".status")).to_have_text("Downloaded", timeout=20_000)
        folder = self.app.cfg.path("models/diarization/Nemotron-3-Diarization")
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["config.json", "model.safetensors", "processor_config.json"])
        self.radio("nemotron-3-diarization").click()  # (Settings is drawn again once it is saved)
        self.page.wait_for_function("() => S.status.speaker_models.used === 'nemotron-3-diarization'")
        expect(self.radio("nemotron-3-diarization")).to_be_checked()
        expect(self.page.locator("#settings .spk-note")).to_have_count(0)
        # deleting the chosen one: TitaNet-small takes over, and the row says so
        self.page.once("dialog", lambda d: d.accept())
        nemotron.locator("[data-dl-remove]").click()
        expect(nemotron.locator(".spk-note")).to_have_text(NOTE)
        self.assertFalse(folder.exists())
        self.assertEqual(self.status()["used"], "titanet-small")
        # and without TitaNet-small there are no speaker labels
        self.page.once("dialog", lambda d: d.accept())
        self.row("titanet-small").locator("[data-dl-remove]").click()
        expect(nemotron.locator(".spk-note")).to_have_text("Not downloaded: there are no speaker labels until it is.")
        self.assertIsNone(self.status()["used"])

    def test_what_the_new_transcription_page_says(self):
        self.put("nemotron-3-diarization")
        self.app.cfg.save_settings(speaker_model="nemotron-3-diarization")
        self.open()  # a local model is picked (whisper-medium, the default)
        hint = self.page.locator("#spkHint")
        self.page.fill("#spkN", "10")
        expect(hint).to_contain_text("Nemotron-3-Diarization tells at most 8 people apart, so it labels up to 8.")
        self.page.fill("#spkN", "8")
        expect(hint).not_to_contain_text("tells at most")
        for m in self.app.cfg.speaker_models().values():
            self.app.downloads.remove(m["item"])
        self.page.reload()
        self.page.wait_for_function("() => typeof S !== 'undefined' && S.status")
        expect(hint).to_contain_text("No speaker model is downloaded, so local models will skip speaker labels.")
        self.page.click("#spk button[data-v='none']")
        expect(hint).to_have_text("One block of text with timestamps, no names.")

    def test_dark_and_narrow(self):
        self.page.set_viewport_size({"width": 420, "height": 860})
        self.app.cfg.save_settings(speaker_model="nemotron-3-diarization")
        self.open_models()
        self.page.evaluate("() => { document.documentElement.dataset.theme = 'dark'; }")
        row = self.row("nemotron-3-diarization")
        row.scroll_into_view_if_needed()
        box, radio, text = (row.bounding_box(), self.radio("nemotron-3-diarization").bounding_box(),
                            row.locator(".set-item-text").bounding_box())
        self.assertLessEqual(box["x"] + box["width"], 420, "no row runs past the window")
        self.assertLess(radio["x"] + radio["width"], text["x"], "the radio button comes before the name")
        self.assertGreater(text["width"], 200, "the name and its line keep room to read")
        self.assertEqual(self.page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"), True)


if __name__ == "__main__":
    unittest.main()
