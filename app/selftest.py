"""Check an installation (source or desktop build): `Sedjem --self-test [--window] [--report out.json]`.

1. worker    the model process starts and imports everything a local run needs
2. server    the interface and the API answer
3. job       a generated tone is uploaded, converted to 16 kHz FLAC and "transcribed" by a stand-in
             for the ElevenLabs API on 127.0.0.1 (nothing leaves the computer)
4. video     a generated MPEG-4 clip is converted to the job page's H.264 video (app/video.py)
5. window    (--window) a native window opens, loads the app and closes
6. models    (--models, downloads about 820 MB) whisper-medium and the voiceprint model are
             downloaded with the app's own downloader, and a public 11 s speech sample (JFK, from
             the openai/whisper repository) is transcribed with speaker labels through the app

Exit code 0 when every check passed. A windowed build has no console, so --report is the way to
see the results there.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import soundfile as sf

from .config import ROOT, Config
from .downloads import Downloads
from .engines import worker_command
from .server import make_server

SAMPLE = "https://github.com/openai/whisper/raw/main/tests/jfk.flac"

REPLY = {"language_code": "ara", "text": "تمام test", "words": [
    {"text": "تمام", "start": 0.1, "end": 0.5, "type": "word", "speaker_id": "speaker_0"},
    {"text": " ", "start": 0.5, "end": 0.6, "type": "spacing", "speaker_id": "speaker_0"},
    {"text": "test", "start": 0.6, "end": 1.0, "type": "word", "speaker_id": "speaker_1"}]}


class FakeElevenLabs(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        body = json.dumps(REPLY).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_DELETE(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


def check_worker(tmp):
    log = Path(tmp) / "worker.log"
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(ROOT),
           "SEDJEM_LOG": str(log)}
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    r = subprocess.run(worker_command(Config()) + ["--check-imports"], cwd=ROOT, env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=600, creationflags=flags)
    output = (r.stdout or "") + (r.stderr or "") + (log.read_text(encoding="utf-8") if log.exists() else "")
    return {"ok": r.returncode == 0, "output": output.strip()[-3000:]}


def get(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers={"X-Sedjem": "1", **(headers or {})}, method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
        return json.loads(body) if "json" in r.headers.get("Content-Type", "") else body.decode("utf-8", "replace")


def check_server_and_job(tmp, results, window):
    mock = ThreadingHTTPServer(("127.0.0.1", 0), FakeElevenLabs)
    threading.Thread(target=mock.serve_forever, daemon=True).start()
    storage = Path(tmp) / "app_data"
    storage.mkdir()
    (storage / "config.toml").write_text(
        f'[[models]]\nid = "elevenlabs"\nbase_url = "http://127.0.0.1:{mock.server_address[1]}"\n', encoding="utf-8")
    cfg = Config(storage=storage, home=tmp)
    cfg.save_key("elevenlabs", "self-test")
    server, app = make_server(cfg, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page, status = get(base + "/"), get(base + "/api/status")
        results["server"] = {"ok": "Sedjem" in page and status.get("app") == "sedjem",
                             "models": {m["id"]: m["ready"] for m in status["models"]}}
        t = np.arange(int(44100 * 2)) / 44100
        wav = Path(tmp) / "tone.wav"
        sf.write(wav, np.stack([0.2 * np.sin(2 * np.pi * 440 * t)] * 2, axis=1).astype(np.float32), 44100)
        job = get(base + "/api/jobs?model=elevenlabs&speakers=auto&name=tone.wav&confirm_upload=1",
                  data=wav.read_bytes(), headers={"Content-Type": "audio/wav"})
        r, end = None, time.time() + 120
        while time.time() < end:
            r = get(f"{base}/api/jobs/{job['id']}")
            if r["job"]["status"] in ("done", "failed", "cancelled"):
                break
            time.sleep(0.3)
        info = sf.info(str(storage / "jobs" / job["id"] / "audio.flac"))
        results["job"] = {"ok": r["job"]["status"] == "done" and len(r["lines"]) == 2 and info.samplerate == 16000
                          and info.channels == 1, "status": r["job"]["status"], "error": r["job"].get("error"),
                          "lines": len(r["lines"]), "audio": f"{info.samplerate} Hz, {info.channels} ch, {info.duration:.1f} s"}
        if window:
            results["window"] = check_window(base + "/")
    finally:
        app.runner.shutdown()
        server.shutdown()
        server.server_close()
        mock.shutdown()
        mock.server_close()


def check_video(tmp):
    """The H.264 encoder that the job page's video is made with, tried on a clip made with FFmpeg's own
    MPEG-4 encoder (in every build)."""
    import av

    from . import video
    clip, out = Path(tmp) / "clip.avi", Path(tmp) / "video.mp4"
    with av.open(str(clip), "w") as c:
        s = c.add_stream("mpeg4", rate=10)
        s.width, s.height, s.pix_fmt = 64, 48, "yuv420p"
        for i in range(10):
            frame = av.VideoFrame.from_ndarray(np.full((48, 64, 3), i * 20, np.uint8), format="rgb24")
            frame.pts = i
            c.mux(s.encode(frame))
        c.mux(s.encode(None))
    shown = video.make(clip, out, lambda t: None, lambda: False)
    with av.open(str(out)) as c:
        codec = c.streams.video[0].codec_context.name
    return {"ok": shown == {"width": 64, "height": 48} and codec == "h264", "encoder": video.encoder(), "codec": codec}


def check_window(url):
    import webview
    outcome = {"ok": False}

    def probe(win):
        try:
            for _ in range(120):
                if win.evaluate_js("document.title") == "Sedjem" and win.evaluate_js(
                        "document.querySelector('#view') && document.querySelector('#view').children.length > 0"):
                    outcome.update(ok=True, backend=getattr(webview, "renderer", None) or "")
                    break
                time.sleep(0.5)
        except Exception as e:  # noqa: BLE001
            outcome["error"] = f"{type(e).__name__}: {e}"
        finally:
            win.destroy()

    watchdog = threading.Timer(180, lambda: os._exit(3))  # never hang a build pipeline
    watchdog.daemon = True
    watchdog.start()
    try:
        webview.start(probe, webview.create_window("Sedjem self-test", url, width=900, height=600))
    except Exception as e:  # noqa: BLE001
        outcome["error"] = f"{type(e).__name__}: {e}"
    watchdog.cancel()
    return outcome


def check_models(tmp):
    """Download real models into an empty data folder and transcribe real speech through the app."""
    import requests
    home = Path(tmp) / "models-home"
    (home / "app_data").mkdir(parents=True)
    cfg = Config(home=home)
    downloads = Downloads(cfg)
    t0 = time.time()
    downloads.start(["whisper-medium", "voiceprints"])
    while any(downloads.busy(i) or (downloads.jobs.get(i) or {}).get("state") == "queued"
              for i in ("whisper-medium", "voiceprints")):
        if time.time() - t0 > 1800:
            return {"ok": False, "error": "downloads took over 30 minutes"}
        time.sleep(1)
    items = ("whisper-medium", "voiceprints")
    if not all(downloads.status(i)["installed"] for i in items):
        return {"ok": False, "downloads": {i: downloads.jobs.get(i) for i in items}}
    download_s = round(time.time() - t0)
    sample = home / "jfk.flac"
    sample.write_bytes(requests.get(SAMPLE, timeout=60).content)
    server, app = make_server(cfg, port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        job = get(base + "/api/jobs", data=json.dumps({"path": str(sample), "model": "whisper-medium",
                                                         "speakers": "auto", "language": "en"}).encode(),
                  headers={"Content-Type": "application/json"})
        t1, r = time.time(), None
        while time.time() - t1 < 900:
            r = get(f"{base}/api/jobs/{job['id']}")
            if r["job"]["status"] in ("done", "failed", "cancelled"):
                break
            time.sleep(1)
        text = " ".join(x["text"] for x in r["lines"]).lower()
        return {"ok": r["job"]["status"] == "done" and "country" in text, "status": r["job"]["status"],
                "error": r["job"].get("error"), "text": text[:300], "download_s": download_s,
                "transcribe_s": round(time.time() - t1), "log": (r.get("log") or "")[-1500:]}
    finally:
        app.runner.shutdown()
        server.shutdown()
        server.server_close()


def run(window=False, report=None, models=False):
    results = {"python": sys.version.split()[0], "platform": sys.platform, "frozen": bool(getattr(sys, "frozen", False))}
    with tempfile.TemporaryDirectory(prefix="sedjem-selftest-") as tmp:
        checks = [("worker", lambda: results.__setitem__("worker", check_worker(tmp))),
                  ("server", lambda: check_server_and_job(tmp, results, window)),
                  ("video", lambda: results.__setitem__("video", check_video(tmp)))]
        if models:
            checks.append(("models", lambda: results.__setitem__("models", check_models(tmp))))
        for name, fn in checks:
            try:
                fn()
            except Exception:  # noqa: BLE001 — every failure goes into the report
                results[name] = {"ok": False, "error": traceback.format_exc()[-3000:]}
    ok = all(v.get("ok") for k, v in results.items() if isinstance(v, dict))
    results["ok"] = ok
    text = json.dumps(results, ensure_ascii=False, indent=1)
    if report:
        Path(report).write_text(text, encoding="utf-8")
    if sys.stdout:
        print(text)
    return 0 if ok else 1
