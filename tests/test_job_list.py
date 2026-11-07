"""Tests for the times the list of transcriptions (GET /api/jobs, app/server.py) gives each job, which the sidebar
orders by (app/static/app.js):

  changed    when its latest version was saved (history/index.json, read without starting or changing the history),
             else when it finished, else when it was added
  recorded   when its recording was made (job.json's source, from sysinfo.recording when the job was prepared), else,
             for a job made before that was kept or not prepared yet, when it was added

The jobs are written here as the app writes them; no model runs, and nothing leaves the temporary data folder. How the
recording's date is found and kept is in tests/test_details.py.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.config import Config  # noqa: E402
from app.jobs import write_json  # noqa: E402
from app.server import make_server  # noqa: E402

LINES = [{"start": 0.5, "end": 3.2, "speaker": "1", "text": "تمام، نبدأ الـ meeting؟"},
         {"start": 3.6, "end": 6.0, "speaker": "2", "text": "Yes, let's start with the sprint."}]
ADDED, FINISHED = "2026-10-20T10:00:00+03:00", "2026-10-20T10:04:30+03:00"


class JobList(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = Path(tempfile.mkdtemp(prefix="sedjem-list-"))
        (cls.home / "app_data").mkdir()
        cls.server, cls.app = make_server(Config(home=cls.home), port=0)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.home, ignore_errors=True)

    def job(self, **meta):
        """A finished job as the app leaves one (job.json and transcript.json); meta changes or adds fields, and a
        field given as None is left out."""
        type(self).n += 1
        jid = f"20261020-1000{self.n:02d}-{self.n:04x}"
        folder = self.home / "app_data" / "jobs" / jid
        folder.mkdir(parents=True)
        job = {"id": jid, "title": "Weekly sync", "source_name": "sync.m4a", "created": ADDED, "model": "whisper-medium",
               "model_title": "whisper-medium code-switching", "kind": "local", "status": "done", "stage": None,
               "options": {"speakers": "auto", "language": "ar", "prompt": ""}, "speaker_names": {}, "audio_s": 6.0,
               "started": ADDED, "finished": FINISHED, **meta}
        write_json(folder / "job.json", {k: v for k, v in job.items() if v is not None})
        write_json(folder / "transcript.json", {"lines": LINES, "edited": False, "model": job["model"]})
        return jid

    def call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"X-Sedjem": "1", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def listed(self):
        return {j["id"]: j for j in self.call("GET", "/api/jobs")["jobs"]}

    def test_changed_is_when_the_latest_version_was_saved(self):
        jid = self.job()
        self.assertEqual(self.listed()[jid]["changed"], FINISHED, "no version saved yet: when it finished")
        self.assertFalse((self.home / "app_data" / "jobs" / jid / "history").exists(), "the list doesn't start a history")
        for n, text in enumerate(("تمام، نبدأ الـ meeting دلوقتي؟", "تمام، يلا نبدأ")):
            self.call("PATCH", f"/api/jobs/{jid}", {"lines": [{**LINES[0], "text": text}, LINES[1]], "message": f"fix {n}"})
            versions = self.call("GET", f"/api/jobs/{jid}/versions")["versions"]
            self.assertEqual(self.listed()[jid]["changed"], versions[-1]["time"])
        self.assertGreater(self.listed()[jid]["changed"], FINISHED)

    def test_without_a_version_it_is_when_it_finished_or_was_added(self):
        failed = self.job(status="failed", finished=None, error="the file has no audio track")
        running = self.job(status="running", stage="transcribing", finished=None)
        unreadable = self.job()
        (self.home / "app_data" / "jobs" / unreadable / "history").mkdir()
        (self.home / "app_data" / "jobs" / unreadable / "history" / "index.json").write_text("{", encoding="utf-8")
        listed = self.listed()
        self.assertEqual((listed[failed]["changed"], listed[running]["changed"]), (ADDED, ADDED))
        self.assertEqual(listed[unreadable]["changed"], FINISHED)
        self.assertEqual(sorted(p.name for p in (self.home / "app_data" / "jobs" / unreadable).iterdir()),
                         ["history", "job.json", "transcript.json"], "an unreadable history is left as it was")

    def test_recorded_is_when_the_recording_was_made_else_when_it_was_added(self):
        own = self.job(source={"name": "sync.m4a", "extension": "m4a", "recorded": "2025-02-01T10:00:00+02:00"})
        older = self.job(source={"name": "sync.m4a", "extension": "m4a", "codec": "aac"})  # kept before the date was
        waiting = self.job(status="preparing", stage="waiting", finished=None)  # not prepared yet: no source
        odd = self.job(source="?")
        listed = self.listed()
        self.assertEqual(listed[own]["recorded"], "2025-02-01T10:00:00+02:00")
        for jid in (older, waiting, odd):
            self.assertEqual(listed[jid]["recorded"], ADDED)


if __name__ == "__main__":
    unittest.main()
