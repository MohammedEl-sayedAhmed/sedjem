"""Tests for the Gemini, Deepgram, AssemblyAI and Azure Speech clients (app/hosted_more.py).

Each service is replaced by a local stand-in that checks the documented request and answers in
the documented response format, so no audio leaves the computer and no API key is needed.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import base64
import email.parser
import email.policy
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import engines, hosted_more as H  # noqa: E402
from app.config import Config  # noqa: E402
from app.server import make_server  # noqa: E402

SR = 16000
KEYS = ("GEMINI_API_KEY", "DEEPGRAM_API_KEY", "ASSEMBLYAI_API_KEY", "AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION")


def write_audio(path, on=1.2, off=0.8, repeats=4):
    """Tone bursts with silent pauses between them, as 16 kHz FLAC like the app's audio.flac."""
    tone = (0.3 * np.sin(2 * np.pi * 300 * np.arange(int(SR * on)) / SR)).astype(np.float32)
    signal = np.concatenate([np.concatenate([tone, np.zeros(int(SR * off), np.float32)]) for _ in range(repeats)])
    sf.write(str(path), signal, SR, format="FLAC", subtype="PCM_16")
    return signal


def wait_until(condition, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.02)
    return False


def in_thread(fn):
    box = {}

    def run():
        try:
            box["result"] = fn()
        except BaseException as e:
            box["error"] = e
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, box


def gemini_reply(body):
    """An interaction as the transcribe model returns it (word_info annotations), or a general
    model's JSON segments."""
    config = (body.get("generation_config") or {}).get("transcription_config")
    if config is None:
        segments = {"segments": [{"start": 0.1, "end": 1.0, "speaker": "A", "text": "تمام deploy؟"},
                                 {"start": 1.4, "end": 1.7, "speaker": "B", "text": "yes"}]}
        content = [{"type": "text", "text": json.dumps(segments, ensure_ascii=False)}]
    else:
        diarized = "diarization_mode" in config["mode"]
        words = [("تمام", "spk_1", "0.100s", "0.500s"), ("deploy", "spk_1", "0.600s", "1.000s"),
                 ("؟", "spk_1", "1.000s", "1.000s"), ("yes", "spk_2", "1.400s", "1.700s")]
        annotations = [{"type": "word_info", "text": t, "start_offset": a, "end_offset": b, **({"speaker": s} if diarized else {})}
                       for t, s, a, b in words]
        content = [{"type": "text", "text": "تمام deploy؟ yes", "annotations": annotations}]
    return {"id": "interactions/abc123", "status": "completed",
            "steps": [{"id": "step_001", "type": "model_output", "content": content}]}


def deepgram_reply(diarized):
    who = (lambda n: {"speaker": n, "speaker_confidence": 0.8}) if diarized else (lambda n: {})
    words = [[{"word": "تمام", "start": 0.1, "end": 0.5, "confidence": 0.9, "punctuated_word": "تمام،", **who(0)},
              {"word": "deploy", "start": 0.6, "end": 1.0, "confidence": 0.9, "punctuated_word": "deploy.", **who(0)}],
             [{"word": "yes", "start": 1.4, "end": 1.7, "confidence": 0.9, "punctuated_word": "Yes.", **who(1)}]]
    return {"metadata": {"request_id": "r1", "duration": 8.0, "channels": 1},
            "results": {"channels": [{"alternatives": [{"transcript": "تمام، deploy. Yes.", "confidence": 0.9,
                                                        "words": words[0] + words[1]}]}],
                        "utterances": [{"start": w[0]["start"], "end": w[-1]["end"], "confidence": 0.9, "channel": 0,
                                        "transcript": " ".join(x["punctuated_word"] for x in w), "words": w, "id": f"u{i}",
                                        **({"speaker": i} if diarized else {})} for i, w in enumerate(words)]}}


def assemblyai_reply(labels):
    words = [{"text": "تمام", "start": 100, "end": 500, "confidence": 0.9, "speaker": "A" if labels else None},
             {"text": "deploy.", "start": 600, "end": 1000, "confidence": 0.9, "speaker": "A" if labels else None},
             {"text": "Yes.", "start": 1400, "end": 1700, "confidence": 0.9, "speaker": "B" if labels else None}]
    utterances = [{"speaker": "A", "text": "تمام deploy.", "start": 100, "end": 1000, "confidence": 0.9, "words": words[:2]},
                  {"speaker": "B", "text": "Yes.", "start": 1400, "end": 1700, "confidence": 0.9, "words": words[2:]}]
    return {"id": "aai1", "status": "completed", "language_code": "ar", "text": "تمام deploy. Yes.",
            "speech_model_used": "universal-3-5-pro", "words": words, "utterances": utterances if labels else None}


def azure_reply(diarized):
    phrases = [{"channel": 0, "offsetMilliseconds": 100, "durationMilliseconds": 900, "text": "تمام deploy.",
                "words": [{"text": "تمام", "offsetMilliseconds": 100, "durationMilliseconds": 400},
                          {"text": "deploy.", "offsetMilliseconds": 600, "durationMilliseconds": 400}],
                "locale": "ar-EG", "confidence": 0.9, **({"speaker": 1} if diarized else {})},
               {"channel": 0, "offsetMilliseconds": 1400, "durationMilliseconds": 300, "text": "Yes.",
                "words": [{"text": "Yes.", "offsetMilliseconds": 1400, "durationMilliseconds": 300}],
                "locale": "ar-EG", "confidence": 0.8, **({"speaker": 0} if diarized else {})}]
    return {"durationMilliseconds": 8000, "combinedPhrases": [{"text": "تمام deploy. Yes."}], "phrases": phrases}


class Mock(BaseHTTPRequestHandler):
    """Stand-ins for the four APIs. calls records what each received; fail makes paths that start
    with a prefix answer with an error; while go is clear the synchronous requests wait."""
    calls, fail, go = [], {}, threading.Event()
    aai = {"refuse_delete": False, "polls": 0}

    def log_message(self, *a):
        pass

    def send(self, status, obj, headers=None):
        data = json.dumps(obj, ensure_ascii=False).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):  # a cancelled client that went away
            pass

    def base(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def request_parts(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        self.aborted = len(raw) < n  # the client stopped sending (a cancelled upload): ignore the request
        path, _, query = self.path.partition("?")
        return raw, path, urllib.parse.parse_qs(query, keep_blank_values=True)

    def form(self, raw):
        msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
            b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + raw)
        return {p.get_param("name", header="content-disposition"): p.get_payload(decode=True) for p in msg.iter_parts()}

    def failed(self):
        for prefix, (status, obj) in self.fail.items():
            if self.path.startswith(prefix):
                self.send(status, obj)
                return True
        return False

    def do_POST(self):
        raw, path, q = self.request_parts()
        if self.aborted or self.failed():
            return
        if path == "/upload/v1beta/files" and "upload_id" not in q:  # Gemini: start a resumable upload
            if self.headers.get("x-goog-api-key") != "gm-key":
                return self.send(401, {"error": {"code": "authentication", "message": "API key not valid."}})
            self.calls.append(("gm-start", {k: self.headers.get(k) for k in (
                "X-Goog-Upload-Protocol", "X-Goog-Upload-Command", "X-Goog-Upload-Header-Content-Length",
                "X-Goog-Upload-Header-Content-Type")}, json.loads(raw)))
            n = sum(1 for c in self.calls if c[0] == "gm-start")
            return self.send(200, {}, {"X-Goog-Upload-URL": f"{self.base()}/upload/v1beta/files?upload_id=u{n}&upload_protocol=resumable"})
        if path == "/upload/v1beta/files":  # Gemini: the bytes
            self.calls.append(("gm-upload", self.headers.get("X-Goog-Upload-Command"), self.headers.get("X-Goog-Upload-Offset"),
                               raw[:4], len(raw), self.headers.get("x-goog-api-key")))
            name = f"files/f{q['upload_id'][0][1:]}"
            return self.send(200, {"file": {"name": name, "displayName": "part.flac", "mimeType": "audio/flac",
                                            "sizeBytes": str(len(raw)), "uri": f"{self.base()}/v1beta/{name}", "state": "PROCESSING"}})
        if path == "/v1beta/interactions":
            if self.headers.get("x-goog-api-key") != "gm-key":
                return self.send(401, {"error": {"code": "authentication", "message": "API key not valid."}})
            body = json.loads(raw)
            self.calls.append(("gm-interaction", body))
            self.go.wait(10)
            return self.send(200, gemini_reply(body))
        if path == "/v1/listen":  # Deepgram
            if self.headers.get("Authorization") != "Token dg-key":
                return self.send(401, {"err_code": "INVALID_AUTH", "err_msg": "Invalid credentials.", "request_id": "r0"})
            self.calls.append(("dg", q, self.headers.get("Content-Type"), raw[:4]))
            self.go.wait(10)
            return self.send(200, deepgram_reply("diarize_model" in q))
        if path == "/v2/upload":  # AssemblyAI
            if self.headers.get("authorization") != "aai-key":
                return self.send(401, {"error": "Authentication error, API token missing/invalid"})
            self.calls.append(("aai-upload", self.headers.get("Content-Type"), raw[:4]))
            return self.send(200, {"upload_url": "https://cdn.assemblyai.com/upload/f1"})
        if path == "/v2/transcript":
            body = json.loads(raw)
            self.calls.append(("aai-create", body))
            self.aai["labels"] = bool(body.get("speaker_labels"))
            return self.send(200, {"id": "aai1", "status": "queued", "audio_url": body["audio_url"]})
        if path == "/speechtotext/transcriptions:transcribe":  # Azure
            if self.headers.get("Ocp-Apim-Subscription-Key") != "az-key":
                return self.send(401, {"error": {"code": "401", "message": "Access denied due to invalid subscription key or wrong API endpoint."}})
            fields = self.form(raw)
            definition = json.loads(fields["definition"])
            self.calls.append(("az", q, definition, fields["audio"][:4]))
            self.go.wait(10)
            return self.send(200, azure_reply("diarization" in definition))
        self.send(404, {})

    def do_GET(self):
        raw, path, q = self.request_parts()
        if self.failed():
            return
        if path.startswith("/v1beta/files/"):
            self.calls.append(("gm-get", path))
            return self.send(200, {"name": path.removeprefix("/v1beta/"), "uri": self.base() + path, "state": "ACTIVE"})
        if path == "/v2/transcript/aai1":
            self.aai["polls"] += 1
            if not self.go.is_set() or self.aai["polls"] < 2:
                return self.send(200, {"id": "aai1", "status": "processing"})
            return self.send(200, assemblyai_reply(self.aai.get("labels")))
        self.send(404, {})

    def do_DELETE(self):
        raw, path, q = self.request_parts()
        if path == "/v2/transcript/aai1" and self.aai["refuse_delete"] and not self.go.is_set():
            self.calls.append(("delete-refused", path))
            return self.send(400, {"error": "Transcript is still processing"})
        self.calls.append(("delete", path))
        self.send(200, {"id": "aai1", "status": "completed"} if path.startswith("/v2/") else {})


class FakeCfg:
    def __init__(self, region=""):
        self.region_value = region

    def api_key(self, model):
        return model["key"]

    def region(self, model):
        return self.region_value


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="sedjem-providers-"))
        cls.mock = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
        threading.Thread(target=cls.mock.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.mock.server_address[1]}"
        for var in KEYS:
            os.environ.pop(var, None)
        (cls.tmp / "data").mkdir()
        cls.config = Config(storage=cls.tmp / "data")

    @classmethod
    def tearDownClass(cls):
        Mock.go.set()
        cls.mock.shutdown()
        cls.mock.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        Mock.calls.clear()
        Mock.fail = {}
        Mock.go.set()
        Mock.aai.update(refuse_delete=False, polls=0)
        self.job_dir = Path(tempfile.mkdtemp(dir=self.tmp))
        self.signal = write_audio(self.job_dir / "audio.flac")
        self.states = []

    def model(self, mid, **extra):
        keys = {"gemini": "gm-key", "deepgram": "dg-key", "assemblyai": "aai-key", "azure": "az-key"}
        return {**self.config.models[mid], "base_url": self.url, "key": keys[mid], "poll_s": 0.02, **extra}

    def run_client(self, fn, model, speakers="auto", language="ar", prompt="", cancelled=lambda: False):
        job = {"options": {"speakers": speakers, "language": language, "prompt": prompt}}
        return fn(FakeCfg(), model, self.job_dir, job, self.states.append, cancelled)

    def calls(self, kind):
        return [c for c in Mock.calls if c[0] == kind]


class Splitting(Base):
    def test_cuts_fall_in_pauses(self):
        pieces = H.split_at_pauses(self.job_dir / "audio.flac", 3.0, self.job_dir / "parts", lambda: False, window_s=1.5)
        self.assertEqual([round(o, 2) for o, _ in pieces], [0.0, 1.5, 3.4, 5.4])
        total = 0
        for offset, path in pieces:
            info = sf.info(str(path))
            self.assertLessEqual(info.duration, 3.0)
            self.assertAlmostEqual(offset, total / SR, places=3)
            total += info.frames
            cut = int(offset * SR)
            self.assertLess(np.abs(self.signal[max(0, cut - 800):cut + 800]).max() if cut else 0, 1e-3, "cut inside a pause")
        self.assertEqual(total, len(self.signal), "no audio lost or repeated")

    def test_short_recording_is_one_piece(self):
        pieces = H.split_at_pauses(self.job_dir / "audio.flac", 60, self.job_dir / "parts", lambda: False)
        self.assertEqual(pieces, [(0.0, self.job_dir / "audio.flac")])
        self.assertFalse((self.job_dir / "parts").exists())

    def test_cancel(self):
        with self.assertRaises(engines.Cancelled):
            H.split_at_pauses(self.job_dir / "audio.flac", 3.0, self.job_dir / "parts", lambda: True)


class Parsers(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(H.seconds("12.340s"), 12.34)
        self.assertEqual(H.seconds(3), 3.0)
        self.assertEqual(H.seconds(None, 7.0), 7.0)

    def test_parts_keep_their_own_speakers(self):
        reply = gemini_reply({"generation_config": {"transcription_config": {"mode": {"diarization_mode": "speaker"}}}})
        lines = H.parts_lines([(0.0, reply), (10.0, reply)], H.gemini_tokens)
        self.assertEqual([(x["speaker"], x["text"], x["start"]) for x in lines],
                         [("1", "تمام deploy؟", 0.1), ("2", "yes", 1.4), ("3", "تمام deploy؟", 10.1), ("4", "yes", 11.4)])
        self.assertEqual({x["speaker"] for x in H.parts_lines([(0.0, reply)], H.gemini_tokens, speakers=False)}, {None})

    def test_gemini_segments_and_plain_text(self):
        lines = H.parts_lines([(5.0, gemini_reply({}))], H.gemini_tokens)
        self.assertEqual([(x["speaker"], x["text"], x["start"]) for x in lines], [("1", "تمام deploy؟", 5.1), ("2", "yes", 6.4)])
        plain = {"steps": [{"type": "model_output", "content": [{"type": "text", "text": "just text"}]}]}
        self.assertEqual(H.parts_lines([(0.0, plain)], H.gemini_tokens)[0]["text"], "just text")

    def test_deepgram(self):
        lines = H.deepgram_lines(deepgram_reply(True))
        self.assertEqual([(x["speaker"], x["text"]) for x in lines], [("1", "تمام، deploy."), ("2", "Yes.")])
        self.assertEqual([x["speaker"] for x in H.deepgram_lines(deepgram_reply(False))], [None])

    def test_assemblyai_times_in_ms(self):
        lines = H.assemblyai_lines(assemblyai_reply(True))
        self.assertEqual([(x["speaker"], x["text"], x["start"], x["end"]) for x in lines],
                         [("1", "تمام deploy.", 0.1, 1.0), ("2", "Yes.", 1.4, 1.7)])
        self.assertEqual([x["speaker"] for x in H.assemblyai_lines(assemblyai_reply(False))], [None])

    def test_azure_phrases(self):
        lines = H.parts_lines([(0.0, azure_reply(True))], H.azure_tokens)
        self.assertEqual([(x["speaker"], x["text"], x["end"]) for x in lines], [("1", "تمام deploy.", 1.0), ("2", "Yes.", 1.7)])


class Clients(Base):
    def test_gemini_parts_through_the_files_api(self):
        model = self.model("gemini", max_minutes=3.0 / 60, inline_limit_mb=0)
        result = self.run_client(H.gemini, model)
        interactions = [c[1] for c in self.calls("gm-interaction")]
        parts = len(interactions)
        self.assertGreater(parts, 1, "an 8 s recording with a 3 s limit goes in parts")
        start = self.calls("gm-start")[0]
        self.assertEqual(start[1]["X-Goog-Upload-Protocol"], "resumable")
        self.assertEqual(start[1]["X-Goog-Upload-Command"], "start")
        self.assertEqual(start[1]["X-Goog-Upload-Header-Content-Type"], "audio/flac")
        self.assertEqual(start[2], {"file": {"display_name": "part1.flac"}})
        upload = self.calls("gm-upload")[0]
        self.assertEqual(upload[1:4], ("upload, finalize", "0", b"fLaC"))
        self.assertEqual(int(start[1]["X-Goog-Upload-Header-Content-Length"]), upload[4])
        self.assertIsNone(upload[5], "the key isn't sent to the upload address")
        self.assertEqual(len(self.calls("gm-get")), parts, "waited for PROCESSING files to be ACTIVE")
        body = interactions[0]
        self.assertEqual(body["model"], "gemini-3.5-transcribe")
        self.assertIs(body["store"], False)
        self.assertEqual(body["input"], [{"type": "audio", "mime_type": "audio/flac", "uri": f"{self.url}/v1beta/files/f1"}])
        self.assertEqual(body["generation_config"]["transcription_config"], {
            "language_codes": [],
            "mode": {"type": "verbatim", "timestamp_granularities": ["word"], "diarization_mode": "speaker"}})
        self.assertEqual(sorted(c[1] for c in self.calls("delete")), sorted(f"/v1beta/files/f{i + 1}" for i in range(parts)))
        lines = result["lines"]
        self.assertEqual(len(lines), 2 * parts)
        self.assertEqual(len({x["speaker"] for x in lines}), 2 * parts, "speaker numbers restart in every part")
        offsets = [x["offset"] for x in json.loads((self.job_dir / "hosted.json").read_text(encoding="utf-8"))]
        self.assertEqual([x["start"] for x in lines[::2]], [round(o + 0.1, 2) for o in offsets])
        self.assertFalse((self.job_dir / "parts").exists(), "the pieces are removed")
        self.assertIn({"stage": "transcribing", "done": 0, "total": parts}, self.states)

    def test_gemini_inline_general_model(self):
        model = self.model("gemini", api_model="gemini-3.8-flash", prompt=True)
        result = self.run_client(H.gemini, model, speakers="3", prompt="Jira, GitHub")
        self.assertFalse(self.calls("gm-start"), "small enough to send inline")
        body = self.calls("gm-interaction")[0][1]
        text, audio = body["input"]
        self.assertIn("Egyptian Arabic", text["text"])
        self.assertIn("Latin script", text["text"])
        self.assertIn("there are 3 of them", text["text"])
        self.assertIn("Jira, GitHub", text["text"])
        self.assertTrue(base64.b64decode(audio["data"]).startswith(b"fLaC"))
        self.assertEqual(body["response_format"]["mime_type"], "application/json")
        self.assertEqual(body["response_format"]["schema"]["required"], ["segments"])
        self.assertNotIn("generation_config", body)
        self.assertEqual([(x["speaker"], x["text"]) for x in result["lines"]], [("1", "تمام deploy؟"), ("2", "yes")])
        self.assertIn({"stage": "remote", "service": "Google"}, self.states)

    def test_gemini_no_labels_english(self):
        result = self.run_client(H.gemini, self.model("gemini"), speakers="none", language="en")
        config = self.calls("gm-interaction")[0][1]["generation_config"]["transcription_config"]
        self.assertEqual(config, {"language_codes": ["en-US"], "mode": {"type": "verbatim", "timestamp_granularities": ["word"]}})
        self.assertEqual({x["speaker"] for x in result["lines"]}, {None})

    def test_gemini_cancel_deletes_the_upload(self):
        Mock.go.clear()
        cancel = threading.Event()
        thread, box = in_thread(lambda: self.run_client(H.gemini, self.model("gemini", inline_limit_mb=0), cancelled=cancel.is_set))
        self.assertTrue(wait_until(lambda: self.calls("gm-interaction")))
        cancel.set()
        thread.join(5)
        self.assertIsInstance(box.get("error"), engines.Cancelled)
        self.assertIn(("delete", "/v1beta/files/f1"), Mock.calls)

    def test_deepgram(self):
        model = self.model("deepgram")
        result = self.run_client(H.deepgram, model, prompt="GitHub, merge request")
        _, q, ctype, head = self.calls("dg")[0]
        self.assertEqual({k: v for k, v in q.items() if k != "keyterm"}, {
            "model": ["nova-3"], "language": ["ar-EG"], "smart_format": ["true"], "utterances": ["true"],
            "mip_opt_out": ["true"], "diarize_model": ["latest"]})
        self.assertEqual(q["keyterm"], ["GitHub", "merge request"])
        self.assertEqual((ctype, head), ("audio/flac", b"fLaC"))
        self.assertEqual([(x["speaker"], x["text"]) for x in result["lines"]], [("1", "تمام، deploy."), ("2", "Yes.")])
        self.assertIn({"stage": "remote", "service": "Deepgram"}, self.states)

    def test_deepgram_no_labels_and_auto(self):
        result = self.run_client(H.deepgram, self.model("deepgram"), speakers="none", language="auto")
        q = self.calls("dg")[0][1]
        self.assertNotIn("diarize_model", q)
        self.assertNotIn("diarize", q)
        self.assertEqual(q["language"], ["ar-EG"], "Deepgram can't detect Arabic, so auto uses the Arabic code")
        self.assertEqual({x["speaker"] for x in result["lines"]}, {None})

    def test_assemblyai(self):
        model = self.model("assemblyai")
        result = self.run_client(H.assemblyai, model, speakers="2", prompt="GitHub, a phrase of far more than six words here")
        self.assertEqual(self.calls("aai-upload")[0][1:], ("application/octet-stream", b"fLaC"))
        self.assertEqual(self.calls("aai-create")[0][1], {
            "audio_url": "https://cdn.assemblyai.com/upload/f1", "speech_models": ["universal-3-5-pro", "universal-2"],
            "language_detection": True, "language_detection_options": {"expected_languages": ["ar", "en"]},
            "speaker_labels": True, "speakers_expected": 2, "keyterms_prompt": ["GitHub"]})
        self.assertGreaterEqual(Mock.aai["polls"], 2, "polled until completed")
        self.assertIn(("delete", "/v2/transcript/aai1"), Mock.calls)
        self.assertEqual([(x["speaker"], x["text"], x["start"]) for x in result["lines"]],
                         [("1", "تمام deploy.", 0.1), ("2", "Yes.", 1.4)])
        self.assertEqual(result["meta"], {"detected_language": "ar"})
        self.assertEqual((self.job_dir / "hosted-job.txt").read_text(encoding="utf-8"), "aai1")

    def test_assemblyai_english_without_labels(self):
        self.run_client(H.assemblyai, self.model("assemblyai", prompt=False), speakers="none", language="en", prompt="x")
        self.assertEqual(self.calls("aai-create")[0][1], {
            "audio_url": "https://cdn.assemblyai.com/upload/f1", "speech_models": ["universal-3-5-pro", "universal-2"],
            "language_code": "en"})

    def test_assemblyai_cancel_deletes_once_it_can(self):
        Mock.go.clear()
        Mock.aai["refuse_delete"] = True
        cancel = threading.Event()
        model = self.model("assemblyai", delete_retry_s=0.2)  # 40 tries: 8 s to see the job finish
        thread, box = in_thread(lambda: self.run_client(H.assemblyai, model, cancelled=cancel.is_set))
        self.assertTrue(wait_until(lambda: Mock.aai["polls"] >= 1))
        cancel.set()
        thread.join(5)
        self.assertIsInstance(box.get("error"), engines.Cancelled)
        self.assertTrue(wait_until(lambda: self.calls("delete-refused")), "tried to delete at once")
        Mock.go.set()  # the service finishes the job; now it can be deleted
        self.assertTrue(wait_until(lambda: ("delete", "/v2/transcript/aai1") in Mock.calls), "deleted later")

    def test_azure(self):
        model = self.model("azure", endpoint=self.url)
        result = self.run_client(H.azure, model, speakers="3")
        _, q, definition, head = self.calls("az")[0]
        self.assertEqual(q, {"api-version": ["2025-10-15"]})
        self.assertEqual(definition, {"locales": ["ar-EG"], "profanityFilterMode": "None",
                                      "diarization": {"enabled": True, "maxSpeakers": 3}})
        self.assertEqual(head, b"fLaC")
        self.assertEqual([(x["speaker"], x["text"]) for x in result["lines"]], [("1", "تمام deploy."), ("2", "Yes.")])

    def test_azure_parts_and_options(self):
        model = self.model("azure", endpoint=self.url, max_minutes=3.0 / 60, prompt=True)
        result = self.run_client(H.azure, model, speakers="auto", language="auto", prompt="Jira, GitHub")
        calls = self.calls("az")
        self.assertGreater(len(calls), 1)
        self.assertEqual(calls[0][2], {"locales": ["ar-EG", "en-US"], "profanityFilterMode": "None",
                                       "diarization": {"enabled": True, "maxSpeakers": 10},
                                       "phraseList": {"phrases": ["Jira", "GitHub"]}})
        offsets = [x["offset"] for x in json.loads((self.job_dir / "hosted.json").read_text(encoding="utf-8"))]
        self.assertEqual([x["start"] for x in result["lines"][::2]], [round(o + 0.1, 2) for o in offsets])
        self.assertEqual(self.run_client(H.azure, model, speakers="1")["lines"][0]["speaker"], "1")
        self.assertEqual(self.calls("az")[-1][2]["diarization"]["maxSpeakers"], 2, "maxSpeakers is at least 2")

    def test_azure_endpoint_from_the_region(self):
        model = {**self.config.models["azure"], "key": "az-key"}
        self.assertEqual(H.azure_endpoint(FakeCfg("westeurope"), model), "https://westeurope.api.cognitive.microsoft.com")
        with self.assertRaises(engines.EngineError):
            H.azure_endpoint(FakeCfg("evil.example.com/x"), model)

    def test_synchronous_requests_cancel(self):
        for kind, fn, model in (("dg", H.deepgram, self.model("deepgram")),
                                ("az", H.azure, self.model("azure", endpoint=self.url))):
            Mock.go.clear()
            cancel = threading.Event()
            thread, box = in_thread(lambda: self.run_client(fn, model, cancelled=cancel.is_set))
            self.assertTrue(wait_until(lambda: self.calls(kind)))
            t0 = time.time()
            cancel.set()
            thread.join(5)
            self.assertIsInstance(box.get("error"), engines.Cancelled, kind)
            self.assertLess(time.time() - t0, 3, "noticed within about half a second")
            Mock.go.set()

    def test_cancel_during_upload(self):
        with self.assertRaises(engines.Cancelled):
            self.run_client(H.deepgram, self.model("deepgram"), cancelled=lambda: True)

    def test_errors(self):
        cases = [("gemini", H.gemini, "/v1beta/interactions",
                  lambda s: {"error": {"code": {401: "authentication", 413: "invalid_request", 429: "rate_limit_exceeded"}[s],
                                       "message": f"gemini says {s}"}}),
                 ("deepgram", H.deepgram, "/v1/listen", lambda s: {"err_code": "X", "err_msg": f"deepgram says {s}", "request_id": "r"}),
                 ("assemblyai", H.assemblyai, "/v2/upload", lambda s: {"error": f"assemblyai says {s}"}),
                 ("azure", H.azure, "/speechtotext/", lambda s: {"code": "InvalidRequest", "message": f"azure says {s}"})]
        hints = {401: "the API key was rejected", 413: "the file is too large", 429: "too many requests"}
        for mid, fn, prefix, body in cases:
            for status, hint in hints.items():
                Mock.fail = {prefix: (status, body(status))}
                model = self.model(mid, endpoint=self.url) if mid == "azure" else self.model(mid)
                with self.assertRaises(engines.EngineError, msg=f"{mid} {status}") as e:
                    self.run_client(fn, model)
                self.assertIn(f"HTTP {status}", str(e.exception))
                self.assertIn(hint, str(e.exception))
                self.assertIn(f"{mid} says {status}", str(e.exception))
        Mock.fail = {}
        with self.assertRaises(engines.EngineError) as e:  # the stand-in's own answer to a wrong key
            self.run_client(H.deepgram, self.model("deepgram", key="wrong"))
        self.assertIn("the API key was rejected; Invalid credentials.", str(e.exception))


class ThroughTheApp(Base):
    """The app's HTTP API with these models: keys and the Azure region, confirmation, cancel."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        storage = cls.tmp / "app"
        storage.mkdir()
        (storage / "config.toml").write_text(
            f'[[models]]\nid = "deepgram"\nbase_url = "{cls.url}"\n\n'
            f'[[models]]\nid = "assemblyai"\nbase_url = "{cls.url}"\npoll_s = 0.02\ndelete_retry_s = 0.05\n\n'
            f'[[models]]\nid = "azure"\nendpoint = "{cls.url}"\n', encoding="utf-8")
        cls.cfg = Config(storage=storage)
        cls.server, cls.app = make_server(cls.cfg, port=0)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.api = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.wav = cls.tmp / "speech.wav"
        write_audio(cls.tmp / "speech.flac")
        data, sr = sf.read(str(cls.tmp / "speech.flac"))
        sf.write(str(cls.wav), data, sr)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        super().tearDownClass()

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.api + path, method=method, headers={"X-Sedjem": "1", "Content-Type": "application/json"},
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def wait_job(self, jid, until=("done", "failed", "cancelled")):
        end = time.time() + 30
        while time.time() < end:
            job = self.call("GET", f"/api/jobs/{jid}")[1]
            if job["job"]["status"] in until:
                return job
            time.sleep(0.05)
        self.fail(f"job {jid} is still {job['job']['status']}")

    def test_status_shows_the_new_models(self):
        models = {m["id"]: m for m in self.call("GET", "/api/status")[1]["models"]}
        for mid in ("gemini", "deepgram", "assemblyai", "azure"):
            self.assertEqual(models[mid]["kind"], "hosted")
            self.assertTrue(models[mid]["privacy"] and models[mid]["speakers_hint"] and models[mid]["prompt_hint"])
        self.assertEqual(models["azure"]["region"], "westeurope", "the config's default")
        self.assertIsNone(models["deepgram"]["region"])

    def test_azure_key_and_region(self):
        status, info = self.call("POST", "/api/keys", {"model": "azure", "key": "az-key", "region": "NorthEurope"})
        self.assertEqual((status, info["key_source"], info["region"]), (200, "app", "northeurope"))
        status, info = self.call("POST", "/api/keys", {"model": "azure", "region": "westus2"})
        self.assertEqual((info["key_source"], info["region"]), ("app", "westus2"), "a region alone keeps the key")
        self.assertEqual(self.call("POST", "/api/keys", {"model": "azure", "region": "evil.example.com/"})[0], 400)
        status, info = self.call("POST", "/api/keys", {"model": "azure", "key": ""})
        self.assertEqual((info["key_source"], info["region"]), (None, "westus2"), "removing the key keeps the region")
        status, info = self.call("POST", "/api/keys", {"model": "azure", "key": "az-key", "region": ""})
        self.assertEqual(info["region"], "westeurope", "an empty region goes back to the config's")
        os.environ["AZURE_SPEECH_REGION"] = "uksouth"
        try:
            self.assertEqual(self.cfg.region(self.cfg.models["azure"]), "uksouth", "the environment wins")
        finally:
            os.environ.pop("AZURE_SPEECH_REGION")
        self.assertEqual(self.call("POST", "/api/keys", {"model": "deepgram", "key": "dg-key"})[1]["key_source"], "app",
                         "other key rows work as before")

    def test_a_job_needs_confirmation_then_runs(self):
        self.call("POST", "/api/keys", {"model": "deepgram", "key": "dg-key"})
        request = {"path": str(self.wav), "model": "deepgram", "speakers": "auto", "language": "ar"}
        self.assertEqual(self.call("POST", "/api/jobs", request)[0], 400, "a hosted model needs the upload confirmed")
        status, job = self.call("POST", "/api/jobs", {**request, "confirm_upload": True})
        self.assertEqual(status, 201, job)
        r = self.wait_job(job["id"])
        self.assertEqual(r["job"]["status"], "done", r)
        self.assertEqual([x["speaker"] for x in r["lines"]], ["1", "2"])
        self.assertEqual(self.calls("dg")[-1][3], b"fLaC", "the 16 kHz FLAC is uploaded")

    def test_cancel_removes_the_remote_transcript(self):
        self.call("POST", "/api/keys", {"model": "assemblyai", "key": "aai-key"})
        Mock.go.clear()
        status, job = self.call("POST", "/api/jobs", {"path": str(self.wav), "model": "assemblyai", "speakers": "none",
                                                      "confirm_upload": True})
        self.assertEqual(status, 201, job)
        self.assertTrue(wait_until(lambda: self.call("GET", f"/api/jobs/{job['id']}")[1]["job"].get("stage") == "remote"))
        self.call("POST", f"/api/jobs/{job['id']}/cancel")
        self.assertEqual(self.wait_job(job["id"])["job"]["status"], "cancelled")
        self.assertIn(("delete", "/v2/transcript/aai1"), Mock.calls)


if __name__ == "__main__":
    unittest.main()
