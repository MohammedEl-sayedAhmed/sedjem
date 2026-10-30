#!/usr/bin/env python3
"""Build the desktop app with PyInstaller into dist/Sedjem/ (Sedjem.exe on Windows).

  python desktop/build.py              windowed build (no console window)
  python desktop/build.py --console    with a console, to see errors while debugging

Build on the system you build for (Windows builds on Windows): PyInstaller does not cross-compile.
The models are not bundled; the app downloads them (Settings → Models). Needs the
packages in requirements.txt and requirements-desktop.txt. The Windows installer is made from the
result with Inno Setup: iscc desktop/installer.iss

Every build carries a stamp, app/_build.json, so that the app knows what it is running (for updates):
  {"version": "0.2.1-preview.20261021", "commit": "<sha>", "date": "<commit date>",
   "channel": "stable" or "preview", "built": "<build time>"}
The version and the channel come from SEDJEM_VERSION and SEDJEM_CHANNEL when they are set (the Desktop
build workflow sets both), otherwise from app/__init__.py, and the channel from the version. The commit
and its date come from git; the times are in UTC. The file is written for the build and removed again,
so a copy run from source never has one.

On Windows, Sedjem.exe also gets a version resource (PyInstaller's --version-file, written to build/), which
Windows shows in the file's properties: Sedjem as the product and the description, the author as the company, the
copyright, Sedjem.exe as the original file name, the version in full as the product version
(0.3.1-preview.20261030), and as the four numbers an .exe holds (0.3.1.0).
"""
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAMP = ROOT / "app" / "_build.json"  # bundled into app/, beside config.toml; git-ignored
# a release, or a nightly preview of the next patch: 0.2.1-preview.20261021, then .2, .3 the same day
VERSION = re.compile(r"\d+\.\d+\.\d+(?:-preview\.\d{8}(?:\.[1-9]\d*)?)?")
AUTHOR = "Mohammed El-sayed Ahmed"


def utc(moment):
    """A time as GitHub's API writes it: 2026-10-21T02:41:07Z."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git(*args):
    """What a git command prints in the project folder, or None without git or a checkout."""
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def app_version():
    text = (ROOT / "app" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M).group(1)


def stamp(env=None, now=None):
    """The build's stamp (see the top of this file). ValueError if SEDJEM_VERSION or SEDJEM_CHANNEL is wrong."""
    env = os.environ if env is None else env
    version = env.get("SEDJEM_VERSION") or app_version()
    if not VERSION.fullmatch(version):
        raise ValueError(f"version {version!r} is neither X.Y.Z nor X.Y.Z-preview.YYYYMMDD[.N]")
    channel = "preview" if "-preview." in version else "stable"
    if env.get("SEDJEM_CHANNEL") and env["SEDJEM_CHANNEL"] != channel:
        raise ValueError(f"SEDJEM_CHANNEL is {env['SEDJEM_CHANNEL']!r}, but version {version} is a {channel} version")
    commit = git("rev-parse", "HEAD")
    seconds = git("show", "-s", "--format=%ct", "HEAD") if commit else None
    return {"version": version, "commit": commit,
            "date": utc(datetime.fromtimestamp(int(seconds), timezone.utc)) if seconds else None,
            "channel": channel, "built": utc(now or datetime.now(timezone.utc))}


def version_numbers(version):
    """The four numbers of an .exe's version, each at most 65535: 0.3.1-preview.20261030 is (0, 3, 1, 0), as a
    preview's date doesn't fit. ValueError for a version that doesn't start with X.Y.Z or has a part too large."""
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", version)
    if not m:
        raise ValueError(f"version {version!r} doesn't start with X.Y.Z")
    numbers = (*(int(n) for n in m.groups()), 0)
    if max(numbers) > 0xFFFF:
        raise ValueError(f"version {version!r} has a part over 65535, which an .exe's version can't hold")
    return numbers


def version_resource(version):
    """Sedjem.exe's version resource (see the top), as the text PyInstaller's --version-file evaluates."""
    numbers = version_numbers(version)
    strings = {"CompanyName": AUTHOR, "FileDescription": "Sedjem", "FileVersion": ".".join(map(str, numbers)),
               "InternalName": "Sedjem", "LegalCopyright": f"Copyright (C) 2026 {AUTHOR}",
               "OriginalFilename": "Sedjem.exe", "ProductName": "Sedjem", "ProductVersion": version}
    table = ",\n".join(f"      StringStruct({key!r}, {value!r})" for key, value in strings.items())
    return ("# UTF-8\n"
            "# Sedjem.exe's version resource, written by desktop/build.py for one build\n"
            "VSVersionInfo(\n"
            f"  ffi=FixedFileInfo(filevers={numbers}, prodvers={numbers}, mask=0x3f, flags=0x0, OS=0x40004,\n"
            "                    fileType=0x1, subtype=0x0, date=(0, 0)),\n"
            "  kids=[\n"
            f"    StringFileInfo([StringTable('040904B0', [\n{table}])]),\n"  # US English, Unicode
            "    VarFileInfo([VarStruct('Translation', [0x0409, 1200])]),\n"
            "  ])\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--console", action="store_true", help="keep a console window (debugging)")
    args = ap.parse_args()
    try:
        info = stamp()
        resource = version_resource(info["version"])
    except ValueError as e:
        sys.exit(f"desktop/build.py: {e}")
    import PyInstaller.__main__  # here, so that the tests can read the stamp without PyInstaller
    sep = os.pathsep
    opts = [
        str(ROOT / "desktop" / "sedjem.py"), "--name", "Sedjem", "--noconfirm", "--clean", "--onedir",
        "--console" if args.console else "--windowed",
        "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"), "--specpath", str(ROOT / "build"),
        "--paths", str(ROOT),
        # the interface and the default settings, read at run time
        "--add-data", f"{ROOT / 'app' / 'static'}{sep}app/static",
        "--add-data", f"{ROOT / 'app' / 'config.toml'}{sep}app",
        "--add-data", f"{ROOT / 'app' / 'catalog.toml'}{sep}app",  # the recommended models
        "--add-data", f"{STAMP}{sep}app",  # what this build is: version, commit, channel
        # the licence and the author credit travel with every copy
        "--add-data", f"{ROOT / 'LICENSE'}{sep}.", "--add-data", f"{ROOT / 'NOTICE'}{sep}.",
        # modules only imported inside functions or by the worker process
        "--hidden-import", "transcribe", "--hidden-import", "speakers", "--hidden-import", "sysinfo",
        "--hidden-import", "nemotron", "--hidden-import", "threadpoolctl",
        "--hidden-import", "app.worker", "--hidden-import", "app.desktop", "--hidden-import", "app.selftest",
        "--hidden-import", "app.mcp",  # Sedjem --mcp, the assistants' server
        "--hidden-import", "cryptography.x509",  # the serving side's certificate (app/remote.py), made on first use
        # packages with native libraries or data files loaded at run time
        "--collect-all", "faster_whisper", "--collect-all", "ctranslate2", "--collect-all", "sherpa_onnx",
        "--collect-all", "transcribe_cpp", "--collect-all", "transcribe_cpp_native", "--collect-all", "av",
        "--collect-all", "webview",
        # YouTube links: yt-dlp (its own PyInstaller hook adds its modules) and the challenge scripts it runs
        "--hidden-import", "yt_dlp", "--collect-data", "yt_dlp_ejs",
        # not used; keeps the build smaller
        "--exclude-module", "tkinter", "--exclude-module", "matplotlib", "--exclude-module", "IPython",
        "--exclude-module", "pytest", "--exclude-module", "hf_xet",
    ]
    icon = ROOT / "desktop" / "sedjem.ico"
    if sys.platform == "win32" and icon.exists():
        opts += ["--icon", str(icon)]
    if sys.platform == "win32":  # the version resource (see the top), kept in build/ with PyInstaller's other files
        resource_file = ROOT / "build" / "version-info.txt"
        resource_file.parent.mkdir(parents=True, exist_ok=True)
        resource_file.write_text(resource, encoding="utf-8")
        opts += ["--version-file", str(resource_file)]
    STAMP.write_text(json.dumps(info, indent=1) + "\n", encoding="utf-8")
    try:
        PyInstaller.__main__.run(opts)
    finally:
        STAMP.unlink(missing_ok=True)  # only builds have a stamp; from source the app reads .git
    bundled = ROOT / "dist" / "Sedjem" / "_internal" / "app" / STAMP.name
    try:
        ok = json.loads(bundled.read_text(encoding="utf-8")) == info
    except (OSError, ValueError):
        ok = False
    if not ok:
        sys.exit(f"desktop/build.py: the build has no stamp, or another one, at {bundled}")
    exe = ROOT / "dist" / "Sedjem" / ("Sedjem.exe" if sys.platform == "win32" else "Sedjem")
    print(f"\nbuilt {exe}: Sedjem {info['version']} ({info['channel']}), commit {info['commit'] or 'unknown'}")
    print(f"check it with: {exe} --self-test --report selftest.json")


if __name__ == "__main__":
    main()
