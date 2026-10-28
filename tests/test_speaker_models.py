"""Tests for the choice of speaker model (Settings → Models): the list in app/config.toml, the setting, which model
labels the speakers while the chosen one isn't downloaded, the downloads (Nemotron-3-Diarization is a folder of three
files), what the speaker step is given, and what the status tells the page. No speaker model runs and nothing comes
from the internet: the model files are stand-ins, and the one download is from a local server.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import hashlib
import json
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
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import engines  # noqa: E402
from app.config import Config  # noqa: E402
from app.downloads import Downloads  # noqa: E402
from app.engines import local_command  # noqa: E402
from app.server import make_server  # noqa: E402

IDS = ["titanet-small", "titanet-large", "campplus", "nemotron-3-diarization"]
NEMOTRON = "models/diarization/Nemotron-3-Diarization"
REVISION = "f667ed73aee57d40cc39428eb768b4fd87a0a29e"  # Nemotron-3-Diarization's files are pinned to it


def temp_home(test):
    """An empty data folder, removed after the test."""
    home = Path(tempfile.mkdtemp(prefix="sedjem-speakers-"))
    (home / "app_data").mkdir()
    test.addCleanup(shutil.rmtree, home, ignore_errors=True)
    return home


def put(cfg, model_id, names=None):
    """Stand-ins for a speaker model's files (all of them, or those named), so it counts as on disk."""
    for f in cfg.speaker_models()[model_id]["files"]:
        if names is None or Path(f["path"]).name in names:
            p = cfg.path(f["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"stand-in")


class TheList(unittest.TestCase):
    """The four speaker models of app/config.toml, and their downloads."""

    def test_the_four_models(self):
        cfg = Config(home=temp_home(self))
        models = cfg.speaker_models()
        self.assertEqual(list(models), IDS)
        self.assertEqual([m["title"] for m in models.values()],
                         ["TitaNet-small", "TitaNet-large", "3D-Speaker CAM++", "Nemotron-3-Diarization"])
        self.assertEqual([m["item"] for m in models.values()], ["voiceprints", *IDS[1:]],
                         "TitaNet-small keeps the download it always had")
        for m in models.values():
            self.assertTrue(m["note"], m["id"])
            for f in m["files"]:
                self.assertRegex(f["sha256"], r"^[0-9a-f]{64}$")
                self.assertGreater(f["size"], 0)
                self.assertTrue(f["url"].startswith("https://"))
        for mid in IDS[:3]:  # one ONNX file each, which is what the speaker step gets
            m = models[mid]
            self.assertEqual([f["path"] for f in m["files"]], [m["path"]])
            self.assertTrue(m["path"].endswith(".onnx"))
        nemotron = models["nemotron-3-diarization"]
        self.assertEqual(nemotron["path"], NEMOTRON, "the speaker step gets the folder")
        self.assertEqual([f["path"] for f in nemotron["files"]],
                         [f"{NEMOTRON}/{n}" for n in ("config.json", "processor_config.json", "model.safetensors")])
        self.assertTrue(all(f"/resolve/{REVISION}/" in f["url"] for f in nemotron["files"]))
        self.assertEqual([m.get("max_speakers") for m in models.values()], [None, None, None, 8])

    def test_each_one_is_a_download(self):
        cfg = Config(home=temp_home(self))
        items = cfg.download_items()
        for m in cfg.speaker_models().values():
            self.assertEqual(items[m["item"]]["files"], m["files"])
            self.assertEqual(items[m["item"]]["title"], f"{m['title']} (speaker labels)")
        status = Downloads(cfg).status("nemotron-3-diarization")
        self.assertEqual((status["size"], status["installed"]), (1458 + 623 + 396954592, False))
        self.assertEqual(Downloads(cfg).status("voiceprints")["size"], 40257283)

    def test_none_configured(self):
        home = temp_home(self)
        (home / "app_data" / "config.toml").write_text("[local]\nspeaker_models = []\n")
        cfg = Config(home=home)
        self.assertEqual(cfg.speaker_models(), {})
        self.assertFalse({"voiceprints", *IDS} & set(cfg.download_items()))
        self.assertIsNone(cfg.speaker_model_choice())
        self.assertIsNone(cfg.speaker_model())
        self.assertEqual(cfg.speaker_args("auto"), [])

    def test_an_older_personal_config_names_titanet_smalls_file(self):
        """[local] voiceprint_model, from before the choice, still says where TitaNet-small's file is."""
        home = temp_home(self)
        own = home / "elsewhere" / "voiceprints.onnx"
        own.parent.mkdir()
        own.write_bytes(b"stand-in")
        (home / "app_data" / "config.toml").write_text(f'[local]\nvoiceprint_model = "{own.as_posix()}"\n')
        cfg = Config(home=home)
        self.assertEqual(cfg.speaker_model()["id"], "titanet-small")
        self.assertEqual(cfg.speaker_args(2), ["--speakers", "2", "--voiceprint-model", str(own)])


class TheChoice(unittest.TestCase):
    def setUp(self):
        self.home = temp_home(self)
        self.cfg = Config(home=self.home)

    def test_the_setting(self):
        cfg = self.cfg
        self.assertEqual((cfg.settings()["speaker_model"], cfg.speaker_model_choice()), ("titanet-small", "titanet-small"))
        self.assertEqual(cfg.save_settings(speaker_model="campplus")["speaker_model"], "campplus")
        self.assertEqual(json.loads(cfg.settings_path.read_text())["speaker_model"], "campplus")
        self.assertEqual(Config(home=self.home).speaker_model_choice(), "campplus", "kept for the next start")
        for bad in ("nope", "", 3, None, ["campplus"], {"id": "campplus"}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                cfg.save_settings(speaker_model=bad)
        self.assertEqual(cfg.speaker_model_choice(), "campplus", "a refused value changes nothing")

    def test_the_default_comes_from_the_config(self):
        (self.home / "app_data" / "config.toml").write_text('[local]\nspeaker_model = "titanet-large"\n')
        cfg = Config(home=self.home)
        self.assertEqual(cfg.speaker_model_choice(), "titanet-large")
        # a choice saved before its model left the list counts as the config's
        cfg.settings_path.write_text(json.dumps({"speaker_model": "gone"}))
        self.assertEqual(cfg.speaker_model_choice(), "titanet-large")

    def test_the_model_used(self):
        cfg = self.cfg
        cfg.save_settings(speaker_model="nemotron-3-diarization")
        self.assertIsNone(cfg.speaker_model(), "nothing downloaded: no speaker labels")
        self.assertFalse(cfg.speakers_ready())
        put(cfg, "titanet-small")
        self.assertEqual(cfg.speaker_model()["id"], "titanet-small", "TitaNet-small until the chosen one is there")
        self.assertTrue(cfg.speakers_ready())
        put(cfg, "nemotron-3-diarization", {"config.json", "processor_config.json"})
        self.assertFalse(cfg.speaker_model_ready(cfg.speaker_models()["nemotron-3-diarization"]), "a file is missing")
        self.assertEqual(cfg.speaker_model()["id"], "titanet-small")
        put(cfg, "nemotron-3-diarization")
        self.assertEqual(cfg.speaker_model()["id"], "nemotron-3-diarization")
        (self.home / "models/diarization/nemo_en_titanet_small.onnx").unlink()
        self.assertEqual(cfg.speaker_model()["id"], "nemotron-3-diarization", "the chosen one needs no TitaNet-small")
        shutil.rmtree(self.home / NEMOTRON)
        self.assertIsNone(cfg.speaker_model())
        put(cfg, "campplus")
        self.assertIsNone(cfg.speaker_model(), "only TitaNet-small stands in for the chosen one")
        cfg.save_settings(speaker_model="campplus")
        self.assertEqual(cfg.speaker_model()["id"], "campplus")

    def test_what_the_speaker_step_gets(self):
        cfg = self.cfg
        put(cfg, "titanet-small")
        small = str(self.home / "models/diarization/nemo_en_titanet_small.onnx")
        self.assertEqual(cfg.speaker_args("none"), [])
        self.assertEqual(cfg.speaker_args("auto"), ["--speakers", "0", "--voiceprint-model", small])
        self.assertEqual(cfg.speaker_args(12), ["--speakers", "12", "--voiceprint-model", small])
        put(cfg, "nemotron-3-diarization")
        cfg.save_settings(speaker_model="nemotron-3-diarization")
        folder = str(self.home / NEMOTRON)
        self.assertEqual(cfg.speaker_args("auto"), ["--speakers", "0", "--voiceprint-model", folder])
        self.assertEqual(cfg.speaker_args(3), ["--speakers", "3", "--voiceprint-model", folder])
        self.assertEqual(cfg.speaker_args("12"), ["--speakers", "8", "--voiceprint-model", folder],
                         "Nemotron-3-Diarization tells at most 8 people apart")

    def test_the_local_models_command(self):
        cfg, model = self.cfg, self.cfg.models["whisper-medium"]
        options = {"speakers": 12, "language": "ar", "prompt": ""}

        def command():
            return local_command(cfg, model, self.home / "a.flac", self.home / "out", self.home / "p.json", options)
        self.assertNotIn("--speakers", command(), "no speaker model downloaded: no labels")
        put(cfg, "titanet-small")
        cmd = command()
        self.assertEqual(cmd[cmd.index("--speakers"):cmd.index("--speakers") + 4],
                         ["--speakers", "12", "--voiceprint-model", str(self.home / "models/diarization/nemo_en_titanet_small.onnx")])
        put(cfg, "nemotron-3-diarization")
        cfg.save_settings(speaker_model="nemotron-3-diarization")
        cmd = command()
        self.assertEqual(cmd[cmd.index("--speakers"):cmd.index("--speakers") + 4],
                         ["--speakers", "8", "--voiceprint-model", str(self.home / NEMOTRON)])
        options["speakers"] = "none"
        self.assertNotIn("--voiceprint-model", command())


class Files(BaseHTTPRequestHandler):
    """Serves the bytes of the stand-in files by path."""
    payloads = {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = self.payloads.get(self.path)
        self.send_response(200 if body is not None else 404)
        self.send_header("Content-Length", str(len(body or b"")))
        self.end_headers()
        self.wfile.write(body or b"")


class NemotronDownload(unittest.TestCase):
    """Nemotron-3-Diarization's three files are one download, into its folder, and one Delete."""

    def test_download_and_delete(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Files)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        cfg = Config(home=temp_home(self))
        models = []
        for m in cfg.local["speaker_models"]:  # the same paths, with small files from the local server
            files = []
            for i, f in enumerate(m["files"]):
                body = f"{m['id']} {i}".encode() * 50
                Files.payloads[f"/{m['id']}/{i}"] = body
                files.append({**f, "url": f"http://127.0.0.1:{server.server_address[1]}/{m['id']}/{i}",
                              "size": len(body), "sha256": hashlib.sha256(body).hexdigest()})
            models.append({**m, "files": files})
        cfg.local = {**cfg.local, "speaker_models": models}
        put(cfg, "campplus")  # another file in models/diarization/, which stays
        cfg.save_settings(speaker_model="nemotron-3-diarization")
        dl = Downloads(cfg)
        self.assertEqual(dl.start(["nemotron-3-diarization"]), ["nemotron-3-diarization"])
        end = time.time() + 30
        while dl.jobs["nemotron-3-diarization"]["state"] not in ("done", "error", "cancelled") and time.time() < end:
            time.sleep(0.05)
        self.assertEqual(dl.jobs["nemotron-3-diarization"]["state"], "done", dl.jobs["nemotron-3-diarization"])
        folder = cfg.path(NEMOTRON)
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["config.json", "model.safetensors", "processor_config.json"])
        self.assertTrue(dl.status("nemotron-3-diarization")["installed"])
        self.assertEqual(cfg.speaker_model()["id"], "nemotron-3-diarization")
        dl.remove("nemotron-3-diarization")
        self.assertFalse(folder.exists(), "its folder goes with its files")
        self.assertTrue(cfg.path(models[2]["path"]).exists(), "the folder above keeps the other models")
        self.assertIsNone(cfg.speaker_model(), "nor TitaNet-small: no speaker labels")


class Status(unittest.TestCase):
    """What the page reads (GET /api/status) and the calls that change it."""

    def setUp(self):
        self.home = temp_home(self)
        gpu = mock.patch.object(engines, "gpu_info", return_value={"devices": []})  # no model process at start
        gpu.start()
        self.addCleanup(gpu.stop)
        self.server, self.app = make_server(Config(home=self.home), port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method, headers={"X-Sedjem": "1", "Content-Type": "application/json"},
                                     data=None if body is None else json.dumps(body).encode())
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_the_status(self):
        put(self.app.cfg, "titanet-small")
        put(self.app.cfg, "nemotron-3-diarization")
        status, st = self.call("GET", "/api/status")
        self.assertEqual(status, 200)
        sp = st["speaker_models"]
        self.assertEqual((sp["chosen"], sp["used"], sp["fallback"]), ("titanet-small", "titanet-small", "titanet-small"))
        self.assertEqual([m["id"] for m in sp["models"]], IDS)
        first, last = sp["models"][0], sp["models"][-1]
        self.assertEqual(set(first), {"id", "title", "note", "maker", "max_speakers", "item", "ready", "download"})
        self.assertEqual((first["title"], first["item"], first["maker"], first["ready"]), ("TitaNet-small", "voiceprints", "NVIDIA", True))
        self.assertEqual((first["download"]["size"], first["download"]["installed"]), (40257283, False),
                         "a stand-in of the wrong size is not the downloaded file")
        self.assertEqual((last["item"], last["max_speakers"], last["ready"]), ("nemotron-3-diarization", 8, True))
        self.assertEqual(last["download"]["size"], 1458 + 623 + 396954592)
        self.assertEqual([m["ready"] for m in sp["models"]], [True, False, False, True])
        self.assertEqual(st["voiceprints"], first["download"], "TitaNet-small's download, as before")
        self.assertTrue(st["speakers_ready"])
        self.assertEqual(st["settings"]["speaker_model"], "titanet-small")

    def test_choosing_saves_it(self):
        put(self.app.cfg, "titanet-small")
        status, st = self.call("POST", "/api/settings", {"speaker_model": "campplus"})
        self.assertEqual(status, 200, st)
        self.assertEqual((st["speaker_models"]["chosen"], st["speaker_models"]["used"]), ("campplus", "titanet-small"))
        self.assertEqual(self.app.cfg.saved_settings()["speaker_model"], "campplus")
        status, st = self.call("POST", "/api/settings", {"speaker_model": "whisper-medium"})
        self.assertEqual(status, 400)
        self.assertIn("speaker_model", st["error"])
        self.assertEqual(self.app.cfg.speaker_model_choice(), "campplus")
        (self.home / "models/diarization/nemo_en_titanet_small.onnx").unlink()
        st = self.call("GET", "/api/status")[1]
        self.assertEqual((st["speaker_models"]["used"], st["speakers_ready"]), (None, False))

    def test_downloads(self):
        with mock.patch.object(self.app.downloads, "start", return_value=[]) as start:
            self.assertEqual(self.call("POST", "/api/downloads/nemotron-3-diarization")[0], 200)
            self.assertEqual(self.call("POST", "/api/downloads/whisper-medium")[0], 200)
        self.assertEqual([c.args[0] for c in start.call_args_list],
                         [["nemotron-3-diarization"], ["whisper-medium", "voiceprints"]],
                         "a local model still comes with TitaNet-small")

    def test_delete_waits_for_a_local_transcription(self):
        put(self.app.cfg, "campplus")
        running = [{"id": "x", "status": "running", "model": "whisper-medium", "kind": "local"}]
        with mock.patch.object(self.app.store, "list", return_value=running):
            status, body = self.call("DELETE", "/api/downloads/campplus")
        self.assertEqual(status, 409, body)
        self.assertTrue((self.home / "models/diarization/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx").exists())
        status, st = self.call("DELETE", "/api/downloads/campplus")
        self.assertEqual(status, 200, st)
        self.assertFalse((self.home / "models/diarization/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx").exists())


if __name__ == "__main__":
    unittest.main()
