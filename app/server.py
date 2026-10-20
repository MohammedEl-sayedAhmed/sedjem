"""The local web server: the UI in app/static and a small JSON API (Python standard library only).

Safety: it listens on 127.0.0.1, answers only requests addressed to that host (no DNS rebinding),
and every request that changes something must carry the X-Sedjem header, which other websites
open in the same browser cannot send (no cross-site requests).
"""
import json
import os
import posixpath
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.parse
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__, assistants, compare, courses, engines, hub, report, video, youtube
from . import transcript as T
from . import history
from .config import FROZEN, power_profile, set_power_profile
from .downloads import Downloads
from .jobs import ACTIVE, Runner, Store, probe_seconds

STATIC = Path(__file__).resolve().parent / "static"
ID = r"(\d{8}-\d{6}-[0-9a-f]{4})"
TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png", ".webmanifest": "application/manifest+json"}
AUDIO_EXT = re.compile(r"^\.[A-Za-z0-9]{1,6}$")


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class App:
    """Shared state for all request threads."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.store = Store(cfg.storage)
        self.runner = Runner(self.store, cfg, gpus=self.gpu_names)
        self.runner.recover()
        self.downloads = Downloads(cfg)
        self.server = None
        self.desktop = False    # set by the desktop app
        self.on_quit = None     # the desktop app closes its window instead of just stopping the server
        self.gpu = None         # what transcribe.py can use here; found in the background at start
        self.gpu_probing = False
        self.used = (0.0, None)  # (time, bytes) of the last count of the transcriptions' size
        self.inspected = {}      # YouTube link -> (time, what youtube.inspect found), for adding it right after
        self.settings_lock = threading.Lock()  # settings.json is read, changed and written one request at a time
        self.probe_gpu()

    def probe_gpu(self):
        if self.gpu_probing:
            return
        self.gpu_probing = True

        def run():
            try:
                self.gpu = engines.gpu_info(self.cfg)
            finally:
                self.gpu_probing = False
        threading.Thread(target=run, daemon=True, name="sedjem-gpu").start()

    def gpu_names(self):
        """The graphics cards the check found, for the job details; None until it has run (or if it failed)."""
        devices = (getattr(self, "gpu", None) or {}).get("devices")  # jobs may start before __init__ ends
        return [d.get("name") for d in devices if isinstance(d, dict)] if isinstance(devices, list) else None

    def cuda_ready(self):
        items = self.cfg.download_items()
        return bool((self.gpu or {}).get("cuda_libs")) or ("cuda" in items and self.downloads.status("cuda")["installed"])

    def runs_on(self, model):
        """Where a local model is expected to run: "gpu" or "cpu"."""
        gpu = self.gpu or {}
        if model["kind"] != "local" or self.cfg.setting("device") == "cpu":
            return "cpu"
        if model["engine"] in ("cohere", "gguf") and gpu.get("devices"):
            return "gpu"
        if model["engine"] == "whisper" and gpu.get("cuda_devices") and self.cuda_ready():
            return "gpu"
        return "cpu"

    def speed(self, model, jobs):
        """The processing time / audio length to expect: measured on this computer (the median of the last
        runs on the same kind of device and power profile), else the config's figure."""
        where, power = self.runs_on(model), power_profile()
        past = [j["rtf"] for j in jobs if j.get("model") == model["id"] and j.get("status") == "done" and j.get("rtf")
                and j.get("kind") == "local" and (j.get("audio_s") or 0) >= 60 and j.get("power") == power
                and device_class(j.get("device")) == where][:5]
        if past:
            return {"rtf": round(statistics.median(past), 3), "measured": True, "runs_on": where}
        rtf = model.get("rtf_gpu") if where == "gpu" and model.get("rtf_gpu") else model.get("rtf")
        return {"rtf": rtf, "measured": False, "runs_on": where}

    def model_info(self, model, jobs=None):
        ready, reason = self.cfg.availability(model)
        keys = ("id", "kind", "engine", "title", "tagline", "facts", "service", "key_url", "rtf", "rtf_gpu", "prompt", "hub",
                "privacy", "prompt_hint", "speakers_hint", "maker")
        items = self.cfg.download_items()
        if model["kind"] == "local":  # for the details in Settings
            try:
                file = (model.get("hub") or {}).get("file") or report.model_file(self.cfg, model) \
                    or posixpath.basename(model.get("cohere_model") or "") or None
            except Exception:  # noqa: BLE001 — a detail is never a reason for the status to fail
                file = None
            extra = {**hub.about(model), "file": file}
        else:
            extra = {"api_model": model.get("api_model") or ", ".join(model.get("speech_models") or []) or None,
                     "language": model.get("locale") or model.get("language")}
        return {**{k: model.get(k) for k in keys}, "about": extra, "ready": ready, "reason": reason,
                "speed": self.speed(model, self.store.list() if jobs is None else jobs) if model["kind"] == "local" else None,
                "key_source": self.cfg.key_source(model) if model["kind"] == "hosted" else None,
                "download": self.downloads.status(model["id"]) if model["id"] in items else None,
                "region": self.cfg.region(model) if "region" in model else None}

    def used_bytes(self):
        """Disk space of all transcriptions (audio copies, transcripts, logs), counted at most every 30 s."""
        at, size = self.used
        if size is None or time.time() - at > 30:
            size = sum(f.stat().st_size for f in self.store.root.rglob("*") if f.is_file())
            self.used = (time.time(), size)
        return size

    def status(self):
        jobs = self.store.list()
        items = self.cfg.download_items()
        gpu = self.gpu or {}
        if gpu.get("cuda_devices") and not gpu.get("cuda_libs") and "cuda" in items \
                and self.downloads.status("cuda")["installed"]:
            self.probe_gpu()  # the NVIDIA libraries were just downloaded: check them again
        return {
            "app": "sedjem", "version": __version__,
            "desktop": self.desktop, "frozen": FROZEN, "home": str(self.cfg.home),
            "models": [self.model_info(m, jobs) for m in self.cfg.models.values()],
            "catalog": hub.catalog(self.cfg),  # recommended models (app/catalog.toml)
            "voiceprints": self.downloads.status("voiceprints") if "voiceprints" in items else None,
            "cuda": self.downloads.status("cuda") if "cuda" in items else None,
            "deno": self.downloads.status("deno") if "deno" in items else None,  # for YouTube links
            "youtube": {"runtime": youtube.runtime(self.cfg), "courses": str(self.cfg.courses)},
            "gpu": self.gpu, "gpu_probing": self.gpu_probing,
            "defaults": self.cfg.defaults,
            "settings": self.cfg.settings(),
            "cpu_threads": os.cpu_count(),
            "speakers_ready": self.cfg.speakers_ready(),
            "power": power_profile(),
            "performance_while_running": bool(self.cfg.setting("performance_while_running")),
            "storage": {"dir": str(self.cfg.storage), "free_gb": round(shutil.disk_usage(self.cfg.storage).free / 1e9, 1),
                        "used_mb": round(self.used_bytes() / 1e6), "jobs": len(jobs)},
            "active": sum(1 for j in jobs if j["status"] in ACTIVE),
        }

    def assistants_view(self):
        """Settings → Assistants: the two switches, what is hidden one by one (newest first, with titles), the
        newest entries of the log of what assistants asked, and the configuration for each assistant."""
        s = self.cfg.settings()
        jobs = {j["id"]: j for j in self.store.list()}  # newest first
        marks = s["mcp_hidden"] if isinstance(s["mcp_hidden"], list) else []
        return {"settings": {k: bool(s[k]) for k in ("mcp_courses", "mcp_recordings")},
                "hidden": [hidden_item(k, jobs) for k in reversed(marks) if isinstance(k, str)],
                "log": [log_item(e, jobs) for e in assistants.read_log(self.cfg, 50)],
                "snippets": assistants.snippets(self.cfg)}


def device_class(device):
    """"gpu" or "cpu" from a job's device, e.g. "vulkan: Intel(R) Iris(R) Xe Graphics" (older jobs have none)."""
    return "gpu" if (device or "").split(":")[0] in ("vulkan", "cuda", "metal", "rocm") else "cpu"


def summary(job):
    keys = ("id", "title", "created", "model", "model_title", "kind", "status", "stage", "done", "total",
            "audio_s", "seconds", "rtf", "elapsed", "source_name", "source_url", "youtube_id", "course")
    return {k: job.get(k) for k in keys}


# ---- assistants (app/assistants.py) -----------------------------------------------------------------

def job_assistants(cfg, job):
    """Whether assistants may see a transcription, for its page. reason: None (they may), "switch" (the switch
    for its kind is off), "course" (its course is hidden) or "hidden" (it is hidden on its own); hidden_key:
    the mark that hides it, if one does. The switch is named first: it hides it whatever the marks say."""
    s = cfg.settings()
    hidden = set(s.get("mcp_hidden") or ())
    key, *rest = assistants.keys(job)
    course = rest[0] if rest else None
    switch = "mcp_courses" if assistants.kind(job) == "course" else "mcp_recordings"
    hidden_key = course if course in hidden else key if key in hidden else None
    reason = None
    if not s[switch]:
        reason = "switch"
    elif hidden_key:
        reason = "course" if hidden_key == course else "hidden"
    return {"visible": assistants.visible(cfg, job, s), "reason": reason, "hidden_key": hidden_key,
            "key": key, "hidden": key in hidden, "course_key": course, "course_hidden": course in hidden,
            "switch": switch, "switch_on": bool(s[switch])}


MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def day(iso):
    """2026-09-25T22:00:00+03:00 -> 25 Sep 2026"""
    try:
        d = datetime.fromisoformat(str(iso))
    except ValueError:
        return ""
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def hidden_item(key, jobs):
    """A transcription or course hidden from assistants, as Settings lists it: {key, kind, title, detail}. One
    that is no longer in the app keeps its key (title None), so its mark can still be taken off."""
    kind, _, ident = str(key).partition(":")
    if kind == "job":
        job = jobs.get(ident)
        if job is None:
            return {"key": key, "kind": "transcription", "title": None, "detail": "This transcription was deleted"}
        course = job.get("course") or {}
        detail = (f"{course.get('title') or 'A course'}, lecture {course.get('index')}" if course.get("id")
                  else "YouTube video" if job.get("youtube_id") else f"Recording, {day(job.get('created'))}".rstrip(", "))
        return {"key": key, "kind": "transcription", "title": job.get("title") or "Untitled", "detail": detail}
    lectures = [j for j in jobs.values() if (j.get("course") or {}).get("id") == ident]
    if not lectures:
        return {"key": key, "kind": "course", "title": None, "detail": "Its lectures were deleted"}
    course = lectures[0]["course"]  # the newest lecture's
    n, count = len({j["course"].get("index") for j in lectures}), course.get("count")
    lectures_text = f"{n} of {count} lectures" if isinstance(count, int) and count > n else f"{n} lecture{'' if n == 1 else 's'}"
    return {"key": key, "kind": "course", "title": course.get("title") or "Course", "detail": lectures_text}


# What an assistant asked, in words: the actions are app/mcp.py's tools, resources/read and prompts/get, and
# the detail is what it was about, as the server words it ("tokenizers, in Prompt Engineering"): (with the
# detail, without one). An action this list doesn't know (a newer server's) is shown by its own name.
ASKED = {"list_transcriptions": ("Listed the transcriptions ({})", "Listed the transcriptions"),
         "list_courses": ("Listed the courses ({})", "Listed the courses"),
         "course_outline": ("Read the outline of {}", "Read the outline of a course"),
         "get_transcript": ("Read {}", "Read a transcript"),
         "search": ("Searched for {}", "Searched the transcripts"),
         "get_course_text": ("Read the course text of {}", "Read the text of a course"),
         "resources/read": ("Read {}", "Read a transcript or a course"),
         "prompts/get": ("Used the prompt {}", "Used a prompt"),
         "tools/call": ("Asked for {}", "Asked for a tool that doesn't exist")}


def asked(entry):
    """An entry of the log in words: Searched for tokenizers, in Prompt Engineering. A detail that only says
    again what the words say ("courses" after Listed the courses) is left out."""
    action = str(entry.get("action") or "")
    detail = " ".join(str(entry.get("detail") or "").split())
    with_detail, plain = ASKED.get(action) or ("{}: {}", action.replace("_", " ").replace("/", " ").strip().capitalize() or "Asked")
    if not detail or detail.lower() in plain.lower().split():
        return plain
    return with_detail.format(detail) if action in ASKED else with_detail.format(plain, detail)


def log_item(entry, jobs):
    """An entry of the log for Settings, with the titles of the transcriptions it names (None for one that
    was deleted since) and what was asked in words."""
    ids = [str(i) for i in entry.get("ids") or []] if isinstance(entry.get("ids"), list) else []
    return {"time": str(entry.get("time") or ""), "client": str(entry.get("client") or ""),
            "action": str(entry.get("action") or ""), "detail": str(entry.get("detail") or ""), "ids": ids,
            "titles": [(jobs[i].get("title") or "Untitled") if i in jobs else None for i in ids], "text": asked(entry)}


def slug(text):
    s = re.sub(r"[^\w\s-]", "", text or "", flags=re.UNICODE).strip()
    return re.sub(r"\s+", "-", s)[:80] or "transcript"


def parse_options(p, cfg, model):
    speakers = str(p.get("speakers", cfg.defaults.get("speakers", "auto"))).lower()
    if speakers not in ("none", "auto"):
        if not speakers.isdigit() or not 1 <= int(speakers) <= 20:
            raise ApiError(400, "speakers must be none, auto or 1-20")
        speakers = int(speakers)
    language = str(p.get("language", cfg.defaults.get("language", "ar")))
    if language not in ("ar", "en", "auto"):
        raise ApiError(400, "language must be ar, en or auto")
    prompt = str(p.get("prompt") or "")[:2000] if model.get("prompt") else ""
    return {"speakers": speakers, "language": language, "prompt": prompt}


class Handler(BaseHTTPRequestHandler):
    server_version = "Sedjem"
    protocol_version = "HTTP/1.1"
    app: App  # set by make_server

    def log_message(self, fmt, *args):  # quiet; errors are returned to the UI instead
        pass

    # ---- plumbing ----------------------------------------------------------------------------
    def reply(self, status, body=b"", ctype="application/json; charset=utf-8", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def json(self, obj, status=200):
        self.reply(status, json.dumps(obj, ensure_ascii=False).encode())

    def body_json(self, limit=5_000_000):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            raise ApiError(413, "request too large")
        self._body_read = True
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            raise ApiError(400, "invalid JSON")

    def guard(self, write):
        cfg = self.app.cfg
        port = self.server.server_address[1]
        if cfg.server["host"] in ("127.0.0.1", "localhost", "::1"):
            allowed = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
            if self.headers.get("Host") not in allowed:
                raise ApiError(403, "requests must be addressed to 127.0.0.1")
            origin = self.headers.get("Origin")
            if origin and origin.removeprefix("http://") not in allowed:
                raise ApiError(403, "cross-site request refused")
        if write and self.headers.get("X-Sedjem") != "1":
            raise ApiError(403, "missing X-Sedjem header")

    def dispatch(self, method):
        self._body_read = False  # one handler serves every request on a keep-alive connection
        path, _, query = self.path.partition("?")
        params = {k: v[-1] for k, v in urllib.parse.parse_qs(query).items()}
        try:
            self.guard(write=method not in ("GET", "HEAD"))
            for verb, pattern, fn in ROUTES:
                m = re.fullmatch(pattern, path)
                if m and verb == ("GET" if method == "HEAD" else method):
                    return fn(self, params, *m.groups())
            raise ApiError(404, "not found")
        except (ApiError, compare.Invalid) as e:
            self.drain()
            self.json({"error": str(e)}, e.status)
        except ConnectionError:  # the browser went away (e.g. seeking the player); Windows: ConnectionAbortedError
            self.close_connection = True
        except Exception as e:  # report instead of dropping the connection
            self.drain()
            self.json({"error": f"{type(e).__name__}: {e}"}, 500)
        finally:
            self.drain()  # a body the handler didn't need would be read as the next request

    def drain(self):
        """Read an unread request body so the connection stays usable."""
        n = int(self.headers.get("Content-Length") or 0)
        if n and not self._body_read:
            if n > 50_000_000:  # not worth reading a big upload we refused: close the connection instead
                self.close_connection = True
            else:
                try:
                    self.rfile.read(n)
                except OSError:
                    self.close_connection = True
        self._body_read = True

    def do_GET(self):
        self.dispatch("GET")

    def do_HEAD(self):
        self.dispatch("HEAD")

    def do_POST(self):
        self.dispatch("POST")

    def do_PATCH(self):
        self.dispatch("PATCH")

    def do_DELETE(self):
        self.dispatch("DELETE")

    # ---- static -----------------------------------------------------------------------------------
    def static(self, _params, name="index.html"):
        f = (STATIC / name).resolve()
        if STATIC not in f.parents or not f.is_file():
            raise ApiError(404, "not found")
        self.reply(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))

    def service_worker(self, params):
        self.static(params, "sw.js")

    # ---- API ----------------------------------------------------------------------------------------
    def status(self, _params):
        self.json(self.app.status())

    def list_jobs(self, _params):
        jobs = self.app.store.list()
        compare.fill_later(self.app.store, jobs)  # fingerprints of older jobs, for the groups
        groups = compare.group_ids(jobs)
        self.json({"jobs": [{**summary(j), "group": groups[j["id"]]} for j in jobs]})

    def ready_model(self, p, default=None):
        """The model a request names (else the default), if it can run now; a hosted one only with the
        user's consent to upload the recording."""
        cfg = self.app.cfg
        model = cfg.models.get(p.get("model") or default or cfg.defaults["model"])
        if model is None:
            raise ApiError(400, "unknown model")
        ready, reason = cfg.availability(model)
        if not ready:
            raise ApiError(400, f"{model['title']} is not ready: {reason}")
        if model["kind"] == "hosted" and str(p.get("confirm_upload")).lower() not in ("1", "true"):
            raise ApiError(400, f"confirm that the recording may be uploaded to {model['service']}")
        return model

    def create_job(self, params):
        cfg, runner = self.app.cfg, self.app.runner
        ctype = self.headers.get("Content-Type", "")
        is_json = ctype.startswith("application/json")
        p = self.body_json() if is_json else params
        model = self.ready_model(p)
        options = parse_options(p, cfg, model)
        if is_json:  # a file already on this computer: read it in place, no copy
            source = Path(str(p.get("path") or "")).expanduser()
            if not source.is_file():
                raise ApiError(400, f"file not found: {source}")
            job = runner.new_job(title=p.get("title") or source.stem, source_name=source.name,
                                 model_id=model["id"], options=options, source_path=str(source.resolve()))
        else:
            name = Path(p.get("name") or "recording").name
            ext = Path(name).suffix if AUDIO_EXT.match(Path(name).suffix or "") else ".bin"
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0:
                raise ApiError(400, "empty upload")
            if n > cfg.max_upload:
                raise ApiError(413, f"file is larger than the {cfg.max_upload // 1024 ** 3} GB limit")
            cfg.storage.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(cfg.storage).free < n * 1.2 + 1e9:
                raise ApiError(507, "not enough free disk space for this file")
            job = runner.new_job(title=p.get("title") or Path(name).stem, source_name=name,
                                 model_id=model["id"], options=options)
            dest = self.app.store.dir(job["id"]) / f"source{ext.lower()}"
            try:
                with open(dest, "wb") as f:
                    left = n
                    while left > 0:
                        chunk = self.rfile.read(min(1 << 20, left))
                        if not chunk:
                            raise ApiError(400, "upload interrupted")
                        f.write(chunk)
                        left -= len(chunk)
                self._body_read = True
            except BaseException:
                self.app.store.delete(job["id"])
                raise
        runner.submit(job["id"])
        self.json(summary(self.app.store.get(job["id"])), 201)

    # YouTube links (app/youtube.py): a video, or a playlist made into one job per video
    def youtube_inspect(self, _params):
        """{url}: the video or playlist, its videos in order and which have captions made by people."""
        url = str(self.body_json().get("url") or "").strip()
        try:
            found = youtube.inspect(self.app.cfg, url)
        except youtube.YouTubeError as e:
            raise ApiError(400, str(e))
        self.app.inspected = {k: v for k, v in self.app.inspected.items() if time.time() - v[0] < 1800}
        self.app.inspected[url] = self.app.inspected[found["url"]] = (time.time(), found)
        self.json(found)

    def youtube_add(self, _params):
        """{url, model, speakers, language, prompt, confirm_upload, captions (use captions made by people,
        default true), entries (the video ids to transcribe; default all)}: one job per video, queued in
        playlist order, with the playlist as their course. Answers {"jobs": [...]}."""
        cfg, runner, store = self.app.cfg, self.app.runner, self.app.store
        p = self.body_json()
        model = self.ready_model(p)
        options = parse_options(p, cfg, model)
        url = str(p.get("url") or "").strip()
        at, found = self.app.inspected.get(url, (0, None))
        if found is None or time.time() - at > 1800:
            try:
                found = youtube.inspect(cfg, url, check_captions=False)
            except youtube.YouTubeError as e:
                raise ApiError(400, str(e))
        wanted = p.get("entries")
        if wanted is not None and not isinstance(wanted, list):
            raise ApiError(400, "entries must be a list of video ids")
        picked = None if wanted is None else {str(x) for x in wanted}
        entries = [e for e in found["entries"] if e["available"] and (picked is None or e["id"] in picked)]
        if not entries:
            raise ApiError(400, "choose at least one video that can be downloaded")
        if any(not youtube.SAFE_ID.fullmatch(str(e["id"])) for e in entries):  # it names files (app/courses.py)
            raise ApiError(400, "a video has an id YouTube doesn't use")
        cfg.storage.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(cfg.storage).free < 2e9:
            raise ApiError(507, "not enough free disk space to download videos")
        course = None
        if found["kind"] == "playlist":
            course = {"id": found["id"], "title": found["title"], "url": found["url"], "count": found["count"]}
            course["folder"] = courses.folder_name(store, course)
        jobs = []
        for e in entries:
            extra = {"course": {**course, "index": e["index"]}} if course else {}
            jobs.append(runner.new_job(title=e["title"], source_name=f"{e['title']}.mp4", model_id=model["id"],
                                       options=options, source_url=youtube.watch_url(e["id"]), youtube_id=e["id"],
                                       use_captions=str(p.get("captions", True)).lower() not in ("0", "false"), **extra))
        for job in jobs:  # in playlist order: downloads run one at a time, in the order they were queued
            runner.submit(job["id"])
        self.json({"jobs": [summary(store.get(j["id"])) for j in jobs]}, 201)

    def job(self, jid):
        job = self.app.store.get(jid)
        if job is None:
            raise ApiError(404, "no such transcription")
        return job

    def get_job(self, _params, jid):
        job = self.job(jid)
        folder = self.app.store.dir(jid)
        data = self.app.store.transcript(jid)
        out = {"job": job, "lines": data["lines"] if data else engines.partial_lines(folder),
               "edited": bool(data and data.get("edited")), "partial": data is None,
               "has_audio": (folder / "audio.flac").exists(),
               "has_video": bool(job.get("video")) and (folder / video.NAME).exists()}
        out["details"] = report.details(job, out["lines"], out["edited"])
        out["detail_groups"] = report.groups(out["details"])  # the same, as the lines the job page shows
        out["version"] = history.current(self.app.store, jid, self.app.cfg)  # None while it runs
        out["assistants"] = job_assistants(self.app.cfg, job)  # whether assistants may read it, and why not
        if job["status"] in ("failed", "interrupted", "cancelled"):
            log = folder / "log.txt"
            out["log"] = log.read_text(encoding="utf-8", errors="replace")[-4000:] if log.exists() else ""
        self.json(out)

    def lines_of(self, jid):
        data = self.app.store.transcript(jid)
        return data["lines"] if data else engines.partial_lines(self.app.store.dir(jid))

    def change_from(self, job, p):
        """What a request asks to change (title, speaker_names, lines, merge: {from, into}), as
        (change, kind, merge) for history.save."""
        change, kind, merge = {}, "rename", None
        if "title" in p:
            change["title"] = str(p["title"]).strip()[:200] or job["title"]
        if "speaker_names" in p:
            change["speaker_names"] = {str(k): str(v).strip()[:60] for k, v in (p["speaker_names"] or {}).items()
                                       if str(v).strip() and re.fullmatch(r"\d{1,3}", str(k))}
        if "lines" in p or "merge" in p:
            if job["status"] in ACTIVE:
                raise ApiError(409, "wait until the transcription has finished")
            lines = self.lines_of(job["id"])
            if "lines" in p:
                lines, kind = clean_lines(p["lines"]), "edit"
            if "merge" in p:
                src, dst = str(p["merge"].get("from")), str(p["merge"].get("into"))
                if not (re.fullmatch(r"\d{1,3}", src) and re.fullmatch(r"\d{1,3}", dst)):
                    raise ApiError(400, "speaker ids are numbers")
                names = change.get("speaker_names", job.get("speaker_names") or {})
                moved, who = sum(1 for x in lines if x["speaker"] == src), {"speaker_names": names}
                if "lines" not in p:
                    kind, merge = "merge", {"from": src, "note": f"{T.speaker_name(who, src)} merged into "
                                            f"{T.speaker_name(who, dst)} ({moved} line{'' if moved == 1 else 's'})"}
                lines = [{**x, "speaker": dst if x["speaker"] == src else x["speaker"]} for x in lines]
                change["speaker_names"] = {k: v for k, v in names.items() if k != src}
            change["lines"] = lines
        return change, kind, merge

    def patch_job(self, _params, jid):
        """A change to the title, speaker names or lines, saved as a new version (app/history.py)."""
        store = self.app.store
        p = self.body_json()
        with store.lock:  # one change at a time, so versions are numbered in the order they are saved
            change, kind, merge = self.change_from(self.job(jid), p)
            history.save(store, jid, change, kind, p.get("message"), cfg=self.app.cfg, merge=merge)
        self.changed(jid)
        self.get_job({}, jid)

    def changed(self, jid, job=None):
        """After a transcript, its title or its speaker names changed (or it was deleted): a YouTube job's
        files in the courses folder follow (app/courses.py)."""
        courses.try_refresh(self.app.store, self.app.cfg, job or self.app.store.get(jid))

    def diff_job(self, _params, jid):
        """What a PATCH with the same body would change, without saving it: edits are reviewed first."""
        change, _, _ = self.change_from(self.job(jid), self.body_json())
        found = history.preview(self.app.store, jid, change, self.app.cfg)
        if found is None:
            raise ApiError(409, "wait until the transcription has finished")
        self.json(found)

    def delete_job(self, _params, jid):
        job = self.job(jid)
        if job["status"] in ACTIVE:
            self.app.runner.cancel(jid)
            for _ in range(60):  # let a running model stop before its folder goes
                if (self.app.store.get(jid) or {}).get("status") not in ACTIVE:
                    break
                threading.Event().wait(0.25)
        self.app.runner.stop_video(jid)  # its files are closed before the folder goes
        self.app.store.delete(jid)
        self.changed(jid, job)  # a deleted lecture leaves its course's files
        self.json({"deleted": jid})

    def cancel_job(self, _params, jid):
        self.job(jid)
        self.app.runner.cancel(jid)
        self.json(summary(self.app.store.get(jid)))

    def rerun_job(self, _params, jid):
        job = self.job(jid)
        p = self.body_json()
        if not (self.app.store.dir(jid) / "audio.flac").exists():
            if job.get("source_url") and job["status"] not in ACTIVE:  # a YouTube download that failed: again
                return self.retry_download(job, p)
            raise ApiError(409, "this transcription has no prepared audio yet")
        model = self.ready_model(p, job["model"])
        options = parse_options({**(job.get("options") or {}), **p}, self.app.cfg, model)
        self.json(summary(self.app.runner.rerun(jid, model["id"], options)), 201)

    def retry_download(self, job, p):
        """The same job, downloaded again (a part already downloaded is resumed), with the model asked for."""
        model = self.ready_model(p, job["model"] if job["model"] in self.app.cfg.models else None)
        options = parse_options({**(job.get("options") or {}), **p}, self.app.cfg, model)
        self.app.runner.cancelled.discard(job["id"])
        self.app.store.update(job["id"], model=model["id"], model_title=model["title"], kind=model["kind"],
                              options=options, error=None, finished=None)
        self.app.runner.submit(job["id"])
        self.json(summary(self.app.store.get(job["id"])), 201)

    def send_file(self, f, ctype):
        """A file, or the part of it a Range header asks for (206; 416 outside the file), so players can seek."""
        size = f.stat().st_size
        start, end, status = 0, size - 1, 200
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:  # suffix range: the last N bytes
                start = max(0, size - int(m.group(2)))
            if start > end or start >= size:
                self.reply(416, headers={"Content-Range": f"bytes */{size}"})
                return
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(f, "rb") as fh:
            fh.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = fh.read(min(1 << 16, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def audio(self, _params, jid):
        self.job(jid)
        f = self.app.store.dir(jid) / "audio.flac"
        if not f.exists():
            raise ApiError(404, "audio not ready")
        self.send_file(f, "audio/flac")

    def video(self, _params, jid):
        """The recording's picture for the job page (app/video.py), when it has one."""
        self.job(jid)
        f = self.app.store.dir(jid) / video.NAME
        if not f.exists():
            raise ApiError(404, "this transcription has no video")
        self.send_file(f, "video/mp4")

    def export(self, params, jid, fmt):
        job = self.job(jid)
        if fmt not in T.EXPORTS:
            raise ApiError(404, "unknown format")
        fn, ctype = T.EXPORTS[fmt]
        n = params.get("version")  # text: "0" is the model's output; without it, the current version
        shown, lines, edited = self.at_version(job, n)
        details = None if params.get("details") == "0" else report.details(shown, lines, edited)
        body = fn(shown, lines, details).encode()
        tag = f"-v{n}" if n else ""
        name = f"{slug(job.get('title'))}{tag}.{fmt}"
        ascii_name = re.sub(r"[^A-Za-z0-9._-]", "", name)
        if not re.search(r"[A-Za-z0-9]", ascii_name.rsplit(".", 1)[0].removesuffix(tag)):
            ascii_name = f"transcript{tag}.{fmt}"
        disposition = f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{urllib.parse.quote(name)}'
        self.reply(200, body, ctype, {"Content-Disposition": disposition})

    # transcripts of one recording: grouping, comparing and combining them (app/compare.py)
    def job_group(self, _params, jid):
        self.job(jid)
        gid, jobs = compare.group_of(self.app.store, jid)
        self.json({"group": gid, "jobs": [{**summary(j), "group": gid} for j in jobs]})

    def compare_jobs(self, params):
        ids = [x for x in str(params.get("ids") or "").split(",") if x]
        self.json(compare.view(self.app.store, ids, params.get("base")))

    def combine_jobs(self, _params):
        """The picked parts saved as a new version of the base transcript; answers like GET /api/jobs/<id>."""
        base, _ = compare.save_combined(self.app.store, self.body_json(), self.app.cfg)
        self.changed(base)
        self.get_job({}, base)

    def combine_preview(self, _params):
        """What POST /api/combine with the same body would change, to review it first."""
        self.json(compare.preview_combined(self.app.store, self.body_json(), self.app.cfg))

    def at_version(self, job, n):
        """(job, lines, edited by hand) as they are now, or as they were at version n."""
        if not n:
            data = self.app.store.transcript(job["id"])
            return job, self.lines_of(job["id"]), bool(data and data.get("edited"))
        found = history.at(self.app.store, job["id"], int(n), self.app.cfg) if re.fullmatch(r"[0-9]{1,6}", n) else None
        if found is None:
            raise ApiError(404, "no such version")
        return found

    # ---- version history (app/history.py) ----------------------------------------------------------
    def versions(self, _params, jid):
        self.job(jid)
        self.json({"versions": history.ensure(self.app.store, jid, self.app.cfg) or []})

    def get_version(self, params, jid, n):
        """Version n and its differences from the version before, or from ?against=m."""
        self.job(jid)
        against = params.get("against")
        if against is not None and not re.fullmatch(r"[0-9]{1,6}", against):
            raise ApiError(404, "no such version")
        found = history.version(self.app.store, jid, int(n), self.app.cfg, None if against is None else int(against))
        if found is None:
            raise ApiError(404, "no such version")
        self.json(found)

    def patch_version(self, _params, jid, n):
        """{"message": ...} adds, changes or clears the message of version n."""
        self.job(jid)
        message = self.body_json().get("message")
        try:
            versions = history.set_message(self.app.store, jid, int(n), message, self.app.cfg)
        except KeyError:
            raise ApiError(404, "no such version")
        self.json({"versions": versions})

    def restore_version(self, _params, jid, n):
        """Version n becomes the current one again, saved as a new version (optional {"message": ...})."""
        self.job(jid)
        message = self.body_json().get("message")
        try:
            history.restore(self.app.store, jid, int(n), message, self.app.cfg)
        except KeyError:
            raise ApiError(404, "no such version")
        self.changed(jid)
        self.get_job({}, jid)

    def save_key(self, _params):
        p = self.body_json()
        model = self.app.cfg.models.get(p.get("model"))
        if model is None or model["kind"] != "hosted":
            raise ApiError(400, "not a hosted model")
        if "region" in p and "region" in model:  # Azure Speech: saved next to the key; empty = the config's
            region = str(p.get("region") or "").strip().lower()
            if region and not re.fullmatch(r"[a-z0-9]{2,40}", region):
                raise ApiError(400, "a region is a name like westeurope")
            self.app.cfg.save_key(f"{model['id']}:region", region)
        if "key" in p or "region" not in p:  # a request with only a region keeps the key
            self.app.cfg.save_key(model["id"], str(p.get("key") or "").strip())
        self.json(self.app.model_info(model))

    def power(self, _params):
        profile = self.body_json().get("profile")
        if profile not in ("performance", "balanced", "power-saver"):
            raise ApiError(400, "unknown power profile")
        if not set_power_profile(profile):
            raise ApiError(500, "could not change the power profile (is powerprofilesctl available?)")
        self.json({"power": power_profile()})

    def probe(self, _params):
        path = Path(str(self.body_json().get("path") or "")).expanduser()
        if not path.is_file():
            raise ApiError(404, f"file not found: {path}")
        self.json({"path": str(path.resolve()), "name": path.name, "size": path.stat().st_size,
                   "seconds": probe_seconds(path)})

    def settings(self, _params):
        p = self.body_json()
        values = {k: p[k] for k in self.app.cfg.SETTINGS if k in p}
        with self.app.settings_lock:
            try:
                self.app.cfg.save_settings(**values)
            except ValueError as e:
                raise ApiError(400, str(e))
        self.json(self.app.status())

    # Assistants over MCP (app/assistants.py; app/mcp.py is the server they start): Settings → Assistants. The
    # two switches are saved with the other settings (POST /api/settings).
    def assistants_info(self, _params):
        self.json(self.app.assistants_view())

    def hide_from_assistants(self, _params):
        """{key: "job:<id>" or "course:<playlist id>", hidden: true or false}: hidden from assistants, or
        shown again. Answers like GET /api/assistants."""
        p = self.body_json()
        if not isinstance(p.get("hidden"), bool):
            raise ApiError(400, "hidden must be true or false")
        with self.app.settings_lock:
            try:
                assistants.set_hidden(self.app.cfg, p.get("key"), p["hidden"])
            except ValueError as e:
                raise ApiError(400, str(e))
        self.json(self.app.assistants_view())

    def clear_assistants_log(self, _params):
        """Empties the list of what assistants asked. Answers like GET /api/assistants."""
        try:
            assistants.clear_log(self.app.cfg)
        except OSError as e:
            raise ApiError(500, f"could not clear the list: {e}")
        self.json(self.app.assistants_view())

    def open_folder(self, _params):
        """Show the data folder in the file manager, or with {"job": id} the courses folder a YouTube job's
        files are in (app/courses.py). Only these folders: the path isn't taken from the request."""
        jid = self.body_json().get("job")
        if jid:
            job = self.job(str(jid))
            if not job.get("youtube_id"):
                raise ApiError(400, "only a YouTube video's transcript has a course folder")
            try:
                path = courses.folder_of(self.app.cfg, job)
            except ValueError:
                raise ApiError(400, "this transcription's course folder is not in the courses folder")
            path = path if path.is_dir() else self.app.cfg.courses  # nothing finished yet: the courses folder
            path.mkdir(parents=True, exist_ok=True)
            folder = str(path)
        else:
            folder = str(self.app.cfg.storage)
        try:
            show_folder(folder)
        except OSError as e:
            raise ApiError(500, f"could not open the folder: {e}")
        self.json({"opened": folder})

    def download(self, _params, item_id):
        items = self.app.cfg.download_items()
        if item_id not in items:
            raise ApiError(404, "nothing to download with that name")
        wanted = [item_id]
        if item_id in self.app.cfg.models and "voiceprints" in items:  # speaker labels need it too
            wanted.append("voiceprints")
        try:
            self.app.downloads.start(wanted)
        except OSError as e:
            raise ApiError(507, str(e))
        self.json(self.app.status())

    def cancel_download(self, _params, item_id):
        self.app.downloads.cancel(item_id)
        self.json(self.app.status())

    def remove_download(self, _params, item_id):
        if item_id not in self.app.cfg.download_items():
            raise ApiError(404, "nothing to remove with that name")
        if any(j["status"] in ACTIVE and (j["model"] == item_id or item_id in ("voiceprints", "cuda")) and j["kind"] == "local"
               or item_id == "deno" and j.get("stage") == "downloading" for j in self.app.store.list()):
            raise ApiError(409, "a transcription is using it; wait until it has finished")
        try:
            self.app.downloads.remove(item_id)
        except RuntimeError as e:
            raise ApiError(409, str(e))
        self.json(self.app.status())

    # Models added from Hugging Face (app/hub.py)
    def hub_inspect(self, _params):
        p = self.body_json()
        try:
            self.json(hub.inspect(self.app.cfg, p.get("url"), p.get("file"), p.get("revision")))
        except hub.HubError as e:
            raise ApiError(400, str(e))

    def hub_add(self, _params):
        p = self.body_json()
        try:
            model = hub.add(self.app.cfg, p.get("url"), p.get("file"), p.get("revision"))
        except hub.HubError as e:
            raise ApiError(400, str(e))
        self.download({}, model["id"])  # with the voiceprint model, as from a model card; replies with the status

    def forget_model(self, _params, model_id):
        if not (self.app.cfg.models.get(model_id) or {}).get("hub"):
            raise ApiError(404, "no model added from Hugging Face with that id")
        if any(j["status"] in ACTIVE and j["model"] == model_id for j in self.app.store.list()):
            raise ApiError(409, "a transcription is using it; wait until it has finished")
        if (self.app.downloads.jobs.get(model_id) or {}).get("state") == "queued" or self.app.downloads.busy(model_id):
            raise ApiError(409, "wait until the download has finished or cancel it")
        self.app.downloads.remove(model_id)
        hub.forget(self.app.cfg, model_id)
        self.json(self.app.status())

    def quit(self, _params):
        self.json({"bye": True})
        target = self.app.on_quit or self.app.server.shutdown
        threading.Thread(target=target, daemon=True).start()


def show_folder(folder):
    """Open one of the app's own folders in the file manager."""
    if os.name == "nt":
        os.startfile(folder)  # noqa: S606 — our own folder
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", folder],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def clean_lines(lines):
    out = []
    for x in lines or []:
        try:
            start, end = float(x["start"]), float(x["end"])
        except (KeyError, TypeError, ValueError):
            raise ApiError(400, "each line needs a numeric start and end")
        text = str(x.get("text") or "").strip()
        speaker = None if x.get("speaker") in (None, "") else str(x["speaker"])
        if speaker is not None and not re.fullmatch(r"\d{1,3}", speaker):
            raise ApiError(400, "speaker ids are numbers")
        if text:
            out.append({"start": start, "end": max(start, end), "text": text[:20000], "speaker": speaker})
    return out


ROUTES = [
    ("GET", r"/", Handler.static),
    ("GET", r"/static/((?:logos/)?[\w.-]+)", Handler.static),  # logos/: the brands' logo files (brands.js)
    ("GET", r"/sw\.js", Handler.service_worker),
    ("GET", r"/api/status", Handler.status),
    ("GET", r"/api/jobs", Handler.list_jobs),
    ("POST", r"/api/jobs", Handler.create_job),
    ("GET", rf"/api/jobs/{ID}", Handler.get_job),
    ("PATCH", rf"/api/jobs/{ID}", Handler.patch_job),
    ("DELETE", rf"/api/jobs/{ID}", Handler.delete_job),
    ("POST", rf"/api/jobs/{ID}/cancel", Handler.cancel_job),
    ("POST", rf"/api/jobs/{ID}/rerun", Handler.rerun_job),
    ("GET", rf"/api/jobs/{ID}/audio", Handler.audio),
    ("GET", rf"/api/jobs/{ID}/video", Handler.video),
    ("GET", rf"/api/jobs/{ID}/export/(\w+)", Handler.export),
    ("GET", rf"/api/jobs/{ID}/group", Handler.job_group),
    ("GET", r"/api/compare", Handler.compare_jobs),
    ("POST", r"/api/combine", Handler.combine_jobs),
    ("POST", r"/api/combine/preview", Handler.combine_preview),
    ("POST", rf"/api/jobs/{ID}/diff", Handler.diff_job),
    ("GET", rf"/api/jobs/{ID}/versions", Handler.versions),
    ("GET", rf"/api/jobs/{ID}/versions/(\d+)", Handler.get_version),
    ("PATCH", rf"/api/jobs/{ID}/versions/(\d+)", Handler.patch_version),
    ("POST", rf"/api/jobs/{ID}/versions/(\d+)/restore", Handler.restore_version),
    ("POST", r"/api/keys", Handler.save_key),
    ("POST", r"/api/power", Handler.power),
    ("POST", r"/api/settings", Handler.settings),
    ("GET", r"/api/assistants", Handler.assistants_info),
    ("POST", r"/api/assistants/hide", Handler.hide_from_assistants),
    ("POST", r"/api/assistants/log/clear", Handler.clear_assistants_log),
    ("POST", r"/api/open-folder", Handler.open_folder),
    ("POST", r"/api/probe", Handler.probe),
    ("POST", r"/api/downloads/([\w-]+)", Handler.download),
    ("POST", r"/api/downloads/([\w-]+)/cancel", Handler.cancel_download),
    ("DELETE", r"/api/downloads/([\w-]+)", Handler.remove_download),
    ("POST", r"/api/youtube/inspect", Handler.youtube_inspect),
    ("POST", r"/api/youtube", Handler.youtube_add),
    ("POST", r"/api/hub/inspect", Handler.hub_inspect),
    ("POST", r"/api/hub/add", Handler.hub_add),
    ("DELETE", r"/api/models/(hf-[\w-]+)", Handler.forget_model),
    ("POST", r"/api/quit", Handler.quit),
]


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address):
        if isinstance(sys.exc_info()[1], ConnectionError):  # the browser dropped a connection it kept open (a player)
            return
        super().handle_error(request, client_address)


def make_server(cfg, host=None, port=None):
    app = App(cfg)
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = Server((host or cfg.server["host"], cfg.server["port"] if port is None else port), handler)
    app.server = server
    return server, app
