"""Tests for the details kept with every transcript: this computer and a recording (sysinfo.py), the
details built from a job (app/report.py), the exports that carry them, the job API, and transcribe.py's
.meta.json. Other systems' sources (the Windows registry, missing files) are simulated. No model,
GPU or network is needed; the hosted service is a local stand-in.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import contextlib
import io
import json
import os
import platform
import re
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import sysinfo  # noqa: E402
from app import __version__, engines, report  # noqa: E402
from app import transcript as T  # noqa: E402
from app.config import Config  # noqa: E402
from app.server import make_server  # noqa: E402

IRIS = "Intel(R) Iris(R) Xe Graphics (ADL GT2)"
MACHINE = {"manufacturer": "LENOVO", "model": "ThinkPad L14 Gen 3", "os": "Ubuntu 24.04.4 LTS (Linux 7.0.0-31-generic)",
           "cpu": "12th Gen Intel(R) Core(TM) i5-1245U", "threads": 12, "ram_gb": 15.3, "arch": "x86_64", "gpus": [IRIS]}
LOCAL_JOB = {
    "id": "20260925-214002-ab12", "title": "Weekly sync", "source_name": "Weekly sync.m4a",
    "created": "2026-09-25T21:40:02+03:00", "model": "cohere", "model_title": "Cohere Transcribe Arabic", "kind": "local",
    "options": {"speakers": "auto", "language": "ar", "prompt": ""}, "status": "done", "speaker_names": {"1": "Mona"},
    "source": {"name": "Weekly sync.m4a", "extension": "m4a", "format": "mov,mp4,m4a,3gp,3g2,mj2",
               "format_name": "QuickTime / MOV", "codec": "aac", "codec_name": "AAC (Advanced Audio Coding)",
               "sample_rate": 44100, "channels": 2, "bit_rate": 127000, "size": 48213342, "duration": 3735.4, "video": None},
    "audio_s": 3735.38, "queued": "2026-09-25T21:41:10+03:00", "started": "2026-09-25T21:41:11+03:00",
    "finished": "2026-09-25T22:01:05+03:00", "engine": "cohere",
    "model_file": "cohere-transcribe-arabic-07-2026-Q4_K_M.gguf", "threads": 10, "gpu_setting": "auto",
    "app_version": "0.1.0", "machine": MACHINE, "power": "performance", "device": f"vulkan: {IRIS}",
    "seconds": 1194.2, "rtf": 0.32, "peak_rss_mb": 3100, "voiceprint_model": "nemo_en_titanet_small.onnx",
}
HOSTED_JOB = {
    "id": "20260925-221500-cd34", "title": "Client call", "source_name": "call.mp3", "created": "2026-09-25T22:15:00+03:00",
    "model": "elevenlabs", "model_title": "ElevenLabs Scribe", "kind": "hosted", "status": "done",
    "options": {"speakers": 2, "language": "ar", "prompt": "GitHub,\nJira"},
    "source": {"name": "call.mp3", "extension": "mp3", "format": "mp3", "format_name": "MP2/3 (MPEG audio layer 2/3)",
               "codec": "mp3", "codec_name": "MP3 (MPEG audio layer 3)", "sample_rate": 16000, "channels": 1,
               "bit_rate": 64000, "size": 950000, "duration": 118.8, "video": None},
    "audio_s": 118.8, "engine": "elevenlabs", "service": "ElevenLabs", "api_model": "scribe_v2", "app_version": "0.1.0",
    "machine": MACHINE, "started": "2026-09-25T22:15:03+03:00", "finished": "2026-09-25T22:17:10+03:00",
    "seconds": 127.0, "rtf": 1.069, "detected_language": "ara",
}
OLD_JOB = {  # a transcription made before the details were kept
    "id": "20260901-100000-ef56", "title": "Old call", "source_name": "call.wav", "created": "2026-09-01T10:00:00+03:00",
    "model": "whisper-medium", "model_title": "whisper-medium code-switching", "kind": "local", "status": "done",
    "options": {"speakers": "none", "language": "ar", "prompt": ""}, "speaker_names": {},
    "started": "2026-09-01T10:00:05+03:00", "finished": "2026-09-01T10:01:06+03:00", "seconds": 61.0, "rtf": 0.55,
    "audio_s": 110.9,
}
LINES = [{"start": 0.5, "end": 2.0, "speaker": "1", "text": "تمام، نبدأ"},
         {"start": 2.5, "end": 4.0, "speaker": "2", "text": "ok, the deadline"},
         {"start": 65.0, "end": 66.0, "speaker": "3", "text": "sprint"}]


def rows(d):
    """{(group, label): (value, more)} from report.groups()."""
    return {(g["title"], r["label"]): (r["value"], r["more"]) for g in report.groups(d) for r in g["rows"]}


def tone(seconds, rate=44100, channels=2):
    t = np.arange(int(rate * seconds)) / rate
    x = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    return np.stack([x] * channels, axis=1) if channels > 1 else x


def fake_winreg(values):
    """A stand-in for the winreg module over {key path: {value name: value}}."""
    class Key:
        def __init__(self, path):
            self.values = values[path]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def open_key(_root, path):
        if path not in values:
            raise FileNotFoundError(path)
        return Key(path)

    def query(key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name], 1

    return types.SimpleNamespace(HKEY_LOCAL_MACHINE=object(), OpenKey=open_key, QueryValueEx=query)


# ---------------------------------------------------------------------------------------------
# sysinfo: this computer
# ---------------------------------------------------------------------------------------------

class MachineTests(unittest.TestCase):
    def setUp(self):
        sysinfo._machine.cache_clear()

    def tearDown(self):
        sysinfo._machine.cache_clear()

    def test_this_computer(self):
        m = sysinfo.machine()
        self.assertEqual(set(m), set(sysinfo.MACHINE_KEYS))
        self.assertEqual(m["threads"], os.cpu_count())
        self.assertGreater(m["ram_gb"], 0.5)
        self.assertTrue(m["os"])
        self.assertTrue(m["arch"])
        self.assertIsNone(m["gpus"], "nobody looked for graphics cards")
        json.dumps(m)
        if sys.platform.startswith("linux"):
            self.assertIn(f"(Linux {platform.release()})", m["os"])
            if "model name" in Path("/proc/cpuinfo").read_text(errors="replace"):
                self.assertTrue(m["cpu"])
            if (sysinfo.DMI / "sys_vendor").exists():
                self.assertTrue(m["manufacturer"])

    def test_read_once_and_given_as_a_copy(self):
        with mock.patch.object(sysinfo, "_linux", wraps=sysinfo._linux) as probe, \
                mock.patch.object(sysinfo.sys, "platform", "linux"):
            first = sysinfo.machine()
            second = sysinfo.machine([IRIS, None])
        self.assertEqual(probe.call_count, 1)
        self.assertEqual(second["gpus"], [IRIS])
        first["cpu"] = "changed"
        self.assertNotEqual(sysinfo.machine()["cpu"], "changed")

    def test_linux_fields_from_the_system_files(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "dmi").mkdir()
            for name, value in (("sys_vendor", "LENOVO\n"), ("product_name", "21C2S3G307\n"),
                                ("product_version", "ThinkPad L14 Gen 3\n")):
                (d / "dmi" / name).write_text(value)
            (d / "os-release").write_text('NAME="Ubuntu"\nPRETTY_NAME="Ubuntu 24.04.4 LTS"\nID=ubuntu\n')
            (d / "cpuinfo").write_text("processor\t: 0\nvendor_id\t: GenuineIntel\n"
                                       "model name\t: 12th Gen Intel(R) Core(TM) i5-1245U\n")
            with mock.patch.object(sysinfo, "DMI", d / "dmi"), mock.patch.object(sysinfo, "OS_RELEASE", d / "os-release"), \
                    mock.patch.object(sysinfo, "CPUINFO", d / "cpuinfo"), mock.patch.object(sysinfo, "KDE_ABOUT", d / "none"), \
                    mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": ""}), \
                    mock.patch("platform.release", return_value="7.0.0-31-generic"), \
                    mock.patch("os.sysconf", create=True, side_effect=lambda k: 4096 if k == "SC_PAGE_SIZE" else 4_000_000):
                info = sysinfo._linux()
        self.assertEqual(info, {"manufacturer": "LENOVO", "model": "ThinkPad L14 Gen 3",
                                "os": "Ubuntu 24.04.4 LTS (Linux 7.0.0-31-generic)",
                                "cpu": "12th Gen Intel(R) Core(TM) i5-1245U", "ram": 4096 * 4_000_000})

    def test_kde_editions_and_the_desktop_are_named(self):
        """Kubuntu keeps Ubuntu's os-release; the name comes from KDE's About file, and the desktop is named."""
        def os_name(release, about, desktop, plasma="5.27.12"):
            with tempfile.TemporaryDirectory() as d:
                d = Path(d)
                (d / "os-release").write_text(release)
                (d / "about").write_text(about)
                (d / "xsessions").mkdir()
                (d / "xsessions" / "plasma.desktop").write_text(f"[Desktop Entry]\nName=Plasma (X11)\nX-KDE-PluginInfo-Version={plasma}\n")
                with mock.patch.object(sysinfo, "OS_RELEASE", d / "os-release"), mock.patch.object(sysinfo, "KDE_ABOUT", d / "about"), \
                        mock.patch.object(sysinfo, "SESSIONS", (d / "wayland-sessions", d / "xsessions")), \
                        mock.patch.dict(os.environ, {"XDG_CURRENT_DESKTOP": desktop}), \
                        mock.patch("platform.release", return_value="7.0.0-31-generic"):
                    return sysinfo._linux_os()
        ubuntu = 'NAME="Ubuntu"\nPRETTY_NAME="Ubuntu 24.04.4 LTS"\nID=ubuntu\n'
        kubuntu = "[General]\nLogoPath=/usr/share/kubuntu-default-settings/kubuntu-circle-128.png\nWebsite=https://www.kubuntu.org\nName=Kubuntu\n"
        self.assertEqual(os_name(ubuntu, kubuntu, "KDE"), "Kubuntu 24.04.4 LTS, KDE Plasma 5.27 (Linux 7.0.0-31-generic)")
        # the same Kubuntu packages, but a GNOME session: that is Ubuntu's own desktop
        self.assertEqual(os_name(ubuntu, kubuntu, "ubuntu:GNOME"), "Ubuntu 24.04.4 LTS, GNOME (Linux 7.0.0-31-generic)")
        # KDE neon already says so in its os-release
        self.assertEqual(os_name('NAME="KDE neon"\nPRETTY_NAME="KDE neon 6.2"\n', "[General]\nName=KDE neon\n", "KDE", "6.2.4"),
                         "KDE neon 6.2, KDE Plasma 6.2 (Linux 7.0.0-31-generic)")
        # outside a desktop session (over SSH) the edition is still named
        self.assertEqual(os_name(ubuntu, kubuntu, ""), "Kubuntu 24.04.4 LTS (Linux 7.0.0-31-generic)")

    def test_missing_files_and_failing_calls_give_none(self):
        missing = Path(tempfile.gettempdir()) / "sedjem-no-such-folder"
        with mock.patch.object(sysinfo, "DMI", missing), mock.patch.object(sysinfo, "OS_RELEASE", missing / "os-release"), \
                mock.patch.object(sysinfo, "CPUINFO", missing / "cpuinfo"), \
                mock.patch("os.sysconf", create=True, side_effect=ValueError("no")), \
                mock.patch("platform.release", side_effect=OSError("no")), \
                mock.patch("platform.machine", side_effect=OSError("no")), \
                mock.patch("os.cpu_count", return_value=None), mock.patch.object(sysinfo.sys, "platform", "linux"):
            m = sysinfo.machine()
        self.assertEqual(m, dict.fromkeys(sysinfo.MACHINE_KEYS))

    def test_other_systems_never_raise(self):
        """The Windows and macOS branches run here without winreg, WinDLL or a working sysctl."""
        for name in ("win32", "darwin"):
            sysinfo._machine.cache_clear()
            with mock.patch.object(sysinfo.sys, "platform", name), \
                    mock.patch("subprocess.run", side_effect=FileNotFoundError("sysctl")):
                m = sysinfo.machine(["GPU"])
            self.assertEqual(set(m), set(sysinfo.MACHINE_KEYS), name)
            self.assertEqual(m["gpus"], ["GPU"])

    def test_windows_from_the_registry(self):
        values = {r"HARDWARE\DESCRIPTION\System\BIOS": {"SystemManufacturer": "LENOVO", "SystemProductName": "21D6CTO1WW",
                                                        "SystemVersion": "ThinkPad P16 Gen 1"},
                  r"HARDWARE\DESCRIPTION\System\CentralProcessor\0": {
                      "ProcessorNameString": "12th Gen Intel(R) Core(TM) i7-12800HX   "},
                  r"SOFTWARE\Microsoft\Windows NT\CurrentVersion": {"ProductName": "Windows 10 Pro", "DisplayVersion": "24H2",
                                                                    "CurrentBuild": "26100", "UBR": 4061}}
        with mock.patch.dict(sys.modules, {"winreg": fake_winreg(values)}), \
                mock.patch.object(sysinfo, "_windows_ram", return_value=32 * 2 ** 30):
            info = sysinfo._windows()
        self.assertEqual(info, {"manufacturer": "LENOVO", "model": "ThinkPad P16 Gen 1",
                                "os": "Windows 11 Pro 24H2 (build 26100.4061)",  # the registry still says 10
                                "cpu": "12th Gen Intel(R) Core(TM) i7-12800HX", "ram": 32 * 2 ** 30})
        values = {r"HARDWARE\DESCRIPTION\System\BIOS": {"SystemManufacturer": "HUAWEI", "SystemProductName": "MCLF-X",
                                                        "SystemVersion": "M1010"},
                  r"SOFTWARE\Microsoft\Windows NT\CurrentVersion": {"ProductName": "Windows 10 Home", "ReleaseId": "2009",
                                                                    "CurrentBuild": "19045"}}
        with mock.patch.dict(sys.modules, {"winreg": fake_winreg(values)}), \
                mock.patch.object(sysinfo, "_windows_ram", side_effect=OSError("no")):
            info = sysinfo._windows()
        self.assertEqual(info, {"manufacturer": "HUAWEI", "model": "MCLF-X", "os": "Windows 10 Home 2009 (build 19045)",
                                "cpu": None, "ram": None})

    def test_model_names(self):
        self.assertEqual(sysinfo.model_name("LENOVO", "21C2S3G307", "ThinkPad L14 Gen 3"), "ThinkPad L14 Gen 3")
        self.assertEqual(sysinfo.model_name("LENOVO", "20XW0055GE", "None"), "20XW0055GE")
        self.assertEqual(sysinfo.model_name("HUAWEI", "MCLF-X", "M1010"), "MCLF-X")
        self.assertEqual(sysinfo.model_name("Dell Inc.", "XPS 13 9310", ""), "XPS 13 9310")
        self.assertIsNone(sysinfo.model_name("To be filled by O.E.M.", "To Be Filled By O.E.M.", "Default string"))

    def test_peak_memory_and_times(self):
        self.assertGreater(sysinfo.peak_memory_mb(), 10)
        now = datetime.fromisoformat(sysinfo.local_time())
        self.assertIsNotNone(now.tzinfo)
        self.assertLess(abs(now.timestamp() - time.time()), 5)
        self.assertEqual(datetime.fromisoformat(sysinfo.local_time(1_790_000_000)).timestamp(), 1_790_000_000)


# ---------------------------------------------------------------------------------------------
# sysinfo: a recording
# ---------------------------------------------------------------------------------------------

class RecordingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="sedjem-rec-"))
        cls.wav, cls.flac, cls.text = cls.tmp / "call.wav", cls.tmp / "note.flac", cls.tmp / "notes.txt"
        sf.write(cls.wav, tone(1.5), 44100, subtype="PCM_16")
        sf.write(cls.flac, tone(1.0, rate=16000, channels=1), 16000, format="FLAC")
        cls.text.write_text("not audio")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_wav(self):
        info = sysinfo.recording(self.wav)
        self.assertEqual(set(info), set(sysinfo.RECORDING_KEYS))
        self.assertEqual({k: info[k] for k in ("name", "extension", "format", "codec", "sample_rate", "channels",
                                               "bit_rate", "size", "video")},
                         {"name": "call.wav", "extension": "wav", "format": "wav", "codec": "pcm_s16le",
                          "sample_rate": 44100, "channels": 2, "bit_rate": 44100 * 16 * 2,
                          "size": self.wav.stat().st_size, "video": None})
        self.assertAlmostEqual(info["duration"], 1.5, places=2)
        self.assertEqual(report.audio_text(info), "PCM 16-bit, 44.1 kHz, stereo")

    def test_flac_under_its_original_name(self):
        info = sysinfo.recording(self.flac, name="Weekly Sync.FLAC")  # an upload is stored as source.flac
        self.assertEqual((info["name"], info["extension"], info["format"], info["codec"]),
                         ("Weekly Sync.FLAC", "flac", "flac", "flac"))
        self.assertEqual((info["sample_rate"], info["channels"]), (16000, 1))
        self.assertAlmostEqual(info["duration"], 1.0, places=2)
        self.assertGreater(info["bit_rate"], 0)  # FLAC has no stream bit rate: the file's own
        self.assertEqual(report.audio_text(info), "FLAC, 16 kHz, mono")

    def test_not_a_recording_or_missing(self):
        info = sysinfo.recording(self.text)
        self.assertEqual((info["name"], info["extension"], info["size"], info["codec"], info["duration"]),
                         ("notes.txt", "txt", 9, None, None))
        info = sysinfo.recording(self.tmp / "gone.m4a")
        self.assertEqual((info["name"], info["extension"], info["size"], info["format"]), ("gone.m4a", "m4a", None, None))


# ---------------------------------------------------------------------------------------------
# The details of a job, and the exports
# ---------------------------------------------------------------------------------------------

class DetailsTests(unittest.TestCase):
    def test_a_local_job(self):
        d = report.details(LOCAL_JOB, LINES, edited=True)
        self.assertEqual(list(d), ["recording", "model", "run", "computer"])
        self.assertEqual(d["recording"]["codec"], "aac")
        self.assertEqual(d["model"]["file"], "cohere-transcribe-arabic-07-2026-Q4_K_M.gguf")
        self.assertEqual((d["run"]["speakers_found"], d["run"]["edited"], d["run"]["threads"]), (3, True, 10))
        self.assertEqual(d["computer"], MACHINE)
        json.dumps(d)
        self.assertEqual([g["title"] for g in report.groups(d)], ["Recording", "Model", "Run", "Computer"])
        expected = {
            ("Recording", "File"): ("Weekly sync.m4a", False),
            ("Recording", "Audio"): ("AAC, 44.1 kHz, stereo", False),
            ("Recording", "Container"): ("MP4 (QuickTime)", True),
            ("Recording", "Bit rate"): ("127 kb/s", True),
            ("Recording", "Size"): ("48 MB", True),
            ("Recording", "Length"): ("1:02:15", True),
            ("Model", "Name"): ("Cohere Transcribe Arabic", False),
            ("Model", "ID"): ("cohere", True),
            ("Model", "Engine"): ("Cohere (transcribe.cpp)", True),
            ("Model", "File"): ("cohere-transcribe-arabic-07-2026-Q4_K_M.gguf", True),
            ("Run", "Ran on"): ("Graphics card: Intel Iris Xe Graphics (Vulkan)", False),
            ("Run", "Took"): ("20 min (0.32× real time)", False),
            ("Run", "Language"): ("Arabic + English", False),
            ("Run", "Speakers"): ("Auto-detect (3 found)", False),
            ("Run", "Edited"): ("Yes, corrected by hand", False),
            ("Run", "Power mode"): ("Performance", True),
            ("Run", "Threads"): ("10 of 12", True),
            ("Run", "Use GPU"): ("Yes", True),
            ("Run", "Added"): ("2026-09-25 21:40", True),
            ("Run", "Queued"): ("2026-09-25 21:41", True),
            ("Run", "Started"): ("2026-09-25 21:41", True),
            ("Run", "Finished"): ("2026-09-25 22:01", True),
            ("Run", "Peak memory"): ("3.0 GB", True),
            ("Run", "Speaker model"): ("nemo_en_titanet_small.onnx", True),
            ("Run", "App"): ("Sedjem 0.1.0", True),
            ("Computer", "Computer"): ("LENOVO ThinkPad L14 Gen 3", False),
            ("Computer", "Processor"): ("12th Gen Intel Core i5-1245U, 12 threads", False),
            ("Computer", "Memory"): ("15.3 GB", True),
            ("Computer", "Graphics"): ("Intel Iris Xe Graphics", True),
            ("Computer", "System"): ("Ubuntu 24.04.4 LTS (Linux 7.0.0-31-generic), x86_64", True),
        }
        self.assertEqual(rows(d), expected)
        self.assertEqual(report.summary(d, "Weekly sync"), [
            "Title: Weekly sync",
            "Recording: Weekly sync.m4a, 1:02:15, AAC, 44.1 kHz, stereo",
            "Model: Cohere Transcribe Arabic, took 20 min (0.32× real time)",
            "Ran on: LENOVO ThinkPad L14 Gen 3, Graphics card: Intel Iris Xe Graphics (Vulkan)",
            "Made: 2026-09-25 22:01 with Sedjem 0.1.0"])

    def test_a_hosted_job(self):
        d = report.details(HOSTED_JOB, LINES[:2])
        r = rows(d)
        self.assertEqual(r[("Model", "Name")], ("ElevenLabs Scribe (scribe_v2)", False))
        self.assertEqual(r[("Model", "Service")], ("ElevenLabs", False))
        self.assertEqual(r[("Run", "Ran on")], ("ElevenLabs", False))
        self.assertEqual(r[("Run", "Took")], ("2 min (1.07× real time)", False))
        self.assertEqual(r[("Run", "Language")], ("Arabic + English (detected: ara)", False))
        self.assertEqual(r[("Run", "Speakers")], ("2 (2 found)", False))
        self.assertEqual(r[("Run", "Vocabulary")], ("GitHub, Jira", False), "one line, whatever was typed")
        self.assertEqual(r[("Recording", "Audio")], ("MP3, 16 kHz, mono", False))
        self.assertTrue(r[("Computer", "Computer")][1], "for a hosted model the computer is under More")
        for key in (("Model", "Engine"), ("Model", "File"), ("Run", "Threads"), ("Run", "Use GPU"), ("Run", "Power mode")):
            self.assertNotIn(key, r)
        self.assertEqual(report.summary(d)[1:], ["Model: ElevenLabs Scribe (scribe_v2), took 2 min (1.07× real time)",
                                                 "Ran on: ElevenLabs", "Made: 2026-09-25 22:17 with Sedjem 0.1.0"])

    def test_computer_details_that_could_not_be_read_say_so(self):
        job = {**LOCAL_JOB, "machine": {**dict.fromkeys(sysinfo.MACHINE_KEYS), "cpu": "Apple M2", "threads": 8}}
        r = rows(report.details(job, []))
        self.assertEqual(r[("Computer", "Processor")][0], "Apple M2, 8 threads")
        for label in ("Computer", "Memory", "Graphics", "System"):
            self.assertEqual(r[("Computer", label)][0], "Not detected", label)
        # an older job recorded no computer at all: nothing is claimed about it
        self.assertFalse([k for k in rows(report.details({**LOCAL_JOB, "machine": None}, [])) if k[0] == "Computer"])

    def test_an_older_job_without_the_new_fields(self):
        d = report.details(OLD_JOB, [])
        json.dumps(d)
        r = rows(d)
        self.assertEqual([g["title"] for g in report.groups(d)], ["Recording", "Model", "Run"], "no Computer group")
        self.assertEqual(r[("Recording", "File")], ("call.wav", False))
        self.assertEqual(r[("Recording", "Length")], ("01:50", True))
        self.assertEqual(r[("Run", "Speakers")], ("No labels", False))
        self.assertEqual(r[("Run", "Took")], ("1 min (0.55× real time)", False))
        self.assertNotIn(("Run", "Ran on"), r)
        for value, _ in r.values():
            self.assertNotRegex(value, r"None|null|undefined|False")
        self.assertEqual(report.summary(d), ["Recording: call.wav, 01:50", "Model: whisper-medium code-switching, "
                                             "took 1 min (0.55× real time)", "Made: 2026-09-01 10:01"])

    def test_odd_values_never_break_it(self):
        job = {**OLD_JOB, "source": "?", "machine": 5, "options": None, "rtf": "fast", "seconds": None,
               "peak_rss_mb": True, "status": "failed", "device": "cpu (after a GPU error)"}
        d = report.details(job)
        r = rows(d)
        self.assertEqual(r[("Run", "Ran on")][0], "Processor (the graphics card failed, so it finished there)")
        self.assertNotIn(("Run", "Took"), r)
        self.assertIn("Status: failed", report.summary(d))

    def test_run_facts(self):
        home = Path(tempfile.mkdtemp(prefix="sedjem-facts-"))
        try:
            (home / "app_data").mkdir()
            cfg = Config(home=home)
            cfg.save_settings(threads=3, device="cpu")
            facts = report.run_facts(cfg, cfg.models["cohere"], [IRIS])
            self.assertEqual({k: facts[k] for k in ("engine", "model_file", "threads", "gpu_setting", "app_version")},
                             {"engine": "cohere", "model_file": "cohere-transcribe-arabic-07-2026-Q4_K_M.gguf",
                              "threads": 3, "gpu_setting": "cpu", "app_version": __version__})
            self.assertEqual(facts["machine"]["gpus"], [IRIS])
            facts = report.run_facts(cfg, cfg.models["whisper-medium"])
            self.assertEqual((facts["model_file"], facts["machine"]["gpus"]), ("whisper-medium-arabic-codeswitched-ct2", None))
            facts = report.run_facts(cfg, cfg.models["speechmatics"])
            self.assertEqual({k: facts.get(k) for k in ("engine", "service", "api_model", "threads", "model_file")},
                             {"engine": "speechmatics", "service": "Speechmatics", "api_model": "enhanced",
                              "threads": None, "model_file": None})
        finally:
            shutil.rmtree(home, ignore_errors=True)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.d = report.details(LOCAL_JOB, LINES, edited=False)

    def test_text_starts_with_a_short_header(self):
        plain = T.to_txt(LOCAL_JOB, LINES)
        self.assertTrue(plain.startswith(f"{T.RLM}[00:00] Mona: تمام، نبدأ\n"), "without details: the lines only")
        text = T.to_txt(LOCAL_JOB, LINES, self.d)
        head, blank, rest = text.partition("\n\n")
        self.assertEqual(head.splitlines(), report.summary(self.d, "Weekly sync"))
        self.assertEqual(rest, plain)

    def test_webvtt_note(self):
        job = {**LOCAL_JOB, "title": "Sync --> plan\nnext"}  # nothing typed may end the note or start a cue
        vtt = T.to_vtt(job, LINES, report.details(job, LINES))
        self.assertTrue(vtt.startswith("WEBVTT\n\nNOTE\nTitle: Sync -> plan next\nRecording: "))
        note = vtt.split("\n\n")[1]
        self.assertNotIn("-->", note)
        self.assertEqual(vtt.count("-->"), len(LINES))
        self.assertIn("00:00:02.500 --> 00:00:04.000\n<v Speaker 2>ok, the deadline", vtt)

    def test_markdown_ends_with_the_details(self):
        md = T.to_md(LOCAL_JOB, LINES, self.d)
        body, _, details = md.partition("## Details")
        self.assertIn("sprint", body)
        for line in ("### Recording", "- File: Weekly sync.m4a", "### Run",
                     "- Ran on: Graphics card: Intel Iris Xe Graphics (Vulkan)", "- Peak memory: 3.0 GB",
                     "### Computer", "- Computer: LENOVO ThinkPad L14 Gen 3"):
            self.assertIn(f"\n{line}\n", details)
        self.assertNotIn("Edited", details, "not edited")

    def test_json_has_the_whole_details(self):
        data = json.loads(T.to_json(LOCAL_JOB, LINES, self.d))
        self.assertEqual(data["details"], json.loads(json.dumps(self.d)))
        self.assertNotIn("details", json.loads(T.to_json(LOCAL_JOB, LINES)))

    def test_srt_stays_as_it_is(self):
        self.assertEqual(T.to_srt(LOCAL_JOB, LINES, self.d), T.to_srt(LOCAL_JOB, LINES))


class RightToLeftTests(unittest.TestCase):
    """Mostly Arabic lines start with a right-to-left mark in the text, subtitle and Markdown exports."""
    LINES = [{"start": 1.0, "end": 2.0, "speaker": "1", "text": "order ال project بتاعنا"},  # opens in English
             {"start": 3.0, "end": 4.0, "speaker": "2", "text": "ok, the deadline بكرة"},
             {"start": 5.0, "end": 6.0, "speaker": None, "text": "تمام"}]
    JOB = {"title": "Sync", "speaker_names": {"1": "Mona"}, "model": "x", "audio_s": 6}

    def test_mostly_arabic_as_on_the_page(self):
        cases = {"تمام، نبدأ": True, "order ال project بتاعنا": True, "الـdata جاهزة": True,
                 "ok, the deadline بكرة": False, "ok, the deadline": False, "12:30 - 45": False, "": False,
                 "ا ب ت d e f g h i j": True,  # 3 of 10 words: exactly 30%
                 "ا ب c d e f g h i j": False}
        for text, expected in cases.items():
            self.assertIs(T.mostly_arabic(text), expected, text)

    def test_the_exports(self):
        m = T.RLM
        self.assertEqual(T.to_txt(self.JOB, self.LINES).splitlines(),
                         [f"{m}[00:01] Mona: order ال project بتاعنا", "[00:03] Speaker 2: ok, the deadline بكرة",
                          f"{m}[00:05] تمام"])
        srt = T.to_srt(self.JOB, self.LINES)
        self.assertIn(f"\n00:00:01,000 --> 00:00:02,000\n{m}Mona: order", srt)
        self.assertIn("\n00:00:03,000 --> 00:00:04,000\nSpeaker 2: ok", srt)
        vtt = T.to_vtt(self.JOB, self.LINES)
        self.assertIn(f"\n00:00:01.000 --> 00:00:02.000\n{m}<v Mona>order", vtt)
        self.assertIn("\n00:00:03.000 --> 00:00:04.000\n<v Speaker 2>ok", vtt)
        self.assertIn(f"\n00:00:05.000 --> 00:00:06.000\n{m}تمام\n", vtt)
        md = T.to_md(self.JOB, self.LINES)
        self.assertIn(f"\n{m}**Mona** `00:01`  \n{m}order ال project بتاعنا\n", md)
        self.assertIn("\n**Speaker 2** `00:03`  \nok, the deadline بكرة\n", md)
        self.assertIn(f"\n{m}`00:05` تمام\n", md)
        self.assertTrue(md.startswith("# Sync\n"))
        self.assertNotIn(m, T.to_json(self.JOB, self.LINES), "JSON keeps the text as it is")


class VendorLogoTests(unittest.TestCase):
    def test_every_name_has_a_logo_and_the_page_loads_them_first(self):
        """app/static/brands.js: a name pointing to a missing logo would break the whole Details panel."""
        js = (ROOT / "app" / "static" / "brands.js").read_text(encoding="utf-8")
        glyphs = dict(re.findall(r'^  (\w+): \{ title: "[^"]+", hex: "([0-9A-F]{6})",\n    path: "M[^"]+" \},$', js, re.M))
        files = re.findall(r'^  (\w+): \{ title: "[^"]+", file: "([\w.-]+)"(?:, dark: "([\w.-]+)")?(?:, (?:fill|wide): true)? \},', js, re.M)
        names = re.findall(r'^  \["(\w+)", /.+/i\],$', js, re.M)
        self.assertGreater(len(glyphs), 10)
        self.assertGreater(len(files), 10)
        self.assertEqual(sorted(set(names)), sorted(set(glyphs) | {slug for slug, *_ in files}))
        # each logo file is there, and each file there is used and listed in SOURCES.md
        folder = ROOT / "app" / "static" / "logos"
        used = {f for _, *pair in files for f in pair if f}
        self.assertEqual(sorted(used), sorted(p.name for p in folder.iterdir() if p.name != "SOURCES.md"))
        sources = (folder / "SOURCES.md").read_text(encoding="utf-8")
        for f in used:
            self.assertIn(f"`{f}`", sources)
            if f.endswith(".svg"):  # cleaned: no scripts, external references or fixed size
                svg = (folder / f).read_text(encoding="utf-8")
                self.assertNotRegex(svg, r"<script|href=\"(?!#)|<svg[^>]*\s(width|height)=")
        html = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")
        self.assertLess(html.index('src="/static/brands.js"'), html.index('src="/static/app.js"'))


# ---------------------------------------------------------------------------------------------
# Through the app: a job records its details, the API gives them, the exports carry them
# ---------------------------------------------------------------------------------------------

REPLY = {"language_code": "ara", "text": "تمام test", "transcription_id": "tr_1", "words": [
    {"text": "تمام", "start": 0.1, "end": 0.5, "type": "word", "speaker_id": "speaker_0"},
    {"text": " ", "start": 0.5, "end": 0.6, "type": "spacing", "speaker_id": "speaker_0"},
    {"text": "test", "start": 0.9, "end": 1.2, "type": "word", "speaker_id": "speaker_1"}]}


class FakeElevenLabs(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.reply(REPLY)

    def do_DELETE(self):
        self.reply({})


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="sedjem-details-"))
        cls.mock = ThreadingHTTPServer(("127.0.0.1", 0), FakeElevenLabs)
        threading.Thread(target=cls.mock.serve_forever, daemon=True).start()
        storage = cls.tmp / "app_data"
        storage.mkdir()
        (storage / "config.toml").write_text(
            f'[[models]]\nid = "elevenlabs"\nbase_url = "http://127.0.0.1:{cls.mock.server_address[1]}"\n')
        os.environ.pop("ELEVENLABS_API_KEY", None)
        cls.cfg = Config(storage=storage, home=cls.tmp)
        cls.cfg.save_key("elevenlabs", "test-key")
        gpu = {"devices": [{"name": IRIS, "kind": "vulkan", "type": "igpu", "memory": 0}], "cuda_devices": 0, "cuda_libs": None}
        with mock.patch.object(engines, "gpu_info", return_value=gpu):
            cls.server, cls.app = make_server(cls.cfg, port=0)
            for _ in range(200):  # the check runs in the background
                if cls.app.gpu:
                    break
                time.sleep(0.02)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.wav = cls.tmp / "Weekly sync.wav"
        sf.write(cls.wav, tone(1.5), 44100, subtype="PCM_16")

    @classmethod
    def tearDownClass(cls):
        cls.app.runner.shutdown()
        cls.server.shutdown()
        cls.server.server_close()
        cls.mock.shutdown()
        cls.mock.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, method, path, body=None, raw=None, headers=None):
        h = {"X-Sedjem": "1", **(headers or {})}
        data = raw
        if body is not None:
            data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                payload = r.read()
                return r.status, json.loads(payload) if "json" in r.headers.get("Content-Type", "") else payload.decode()
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def wait(self, jid, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            _, r = self.call("GET", f"/api/jobs/{jid}")
            if r["job"]["status"] in ("done", "failed", "cancelled"):
                return r
            time.sleep(0.2)
        self.fail(f"job {jid} still {r['job']['status']}")

    def test_a_job_records_and_returns_its_details(self):
        status, job = self.call("POST", "/api/jobs?model=elevenlabs&speakers=auto&language=ar&title=Weekly%20sync"
                                        "&name=Weekly%20sync.wav&confirm_upload=1",
                                raw=self.wav.read_bytes(), headers={"Content-Type": "audio/wav"})
        self.assertEqual(status, 201, job)
        r = self.wait(job["id"])
        self.assertEqual(r["job"]["status"], "done", r)
        j = r["job"]
        self.assertFalse(list((self.cfg.storage / "jobs" / j["id"]).glob("source.*")), "the upload itself is gone")
        self.assertEqual({k: j["source"][k] for k in ("name", "extension", "format", "codec", "sample_rate", "channels")},
                         {"name": "Weekly sync.wav", "extension": "wav", "format": "wav", "codec": "pcm_s16le",
                          "sample_rate": 44100, "channels": 2})
        self.assertEqual((j["engine"], j["service"], j["api_model"], j["app_version"]),
                         ("elevenlabs", "ElevenLabs", "scribe_v2", __version__))
        self.assertLessEqual(j["created"], j["queued"])
        self.assertLessEqual(j["queued"], j["started"])
        self.assertEqual(j["machine"]["gpus"], [IRIS], "the app's GPU check, passed in")
        self.assertEqual(j["machine"]["threads"], os.cpu_count())
        self.assertEqual(r["details"], json.loads(json.dumps(report.details(j, r["lines"], False))))
        self.assertEqual(r["details"]["run"]["speakers_found"], 2)
        titles = [g["title"] for g in r["detail_groups"]]
        self.assertEqual(titles, ["Recording", "Model", "Run", "Computer"])

        jid = j["id"]
        _, text = self.call("GET", f"/api/jobs/{jid}/export/txt")
        self.assertTrue(text.startswith("Title: Weekly sync\nRecording: Weekly sync.wav, 00:01, PCM 16-bit, 44.1 kHz, stereo\n"),
                        text)
        _, plain = self.call("GET", f"/api/jobs/{jid}/export/txt?details=0")
        self.assertTrue(plain.startswith(f"{T.RLM}[00:00] Speaker 1: تمام"), plain)
        self.assertTrue(text.endswith("\n\n" + plain))
        _, data = self.call("GET", f"/api/jobs/{jid}/export/json")
        self.assertEqual(data["details"]["recording"]["codec"], "pcm_s16le")
        _, md = self.call("GET", f"/api/jobs/{jid}/export/md")
        self.assertIn("\n## Details\n", md)
        _, vtt = self.call("GET", f"/api/jobs/{jid}/export/vtt")
        self.assertTrue(vtt.startswith("WEBVTT\n\nNOTE\nTitle: Weekly sync\n"))
        _, srt = self.call("GET", f"/api/jobs/{jid}/export/srt")
        self.assertTrue(srt.startswith("1\n00:00:00,100 --> "))

        self.call("PATCH", f"/api/jobs/{jid}", {"lines": r["lines"][:1]})
        _, r = self.call("GET", f"/api/jobs/{jid}")
        self.assertIn({"label": "Edited", "value": "Yes, corrected by hand", "more": False},
                      next(g for g in r["detail_groups"] if g["title"] == "Run")["rows"])

        status, new = self.call("POST", f"/api/jobs/{jid}/rerun", {"model": "elevenlabs", "confirm_upload": True})
        self.assertEqual(status, 201, new)
        again = self.wait(new["id"])["job"]
        self.assertEqual(again["source"], j["source"], "a rerun keeps the recording's details")


# ---------------------------------------------------------------------------------------------
# transcribe.py's .meta.json
# ---------------------------------------------------------------------------------------------

class FakeEngine:
    """Stands in for a speech model: loads nothing, returns the same words for every chunk."""
    name, device, prompt = "fake", "cpu", None

    def __init__(self, args):
        pass

    def __call__(self, audio):
        return "كلام"


class MetaJsonTests(unittest.TestCase):
    def test_the_computer_the_input_file_and_the_times(self):
        import transcribe
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "call.wav"
            sf.write(wav, tone(2.0, rate=16000, channels=1), 16000, subtype="PCM_16")
            argv = ["transcribe.py", str(wav), "--out", tmp]
            with mock.patch.dict(transcribe.ENGINES, {"whisper": FakeEngine}), mock.patch.object(sys, "argv", argv), \
                    contextlib.redirect_stdout(io.StringIO()):
                transcribe.main()
            meta = json.loads((Path(tmp) / "call.fake.meta.json").read_text(encoding="utf-8"))
        self.assertEqual({k: meta["source"][k] for k in ("name", "codec", "sample_rate", "channels")},
                         {"name": "call.wav", "codec": "pcm_s16le", "sample_rate": 16000, "channels": 1})
        self.assertEqual(set(meta["machine"]), set(sysinfo.MACHINE_KEYS))
        self.assertEqual(meta["machine"]["threads"], os.cpu_count())
        started, finished = datetime.fromisoformat(meta["started"]), datetime.fromisoformat(meta["finished"])
        self.assertLessEqual(started, finished)
        self.assertLess((finished - started).total_seconds(), meta["seconds"] + 2)
        self.assertGreater(meta["peak_rss_mb"], 10)
        self.assertEqual((meta["engine"], meta["device"]), ("fake", "cpu"))


if __name__ == "__main__":
    unittest.main()
