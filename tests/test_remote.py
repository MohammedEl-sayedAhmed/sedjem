"""Tests of running jobs on another computer on the same network (app/remote.py): two Sedjem servers in this process,
one that sends jobs ("Small laptop") and one that runs them ("GPU laptop"), with a stand-in for the local models
(engines.run_local), so no model runs and nothing leaves this computer. Pairing and its codes, the tokens, the refusal
of addresses that aren't private, the pinned certificate, a job sent, run and collected with its details, consent,
cancel, the fallback when the other computer is off, a computer lost midway, the checks on uploads and what comes
back, and the cleanup on the serving side.

The two talk over 127.0.0.1, which the serving side refuses like any address that isn't private, so these tests allow
it, except the one that checks the refusal.
Run: .venv/bin/python -m unittest tests.test_remote -v
"""
import json
import os
import shutil
import socket
import ssl
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import engines, remote  # noqa: E402
from app.config import Config  # noqa: E402
from app.server import make_server  # noqa: E402

GPU = {"devices": [{"name": "NVIDIA GeForce RTX 3060", "kind": "cuda", "type": "dgpu", "memory": 4096}],
       "cuda_devices": 1, "cuda_libs": "/x"}
LINES = [{"start": 0.0, "end": 1.2, "speaker": "1", "text": "order ال discount code"},
         {"start": 1.2, "end": 2.0, "speaker": "2", "text": "Let's start the sprint planning."}]
COHERE = "models/cohere-transcribe-arabic-07-2026-gguf/cohere-transcribe-arabic-07-2026-Q4_K_M.gguf"
LOOPBACK = ("127.0.0.1", "::1", "::ffff:127.0.0.1")
REAL_PRIVATE = remote.private


class StandIn:
    """engines.run_local's stand-in: records who ran what, reports progress and partial lines, and while hold is set
    keeps running until it is cancelled or let go."""

    def __init__(self):
        self.calls, self.hold, self.running = [], threading.Event(), threading.Event()

    def __call__(self, cfg, model, job_dir, job, on_progress, cancelled, register=None):
        self.calls.append({"home": cfg.home, "model": model["id"], "options": dict(job.get("options") or {})})
        (Path(job_dir) / "engine").mkdir(exist_ok=True)
        (Path(job_dir) / "engine" / "audio.json").write_text(json.dumps(LINES[:1]), encoding="utf-8")
        on_progress({"stage": "transcribing", "done": 1, "total": 2})
        self.running.set()
        while self.hold.is_set():
            if cancelled():
                raise engines.Cancelled()
            time.sleep(0.05)
        return {"lines": LINES, "meta": {"device": "cuda: NVIDIA GeForce RTX 3060 (float16)", "peak_rss_mb": 900}}


def home(name, models):
    """A data folder for one of the two computers: its name, a port of its own, and the models it has."""
    folder = Path(tempfile.mkdtemp(prefix="sedjem-remote-"))
    (folder / "app_data").mkdir()
    (folder / "app_data" / "settings.json").write_text(json.dumps({"update_check": False, "usage_counts": False}))
    (folder / "app_data" / "config.toml").write_text(f'[remote]\nport = 0\nname = "{name}"\n', encoding="utf-8")
    for path in models:  # a model file is enough for the model to count as installed
        (folder / path).parent.mkdir(parents=True, exist_ok=True)
        (folder / path).write_bytes(b"stand-in")
    return folder


class Computer:
    """One Sedjem server on a free port, with its own data folder."""

    def __init__(self, name, models):
        self.home = home(name, models)
        self.server, self.app = make_server(Config(home=self.home), port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"X-Sedjem": "1", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def close(self):
        self.app.remote.stop()
        self.app.runner.shutdown()
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.home, ignore_errors=True)


def wait_for(check, timeout=30, what="the condition"):
    end = time.time() + timeout
    while time.time() < end:
        found = check()
        if found:
            return found
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


class Helpers(unittest.TestCase):
    def test_private_addresses(self):
        for ip in ("10.1.2.3", "172.16.0.9", "172.31.255.255", "192.168.1.20", "169.254.10.1", "100.64.0.1",
                   "100.101.102.103", "100.127.255.254", "fd7a:115c:a1e0::1", "fc00::5", "fe80::1", "fe80::1%eth0",
                   "::ffff:192.168.0.7"):
            self.assertTrue(remote.private(ip), ip)
        for ip in ("8.8.8.8", "1.1.1.1", "172.32.0.1", "100.128.0.1", "100.63.255.255", "192.169.0.1", "127.0.0.1",
                   "::1", "0.0.0.0", "2001:db8::1", "2a00:1450::1", "::ffff:8.8.8.8", "224.0.0.1", "not an address", ""):
            self.assertFalse(remote.private(ip), ip)

    def test_codes(self):
        code = remote.new_code()
        self.assertRegex(code, r"^[0-9A-HJKMNP-TV-Z]{8}$")
        self.assertEqual(remote.normal_code(f" {remote.shown_code(code).lower()} "), code)
        self.assertEqual(remote.normal_code("7kq2-m9xo"), "7KQ2M9X0", "O is read as 0, as in Crockford's base 32")
        self.assertEqual(remote.normal_code("ILL2 3456"), "11123456")
        for bad in ("", "1234567", "123456789", "UUUU-UUUU", "12345678<"):
            self.assertIsNone(remote.normal_code(bad), bad)
        key = remote.pair_key(code, "ab" * 32, "01" * 32)
        self.assertNotEqual(key, remote.pair_key(code, "cd" * 32, "01" * 32), "the key depends on the certificate")
        self.assertNotEqual(key, remote.pair_key(code, "ab" * 32, "02" * 32),
                            "and on the attempt's nonce, so no table of keys can be made in advance")
        self.assertNotEqual(key, remote.pair_key(remote.new_code(), "ab" * 32, "01" * 32))

    def test_addresses(self):
        self.assertEqual(remote.parse_address("192.168.1.20"), ("192.168.1.20", remote.PORT))
        self.assertEqual(remote.parse_address(" https://192.168.1.20:9000/ "), ("192.168.1.20", 9000))
        self.assertEqual(remote.parse_address("[fd7a:115c:a1e0::1]:8800"), ("fd7a:115c:a1e0::1", 8800))
        self.assertEqual(remote.parse_address("fd7a:115c:a1e0::1"), ("fd7a:115c:a1e0::1", remote.PORT))
        self.assertEqual(remote.parse_address("office-pc.tail1234.ts.net"), ("office-pc.tail1234.ts.net", remote.PORT))
        for bad in ("", "a b", "host:99999", "host:x", "-host", "../etc"):
            with self.assertRaises(remote.RemoteError, msg=bad):
                remote.parse_address(bad)
        self.assertEqual(remote.shown_address("fd7a::1", 9000), "[fd7a::1]:9000")
        self.assertEqual(remote.shown_address("192.168.1.20", remote.PORT), "192.168.1.20")

    def test_what_comes_back_is_data(self):
        self.assertEqual(remote.clean_lines([{"start": 0, "end": 1, "speaker": "1", "text": " hi "},
                                             {"start": 2, "end": 1, "speaker": None, "text": "end before start"},
                                             {"start": 3, "end": 4, "speaker": None, "text": "  "}]),
                         [{"start": 0.0, "end": 1.0, "speaker": "1", "text": "hi"},
                          {"start": 2.0, "end": 2.0, "speaker": None, "text": "end before start"}])
        self.assertEqual(len(remote.clean_lines([{"start": 0, "end": 1, "speaker": None, "text": "x" * 50000}])[0]["text"]),
                         20000)
        for bad in ({"lines": []}, "text", [1], [{"start": "0", "end": 1, "text": "a"}],
                    [{"start": float("nan"), "end": 1, "text": "a"}], [{"start": -1, "end": 1, "text": "a"}],
                    [{"start": 0, "end": 1, "text": 5}], [{"start": 0, "end": 1, "text": "a", "speaker": "../1"}],
                    [{"start": 0, "end": 1, "text": "a", "speaker": 1}], [{"start": True, "end": 1, "text": "a"}]):
            with self.assertRaises(remote.RemoteError, msg=repr(bad)):
                remote.clean_lines(bad)
        facts = remote.clean_facts({"device": "cuda: " + "A" * 500, "threads": 8, "peak_rss_mb": "lots",
                                    "machine": {"cpu": "i7", "threads": 24, "gpus": ["RTX 3060", 5, "B" * 300], "extra": 1},
                                    "command": "rm -rf /", "power": ["performance"]})
        self.assertEqual(len(facts["device"]), 200)
        self.assertEqual(facts["threads"], 8)
        self.assertNotIn("peak_rss_mb", facts)
        self.assertNotIn("command", facts)
        self.assertNotIn("power", facts)
        self.assertEqual(facts["machine"]["gpus"], ["RTX 3060", "B" * 200])
        self.assertNotIn("extra", facts["machine"])
        with self.assertRaises(remote.RemoteError):
            remote.clean_hello({"name": "x"})
        hello = remote.clean_hello({"app": "sedjem", "name": "GPU\n laptop‮", "busy": -3, "models": [
            {"id": "cohere", "title": "Cohere", "speed": {"rtf": 0.1, "runs_on": "gpu", "measured": "yes"}},
            {"id": "../../etc", "title": "bad"}, "not a model", {"id": "x" * 101}]})
        self.assertEqual(hello["name"], "GPU laptop")
        self.assertEqual([m["id"] for m in hello["models"]], ["cohere"])
        self.assertEqual(hello["models"][0]["speed"], {"rtf": 0.1, "measured": False, "runs_on": "gpu"})
        self.assertEqual(hello["busy"], 0)
        for huge in (10 ** 400, -10 ** 400):  # too large for a float: not a number here
            self.assertIsNone(remote.number(huge))
            with self.assertRaises(remote.RemoteError):
                remote.clean_lines([{"start": huge, "end": 1, "text": "a"}])
            self.assertEqual(remote.clean_facts({"threads": huge}), {})
        with self.assertRaises(remote.RemoteError):
            remote.clean_state({"status": "exploded"})
        self.assertIsNone(remote.clean_state({"status": "running", "stage": "<script>"})["stage"])


def flac(path, seconds=1.0, rate=16000, channels=1):
    sf.write(path, np.zeros((int(seconds * rate), channels), dtype=np.float32), rate, format="FLAC")
    return Path(path).read_bytes()


class Limits(unittest.TestCase):
    """What the serving side takes: FLAC whose header says at most 48 kHz, two channels and LONGEST_HOURS, decoded no
    further than that whatever the header says, and errors in fixed words."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sedjem-limits-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_flac_header(self):
        self.assertIsNone(remote.streaminfo_problem(flac(self.tmp / "ok.flac", rate=48000, channels=2)[:42]))
        self.assertIn("isn't FLAC", remote.streaminfo_problem(b"RIFF" + b"\0" * 38))
        self.assertIn("isn't FLAC", remote.streaminfo_problem(b"fLaC"))
        self.assertIn("at most 48 kHz and 2 channels", remote.streaminfo_problem(flac(self.tmp / "a.flac", channels=3)[:42]))
        self.assertIn("at most 48 kHz", remote.streaminfo_problem(flac(self.tmp / "b.flac", rate=96000)[:42]))
        head = bytearray(flac(self.tmp / "c.flac")[:42])  # a header that says 50 hours at 16 kHz
        samples = 50 * 3600 * 16000
        head[21] = (head[21] & 0xF0) | (samples >> 32)
        head[22:26] = (samples & 0xFFFFFFFF).to_bytes(4, "big")
        self.assertIn("longer than the 48 hours", remote.streaminfo_problem(bytes(head)))

    def test_decoding_stops_at_the_limit(self):
        from app import jobs
        source = self.tmp / "source.flac"
        flac(source, seconds=3)
        with self.assertRaisesRegex(engines.EngineError, "longer than"):
            jobs.convert(source, self.tmp / "audio.flac", lambda t: None, lambda: False,
                         {"format": "flac", "rate": 48000, "channels": 2, "seconds": 2})
        self.assertFalse((self.tmp / "audio.flac").exists())
        flac(source, seconds=1, channels=2, rate=44100)
        with self.assertRaisesRegex(engines.EngineError, "too many channels"):
            jobs.convert(source, self.tmp / "audio.flac", lambda t: None, lambda: False, {"channels": 1})
        wav = self.tmp / "source.wav"
        sf.write(wav, np.zeros(1600, dtype=np.float32), 16000)
        with self.assertRaisesRegex(engines.EngineError, "no FLAC audio"):  # read only as FLAC
            jobs.convert(wav, self.tmp / "audio.flac", lambda t: None, lambda: False, {"format": "flac"})
        jobs.convert(source, self.tmp / "audio.flac", lambda t: None, lambda: False,
                     {"format": "flac", "rate": 48000, "channels": 2, "seconds": 2})
        self.assertAlmostEqual(sf.info(str(self.tmp / "audio.flac")).duration, 1.0, places=1)

    def test_errors_in_fixed_words(self):
        secret = "/home/you/.local/share/sedjem/remote/jobs/20261030-120000-abcd/source.flac"
        failed = {"status": "failed", "error": f"InvalidDataError: [Errno 1094995529] {secret}"}
        self.assertNotIn("/home", remote.served_error(failed))
        self.assertIn("couldn't be read there", remote.served_error(failed))
        self.assertEqual(remote.served_error({**failed, "started": "x"}), "the model failed there")
        self.assertIsNone(remote.served_error({"status": "running", "error": secret}))


class TwoComputers(unittest.TestCase):
    """A small laptop that sends jobs, and a laptop with a graphics card that runs them."""

    @classmethod
    def setUpClass(cls):
        cls.gpu = mock.patch.object(engines, "gpu_info", return_value=GPU)
        cls.gpu.start()
        cls.local = mock.patch.object(remote, "private", side_effect=lambda ip: REAL_PRIVATE(ip) or ip in LOOPBACK)
        cls.local.start()
        cls.stand_in = StandIn()
        cls.models = mock.patch.object(engines, "run_local", side_effect=cls.stand_in)
        cls.models.start()
        cls.retry = mock.patch.object(remote, "RETRY_SECONDS", 1)
        cls.retry.start()
        # the small laptop has Cohere; the GPU laptop has Cohere and Whisper large-v3
        cls.a = Computer("Small laptop", [COHERE])
        cls.b = Computer("GPU laptop", [COHERE, "models/faster-whisper-large-v3/model.bin"])
        t = np.arange(int(16000 * 2.5)) / 16000
        cls.wav = cls.a.home / "call.wav"
        sf.write(cls.wav, (0.2 * np.sin(2 * np.pi * 330 * t)).astype(np.float32), 16000)
        status, _ = cls.b.call("POST", "/api/settings", {"remote_serve": True})
        assert status == 200
        cls.pair()

    @classmethod
    def tearDownClass(cls):
        cls.stand_in.hold.clear()
        for c in (cls.a, cls.b):
            c.close()
        for p in (cls.retry, cls.models, cls.local, cls.gpu):
            p.stop()

    def setUp(self):
        self.stand_in.hold.clear()
        self.stand_in.running.clear()
        self.stand_in.calls.clear()
        self.b.app.remote.failures.clear()
        if self.b.app.remote.listener is None:
            self.b.app.remote.listen()
        self.b.app.remote.drop_served()  # each test starts with no job there
        self.follow()

    @classmethod
    def pair(cls):
        """Pair the small laptop with the GPU laptop, as Settings does, and return the paired computer."""
        b = cls.b.app.remote
        code = b.open_code()["code"]
        status, view = cls.a.call("POST", "/api/remote/pair", {"address": f"127.0.0.1:{b.listening_port()}",
                                                               "code": code})
        assert status == 200, view
        return view["computers"][-1]

    def follow(self):
        """The GPU laptop listens on a new port each time it starts listening (port 0): the small laptop follows."""
        computers = self.a.app.remote.computers()
        for c in computers:
            c["port"] = self.b.app.remote.listening_port() or c["port"]
        self.a.app.remote.save_computers(computers)

    def computer(self):
        return self.a.app.remote.computers()[-1]

    def send(self, model="cohere", **extra):
        body = {"path": str(self.wav), "model": model, "run_on": self.computer()["id"], "speakers": "2",
                "language": "en", "confirm_send": "1", **extra}
        return self.a.call("POST", "/api/jobs", body)

    def job(self, jid, until=("done", "failed", "cancelled", "interrupted"), timeout=30):
        return wait_for(lambda: (lambda r: r[1] if r[1]["job"]["status"] in until else None)(
            self.a.call("GET", f"/api/jobs/{jid}")), timeout, f"job {jid}")

    def served(self):
        return self.b.app.served.list()

    def raw(self, method, path, token=None, body=None, headers=None):
        """A request to the GPU laptop's serving side, past the app (with the pinned certificate)."""
        computer = self.computer()
        conn = remote.connect("127.0.0.1", self.b.app.remote.listening_port(), computer["fingerprint"], 5)
        try:
            h = {"Connection": "close", **({"Authorization": f"Bearer {token}"} if token else {}), **(headers or {})}
            conn.request(method, path, body=body, headers=h)
            r = conn.getresponse()
            return r.status, json.loads(r.read() or b"{}")
        finally:
            conn.close()

    def token(self):
        return self.a.app.cfg.secrets()[f"remote:{self.computer()['id']}"]

    # ---- off by default, and what each side lists ---------------------------------------------------
    def test_off_by_default(self):
        self.assertFalse(self.a.app.cfg.setting("remote_serve"))
        self.assertIsNone(self.a.app.remote.listener, "the small laptop never switched it on: no port is open")
        status, view = self.a.call("GET", "/api/remote")
        self.assertEqual((view["serve"]["on"], view["serve"]["listening"], view["serve"]["addresses"]), (False, False, []))
        status, body = self.a.call("POST", "/api/remote/code")
        self.assertEqual(status, 409)
        self.assertIn("Turn on Run jobs for other computers", body["error"])
        self.assertEqual(self.a.call("POST", "/api/settings", {"remote_serve": "yes"})[0], 400)

    def test_both_sides_list_the_pairing(self):
        _, view = self.a.call("GET", "/api/remote")
        self.assertEqual([c["name"] for c in view["computers"]], ["GPU laptop"])
        self.assertNotIn("token", json.dumps(view), "the token is never shown")
        _, status = self.a.call("GET", "/api/status")
        self.assertEqual(status["remote"]["computers"][0]["name"], "GPU laptop")
        _, view = self.b.call("GET", "/api/remote")
        self.assertEqual([c["name"] for c in view["serve"]["clients"]], ["Small laptop"])
        self.assertTrue(view["serve"]["listening"])
        self.assertRegex(view["serve"]["fingerprint"], r"^([0-9A-F]{2}:){31}[0-9A-F]{2}$")
        stored = json.loads((self.b.home / "app_data" / "remote" / "clients.json").read_text())["clients"][0]
        self.assertEqual(stored["token_sha256"], remote.digest(self.token()), "the serving side keeps only a hash")
        self.assertNotIn(self.token(), (self.b.home / "app_data" / "remote" / "clients.json").read_text())
        if os.name != "nt":
            self.assertEqual((self.b.home / "app_data" / "remote" / "clients.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual((self.b.home / "app_data" / "remote" / "key.pem").stat().st_mode & 0o777, 0o600)
            self.assertEqual((self.a.home / "app_data" / "secrets.json").stat().st_mode & 0o777, 0o600)

    # ---- pairing --------------------------------------------------------------------------------------
    def test_pairing_codes(self):
        b, port = self.b.app.remote, self.b.app.remote.listening_port()
        pair = lambda code: self.a.call("POST", "/api/remote/pair", {"address": f"127.0.0.1:{port}", "code": code})
        self.assertIn("eight letters and digits", pair("12345")[1]["error"])
        code = b.open_code()["code"]
        wrong = remote.shown_code("".join("1" if c != "1" else "2" for c in remote.normal_code(code)))
        status, body = pair(wrong)
        self.assertEqual(status, 400)
        self.assertIn("The code is wrong", body["error"])
        self.assertEqual(pair(code)[0], 200, "a wrong try doesn't void the code")
        status, body = pair(code)
        self.assertEqual(status, 400)
        self.assertIn("No pairing code is open on that computer", body["error"], "a code works once")
        code = b.open_code()["code"]
        b.code["expires"] = time.time() - 1
        self.assertIn("No pairing code is open", pair(code)[1]["error"], "after ten minutes it no longer works")
        code = b.open_code()["code"]
        for i in range(remote.CODE_TRIES - 1):
            self.assertIn("Check it and the address", pair(wrong)[1]["error"])
        self.assertIn("it was tried too often", pair(wrong)[1]["error"])
        self.assertIsNone(b.code, "five wrong tries void the code")
        self.assertIn("No pairing code is open", pair(code)[1]["error"])
        b.failures.clear()
        before = self.computer()["id"]
        self.pair()
        _, view = self.a.call("GET", "/api/remote")
        self.assertEqual(len(view["computers"]), 1, "pairing again with the same computer replaces it")
        self.assertEqual(view["computers"][0]["id"], before, "and keeps its id, which jobs and the form name")
        _, view = self.b.call("GET", "/api/remote")
        self.assertEqual(len(view["serve"]["clients"]), 1, "and the computer it replaced was dropped there")

    def test_failures_are_rate_limited(self):
        b = self.b.app.remote
        code = b.open_code()["code"]
        for _ in range(remote.FAILURES):
            self.assertEqual(self.raw("GET", "/v1/hello", token="x" * 43)[0], 401)
        with self.assertRaises(remote.Unreachable):  # refused before TLS, like an address that isn't private
            self.raw("GET", "/v1/hello", token=self.token())
        status, body = self.a.call("POST", "/api/remote/pair", {"address": f"127.0.0.1:{b.listening_port()}",
                                                                "code": code})
        self.assertEqual(status, 502, "even the right code is refused from that address for now")
        b.failures.clear()
        self.assertEqual(self.raw("GET", "/v1/hello", token=self.token())[0], 200)

    # ---- tokens -------------------------------------------------------------------------------------
    def test_every_request_needs_the_token(self):
        for method, path in (("GET", "/v1/hello"), ("POST", "/v1/jobs"), ("GET", "/v1/jobs/20261030-120000-abcd"),
                             ("GET", "/v1/jobs/20261030-120000-abcd/result"), ("POST", "/v1/unpair"),
                             ("DELETE", "/v1/jobs/20261030-120000-abcd"), ("GET", "/v1/anything")):
            t = self.token()
            for token in (None, "x" * 43, t[:-1] + ("A" if t[-1] != "A" else "B")):
                self.b.app.remote.failures.clear()
                self.assertEqual(self.raw(method, path, token=token)[0], 401, (method, path, token))
        status, hello = self.raw("GET", "/v1/hello", token=self.token())
        self.assertEqual((status, hello["name"]), (200, "GPU laptop"))
        self.assertEqual(sorted(m["id"] for m in hello["models"]), ["cohere", "large-v3"],
                         "only the models installed there")

    def test_revoked_on_either_side(self):
        cid = next(c["id"] for c in self.b.app.remote.clients() if c["token_sha256"] == remote.digest(self.token()))
        self.assertEqual(self.b.call("DELETE", f"/api/remote/clients/{cid}")[0], 200)
        found = self.a.app.remote.ask(self.computer()["id"])
        self.assertFalse(found["online"])
        self.assertIn("no longer takes jobs from this computer", found["error"])
        self.b.app.remote.failures.clear()
        self.pair()
        token, pinned = self.token(), self.computer()["fingerprint"]
        status, view = self.a.call("DELETE", f"/api/remote/computers/{self.computer()['id']}")
        self.assertEqual((status, view["computers"]), (200, []))
        self.assertEqual(self.b.app.remote.clients(), [], "the other side was told")
        self.assertEqual(self.raw_token_status(token, pinned), 401)
        self.pair()

    def raw_token_status(self, token, pinned):
        conn = remote.connect("127.0.0.1", self.b.app.remote.listening_port(), pinned, 5)
        try:
            conn.request("GET", "/v1/hello", headers={"Authorization": f"Bearer {token}", "Connection": "close"})
            return conn.getresponse().status
        finally:
            conn.close()

    # ---- addresses ----------------------------------------------------------------------------------
    def test_refuses_addresses_that_arent_private(self):
        b = self.b.app.remote
        before = b.refused
        with mock.patch.object(remote, "private", REAL_PRIVATE):  # 127.0.0.1 is no longer allowed
            with socket.create_connection(("127.0.0.1", b.listening_port()), timeout=5) as s:
                ctx = remote.client_context()
                with self.assertRaises((ssl.SSLError, OSError)):  # closed before TLS: nothing is read
                    ctx.wrap_socket(s).do_handshake()
            self.assertEqual(b.refused, before + 1)
            status, body = self.a.call("POST", "/api/remote/pair", {"address": f"127.0.0.1:{b.listening_port()}",
                                                                    "code": "1234-5678"})
            self.assertEqual(status, 400, "the sending side won't send to it either")
            self.assertIn("isn't a private address", body["error"])
        self.assertEqual(self.raw("GET", "/v1/hello", token=self.token())[0], 200)

    # ---- the certificate pinned at pairing ------------------------------------------------------------
    def test_another_certificate_is_refused(self):
        b = self.b.app.remote
        b.stop()
        for name in ("cert.pem", "key.pem", "clients.json"):
            (b.folder / name).unlink()
        b.listen()  # a new certificate, as on a computer set up again
        self.follow()
        with mock.patch.object(remote.Handler, "dispatch", autospec=True, side_effect=remote.Handler.dispatch) as seen:
            found = self.a.app.remote.ask(self.computer()["id"])
            self.assertFalse(found["online"])
            self.assertIn("its certificate is another one, so nothing was sent", found["error"])
            status, job = self.send()
            self.assertEqual(status, 201)
            done = self.job(job["id"])
            self.assertEqual(done["job"]["status"], "failed")
            self.assertIn("its certificate is another one", done["job"]["error"])
            self.assertFalse(done["job"].get("remote_note"), "not a computer that didn't answer: no fallback")
            self.assertEqual(seen.call_count, 0, "no request reached the serving side")
        self.pair()

    # ---- a job ----------------------------------------------------------------------------------------
    def test_a_job_sent_run_and_collected(self):
        computer = self.a.app.remote.computers()[-1]
        self.a.app.remote.save_computers([{**computer, "consented": False}])
        status, body = self.send(confirm_send="0")
        self.assertEqual(status, 400, "the first time, the form's tick is needed")
        self.assertIn("confirm that the recording may be sent to GPU laptop on your network", body["error"])
        status, job = self.send()
        self.assertEqual(status, 201)
        self.assertEqual((job["kind"], job["model_title"], job["remote"]["name"]),
                         ("remote", "Cohere Transcribe Arabic", "GPU laptop"))
        self.assertTrue(self.computer()["consented"], "asked once for each computer")
        done = self.job(job["id"])
        self.assertEqual(done["job"]["status"], "done", done["job"].get("error"))
        self.assertEqual(done["lines"], LINES)
        self.assertEqual(len(self.stand_in.calls), 1)
        call = self.stand_in.calls[0]
        self.assertEqual(call["home"], self.b.home, "it ran on the GPU laptop")
        self.assertEqual((call["model"], call["options"]["speakers"], call["options"]["language"]), ("cohere", 2, "en"))
        rows = {r["label"]: r["value"] for g in done["detail_groups"] for r in g["rows"]}
        self.assertEqual(rows["Ran on"], "GPU laptop, graphics card: NVIDIA GeForce RTX 3060 (CUDA)")
        self.assertEqual(done["details"]["run"]["remote"], "GPU laptop")
        self.assertIn("Computer", [g["title"] for g in done["detail_groups"]], "that computer's hardware")
        self.assertEqual(done["details"]["run"]["peak_memory_mb"], 900)
        self.assertEqual(done["version"], 0, "its history starts as for a job run here")
        wait_for(lambda: not self.served(), what="the served job to be deleted once collected")
        self.assertEqual(self.b.call("GET", "/api/jobs")[1]["jobs"], [], "the GPU laptop's library stays its own")
        self.assertIn(job["id"], [j["id"] for j in self.a.call("GET", "/api/jobs")[1]["jobs"]])
        status, again = self.send(confirm_send="0")
        self.assertEqual(status, 201, "not asked again for this computer")
        self.assertEqual(self.job(again["id"])["job"]["status"], "done")
        status, rerun = self.a.call("POST", f"/api/jobs/{job['id']}/rerun", {"model": "large-v3",
                                                                            "run_on": self.computer()["id"]})
        self.assertEqual(status, 201, rerun)
        self.assertEqual(self.job(rerun["id"])["job"]["remote"]["name"], "GPU laptop")
        self.assertEqual(self.stand_in.calls[-1]["model"], "large-v3")

    def test_models_that_arent_there(self):
        status, body = self.send(model="whisper-medium")
        self.assertEqual(status, 400)
        self.assertIn("isn't installed on GPU laptop", body["error"])
        self.assertEqual(self.send(model="../x")[0], 400)
        status, body = self.a.call("POST", "/api/jobs", {"path": str(self.wav), "model": "cohere", "run_on": "0" * 12})
        self.assertEqual(status, 400)
        self.assertIn("isn't paired", body["error"])

    def test_cancel(self):
        self.stand_in.hold.set()
        status, job = self.send()
        wait_for(self.stand_in.running.is_set, what="the job to run on the GPU laptop")
        running = self.job(job["id"], until=("running",))
        wait_for(lambda: self.a.call("GET", f"/api/jobs/{job['id']}")[1]["lines"], what="the lines so far")
        self.assertEqual(self.a.call("POST", f"/api/jobs/{job['id']}/cancel")[0], 200)
        self.assertEqual(self.job(job["id"])["job"]["status"], "cancelled")
        wait_for(lambda: not self.served(), what="the job to go from the GPU laptop")
        self.assertIn(running["job"]["stage"], ("transcribing", "remote-preparing", "remote-queued", "sending", "starting"))

    # ---- when the other computer doesn't answer -------------------------------------------------------
    def test_runs_here_when_the_other_computer_is_off(self):
        self.b.app.remote.stop()
        status, job = self.send()
        self.assertEqual(status, 201, "a computer that doesn't answer now still gets the job")
        done = self.job(job["id"])
        self.assertEqual(done["job"]["status"], "done", done["job"].get("error"))
        self.assertEqual(self.stand_in.calls[0]["home"], self.a.home, "it ran here")
        self.assertEqual(done["job"]["kind"], "local")
        self.assertEqual(done["job"]["remote_note"], "GPU laptop didn't answer when this started, so it ran on this computer")
        rows = {r["label"]: r["value"] for g in done["detail_groups"] for r in g["rows"]}
        self.assertEqual(rows["Note"], done["job"]["remote_note"])
        self.assertNotIn("GPU laptop", rows["Ran on"])
        status, job = self.send(model="large-v3")  # on the GPU laptop only
        failed = self.job(job["id"])["job"]
        self.assertEqual(failed["status"], "failed")
        self.assertIn("GPU laptop didn't answer", failed["error"])
        self.assertIn("isn't installed on this computer, so it couldn't run here instead", failed["error"])

    def test_a_computer_that_stops_answering_midway(self):
        self.stand_in.hold.set()
        status, job = self.send()
        wait_for(self.stand_in.running.is_set, what="the job to run on the GPU laptop")
        self.b.app.remote.stop()  # the GPU laptop goes to sleep, say
        failed = self.job(job["id"], timeout=30)["job"]
        self.assertEqual(failed["status"], "failed")
        self.assertTrue(failed["remote_lost"])
        self.assertIn("GPU laptop stopped answering", failed["error"])
        self.assertIn("Run it here instead", failed["error"])
        self.assertEqual(len(self.stand_in.calls), 1, "it didn't switch to this computer by itself")
        self.stand_in.hold.clear()
        status, here = self.a.call("POST", f"/api/jobs/{job['id']}/rerun", {"model": "cohere"})  # Run here instead
        self.assertEqual(status, 201)
        self.assertEqual(self.job(here["id"])["job"]["status"], "done")
        self.assertEqual(self.stand_in.calls[-1]["home"], self.a.home)
        self.b.app.remote.drop_served()

    # ---- the security review's findings --------------------------------------------------------------------
    def test_switching_off_under_a_flood_of_connections(self):
        b = self.b.app.remote
        for _ in range(3):  # it hung 2 times in 5 when the listener stopped under the lock its thread needed
            port, stop = b.listening_port(), threading.Event()

            def flood():
                while not stop.is_set():
                    try:
                        socket.create_connection(("127.0.0.1", port), timeout=1).close()
                    except OSError:
                        time.sleep(0.01)
            threads = [threading.Thread(target=flood, daemon=True) for _ in range(4)]
            for t in threads:
                t.start()
            time.sleep(0.3)
            done = []
            t = threading.Thread(target=lambda: done.append(self.b.call("POST", "/api/settings", {"remote_serve": False})[0]),
                                 daemon=True)
            t.start()
            t.join(10)
            stop.set()
            self.assertEqual(done, [200], "switching off returned")
            self.assertIsNone(b.listener)
            self.assertTrue(b.lock.acquire(timeout=1), "and let go of its lock")
            b.lock.release()
            self.assertEqual(self.b.call("POST", "/api/settings", {"remote_serve": True})[0], 200)
        self.follow()

    def test_one_address_holds_few_connections(self):
        b = self.b.app.remote
        held = [socket.create_connection(("127.0.0.1", b.listening_port()), timeout=5) for _ in range(remote.MAX_PER_ADDRESS)]
        try:
            wait_for(lambda: sum(b.listener.open.values()) == remote.MAX_PER_ADDRESS, what="the connections to be held")
            with socket.create_connection(("127.0.0.1", b.listening_port()), timeout=5) as extra:
                self.assertEqual(extra.recv(1), b"", "one more from the same address is closed at once")
        finally:
            for s in held:
                s.close()
        wait_for(lambda: not b.listener.open, what="the slots to be given back")
        self.assertEqual(self.raw("GET", "/v1/hello", token=self.token())[0], 200)

    def test_a_quota_for_each_computer(self):
        token, head = self.token(), {"Content-Type": "audio/flac"}
        body = flac(self.a.home / "quota.flac")
        ids = []
        for _ in range(remote.MAX_JOBS):
            status, made = self.raw("POST", "/v1/jobs?model=cohere", token, body, head)
            self.assertEqual(status, 201, made)
            ids.append(made["id"])
            self.raw("POST", f"/v1/jobs/{made['id']}/cancel", token)
        status, body_ = self.raw("POST", "/v1/jobs?model=cohere", token, body, head)
        self.assertEqual(status, 429, "cancelled and uncollected jobs count too")
        self.assertIn("already has 10 jobs there", body_["error"])
        for jid in ids:
            self.raw("DELETE", f"/v1/jobs/{jid}", token)
        with mock.patch.object(remote, "CLIENT_BYTES", 100):
            status, body_ = self.raw("POST", "/v1/jobs?model=cohere", token, body, head)
            self.assertEqual(status, 507)
            self.assertIn("would take more than", body_["error"])
        b, client = self.b.app.remote, self.b.app.remote.clients()[0]
        disk = shutil.disk_usage(self.b.home)
        room = disk._replace(free=remote.FREE_FLOOR + 3 * 2 ** 20)  # 3 MB above the floor
        with mock.patch.object(remote.shutil, "disk_usage", return_value=room):
            first = b.reserve(client, 2 ** 20 // 2)  # reserves 1 MB (the upload and its audio)
            with self.assertRaises(remote.Denied, msg="space reserved for an upload in flight counts"):
                b.reserve(client, 3 * 2 ** 19)  # 3 MB more: it would fit alone, not with the first
            b.uploads.pop(first)
            b.uploads.pop(b.reserve(client, 3 * 2 ** 19))
        self.assertEqual(self.served(), [])

    def test_a_job_cancelled_before_it_converts_keeps_no_recording(self):
        token = self.token()
        runner, served = self.b.app.runner, self.b.app.served
        with mock.patch.object(runner, "submit"):  # made, but not queued yet
            status, made = self.raw("POST", "/v1/jobs?model=cohere", token, flac(self.a.home / "c.flac"),
                                    {"Content-Type": "audio/flac"})
        folder = served.dir(made["id"])
        self.assertTrue((folder / "source.flac").exists())
        runner.cancel(made["id"])
        runner.prepare(made["id"], "prepare")
        self.assertFalse((folder / "source.flac").exists())
        with mock.patch.object(runner, "submit"):
            status, made = self.raw("POST", "/v1/jobs?model=cohere", token, flac(self.a.home / "c.flac"),
                                    {"Content-Type": "audio/flac"})
        from app import jobs
        with mock.patch.object(jobs, "convert", side_effect=engines.Cancelled()):  # cancelled while converting
            runner.prepare(made["id"], "prepare")
        self.assertFalse((served.dir(made["id"]) / "source.flac").exists())
        self.b.app.remote.drop_served()

    def test_an_upload_in_flight_when_the_computer_is_removed(self):
        b, data = self.b.app.remote, flac(self.a.home / "f.flac")
        client = next(c for c in b.clients() if c["token_sha256"] == remote.digest(self.token()))

        class Body:  # the computer is removed there while its recording is still coming in
            def __init__(self):
                self.data, self.at = data, 0

            def read(self, n):
                chunk = self.data[self.at:self.at + n]
                self.at += len(chunk)
                if self.at == len(self.data):
                    b.revoke(client["id"])
                return chunk
        with mock.patch.object(self.b.app.runner, "submit") as submit:
            with self.assertRaises(remote.Denied) as e:
                b.accept(client, {"model": "cohere"}, len(data), Body())
        self.assertEqual(e.exception.status, 401)
        submit.assert_not_called()
        self.assertEqual(self.served(), [], "its job was dropped, not queued")
        self.b.app.remote.failures.clear()
        self.pair()

    def test_nothing_about_this_computer_before_a_token(self):
        b = self.b.app.remote
        b.close_code()
        conn = remote.connect("127.0.0.1", b.listening_port(), None, 5, pairing=True)
        try:
            conn.request("POST", "/v1/pair", body=json.dumps({"name": "x", "nonce": "a" * 64, "proof": "b" * 64}),
                         headers={"Content-Type": "application/json", "Connection": "close"})
            r = conn.getresponse()
            answer = json.loads(r.read())
        finally:
            conn.close()
        self.assertEqual(r.status, 403)
        self.assertNotIn("GPU laptop", answer["error"])

    def test_a_paired_computer_needs_its_pinned_certificate(self):
        computer = self.computer()
        with self.assertRaises(remote.RemoteError):  # a null fingerprint is never "don't check"
            remote.Connection("127.0.0.1", 1, None, 5)
        self.a.app.remote.save_computers([{**computer, "fingerprint": None}])
        self.assertEqual(self.a.app.remote.computers(), [], "an entry without a valid fingerprint is left out")
        self.a.app.remote.save_computers([computer])

    def test_the_lines_so_far_come_in_batches(self):
        token = self.token()
        many = [{"start": float(i), "end": i + 0.5, "speaker": "1", "text": f"line {i} " + "x" * 100} for i in range(6000)]
        with mock.patch.object(self.b.app.runner, "submit"):
            _, made = self.raw("POST", "/v1/jobs?model=cohere", token, flac(self.a.home / "l.flac"),
                               {"Content-Type": "audio/flac"})
        self.b.app.served.update(made["id"], status="running")
        with mock.patch.object(engines, "partial_lines", return_value=many):
            got, after = [], 0
            while True:
                status, state = self.raw("GET", f"/v1/jobs/{made['id']}?after={after}", token)
                self.assertEqual(status, 200)
                self.assertLess(len(json.dumps(state)), 400_000, "each answer stays small")
                got += state["lines"]
                after = len(got)
                if after >= state["count"]:
                    break
        self.assertEqual(got, many)
        self.b.app.remote.drop_served()

    def test_a_long_job_is_followed_to_the_end(self):
        many = [{"start": float(i), "end": i + 0.5, "speaker": "1", "text": f"line {i} " + "x" * 150} for i in range(6000)]
        self.stand_in.hold.set()
        status, job = self.send()
        wait_for(self.stand_in.running.is_set, what="the job to run on the GPU laptop")
        with mock.patch.object(engines, "partial_lines", side_effect=lambda d: many if "remote" in str(d) else
                               engines.read_json(Path(d) / "engine" / "remote.json") or []):
            wait_for(lambda: len(self.a.call("GET", f"/api/jobs/{job['id']}")[1]["lines"]) == 6000, timeout=60,
                     what="all the lines so far, in batches")
        self.stand_in.hold.clear()
        self.assertEqual(self.job(job["id"])["job"]["status"], "done")

    def test_a_result_too_long_is_refused(self):
        with mock.patch.object(remote, "RESULT_LONGEST", 50):
            status, job = self.send()
            failed = self.job(job["id"])["job"]
        self.assertEqual(failed["status"], "failed")
        self.assertIn("answer was too long", failed["error"])

    # ---- the serving side's checks ----------------------------------------------------------------------
    def test_uploads_are_checked(self):
        token, tone = self.token(), self.a.home / "tone.flac"
        sf.write(tone, np.zeros(1600, dtype=np.float32), 16000, format="FLAC")
        head = {"Content-Type": "audio/flac"}
        self.assertEqual(self.raw("POST", "/v1/jobs?model=cohere", token, b"RIFF....WAVE", head)[0], 400)
        status, body = self.raw("POST", "/v1/jobs?model=cohere", token, flac(self.a.home / "3ch.flac", channels=3), head)
        self.assertEqual(status, 400)
        self.assertIn("at most 48 kHz and 2 channels", body["error"])
        self.assertEqual(self.raw("POST", "/v1/jobs?model=cohere", token, tone.read_bytes(),
                                  {"Content-Type": "audio/wav"})[0], 415)
        status, body = self.raw("POST", "/v1/jobs?model=whisper-medium", token, tone.read_bytes(), head)
        self.assertEqual(status, 400)
        self.assertIn("isn't installed on GPU laptop", body["error"])
        self.assertEqual(self.raw("POST", "/v1/jobs?model=cohere&speakers=99", token, tone.read_bytes(), head)[0], 400)
        with mock.patch.object(remote, "MAX_UPLOAD", 10):
            self.assertEqual(self.raw("POST", "/v1/jobs?model=cohere", token, tone.read_bytes(), head)[0], 413)
        self.assertEqual(self.raw("POST", "/v1/jobs?model=cohere&path=/etc/passwd", token, tone.read_bytes(), head)[0], 201)
        made = wait_for(lambda: [j for j in self.served() if j["status"] == "done"], what="the served job")[0]
        self.assertEqual(made["title"], "From Small laptop")
        self.assertNotIn("source_path", made, "a path in the request is never read")
        folder = self.b.app.served.dir(made["id"])
        self.assertFalse((folder / "source.flac").exists(), "the upload is deleted once converted")
        self.assertEqual(self.raw("GET", "/v1/jobs/20261030-120000-abcd", token)[0], 404)
        self.assertEqual(self.raw("GET", "/v1/jobs/..%2F..%2Fsecrets", token)[0], 404)
        self.assertEqual(self.raw("GET", f"/v1/jobs/{made['id']}/result", token)[1]["lines"], LINES)
        self.assertEqual(self.raw("DELETE", f"/v1/jobs/{made['id']}", token)[0], 200)
        self.assertFalse(folder.exists())

    def test_cleanup_on_the_serving_side(self):
        token, tone = self.token(), self.a.home / "tone.flac"
        sf.write(tone, np.zeros(1600, dtype=np.float32), 16000, format="FLAC")
        ids = [self.raw("POST", "/v1/jobs?model=cohere", token, tone.read_bytes(), {"Content-Type": "audio/flac"})[1]["id"]
               for _ in range(2)]
        wait_for(lambda: all((self.b.app.served.get(i) or {}).get("status") == "done" for i in ids), what="both jobs")
        old = (datetime.now().astimezone() - timedelta(hours=25)).isoformat(timespec="seconds")
        self.b.app.served.update(ids[0], finished=old)
        self.b.app.remote.tidy()
        self.assertEqual([j["id"] for j in self.served()], [ids[1]], "one never collected goes after a day")
        self.b.app.served.update(ids[1], status="running")
        self.b.app.remote.tidy(restarted=True)  # as at the app's start
        self.assertEqual(self.b.app.served.get(ids[1])["status"], "interrupted", "a job left running isn't run again")
        self.assertEqual(self.b.call("POST", "/api/settings", {"remote_serve": False})[0], 200)
        self.assertIsNone(self.b.app.remote.listener, "switched off, nothing listens")
        wait_for(lambda: not self.served(), what="the served jobs to go when it is switched off")
        self.assertEqual(self.b.call("POST", "/api/settings", {"remote_serve": True})[0], 200)
        self.follow()
        self.assertTrue(self.a.app.remote.ask(self.computer()["id"])["online"], "still paired once it is on again")


if __name__ == "__main__":
    unittest.main()
