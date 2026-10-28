"""Tests for GPU use and the settings saved from the app: device choice and CPU fallback in transcribe.py
(with stand-ins for transcribe.cpp and faster-whisper), unpacking the NVIDIA libraries, the settings
file, switching the power profile back, estimates from measured speed, and the new status fields.
No GPU, model or network is needed.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import hashlib
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
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import transcribe  # noqa: E402
from app import engines, jobs  # noqa: E402
from app.config import Config  # noqa: E402
from app.downloads import Downloads  # noqa: E402
from app.server import App, device_class, make_server  # noqa: E402


# ---------------------------------------------------------------------------------------------
# A stand-in for transcribe_cpp
# ---------------------------------------------------------------------------------------------

class TranscribeError(RuntimeError):
    pass


class OutputTruncated(TranscribeError):
    pass


class InvalidArgument(TranscribeError):
    pass


class UnsupportedRequest(TranscribeError):
    pass


def device(name, kind="vulkan", dtype="gpu", memory=4 << 30):
    return types.SimpleNamespace(description=name, kind=kind, device_type=dtype, memory_total=memory)


def fake_transcribe_cpp(devices, fail_load=(), fail_run=(), arch="cohere_asr"):
    """Models load on any backend except those in fail_load; sessions on a backend in fail_run fail after
    their first (warm-up) run."""
    tc = types.SimpleNamespace()  # stands in for the module in sys.modules
    tc.errors = types.SimpleNamespace(TranscribeError=TranscribeError, OutputTruncated=OutputTruncated,
                                      InvalidArgument=InvalidArgument, UnsupportedRequest=UnsupportedRequest)
    tc.loaded, tc.prompts = [], []
    tc.WhisperRunOptions = lambda initial_prompt=None: types.SimpleNamespace(initial_prompt=initial_prompt)

    class Session:
        def __init__(self, backend):
            self.backend, self.calls = backend, 0

        def run(self, audio, language=None, family=None):
            self.calls += 1
            tc.prompts.append(family and family.initial_prompt)
            if self.backend in fail_run and self.calls > 1:
                raise TranscribeError("device lost")
            return types.SimpleNamespace(text=f"text from {self.backend}")

    class Model:
        def __init__(self, path, backend="auto", device=None):
            if backend in fail_load:
                raise TranscribeError(f"cannot load on {backend}")
            self.backend, self.arch = backend, arch
            tc.loaded.append((backend, getattr(device, "description", None)))

        def session(self, n_threads=4):
            return Session(self.backend)

    tc.Model = Model
    tc.backends = lambda: devices
    return tc


def cohere_args(device="auto", model="model.gguf", prompt=None):
    return types.SimpleNamespace(cohere_model=model, threads=2, language="ar", device=device, prompt=prompt)


class GpuChoiceTests(unittest.TestCase):
    def test_discrete_gpu_first_and_no_cpu(self):
        devs = [device("Intel(R) Iris(R) Xe Graphics", dtype="igpu", memory=12 << 30), device("CPU", "cpu", "cpu"),
                device("NVIDIA RTX A1000 Laptop GPU", memory=4 << 30)]
        with mock.patch.dict(sys.modules, {"transcribe_cpp": fake_transcribe_cpp(devs)}):
            names = [d.description for d in transcribe.gpu_devices()]
        self.assertEqual(names, ["NVIDIA RTX A1000 Laptop GPU", "Intel(R) Iris(R) Xe Graphics"])

    def test_no_gpu_runtime(self):
        tc = fake_transcribe_cpp([])
        tc.backends = mock.Mock(side_effect=OSError("no Vulkan"))
        with mock.patch.dict(sys.modules, {"transcribe_cpp": tc}):
            self.assertEqual(transcribe.gpu_devices(), [])

    def test_cohere_on_the_best_gpu(self):
        tc = fake_transcribe_cpp([device("Intel Iris Xe", dtype="igpu"), device("NVIDIA RTX A1000")])
        with mock.patch.dict(sys.modules, {"transcribe_cpp": tc}):
            engine = transcribe.Cohere(cohere_args())
            self.assertEqual(engine.device, "vulkan: NVIDIA RTX A1000")
            self.assertEqual(engine(np.zeros(16000, np.float32)), "text from vulkan")

    def test_cohere_tries_the_next_gpu_then_the_cpu(self):
        tc = fake_transcribe_cpp([device("NVIDIA RTX A1000")], fail_load=("vulkan",))
        with mock.patch.dict(sys.modules, {"transcribe_cpp": tc}):
            engine = transcribe.Cohere(cohere_args())
        self.assertEqual(engine.device, "cpu")
        self.assertEqual(tc.loaded, [("cpu", None)])

    def test_cohere_cpu_only_never_touches_the_gpu(self):
        tc = fake_transcribe_cpp([device("NVIDIA RTX A1000")])
        with mock.patch.dict(sys.modules, {"transcribe_cpp": tc}):
            engine = transcribe.Cohere(cohere_args("cpu"))
        self.assertEqual((engine.device, tc.loaded), ("cpu", [("cpu", None)]))

    def test_cohere_gpu_required_but_missing(self):
        with mock.patch.dict(sys.modules, {"transcribe_cpp": fake_transcribe_cpp([])}):
            with self.assertRaises(SystemExit):
                transcribe.Cohere(cohere_args("gpu"))

    def test_cohere_gpu_error_mid_run_continues_on_the_cpu(self):
        tc = fake_transcribe_cpp([device("NVIDIA RTX A1000")], fail_run=("vulkan",))
        with mock.patch.dict(sys.modules, {"transcribe_cpp": tc}):
            engine = transcribe.Cohere(cohere_args())
            self.assertEqual(engine.device, "vulkan: NVIDIA RTX A1000")
            self.assertEqual(engine(np.zeros(16000, np.float32)), "text from cpu")
        self.assertEqual(engine.device, "cpu (after a GPU error)")


class WhisperGgufPromptTests(unittest.TestCase):
    """A Whisper GGUF gets an initial prompt as faster-whisper does: large-v3 the Egyptian style hint."""

    def prompts(self, model, prompt=None, arch="whisper"):
        tc = fake_transcribe_cpp([], arch=arch)
        with mock.patch.dict(sys.modules, {"transcribe_cpp": tc}):
            engine = transcribe.Cohere(cohere_args("cpu", model, prompt))
            engine(np.zeros(16000, np.float32))
        return tc.prompts

    def test_large_v3_gets_the_style_hint(self):
        self.assertEqual(self.prompts("whisper-large-v3-Q8_0.gguf"), [transcribe.STYLE_PROMPT])

    def test_the_apps_prompt_is_used_as_given(self):
        hint = f"{transcribe.STYLE_PROMPT} Jira, GitHub"
        self.assertEqual(self.prompts("whisper-large-v3-Q8_0.gguf", hint), [hint])
        self.assertEqual(self.prompts("whisper-large-v3-Q8_0.gguf", ""), [None], "an empty prompt turns it off")

    def test_turbo_and_other_families_get_no_hint(self):
        self.assertEqual(self.prompts("whisper-large-v3-turbo-Q8_0.gguf"), [None])
        self.assertEqual(self.prompts("whisper-large-v3-turbo-Q8_0.gguf", "Jira"), ["Jira"])
        self.assertEqual(self.prompts("cohere-transcribe-arabic-07-2026-Q4_K_M.gguf", "Jira", arch="cohere_asr"), [None])

    def test_which_names_get_the_hint(self):
        for name, hint in (("large-v3", True), ("Systran/faster-whisper-large-v3", True), ("whisper-large-v3-Q5_K_M.gguf", True),
                           ("faster-whisper-large-v3-turbo", False), ("whisper-large-v3_turbo.gguf", False),
                           ("whisper-medium-arabic-codeswitched-ct2", False)):
            self.assertEqual(transcribe.gets_style_hint(name), hint, name)

    def test_the_app_puts_the_hint_before_the_terms(self):
        cfg = Config(home=Path(tempfile.mkdtemp(prefix="sedjem-hint-")))
        self.addCleanup(shutil.rmtree, cfg.home, True)
        m = {"id": "hf-x", "kind": "local", "engine": "gguf", "prompt": True, "cohere_model": "models/hf/x/whisper-large-v3-Q8_0.gguf",
             "hub": {"architecture": "whisper"}}
        cmd = engines.local_command(cfg, m, "in.flac", "out", "progress.json", {"prompt": "Jira, GitHub"})
        self.assertEqual(cmd[cmd.index("--prompt") + 1], f"{transcribe.STYLE_PROMPT} Jira, GitHub")
        turbo = {**m, "cohere_model": "models/hf/x/whisper-large-v3-turbo-Q8_0.gguf"}
        cmd = engines.local_command(cfg, turbo, "in.flac", "out", "progress.json", {"prompt": "Jira"})
        self.assertEqual(cmd[cmd.index("--prompt") + 1], "Jira")


class FakeWhisperModel:
    """Records how it was created; transcribe() returns one segment with two words, or fails on CUDA."""
    made = []
    fail_on_cuda = None  # None, "load" or "run"

    def __init__(self, source, device="cpu", compute_type="default", **kwargs):
        if device == "cuda" and FakeWhisperModel.fail_on_cuda == "load":
            raise RuntimeError("CUDA failed with error no kernel image")
        self.device, self.compute_type = device, compute_type
        FakeWhisperModel.made.append((device, compute_type))

    def transcribe(self, audio, **kwargs):
        if self.device == "cuda" and FakeWhisperModel.fail_on_cuda == "run" and kwargs.get("word_timestamps"):
            raise RuntimeError("CUDA out of memory")
        words = [types.SimpleNamespace(start=0.0, end=0.4, word=" one"), types.SimpleNamespace(start=0.4, end=0.9, word=" two")]
        return iter([types.SimpleNamespace(seek=0, words=words)]), None


def whisper_args(device="auto"):
    return types.SimpleNamespace(whisper_model="models/whisper-medium", threads=2, language="ar", prompt=None,
                                 device=device, cuda_libs=None)


class WhisperDeviceTests(unittest.TestCase):
    def setUp(self):
        FakeWhisperModel.made, FakeWhisperModel.fail_on_cuda = [], None
        self.patches = [mock.patch.object(transcribe, "WhisperModel", FakeWhisperModel),
                        mock.patch.object(transcribe, "nvidia_gpu_name", return_value="NVIDIA RTX A1000")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_cpu_without_an_nvidia_gpu(self):
        with mock.patch.object(transcribe, "cuda_device_count", return_value=0):
            engine = transcribe.Whisper(whisper_args())
        self.assertEqual((engine.device, FakeWhisperModel.made), ("cpu", [("cpu", "int8")]))

    def test_cpu_without_the_nvidia_libraries(self):
        with mock.patch.object(transcribe, "cuda_device_count", return_value=1), \
                mock.patch.object(transcribe, "load_cuda_libs", return_value=None):
            self.assertEqual(transcribe.Whisper(whisper_args()).device, "cpu")

    def test_cuda_when_ready(self):
        with mock.patch.object(transcribe, "cuda_device_count", return_value=1), \
                mock.patch.object(transcribe, "load_cuda_libs", return_value="/libs"):
            engine = transcribe.Whisper(whisper_args())
        self.assertEqual(engine.device, "cuda: NVIDIA RTX A1000 (int8_float16)")
        self.assertEqual(engine.words(np.zeros(16000, np.float32)), [(0.0, 0.4, "one"), (0.4, 0.9, "two")])

    def test_cuda_load_failure_falls_back(self):
        FakeWhisperModel.fail_on_cuda = "load"
        with mock.patch.object(transcribe, "cuda_device_count", return_value=1), \
                mock.patch.object(transcribe, "load_cuda_libs", return_value="/libs"):
            self.assertEqual(transcribe.Whisper(whisper_args()).device, "cpu")

    def test_cuda_error_mid_run_continues_on_the_cpu(self):
        FakeWhisperModel.fail_on_cuda = "run"
        with mock.patch.object(transcribe, "cuda_device_count", return_value=1), \
                mock.patch.object(transcribe, "load_cuda_libs", return_value="/libs"):
            engine = transcribe.Whisper(whisper_args())
            self.assertEqual(len(engine.words(np.zeros(16000, np.float32))), 2)
        self.assertEqual(engine.device, "cpu (after a GPU error)")

    def test_gpu_required_but_missing(self):
        with mock.patch.object(transcribe, "cuda_device_count", return_value=0):
            with self.assertRaises(SystemExit):
                transcribe.Whisper(whisper_args("gpu"))

    def test_no_cuda_libraries_in_an_empty_folder(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(transcribe, "CUDA_LIBS", Path(d) / "none"), \
                mock.patch.dict(sys.modules, {"nvidia": None, "nvidia.cublas": None}), \
                mock.patch("ctypes.CDLL", side_effect=OSError("not found")):
            transcribe._cuda_libs.clear()
            self.assertIsNone(transcribe.load_cuda_libs(d))
        transcribe._cuda_libs.clear()


# ---------------------------------------------------------------------------------------------
# Unpacking the NVIDIA libraries from their package
# ---------------------------------------------------------------------------------------------

def package(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
        z.writestr("nvidia/cublas/include/cublas.h", b"/* not unpacked */")
    return buf.getvalue()


LIB, LT = os.urandom(50_000), os.urandom(120_000)
WHEEL = package({"nvidia/cublas/lib/libcublas.so.12": LIB, "nvidia/cublas/lib/libcublasLt.so.12": LT})


class Serve(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(WHEEL)))
        self.end_headers()
        self.wfile.write(WHEEL)


class UnpackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Serve)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/pkg.whl"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="sedjem-gpu-"))
        (self.home / "app_data").mkdir()
        self.cfg = Config(home=self.home)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def cuda_item(self, lt_size=len(LT)):
        f = {"platform": "win32" if os.name == "nt" else sys.platform, "url": self.url,
             "path": "models/nvidia-cuda/pkg.whl", "size": len(WHEEL), "sha256": hashlib.sha256(WHEEL).hexdigest(),
             "unpack": {"nvidia/cublas/lib/libcublas.so.12": len(LIB), "nvidia/cublas/lib/libcublasLt.so.12": lt_size}}
        self.cfg.local = {**self.cfg.local, "cuda_files": [f]}
        return f

    def wait(self, dl):
        end = time.time() + 30
        while time.time() < end:
            if (dl.jobs.get("cuda") or {}).get("state") in ("done", "error", "cancelled"):
                return dl.jobs["cuda"]
            time.sleep(0.05)
        self.fail(f"still {dl.jobs.get('cuda')}")

    def test_only_the_libraries_are_kept(self):
        self.cuda_item()
        self.assertIn("cuda", self.cfg.download_items())
        self.assertEqual(self.cfg.cuda_dir(), self.home / "models/nvidia-cuda")
        dl = Downloads(self.cfg)
        self.assertEqual(dl.status("cuda")["on_disk"], len(LIB) + len(LT))
        dl.start(["cuda"])
        self.assertEqual(self.wait(dl)["state"], "done")
        folder = self.home / "models/nvidia-cuda"
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["libcublas.so.12", "libcublasLt.so.12"])
        self.assertEqual((folder / "libcublasLt.so.12").read_bytes(), LT)
        self.assertTrue(dl.status("cuda")["installed"])
        dl.remove("cuda")
        self.assertEqual(list(folder.iterdir()), [])

    def test_a_member_of_the_wrong_size_fails_cleanly(self):
        self.cuda_item(lt_size=len(LT) + 1)
        dl = Downloads(self.cfg)
        dl.start(["cuda"])
        job = self.wait(dl)
        self.assertEqual(job["state"], "error")
        self.assertIn("expected size", job["error"])
        self.assertFalse(any(p.name.endswith(".part") for p in (self.home / "models/nvidia-cuda").iterdir()))
        self.assertFalse(dl.status("cuda")["installed"])

    def test_an_unpack_interrupted_earlier_resumes_without_downloading(self):
        f = self.cuda_item()
        dest = self.cfg.path(f["path"])
        dest.parent.mkdir(parents=True)
        dest.write_bytes(WHEEL)  # verified earlier, then the app was closed
        dl = Downloads(self.cfg)
        self.assertEqual(dl.status("cuda")["missing"], 0)
        with mock.patch.object(dl, "fetch", side_effect=AssertionError("downloaded again")):
            dl.start(["cuda"])
            self.assertEqual(self.wait(dl)["state"], "done")
        self.assertFalse(dest.exists())


# ---------------------------------------------------------------------------------------------
# Settings saved from the app, power profile, estimates, status
# ---------------------------------------------------------------------------------------------

class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="sedjem-set-"))
        (self.home / "app_data").mkdir()
        self.cfg = Config(home=self.home)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def test_defaults_come_from_the_config(self):
        self.assertEqual(self.cfg.settings(), {"performance_while_running": False, "device": "auto", "threads": 10,
                                               "mcp_courses": True, "mcp_recordings": True, "mcp_hidden": [],
                                               "mcp_write": False,  # assistants only read until the user says
                                               "update_check": None, "update_channel": "stable", "update_skip": None})

    def test_saved_settings_win_and_survive_a_restart(self):
        self.cfg.save_settings(device="cpu", threads=2, performance_while_running=True)
        again = Config(home=self.home)
        self.assertEqual(again.settings(), {"performance_while_running": True, "device": "cpu", "threads": 2,
                                            "mcp_courses": True, "mcp_recordings": True, "mcp_hidden": [], "mcp_write": False,
                                            "update_check": None, "update_channel": "stable", "update_skip": None})

    def test_bad_values_are_refused(self):
        for bad in ({"device": "tpu"}, {"threads": 0}, {"threads": 10_000}, {"threads": True},
                    {"performance_while_running": "yes"}, {"port": 1}):
            with self.assertRaises(ValueError, msg=bad):
                self.cfg.save_settings(**bad)
        self.assertFalse(self.cfg.settings_path.exists())

    def test_the_model_process_gets_device_threads_and_the_libraries_folder(self):
        self.cfg.save_settings(device="cpu", threads=3)
        model = self.cfg.models["cohere"]
        cmd = engines.local_command(self.cfg, model, "a.flac", "out", "p.json", {"speakers": "none"})
        self.assertEqual(cmd[cmd.index("--device") + 1], "cpu")
        self.assertEqual(cmd[cmd.index("--threads") + 1], "3")
        self.assertEqual(cmd[cmd.index("--cuda-libs") + 1], str(self.cfg.cuda_dir()))

    def test_power_profile_is_restored_even_if_the_setting_was_turned_off(self):
        runner = types.SimpleNamespace(cfg=self.cfg, saved_profile=None)
        self.cfg.save_settings(performance_while_running=True)
        with mock.patch.object(jobs, "power_profile", return_value="power-saver"), \
                mock.patch.object(jobs, "set_power_profile", return_value=True) as setp:
            jobs.Runner.performance(runner, True)
            self.assertEqual(runner.saved_profile, "power-saver")
            self.cfg.save_settings(performance_while_running=False)  # turned off while a job runs
            jobs.Runner.performance(runner, False)
        self.assertEqual([c.args for c in setp.call_args_list], [("performance",), ("power-saver",)])
        self.assertIsNone(runner.saved_profile)

    def test_estimates(self):
        gpu = {"devices": [{"name": "Intel(R) Iris(R) Xe Graphics", "kind": "vulkan", "type": "igpu", "memory": 0}],
               "cuda_devices": 0, "cuda_libs": None}
        with mock.patch.object(engines, "gpu_info", return_value=gpu):
            app = App(self.cfg)
            for _ in range(100):
                if app.gpu:
                    break
                time.sleep(0.02)
        cohere, whisper = self.cfg.models["cohere"], self.cfg.models["whisper-medium"]
        with mock.patch("app.server.power_profile", return_value="performance"):
            self.assertEqual(app.runs_on(cohere), "gpu")
            self.assertEqual(app.runs_on(whisper), "cpu")  # no NVIDIA GPU
            self.assertEqual(app.speed(cohere, []), {"rtf": cohere["rtf_gpu"], "measured": False, "runs_on": "gpu"})
            past = [{"model": "cohere", "status": "done", "kind": "local", "rtf": r, "audio_s": 600, "power": "performance",
                     "device": "vulkan: Intel(R) Iris(R) Xe Graphics"} for r in (0.2, 0.1, 0.12)]
            past.append({**past[0], "rtf": 0.9, "device": "cpu"})  # a CPU run doesn't count for the GPU
            past.append({**past[0], "rtf": 0.9, "power": "power-saver"})  # nor one in another power mode
            past.append({**past[0], "rtf": 0.9, "audio_s": 20})  # nor a short clip
            self.assertEqual(app.speed(cohere, past), {"rtf": 0.12, "measured": True, "runs_on": "gpu"})
            self.cfg.save_settings(device="cpu")
            self.assertEqual(app.runs_on(cohere), "cpu")
        self.assertEqual(device_class("cuda: NVIDIA RTX A1000 (int8_float16)"), "gpu")
        self.assertEqual(device_class("cpu (after a GPU error)"), "cpu")
        self.assertEqual(device_class(None), "cpu")


class SettingsApiTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="sedjem-api-"))
        (self.home / "app_data").mkdir()
        with mock.patch.object(engines, "gpu_info", return_value={"devices": [], "cuda_devices": 0, "cuda_libs": None}):
            self.server, self.app = make_server(Config(home=self.home), port=0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.home, ignore_errors=True)

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method, data=json.dumps(body).encode() if body else None,
                                     headers={"X-Sedjem": "1", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_status_and_settings(self):
        code, st = self.call("GET", "/api/status")
        self.assertEqual(code, 200)
        for key in ("version", "settings", "gpu", "cpu_threads", "cuda"):
            self.assertIn(key, st)
        self.assertEqual(st["storage"]["jobs"], 0)
        code, st = self.call("POST", "/api/settings", {"device": "cpu", "threads": 1})
        self.assertEqual((code, st["settings"]["device"], st["settings"]["threads"]), (200, "cpu", 1))
        code, err = self.call("POST", "/api/settings", {"threads": -3})
        self.assertEqual(code, 400)
        self.assertIn("threads", err["error"])


if __name__ == "__main__":
    unittest.main()
