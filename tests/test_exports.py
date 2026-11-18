"""Tests for exporting many transcripts at once (#9, app/exports.py): the file names, a course's lectures numbered in
a folder of their own, a name used twice, one combined file in text, Markdown or JSON, POST /api/export with a zip,
the files as JSON or one combined file, what it refuses, and the desktop window's save and folder.
Run: .venv/bin/python -m unittest tests.test_exports -v
"""
import io
import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import desktop, exports  # noqa: E402
from app.config import Config  # noqa: E402
from app.server import make_server, slug  # noqa: E402

LINES = [{"start": 0.0, "end": 2.5, "speaker": "S1", "text": "Good morning, let's start."},
         {"start": 2.5, "end": 5.0, "speaker": "S2", "text": "The login bug is fixed."}]
COURSE = {"id": "PLpython", "title": "Python for beginners", "count": 3, "url": "https://www.youtube.com/playlist?list=PLpython"}
IDS = {"meeting": "20261019-100000-a001", "again": "20261019-100000-a002", "lec1": "20261019-110000-c001",
       "lec2": "20261019-110000-c002", "running": "20261019-120000-d001", "other": "20261019-130000-e001"}


def make(storage, jid, title, lines=LINES, **fields):
    folder = Path(storage) / "jobs" / jid
    folder.mkdir(parents=True)
    job = {"id": jid, "title": title, "created": "2026-10-19T10:00:00+03:00", "model": "cohere",
           "model_title": "Cohere Transcribe Arabic", "kind": "local", "status": "done", "audio_s": 5.0,
           "options": {"speakers": "auto", "language": "ar"}, "speaker_names": {"S1": "Mona"}, **fields}
    (folder / "job.json").write_text(json.dumps(job), encoding="utf-8")
    if lines is not None:
        (folder / "transcript.json").write_text(json.dumps({"lines": lines, "edited": False}), encoding="utf-8")


def fill(storage):
    make(storage, IDS["meeting"], "Sprint planning")
    make(storage, IDS["again"], "Sprint planning")  # the same title: another transcript of the recording
    make(storage, IDS["lec1"], "Variables", course={**COURSE, "index": 1})
    make(storage, IDS["lec2"], "Loops", course={**COURSE, "index": 2})
    make(storage, IDS["running"], "Not done yet", lines=None, status="running")
    make(storage, IDS["other"], "Lecture elsewhere", course={**COURSE, "id": "PLother", "title": "Other course", "index": 1})


class Files(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="sedjem-exports-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.server, self.app = make_server(Config(home=self.home), port=0)
        self.addCleanup(self.server.server_close)
        fill(self.app.cfg.storage)
        self.store = self.app.store

    def chosen(self, *keys):
        return exports.chosen(self.store, [IDS[k] for k in keys])

    def test_names_folders_and_order(self):
        found = exports.files(self.chosen("lec2", "meeting", "lec1", "again"), "txt", slug)
        self.assertEqual([p for p, _ in found], ["Python-for-beginners/02-Loops.txt", "Sprint-planning.txt",
                                                  "Python-for-beginners/01-Variables.txt", "Sprint-planning-2.txt"])
        self.assertIn("Mona", found[1][1])
        self.assertIn("The login bug is fixed.", found[1][1])

    def test_only_those_with_a_transcript_each_once(self):
        jobs = self.chosen("meeting", "running", "meeting")
        self.assertEqual([j["id"] for j, _ in jobs], [IDS["meeting"]])
        with self.assertRaises(KeyError):
            exports.chosen(self.store, ["20200101-000000-0000"])

    def test_one_combined_file(self):
        jobs = self.chosen("meeting", "lec1")
        as_json = json.loads(exports.combined(jobs, "json"))
        self.assertEqual([x["title"] for x in as_json], ["Sprint planning", "Variables"])
        md = exports.combined(jobs, "md")
        self.assertLess(md.index("# Sprint planning"), md.index("# Variables"))
        self.assertIn("\n---\n", md)
        self.assertEqual(exports.combined(jobs, "txt").count(exports.RULE), 1)

    def test_the_name_of_a_zip(self):
        self.assertEqual(exports.name(self.chosen("lec1", "lec2"), slug), "Python-for-beginners")
        self.assertEqual(exports.name(self.chosen("lec1", "other"), slug), "sedjem-transcripts")
        self.assertEqual(exports.name(self.chosen("lec1", "meeting"), slug), "sedjem-transcripts")

    def test_write_stays_inside_the_folder(self):
        out = self.home / "out"
        self.assertEqual(exports.write(out, [("a/b.txt", "x"), ("c.txt", "y")]), 2)
        self.assertEqual((out / "a" / "b.txt").read_text(encoding="utf-8"), "x")
        with self.assertRaises(ValueError):
            exports.write(out, [("../escape.txt", "z")])
        self.assertFalse((self.home / "escape.txt").exists())


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = Path(tempfile.mkdtemp(prefix="sedjem-exports-api-"))
        cls.server, cls.app = make_server(Config(home=cls.home), port=0)
        fill(cls.app.cfg.storage)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.home, ignore_errors=True)

    def post(self, body):
        req = urllib.request.Request(self.base + "/api/export", method="POST", data=json.dumps(body).encode(),
                                     headers={"X-Sedjem": "1", "X-Sedjem-Token": self.app.session.secret,
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_a_zip(self):
        status, headers, body = self.post({"ids": [IDS["lec1"], IDS["lec2"]], "format": "srt"})
        self.assertEqual((status, headers["Content-Type"]), (200, "application/zip"))
        self.assertIn('filename="Python-for-beginners.zip"', headers["Content-Disposition"])
        with zipfile.ZipFile(io.BytesIO(body)) as z:
            self.assertEqual(z.namelist(), ["Python-for-beginners/01-Variables.srt", "Python-for-beginners/02-Loops.srt"])
            self.assertIn("00:00:02,500 --> 00:00:05,000", z.read(z.namelist()[0]).decode())

    def test_the_files_for_a_folder(self):
        status, _, body = self.post({"ids": [IDS["meeting"], IDS["running"]], "format": "md", "as": "files"})
        self.assertEqual(status, 200)
        files = json.loads(body)["files"]
        self.assertEqual([f["path"] for f in files], ["Sprint-planning.md"])
        self.assertTrue(files[0]["text"].startswith("# Sprint planning"))

    def test_one_combined_file(self):
        status, headers, body = self.post({"ids": [IDS["meeting"], IDS["lec1"]], "format": "json", "combined": True})
        self.assertEqual(status, 200)
        self.assertIn('filename="sedjem-transcripts.json"', headers["Content-Disposition"])
        self.assertEqual(len(json.loads(body)), 2)

    def test_what_it_refuses(self):
        for body, status in (({"ids": [IDS["meeting"]], "format": "srt", "combined": True}, 400),
                             ({"ids": [IDS["meeting"]], "format": "docx"}, 400),
                             ({"ids": [], "format": "txt"}, 400),
                             ({"ids": ["../../etc"], "format": "txt"}, 400),
                             ({"ids": IDS["meeting"], "format": "txt"}, 400),
                             ({"ids": ["20200101-000000-0000"], "format": "txt"}, 404),
                             ({"ids": [IDS["running"]], "format": "txt"}, 400)):
            with self.subTest(body=body):
                self.assertEqual(self.post(body)[0], status)


class Desktop(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="sedjem-exports-desktop-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.server, self.app = make_server(Config(home=self.home), port=0)
        self.addCleanup(self.server.server_close)
        fill(self.app.cfg.storage)
        self.api = desktop.Api(self.app)
        self.api._window = mock.Mock()
        self.api._dialog = lambda kind: kind

    def test_save_many_writes_the_zip_or_the_combined_file(self):
        target = self.home / "picked.zip"
        self.api._window.create_file_dialog.return_value = [str(target)]
        self.assertEqual(self.api.save_many([IDS["lec1"], IDS["lec2"]], "txt"), str(target))
        self.assertEqual(self.api._window.create_file_dialog.call_args.kwargs["save_filename"], "Python-for-beginners.zip")
        with zipfile.ZipFile(target) as z:
            self.assertEqual(len(z.namelist()), 2)
        one = self.home / "all.md"
        self.api._window.create_file_dialog.return_value = [str(one)]
        self.api.save_many([IDS["meeting"], IDS["lec1"]], "md", True)
        self.assertIn("# Variables", one.read_text(encoding="utf-8"))

    def test_export_folder_writes_a_file_each(self):
        out = self.home / "folder"
        out.mkdir()
        self.api._window.create_file_dialog.return_value = [str(out)]
        self.assertEqual(self.api.export_folder([IDS["meeting"], IDS["lec1"]], "vtt"), {"folder": str(out), "count": 2})
        self.assertEqual(self.api._window.create_file_dialog.call_args.args, ("FOLDER",))
        self.assertTrue((out / "Python-for-beginners" / "01-Variables.vtt").is_file())
        self.assertTrue((out / "Sprint-planning.vtt").is_file())

    def test_nothing_picked_or_nothing_to_export(self):
        self.api._window.create_file_dialog.return_value = None
        self.assertIsNone(self.api.export_folder([IDS["meeting"]], "txt"))
        self.assertIsNone(self.api.save_many([IDS["meeting"]], "txt"))
        self.assertIsNone(self.api.export_folder([IDS["running"]], "txt"))
        self.assertIsNone(self.api.save_many(["20200101-000000-0000"], "txt"))
        self.assertIsNone(self.api.save_many([IDS["meeting"]], "docx"))


if __name__ == "__main__":
    unittest.main()
