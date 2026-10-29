"""Anonymous usage counts (Settings → About; docs/13-usage-counts.md): how many installs use Sedjem and in which
countries, on which versions and systems, and what they use. They go to Aptabase (aptabase.com) as three events,
about six a month for an install that is used every week:

  install       once for each data folder, the first time the counts are allowed
  active_month  once a calendar month, the first time Sedjem runs that month: the kind of install, the window, and
                the computer in broad ranges (the graphics card's maker, the memory, the processor threads)
  week_summary  once for each week that had any use, the first time Sedjem runs after it ended: how many
                transcriptions and minutes of audio, by model, with speaker labels and from YouTube, the compares,
                the failures and the exports of each format

Months and weeks are UTC, the weeks ISO's (2026-W44). Only the ends of runs, the exports, the compare view and
YouTube jobs are counted. Nothing else is sent: no transcript, audio, file name or path, link, title, speaker name,
key, host or user name, no identifier, no error message and no one run's length. Every event passes checked() on
its way out, which lets through only these events with these fields. <storage>/usage.json keeps the flags (when the
install was counted, the month counted last), the counts of the weeks not sent yet and the events waiting.

The counts are allowed when the switch is on (usage_counts: on by default, but nothing is counted or sent before
the first-start dialog has said so), app/config.toml has an app key ([usage] app_key), DO_NOT_TRACK and
SEDJEM_NO_USAGE aren't set, and the tests aren't running. Turning the switch off deletes the counts kept and the
events waiting.

Sending: a thread posts the waiting events to Aptabase's batch endpoint with urllib, soon after start and whenever
something is due (it looks every 15 minutes). An event Aptabase refuses (400, or 200 with an error for a key it
doesn't take) is dropped; one that couldn't be sent (no connection, 408, 429, 5xx) is tried again an hour later,
until it is a day old, which Aptabase no longer takes. It never raises, never blocks the app and prints nothing.
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
MAX_AGE = 24 * 3600   # Aptabase takes no event older than this
BATCH = 25            # events in one request, at most

# What may be sent. week_summary has these numbers and the week ("2026-W44"); locally the audio is kept in seconds
# and sent in whole minutes. active_month has these props, each one of its values. install has none.
ENGINES = ("cohere", "whisper_medium", "whisper_large", "other_local", "hosted")
MODELS = {"cohere": "cohere", "whisper-medium": "whisper_medium", "large-v3": "whisper_large"}  # the built-in local ones
FORMATS = ("txt", "srt", "vtt", "md", "json")
SUMMARY = ("transcriptions", "minutes", *ENGINES, "speakers", "nemotron", "youtube", "compares", "failed", *FORMATS)
COUNTS = tuple("seconds" if k == "minutes" else k for k in SUMMARY)
VALUES = {"kind": ("installer", "linux-build", "source", "other"), "ui": ("desktop", "browser"),
          "gpu": ("none", "intel", "amd", "nvidia", "other"), "ram": ("under 8", "8-16", "16-32", "over 32"),
          "cores": ("under 4", "4-8", "8-16", "over 16")}
# systemProps, with the most characters Aptabase takes for each (None: a boolean)
SYSTEM = {"isDebug": None, "osName": 30, "osVersion": 100, "locale": 10, "appVersion": 50, "engineName": 30,
          "engineVersion": 30, "sdkVersion": 40}
WEEK = re.compile(r"\d{4}-W\d{2}")
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


def session_id(t):
    """Aptabase's session id: the seconds since 1970 and eight random digits. A new one each time events are made."""
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
    0.3.0-preview.20261021)."""
    os_name, os_version = system_name()
    return {"isDebug": os.environ.get("SEDJEM_USAGE_DEBUG") == "1", "osName": clip(os_name, 30),
            "osVersion": clip(os_version, 100), "locale": system_locale(), "appVersion": clip(version, 50),
            "engineName": "python", "engineVersion": clip(platform.python_version(), 30), "sdkVersion": SDK}


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


def ram_range(gb):
    """The memory as a range. The system reports a little less than the computer was sold with (some is kept for
    the graphics), so a computer with 8 or 16 GB counts as 8-16 and one with 24 or 32 GB as 16-32."""
    if not isinstance(gb, (int, float)) or gb <= 0:
        return "under 8"
    return "under 8" if gb < 6.5 else "8-16" if gb < 16.5 else "16-32" if gb < 32.5 else "over 32"


def cores_range(n):
    """The processor threads as a range: 4 to 8 counts as 4-8, 9 to 16 as 8-16."""
    n = n if isinstance(n, int) and n > 0 else 1
    return "under 4" if n < 4 else "4-8" if n <= 8 else "8-16" if n <= 16 else "over 16"


def facts(kind, desktop, gpu, machine=None):
    """active_month's props. kind: app/updates.py's; desktop: started as the desktop app; gpu: the app's GPU check."""
    machine = machine if isinstance(machine, dict) else sysinfo.machine()
    return {"kind": install_kind(kind), "ui": "desktop" if desktop else "browser", "gpu": gpu_kind(gpu),
            "ram": ram_range(machine.get("ram_gb")), "cores": cores_range(machine.get("threads"))}


def engine_of(job):
    if job.get("kind") == "hosted":
        return "hosted"
    return MODELS.get(job.get("model"), "other_local")


def week_props(week, counts):
    """week_summary's props from a week's counts: whole numbers, the audio in minutes, and the week."""
    props = {k: int(counts.get(k) or 0) for k in SUMMARY if k != "minutes"}
    props["minutes"] = round(float(counts.get("seconds") or 0) / 60)
    return {**{k: props[k] for k in SUMMARY}, "week": week}


def checked(event):
    """The event with only what may be sent, or None when it isn't one of the three events as this module makes
    them (one changed in usage.json, say)."""
    try:
        name, props, system_props = event["eventName"], event["props"], event["systemProps"]
        if name == "install":
            ok = props == {}
        elif name == "active_month":
            ok = props.keys() == VALUES.keys() and all(props[k] in VALUES[k] for k in VALUES)
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


def made_at(event):
    try:
        return datetime.fromisoformat(event["timestamp"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return 0.0


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

    version() is the version Settings → About shows; facts() gives active_month's props (the app waits for its GPU
    check first, in this module's thread)."""

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
        """usage.json, read with care: {"install": the day it was counted, "month": the month counted last, "weeks":
        {week: counts}, "queue": the events waiting}."""
        data = _read_json(self.path)
        weeks = data.get("weeks") if isinstance(data.get("weeks"), dict) else {}
        return {"install": data["install"] if isinstance(data.get("install"), str) else None,
                "month": data["month"] if isinstance(data.get("month"), str) else None,
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
        """Delete the counts kept and the events waiting. The flags stay, so turning the counts on again doesn't
        count the install, or a month, twice."""
        with self.lock:
            if self.data["weeks"] or self.data["queue"]:
                self.data["weeks"], self.data["queue"] = {}, []
                self.save()

    # ---- counting ------------------------------------------------------------------------------
    def count(self, now=None, **add):
        """Add to this week's counts, while the counts are allowed. Never raises."""
        try:
            if not add or not self.allowed():
                return
            week = week_of(self.clock() if now is None else now)
            with self.lock:
                counts = self.data["weeks"].setdefault(week, {})
                for k, v in add.items():
                    if k in COUNTS:
                        counts[k] = (counts.get(k) or 0) + v
                self.save()
        except Exception:  # noqa: BLE001 — counting never gets in the way
            pass

    def job_ended(self, job, lines=None):
        """A job finished or failed (app/jobs.py). A model run counts as a transcription, with its audio length, its
        model and whether it has speaker labels (and from Nemotron); captions made by people only as a YouTube job."""
        if not isinstance(job, dict) or job.get("status") not in ("done", "failed"):
            return
        if job["status"] == "failed":
            self.count(failed=1)
            return
        add = {}
        if job.get("kind") in ("local", "hosted"):
            add.update({"transcriptions": 1, "seconds": max(0.0, float(job.get("audio_s") or 0)), engine_of(job): 1})
            if any(isinstance(x, dict) and x.get("speaker") is not None for x in lines or []):
                add["speakers"] = 1
            if "nemotron" in str(job.get("voiceprint_model") or "").lower():
                add["nemotron"] = 1
        if job.get("youtube_id"):
            add["youtube"] = 1
        self.count(**add)

    def exported(self, fmt):
        if fmt in FORMATS:
            self.count(**{fmt: 1})

    def compared(self):
        self.count(compares=1)

    # ---- sending -------------------------------------------------------------------------------
    def due(self, now):
        """Queue what is due now: the install, this month, and each week before this one that has counts (the
        week's counts become its event). Returns how many events were added."""
        month, week = month_of(now), week_of(now)
        props = None
        if self.data["month"] != month:
            try:
                props = self.facts()  # may wait for the app's GPU check, so not under the lock
            except Exception:  # noqa: BLE001 — the month is counted at the next look
                props = None
        base = {"timestamp": stamp(now), "sessionId": session_id(now), "systemProps": system(self.version())}
        made = []
        with self.lock:
            if self.data["install"] is None:
                made.append({**base, "eventName": "install", "props": {}})
                self.data["install"] = utc(now).date().isoformat()
            if self.data["month"] != month and props is not None:
                made.append({**base, "eventName": "active_month", "props": props})
                self.data["month"] = month
            for w in [w for w in sorted(self.data["weeks"]) if w < week]:
                counts = self.data["weeks"].pop(w)
                if any(counts.get(k) for k in COUNTS):
                    made.append({**base, "eventName": "week_summary", "props": week_props(w, counts)})
                else:  # nothing to send for it, but the file changes
                    self.save()
            self.data["queue"].extend(made)
        return len(made)

    def tick(self, now=None):
        """One look at the clock: what is due is queued and what waits is sent. True if a request was made."""
        now = self.clock() if now is None else now
        if not self.allowed():
            if not test_run():
                self.forget()  # turned off (or opted out) meanwhile: nothing is kept
            return False
        added = self.due(now)
        with self.lock:
            old = [e for e in self.data["queue"] if now - made_at(e) > MAX_AGE]
            if old or added:
                self.data["queue"] = [e for e in self.data["queue"] if not any(e is o for o in old)]
                self.save()  # also the weeks taken out of the counts, and the flags
            waiting = list(self.data["queue"])
        if not waiting or (self.failed is not None and now - self.failed < RETRY and not added):
            return False
        self.flush(waiting, now)
        return True

    def flush(self, waiting, now):
        """Send the waiting events, 25 to a request, until one can't be sent."""
        with self.send_lock:
            url, agent = self.url(), self.agent()
            for i in range(0, len(waiting), BATCH):
                batch = waiting[i:i + BATCH]
                out = [e for e in (checked(e) for e in batch) if e is not None]
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
        """Count and send from now on. The app's entry points call this (python -m app, app.desktop); servers made
        in the tests never send by themselves, and a copy without a key starts nothing."""
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
