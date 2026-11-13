"""Tests for model downloads, the model paths and the worker command (no network: a local server)."""
import hashlib
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.config import Config  # noqa: E402
from app.downloads import Downloads  # noqa: E402
from app.engines import local_command, worker_command  # noqa: E402

PAYLOAD = os.urandom(3_000_000)


class Files(BaseHTTPRequestHandler):
    """Serves PAYLOAD with Range support; /norange ignores ranges; /slow sends slowly."""
    ranges = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        start = 0
        rng = self.headers.get("Range")
        if rng and not self.path.startswith("/norange"):
            start = int(rng.split("=")[1].split("-")[0])
            self.ranges.append(start)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}")
        else:
            self.send_response(200)
        body = PAYLOAD[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        step = 65536 if self.path.startswith("/slow") else len(body)
        for i in range(0, len(body), step):
            try:
                self.wfile.write(body[i:i + step])
            except ConnectionError:  # the client cancelled (Windows raises ConnectionAbortedError)
                return
            if self.path.startswith("/slow"):
                time.sleep(0.05)


class DownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Files)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="sedjem-dl-"))
        (self.home / "app_data").mkdir()
        self.cfg = Config(home=self.home)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def item(self, path="/file", sha=None):
        f = {"url": self.url + path, "path": "models/test/model.bin", "size": len(PAYLOAD),
             "sha256": sha or hashlib.sha256(PAYLOAD).hexdigest()}
        self.cfg.models = {"t": {"id": "t", "kind": "local", "title": "Test", "files": [f]}}
        self.cfg.local = {**self.cfg.local, "speaker_models": []}
        return f

    def wait(self, dl, item_id="t", until=("done", "error", "cancelled")):
        end = time.time() + 30
        while time.time() < end:
            if (dl.jobs.get(item_id) or {}).get("state") in until:
                return dl.jobs[item_id]
            time.sleep(0.05)
        self.fail(f"download still {dl.jobs.get(item_id)}")

    def test_a_download_that_ends_while_its_status_is_read_is_never_done_without_its_files(self):
        # the worker puts the files in place and says "done" after; the status reads the state first, so it may
        # say "downloading" with the files there, but never "done" without them
        f = self.item()
        dl = Downloads(self.cfg)
        dl.jobs["t"] = {"state": "downloading", "done": 0, "total": f["size"], "error": None}
        real = dl.installed

        def ends_now(file):  # the download ends while its files are looked at
            seen = real(file)
            target = self.home / f["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(PAYLOAD)
            dl.jobs["t"]["state"] = "done"
            return seen

        with mock.patch.object(dl, "installed", side_effect=ends_now):
            st = dl.status("t")
        self.assertEqual((st["state"], st["installed"]), ("downloading", False))
        st = dl.status("t")
        self.assertEqual((st["state"], st["installed"]), ("done", True))

    def test_download_and_verify(self):
        self.item()
        dl = Downloads(self.cfg)
        self.assertFalse(dl.status("t")["installed"])
        dl.start(["t"])
        job = self.wait(dl)
        self.assertEqual(job["state"], "done", job)
        target = self.home / "models/test/model.bin"
        self.assertEqual(target.read_bytes(), PAYLOAD)
        self.assertTrue(dl.status("t")["installed"])
        self.assertFalse(target.with_name("model.bin.part").exists())
        dl.remove("t")
        self.assertFalse(target.exists())

    def test_resume_from_partial_file(self):
        f = self.item()
        part = self.home / "models/test/model.bin.part"
        part.parent.mkdir(parents=True)
        part.write_bytes(PAYLOAD[:1_000_000])
        Files.ranges.clear()
        dl = Downloads(self.cfg)
        self.assertEqual(dl.status("t")["partial"], 1_000_000)
        dl.start(["t"])
        self.assertEqual(self.wait(dl)["state"], "done")
        self.assertEqual(Files.ranges, [1_000_000], "continues where it stopped")
        self.assertEqual(self.cfg.path(f["path"]).read_bytes(), PAYLOAD)

    def test_server_without_ranges_restarts_the_file(self):
        self.item("/norange")
        part = self.home / "models/test/model.bin.part"
        part.parent.mkdir(parents=True)
        part.write_bytes(b"x" * 500_000)  # stale bytes that must not end up in the file
        dl = Downloads(self.cfg)
        dl.start(["t"])
        self.assertEqual(self.wait(dl)["state"], "done")
        self.assertEqual((self.home / "models/test/model.bin").read_bytes(), PAYLOAD)

    def test_checksum_mismatch(self):
        self.item(sha="0" * 64)
        dl = Downloads(self.cfg)
        dl.start(["t"])
        job = self.wait(dl)
        self.assertEqual(job["state"], "error")
        self.assertIn("checksum", job["error"])
        self.assertFalse((self.home / "models/test/model.bin").exists())
        self.assertFalse((self.home / "models/test/model.bin.part").exists())

    def test_cancel(self):
        self.item("/slow")
        dl = Downloads(self.cfg)
        dl.start(["t"])
        time.sleep(0.3)
        dl.cancel("t")
        self.assertEqual(self.wait(dl)["state"], "cancelled")
        self.assertFalse((self.home / "models/test/model.bin").exists())


def small_model(cfg, model_id):
    """A model of the config whose download files are 10 bytes each, so that a test can put them in place."""
    m = cfg.models[model_id]
    m["files"] = [{**f, "size": 10} for f in m["files"]]
    return m


def put_files(cfg, files):
    """Put download files in place at their full size, as a download leaves them."""
    for f in files:
        p = cfg.path(f["path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * f["size"])


class PathTests(unittest.TestCase):
    def test_models_resolve_under_the_data_folder(self):
        home = Path(tempfile.mkdtemp(prefix="sedjem-home-"))
        try:
            cfg = Config(home=home)
            self.assertEqual(cfg.storage, home / "app_data")
            m = small_model(cfg, "whisper-medium")
            self.assertIsNone(cfg.whisper_source(m))  # nothing downloaded in an empty home
            folder = home / m["whisper_model"]
            put_files(cfg, m["files"])
            self.assertEqual(cfg.whisper_source(m), str(folder))
            self.assertEqual(cfg.availability(m), (True, None))
            cmd = local_command(cfg, m, home / "a.flac", home / "out", home / "p.json",
                                {"speakers": "none", "language": "ar", "prompt": ""})
            self.assertEqual(cmd[:3], [sys.executable, "-m", "app.worker"])
            self.assertEqual(cmd[cmd.index("--whisper-model") + 1], str(folder))
            self.assertNotIn("--speakers", cmd)
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_a_whisper_model_needs_all_the_files_of_its_download(self):
        """A download puts each file in place once it is checked, model.bin first for the built-in Whisper models.
        One that stopped after model.bin leaves a folder that is not a whole model: not ready, and the download
        shows what is left (#110)."""
        home = Path(tempfile.mkdtemp(prefix="sedjem-home-"))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        cfg = Config(home=home)
        with mock.patch("huggingface_hub.try_to_load_from_cache", return_value=None):  # not this computer's cache
            for mid in ("whisper-medium", "large-v3"):
                with self.subTest(mid):
                    m = small_model(cfg, mid)
                    first, *rest = m["files"]
                    self.assertTrue(first["path"].endswith("/model.bin"), "the case of #110: model.bin comes first")
                    put_files(cfg, [first])
                    self.assertIsNone(cfg.whisper_source(m))
                    self.assertEqual(cfg.availability(m), (False, "not downloaded"))
                    status = Downloads(cfg).status(mid)
                    self.assertFalse(status["installed"])
                    self.assertEqual(status["missing"], sum(f["size"] for f in rest))
                    put_files(cfg, rest)
                    self.assertEqual(cfg.whisper_source(m), str(cfg.path(m["whisper_model"])))
                    self.assertTrue(Downloads(cfg).status(mid)["installed"])

    def test_large_v3_from_the_hugging_face_cache(self):
        """Large-v3 runs from the Hugging Face cache when Sedjem has no whole copy of its own: none at all, or a
        download that stopped after model.bin."""
        home = Path(tempfile.mkdtemp(prefix="sedjem-home-"))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        cfg = Config(home=home)
        m = small_model(cfg, "large-v3")
        with mock.patch("huggingface_hub.try_to_load_from_cache", return_value="/hf/model.bin") as cache:
            self.assertEqual(cfg.whisper_source(m), "large-v3")
            cache.assert_called_with("Systran/faster-whisper-large-v3", "model.bin")
            put_files(cfg, m["files"][:1])
            self.assertEqual(cfg.whisper_source(m), "large-v3")
            self.assertEqual(cfg.availability(m), (True, None))

    def test_worker_command_from_source(self):
        self.assertEqual(worker_command(Config()), [sys.executable, "-m", "app.worker"])


if __name__ == "__main__":
    unittest.main()
