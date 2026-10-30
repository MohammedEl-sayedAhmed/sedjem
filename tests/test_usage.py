"""Tests for the anonymous usage counts (app/usage.py) and their public totals (tools/usage_totals.py).

The three events carry only the fields and values that may be sent, with the right types, and none of a
recording's names, paths, links or text, nor an API key or what an assistant asked; every prop of active_month and
week_summary is tried. install, active_month and week_summary are each sent once for their period (months and weeks
in UTC), and say which month or week they count; an event gets its time when it is sent. Every counting point is
tried: the end of a run (by model, service, speaker model, length, language and device), a failure at each stage, a
cancel, the starts and the days of use, the assistants' log, an export and a copy, the compare view, edits,
branches, restores, combines, YouTube courses, downloads and Update now. Nothing is counted or sent before the
first-start dialog, with the switch off, without a key, with DO_NOT_TRACK or SEDJEM_NO_USAGE, or while the tests run.
The sender is tried against a stand-in for Aptabase on 127.0.0.1: accepted, a key Aptabase doesn't take, 400, 408,
429, 5xx, a timeout, no connection, a redirect, events that waited for days, batches of 25, at most 100 waiting.
The totals and KPIs are made from made-up exports, with the three-install threshold, and so are the website's, with
the three-visitor threshold for countries and sites; the website reads back what the script writes. The docs must list
exactly what is sent, and the totals script must know the same lists as the app and the website.

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
import tomllib
import unittest
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

try:
    import yaml
except ImportError:  # the workflow check is then skipped
    yaml = None

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import assistants, desktop, engines, updates, usage, youtube  # noqa: E402
from app.config import Config  # noqa: E402
from app.downloads import Downloads  # noqa: E402
from app.server import make_server  # noqa: E402
from ui_support import demo_job  # noqa: E402

spec = importlib.util.spec_from_file_location("usage_totals", ROOT / "tools" / "usage_totals.py")
assert spec and spec.loader
totals = importlib.util.module_from_spec(spec)
spec.loader.exec_module(totals)

KEY = "A-SH-1234567890"
KEY_ENVS = [m["key_env"] for m in tomllib.loads((ROOT / "app" / "config.toml").read_text(encoding="utf-8"))["models"]
            if m.get("key_env")]  # the hosted services' API keys in the environment


def at(text):
    """A moment as seconds since 1970, from an ISO 8601 time."""
    return datetime.fromisoformat(text).timestamp()


T = at("2026-10-28T12:00:00+00:00")  # a Wednesday, in ISO week 2026-W44
NEXT_WEEK = at("2026-11-02T09:00:00+00:00")  # Monday of 2026-W45, and a new month
HOUR, DAY = 3600, 86400
GPU = {"devices": [{"name": "Intel(R) Iris(R) Xe Graphics", "kind": "vulkan", "type": "igpu", "memory": 0}],
       "cuda_devices": 0, "cuda_libs": None}
MACHINE = {"ram_gb": 15.4, "threads": 12, "cpu": "12th Gen Intel(R) Core(TM) i5-1245U", "arch": "x86_64"}
# active_month's props but the month and the install's age, as the app's facts() gives them
FACTS = {"kind": "source", "ui": "desktop", "cpu": "intel", "gpu": "intel", "gpu_vram": "none", "cuda": "no",
         "vulkan": "yes", "ram": "8-16", "cores": "8-16", "power": "balanced", "disk_free": "50-200",
         "channel": "stable", "update_check": "on", "language": "ar", "speaker_model": "titanet_small",
         "device": "auto", "mcp_write": "off", "library": "10-49", "courses": "1-4",
         **dict.fromkeys(usage.FLAGS, 0), "has_cohere": 1, "key_elevenlabs": 1}
# What a transcription holds that must never leave the computer
SECRETS = {"title": "Board meeting about the salaries", "source_name": "ceo-private-call.m4a",
           "source_path": "/home/alice/private/ceo-private-call.m4a", "youtube_id": "dQw4w9WgXcQ",
           "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "speaker_names": {"1": "Alice Example"},
           "error": "Traceback: /home/alice/private failed", "voiceprint_model": "Nemotron-3-Diarization"}
LINE_TEXT = "the password is hunter2-secret"
API_KEY = "sk-live-hunter2-do-not-send"


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

    def props(self, name):
        return [e["props"] for e in self.events() if e["eventName"] == name]


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
    for var in ("DO_NOT_TRACK", "SEDJEM_NO_USAGE", "SEDJEM_USAGE_DEBUG", *KEY_ENVS):
        os.environ.pop(var, None)


def job(**fields):
    """A finished local run of Cohere on a graphics card, as the runner hands it over, with what must never be
    sent."""
    return {"id": "20261028-120000-a001", "status": "done", "kind": "local", "model": "cohere", "audio_s": 125.4,
            "seconds": 31.2, "device": "vulkan: Intel(R) Iris(R) Xe Graphics", "options":
            {"speakers": "auto", "language": "ar", "prompt": ""}, **SECRETS, **fields}


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
        u = usage.Usage(self.cfg, version=lambda: "0.3.1", facts=lambda: dict(FACTS))
        u.clock = lambda: now
        return u

    def state(self):
        return json.loads((self.home / "app_data" / "usage.json").read_text(encoding="utf-8"))

    def week(self, now=T):
        """What usage.json keeps for the week of `now`."""
        return self.u.data["weeks"].get(usage.week_of(now), {})

    def summary(self, at_time=NEXT_WEEK):
        """The week_summary sent for the week before at_time."""
        self.u.tick(at_time)
        return self.fake.props("week_summary")[-1]


class Events(UsageCase):
    def test_the_events_carry_only_what_may_be_sent(self):
        self.cfg.save_key("elevenlabs", API_KEY)
        self.u.job_ended(job(), [{"start": 0, "end": 2, "speaker": "1", "text": LINE_TEXT}])
        self.u.job_ended(job(status="failed"), stage="model")
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
            self.assertEqual(headers["user-agent"], f"{s['osName']}/{s['osVersion']} Sedjem/0.3.1 {s['locale']}".strip())
        allowed_strings = {"install", "active_month", "week_summary", "2026-W44", "2026-10", "2026-11", "python",
                           usage.SDK, "0.3.1", *(v for values in usage.VALUES.values() for v in values)}
        for e in self.fake.events():
            self.assertEqual(set(e), {"timestamp", "sessionId", "eventName", "systemProps", "props"})
            self.assertRegex(e["timestamp"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.000Z$")
            self.assertRegex(e["sessionId"], r"^\d{18}$")
            s = e["systemProps"]
            self.assertEqual(set(s), {"isDebug", "osName", "osVersion", "locale", "appVersion", "engineName",
                                      "engineVersion", "sdkVersion"}, "no device model, no build number")
            self.assertIs(s["isDebug"], False)
            self.assertTrue(all(isinstance(v, str) for k, v in s.items() if k != "isDebug"))
            self.assertRegex(s["locale"], r"^([a-z]{2}(-[A-Z]{2})?)?$")
            strings = [v for v in e["props"].values() if isinstance(v, str)]
            self.assertTrue(set(strings) <= allowed_strings, strings)
            self.assertTrue(all(type(v) is int and v >= 0 for v in e["props"].values() if not isinstance(v, str)))
        install, october, november, week = self.fake.events()
        self.assertEqual(install["props"], {})
        self.assertEqual(october["props"], {**FACTS, "month": "2026-10", "install_age": "0"})
        self.assertEqual(november["props"], {**FACTS, "month": "2026-11", "install_age": "1"})
        self.assertEqual(set(october["props"]), {*usage.VALUES, *usage.FLAGS, "month"})
        self.assertEqual((install["timestamp"], week["timestamp"]), (usage.stamp(T), usage.stamp(NEXT_WEEK)),
                         "the time each was sent")
        self.assertEqual(install["sessionId"], october["sessionId"], "sent together, one session")
        self.assertNotEqual(week["sessionId"], october["sessionId"], "sent later, another")
        self.assertEqual(week["props"], {**dict.fromkeys(usage.SUMMARY, 0), "transcriptions": 1, "minutes": 2,
                                         "cohere": 1, "min_cohere": 2, "proc_cohere": 1, "speakers": 1,
                                         "speakers_estimated": 1, "nemotron": 1, "runs_under_5": 1, "lang_ar": 1,
                                         "gpu_runs": 1, "youtube": 1, "compares": 1, "failed": 1, "failed_model": 1,
                                         "srt": 1, "days_used": 1, "week": "2026-W44"})
        self.assertEqual(list(week["props"]), [*usage.SUMMARY, "week"])

        sent = json.dumps([r[1:] for r in self.fake.requests])
        for secret in (*[v for v in SECRETS.values() if isinstance(v, str)], "Alice", LINE_TEXT, "hunter2", API_KEY,
                       "youtube.com", "ceo", "/home", str(self.home), "125.4", "31.2"):
            self.assertNotIn(secret, sent)

    def test_the_counts_by_model_service_and_feature(self):
        labelled = [{"start": 0, "end": 1, "speaker": "1", "text": "x"}]
        bare = [{"start": 0, "end": 1, "speaker": None, "text": "x"}]
        none = {"voiceprint_model": None, "youtube_id": None}
        self.u.job_ended(job(model="cohere", audio_s=60, seconds=15, voiceprint_model="nemo_en_titanet_small.onnx",
                             youtube_id=None, options={"speakers": 2, "language": "ar"}), labelled)
        self.u.job_ended(job(model="whisper-medium", audio_s=90, seconds=50, device="cuda: NVIDIA RTX A1000 (int8_float16)",
                             options={"speakers": "none", "language": "en"}, **none), bare)
        self.u.job_ended(job(model="large-v3", audio_s=1800, seconds=3300, device="cpu",
                             options={"speakers": "auto", "language": "auto"}, **none), bare)
        self.u.job_ended(job(model="hf-someone-whisper-small", audio_s=3600, seconds=1200, device="cpu (after a GPU error)",
                             options={"speakers": "auto", "language": "ar"},
                             voiceprint_model="3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx",
                             youtube_id=None), labelled)
        self.u.job_ended(job(model="elevenlabs", kind="hosted", audio_s=600, seconds=30, device=None,
                             options={"speakers": "auto", "language": "ar"}, **none), labelled)
        self.u.job_ended(job(model="groq", kind="hosted", audio_s=300, seconds=12, device=None,
                             voiceprint_model="/home/alice/models/diarization/nemo_en_titanet_large.onnx",
                             options={"speakers": 3, "language": "ar"}, youtube_id=None), labelled)
        self.u.job_ended(job(model="hosted-of-my-own", kind="hosted", audio_s=60, seconds=6, device=None, **none), bare)
        self.u.job_ended(job(model="youtube-captions", kind="captions", audio_s=1200, voiceprint_model=None), labelled)
        for status in ("cancelled", "interrupted", "running"):
            self.u.job_ended(job(status=status))
        self.u.job_ended(None)
        for fmt in (*usage.FORMATS, "docx"):
            self.u.exported(fmt)
        self.u.exported("txt")
        week = self.summary()
        self.assertEqual({k: v for k, v in week.items() if v}, {
            "transcriptions": 7, "minutes": 108, "cohere": 1, "whisper_medium": 1, "whisper_large": 1, "other_local": 1,
            "hosted": 3, "elevenlabs": 1, "groq": 1, "speakers": 4, "speakers_given": 2, "speakers_estimated": 2,
            "titanet_small": 1, "titanet_large": 1, "campp": 1, "runs_under_5": 3, "runs_5_30": 2, "runs_30_60": 1,
            "runs_over_60": 1, "lang_ar": 5, "lang_en": 1, "lang_auto": 1, "cpu_runs": 2, "gpu_runs": 2,
            "gpu_fallbacks": 1, "min_cohere": 1, "min_whisper_medium": 2, "min_whisper_large": 30,
            "min_other_local": 60, "min_hosted": 16, "proc_whisper_medium": 1, "proc_whisper_large": 55,
            "proc_other_local": 20, "proc_hosted": 1, "youtube": 1, "youtube_captions": 1, "txt": 2, "srt": 1,
            "vtt": 1, "md": 1, "json": 1, "days_used": 1, "week": "2026-W44"})
        self.assertEqual(week["proc_cohere"], 0, "15 s rounds down to no minutes")

    def test_failures_and_cancels_by_stage(self):
        for stage in ("read", "model", "hosted", "download", "download", None, "elsewhere"):
            self.u.job_ended(job(status="failed"), stage=stage)
        self.u.job_ended(job(status="running"), stage="cancelled")
        self.u.job_ended(job(status="cancelled"), stage="cancelled")
        week = self.summary()
        self.assertEqual({k: week[k] for k in ("failed", "failed_read", "failed_model", "failed_hosted",
                                               "failed_download", "cancelled", "transcriptions")},
                         {"failed": 7, "failed_read": 1, "failed_model": 1, "failed_hosted": 1, "failed_download": 2,
                          "cancelled": 2, "transcriptions": 0})

    def test_the_days_of_use_and_the_starts(self):
        monday = at("2026-10-26T12:00:00+00:00")
        for day in (0, 0, 2, 6):  # Monday twice, Wednesday, Sunday: three days
            self.u.count(now=monday + day * DAY, copies=1)
        self.u.clock = lambda: monday + DAY  # started on the Tuesday
        self.u.thread = "running"  # as after start(), without a thread of the test's own
        self.u.start()
        week = self.summary()
        self.assertEqual((week["days_used"], week["starts"], week["copies"]), (4, 1, 4))
        self.assertEqual(usage.weekday_bit(at("2026-10-26T12:00:00+00:00")), 1)
        self.assertEqual(usage.week_props("2026-W44", {"days": 0b11111111111})["days_used"], 7, "at most seven")

    def test_the_active_month_values(self):
        nvidia = {"devices": [{"name": "Intel(R) UHD Graphics"}], "cuda_devices": 1}
        self.assertEqual(usage.gpu_kind(nvidia), "nvidia", "an NVIDIA card through CUDA comes first")
        self.assertEqual(usage.gpu_kind({"devices": [{"name": "NVIDIA RTX A1000 Laptop GPU"}, {"name": "Intel(R) UHD Graphics"}]}), "nvidia")
        self.assertEqual(usage.gpu_kind({"devices": [{"name": "AMD Radeon(TM) Graphics"}, {"name": "Intel(R) Arc(TM) A380"}]}), "amd")
        self.assertEqual(usage.gpu_kind(GPU), "intel")
        self.assertEqual(usage.gpu_kind({"devices": [{"name": "Apple M2"}]}), "other")
        for none in ({"devices": [], "cuda_devices": 0}, {"error": "exit 1"}, None, {"devices": "?"}):
            self.assertEqual(usage.gpu_kind(none), "none")
        card = lambda gib, kind="gpu": {"devices": [{"name": "x", "kind": "vulkan", "type": kind, "memory": int(gib * 2 ** 30)}]}  # noqa: E731
        self.assertEqual([usage.gpu_vram(card(g)) for g in (2, 3.9, 4, 6, 7.98, 12, 16, 24)],
                         ["under 4", "4-8", "4-8", "4-8", "4-8", "8-16", "8-16", "over 16"])
        self.assertEqual([usage.gpu_vram(g) for g in (card(16, "igpu"), GPU, None, {"devices": [{"type": "gpu"}]})],
                         ["none"] * 4, "integrated graphics, or no size: none")
        self.assertEqual([usage.cpu_kind(m) for m in ({"cpu": "12th Gen Intel(R) Core(TM) i7-12850HX", "arch": "AMD64"},
                                                        {"cpu": "AMD Ryzen 7 7840U w/ Radeon 780M Graphics", "arch": "x86_64"},
                                                        {"cpu": "Apple M2", "arch": "arm64"},
                                                        {"cpu": "Snapdragon(R) X Elite", "arch": "ARM64"},
                                                        {"cpu": "Qualcomm", "arch": "aarch64"}, {"cpu": None}, {})],
                         ["intel", "amd", "arm", "arm", "arm", "other", "other"])
        self.assertEqual([usage.ram_range(gb) for gb in (3.8, 5.8, 6.8, 7.6, 11.6, 15.4, 23.4, 31.7, 62.6, None)],
                         ["under 8", "under 8", "8-16", "8-16", "8-16", "8-16", "16-32", "16-32", "over 32", "under 8"])
        self.assertEqual([usage.cores_range(n) for n in (2, 3, 4, 8, 9, 16, 17, 24, None)],
                         ["under 4", "under 4", "4-8", "4-8", "8-16", "8-16", "over 16", "over 16", "under 4"])
        self.assertEqual([usage.install_kind(k, frozen=True) for k in ("windows", "linux", "other", "source")],
                         ["installer", "linux-build", "other", "other"])
        self.assertEqual([usage.install_kind(k, frozen=False) for k in ("source", "other")], ["source", "source"])
        self.assertEqual([usage.facts("source", False, GPU, MACHINE, free=gb * 1e9)["disk_free"] for gb in (3, 10, 49.9, 50, 199, 200, 900)],
                         ["under 10", "10-50", "10-50", "50-200", "50-200", "over 200", "over 200"])
        self.assertEqual(usage.facts("linux", False, None, {"ram_gb": 31.7, "threads": 24, "cpu": "AMD EPYC", "arch": "x86_64"},
                                     power="performance", free=12e9),
                         {"kind": "source", "ui": "browser", "cpu": "amd", "gpu": "none", "gpu_vram": "none", "cuda": "no",
                          "vulkan": "no", "ram": "16-32", "cores": "over 16", "power": "performance", "disk_free": "10-50"})
        self.assertEqual(usage.facts("windows", True, {**card(4), "cuda_devices": 1}, MACHINE, power=None, free=None),
                         {"kind": "source", "ui": "desktop", "cpu": "intel", "gpu": "nvidia", "gpu_vram": "4-8",
                          "cuda": "yes", "vulkan": "yes", "ram": "8-16", "cores": "8-16", "power": "unknown",
                          "disk_free": "under 10"})
        self.assertEqual([usage.age_range("2026-10-28", m) for m in ("2026-10", "2026-11", "2026-12", "2027-01", "2027-03",
                                                                     "2027-04", "2027-09", "2027-10", "2030-01", "2025-01")],
                         ["0", "1", "2", "3-5", "3-5", "6-11", "6-11", "12+", "12+", "0"])
        self.assertEqual(usage.age_range(None, "2026-10"), "0")
        for kind in ("source", "windows", "linux", "other"):  # whatever comes in, only the listed values go out
            for gpu in (None, GPU, card(9), {"devices": [{"name": "x"}]}):
                for machine in ({}, MACHINE, {"ram_gb": "lots", "threads": "many", "cpu": 7, "arch": None}):
                    for power in (None, "performance", "turbo"):
                        found = usage.facts(kind, kind == "windows", gpu, machine, power=power, free=5e11)
                        self.assertTrue(all(found[k] in usage.VALUES[k] for k in found), found)

    def test_the_settings_library_and_adoption(self):
        cfg = self.cfg
        self.assertEqual(usage.settings_facts(cfg), {"channel": "stable", "update_check": "unasked", "language": "ar",
                                                     "speaker_model": "titanet_small", "device": "auto", "mcp_write": "off"})
        cfg.save_settings(update_channel="latest", update_check=True, device="cpu", mcp_write=True,
                          speaker_model="nemotron-3-diarization")
        self.assertEqual(usage.settings_facts(cfg), {"channel": "latest", "update_check": "on", "language": "ar",
                                                     "speaker_model": "nemotron", "device": "cpu", "mcp_write": "on"})
        cfg.save_settings(update_check=False, speaker_model="campplus")
        self.assertEqual((usage.settings_facts(cfg)["update_check"], usage.settings_facts(cfg)["speaker_model"]), ("off", "campp"))
        for language, sent in (("en", "en"), ("auto", "auto"), ("fr", "other")):
            with mock.patch.dict(cfg.defaults, {"language": language}):
                self.assertEqual(usage.settings_facts(cfg)["language"], sent)

        jobs = [{"id": str(i)} for i in range(12)] + [{"id": f"c{i}", "course": {"id": f"PL{i % 6}"}} for i in range(40)]
        self.assertEqual(usage.library_facts(jobs), {"library": "50-199", "courses": "5-19"})
        self.assertEqual([usage.library_facts([{}] * n)["library"] for n in (0, 1, 9, 10, 49, 50, 199, 200)],
                         ["0", "1-9", "1-9", "10-49", "10-49", "50-199", "50-199", "200+"])
        self.assertEqual([usage.library_facts([{"course": {"id": f"PL{i}"}} for i in range(n)])["courses"]
                          for n in (0, 1, 4, 5, 19, 20)], ["0", "1-4", "1-4", "5-19", "5-19", "20+"])

        no_cache = mock.patch.object(Config, "whisper_source", return_value=None)  # whatever this computer's HF cache has
        no_cache.start()
        self.addCleanup(no_cache.stop)
        flags = usage.adoption_facts(cfg)
        self.assertEqual(set(flags), set(usage.FLAGS))
        self.assertTrue(all(type(v) is int and v in (0, 1) for v in flags.values()))
        self.assertEqual(sum(v for k, v in flags.items() if k.startswith("key_")), 0)
        cfg.save_key("elevenlabs", API_KEY)
        cfg.save_key("azure", "another-secret-key")
        with mock.patch.dict(os.environ, {cfg.models["openai"]["key_env"]: "from-the-environment"}):
            flags = usage.adoption_facts(cfg)
        self.assertEqual({k for k, v in flags.items() if k.startswith("key_") and v}, {"key_elevenlabs", "key_azure", "key_openai"})
        self.assertNotIn(API_KEY, json.dumps(flags))
        speakers = cfg.speaker_models()
        for mid in ("titanet-large", "nemotron-3-diarization"):  # their files, as a finished download leaves them
            for f in speakers[mid]["files"] or [{"path": speakers[mid]["path"]}]:
                path = cfg.path(f["path"])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x")
        cohere = cfg.path(cfg.models["cohere"]["cohere_model"])
        cohere.parent.mkdir(parents=True, exist_ok=True)
        cohere.write_bytes(b"x")
        flags = usage.adoption_facts(cfg)
        self.assertEqual({k for k, v in flags.items() if k.startswith("has_") and v},
                         {"has_cohere", "has_titanet_large", "has_nemotron"})

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
            self.assertEqual(len(usage.system("0.3.1")["osVersion"]), 100, "cut to what Aptabase takes")
        for given, expected in (("ar_EG.UTF-8", "ar-EG"), ("en_US.UTF-8", "en-US"), ("en-us", "en-US"), ("de", "de"),
                                ("de_DE@euro", "de-DE"), ("sr-Latn-RS", "sr"), ("C.UTF-8", ""), ("POSIX", ""), ("", "")):
            self.assertEqual(usage.system_locale(given), expected, given)
        with mock.patch.object(usage.sys, "platform", "win32"), mock.patch.object(usage, "windows_locale", return_value="ar-EG"):
            self.assertEqual(usage.system_locale(), "ar-EG")
        with mock.patch.dict(os.environ, {"LC_ALL": "", "LC_MESSAGES": "", "LANG": "ar_EG.UTF-8"}), \
                mock.patch.object(usage.sys, "platform", "linux"):
            self.assertEqual(usage.system_locale(), "ar-EG")
        self.assertIs(usage.system("0.3.1")["isDebug"], False)
        with mock.patch.dict(os.environ, {"SEDJEM_USAGE_DEBUG": "1"}):
            self.assertIs(usage.system("0.3.1")["isDebug"], True)
        self.assertEqual(usage.system("0.3.2-preview.20261101")["appVersion"], "0.3.2-preview.20261101")

    def test_only_the_listed_events_and_fields_leave(self):
        self.u.tick(T)
        good = self.fake.events()[1]
        self.assertEqual(usage.checked(good), good)
        self.assertEqual(usage.checked({**good, "userId": "abc"}), good, "a field of its own is left out")
        swap = lambda **p: {**good, "props": {**good["props"], **p}}  # noqa: E731
        bad = [{**good, "eventName": "page_view"}, swap(title="x"), swap(gpu="NVIDIA RTX A1000"), swap(month="November"),
               swap(month="2026-13"), swap(has_cohere=2), swap(has_cohere=True), swap(has_cohere="1"), swap(key_groq=-1),
               swap(install_age="7"), swap(library="12"), swap(theme="dark"),
               {**good, "props": {k: v for k, v in good["props"].items() if k != "month"}},
               {**good, "props": {k: v for k, v in good["props"].items() if k != "key_mistral"}},
               {**good, "systemProps": {**good["systemProps"], "deviceModel": "ThinkPad P16"}},
               {**good, "systemProps": {**good["systemProps"], "locale": "en-US-POSIX-x"}},
               {**good, "systemProps": {**good["systemProps"], "isDebug": "false"}},
               {**good, "timestamp": "yesterday"}, {**good, "sessionId": "my-laptop"}, {**good, "sessionId": 17},
               {"eventName": "install", "props": {"path": "/home/alice"}, "systemProps": good["systemProps"],
                "timestamp": good["timestamp"], "sessionId": good["sessionId"]},
               {k: v for k, v in good.items() if k != "systemProps"}, {k: v for k, v in good.items() if k != "timestamp"},
               "install", None]
        week = {**good, "eventName": "week_summary", "props": {**dict.fromkeys(usage.SUMMARY, 0), "week": "2026-W44"}}
        self.assertIsNotNone(usage.checked(week))
        wswap = lambda **p: {**week, "props": {**week["props"], **p}}  # noqa: E731
        bad += [wswap(minutes=12.5), wswap(txt=-1), wswap(txt=True), wswap(txt="3"), wswap(week="last week"),
                wswap(searches=1), {**week, "props": {k: v for k, v in week["props"].items() if k != "copies"}}]
        for event in bad:
            self.assertIsNone(usage.checked(event), event)

        # an event changed in usage.json, and events kept from 0.3.0, are dropped instead of sent
        self.fake.requests.clear()
        data = self.state()
        v1_month = {"timestamp": usage.stamp(T), "sessionId": "176165280012345678", "eventName": "active_month",
                    "systemProps": good["systemProps"],
                    "props": {"kind": "source", "ui": "browser", "gpu": "none", "ram": "8-16", "cores": "4-8"}}
        data["queue"] = [{**good, "props": {**good["props"], "title": SECRETS["title"]}}, {**good, "eventName": "x"}, v1_month]
        (self.home / "app_data" / "usage.json").write_text(json.dumps(data), encoding="utf-8")
        self.usage(T + HOUR).tick(T + HOUR)
        self.assertEqual(self.fake.requests, [])
        self.assertEqual(self.state()["queue"], [])


class Assistants(UsageCase):
    """What assistants asked, counted from the log app/mcp.py writes (app/assistants.py)."""

    def log(self, *entries):
        """Add entries to the assistants' log: (time, action, version)."""
        path = assistants.log_path(self.cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for when, action, *version in entries:
                entry = {"time": datetime.fromtimestamp(when, timezone.utc).astimezone().isoformat(timespec="seconds"),
                         "client": "Claude Desktop 1.2", "action": action, "detail": "salaries in the board meeting",
                         "ids": ["20261028-120000-a001"], **({"version": version[0]} if version else {})}
                f.write(json.dumps(entry) + "\n")

    def test_requests_and_saves_from_the_log(self):
        self.log((T - DAY, "search"), (T - HOUR, "get_transcript"))
        self.u.tick(T)
        self.assertEqual(self.week(), {}, "the first look only notes where the log ends")
        self.log((T + 60, "search"), (T + 60, "get_transcript"), (T + 120, "save_version", 4),
                 (T + 180, "save_version"))  # the last one refused: no version
        self.u.tick(T + HOUR)
        self.assertEqual((self.week()["assistant_requests"], self.week()["assistant_saves"]), (4, 1))
        self.u.tick(T + 2 * HOUR)
        self.assertEqual(self.week()["assistant_requests"], 4, "counted once")
        self.log((T + 180, "list_courses"), (T + 3 * DAY, "search"))  # the same second as the newest counted, then later
        self.u.tick(T + 3 * DAY + HOUR)
        self.assertEqual(self.week()["assistant_requests"], 6)
        week = self.summary()
        self.assertEqual((week["assistant_requests"], week["assistant_saves"]), (6, 1))
        sent = json.dumps(self.fake.events())
        for private in ("salaries", "Claude Desktop", "get_transcript", "20261028-120000-a001"):
            self.assertNotIn(private, sent)

    def test_a_cleared_log_and_a_switch_turned_off(self):
        self.log((T - HOUR, "search"))
        self.u.tick(T)
        assistants.clear_log(self.cfg)
        self.u.tick(T + 60)
        self.log((T + 120, "search"))
        self.u.tick(T + 180)
        self.assertEqual(self.week()["assistant_requests"], 1)
        before = self.cfg.settings()
        self.cfg.save_settings(usage_counts=False)
        self.u.settings_changed(before, self.cfg.settings())
        self.log((T + 240, "search"), (T + 300, "search"))  # asked while the counts were off
        self.cfg.save_settings(usage_counts=True)
        self.u.tick(T + 360)
        self.log((T + 420, "search"))
        self.u.tick(T + 480)
        self.assertEqual(self.week()["assistant_requests"], 1, "only what was asked after they were on again")
        self.assertIsNone(usage.log_time({"time": "yesterday"}))
        self.assertIsNone(usage.log_time({}))


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
        usage.Usage(cfg, version=lambda: "0.3.1", facts=lambda: dict(FACTS)).tick(T + 3 * HOUR)
        self.assertEqual(self.fake.names().count("install"), 2, "another data folder is another install")

    def test_active_month_once_a_calendar_month_with_its_month(self):
        last = at("2026-10-31T23:59:00+00:00")
        for moment in (T, T + DAY, last):
            self.u.tick(moment)
        self.usage(last).tick(last)
        self.assertEqual([p["month"] for p in self.fake.props("active_month")], ["2026-10"])
        first = at("2026-11-01T00:01:00+00:00")
        self.u.tick(first)
        self.u.tick(first + 10 * DAY)
        self.assertEqual([p["month"] for p in self.fake.props("active_month")], ["2026-10", "2026-11"])

    def test_the_install_age(self):
        ages = {}
        for month, moment in (("2026-10", T), ("2026-11", at("2026-11-15T12:00:00+00:00")),
                              ("2027-02", at("2027-02-15T12:00:00+00:00")), ("2027-05", at("2027-05-15T12:00:00+00:00")),
                              ("2027-11", at("2027-11-15T12:00:00+00:00"))):
            self.u.tick(moment)
            ages[month] = self.fake.props("active_month")[-1]["install_age"]
        self.assertEqual(ages, {"2026-10": "0", "2026-11": "1", "2027-02": "3-5", "2027-05": "6-11", "2027-11": "12+"})

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
        weeks = self.fake.props("week_summary")
        self.assertEqual(len(weeks), 1)
        self.assertEqual((weeks[0]["week"], weeks[0]["transcriptions"], weeks[0]["minutes"], weeks[0]["txt"]),
                         ("2026-W44", 1, 10, 1))
        self.assertEqual(self.state()["weeks"], {})

    def test_weeks_without_use_send_nothing_and_old_weeks_go_one_by_one(self):
        self.u.count(now=T - 14 * DAY, compares=1)  # 2026-W42
        self.u.count(now=T - 7 * DAY, failed=1)     # 2026-W43
        self.u.tick(NEXT_WEEK)  # W44 had no use
        self.assertEqual([p["week"] for p in self.fake.props("week_summary")], ["2026-W42", "2026-W43"])
        self.assertEqual(len(self.fake.requests), 1, "with the install and the month, in one request")

    def test_the_week_being_counted_waits_in_usage_json(self):
        self.u.count(now=T, transcriptions=1, seconds=61.5, hosted=1)
        self.assertEqual(self.state()["weeks"], {"2026-W44": {"transcriptions": 1, "seconds": 61.5, "hosted": 1, "days": 4}})
        again = self.usage(NEXT_WEEK)  # started again after the week
        again.tick(NEXT_WEEK)
        week = self.fake.props("week_summary")[0]
        self.assertEqual((week["transcriptions"], week["minutes"], week["hosted"], week["days_used"]), (1, 1, 1, 1))

    def test_a_week_counted_by_0_3_0(self):
        data = {"install": "2026-10-20", "month": "2026-10", "weeks": {"2026-W44": {"transcriptions": 2, "seconds": 300,
                                                                                    "cohere": 2, "txt": 1}}, "queue": []}
        (self.home / "app_data" / "usage.json").write_text(json.dumps(data), encoding="utf-8")
        self.usage(NEXT_WEEK).tick(NEXT_WEEK)
        week = self.fake.props("week_summary")[0]
        self.assertEqual({k: v for k, v in week.items() if v}, {"transcriptions": 2, "minutes": 5, "cohere": 2, "txt": 1,
                                                                 "week": "2026-W44"})
        self.assertEqual(week["days_used"], 0, "not known for a week counted before 0.3.1")


class Disabled(UsageCase):
    def assertQuiet(self, u, at_time=NEXT_WEEK):
        u.job_ended(job())
        u.exported("txt")
        u.compared()
        u.downloaded("cohere")
        u.count(copies=1)
        self.assertFalse(u.tick(at_time))
        self.assertEqual(self.fake.requests, [])

    def test_nothing_before_the_first_start_dialog(self):
        self.cfg.save_settings(usage_counts=None)
        self.assertEqual(self.u.summary(), {"available": True, "on": True, "ask": True, "blocked": None})
        self.u.thread = "running"  # as after start(), without a thread of the test's own
        self.u.start()
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
        self.assertEqual(self.state(), {"install": "2026-10-28", "month": "2026-10", "assistants": None, "weeks": {},
                                        "queue": []}, "the counts and the events waiting are deleted; the flags stay")
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
        u = usage.Usage(self.cfg, version=lambda: "0.3.1", facts=lambda: dict(FACTS))
        self.assertEqual(u.url(), "https://eu.aptabase.com/api/v0/events")
        self.assertFalse(u.allowed(), "a key and the switch on, but the tests run")
        u.job_ended(job())
        u.count(starts=1)
        self.assertFalse(u.tick(T))
        self.assertFalse((self.home / "app_data" / "usage.json").exists())
        self.assertEqual(self.connections, [])

    def test_even_a_test_that_lets_them_through_reaches_only_this_computer(self):
        with mock.patch.object(usage, "test_run", return_value=False):
            u = usage.Usage(self.cfg, version=lambda: "0.3.1", facts=lambda: dict(FACTS))
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


class Workflows(unittest.TestCase):
    """CI runs the tests as a developer does, with neither opt-out variable set, so nothing in them hides what a
    developer would meet (the first-start dialog over the browser tests, say). SEDJEM_NO_USAGE is set only where the
    workflows start Sedjem itself, outside the tests."""

    def flow(self, name):
        if yaml is None:
            self.skipTest("no PyYAML")
        return yaml.safe_load((ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"))

    def test_the_tests_run_unmasked_and_the_app_is_told_not_to_send(self):
        tests = self.flow("tests.yml")
        suite = tests["jobs"]["test"]
        step = next(s for s in suite["steps"] if "unittest" in str(s.get("run")))
        for env in (tests.get("env"), suite.get("env"), step.get("env")):
            self.assertFalse({"SEDJEM_NO_USAGE", "DO_NOT_TRACK"} & set(env or {}), env)
        self.assertEqual(tests["jobs"]["windows-source"]["env"]["SEDJEM_NO_USAGE"], "1", ".\\app.cmd runs Sedjem itself")
        self.assertEqual(self.flow("desktop.yml")["env"]["SEDJEM_NO_USAGE"], "1", "so do the builds and their checks")


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
                first, second = (r[2] for r in self.fake.requests)
                self.assertEqual([{**e, "timestamp": None, "sessionId": None} for e in first],
                                 [{**e, "timestamp": None, "sessionId": None} for e in second], "the same events")
                self.assertEqual((first[0]["timestamp"], second[0]["timestamp"]), (usage.stamp(T), usage.stamp(T + HOUR)),
                                 "each time with the time it was sent")

    def test_events_that_waited_for_days_are_sent_with_their_period(self):
        self.fake.answer = 503
        self.u.tick(T)
        self.u.count(now=T, transcriptions=1)
        self.assertNotIn("timestamp", json.dumps(self.state()["queue"]), "kept without a time")
        self.fake.answer = "ok"
        later = at("2026-11-04T08:00:00+00:00")  # six days offline, into another month and week
        self.u.tick(later)
        self.assertEqual(self.fake.names()[-4:], ["install", "active_month", "active_month", "week_summary"])
        sent = self.fake.requests[-1][2]
        self.assertTrue(all(e["timestamp"] == usage.stamp(later) for e in sent))
        self.assertTrue(all(e["sessionId"].startswith(str(int(later))) for e in sent))
        self.assertEqual([e["props"].get("month") or e["props"].get("week") for e in sent[1:]],
                         ["2026-10", "2026-11", "2026-W44"], "the period each counts, whenever it was sent")

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

    def test_at_most_25_events_a_request(self):
        for weeks_ago in range(1, 31):
            self.u.count(now=T - weeks_ago * 7 * DAY, compares=1)
        self.u.tick(T)
        self.assertEqual([len(events) for _, _, events in self.fake.requests], [25, 7])
        self.assertEqual(self.state()["queue"], [])

    def test_at_most_100_events_wait(self):
        self.fake.answer = 503
        for weeks_ago in range(1, 121):
            self.u.count(now=T - weeks_ago * 7 * DAY, compares=1)
        self.u.tick(T)
        queue = self.state()["queue"]
        self.assertEqual(len(queue), usage.KEEP)
        self.assertEqual((queue[0]["props"]["week"], queue[-1]["props"]["week"]),
                         (usage.week_of(T - 100 * 7 * DAY), "2026-W43"), "the newest kept")

    def test_it_never_raises(self):
        def broken():
            raise RuntimeError("the GPU check broke")
        u = usage.Usage(self.cfg, version=lambda: "0.3.1", facts=broken)
        self.assertTrue(u.tick(T))
        self.assertEqual(self.fake.names(), ["install"], "the month waits for the next look")
        u.facts = lambda: dict(FACTS)
        u.tick(T + 60)
        self.assertEqual(self.fake.names(), ["install", "active_month"])
        self.assertEqual(usage.post("http://127.0.0.1:1/api/v0/events", KEY, "x", []), (None, None))
        with mock.patch.object(usage, "replace_file", side_effect=PermissionError("locked")):
            self.u.count(now=T, txt=1)  # usage.json can't be written: kept in memory
        self.assertEqual(self.u.data["weeks"]["2026-W44"], {"txt": 1, "days": 4})
        self.u.count(now=T, **{"no such count": 1, "txt": 1, "days": 99})
        self.assertEqual(self.u.data["weeks"]["2026-W44"], {"txt": 2, "days": 4})
        (self.home / "app_data" / "mcp").mkdir()
        (self.home / "app_data" / "mcp" / "log.jsonl").write_text("not json\n[1]\n{\"time\": 5}\n", encoding="utf-8")
        self.u.tick(T + 120)  # a log that isn't one counts nothing

    def test_debug_events(self):
        with mock.patch.dict(os.environ, {"SEDJEM_USAGE_DEBUG": "1"}):
            self.u.tick(T)
        self.assertTrue(all(e["systemProps"]["isDebug"] is True for e in self.fake.events()))

    def test_the_thread(self):
        with mock.patch.object(usage, "START_DELAY", 0):
            self.u.start()
            self.u.start()  # counted again, but one thread
            deadline = time.monotonic() + 10
            while len(self.fake.requests) < 1 and time.monotonic() < deadline:
                time.sleep(0.05)
        self.assertEqual(self.fake.names(), ["install", "active_month"])
        self.assertEqual([t.name for t in threading.enumerate()].count("sedjem-usage"), 1)
        self.assertEqual(self.week()["starts"], 2)


class AppCounts(UsageCase):
    """The counting points of the app, through its HTTP API, its runner and its downloads."""
    A, B = "20261020-100000-a001", "20261020-110000-a002"

    def setUp(self):
        super().setUp()
        demo_job(self.home, self.A)
        demo_job(self.home, self.B, model="whisper-medium", model_title="whisper-medium", rerun_of=self.A,
                 lines=[{"start": 0.0, "end": 4.2, "speaker": "1", "text": "another text altogether"}])
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
        return {k: v for k, v in self.app.usage.data["weeks"].get(usage.week_of(time.time()), {}).items() if k != "days"}

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

    def test_exports_and_copies(self):
        for path in (f"/api/jobs/{self.A}/export/srt", f"/api/jobs/{self.A}/export/srt?version=0",
                     f"/api/jobs/{self.A}/export/md", f"/api/jobs/{self.A}/export/txt?details=0",
                     f"/api/jobs/{self.A}/export/txt?details=0&version=0"):
            self.assertEqual(self.call("GET", path)[0], 200)
        self.assertEqual(self.call("HEAD", f"/api/jobs/{self.A}/export/json")[0], 200)
        self.assertEqual(self.call("GET", f"/api/jobs/{self.A}/export/docx")[0], 404)
        self.assertEqual(self.counts(), {"srt": 2, "md": 1, "copies": 2}, "Copy text asks for the text without the details")

        path = self.home / "saved.json"
        window = mock.Mock(create_file_dialog=mock.Mock(return_value=[str(path)]))
        api = desktop.Api(self.app)
        api._window = window
        with mock.patch.object(desktop.Api, "_dialog", return_value="save"):
            self.assertEqual(api.save_export(self.A, "json"), str(path))
        self.assertEqual(self.counts(), {"srt": 2, "md": 1, "copies": 2, "json": 1}, "the desktop app's save dialog too")

    def test_the_compare_view_and_combining(self):
        self.assertEqual(self.call("GET", f"/api/compare?ids={self.A},{self.B}")[0], 200)
        self.assertEqual(self.call("GET", f"/api/compare?ids={self.A},{self.B}&base={self.B}")[0], 200)
        self.assertEqual(self.call("GET", f"/api/compare?ids={self.A},nope")[0], 404)
        body = {"ids": [self.A, self.B], "base": self.A, "picks": [{"start": 0.0, "end": 4.2, "from": self.B}]}
        self.assertEqual(self.call("POST", "/api/combine/preview", body)[0], 200)
        self.assertEqual(self.call("POST", "/api/combine", body)[0], 200)
        self.assertEqual(self.call("POST", "/api/combine", {**body, "picks": []})[0], 400)
        self.assertEqual(self.counts(), {"compares": 1, "combines": 1}, "a preview isn't a combine")

    def test_edits_branches_and_restores(self):
        jid = self.A
        self.assertEqual(self.call("PATCH", f"/api/jobs/{jid}", {"title": "A new title"})[0], 200)
        self.assertEqual(self.call("PATCH", f"/api/jobs/{jid}", {"speaker_names": {"1": "Mona"}})[0], 200)
        lines = self.call("GET", f"/api/jobs/{jid}")[1]["lines"]
        self.assertEqual(self.call("PATCH", f"/api/jobs/{jid}", {"lines": [{**lines[0], "text": "fixed"}, *lines[1:]]})[0], 200)
        self.assertEqual(self.call("PATCH", f"/api/jobs/{jid}", {"merge": {"from": "2", "into": "1"}})[0], 200)
        self.assertEqual(self.call("PATCH", f"/api/jobs/{jid}", {"title": "A new title"})[0], 200)  # no change: no count
        self.assertEqual(self.call("POST", f"/api/jobs/{jid}/diff", {"title": "Only looked at"})[0], 200)
        self.assertEqual(self.call("POST", f"/api/jobs/{jid}/versions/0/restore", {})[0], 200)
        self.assertEqual(self.call("POST", f"/api/jobs/{jid}/branch", {"version": 0, "name": "Mine"})[0], 201)
        self.assertEqual(self.counts(), {"renames": 2, "edit_sessions": 1, "merges": 1, "restores": 1, "branches": 1})

    def test_the_end_of_a_run_is_counted(self):
        runner, store = self.app.runner, self.app.store
        lines = [{"start": 0.0, "end": 2.0, "speaker": "1", "text": "Hello"}]
        options = {"speakers": "auto", "language": "ar", "prompt": ""}

        def queued(model, seconds, **extra):
            j = runner.new_job(title="A call", source_name="call.m4a", model_id=model, options=options, **extra)
            store.update(j["id"], status="queued", audio_s=seconds)
            return j["id"]
        with mock.patch.object(engines, "run_hosted", return_value={"lines": lines, "meta": {}}):
            runner.work(queued("elevenlabs", 95.0), "hosted")
        with mock.patch.object(engines, "run_hosted", side_effect=engines.EngineError("the service said no")):
            runner.work(queued("deepgram", 30.0), "hosted")
        for device in ("vulkan: Intel(R) Iris(R) Xe Graphics", "cpu (after a GPU error)"):
            with mock.patch.object(engines, "run_local", return_value={"lines": lines, "meta": {"device": device}}):
                runner.work(queued("cohere", 400.0), "local")
        with mock.patch.object(engines, "run_local", side_effect=engines.EngineError("out of memory")):
            runner.work(queued("cohere", 10.0), "local")
        yt = runner.new_job(title="A lecture", source_name="lecture.mp4", model_id="cohere", options={"speakers": "none"},
                            source_url=youtube.watch_url("dQw4w9WgXcQ"), youtube_id="dQw4w9WgXcQ")
        with mock.patch.object(youtube, "download", side_effect=youtube.YouTubeError("This video is private")):
            runner.download(yt["id"], "download")
        unread = runner.new_job(title="Gone", source_name="gone.m4a", model_id="cohere", options=options,
                                source_path=str(self.home / "gone.m4a"))
        runner.submit(unread["id"])  # prepare can't find the recording: the runner's own thread fails it
        deadline = time.monotonic() + 20
        while store.get(unread["id"])["status"] != "failed" and time.monotonic() < deadline:
            time.sleep(0.05)
        deadline = time.monotonic() + 5
        while "failed_read" not in self.counts() and time.monotonic() < deadline:
            time.sleep(0.05)
        counts = self.counts()
        took = {k: counts.pop(k) for k in ("proc_seconds_hosted", "proc_seconds_cohere")}
        self.assertTrue(all(isinstance(v, float) and 0 <= v < 30 for v in took.values()), took)
        self.assertEqual(counts, {
            "transcriptions": 3, "seconds": 895.0, "hosted": 1, "elevenlabs": 1, "cohere": 2, "seconds_hosted": 95.0,
            "seconds_cohere": 800.0, "runs_5_30": 2, "runs_under_5": 1, "speakers": 3, "speakers_estimated": 3,
            "lang_ar": 3, "gpu_runs": 1, "cpu_runs": 1, "gpu_fallbacks": 1, "failed": 4, "failed_hosted": 1,
            "failed_model": 1, "failed_download": 1, "failed_read": 1})

    def test_cancels_are_counted_once(self):
        runner = self.app.runner
        waiting = runner.new_job(title="Waiting", source_name="w.m4a", model_id="cohere", options={"speakers": "none"})
        self.assertEqual(self.call("POST", f"/api/jobs/{waiting['id']}/cancel")[0], 200)
        self.assertEqual(self.call("POST", f"/api/jobs/{waiting['id']}/cancel")[0], 200)  # again: not counted again
        self.assertEqual(self.call("POST", f"/api/jobs/{self.A}/cancel")[0], 200)  # a finished job isn't cancelled
        running = runner.new_job(title="Running", source_name="r.m4a", model_id="cohere", options={"speakers": "none"})
        self.app.store.update(running["id"], status="running")  # (deleting a running job cancels it the same way)
        self.assertEqual(self.call("POST", f"/api/jobs/{running['id']}/cancel")[0], 200)
        self.assertEqual(self.counts(), {"cancelled": 2})

    def test_youtube_courses_downloads_and_update_now(self):
        self.cfg.save_key("elevenlabs", API_KEY)
        playlist = {"kind": "playlist", "id": "PLabcdefghijklmnop", "title": "A course", "url": "https://www.youtube.com/playlist?list=PLabcdefghijklmnop",
                    "count": 2, "entries": [{"id": "dQw4w9WgXcQ", "title": "One", "index": 1, "available": True},
                                            {"id": "oHg5SJYRHA0", "title": "Two", "index": 2, "available": True}]}
        body = {"url": playlist["url"], "model": "elevenlabs", "confirm_upload": True}
        with mock.patch.object(youtube, "inspect", return_value=playlist), mock.patch.object(self.app.runner, "submit"):
            self.assertEqual(self.call("POST", "/api/youtube", body)[0], 201)
            self.assertEqual(self.call("POST", "/api/youtube", body)[0], 201)  # the same playlist again: not a new course
            self.assertEqual(self.call("POST", "/api/youtube", {**body, "url": "https://youtu.be/dQw4w9WgXcQ"})[0], 201)
        with mock.patch.object(youtube, "inspect", return_value={**playlist, "kind": "video", "entries": playlist["entries"][:1]}), \
                mock.patch.object(self.app.runner, "submit"):
            self.assertEqual(self.call("POST", "/api/youtube", body)[0], 201)  # a single video isn't a course
        self.assertEqual(self.counts(), {"courses_created": 1})

        items = ["titanet-large", "deno", "cohere"]
        self.app.downloads.jobs.update({i: {"state": "queued", "done": 0, "total": 0, "error": None} for i in items})
        with mock.patch.object(Downloads, "installed", return_value=True), mock.patch.object(Downloads, "converted", return_value=True):
            self.app.downloads.run(items)
        self.assertEqual(self.counts(), {"courses_created": 1, "model_downloads": 2}, "not Deno")

        with mock.patch.object(self.app.updates, "start_install"):
            self.assertEqual(self.call("POST", "/api/updates/install")[0], 200)
        with mock.patch.object(self.app.updates, "start_install", side_effect=updates.Refused("nothing to install")):
            self.assertEqual(self.call("POST", "/api/updates/install")[0], 409)
        self.assertEqual(self.counts(), {"courses_created": 1, "model_downloads": 2, "update_installs": 1})

    def test_the_apps_own_facts(self):
        facts = self.app.usage_facts()
        self.assertEqual(set(facts), {*usage.VALUES, *usage.FLAGS} - {"install_age"})
        self.assertTrue(all(facts[k] in usage.VALUES[k] for k in facts if k in usage.VALUES), facts)
        self.assertTrue(all(facts[k] in (0, 1) for k in usage.FLAGS))
        self.assertEqual((facts["library"], facts["courses"], facts["ui"], facts["gpu"], facts["vulkan"]),
                         ("1-9", "0", "browser", "intel", "yes"))
        event = {"eventName": "active_month", "props": {**facts, "month": "2026-10", "install_age": "0"},
                 "systemProps": usage.system("0.3.1"), "timestamp": usage.stamp(T), "sessionId": usage.session_id(T)}
        self.assertIsNotNone(usage.checked(event), "the app's facts pass the whitelist")


# ---------------------------------------------------------------------------------------------
# The public totals (tools/usage_totals.py)
# ---------------------------------------------------------------------------------------------

HEADER = ["timestamp", "user_id", "session_id", "event_name", "string_props", "numeric_props", "os_name", "os_version",
          "locale", "app_version", "app_build_number", "engine_name", "engine_version", "country_code", "country_name",
          "region_name"]
V2 = {k: v for k, v in FACTS.items()}  # an active_month of 0.3.1: FACTS with the month and the install's age


def row(event, country="EG", region="Cairo", locale="ar-eg", sent="2026-11-03 10:15:00", props=None,
        os_name="Windows", os_version="10.0.26100", version="0.3.1"):
    """A row of Aptabase's export, as its CSV has them: the string props and the numeric ones apart."""
    props = props or {}
    return {"timestamp": sent, "user_id": "9F86D081884C7D65", "session_id": "176174659212345678",
            "event_name": event, "string_props": json.dumps({k: v for k, v in props.items() if isinstance(v, str)}),
            "numeric_props": json.dumps({k: v for k, v in props.items() if not isinstance(v, str)}),
            "os_name": os_name, "os_version": os_version, "locale": locale, "app_version": version,
            "app_build_number": "", "engine_name": "python", "engine_version": "3.12.15", "country_code": country,
            "country_name": "", "region_name": region}


def month(country="EG", region="Cairo", locale="ar-eg", age="0", sent="2026-11-03 10:15:00", m="2026-11",
          os_name="Windows", os_version="10.0.26100", version="0.3.1", **props):
    return row("active_month", country, region, locale, sent, {**V2, "month": m, "install_age": age, **props},
               os_name, os_version, version)


def summary(week, **numbers):
    return {**dict.fromkeys(usage.SUMMARY, 0), **numbers, "week": week}


V1_SUMMARY = ("transcriptions", "minutes", "cohere", "whisper_medium", "whisper_large", "other_local", "hosted",
              "speakers", "nemotron", "youtube", "compares", "failed", "txt", "srt", "vtt", "md", "json")


def export(rows, header=HEADER, quotes=False):
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=header, extrasaction="ignore", lineterminator="\n")
    w.writerow({h: h for h in header})
    for r in rows:
        if quotes:  # the export may swap the double quotes of the props for single ones
            r = {**r, "string_props": r["string_props"].replace('"', "'"), "numeric_props": r["numeric_props"].replace('"', "'")}
        w.writerow(r)
    return out.getvalue()


def website():
    """An export of the Sedjem website app with November 2026's page views, 16 of them by 8 visitors (a visitor is a
    day's hash): Egypt 6 by 4 (E1 on two days), Saudi Arabia 4 by 2, Germany 5 by 1 and one with no country; linked
    from linkedin.com by 3 visitors, github.com by 2 and an intranet by 1. Five download clicks, and five events the
    pages never send; and a page view in October and one in December."""
    def visit(user, day, page, device, referrer, country, times=1):
        return [{**row("page_view", country, "", "", f"2026-11-{day} 10:00:00", {"page": page, "device": device, "theme":
                                                                                 "system", "referrer": referrer},
                       os_name="Android", os_version="", version="0.3.1"), "user_id": user}] * times
    def click(platform, place="get", page="en"):
        return {**row("download", "EG", "", "", "2026-11-05 10:01:00", {"platform": platform, "place": place, "page": page}),
                "user_id": "E1"}
    rows = (visit("E1", "05", "en", "phone", "none", "EG", 2) + visit("E1", "05", "ar", "phone", "linkedin.com", "EG")
            + visit("E2", "05", "ar", "desktop", "linkedin.com", "EG") + visit("E1", "06", "en", "desktop", "linkedin.com", "EG")
            + visit("E3", "07", "404", "tablet", "none", "EG") + visit("S1", "08", "en", "desktop", "github.com", "SA", 2)
            + visit("S2", "08", "ar", "phone", "github.com", "SA", 2) + visit("D1", "09", "en", "desktop", "none", "DE", 5)
            + visit("X1", "10", "en", "phone", "intranet.example.corp", ""))
    rows += [click("windows", "hero"), click("windows"), click("windows", page="ar"), click("linux"), click("source")]
    good = visit("E9", "11", "en", "phone", "none", "EG")[0]
    rows += [{**good, "string_props": json.dumps(props)} for props in (
        {"page": "fr", "device": "phone", "theme": "system", "referrer": "none"},
        {"page": "en", "device": "phone", "theme": "system", "referrer": "none", "title": "Board meeting"},
        {"page": "en", "device": "phone", "theme": "system", "referrer": "Bad Host!"})]
    rows += [click("mac"), {**good, "event_name": "active_month"}]
    rows += [{**good, "timestamp": "2026-10-31 23:00:00"}, {**good, "timestamp": "2026-12-01 08:00:00"}]
    return rows


def november():
    """Two exports: November's, and December's so far. November 2026 has 11 active installs: Egypt 4 (Cairo 3, Giza
    1, on Linux), Saudi Arabia 3, the Emirates 2 (one sent late, in December's export), the US 1 (a preview), and
    one from 0.3.0 with no country and none of the new props; 5 new installs; and four weeks of use, three from
    0.3.1 and one from 0.3.0. And what doesn't count for November: an October active_month and week sent in
    November, a December week, a stranger's event and five week summaries with numbers Sedjem never sends."""
    first = [month(age="0", update_check="on", has_cohere=1, key_elevenlabs=1),
             month(age="0", update_check="on", has_cohere=1, key_elevenlabs=1),
             month(age="1", update_check="off", channel="latest", has_cohere=1, key_elevenlabs=0),
             month(region="Giza", age="1", update_check="on", has_cohere=1, key_elevenlabs=0, os_name="Linux",
                   os_version="Ubuntu 24.04.4 LTS"),
             month("SA", "Riyadh", "ar-sa", age="1", gpu="nvidia", update_check="on", has_cohere=1, key_elevenlabs=0),
             month("SA", "Riyadh", "ar-sa", age="3-5", gpu="nvidia", update_check="off", has_cohere=0, key_elevenlabs=0),
             month("SA", "Riyadh", "ar-sa", age="3-5", gpu="nvidia", update_check="on", has_cohere=0, key_elevenlabs=0,
                   channel="latest"),
             month("AE", "Dubai", "en-ae", age="12+", update_check="unasked", has_cohere=0, key_elevenlabs=0),
             month("US", "California", "en-us", age="0", update_check="off", has_cohere=0, key_elevenlabs=0,
                   version="0.3.2-preview.20261101"),
             row("active_month", "", "", "", props={"kind": "source", "ui": "browser", "gpu": "Hacked GPU", "ram": "8-16",
                                                   "cores": "4-8"}, version="0.3.0"),
             month(m="2026-10", age="0", sent="2026-11-01 08:00:00")]  # October's, sent late
    first += [row("install", c) for c in ("EG", "EG", "SA", "AE", "US")]
    weeks = [summary("2026-W45", transcriptions=10, minutes=300, cohere=6, whisper_medium=2, hosted=2, elevenlabs=2,
                     speakers=7, speakers_given=3, speakers_estimated=4, titanet_small=5, runs_under_5=2, runs_5_30=6,
                     runs_30_60=2, lang_ar=9, lang_auto=1, cpu_runs=5, gpu_runs=3, gpu_fallbacks=1, min_cohere=180,
                     proc_cohere=45, min_whisper_medium=60, proc_whisper_medium=30, min_hosted=60, proc_hosted=6,
                     failed=1, failed_model=1, youtube=2, txt=3, srt=1, copies=2, days_used=3, starts=4,
                     assistant_requests=5, assistant_saves=1, edit_sessions=2),
             summary("2026-W45", transcriptions=4, minutes=125, whisper_large=1, other_local=1, hosted=2, openai=1,
                     groq=1, speakers=2, speakers_estimated=2, nemotron=1, titanet_large=1, runs_5_30=2, runs_30_60=1,
                     runs_over_60=1, lang_en=1, lang_ar=3, cpu_runs=2, min_whisper_large=60, proc_whisper_large=110,
                     min_other_local=20, proc_other_local=10, min_hosted=45, proc_hosted=5, failed=2, failed_read=1,
                     failed_download=1, cancelled=1, compares=2, combines=1, md=2, days_used=2, starts=2,
                     courses_created=1),
             summary("2026-W46", transcriptions=1, minutes=15, cohere=1, runs_5_30=1, lang_ar=1, gpu_runs=1,
                     min_cohere=15, proc_cohere=3, json=1, days_used=1, starts=1, renames=1)]
    first += [row("week_summary", props=w) for w in weeks]
    first.append(row("week_summary", props={**{k: 0 for k in V1_SUMMARY}, "transcriptions": 2, "minutes": 30, "cohere": 1,
                                            "whisper_medium": 1, "speakers": 1, "youtube": 1, "vtt": 1, "week": "2026-W47"},
                     version="0.3.0"))
    first.append(row("week_summary", sent="2026-11-02 09:00:00", props=summary("2026-W44", transcriptions=50, minutes=900,
                                                                             cohere=50)))  # October's week
    for bad in ({"minutes": -5}, {"transcriptions": 2.5}, {"txt": 10 ** 9}, {"transcriptions": "many"}):
        first.append(row("week_summary", props=summary("2026-W45", **bad)))
    first.append(row("week_summary", props={**summary("2026-W45"), "week": "last week"}))
    first.append(row("page_view"))
    second = [month("AE", "Dubai", "en-ae", age="6-11", sent="2026-12-02 07:00:00", update_check="on", has_cohere=0,
                    key_elevenlabs=0),  # November's, sent late
              row("week_summary", sent="2026-12-08 09:00:00", props=summary("2026-W49", transcriptions=7)),
              row("install", "EG", sent="2026-12-02 09:00:00")]
    return first, second


class Totals(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sedjem-totals-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, text, name="sedjem-release-2026-11-01.csv"):
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path

    def exports(self):
        first, second = november()
        return [self.write(export(first), "november.csv"), self.write(export(second), "december.csv")]

    def test_the_kpis_of_a_month(self):
        rows = totals.read(self.exports())
        t = totals.totals(rows, "2026-11", "2026-12-03")
        i = t["installs"]
        self.assertEqual((t["month"], t["exported"], i["active"], i["new"]), ("2026-11", "2026-12-03", 11, 5))
        self.assertEqual(i["countries"], {"count": 4, "installs": {"EG": 4, "SA": 3, "other": 4}},
                         "the Emirates (2), the US (1) and no country (1) are folded: fewer than 3 installs")
        self.assertEqual(i["regions"], {"EG: Cairo": 3, "SA: Riyadh": 3, "other": 5})
        self.assertEqual(i["locales"], {"ar-eg": 4, "ar-sa": 3, "other": 4})
        self.assertEqual(i["systems"], {"Windows": 10, "other": 1})
        self.assertEqual(i["system_versions"], {"Windows 10.0.26100": 10, "other": 1})
        self.assertEqual(i["versions"], {"0.3.1": 9, "other": 2})
        self.assertEqual(t["retention"], {"install_age": {"0": 3, "1": 3, "3-5": 2, "6-11": 1, "12+": 1, "unknown": 1}})
        self.assertEqual(t["settings"]["update_check"], {"on": 0.545, "off": 0.273, "unasked": 0.091, "unknown": 0.091})
        self.assertEqual(t["settings"]["update_check_on"], 0.6)
        self.assertEqual(t["settings"]["channel"], {"stable": 0.727, "latest": 0.182, "unknown": 0.091})
        self.assertEqual(t["hardware"]["gpu"], {"nvidia": 3, "intel": 7, "unknown": 1}, "an unknown value isn't published")
        self.assertEqual(t["hardware"]["ram"], {"8-16": 11})
        self.assertEqual(t["hardware"]["cpu"], {"intel": 10, "unknown": 1})
        self.assertEqual(t["library"], {"library": {"10-49": 10, "unknown": 1}, "courses": {"1-4": 10, "unknown": 1}})
        self.assertEqual(t["adoption"]["models"], {"cohere": 0.5, "whisper_medium": 0.0, "whisper_large": 0.0,
                                                   "other_local": 0.0, "titanet_large": 0.0, "campp": 0.0, "nemotron": 0.0})
        self.assertEqual(t["adoption"]["hosted_keys"]["elevenlabs"], 0.2)
        self.assertEqual(t["weekly"], {"active_installs": {"2026-W45": 2, "2026-W46": 1, "2026-W47": 1},
                                       "days_used": 2.0, "starts": 2.33, "transcriptions": 4.25, "hours": 1.95})
        r = t["runs"]
        self.assertEqual((r["transcriptions"], r["hours"], r["hosted_share"]), (17, 7.8, 0.235))
        self.assertEqual(r["models"], {"cohere": 8, "whisper_medium": 3, "whisper_large": 1, "other_local": 1, "hosted": 4})
        self.assertEqual({k: v for k, v in r["hosted_services"].items() if v}, {"elevenlabs": 2, "openai": 1, "groq": 1})
        self.assertEqual(r["speaker_models"], {"titanet_small": 5, "titanet_large": 1, "campp": 0, "nemotron": 1})
        self.assertEqual(r["speakers"], {"labelled": 10, "given": 3, "estimated": 6})
        self.assertEqual(r["lengths"], {"under 5": 2, "5-30": 9, "30-60": 3, "over 60": 1})
        self.assertEqual(r["languages"], {"ar": 13, "en": 1, "auto": 1})
        self.assertEqual(t["devices"], {"cpu_runs": 7, "gpu_runs": 4, "gpu_fallbacks": 1, "gpu_share": 0.364,
                                        "fallback_rate": 0.2, "real_time_factor": {"cohere": 0.25, "whisper_medium": 0.5,
                                                                                   "whisper_large": 1.83, "other_local": 0.5,
                                                                                   "hosted": 0.1}})
        self.assertEqual(t["rates"], {"ended": 21, "failed": 0.143, "failed_read": 0.048, "failed_model": 0.048,
                                      "failed_hosted": 0.0, "failed_download": 0.048, "cancelled": 0.048})
        f = t["features"]
        self.assertEqual(f["weeks_using"], {"youtube": 0.5, "editing": 0.667, "compare": 0.25, "combine": 0.333,
                                            "export": 1.0, "copy": 0.333, "assistants": 0.333})
        self.assertEqual({k: f[k] for k in ("youtube", "courses_created", "compares", "combines", "edit_sessions",
                                            "renames", "copies", "assistant_requests", "assistant_saves")},
                         {"youtube": 3, "courses_created": 1, "compares": 2, "combines": 1, "edit_sessions": 2,
                          "renames": 1, "copies": 2, "assistant_requests": 5, "assistant_saves": 1})
        self.assertEqual(f["exports"], {"txt": 3, "srt": 1, "vtt": 1, "md": 2, "json": 1})
        self.assertEqual(t["skipped"], 5)
        published = json.dumps(t)
        for private in ("9F86D081884C7D65", "176174659212345678", "Giza", "Dubai", "California", "Hacked", "AE", '"US"'):
            self.assertNotIn(private, published)

    def test_the_period_comes_from_the_event(self):
        rows = totals.read(self.exports())
        october = totals.totals(rows, "2026-10", "2026-12-03")
        self.assertEqual((october["installs"]["active"], october["runs"]["transcriptions"]), (1, 50),
                         "October's active_month and week, sent in November")
        december = totals.totals(rows, "2026-12", "2026-12-03")
        self.assertEqual((december["installs"]["active"], december["installs"]["new"], december["runs"]["transcriptions"]),
                         (0, 1, 7))
        self.assertEqual([totals.week_month(w) for w in ("2026-W44", "2026-W45", "2026-W48", "2026-W49", "2026-W53",
                                                          "2027-W01", "2026-W54", "nope")],
                         ["2026-10", "2026-11", "2026-11", "2026-12", "2026-12", "2027-01", None, None])

    def test_a_tolerant_reader(self):
        first, _ = november()
        shuffled = ["Country_Code", "Event-Name", "os name", "OS_VERSION", "AppVersion", "Numeric_Props", "Timestamp",
                    "String_Props", "Region_Name", "LOCALE"]
        text = export(first, header=[totals.column_key(h) for h in shuffled], quotes=True)
        text = ",".join(shuffled) + text[text.index("\n"):]
        rows = totals.read(self.write("\ufeff" + text))
        t = totals.totals(rows, "2026-11", "2026-12-03")
        self.assertEqual((t["installs"]["active"], t["runs"]["transcriptions"], t["installs"]["countries"]["installs"]["EG"]),
                         (10, 17, 4))
        one = [{**{k: v for k, v in r.items() if not k.endswith("_props")},
                "props": json.dumps({**json.loads(r["string_props"]), **json.loads(r["numeric_props"])})} for r in first]
        header = [h for h in HEADER if not h.endswith("_props")] + ["props"]
        self.assertEqual(totals.totals(totals.read(self.write(export(one, header=header), "one.csv")), "2026-11", "x")
                         ["runs"]["transcriptions"], 17)
        without = [h for h in HEADER if h not in ("region_name", "locale")]
        t = totals.totals(totals.read(self.write(export(first, header=without), "plain.csv")), "2026-11", "x")
        self.assertEqual((t["installs"]["regions"], t["installs"]["locales"]), ({"other": 10}, {"other": 10}),
                         "without those columns, nothing is named")

    def test_a_missing_column_is_named(self):
        first, _ = november()
        for missing, words in (("country_code", "no country column (looked for country_code or countrycode)"),
                               ("numeric_props", "no props column (looked for string_props and numeric_props, or props)"),
                               ("event_name", "no event column")):
            with self.subTest(missing), self.assertRaises(totals.ExportError) as e:
                totals.read(self.write(export(first, header=[h for h in HEADER if h != missing])))
            self.assertIn(words, str(e.exception))
            self.assertIn("its columns are: timestamp", str(e.exception))
        with self.assertRaises(totals.ExportError) as e:
            totals.read(self.write(""))
        self.assertIn("is empty", str(e.exception))

    def test_the_month_to_total(self):
        self.assertEqual([totals.last_month(d) for d in ("2026-12-03", "2027-01-05", "2026-11-30")],
                         ["2026-11", "2026-12", "2026-10"])
        with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as e:
            totals.main([str(p) for p in self.exports()] + ["--exported", "2027-03-01", "--root", str(self.tmp)])
        self.assertIn("the app's exports have nothing for 2027-02 (they have 2026-10, 2026-11, 2026-12); choose the "
                      "month with --month", str(e.exception.code))

    def test_writing_the_totals(self):
        root = self.tmp / "repo"
        (root / "docs").mkdir(parents=True)
        for page in ("README.md", "docs/13-usage-counts.md"):
            shutil.copy(ROOT / page, root / page)
        paths = [str(p) for p in self.exports()]
        with mock.patch("sys.stdout", io.StringIO()) as out:
            totals.main([*paths, "--exported", "2026-12-03", "--root", str(root)])
        self.assertIn("November 2026: 11 active installs, 5 new, 4 countries, 17 transcriptions, 7.8 hours; 5 week "
                      "summaries left out", out.getvalue())
        data = json.loads((root / "docs" / "usage.json").read_text(encoding="utf-8"))
        self.assertEqual(list(data["months"]), ["2026-11"])
        readme = (root / "README.md").read_text(encoding="utf-8")
        self.assertIn("In November 2026, 11 installs ran Sedjem, in 4 countries, and 5 were new. They made 17 "
                      "transcriptions of 7.8 hours of audio, 24% of them with hosted services.", " ".join(readme.split()))
        self.assertNotIn("No totals yet", readme)
        docs = (root / "docs" / "13-usage-counts.md").read_text(encoding="utf-8")
        self.assertIn("| November 2026 | 11 | 5 | 4 | 17 | 7.8 | 24% |", docs)

        with mock.patch("sys.stdout", io.StringIO()):
            totals.main([*paths, "--month", "2026-10", "--exported", "2026-12-03", "--root", str(root)])
            totals.main([*paths, "--exported", "2026-12-04", "--root", str(root)])  # November again
        data = json.loads((root / "docs" / "usage.json").read_text(encoding="utf-8"))
        self.assertEqual(list(data["months"]), ["2026-10", "2026-11"])
        self.assertEqual(data["months"]["2026-11"]["exported"], "2026-12-04", "a month totalled again is replaced")
        docs = (root / "docs" / "13-usage-counts.md").read_text(encoding="utf-8")
        self.assertLess(docs.index("| November 2026 |"), docs.index("| October 2026 |"), "newest first")
        self.assertEqual(docs.count(totals.START), 1)
        self.assertIn("In November 2026", (root / "README.md").read_text(encoding="utf-8"), "the newest month")

        (root / "README.md").write_text("no markers here\n", encoding="utf-8")
        with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as e:
            totals.main([*paths, "--exported", "2026-12-03", "--root", str(root)])
        self.assertIn("README.md has no <!-- usage-totals:start -->", str(e.exception.code))
        for bad in (["--month", "November"], ["--exported", "yesterday"]):
            with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
                totals.main([*paths, "--root", str(root), *bad])

    def test_the_pages_have_their_place_for_the_totals(self):
        for page in ("README.md", "docs/13-usage-counts.md"):
            text = (ROOT / page).read_text(encoding="utf-8")
            self.assertEqual((text.count(totals.START), text.count(totals.END)), (1, 1), page)
            self.assertLess(text.index(totals.START), text.index(totals.END))

    def test_the_websites_totals(self):
        site = totals.website_totals(totals.read(self.write(export(website()), "website.csv")), "2026-11", "2026-12-03")
        self.assertEqual((site["month"], site["exported"], site["page_views"], site["visitors"]), ("2026-11", "2026-12-03", 16, 8))
        self.assertEqual(site["countries"], {"count": 3, "page_views": {"EG": 6, "other": 10}},
                         "Saudi Arabia (4 views by 2 visitors), Germany (5 by 1) and no country are folded")
        self.assertEqual(site["pages"], {"en": 11, "ar": 4, "404": 1})
        self.assertEqual(site["devices"], {"phone": 6, "tablet": 1, "desktop": 9})
        self.assertEqual(site["referrers"], {"none": 8, "sites": {"linkedin.com": 3, "other": 5}},
                         "github.com (4 views by 2 visitors) and the intranet (1) are folded")
        self.assertEqual(site["downloads"], {"clicks": 5, "platforms": {"windows": 3, "linux": 1, "source": 1}})
        self.assertEqual(site["skipped"], 5)
        published = json.dumps(site)
        for private in ("E1", "S1", "D1", "intranet.example.corp", "github.com", '"SA"', '"DE"', "9F86D081884C7D65"):
            self.assertNotIn(private, published)
        # at most ten sites by name, each with three visitors or more; the rest are "other"
        rows = [{"visitor": (f"2026-11-0{i % 3 + 1}", f"u{site_}"), "site": f"site{site_:02}.example"}
                for site_ in range(12) for i in range(3 + site_)]
        folded = totals.fold_visitors(rows, lambda r: r["site"], most=totals.SITES)
        self.assertEqual(list(folded), [f"site{n:02}.example" for n in range(11, 1, -1)] + ["other"])
        self.assertEqual(folded["other"], 3 + 4)
        # without a user column, every page view counts as a visitor of its own
        rows = totals.read(self.write(export(website(), header=[h for h in HEADER if h != "user_id"]), "plain.csv"))
        self.assertEqual(totals.website_totals(rows, "2026-11", "x")["visitors"], 16)

    def test_writing_the_websites_totals(self):
        root = self.tmp / "repo"
        (root / "docs").mkdir(parents=True)
        for page in ("README.md", "docs/13-usage-counts.md"):
            shutil.copy(ROOT / page, root / page)
        app, site = [str(p) for p in self.exports()], str(self.write(export(website()), "website.csv"))
        with mock.patch("sys.stdout", io.StringIO()) as out:
            totals.main([*app, "--website", site, "--exported", "2026-12-03", "--root", str(root)])
        self.assertIn("The website in November 2026: 16 page views by 8 visitors, 3 countries, 5 download clicks; 5 "
                      "events left out", out.getvalue())
        data = json.loads((root / "docs" / "usage.json").read_text(encoding="utf-8"))
        self.assertEqual((list(data["months"]), list(data["website"])), (["2026-11"], ["2026-11"]))
        readme = (root / "README.md").read_text(encoding="utf-8")
        # the website alone, for another month: the app's months and the README stay as they were
        with mock.patch("sys.stdout", io.StringIO()) as out:
            totals.main(["--website", site, "--month", "2026-10", "--exported", "2026-12-03", "--root", str(root)])
        self.assertNotIn("README.md", out.getvalue())
        data = json.loads((root / "docs" / "usage.json").read_text(encoding="utf-8"))
        self.assertEqual((list(data["months"]), list(data["website"])), (["2026-11"], ["2026-10", "2026-11"]))
        self.assertEqual(data["website"]["2026-10"]["page_views"], 1)
        self.assertEqual((root / "README.md").read_text(encoding="utf-8"), readme)
        for args, words in (([], "give the app's exports, the website's (--website), or both"),
                            (["--website", site, "--month", "2026-09"], "the website's exports have nothing for 2026-09")):
            with self.subTest(words), mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit) as e:
                totals.main([*args, "--root", str(root)])
            self.assertIn(words, str(e.exception.code))

    def test_the_website_reads_what_the_script_writes(self):
        """The page's section of numbers (site/build.py monthly) reads back the newest month docs/usage.json has, with
        the website's totals for the same month."""
        spec = importlib.util.spec_from_file_location("site_build", ROOT / "site" / "build.py")
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        root = self.tmp / "repo"
        (root / "docs").mkdir(parents=True)
        for page in ("README.md", "docs/13-usage-counts.md"):
            shutil.copy(ROOT / page, root / page)
        rows = totals.read(self.exports())
        t = totals.totals(rows, "2026-11", "2026-12-03")
        site = totals.website_totals(totals.read(self.write(export(website()), "website.csv")), "2026-11", "2026-12-03")
        totals.write(root, totals.totals(rows, "2026-10", "2026-12-03"))
        totals.write(root, t, site)
        self.assertEqual(build.monthly(root / "docs" / "usage.json"),
                         {"app": {"month": "2026-11", "installs": 11, "countries": 4, "transcriptions": 17, "hours": 7.8,
                                  "model": "cohere"},
                          "website": {"page_views": 16, "countries": 3, "clicks": 5}})
        self.assertEqual(build.ENGINES, totals.ENGINES)

    def test_the_script_knows_what_the_website_sends(self):
        js = (ROOT / "site" / "counts.js").read_text(encoding="utf-8")
        events = json.loads(re.search(r"var EVENTS = (\{.*?\});", js, re.S).group(1))
        self.assertEqual({name: {k: tuple(v) if v else None for k, v in props.items()} for name, props in events.items()},
                         totals.WEBSITE)
        self.assertEqual(re.search(r"var HOST = /\^(.*?)\$/;", js).group(1), totals.HOST.pattern)

    def test_the_script_knows_what_the_app_sends(self):
        self.assertEqual(totals.SUMMARY, usage.SUMMARY)
        self.assertEqual(totals.VALUES, usage.VALUES)
        self.assertEqual(totals.FLAGS, usage.FLAGS)
        self.assertEqual((totals.ENGINES, totals.SERVICES, totals.FORMATS, totals.LANGUAGES),
                         (usage.ENGINES, usage.SERVICES, usage.FORMATS, usage.LANGUAGES))
        self.assertEqual(set(totals.SPEAKER_MODELS), set(usage.SPEAKER_MODELS.values()))
        self.assertEqual(set(totals.FAILURES), set(usage.FAILURES.values()))
        self.assertEqual(set(totals.SETTINGS) | set(totals.HARDWARE) | {"install_age", "library", "courses"},
                         set(usage.VALUES), "every active_month prop has its place in the totals")


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
        self.assertTrue(set(usage.VALUES) | set(usage.FLAGS) | {"month"} <= named)
        self.assertTrue({v for values in usage.VALUES.values() for v in values} <= named,
                        {v for values in usage.VALUES.values() for v in values} - named)
        self.assertTrue(set(usage.SUMMARY) | {"week"} <= named, set(usage.SUMMARY) - named)
        self.assertIn(f"`{usage.SDK}`", held)
        when = self.section("When something is sent")
        self.assertEqual(re.findall(r"^\| `(\w+)` \|", when, re.M), ["install", "active_month", "week_summary"])

    def test_nothing_is_listed_that_isnt_sent(self):
        held = self.section("What each event holds")
        rows = re.findall(r"^\| (`[^|]+`(?:, `[^|]+`)*) \|", held, re.M)
        listed = {name for cell in rows for name in re.findall(r"`([^`]+)`", cell)}
        sent = set(usage.SYSTEM) | set(usage.VALUES) | set(usage.FLAGS) | set(usage.SUMMARY) | {"month", "week"}
        self.assertEqual(listed, sent)

    def test_the_numbers_in_the_text(self):
        self.assertEqual((usage.START_DELAY, usage.WAKE, usage.RETRY, usage.BATCH, usage.TIMEOUT, usage.KEEP),
                         (20, 900, 3600, 25, 10, 100), "the docs say so: twenty seconds, fifteen minutes, an hour, 25 "
                                                       "in one request, 10 seconds, at most 100")
        for words in ("about twenty seconds after it starts and then every fifteen minutes", "up to 25 in one request",
                      "after 10 seconds", "tried again an hour later", "at most 100, the oldest dropped first",
                      "gets its time as it is sent", "`DO_NOT_TRACK=1`", "`SEDJEM_NO_USAGE=1`", "*Settings → About*",
                      "at least three installs", "https://aptabase.com/legal/privacy", "https://aptabase.com/legal/dpa",
                      "up to five years", "Germany", "article 28", "whose Thursday is in the month"):
            self.assertIn(words, " ".join(self.page.split()))
        self.assertEqual(totals.LEAST, 3)

    def test_the_promise_says_where_and_how_to_turn_it_off(self):
        readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
        self.assertIn("GitHub to check for updates when allowed, and Aptabase for the usage counts below", readme)
        self.assertIn("*Settings → About* or `DO_NOT_TRACK=1` turns them off. See [usage counts](docs/13-usage-counts.md)", readme)
        app = " ".join((ROOT / "docs" / "09-app.md").read_text(encoding="utf-8").split())
        privacy = app.split("## Privacy", 1)[1].split("## ", 1)[0]
        for words in ("Aptabase", "EU region", "`DO_NOT_TRACK=1`", "`SEDJEM_NO_USAGE=1`", "*Settings → About*",
                      "13-usage-counts.md", "some settings", "which models are downloaded", "never the key",
                      "anything an assistant asked"):
            self.assertIn(words, privacy)
        self.assertNotIn("With local models, nothing leaves the computer", app)
        self.assertIn("[Usage counts](13-usage-counts.md)", (ROOT / "docs" / "README.md").read_text(encoding="utf-8"))

    def test_plain_style(self):
        for name in ("docs/13-usage-counts.md", "README.md", "docs/09-app.md", ".github/release-notes/v0.3.1.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("—", text, name)
        self.assertNotIn("**", self.page)

    def test_the_release(self):
        from app import __version__
        notes = (ROOT / ".github" / "release-notes" / f"v{__version__}.md").read_text(encoding="utf-8")
        self.assertIn(f"Sedjem-{__version__}-setup.exe", notes)
        self.assertIn("0.3.0 can update itself: *Settings → Updates*", notes)
        self.assertIn(f'#define AppVersion "{__version__}"', (ROOT / "desktop" / "installer.iss").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
