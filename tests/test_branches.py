"""Tests for a recording's main transcript and its branches (app/jobs.py Runner.branch, app/compare.py, the routes in
app/server.py), the stale-save check (app/history.py base_version), and the lock that the app and the MCP server
(another process) both take before they change a history. The MCP side of it is in tests/test_mcp.py.

  Branch       POST /api/jobs/<id>/branch: a new transcript in the same group whose version 0 is exactly the
               version it was made from, the audio shared, no model run, and a hidden mark carried over
  Main         POST /api/jobs/<id>/main: one per recording, finished ones only, listed first; course files follow it
  Combine      from a branch into the main transcript: one new version that says where each stretch came from
  Stale save   PATCH with an old base_version: 409, and nothing changes; without it, PATCH as before
  Lock         re-entrant in a thread, and held across processes

The jobs are written here as the app writes them, with a short generated tone as their audio; no model runs, and
nothing leaves the temporary data folder.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import assistants, courses, engines, history  # noqa: E402
from app import mcp as sedjem_mcp  # noqa: E402
from app.config import Config  # noqa: E402
from app.jobs import Runner, Store, write_json  # noqa: E402
from app.server import make_server  # noqa: E402

LINES = [
    {"start": 0.5, "end": 3.2, "speaker": "1", "text": "تمام، نبدأ الـ meeting؟"},
    {"start": 3.6, "end": 6.0, "speaker": "2", "text": "Yes, let's start with the sprint."},
    {"start": 6.4, "end": 9.1, "speaker": "1", "text": "الـ deadline يوم الخميس"},
    {"start": 9.5, "end": 12.0, "speaker": "3", "text": "I'll update the Jira board."},
]
OTHER = [dict(x, start=x["start"] + 0.1, end=x["end"] + 0.1) for x in LINES]  # a second model, a little later
OTHER[1]["text"] = "Yes, let us start with the sprint review."
OTHER[3]["text"] = "I will update the Jira board today."
COHERE = {"model": "cohere", "model_title": "Cohere Transcribe Arabic"}
META = {sedjem_mcp.VERSION_KEY: "2026-07-28", sedjem_mcp.CAPABILITIES_KEY: {},
        sedjem_mcp.CLIENT_KEY: {"name": "sedjem-tests", "version": "1.0"}}


def changed(lines, i, text):
    return [{**x, "text": text} if n == i else dict(x) for n, x in enumerate(lines)]


def make_job(home, n, lines=LINES, **meta):
    """A finished job as the app leaves one: job.json, transcript.json and a 2 s tone as audio.flac (its own pitch,
    so no two jobs count as the same recording unless rerun_of links them)."""
    jid = f"20261020-1000{n:02d}-{n:04x}"
    folder = Path(home) / "app_data" / "jobs" / jid
    folder.mkdir(parents=True)
    t = np.arange(32000) / 16000
    sf.write(folder / "audio.flac", (0.05 * np.sin(2 * np.pi * (200 + 7 * n) * t)).astype(np.float32), 16000)
    stamp = f"2026-10-20T10:00:{n:02d}+03:00"
    job = {"id": jid, "title": "Weekly sync", "source_name": "sync.m4a", "created": stamp, "model": "whisper-medium",
           "model_title": "whisper-medium code-switching", "kind": "local", "status": "done", "stage": None,
           "options": {"speakers": "auto", "language": "ar", "prompt": ""}, "speaker_names": {}, "audio_s": 12.0,
           "started": stamp, "finished": stamp, **meta}
    write_json(folder / "job.json", job)
    if lines is not None:
        write_json(folder / "transcript.json", {"lines": lines, "edited": False, "model": job["model"]})
    return jid


class Base(unittest.TestCase):
    """A Sedjem server on a temporary data folder (and its own courses folder), one per test class."""

    @classmethod
    def setUpClass(cls):
        cls.home = Path(tempfile.mkdtemp(prefix="sedjem-branches-"))
        (cls.home / "app_data").mkdir()
        cls.cfg = Config(home=cls.home)
        cls.server, cls.app = make_server(cls.cfg, port=0)
        cls.store = cls.app.store
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.home, ignore_errors=True)

    def job(self, lines=LINES, **meta):
        type(self).n += 1
        return make_job(self.home, type(self).n, lines, **meta)

    def call(self, method, path, body=None, headers=None):
        h = {"X-Sedjem": "1", **(headers or {})}
        data = None
        if body is not None:
            data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                payload = r.read()
                return r.status, json.loads(payload) if "json" in r.headers.get("Content-Type", "") else payload
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def ok(self, method, path, body=None, status=200):
        got, r = self.call(method, path, body)
        self.assertEqual(got, status, r)
        return r

    def versions(self, jid):
        return self.ok("GET", f"/api/jobs/{jid}/versions")["versions"]

    def three_versions(self, jid):
        """Versions 1, 2 and 3 of a job, each changing something different, and a version 4 after them."""
        v1 = changed(LINES, 0, "تمام، نبدأ الـ meeting دلوقتي؟")
        self.ok("PATCH", f"/api/jobs/{jid}", {"lines": v1, "message": "first"})
        self.ok("PATCH", f"/api/jobs/{jid}", {"speaker_names": {"1": "Mona", "2": "Omar"}, "message": "names"})
        v3 = [{**x, "speaker": "1" if x["speaker"] == "3" else x["speaker"]} for x in changed(v1, 3, "I'll update Jira.")]
        self.ok("PATCH", f"/api/jobs/{jid}", {"lines": v3, "title": "Sprint planning", "message": "third"})
        self.ok("PATCH", f"/api/jobs/{jid}", {"lines": changed(v3, 2, "الـ deadline يوم الأربع"), "message": "after"})
        self.assertEqual([v["n"] for v in self.versions(jid)], [0, 1, 2, 3, 4])

    def mcp(self, name, **args):
        """(text, is_error) of an MCP tool call on the same data folder, as an assistant makes it."""
        server = sedjem_mcp.Server(self.cfg)
        r = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": name, "arguments": args, "_meta": META}})["result"]
        return r["content"][0]["text"], bool(r.get("isError"))


class Branching(Base):
    def test_branching_at_v3_gives_a_job_in_the_same_group_whose_v0_is_exactly_v3_and_no_model_ran(self):
        a = self.job()
        b = self.job(OTHER, rerun_of=a, **COHERE)  # another model's transcript of the recording
        self.three_versions(a)
        v3 = self.ok("GET", f"/api/jobs/{a}/versions/3")
        runner = self.app.runner
        with mock.patch.object(engines, "run_local") as local, mock.patch.object(engines, "run_hosted") as hosted, \
                mock.patch.object(Runner, "submit") as submit:
            new = self.ok("POST", f"/api/jobs/{a}/branch", {"version": 3, "name": "suggestions"}, 201)
        self.assertFalse(local.called or hosted.called or submit.called, "no model ran, nothing was queued")
        self.assertTrue(all(q.empty() for q in runner.queues.values()))
        sid = new["id"]
        self.assertEqual((new["status"], new["main"]), ("done", False), "done at once")
        self.assertEqual(new["branch"], {"name": "suggestions", "from": {"id": a, "version": 3}})

        # in the same group
        group = self.ok("GET", f"/api/jobs/{a}/group")
        self.assertEqual([j["id"] for j in group["jobs"]], [a, b, sid])
        self.assertEqual([j["label"] for j in group["jobs"]], ["whisper-medium code-switching", "Cohere Transcribe Arabic",
                                                               "suggestions"])
        listed = {j["id"]: j for j in self.ok("GET", "/api/jobs")["jobs"]}
        self.assertEqual(listed[sid]["group"], listed[a]["group"])

        # its version 0 is exactly version 3: the lines with their times and speakers, the names and the title
        versions = self.versions(sid)
        self.assertEqual([(v["n"], v["kind"], v["origin"]) for v in versions], [(0, "branch", {"id": a, "version": 3})])
        self.assertEqual(versions[0]["summary"], "Branched from whisper-medium code-switching, version 3")
        v0 = self.ok("GET", f"/api/jobs/{sid}/versions/0")
        for key in ("lines", "speaker_names", "title"):
            self.assertEqual(v0[key], v3[key], key)
        self.assertEqual(v3["speaker_names"], {"1": "Mona", "2": "Omar"})
        self.assertNotEqual(v3["lines"], self.ok("GET", f"/api/jobs/{a}")["lines"], "not the latest version, v4")
        page = self.ok("GET", f"/api/jobs/{sid}")
        self.assertEqual((page["lines"], page["job"]["speaker_names"], page["job"]["title"], page["version"], page["edited"]),
                         (v3["lines"], v3["speaker_names"], "Sprint planning", 0, False))
        self.assertEqual(page["job"]["rerun_of"], a)

        # the audio is the same file, and the folder has no model output
        folder, source = self.cfg.storage / "jobs" / sid, self.cfg.storage / "jobs" / a
        self.assertTrue(os.path.samefile(folder / "audio.flac", source / "audio.flac"), "shared by hardlink")
        self.assertFalse((folder / "engine").exists() or (folder / "hosted.json").exists())
        self.assertFalse(self.ok("GET", f"/api/jobs/{sid}/export/json")["details"]["run"]["edited"])

        # it goes on like any transcript, and its version 0 stays
        edited = self.ok("PATCH", f"/api/jobs/{sid}", {"lines": changed(v3["lines"], 1, "Yes."), "message": "checked"})
        self.assertEqual((edited["version"], edited["edited"]), (1, True))
        self.assertEqual(self.ok("POST", f"/api/jobs/{sid}/versions/0/restore", {})["edited"], False)
        self.assertEqual(self.ok("GET", f"/api/jobs/{a}")["version"], 4, "the transcript it came from is left alone")

    def test_a_branch_of_a_branch_and_of_version_0(self):
        a = self.job()
        first = self.ok("POST", f"/api/jobs/{a}/branch", {"version": 0, "name": "  checked \n by hand  "}, 201)
        self.assertEqual(first["branch"]["name"], "checked by hand", "spaces folded")
        second = self.ok("POST", f"/api/jobs/{first['id']}/branch", {"version": "0", "name": "x" * 80}, 201)
        self.assertEqual(second["branch"], {"name": "x" * 60, "from": {"id": first["id"], "version": 0}}, "cut at 60")
        self.assertEqual(self.versions(second["id"])[0]["summary"], "Branched from checked by hand, version 0")
        group = [j["id"] for j in self.ok("GET", f"/api/jobs/{a}/group")["jobs"]]
        self.assertEqual((group[0], set(group[1:])), (a, {first["id"], second["id"]}), "(made in the same second)")

    def test_branch_refusals(self):
        a = self.job()
        before = sorted(p.name for p in (self.cfg.storage / "jobs").iterdir())
        for body, status in (({"version": 9, "name": "x"}, 404), ({"version": -1, "name": "x"}, 400),
                             ({"version": "one", "name": "x"}, 400), ({"version": True, "name": "x"}, 400),
                             ({"version": 0}, 400), ({"version": 0, "name": "   "}, 400), ({"name": "x"}, 400)):
            self.assertEqual(self.call("POST", f"/api/jobs/{a}/branch", body)[0], status, body)
        self.assertEqual(self.call("POST", "/api/jobs/20200101-000000-0000/branch", {"version": 0, "name": "x"})[0], 404)
        self.assertEqual(self.call("POST", f"/api/jobs/{a}/branch", {"version": 0, "name": "x"},
                                   headers={"X-Sedjem": ""})[0], 403)
        running = self.job(status="running")
        self.assertEqual(self.call("POST", f"/api/jobs/{running}/branch", {"version": 0, "name": "x"})[0], 409)
        self.assertEqual(sorted(p.name for p in (self.cfg.storage / "jobs").iterdir()), sorted(before + [running]),
                         "no job was made")

    def test_a_branch_of_a_hidden_transcription_is_hidden_too(self):
        a, shown = self.job(), self.job()
        assistants.set_hidden(self.cfg, f"job:{a}", True)
        seen = []
        real = Runner.branch

        def watch(runner, jid, n, name, before=None):  # hidden before it is listed
            def check(new):
                before(new)
                seen.append((f"job:{new}" in self.cfg.setting("mcp_hidden"), (self.cfg.storage / "jobs" / new / "job.json").exists()))
            return real(runner, jid, n, name, check)
        with mock.patch.object(Runner, "branch", watch):
            new = self.ok("POST", f"/api/jobs/{a}/branch", {"version": 0, "name": "private"}, 201)["id"]
        self.assertEqual(seen, [(True, False)])
        self.assertIn(f"job:{new}", self.cfg.setting("mcp_hidden"))
        text, failed = self.mcp("get_transcript", id=new)
        self.assertTrue(failed and text.startswith("There is no transcription"), text)
        other = self.ok("POST", f"/api/jobs/{shown}/branch", {"version": 0, "name": "open"}, 201)["id"]
        self.assertNotIn(f"job:{other}", self.cfg.setting("mcp_hidden"), "a visible one's branch stays visible")
        self.assertFalse(self.mcp("get_transcript", id=other)[1])


class Main(Base):
    def test_a_branch_marked_main_is_listed_first_with_the_model_transcripts_as_its_branches(self):
        a = self.job(title="Retro")
        b = self.job(OTHER, rerun_of=a, title="Retro", **COHERE)
        self.ok("PATCH", f"/api/jobs/{a}", {"lines": changed(LINES, 0, "تمام، نبدأ"), "message": "fixed"})
        s = self.ok("POST", f"/api/jobs/{a}/branch", {"version": 1, "name": "main text"}, 201)["id"]
        self.assertEqual([j["id"] for j in self.ok("GET", f"/api/jobs/{a}/group")["jobs"]], [a, b, s], "no main yet")

        group = self.ok("POST", f"/api/jobs/{s}/main", {"main": True})
        self.assertEqual([(j["id"], j["main"], j["label"]) for j in group["jobs"]],
                         [(s, True, "main text"), (a, False, "whisper-medium code-switching"),
                          (b, False, "Cohere Transcribe Arabic")], "main first, then the others oldest first")
        self.assertEqual(self.ok("GET", f"/api/jobs/{b}/group")["jobs"], group["jobs"])
        listed = {j["id"]: j["main"] for j in self.ok("GET", "/api/jobs")["jobs"] if j["id"] in (a, b, s)}
        self.assertEqual(listed, {a: False, b: False, s: True})
        self.assertTrue(self.store.get(s)["main"], "stored in job.json")

        # and an assistant sees the recording once: the main transcript, then the model transcripts as its branches
        text, _ = self.mcp("list_transcriptions", text="Retro")
        self.assertTrue(text.startswith("1 recording with 3 transcriptions"), text)
        self.assertEqual([line.split(" · ")[0] for line in text.splitlines() if " · " in line][:1], [f"  id {s}"])
        self.assertIn("the main transcript of this recording (main text), latest version 0; its branches:", text)
        self.assertIn(f"  - whisper-medium code-switching · whisper-medium code-switching · id {a} · latest version 1", text)
        self.assertIn(f"  - Cohere Transcribe Arabic · Cohere Transcribe Arabic · id {b} · latest version 0", text)
        self.assertLess(text.index(f"id {s}"), text.index(f"id {a}"))
        self.assertLess(text.index(f"id {a}"), text.index(f"id {b}"))

    def test_one_main_per_recording(self):
        a = self.job()
        b = self.job(OTHER, rerun_of=a, **COHERE)
        other = self.job()  # another recording, with its own main transcript
        self.ok("POST", f"/api/jobs/{other}/main", {"main": True})
        self.ok("POST", f"/api/jobs/{a}/main", {"main": True})
        group = self.ok("POST", f"/api/jobs/{b}/main", {"main": True})["jobs"]
        self.assertEqual([(j["id"], j["main"]) for j in group], [(b, True), (a, False)])
        self.assertEqual((self.store.get(a)["main"], self.store.get(b)["main"]), (False, True))
        self.assertTrue(self.store.get(other)["main"], "another recording keeps its own")
        group = self.ok("POST", f"/api/jobs/{b}/main", {"main": False})["jobs"]
        self.assertEqual([(j["id"], j["main"]) for j in group], [(a, False), (b, False)], "no main: as before")
        # two marked in one group (two groups found to be one recording later): the newest counts
        self.store.update(a, main=True)
        self.store.update(b, main=True)
        self.assertEqual([j["id"] for j in self.ok("GET", f"/api/jobs/{a}/group")["jobs"] if j["main"]], [b])
        self.assertEqual({j["id"] for j in self.ok("GET", "/api/jobs")["jobs"] if j["main"] and j["id"] in (a, b, other)},
                         {b, other})

    def test_only_a_finished_transcription_can_be_main(self):
        a = self.job()
        running = self.job(rerun_of=a, status="running")
        failed = self.job(rerun_of=a, status="failed")
        for jid in (running, failed):
            self.assertEqual(self.call("POST", f"/api/jobs/{jid}/main", {"main": True})[0], 409)
            self.assertNotIn("main", self.store.get(jid))
        for body in ({}, {"main": "yes"}, {"main": 1}, {"main": None}):
            self.assertEqual(self.call("POST", f"/api/jobs/{a}/main", body)[0], 400, body)
        self.assertEqual(self.call("POST", "/api/jobs/20200101-000000-0000/main", {"main": True})[0], 404)
        self.assertEqual(self.call("POST", f"/api/jobs/{a}/main", {"main": True}, headers={"X-Sedjem": ""})[0], 403)
        self.assertEqual(self.call("POST", f"/api/jobs/{running}/main", {"main": False})[0], 200, "unmarking is fine")

    def test_course_files_follow_the_main_transcript(self):
        course = {"id": "PLbranches1", "title": "Branches course", "url": "https://www.youtube.com/playlist?list=PLbranches1",
                  "count": 3, "folder": "Branches course", "index": 2}
        lecture = {"youtube_id": "vidbranch02", "source_url": "https://www.youtube.com/watch?v=vidbranch02", "course": course}
        first = self.job(changed(LINES, 0, "the first model's text"), title="Lecture two",
                         finished="2026-10-20T10:00:00+03:00", **lecture)
        later = self.job(changed(LINES, 0, "the later model's text"), title="Lecture two", rerun_of=first,
                         finished="2026-10-20T11:00:00+03:00", **{**lecture, **COHERE})
        md = self.cfg.courses / "Branches course" / "02 - Lecture two.md"
        marks = {"first": "the first model's text", "later": "the later model's text", "branch": "a branch's own text"}

        def follows():
            courses.refresh(self.store, self.cfg, self.store.get(first))
            text = md.read_text(encoding="utf-8")
            return [name for name, mark in marks.items() if mark in text]
        self.assertEqual(follows(), ["later"], "the one that finished last")
        self.ok("POST", f"/api/jobs/{first}/main", {"main": True})
        self.assertIn("the first model's text", md.read_text(encoding="utf-8"), "marking it main rewrites the files")
        self.assertEqual(follows(), ["first"])
        s = self.ok("POST", f"/api/jobs/{later}/branch", {"version": 0, "name": "notes"}, 201)["id"]
        self.ok("PATCH", f"/api/jobs/{s}", {"lines": changed(LINES, 0, "a branch's own text")})
        self.assertEqual(follows(), ["first"], "a newer branch doesn't take the main one's place")
        self.ok("POST", f"/api/jobs/{first}/main", {"main": False})
        self.assertEqual(follows(), ["later"], "without a main one: the last finished, leaving out branches")
        self.ok("POST", f"/api/jobs/{s}/main", {"main": True})
        self.assertEqual(follows(), ["branch"], "a branch that is main")
        self.assertIn("a branch's own text", (md.parent / "course.md").read_text(encoding="utf-8"))


class CombineIntoMain(Base):
    def test_combining_from_a_branch_into_main_makes_one_version_that_says_where_each_stretch_came_from(self):
        a = self.job()
        b = self.job(OTHER, rerun_of=a, **COHERE)
        main = self.ok("POST", f"/api/jobs/{a}/branch", {"version": 0, "name": "main"}, 201)["id"]
        self.ok("POST", f"/api/jobs/{main}/main", {"main": True})
        guesses = self.ok("POST", f"/api/jobs/{a}/branch", {"version": 0, "name": "suggestions"}, 201)["id"]
        guessed = changed(changed(LINES, 0, "تمام، نبدأ الـ meeting دلوقتي"), 2, "الـ deadline يوم الأربع")
        self.ok("PATCH", f"/api/jobs/{guesses}", {"lines": guessed, "message": "machine guesses"})
        self.assertEqual(self.ok("PATCH", f"/api/jobs/{guesses}", {"lines": changed(guessed, 3, "Jira, today."),
                                                                   "message": "more"})["version"], 2)
        before = [v["n"] for v in self.versions(main)]
        view = self.ok("GET", f"/api/compare?ids={guesses},{main}")
        self.assertEqual(view["base"], main, "the main transcript is the base when none is given")
        self.assertEqual([(j["label"], j["main"]) for j in view["jobs"]], [("suggestions", False), ("main", True)])
        self.assertEqual(self.ok("GET", f"/api/compare?ids={guesses},{b}")["base"], guesses, "without it: the first, as before")

        body = {"ids": [guesses, b, main],
                "picks": [{"start": 0, "end": 3.4, "from": guesses}, {"start": 3.4, "end": 6.2, "from": b},
                          {"start": 6.2, "end": 9.3, "from": guesses}]}
        r = self.ok("POST", "/api/combine", {**body, "message": "checked against the recording"})  # (no base: main)
        self.assertEqual(r["job"]["id"], main)
        after = self.versions(main)
        self.assertEqual([v["n"] for v in after], before + [before[-1] + 1], "one new version of main")
        v = after[-1]
        self.assertEqual((v["kind"], v["message"]), ("combine", "checked against the recording"))
        self.assertEqual(v["combined"]["ranges"], [{"start": 0, "end": 3.4, "from": guesses, "version": 2},
                                                   {"start": 3.4, "end": 6.2, "from": b, "version": 0},
                                                   {"start": 6.2, "end": 9.3, "from": guesses, "version": 2}])
        self.assertEqual([(x["id"], x["label"], x["version"]) for x in v["combined"]["sources"]],
                         [(guesses, "suggestions", 2), (b, "Cohere Transcribe Arabic", 0)])  # (in the order of ids)
        self.assertTrue(v["summary"].startswith("With the text of suggestions (version 2) for 00:00–00:03, 00:06–00:09; "
                                                "Cohere Transcribe Arabic (version 0) for 00:03–00:06: "), v["summary"])
        self.assertEqual([x["text"] for x in r["lines"]], [guessed[0]["text"], OTHER[1]["text"], guessed[2]["text"],
                                                          LINES[3]["text"]])
        self.assertEqual(self.ok("GET", f"/api/jobs/{guesses}")["version"], 2, "the branch is left as it was")
        main_job = self.ok("GET", f"/api/jobs/{main}")["job"]
        self.assertEqual((main_job["main"], main_job["branch"]["name"]), (True, "main"))


class StaleSave(Base):
    def test_patch_with_an_old_base_version_gets_409_and_changes_nothing(self):
        a = self.job()
        self.assertEqual(self.ok("PATCH", f"/api/jobs/{a}", {"title": "Planning", "base_version": 0})["version"], 1)
        folder = self.cfg.storage / "jobs" / a
        files = {p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file() and p.suffix == ".json"}
        for body in ({"lines": changed(LINES, 0, "late"), "base_version": 0}, {"title": "Stale", "base_version": 0},
                     {"speaker_names": {"1": "Mona"}, "base_version": 0}, {"merge": {"from": "3", "into": "1"}, "base_version": 0}):
            status, r = self.call("PATCH", f"/api/jobs/{a}", body)
            self.assertEqual((status, r["version"]), (409, 1), body)
            self.assertIn("version 1 is the latest", r["error"])
        self.assertEqual({p.relative_to(folder): p.read_bytes() for p in folder.rglob("*") if p.is_file() and p.suffix == ".json"},
                         files, "nothing changed: not the transcript, the job or its history")
        self.assertEqual(self.ok("PATCH", f"/api/jobs/{a}", {"title": "Planning 2", "base_version": 1, "message": "x"})["version"], 2)
        for bad in ("1", -1, 1.5, True, [1]):
            self.assertEqual(self.call("PATCH", f"/api/jobs/{a}", {"title": "x", "base_version": bad})[0], 400, bad)
        running = self.job(lines=None, status="running")
        status, r = self.call("PATCH", f"/api/jobs/{running}", {"title": "x", "base_version": 0})
        self.assertEqual((status, r["version"]), (409, None), "no versions yet")
        self.assertEqual(self.store.get(running)["title"], "Weekly sync")

    def test_patch_without_base_version_works_as_before(self):
        a = self.job()
        self.ok("PATCH", f"/api/jobs/{a}", {"lines": changed(LINES, 0, "one"), "message": "one"})
        r = self.ok("PATCH", f"/api/jobs/{a}", {"lines": changed(LINES, 0, "two"), "message": "two"})
        self.assertEqual((r["version"], r["lines"][0]["text"]), (2, "two"), "the last change wins, as before")
        self.assertEqual(self.ok("PATCH", f"/api/jobs/{a}", {"speaker_names": {"1": "Mona"}})["version"], 3)
        self.assertEqual(self.ok("PATCH", f"/api/jobs/{a}", {"title": "Folded"})["version"], 3, "quick changes still fold")
        running = self.job(lines=None, status="running")
        self.assertEqual(self.ok("PATCH", f"/api/jobs/{running}", {"title": "Renamed"})["job"]["title"], "Renamed")


class Lock(unittest.TestCase):
    """history.locked: re-entrant in one thread, and exclusive across processes (the app and the MCP server)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sedjem-lock-"))
        self.store = Store(self.tmp / "app_data")
        self.jid = make_job(self.tmp, 1)
        history.current(self.store, self.jid)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_lock_is_reentrant(self):
        got = []
        with history.locked(self.store, self.jid):
            with history.locked(self.store, self.jid):  # save() and ensure() take it again inside
                entry = history.save(self.store, self.jid, {"lines": changed(LINES, 0, "held")}, "edit")
            self.assertEqual(entry["n"], 1, "no deadlock")

            def wait():
                with history.locked(self.store, self.jid):
                    got.append(time.time())
            other = threading.Thread(target=wait)
            other.start()
            time.sleep(0.3)
            self.assertEqual(got, [], "still held after the inner one ended")
            released = time.time()
        other.join(10)
        self.assertEqual(len(got), 1)
        self.assertGreaterEqual(got[0], released)
        with history.locked(self.store, "20200101-000000-0000"):  # a job that isn't there: no file is made
            pass
        self.assertFalse((self.store.root / "20200101-000000-0000").exists())

    def test_the_lock_is_held_across_processes(self):
        code = ("import sys, time\nsys.path.insert(0, sys.argv[1])\nfrom app import history\nfrom app.jobs import Store\n"
                "store = Store(sys.argv[2])\nprint('ready', flush=True)\nwith history.locked(store, sys.argv[3]):\n"
                "    print('held', flush=True)\n    time.sleep(float(sys.argv[4]))\n")

        def start(hold):
            proc = subprocess.Popen([sys.executable, "-c", code, str(ROOT), str(self.tmp / "app_data"), self.jid, str(hold)],
                                    stdout=subprocess.PIPE, text=True)
            lines = queue.Queue()
            threading.Thread(target=lambda: [lines.put(x.strip()) for x in proc.stdout] + [lines.put(None)], daemon=True).start()
            self.addCleanup(lambda: (proc.kill(), proc.wait(), proc.stdout.close()))
            return proc, lines

        with history.locked(self.store, self.jid), history.locked(self.store, self.jid):
            proc, lines = start(0)
            self.assertEqual(lines.get(timeout=60), "ready")
            with self.assertRaises(queue.Empty):
                lines.get(timeout=1.5)  # the other process waits for this one
        self.assertEqual(lines.get(timeout=20), "held", "and goes on once it lets go")
        self.assertEqual(proc.wait(20), 0)

        proc, lines = start(1.5)  # the other way round: this process waits
        self.assertEqual(lines.get(timeout=60), "ready")
        self.assertEqual(lines.get(timeout=20), "held")
        t0 = time.time()
        with history.locked(self.store, self.jid):
            waited = time.time() - t0
        self.assertGreater(waited, 0.8, "it waited until the other process let go")
        self.assertEqual(proc.wait(20), 0)


if __name__ == "__main__":
    unittest.main()
