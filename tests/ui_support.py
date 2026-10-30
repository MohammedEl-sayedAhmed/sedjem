"""Helpers for the browser tests (tests/test_ui*.py): a Sedjem server on a free port with a throwaway
data folder and demo transcripts, and a headless Chromium driven by Playwright. Nothing here touches your
own data or the network.

The browser: the Chromium that `playwright install chromium` downloads if it is there, else the installed
Chrome or Edge (GitHub's runners have both). Set SEDJEM_UI_HEADED=1 to watch the tests, and
SEDJEM_UI_REQUIRED=1 (as CI does) to fail instead of skipping when Playwright or a browser is missing.
With SEDJEM_UI_TRACES set to a folder (as CI does), a test that fails leaves there its Playwright trace,
<test>.zip (open it with `playwright show-trace` or at https://trace.playwright.dev), and a screenshot of its
page, <test>.png.

Run: .venv/bin/python -m unittest discover -s tests -p "test_ui*.py" -v
Needs: pip install -r requirements-dev.txt
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from unittest import mock

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import engines  # noqa: E402
from app.config import Config  # noqa: E402
from app.server import make_server  # noqa: E402
from media import make_clip  # noqa: E402

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

REQUIRED = os.environ.get("SEDJEM_UI_REQUIRED") == "1"
TRACES = os.environ.get("SEDJEM_UI_TRACES")

# What the app's GPU check and the computer look like in the demo (a laptop with an Intel GPU)
GPU = {"devices": [{"name": "Intel(R) Iris(R) Xe Graphics (ADL GT2)", "kind": "vulkan", "type": "igpu", "memory": 0}],
       "cuda_devices": 0, "cuda_libs": None}
MACHINE = {"manufacturer": "LENOVO", "model": "ThinkPad P16 Gen 1", "os": "Ubuntu 24.04.4 LTS (Linux 6.8.0)",
           "cpu": "12th Gen Intel(R) Core(TM) i7-12850HX", "threads": 24, "ram_gb": 31.7, "arch": "x86_64",
           "gpus": ["NVIDIA RTX A1000 Laptop GPU", "Intel(R) UHD Graphics"]}

# Made-up lines in the style of the recordings: Egyptian Arabic with English terms, some opening in English
LINES = [
    {"start": 0.0, "end": 4.2, "speaker": "1", "text": "order ال discount code عشان أعمل checkout بسرعة"},
    {"start": 4.2, "end": 8.0, "speaker": "2", "text": "the project ده محتاج تركيز عشان ال deadline قربت"},
    {"start": 8.0, "end": 11.5, "speaker": "1", "text": "We need to finish the whole task today يعني"},
    {"start": 11.5, "end": 15.0, "speaker": "2", "text": "ال API معتمد على ال back-end اللي الـteam بيعمله."},
    {"start": 15.0, "end": 18.0, "speaker": "1", "text": "Let's start the sprint planning now."},
    {"start": 18.0, "end": 21.0, "speaker": "2", "text": "12:30"},
]


def demo_job(home, jid, title="Sprint review", lines=LINES, seconds=21.0, video=None, **fields):
    """A finished local job as the app stores it: job.json, transcript.json and a 16 kHz FLAC (a quiet tone,
    a different one for each job: jobs with the same audio count as one recording, see app/compare.py).
    video=(width, height): the recording had a picture, so the folder has a small video.mp4 (made here, H.264
    and AAC at 10 frames a second, as long as the audio)."""
    folder = Path(home) / "app_data" / "jobs" / jid
    folder.mkdir(parents=True)
    t = np.arange(int(seconds * 16000)) / 16000
    freq = 180 + zlib.crc32(jid.encode()) % 200
    sf.write(folder / "audio.flac", (0.05 * np.sin(2 * np.pi * freq * t)).astype(np.float32), 16000)
    if video:
        make_clip(folder / "video.mp4", *video, seconds=seconds)
        fields = {"video": {"width": video[0], "height": video[1]}, **fields}
    job = {"id": jid, "title": title, "source_name": "planning.m4a", "created": "2026-09-25T22:00:00+03:00",
           "model": "cohere", "model_title": "Cohere Transcribe Arabic", "kind": "local", "engine": "cohere",
           "model_file": "cohere-transcribe-arabic-07-2026-Q4_K_M.gguf", "status": "done", "stage": None,
           "options": {"speakers": "auto", "language": "ar", "prompt": ""}, "speaker_names": {},
           "audio_s": seconds, "seconds": 3.1, "rtf": 0.148, "peak_rss_mb": 313, "power": "performance",
           "threads": 10, "gpu_setting": "auto", "device": "vulkan: Intel(R) Iris(R) Xe Graphics (ADL GT2)",
           "started": "2026-09-25T22:00:01+03:00", "finished": "2026-09-25T22:00:04+03:00", "app_version": "0.1.0",
           "source": {"name": "planning.m4a", "extension": "m4a", "format": "mov,mp4,m4a,3gp,3g2,mj2",
                      "codec": "aac", "codec_name": "AAC (Advanced Audio Coding)", "sample_rate": 44100,
                      "channels": 2, "bit_rate": 128000, "size": 812000, "duration": seconds,
                      "video": "h264" if video else None},
           "machine": MACHINE, **fields}
    (folder / "job.json").write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    (folder / "transcript.json").write_text(json.dumps({"lines": lines, "edited": False, "model": "cohere"},
                                                       ensure_ascii=False), encoding="utf-8")
    return jid


def launch(playwright, args=()):
    """The browser, with args for its command line."""
    headless = os.environ.get("SEDJEM_UI_HEADED") != "1"
    tried = []
    for options in ({}, {"channel": "chrome"}, {"channel": "msedge"}):
        try:
            return playwright.chromium.launch(headless=headless, args=list(args), **options)
        except PlaywrightError as e:
            tried.append(f"{options or 'bundled'}: {str(e).splitlines()[0]}")
    message = "no Chromium for Playwright (" + "; ".join(tried) + ")"
    if REQUIRED:
        raise RuntimeError(message)
    raise unittest.SkipTest(message)


class UiTestCase(unittest.TestCase):
    """One Sedjem server and one browser per test class; a fresh page per test. A test fails if the page
    logs a JavaScript error. Subclasses list demo jobs as `jobs = ({"jid": ..., ...}, ...)`, and may give
    `config`, TOML for the data folder's config.toml (a stand-in key for the usage counts, say)."""
    jobs = ()
    config = ""
    outcome = None

    def run(self, result=None):
        self.outcome = result  # where unittest records failures, for tearDown to see whether this test failed
        return super().run(result)

    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            if REQUIRED:
                raise RuntimeError("Playwright is not installed: pip install -r requirements-dev.txt")
            raise unittest.SkipTest("Playwright is not installed (pip install -r requirements-dev.txt)")
        cls.home = Path(tempfile.mkdtemp(prefix="sedjem-ui-"))
        (cls.home / "app_data").mkdir()
        # The first-start dialog over the page is answered: no automatic checks for updates, and (since a key ships in
        # app/config.toml) no usage counts, which the tests never send anyway
        (cls.home / "app_data" / "settings.json").write_text(json.dumps({"update_check": False, "usage_counts": False}),
                                                             encoding="utf-8")
        if cls.config:
            (cls.home / "app_data" / "config.toml").write_text(cls.config, encoding="utf-8")
        for job in cls.jobs:
            demo_job(cls.home, **job)
        cls.gpu = mock.patch.object(engines, "gpu_info", return_value=GPU)
        cls.gpu.start()
        cls.server, cls.app = make_server(Config(home=cls.home), port=0)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = launch(cls.playwright)
        except BaseException:
            cls.stop()
            raise

    @classmethod
    def stop(cls):
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.gpu.stop()
        shutil.rmtree(cls.home, ignore_errors=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.stop()

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 800}, accept_downloads=True)
        if TRACES:
            self.context.tracing.start(screenshots=True, snapshots=True)
        self.page = self.context.new_page()
        self.page.set_default_timeout(10_000)
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.page.on("console", lambda m: m.type == "error" and self.console_error(m.text))

    def console_error(self, text):
        """An error the page logged: it fails the test. Except one: Windows on a CI machine sometimes runs out of
        socket buffers (WSAENOBUFS, which Chrome shows as net::ERR_NO_BUFFER_SPACE). That is the machine's trouble,
        not the page's, so it is noted in the log instead; the app asks again, and the test's own checks still run."""
        if "net::ERR_NO_BUFFER_SPACE" in text:
            print(f"\n(the machine ran out of socket buffers: {text})", file=sys.stderr, flush=True)
        else:
            self.errors.append(text)

    def tearDown(self):
        if TRACES:
            self.keep_trace()
        self.context.close()
        self.assertEqual(self.errors, [], "the page logged errors")

    def failed(self):
        """Whether this test has failed so far: an assertion or an error in it, or an error its page logged."""
        if self.errors or self.outcome is None:
            return True
        return any(getattr(t, "test_case", t) is self for t, _ in self.outcome.failures + self.outcome.errors)

    def keep_trace(self):
        """The trace and a screenshot of a failed test, into the SEDJEM_UI_TRACES folder; nothing of one that passed."""
        if not self.failed():
            self.context.tracing.stop()
            return
        folder = Path(TRACES)
        folder.mkdir(parents=True, exist_ok=True)
        name = "".join(c if c.isalnum() or c in "._-" else "_" for c in self.id())
        try:
            self.page.screenshot(path=str(folder / f"{name}.png"), full_page=True)
        except PlaywrightError as e:  # a page that crashed or closed has no picture; the trace still has its last one
            print(f"\n(no screenshot of {self.id()}: {str(e).splitlines()[0]})", file=sys.stderr)
        self.context.tracing.stop(path=str(folder / f"{name}.zip"))

    def open(self, route="#/new"):
        """Load the app at a route and wait until it has its status. The first page a browser opens can take long
        on a CI machine (on Windows it once took more than the usual 10 s), so the load has 30 s."""
        self.page.goto(f"{self.base}/{route}", timeout=30_000)
        self.page.wait_for_function("() => typeof S !== 'undefined' && S.status", timeout=30_000)

    def api(self, method, path, body=None):
        """Call the app's API from the page (with the header every change needs)."""
        return self.page.evaluate(
            """async ([method, path, body]) => {
                const r = await fetch(path, { method, headers: { "X-Sedjem": "1", "Content-Type": "application/json" },
                                              body: body === null ? undefined : JSON.stringify(body) });
                return { status: r.status, json: await r.json().catch(() => null) };
            }""", [method, path, body])

    def open_settings(self, tab=None):
        """Open Settings, on one of its tabs: models, add, hosted, speed, assistants or about."""
        self.page.click("#settingsBtn")
        self.page.wait_for_selector("#settings[open] .set-panel")
        if tab:
            self.page.click(f"#settings [data-set-tab='{tab}']")
            self.page.wait_for_selector(f"#settings #tab-{tab}[aria-selected='true']")
