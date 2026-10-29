"""Tests for the anonymous usage counts (app/usage.py) and their public totals (tools/usage_totals.py).

The three events carry only the fields and values that may be sent, with the right types, and none of a
recording's names, paths, links or text; install, active_month and week_summary are each sent once for their
period (months and weeks in UTC); nothing is counted or sent before the first-start dialog, with the switch off,
without a key, with DO_NOT_TRACK or SEDJEM_NO_USAGE, or while the tests run. The sender is tried against a stand-in
for Aptabase on 127.0.0.1: accepted, a key Aptabase doesn't take, 400, 408, 429, 5xx, a timeout, no connection, a
redirect, events a day old, batches of 25. The app counts at the end of a run, an export, the compare view and a
YouTube job, through the HTTP API and the runner. The totals are made from a made-up export, with the
three-install threshold for countries. The docs must list exactly what is sent.

Nothing here reaches Aptabase: while unittest is loaded app/usage.py counts and sends nothing, and post() refuses
any address but 127.0.0.1. The tests that try the sending replace test_run() and point the key at the stand-in.
Run: .venv/bin/python -m unittest tests.test_usage -v
"""
import csv
import importlib.util
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import desktop, engines, usage, youtube  # noqa: E402
from app.config import Config  # noqa: E402
from app.server import make_server  # noqa: E402
from ui_support import demo_job  # noqa: E402

spec = importlib.util.spec_from_file_location("usage_totals", ROOT / "tools" / "usage_totals.py")
assert spec and spec.loader
totals = importlib.util.module_from_spec(spec)
spec.loader.exec_module(totals)

KEY = "A-SH-1234567890"


def at(text):
    """A moment as seconds since 1970, from an ISO 8601 time."""
    return datetime.fromisoformat(text).timestamp()


T = at("2026-10-28T12:00:00+00:00")  # a Wednesday, in ISO week 2026-W44
NEXT_WEEK = at("2026-11-02T09:00:00+00:00")  # Monday of 2026-W45, and a new month
HOUR, DAY = 3600, 86400
GPU = {"devices": [{"name": "Intel(R) Iris(R) Xe Graphics", "kind": "vulkan", "type": "igpu", "memory": 0}],
       "cuda_devices": 0, "cuda_libs": None}
MACHINE = {"ram_gb": 15.4, "threads": 12}
FACTS = {"kind": "source", "ui": "desktop", "gpu": "intel", "ram": "8-16", "cores": "8-16"}
# What a transcription holds that must never leave the computer
SECRETS = {"title": "Board meeting about the salaries", "source_name": "ceo-private-call.m4a",
           "source_path": "/home/alice/private/ceo-private-call.m4a", "youtube_id": "dQw4w9WgXcQ",
           "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "speaker_names": {"1": "Alice Example"},
           "error": "Traceback: /home/alice/private failed", "voiceprint_model": "Nemotron-3-Diarization"}
LINE_TEXT = "the password is hunter2-secret"


class Quiet(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):  # a client that gave up (the timeout test)
        pass


class FakeAptabase:
    """A stand-in for Aptabase's batch endpoint on 127.0.0.1. answer: "ok" (200 {}), "unknown key" (200 with the
    error Aptabase sends for a key it doesn't know or an account that is locked), a status code, "slow" (answers
    after the sender gave up) or "redirect". requests: (path, headers in lower case, the events)."""

    def __init__(self):
        self.answer, self.requests = "ok", []
        self.server = Quiet(("127.0.0.1", 0), type("Handler", (Answers,), {"fake": self}))
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def events(self):
        return [e for _, _, events in self.requests for e in events]

    def names(self):
        return [e["eventName"] for e in self.events()]


class Answers(BaseHTTPRequestHandler):
    fake: FakeAptabase

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.fake.requests.append((self.path, {k.lower(): v for k, v in self.headers.items()}, json.loads(body)))
        answer = self.fake.answer
        if answer == "slow":
            time.sleep(1.5)
        if answer == "redirect":
            self.send_response(302)
            self.send_header("Location", f"{self.fake.url}/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        status, out = 200, b"{}"
        if answer == "unknown key":
            out = json.dumps({"error": f"Application not found with given app key: {KEY}. Events are not being recorded."}).encode()
        elif isinstance(answer, int):
            status, out = answer, b"Something went wrong"
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def clean_environment(case):
    """None of the variables that change the counts, whatever the computer running the tests has set."""
    env = mock.patch.dict(os.environ)
    env.start()
    case.addCleanup(env.stop)
    for var in ("DO_NOT_TRACK", "SEDJEM_NO_USAGE", "SEDJEM_USAGE_DEBUG"):
        os.environ.pop(var, None)


def job(**fields):
    """A finished local run of Cohere, as the runner hands it over, with what must never be sent."""
    return {"id": "20261028-120000-a001", "status": "done", "kind": "local", "model": "cohere", "audio_s": 125.4,
            **SECRETS, **fields}


class UsageCase(unittest.TestCase):
    """A data folder of its own, with the stand-in's key and address in its config.toml and the switch on, and
    test_run() replaced so that the counts are allowed (post() still reaches only 127.0.0.1)."""

    def setUp(self):
        clean_environment(self)
        self.home = Path(tempfile.mkdtemp(prefix="sedjem-usage-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / "app_data").mkdir()
        self.fake = FakeAptabase()
        self.addCleanup(self.fake.close)
        self.config(f'[usage]\napp_key = "{KEY}"\nhost = "{self.fake.url}"\n')
        self.cfg.save_settings(usage_counts=True)
        allowed = mock.patch.object(usage, "test_run", return_value=False)
        allowed.start()
        self.addCleanup(allowed.stop)
        self.u = self.usage()

    def config(self, toml):
        (self.home / "app_data" / "config.toml").write_text(toml, encoding="utf-8")
        self.cfg = Config(home=self.home)

    def usage(self, now=T):
        """The counts as the app makes them, at a moment the test chooses."""
        u = usage.Usage(self.cfg, version=lambda: "0.3.0", facts=lambda: usage.facts("source", True, GPU, MACHINE))
        u.clock = lambda: now
        return u

    def state(self):
        return json.loads((self.home / "app_data" / "usage.json").read_text(encoding="utf-8"))


class Events(UsageCase):
    def test_the_events_carry_only_what_may_be_sent(self):
        self.u.job_ended(job(), [{"start": 0, "end": 2, "speaker": "1", "text": LINE_TEXT}])
        self.u.job_ended(job(status="failed"))
        self.u.exported("srt")
        self.u.compared()
        self.assertTrue(self.u.tick(T))
        self.assertTrue(self.u.tick(NEXT_WEEK))
        self.assertEqual(self.fake.names(), ["install", "active_month", "active_month", "week_summary"],
                         "a new month and the week that ended")
        for path, headers, events in self.fake.requests:
            self.assertEqual(path, "/api/v0/events")
            self.assertEqual(headers["app-key"], KEY)
            self.assertEqual(headers["content-type"], "application/json")
            s = events[0]["systemProps"]
            self.assertEqual(headers["user-agent"], f"{s['osName']}/{s['osVersion']} Sedjem/0.3.0 {s['locale']}".strip())
        allowed_strings = {"install", "active_month", "week_summary", "2026-W44", "python", usage.SDK, "0.3.0",
                           *(v for values in usage.VALUES.values() for v in values)}
        for e in self.fake.events():
            self.assertEqual(set(e), {"timestamp", "sessionId", "eventName", "systemProps", "props"})
            self.assertRegex(e["timestamp"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.000Z$")
            self.assertRegex(e["sessionId"], r"^\d{18}$")
            s = e["systemProps"]
            self.assertEqual(set(s), {"isDebug", "osName", "osVersion", "locale", "appVersion", "engineName",
                                      "engineVersion", "sdkVersion"}, "no device model, no build number")
            self.assertIs(s["isDebug"], False)
            self.assertIn(s["osName"], ("Windows", "Linux", "macOS", platform_name()))
            self.assertTrue(all(isinstance(v, str) for k, v in s.items() if k != "isDebug"))
            self.assertRegex(s["locale"], r"^([a-z]{2}(-[A-Z]{2})?)?$")
            strings = [v for v in e["props"].values() if isinstance(v, str)]
            self.assertTrue(set(strings) <= allowed_strings, strings)
        install, month, _, week = self.fake.events()
        self.assertEqual(install["props"], {})
        self.assertEqual(month["props"], FACTS)
        self.assertEqual(install["timestamp"], usage.stamp(T))
        self.assertTrue(install["sessionId"].startswith(str(int(T))))
        self.assertEqual(install["sessionId"], month["sessionId"], "made together, one session")
        self.assertNotEqual(week["sessionId"], month["sessionId"], "made later, another")
        self.assertEqual(week["props"], {**dict.fromkeys(usage.SUMMARY, 0), "transcriptions": 1, "minutes": 2,
                                         "cohere": 1, "speakers": 1, "nemotron": 1, "youtube": 1, "compares": 1,
                                         "failed": 1, "srt": 1, "week": "2026-W44"})
        self.assertEqual(list(week["props"]), [*usage.SUMMARY, "week"])
        self.assertTrue(all(type(week["props"][k]) is int for k in usage.SUMMARY))

        sent = json.dumps([r[1:] for r in self.fake.requests])
        for secret in (*[v for v in SECRETS.values() if isinstance(v, str)], "Alice", LINE_TEXT, "hunter2",
                       "youtube.com", "ceo", "/home", str(self.home), "125.4"):
            self.assertNotIn(secret, sent)

    def test_the_counts_by_model_and_feature(self):
        lines = [{"start": 0, "end": 1, "speaker": "1", "text": "x"}]
        bare = [{"start": 0, "end": 1, "speaker": None, "text": "x"}]
        self.u.job_ended(job(model="cohere", audio_s=60, voiceprint_model="nemo_en_titanet_small.onnx", youtube_id=None), lines)
        self.u.job_ended(job(model="whisper-medium", audio_s=90, voiceprint_model=None, youtube_id=None), bare)
        self.u.job_ended(job(model="large-v3", audio_s=30, voiceprint_model=None, youtube_id=None), bare)
        self.u.job_ended(job(model="hf-someone-whisper-small", audio_s=20, voiceprint_model=None, youtube_id=None), bare)
        self.u.job_ended(job(model="elevenlabs", kind="hosted", audio_s=600, voiceprint_model=None, youtube_id=None), lines)
        self.u.job_ended(job(model="youtube-captions", kind="captions", audio_s=1200, voiceprint_model=None), lines)
        self.u.job_ended(job(status="cancelled"))
        self.u.job_ended(job(status="interrupted"))
        self.u.job_ended(None)
        for fmt in (*usage.FORMATS, "docx"):
            self.u.exported(fmt)
        self.u.exported("txt")
        self.u.tick(NEXT_WEEK)
        week = next(e for e in self.fake.events() if e["eventName"] == "week_summary")["props"]
        self.assertEqual(week, {"transcriptions": 5, "minutes": 13, "cohere": 1, "whisper_medium": 1, "whisper_large": 1,
                                "other_local": 1, "hosted": 1, "speakers": 2, "nemotron": 0, "youtube": 1,
                                "compares": 0, "failed": 0, "txt": 2, "srt": 1, "vtt": 1, "md": 1, "json": 1,
                                "week": "2026-W44"})

    def test_the_active_month_values(self):
        nvidia = {"devices": [{"name": "Intel(R) UHD Graphics"}], "cuda_devices": 1}
        self.assertEqual(usage.gpu_kind(nvidia), "nvidia", "an NVIDIA card through CUDA comes first")
        self.assertEqual(usage.gpu_kind({"devices": [{"name": "NVIDIA RTX A1000 Laptop GPU"}, {"name": "Intel(R) UHD Graphics"}]}), "nvidia")
        self.assertEqual(usage.gpu_kind({"devices": [{"name": "AMD Radeon(TM) Graphics"}, {"name": "Intel(R) Arc(TM) A380"}]}), "amd")
        self.assertEqual(usage.gpu_kind({"devices": [{"name": "Radeon RX 7600"}]}), "amd")
        self.assertEqual(usage.gpu_kind(GPU), "intel")
        self.assertEqual(usage.gpu_kind({"devices": [{"name": "Apple M2"}]}), "other")
        for none in ({"devices": [], "cuda_devices": 0}, {"error": "exit 1"}, None, {"devices": "?"}):
            self.assertEqual(usage.gpu_kind(none), "none")
        self.assertEqual([usage.ram_range(gb) for gb in (3.8, 5.8, 6.8, 7.6, 11.6, 15.4, 23.4, 31.7, 62.6, None)],
                         ["under 8", "under 8", "8-16", "8-16", "8-16", "8-16", "16-32", "16-32", "over 32", "under 8"])
        self.assertEqual([usage.cores_range(n) for n in (2, 3, 4, 8, 9, 16, 17, 24, None)],
                         ["under 4", "under 4", "4-8", "4-8", "8-16", "8-16", "over 16", "over 16", "under 4"])
        self.assertEqual([usage.install_kind(k, frozen=True) for k in ("windows", "linux", "other", "source")],
                         ["installer", "linux-build", "other", "other"])
        self.assertEqual([usage.install_kind(k, frozen=False) for k in ("source", "other")], ["source", "source"],
                         "from source, with or without .git")
        self.assertEqual(usage.facts("linux", False, None, {"ram_gb": 31.7, "threads": 24}),
                         {"kind": "source", "ui": "browser", "gpu": "none", "ram": "16-32", "cores": "over 16"})
        for kind in ("source", "windows", "linux", "other"):  # whatever comes in, only the listed values go out
            for gpu in (None, GPU, {"devices": [{"name": "x"}]}):
                for machine in ({}, MACHINE, {"ram_gb": "lots", "threads": "many"}):
                    facts = usage.facts(kind, kind == "windows", gpu, machine)
                    self.assertTrue(all(facts[k] in usage.VALUES[k] for k in usage.VALUES), facts)

    def test_the_system_props(self):
        with mock.patch.object(usage.platform, "version", return_value="10.0.26100"):
            self.assertEqual(usage.system_name("win32"), ("Windows", "10.0.26100"))
        with mock.patch.object(usage.platform, "mac_ver", return_value=("15.1", ("", "", ""), "arm64")):
            self.assertEqual(usage.system_name("darwin"), ("macOS", "15.1"))
        release = self.home / "os-release"
        release.write_text('NAME="Ubuntu"\nPRETTY_NAME="Ubuntu 24.04.4 LTS"\nVERSION_ID="24.04"\n', encoding="utf-8")
        self.assertEqual(usage.pretty_name((self.home / "missing", release)), "Ubuntu 24.04.4 LTS")
        self.assertEqual(usage.pretty_name((self.home / "missing",)), "")
        with mock.patch.object(usage, "pretty_name", return_value="Fedora Linux 43 (Workstation Edition)"):
            self.assertEqual(usage.system_name("linux"), ("Linux", "Fedora Linux 43 (Workstation Edition)"))
        with mock.patch.object(usage, "system_name", return_value=("Linux", "Fedora Linux 43" + " x" * 80)):
            self.assertEqual(len(usage.system("0.3.0")["osVersion"]), 100, "cut to what Aptabase takes")
        for given, expected in (("ar_EG.UTF-8", "ar-EG"), ("en_US.UTF-8", "en-US"), ("en-us", "en-US"), ("de", "de"),
                                ("de_DE@euro", "de-DE"), ("sr-Latn-RS", "sr"), ("C.UTF-8", ""), ("POSIX", ""), ("", "")):
            self.assertEqual(usage.system_locale(given), expected, given)
        with mock.patch.object(usage.sys, "platform", "win32"), mock.patch.object(usage, "windows_locale", return_value="ar-EG"):
            self.assertEqual(usage.system_locale(), "ar-EG")
        with mock.patch.dict(os.environ, {"LC_ALL": "", "LC_MESSAGES": "", "LANG": "ar_EG.UTF-8"}), \
                mock.patch.object(usage.sys, "platform", "linux"):
            self.assertEqual(usage.system_locale(), "ar-EG")
        self.assertIs(usage.system("0.3.0")["isDebug"], False)
        with mock.patch.dict(os.environ, {"SEDJEM_USAGE_DEBUG": "1"}):
            self.assertIs(usage.system("0.3.0")["isDebug"], True)
        self.assertEqual(usage.system("0.3.1-preview.20261101")["appVersion"], "0.3.1-preview.20261101")

    def test_only_the_listed_events_and_fields_leave(self):
        self.u.tick(T)
        good = self.fake.events()[1]
        self.assertEqual(usage.checked(good), good)
        self.assertEqual(usage.checked({**good, "userId": "abc"}), good, "a field of its own is left out")
        bad = [{**good, "eventName": "page_view"}, {**good, "props": {**good["props"], "title": "x"}},
               {**good, "props": {**good["props"], "gpu": "NVIDIA RTX A1000"}},
               {**good, "systemProps": {**good["systemProps"], "deviceModel": "ThinkPad P16"}},
               {**good, "systemProps": {**good["systemProps"], "locale": "en-US-POSIX-x"}},
               {**good, "systemProps": {**good["systemProps"], "isDebug": "false"}},
               {**good, "timestamp": "yesterday"}, {**good, "sessionId": "my-laptop"}, {**good, "sessionId": 17},
               {"eventName": "install", "props": {"path": "/home/alice"}, "systemProps": good["systemProps"],
                "timestamp": good["timestamp"], "sessionId": good["sessionId"]},
               {k: v for k, v in good.items() if k != "systemProps"}, "install", None]
        week = {**good, "eventName": "week_summary", "props": {**dict.fromkeys(usage.SUMMARY, 0), "week": "2026-W44"}}
        self.assertIsNotNone(usage.checked(week))
        bad += [{**week, "props": {**week["props"], "minutes": 12.5}}, {**week, "props": {**week["props"], "txt": -1}},
                {**week, "props": {**week["props"], "txt": True}}, {**week, "props": {**week["props"], "txt": "3"}},
                {**week, "props": {**week["props"], "week": "last week"}},
                {**week, "props": {k: v for k, v in week["props"].items() if k != "failed"}}]
        for event in bad:
            self.assertIsNone(usage.checked(event), event)

        # an event changed in usage.json is dropped instead of sent
        self.fake.requests.clear()
        data = self.state()
        data["queue"] = [{**good, "props": {**good["props"], "title": SECRETS["title"]}}, {**good, "eventName": "x"}]
        (self.home / "app_data" / "usage.json").write_text(json.dumps(data), encoding="utf-8")
        self.usage(T + HOUR).tick(T + HOUR)
        self.assertEqual(self.fake.requests, [])
        self.assertEqual(self.state()["queue"], [])


def platform_name():
    return usage.system_name()[0]


class Periods(UsageCase):
    def test_install_once_for_each_data_folder(self):
        self.u.tick(T)
        self.u.tick(T + HOUR)
        self.usage(T + 2 * HOUR).tick(T + 2 * HOUR)  # started again
        self.assertEqual(self.fake.names().count("install"), 1)
        self.assertEqual(self.state()["install"], "2026-10-28")
        other = Path(tempfile.mkdtemp(prefix="sedjem-usage-other-"))
        self.addCleanup(shutil.rmtree, other, True)
        (other / "app_data").mkdir()
        shutil.copy(self.home / "app_data" / "config.toml", other / "app_data")
        cfg = Config(home=other)
        cfg.save_settings(usage_counts=True)
        usage.Usage(cfg, version=lambda: "0.3.0", facts=lambda: FACTS).tick(T + 3 * HOUR)
        self.assertEqual(self.fake.names().count("install"), 2, "another data folder is another install")

    def test_active_month_once_a_calendar_month(self):
        last = at("2026-10-31T23:59:00+00:00")
        for moment in (T, T + DAY, last):
            self.u.tick(moment)
        self.usage(last).tick(last)
        self.assertEqual(self.fake.names().count("active_month"), 1)
        first = at("2026-11-01T00:01:00+00:00")
        self.u.tick(first)
        self.u.tick(first + 10 * DAY)
        self.assertEqual(self.fake.names().count("active_month"), 2)
        self.assertEqual(self.fake.events()[-1]["timestamp"], usage.stamp(first))

    def test_months_and_weeks_are_utc(self):
        cairo = at("2026-11-01T00:30:00+02:00")  # still 31 October in UTC
        self.assertEqual((usage.month_of(cairo), usage.week_of(cairo)), ("2026-10", "2026-W44"))
        self.assertEqual((usage.month_of(NEXT_WEEK), usage.week_of(NEXT_WEEK)), ("2026-11", "2026-W45"))
        self.assertEqual(usage.week_of(at("2027-01-01T12:00:00+00:00")), "2026-W53", "an ISO week")
        self.assertEqual(usage.stamp(at("2026-10-28T15:00:00+03:00")), "2026-10-28T12:00:00.000Z")

    def test_week_summary_once_for_each_week_with_use(self):
        self.u.tick(T)  # the install and the month
        self.u.count(now=T, transcriptions=1, seconds=600, cohere=1)
        self.u.tick(T + HOUR)
        self.assertNotIn("week_summary", self.fake.names(), "not before the week has ended")
        self.u.count(now=at("2026-11-01T23:00:00+00:00"), txt=1)  # Sunday: the same week
        self.u.tick(NEXT_WEEK)
        self.u.tick(NEXT_WEEK + HOUR)
        self.usage(NEXT_WEEK + DAY).tick(NEXT_WEEK + DAY)
        weeks = [e["props"] for e in self.fake.events() if e["eventName"] == "week_summary"]
        self.assertEqual(len(weeks), 1)
        self.assertEqual((weeks[0]["week"], weeks[0]["transcriptions"], weeks[0]["minutes"], weeks[0]["txt"]),
                         ("2026-W44", 1, 10, 1))
        self.assertEqual(self.state()["weeks"], {})

    def test_weeks_without_use_send_nothing_and_old_weeks_go_one_by_one(self):
        self.u.count(now=T - 14 * DAY, compares=1)  # 2026-W42
        self.u.count(now=T - 7 * DAY, failed=1)     # 2026-W43
        self.u.tick(NEXT_WEEK)  # W44 had no use
        weeks = [e["props"]["week"] for e in self.fake.events() if e["eventName"] == "week_summary"]
        self.assertEqual(weeks, ["2026-W42", "2026-W43"])
        self.assertEqual(len(self.fake.requests), 1, "with the install and the month, in one request")

    def test_the_week_being_counted_waits_in_usage_json(self):
        self.u.count(now=T, transcriptions=1, seconds=61.5, hosted=1)
        self.assertEqual(self.state()["weeks"], {"2026-W44": {"transcriptions": 1, "seconds": 61.5, "hosted": 1}})
        again = self.usage(NEXT_WEEK)  # started again after the week
        again.tick(NEXT_WEEK)
        week = next(e for e in self.fake.events() if e["eventName"] == "week_summary")["props"]
        self.assertEqual((week["transcriptions"], week["minutes"], week["hosted"]), (1, 1, 1))


class Disabled(UsageCase):
    def assertQuiet(self, u, at_time=NEXT_WEEK):
        u.job_ended(job())
        u.exported("txt")
        u.compared()
        self.assertFalse(u.tick(at_time))
        self.assertEqual(self.fake.requests, [])

    def test_nothing_before_the_first_start_dialog(self):
        self.cfg.save_settings(usage_counts=None)
        self.assertEqual(self.u.summary(), {"available": True, "on": True, "ask": True, "blocked": None})
        self.assertQuiet(self.u)
        self.assertFalse((self.home / "app_data" / "usage.json").exists(), "nothing counted either")

    def test_switched_off_nothing_is_counted_and_what_was_kept_goes(self):
        self.fake.answer = 503  # the install and the month wait
        self.u.tick(T)
        self.u.count(now=T, transcriptions=1, seconds=60, cohere=1)
        self.assertEqual(len(self.state()["queue"]), 2)
        before = self.cfg.settings()
        self.cfg.save_settings(usage_counts=False)
        self.u.settings_changed(before, self.cfg.settings())
        self.assertEqual(self.state(), {"install": "2026-10-28", "month": "2026-10", "weeks": {}, "queue": []},
                         "the counts and the events waiting are deleted; the flags stay")
        self.fake.answer, self.fake.requests[:] = "ok", []
        self.assertQuiet(self.u)
        self.assertEqual(self.u.summary(), {"available": True, "on": False, "ask": False, "blocked": None})
        before = self.cfg.settings()
        self.cfg.save_settings(usage_counts=True)
        self.u.settings_changed(before, self.cfg.settings())
        self.assertTrue(self.u.wake.is_set(), "turned on again: sent soon")
        self.u.tick(T + 2 * HOUR)
        self.assertEqual(self.fake.names(), [], "not the install or this month a second time")

    def test_a_setting_changed_by_hand_while_it_runs(self):
        self.u.count(now=T, transcriptions=1)
        (self.home / "app_data" / "settings.json").write_text(json.dumps({"usage_counts": False}), encoding="utf-8")
        self.assertFalse(self.u.tick(T))
        self.assertEqual(self.state()["weeks"], {})

    def test_the_shipped_key_sends_to_the_eu_region(self):
        self.config("")  # nothing in the data folder's config.toml: app/config.toml's key applies
        self.assertEqual(self.usage().url(), "https://eu.aptabase.com/api/v0/events")

    def test_without_a_key(self):
        for toml in ('[usage]\napp_key = ""\n', '[usage]\napp_key = "A-SH-1234567890"\n',
                     '[usage]\napp_key = "A-SH-1234567890"\nhost = "http://analytics.example.com"\n',
                     '[usage]\napp_key = "B-EU-1234567890"\n', '[usage]\napp_key = "A-EU-12345 67890"\n',
                     '[usage]\napp_key = "A-EU-"\n'):
            with self.subTest(toml):
                shutil.rmtree(self.home / "app_data")
                (self.home / "app_data").mkdir()
                self.config(toml)
                self.cfg.save_settings(usage_counts=True)
                u = self.usage()
                self.assertIsNone(u.url())
                self.assertEqual(u.summary(), {"available": False, "on": True, "ask": False, "blocked": None})
                self.assertQuiet(u)
                u.start()
                self.assertIsNone(u.thread, "nothing runs without a key")
                self.assertFalse((self.home / "app_data" / "usage.json").exists(), "and nothing else changes")

    def test_the_keys_and_their_hosts(self):
        for toml, url in (('app_key = "A-EU-1234567890"', "https://eu.aptabase.com/api/v0/events"),
                          ('app_key = "A-US-1234567890"', "https://us.aptabase.com/api/v0/events"),
                          ('app_key = "A-EU-1234567890"\nhost = "https://elsewhere.example.com"', "https://eu.aptabase.com/api/v0/events"),
                          ('app_key = "A-SH-1234567890"\nhost = "https://analytics.example.com/"', "https://analytics.example.com/api/v0/events")):
            self.config(f"[usage]\n{toml}\n")
            self.assertEqual(self.usage().url(), url)

    def test_the_environment_turns_them_off(self):
        for var in usage.OPT_OUT:
            for value in ("1", "true", "yes"):
                with self.subTest(var=var, value=value), mock.patch.dict(os.environ, {var: value}):
                    u = self.usage()
                    self.assertEqual(u.summary(), {"available": True, "on": False, "ask": False, "blocked": var})
                    self.assertFalse(u.allowed())
                    self.assertQuiet(u)
        for value in ("0", "false", "", " "):
            with mock.patch.dict(os.environ, {"DO_NOT_TRACK": value, "SEDJEM_NO_USAGE": value}):
                self.assertIsNone(self.usage().blocked(), repr(value))
        self.u.count(now=T, transcriptions=1)
        with mock.patch.dict(os.environ, {"DO_NOT_TRACK": "1"}):
            self.u.tick(T)
        self.assertEqual(self.state()["weeks"], {}, "set while counts were kept: they go")


class TestsNeverSend(unittest.TestCase):
    """Without replacing test_run(), as every other test of the app runs."""

    def setUp(self):
        clean_environment(self)
        self.home = Path(tempfile.mkdtemp(prefix="sedjem-usage-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        (self.home / "app_data").mkdir()
        (self.home / "app_data" / "config.toml").write_text('[usage]\napp_key = "A-EU-1234567890"\n', encoding="utf-8")
        self.cfg = Config(home=self.home)
        self.cfg.save_settings(usage_counts=True)
        self.connections = []
        real = socket.create_connection

        def record(address, *args, **kwargs):  # every connection the code under test opens, refused if not here
            self.connections.append(address)
            if address[0] not in ("127.0.0.1", "localhost", "::1"):
                raise AssertionError(f"a test tried to reach {address}")
            return real(address, *args, **kwargs)
        guard = mock.patch.object(socket, "create_connection", record)
        guard.start()
        self.addCleanup(guard.stop)

    def test_the_tests_never_count_or_send(self):
        self.assertTrue(usage.test_run())
        u = usage.Usage(self.cfg, version=lambda: "0.3.0", facts=lambda: FACTS)
        self.assertEqual(u.url(), "https://eu.aptabase.com/api/v0/events")
        self.assertFalse(u.allowed(), "a key and the switch on, but the tests run")
        u.job_ended(job())
        self.assertFalse(u.tick(T))
        self.assertFalse((self.home / "app_data" / "usage.json").exists())
        self.assertEqual(self.connections, [])

    def test_even_a_test_that_lets_them_through_reaches_only_this_computer(self):
        with mock.patch.object(usage, "test_run", return_value=False):
            u = usage.Usage(self.cfg, version=lambda: "0.3.0", facts=lambda: FACTS)
            self.assertTrue(u.allowed())
            self.assertFalse(u.tick(T) and self.connections)
            self.assertEqual(len(u.data["queue"]), 2, "kept, as if offline")
        self.assertEqual(usage.post("https://eu.aptabase.com/api/v0/events", "A-EU-1234567890", "x", []), (None, None))
        self.assertEqual(self.connections, [])

    def test_servers_made_in_the_tests_never_send(self):
        with mock.patch.object(engines, "gpu_info", return_value=GPU):
            server, app = make_server(self.cfg, port=0)
            while app.gpu_probing:  # the check runs under the stand-in, not transcribe.py
                time.sleep(0.01)
        try:
            self.assertIsNotNone(app.usage.url())
            self.assertIsNone(app.usage.thread, "only the app's entry points start the sending")
        finally:
            server.server_close()

    def test_the_app_never_loads_unittest(self):
        """test_run() tells the tests from the app by unittest (or pytest) being loaded: the app must never load
        them, or it would stop sending its counts."""
        code = ("import sys; import app.server, app.desktop, app.__main__, app.jobs, app.mcp, app.selftest, app.usage; "
                "print(sorted(m for m in ('unittest', 'pytest') if m in sys.modules))")
        p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120,
                           env={**os.environ, "PYTHONPATH": str(ROOT)})
        self.assertEqual(p.returncode, 0, p.stderr[-2000:])
        self.assertEqual(p.stdout.strip().splitlines()[-1], "[]")

    def test_the_entry_points_start_the_sending(self):
        for path in (ROOT / "app" / "__main__.py", ROOT / "app" / "desktop.py"):
            self.assertIn("app.usage.start()", path.read_text(encoding="utf-8"), path.name)
        self.assertNotIn("usage.start()", (ROOT / "app" / "server.py").read_text(encoding="utf-8"))


class Sending(UsageCase):
    def test_accepted(self):
        self.assertTrue(self.u.tick(T))
        self.assertEqual(self.fake.names(), ["install", "active_month"])
        self.assertEqual(self.state()["queue"], [])
        self.assertFalse(self.u.tick(T + HOUR), "nothing waits")

    def test_a_key_aptabase_doesnt_take(self):
        self.fake.answer = "unknown key"  # 200 with an error: a key it doesn't know, or a locked account
        self.u.tick(T)
        self.assertEqual(self.state()["queue"], [], "dropped, not tried again")
        self.u.tick(T + 2 * HOUR)
        self.assertEqual(len(self.fake.requests), 1)

    def test_a_bad_request_is_dropped(self):
        for answer in (400, 404, 401):
            with self.subTest(answer):
                self.u.count(now=T - 7 * DAY, txt=1)
                self.fake.answer, self.fake.requests[:] = answer, []
                self.u.tick(T)
                self.assertEqual((len(self.fake.requests), self.state()["queue"]), (1, []))

    def test_busy_or_offline_is_tried_again_an_hour_later(self):
        for answer in (408, 429, 500, 502, 503):
            with self.subTest(answer):
                shutil.rmtree(self.home / "app_data")
                (self.home / "app_data").mkdir()
                self.config(f'[usage]\napp_key = "{KEY}"\nhost = "{self.fake.url}"\n')
                self.cfg.save_settings(usage_counts=True)
                u = self.usage()
                self.fake.answer, self.fake.requests[:] = answer, []
                self.assertTrue(u.tick(T))
                self.assertEqual(len(self.state()["queue"]), 2)
                self.assertFalse(u.tick(T + HOUR - 60), "not within the hour")
                self.fake.answer = "ok"
                self.assertTrue(u.tick(T + HOUR))
                self.assertEqual(self.state()["queue"], [])
                self.assertEqual(len(self.fake.requests), 2)
                self.assertEqual(self.fake.requests[0][2], self.fake.requests[1][2], "the same events, as made")

    def test_new_events_are_sent_at_once_even_after_a_failure(self):
        self.fake.answer = 503
        self.u.tick(T)
        self.fake.answer = "ok"
        self.u.count(now=T - 7 * DAY, compares=1)  # a week that has ended: its summary is new
        self.assertTrue(self.u.tick(T + 60))
        self.assertEqual(self.fake.names(), ["install", "active_month", "install", "active_month", "week_summary"])
        self.assertEqual(self.state()["queue"], [])

    def test_a_timeout(self):
        self.fake.answer = "slow"
        with mock.patch.object(usage, "TIMEOUT", 0.3):
            started = time.monotonic()
            self.assertTrue(self.u.tick(T))
            self.assertLess(time.monotonic() - started, 1.4, "the sender gave up")
        self.assertEqual(len(self.state()["queue"]), 2, "kept, to try again")

    def test_no_connection(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            closed = s.getsockname()[1]  # nothing listens there once it is closed
        self.config(f'[usage]\napp_key = "{KEY}"\nhost = "http://127.0.0.1:{closed}"\n')
        u = self.usage()
        self.assertTrue(u.tick(T))
        self.assertEqual(len(self.state()["queue"]), 2)
        self.assertIsNotNone(u.failed)

    def test_a_redirect_isnt_followed(self):
        self.fake.answer = "redirect"
        self.u.tick(T)
        self.assertEqual([r[0] for r in self.fake.requests], ["/api/v0/events"], "nothing sent to /elsewhere")
        self.assertEqual(self.state()["queue"], [])

    def test_events_a_day_old_are_dropped(self):
        self.fake.answer = 503
        self.u.tick(T)
        self.u.tick(T + 23 * HOUR)
        self.assertEqual(len(self.fake.requests), 2, "tried again within the day")
        self.fake.answer = "ok"
        self.assertFalse(self.u.tick(T + 24 * HOUR + 60), "older than a day: Aptabase wouldn't take them")
        self.assertEqual(len(self.fake.requests), 2)
        self.assertEqual(self.state()["queue"], [])
        self.assertEqual((self.state()["install"], self.state()["month"]), ("2026-10-28", "2026-10"), "not made again")

    def test_at_most_25_events_a_request(self):
        for weeks_ago in range(1, 31):
            self.u.count(now=T - weeks_ago * 7 * DAY, compares=1)
        self.u.tick(T)
        self.assertEqual([len(events) for _, _, events in self.fake.requests], [25, 7])
        self.assertEqual(self.state()["queue"], [])

    def test_it_never_raises(self):
        def broken():
            raise RuntimeError("the GPU check broke")
        u = usage.Usage(self.cfg, version=lambda: "0.3.0", facts=broken)
        self.assertTrue(u.tick(T))
        self.assertEqual(self.fake.names(), ["install"], "the month waits for the next look")
        u.facts = lambda: FACTS
        u.tick(T + 60)
        self.assertEqual(self.fake.names(), ["install", "active_month"])
        self.assertEqual(usage.post("http://127.0.0.1:1/api/v0/events", KEY, "x", []), (None, None))
        with mock.patch.object(usage, "replace_file", side_effect=PermissionError("locked")):
            self.u.count(now=T, txt=1)  # usage.json can't be written: kept in memory
        self.assertEqual(self.u.data["weeks"]["2026-W44"], {"txt": 1})
        self.u.count(now=T, **{"no such count": 1, "txt": 1})
        self.assertEqual(self.u.data["weeks"]["2026-W44"], {"txt": 2})

    def test_debug_events(self):
        with mock.patch.dict(os.environ, {"SEDJEM_USAGE_DEBUG": "1"}):
            self.u.tick(T)
        self.assertTrue(all(e["systemProps"]["isDebug"] is True for e in self.fake.events()))

    def test_the_thread(self):
        with mock.patch.object(usage, "START_DELAY", 0):
            self.u.start()
            self.u.start()  # once
            deadline = time.monotonic() + 10
            while len(self.fake.requests) < 1 and time.monotonic() < deadline:
                time.sleep(0.05)
        self.assertEqual(self.fake.names(), ["install", "active_month"])
        self.assertEqual([t.name for t in threading.enumerate()].count("sedjem-usage"), 1)


class AppCounts(UsageCase):
    """The counting points of the app, through its HTTP API and its runner."""
    A, B = "20261020-100000-a001", "20261020-110000-a002"

    def setUp(self):
        super().setUp()
        demo_job(self.home, self.A)
        demo_job(self.home, self.B, model="whisper-medium", model_title="whisper-medium", rerun_of=self.A)
        gpu = mock.patch.object(engines, "gpu_info", return_value=GPU)
        gpu.start()
        self.addCleanup(gpu.stop)
        self.server, self.app = make_server(self.cfg, port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                     headers={"X-Sedjem": "1", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return r.status, json.loads(raw) if raw and method != "HEAD" and "json" in r.headers.get("Content-Type", "") else raw
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def counts(self):
        return self.app.usage.data["weeks"].get(usage.week_of(time.time()), {})

    def test_the_status_and_the_switch(self):
        status, st = self.call("GET", "/api/status")
        self.assertEqual(st["usage"], {"available": True, "on": True, "ask": False, "blocked": None})
        self.assertIs(st["settings"]["usage_counts"], True)
        status, st = self.call("POST", "/api/settings", {"usage_counts": None})
        self.assertEqual(st["usage"], {"available": True, "on": True, "ask": True, "blocked": None})
        self.app.usage.count(transcriptions=1)  # not allowed now: not counted
        self.assertEqual(self.counts(), {})
        status, st = self.call("POST", "/api/settings", {"usage_counts": True})
        self.assertTrue(self.app.usage.wake.is_set())
        self.app.usage.count(transcriptions=1)
        status, st = self.call("POST", "/api/settings", {"usage_counts": False})
        self.assertEqual((status, st["usage"]["on"], self.app.usage.data["weeks"]), (200, False, {}))
        for bad in ("yes", 1, "true"):
            self.assertEqual(self.call("POST", "/api/settings", {"usage_counts": bad})[0], 400)
        self.assertIs(self.cfg.setting("usage_counts"), False)

    def test_exports_are_counted(self):
        for path in (f"/api/jobs/{self.A}/export/srt", f"/api/jobs/{self.A}/export/srt?version=0",
                     f"/api/jobs/{self.A}/export/md", f"/api/jobs/{self.A}/export/txt?details=0"):
            self.assertEqual(self.call("GET", path)[0], 200)
        self.assertEqual(self.call("HEAD", f"/api/jobs/{self.A}/export/json")[0], 200)
        self.assertEqual(self.call("GET", f"/api/jobs/{self.A}/export/docx")[0], 404)
        self.assertEqual(self.counts(), {"srt": 2, "md": 1}, "copying the text (details=0) isn't an export")

        path = self.home / "saved.json"
        window = mock.Mock(create_file_dialog=mock.Mock(return_value=[str(path)]))
        api = desktop.Api(self.app)
        api._window = window
        with mock.patch.object(desktop.Api, "_dialog", return_value="save"):
            self.assertEqual(api.save_export(self.A, "json"), str(path))
        self.assertEqual(self.counts(), {"srt": 2, "md": 1, "json": 1}, "the desktop app's save dialog too")

    def test_the_compare_view_is_counted(self):
        self.assertEqual(self.call("GET", f"/api/compare?ids={self.A},{self.B}")[0], 200)
        self.assertEqual(self.call("GET", f"/api/compare?ids={self.A},{self.B}&base={self.B}")[0], 200)
        self.assertEqual(self.call("GET", f"/api/compare?ids={self.A},nope")[0], 404)
        self.assertEqual(self.counts(), {"compares": 1}, "opened once; another base isn't another compare")

    def test_the_end_of_a_run_is_counted(self):
        runner, store = self.app.runner, self.app.store
        model = "elevenlabs"
        lines = [{"start": 0.0, "end": 2.0, "speaker": "1", "text": "Hello"}]
        new = runner.new_job(title="A call", source_name="call.m4a", model_id=model,
                             options={"speakers": "auto", "language": "ar", "prompt": ""})
        store.update(new["id"], status="queued", audio_s=95.0)
        with mock.patch.object(engines, "run_hosted", return_value={"lines": lines, "meta": {}}):
            runner.work(new["id"], "hosted")
        self.assertEqual(store.get(new["id"])["status"], "done")
        failed = runner.new_job(title="Another", source_name="b.m4a", model_id=model, options={"speakers": "none"})
        store.update(failed["id"], status="queued", audio_s=30.0)
        with mock.patch.object(engines, "run_hosted", side_effect=engines.EngineError("the service said no")):
            runner.work(failed["id"], "hosted")
        yt = runner.new_job(title="A lecture", source_name="lecture.mp4", model_id="cohere", options={"speakers": "none"},
                            source_url=youtube.watch_url("dQw4w9WgXcQ"), youtube_id="dQw4w9WgXcQ")
        with mock.patch.object(youtube, "download", side_effect=youtube.YouTubeError("This video is private")):
            runner.download(yt["id"], "download")
        self.assertEqual(store.get(yt["id"])["status"], "failed")
        self.assertEqual(self.counts(), {"transcriptions": 1, "seconds": 95.0, "hosted": 1, "speakers": 1, "failed": 2})


# ---------------------------------------------------------------------------------------------
# The public totals (tools/usage_totals.py)
# ---------------------------------------------------------------------------------------------

HEADER = ["timestamp", "user_id", "session_id", "event_name", "string_props", "numeric_props", "os_name", "os_version",
          "locale", "app_version", "app_build_number", "engine_name", "engine_version", "country_code", "country_name",
          "region_name"]


def row(event, country="EG", month="2026-11", strings=None, numbers=None, os_name="Windows", os_version="10.0.26100",
        version="0.3.0"):
    """A row of Aptabase's export, as its CSV has them."""
    return {"timestamp": f"{month}-03 10:15:00", "user_id": "9F86D081884C7D65", "session_id": "176174659212345678",
            "event_name": event, "string_props": json.dumps(strings or {}), "numeric_props": json.dumps(numbers or {}),
            "os_name": os_name, "os_version": os_version, "locale": "ar-eg", "app_version": version,
            "app_build_number": "", "engine_name": "python", "engine_version": "3.12.15", "country_code": country,
            "country_name": "", "region_name": "Cairo"}


def week(**numbers):
    return {"numbers": {**dict.fromkeys(usage.SUMMARY, 0), **numbers}, "strings": {"week": "2026-W45"}}


def export(rows, header=HEADER, quotes=False):
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=header, extrasaction="ignore", lineterminator="\n")
    w.writerow({h: h for h in header})
    for r in rows:
        if quotes:  # the export may swap the double quotes of the props for single ones
            r = {**r, "string_props": r["string_props"].replace('"', "'"), "numeric_props": r["numeric_props"].replace('"', "'")}
        w.writerow(r)
    return out.getvalue()


def month_rows():
    """November 2026: 4 installs active in Egypt, 3 in Saudi Arabia, 2 in the Emirates, 1 in the US and 1 with no
    country; 5 new installs; three weeks of use. And what must be left out: another month, a stranger's event and
    week summaries with numbers Sedjem never sends."""
    active = lambda country, **kw: row("active_month", country, strings={**FACTS, **kw.pop("strings", {})}, **kw)  # noqa: E731
    rows = [active("EG") for _ in range(3)] + [active("EG", os_name="Linux", os_version="Ubuntu 24.04.4 LTS")]
    rows += [active("SA", strings={"gpu": "nvidia"}) for _ in range(3)] + [active("AE") for _ in range(2)]
    rows += [active("US", version="0.3.1-preview.20261101"), active("", strings={"gpu": "Hacked GPU"})]
    rows += [row("install", c) for c in ("EG", "EG", "SA", "AE", "US")]
    for numbers in ({"transcriptions": 10, "minutes": 300, "cohere": 6, "whisper_medium": 2, "hosted": 2, "speakers": 7,
                     "txt": 3, "srt": 1, "youtube": 2},
                    {"transcriptions": 4, "minutes": 125, "whisper_large": 1, "other_local": 1, "hosted": 2, "failed": 1,
                     "compares": 2, "nemotron": 1, "md": 2},
                    {"transcriptions": 1, "minutes": 15, "cohere": 1, "json": 1}):
        w = week(**numbers)
        rows.append(row("week_summary", strings=w["strings"], numbers=w["numbers"]))
    for bad in ({"minutes": -5}, {"transcriptions": 2.5}, {"txt": 10 ** 9}, {"transcriptions": "many"}):
        w = week(**bad)
        rows.append(row("week_summary", strings=w["strings"], numbers=w["numbers"]))
    rows.append(row("week_summary", strings={"week": "last week"}, numbers=week()["numbers"]))
    rows += [row("active_month", "EG", month="2026-10", strings=FACTS), row("page_view", "EG")]
    return rows


class Totals(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sedjem-totals-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, text, name="sedjem-release-2026-11-01.csv"):
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_the_totals_of_a_month(self):
        rows = totals.read(self.write(export(month_rows())))
        t = totals.totals(rows, totals.pick_month(rows, "2026-11"), "2026-12-01")
        self.assertEqual((t["month"], t["exported"], t["active_installs"], t["new_installs"]), ("2026-11", "2026-12-01", 11, 5))
        self.assertEqual(t["countries"], {"count": 4, "installs": {"EG": 4, "SA": 3, "other": 4}},
                         "the Emirates (2), the US (1) and no country (1) are folded: fewer than 3 installs")
        self.assertEqual(t["systems"], {"Windows": 10, "other": 1})
        self.assertEqual(t["system_versions"], {"Windows 10.0.26100": 10, "other": 1})
        self.assertEqual(t["versions"], {"0.3.0": 10, "other": 1})
        self.assertEqual(t["installs"]["gpu"], {"intel": 7, "nvidia": 3, "other": 1}, "an unknown value isn't published")
        self.assertEqual(t["installs"]["ui"], {"desktop": 11})
        self.assertEqual((t["weeks"], t["transcriptions"], t["hours"], t["skipped"]), (3, 15, 7.3, 5))
        self.assertEqual(t["models"], {"cohere": 7, "whisper_medium": 2, "whisper_large": 1, "other_local": 1, "hosted": 4})
        self.assertEqual(t["hosted_share"], round(4 / 15, 3))
        self.assertEqual((t["speakers"], t["nemotron"], t["youtube"], t["compares"], t["failed"]), (7, 1, 2, 2, 1))
        self.assertEqual(t["exports"], {"txt": 3, "srt": 1, "vtt": 0, "md": 2, "json": 1})
        published = json.dumps(t)
        for private in ("9F86D081884C7D65", "176174659212345678", "Cairo", "AE", "US", "Hacked", "2026-W45"):
            self.assertNotIn(private, published)

    def test_a_tolerant_reader(self):
        shuffled = ["Country_Code", "Event-Name", "os name", "OS_VERSION", "AppVersion", "Numeric_Props", "Timestamp",
                    "String_Props", "Region_Name"]
        text = export(month_rows(), header=[totals.column_key(h) for h in shuffled], quotes=True)
        text = ",".join(shuffled) + text[text.index("\n"):]
        rows = totals.read(self.write("\ufeff" + text))
        t = totals.totals(rows, "2026-11", "2026-12-01")
        self.assertEqual((t["active_installs"], t["transcriptions"], t["countries"]["installs"]["EG"]), (11, 15, 4))
        one = [{**{k: v for k, v in r.items() if not k.endswith("_props")},
                "props": json.dumps({**json.loads(r["string_props"]), **json.loads(r["numeric_props"])})} for r in month_rows()]
        header = [h for h in HEADER if not h.endswith("_props")] + ["props"]
        self.assertEqual(totals.totals(totals.read(self.write(export(one, header=header), "one.csv")), "2026-11", "x")["transcriptions"], 15)

    def test_a_missing_column_is_named(self):
        for missing, words in (("country_code", "no country column (looked for country_code or countrycode)"),
                               ("numeric_props", "no props column (looked for string_props and numeric_props, or props)"),
                               ("event_name", "no event column")):
            with self.subTest(missing), self.assertRaises(totals.ExportError) as e:
                totals.read(self.write(export(month_rows(), header=[h for h in HEADER if h != missing])))
            self.assertIn(words, str(e.exception))
            self.assertIn("its columns are: timestamp", str(e.exception))
        with self.assertRaises(totals.ExportError) as e:
            totals.read(self.write(""))
        self.assertIn("is empty", str(e.exception))

    def test_the_month(self):
        rows = totals.read(self.write(export(month_rows())))
        with self.assertRaises(totals.ExportError) as e:
            totals.pick_month(rows)
        self.assertIn("2026-10, 2026-11; choose one with --month", str(e.exception))
        self.assertEqual(totals.pick_month([r for r in rows if r["month"] == "2026-11"]), "2026-11")
        with self.assertRaises(totals.ExportError):
            totals.pick_month(rows, "2026-12")

    def test_writing_the_totals(self):
        root = self.tmp / "repo"
        (root / "docs").mkdir(parents=True)
        for page in ("README.md", "docs/13-usage-counts.md"):
            shutil.copy(ROOT / page, root / page)
        path = self.write(export(month_rows()))
        with mock.patch("sys.stdout", io.StringIO()) as out:
            totals.main([str(path), "--month", "2026-11", "--exported", "2026-12-01", "--root", str(root)])
        self.assertIn("November 2026: 11 active installs, 5 new, 4 countries, 15 transcriptions, 7.3 hours", out.getvalue())
        data = json.loads((root / "docs" / "usage.json").read_text(encoding="utf-8"))
        self.assertEqual(list(data["months"]), ["2026-11"])
        readme = (root / "README.md").read_text(encoding="utf-8")
        self.assertIn("In November 2026, 11 installs ran Sedjem, in 4 countries, and 5 were new. They made 15 "
                      "transcriptions of 7.3 hours of audio, 27% of them with hosted services.", " ".join(readme.split()))
        self.assertNotIn("No totals yet", readme)
        docs = (root / "docs" / "13-usage-counts.md").read_text(encoding="utf-8")
        self.assertIn("| November 2026 | 11 | 5 | 4 | 15 | 7.3 | 27% |", docs)

        december = [{**r, "timestamp": "2026-12-04 08:00:00"} for r in month_rows() if r["timestamp"].startswith("2026-11")][:3]
        with mock.patch("sys.stdout", io.StringIO()):
            totals.main([str(self.write(export(december), "december.csv")), "--exported", "2027-01-02", "--root", str(root)])
            totals.main([str(path), "--month", "2026-11", "--exported", "2026-12-02", "--root", str(root)])  # again
        data = json.loads((root / "docs" / "usage.json").read_text(encoding="utf-8"))
        self.assertEqual(list(data["months"]), ["2026-11", "2026-12"])
        self.assertEqual(data["months"]["2026-11"]["exported"], "2026-12-02", "a month exported again is replaced")
        docs = (root / "docs" / "13-usage-counts.md").read_text(encoding="utf-8")
        self.assertLess(docs.index("| December 2026 |"), docs.index("| November 2026 |"), "newest first")
        self.assertEqual(docs.count(totals.START), 1)
        self.assertIn("In December 2026", (root / "README.md").read_text(encoding="utf-8"))

        (root / "README.md").write_text("no markers here\n", encoding="utf-8")
        with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as e:
            totals.main([str(path), "--month", "2026-11", "--root", str(root)])
        self.assertIn("README.md has no <!-- usage-totals:start -->", str(e.exception.code))
        for bad in (["--month", "November"], ["--exported", "yesterday"]):
            with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                totals.main([str(path), "--root", str(root), *bad])

    def test_the_pages_have_their_place_for_the_totals(self):
        for page in ("README.md", "docs/13-usage-counts.md"):
            text = (ROOT / page).read_text(encoding="utf-8")
            self.assertEqual((text.count(totals.START), text.count(totals.END)), (1, 1), page)
            self.assertLess(text.index(totals.START), text.index(totals.END))


# ---------------------------------------------------------------------------------------------
# The docs say what the code does
# ---------------------------------------------------------------------------------------------

class Docs(unittest.TestCase):
    page = (ROOT / "docs" / "13-usage-counts.md").read_text(encoding="utf-8")

    def section(self, heading):
        return self.page.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]

    def test_every_field_and_value_sent_is_listed(self):
        held = self.section("What each event holds")
        named = set(re.findall(r"`([^`]+)`", held))
        self.assertTrue(set(usage.SYSTEM) <= named, set(usage.SYSTEM) - named)
        self.assertTrue(set(usage.VALUES) <= named)
        self.assertTrue({v for values in usage.VALUES.values() for v in values} <= named)
        self.assertTrue(set(usage.SUMMARY) | {"week"} <= named, set(usage.SUMMARY) - named)
        self.assertIn(f"`{usage.SDK}`", held)
        when = self.section("When something is sent")
        self.assertEqual(re.findall(r"^\| `(\w+)` \|", when, re.M), ["install", "active_month", "week_summary"])

    def test_nothing_is_listed_that_isnt_sent(self):
        held = self.section("What each event holds")
        rows = re.findall(r"^\| (`[^|]+`(?:, `[^|]+`)*) \|", held, re.M)
        listed = {name for cell in rows for name in re.findall(r"`([^`]+)`", cell)}
        sent = set(usage.SYSTEM) | set(usage.VALUES) | set(usage.SUMMARY)
        self.assertEqual(listed, sent)

    def test_the_numbers_in_the_text(self):
        self.assertEqual((usage.START_DELAY, usage.WAKE, usage.RETRY, usage.MAX_AGE, usage.BATCH, usage.TIMEOUT),
                         (20, 900, 3600, 86400, 25, 10), "the docs say so: twenty seconds, fifteen minutes, an hour, "
                                                          "a day, 25 in one request, 10 seconds")
        for words in ("about twenty seconds after it starts and then every fifteen minutes", "up to 25 in one request",
                      "after 10 seconds", "tried again an hour later", "dropped once they are a day old",
                      "`DO_NOT_TRACK=1`", "`SEDJEM_NO_USAGE=1`", "*Settings → About*", "at least three installs",
                      "https://aptabase.com/legal/privacy", "https://aptabase.com/legal/dpa", "up to five years",
                      "Germany", "article 28"):
            self.assertIn(words, " ".join(self.page.split()))
        self.assertEqual(totals.LEAST, 3)

    def test_the_promise_says_where_and_how_to_turn_it_off(self):
        readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
        self.assertIn("GitHub to check for updates when allowed, and Aptabase for the usage counts below", readme)
        self.assertIn("*Settings → About* or `DO_NOT_TRACK=1` turns them off. See [usage counts](docs/13-usage-counts.md)", readme)
        app = " ".join((ROOT / "docs" / "09-app.md").read_text(encoding="utf-8").split())
        privacy = app.split("## Privacy", 1)[1].split("## ", 1)[0]
        for words in ("Aptabase", "EU region", "`DO_NOT_TRACK=1`", "`SEDJEM_NO_USAGE=1`", "*Settings → About*", "13-usage-counts.md"):
            self.assertIn(words, privacy)
        self.assertNotIn("With local models, nothing leaves the computer", app)
        self.assertIn("[Usage counts](13-usage-counts.md)", (ROOT / "docs" / "README.md").read_text(encoding="utf-8"))

    def test_plain_style(self):
        for name in ("docs/13-usage-counts.md", "README.md", "docs/09-app.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("—", text, name)
        self.assertNotIn("**", self.page)


if __name__ == "__main__":
    unittest.main()
