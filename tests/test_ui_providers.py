"""Browser tests of the hosted providers in the interface, in headless Chromium (see tests/ui_support.py):
the Azure region saved next to its key, the cards, hints and upload consent of the added providers, and
that a hosted model starts only after the upload is confirmed. The job at the end goes to a local stand-in
for Deepgram, so nothing leaves the computer.
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui*.py" -v
"""
import json
import os
import re
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import soundfile as sf

from ui_support import UiTestCase

try:
    from playwright.sync_api import expect
except ImportError:  # UiTestCase skips the tests
    expect = None

# Deepgram's pre-recorded reply (utterances with punctuated words and speakers), shortened
DEEPGRAM_REPLY = {"metadata": {"request_id": "r1", "duration": 2.0, "channels": 1}, "results": {
    "channels": [{"alternatives": [{"transcript": "تمام، نبدأ ال sprint. Yes, let's start.", "confidence": 0.9, "words": []}]}],
    "utterances": [
        {"start": 0.1, "end": 1.0, "confidence": 0.9, "channel": 0, "speaker": 0, "id": "u0", "transcript": "تمام، نبدأ ال sprint.",
         "words": [{"word": w, "punctuated_word": p, "start": s, "end": e, "confidence": 0.9, "speaker": 0}
                   for w, p, s, e in (("تمام", "تمام،", 0.1, 0.3), ("نبدأ", "نبدأ", 0.35, 0.6), ("ال", "ال", 0.62, 0.7),
                                      ("sprint", "sprint.", 0.72, 1.0))]},
        {"start": 1.3, "end": 1.9, "confidence": 0.9, "channel": 0, "speaker": 1, "id": "u1", "transcript": "Yes, let's start.",
         "words": [{"word": w, "punctuated_word": p, "start": s, "end": e, "confidence": 0.9, "speaker": 1}
                   for w, p, s, e in (("yes", "Yes,", 1.3, 1.5), ("let's", "let's", 1.52, 1.7), ("start", "start.", 1.72, 1.9))]}]}}


class DeepgramStandIn(BaseHTTPRequestHandler):
    """Answers /v1/listen the way Deepgram's pre-recorded API does and keeps what it received."""
    calls = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        path, _, query = self.path.partition("?")
        self.calls.append({"path": path, "query": urllib.parse.parse_qs(query), "auth": self.headers.get("Authorization"),
                           "type": self.headers.get("Content-Type"), "head": body[:4]})
        reply = json.dumps(DEEPGRAM_REPLY, ensure_ascii=False).encode()
        self.send_response(200 if path == "/v1/listen" else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)


class Ignoring(list):
    """The page's errors, leaving out the ones a test causes on purpose."""

    def __init__(self, *expected):
        super().__init__()
        self.expected = expected

    def append(self, text):
        if not any(x in text for x in self.expected):
            super().append(text)


class HostedProviders(UiTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for m in cls.app.cfg.models.values():  # keys and regions come only from what the tests save
            for var in (m.get("key_env"), m.get("region_env")):
                if var:
                    os.environ.pop(var, None)
        cls.standin = ThreadingHTTPServer(("127.0.0.1", 0), DeepgramStandIn)
        threading.Thread(target=cls.standin.serve_forever, daemon=True).start()
        cls.app.cfg.models["deepgram"]["base_url"] = f"http://127.0.0.1:{cls.standin.server_address[1]}"
        cls.recording = cls.home / "call.wav"
        t = np.arange(2 * 16000) / 16000
        sf.write(cls.recording, (0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32), 16000)

    @classmethod
    def tearDownClass(cls):
        cls.standin.shutdown()
        cls.standin.server_close()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        DeepgramStandIn.calls.clear()

    def added(self):
        """The hosted models the provider branches added: the ones with their own upload-consent text."""
        return [m for m in self.api("GET", "/api/status")["json"]["models"] if m["kind"] == "hosted" and m.get("privacy")]

    def save_keys(self, *ids):
        """Through the app's API (so the page must be open), then load the page again to use them."""
        for mid in ids:
            self.assertEqual(self.api("POST", "/api/keys", {"model": mid, "key": f"{mid}-test-key"})["status"], 200)
            self.addCleanup(self.app.cfg.save_key, mid, "")  # after the page is closed, so not through it
        self.page.reload()  # open() on the same route would only change the hash
        self.page.wait_for_function("() => typeof S !== 'undefined' && S.status")

    def azure(self):
        m = next(x for x in self.api("GET", "/api/status")["json"]["models"] if x["id"] == "azure")
        return m["key_source"], m["region"]

    def test_azure_region_is_saved_with_the_key(self):
        self.errors = Ignoring("status of 400")  # the refused region below is logged by the browser
        self.addCleanup(self.app.cfg.save_key, "azure", "")
        self.addCleanup(self.app.cfg.save_key, "azure:region", "")
        self.open()
        self.open_settings("hosted")
        row = self.page.locator("#key-azure")
        open_row = lambda: row.locator("[data-key-toggle]").click()  # a key row opens only when asked
        open_row()
        expect(row.locator("[data-region]")).to_have_value("westeurope")  # the config's default
        self.page.locator("#key-deepgram [data-key-toggle]").click()
        expect(self.page.locator("#key-deepgram [data-region]")).to_have_count(0)  # only Azure has a region
        open_row()

        row.locator("[data-key]").fill("az-test-key")
        row.locator("[data-region]").fill("NorthEurope")
        row.locator("button[type=submit]").click()
        self.page.wait_for_selector(".toast >> text=Key saved")
        self.assertEqual(self.azure(), ("app", "northeurope"))
        expect(row.locator(".status")).to_have_text("Key saved")
        open_row()
        expect(row.locator("[data-region]")).to_have_value("northeurope")

        row.locator("[data-region]").fill("westus2")  # a region alone keeps the key
        row.locator("button[type=submit]").click()
        self.page.wait_for_selector(".toast >> text=Region saved")
        self.assertEqual(self.azure(), ("app", "westus2"))

        open_row()
        row.locator("[data-region]").fill("west europe")  # not a region name: refused with the reason
        row.locator("button[type=submit]").click()
        self.page.wait_for_selector(".toast.error >> text=a region is a name like westeurope")
        self.assertEqual(self.azure(), ("app", "westus2"))

        self.page.once("dialog", lambda d: d.accept())  # "Remove the saved key?"
        row.locator("[data-clear-key]").click()
        self.page.wait_for_selector(".toast >> text=Key removed")
        self.assertEqual(self.azure(), (None, "westus2"), "removing the key keeps the region")
        expect(row.locator(".status")).to_have_text("No key")

    def test_cards_hints_and_consent_of_the_added_providers(self):
        self.open()
        providers = self.added()
        self.assertLessEqual({"gemini", "deepgram", "assemblyai", "azure"}, {m["id"] for m in providers})
        for m in providers:  # without a key the card says what the model is and asks for one
            card = self.page.locator(f".model-card[data-model='{m['id']}']")
            expect(card).to_have_class(re.compile(r"\bunavailable\b"))
            expect(card.locator(".mc-badges")).to_contain_text(f"Uploads to {m['service']}")
            expect(card.locator(".mc-title")).to_have_text(m["title"])
            expect(card.locator(".mc-tag")).to_have_text(m["tagline"])
            expect(card.locator("li")).to_have_text(m["facts"])
            expect(card.locator(".mc-foot")).to_contain_text("Needs an API key")
        self.page.click(".model-card[data-model='azure'] [data-open-settings]")  # "add it" goes to the key
        expect(self.page.locator("#settings")).to_have_attribute("open", "")
        expect(self.page.locator("[data-key='azure']")).to_be_focused()

        self.save_keys(*(m["id"] for m in providers))
        for m in providers:  # with a key: picked, it shows its own hints and what the upload means
            card = self.page.locator(f".model-card[data-model='{m['id']}']")
            expect(card).not_to_have_class(re.compile(r"\bunavailable\b"))
            card.click()
            expect(card).to_have_attribute("aria-checked", "true")
            consent = self.page.locator(".card.consent")
            expect(consent).to_contain_text(f"This model uploads the recording to {m['service']}.")
            expect(consent).to_contain_text(m["privacy"])
            expect(consent.locator("label")).to_have_text(f"Upload this recording to {m['service']}")
            expect(self.page.locator("#spk ~ p.hint")).to_have_text(m["speakers_hint"])
            if m["prompt"]:
                expect(self.page.locator("#promptInput ~ p.hint")).to_have_text(m["prompt_hint"])
            else:
                expect(self.page.locator("#promptInput")).to_have_count(0)

    def test_a_hosted_model_starts_only_after_the_upload_is_confirmed(self):
        self.open()
        self.save_keys("deepgram", "assemblyai")
        self.page.click("details.path summary")
        self.page.fill("#pathInput", str(self.recording))
        self.page.click("#pathBtn")
        self.page.wait_for_selector(".chosen .name >> text=call.wav")
        expect(self.page.locator(".model-card[data-model='deepgram']")).not_to_have_class(re.compile(r"\bunavailable\b"))
        self.page.click(".model-card[data-model='deepgram']")
        start, box = self.page.locator("#startBtn"), self.page.locator("#confirmUpload")
        expect(box).not_to_be_checked()
        expect(start).to_be_disabled()
        box.click()
        expect(box).to_be_checked()
        expect(start).to_be_enabled()
        box.click()
        expect(start).to_be_disabled()

        box.click()  # the confirmation is for one model: another hosted model asks again
        expect(start).to_be_enabled()
        self.page.click(".model-card[data-model='assemblyai']")
        expect(box).not_to_be_checked()
        expect(start).to_be_disabled()
        self.page.click(".model-card[data-model='deepgram']")
        expect(start).to_be_disabled()
        self.assertEqual(DeepgramStandIn.calls, [], "nothing was sent before the upload was confirmed")

        box.click()
        start.click()
        # the job converts the recording, sends it to the stand-in and shows the transcript (slower on CI)
        expect(self.page.locator(".line .text")).to_have_text(["تمام، نبدأ ال sprint.", "Yes, let's start."], timeout=30_000)
        self.assertIn("#/job/", self.page.url)
        self.assertEqual(len(DeepgramStandIn.calls), 1)
        call = DeepgramStandIn.calls[0]
        self.assertEqual((call["path"], call["auth"], call["type"], call["head"]),
                         ("/v1/listen", "Token deepgram-test-key", "audio/flac", b"fLaC"))
        self.assertEqual(call["query"]["mip_opt_out"], ["true"])


if __name__ == "__main__":
    unittest.main()
