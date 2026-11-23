"""What assistants connected over MCP (app/mcp.py) may see and do, the log of what they asked, and how to connect them.

The settings, saved like the others in <storage>/settings.json and changed in Settings → Assistants:
  mcp_courses     courses (YouTube playlists, Google Drive folders, and folders or files on the computer added as
                  a course) and YouTube videos added on their own are visible (default on)
  mcp_recordings  every other transcription (meetings, calls, files) is visible (default on)
  mcp_hidden      transcriptions and courses hidden one by one: "job:<id>" and "course:<course id>"
  mcp_write       assistants may save new versions of transcripts (save_version; default off). Transcript text is
                  quoted data that anyone in a recording could have said, so nothing an assistant reads in it can
                  lead to a save unless the user turned this on; a save never deletes anything either.
  mcp_links       assistants may start transcriptions from YouTube and Google Drive links (default off)
  mcp_files       assistants may transcribe recordings inside the folders in mcp_folders (default off)
  mcp_folders     those folders: absolute paths, at most MAX_FOLDERS
  mcp_hosted      assistants may choose a hosted model, which uploads the recording (default off: local models only)
The four switches that let an assistant act are off until the user turns them on, for the same reason as saving:
transcript text and web pages can hold words that try to steer an assistant. An assistant starts a transcription
through the running app (app/server.py checks these rules again for every request that names an assistant), and
it may cancel only a transcription that an assistant started (its job has "assistant": {"client": name}).

To an assistant, a hidden or switched-off transcription is as if it didn't exist: it is never listed, searched,
read, offered as a resource or suggested in a completion.

The log is <storage>/mcp/log.jsonl, one JSON object per line, oldest first:
  {"time": ISO time, "client": "Claude Desktop 1.2", "action": "search", "detail": "tokenizers", "ids": [job ids]}
with "version" too for a version an assistant saved. It is kept to its newest LOG_KEEP entries and shown in
Settings → Assistants.
"""
import json
import os
import re
import sys
import threading
from datetime import datetime
from pathlib import Path

from .config import FROZEN, ROOT, home_dir

ID = r"\d{8}-\d{6}-[0-9a-f]{4}"                 # a transcription (app/jobs.py ID_RE)
COURSE_ID = r"[A-Za-z0-9_-]{2,100}"  # a YouTube playlist (app/youtube.py LIST_ID), a Drive folder (app/drive.py
# DRIVE_ID), or recordings on the computer (app/local.py COURSE_ID)
HIDDEN_KEY = re.compile(rf"^(job:{ID}|course:{COURSE_ID})$")
MAX_HIDDEN = 5000
MAX_FOLDERS, MAX_FOLDER_CHARS = 50, 1000  # mcp_folders: how many, and how long each path may be
# The recordings the app takes by extension: what an assistant may transcribe from a folder, the desktop app's file
# picker's filter (app/desktop.py RECORDINGS), and the recordings listed in a folder or among files picked together
# (app/local.py, and the page's local.js through GET /api/status)
RECORDING_TYPES = (".mp3", ".m4a", ".wav", ".ogg", ".opus", ".flac", ".aac", ".amr", ".wma", ".mp4", ".mkv", ".mov",
                   ".webm", ".avi", ".3gp")
LOG_KEEP, LOG_TRIM_AT = 2000, 2500
_log_lock = threading.Lock()


# ---- what assistants may see ----------------------------------------------------------------------

def kind(job):
    """"course" for a course lecture (of a YouTube playlist, a Google Drive folder, or recordings on the computer added
    as a course) or a YouTube video added on its own, else "recording"."""
    return "course" if job.get("youtube_id") or job.get("course") else "recording"


def keys(job):
    """The hide keys that apply to a transcription: its own, and its course's."""
    out = [f"job:{job.get('id')}"]
    if (job.get("course") or {}).get("id"):
        out.append(f"course:{job['course']['id']}")
    return out


def visible(cfg, job, settings=None):
    """Whether an assistant may see this transcription. settings: cfg.settings(), read once by callers that
    check many jobs."""
    s = settings if settings is not None else cfg.settings()
    if not s["mcp_courses" if kind(job) == "course" else "mcp_recordings"]:
        return False
    hidden = s.get("mcp_hidden")
    hidden = {k for k in hidden if isinstance(k, str)} if isinstance(hidden, list) else set()  # a hand edit can break it
    return not any(k in hidden for k in keys(job))


def writes(cfg, settings=None):
    """Whether assistants may save new versions of transcripts (mcp_write: only when the user turned it on)."""
    s = settings if settings is not None else cfg.settings()
    return s.get("mcp_write") is True


def links(cfg, settings=None):
    """Whether assistants may start transcriptions from YouTube and Google Drive links (mcp_links)."""
    s = settings if settings is not None else cfg.settings()
    return s.get("mcp_links") is True


def files(cfg, settings=None):
    """Whether assistants may transcribe recordings inside the allowed folders (mcp_files)."""
    s = settings if settings is not None else cfg.settings()
    return s.get("mcp_files") is True


def hosted(cfg, settings=None):
    """Whether assistants may choose a hosted model, which uploads the recording (mcp_hosted)."""
    s = settings if settings is not None else cfg.settings()
    return s.get("mcp_hosted") is True


def starts_jobs(cfg, settings=None):
    """Whether assistants may start transcriptions at all: from links, or from files."""
    s = settings if settings is not None else cfg.settings()
    return links(cfg, s) or files(cfg, s)


class Refused(ValueError):
    """A file an assistant may not transcribe, and why, in words for the assistant."""


def folders(settings):
    """The folders an assistant may transcribe files from: mcp_folders with every symbolic link followed, those
    that are folders now (a path written by hand that isn't absolute is left out)."""
    saved = settings.get("mcp_folders")
    out = []
    for f in saved if isinstance(saved, list) else []:
        if isinstance(f, str) and f and "\0" not in f and os.path.isabs(f):
            real = os.path.realpath(f)
            if os.path.isdir(real) and real not in out:
                out.append(real)
    return out


def recording_path(path, settings):
    """The real path of a recording an assistant asked to transcribe, once it is allowed: a file (not a folder) with
    a recording's extension, inside one of the allowed folders, both with every symbolic link followed, so neither
    ".." nor a link can lead outside them. Raises Refused saying why. The caller checks mcp_files."""
    if not isinstance(path, str) or not path.strip() or "\0" in path or len(path) > 4096:
        raise Refused("Give the recording's full path.")
    if not os.path.isabs(path):
        raise Refused(f"Give the recording's full path, not {path!r}.")
    allowed = folders(settings)
    if not allowed:
        raise Refused("The user hasn't chosen any folders that assistants may transcribe files from: they are chosen "
                      "in Settings → Assistants.")
    real = os.path.realpath(path)
    # Inside an allowed folder by its path's parts (commonpath), not as text, so /music-other isn't inside /music.
    # The same test is made again as text on the folder with a separator after it, which is the check CodeQL
    # recognises before a path is used.
    outside = Refused(f"{path} is not inside a folder that the user lets assistants transcribe files from: "
                      f"{', '.join(allowed)}.")
    inside = [f for f in allowed if _common(real, f) == f]
    if not inside:
        raise outside
    if not real.startswith(tuple(f if f.endswith(os.sep) else f + os.sep for f in inside)):
        raise outside
    if not os.path.exists(real):
        raise Refused(f"There is no file at {path}.")
    if not os.path.isfile(real):
        raise Refused(f"{path} is not a file.")
    if os.path.splitext(real)[1].lower() not in RECORDING_TYPES:
        raise Refused(f"{path} is not a recording that Sedjem takes ({', '.join(RECORDING_TYPES)}).")
    return real


def _common(path, folder):
    """The path the two have in common, or None (on Windows, two drives have none)."""
    try:
        return os.path.commonpath([path, folder])
    except ValueError:
        return None


def check_folders(value):
    """Whether a value is a valid mcp_folders setting (used by Config.save_settings): a list of different absolute
    paths. Nothing on disk is read here; a folder that isn't there allows nothing."""
    return isinstance(value, list) and len(value) <= MAX_FOLDERS and len(set(map(str, value))) == len(value) and all(
        isinstance(f, str) and 0 < len(f) <= MAX_FOLDER_CHARS and f == f.strip() and "\0" not in f
        and os.path.isabs(f) for f in value)


def set_hidden(cfg, key, hide):
    """Hide ("job:<id>" or "course:<course id>") from assistants, or show again; returns the settings."""
    if not HIDDEN_KEY.match(str(key or "")):
        raise ValueError(f"not a transcription or a course: {key!r}")
    saved = cfg.setting("mcp_hidden")
    # entries that aren't valid (a settings.json edited by hand) are dropped, or no change could ever be saved
    hidden = [k for k in saved if k != key and isinstance(k, str) and HIDDEN_KEY.match(k)] if isinstance(saved, list) else []
    if hide:
        hidden.append(key)
    return cfg.save_settings(mcp_hidden=hidden)


def check_hidden(value):
    """Whether a value is a valid mcp_hidden setting (used by Config.save_settings)."""
    return isinstance(value, list) and len(value) <= MAX_HIDDEN and all(
        isinstance(k, str) and HIDDEN_KEY.match(k) for k in value)


# ---- the log ---------------------------------------------------------------------------------------

def log_path(cfg):
    return cfg.storage / "mcp" / "log.jsonl"


def log(cfg, client, action, detail="", ids=(), version=None):
    """Add one entry; never raises (a log that can't be written must not stop an answer). version: the number of the
    version an assistant saved."""
    entry = {"time": datetime.now().astimezone().isoformat(timespec="seconds"), "client": str(client or "")[:120],
             "action": str(action)[:60], "detail": str(detail or "")[:300], "ids": [str(i) for i in ids][:200],
             **({"version": version} if isinstance(version, int) and not isinstance(version, bool) else {})}
    path = log_path(cfg)
    try:
        with _log_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            _trim(path)
    except OSError:
        pass
    return entry


def _trim(path):
    """Keep the newest LOG_KEEP entries once there are more than LOG_TRIM_AT."""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    if len(lines) > LOG_TRIM_AT:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text("".join(lines[-LOG_KEEP:]), encoding="utf-8")
        os.replace(tmp, path)


def read_log(cfg, limit=50):
    """The newest entries first (skipping any line that isn't a whole entry)."""
    try:
        with open(log_path(cfg), encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
            if len(out) >= limit:
                break
    return out


def clear_log(cfg):
    log_path(cfg).unlink(missing_ok=True)


# ---- connecting an assistant -------------------------------------------------------------------------

def launch(cfg):
    """How an assistant starts Sedjem's MCP server: (command, args, env). The desktop build runs itself with
    --mcp; from source it is this Python with the project on PYTHONPATH. SEDJEM_HOME is set when the app
    runs on another data folder than the default, so the server reads the same transcriptions."""
    env = {} if FROZEN else {"PYTHONPATH": str(ROOT)}
    if cfg.home.resolve() != home_dir().resolve() or os.environ.get("SEDJEM_HOME"):
        env["SEDJEM_HOME"] = str(cfg.home)
    if FROZEN:
        return sys.executable, ["--mcp"], env
    return console_python(sys.executable), ["-P", "-m", "app.mcp"], env


def console_python(exe):
    """python.exe instead of the pythonw.exe next to it: app.cmd starts Sedjem with pythonw (no console window),
    and an assistant talks to the MCP server over the standard streams, which is what python.exe is for."""
    path = Path(exe)
    if path.name.lower() == "pythonw.exe" and (path.parent / "python.exe").is_file():
        return str(path.parent / "python.exe")
    return exe  # as given (Path would turn its slashes around on Windows)


def snippets(cfg, name="sedjem"):
    """The configuration for each assistant, ready to copy: [{"client", "where", "kind", "text"}]."""
    command, args, env = launch(cfg)
    server = {"command": command, "args": args, **({"env": env} if env else {})}
    desktop = {"win32": r"%APPDATA%\Claude\claude_desktop_config.json",
               "darwin": "~/Library/Application Support/Claude/claude_desktop_config.json"}.get(
        sys.platform, "~/.config/Claude/claude_desktop_config.json")
    code = ["claude", "mcp", "add", "--scope", "user", name]
    for k, v in env.items():
        code += ["-e", f"{k}={v}"]
    code += ["--", command, *args]
    return [
        {"client": "Claude Desktop", "where": desktop, "kind": "json",
         "text": json.dumps({"mcpServers": {name: server}}, indent=2, ensure_ascii=False)},
        {"client": "Claude Code", "where": "a terminal", "kind": "command", "text": _command_line(code)},
        {"client": "Cursor", "where": "~/.cursor/mcp.json", "kind": "json",
         "text": json.dumps({"mcpServers": {name: server}}, indent=2, ensure_ascii=False)},
        {"client": "VS Code", "where": ".vscode/mcp.json in a folder, or the user's mcp.json", "kind": "json",
         "text": json.dumps({"servers": {name: {"type": "stdio", **server}}}, indent=2, ensure_ascii=False)},
    ]


def _command_line(parts):
    """A command line to paste: each part quoted for the shell it will be pasted in."""
    if sys.platform == "win32":
        return " ".join(f'"{p}"' if re.search(r'[\s"&|<>^]', p) else p for p in parts)
    return " ".join(p if re.fullmatch(r"[\w@%+=:,./-]+", p) else "'" + p.replace("'", "'\\''") + "'" for p in parts)
