"""Tests for the version history of transcripts (app/history.py) and its part of the API: the model's
output kept as version 0, a new version with a message and a summary for every change, going back to
an earlier version, jobs from before the history, exports of a version, several changes at once, and
edits that are still there after the app restarts. The job folders are written the way the app writes
them, so no model runs.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import json
import shutil
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
from app import engines, history, transcript as T  # noqa: E402
from app.config import Config  # noqa: E402
from app.jobs import Store, now  # noqa: E402
from app.server import make_server  # noqa: E402

# transcribe.py's output (engine/*.json) for a short made-up meeting: speakers are numbers or None
MODEL_LINES = [
    {"start": 0.5, "end": 3.2, "speaker": 1, "text": "تمام، نبدأ الـ meeting؟"},
    {"start": 3.6, "end": 6.0, "speaker": 2, "text": "Yes, let's start with the sprint."},
    {"start": 6.4, "end": 9.1, "speaker": 1, "text": "الـ deadline يوم الخميس"},
    {"start": 9.5, "end": 12.0, "speaker": 3, "text": "I'll update the Jira board."},
]
# a hosted job keeps the service's reply as hosted.json (ElevenLabs' format)
HOSTED_REPLY = {"language_code": "ara", "transcription_id": "tr_1", "words": [
    {"text": "تمام،", "start": 0.1, "end": 0.5, "type": "word", "speaker_id": "speaker_0"},
    {"text": "نبدأ", "start": 0.6, "end": 0.9, "type": "word", "speaker_id": "speaker_0"},
    {"text": "Yes,", "start": 1.9, "end": 2.2, "type": "word", "speaker_id": "speaker_1"},
    {"text": "go", "start": 2.3, "end": 2.6, "type": "word", "speaker_id": "speaker_1"},
]}


def make_job(store, kind="local", edited=None, names=None, keep_output=True, start=True, status="done"):
    """A job folder as the app leaves a finished job: job.json, transcript.json (the lines given as edited,
    or the model's), and the model's own output (engine/ for a local model, hosted.json for a hosted one).
    start does what the app does when a job finishes; without it the folder is like one from before the
    history existed. Returns (job id, the model's lines)."""
    jid = store.new_id()
    while store.dir(jid).exists():
        jid = store.new_id()
    model, title = ("whisper-medium", "whisper-medium code-switching") if kind == "local" else ("elevenlabs", "ElevenLabs")
    store.create({"id": jid, "title": "Weekly sync", "source_name": "sync.m4a", "created": now(), "model": model,
                  "model_title": title, "kind": kind, "options": {"speakers": "auto", "language": "ar", "prompt": ""},
                  "status": status, "stage": None, "speaker_names": names or {}, "finished": now(), "audio_s": 12.0})
    folder = store.dir(jid)
    if kind == "local":
        (folder / "engine").mkdir()
        (folder / "engine" / "audio.whisper.speakers.json").write_text(json.dumps(MODEL_LINES, ensure_ascii=False),
                                                                        encoding="utf-8")
        lines = T.from_transcribe_py(MODEL_LINES)
    else:
        (folder / "hosted.json").write_text(json.dumps(HOSTED_REPLY, ensure_ascii=False), encoding="utf-8")
        lines = engines.elevenlabs_lines(HOSTED_REPLY)
    if status == "done":
        store.save_transcript(jid, {"lines": edited or lines, "edited": edited is not None, "model": model})
    if not keep_output:
        shutil.rmtree(folder / "engine", ignore_errors=True)
        (folder / "hosted.json").unlink(missing_ok=True)
    if start:
        history.current(store, jid)
    return jid, lines


def changed(lines, i, text):
    out = [dict(x) for x in lines]
    out[i]["text"] = text
    return out


class HistoryInStore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sedjem-history-"))
        self.store = Store(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def numbers(self, jid):
        return [v["n"] for v in history.ensure(self.store, jid)]

    def test_a_speaker_written_as_a_number_or_as_text_is_the_same_speaker(self):
        # transcribe.py writes speakers as numbers (MODEL_LINES), the app's edits as text: one line edited is one
        # line changed, not every line with a speaker
        as_text = [{**x, "speaker": None if x["speaker"] is None else str(x["speaker"])} for x in MODEL_LINES]
        edited = changed(as_text, 1, "Yes, let's start with the review.")
        old = {"title": "Weekly sync", "speaker_names": {}, "lines": MODEL_LINES}
        self.assertEqual(history.describe(old, {**old, "lines": edited}), "1 line changed")
        self.assertEqual([r["op"] for r in history.diff_lines(MODEL_LINES, edited)], ["same", "changed", "same", "same"])

    def test_model_output_is_version_0_and_never_changes(self):
        jid, lines = make_job(self.store)
        v0_file = self.store.dir(jid) / "history" / "v0000.json"
        v0_bytes = v0_file.read_bytes()
        edited = changed(lines, 0, "تمام، نبدأ الـ meeting دلوقتي؟")
        self.assertEqual(history.save(self.store, jid, {"lines": edited}, "edit", "Fixed the first line")["n"], 1,
                         "the first saved edit is version 1")
        history.save(self.store, jid, {"speaker_names": {"1": "Mona"}}, "rename")
        history.save(self.store, jid, {"title": "Sprint planning"}, "rename", "A better title")
        merged = [{**x, "speaker": "1" if x["speaker"] == "3" else x["speaker"]} for x in edited]
        history.save(self.store, jid, {"lines": merged}, "merge")
        history.restore(self.store, jid, 1)
        self.assertEqual(v0_file.read_bytes(), v0_bytes)
        v0 = history.version(self.store, jid, 0)
        self.assertEqual((v0["version"]["n"], v0["version"]["kind"], v0["version"]["parent"]), (0, "model output", None))
        self.assertEqual((v0["lines"], v0["speaker_names"], v0["changes"]), (lines, {}, None))
        self.assertEqual(self.numbers(jid), [0, 1, 2, 3, 4, 5])
        self.assertEqual(self.store.transcript(jid)["lines"], edited, "the current version is what the app reads")

    def test_messages_including_empty(self):
        jid, lines = make_job(self.store)
        messages = ["Fixed the Jira line", "", None, "  two\n  lines ", "x" * 800]
        for i, message in enumerate(messages):
            history.save(self.store, jid, {"lines": changed(lines, 3, f"I'll update the Jira board ({i}).")}, "edit",
                         message)
        self.assertEqual([v["message"] for v in history.ensure(self.store, jid)],
                         ["", "Fixed the Jira line", "", "", "two lines", "x" * 500])
        history.set_message(self.store, jid, 0, "Straight from the model")
        history.set_message(self.store, jid, 1, "")
        versions = history.ensure(self.store, jid)
        self.assertEqual((versions[0]["message"], versions[1]["message"]), ("Straight from the model", ""))
        self.assertEqual(versions[0]["kind"], "model output", "a message doesn't change the version")
        with self.assertRaises(KeyError):
            history.set_message(self.store, jid, 99, "no such version")

    def test_automatic_summaries(self):
        jid, lines = make_job(self.store)
        save = lambda change, message="": history.save(self.store, jid, change, "edit", message)["summary"]  # noqa: E731
        self.assertEqual(history.version(self.store, jid, 0)["version"]["summary"],
                         "4 lines from whisper-medium code-switching")
        three = [{**x, "text": x["text"] + " ok"} if i < 3 else x for i, x in enumerate(lines)]
        added = {"start": 20.0, "end": 21.0, "speaker": "2", "text": "Bye"}
        self.assertEqual(save({"lines": three}), "3 lines changed")
        self.assertEqual(save({"lines": three[:3]}), "1 line removed")
        self.assertEqual(save({"lines": three[:3] + [added]}), "1 line added")
        self.assertEqual(save({"lines": [{**three[0], "speaker": "2"}] + three[1:3] + [added]}), "1 line changed")
        self.assertEqual(save({"speaker_names": {"1": "Mona"}}), "Speaker 1 renamed to Mona")
        self.assertEqual(save({"title": "Sprint planning"}), "Title changed to “Sprint planning”")
        self.assertEqual(save({"lines": three[:3] + [added], "speaker_names": {"1": "Mona Ali", "2": "Ali"}}),
                         "1 line changed, Mona renamed to Mona Ali, Speaker 2 renamed to Ali")
        state = history.version(self.store, jid, 1)
        self.assertEqual(history.describe(state, state), "No changes")

    def test_nothing_changed_makes_no_version(self):
        jid, lines = make_job(self.store)
        self.assertIsNone(history.save(self.store, jid, {"lines": lines}, "edit", "Nothing really"))
        self.assertIsNone(history.save(self.store, jid, {"title": "Weekly sync"}, "rename"))
        self.assertEqual(self.numbers(jid), [0])

    def test_restore_makes_a_new_version(self):
        jid, lines = make_job(self.store)
        edited = changed(lines, 1, "Yes, let's start with the sprint review.")
        history.save(self.store, jid, {"lines": edited}, "edit", "Fixed a word")
        history.save(self.store, jid, {"speaker_names": {"2": "Ali"}}, "rename")
        entry = history.restore(self.store, jid, 0, "Back to the model")
        self.assertEqual((entry["n"], entry["kind"], entry["restored_from"], entry["parent"], entry["message"]),
                         (3, "restore", 0, 2, "Back to the model"))
        self.assertEqual(entry["summary"], "Restored version 0 (1 line changed, Ali renamed to Speaker 2)")
        v0, v3 = history.version(self.store, jid, 0), history.version(self.store, jid, 3)
        self.assertEqual({k: v3[k] for k in ("title", "speaker_names", "lines")},
                         {k: v0[k] for k in ("title", "speaker_names", "lines")})
        self.assertEqual(history.version(self.store, jid, 1)["lines"], edited, "the versions in between stay")
        self.assertEqual(self.store.transcript(jid)["lines"], lines)
        self.assertFalse(self.store.transcript(jid)["edited"], "back to the model's lines")
        self.assertEqual(self.store.get(jid)["speaker_names"], {})
        self.assertIsNone(history.restore(self.store, jid, 0), "already the same: no new version")
        self.assertEqual(history.restore(self.store, jid, 1)["n"], 4)
        self.assertTrue(self.store.transcript(jid)["edited"])
        with self.assertRaises(KeyError):
            history.restore(self.store, jid, 99)
        self.assertEqual(self.numbers(jid), [0, 1, 2, 3, 4])

    def test_quick_changes_close_together_are_one_version(self):
        jid, lines = make_job(self.store)

        def quick(change, kind="rename", message="", merge=None):
            return history.save(self.store, jid, change, kind, message, merge=merge)
        self.assertEqual(quick({"speaker_names": {"1": "Mona"}})["n"], 1)
        self.assertEqual(quick({"speaker_names": {"1": "Mona", "2": "Ali"}})["n"], 1)
        self.assertEqual(quick({"title": "Sprint planning"})["n"], 1)
        merged = [{**x, "speaker": "2" if x["speaker"] == "3" else x["speaker"]} for x in lines]
        folded = quick({"lines": merged}, "merge", merge={"from": "3", "note": "Speaker 3 merged into Ali (1 line)"})
        self.assertEqual((folded["n"], folded["kind"]), (1, "merge"))
        self.assertEqual(folded["summary"], "Speaker 3 merged into Ali (1 line), Speaker 1 renamed to Mona, "
                                            "Speaker 2 renamed to Ali, title changed to “Sprint planning”")
        v1 = history.version(self.store, jid, 1)
        self.assertEqual((v1["speaker_names"], v1["title"], v1["lines"]),
                         ({"1": "Mona", "2": "Ali"}, "Sprint planning", merged))
        self.assertEqual(quick({"speaker_names": {"1": "Mona", "2": "Ali", "3": "Omar"}}, message="From the invite")["n"], 2)
        self.assertEqual(quick({"speaker_names": {"1": "Mona", "2": "Ali"}})["n"], 3, "after a message: its own version")
        back = quick({"speaker_names": {"1": "Mona", "2": "Ali", "3": "Omar"}})  # back to version 2: its own step too
        self.assertEqual((back["n"], back["summary"]), (4, "Speaker 3 renamed to Omar"))
        with mock.patch.object(history, "FOLD_SECONDS", 0):
            self.assertEqual(quick({"title": "Planning"})["n"], 5)
        self.assertEqual(history.save(self.store, jid, {"lines": lines}, "edit")["n"], 6, "an edit session is not folded")
        self.assertEqual(quick({"title": "Planning, week 40"})["n"], 7, "and nothing is folded into one")

    def test_one_edit_session_is_one_version(self):
        jid, lines = make_job(self.store)
        change = {"lines": changed(lines, 0, "تمام، نبدأ"), "speaker_names": {"1": "Mona"}, "title": "Sprint planning"}
        entry = history.save(self.store, jid, change, "edit", "Checked against the recording")
        self.assertEqual((entry["n"], entry["kind"], entry["message"]), (1, "edit", "Checked against the recording"))
        self.assertEqual(entry["summary"], "1 line changed, Speaker 1 renamed to Mona, title changed to “Sprint planning”")

    def test_review_before_saving(self):
        """preview: what saving would change, as a diff, without saving anything."""
        jid, lines = make_job(self.store)
        change = {"lines": changed(lines, 1, "Yes, let's start with the sprint review."), "speaker_names": {"2": "Ali"},
                  "title": "Sprint planning"}
        found = history.preview(self.store, jid, change)
        self.assertEqual(found["version"], 0, "compared with the model's output")
        self.assertEqual(found["summary"], "1 line changed, Speaker 2 renamed to Ali, title changed to “Sprint planning”")
        ch = found["changes"]
        self.assertEqual(ch["stat"], "1 line changed, 1 speaker renamed, title changed")
        self.assertEqual((ch["title"], ch["renamed"]), (["Weekly sync", "Sprint planning"],
                                                         [{"id": "2", "from": "Speaker 2", "to": "Ali"}]))
        self.assertEqual((ch["names_before"], ch["names_after"]), ({}, {"2": "Ali"}))
        self.assertEqual([r["op"] for r in ch["lines"]], ["same", "changed", "same", "same"])
        self.assertEqual(ch["lines"][1]["words"], [["=", "Yes, let's start with the"], ["-", "sprint."], ["+", "sprint review."]])
        self.assertEqual(self.numbers(jid), [0], "nothing saved")
        self.assertEqual(self.store.transcript(jid)["lines"], lines)

    def test_compare_any_two_versions(self):
        jid, lines = make_job(self.store)
        history.save(self.store, jid, {"lines": changed(lines, 0, "one")}, "edit")
        history.save(self.store, jid, {"lines": changed(changed(lines, 0, "one"), 3, "two")}, "edit")
        self.assertEqual(history.version(self.store, jid, 2)["changes"]["stat"], "1 line changed")
        two_to_zero = history.version(self.store, jid, 2, against=0)
        self.assertEqual((two_to_zero["against"], two_to_zero["changes"]["stat"]), (0, "2 lines changed"))
        self.assertEqual(history.version(self.store, jid, 0, against=2)["changes"]["stat"], "2 lines changed")
        self.assertEqual(history.version(self.store, jid, 1)["against"], 0, "the one before version 1 is version 0")
        self.assertIsNone(history.version(self.store, jid, 0)["changes"], "the original has nothing before it")
        self.assertIsNone(history.version(self.store, jid, 2, against=9))

    def test_old_job_with_the_model_output(self):
        jid, lines = make_job(self.store, start=False, names={"1": "Mona"},
                              edited=changed(T.from_transcribe_py(MODEL_LINES), 2, "الـ deadline يوم الأربع"))
        self.assertFalse((self.store.dir(jid) / "history").exists())
        versions = history.ensure(self.store, jid)
        self.assertEqual([(v["n"], v["kind"]) for v in versions], [(0, "model output"), (1, "edit")])
        v0, v1 = history.version(self.store, jid, 0), history.version(self.store, jid, 1)
        self.assertEqual((v0["lines"], v0["speaker_names"]), (lines, {}))
        self.assertEqual(v1["version"]["summary"],
                         "Changes made before the history began: 1 line changed, Speaker 1 renamed to Mona")
        self.assertEqual((v1["lines"], v1["speaker_names"]), (self.store.transcript(jid)["lines"], {"1": "Mona"}))
        self.assertEqual(self.numbers(jid), [0, 1], "started once")

    def test_old_hosted_job_with_the_model_output(self):
        jid, lines = make_job(self.store, kind="hosted", start=False,
                              edited=changed(engines.elevenlabs_lines(HOSTED_REPLY), 1, "Yes, go on"))
        self.assertEqual(history.version(self.store, jid, 0)["lines"], lines)
        self.assertEqual(history.version(self.store, jid, 0)["version"]["kind"], "model output")
        self.assertEqual(history.version(self.store, jid, 1)["lines"][1]["text"], "Yes, go on")

    def test_old_job_never_edited(self):
        jid, lines = make_job(self.store, start=False, names={"2": "Ali"})
        v0, v1 = history.version(self.store, jid, 0), history.version(self.store, jid, 1)
        self.assertEqual((v0["lines"], v0["speaker_names"]), (lines, {}))
        self.assertEqual(v1["version"]["summary"], "Changes made before the history began: Speaker 2 renamed to Ali")

    def test_old_job_without_the_model_output(self):
        edited = changed(T.from_transcribe_py(MODEL_LINES), 0, "تمام")
        jid, _ = make_job(self.store, start=False, keep_output=False, edited=edited)
        versions = history.ensure(self.store, jid)
        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["kind"], "edit")
        self.assertIn("original output was not kept", versions[0]["summary"])
        self.assertEqual((versions[0]["n"], history.version(self.store, jid, 0)["lines"]), (0, edited))

    def test_changes_made_outside_the_app_are_kept(self):
        jid, lines = make_job(self.store)
        self.store.save_transcript(jid, {"lines": lines[1:], "edited": True, "model": "whisper-medium"})
        versions = history.ensure(self.store, jid)
        self.assertEqual(versions[-1]["summary"], "Changes made outside the history: 1 line removed")
        self.assertEqual(history.version(self.store, jid, 1)["lines"], lines[1:])

    def test_a_running_job_has_no_history_yet(self):
        jid, _ = make_job(self.store, status="running", start=False)
        self.assertIsNone(history.ensure(self.store, jid))
        self.assertIsNone(history.current(self.store, jid))
        self.assertIsNone(history.save(self.store, jid, {"title": "Sprint planning"}, "rename"))
        self.assertEqual(self.store.get(jid)["title"], "Sprint planning", "the change is made all the same")
        self.assertFalse((self.store.dir(jid) / "history").exists())

    def test_an_unreadable_history_is_kept_aside(self):
        jid, lines = make_job(self.store)
        history.save(self.store, jid, {"lines": lines[:3]}, "edit", "Dropped the last line")
        (self.store.dir(jid) / "history" / "index.json").write_text("{not json", encoding="utf-8")
        versions = history.ensure(self.store, jid)
        self.assertEqual([v["kind"] for v in versions], ["model output", "edit"])
        aside = list(self.store.dir(jid).glob("history-unreadable-*"))
        self.assertEqual(len(aside), 1)
        self.assertTrue((aside[0] / "v0001.json").exists(), "nothing is deleted")

    def test_line_differences(self):
        old = T.from_transcribe_py(MODEL_LINES)
        new = [{**old[0], "text": "تمام، نبدأ الـ meeting دلوقتي؟"}, {**old[1], "speaker": "3"}, old[3],
               {"start": 13.0, "end": 14.0, "speaker": "2", "text": "Thanks"}]
        rows = history.diff_lines(old, new)
        self.assertEqual([r["op"] for r in rows], ["changed", "changed", "removed", "same", "added"])
        self.assertEqual(rows[0]["words"], [["=", "تمام، نبدأ الـ"], ["-", "meeting؟"], ["+", "meeting دلوقتي؟"]])
        self.assertNotIn("words", rows[1], "only the speaker changed")
        self.assertEqual((rows[1]["old"]["speaker"], rows[1]["line"]["speaker"]), ("2", "3"))
        self.assertEqual(rows[2]["line"], old[2])


class HistoryApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="sedjem-history-api-"))
        (cls.tmp / "data").mkdir()
        cls.cfg = Config(storage=cls.tmp / "data")
        cls.server, cls.app = make_server(cls.cfg, port=0)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"X-Sedjem": "1", **({"Content-Type": "application/json"} if data else {})}
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                payload = r.read()
                return r.status, (json.loads(payload) if "json" in r.headers.get("Content-Type", "") else payload), r.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}"), e.headers

    def test_routes(self):
        jid, lines = make_job(self.app.store)
        base = f"/api/jobs/{jid}"
        self.assertEqual(self.call("GET", base)[1]["version"], 0, "the model's output is version 0")
        fixed = changed(lines, 3, "I'll update the Jira board today.")
        status, r, _ = self.call("PATCH", base, {"lines": fixed, "message": "Fixed the Jira line"})
        self.assertEqual((status, r["version"], r["lines"][3]["text"]), (200, 1, "I'll update the Jira board today."))
        versions = self.call("GET", f"{base}/versions")[1]["versions"]
        self.assertEqual([(v["n"], v["kind"], v["message"]) for v in versions],
                         [(0, "model output", ""), (1, "edit", "Fixed the Jira line")])
        v1 = self.call("GET", f"{base}/versions/1")[1]
        self.assertEqual([row["op"] for row in v1["changes"]["lines"]], ["same", "same", "same", "changed"])
        self.assertEqual((v1["against"], v1["changes"]["lines"][3]["words"][-1]), (0, ["+", "board today."]))
        self.assertIsNone(self.call("GET", f"{base}/versions/0")[1]["changes"])
        self.assertEqual(self.call("GET", f"{base}/versions/9")[0], 404)
        status, r, _ = self.call("PATCH", f"{base}/versions/1", {"message": "Fixed one line"})
        self.assertEqual(r["versions"][1]["message"], "Fixed one line")
        self.assertEqual(self.call("PATCH", f"{base}/versions/9", {"message": "x"})[0], 404)

        self.assertEqual(self.call("PATCH", base, {"speaker_names": {"1": "Mona"}})[1]["version"], 2)
        r = self.call("PATCH", base, {"merge": {"from": "3", "into": "1"}})[1]  # right after the rename: the same version
        self.assertEqual((r["version"], {x["speaker"] for x in r["lines"]}), (2, {"1", "2"}))
        r = self.call("PATCH", base, {"title": "Sprint planning", "message": "A clearer title"})[1]
        self.assertEqual(r["version"], 3)
        versions = self.call("GET", f"{base}/versions")[1]["versions"]
        self.assertEqual([(v["n"], v["kind"], v["summary"]) for v in versions[2:]],
                         [(2, "merge", "Speaker 3 merged into Mona (1 line), Speaker 1 renamed to Mona"),
                          (3, "rename", "Title changed to “Sprint planning”")])

        # reviewing edits before they are saved: the diff against the current version, nothing saved
        proposal = {"lines": changed(r["lines"], 0, "تمام"), "speaker_names": {"1": "Mona", "2": "Ali"}}
        status, d, _ = self.call("POST", f"{base}/diff", proposal)
        self.assertEqual((status, d["version"], d["changes"]["stat"]), (200, 3, "1 line changed, 1 speaker renamed"))
        self.assertEqual(self.call("GET", base)[1]["version"], 3)
        self.assertEqual(self.call("POST", f"{base}/diff", {"lines": [{"start": "x"}]})[0], 400)
        # comparing any two versions, the original too
        v3 = self.call("GET", f"{base}/versions/3?against=0")[1]
        self.assertEqual((v3["against"], v3["changes"]["stat"]), (0, "1 line changed, 1 speaker renamed, title changed"))
        self.assertEqual(self.call("GET", f"{base}/versions/3?against=77")[0], 404)
        self.assertEqual(self.call("GET", f"{base}/versions/3?against=x")[0], 404)

        status, r, _ = self.call("POST", f"{base}/versions/0/restore", {"message": "Back to the model's text"})
        self.assertEqual((status, r["version"], r["lines"], r["job"]["speaker_names"], r["job"]["title"]),
                         (200, 4, lines, {}, "Weekly sync"))
        self.assertFalse(r["edited"])
        self.assertEqual(self.call("POST", f"{base}/versions/9/restore", {})[0], 404)

        job = self.app.store.get(jid)
        status, txt, headers = self.call("GET", f"{base}/export/txt?version=1&details=0")
        self.assertEqual(txt.decode(), T.to_txt({**job, "title": "Weekly sync", "speaker_names": {}}, fixed))
        self.assertIn("Weekly-sync-v1.txt", headers["Content-Disposition"])
        status, txt, headers = self.call("GET", f"{base}/export/txt?version=0&details=0")
        self.assertEqual(txt.decode(), T.to_txt(job, lines), "version 0: the model's output")
        self.assertIn("Weekly-sync-v0.txt", headers["Content-Disposition"])
        self.assertEqual(self.call("GET", f"{base}/export/txt?details=0")[1].decode(), T.to_txt(job, lines),
                         "the current version")
        # the details that come with an export say whether that version was corrected by hand
        self.assertTrue(self.call("GET", f"{base}/export/json?version=1")[1]["details"]["run"]["edited"])
        self.assertFalse(self.call("GET", f"{base}/export/json?version=0")[1]["details"]["run"]["edited"])
        self.assertFalse(self.call("GET", f"{base}/export/json")[1]["details"]["run"]["edited"])
        self.assertTrue(self.call("GET", f"{base}/export/md?version=3")[1].decode().startswith("# Sprint planning"))
        data = self.call("GET", f"{base}/export/json?version=2")[1]  # (parsed: it is JSON)
        self.assertEqual(data["speakers"], {"1": "Mona", "2": "Speaker 2"})
        self.assertEqual(self.call("GET", f"{base}/export/txt?version=99")[0], 404)
        self.assertEqual(self.call("GET", f"{base}/export/txt?version=abc")[0], 404)

    def test_two_changes_at_the_same_moment(self):
        """Two PATCH requests at once: both are kept, each as its own version, numbered in order."""
        jid, lines = make_job(self.app.store)
        base = f"/api/jobs/{jid}"
        for i in range(4):
            gate, replies = threading.Barrier(2), []

            def patch(body):
                gate.wait()
                replies.append(self.call("PATCH", base, body))
            bodies = [{"lines": changed(lines, 1, f"Yes, let's start ({i})."), "message": f"edit {i}"},
                      {"title": f"Sprint planning {i}", "message": f"title {i}"}]
            threads = [threading.Thread(target=patch, args=(b,)) for b in bodies]
            [t.start() for t in threads]
            [t.join() for t in threads]
            self.assertEqual([r[0] for r in replies], [200, 200])
        versions = self.call("GET", f"{base}/versions")[1]["versions"]
        self.assertEqual([v["n"] for v in versions], list(range(0, 9)))
        self.assertEqual([v["parent"] for v in versions], [None] + list(range(0, 8)))
        self.assertEqual({v["message"] for v in versions[1:]}, {f"{k} {i}" for k in ("edit", "title") for i in range(4)})
        now_ = self.call("GET", base)[1]
        latest = self.call("GET", f"{base}/versions/8")[1]
        self.assertEqual((latest["lines"], latest["title"]), (now_["lines"], now_["job"]["title"]))
        self.assertEqual((now_["job"]["title"], now_["lines"][1]["text"]), ("Sprint planning 3", "Yes, let's start (3)."))

    def test_a_finished_job_starts_its_history(self):
        """Through the app's own queue, with the hosted service replaced by a stand-in."""
        wav = self.tmp / "tone.wav"
        sf.write(wav, (0.2 * np.sin(2 * np.pi * 440 * np.arange(16000) / 16000)).astype(np.float32), 16000)
        self.cfg.save_key("elevenlabs", "test-key")

        def hosted(cfg, model, job_dir, job, on_progress, cancelled):
            (Path(job_dir) / "hosted.json").write_text(json.dumps(HOSTED_REPLY), encoding="utf-8")
            return {"lines": engines.elevenlabs_lines(HOSTED_REPLY), "meta": {}}
        with mock.patch.object(engines, "run_hosted", hosted):
            status, job, _ = self.call("POST", "/api/jobs", {"path": str(wav), "model": "elevenlabs",
                                                             "speakers": "auto", "confirm_upload": True})
            self.assertEqual(status, 201, job)
            index = self.cfg.storage / "jobs" / job["id"] / "history" / "index.json"
            end = time.time() + 60
            while time.time() < end and not index.exists():  # (the job list, not the job, which would start it too)
                time.sleep(0.1)
        self.assertEqual([j["status"] for j in self.call("GET", "/api/jobs")[1]["jobs"] if j["id"] == job["id"]], ["done"])
        versions = json.loads(index.read_text(encoding="utf-8"))["versions"]
        self.assertEqual([(v["n"], v["kind"]) for v in versions], [(0, "model output")])
        self.assertEqual(self.call("GET", f"/api/jobs/{job['id']}/versions/0")[1]["lines"],
                         engines.elevenlabs_lines(HOSTED_REPLY))


class EditsAreSaved(unittest.TestCase):
    """Edits made through the HTTP API of a running app, checked on disk, and read back after the app
    restarts on the same data folder."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sedjem-history-restart-"))
        (self.tmp / "data").mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self):
        server, app = make_server(Config(storage=self.tmp / "data"), port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{server.server_address[1]}"
        return server, app

    def stop(self, server, app):
        app.runner.shutdown()
        server.shutdown()
        server.server_close()

    call = HistoryApi.call

    def test_edits_survive_a_restart(self):
        server, app = self.start()
        jid, model_lines = make_job(app.store)
        base, folder = f"/api/jobs/{jid}", self.tmp / "data" / "jobs" / jid
        self.assertEqual(self.call("GET", base)[1]["version"], 0, "the model's output is version 0")
        fixed = changed(model_lines, 2, "الـ deadline يوم الأربع")
        merged = [{**x, "speaker": "1" if x["speaker"] == "3" else x["speaker"]} for x in fixed]
        self.assertEqual(self.call("PATCH", base, {"lines": fixed, "message": "Fixed the deadline"})[1]["version"], 1,
                         "the first saved edit is version 1")
        steps = [("PATCH", base, {"speaker_names": {"1": "Mona"}}),
                 ("PATCH", base, {"speaker_names": {"1": "Mona", "2": "Ali"}}),  # quick changes close together:
                 ("PATCH", base, {"merge": {"from": "3", "into": "1"}}),           # one version with the rename
                 ("PATCH", base, {"title": "Sprint planning", "message": "New title"}),
                 ("PATCH", base, {"lines": changed(merged, 0, "تمام، نبدأ"), "message": ""}),
                 ("POST", f"{base}/versions/1/restore", {"message": "Back to the fixed text"}),
                 ("PATCH", f"{base}/versions/0", {"message": "Straight from the model"})]
        for method, path, body in steps:
            self.assertEqual(self.call(method, path, body)[0], 200, (method, path, body))
        before = self.call("GET", base)[1]
        versions = self.call("GET", f"{base}/versions")[1]["versions"]
        exports = {fmt: self.call("GET", f"{base}/export/{fmt}")[1] for fmt in T.EXPORTS}
        numbered = [(0, "model output", "Straight from the model"), (1, "edit", "Fixed the deadline"),
                    (2, "merge", ""), (3, "rename", "New title"), (4, "edit", ""), (5, "restore", "Back to the fixed text")]
        self.assertEqual(before["version"], 5)
        self.assertEqual([(v["n"], v["kind"], v["message"]) for v in versions], numbered)
        self.assertEqual([v["parent"] for v in versions], [None, 0, 1, 2, 3, 4])

        # on disk: the index, one snapshot per version, and the current version where the app reads it
        index = json.loads((folder / "history" / "index.json").read_text(encoding="utf-8"))
        self.assertEqual(index["versions"], versions)
        self.assertEqual(sorted(p.name for p in (folder / "history").glob("v*.json")),
                         [f"v{n:04d}.json" for n in range(6)])
        snapshots = {v["n"]: json.loads((folder / "history" / f"v{v['n']:04d}.json").read_text(encoding="utf-8"))
                     for v in versions}
        self.assertEqual(snapshots[0], {"title": "Weekly sync", "speaker_names": {}, "lines": model_lines})
        self.assertEqual(snapshots[5], snapshots[1])
        self.assertEqual(snapshots[5]["lines"], fixed)
        self.assertEqual((snapshots[2]["speaker_names"], snapshots[2]["lines"]), ({"1": "Mona", "2": "Ali"}, merged))
        self.assertEqual(snapshots[4]["lines"][0]["text"], "تمام، نبدأ")
        self.assertEqual(json.loads((folder / "transcript.json").read_text(encoding="utf-8"))["lines"], fixed)
        job_file = json.loads((folder / "job.json").read_text(encoding="utf-8"))
        self.assertEqual((job_file["title"], job_file["speaker_names"]), ("Weekly sync", {}))

        self.stop(server, app)
        server, app = self.start()
        try:
            after = self.call("GET", base)[1]
            self.assertEqual({k: after[k] for k in ("lines", "edited", "version")},
                             {k: before[k] for k in ("lines", "edited", "version")})
            self.assertEqual((after["job"]["title"], after["job"]["speaker_names"]), ("Weekly sync", {}))
            versions_after = self.call("GET", f"{base}/versions")[1]["versions"]
            self.assertEqual(versions_after, versions)
            self.assertEqual([(v["n"], v["kind"], v["message"]) for v in versions_after], numbered,
                             "still version 0 for the original and 1 for the first edit")
            original = self.call("GET", f"{base}/versions/0")[1]
            self.assertEqual((original["lines"], original["speaker_names"], original["version"]["kind"]),
                             (T.from_transcribe_py(MODEL_LINES), {}, "model output"))
            self.assertEqual(self.call("GET", f"{base}/versions/1")[1]["against"], 0)
            for fmt, body in exports.items():
                self.assertEqual(self.call("GET", f"{base}/export/{fmt}")[1], body, fmt)
                self.assertEqual(self.call("GET", f"{base}/export/{fmt}?version=5")[1], body, fmt)
            restored, source = self.call("GET", f"{base}/versions/5")[1], self.call("GET", f"{base}/versions/1")[1]
            self.assertEqual({k: restored[k] for k in ("title", "speaker_names", "lines")},
                             {k: source[k] for k in ("title", "speaker_names", "lines")})
            v2 = self.call("GET", f"{base}/versions/2")[1]
            self.assertEqual({x["speaker"] for x in v2["lines"]}, {"1", "2"})
            self.assertEqual(v2["speaker_names"], {"1": "Mona", "2": "Ali"})
            r = self.call("POST", f"{base}/versions/3/restore", {})[1]  # and it goes on after the restart
            self.assertEqual((r["version"], r["job"]["title"]), (6, "Sprint planning"))
        finally:
            self.stop(server, app)


if __name__ == "__main__":
    unittest.main()
