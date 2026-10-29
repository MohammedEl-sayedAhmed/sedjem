"""Anonymous usage counts (Settings → About; docs/13-usage-counts.md): how many installs use Sedjem and in which
countries, on which versions, systems and settings, and what they use. They go to Aptabase (aptabase.com) as three
events, about six a month for an install that is used every week:

  install       once for each data folder, the first time the counts are allowed
  active_month  once a calendar month, the first time Sedjem runs that month: the month and how long ago the install
                was counted, the kind of install and the window, some settings, the computer in broad ranges, how
                many transcriptions and courses there are in ranges, and which models are downloaded and which
                hosted services have a key (whether there is one, never the key)
  week_summary  once for each week that had any use, the first time Sedjem runs after it ended: the week, on how
                many days Sedjem was used and how often it was started, and how many runs there were, how long,
                with which model, service, speaker model, language and device, how many failed or were cancelled,
                and how many exports, copies, compares, edits, YouTube videos and assistant requests

Months and weeks are UTC, the weeks ISO's (2026-W44); the event says which month or week it counts, so it may be
sent later. Counted are only the ends of runs and YouTube jobs, the exports and copies, the compare view, saved
edits, branches, restores and combines, downloads, Update now, the app's starts and the assistants' log. Nothing
else is sent: no transcript, audio, file name or path, link, title, speaker name, key, host or user name, no
identifier, no error message and no one run's length. Every event passes checked() on its way out, which lets through
only these events with these fields, each value from a fixed list or a whole number. <storage>/usage.json keeps the
flags (when the install was counted, the month counted last, how far the assistants' log was read), the counts of
the weeks not sent yet and the events waiting.

The counts are allowed when the switch is on (usage_counts: on by default, but nothing is counted or sent before
the first-start dialog has said so), app/config.toml has an app key ([usage] app_key), DO_NOT_TRACK and
SEDJEM_NO_USAGE aren't set, and the tests aren't running. Turning the switch off deletes the counts kept and the
events waiting.

Sending: a thread posts the waiting events to Aptabase's batch endpoint with urllib, soon after start and whenever
something is due (it looks every 15 minutes). Each event gets its time when it is sent, so one that waits while the
computer is offline is still taken. An event Aptabase refuses (400, or 200 with an error for a key it doesn't take)
is dropped; one that couldn't be sent (no connection, 408, 429, 5xx) is tried again an hour later. It never raises,
never blocks the app and prints nothing.
"""
import json
import os
import platform
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path

import sysinfo

from . import assistants
from .config import FROZEN, _read_json, replace_file
from .updates import LOOPBACK, api_ok

SDK = "sedjem-usage@1"
HOSTS = {"EU": "https://eu.aptabase.com", "US": "https://us.aptabase.com"}  # an A-SH- key needs [usage] host
KEY = re.compile(r"A-(EU|US|SH)-[A-Za-z0-9]{1,64}")
PATH = "/api/v0/events"
OPT_OUT = ("DO_NOT_TRACK", "SEDJEM_NO_USAGE")
TESTING = ("unittest", "pytest")  # the tests import one of these and the app never does (tests/test_usage.py checks)

TIMEOUT = 10          # seconds, for one request
START_DELAY = 20      # the first look: this long after start (start stays fast)
WAKE = 900            # how often the thread looks at the clock
RETRY = 3600          # after a request that couldn't be sent
BATCH = 25            # events in one request, at most
KEEP = 100            # events kept waiting, at most (about a year and a half of them); the oldest go first

# Where the counts come from: the app's ids of the built-in local models, the hosted services and the speaker
# models, and what a run's speaker model file or folder is called
ENGINES = ("cohere", "whisper_medium", "whisper_large", "other_local", "hosted")
MODELS = {"cohere": "cohere", "whisper-medium": "whisper_medium", "large-v3": "whisper_large"}
SERVICES = ("elevenlabs", "speechmatics", "gemini", "deepgram", "assemblyai", "azure", "openai", "groq", "mistral")
SPEAKER_MODELS = {"titanet-small": "titanet_small", "titanet-large": "titanet_large", "campplus": "campp",
                  "nemotron-3-diarization": "nemotron"}
SPEAKER_FILES = (("nemotron", "nemotron"), ("titanet_small", "titanet_small"), ("titanet_large", "titanet_large"),
                 ("campplus", "campp"))
FORMATS = ("txt", "srt", "vtt", "md", "json")
LANGUAGES = ("ar", "en", "auto")
GPU_KINDS = ("vulkan", "cuda", "metal", "rocm")  # a local run's device, as app/server.py device_class reads it
FALLBACK = "after a GPU error"  # transcribe.py's device when a run went on on the CPU: "cpu (after a GPU error)"
EDITS = {"edit": "edit_sessions", "rename": "renames", "merge": "merges"}  # app/history.py's kinds of change
FAILURES = {"read": "failed_read", "model": "failed_model", "hosted": "failed_hosted", "download": "failed_download"}

# What may be sent. week_summary has these whole numbers and the week ("2026-W44"); locally the times are kept in
# seconds and sent in whole minutes, and the days of use as a set of weekdays. active_month has the props in VALUES,
# each one of its values, the ones in FLAGS, each 0 or 1, and the month ("2026-11"). install has none.
SUMMARY = ("transcriptions", "minutes", *ENGINES, *SERVICES, "speakers", "speakers_given", "speakers_estimated",
           "titanet_small", "titanet_large", "campp", "nemotron", "runs_under_5", "runs_5_30", "runs_30_60",
           "runs_over_60", "lang_ar", "lang_en", "lang_auto", "cpu_runs", "gpu_runs", "gpu_fallbacks",
           *(f"min_{e}" for e in ENGINES), *(f"proc_{e}" for e in ENGINES), "failed", "failed_read", "failed_model",
           "failed_hosted", "failed_download", "cancelled", "youtube", "youtube_captions", "courses_created",
           "compares", "combines", "edit_sessions", "renames", "merges", "branches", "restores", *FORMATS, "copies",
           "days_used", "starts", "assistant_requests", "assistant_saves", "model_downloads", "update_installs")
SECONDS = {"minutes": "seconds", **{f"min_{e}": f"seconds_{e}" for e in ENGINES},
           **{f"proc_{e}": f"proc_seconds_{e}" for e in ENGINES}}  # sent in minutes, kept in seconds
COUNTS = tuple(SECONDS.get(k, "days" if k == "days_used" else k) for k in SUMMARY)  # as usage.json keeps them
VALUES = {"kind": ("installer", "linux-build", "source", "other"), "ui": ("desktop", "browser"),
          "install_age": ("0", "1", "2", "3-5", "6-11", "12+"),
          "channel": ("stable", "latest"), "update_check": ("on", "off", "unasked"),
          "language": ("ar", "en", "auto", "other"),
          "speaker_model": ("titanet_small", "titanet_large", "campp", "nemotron", "other"),
          "device": ("auto", "cpu"), "mcp_write": ("on", "off"),
          "cpu": ("intel", "amd", "arm", "other"), "gpu": ("none", "intel", "amd", "nvidia", "other"),
          "gpu_vram": ("none", "under 4", "4-8", "8-16", "over 16"), "cuda": ("yes", "no"), "vulkan": ("yes", "no"),
          "ram": ("under 8", "8-16", "16-32", "over 32"), "cores": ("under 4", "4-8", "8-16", "over 16"),
          "power": ("power-saver", "balanced", "performance", "unknown"),
          "disk_free": ("under 10", "10-50", "50-200", "over 200"),
          "library": ("0", "1-9", "10-49", "50-199", "200+"), "courses": ("0", "1-4", "5-19", "20+")}
FLAGS = ("has_cohere", "has_whisper_medium", "has_whisper_large", "has_other_local", "has_titanet_large",
         "has_campp", "has_nemotron", *(f"key_{s}" for s in SERVICES))
# systemProps, with the most characters Aptabase takes for each (None: a boolean)
SYSTEM = {"isDebug": None, "osName": 30, "osVersion": 100, "locale": 10, "appVersion": 50, "engineName": 30,
          "engineVersion": 30, "sdkVersion": 40}
WEEK = re.compile(r"\d{4}-W\d{2}")
MONTH = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z")
SESSION = re.compile(r"[1-9]\d{9,19}")


def test_run():
    """True while the tests run: then nothing is counted or sent (post() also refuses any address but this
    computer's on its own, so a test that replaces this function can reach only its stand-in)."""
    return any(m in sys.modules for m in TESTING)


def opted_out(value):
    """DO_NOT_TRACK=1 and SEDJEM_NO_USAGE=1 turn the counts off, and so does any other value but 0 or false."""
    return (value or "").strip().lower() not in ("", "0", "false", "no", "off")


# ---------------------------------------------------------------------------------------------
# What is sent
# ---------------------------------------------------------------------------------------------

def utc(t):
    return datetime.fromtimestamp(t, timezone.utc)


def stamp(t):
    """An event's time as Aptabase reads it, in UTC: 2026-10-29T14:03:12.000Z."""
    return utc(t).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def month_of(t):
    return utc(t).strftime("%Y-%m")


def week_of(t):
    year, week, _ = utc(t).isocalendar()
    return f"{year}-W{week:02d}"


def weekday_bit(t):
    """The local day of the week of a moment, as a bit: Monday 1, Tuesday 2, ... Sunday 64."""
    return 1 << datetime.fromtimestamp(t).weekday()


def session_id(t):
    """Aptabase's session id: the seconds since 1970 and eight random digits. A new one for each request."""
    return str(int(t) * 10 ** 8 + secrets.randbelow(10 ** 8))


def pretty_name(paths=(sysinfo.OS_RELEASE, Path("/usr/lib/os-release"))):
    """Linux: the system's name as os-release gives it (PRETTY_NAME, "Ubuntu 24.04.4 LTS"), or ""."""
    for path in paths:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(r"""^PRETTY_NAME=["']?(.*?)["']?[ \t]*$""", text, re.M)
        return m.group(1).strip() if m else ""
    return ""


def system_name(platform_name=None):
    """(osName, osVersion): Windows and its version number (10.0.26100), Linux and its PRETTY_NAME, macOS and its
    version."""
    p = platform_name or sys.platform
    if p == "win32":
        return "Windows", platform.version()
    if p == "darwin":
        return "macOS", platform.mac_ver()[0]
    if p.startswith("linux"):
        return "Linux", pretty_name()
    return platform.system() or p, platform.release()


def windows_locale():
    import ctypes
    buf = ctypes.create_unicode_buffer(85)
    return buf.value if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85) else ""


def system_locale(name=None):
    """The system's language and region as "ar-EG" or "en-US" (the language alone without a region), or "". name:
    the locale as the system names it (en_US.UTF-8), else Windows' user locale or the environment's."""
    if name is None:
        try:
            name = windows_locale() if sys.platform == "win32" else \
                next((os.environ[v] for v in ("LC_ALL", "LC_MESSAGES", "LANG") if os.environ.get(v)), "")
        except Exception:  # noqa: BLE001 — unknown, then
            name = ""
    m = re.match(r"([A-Za-z]{2})(?:[_-]([A-Za-z]{2}))?(?=$|[.@_-])", name or "")
    return (m[1].lower() + (f"-{m[2].upper()}" if m[2] else "")) if m else ""


def clip(value, limit):
    return " ".join(str(value or "").split())[:limit]


def system(version):
    """The systemProps of every event. version: the version Settings → About shows (a nightly preview's is
    0.3.1-preview.20261101)."""
    os_name, os_version = system_name()
    return {"isDebug": os.environ.get("SEDJEM_USAGE_DEBUG") == "1", "osName": clip(os_name, 30),
            "osVersion": clip(os_version, 100), "locale": system_locale(), "appVersion": clip(version, 50),
            "engineName": "python", "engineVersion": clip(platform.python_version(), 30), "sdkVersion": SDK}


def ranged(n, edges, names):
    """The name of the range n falls in: names[i] for n below edges[i], the last name above them all."""
    return next((name for edge, name in zip(edges, names) if n < edge), names[-1])


def install_kind(kind, frozen=FROZEN):
    """installer (the Windows installer's copy), linux-build, source, or other (another desktop build). kind is
    app/updates.py's: source, windows, linux or other."""
    if not frozen:
        return "source"
    return {"windows": "installer", "linux": "linux-build"}.get(kind, "other")


def gpu_kind(gpu):
    """The graphics card the app's own check found that matters most for speed: nvidia, else amd, else intel, else
    other; none without one (or when the check failed)."""
    gpu = gpu if isinstance(gpu, dict) else {}
    names = " ".join(str(d.get("name") or "") for d in gpu.get("devices") or [] if isinstance(d, dict)).lower()
    if gpu.get("cuda_devices") or re.search(r"nvidia|geforce|quadro|tesla|\brtx\b", names):
        return "nvidia"
    if re.search(r"\bamd\b|radeon|\bati\b", names):
        return "amd"
    if "intel" in names:
        return "intel"
    return "other" if names.strip() else "none"


def gpu_vram(gpu):
    """The memory of the best dedicated graphics card the check found, as a range. Integrated graphics share the
    computer's memory, so they count as none. Cards report a little less than they were sold with, so one with 4, 6
    or 8 GB counts as 4-8 and one with 12 or 16 GB as 8-16."""
    devices = (gpu or {}).get("devices") if isinstance(gpu, dict) else None
    sizes = [d.get("memory") for d in devices or [] if isinstance(d, dict) and d.get("type") == "gpu"
             and isinstance(d.get("memory"), (int, float)) and d["memory"] > 0]
    if not sizes:
        return "none"
    return ranged(max(sizes) / 2 ** 30, (3.5, 8.5, 16.5), ("under 4", "4-8", "8-16", "over 16"))


def cpu_kind(machine):
    """The processor's maker: arm for any ARM processor (Apple's, Qualcomm's), else intel or amd by its name."""
    arch, name = str(machine.get("arch") or "").lower(), str(machine.get("cpu") or "").lower()
    if arch.startswith(("arm", "aarch")):
        return "arm"
    if "intel" in name:
        return "intel"
    return "amd" if re.search(r"\bamd\b|ryzen|epyc|threadripper", name) else "other"


def ram_range(gb):
    """The memory as a range. The system reports a little less than the computer was sold with (some is kept for
    the graphics), so a computer with 8 or 16 GB counts as 8-16 and one with 24 or 32 GB as 16-32."""
    if not isinstance(gb, (int, float)) or gb <= 0:
        return "under 8"
    return ranged(gb, (6.5, 16.5, 32.5), ("under 8", "8-16", "16-32", "over 32"))


def cores_range(n):
    """The processor threads as a range: 4 to 8 counts as 4-8, 9 to 16 as 8-16."""
    n = n if isinstance(n, int) and n > 0 else 1
    return ranged(n, (4, 9, 17), ("under 4", "4-8", "8-16", "over 16"))


def facts(kind, desktop, gpu, machine=None, power=None, free=None):
    """active_month's props about what runs where. kind: app/updates.py's; desktop: started as the desktop app; gpu:
    the app's GPU check; power: the power profile (Linux); free: the bytes free on the data folder's disk."""
    machine = machine if isinstance(machine, dict) else sysinfo.machine()
    gpu = gpu if isinstance(gpu, dict) else {}
    return {"kind": install_kind(kind), "ui": "desktop" if desktop else "browser", "cpu": cpu_kind(machine),
            "gpu": gpu_kind(gpu), "gpu_vram": gpu_vram(gpu), "cuda": "yes" if gpu.get("cuda_devices") else "no",
            "vulkan": "yes" if any(isinstance(d, dict) and d.get("kind") == "vulkan" for d in gpu.get("devices") or [])
            else "no",
            "ram": ram_range(machine.get("ram_gb")), "cores": cores_range(machine.get("threads")),
            "power": power if power in VALUES["power"] else "unknown",
            "disk_free": ranged(free / 1e9, (10, 50, 200), VALUES["disk_free"])
            if isinstance(free, (int, float)) else "under 10"}


def settings_facts(cfg):
    """active_month's props about the settings: the update channel and whether Sedjem checks by itself, the default
    language, the speaker model chosen, whether the graphics card may be used, and whether assistants may save."""
    s = cfg.settings()
    language = cfg.defaults.get("language")
    return {"channel": s.get("update_channel") if s.get("update_channel") in ("stable", "latest") else "stable",
            "update_check": {True: "on", False: "off"}.get(s.get("update_check"), "unasked"),
            "language": language if language in LANGUAGES else "other",
            "speaker_model": SPEAKER_MODELS.get(cfg.speaker_model_choice(), "other"),
            "device": "cpu" if s.get("device") == "cpu" else "auto",  # as the app reads it: anything else is auto
            "mcp_write": "on" if s.get("mcp_write") is True else "off"}


def library_facts(jobs):
    """active_month's props about the library: how many transcriptions there are and how many courses (YouTube
    playlists), in ranges."""
    courses = {(j.get("course") or {}).get("id") for j in jobs if isinstance(j, dict)} - {None, ""}
    return {"library": ranged(len(jobs), (1, 10, 50, 200), VALUES["library"]),
            "courses": ranged(len(courses), (1, 5, 20), VALUES["courses"])}


def adoption_facts(cfg):
    """active_month's 0/1 props: which models are downloaded (any local model besides the three built in counts as
    other), and which hosted services have a key, from the app or the environment (whether there is one, never the
    key)."""
    def ready(model):
        try:
            return int(bool(model) and cfg.availability(model)[0])
        except Exception:  # noqa: BLE001 — not known: as not downloaded
            return 0

    speakers = cfg.speaker_models()

    def speaker_ready(mid):
        try:
            return int(mid in speakers and cfg.speaker_model_ready(speakers[mid]))
        except Exception:  # noqa: BLE001
            return 0

    models = cfg.models
    others = [m for m in models.values() if m.get("kind") == "local" and m["id"] not in MODELS]
    return {"has_cohere": ready(models.get("cohere")), "has_whisper_medium": ready(models.get("whisper-medium")),
            "has_whisper_large": ready(models.get("large-v3")), "has_other_local": int(any(ready(m) for m in others)),
            "has_titanet_large": speaker_ready("titanet-large"), "has_campp": speaker_ready("campplus"),
            "has_nemotron": speaker_ready("nemotron-3-diarization"),
            **{f"key_{s}": int(s in models and bool(cfg.api_key(models[s]))) for s in SERVICES}}


def age_range(install_day, month):
    """How many calendar months since the install was counted, as a range ("0" in the month it was counted)."""
    try:
        y1, m1 = int(install_day[:4]), int(install_day[5:7])
        y2, m2 = int(month[:4]), int(month[5:7])
    except (TypeError, ValueError):
        return "0"
    return ranged(max(0, (y2 - y1) * 12 + m2 - m1), (1, 2, 3, 6, 12), VALUES["install_age"])


def engine_of(job):
    if job.get("kind") == "hosted":
        return "hosted"
    return MODELS.get(job.get("model"), "other_local")


def speaker_model_of(name):
    """The speaker model a run's labels came from, by its file or folder name, or None."""
    name = str(name or "").lower()
    return next((ours for part, ours in SPEAKER_FILES if part in name), None)


def week_props(week, counts):
    """week_summary's props from a week's counts: whole numbers, the times in minutes, the days of use, and the
    week."""
    props = {}
    for k in SUMMARY:
        if k in SECONDS:
            props[k] = round(max(0.0, float(counts.get(SECONDS[k]) or 0)) / 60)
        elif k == "days_used":
            props[k] = bin(int(counts.get("days") or 0) & 0x7F).count("1")
        else:
            props[k] = max(0, int(counts.get(k) or 0))
    return {**props, "week": week}


def checked(event):
    """The event with only what may be sent, or None when it isn't one of the three events as this module makes
    them (one changed in usage.json, or one kept from an older version, say)."""
    try:
        name, props, system_props = event["eventName"], event["props"], event["systemProps"]
        if name == "install":
            ok = props == {}
        elif name == "active_month":
            ok = props.keys() == {*VALUES, *FLAGS, "month"} and all(props[k] in VALUES[k] for k in VALUES) \
                and all(type(props[k]) is int and props[k] in (0, 1) for k in FLAGS) \
                and isinstance(props["month"], str) and MONTH.fullmatch(props["month"]) is not None
        elif name == "week_summary":
            ok = props.keys() == {*SUMMARY, "week"} and isinstance(props["week"], str) \
                and WEEK.fullmatch(props["week"]) is not None \
                and all(type(props[k]) is int and props[k] >= 0 for k in SUMMARY)
        else:
            ok = False
        ok = ok and system_props.keys() == SYSTEM.keys() and type(system_props["isDebug"]) is bool \
            and all(type(system_props[k]) is str and len(system_props[k]) <= n for k, n in SYSTEM.items() if n) \
            and type(event["timestamp"]) is str and STAMP.fullmatch(event["timestamp"]) is not None \
            and type(event["sessionId"]) is str and SESSION.fullmatch(event["sessionId"]) is not None
    except (KeyError, TypeError, AttributeError):
        return None
    if not ok:
        return None
    return {"timestamp": event["timestamp"], "sessionId": event["sessionId"], "eventName": name,
            "systemProps": {k: system_props[k] for k in SYSTEM}, "props": dict(props)}


def log_time(entry):
    """When an entry of the assistants' log was written, as seconds since 1970, or None."""
    try:
        return datetime.fromisoformat(str(entry.get("time"))).timestamp()
    except (TypeError, ValueError, AttributeError, OverflowError):
        return None


# ---------------------------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------------------------

class NoRedirect(urllib.request.HTTPRedirectHandler):
    """The events go to the address asked for or nowhere: a redirect is an answer like any other (and dropped)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def post(url, key, agent, events, timeout=None):
    """POST the events to Aptabase: (HTTP status, the answer's JSON or None); status None when there was no answer
    (no connection, a timeout). While the tests run only this computer can be reached, whatever a test changed."""
    if any(m in sys.modules for m in TESTING) and urllib.parse.urlsplit(url).hostname not in LOOPBACK:
        return None, None
    body = json.dumps(events, ensure_ascii=True).encode("ascii")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"App-Key": key, "Content-Type": "application/json", "User-Agent": agent})
    try:
        with OPENER.open(req, timeout=TIMEOUT if timeout is None else timeout) as r:
            status, raw = r.status, r.read(65536)
    except urllib.error.HTTPError as e:
        return e.code, None
    except (OSError, ValueError, HTTPException):  # no connection, a timeout, a connection dropped mid-answer
        return None, None
    try:
        return status, json.loads(raw or b"null")
    except ValueError:
        return status, None


def outcome(status, answer):
    """What became of events posted: "sent", "dropped" (refused: a bad event, or a key Aptabase doesn't take,
    which it answers with 200 and an error) or "later" (no answer, 408, 429, 5xx: try again)."""
    if status is None or status in (408, 429) or status >= 500:
        return "later"
    if status == 200:
        return "dropped" if isinstance(answer, dict) and answer.get("error") else "sent"
    return "dropped"  # 400, and any other answer: sending the same events again won't change it


# ---------------------------------------------------------------------------------------------
# The app's counts
# ---------------------------------------------------------------------------------------------

class Usage:
    """The counts, the switch's state for /api/status, and the thread that sends.

    version() is the version Settings → About shows; facts() gives active_month's props but the month and the
    install's age (the app waits for its GPU check first, in this module's thread)."""

    def __init__(self, cfg, version=None, facts=None):
        self.cfg = cfg
        self.version = version or (lambda: "")
        self.facts = facts or (lambda: {})
        conf = cfg.data.get("usage") if isinstance(cfg.data.get("usage"), dict) else {}
        self.key = str(conf.get("app_key") or "").strip()
        self.host = str(conf.get("host") or "").strip().rstrip("/")
        self.path = cfg.storage / "usage.json"
        self.lock = threading.RLock()
        self.send_lock = threading.Lock()
        self.wake = threading.Event()
        self.thread = None
        self.failed = None  # when the last request that couldn't be sent was tried
        self.clock = time.time  # the tests set their own
        self.data = self.load()

    # ---- state ---------------------------------------------------------------------------------
    def load(self):
        """usage.json, read with care: {"install": the day it was counted, "month": the month counted last,
        "assistants": [the time of the newest entry of the assistants' log counted, how many entries had that time],
        "weeks": {week: counts}, "queue": the events waiting, without their time}."""
        data = _read_json(self.path)
        weeks = data.get("weeks") if isinstance(data.get("weeks"), dict) else {}
        seen = data.get("assistants")
        return {"install": data["install"] if isinstance(data.get("install"), str) else None,
                "month": data["month"] if isinstance(data.get("month"), str) else None,
                "assistants": seen if isinstance(seen, list) and len(seen) == 2
                and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in seen) else None,
                "weeks": {w: c for w, c in weeks.items() if WEEK.fullmatch(str(w)) and isinstance(c, dict)},
                "queue": [e for e in data.get("queue") or [] if isinstance(e, dict)] if isinstance(data.get("queue"), list) else []}

    def save(self):
        try:
            self.cfg.storage.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            replace_file(tmp, self.path)
        except OSError:
            pass  # kept in memory until the next change

    def url(self):
        """Where the events go: the app key's region (A-EU-, A-US-), or [usage] host for a self-hosted key (A-SH-);
        None without a key that can be used."""
        m = KEY.fullmatch(self.key)
        host = (HOSTS.get(m[1]) or self.host) if m else ""
        return f"{host}{PATH}" if host and api_ok(host) else None

    def blocked(self):
        """The variable in the environment that turns the counts off whatever the switch says, or None."""
        return next((v for v in OPT_OUT if opted_out(os.environ.get(v))), None)

    def allowed(self, settings=None):
        """Whether Sedjem counts and sends now."""
        s = settings if settings is not None else self.cfg.settings()
        return s.get("usage_counts") is True and self.url() is not None and self.blocked() is None and not test_run()

    def summary(self, settings=None):
        """For GET /api/status: available (this copy has a key, so Settings shows the switch), on (the switch as
        Settings shows it), ask (the first-start dialog is to say that they are on) and blocked (the variable that
        turns them off)."""
        value = (settings if settings is not None else self.cfg.settings()).get("usage_counts")
        blocked = self.blocked()
        return {"available": self.url() is not None, "on": value is not False and blocked is None,
                "ask": value is None and blocked is None and self.url() is not None, "blocked": blocked}

    def settings_changed(self, before, after):
        """After the settings changed: turned off, what was kept is deleted at once; allowed now, the install and
        this month are sent soon (when the thread runs)."""
        was, now = before.get("usage_counts"), after.get("usage_counts")
        if now is False and was is not False:
            self.forget()
        elif now is True and was is not True:
            self.wake.set()

    def forget(self):
        """Delete the counts kept and the events waiting, and start the assistants' log afresh when the counts are
        on again (what assistants asked meanwhile isn't counted). The other flags stay, so turning the counts on
        again doesn't count the install, or a month, twice."""
        with self.lock:
            if self.data["weeks"] or self.data["queue"] or self.data["assistants"] is not None:
                self.data["weeks"], self.data["queue"], self.data["assistants"] = {}, [], None
                self.save()

    # ---- counting ------------------------------------------------------------------------------
    def add(self, counts, now):
        """Add counts to the week of `now`, and mark its day as a day of use. The caller holds the lock."""
        week = self.data["weeks"].setdefault(week_of(now), {})
        for k, v in counts.items():
            if k in COUNTS and k != "days":
                week[k] = (week.get(k) or 0) + v
        week["days"] = int(week.get("days") or 0) | weekday_bit(now)

    def count(self, now=None, **add):
        """Add to this week's counts, while the counts are allowed. Never raises."""
        try:
            if not add or not self.allowed():
                return
            with self.lock:
                self.add(add, self.clock() if now is None else now)
                self.save()
        except Exception:  # noqa: BLE001 — counting never gets in the way
            pass

    def job_ended(self, job, lines=None, stage=None):
        """A job finished, failed or was cancelled (app/jobs.py). stage: where a failed job stopped (read, model,
        hosted, download), or "cancelled" for a job the user cancelled. A model run counts as a transcription, with
        its audio length and processing time, its model or service, its speaker labels and where the number of
        speakers came from, its language and device; taking a YouTube video's captions made by people counts only
        as a YouTube job."""
        if not isinstance(job, dict):
            return
        if stage == "cancelled":
            self.count(cancelled=1)
            return
        if job.get("status") == "failed":
            self.count(failed=1, **({FAILURES[stage]: 1} if stage in FAILURES else {}))
            return
        if job.get("status") != "done":
            return
        add = {}
        if job.get("kind") in ("local", "hosted"):
            engine, audio = engine_of(job), max(0.0, float(job.get("audio_s") or 0))
            add.update({"transcriptions": 1, "seconds": audio, engine: 1, f"seconds_{engine}": audio,
                        f"proc_seconds_{engine}": max(0.0, float(job.get("seconds") or 0)),
                        ranged(audio, (300, 1800, 3600), ("runs_under_5", "runs_5_30", "runs_30_60", "runs_over_60")): 1})
            if engine == "hosted" and job.get("model") in SERVICES:
                add[job["model"]] = 1
            options = job.get("options") if isinstance(job.get("options"), dict) else {}
            if any(isinstance(x, dict) and x.get("speaker") is not None for x in lines or []):
                given = isinstance(options.get("speakers"), int) and not isinstance(options.get("speakers"), bool)
                add.update({"speakers": 1, "speakers_given" if given else "speakers_estimated": 1})
                which = speaker_model_of(job.get("voiceprint_model"))
                if which:
                    add[which] = 1
            if options.get("language") in LANGUAGES:
                add[f"lang_{options['language']}"] = 1
            if job["kind"] == "local":
                device = str(job.get("device") or "")
                if device.split(":")[0] in GPU_KINDS:
                    add["gpu_runs"] = 1
                else:
                    add["cpu_runs"] = 1
                    if FALLBACK in device:
                        add["gpu_fallbacks"] = 1
        if job.get("youtube_id"):
            add["youtube"] = 1
            if job.get("kind") == "captions":
                add["youtube_captions"] = 1
        self.count(**add)

    def exported(self, fmt):
        if fmt in FORMATS:
            self.count(**{fmt: 1})

    def compared(self):
        self.count(compares=1)

    def downloaded(self, item):
        """A download finished (app/downloads.py): a model or a speaker model, not NVIDIA's libraries or Deno."""
        if item not in ("cuda", "deno"):
            self.count(model_downloads=1)

    def read_assistants(self):
        """Count what assistants asked since the last look, from the assistants' log (app/assistants.py), into the
        week of each entry: every entry is a request, and an entry of a version an assistant saved is a save too.
        Only the time and the kind of each entry are read. The first look only notes where the log ends."""
        found = []
        for entry in assistants.read_log(self.cfg, assistants.LOG_TRIM_AT):
            t = log_time(entry)
            if t is not None:
                saved = entry.get("action") == "save_version" and type(entry.get("version")) is int
                found.append((t, saved))
        found.sort(key=lambda x: x[0])
        with self.lock:
            seen = self.data["assistants"]
            if found:
                newest = found[-1][0]
                mark = [newest, sum(1 for t, _ in found if t == newest)]
            else:
                mark = [seen[0], 0] if seen else [0.0, 0]
            if seen is not None:
                at_mark = 0
                for t, saved in found:
                    if t == seen[0]:
                        at_mark += 1
                        if at_mark <= seen[1]:
                            continue
                    elif t < seen[0]:
                        continue
                    self.add({"assistant_requests": 1, **({"assistant_saves": 1} if saved else {})}, t)
            if mark != seen:
                self.data["assistants"] = mark
                self.save()

    def due(self, now):
        """Queue what is due now: the install, this month, and each week before this one that has counts (the
        week's counts become its event). Returns how many events were added."""
        month, week = month_of(now), week_of(now)
        props = None
        if self.data["month"] != month:
            try:
                props = {**self.facts(), "month": month}  # may wait for the app's GPU check, so not under the lock
            except Exception:  # noqa: BLE001 — the month is counted at the next look
                props = None
        base = {"systemProps": system(self.version())}
        made, changed = [], False
        with self.lock:
            if self.data["install"] is None:
                made.append({**base, "eventName": "install", "props": {}})
                self.data["install"] = utc(now).date().isoformat()
            if self.data["month"] != month and props is not None:
                props["install_age"] = age_range(self.data["install"], month)
                made.append({**base, "eventName": "active_month", "props": props})
                self.data["month"] = month
            for w in [w for w in sorted(self.data["weeks"]) if w < week]:
                counts, changed = self.data["weeks"].pop(w), True
                if any(counts.get(k) for k in COUNTS):
                    made.append({**base, "eventName": "week_summary", "props": week_props(w, counts)})
            self.data["queue"] = (self.data["queue"] + made)[-KEEP:]
            if made or changed:
                self.save()
        return len(made)

    def tick(self, now=None):
        """One look at the clock: the assistants' log is read, what is due is queued and what waits is sent. True if
        a request was made."""
        now = self.clock() if now is None else now
        if not self.allowed():
            if not test_run():
                self.forget()  # turned off (or opted out) meanwhile: nothing is kept
            return False
        try:
            self.read_assistants()
        except Exception:  # noqa: BLE001 — the log is read again at the next look
            pass
        added = self.due(now)
        with self.lock:
            waiting = list(self.data["queue"])
        if not waiting or (self.failed is not None and now - self.failed < RETRY and not added):
            return False
        self.flush(waiting, now)
        return True

    def flush(self, waiting, now):
        """Send the waiting events, 25 to a request, until one can't be sent. Each gets its time now, as it is sent,
        and a new session id for the request."""
        with self.send_lock:
            url, agent = self.url(), self.agent()
            for i in range(0, len(waiting), BATCH):
                batch = waiting[i:i + BATCH]
                sent = {"timestamp": stamp(now), "sessionId": session_id(now)}
                out = [e for e in (checked({**e, **sent}) for e in batch) if e is not None]
                result = outcome(*post(url, self.key, agent, out)) if out else "dropped"
                with self.lock:
                    if result == "later":
                        self.failed = now
                        return
                    self.failed = None
                    self.data["queue"] = [e for e in self.data["queue"] if not any(e is b for b in batch)]
                    self.save()

    def agent(self):
        """The User-Agent: "{osName}/{osVersion} Sedjem/{version} {locale}". Aptabase hashes it with the IP address
        into the day's pseudonymous user (and keeps neither)."""
        s = system(self.version())
        text = f"{s['osName']}/{s['osVersion']} Sedjem/{s['appVersion']} {s['locale']}".strip()
        return re.sub(r"[^\x20-\x7e]", "?", text)

    # ---- in the background -----------------------------------------------------------------------
    def start(self):
        """Count this start of the app, and count and send from now on. The app's entry points call this
        (python -m app, app.desktop); servers made in the tests never send by themselves, and a copy without a key
        starts nothing."""
        self.count(starts=1)
        if self.thread is None and self.url() is not None:
            self.thread = threading.Thread(target=self.loop, daemon=True, name="sedjem-usage")
            self.thread.start()

    def loop(self):
        self.wake.wait(START_DELAY)
        while True:
            self.wake.clear()
            try:
                self.tick()
            except Exception:  # noqa: BLE001, S110 — the counts never take the app down
                pass
            self.wake.wait(WAKE)
