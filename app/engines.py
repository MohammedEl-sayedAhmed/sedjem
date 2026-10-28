"""Run one transcription: local models through transcribe.py, hosted ones through their APIs.

Every runner takes on_progress(dict) and cancelled() callbacks and returns
{"lines": [...], "meta": {...}} with lines as {start, end, speaker ("1", "2", ... or None), text}.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import requests

from .config import FROZEN, ROOT
from .transcript import from_transcribe_py, lines_from_words, relabel


class Cancelled(Exception):
    pass


class EngineError(Exception):
    pass


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------------------------------------
# Local models: transcribe.py in a subprocess (its own process group, so cancel stops it cleanly)
# ---------------------------------------------------------------------------------------------

def gpu_info(cfg, timeout=90):
    """What transcribe.py finds on this computer ({"devices": [...], "cuda_devices", "cuda_libs"}), from a
    short run of the model process, so the heavy GPU libraries never load into the app itself."""
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(ROOT)}
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        r = subprocess.run(worker_command(cfg) + ["--gpu-info", "--cuda-libs", str(cfg.cuda_dir())], cwd=ROOT, env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=flags)
        line = next((x for x in reversed(r.stdout.splitlines()) if x.startswith("{")), None)
        return json.loads(line) if line else {"error": (r.stderr or r.stdout).strip()[-300:] or f"exit {r.returncode}"}
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        return {"error": str(e)}


def worker_command(cfg):
    """How to start transcribe.py: the desktop build runs itself with --transcribe."""
    if FROZEN:
        return [sys.executable, "--transcribe"]
    return [cfg.python(), "-m", "app.worker"]


def local_command(cfg, model, audio, out_dir, progress_file, options):
    cmd = worker_command(cfg) + [
        str(audio), "--engine", model["engine"], "--out", str(out_dir), "--threads", str(cfg.setting("threads")),
        "--language", options.get("language", "ar"), "--progress-file", str(progress_file),
        "--device", cfg.setting("device"), "--cuda-libs", str(cfg.cuda_dir())]
    whisper = cfg.whisper_source(model) if model["engine"] == "whisper" else None
    if model["engine"] == "whisper":
        cmd += ["--whisper-model", whisper or model["whisper_model"]]
    elif model["engine"] in ("cohere", "gguf"):  # gguf: any speech model transcribe.cpp runs
        cmd += ["--cohere-model", str(cfg.path(model["cohere_model"]))]
    cmd += cfg.speaker_args(options.get("speakers", "none"))  # the speaker model chosen in Settings, or TitaNet-small
    hint = (options.get("prompt") or "").strip()
    if hint and model.get("prompt"):
        from transcribe import STYLE_PROMPT, gets_style_hint
        name = Path(whisper or "").name if model["engine"] == "whisper" else Path(model.get("cohere_model") or "").name
        if gets_style_hint(name) and (model["engine"] == "whisper" or (model.get("hub") or {}).get("architecture") == "whisper"):
            hint = f"{STYLE_PROMPT} {hint}"  # keep large-v3's Egyptian style hint, add the terms
        cmd += ["--prompt", hint]
    return cmd


def partial_lines(job_dir):
    """What a running (or interrupted) local job has transcribed so far (read_json returns None
    while transcribe.py is rewriting the file; the next poll gets it)."""
    for p in sorted((Path(job_dir) / "engine").glob("*.json")):
        if not p.name.endswith((".words.json", ".meta.json")):
            data = read_json(p)
            if isinstance(data, list):
                return from_transcribe_py(data)
    return []


def spawn(cmd, log, env):
    """Start a model run in its own process group, without a console window on Windows."""
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        return subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=env, creationflags=flags)
    return subprocess.Popen(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)


def stop(proc):
    if proc.poll() is not None:
        return
    if os.name == "nt":  # transcribe.py starts no children, so ending the process is enough
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    except ProcessLookupError:
        pass


def run_local(cfg, model, job_dir, job, on_progress, cancelled, register=None):
    job_dir = Path(job_dir)
    out_dir, progress_file, log_path = job_dir / "engine", job_dir / "progress.json", job_dir / "log.txt"
    shutil.rmtree(out_dir, ignore_errors=True)
    progress_file.unlink(missing_ok=True)
    cmd = local_command(cfg, model, job_dir / "audio.flac", out_dir, progress_file, job.get("options") or {})
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
           "PYTHONPATH": str(ROOT), "SEDJEM_LOG": str(log_path)}
    log_path.write_text("", encoding="utf-8")
    with open(log_path, "a", encoding="utf-8") as log:  # append: the worker may open it too
        proc = spawn(cmd, log, env)
        if register:
            register(proc)
        last = None
        while True:
            try:
                proc.wait(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                pass
            if cancelled():
                stop(proc)
                raise Cancelled()
            state = read_json(progress_file)
            if state and state != last:
                on_progress(state)
                last = state
    if proc.returncode != 0:
        tail = [x for x in log_path.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()][-12:]
        if proc.returncode < 0:
            raise EngineError(f"the model process was stopped (signal {-proc.returncode}; out of memory?)\n"
                              + "\n".join(tail))
        raise EngineError("\n".join(tail) or f"transcribe.py exited with code {proc.returncode}")
    meta = next((read_json(p) for p in out_dir.glob("*.meta.json")), None) or {}
    return {"lines": partial_lines(job_dir),
            "meta": {k: meta[k] for k in ("peak_rss_mb", "voiceprint_model", "device") if k in meta}}


# ---------------------------------------------------------------------------------------------
# Hosted models. Uploads stream from disk with progress, and can be cancelled between blocks.
# ---------------------------------------------------------------------------------------------

class Multipart:
    """A multipart/form-data body read in blocks, so requests streams it with a Content-Length."""

    def __init__(self, fields, file_field, path, filename, content_type, on_read, cancelled):
        self.boundary = uuid.uuid4().hex
        head = b"".join(
            f'--{self.boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
            for k, v in fields)
        head += (f'--{self.boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
                 f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n').encode()
        self.parts = [head, None, f"\r\n--{self.boundary}--\r\n".encode()]
        self.file, self.size = open(path, "rb"), Path(path).stat().st_size
        self.total = len(head) + self.size + len(self.parts[2])
        self.sent, self.index, self.on_read, self.cancelled = 0, 0, on_read, cancelled

    @property
    def content_type(self):
        return f"multipart/form-data; boundary={self.boundary}"

    def __len__(self):
        return self.total

    def read(self, n=-1):
        if self.cancelled():
            raise Cancelled()
        n = self.total if n is None or n < 0 else n
        out = b""
        while len(out) < n and self.index < 3:
            part = self.parts[self.index]
            chunk = self.file.read(n - len(out)) if part is None else part[:n - len(out)]
            if part is not None:
                self.parts[self.index] = part[len(chunk):]
            if not chunk:
                self.index += 1
                continue
            out += chunk
        self.sent += len(out)
        self.on_read(self.sent, self.total)
        return out

    def close(self):
        self.file.close()


def check(r, service):
    """Raise EngineError with a readable message for an error response."""
    if r.status_code < 400:
        return
    detail = ""
    if "json" in r.headers.get("Content-Type", ""):
        try:
            body = r.json()
            detail = body.get("detail") or body.get("error") or "" if isinstance(body, dict) else body
            if isinstance(detail, dict):  # ElevenLabs: {"detail": {"code", "message", ...}}
                detail = detail.get("message") or detail.get("code") or ""
            elif isinstance(detail, list):  # ElevenLabs 422: [{"loc", "msg", "type"}, ...]
                detail = "; ".join(str(x.get("msg", x)) if isinstance(x, dict) else str(x) for x in detail)
        except ValueError:
            pass
    hints = {401: "the API key was rejected", 402: "not enough credit on the account",
             403: "the key or plan doesn't allow this", 404: "not found", 413: "the file is too large for the service",
             423: "the job is still running", 429: "too many requests or the account's limit is reached; try again later"}
    message = "; ".join(x for x in (hints.get(r.status_code), str(detail).strip()) if x)
    raise EngineError(f"{service}: HTTP {r.status_code}" + (f" — {message}" if message else ""))


def upload_progress(on_progress, service):
    last = [0.0]

    def on_read(sent, total):
        if time.time() - last[0] > 0.5 or sent == total:
            last[0] = time.time()
            on_progress({"stage": "uploading", "done": sent, "total": total, "service": service})
    return on_read


def split_terms(text):
    """A vocabulary hint as separate terms: comma-separated or one per line."""
    return [t.strip() for t in re.split(r"[,\n،]", text or "") if t.strip()]


def elevenlabs(cfg, model, job_dir, job, on_progress, cancelled):
    """ElevenLabs Speech-to-Text (Scribe): one request uploads the file and returns the words."""
    opts = job.get("options") or {}
    base, headers = model["base_url"].rstrip("/"), {"xi-api-key": cfg.api_key(model)}
    speakers = opts.get("speakers", "none")
    fields = [("model_id", model.get("api_model", "scribe_v2")), ("timestamps_granularity", "word"),
              ("tag_audio_events", "true" if model.get("tag_audio_events") else "false"),
              ("diarize", "false" if speakers == "none" else "true")]
    if speakers not in ("none", "auto"):
        fields.append(("num_speakers", str(min(32, int(speakers)))))  # a maximum, not an exact count
    if opts.get("language") in ("ar", "en"):  # omitted = detected
        fields.append(("language_code", opts["language"]))
    if model.get("prompt"):  # key terms: under 50 characters, at most 5 words, no <>{}[]\
        terms = [t for t in split_terms(opts.get("prompt")) if len(t) < 50 and len(t.split()) <= 5
                 and not re.search(r"[<>{}\[\]\\]", t)]
        fields += [("keyterms", t) for t in terms[:1000]]
    body = Multipart(fields, "file", Path(job_dir) / "audio.flac", "audio.flac", "audio/flac",
                     upload_progress(on_progress, "ElevenLabs"), cancelled)
    try:
        on_progress({"stage": "uploading", "done": 0, "total": len(body), "service": "ElevenLabs"})
        r = requests.post(f"{base}/v1/speech-to-text", data=body,
                          headers={**headers, "Content-Type": body.content_type}, timeout=(30, 3 * 3600))
    finally:
        body.close()
    check(r, "ElevenLabs")
    data = r.json()
    (Path(job_dir) / "hosted.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    if model.get("delete_after", True) and data.get("transcription_id"):
        try:  # keep it out of the ElevenLabs history; the local copy is all the app needs
            requests.delete(f"{base}/v1/speech-to-text/transcripts/{data['transcription_id']}",
                            headers=headers, timeout=30)
        except requests.RequestException:
            pass
    return {"lines": elevenlabs_lines(data), "meta": {"detected_language": data.get("language_code")}}


def elevenlabs_lines(data):
    words = [w for w in data.get("words") or [] if w.get("type", "word") in ("word", "audio_event")]
    names = relabel(w.get("speaker_id") for w in words)
    tokens, t = [], 0.0
    for w in words:
        text = (w.get("text") or "").strip()
        if not text:
            continue
        start = float(w["start"]) if w.get("start") is not None else t
        end = float(w["end"]) if w.get("end") is not None else start
        t = end
        tokens.append({"start": start, "end": end, "text": text, "speaker": names.get(w.get("speaker_id")),
                       "glue": all(not c.isalnum() for c in text)})  # a lone punctuation mark
    if not tokens and data.get("text"):
        return [{"start": 0.0, "end": 0.0, "speaker": None, "text": data["text"].strip()}]
    return lines_from_words(tokens)


def speechmatics(cfg, model, job_dir, job, on_progress, cancelled):
    """Speechmatics batch API: create a job (the upload), poll until done, fetch json-v2, delete it."""
    opts = job.get("options") or {}
    base, auth = model["base_url"].rstrip("/"), {"Authorization": f"Bearer {cfg.api_key(model)}"}
    language = {"en": "en", "auto": "auto"}.get(opts.get("language"), model.get("language", "ar_en"))
    tc = {"language": language, "model": model.get("api_model", "enhanced")}
    if opts.get("speakers", "none") != "none":  # batch jobs can't be told how many speakers there are
        tc["diarization"] = "speaker"
        tc["speaker_diarization_config"] = {"speaker_sensitivity": float(model.get("speaker_sensitivity", 0.5))}
    if model.get("prompt"):  # custom vocabulary: longer entries than 6 words are ignored by the service
        terms = [t for t in split_terms(opts.get("prompt")) if len(t.split()) <= 6]
        if terms:
            tc["additional_vocab"] = [{"content": t} for t in terms[:1000]]
    config = {"type": "transcription", "transcription_config": tc}
    body = Multipart([("config", json.dumps(config, ensure_ascii=False))], "data_file", Path(job_dir) / "audio.flac",
                     "audio.flac", "audio/flac", upload_progress(on_progress, "Speechmatics"), cancelled)
    try:
        on_progress({"stage": "uploading", "done": 0, "total": len(body), "service": "Speechmatics"})
        r = requests.post(f"{base}/jobs/", data=body, headers={**auth, "Content-Type": body.content_type},
                          timeout=(30, 3 * 3600))
    finally:
        body.close()
    check(r, "Speechmatics")
    sm_id = r.json()["id"]
    (Path(job_dir) / "hosted-job.txt").write_text(sm_id, encoding="utf-8")
    t0 = time.time()
    try:
        while True:
            r = requests.get(f"{base}/jobs/{sm_id}", params={"wait": 0}, headers=auth, timeout=60)
            if r.status_code == 429:
                status = "running"
            else:
                check(r, "Speechmatics")
                job_info = r.json().get("job", {})
                status = job_info.get("status")
                if status == "done":
                    break
                if status in ("rejected", "deleted", "expired"):
                    errors = "; ".join(e.get("message", "") for e in job_info.get("errors") or [])
                    raise EngineError(f"Speechmatics: the job was {status}" + (f" — {errors}" if errors else ""))
            on_progress({"stage": "remote", "service": "Speechmatics", "elapsed": round(time.time() - t0)})
            for _ in range(10):  # poll every 5 s, but notice a cancel within half a second
                if cancelled():
                    raise Cancelled()
                time.sleep(0.5)
        r = requests.get(f"{base}/jobs/{sm_id}/transcript", params={"format": "json-v2", "wait": 0},
                         headers=auth, timeout=120)
        check(r, "Speechmatics")
        data = r.json()
        (Path(job_dir) / "hosted.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except BaseException:
        try:  # stop and remove the job there too
            requests.delete(f"{base}/jobs/{sm_id}", params={"force": "true"}, headers=auth, timeout=30)
        except requests.RequestException:
            pass
        raise
    if model.get("delete_after", True):
        try:
            requests.delete(f"{base}/jobs/{sm_id}", headers=auth, timeout=30)
        except requests.RequestException:
            pass
    return {"lines": speechmatics_lines(data), "meta": {}}


def speechmatics_lines(data):
    items = [x for x in data.get("results") or [] if x.get("type") in ("word", "punctuation") and x.get("alternatives")]
    who = lambda x: x["alternatives"][0].get("speaker") or "UU"  # UU = unknown speaker
    names = relabel(who(x) for x in items if who(x) != "UU")
    tokens, prefix = [], ""
    for x in items:
        text = x["alternatives"][0].get("content", "")
        attach = x.get("attaches_to", "previous") if x["type"] == "punctuation" else "none"
        if attach == "next":  # e.g. an opening quote: joins the following word
            prefix += text
            continue
        tokens.append({"start": float(x.get("start_time", 0)), "end": float(x.get("end_time", 0)),
                       "text": prefix + text, "speaker": names.get(who(x)), "glue": attach in ("previous", "both")})
        prefix = ""
    return lines_from_words(tokens)


HOSTED = {"elevenlabs": elevenlabs, "speechmatics": speechmatics}


def run_hosted(cfg, model, job_dir, job, on_progress, cancelled):
    from . import hosted_openai  # adds OpenAI, Groq and Mistral to HOSTED
    if not cfg.api_key(model):
        raise EngineError(f"no API key for {model['title']}: add it in Settings")
    from . import hosted_more  # adds Gemini, Deepgram, AssemblyAI and Azure Speech to HOSTED
    return HOSTED[model["engine"]](cfg, model, job_dir, job, on_progress, cancelled)
