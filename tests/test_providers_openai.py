"""Tests for the OpenAI, Groq and Mistral clients (app/hosted_openai.py).

The services are replaced by a local mock server that follows the documented request and reply
formats, so no audio leaves this computer and no keys are needed.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import base64
import contextlib
import email.parser
import email.policy
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import av
import numpy as np
import requests
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import engines, worker  # noqa: E402
from app import hosted_openai as H  # noqa: E402
from app.config import Config  # noqa: E402
from app.engines import Cancelled, EngineError  # noqa: E402
from app.server import make_server  # noqa: E402
from transcribe import STYLE_PROMPT  # noqa: E402

SR = 16000
KEYS = {"openai": "sk-test", "groq": "gsk-test", "mistral": "mi-test"}
# Stands in for app/worker.py --speaker-timeline (no voiceprint model here): prints its arguments, then
# a voice every 0.75 s, Speaker 1 until 25 s and Speaker 2 after. FAKE_VOICES=sleep or fail changes that.
FAKE_WORKER = """
import json, os, sys, time
print(json.dumps({"argv": sys.argv[1:]}))
if os.environ.get("FAKE_VOICES") == "sleep":
    time.sleep(30)
if os.environ.get("FAKE_VOICES") == "fail":
    sys.exit("could not load the voiceprint model")
centers = [round(0.75 * k, 2) for k in range(1, 67)]
print("voices: Speaker 1 talks 25 s, Speaker 2 talks 25 s")
print(json.dumps({"centers": centers, "labels": [1 if c < 25 else 2 for c in centers]}))
"""


def recording(seconds=50.0, burst=4.0, gap=1.0):
    """A stand-in for speech: bursts of a wavering tone with silent pauses between them."""
    t = np.arange(int(seconds * SR)) / SR
    x = 0.3 * np.sin(2 * np.pi * (180 + 40 * np.sin(2 * np.pi * 2 * t)) * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 5 * t))
    x[(t % (burst + gap)) >= burst] = 0.0
    return x.astype(np.float32)


def duration(data):
    """Length in seconds of an encoded file, by decoding it."""
    with av.open(io.BytesIO(data)) as c:
        s = c.streams.audio[0]
        n = sum(f.samples for f in c.decode(s))
        return n / s.codec_context.sample_rate


def container(data):
    for magic, name in ((b"\x1a\x45\xdf\xa3", "webm"), (b"OggS", "ogg"), (b"fLaC", "flac"), (b"ID3", "mp3")):
        if data.startswith(magic):
            return name
    return "mp3" if data[:2] in (b"\xff\xf3", b"\xff\xf2", b"\xff\xfb") else None


class Splitting(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="sedjem-split-"))
        cls.audio = cls.tmp / "audio.flac"
        sf.write(cls.audio, recording(), SR, subtype="PCM_16", format="FLAC")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_spans_cover_the_recording_and_cut_in_pauses(self):
        power, seconds = H.loudness(self.audio)
        self.assertAlmostEqual(seconds, 50.0)
        for most, window in ((25, 15), (20, 5), (11.25, 2.8)):
            spans = H.spans(power, seconds, most, window)
            self.assertEqual(spans[0][0], 0.0)
            self.assertEqual(spans[-1][1], 50.0)
            for (a, b), (c, _) in zip(spans, spans[1:]):
                self.assertEqual(b, c, "the parts are contiguous")
                self.assertLessEqual(b - a, most)
                self.assertGreaterEqual(b - a, most - window - 0.02)
                self.assertGreaterEqual(b % 5.0, 4.0, f"cut at {b} s is not in a pause")
        self.assertEqual(H.spans(power, seconds, 60, 10), [(0.0, 50.0)])

    def test_silence_is_not_sent(self):
        power, _ = H.loudness(self.audio)
        self.assertTrue(H.silent(power, 4.1, 4.9))
        self.assertFalse(H.silent(power, 3.5, 4.5))

    def test_part_length_from_size_and_duration_limits(self):
        model = {"max_part_mb": 24, "max_part_s": 1200}
        self.assertEqual(H.part_seconds(model, self.audio, "webm", 32), 1200)
        self.assertAlmostEqual(H.part_seconds({"max_part_mb": 24, "max_part_s": 9999}, self.audio, "webm", 32), 5400)
        flac = H.part_seconds({"max_part_mb": 0.5, "max_part_s": 9999}, self.audio, "flac", 0)
        rate = self.audio.stat().st_size / 50.0  # bytes per second of this recording
        self.assertGreater(flac, 10, "not the 10 s floor")
        self.assertLess(flac * rate * 1.3, 0.5e6, "a FLAC part is planned with a margin")

    def test_encoding(self):
        expect = {"webm": "webm", "ogg": "ogg", "mp3": "mp3", "flac": "flac"}
        for fmt, kind in expect.items():
            dest = self.tmp / f"part.{fmt}"
            H.encode(self.audio, 12.5, 27.5, fmt, 32, dest)
            data = dest.read_bytes()
            self.assertEqual(container(data), kind, fmt)
            self.assertAlmostEqual(duration(data), 15.0, delta=0.08, msg=fmt)
            if fmt != "flac":  # Opus's variable bitrate runs over by about 10% on this steady tone
                self.assertLess(len(data), 15 * 32000 / 8 * 1.25, f"{fmt} is about 32 kbps")
        # the samples of a FLAC part are the recording's own
        part, _ = sf.read(self.tmp / "part.flac", dtype="int16")
        whole, _ = sf.read(self.audio, dtype="int16")
        np.testing.assert_array_equal(part, whole[int(12.5 * SR):int(27.5 * SR)])


class Replies(unittest.TestCase):
    def test_diarized_segments_get_offsets_and_numbers_across_parts(self):
        v = H.Voices()
        part1 = {"segments": [{"id": "seg_0", "speaker": "A", "start": 0.5, "end": 6.0, "text": " تمام",
                               "type": "transcript.text.segment"},
                              {"id": "seg_1", "speaker": "B", "start": 6.5, "end": 9.0, "text": "okay"}]}
        lines = H.segment_lines(part1, 0, 100.0, 120.0, v, {})
        self.assertEqual([(x["start"], x["end"], x["speaker"], x["text"]) for x in lines],
                         [(100.5, 106.0, "1", "تمام"), (106.5, 109.0, "2", "okay")])
        refs = v.references()
        self.assertEqual([r[:2] for r in refs], [("Speaker 1", "1")], "B's 2.5 s turn is too short for a clip")
        self.assertAlmostEqual(refs[0][3] - refs[0][2], 5.0)
        part2 = {"segments": [{"speaker": "Speaker 1", "start": 1, "end": 2, "text": "x"},
                              {"speaker": "A", "start": 3, "end": 4, "text": "y"}]}
        lines = H.segment_lines(part2, 1, 120.0, 140.0, v, {"Speaker 1": "1"})
        self.assertEqual([x["speaker"] for x in lines], ["1", "3"], "part 2's A is a new person")

    def test_whisper_segments(self):
        reply = {"task": "transcribe", "language": "arabic", "duration": 20.0, "text": "...",
                 "segments": [{"id": 0, "seek": 0, "start": 0.0, "end": 4.2, "text": " نبدأ ال meeting",
                               "avg_logprob": -0.3, "compression_ratio": 1.2, "no_speech_prob": 0.02},
                              {"id": 1, "seek": 0, "start": 15.0, "end": 19.9, "text": " شكرا لكم",
                               "avg_logprob": -1.4, "compression_ratio": 1.0, "no_speech_prob": 0.9},
                              {"id": 2, "start": 18.0, "end": 25.0, "text": "tail"}]}
        lines = H.segment_lines(reply, 3, 60.0, 80.0)
        self.assertEqual([(x["start"], x["end"], x["speaker"], x["text"]) for x in lines],
                         [(60.0, 64.2, None, "نبدأ ال meeting"), (78.0, 80.0, None, "tail")])
        self.assertEqual(H.detected_language([{"reply": reply}]), "arabic")

    def test_text_only_reply(self):
        reply = {"text": " خلصنا ال deploy", "languages": [{"code": "ar"}, {"code": "en"}],
                 "usage": {"type": "duration", "seconds": 25}}
        self.assertEqual(H.segment_lines(reply, 0, 25.0, 48.5),
                         [{"start": 25.0, "end": 48.5, "speaker": None, "text": "خلصنا ال deploy"}])
        self.assertEqual(H.text_lines({"text": "  "}, 0, 0, 1), [])
        self.assertEqual(H.detected_language([{"reply": {"text": ""}}, {"reply": reply}]), "ar, en")

    def test_whisper_prompt(self):
        fields = dict(H.whisper_prompt({"prompt": "GitHub, Jira\nsprint", "language": "ar"}))
        self.assertEqual(fields["prompt"], f"{STYLE_PROMPT} GitHub, Jira, sprint")
        self.assertEqual(H.whisper_prompt({"prompt": "", "language": "auto"}), [("prompt", STYLE_PROMPT)])
        self.assertEqual(H.whisper_prompt({"prompt": "GitHub", "language": "en"}), [("prompt", "GitHub")])
        self.assertEqual(H.whisper_prompt({"prompt": "", "language": "en"}), [])
        long = dict(H.whisper_prompt({"prompt": ", ".join(f"term{i:03d}" for i in range(100))}))["prompt"]
        self.assertLess(len(long) - len(STYLE_PROMPT), 205, "terms are cut to fit Whisper's 224 tokens")

    def test_retry_rules(self):
        def reply(status, **headers):
            r = requests.Response()
            r.status_code, r.headers = status, requests.structures.CaseInsensitiveDict(headers)
            return r
        self.assertEqual(H.retry_delay(reply(429, **{"Retry-After": "7"}), 0), 7.0)
        self.assertIsNone(H.retry_delay(reply(429), 0), "a used-up quota has no Retry-After")
        self.assertIsNone(H.retry_delay(reply(429, **{"Retry-After": "3600"}), 0))
        self.assertEqual(H.retry_delay(reply(503), 0), 2)
        self.assertIsNone(H.retry_delay(reply(400), 0))

    def test_mistral_error_message(self):
        r = requests.Response()
        r.status_code, r.headers = 401, requests.structures.CaseInsensitiveDict({"Content-Type": "application/json"})
        r._content = b'{"message": "Unauthorized", "request_id": "r1"}'
        with self.assertRaises(EngineError) as e:
            H.check(r, "Mistral")
        self.assertIn("HTTP 401", str(e.exception))
        self.assertIn("the API key was rejected", str(e.exception))
        self.assertIn("Unauthorized", str(e.exception))
        r._content = b'{"object": "error", "message": {"detail": [{"loc": ["body", "model"], "msg": "Field required"}]}}'
        r.status_code = 422
        with self.assertRaises(EngineError) as e:
            H.check(r, "Mistral")
        self.assertIn("Field required", str(e.exception))


class MockApi(BaseHTTPRequestHandler):
    """OpenAI, Groq and Mistral stand-ins: they check the key, record each request (form fields and
    the uploaded file) and answer in the documented formats."""
    calls = []
    script = []                 # (status, headers, body) to answer the next requests with
    hold = threading.Event()    # while clear, requests wait before they are answered
    hold.set()

    def log_message(self, *a):
        pass

    def fields(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
            b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body)
        out, files = {}, {}
        for part in msg.iter_parts():
            name = part.get_param("name", header="content-disposition")
            value = part.get_payload(decode=True)
            if part.get_filename():
                files[name] = (part.get_filename(), part.get_content_type(), value)
            else:
                out.setdefault(name, []).append(value.decode())
        return out, files

    def send(self, status, obj, headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        except ConnectionError:  # the client gave up (a cancelled upload)
            pass

    def do_POST(self):
        service = self.path.split("/")[1]
        if not self.path.endswith("/audio/transcriptions") or service not in KEYS:
            return self.send(404, {"error": {"message": "not found"}})
        fields, files = self.fields()
        if "file" not in files:  # an upload cut off by a cancel
            return self.send(400, {"error": {"message": "no file"}})
        if self.headers.get("Authorization") != f"Bearer {KEYS[service]}":
            if service == "mistral":
                return self.send(401, {"message": "Unauthorized", "request_id": "req_1"})
            return self.send(401, {"error": {"message": "Incorrect API key provided", "type": "invalid_request_error",
                                             "param": None, "code": "invalid_api_key"}})
        name, ctype, data = files["file"]
        self.calls.append({"service": service, "fields": fields, "name": name, "type": ctype, "file": data,
                           "seconds": duration(data)})
        self.hold.wait(30)
        if self.script:
            return self.send(*self.script.pop(0))
        self.send(200, getattr(self, service)(fields, self.calls[-1]["seconds"]))

    def openai(self, fields, d):
        model = fields["model"][0]
        if model == "gpt-4o-transcribe-diarize":
            names = fields.get("known_speaker_names[]", [])
            a, b = (names[0], "A") if names else ("A", "B")
            segs = [{"id": "seg_0", "speaker": a, "start": 0.0, "end": round(0.45 * d, 2), "text": f"turn {a}",
                     "type": "transcript.text.segment"},
                    {"id": "seg_1", "speaker": b, "start": round(0.5 * d, 2), "end": round(0.95 * d, 2),
                     "text": f"turn {b}", "type": "transcript.text.segment"}]
            return {"task": "transcribe", "duration": d, "text": " ".join(s["text"] for s in segs), "segments": segs,
                    "usage": {"type": "tokens", "input_tokens": 10, "output_tokens": 5, "total_tokens": 15}}
        return {"text": f"piece of {d:.1f} s", "languages": [{"code": "ar"}, {"code": "en"}],
                "usage": {"type": "duration", "seconds": round(d)}}

    def groq(self, fields, d):
        segs = [{"id": 0, "seek": 0, "start": 0.0, "end": round(0.4 * d, 2), "text": " أول جملة", "tokens": [1],
                 "temperature": 0.0, "avg_logprob": -0.2, "compression_ratio": 1.1, "no_speech_prob": 0.01},
                {"id": 1, "seek": 0, "start": round(0.5 * d, 2), "end": round(0.8 * d, 2), "text": " تاني جملة",
                 "tokens": [2], "temperature": 0.0, "avg_logprob": -0.3, "compression_ratio": 1.2, "no_speech_prob": 0.02},
                {"id": 2, "seek": 0, "start": round(0.8 * d, 2), "end": d, "text": " شكرا لكم", "tokens": [3],
                 "temperature": 0.0, "avg_logprob": -1.5, "compression_ratio": 0.9, "no_speech_prob": 0.93}]
        return {"task": "transcribe", "language": "Arabic", "duration": d, "text": "...", "segments": segs,
                "x_groq": {"id": "req_01"}}

    def mistral(self, fields, d):
        diarize = fields.get("diarize") == ["true"]
        segs = [{"text": " هنعمل merge_request النهارده", "start": 0.4, "end": 5.0,
                 "speaker_id": "speaker_1" if diarize else None, "type": "transcription_segment"},
                {"text": " تمام، على Jira", "start": 5.5, "end": 9.0,
                 "speaker_id": "speaker_2" if diarize else None, "type": "transcription_segment"}]
        return {"model": fields["model"][0], "text": "...", "language": "ar", "segments": segs,
                "usage": {"prompt_audio_seconds": round(d), "prompt_tokens": 4, "total_tokens": 90,
                          "completion_tokens": 86}}


class Flow(unittest.TestCase):
    """The whole path through the app's API: upload confirmed, parts sent to the mock, lines joined."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="sedjem-providers-"))
        cls.mock = ThreadingHTTPServer(("127.0.0.1", 0), MockApi)
        threading.Thread(target=cls.mock.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{cls.mock.server_address[1]}"
        storage = cls.tmp / "data"
        storage.mkdir()
        (cls.tmp / "voiceprints.onnx").write_bytes(b"stand-in")  # so speaker labels count as ready
        cls.fake = cls.tmp / "fake_worker.py"
        cls.fake.write_text(FAKE_WORKER, encoding="utf-8")
        (storage / "config.toml").write_text(  # the speaker model in use: the stand-in, as TitaNet-small
            f'[local]\nspeaker_models = [{{ id = "titanet-small", title = "TitaNet-small", '
            f'path = "{(cls.tmp / "voiceprints.onnx").as_posix()}" }}]\n\n'
            f'[[models]]\nid = "openai"\nbase_url = "{url}/openai/v1"\nmax_part_s = 20\n\n'
            f'[[models]]\nid = "groq"\nbase_url = "{url}/groq/openai/v1"\nmax_part_mb = 0.05\n\n'
            f'[[models]]\nid = "mistral"\nbase_url = "{url}/mistral/v1"\n')
        for var in ("OPENAI_API_KEY", "GROQ_API_KEY", "MISTRAL_API_KEY"):
            os.environ.pop(var, None)
        cls.no_gpu_probe = mock.patch.object(engines, "gpu_info", return_value={"devices": []})
        cls.no_gpu_probe.start()  # the app looks for a GPU at start, in a model process; not needed here
        cls.cfg = Config(storage=storage)
        cls.server, cls.app = make_server(cls.cfg, port=0)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.wav = cls.tmp / "meeting.wav"
        sf.write(cls.wav, recording(), SR)
        for model, key in KEYS.items():
            cls.call("POST", "/api/keys", {"model": model, "key": key})

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.mock.shutdown()
        cls.mock.server_close()
        cls.no_gpu_probe.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        MockApi.calls.clear()
        MockApi.script.clear()
        MockApi.hold.set()

    @classmethod
    def call(cls, method, path, body=None):
        req = urllib.request.Request(cls.base + path, data=None if body is None else json.dumps(body).encode(),
                                     method=method, headers={"X-Sedjem": "1", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def start(self, model, speakers="none", prompt="", language="ar"):
        status, job = self.call("POST", "/api/jobs", {"path": str(self.wav), "model": model, "speakers": speakers,
                                                      "language": language, "prompt": prompt, "confirm_upload": True})
        self.assertEqual(status, 201, job)
        return job["id"]

    def wait(self, jid, until=("done", "failed", "cancelled"), timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            _, r = self.call("GET", f"/api/jobs/{jid}")
            if r["job"]["status"] in until:
                return r
            time.sleep(0.1)
        self.fail(f"job {jid} is still {r['job']['status']}")

    def test_1_status_and_confirmation(self):
        _, st = self.call("GET", "/api/status")
        models = {m["id"]: m for m in st["models"]}
        for mid in KEYS:
            self.assertEqual(models[mid]["kind"], "hosted")
            self.assertTrue(models[mid]["ready"])
            self.assertTrue(models[mid]["privacy"] and models[mid]["prompt_hint"] and models[mid]["speakers_hint"])
        status, body = self.call("POST", "/api/jobs", {"path": str(self.wav), "model": "openai"})
        self.assertEqual(status, 400, "a hosted model needs the upload confirmed")
        self.assertIn("OpenAI", body["error"])

    def test_2_openai_text_pieces(self):
        r = self.wait(self.start("openai", prompt="GitHub, Jira"))
        self.assertEqual(r["job"]["status"], "done", r)
        calls = MockApi.calls
        self.assertGreaterEqual(len(calls), 2, "each stretch of up to 25 s is its own request")
        for c in calls:
            f = c["fields"]
            self.assertEqual(f["model"], ["gpt-transcribe"])
            self.assertEqual(f["response_format"], ["json"])
            self.assertEqual(f["languages[]"], ["ar", "en"])
            self.assertEqual(f["keywords[]"], ["GitHub", "Jira"])
            self.assertNotIn("language", f)
            self.assertNotIn("prompt", f)
            self.assertEqual((container(c["file"]), c["type"]), ("webm", "audio/webm"))
            self.assertTrue(c["name"].endswith(".webm"), "OpenAI reads the format from the file name")
            self.assertLessEqual(c["seconds"], 25.1)
        lines = r["lines"]
        self.assertEqual(len(lines), len(calls))
        self.assertEqual(lines[0]["start"], 0.0)
        self.assertAlmostEqual(lines[-1]["end"], 50.0, places=1)
        for x, y, c in zip(lines, lines[1:], calls):
            self.assertEqual(x["end"], y["start"], "each line is one stretch, back to back")
            self.assertAlmostEqual(x["end"] - x["start"], c["seconds"], delta=0.08)
            self.assertEqual(x["text"], f"piece of {c['seconds']:.1f} s")
        self.assertEqual(r["job"]["detected_language"], "ar, en")
        self.assertEqual(r["job"]["api_model"], "gpt-transcribe")

    def test_3_openai_speaker_labels_carry_across_parts(self):
        r = self.wait(self.start("openai", speakers="auto", prompt="GitHub"))
        self.assertEqual(r["job"]["status"], "done", r)
        calls = MockApi.calls
        self.assertEqual(len(calls), 3, "a 50 s recording in parts of at most 20 s")
        for c in calls:
            f = c["fields"]
            self.assertEqual(f["model"], ["gpt-4o-transcribe-diarize"])
            self.assertEqual(f["response_format"], ["diarized_json"])
            self.assertEqual(f["chunking_strategy"], ["auto"])
            self.assertEqual(f["language"], ["ar"])
            self.assertNotIn("prompt", f, "the diarize model takes no prompt")
            self.assertLessEqual(c["seconds"], 20.05)
        self.assertNotIn("known_speaker_names[]", calls[0]["fields"])
        second = calls[1]["fields"]
        self.assertEqual(second["known_speaker_names[]"], ["Speaker 1", "Speaker 2"])
        for ref in second["known_speaker_references[]"]:
            head, b64 = ref.split(",", 1)
            self.assertEqual(head, "data:audio/webm;base64")
            self.assertTrue(3.0 <= duration(base64.b64decode(b64)) <= 8.1, "clips of 2 to 10 s")
        self.assertEqual(calls[2]["fields"]["known_speaker_names[]"], ["Speaker 1", "Speaker 2", "Speaker 3"])
        self.assertEqual([x["speaker"] for x in r["lines"]], ["1", "2", "1", "3", "1", "4"])
        starts = np.cumsum([0] + [c["seconds"] for c in calls])
        for k in range(3):  # each part's times start where the part does
            self.assertAlmostEqual(r["lines"][2 * k]["start"], starts[k], delta=0.08)
            self.assertAlmostEqual(r["lines"][2 * k + 1]["start"], starts[k] + round(0.5 * calls[k]["seconds"], 2),
                                   delta=0.08)

    def test_4_groq_parts_by_size(self):
        r = self.wait(self.start("groq", prompt="GitHub, Jira"))
        self.assertEqual(r["job"]["status"], "done", r)
        calls = MockApi.calls
        self.assertGreaterEqual(len(calls), 4, "a 50 KB limit makes parts of about 10 s")
        for c in calls:
            f = c["fields"]
            self.assertEqual(f["model"], ["whisper-large-v3"])
            self.assertEqual(f["response_format"], ["verbose_json"])
            self.assertEqual(f["timestamp_granularities[]"], ["segment"])
            self.assertEqual(f["language"], ["ar"])
            self.assertEqual(f["prompt"], [f"{STYLE_PROMPT} GitHub, Jira"])
            self.assertLessEqual(len(c["file"]), 50_000)
            self.assertEqual(container(c["file"]), "webm")
        lines = r["lines"]
        self.assertEqual(len(lines), 2 * len(calls), "the silent third segment of each part is dropped")
        self.assertNotIn("شكرا لكم", " ".join(x["text"] for x in lines))
        starts = np.cumsum([0] + [c["seconds"] for c in calls])
        for k, c in enumerate(calls):
            self.assertAlmostEqual(lines[2 * k]["start"], starts[k], delta=0.1)
            self.assertAlmostEqual(lines[2 * k + 1]["end"], starts[k] + round(0.8 * c["seconds"], 2), delta=0.1)
        self.assertTrue(all(x["speaker"] is None for x in lines))
        self.assertEqual(r["job"]["detected_language"], "Arabic")

    def test_5_mistral_segments_and_speakers(self):
        r = self.wait(self.start("mistral", speakers="3", prompt="merge request, Jira"))
        self.assertEqual(r["job"]["status"], "done", r)
        self.assertEqual(len(MockApi.calls), 1, "Voxtral takes up to 58 min in one request here")
        f = MockApi.calls[0]["fields"]
        self.assertEqual(f["model"], ["voxtral-mini-2602"])
        self.assertEqual(f["timestamp_granularities"], ["segment"])
        self.assertEqual(f["diarize"], ["true"])
        self.assertEqual(f["context_bias"], ["merge_request", "Jira"])
        self.assertNotIn("language", f, "timestamps can't be combined with a language")
        self.assertEqual([(x["start"], x["end"], x["speaker"], x["text"]) for x in r["lines"]],
                         [(0.4, 5.0, "1", "هنعمل merge request النهارده"), (5.5, 9.0, "2", "تمام، على Jira")])
        r = self.wait(self.start("mistral"))
        self.assertNotIn("diarize", MockApi.calls[-1]["fields"])
        self.assertEqual({x["speaker"] for x in r["lines"]}, {None})

    def test_6_errors(self):
        self.call("POST", "/api/keys", {"model": "mistral", "key": "wrong"})
        r = self.wait(self.start("mistral"))
        self.call("POST", "/api/keys", {"model": "mistral", "key": KEYS["mistral"]})
        self.assertEqual(r["job"]["status"], "failed")
        self.assertIn("Mistral: HTTP 401", r["job"]["error"])
        self.assertIn("the API key was rejected; Unauthorized", r["job"]["error"])

        MockApi.script[:] = [(413, {"error": {"message": "Request Entity Too Large", "type": "invalid_request_error",
                                              "code": "request_too_large"}})]
        r = self.wait(self.start("groq"))
        self.assertEqual(r["job"]["status"], "failed")
        self.assertIn("Groq: HTTP 413", r["job"]["error"])
        self.assertIn("too large", r["job"]["error"])
        self.assertTrue(r["lines"] == [] and r["partial"])

        MockApi.calls.clear()
        MockApi.script[:] = [(429, {"error": {"message": "You exceeded your current quota", "type": "insufficient_quota",
                                              "code": "insufficient_quota"}})]
        r = self.wait(self.start("openai", speakers="2"))
        self.assertEqual(r["job"]["status"], "failed")
        self.assertIn("OpenAI: HTTP 429", r["job"]["error"])
        self.assertIn("You exceeded your current quota", r["job"]["error"])
        self.assertEqual(len(MockApi.calls), 1, "a used-up quota is not tried again")

        MockApi.calls.clear()
        MockApi.script[:] = [(429, {"error": {"message": "Rate limit reached"}}, {"Retry-After": "0"}),
                             (503, {"error": {"message": "overloaded", "code": "server_is_overloaded"}})]
        with mock.patch.object(H, "pause", lambda seconds, cancelled: None):
            r = self.wait(self.start("openai", speakers="2"))
        self.assertEqual(r["job"]["status"], "done", r)
        self.assertEqual(len(MockApi.calls), 5, "three parts, two of the tries answered with 429 and 503")

        MockApi.calls.clear()
        MockApi.script[:] = [(429, {"error": {"message": "Rate limit reached"}}, {"Retry-After": "0"})] * 6
        r = self.wait(self.start("openai", speakers="2"))
        self.assertEqual(r["job"]["status"], "failed")
        self.assertIn("Rate limit reached", r["job"]["error"])
        self.assertEqual(len(MockApi.calls), 5, "five tries in all")

    def test_7_cancel_between_parts(self):
        MockApi.hold.clear()
        jid = self.start("groq")
        end = time.time() + 20
        while not MockApi.calls and time.time() < end:
            time.sleep(0.05)
        self.assertEqual(self.call("GET", f"/api/jobs/{jid}")[1]["job"]["stage"], "transcribing")
        self.call("POST", f"/api/jobs/{jid}/cancel")
        MockApi.hold.set()
        r = self.wait(jid)
        self.assertEqual(r["job"]["status"], "cancelled")
        self.assertEqual(len(MockApi.calls), 1, "no part is sent after a cancel")
        self.assertFalse(list((self.cfg.storage / "jobs" / jid).glob("upload.*")), "the upload file is removed")

    def test_8_groq_speaker_labels_from_voiceprints(self):
        with mock.patch.object(engines, "worker_command", lambda cfg: [sys.executable, str(self.fake)]):
            r = self.wait(self.start("groq", speakers="2"))
        self.assertEqual(r["job"]["status"], "done", r)
        lines = r["lines"]
        self.assertEqual({x["speaker"] for x in lines if x["end"] < 24.5}, {"1"})
        self.assertEqual({x["speaker"] for x in lines if x["start"] > 25.5}, {"2"})
        self.assertEqual(r["job"]["voiceprint_model"], "voiceprints.onnx")
        folder = self.cfg.storage / "jobs" / r["job"]["id"]
        argv = json.loads((folder / "log.txt").read_text(encoding="utf-8").splitlines()[0])["argv"]
        self.assertEqual(argv[:2], ["--speaker-timeline", str(folder / "audio.flac")])
        self.assertEqual(argv[argv.index("--speakers") + 1], "2")
        self.assertEqual(Path(argv[argv.index("--voiceprint-model") + 1]), self.tmp / "voiceprints.onnx")


class VoiceStep(unittest.TestCase):
    """Speaker labels from the speaker model on this computer, for the models that give none."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="sedjem-voices-"))
        sf.write(cls.tmp / "audio.flac", recording(10.0), SR, subtype="PCM_16", format="FLAC")
        cls.fake = cls.tmp / "fake_worker.py"
        cls.fake.write_text(FAKE_WORKER, encoding="utf-8")
        cls.cfg = Config(home=cls.tmp)  # with a stand-in for TitaNet-small, the speaker model chosen at first
        cls.titanet = cls.tmp / "models" / "diarization" / "nemo_en_titanet_small.onnx"
        cls.titanet.parent.mkdir(parents=True)
        cls.titanet.write_bytes(b"stand-in")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_worker_mode_prints_the_timeline(self):
        fake = types.SimpleNamespace(centers=np.array([0.75, 1.5, 2.25]), labels=np.array([1, 2, 2]))
        out = io.StringIO()
        with mock.patch("speakers.diarize", return_value=fake) as diarize, contextlib.redirect_stdout(out):
            code = worker.speaker_timeline([str(self.tmp / "audio.flac"), "--speakers", "0",
                                            "--voiceprint-model", "x.onnx", "--threads", "2"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue()), {"centers": [0.75, 1.5, 2.25], "labels": [1, 2, 2]})
        audio, n, threads, model = diarize.call_args[0]
        self.assertEqual((audio.dtype, len(audio), n, threads, model), (np.float32, 10 * SR, None, 2, "x.onnx"))

    def test_the_speaker_heard_most(self):
        t = H.Timeline([1, 2, 3, 4, 5, 6], [1, 1, 2, 2, 2, 1])
        self.assertEqual(t.speaker(0.5, 3.5), "1")
        self.assertEqual(t.speaker(2.5, 5.5), "2")
        self.assertEqual(t.speaker(3.4, 3.45), "2", "no window inside the line: the nearest one")
        self.assertIsNone(H.Timeline([], []).speaker(0, 1))

    def step(self, behaviour, cancel_after=None):
        t0 = time.time()
        cancelled = (lambda: time.time() - t0 > cancel_after) if cancel_after else (lambda: False)
        progress = []
        with mock.patch.object(engines, "worker_command", lambda cfg: [sys.executable, str(self.fake)]), \
                mock.patch.dict(os.environ, {"FAKE_VOICES": behaviour}):
            return H.voice_timeline(self.cfg, self.tmp, {"speakers": 3}, progress.append, cancelled), progress

    def test_voice_step_runs_in_the_model_process(self):
        (self.tmp / "log.txt").unlink(missing_ok=True)
        timeline, progress = self.step("")
        self.assertEqual(progress, [{"stage": "speakers"}])
        self.assertEqual((timeline.speaker(0, 10), timeline.speaker(30, 40)), ("1", "2"))
        argv = json.loads((self.tmp / "log.txt").read_text(encoding="utf-8").splitlines()[0])["argv"]
        self.assertEqual(argv, ["--speaker-timeline", str(self.tmp / "audio.flac"), "--speakers", "3",
                                "--voiceprint-model", str(self.titanet), "--threads", "4"])
        self.assertEqual(timeline.model, "nemo_en_titanet_small.onnx")
        self.assertEqual(H.result([], [], "whisper-large-v3", timeline)["meta"]["voiceprint_model"], "nemo_en_titanet_small.onnx")
        self.assertIsNone(H.voice_timeline(self.cfg, self.tmp, {"speakers": "none"}, print, lambda: False))

    def test_voice_step_with_the_speaker_model_chosen(self):
        """Nemotron-3-Diarization chosen in Settings: the step gets its folder, and at most 8 speakers."""
        folder = self.tmp / "models" / "diarization" / "Nemotron-3-Diarization"
        folder.mkdir(parents=True, exist_ok=True)
        self.addCleanup(self.cfg.save_settings, speaker_model="titanet-small")
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        for name in ("config.json", "processor_config.json", "model.safetensors"):
            (folder / name).write_bytes(b"{}")
        self.cfg.save_settings(speaker_model="nemotron-3-diarization")
        (self.tmp / "log.txt").unlink(missing_ok=True)
        with mock.patch.object(engines, "worker_command", lambda cfg: [sys.executable, str(self.fake)]):
            timeline = H.voice_timeline(self.cfg, self.tmp, {"speakers": 12}, lambda p: None, lambda: False)
        argv = json.loads((self.tmp / "log.txt").read_text(encoding="utf-8").splitlines()[0])["argv"]
        self.assertEqual(argv[2:6], ["--speakers", "8", "--voiceprint-model", str(folder)])
        self.assertEqual(H.result([], [], "whisper-large-v3", timeline)["meta"]["voiceprint_model"], "Nemotron-3-Diarization")

    def test_voice_step_failure_and_cancel(self):
        with self.assertRaises(EngineError) as e:
            self.step("fail")
        self.assertIn("could not load the voiceprint model", str(e.exception))
        t0 = time.time()
        with self.assertRaises(Cancelled):
            self.step("sleep", cancel_after=0.5)
        self.assertLess(time.time() - t0, 10, "a cancel stops the model process")


class Direct(unittest.TestCase):
    """The request helper and the size check, without the app around them."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="sedjem-direct-"))
        cls.mock = ThreadingHTTPServer(("127.0.0.1", 0), MockApi)
        threading.Thread(target=cls.mock.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.mock.server_address[1]}"
        sf.write(cls.tmp / "audio.flac", recording(20.0), SR, subtype="PCM_16", format="FLAC")

    @classmethod
    def tearDownClass(cls):
        cls.mock.shutdown()
        cls.mock.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        MockApi.calls.clear()
        MockApi.script.clear()
        MockApi.hold.set()

    def test_cancel_during_the_upload(self):
        H.encode(self.tmp / "audio.flac", 0, 20, "webm", 32, self.tmp / "up.webm")
        with requests.Session() as s, self.assertRaises(Cancelled):
            H.post(s, f"{self.url}/groq/openai/v1/audio/transcriptions", {"Authorization": "Bearer gsk-test"},
                   [("model", "whisper-large-v3")], self.tmp / "up.webm", "audio/webm", lambda *a: None,
                   lambda: True, "Groq")
        self.assertEqual(MockApi.calls, [])

    def test_a_part_larger_than_planned_is_cut_again(self):
        class Cfg:
            def api_key(self, model):
                return KEYS["groq"]
        model = {"service": "Groq", "base_url": f"{self.url}/groq/openai/v1", "upload_format": "flac",
                 "max_part_mb": 0.25}
        progress = []
        with mock.patch.object(H, "part_seconds", lambda *a: 3600):  # plan one part; FLAC turns out larger
            lines, replies = H.run(Cfg(), model, self.tmp, progress.append, lambda: False,
                                   lambda *a: [("model", "whisper-large-v3")], H.segment_lines)
        sizes = [len(c["file"]) for c in MockApi.calls]
        self.assertGreaterEqual(len(sizes), 2)
        self.assertTrue(all(n <= 250_000 for n in sizes), sizes)
        self.assertTrue(all(container(c["file"]) == "flac" for c in MockApi.calls))
        self.assertAlmostEqual(sum(c["seconds"] for c in MockApi.calls), 20.0, delta=0.01)
        self.assertEqual([x["start"] for x in replies], sorted(x["start"] for x in replies))
        self.assertTrue(any(p.get("stage") == "transcribing" for p in progress))
        self.assertEqual(json.loads((self.tmp / "engine" / "lines.json").read_text(encoding="utf-8")), lines,
                         "the lines so far are where the app shows a running job's transcript")

    def test_a_reply_at_the_output_limit_is_sent_again_in_halves(self):
        class Cfg:
            def api_key(self, model):
                return KEYS["openai"]
        model = {"service": "OpenAI", "base_url": f"{self.url}/openai/v1", "max_output_tokens": 2000}
        cut_short = {"task": "transcribe", "duration": 20.0, "text": "cut short", "segments": [
            {"id": "seg_0", "speaker": "A", "start": 0.0, "end": 5.0, "text": "cut short"}],
            "usage": {"type": "tokens", "input_tokens": 300, "output_tokens": 2000, "total_tokens": 2300}}
        MockApi.script[:] = [(200, cut_short)]
        fields = [("model", "gpt-4o-transcribe-diarize"), ("response_format", "diarized_json")]
        lines, replies = H.run(Cfg(), model, self.tmp, lambda p: None, lambda: False, lambda *a: fields,
                               lambda r, i, a, b: H.segment_lines(r, i, a, b, H.Voices(), {}))
        seconds = [c["seconds"] for c in MockApi.calls]
        self.assertEqual(len(seconds), 3)
        self.assertAlmostEqual(seconds[0], 20.0, delta=0.05)
        self.assertTrue(all(5 < s < 15 for s in seconds[1:]), seconds)
        self.assertAlmostEqual(sum(seconds[1:]), 20.0, delta=0.05)
        self.assertNotIn("cut short", [x["text"] for x in lines], "the reply that hit the limit is dropped")
        self.assertEqual(len(replies), 2)
        self.assertEqual((replies[0]["start"], replies[-1]["end"]), (0.0, 20.0))


if __name__ == "__main__":
    unittest.main()
