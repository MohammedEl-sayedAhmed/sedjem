#!/usr/bin/env python3
"""Build the desktop app with PyInstaller into dist/Sedjem/ (Sedjem.exe on Windows).

  python desktop/build.py              windowed build (no console window)
  python desktop/build.py --console    with a console, to see errors while debugging

Build on the system you build for (Windows builds on Windows): PyInstaller does not cross-compile.
The models are not bundled; the app downloads them (Settings → Models). Needs the
packages in requirements.txt and requirements-desktop.txt. The Windows installer is made from the
result with Inno Setup: iscc desktop/installer.iss
"""
import argparse
import os
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--console", action="store_true", help="keep a console window (debugging)")
    args = ap.parse_args()
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
        # the licence and the author credit travel with every copy
        "--add-data", f"{ROOT / 'LICENSE'}{sep}.", "--add-data", f"{ROOT / 'NOTICE'}{sep}.",
        # modules only imported inside functions or by the worker process
        "--hidden-import", "transcribe", "--hidden-import", "speakers", "--hidden-import", "sysinfo",
        "--hidden-import", "app.worker", "--hidden-import", "app.desktop", "--hidden-import", "app.selftest",
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
    PyInstaller.__main__.run(opts)
    exe = ROOT / "dist" / "Sedjem" / ("Sedjem.exe" if sys.platform == "win32" else "Sedjem")
    print(f"\nbuilt {exe}\ncheck it with: {exe} --self-test --report selftest.json")


if __name__ == "__main__":
    main()
