"""What assistants connected over MCP (app/mcp.py) may see, the log of what they asked, and how to connect them.

Three settings, saved like the others in <storage>/settings.json and changed in Settings → Assistants:
  mcp_courses     YouTube courses and videos added on their own are visible (default on)
  mcp_recordings  every other transcription (meetings, calls, files) is visible (default on)
  mcp_hidden      transcriptions and courses hidden one by one: "job:<id>" and "course:<playlist id>"

To an assistant, a hidden or switched-off transcription is as if it didn't exist: it is never listed, searched,
read, offered as a resource or suggested in a completion.

The log is <storage>/mcp/log.jsonl, one JSON object per line, oldest first:
  {"time": ISO time, "client": "Claude Desktop 1.2", "action": "search", "detail": "tokenizers", "ids": [job ids]}
It is kept to its newest LOG_KEEP entries and shown in Settings → Assistants.
"""
import json
import os
import re
import sys
import threading
from datetime import datetime

from .config import FROZEN, ROOT, home_dir

ID = r"\d{8}-\d{6}-[0-9a-f]{4}"                 # a transcription (app/jobs.py ID_RE)
COURSE_ID = r"[A-Za-z0-9_-]{2,64}"              # a YouTube playlist (app/youtube.py LIST_ID)
HIDDEN_KEY = re.compile(rf"^(job:{ID}|course:{COURSE_ID})$")
MAX_HIDDEN = 5000
LOG_KEEP, LOG_TRIM_AT = 2000, 2500
_log_lock = threading.Lock()


# ---- what assistants may see ----------------------------------------------------------------------

def kind(job):
    """"course" for a YouTube lecture or a YouTube video added on its own, else "recording"."""
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
    hidden = set(s.get("mcp_hidden") or ())
    return not any(k in hidden for k in keys(job))


def set_hidden(cfg, key, hide):
    """Hide ("job:<id>" or "course:<playlist id>") from assistants, or show again; returns the settings."""
    if not HIDDEN_KEY.match(str(key or "")):
        raise ValueError(f"not a transcription or a course: {key!r}")
    hidden = [k for k in cfg.setting("mcp_hidden") or [] if k != key]
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


def log(cfg, client, action, detail="", ids=()):
    """Add one entry; never raises (a log that can't be written must not stop an answer)."""
    entry = {"time": datetime.now().astimezone().isoformat(timespec="seconds"), "client": str(client or "")[:120],
             "action": str(action)[:60], "detail": str(detail or "")[:300], "ids": [str(i) for i in ids][:200]}
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
    return sys.executable, ["-P", "-m", "app.mcp"], env


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
