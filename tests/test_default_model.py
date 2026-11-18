"""Tests for the default model (#6): the model a new transcription starts with, chosen in Settings → Models and saved
as default_model in settings.json, over [defaults] model in app/config.toml; a choice that is no longer one of the
models falls back to the config's. The status gives it to the page, and the API uses it for a job that names no model.
Run: .venv/bin/python -m unittest tests.test_default_model -v
"""
import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Config  # noqa: E402
from app.server import make_server  # noqa: E402


class Settings(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="sedjem-default-model-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.cfg = Config(home=self.home)

    def test_none_chosen_is_the_configs(self):
        self.assertIsNone(self.cfg.settings()["default_model"])
        self.assertEqual(self.cfg.default_model(), self.cfg.defaults["model"])
        self.assertEqual(self.cfg.default_model(), "whisper-medium")

    def test_the_choice_is_saved_and_kept_across_restarts(self):
        self.assertEqual(self.cfg.save_settings(default_model="elevenlabs")["default_model"], "elevenlabs")
        self.assertEqual(json.loads(self.cfg.settings_path.read_text(encoding="utf-8"))["default_model"], "elevenlabs")
        self.assertEqual(Config(home=self.home).default_model(), "elevenlabs")
        self.cfg.save_settings(default_model=None)  # the config's again
        self.assertEqual(Config(home=self.home).default_model(), "whisper-medium")

    def test_what_isnt_a_model_is_refused(self):
        for bad in ("nope", "", 1, True, ["elevenlabs"], {"id": "elevenlabs"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.cfg.save_settings(default_model=bad)
        self.assertNotIn("default_model", self.cfg.saved_settings())

    def test_a_choice_that_is_no_longer_a_model_falls_back(self):
        """A model added from Hugging Face and removed again, or a settings.json edited by hand."""
        for saved in ("hf-removed-model", ["elevenlabs"], 7):
            with self.subTest(saved=saved):
                self.cfg.settings_path.parent.mkdir(parents=True, exist_ok=True)
                self.cfg.settings_path.write_text(json.dumps({"default_model": saved}), encoding="utf-8")
                self.assertEqual(Config(home=self.home).default_model(), "whisper-medium")


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = Path(tempfile.mkdtemp(prefix="sedjem-default-model-api-"))
        cls.server, cls.app = make_server(Config(home=cls.home), port=0)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.home, ignore_errors=True)

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method,
                                     headers={"X-Sedjem": "1", "X-Sedjem-Token": self.app.session.secret,
                                              "Content-Type": "application/json"},
                                     data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def tearDown(self):
        self.app.cfg.save_settings(default_model=None)

    def test_the_status_gives_the_page_the_model_chosen(self):
        status, st = self.call("GET", "/api/status")
        self.assertEqual((status, st["defaults"]["model"], st["settings"]["default_model"]), (200, "whisper-medium", None))
        status, st = self.call("POST", "/api/settings", {"default_model": "elevenlabs"})
        self.assertEqual(status, 200, st)
        self.assertEqual((st["defaults"]["model"], st["settings"]["default_model"]), ("elevenlabs", "elevenlabs"))
        self.assertEqual((st["defaults"]["speakers"], st["defaults"]["language"]), ("auto", "ar"), "the other defaults stay")
        self.assertEqual(self.app.cfg.defaults["model"], "whisper-medium", "the config itself isn't changed")

    def test_a_job_that_names_no_model_gets_the_one_chosen(self):
        status, body = self.call("POST", "/api/jobs", {"path": str(self.home / "meeting.wav")})
        self.assertEqual(status, 400)
        self.assertIn("whisper-medium", body["error"])  # the config's model, not downloaded here
        self.call("POST", "/api/settings", {"default_model": "elevenlabs"})
        status, body = self.call("POST", "/api/jobs", {"path": str(self.home / "meeting.wav")})
        self.assertEqual(status, 400)
        self.assertIn("ElevenLabs", body["error"])  # the one chosen: no key here, or no consent to the upload

    def test_a_bad_choice_is_refused(self):
        status, body = self.call("POST", "/api/settings", {"default_model": "nope"})
        self.assertEqual(status, 400)
        self.assertIn("default_model", body["error"])
        self.assertEqual(self.call("GET", "/api/status")[1]["defaults"]["model"], "whisper-medium")


if __name__ == "__main__":
    unittest.main()
