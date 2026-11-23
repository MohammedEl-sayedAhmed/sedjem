"""Tests for recordings on this computer as a course (app/local.py): a folder's listing (its recordings in natural name
order, the subfolders, other files, hidden files and links leading out of it that are skipped and counted, an empty,
missing or unreadable folder, a file given for a folder, the cut at 300), paths and names of files picked together,
and the course made through the app's API from a folder, from paths, and from uploads; then what assistants see of
such a course over MCP (app/mcp.py), and the usage counts that leave it out. The hosted service is replaced by a
stand-in, and the recordings are short WAV files written here, so nothing leaves this computer.
Run: .venv/bin/python -m unittest tests.test_local -v
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import assistants, engines, local, usage  # noqa: E402
from app import mcp as sedjem_mcp  # noqa: E402
from app.config import Config  # noqa: E402
from app.server import make_server  # noqa: E402

LINE = [{"start": 0.0, "end": 1.5, "speaker": "1", "text": "Spoken words"}]


def wav(path, seconds=0.5, freq=330):
    """A short tone as a WAV file: a recording the app can read."""
    t = np.arange(int(16000 * seconds)) / 16000
    sf.write(path, (0.1 * np.sin(2 * np.pi * freq * t)).astype(np.float32), 16000)
    return Path(path)


def touch(path, text="x"):
    Path(path).write_text(text, encoding="utf-8")
    return Path(path)


def links_work(folder):
    """Whether this system lets the tests make symbolic links (Windows needs a setting for it)."""
    try:
        os.symlink(folder, Path(folder) / ".probe")
    except (OSError, NotImplementedError):
        return False
    os.unlink(Path(folder) / ".probe")
    return True


class Listing(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sedjem-local-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.folder = self.tmp / "Signals and systems"
        self.folder.mkdir()

    def names(self, found):
        return [e["name"] for e in found["entries"]]

    def test_a_folders_recordings_in_natural_name_order(self):
        for name in ("Lecture 10 - Filters.mp4", "lecture 2 - Signals.M4A", "Lecture 1 - Welcome.wav", "Slides.pdf",
                     "notes.txt", ".Lecture 3.mp3", "._Lecture 1 - Welcome.wav", "Week 1.2"):
            touch(self.folder / name)
        (self.folder / "Exams").mkdir()
        (self.folder / "Exams" / "Exam.mp3").write_text("x")
        found = local.inspect_folder(str(self.folder))
        self.assertEqual(self.names(found), ["Lecture 1 - Welcome.wav", "lecture 2 - Signals.M4A", "Lecture 10 - Filters.mp4"],
                         "natural name order, any case; subfolders aren't read")
        self.assertEqual([(e["index"], e["title"]) for e in found["entries"]],
                         [(1, "Lecture 1 - Welcome"), (2, "lecture 2 - Signals"), (3, "Lecture 10 - Filters")])
        self.assertEqual(found["entries"][0]["id"], os.path.realpath(self.folder / "Lecture 1 - Welcome.wav"),
                         "each by its real path")
        self.assertEqual((found["kind"], found["title"], found["count"], found["cut"]),
                         ("folder", "Signals and systems", 3, False))
        self.assertEqual(found["skipped"], {"folders": 1, "files": 5, "links": 0},
                         "the subfolder, and the other and hidden files")
        self.assertEqual(local.words(found["skipped"]), "1 subfolder and 5 other files")
        self.assertEqual(found["path"], os.path.realpath(self.folder))

    def test_symbolic_links(self):
        if not links_work(self.tmp):
            self.skipTest("this system makes no symbolic links")
        outside = self.tmp / "Elsewhere"
        outside.mkdir()
        wav(outside / "Secret call.wav")
        (outside / "Inside").mkdir()
        touch(self.folder / "Lecture 1.mp3")
        os.symlink(outside / "Secret call.wav", self.folder / "Lecture 2.wav")  # a file outside: skipped
        os.symlink(self.folder / "Lecture 1.mp3", self.folder / "Lecture 1 again.mp3")  # a file inside: listed once
        os.symlink(outside / "Inside", self.folder / "Linked folder")  # a folder: a subfolder
        os.symlink(self.tmp / "missing.mp3", self.folder / "Broken.mp3")  # leads nowhere, and out of the folder
        found = local.inspect_folder(str(self.folder))
        self.assertEqual(self.names(found), ["Lecture 1.mp3"])
        self.assertEqual(found["skipped"], {"folders": 1, "files": 0, "links": 2})
        self.assertEqual(local.words(found["skipped"]), "1 subfolder and 2 links to files outside the folder")
        os.symlink(self.folder, self.tmp / "Link to the course")  # the folder by a link: its real path
        again = local.inspect_folder(str(self.tmp / "Link to the course"))
        self.assertEqual((again["path"], again["entries"][0]["id"]),
                         (os.path.realpath(self.folder), os.path.realpath(self.folder / "Lecture 1.mp3")))

    def test_messages(self):
        empty = self.tmp / "Empty"
        empty.mkdir()
        with self.assertRaisesRegex(local.LocalError, "^this folder is empty$"):
            local.inspect_folder(str(empty))
        touch(self.folder / "Slides.pdf")
        (self.folder / "Exams").mkdir()
        with self.assertRaisesRegex(local.LocalError, r"^this folder has no recordings \(1 subfolder and 1 other file skipped\)$"):
            local.inspect_folder(str(self.folder))
        with self.assertRaisesRegex(local.LocalError, "^there is no folder at "):
            local.inspect_folder(str(self.tmp / "Missing"))
        with self.assertRaisesRegex(local.LocalError, "is a file, not a folder$"):
            local.inspect_folder(str(self.folder / "Slides.pdf"))
        with self.assertRaisesRegex(local.LocalError, "^there is no file or folder at "):
            local.inspect(str(self.tmp / "Missing"))
        for given in ("Lectures", "", "  ", None, 7, "/x\0y", "/" + "x" * 5000):
            with self.assertRaisesRegex(local.LocalError, "full path"):
                local.inspect(given)
        file = wav(self.tmp / "One call.wav")
        self.assertEqual(local.inspect(str(file)), {"kind": "file", "path": str(file)}, "a file: the caller probes it")

    @unittest.skipIf(os.name == "nt" or hasattr(os, "geteuid") and os.geteuid() == 0, "needs file permissions that hold")
    def test_a_folder_that_cant_be_read(self):
        touch(self.folder / "Lecture 1.mp3")
        self.folder.chmod(0)
        self.addCleanup(self.folder.chmod, 0o755)
        with self.assertRaisesRegex(local.LocalError, "^Sedjem isn't allowed to read the folder "):
            local.inspect_folder(str(self.folder))

    def test_the_list_is_cut_at_300(self):
        for n in range(1, 302):
            touch(self.folder / f"Part {n}.mp3")
        found = local.inspect_folder(str(self.folder))
        self.assertEqual((found["count"], found["cut"], found["entries"][-1]["name"]), (300, True, "Part 300.mp3"))
        (self.folder / "Part 301.mp3").unlink()
        self.assertEqual((local.inspect_folder(str(self.folder))["count"], local.inspect_folder(str(self.folder))["cut"]),
                         (300, False), "300 are all there")

    def test_paths_picked_together(self):
        a, b = wav(self.tmp / "Lecture 10.wav"), wav(self.folder / "Lecture 2.wav")
        pdf = touch(self.tmp / "Slides.pdf")
        found = local.inspect_files([str(a), str(b), str(pdf), str(self.folder), str(a)])
        self.assertEqual([(e["index"], e["name"], e["id"]) for e in found["entries"]],
                         [(1, "Lecture 2.wav", os.path.realpath(b)), (2, "Lecture 10.wav", os.path.realpath(a))],
                         "in natural name order, from any folder, each once")
        self.assertEqual((found["kind"], found["skipped"], found["title"]),
                         ("files", {"folders": 1, "files": 1}, f"Files added on {local.day()}"))
        self.assertGreater(found["entries"][0]["size"], 0)
        with self.assertRaisesRegex(local.LocalError, r"^none of these files is a recording \(1 folder and 1 other file skipped\)$"):
            local.inspect_files([str(pdf), str(self.folder)])
        with self.assertRaisesRegex(local.LocalError, "^there is no file at "):
            local.inspect_files([str(a), str(self.tmp / "gone.mp3")])
        for given, said in (([], "give the files' full paths"), ("x", "give the files' full paths"),
                            (None, "give the files' full paths"), ([str(a)] * (local.MAX_PATHS + 1), "pick at most 5000")):
            with self.assertRaisesRegex(local.LocalError, said):
                local.inspect_files(given)

    def test_names_of_files_from_a_browser(self):
        found = local.inspect_names(["Lecture 10.mp3", "Slides.pdf", "lecture 2.WAV", ".x.mp3", "Lecture 10.mp3"], 1)
        self.assertEqual([(e["index"], e["name"], e["id"], e["size"]) for e in found["entries"]],
                         [(1, "lecture 2.WAV", "2", None), (2, "Lecture 10.mp3", "0", None), (3, "Lecture 10.mp3", "4", None)],
                         "each by its place in the list given: two files of the same name are two files")
        self.assertEqual((found["upload"], found["skipped"]), (True, {"folders": 1, "files": 2}))
        with self.assertRaisesRegex(local.LocalError, r"\(1 other file skipped\)"):
            local.inspect_names(["Slides.pdf"])
        for names, folders, said in (([], 0, "give the files' names"), ([7], 0, "give the files' names"),
                                     (["a.mp3", ""], 0, "give the files' names"), ("a.mp3", 0, "give the files' names"),
                                     (["a.mp3"], -1, "folders must be a number"), (["a.mp3"], True, "folders must be a number"),
                                     (["a.mp3"] * (local.MAX_PATHS + 1), 0, "pick at most")):
            with self.assertRaisesRegex(local.LocalError, said):
                local.inspect_names(names, folders)

    def test_course_names_and_ids(self):
        self.assertEqual(local.course_title("  Signals \n and   systems "), "Signals and systems")
        self.assertEqual(local.course_title(""), f"Files added on {local.day()}")
        self.assertEqual(local.day(__import__("datetime").datetime(2026, 11, 3)), "3 Nov 2026")
        made = local.files_course("", 3)
        self.assertRegex(made["id"], r"^files-[0-9a-f]{20}$")
        self.assertNotEqual(made["id"], local.files_course("", 3)["id"], "files picked together: a new course each time")
        self.assertEqual(local.files_course("Week 1", 2, made["id"])["id"], made["id"])
        for forged in ("folder-" + "0" * 20, "../x", "PLabc"):
            with self.assertRaises(local.LocalError):
                local.files_course("x", 1, forged)
        self.assertTrue(assistants.HIDDEN_KEY.match(f"course:{made['id']}"), "a course that can be hidden from assistants")
        self.assertIsNone(local.upload_course({"name": "x.mp3"}))
        self.assertEqual(local.upload_course({"course_title": "Week 1", "course_index": "2", "course_count": "3",
                                              "course": made["id"]}),
                         {"id": made["id"], "title": "Week 1", "count": 3, "local": "files", "index": 2})
        for bad in ({"course_title": "x"}, {"course_index": "0", "course_count": "2"}, {"course_index": "3", "course_count": "2"},
                    {"course_index": "1", "course_count": "301"}, {"course_index": "a", "course_count": "2"}):
            with self.assertRaises(local.LocalError):
                local.upload_course(bad)


class Api(unittest.TestCase):
    """Through the app's HTTP API and its queues, with the hosted service replaced by a stand-in."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="sedjem-local-api-"))
        cls.cfg = Config(home=cls.tmp / "home")
        cls.cfg.save_key("elevenlabs", "test-key")
        cls.folder = cls.tmp / "Signals and systems"
        cls.folder.mkdir()
        for n, name in enumerate(("Lecture 10 - Filters.wav", "Lecture 2 - Signals.wav", "Lecture 1 - Welcome.wav")):
            wav(cls.folder / name, freq=300 + 40 * n)
        touch(cls.folder / "Slides.pdf")
        (cls.folder / "Exams").mkdir()
        cls.ran = []

        def hosted(cfg, model, job_dir, job, on_progress, cancelled):
            cls.ran.append(job["title"])
            return {"lines": [{**LINE[0], "text": f"Spoken words of {job['title']}"}], "meta": {}}
        cls.patch = mock.patch.object(engines, "run_hosted", hosted)
        cls.patch.start()
        cls.server, cls.app = make_server(cls.cfg, port=0)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.patch.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, method, path, body=None, raw=None, ctype="application/json"):
        h = {"X-Sedjem": "1", "X-Sedjem-Token": self.app.session.secret, "Content-Type": ctype}
        data = raw if raw is not None else None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def upload(self, path, **params):
        query = urllib.parse.urlencode({"model": "elevenlabs", "confirm_upload": "1", "name": Path(path).name, **params})
        return self.call("POST", f"/api/jobs?{query}", raw=Path(path).read_bytes(), ctype="audio/wav")

    def wait(self, jid, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            r = self.call("GET", f"/api/jobs/{jid}")[1]
            if r["job"]["status"] in ("done", "failed", "cancelled"):
                return r
            time.sleep(0.1)
        self.fail(f"job {jid} is still {r['job']['status']}")

    def add(self, **body):
        return self.call("POST", "/api/local", {"model": "elevenlabs", "confirm_upload": True, **body})

    def test_1_a_folder_is_listed_and_becomes_a_course(self):
        status, found = self.call("POST", "/api/local/inspect", {"path": str(self.folder)})
        self.assertEqual((status, found["kind"], found["title"], found["count"], found["skipped"]),
                         (200, "folder", "Signals and systems", 3, {"folders": 1, "files": 1, "links": 0}), found)
        self.assertEqual([e["name"] for e in found["entries"]],
                         ["Lecture 1 - Welcome.wav", "Lecture 2 - Signals.wav", "Lecture 10 - Filters.wav"])
        ids = [e["id"] for e in found["entries"]]
        self.ran.clear()
        with mock.patch.object(self.app.usage, "count") as count:
            status, r = self.add(path=str(self.folder), entries=[ids[0], ids[2]])
        self.assertEqual(status, 201, r)
        self.assertEqual(count.call_args_list, [], "no usage count")
        cid = r["jobs"][0]["course"]["id"]
        self.assertRegex(cid, r"^folder-[0-9a-f]{20}$")
        course = {"id": cid, "title": "Signals and systems", "count": 3, "local": "folder", "path": os.path.realpath(self.folder)}
        self.assertEqual([(j["title"], j["source_name"], j["course"], j["source_url"], j["drive_id"]) for j in r["jobs"]],
                         [("Lecture 1 - Welcome", "Lecture 1 - Welcome.wav", {**course, "index": 1}, None, None),
                          ("Lecture 10 - Filters", "Lecture 10 - Filters.wav", {**course, "index": 3}, None, None)],
                         "the picked recordings, numbered in the folder's order, with the folder as their course")
        done = [self.wait(j["id"]) for j in r["jobs"]]
        self.assertEqual([d["job"]["status"] for d in done], ["done", "done"], [d["job"].get("error") for d in done])
        self.assertEqual(self.ran, ["Lecture 1 - Welcome", "Lecture 10 - Filters"], "in the folder's order")
        self.assertEqual([d["job"]["source_path"] for d in done], [ids[0], ids[2]], "each read where it is")
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()),
                         ["Exams", "Lecture 1 - Welcome.wav", "Lecture 10 - Filters.wav", "Lecture 2 - Signals.wav",
                          "Slides.pdf"], "nothing written into the folder")
        self.assertEqual([p.name for j in r["jobs"] for p in self.app.store.dir(j["id"]).glob("source.*")], [], "nothing copied")
        self.assertFalse(any(self.cfg.courses.rglob("*")), "no files in the courses folder")
        status, files = self.call("POST", "/api/export", {"ids": [j["id"] for j in r["jobs"]], "format": "txt", "as": "files"})
        self.assertEqual((status, [f["path"] for f in files["files"]]),
                         (200, ["Signals-and-systems/01-Lecture-1---Welcome.txt", "Signals-and-systems/03-Lecture-10---Filters.txt"]),
                         "exported as a course")
        self.assertEqual(usage.library_facts(self.app.store.list())["courses"], "0", "only YouTube courses are counted")
        status, again = self.add(path=str(self.folder), entries=[ids[1]])
        self.assertEqual((status, again["jobs"][0]["course"]), (201, {**course, "index": 2}), "the same folder: the same course")
        self.wait(again["jobs"][0]["id"])

    def test_2_files_picked_together_become_one_course(self):
        a, b = self.folder / "Lecture 2 - Signals.wav", self.folder / "Lecture 10 - Filters.wav"
        status, found = self.call("POST", "/api/local/inspect", {"paths": [str(b), str(a), str(self.folder / "Slides.pdf")]})
        self.assertEqual((status, [e["name"] for e in found["entries"]], found["skipped"]),
                         (200, ["Lecture 2 - Signals.wav", "Lecture 10 - Filters.wav"], {"folders": 0, "files": 1}))
        status, r = self.add(paths=[str(b), str(a)], title="  Week 1 \n")
        self.assertEqual(status, 201, r)
        cid = r["jobs"][0]["course"]["id"]
        self.assertRegex(cid, r"^files-[0-9a-f]{20}$")
        self.assertEqual([(j["title"], j["course"]) for j in r["jobs"]],
                         [("Lecture 2 - Signals", {"id": cid, "title": "Week 1", "count": 2, "local": "files", "index": 1}),
                          ("Lecture 10 - Filters", {"id": cid, "title": "Week 1", "count": 2, "local": "files", "index": 2})])
        for j in r["jobs"]:
            self.assertEqual(self.wait(j["id"])["job"]["status"], "done")
        status, r = self.add(paths=[str(a)])
        self.assertEqual((status, r["jobs"][0]["course"]["title"]), (201, f"Files added on {local.day()}"), "a name of its own")
        self.assertNotEqual(r["jobs"][0]["course"]["id"], cid, "files picked again: another course")
        self.wait(r["jobs"][0]["id"])

    def test_3_files_uploaded_together_become_one_course(self):
        a, b = self.folder / "Lecture 1 - Welcome.wav", self.folder / "Lecture 2 - Signals.wav"
        status, first = self.upload(a, course_title="Uploaded week", course_index=1, course_count=2)
        self.assertEqual(status, 201, first)
        cid = first["course"]["id"]
        self.assertRegex(cid, r"^files-[0-9a-f]{20}$")
        status, second = self.upload(b, course_title="Uploaded week", course_index=2, course_count=2, course=cid)
        self.assertEqual(status, 201, second)
        self.assertEqual([(j["title"], j["course"]) for j in (first, second)],
                         [("Lecture 1 - Welcome", {"id": cid, "title": "Uploaded week", "count": 2, "local": "files", "index": 1}),
                          ("Lecture 2 - Signals", {"id": cid, "title": "Uploaded week", "count": 2, "local": "files", "index": 2})])
        for j in (first, second):
            self.assertEqual(self.wait(j["id"])["job"]["status"], "done")
        self.assertEqual(self.upload(a, course_title="x", course_index=1, course_count=1, course="folder-" + "0" * 20),
                         (400, {"error": "that isn't the id of files added together"}))
        self.assertEqual(self.upload(a, course_index=3, course_count=2)[0], 400)
        status, alone = self.upload(a)
        self.assertEqual((status, alone["course"]), (201, None), "a file on its own: no course, as before")
        self.wait(alone["id"])

    def test_4_messages(self):
        missing = str(self.tmp / "Missing")
        self.assertEqual(self.call("POST", "/api/local/inspect", {"path": missing}),
                         (400, {"error": f"there is no file or folder at {missing}"}))
        self.assertEqual(self.add(path=missing), (400, {"error": f"there is no folder at {missing}"}))
        file = str(self.folder / "Lecture 1 - Welcome.wav")
        self.assertEqual(self.add(path=file), (400, {"error": f"{file} is a file, not a folder"}))
        status, found = self.call("POST", "/api/local/inspect", {"path": file})
        self.assertEqual((status, found["kind"], found["name"], found["path"]),
                         (200, "file", "Lecture 1 - Welcome.wav", os.path.realpath(file)), "a file on its own, as before")
        self.assertAlmostEqual(found["seconds"], 0.5, places=1)
        self.assertEqual(self.call("POST", "/api/jobs", {"path": str(self.folder), "model": "elevenlabs", "confirm_upload": True}),
                         (400, {"error": f"{self.folder} is a folder, not a file: a folder's recordings are added as a course"}))
        self.assertEqual(self.call("POST", "/api/probe", {"path": str(self.folder)}),
                         (400, {"error": f"{self.folder} is a folder, not a file"}))
        self.assertEqual(self.add(path=str(self.folder), entries=["/etc/passwd"]), (400, {"error": "choose at least one recording"}),
                         "only the folder's own recordings")
        self.assertEqual(self.add(path=str(self.folder), entries="all")[0], 400)
        self.assertEqual(self.call("POST", "/api/local", {"path": str(self.folder), "model": "elevenlabs"}),
                         (400, {"error": "confirm that the recording may be uploaded to ElevenLabs"}))
        for path, body in (("/api/local/inspect", {"path": str(self.folder)}), ("/api/local", {"path": str(self.folder)})):
            self.assertEqual(self.call("POST", path, {**body, "assistant": "Notes helper"}),
                             (403, {"error": "assistants can't add a folder or files as a course; they transcribe one file at a time"}))


class Assistants(unittest.TestCase):
    """What assistants see of a course of recordings on the computer (app/mcp.py): the course and its lectures, never
    a path."""

    FOLDER = "folder-" + "a1" * 10
    FILES = "files-" + "b2" * 10

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="sedjem-local-mcp-"))
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)
        self.folder_path = "/home/you/Private/Signals and systems"
        course = {"id": self.FOLDER, "title": "Signals and systems", "count": 3, "local": "folder", "path": self.folder_path}
        self.lecture("20261020-090000-f101", "Lecture 1 - Welcome", {**course, "index": 1})
        self.lecture("20261020-090000-f103", "Lecture 10 - Filters", {**course, "index": 3})
        files = {"id": self.FILES, "title": "Week 1", "count": 2, "local": "files"}
        self.lecture("20261021-090000-f201", "Monday", {**files, "index": 1}, source_path="/home/you/Week 1/Monday.mp3")
        self.mcp = sedjem_mcp.Server(Config(home=self.home))

    def lecture(self, jid, title, course, **fields):
        folder = self.home / "app_data" / "jobs" / jid
        folder.mkdir(parents=True)
        job = {"id": jid, "title": title, "source_name": f"{title}.wav", "created": f"2026-10-20T09:00:0{course['index']}+03:00",
               "model": "cohere", "model_title": "Cohere Transcribe Arabic", "kind": "local", "status": "done",
               "audio_s": 30.0, "speaker_names": {}, "finished": "2026-10-20T09:10:00+03:00",
               "source_path": f"{self.folder_path}/{title}.wav", "course": course, **fields}
        (folder / "job.json").write_text(json.dumps(job), encoding="utf-8")
        (folder / "transcript.json").write_text(json.dumps({"lines": [{**LINE[0], "text": f"Words of {title}"}]}),
                                                encoding="utf-8")

    def tool(self, name, **args):
        meta = {sedjem_mcp.VERSION_KEY: "2026-07-28", sedjem_mcp.CAPABILITIES_KEY: {}}
        r = self.mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": name, "arguments": args, "_meta": meta}})["result"]
        self.assertFalse(r.get("isError"), r)
        text = r["content"][0]["text"]
        self.assertNotIn("/home/you", text, "no path")
        return text

    def test_listed_with_their_outline_and_text(self):
        text = self.tool("list_courses")
        self.assertIn(f"- Signals and systems\n  course id {self.FOLDER} · a folder on the user's computer\n"
                      "  2 of 3 lectures transcribed", text)
        self.assertIn(f"- Week 1\n  course id {self.FILES} · files on the user's computer\n  1 of 2 lectures transcribed", text)
        self.assertNotIn("youtube.com", text)
        self.assertNotIn("drive.google.com", text)
        text = self.tool("course_outline", course="signals and systems")
        self.assertIn(f"course id {self.FOLDER} · a folder on the user's computer\n3 lectures in the folder, 2 transcribed", text)
        self.assertIn("3. Lecture 10 - Filters · 00:30 · id 20261020-090000-f103 · done\n   \"Words of Lecture 10 - Filters\"", text,
                      "no link under a lecture")
        self.assertIn("Not added to Sedjem: lecture 2.", text)
        self.assertIn("2 lectures in the course, 1 transcribed", self.tool("course_outline", course=self.FILES))
        text = self.tool("get_course_text", course=self.FOLDER)
        self.assertIn("Recordings from a folder on the user's computer.", text)
        self.assertIn("in the order of the files' names. Times in brackets are minutes:seconds into each recording.", text)
        self.assertIn("## Lecture 3: Lecture 10 - Filters (00:30)\n\n[00:00] Words of Lecture 10 - Filters", text)
        for word in ("Playlist", "playlist", "Drive", "Folder:", "File:"):
            self.assertNotIn(word, text)
        self.assertIn("Recordings from files picked together on the user's computer.", self.tool("get_course_text", course="Week 1"))
        text = self.tool("list_transcriptions", kind="course")
        self.assertIn(f"  course Signals and systems ({self.FOLDER}), lecture 1 of 3", text)
        self.assertNotIn("lecture 1 of 3 ·", text, "no link")
        self.assertIn("Words of Lecture 10 - Filters", self.tool("get_transcript", course=self.FOLDER, lecture=3))
        resource = self.mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "resources/read", "params": {
            "uri": f"sedjem://course/{self.FOLDER}", "_meta": {sedjem_mcp.VERSION_KEY: "2026-07-28", sedjem_mcp.CAPABILITIES_KEY: {}}}})
        self.assertNotIn("/home/you", json.dumps(resource))
        self.assertIn("Recordings from a folder on the user's computer.", resource["result"]["contents"][0]["text"])

    def test_the_courses_switch_and_hiding(self):
        cfg = self.mcp.cfg
        assistants.set_hidden(cfg, f"course:{self.FOLDER}", True)
        self.assertNotIn("Signals and systems", self.tool("list_courses"))
        self.assertIn("Week 1", self.tool("list_courses"))
        cfg.save_settings(mcp_courses=False)
        self.assertIn("There are no courses", self.tool("list_courses"), "a course, under the switch for courses")
        self.assertIn("and so do a folder or several files on the user's computer added as one", self.tool("list_courses"))

    def test_a_course_changed_by_hand_isnt_taken_for_a_playlist(self):
        for n, cid in enumerate(("folder-../x", "folder-abc", "PLabcdef")):
            self.lecture(f"20261022-090000-f30{n}", f"Changed {n}", {"id": cid, "title": f"Changed {n}", "count": 1,
                                                                    "local": "folder", "index": 1})
        self.assertNotIn("Changed", self.tool("list_courses"), "marked as on the computer, without such an id: no course")
        self.assertIsNone(sedjem_mcp.lecture({"course": {"id": self.FOLDER, "local": "folder", "index": 1},
                                              "youtube_id": "abcdefghijk"}), "a YouTube video's course is a playlist's")


if __name__ == "__main__":
    unittest.main()
