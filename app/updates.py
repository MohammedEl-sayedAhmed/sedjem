"""Updates: tell the user when a newer Sedjem is out, and update it when they ask (Settings → Updates;
docs/12-updates.md).

Two channels (the setting update_channel):
  stable  GitHub releases that aren't drafts or pre-releases: the newest version above the running one
  latest  a git checkout: the newest commit on main whose Tests workflow passed (on Windows and Linux), when
          the checkout is behind it; a desktop build: the newest release or nightly preview (a pre-release
          tagged vX.Y.Z-preview.YYYYMMDD) above the running version

What runs here: a desktop build reads app/_build.json, which desktop/build.py stamps into it (version, commit,
date, channel); from source the commit is read from the files in .git (git itself isn't needed for that).

A check asks GitHub's API without logging in, with If-None-Match against the answers kept in
<storage>/updates.json, so an answer that hasn't changed costs nothing. It runs in the background soon after
start and then once a day, only when the user agreed (update_check true; the app asks once), and whenever they
press Check now. It never raises: no network, the rate limit and answers that make no sense become the error
the state shows.

Update now runs only when the user asks, one at a time:
  source   a git checkout of main without local changes: git fetch origin main, then git merge --ff-only to
           the offered commit, once it is known to be on origin's main and a fast-forward. Requirement files
           that changed are installed first into the running environment (uv pip, else pip). Then the app
           starts itself again with the same command line and port, and this process quits.
  windows  the release's installer is downloaded to <storage>/updates/, checked against the release's
           SHA256SUMS.txt and run in its update mode, which installs over this copy and then starts it; this
           process quits
  linux    the release's archive is downloaded, checked, and unpacked beside the running build as
           Sedjem-<version>/, which opens in the file manager
Only this repository's release files are downloaded, over HTTPS (the tests' stand-in for the API, on
127.0.0.1, is the one exception).
"""
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
import urllib.parse
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from . import __version__
from .config import FROZEN, ROOT, _read_json, replace_file

REPO = "MohammedEl-sayedAhmed/sedjem"  # [updates] repo and api in app/config.toml; the tests point api at a stand-in
API = "https://api.github.com"
WORKFLOW = "tests.yml"                 # a commit on main is offered on Latest once this workflow passed for it
STAMP = Path(__file__).resolve().parent / "_build.json"  # written by desktop/build.py into the build only
SUMS = "SHA256SUMS.txt"
INSTALLER_FLAGS = ["/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/UPDATE=1"]
ACTIONS = {"source": "git", "windows": "installer", "linux": "archive", "other": "download"}
BUSY = ("downloading", "verifying", "installing", "restarting")
RESTART_ENV = "SEDJEM_RESTART"  # "<pid>:<port>", set for the process restart() starts

TIMEOUT = (5, 10)        # connect, read, for one request to the API
CHECK_TIME = 15          # a whole check stops after this long
START_DELAY = 10         # the background check: this long after start (start stays fast), then once a day
EVERY = 24 * 3600
RETRY = 3600             # after a background check that failed (offline, say)
WAKE = 900               # how often the background thread looks at the clock
RESTART_GRACE = 3.0      # the restarted app must still run after this long before the old one quits
MAX_CHANGES = 50
MAX_DOWNLOAD = 2 << 30   # bytes; the installer is about 80 MB and the Linux archive about 200 MB

OFFLINE = "offline"
RATE_LIMITED = "rate limited"
ODD = "GitHub sent an answer Sedjem doesn't understand"
LOOPBACK = ("127.0.0.1", "localhost", "::1")

SHA = re.compile(r"[0-9a-f]{40}")
VERSION = re.compile(r"v?(\d{1,6})\.(\d{1,6})\.(\d{1,6})(?:-preview\.(\d{8})(?:\.(\d{1,4}))?)?")
REQUIREMENTS = re.compile(r"requirements[\w.-]*\.txt")  # requirements.txt, requirements-desktop.txt, ... at the top
REPO_NAME = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
SEGMENT = re.compile(r"[A-Za-z0-9_.+-]{1,200}")


class CheckError(Exception):
    """Why a check failed, in the words the state shows: "offline", "rate limited" or a sentence."""


class RateLimited(CheckError):
    def __init__(self, reset):
        super().__init__(RATE_LIMITED)
        self.reset = reset  # when GitHub takes requests again (epoch seconds)


class NotFound(CheckError):
    pass


class Refused(Exception):
    """Update now can't start: the reason, in plain words."""


class InstallError(Exception):
    """Update now failed: the reason, in plain words."""


# ---------------------------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------------------------

def parse_version(text):
    """A version as a tuple that sorts the way releases and previews follow each other, or None: "0.3.0" is
    (0, 3, 0, 1, 0, 0) and "0.3.0-preview.20261021.2" is (0, 3, 0, 0, 20261021, 2), so 0.2.0 <
    0.3.0-preview.20261021 < 0.3.0-preview.20261021.2 < 0.3.0-preview.20261022 < 0.3.0. A tag's v is allowed."""
    m = VERSION.fullmatch(text.strip()) if isinstance(text, str) else None
    if not m:
        return None
    major, minor, patch, day, n = m.groups()
    return int(major), int(minor), int(patch), 0 if day else 1, int(day or 0), int(n or 0)


def newer(a, b):
    """True if version a is above version b; False when either isn't a version."""
    pa, pb = parse_version(a), parse_version(b)
    return pa is not None and pb is not None and pa > pb


def check_skip(value):
    """A value update_skip may hold: None, a version or a commit."""
    return value is None or isinstance(value, str) and (SHA.fullmatch(value) is not None or parse_version(value) is not None)


# ---------------------------------------------------------------------------------------------
# What runs here
# ---------------------------------------------------------------------------------------------

def git_dirs(root):
    """(the checkout's git folder, the folder its branches are kept in), or (None, None). They differ in a
    worktree: its .git is a file that names its own folder, and that folder's commondir names the shared one."""
    dot = Path(root) / ".git"
    try:
        if dot.is_dir():
            own = dot
        elif dot.is_file():
            text = dot.read_text(encoding="utf-8").strip()
            if not text.startswith("gitdir:"):
                return None, None
            own = Path(text[len("gitdir:"):].strip())
            own = own if own.is_absolute() else dot.parent / own
        else:
            return None, None
        common = own
        if (own / "commondir").is_file():
            shared = Path((own / "commondir").read_text(encoding="utf-8").strip())
            common = shared if shared.is_absolute() else own / shared
        return own, common
    except (OSError, ValueError):
        return None, None


def read_ref(own, common, ref, depth=0):
    """The commit a ref names: its file (the worktree's own folder first, then the shared one), else the line in
    packed-refs; None if neither has it."""
    if depth > 4 or not ref.startswith("refs/") or ".." in ref.split("/"):
        return None
    for folder in dict.fromkeys((own, common)):
        try:
            text = (folder / ref).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if SHA.fullmatch(text):
            return text
        return read_ref(own, common, text[4:].strip(), depth + 1) if text.startswith("ref:") else None
    try:
        lines = (common / "packed-refs").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:  # "<sha> <ref>"; "#" starts the header and "^" a tag's commit
        sha, _, name = line.strip().partition(" ")
        if name.strip() == ref and SHA.fullmatch(sha):
            return sha
    return None


def read_head(root):
    """(commit, branch) of a git checkout, read from the files in .git (git itself isn't needed). branch is None
    when HEAD isn't on a branch; (None, None) when there is no checkout or it can't be read this way."""
    own, common = git_dirs(root)
    if own is None:
        return None, None
    try:
        head = (own / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None, None
    if SHA.fullmatch(head):
        return head, None
    if not head.startswith("ref:"):
        return None, None
    ref = head[4:].strip()
    return read_ref(own, common, ref), ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else None


def run_git(root, *args, timeout=60):
    """git in the checkout, never asking for a password: the finished process, or None without git (or when it
    couldn't run in time)."""
    exe = shutil.which("git")
    if not exe:
        return None
    try:
        return subprocess.run([exe, "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout, stdin=subprocess.DEVNULL,
                              env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.SubprocessError):
        return None


def iso_date(s):
    """An ISO 8601 date as git (2.41 and later) writes one: Z for UTC, else the offset."""
    return re.sub(r"[+-]00:00$", "Z", s)


def commit_date(root, sha):
    """When a commit was made (ISO 8601), from its object in .git when it is kept loose, else from git; None if
    neither knows."""
    _, common = git_dirs(root)
    if common is not None:
        try:
            data = zlib.decompress((common / "objects" / sha[:2] / sha[2:]).read_bytes())
            for line in data.split(b"\n\n", 1)[0].split(b"\n"):
                if line.startswith(b"committer "):  # committer Name <email> 1697800000 +0300
                    seconds, offset = line.rsplit(b" ", 2)[-2:]
                    shift = timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5]))
                    zone = timezone(-shift if offset.startswith(b"-") else shift)
                    return iso_date(datetime.fromtimestamp(int(seconds), zone).isoformat())
        except (OSError, zlib.error, ValueError, OverflowError):
            pass
    p = run_git(root, "show", "-s", "--format=%cI", sha, timeout=10)
    return (iso_date(p.stdout.strip()) or None) if p is not None and p.returncode == 0 else None


def running(root=ROOT, frozen=FROZEN, stamp=STAMP, executable=None, platform=None):
    """What runs here: {"version", "commit", "date", "kind", "build_channel", "branch"}. kind is "source" (a git
    checkout), "windows" (a desktop build the installer put there: its uninstaller is next to it), "linux" (a
    desktop build on Linux) or "other". branch, a checkout's branch, is for Update now and not in the API."""
    platform = platform or sys.platform
    if frozen:
        data = _read_json(stamp)  # {} when the build has no stamp
        version = data["version"].strip().removeprefix("v") if parse_version(data.get("version")) else __version__
        commit = str(data.get("commit") or "").lower()
        exe = Path(executable or sys.executable)
        if platform == "win32":
            kind = "windows" if (exe.parent / "unins000.exe").is_file() else "other"
        else:
            kind = "linux" if platform.startswith("linux") else "other"
        return {"version": version, "commit": commit if SHA.fullmatch(commit) else None,
                "date": data["date"] if isinstance(data.get("date"), str) else None, "kind": kind,
                "build_channel": data["channel"] if data.get("channel") in ("stable", "preview") else None,
                "branch": None}
    checkout = (Path(root) / ".git").exists()
    commit, branch = read_head(root)
    if checkout and commit is None:  # stored in a way the files don't show (reftable): ask git
        p = run_git(root, "rev-parse", "HEAD")
        commit = p.stdout.strip() if p is not None and SHA.fullmatch(p.stdout.strip()) else None
        p = run_git(root, "symbolic-ref", "-q", "--short", "HEAD")
        branch = p.stdout.strip() or None if p is not None and p.returncode == 0 else None
    return {"version": __version__, "commit": commit, "date": commit_date(root, commit) if commit else None,
            "kind": "source" if checkout else "other", "build_channel": None, "branch": branch}


# ---------------------------------------------------------------------------------------------
# GitHub's API
# ---------------------------------------------------------------------------------------------

def api_ok(api):
    """The API's address must be HTTPS (plain HTTP only to this computer: the tests' stand-in)."""
    u = urllib.parse.urlsplit(api)
    return bool(u.hostname) and (u.scheme == "https" or u.scheme == "http" and u.hostname in LOOPBACK)


def secure(url):
    """Where a download ended up after GitHub's redirects: HTTPS (or this computer, in the tests)."""
    u = urllib.parse.urlsplit(url or "")
    return u.scheme == "https" or u.scheme == "http" and u.hostname in LOOPBACK


def reset_time(r):
    """When GitHub takes requests again after answering that the limit is reached (epoch seconds)."""
    now = time.time()
    try:
        if r.headers.get("Retry-After"):
            return now + max(1, min(3600, int(r.headers["Retry-After"])))
        if r.headers.get("X-RateLimit-Reset"):
            return min(now + 3600, max(now + 60, float(r.headers["X-RateLimit-Reset"])))
    except ValueError:
        pass
    return now + 600


class GitHub:
    """GitHub's REST API without a login. Answers are kept, cut down to what is used, with their ETag, and asked
    for again with If-None-Match: one that hasn't changed (304) doesn't count against the hourly limit."""

    def __init__(self, api, repo, cache=None, seconds=CHECK_TIME):
        self.api, self.repo = api.rstrip("/"), repo
        self.cache = dict(cache or {})
        self.used = set()
        self.deadline = time.monotonic() + seconds
        self.http = requests.Session()
        # GitHub asks every program to name itself; nothing else about the computer or the user is sent
        self.http.headers.update({"User-Agent": "Sedjem", "Accept": "application/vnd.github+json",
                                  "X-GitHub-Api-Version": "2022-11-28"})

    def close(self):
        self.http.close()

    def get(self, path, keep, accept=None, text=False):
        """keep(the answer) for GET <api><path>: JSON (or text) cut down to what is used."""
        url = self.api + path
        left = self.deadline - time.monotonic()
        if left < 1:  # out of time: as good as no connection
            raise CheckError(OFFLINE)
        old = self.cache.get(url)
        headers = {"Accept": accept} if accept else {}
        if isinstance(old, dict) and old.get("etag"):
            headers["If-None-Match"] = old["etag"]
        try:
            r = self.http.get(url, headers=headers, timeout=(min(TIMEOUT[0], left), min(TIMEOUT[1], left)))
        except requests.RequestException:
            raise CheckError(OFFLINE) from None
        self.used.add(url)
        if r.status_code == 304 and isinstance(old, dict) and "data" in old:
            return old["data"]
        if r.status_code == 429 or r.status_code == 403 and (r.headers.get("X-RateLimit-Remaining") == "0"
                                                             or "rate limit" in r.text.lower()):
            raise RateLimited(reset_time(r))
        if r.status_code == 404:
            raise NotFound(f"GitHub has nothing at {path}")
        if r.status_code != 200:
            raise CheckError(f"GitHub answered with an error (HTTP {r.status_code})")
        try:
            data = keep(r.text if text else r.json())
        except (ValueError, TypeError, KeyError, AttributeError, IndexError):
            raise CheckError(ODD) from None
        if r.headers.get("ETag"):
            self.cache[url] = {"etag": r.headers["ETag"], "data": data}
        else:
            self.cache.pop(url, None)
        return data


def first_line(text, limit=200):
    return str(text or "").strip().split("\n", 1)[0].strip()[:limit]


def keep_releases(data):
    """The releases, with what is used of each."""
    if not isinstance(data, list):
        raise ValueError("not a list of releases")
    out = []
    for r in data:
        if not isinstance(r, dict) or not isinstance(r.get("tag_name"), str):
            continue
        assets = r.get("assets") if isinstance(r.get("assets"), list) else []
        out.append({"tag": r["tag_name"], "draft": bool(r.get("draft")), "prerelease": bool(r.get("prerelease")),
                    "notes": str(r.get("body") or "")[:20000] or None, "url": str(r.get("html_url") or ""),
                    "date": r["published_at"] if isinstance(r.get("published_at"), str) else None,
                    "commit": str(r.get("target_commitish") or ""),
                    "assets": [{"name": str(a.get("name") or ""), "size": a["size"] if type(a.get("size")) is int else None,
                                "url": str(a.get("url") or ""), "download": str(a.get("browser_download_url") or "")}
                               for a in assets if isinstance(a, dict)]})
    return out


def keep_runs(data):
    """The workflow runs, with what is used of each."""
    runs = data["workflow_runs"]
    if not isinstance(runs, list):
        raise ValueError("not a list of runs")
    out = []
    for r in runs:
        if not isinstance(r, dict):
            continue
        repo = r.get("head_repository") if isinstance(r.get("head_repository"), dict) else {}
        head = r.get("head_commit") if isinstance(r.get("head_commit"), dict) else {}
        out.append({"sha": str(r.get("head_sha") or ""), "branch": r.get("head_branch"), "event": r.get("event"),
                    "status": r.get("status"), "conclusion": r.get("conclusion"), "repo": repo.get("full_name"),
                    "date": head["timestamp"] if isinstance(head.get("timestamp"), str) else None})
    return out


def keep_compare(data):
    """A comparison of two commits: how they relate, the new commits (newest first, at most MAX_CHANGES) and the
    requirement files the change touches."""
    if data.get("status") not in ("ahead", "behind", "identical", "diverged"):
        raise ValueError("no status")
    commits = []
    for c in data.get("commits") or []:
        if not isinstance(c, dict) or not SHA.fullmatch(str(c.get("sha") or "")):
            continue
        info = c.get("commit") if isinstance(c.get("commit"), dict) else {}
        who = next((x for x in (info.get("committer"), info.get("author")) if isinstance(x, dict)), {})
        commits.append({"sha": c["sha"], "title": first_line(info.get("message")) or c["sha"][:7],
                        "date": who["date"] if isinstance(who.get("date"), str) else None,
                        "url": link(c.get("html_url"), "")})
    files = [f["filename"] for f in data.get("files") or [] if isinstance(f, dict) and isinstance(f.get("filename"), str)]
    return {"status": data["status"], "ahead": int(data.get("ahead_by") or 0), "behind": int(data.get("behind_by") or 0),
            "commits": commits[::-1][:MAX_CHANGES], "url": str(data.get("html_url") or ""),
            "requirements": sorted({f for f in files if REQUIREMENTS.fullmatch(f)})}


def keep_sha(text):
    sha = text.strip().lower()
    if not SHA.fullmatch(sha):
        raise ValueError("not a commit")
    return sha


def link(url, fallback):
    """A page on github.com to show (the app opens only these), else the fallback."""
    return url if isinstance(url, str) and url.startswith("https://github.com/") else fallback


def changes_title(n):
    return f"{n} new change{'' if n == 1 else 's'}"


def parse_sums(text):
    """{file name: SHA-256} from a SHA256SUMS.txt as sha256sum writes it ("<hash>  <name>" or "<hash> *<name>")."""
    out = {}
    for line in text.splitlines():
        m = re.fullmatch(r"([0-9a-fA-F]{64}) [ *]?(.+)", line.strip())
        if m:
            out[m[2].strip().removeprefix("./")] = m[1].lower()
    return out


# ---------------------------------------------------------------------------------------------
# Starting things that outlive this process
# ---------------------------------------------------------------------------------------------

def spawn(cmd, **kw):
    """Start a program that keeps running after this process quits: the restarted app, or the installer."""
    if os.name == "nt":
        kw.setdefault("creationflags", subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    else:
        kw.setdefault("start_new_session", True)
    return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, close_fds=True, **kw)


def restart_command():
    """The command line that started this process, to start it again: the same interpreter and options, and
    -m app or -m app.desktop with the arguments it was given."""
    return [sys.executable, *sys.orig_argv[1:]]


def port_free(port):
    with socket.socket() as s:
        if os.name != "nt":  # as the server binds it: connections the old process left behind don't block it
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def restarted(wait=30.0):
    """In the process restart() started: the port the app had, once the process before has let it go (it closes
    its window and stops the server first); None for an ordinary start. The mark is taken out of the
    environment, so programs this process starts don't inherit it."""
    m = re.fullmatch(r"(\d{1,10}):(\d{1,5})", os.environ.pop(RESTART_ENV, ""))
    if not m or not 0 < int(m[2]) < 65536:
        return None
    port = int(m[2])
    deadline = time.monotonic() + wait
    while not port_free(port) and time.monotonic() < deadline:
        time.sleep(0.2)
    return port


def run_installer(cmd, cwd):
    """Install requirement files with uv or pip (the tests replace this)."""
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=1800, stdin=subprocess.DEVNULL,
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def requirement_names(text):
    """The packages a requirements file names (options, comments and blank lines skipped)."""
    names = []
    for line in text.splitlines():
        line = re.split(r"(?:^|\s)#", line, maxsplit=1)[0].strip()
        m = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", line) if line and not line.startswith("-") else None
        if m:
            names.append(m.group(0))
    return names


def installed(name):
    try:
        importlib.metadata.distribution(name)
        return True
    except (importlib.metadata.PackageNotFoundError, ValueError):
        return False


def unpack(archive, dest):
    """Unpack a .tar.gz build into dest, a new folder; the archive's one top folder (Sedjem/) becomes dest. Nothing
    may land outside it: absolute names, .. and links that lead out are refused, and so are device files."""
    def safe(member, path):
        name = member.name.replace("\\", "/")
        if name.startswith("/") or re.match(r"[A-Za-z]:", name) or ".." in name.split("/"):
            raise InstallError(f"The archive holds a file outside its folder ({member.name}), so it wasn't unpacked.")
        return tarfile.data_filter(member, path)

    part = dest.with_name(f".{dest.name}.part")
    shutil.rmtree(part, ignore_errors=True)
    part.mkdir(parents=True)
    try:
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(part, filter=safe)
        entries = list(part.iterdir())
        top = entries[0] if len(entries) == 1 and entries[0].is_dir() and not entries[0].is_symlink() else part
        os.replace(top, dest)
    except tarfile.TarError as e:
        raise InstallError(f"The archive couldn't be unpacked safely ({e}).") from None
    finally:
        shutil.rmtree(part, ignore_errors=True)


def now_iso(t=None):
    return datetime.fromtimestamp(time.time() if t is None else t).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------------------------
# The app's updates
# ---------------------------------------------------------------------------------------------

class Updates:
    """The state Settings → Updates shows (GET /api/updates), the checks, and Update now.

    quit(restarting) quits the app the way Settings → Quit does; show_folder(path) opens a folder in the file
    manager; port() is the port the server listens on. The other arguments describe what runs here, so the tests
    can stand in for a desktop build or a checkout of their own."""

    def __init__(self, cfg, quit=None, show_folder=None, port=None, root=ROOT, frozen=FROZEN, stamp=STAMP,
                 executable=None, platform=None):
        self.cfg = cfg
        self.quit = quit or (lambda restarting=False: None)
        self.show_folder = show_folder or (lambda folder: None)
        self.port = port or (lambda: None)
        self.root, self.frozen, self.stamp = Path(root), frozen, stamp
        self.executable = Path(executable or sys.executable)
        self.platform = platform or sys.platform
        conf = cfg.data.get("updates") if isinstance(cfg.data.get("updates"), dict) else {}
        self.repo = str(conf.get("repo") or REPO)
        self.api = str(conf.get("api") or API).rstrip("/")
        self.path = cfg.storage / "updates.json"
        self.folder = cfg.storage / "updates"  # downloads
        self.lock = threading.RLock()
        self.check_lock = threading.Lock()
        self.wake = threading.Event()
        self.thread = None
        self.checking = False
        self.checked_here = False  # a check ran since this process started
        self.install = None        # Update now's progress; in memory only, so a fresh start reports none
        self._running = None
        self.data = _read_json(self.path)  # {"result", "attempted", "rate_reset", "cache"}

    # ---- state -------------------------------------------------------------------------------------
    def running(self):
        """What runs here, read once: the code in memory is the code this process started with."""
        if self._running is None:
            self._running = running(self.root, self.frozen, self.stamp, self.executable, self.platform)
        return self._running

    def settings(self, s=None):
        """(update_check, update_channel, update_skip), read with care: settings.json may have been changed by hand."""
        s = s or self.cfg.settings()
        enabled = s.get("update_check") if s.get("update_check") in (True, False) else None
        channel = s.get("update_channel") if s.get("update_channel") in ("stable", "latest") else "stable"
        skip = s.get("update_skip") if isinstance(s.get("update_skip"), str) else None
        return enabled, channel, skip

    def result(self, channel):
        """The last check's result when it was made on this channel for what runs now, else {} (a check on the other
        channel, or before an update)."""
        run = self.running()
        res = self.data.get("result")
        return res if isinstance(res, dict) and res.get("for") == [channel, run["version"], run["commit"]] else {}

    def state(self, refresh=True):
        """GET /api/updates. refresh: look again at what could block Update now in a checkout (local changes...)."""
        enabled, channel, skip = self.settings()
        run = self.running()
        with self.lock:
            res = self.result(channel)
            available = dict(res["available"]) if isinstance(res.get("available"), dict) else None
            target = res.get("target") or {}
            install = dict(self.install) if self.install else None
        if available:
            available["skipped"] = skip is not None and skip == target.get("key")
            if refresh and available["action"] == "git" and not (install and install["state"] in BUSY):
                available["blocked"], available["commands"] = self.source_blockers(target)
        return {"enabled": enabled, "channel": channel,
                "running": {k: run[k] for k in ("version", "commit", "date", "kind", "build_channel")},
                "checked": res.get("checked"), "checking": self.checking, "error": res.get("error"),
                "found": res.get("found"), "note": res.get("note"), "available": available, "install": install}

    def summary(self, settings=None):
        """For GET /api/status: whether to ask about checking (first start), and the top bar's pill. A skipped
        version or commit has no pill."""
        enabled, channel, skip = self.settings(settings)
        res = self.result(channel)
        target = res.get("target") or {}
        show = bool(res.get("available")) and not (skip is not None and skip == target.get("key"))
        return {"ask": enabled is None, "available": show, "label": target.get("label") if show else None}

    def save(self):
        try:
            self.cfg.storage.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            replace_file(tmp, self.path)
        except OSError:
            pass  # the state still works from memory

    # ---- checking ---------------------------------------------------------------------------------
    def check(self):
        """Ask GitHub what the channel offers now (Check now, and the background check). One check at a time: a
        caller that finds one running waits for it and takes its result. Never raises."""
        before = self.data.get("attempted")
        if not self.check_lock.acquire(timeout=CHECK_TIME + 15):
            return
        try:
            if self.data.get("attempted") != before:  # one finished while this one waited
                return
            self.checking = True
            self._check()
        finally:
            self.checking = False
            self.checked_here = True
            self.check_lock.release()

    def _check(self):
        _, channel, _ = self.settings()
        now = time.time()
        found = available = target = note = error = None
        gh = None
        reset = self.data.get("rate_reset")
        try:
            run = self.running()
            if not (REPO_NAME.fullmatch(self.repo) and api_ok(self.api)):
                raise CheckError("the [updates] repo or api in config.toml can't be used")
            if isinstance(reset, (int, float)) and now < reset:
                raise RateLimited(reset)
            gh = GitHub(self.api, self.repo, self.data.get("cache") if isinstance(self.data.get("cache"), dict) else {})
            found, available, target, note = self.find(gh, run, channel)
        except RateLimited as e:
            error, reset = str(e), e.reset
        except CheckError as e:
            error = str(e)
        except Exception as e:  # noqa: BLE001 — a check never takes the app down
            error = f"{type(e).__name__}: {e}"
        finally:
            if gh is not None:
                gh.close()
        with self.lock:
            run = self.running()
            mark = [channel, run["version"], run["commit"]]
            if error is None:
                self.data["result"] = {"for": mark, "checked": now_iso(now), "error": None, "found": found,
                                       "note": note, "available": available, "target": target}
            else:  # what the last check that worked found stays
                self.data["result"] = {**self.result(channel), "for": mark, "error": error}
            self.data["attempted"] = now
            self.data["rate_reset"] = reset if error == RATE_LIMITED else None
            if gh is not None:  # the answers this check used, for If-None-Match next time
                self.data["cache"] = gh.cache if error else {u: gh.cache[u] for u in gh.used if u in gh.cache}
            self.save()

    def find(self, gh, run, channel):
        """What the channel offers what runs here: (found, available, target, note). found is "update", "up to
        date", "ahead" or "diverged" (the last two for a checkout, with a note that says so). target is what
        Update now and skipping need, kept out of the API's answer."""
        if run["kind"] != "source":
            release = self.newest_release(gh, run["version"], previews=channel == "latest")
            return self.offer_release(gh, run, channel, release) if release else ("up to date", None, None, None)
        if channel == "latest":
            path = (f"/repos/{self.repo}/actions/workflows/{WORKFLOW}/runs"
                    "?branch=main&event=push&status=success&per_page=1")
            tested = next((r for r in gh.get(path, keep_runs) if self.tested(r)), None)
            if tested is None:
                return "up to date", None, None, None
            return self.offer_commit(gh, run, channel, tested["sha"], None)
        release = self.newest_release(gh, run["version"], previews=False)
        if release is None:
            return "up to date", None, None, None
        sha = release["commit"] if SHA.fullmatch(release["commit"]) else gh.get(
            f"/repos/{self.repo}/commits/{urllib.parse.quote(release['tag'], safe='')}", keep_sha,
            accept="application/vnd.github.sha", text=True)  # a release's target is "main"; its tag has the commit
        return self.offer_commit(gh, run, channel, sha, release)

    def tested(self, r):
        """A Tests run that passed for a push to main of this repository (a pull request from a fork whose branch
        is also called main doesn't count)."""
        return bool(SHA.fullmatch(r["sha"])) and r["branch"] == "main" and r["event"] == "push" \
            and r["status"] == "completed" and r["conclusion"] == "success" \
            and (r["repo"] is None or str(r["repo"]).lower() == self.repo.lower())

    def newest_release(self, gh, current, previews):
        """The newest release above the running version: stable releases, and with previews also the nightly
        preview builds (pre-releases tagged vX.Y.Z-preview.YYYYMMDD). Drafts and other pre-releases never."""
        best = None
        for r in gh.get(f"/repos/{self.repo}/releases?per_page=30", keep_releases):
            p = parse_version(r["tag"])
            if r["draft"] or p is None or r["prerelease"] != (p[3] == 0) or r["prerelease"] and not previews:
                continue
            if newer(r["tag"], current) and (best is None or p > parse_version(best["tag"])):
                best = r
        return best

    def offer_commit(self, gh, run, channel, sha, release):
        """A git checkout and the commit the channel offers (the newest tested one on main, or a release's)."""
        if run["commit"] is None:
            raise CheckError("couldn't read this checkout's commit from .git")
        if run["commit"] == sha:
            return "up to date", None, None, None
        try:
            cmp = gh.get(f"/repos/{self.repo}/compare/{run['commit']}...{sha}", keep_compare)
        except NotFound:
            return ("diverged", None, None, "This checkout is on a commit that isn't on GitHub, so it can't be "
                    "updated from there. Update it by hand.")
        if cmp["status"] == "identical":
            return "up to date", None, None, None
        offered = f"Sedjem {release['tag'].removeprefix('v')}" if release else "the newest tested commit on main"
        if cmp["status"] == "behind":
            return "ahead", None, None, f"This checkout has everything in {offered}, and newer commits."
        if cmp["status"] == "diverged":
            return ("diverged", None, None, f"This checkout and {offered} have each moved on separately, so it "
                    "can't be updated by fast-forwarding. Update it by hand.")
        n, commits = cmp["ahead"], cmp["commits"]
        compare = link(cmp["url"], f"https://github.com/{self.repo}/compare/{run['commit']}...{sha}")
        if release:
            version = release["tag"].removeprefix("v")
            available = {"channel": channel, "version": version, "commit": sha, "date": release["date"],
                         "title": f"Sedjem {version}", "notes": release["notes"], "changes": commits, "ahead": n,
                         "url": link(release["url"], compare)}
            key, label = version, f"Update available · {version}"
        else:
            available = {"channel": channel, "version": None, "commit": sha,
                         "date": commits[0]["date"] if commits else None, "title": changes_title(n), "notes": None,
                         "changes": commits, "ahead": n, "url": compare}
            key, label = sha, changes_title(n)
        target = {"key": key, "label": label, "commit": sha, "requirements": cmp["requirements"]}
        available.update(skipped=False, action="git")
        available["blocked"], available["commands"] = self.source_blockers(target)
        return "update", available, target, None

    def offer_release(self, gh, run, channel, release):
        """A desktop build (or a copy without git) and the release the channel offers. On Latest the commits since
        the build's own are listed, when the build knows its commit."""
        version = release["tag"].removeprefix("v")
        commit = release["commit"] if SHA.fullmatch(release["commit"]) else None
        changes, n = [], 0
        if channel == "latest" and run["commit"]:
            head = commit or urllib.parse.quote(release["tag"], safe="")
            try:
                cmp = gh.get(f"/repos/{self.repo}/compare/{run['commit']}...{head}", keep_compare)
                if cmp["status"] == "ahead":
                    changes, n = cmp["commits"], cmp["ahead"]
            except NotFound:  # the build's commit isn't on GitHub: no list, the version is enough
                pass
        title = changes_title(n) if n else f"Sedjem {version}"
        page = f"https://github.com/{self.repo}/releases/tag/{urllib.parse.quote(release['tag'])}"
        available = {"channel": channel, "version": version, "commit": commit, "date": release["date"], "title": title,
                     "notes": release["notes"], "changes": changes, "ahead": n, "url": link(release["url"], page),
                     "skipped": False, "action": ACTIONS[run["kind"]], "blocked": None, "commands": []}
        target = {"key": version, "label": title if n else f"Update available · {version}", "commit": commit,
                  "assets": release["assets"]}
        available["blocked"] = self.release_blocker(run["kind"], version, release["assets"])
        return "update", available, target, None

    def release_blocker(self, kind, version, assets):
        """Why Update now can't install this release on this copy, or None."""
        if kind == "other":
            return "Sedjem can't update this copy by itself. Download the new version from its release page."
        names = {a["name"]: a for a in assets}
        name = asset_name(kind, version)
        if name not in names:
            return f"This release has no {name}."
        if SUMS not in names:
            return f"This release has no {SUMS} to check the download against."
        if not (self.asset_url(names[name]) and self.asset_url(names[SUMS])):
            return "This release's files aren't where this repository keeps its releases, so Sedjem won't download them."
        if kind == "linux":
            parent = self.executable.resolve().parent.parent
            if not os.access(parent, os.W_OK):
                return f"Sedjem can't write to {parent}, where the new version would be unpacked."
        return None

    def source_blockers(self, target):
        """(why Update now can't run on this checkout, or None; the commands to update it by hand when it can't),
        looked at afresh: the checkout may have changed since the check."""
        sha = target.get("commit")
        problem, stash, branch = None, False, read_head(self.root)[1]
        if not shutil.which("git"):
            problem = "Git isn't installed, or isn't on the PATH, so Sedjem can't update this checkout."
        else:
            if branch != "main":
                problem = (f"This checkout is on the branch {branch}, not main." if branch
                           else "This checkout isn't on a branch (HEAD is detached), so it isn't on main.")
            p = run_git(self.root, "--no-optional-locks", "status", "--porcelain", "--untracked-files=no", timeout=30)
            if p is None or p.returncode != 0:
                problem = problem or f"git status didn't work here: {first_line(p.stderr if p else '') or 'no answer'}"
            elif p.stdout.strip():
                stash = True
                problem = problem or "This checkout has local changes. Commit or stash them first."
            p = run_git(self.root, "remote", "get-url", "origin")
            if (p is None or p.returncode != 0) and not problem:
                problem = "This checkout has no remote called origin to fetch from."
        files = [f for f in target.get("requirements") or [] if self.applies(f)]
        tool = self.installer()
        if files and tool is None and not problem:
            problem = "The new version needs other packages, and neither uv nor pip is here to install them."
        return problem, self.manual_commands(sha, stash, branch != "main", files, tool) if problem else []

    def manual_commands(self, sha, stash, switch, files, tool):
        """The commands that update the checkout by hand, from its folder."""
        python = sys.executable
        commands = [f'cd "{self.root}"']
        if stash:
            commands.append('git stash push -m "Before updating Sedjem"')
        if switch:
            commands.append("git switch main")
        commands += ["git fetch origin main", f"git merge --ff-only {sha}"]
        for f in files:
            commands.append(f'uv pip install -p "{python}" -r {f}' if tool and tool[0] == "uv"
                            else f'"{python}" -m pip install -r {f}')
        if stash:
            commands.append("git stash pop")
        return commands

    def applies(self, name):
        """Whether a changed requirement file is one this environment was set up with: requirements.txt always,
        another (desktop, dev) when a package it names is installed here."""
        if name == "requirements.txt":
            return True
        try:
            text = (self.root / name).read_text(encoding="utf-8")
        except OSError:  # a new file: this environment wasn't set up with it
            return False
        return any(installed(n) for n in requirement_names(text))

    def installer(self):
        """("uv" or "pip", the command that installs a requirements file into the running environment without the
        file), or None: uv when it is there (an environment made with uv has no pip), else pip."""
        uv = shutil.which("uv")
        if uv:
            return "uv", [uv, "pip", "install", "-p", sys.executable, "-r"]
        if importlib.util.find_spec("pip") is not None:
            return "pip", [sys.executable, "-m", "pip", "install", "-r"]
        return None

    # ---- in the background ---------------------------------------------------------------------------
    def start(self):
        """Check in the background from now on: soon after start, then once a day, while update_check is true.
        The app's entry points call this (python -m app, app.desktop); servers made in the tests never check by
        themselves."""
        if self.thread is None:
            self.thread = threading.Thread(target=self.loop, daemon=True, name="sedjem-updates")
            self.thread.start()

    def loop(self):
        self.wake.wait(START_DELAY)
        self.clean()
        while True:
            self.wake.clear()
            try:
                self.tick()
            except Exception:  # noqa: BLE001, S110 — the background check never takes the app down
                pass
            self.wake.wait(WAKE)

    def tick(self, now=None):
        """One look at the clock: check when the user allows it and one is due (the first since start, then a day
        after the last, or an hour after one that failed). True if it checked."""
        now = time.time() if now is None else now
        enabled, channel, _ = self.settings()
        if enabled is not True:
            return False
        reset = self.data.get("rate_reset")
        if isinstance(reset, (int, float)) and now < reset:
            return False
        if self.checked_here:
            last = self.data.get("attempted") if isinstance(self.data.get("attempted"), (int, float)) else 0
            if now - last < (RETRY if self.result(channel).get("error") else EVERY):
                return False
        self.check()
        return True

    def settings_changed(self, before, after):
        """After the settings changed: switching checks on, or to the other channel, checks soon (when the
        background thread runs)."""
        was, after_enabled = self.settings(before), self.settings(after)
        if after_enabled[0] is True and (was[0] is not True or was[1] != after_enabled[1]):
            self.checked_here = False
            self.wake.set()

    def clean(self):
        """Delete what earlier updates downloaded (an installer that already ran, say)."""
        if self.install is None and self.folder.is_dir():
            shutil.rmtree(self.folder, ignore_errors=True)

    def skip(self, on):
        """Skip what is available now (it stays listed, marked skipped, but has no pill or notice), or stop skipping.
        The caller holds the settings lock."""
        if not on:
            self.cfg.save_settings(update_skip=None)
            return
        _, channel, _ = self.settings()
        res = self.result(channel)
        if not res.get("available"):
            raise Refused("There is nothing to skip. Check for updates first.")
        self.cfg.save_settings(update_skip=res["target"]["key"])

    # ---- Update now ------------------------------------------------------------------------------------
    def start_install(self, jobs_active=False):
        """Update now. What can be checked at once is checked here (Refused says why not); the work goes on in the
        background, with its progress in the state's "install"."""
        _, channel, _ = self.settings()
        with self.lock:
            res = self.result(channel)
            available, target = res.get("available"), res.get("target")
        if not available:
            raise Refused("There is no update to install. Check for updates first.")
        action = available["action"]
        if action == "download":
            raise Refused(available.get("blocked") or "Download the new version from its release page.")
        if action in ("git", "installer") and jobs_active:
            raise Refused("Sedjem restarts to update. Wait until the transcriptions that are running have finished, "
                          "or cancel them, then update.")
        if action == "git":
            blocked = self.source_blockers(target)[0]
        else:
            blocked = self.release_blocker(self.running()["kind"], available["version"], target.get("assets") or [])
        if blocked:
            raise Refused(blocked)
        with self.lock:
            if self.install and self.install["state"] in BUSY:
                raise Refused("An update is already running.")
            self.install = {"state": "downloading", "done": 0, "total": None, "error": None, "folder": None}
        work = {"git": self.update_source, "installer": self.update_windows, "archive": self.update_linux}[action]
        threading.Thread(target=self.run_install, args=(work, dict(available), dict(target)), daemon=True,
                         name="sedjem-update").start()

    def run_install(self, work, available, target):
        try:
            work(available, target)
        except Exception as e:  # noqa: BLE001 — shown in Settings → Updates
            message = str(e) if isinstance(e, InstallError) else f"{type(e).__name__}: {e}"
            self.progress(state="failed", error=message)

    def progress(self, **values):
        with self.lock:
            if self.install is not None:
                self.install.update(values)

    def git(self, *args, timeout=120):
        """Run git in the checkout for Update now: its output, or InstallError with what git said."""
        p = run_git(self.root, *args, timeout=timeout)
        if p is None:
            raise InstallError(f"git {args[0]} didn't run (is git installed, and did it finish in time?).")
        if p.returncode != 0:
            said = (p.stderr or p.stdout).strip().splitlines()
            raise InstallError(f"git {args[0]} failed: {said[-1] if said else f'exit code {p.returncode}'}")
        return p.stdout

    def is_ancestor(self, older, newer_commit):
        p = run_git(self.root, "merge-base", "--is-ancestor", older, newer_commit)
        if p is None or p.returncode not in (0, 1):
            raise InstallError("git merge-base didn't work here.")
        return p.returncode == 0

    def update_source(self, available, target):
        """git fetch, check, requirements, git merge --ff-only, restart."""
        sha = target["commit"]
        self.git("fetch", "origin", "main", timeout=600)
        p = run_git(self.root, "cat-file", "-e", f"{sha}^{{commit}}")
        if p is None or p.returncode != 0 or not self.is_ancestor(sha, "FETCH_HEAD"):
            raise InstallError("The offered commit isn't on main at origin, so Sedjem won't move this checkout to it.")
        if not self.is_ancestor("HEAD", sha):
            raise InstallError("This checkout has commits that the offered one doesn't have, so it can't be updated "
                               "by fast-forwarding. Update it by hand.")
        self.progress(state="installing")
        changed = [f.strip() for f in self.git("diff", "--name-only", "HEAD", sha).splitlines()]
        files = [f for f in changed if REQUIREMENTS.fullmatch(f) and self.applies(f)]
        if files:  # installed before the merge: if this fails, the checkout stays as it was
            tool = self.installer()
            if tool is None:
                raise InstallError("The new version needs other packages, and neither uv nor pip is here to install "
                                   "them. Install uv or pip, then update by hand.")
            folder = self.folder / "requirements"
            folder.mkdir(parents=True, exist_ok=True)
            for f in files:
                path = folder / f
                path.write_text(self.git("show", f"{sha}:{f}"), encoding="utf-8")
                p = run_installer([*tool[1], str(path)], cwd=str(self.root))
                if p.returncode != 0:
                    said = (p.stderr or p.stdout or "").strip().splitlines()
                    raise InstallError(f"Installing the packages in {f} failed: {said[-1] if said else p.returncode}")
        self.git("merge", "--ff-only", sha)
        self.progress(state="restarting")
        self.restart()

    def restart(self):
        """Start this program again with the same command line and port, then quit this one. The new process waits
        until this one has let the port go (restarted()), and writes what it prints to <storage>/sedjem.log. If it
        ends at once (the new code can't even be loaded), this one keeps running and says so."""
        env = {**os.environ, RESTART_ENV: f"{os.getpid()}:{self.port() or 0}"}
        log_path = self.cfg.storage / "sedjem.log"
        try:
            self.cfg.storage.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as log:
                proc = spawn(restart_command(), cwd=os.getcwd(), env=env, stdout=log, stderr=subprocess.STDOUT)
        except OSError as e:
            raise InstallError(f"Sedjem was updated but couldn't start again ({e}). Quit it and start it again.") from None
        end = time.monotonic() + RESTART_GRACE
        while time.monotonic() < end:
            if proc.poll() is not None:
                raise InstallError(f"Sedjem was updated, but the new version stopped as it started (exit code "
                                   f"{proc.returncode}); what it printed is in {log_path}. This one keeps running.")
            time.sleep(0.1)
        print(f"Sedjem was updated and runs again in the background; it writes to {log_path}", flush=True)
        self.quit(restarting=True)

    def update_windows(self, available, target):
        """Download and check the installer, start it in its update mode, quit: it installs over this copy and then
        starts the new one."""
        path = self.download(target.get("assets") or [], asset_name("windows", available["version"]))
        self.progress(state="installing")
        spawn([str(path), *INSTALLER_FLAGS], cwd=str(path.parent))
        self.progress(state="restarting")
        self.quit(restarting=True)

    def update_linux(self, available, target):
        """Download and check the archive, unpack it beside the running build as Sedjem-<version>/, show it."""
        version = available["version"]
        dest = self.executable.resolve().parent.parent / f"Sedjem-{version}"
        if dest.exists():
            if _read_json(dest / "_internal" / "app" / "_build.json").get("version") != version:
                raise InstallError(f"{dest} is already there. Move or delete it, then try again.")
        else:
            path = self.download(target.get("assets") or [], asset_name("linux", version))
            self.progress(state="installing")
            unpack(path, dest)
            path.unlink(missing_ok=True)
        self.progress(state="done", folder=str(dest))
        try:
            self.show_folder(str(dest))
        except OSError:
            pass

    def asset_url(self, asset):
        """(address, headers) to download one of this repository's release files: the API's address for it, else
        its address on github.com; None for anything else."""
        api = urllib.parse.urlsplit(self.api)
        u = urllib.parse.urlsplit(asset.get("url") or "")
        prefix = f"{api.path.rstrip('/')}/repos/{self.repo}/releases/assets/".lower()
        if api_ok(self.api) and (u.scheme, u.netloc.lower()) == (api.scheme, api.netloc.lower()) \
                and not (u.query or u.fragment) and u.path.lower().startswith(prefix) \
                and re.fullmatch(r"[0-9]{1,20}", u.path[len(prefix):]):
            return asset["url"], {"Accept": "application/octet-stream"}
        w = urllib.parse.urlsplit(asset.get("download") or "")
        parts = w.path.split("/")
        if w.scheme == "https" and w.netloc.lower() == "github.com" and not (w.query or w.fragment) and len(parts) == 7 \
                and "/".join(parts[1:3]).lower() == self.repo.lower() and parts[3:5] == ["releases", "download"] \
                and all(SEGMENT.fullmatch(p) and p not in (".", "..") for p in parts[5:]):
            return asset["download"], {}
        return None

    def download(self, assets, name):
        """A release file, checked against the release's SHA256SUMS.txt, saved in <storage>/updates/: its path. A
        file that doesn't match is deleted."""
        by_name = {a["name"]: a for a in assets}
        if name not in by_name or SUMS not in by_name:
            raise InstallError(f"This release has no {name if name not in by_name else SUMS}.")
        found, sums_found = self.asset_url(by_name[name]), self.asset_url(by_name[SUMS])
        if not (found and sums_found):
            raise InstallError("This release's files aren't where this repository keeps its releases, so Sedjem "
                               "won't download them.")
        shutil.rmtree(self.folder, ignore_errors=True)  # what an earlier update downloaded
        self.folder.mkdir(parents=True, exist_ok=True)
        part, dest = self.folder / f"{name}.part", self.folder / name
        http = requests.Session()
        http.headers["User-Agent"] = "Sedjem"
        try:
            with http.get(sums_found[0], headers=sums_found[1], stream=True, timeout=(10, 30)) as r:
                if r.status_code != 200 or not secure(r.url):
                    raise InstallError(f"GitHub answered HTTP {r.status_code} for {SUMS}.")
                raw = r.raw.read(65537, decode_content=True)
            if len(raw) > 65536:
                raise InstallError(f"{SUMS} is far larger than it should be.")
            expected = parse_sums(raw.decode("utf-8", errors="replace")).get(name)
            if not expected:
                raise InstallError(f"{SUMS} doesn't list {name}.")
            digest, done = hashlib.sha256(), 0
            with http.get(found[0], headers={**found[1], "Accept-Encoding": "identity"}, stream=True, timeout=(20, 60)) as r:
                if r.status_code != 200 or not secure(r.url):
                    raise InstallError(f"GitHub answered HTTP {r.status_code} for {name}.")
                total = int(r.headers.get("Content-Length") or 0) or by_name[name].get("size")
                self.progress(state="downloading", done=0, total=total)
                with open(part, "wb") as out:
                    for chunk in r.iter_content(1 << 20):
                        out.write(chunk)
                        digest.update(chunk)
                        done += len(chunk)
                        self.progress(done=done)
                        if done > MAX_DOWNLOAD:
                            raise InstallError(f"{name} is far larger than it should be.")
            self.progress(state="verifying")
            if digest.hexdigest() != expected:
                raise InstallError(f"{name} doesn't match its checksum in {SUMS}: the download was damaged or changed, "
                                   "so it was deleted. Try again.")
            replace_file(part, dest)
            return dest
        except requests.RequestException:
            raise InstallError(f"Downloading {name} failed: no connection to GitHub, or it was cut off. Try again.") from None
        finally:
            http.close()
            part.unlink(missing_ok=True)


def asset_name(kind, version):
    return f"Sedjem-{version}-setup.exe" if kind == "windows" else f"Sedjem-{version}-linux-x64.tar.gz"
