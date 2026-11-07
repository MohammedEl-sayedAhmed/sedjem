"""Browser tests of Run again with on a transcript's page (app/static/app.js): the models that already made a
transcript of the recording are shown as already run, each a link to that transcript (or to the recording's main
one, when it is theirs), the ones still to try are offered as before, and Again with other options runs the
transcript's own model on the recording again with other speakers, language and vocabulary. Headless Chromium
against a Sedjem server with demo transcripts and three hosted models with stand-in keys (see tests/ui_support.py).
Nothing is uploaded: the runner never starts the new job.
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui*.py" -v
"""
import unittest
from unittest import mock

from ui_support import LINES, UiTestCase

# A recording run with two models (the second a re-run of the first), and with a third that failed on it
A, B, FAILED = "20260927-100000-a1a1", "20260927-100100-b2b2", "20260927-100200-c3c3"
# A recording run twice with one model, the first of the two its main transcript, and once with another
R1, R2, R3 = "20260927-090000-d4d4", "20260927-090100-e5e5", "20260927-090200-f6f6"
ELEVENLABS = {"model": "elevenlabs", "model_title": "ElevenLabs Scribe v2", "kind": "hosted"}
DEEPGRAM = {"model": "deepgram", "model_title": "Deepgram Nova-3", "kind": "hosted"}
SPEECHMATICS = {"model": "speechmatics", "model_title": "Speechmatics", "kind": "hosted"}
SECOND = [dict(x, start=x["start"] + 0.1, end=x["end"] + 0.1) for x in LINES]


class RunAgain(UiTestCase):
    jobs = ({"jid": A, "title": "Planning", "created": "2026-09-27T10:00:00+03:00", **ELEVENLABS,
             "options": {"speakers": 2, "language": "ar", "prompt": "Jira"}},
            {"jid": B, "title": "Planning", "lines": SECOND, "rerun_of": A, "created": "2026-09-27T10:01:00+03:00", **DEEPGRAM},
            {"jid": FAILED, "title": "Planning", "lines": [], "rerun_of": A, "status": "failed", "error": "stand-in failure",
             "created": "2026-09-27T10:02:00+03:00", **SPEECHMATICS},
            {"jid": R1, "title": "Retro", "created": "2026-09-27T09:00:00+03:00", "main": True, **ELEVENLABS},
            {"jid": R2, "title": "Retro", "lines": SECOND, "rerun_of": R1, "created": "2026-09-27T09:01:00+03:00", **ELEVENLABS},
            {"jid": R3, "title": "Retro", "lines": SECOND, "rerun_of": R1, "created": "2026-09-27T09:02:00+03:00", **DEEPGRAM})

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for model_id in ("elevenlabs", "deepgram", "speechmatics"):  # ready; the local models are not downloaded
            cls.app.cfg.save_key(model_id, "test-key")

    def setUp(self):
        super().setUp()
        self.dialogs = []  # the question before a recording is uploaded, answered yes
        self.page.on("dialog", lambda d: (self.dialogs.append(d.message), d.accept()))

    def job_page(self, jid):
        self.open(f"#/job/{jid}")
        self.page.wait_for_selector("#transcript .line .text")

    def open_menu(self):
        self.page.click("[data-menu='rerunMenu']")
        self.page.wait_for_selector("#rerunMenu.open")

    def items(self):
        """The menu's entries in order: [kind, model or entry, the text on its right, the transcript a link opens]."""
        return self.page.eval_on_selector_all("#rerunMenu > a, #rerunMenu > button", """els => els.map(e => [
            e.matches('a.ran') ? 'ran' : e.dataset.rerun ? 'try' : e.id || 'note', e.dataset.ran || e.dataset.rerun
            || e.childNodes[e.childNodes.length - 1].textContent.trim(), e.querySelector('.sub')?.innerText.trim() || '',
            e.getAttribute('href') || ''])""")

    def test_models_that_ran_link_to_their_transcripts_and_the_others_are_offered(self):
        self.job_page(A)
        self.open_menu()
        self.assertEqual(self.items(), [
            ["try", "speechmatics", "uploads", ""],  # its run failed, so it is still to try
            ["ran", "elevenlabs", "this transcript", f"#/job/{A}"],
            ["ran", "deepgram", "already run", f"#/job/{B}"],
            ["againBtn", "Again with other options…", "", ""]])
        self.assertEqual(self.page.eval_on_selector_all("#rerunMenu hr", "els => els.length"), 2)

        self.page.click("#rerunMenu a.ran[data-ran='deepgram']")  # the link opens that transcript
        self.page.wait_for_url(f"**/#/job/{B}")
        self.page.wait_for_selector("#groupBar .pill.ver.on >> text=Deepgram Nova-3")
        self.page.wait_for_selector("#rerunMenu:not(.open)", state="attached")
        self.open_menu()
        self.assertEqual(self.items(), [
            ["try", "speechmatics", "uploads", ""],
            ["ran", "elevenlabs", "already run", f"#/job/{A}"],
            ["ran", "deepgram", "this transcript", f"#/job/{B}"],
            ["againBtn", "Again with other options…", "", ""]])
        self.page.click("#rerunMenu a.ran[data-ran='elevenlabs']")
        self.page.wait_for_url(f"**/#/job/{A}")
        self.page.wait_for_selector("#groupBar .pill.ver.on >> text=ElevenLabs Scribe v2")
        self.assertEqual(self.dialogs, [], "following a link asks nothing")

        # Again with other options: the same model, with the options changed from this transcript's
        self.open_menu()
        self.page.click("#againBtn")
        self.page.wait_for_selector("#again[open]")
        self.assertEqual(self.page.inner_text("#againTitle"), "Run again with other options")
        self.assertIn("With ElevenLabs Scribe, on the same recording.", self.page.inner_text("#againBody"))
        self.assertEqual(self.page.get_attribute("#againSpk button[aria-pressed='true']", "data-v"), "2", "this transcript's")
        self.assertEqual(self.page.get_attribute("#againLang button[aria-pressed='true']", "data-v"), "ar")
        self.assertEqual(self.page.input_value("#againPrompt"), "Jira")
        self.page.click("#againSpk button[data-v='none']")
        self.page.fill("#againSpkN", "8")  # (a number past the buttons)
        self.assertEqual(self.page.eval_on_selector_all("#againSpk button[aria-pressed='true']", "els => els.length"), 0)
        self.page.click("#againLang button[data-v='en']")
        self.assertEqual(self.page.input_value("#againSpkN"), "8", "kept when the language changes")
        self.page.fill("#againPrompt", "Jira, GitHub")
        with mock.patch.object(self.app.runner, "submit") as submit:  # never started, so nothing is uploaded
            self.page.click("#againGo")
            self.page.wait_for_function(f"() => /^#\\/job\\//.test(location.hash) && location.hash !== '#/job/{A}'")
        new = self.page.evaluate("() => location.hash.split('/').pop()")
        submit.assert_called_once_with(new)
        self.page.wait_for_selector("#again", state="hidden")
        self.assertEqual(self.dialogs, ["Upload this recording to ElevenLabs to transcribe it? It leaves this computer."])
        job = self.api("GET", f"/api/jobs/{new}")["json"]["job"]
        self.assertEqual((job["model"], job["rerun_of"]), ("elevenlabs", A))
        self.assertEqual(job["options"], {"speakers": 8, "language": "en", "prompt": "Jira, GitHub"})

        # the new transcript's page: its model is the one running on it
        self.page.wait_for_selector("#progressCard")
        self.page.wait_for_function("() => S.jobs.some((j) => j.id === location.hash.split('/').pop())")
        self.open_menu()
        self.assertEqual(self.items()[1], ["ran", "elevenlabs", "this transcript", f"#/job/{new}"])

    def test_a_model_run_more_than_once_links_to_the_main_transcript(self):
        self.job_page(R3)
        self.open_menu()
        self.assertEqual(self.items(), [
            ["try", "speechmatics", "uploads", ""],
            ["ran", "elevenlabs", "already run", f"#/job/{R1}"],  # the main one, not the newer R2
            ["ran", "deepgram", "this transcript", f"#/job/{R3}"],
            ["againBtn", "Again with other options…", "", ""]])
        self.assertEqual(self.page.get_attribute("#rerunMenu a.ran[data-ran='elevenlabs']", "title"),
                         "Open the recording's main transcript, made with it")
        self.page.click("#rerunMenu a.ran[data-ran='elevenlabs']")
        self.page.wait_for_url(f"**/#/job/{R1}")
        self.page.wait_for_selector("#mainPill", state="visible")

        # without a main transcript, the newest the model made: the open menu follows the list when it is fetched again
        self.page.click("#groupBar .pill.ver >> text=Deepgram Nova-3")
        self.page.wait_for_url(f"**/#/job/{R3}")
        self.page.wait_for_selector("#groupBar .pill.ver.on >> text=Deepgram Nova-3")
        self.open_menu()
        self.assertEqual(self.items()[1], ["ran", "elevenlabs", "already run", f"#/job/{R1}"])
        self.assertEqual(self.api("POST", f"/api/jobs/{R1}/main", {"main": False})["status"], 200)
        self.page.evaluate("() => refreshJobs()")  # (as the app does every 10 seconds)
        self.page.wait_for_selector(f"#rerunMenu.open a.ran[data-ran='elevenlabs'][href='#/job/{R2}']")
        self.assertEqual(self.items()[1], ["ran", "elevenlabs", "already run", f"#/job/{R2}"])
        self.page.click("#rerunMenu a.ran[data-ran='elevenlabs']")  # still a working link
        self.page.wait_for_url(f"**/#/job/{R2}")
        self.assertEqual(self.api("POST", f"/api/jobs/{R1}/main", {"main": True})["status"], 200)


if __name__ == "__main__":
    unittest.main()
