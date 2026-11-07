"""Tests for transcripts of one recording (app/compare.py): grouping, lining up by time, the word
differences, the speaker mapping, combining, and the HTTP routes.

The transcripts are made up; the audio is generated tones. Nothing leaves the laptop.
Run: .venv/bin/python -m unittest discover -s tests -v
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
import urllib.request
from pathlib import Path
from unittest import mock

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import compare as C  # noqa: E402
from app.config import Config  # noqa: E402
from app.jobs import Store, convert, write_json  # noqa: E402
from app.server import make_server  # noqa: E402


def L(start, end, text, speaker=None):
    return {"start": start, "end": end, "text": text, "speaker": speaker}


def tone(path, freq, seconds=3.0):
    t = np.arange(int(16000 * seconds)) / 16000
    sf.write(path, (0.2 * np.sin(2 * np.pi * freq * t)).astype(np.float32), 16000)
    return path


def job_id(n):
    return f"20260925-1000{n:02d}-{n:04x}"


def make_job(store, n, lines=None, audio=None, link=None, **meta):
    """A finished job made by hand: its audio converted from a file like the app does, or hardlinked
    from another job (a re-run), and a transcript."""
    jid = job_id(n)
    folder = store.root / jid
    folder.mkdir(parents=True)
    if audio:
        convert(audio, folder / "audio.flac", lambda t: None, lambda: False)
    elif link:
        os.link(store.root / link / "audio.flac", folder / "audio.flac")
    if lines is not None:
        store.save_transcript(jid, {"lines": lines, "edited": False, "model": meta.get("model", "whisper-medium")})
    write_json(folder / "job.json", {
        "id": jid, "title": "Weekly sync", "source_name": "sync.wav", "created": f"2026-09-25T10:00:{n:02d}+03:00",
        "model": "whisper-medium", "model_title": "whisper-medium code-switching", "kind": "local",
        "options": {"speakers": "auto", "language": "ar", "prompt": ""}, "status": "done", "stage": None,
        "speaker_names": {}, "audio_s": 3.0, **meta})
    return jid


class Grouping(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sedjem-compare-"))
        self.store = Store(self.tmp / "data")
        self.a440 = tone(self.tmp / "a440.wav", 440)
        self.a660 = tone(self.tmp / "a660.wav", 660)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fingerprint_is_of_the_samples(self):
        x, y = self.tmp / "x.flac", self.tmp / "y.flac"
        samples, _ = sf.read(self.a440, dtype="int16")
        sf.write(x, samples, 16000, subtype="PCM_16", format="FLAC", compression_level=0.0)
        sf.write(y, samples, 16000, subtype="PCM_16", format="FLAC", compression_level=1.0)
        self.assertNotEqual(x.read_bytes(), y.read_bytes(), "the FLAC bytes differ")
        self.assertEqual(C.fingerprint(x), C.fingerprint(y), "but the samples, and so the fingerprint, don't")
        convert(self.a660, self.tmp / "z.flac", lambda t: None, lambda: False)
        self.assertNotEqual(C.fingerprint(x), C.fingerprint(self.tmp / "z.flac"))
        self.assertIsNone(C.try_fingerprint(self.tmp / "missing.flac"))

    def test_reruns_group_without_fingerprints(self):
        a = make_job(self.store, 1, audio=self.a440)
        b = make_job(self.store, 2, link=a, rerun_of=a, model="cohere")
        c = make_job(self.store, 3, link=b, rerun_of=b, model="elevenlabs")  # a re-run of a re-run
        other = make_job(self.store, 4, audio=self.a660)
        ids = C.group_ids(self.store.list())
        self.assertEqual({ids[a], ids[b], ids[c]}, {a}, "named after the oldest job")
        self.assertEqual(ids[other], other)

    def test_same_audio_added_twice_and_older_jobs(self):
        # made before fingerprints existed: nothing links them until the fingerprints are worked out
        a = make_job(self.store, 1, audio=self.a440)
        b = make_job(self.store, 2, audio=self.a440)  # the same file added again
        c = make_job(self.store, 3, link=a)  # a re-run whose rerun_of was lost: reads the shared file once
        d = make_job(self.store, 4, audio=self.a660)  # another recording
        e = make_job(self.store, 5, audio=self.a440, status="running")  # still busy: prepare will do it
        ids = C.group_ids(self.store.list())
        self.assertEqual(len(set(ids.values())), 5)
        with mock.patch.object(C, "fingerprint", wraps=C.fingerprint) as fp:
            C.fill_fingerprints(self.store)
        self.assertEqual(fp.call_count, 3, "a, b and d; c shares a's file")
        self.assertIsNone(self.store.get(e).get("fingerprint"))
        ids = C.group_ids(self.store.list())
        self.assertEqual({ids[a], ids[b], ids[c]}, {a})
        self.assertEqual(ids[d], d)
        gid, jobs = C.group_of(self.store, b)
        self.assertEqual((gid, [j["id"] for j in jobs]), (a, [a, b, c]))
        with self.assertRaises(C.Invalid):
            C.group_of(self.store, job_id(9))

    def test_fill_later_runs_in_the_background(self):
        a = make_job(self.store, 1, audio=self.a440)
        b = make_job(self.store, 2, audio=self.a440)
        C.fill_later(self.store, self.store.list())
        end = time.time() + 20
        while time.time() < end and not all(j.get("fingerprint") for j in self.store.list()):
            time.sleep(0.05)
        ids = C.group_ids(self.store.list())
        self.assertEqual(ids[a], ids[b])

    def test_unreadable_audio_is_left_alone(self):
        a = make_job(self.store, 1)
        (self.store.root / a / "audio.flac").write_bytes(b"not audio")
        C.fill_fingerprints(self.store)
        self.assertIsNone(self.store.get(a).get("fingerprint"))
        self.assertFalse(C._needs_fingerprint(self.store, self.store.get(a)), "not tried again")


class Alignment(unittest.TestCase):
    def check_partition(self, tracks, rows):
        """Every line is in exactly one row, the one whose [from, to) holds its middle."""
        seen = set()
        for r in rows:
            for k, idx in enumerate(r["lines"]):
                for i in idx:
                    x = tracks[k][i]
                    self.assertLessEqual(r["from"], (x["start"] + x["end"]) / 2)
                    self.assertLess((x["start"] + x["end"]) / 2, r["to"])
                    self.assertNotIn((k, i), seen)
                    seen.add((k, i))
        self.assertEqual(seen, {(k, i) for k, t in enumerate(tracks) for i in range(len(t))})
        for a, b in zip(rows, rows[1:]):
            self.assertLessEqual(a["to"], b["from"])

    def test_rows_cut_at_shared_pauses(self):
        a = [L(0, 4.2, "one"), L(5.0, 9.8, "two")]
        b = [L(0.1, 4.0, "one"), L(5.1, 9.9, "two")]
        rows = C.align([a, b])
        self.assertEqual([r["lines"] for r in rows], [[[0], [0]], [[1], [1]]])
        self.assertTrue(4.0 < rows[0]["to"] <= 5.0)
        self.assertEqual((rows[0]["start"], rows[0]["end"]), (0, 4.2))
        self.check_partition([a, b], rows)

    def test_lines_that_cross_stay_in_one_row(self):
        a = [L(0, 6, "a"), L(6.1, 12, "b")]
        b = [L(0, 8, "c"), L(8.1, 12, "d")]
        rows = C.align([a, b])
        self.assertEqual([r["lines"] for r in rows], [[[0, 1], [0, 1]]])

    def test_small_disagreements_at_the_edges(self):
        # the models end the first turn 0.3 s apart and there is no pause: still two rows
        a = [L(0, 10.3, "a"), L(10.4, 20, "b")]
        b = [L(0, 10.0, "c"), L(10.1, 20, "d")]
        rows = C.align([a, b])
        self.assertEqual([r["lines"] for r in rows], [[[0], [0]], [[1], [1]]])
        self.assertTrue(10.0 <= rows[0]["to"] <= 10.4, rows[0]["to"])
        self.check_partition([a, b], rows)

    def test_a_word_only_one_model_heard_joins_its_neighbour(self):
        a = [L(0, 4.2, "تمام نبدأ"), L(5.0, 9.8, "النهارده")]
        b = [L(0, 4.0, "تمام نبدأ"), L(4.3, 4.6, "اه"), L(5.1, 9.9, "النهارده")]
        rows = C.align([a, b])
        self.assertEqual([r["lines"] for r in rows], [[[0], [0, 1]], [[1], [2]]])
        self.check_partition([a, b], rows)
        # far from anything else, it keeps a row of its own
        b = [L(0, 4.0, "x"), L(12.0, 12.3, "اه")]
        rows = C.align([[L(0, 4.2, "x")], b])
        self.assertEqual([r["lines"] for r in rows], [[[0], [0]], [[], [1]]])

    def test_three_transcripts_and_gaps(self):
        a = [L(0, 3, "a0", "1"), L(3.2, 7, "a1", "2"), L(20, 24, "a2", "1")]
        b = [L(0.2, 7.1, "b0", "1"), L(19.8, 24.2, "b1", "2")]
        c = [L(0, 2.9, "c0", "1"), L(3.1, 6.8, "c1", "1"), L(30, 33, "c2", "1")]
        rows = C.align([a, b, c])
        self.assertEqual([r["lines"] for r in rows], [[[0, 1], [0], [0, 1]], [[2], [1], []], [[], [], [2]]])
        self.check_partition([a, b, c], rows)

    def test_many_lines(self):
        rng = np.random.default_rng(1)
        tracks = []
        for _ in range(3):
            t, lines = 0.0, []
            while t < 600:
                d = float(rng.uniform(0.3, 12))
                lines.append(L(round(t, 2), round(t + d, 2), "w"))
                t += d + float(rng.uniform(0, 1.5))
            tracks.append(lines)
        rows = C.align(tracks)
        self.assertGreater(len(rows), 10)
        self.check_partition(tracks, rows)

    def test_nothing(self):
        self.assertEqual(C.align([[], []]), [])


class Differences(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(C.normalize("الـdeadline"), ["ال", "deadline"])
        self.assertEqual(C.normalize("Meeting؟"), ["meeting"])
        self.assertEqual(C.normalize("أحمد"), ["احمد"])
        self.assertEqual(C.normalize("النهاردة"), C.normalize("النهارده"))
        self.assertEqual(C.normalize("على"), C.normalize("علي"))
        self.assertEqual(C.normalize("مُهِمّ"), ["مهم"])
        self.assertEqual(C.normalize("٣"), ["3"])
        self.assertEqual(C.normalize("—"), [])

    def codes(self, *texts):
        return C.differences([C.words(t) for t in texts])

    def test_mixed_arabic_and_english(self):
        a = "تمام، نبدأ الـ meeting بتاع النهارده"
        b = "تمام نبدا ال Meeting بتاعة النهاردة."
        self.assertEqual(self.codes(a, b), [[0, 0, 0, 0, 2, 0], [0, 0, 0, 0, 2, 0]])
        # an English word written in Arabic letters is a difference; an extra word is marked on its side
        self.assertEqual(self.codes("الـ deadline يوم الخميس", "الديدلاين يوم الخميس ده"),
                         [[2, 2, 0, 0], [2, 0, 0, 2]])

    def test_prefix_written_onto_the_word(self):
        self.assertEqual(self.codes("الـdata جاهزة", "ال data جاهزه"), [[0, 0], [0, 0, 0]])
        self.assertEqual(self.codes("الـdata", "data"), [[2], [0]], "the word differs by its prefix")

    def test_three_transcripts(self):
        codes = self.codes("we deploy on Thursday", "we deploy on Tuesday", "we deploy Thursday")
        self.assertEqual(codes, [[0, 0, 1, 1], [0, 0, 1, 2], [0, 0, 1]])

    def test_an_empty_cell(self):
        self.assertEqual(self.codes("okay then", ""), [[2, 2], []])

    def test_spans(self):
        ws = C.words("a b c d")
        self.assertEqual(C.spans(ws, [0, 0, 2, 0]), [["a b", 0], ["c", 2], ["d", 0]])


class Speakers(unittest.TestCase):
    base = [L(0, 10, "x", "1"), L(10, 20, "x", "2"), L(20, 30, "x", "1"), L(30, 40, "x", "3")]

    def test_numbers_that_differ(self):
        src = [L(0, 10, "x", "2"), L(10, 20, "x", "1"), L(20, 30, "x", "2"), L(30, 40, "x", "3")]
        self.assertEqual(C.map_speakers(src, self.base), {"2": "1", "1": "2", "3": "3"})
        self.assertEqual(C.talk_overlap(src, self.base)["2"], {"1": 20})

    def test_a_person_split_in_two_maps_to_one(self):
        src = [L(0, 10, "x", "1"), L(10, 20, "x", "2"), L(20, 30, "x", "4"), L(30, 40, "x", "3")]
        self.assertEqual(C.map_speakers(src, self.base), {"1": "1", "2": "2", "4": "1", "3": "3"})

    def test_timing_noise_does_not_decide(self):
        base = [L(0, 50, "x", "1"), L(50, 60, "x", "2")]
        src = [L(0, 20, "x", "1"), L(20, 51, "x", "2"), L(52, 60, "x", "3")]  # 2 overlaps base 2 by 1 s
        self.assertEqual(C.map_speakers(src, base), {"1": "1", "2": "1", "3": "2"})

    def test_mixed_speakers_are_matched_one_to_one(self):
        base = [L(0, 30, "x", "1"), L(30, 50, "x", "2")]
        src = [L(0, 26, "x", "2"), L(26, 50, "x", "1")]  # src 1 overlaps base 1 by 4 s and base 2 by 20 s
        self.assertEqual(C.map_speakers(src, base), {"2": "1", "1": "2"})

    def test_new_numbers_and_no_labels(self):
        src = [L(0, 10, "x", "1"), L(100, 110, "x", "2"), L(110, 120, "x", None)]
        self.assertEqual(C.map_speakers(src, self.base), {"1": "1", "2": "4"})
        self.assertEqual(C.map_speakers(src, [L(0, 10, "x", None)]), {"1": "1", "2": "2"})


class Combining(unittest.TestCase):
    base = [L(0, 4, "b0", "1"), L(5, 9, "b1", "2"), L(10, 14, "b2", "1"), L(15, 19, "b3", "2")]
    src = [L(0.1, 4.1, "s0", "2"), L(5.1, 9.1, "s1", "1"), L(10.1, 14.1, "s2", "2"), L(15.1, 19.1, "s3", "1")]
    third = [L(0, 19, "t0", "1")]

    def run_combine(self, picks, mapping=None):
        tracks = {"b": self.base, "s": self.src, "t": self.third}
        return [x["text"] for x in C.combine(tracks, "b", picks, mapping or {"s": {"1": "2", "2": "1"}})]

    def test_nothing_picked_is_the_base(self):
        self.assertEqual(self.run_combine([]), ["b0", "b1", "b2", "b3"])

    def test_by_rows(self):
        rows = C.align([self.base, self.src])
        picks = [{"start": rows[1]["from"], "end": rows[1]["to"], "from": "s"},
                 {"start": rows[3]["from"], "end": rows[3]["to"], "from": "s"}]
        self.assertEqual(self.run_combine(picks), ["b0", "s1", "b2", "s3"])
        lines = C.combine({"b": self.base, "s": self.src}, "b", picks, {"s": {"1": "2", "2": "1"}})
        self.assertEqual([x["speaker"] for x in lines], ["1", "2", "1", "2"], "mapped onto the base's speakers")

    def test_time_range_edges(self):
        # a line is in a range when its middle is, and a range holds its start but not its end.
        # The middles: s1 7.1, b2 12, s2 12.1
        self.assertEqual(self.run_combine([{"start": 7.1, "end": 12, "from": "s"}]), ["b0", "b1", "s1", "b2", "b3"])
        self.assertEqual(self.run_combine([{"start": 7.2, "end": 12.1, "from": "s"}]), ["b0", "b1", "b3"],
                         "b2 is inside, but s's line there isn't")

    def test_overlapping_picks_the_later_wins(self):
        picks = [{"start": 0, "end": 12, "from": "s"}, {"start": 6, "end": 19, "from": "t"}]
        self.assertEqual(self.run_combine(picks), ["t0", "s0"], "t0's middle (9.5) is in t's part")
        self.assertEqual(C.timeline(picks, "b"), [(0, 6, "s"), (6, 19, "t")])
        picks.append({"start": 1, "end": 3, "from": "b"})  # the base again, in the middle of s's part
        self.assertEqual(C.timeline(picks, "b"), [(0, 1, "s"), (3, 6, "s"), (6, 19, "t")])

    def test_gaps_between_picks_are_the_base(self):
        picks = [{"start": 0, "end": 4.6, "from": "s"}, {"start": 14.6, "end": 20, "from": "s"}]
        self.assertEqual(self.run_combine(picks), ["s0", "b1", "b2", "s3"])
        self.assertEqual(C.timeline([{"start": 0, "end": 5, "from": "s"}, {"start": 5, "end": 9, "from": "s"}], "b"),
                         [(0, 9, "s")], "neighbouring picks are one stretch")

    def test_nothing_where_the_picked_transcript_has_nothing(self):
        src = [L(0.1, 4.1, "s0", "1")]
        lines = C.combine({"b": self.base, "s": src}, "b", [{"start": 4.5, "end": 9.5, "from": "s"}], {})
        self.assertEqual([x["text"] for x in lines], ["b0", "b2", "b3"])


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="sedjem-compare-api-"))
        storage = cls.tmp / "data"
        storage.mkdir()
        for var in ("ELEVENLABS_API_KEY", "SPEECHMATICS_API_KEY"):
            os.environ.pop(var, None)
        cls.cfg = Config(storage=storage)
        cls.store = Store(storage)
        a440 = tone(cls.tmp / "a440.wav", 440, 25)
        a660 = tone(cls.tmp / "a660.wav", 660, 25)
        cls.base_lines = [L(0, 4, "تمام، نبدأ الـ meeting", "1"), L(5, 9, "الـ deadline يوم الخميس", "2"),
                          L(10, 14, "okay, I will send the report", "1"), L(15, 19, "شكرا يا جماعة", "2")]
        cls.other_lines = [L(0.1, 4.1, "تمام نبدا ال meeting", "2"), L(5.1, 9.1, "الديدلاين يوم الخميس", "1"),
                           L(10.1, 14.1, "okay I'll send the report", "2"), L(15.1, 19.1, "شكرا يا جماعه", "1")]
        cls.a = make_job(cls.store, 1, cls.base_lines, audio=a440, speaker_names={"1": "Mona", "2": "Omar"})
        cls.b = make_job(cls.store, 2, cls.other_lines, link=cls.a, rerun_of=cls.a, model="cohere",
                         model_title="Cohere Transcribe Arabic", speaker_names={"1": "Omar A."})
        cls.c = make_job(cls.store, 3, [L(0, 19, "x", None)], audio=a660)  # another recording
        cls.running = make_job(cls.store, 4, [], link=cls.a, rerun_of=cls.a)
        cls.server, cls.app = make_server(cls.cfg, port=0)
        cls.store.update(cls.running, status="running")  # after the start, which marks running jobs interrupted
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.a440, cls.a660 = a440, a660

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, method, path, body=None, headers=None):
        h = {"X-Sedjem": "1", **(headers or {})}
        data = None
        if body is not None:
            data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                payload = r.read()
                return r.status, (json.loads(payload) if "json" in r.headers.get("Content-Type", "") else payload), r.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}"), e.headers

    def test_1_groups_in_the_list_and_the_group_route(self):
        status, body, _ = self.call("GET", "/api/jobs")
        groups = {j["id"]: j["group"] for j in body["jobs"]}
        self.assertEqual({groups[self.a], groups[self.b], groups[self.running]}, {self.a})
        self.assertEqual(groups[self.c], self.c)
        status, body, _ = self.call("GET", f"/api/jobs/{self.b}/group")
        self.assertEqual(status, 200)
        self.assertEqual(body["group"], self.a)
        self.assertEqual([j["id"] for j in body["jobs"]], [self.a, self.b, self.running])
        self.assertEqual(self.call("GET", f"/api/jobs/{job_id(99)}/group")[0], 404)

    def test_2_prepare_stores_the_fingerprint(self):
        """The same file added twice ends up in one group; a re-run takes the fingerprint along."""
        runner = self.app.runner
        made = []
        for wav in (self.a660, self.a660, self.a440):
            job = runner.new_job(title="Tones", source_name=wav.name, model_id="elevenlabs",
                                 options={"speakers": "none", "language": "ar", "prompt": ""}, source_path=str(wav))
            runner.submit(job["id"])  # prepared for real; the run then fails at once (no API key, no upload)
            made.append(job["id"])
        end = time.time() + 30
        while time.time() < end and any(self.store.get(j)["status"] in ("preparing", "queued", "running") for j in made):
            time.sleep(0.1)
        jobs = [self.store.get(j) for j in made]
        self.assertTrue(all(j["fingerprint"] for j in jobs), jobs)
        self.assertEqual(jobs[0]["fingerprint"], jobs[1]["fingerprint"])
        _, body, _ = self.call("GET", "/api/jobs")
        groups = {j["id"]: j["group"] for j in body["jobs"]}
        self.assertEqual(groups[made[0]], groups[made[1]])
        self.assertEqual(groups[made[1]], groups[self.c], "the recording added earlier by hand, too")
        self.assertEqual(groups[made[2]], self.a)
        again = runner.rerun(made[0], "elevenlabs", {"speakers": "none", "language": "ar", "prompt": ""})
        self.assertEqual(again["fingerprint"], jobs[0]["fingerprint"])
        for jid in made + [again["id"]]:
            while self.store.get(jid)["status"] in ("preparing", "queued", "running"):
                time.sleep(0.1)
            self.store.delete(jid)

    def test_3_compare(self):
        status, body, _ = self.call("GET", f"/api/compare?ids={self.a},{self.b}")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["base"], self.a)
        self.assertEqual(len(body["rows"]), 4)
        self.assertEqual(body["mapping"][self.b], {"2": "1", "1": "2"})
        self.assertEqual(body["jobs"][0]["speaker_names"], {"1": "Mona", "2": "Omar"})
        self.assertEqual([s["id"] for s in body["jobs"][1]["speakers"]], ["1", "2"])
        row = body["rows"][1]
        self.assertFalse(row["same"])
        self.assertEqual(row["cells"][0][0]["spans"], [["الـ deadline", 2], ["يوم الخميس", 0]])
        self.assertEqual(row["cells"][1][0]["spans"], [["الديدلاين", 2], ["يوم الخميس", 0]])
        self.assertTrue(body["rows"][3]["same"], "only the spelling of ة differs")
        _, other, _ = self.call("GET", f"/api/compare?ids={self.a},{self.b}&base={self.b}")
        self.assertEqual(other["mapping"][self.a], {"1": "2", "2": "1"})

    def test_3_compare_refuses(self):
        self.assertEqual(self.call("GET", f"/api/compare?ids={self.a}")[0], 400)
        self.assertEqual(self.call("GET", f"/api/compare?ids={self.a},{self.a}")[0], 400)
        self.assertEqual(self.call("GET", f"/api/compare?ids={self.a},{self.c}")[0], 400, "different recordings")
        self.assertEqual(self.call("GET", f"/api/compare?ids={self.a},{job_id(99)}")[0], 404)
        self.assertEqual(self.call("GET", f"/api/compare?ids={self.a},{self.running}")[0], 409)
        self.assertEqual(self.call("GET", f"/api/compare?ids={self.a},{self.b}&base={self.c}")[0], 400)
        self.assertEqual(self.call("GET", "/api/compare?ids=../x,y")[0], 404)

    def pair(self, n):
        """A fresh base transcript and a re-run of it with another model, for a test that saves onto it."""
        a = make_job(self.store, n, self.base_lines, link=self.a, rerun_of=self.a, audio_s=25.0,
                     speaker_names={"1": "Mona", "2": "Omar"})
        b = make_job(self.store, n + 1, self.other_lines, link=self.a, rerun_of=self.a, model="cohere",
                     model_title="Cohere Transcribe Arabic", audio_s=25.0, speaker_names={"1": "Omar A."})
        return a, b

    def test_4_combine(self):
        a, b = self.pair(10)
        _, view, _ = self.call("GET", f"/api/compare?ids={a},{b}")
        rows = view["rows"]
        body = {"ids": [a, b], "base": a,
                "picks": [{"start": rows[2]["from"], "end": rows[2]["to"], "from": b},  # a row
                          {"start": 14.5, "end": 25, "from": b}],  # a time range
                "speakers": {b: {"1": "2", "2": "3"}}}  # the user says Cohere's 2 is someone new
        jobs_before = sorted(p.name for p in (self.cfg.storage / "jobs").iterdir())

        # reviewed first, like an edit: nothing is saved yet
        status, pre, _ = self.call("POST", "/api/combine/preview", body)
        self.assertEqual(status, 200, pre)
        self.assertEqual((pre["base"], pre["version"]), (a, 1), "v0 the model's output, v1 the names given before the history began")
        # named by its label in the recording (the second Cohere transcript of it) and the version it was at
        self.assertTrue(pre["summary"].startswith("With the text of Cohere Transcribe Arabic 2 (version 1) for 00:09–00:25: "),
                        pre["summary"])
        self.assertEqual([r["op"] for r in pre["changes"]["lines"]].count("same"), 2)
        self.assertEqual(self.call("GET", f"/api/jobs/{a}")[1]["lines"], self.base_lines)

        status, r, _ = self.call("POST", "/api/combine", {**body, "message": "Best of both"})
        self.assertEqual(status, 200, r)
        self.assertEqual((r["job"]["id"], r["version"], r["edited"]), (a, 2, True))
        self.assertEqual([x["text"] for x in r["lines"]], [self.base_lines[0]["text"], self.base_lines[1]["text"],
                                                           self.other_lines[2]["text"], self.other_lines[3]["text"]])
        self.assertEqual([x["speaker"] for x in r["lines"]], ["1", "2", "3", "2"])
        self.assertEqual(r["job"]["speaker_names"], {"1": "Mona", "2": "Omar"}, "the base's names; Cohere's 2 had none")
        self.assertEqual((r["job"]["title"], r["job"]["model"]), ("Weekly sync", "whisper-medium"), "the same transcript")
        self.assertEqual(sorted(p.name for p in (self.cfg.storage / "jobs").iterdir()), jobs_before, "no new job")
        self.assertEqual(self.call("GET", f"/api/jobs/{b}")[1]["lines"], self.other_lines, "the sources are left as they were")

        # a version in History like any other
        _, v, _ = self.call("GET", f"/api/jobs/{a}/versions")
        v1 = v["versions"][-1]
        self.assertEqual([x["n"] for x in v["versions"]], [0, 1, 2])
        self.assertEqual((v["versions"][0]["kind"], v1["kind"], v1["message"]), ("model output", "combine", "Best of both"))
        self.assertEqual(v1["summary"], pre["summary"])
        self.assertEqual(v1["combined"]["ranges"], [{"start": rows[2]["from"], "end": 25, "from": b, "version": 1}])
        self.assertEqual(v1["combined"]["sources"], [{"id": b, "model_title": "Cohere Transcribe Arabic",
                                                      "label": "Cohere Transcribe Arabic 2", "version": 1}])
        self.assertEqual(v1["combined"]["speakers"][b], {"1": "2", "2": "3"})
        _, v0, _ = self.call("GET", f"/api/jobs/{a}/versions/0")
        self.assertEqual(v0["lines"], self.base_lines, "version 0 is still the model's output")
        _, txt, _ = self.call("GET", f"/api/jobs/{a}/export/txt")
        self.assertIn("[00:00] Mona: تمام، نبدأ الـ meeting", txt.decode())
        self.assertIn("Speaker 3: okay I'll send the report", txt.decode())
        self.assertEqual(self.call("POST", "/api/combine", body)[0], 409, "the same again: nothing to save")

        # compared again with its new text, then undone by restoring version 0
        _, again, _ = self.call("GET", f"/api/compare?ids={a},{b}")
        self.assertTrue(all(row["same"] for row in again["rows"][2:]), "the picked rows now read the same")
        status, r, _ = self.call("POST", f"/api/jobs/{a}/versions/0/restore", {})
        self.assertEqual((status, r["version"], r["lines"]), (200, 3, self.base_lines))
        self.assertEqual(r["job"]["speaker_names"], {}, "as the model wrote it")

    def test_4_combine_names_new_speakers_after_their_source(self):
        a, b = self.pair(20)
        body = {"ids": [a, b], "base": a, "picks": [{"start": 0, "end": 25, "from": b}], "speakers": {b: {"1": "4"}}}
        status, r, _ = self.call("POST", "/api/combine", body)
        self.assertEqual(status, 200, r)
        self.assertEqual(self.store.get(a)["speaker_names"], {"1": "Mona", "2": "Omar", "4": "Omar A."})
        self.assertEqual(self.store.get(a)["title"], "Weekly sync")
        # with b as the base instead, b gets the new version and a is untouched
        c, d = self.pair(30)
        status, r, _ = self.call("POST", "/api/combine", {"ids": [c, d], "base": d, "picks": [{"start": 0, "end": 5, "from": c}]})
        self.assertEqual((status, r["job"]["id"], r["version"]), (200, d, 2))
        self.assertEqual(r["lines"][0]["text"], self.base_lines[0]["text"])
        self.assertEqual(r["lines"][0]["speaker"], "2", "a's speaker 1 is b's 2")

    def test_4_combine_refuses(self):
        a, b = self.pair(40)
        good = {"ids": [a, b], "base": a, "picks": [{"start": 0, "end": 5, "from": b}]}
        for bad in ({**good, "ids": [a]}, {**good, "ids": [a, self.c]}, {**good, "base": self.c},
                    {**good, "picks": []}, {**good, "picks": [{"start": 0, "end": 5, "from": a}]},
                    {**good, "picks": [{"start": 5, "end": 1, "from": b}]},
                    {**good, "picks": [{"start": 1, "end": 5, "from": self.c}]},
                    {**good, "picks": [{"start": "x", "end": 5, "from": b}]},
                    {**good, "picks": "all"}, {**good, "speakers": {b: {"1": "<b>"}}}, {**good, "speakers": [1]}, []):
            self.assertEqual(self.call("POST", "/api/combine", bad)[0], 400, bad)
            self.assertEqual(self.call("POST", "/api/combine/preview", bad)[0], 400, bad)
        self.assertEqual(self.call("POST", "/api/combine", {**good, "ids": [a, self.running]})[0], 409)
        self.assertEqual(self.call("POST", "/api/combine", good, headers={"X-Sedjem": ""})[0], 403)
        self.assertEqual(self.call("GET", f"/api/jobs/{a}")[1]["lines"], self.base_lines, "nothing was saved")
        self.assertEqual([x["n"] for x in self.call("GET", f"/api/jobs/{a}/versions")[1]["versions"]], [0, 1])


if __name__ == "__main__":
    unittest.main()
