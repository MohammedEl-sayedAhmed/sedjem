"""Settings: app/config.toml, optional overrides in <storage>/config.toml, and API keys.

Two base folders:
  ROOT  where the code and bundled files are (the project folder, or the unpacked desktop build)
  home  where data and models go: the project folder when run from source, a per-user folder in
        the desktop build (%LOCALAPPDATA%\\Sedjem on Windows, ~/.local/share/sedjem on Linux,
        ~/Library/Application Support/Sedjem on macOS); SEDJEM_HOME overrides both. The app was called
        Tafrigh before, so TAFRIGH_HOME still works and an existing Tafrigh folder is moved to the new name
"""
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))  # running from a PyInstaller build
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "app" / "config.toml"


def home_dir():
    for var in ("SEDJEM_HOME", "TAFRIGH_HOME"):
        if os.environ.get(var):
            return Path(os.environ[var]).expanduser()
    if not FROZEN:
        return ROOT
    if sys.platform == "win32":
        base, name, old = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"), "Sedjem", "Tafrigh"
    elif sys.platform == "darwin":
        base, name, old = Path.home() / "Library" / "Application Support", "Sedjem", "Tafrigh"
    else:
        base, name, old = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"), "sedjem", "tafrigh"
    if not (base / name).exists() and (base / old).is_dir():
        try:
            (base / old).rename(base / name)
        except OSError:
            return base / old  # in use or not allowed: keep using the old folder
    return base / name


def arch():
    """The processor family, as the config's download lists name it: "x86_64" or "arm64"."""
    machine = platform.machine().lower()
    return {"amd64": "x86_64", "x64": "x86_64", "aarch64": "arm64", "armv8": "arm64"}.get(machine, machine)


def replace_file(src, dst, tries=40):
    """os.replace, retried: on Windows it fails while another thread or process has dst open."""
    for i in range(tries):
        try:
            return os.replace(src, dst)
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.05)


def merge(base, extra):
    """Overlay extra on base: tables merge key by key, [[models]] merge by id."""
    out = dict(base)
    for key, value in extra.items():
        if key == "models":
            models = {m["id"]: dict(m) for m in base.get("models", [])}
            for m in value:
                models[m["id"]] = {**models.get(m["id"], {}), **m}
            out["models"] = list(models.values())
        elif isinstance(value, dict) and isinstance(base.get(key), dict):
            out[key] = {**base[key], **value}
        else:
            out[key] = value
    return out


def is_path(name):
    """A model given as a folder (models/..., ./x, C:\\x) rather than a faster-whisper name."""
    return "/" in name or "\\" in name or name.startswith(".") or Path(name).is_absolute()


class Config:
    def __init__(self, path=None, storage=None, home=None):
        data = tomllib.loads(Path(path or DEFAULT_CONFIG).read_text(encoding="utf-8"))
        self.home = Path(home) if home else home_dir()
        self.storage = Path(storage) if storage else self.path(data["storage"]["dir"])
        override = self.storage / "config.toml"
        if override.exists():
            data = merge(data, tomllib.loads(override.read_text(encoding="utf-8")))
        self.data = data
        self.server = data["server"]
        self.defaults = data["defaults"]
        self.local = data["local"]
        self.keep_original = data["storage"].get("keep_original", False)
        self.max_upload = int(data["storage"].get("max_upload_gb", 4) * 1024 ** 3)
        self.courses = self.path(data["storage"].get("courses", "courses"))  # transcripts of YouTube links (app/courses.py)
        self.models = {m["id"]: m for m in data["models"] if not m.get("disabled")}
        # Models added from Hugging Face in the app (app/hub.py); they never replace a model of the config.
        self.added_models_path = self.storage / "models.json"
        for m in _read_json(self.added_models_path).get("models", []):
            if isinstance(m, dict) and m.get("hub") and m.get("kind") == "local" and str(m.get("id")).startswith("hf-") \
                    and all(m["id"] != x["id"] for x in data["models"]):
                self.models[m["id"]] = m
        self.secrets_path = self.storage / "secrets.json"
        self.settings_path = self.storage / "settings.json"

    def path(self, value):
        """A path from the config: absolute, or relative to the data folder (home)."""
        return Path(value) if Path(value).is_absolute() else self.home / value

    def python(self):
        """The interpreter for local model runs: [local] python if set, else the one running the app."""
        return str(self.path(self.local["python"])) if self.local.get("python") else sys.executable

    # API keys: environment first, then the ones saved from the app.
    def secrets(self):
        return _read_json(self.secrets_path)

    # Settings changed in the app (Settings → Speed) are saved to <storage>/settings.json and win over
    # the same keys in [local].
    SETTINGS = {"performance_while_running": False, "device": "auto", "threads": 10}

    def saved_settings(self):
        return _read_json(self.settings_path)

    def setting(self, key):
        return self.saved_settings().get(key, self.local.get(key, self.SETTINGS[key]))

    def settings(self):
        return {key: self.setting(key) for key in self.SETTINGS}

    def save_settings(self, **values):
        """Check and save settings from the app; returns them all. Raises ValueError on a bad value."""
        for key, value in values.items():
            if key == "performance_while_running" and isinstance(value, bool):
                continue
            if key == "device" and value in ("auto", "cpu"):
                continue
            if key == "threads" and isinstance(value, int) and not isinstance(value, bool) \
                    and 1 <= value <= max(1, os.cpu_count() or 1):
                continue
            raise ValueError(f"bad value for {key}: {value!r}")
        saved = {**self.saved_settings(), **values}
        self.storage.mkdir(parents=True, exist_ok=True)
        tmp = self.settings_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(saved, indent=1), encoding="utf-8")
        replace_file(tmp, self.settings_path)
        return self.settings()

    def api_key(self, model):
        env = os.environ.get(model.get("key_env", ""), "").strip()
        return env or self.secrets().get(model["id"], "").strip()

    def key_source(self, model):
        if os.environ.get(model.get("key_env", ""), "").strip():
            return "environment"
        return "app" if self.secrets().get(model["id"], "").strip() else None

    def region(self, model):
        """A hosted model's region (Azure Speech keys work only in their resource's region): the
        environment, then the one saved in Settings (next to the key), then the config."""
        env = os.environ.get(model.get("region_env", ""), "").strip()
        return (env or self.secrets().get(f"{model['id']}:region", "").strip() or model.get("region", "")).lower()

    def save_key(self, model_id, key):
        secrets = self.secrets()
        if key:
            secrets[model_id] = key
        else:
            secrets.pop(model_id, None)
        self.storage.mkdir(parents=True, exist_ok=True)
        tmp = self.secrets_path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # mode is ignored on Windows,
        with os.fdopen(fd, "w", encoding="utf-8") as f:                    # where the folder is per-user
            json.dump(secrets, f)
        replace_file(tmp, self.secrets_path)

    def whisper_source(self, model):
        """What to pass as --whisper-model: the model folder if downloaded, else a faster-whisper
        name found in the Hugging Face cache (the model's `fallback`), else None."""
        for name in (model.get("whisper_model"), model.get("fallback")):
            if not name:
                continue
            if is_path(name):
                if (self.path(name) / "model.bin").exists():
                    return str(self.path(name))
            else:
                from huggingface_hub import try_to_load_from_cache
                if isinstance(try_to_load_from_cache(f"Systran/faster-whisper-{name}", "model.bin"), str):
                    return name
        return None

    def availability(self, model):
        """(ready, reason): whether the model can run now, and what is missing if not."""
        if model["kind"] == "hosted":
            return (True, None) if self.api_key(model) else (False, "needs an API key")
        if model["engine"] == "whisper":
            return (True, None) if self.whisper_source(model) else (False, "not downloaded")
        if model["engine"] in ("cohere", "gguf"):  # gguf: any GGUF speech model transcribe.cpp runs
            return (True, None) if self.path(model["cohere_model"]).exists() else (False, "not downloaded")
        return False, f"unknown engine {model['engine']}"

    def speakers_ready(self):
        return self.path(self.local["voiceprint_model"]).exists()

    def download_items(self):
        """Everything the app can download: {id: {"title", "files": [{url, path, size, sha256}], "convert"}}
        (convert: a Transformers checkpoint turned into a faster-whisper model after the download)."""
        items = {mid: {"title": m["title"], "files": m["files"], "convert": m.get("convert")}
                 for mid, m in self.models.items() if m["kind"] == "local" and m.get("files")}
        if self.local.get("voiceprint_files"):
            items["voiceprints"] = {"title": "Voiceprint model (speaker labels)", "files": self.local["voiceprint_files"]}
        platform = "win32" if os.name == "nt" else sys.platform
        cuda = [f for f in self.local.get("cuda_files", []) if f.get("platform") == platform]
        if cuda:
            items["cuda"] = {"title": "NVIDIA GPU libraries (for Whisper)", "files": cuda}
        deno = [f for f in self.local.get("deno_files", []) if f.get("platform") == platform and f.get("arch") == arch()]
        if deno:
            items["deno"] = {"title": "Deno (for YouTube links)", "files": deno[:1]}
        return items

    def cuda_dir(self):
        """Where the NVIDIA libraries are unpacked (transcribe.py --cuda-libs)."""
        files = self.download_items().get("cuda", {}).get("files")
        return self.path(files[0]["path"]).parent if files else self.path("models/nvidia-cuda")


def _read_json(path):
    """A small JSON file as a dict ({} if missing or unreadable); retried while Windows replaces it."""
    for _ in range(20):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (PermissionError, ValueError):  # Windows: being replaced right now
            time.sleep(0.025)
        except OSError:
            return {}
    return {}


def power_profile():
    """The current power profile (power-saver, balanced, performance), or None if unknown."""
    if not shutil.which("powerprofilesctl"):
        return None
    try:
        return subprocess.run(["powerprofilesctl", "get"], capture_output=True, text=True, timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def set_power_profile(profile):
    try:
        return subprocess.run(["powerprofilesctl", "set", profile], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
