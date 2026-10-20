"""Tests for the app (sedjem): transcript helpers, the hosted-API parsers and the HTTP API.

The hosted services are replaced by a local mock server, so nothing leaves this computer.
Run: .venv/bin/python -m unittest discover -s tests -v
With RUN_MODEL_TESTS=1 it also transcribes a public Perle clip with the local whisper-medium model.
"""
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
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import engines, transcript as T  # noqa: E402
from app.config import Config  # noqa: E402
from app.server import make_server  # noqa: E402

ELEVENLABS_REPLY = {
    "language_code": "ara", "language_probability": 0.91, "text": "...", "transcription_id": "tr_123",
    "words": [
        {"text": "تمام،", "start": 0.12, "end": 0.52, "type": "word", "speaker_id": "speaker_0"},
        {"text": " ", "start": 0.52, "end": 0.58, "type": "spacing", "speaker_id": "speaker_0"},
        {"text": "نبدأ", "start": 0.6, "end": 0.9, "type": "word", "speaker_id": "speaker_0"},
        {"text": "meeting؟", "start": 1.1, "end": 1.62, "type": "word", "speaker_id": "speaker_0"},
        {"text": " ", "start": 1.62, "end": 1.9, "type": "spacing", "speaker_id": "speaker_1"},
        {"text": "Yes,", "start": 1.9, "end": 2.2, "type": "word", "speaker_id": "speaker_1"},
        {"text": "let's", "start": None, "end": None, "type": "word", "speaker_id": "speaker_1"},
        {"text": "start.", "start": 2.5, "end": 2.9, "type": "word", "speaker_id": "speaker_1"},
    ],
}

SPEECHMATICS_REPLY = {
    "format": "2.9", "metadata": {"language_pack_info": {"word_delimiter": " "}},
    "results": [
        {"type": "word", "start_time": 0.1, "end_time": 0.5, "alternatives": [{"content": "تمام", "speaker": "S1"}]},
        {"type": "punctuation", "start_time": 0.5, "end_time": 0.5, "attaches_to": "previous",
         "alternatives": [{"content": ",", "speaker": "S1"}]},
        {"type": "word", "start_time": 0.6, "end_time": 1.0, "alternatives": [{"content": "deploy", "speaker": "S1"}]},
        {"type": "punctuation", "start_time": 1.0, "end_time": 1.0, "attaches_to": "previous",
         "alternatives": [{"content": ".", "speaker": "S1"}]},
        {"type": "word", "start_time": 1.2, "end_time": 1.6, "alternatives": [{"content": "okay", "speaker": "S2"}]},
        {"type": "punctuation", "start_time": 1.6, "end_time": 1.6, "attaches_to": "next",
         "alternatives": [{"content": "«", "speaker": "S2"}]},
        {"type": "word", "start_time": 1.7, "end_time": 2.0, "alternatives": [{"content": "sprint", "speaker": "S2"}]},
        {"type": "word", "start_time": 2.1, "end_time": 2.4, "alternatives": [{"content": "hmm", "speaker": "UU"}]},
    ],
}


class TranscriptHelpers(unittest.TestCase):
    def test_lines_group_by_speaker_and_pause(self):
        words = [{"start": 0, "end": 0.5, "text": "a", "speaker": "1", "glue": False},
                 {"start": 0.6, "end": 1, "text": ",", "speaker": "1", "glue": True},
                 {"start": 1.1, "end": 1.5, "text": "b", "speaker": "1", "glue": False},
                 {"start": 4, "end": 4.5, "text": "c", "speaker": "1", "glue": False},  # pause > 1.5 s
                 {"start": 4.6, "end": 5, "text": "d", "speaker": "2", "glue": False}]
        lines = T.lines_from_words(words)
        self.assertEqual([x["text"] for x in lines], ["a, b", "c", "d"])
        self.assertEqual([x["speaker"] for x in lines], ["1", "1", "2"])

    def test_relabel_in_order_of_appearance(self):
        self.assertEqual(T.relabel(["S2", None, "S1", "S2"]), {"S2": "1", "S1": "2"})

    def test_exports(self):
        job = {"title": "اجتماع", "speaker_names": {"1": "Mona"}, "model": "x", "audio_s": 70}
        lines = [{"start": 1.25, "end": 3.5, "speaker": "1", "text": "hello"},
                 {"start": 65, "end": 66, "speaker": "2", "text": "ahlan"},
                 {"start": 67, "end": 68, "speaker": None, "text": "noise"}]
        self.assertEqual(T.to_txt(job, lines), "[00:01] Mona: hello\n[01:05] Speaker 2: ahlan\n[01:07] noise\n")
        srt = T.to_srt(job, lines)
        self.assertIn("1\n00:00:01,250 --> 00:00:03,500\nMona: hello\n", srt)
        vtt = T.to_vtt(job, lines)
        self.assertTrue(vtt.startswith("WEBVTT"))
        self.assertIn("00:01:05.000 --> 00:01:06.000\n<v Speaker 2>ahlan", vtt)
        data = json.loads(T.to_json(job, lines))
        self.assertEqual(data["speakers"], {"1": "Mona", "2": "Speaker 2"})
        self.assertIn("# اجتماع", T.to_md(job, lines))
        self.assertEqual(T.clock(3725), "1:02:05")


class HostedParsers(unittest.TestCase):
    def test_elevenlabs(self):
        lines = engines.elevenlabs_lines(ELEVENLABS_REPLY)
        self.assertEqual([(x["speaker"], x["text"]) for x in lines],
                         [("1", "تمام، نبدأ meeting؟"), ("2", "Yes, let's start.")])
        self.assertEqual(lines[1]["start"], 1.9)

    def test_speechmatics(self):
        lines = engines.speechmatics_lines(SPEECHMATICS_REPLY)
        self.assertEqual([(x["speaker"], x["text"]) for x in lines],
                         [("1", "تمام, deploy."), ("2", "okay «sprint"), (None, "hmm")])

    def test_split_terms(self):
        self.assertEqual(engines.split_terms("GitHub, Jira\nbackend، deploy"), ["GitHub", "Jira", "backend", "deploy"])


class MockServices(BaseHTTPRequestHandler):
    """ElevenLabs and Speechmatics stand-ins that record what they received."""
    calls = []
    sm_hold = threading.Event()  # while clear, Speechmatics jobs stay "running"

    def log_message(self, *a):
        pass

    def fields(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
            b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body)
        out = {}
        for part in msg.iter_parts():
            name = part.get_param("name", header="content-disposition")
            value = part.get_payload(decode=True)
            out.setdefault(name, []).append(value if part.get_filename() else value.decode())
        return out

    def send(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path == "/v1/speech-to-text":
            if self.headers.get("xi-api-key") != "el-key":
                return self.send(401, {"detail": {"code": "invalid_api_key", "message": "Invalid API key"}})
            self.calls.append(("el-post", self.fields()))
            return self.send(200, ELEVENLABS_REPLY)
        if self.path == "/v2/jobs/":
            self.calls.append(("sm-post", self.fields(), self.headers.get("Authorization")))
            return self.send(201, {"id": "job1"})
        self.send(404, {})

    def do_GET(self):
        if self.path.startswith("/v2/jobs/job1/transcript"):
            self.calls.append(("sm-transcript",))
            return self.send(200, SPEECHMATICS_REPLY)
        if self.path.startswith("/v2/jobs/job1"):
            self.calls.append(("sm-status",))
            return self.send(200, {"job": {"id": "job1", "status": "done" if self.sm_hold.is_set() else "running"}})
        self.send(404, {})

    def do_DELETE(self):
        self.calls.append(("delete", self.path))
        self.send(200, {})


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="sedjem-test-"))
        cls.mock = ThreadingHTTPServer(("127.0.0.1", 0), MockServices)
        threading.Thread(target=cls.mock.serve_forever, daemon=True).start()
        mock_url = f"http://127.0.0.1:{cls.mock.server_address[1]}"
        storage = cls.tmp / "data"
        storage.mkdir()
        (storage / "config.toml").write_text(
            f'[[models]]\nid = "elevenlabs"\nbase_url = "{mock_url}"\n\n'
            f'[[models]]\nid = "speechmatics"\nbase_url = "{mock_url}/v2"\n')
        for var in ("ELEVENLABS_API_KEY", "SPEECHMATICS_API_KEY"):
            os.environ.pop(var, None)
        cls.cfg = Config(storage=storage)
        cls.server, cls.app = make_server(cls.cfg, port=0)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        t = np.arange(int(16000 * 1.5)) / 16000
        cls.wav = cls.tmp / "tone.wav"
        sf.write(cls.wav, (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32), 16000)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.mock.shutdown()
        cls.mock.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, method, path, body=None, headers=None, raw=None):
        h = {"X-Sedjem": "1", **(headers or {})}
        data = raw
        if body is not None:
            data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                payload = r.read()
                return r.status, (json.loads(payload) if "json" in r.headers.get("Content-Type", "") else payload), r.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}"), e.headers

    def wait(self, jid, until=("done", "failed", "cancelled"), timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            _, r, _ = self.call("GET", f"/api/jobs/{jid}")
            if r["job"]["status"] in until:
                return r
            time.sleep(0.2)
        self.fail(f"job {jid} still {r['job']['status']}")

    def test_1_security(self):
        self.assertEqual(self.call("POST", "/api/keys", {"model": "elevenlabs", "key": "x"}, headers={"X-Sedjem": ""})[0], 403)
        self.assertEqual(self.call("GET", "/api/status", headers={"Host": "evil.example:80"})[0], 403)
        self.assertEqual(self.call("POST", "/api/keys", {"model": "elevenlabs", "key": "x"},
                                   headers={"Origin": "http://evil.example"})[0], 403)
        status, body, _ = self.call("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["app"], "sedjem")
        hosted = {m["id"]: m for m in body["models"] if m["kind"] == "hosted"}
        self.assertFalse(hosted["elevenlabs"]["ready"])

    def test_2_elevenlabs_upload_flow(self):
        self.call("POST", "/api/keys", {"model": "elevenlabs", "key": "el-key"})
        params = "model=elevenlabs&speakers=2&language=ar&prompt=GitHub,%20Jira&name=tone.wav&title=Tone"
        status, body, _ = self.call("POST", f"/api/jobs?{params}", raw=self.wav.read_bytes(),
                                    headers={"Content-Type": "audio/wav"})
        self.assertEqual(status, 400, "a hosted model needs the upload confirmed")
        status, job, _ = self.call("POST", f"/api/jobs?{params}&confirm_upload=1", raw=self.wav.read_bytes(),
                                   headers={"Content-Type": "audio/wav"})
        self.assertEqual(status, 201, job)
        r = self.wait(job["id"])
        self.assertEqual(r["job"]["status"], "done", r)
        self.assertEqual(len(r["lines"]), 2)
        self.assertAlmostEqual(r["job"]["audio_s"], 1.5, places=1)
        sent = [c for c in MockServices.calls if c[0] == "el-post"][-1][1]
        self.assertEqual(sent["model_id"], ["scribe_v2"])
        self.assertEqual(sent["diarize"], ["true"])
        self.assertEqual(sent["num_speakers"], ["2"])
        self.assertEqual(sent["language_code"], ["ar"])
        self.assertEqual(sent["keyterms"], ["GitHub", "Jira"])
        self.assertTrue(sent["file"][0].startswith(b"fLaC"), "the upload is the 16 kHz FLAC")
        self.assertIn(("delete", "/v1/speech-to-text/transcripts/tr_123"), MockServices.calls)
        folder = self.cfg.storage / "jobs" / job["id"]
        self.assertFalse(list(folder.glob("source.*")), "the uploaded original is removed after conversion")

        jid = job["id"]
        status, r, _ = self.call("PATCH", f"/api/jobs/{jid}", {"speaker_names": {"1": "Mona", "x": "bad"}})
        self.assertEqual(r["job"]["speaker_names"], {"1": "Mona"})
        status, r, _ = self.call("PATCH", f"/api/jobs/{jid}", {"merge": {"from": "2", "into": "1"}})
        self.assertEqual({x["speaker"] for x in r["lines"]}, {"1"})
        self.assertTrue(r["edited"])
        lines = r["lines"]
        lines[0]["text"] = "edited text"
        status, r, _ = self.call("PATCH", f"/api/jobs/{jid}", {"lines": lines})
        self.assertEqual(r["lines"][0]["text"], "edited text")
        self.assertEqual(self.call("PATCH", f"/api/jobs/{jid}", {"lines": [{"start": 0, "end": 1, "text": "x",
                                                                          "speaker": "<b>"}]})[0], 400)
        status, txt, headers = self.call("GET", f"/api/jobs/{jid}/export/txt")
        self.assertIn("Mona: edited text", txt.decode())
        self.assertIn("attachment", headers["Content-Disposition"])
        req = urllib.request.Request(self.base + f"/api/jobs/{jid}/audio", headers={"Range": "bytes=0-99"})
        with urllib.request.urlopen(req) as resp:
            self.assertEqual(resp.status, 206)
            self.assertEqual(len(resp.read()), 100)
            self.assertTrue(resp.headers["Content-Range"].startswith("bytes 0-99/"))

    def test_3_speechmatics_path_flow_and_rerun(self):
        self.call("POST", "/api/keys", {"model": "speechmatics", "key": "sm-key"})
        MockServices.sm_hold.set()
        status, job, _ = self.call("POST", "/api/jobs", {"path": str(self.wav), "model": "speechmatics",
                                                         "speakers": "auto", "language": "ar", "confirm_upload": True,
                                                         "prompt": "sprint, a very long phrase of more than six words here"})
        self.assertEqual(status, 201, job)
        r = self.wait(job["id"])
        self.assertEqual(r["job"]["status"], "done", r)
        self.assertEqual([x["speaker"] for x in r["lines"]], ["1", "2", None])
        post = [c for c in MockServices.calls if c[0] == "sm-post"][-1]
        config = json.loads(post[1]["config"][0])["transcription_config"]
        self.assertEqual(post[2], "Bearer sm-key")
        self.assertEqual(config["language"], "ar_en")
        self.assertEqual(config["model"], "enhanced")
        self.assertEqual(config["diarization"], "speaker")
        self.assertEqual(config["additional_vocab"], [{"content": "sprint"}])
        self.assertIn(("delete", "/v2/jobs/job1"), MockServices.calls)
        self.assertTrue(self.wav.exists(), "a file used in place is never touched")

        status, new, _ = self.call("POST", f"/api/jobs/{job['id']}/rerun", {"model": "elevenlabs"})
        self.assertEqual(status, 400, "a rerun on a hosted model needs the upload confirmed too")
        status, new, _ = self.call("POST", f"/api/jobs/{job['id']}/rerun", {"model": "elevenlabs", "confirm_upload": True})
        self.assertEqual(status, 201, new)
        self.assertEqual(self.wait(new["id"])["job"]["status"], "done")
        a = self.cfg.storage / "jobs" / job["id"] / "audio.flac"
        b = self.cfg.storage / "jobs" / new["id"] / "audio.flac"
        self.assertEqual(a.stat().st_ino, b.stat().st_ino, "a rerun shares the audio file")

    def test_4_cancel_removes_the_remote_job(self):
        MockServices.sm_hold.clear()
        status, job, _ = self.call("POST", "/api/jobs", {"path": str(self.wav), "model": "speechmatics",
                                                         "speakers": "none", "confirm_upload": True})
        self.wait(job["id"], until=("running",))
        end = time.time() + 20
        while time.time() < end and self.call("GET", f"/api/jobs/{job['id']}")[1]["job"].get("stage") != "remote":
            time.sleep(0.2)
        self.call("POST", f"/api/jobs/{job['id']}/cancel")
        r = self.wait(job["id"], until=("cancelled",))
        self.assertEqual(r["job"]["status"], "cancelled")
        self.assertIn(("delete", "/v2/jobs/job1?force=true"), MockServices.calls)
        MockServices.sm_hold.set()
        status, body, _ = self.call("DELETE", f"/api/jobs/{job['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(self.call("GET", f"/api/jobs/{job['id']}")[0], 404)

    def test_5_keep_alive_after_an_ignored_body(self):
        """A POST whose handler ignores the body must not garble the next request on the connection."""
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        try:
            conn.request("POST", "/api/downloads/voiceprints/cancel", body=b"{}",
                         headers={"X-Sedjem": "1", "Content-Type": "application/json"})
            r = conn.getresponse()
            r.read()
            self.assertEqual(r.status, 200)
            conn.request("GET", "/api/status")
            r = conn.getresponse()
            self.assertEqual(r.status, 200)
            self.assertEqual(json.loads(r.read())["app"], "sedjem")
        finally:
            conn.close()

    def test_5_bad_input(self):
        self.assertEqual(self.call("POST", "/api/jobs", {"path": "/nonexistent.wav", "model": "whisper-medium"})[0], 400)
        self.assertEqual(self.call("POST", "/api/jobs", {"path": str(self.wav), "model": "nope"})[0], 400)
        self.assertEqual(self.call("GET", "/api/jobs/../../etc/passwd")[0], 404)
        self.assertEqual(self.call("GET", "/static/../server.py")[0], 404)

    @unittest.skipUnless(os.environ.get("RUN_MODEL_TESTS"), "set RUN_MODEL_TESTS=1 to run a local model")
    def test_6_local_whisper_on_a_public_clip(self):
        clip = ROOT / "data" / "perle" / "wav16k" / "perle0603.wav"
        if not clip.exists() or not self.cfg.availability(self.cfg.models["whisper-medium"])[0]:
            self.skipTest("needs data/perle and the whisper-medium model")
        status, job, _ = self.call("POST", "/api/jobs", {"path": str(clip), "model": "whisper-medium", "speakers": "none"})
        self.assertEqual(status, 201, job)
        r = self.wait(job["id"], timeout=600)
        self.assertEqual(r["job"]["status"], "done", r.get("log"))
        self.assertTrue(r["lines"] and r["lines"][0]["text"])


class Backlog(unittest.TestCase):
    """A browser opens several connections at once while the server may still be busy: none is refused.
    (Windows refuses connections beyond the listen queue, which made a page fail to load in CI.)"""

    def test_a_burst_of_connections_waits_instead_of_being_refused(self):
        import socket
        home = Path(tempfile.mkdtemp(prefix="sedjem-backlog-"))
        server, _app = make_server(Config(home=home), port=0)  # listening, but not accepting yet
        socks = []
        try:
            for _ in range(30):
                c = socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=5)
                socks.append(c)
            self.assertEqual(len(socks), 30)
        finally:
            for c in socks:
                c.close()
            server.server_close()
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
