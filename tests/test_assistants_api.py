"""Tests of Settings → Assistants through the app's HTTP API (app/server.py, over app/assistants.py): the
switches, saved with the other settings; the folders assistants may transcribe files from; what is hidden one by
one, with titles (and the key of one deleted since); the log of what assistants asked, with the titles it names and
in words, and clearing it; the connection snippets; and what a transcription's page is told about it. Every change
needs the X-Sedjem header. A request that names an assistant (as app/mcp.py sends them) is held to the assistant
rules by the server itself: the switches, a file only inside an allowed folder, hosted models only when allowed,
never another computer, and a cancel only of a job an assistant started.
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
import os  # noqa: E402

from app import assistants, engines, youtube  # noqa: E402
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

    def setUp(self):  # each test starts with everything visible, saving and jobs off and nothing in the log
        self.cfg.save_settings(mcp_courses=True, mcp_recordings=True, mcp_hidden=[], mcp_write=False, mcp_links=False,
                               mcp_files=False, mcp_folders=[], mcp_hosted=False)
        assistants.clear_log(self.cfg)

    def call(self, method, path, body=None, headers=None):
        h = {"X-Sedjem": "1",
             "X-Sedjem-Token": self.app.session.secret, "Content-Type": "application/json", **(headers or {})}
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
        self.assertEqual(set(body), {"settings", "folders", "hidden", "log", "snippets"})
        self.assertEqual(body["settings"], {"mcp_courses": True, "mcp_recordings": True, "mcp_write": False,
                                            "mcp_links": False, "mcp_files": False, "mcp_hosted": False})
        self.assertEqual((body["folders"], body["hidden"], body["log"]), ([], [], []))
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
        self.assertEqual(self.call("GET", "/api/assistants")[1]["settings"],
                         {"mcp_courses": True, "mcp_recordings": False, "mcp_write": False, "mcp_links": False,
                          "mcp_files": False, "mcp_hosted": False}, "saving and starting jobs are off at first")
        status, st = self.call("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual({k: st["settings"][k] for k in ("mcp_courses", "mcp_recordings", "mcp_hidden")},
                         {"mcp_courses": True, "mcp_recordings": False, "mcp_hidden": []})
        self.assertFalse(Config(home=self.home).settings()["mcp_recordings"], "kept for the next start")

    def test_the_saving_switch_is_saved(self):
        status, st = self.call("POST", "/api/settings", {"mcp_write": True})
        self.assertEqual((status, st["settings"]["mcp_write"]), (200, True))
        self.assertTrue(self.call("GET", "/api/assistants")[1]["settings"]["mcp_write"])
        self.assertTrue(Config(home=self.home).settings()["mcp_write"], "kept for the next start")
        for bad in ("true", 1, None):
            self.assertEqual(self.call("POST", "/api/settings", {"mcp_write": bad})[0], 400, bad)
        self.assertTrue(self.cfg.settings()["mcp_write"])
        self.assertEqual(self.call("POST", "/api/settings", {"mcp_write": False})[1]["settings"]["mcp_write"], False)

    def test_the_job_switches_and_the_folders_are_saved(self):
        folder = self.home / "recordings"
        folder.mkdir(exist_ok=True)
        status, st = self.call("POST", "/api/settings", {"mcp_links": True, "mcp_files": True, "mcp_hosted": True,
                                                         "mcp_folders": [str(folder), str(self.home / "gone")]})
        self.assertEqual(status, 200, st)
        self.assertEqual(st["settings"]["mcp_folders"], [str(folder), str(self.home / "gone")])
        body = self.call("GET", "/api/assistants")[1]
        self.assertEqual({k: body["settings"][k] for k in ("mcp_links", "mcp_files", "mcp_hosted")},
                         {"mcp_links": True, "mcp_files": True, "mcp_hosted": True})
        self.assertEqual(body["folders"], [{"path": str(folder), "found": True}, {"path": str(self.home / "gone"), "found": False}])
        self.assertEqual(Config(home=self.home).settings()["mcp_folders"], [str(folder), str(self.home / "gone")],
                         "kept for the next start")
        for bad in ({"mcp_links": "true"}, {"mcp_files": 1}, {"mcp_hosted": None}, {"mcp_folders": "recordings"},
                    {"mcp_folders": ["recordings"]}, {"mcp_folders": [str(folder), str(folder)]},
                    {"mcp_folders": [f" {folder}"]}, {"mcp_folders": [3]}, {"mcp_folders": [""]},
                    {"mcp_folders": [str(self.home / "x\0y")]}, {"mcp_folders": [str(self.home / ("x" * 1001))]},
                    {"mcp_folders": [str(self.home / f"f{i}") for i in range(51)]}):
            self.assertEqual(self.call("POST", "/api/settings", bad)[0], 400, bad)
        self.assertEqual(self.cfg.settings()["mcp_folders"], [str(folder), str(self.home / "gone")], "unchanged")

    def test_an_assistants_requests_are_held_to_the_rules_by_the_app_itself(self):
        """What app/mcp.py checks first is checked again here, for any request that names an assistant."""
        self.addCleanup(self.forget_new_jobs)
        folder, outside = self.home / "recordings", self.home / "elsewhere.m4a"
        folder.mkdir(exist_ok=True)
        (folder / "standup.m4a").write_bytes(b"(a recording)")
        outside.write_bytes(b"(a recording outside the folder)")
        playlist = {"site": "youtube", "kind": "playlist", "id": "PLabcdefghij", "title": "A course", "count": 1,
                    "url": "https://www.youtube.com/playlist?list=PLabcdefghij",
                    "entries": [{"id": "dQw4w9WgXcQ", "title": "One", "index": 1, "available": True}]}
        mine = {"path": str(folder / "standup.m4a"), "model": "cohere", "assistant": "Rogue 1.0"}
        link = {"url": playlist["url"], "model": "cohere", "assistant": "Rogue 1.0"}
        with mock.patch.object(Config, "availability", lambda cfg, model: (True, None)), \
                mock.patch.object(self.app.runner, "submit"), mock.patch.object(youtube, "inspect", return_value=playlist):
            def refused(method, path, body, status=403):
                got = self.call(method, path, body)
                self.assertEqual(got[0], status, (path, body, got))
                return got[1]["error"]
            self.assertIn("may not transcribe files", refused("POST", "/api/jobs", mine))
            self.assertIn("may not start transcriptions from links", refused("POST", "/api/link/inspect", link))
            self.assertIn("may not start transcriptions from links", refused("POST", "/api/link", link))
            self.cfg.save_settings(mcp_files=True, mcp_folders=[str(folder)], mcp_links=True)
            self.assertIn("is not inside a folder", refused("POST", "/api/jobs", {**mine, "path": str(outside)}))
            self.assertIn("is not inside a folder", refused("POST", "/api/jobs", {**mine, "path": str(folder / ".." / outside.name)}))
            self.assertIn("another computer", refused("POST", "/api/jobs", {**mine, "run_on": "0" * 12}))
            self.assertEqual(refused("POST", "/api/jobs", {**mine, "assistant": 3}, 400), "assistant must be the assistant's name")
            hosted = {**mine, "model": "elevenlabs", "confirm_upload": True}
            self.assertIn("hosted models are off for assistants", refused("POST", "/api/jobs", hosted))
            self.assertIn("hosted models are off for assistants", refused("POST", "/api/link", {**link, "model": "elevenlabs",
                                                                                                "confirm_upload": True}))
            req = urllib.request.Request(self.base + "/api/jobs?model=cohere&name=x.m4a&assistant=Rogue", data=b"(sound)",
                                         method="POST", headers={"X-Sedjem": "1", "X-Sedjem-Token": self.app.session.secret,
                                                                 "Content-Type": "audio/mp4"})
            with self.assertRaises(urllib.error.HTTPError) as upload:
                urllib.request.urlopen(req, timeout=30)
            self.assertEqual(upload.exception.code, 403, "an assistant never uploads")
            self.assertEqual(sum(1 for j in self.app.store.list() if j.get("assistant")), 0, "nothing was started")

            status, job = self.call("POST", "/api/jobs", mine)
            self.assertEqual((status, job["assistant"]), (201, {"client": "Rogue 1.0"}))
            self.assertEqual(self.app.store.get(job["id"])["source_path"], os.path.realpath(folder / "standup.m4a"))
            self.cfg.save_settings(mcp_hosted=True)
            self.assertEqual(self.call("POST", "/api/jobs", hosted)[0], 201)
            status, found = self.call("POST", "/api/link", link)
            self.assertEqual((status, [j["assistant"] for j in found["jobs"]]), (201, [{"client": "Rogue 1.0"}]))
            status, own = self.call("POST", "/api/jobs", {"path": str(outside), "model": "cohere"})
            self.assertEqual((status, own["assistant"]), (201, None), "the user's own window isn't held to them")

            self.assertEqual(refused("POST", f"/api/jobs/{own['id']}/cancel", {"assistant": "Rogue 1.0"}),
                             "an assistant may cancel only a transcription that an assistant started")
            self.assertEqual(self.app.store.get(own["id"])["status"], "preparing")
            self.hide(f"job:{job['id']}")
            self.assertEqual(refused("POST", f"/api/jobs/{job['id']}/cancel", {"assistant": "Rogue 1.0"}, 404),
                             "no such transcription")
            self.hide(f"job:{job['id']}", False)
            self.cfg.save_settings(mcp_files=False, mcp_links=False)
            self.assertIn("may not start transcriptions", refused("POST", f"/api/jobs/{job['id']}/cancel", {"assistant": "Rogue 1.0"}))
            self.cfg.save_settings(mcp_files=True)
            status, now = self.call("POST", f"/api/jobs/{job['id']}/cancel", {"assistant": "Rogue 1.0"})
            self.assertEqual((status, now["status"]), (200, "cancelled"))

    def forget_new_jobs(self):
        """The other tests count on the four transcriptions of setUpClass."""
        for j in self.app.store.list():
            if j["id"] not in {jid for jid, _, _ in JOBS}:
                self.app.store.delete(j["id"])

    def test_a_saved_version_stands_out_in_the_log(self):
        assistants.log(self.cfg, "Claude Desktop 1.2", "save_version", "Saved version 3 of Sprint planning", [MEETING], 3)
        assistants.log(self.cfg, "Cursor", "save_version", "Asked to save a version of Sprint planning, refused: base_version "
                                                           "1 is not the latest version", [MEETING])
        refused, saved = self.call("GET", "/api/assistants")[1]["log"]
        self.assertEqual({k: saved[k] for k in ("text", "titles", "saved", "version")},
                         {"text": "Saved version 3 of Sprint planning", "titles": ["Sprint planning"], "saved": True,
                          "version": 3})
        self.assertEqual((refused["saved"], refused["version"]), (False, None))
        self.assertTrue(refused["text"].startswith("Asked to save a version of Sprint planning, refused"))

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



class LogWording(unittest.TestCase):
    """The log's wording in Settings (server.ASKED) is written for the actions app/mcp.py logs: every one of
    them must have its words, or a new tool would show up in Settings as its bare name."""

    def test_every_action_the_mcp_server_logs_has_words(self):
        from app import mcp, server
        actions = set(mcp.TOOL) | {"resources/read", "prompts/get", "tools/call"}
        self.assertEqual(actions - set(server.ASKED), set())
        self.assertEqual(server.asked({"action": "search", "detail": "tokenizers, in Prompt Engineering"}),
                         "Searched for tokenizers, in Prompt Engineering")
        self.assertEqual(server.asked({"action": "tools/call", "detail": "a tool that doesn't exist: x"}),
                         "Asked for a tool that doesn't exist: x")


if __name__ == "__main__":
    unittest.main()
