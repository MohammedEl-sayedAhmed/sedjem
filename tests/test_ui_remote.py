"""Browser tests of running jobs on another computer (app/static/remote.js): Settings → Other computers, with the switch
that is off at first, the pairing code it shows once switched on, and pairing with another Sedjem by its address and
code; then the New transcription form with the choice of where to run, the models the other computer has, the tick
asked the first time, a job sent there and its details saying where it ran; and a paired computer that doesn't answer.
Headless Chromium against this app (the sending side, see tests/ui_support.py) and a second Sedjem in this process that
runs the jobs, with a stand-in for its models (as in tests/test_remote.py).
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui_remote.py" -v
"""
import re
import unittest
from unittest import mock

import numpy as np
import soundfile as sf
from test_remote import COHERE, LOOPBACK, REAL_PRIVATE, Computer, StandIn, wait_for
from ui_support import UiTestCase  # (puts the project on the path)

from app import engines, remote


class OtherComputers(UiTestCase):
    config = '[remote]\nport = 0\nname = "Small laptop"\n'

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.patches = [mock.patch.object(remote, "private", side_effect=lambda ip: REAL_PRIVATE(ip) or ip in LOOPBACK)]
        cls.stand_in = StandIn()
        cls.patches.append(mock.patch.object(engines, "run_local", side_effect=cls.stand_in))
        for p in cls.patches:
            p.start()
        cls.b = Computer("GPU laptop", [COHERE, "models/faster-whisper-large-v3/model.bin"])
        cls.b.app.cfg.save_settings(remote_serve=True)
        cls.b.app.remote.apply()
        cls.wav = cls.home / "call.wav"
        t = np.arange(int(16000 * 2.5)) / 16000
        sf.write(cls.wav, (0.2 * np.sin(2 * np.pi * 330 * t)).astype(np.float32), 16000)

    @classmethod
    def tearDownClass(cls):
        cls.b.close()
        for p in reversed(cls.patches):
            p.stop()
        super().tearDownClass()

    def texts(self, selector):
        return self.page.eval_on_selector_all(selector, "els => els.map(e => e.innerText.replace(/\\s+/g, ' ').trim())")

    def open_tab(self):
        self.open_settings("remote")
        self.page.wait_for_selector("#settings .rm-sec")

    def pair(self):
        """Pair this app with the GPU laptop through the form in Settings."""
        code = self.b.app.remote.open_code()["code"]
        self.open_tab()
        self.page.fill("#rmAddress", f"127.0.0.1:{self.b.app.remote.listening_port()}")
        self.page.fill("#rmCode", code.lower())
        self.page.click("#rmPairBtn")
        self.page.wait_for_selector(".toast >> text=Paired with GPU laptop")
        self.page.wait_for_selector("#settings .rm-computers >> text=GPU laptop")
        self.page.click("#settings [data-close] >> nth=0")

    def test_the_tab_and_the_switch(self):
        self.open()
        self.open_tab()
        self.assertEqual(self.texts("#settings .rm-sec > h4"), ["Send jobs to another computer", "Run jobs for other computers"])
        self.assertIn("travels only over your local network, encrypted", self.page.inner_text("#settings .rm-intro"))
        switch = "#settings [data-setting=remote_serve]"
        self.assertFalse(self.page.is_checked(switch), "off at first")
        hint = self.page.inner_text("#settings .rm-sec:last-child .set-row .hint")
        self.assertIn("Off unless you turn it on", hint)
        self.assertIn("Tailscale (100.64.0.0/10)", hint)
        self.assertEqual(self.page.locator("#rmShowCode").count(), 0, "no code while it is off")
        self.assertIsNone(self.app.remote.listener)
        self.page.locator(switch).check()
        self.page.wait_for_selector("#rmShowCode")
        self.assertIsNotNone(self.app.remote.listener, "switched on, it listens")
        self.assertIn("This computer is Small laptop", self.page.inner_text("#settings .rm-here"))
        self.page.click("#rmShowCode")
        code = self.page.inner_text("#rmCodeText")
        self.assertRegex(code, r"^[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}$")
        self.assertEqual(code, remote.shown_code(self.app.remote.code["code"]))
        self.assertIn("It works once, for 10 minutes", self.page.inner_text("#settings .rm-code"))
        self.assertRegex(self.page.inner_text("#settings .rm-print code"), r"^([0-9A-F]{2}:){31}[0-9A-F]{2}$")
        self.page.click("#rmCloseCode")
        self.page.wait_for_selector("#rmShowCode")
        self.assertIsNone(self.app.remote.code)
        self.page.locator(switch).uncheck()
        self.page.wait_for_selector("#settings .rm-sec:last-child >> text=Computers that can send jobs here", state="detached")
        self.assertIsNone(self.app.remote.listener, "switched off, nothing listens")

    def test_a_job_on_the_other_computer(self):
        self.open()
        self.pair()
        self.page.set_input_files("#fileInput", str(self.wav))
        self.page.wait_for_selector(".chosen")
        self.assertEqual(self.texts("[data-run-on]"), ["This computer", "GPU laptop"])
        self.page.click("[data-run-on]:not([data-run-on=''])")
        self.page.wait_for_selector(".model-card[data-rm-model]")
        self.assertEqual(self.page.eval_on_selector_all(".model-card[data-rm-model]", "els => els.map(e => e.dataset.rmModel)"),
                         ["cohere", "large-v3"], "the models installed there")
        self.assertEqual(set(self.texts(".model-card[data-rm-model] .mc-badges .pill")), {"On GPU laptop"})
        consent = self.page.inner_text(".rm-consent")
        self.assertIn("The recording will be sent to GPU laptop on your network", consent)
        self.assertTrue(self.page.is_disabled("#startBtn"), "not before the tick, the first time")
        self.page.check("#confirmSend")
        self.page.click("#startBtn")
        self.page.wait_for_url(re.compile(r"#/job/"))
        jid = self.page.url.rsplit("/", 1)[1]
        wait_for(lambda: self.app.store.get(jid)["status"] == "done", what="the job sent to the GPU laptop")
        self.page.wait_for_selector(".det-dev")
        self.assertIn("Ran on GPU laptop", self.page.inner_text(".det-dev .det-sub"))
        self.assertEqual(self.stand_in.calls[-1]["home"], self.b.home)
        self.page.click("#moreDetails summary")
        rows = dict(zip(self.texts("#moreDetails dt"), self.texts("#moreDetails dd")))
        self.assertEqual(rows["Ran on"], "GPU laptop, graphics card: NVIDIA GeForce RTX 3060 (CUDA)")
        self.page.goto(f"{self.base}/#/new")
        self.page.set_input_files("#fileInput", str(self.wav))
        self.page.wait_for_selector(".rm-consent")
        self.assertEqual(self.page.locator("#confirmSend").count(), 0, "not asked again for this computer")
        self.assertTrue(self.page.is_enabled("#startBtn"))

    def test_a_computer_that_doesnt_answer(self):
        self.open()
        self.pair()
        self.b.app.remote.stop()
        try:
            self.page.click("[data-run-on]:not([data-run-on=''])")
            self.page.wait_for_selector(".rm-off")
            self.assertIn("GPU laptop isn't answering", self.page.inner_text(".rm-off"))
            self.assertTrue(self.page.is_disabled("#startBtn"))
            self.page.click("[data-run-on='']")
            self.page.wait_for_selector(".model-card[data-model]")
        finally:
            self.b.app.remote.listen()
        computers = self.app.remote.computers()
        for c in computers:
            c["port"] = self.b.app.remote.listening_port()
        self.app.remote.save_computers(computers)
        self.page.click("[data-run-on]:not([data-run-on=''])")
        self.page.wait_for_selector(".model-card[data-rm-model]")


if __name__ == "__main__":
    unittest.main()
