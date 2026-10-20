"""Tests of Settings → Assistants through the app's HTTP API (app/server.py, over app/assistants.py): the two
switches, saved with the other settings; what is hidden one by one, with titles (and the key of one deleted
since); the log of what assistants asked, with the titles it names and in words, and clearing it; the
connection snippets; and what a transcription's page is told about it. Every change needs the X-Sedjem header.
Run: .venv/bin/python -m unittest discover -s tests -p "test_assistants_api.py" -v
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
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import assistants, engines  # noqa: E402
from app.config import Config  # noqa: E402
from app.server import make_server  # noqa: E402

MEETING, INTRO, LOOPS, TALK = "20261019-100000-aaaa", "20261019-110000-bbbb", "20261019-110100-cccc", "20261019-120000-dddd"
GONE = "20261001-090000-eeee"  # a transcription deleted since
COURSE = {"id": "PLpython", "title": "Python for beginners", "url": "https://www.youtube.com/playlist?list=PLpython",
          "count": 38}
JOBS = ((MEETING, "Sprint planning", {}),
        (INTRO, "Introduction", {"youtube_id": "vidpython01", "course": {**COURSE, "index": 1}}),
        (LOOPS, "Loops", {"youtube_id": "vidpython04", "course": {**COURSE, "index": 4}}),
        (TALK, "A talk on its own", {"youtube_id": "vidalone001"}))


class AssistantsApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = Path(tempfile.mkdtemp(prefix="sedjem-assistants-api-"))
        for jid, title, extra in JOBS:
            folder = cls.home / "app_data" / "jobs" / jid
            folder.mkdir(parents=True)
            job = {"id": jid, "title": title, "created": f"2026-10-19T{jid[9:11]}:00:00+03:00", "status": "done",
                   "model": "cohere", "model_title": "Cohere Transcribe Arabic", "kind": "local", "audio_s": 60, **extra}
            (folder / "job.json").write_text(json.dumps(job), encoding="utf-8")
            (folder / "transcript.json").write_text(json.dumps(
                {"lines": [{"start": 0.0, "end": 2.0, "speaker": "1", "text": "Hello"}]}), encoding="utf-8")
        cls.gpu = mock.patch.object(engines, "gpu_info", return_value={"devices": [], "cuda_devices": 0, "cuda_libs": None})
        cls.gpu.start()
        cls.server, cls.app = make_server(Config(home=cls.home), port=0)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.cfg = cls.app.cfg

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.gpu.stop()
        shutil.rmtree(cls.home, ignore_errors=True)

    def setUp(self):  # each test starts with everything visible and nothing in the log
        self.cfg.save_settings(mcp_courses=True, mcp_recordings=True, mcp_hidden=[])
        assistants.clear_log(self.cfg)

    def call(self, method, path, body=None, headers=None):
        h = {"X-Sedjem": "1", "Content-Type": "application/json", **(headers or {})}
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def hide(self, key, hidden=True):
        status, body = self.call("POST", "/api/assistants/hide", {"key": key, "hidden": hidden})
        self.assertEqual(status, 200, body)
        return body

    def test_what_settings_shows(self):
        status, body = self.call("GET", "/api/assistants")
        self.assertEqual(status, 200)
        self.assertEqual(set(body), {"settings", "hidden", "log", "snippets"})
        self.assertEqual(body["settings"], {"mcp_courses": True, "mcp_recordings": True})
        self.assertEqual((body["hidden"], body["log"]), ([], []))
        self.assertEqual(body["snippets"], assistants.snippets(self.cfg))
        self.assertEqual([s["client"] for s in body["snippets"]], ["Claude Desktop", "Claude Code", "Cursor", "VS Code"])

    def test_hiding_and_showing_again(self):
        body = self.hide(f"job:{MEETING}")
        self.assertEqual(body["hidden"], [{"key": f"job:{MEETING}", "kind": "transcription", "title": "Sprint planning",
                                           "detail": "Recording, 19 Oct 2026"}])
        self.hide("course:PLpython")
        self.hide(f"job:{TALK}")
        body = self.hide(f"job:{LOOPS}")
        self.assertEqual([(h["title"], h["detail"]) for h in body["hidden"]],
                         [("Loops", "Python for beginners, lecture 4"), ("A talk on its own", "YouTube video"),
                          ("Python for beginners", "2 of 38 lectures"), ("Sprint planning", "Recording, 19 Oct 2026")],
                         "the newest first; a course counts the lectures in the app")
        self.assertEqual(body["hidden"][2]["kind"], "course")
        self.assertEqual(self.cfg.setting("mcp_hidden"), [f"job:{MEETING}", "course:PLpython", f"job:{TALK}", f"job:{LOOPS}"])
        body = self.hide(f"job:{MEETING}", False)
        self.hide(f"job:{MEETING}", False)  # showing again twice is harmless
        self.assertNotIn(f"job:{MEETING}", [h["key"] for h in body["hidden"]])
        self.assertEqual(len(self.call("GET", "/api/assistants")[1]["hidden"]), 3)
        self.assertEqual(self.cfg.settings()["threads"], 10, "the other settings are kept")

    def test_a_deleted_transcription_keeps_its_key(self):
        self.hide(f"job:{GONE}")
        body = self.hide("course:PLgone")
        self.assertEqual(body["hidden"], [
            {"key": "course:PLgone", "kind": "course", "title": None, "detail": "Its lectures were deleted"},
            {"key": f"job:{GONE}", "kind": "transcription", "title": None, "detail": "This transcription was deleted"}])
        self.hide(f"job:{GONE}", False)
        self.assertEqual([h["key"] for h in self.hide("course:PLgone", False)["hidden"]], [])

    def test_hiding_many_at_once_keeps_them_all(self):
        keys = [f"course:PLmany{i:02d}" for i in range(16)]
        threads = [threading.Thread(target=self.hide, args=(k,)) for k in keys]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(self.cfg.setting("mcp_hidden")), keys)

    def test_bad_requests_are_refused(self):
        for key in ("job:../../etc/passwd", "course:", "meeting:x", "", None, 3, f"job:{MEETING.upper()}", "course:a b"):
            status, body = self.call("POST", "/api/assistants/hide", {"key": key, "hidden": True})
            self.assertEqual(status, 400, key)
            self.assertIn("not a transcription or a course", body["error"])
        for hidden in ("yes", 1, None):
            status, body = self.call("POST", "/api/assistants/hide", {"key": f"job:{MEETING}", "hidden": hidden})
            self.assertEqual((status, body["error"]), (400, "hidden must be true or false"))
        self.assertEqual(self.call("POST", "/api/assistants/hide", {"key": f"job:{MEETING}"})[0], 400)
        self.assertEqual(self.call("POST", "/api/settings", {"mcp_courses": "no"})[0], 400)
        self.assertEqual(self.call("POST", "/api/settings", {"mcp_hidden": ["job:x"]})[0], 400)
        self.assertEqual(self.cfg.settings()["mcp_hidden"], [])
        self.assertTrue(self.cfg.settings()["mcp_courses"])

    def test_changes_need_the_header(self):
        assistants.log(self.cfg, "Cursor", "search", "loops")
        no_header = {"X-Sedjem": ""}
        self.assertEqual(self.call("POST", "/api/assistants/hide", {"key": f"job:{MEETING}", "hidden": True},
                                   headers=no_header)[0], 403)
        self.assertEqual(self.call("POST", "/api/assistants/log/clear", headers=no_header)[0], 403)
        self.assertEqual(self.call("POST", "/api/settings", {"mcp_recordings": False}, headers=no_header)[0], 403)
        self.assertEqual(self.call("POST", "/api/assistants/hide", {"key": f"job:{MEETING}", "hidden": True},
                                   headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.call("GET", "/api/assistants", headers={"Host": "evil.example:80"})[0], 403)
        status, body = self.call("GET", "/api/assistants", headers=no_header)  # reading needs no header
        self.assertEqual((status, body["hidden"], len(body["log"])), (200, [], 1))
        self.assertTrue(self.cfg.settings()["mcp_recordings"])

    def test_the_log(self):
        # the details as app/mcp.py words them
        for client, action, detail, ids in (
                ("Claude Desktop 1.2", "search", "tokenizers", [INTRO, GONE]),
                ("Claude Desktop 1.2", "search", "loops, in Python for beginners", [LOOPS]),
                ("Cursor", "get_transcript", "Python for beginners, lecture 1, 00:00-10:00", [INTRO]),
                ("Cursor", "list_transcriptions", '"loops", done', [LOOPS]),
                ("Cursor", "list_courses", "courses", []),
                ("VS Code", "course_outline", "Python for beginners", [INTRO, LOOPS]),
                ("VS Code", "get_course_text", "Python for beginners, characters 1-50,000", [INTRO, LOOPS]),
                ("Claude Code", "resources/read", "Sprint planning", [MEETING]),
                ("Claude Code", "prompts/get", "summarize: Sprint planning", [MEETING]),
                ("Claude Code", "search", "", []),  # a call that failed before it said what it was about
                ("Claude Code", "rename_speaker", "", []),  # a tool this app doesn't know yet
                ("Claude Code", "edit_line", "Sprint planning, line 4", [MEETING])):
            assistants.log(self.cfg, client, action, detail, ids)
        log = self.call("GET", "/api/assistants")[1]["log"]
        self.assertEqual([e["text"] for e in reversed(log)], [
            "Searched for tokenizers",
            "Searched for loops, in Python for beginners",
            "Read Python for beginners, lecture 1, 00:00-10:00",
            'Listed the transcriptions ("loops", done)',
            "Listed the courses",
            "Read the outline of Python for beginners",
            "Read the course text of Python for beginners, characters 1-50,000",
            "Read Sprint planning",
            "Used the prompt summarize: Sprint planning",
            "Searched the transcripts",
            "Rename speaker",
            "Edit line: Sprint planning, line 4"])
        first = log[-1]
        self.assertEqual((first["client"], first["action"], first["detail"]), ("Claude Desktop 1.2", "search", "tokenizers"))
        self.assertEqual((first["ids"], first["titles"]), ([INTRO, GONE], ["Introduction", None]),
                         "a transcription deleted since has no title")
        self.assertRegex(first["time"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d")
        for i in range(60):
            assistants.log(self.cfg, "Cursor", "search", f"q{i}")
        log = self.call("GET", "/api/assistants")[1]["log"]
        self.assertEqual((len(log), log[0]["detail"]), (50, "q59"), "the newest 50")
        status, body = self.call("POST", "/api/assistants/log/clear")
        self.assertEqual((status, body["log"]), (200, []))
        self.assertFalse(assistants.log_path(self.cfg).exists())
        self.assertEqual(self.call("POST", "/api/assistants/log/clear")[0], 200, "clearing an empty log is harmless")

    def test_the_switches_are_saved_with_the_other_settings(self):
        status, st = self.call("POST", "/api/settings", {"mcp_recordings": False})
        self.assertEqual((status, st["settings"]["mcp_recordings"], st["settings"]["mcp_courses"]), (200, False, True))
        self.assertEqual(self.call("GET", "/api/assistants")[1]["settings"], {"mcp_courses": True, "mcp_recordings": False})
        status, st = self.call("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual({k: st["settings"][k] for k in ("mcp_courses", "mcp_recordings", "mcp_hidden")},
                         {"mcp_courses": True, "mcp_recordings": False, "mcp_hidden": []})
        self.assertFalse(Config(home=self.home).settings()["mcp_recordings"], "kept for the next start")

    def test_a_transcription_page_says_why_it_is_hidden(self):
        def state(jid):
            a = self.call("GET", f"/api/jobs/{jid}")[1]["assistants"]
            return a["visible"], a["reason"], a["hidden_key"]
        meeting = self.call("GET", f"/api/jobs/{MEETING}")[1]["assistants"]
        self.assertEqual(meeting, {"visible": True, "reason": None, "hidden_key": None, "key": f"job:{MEETING}",
                                   "hidden": False, "course_key": None, "course_hidden": False,
                                   "switch": "mcp_recordings", "switch_on": True})
        self.hide(f"job:{MEETING}")
        self.assertEqual(state(MEETING), (False, "hidden", f"job:{MEETING}"))
        self.call("POST", "/api/settings", {"mcp_recordings": False})
        self.assertEqual(state(MEETING), (False, "switch", f"job:{MEETING}"), "the switch is named first")
        self.assertEqual(state(INTRO), (True, None, None), "a lecture follows the other switch")

        self.hide("course:PLpython")
        self.hide(f"job:{LOOPS}")
        loops = self.call("GET", f"/api/jobs/{LOOPS}")[1]["assistants"]
        self.assertEqual((loops["reason"], loops["hidden_key"], loops["course_key"], loops["hidden"], loops["course_hidden"]),
                         ("course", "course:PLpython", "course:PLpython", True, True))
        self.assertEqual(state(INTRO), (False, "course", "course:PLpython"))
        self.hide("course:PLpython", False)
        self.assertEqual(state(LOOPS), (False, "hidden", f"job:{LOOPS}"))
        self.call("POST", "/api/settings", {"mcp_courses": False})
        talk = self.call("GET", f"/api/jobs/{TALK}")[1]["assistants"]
        self.assertEqual((talk["switch"], talk["reason"], talk["course_key"]), ("mcp_courses", "switch", None),
                         "a YouTube video on its own counts with the courses")


if __name__ == "__main__":
    unittest.main()
