"""Sedjem as a desktop app: the same local server, shown in its own window.

The window, in order of preference:
  1. pywebview: a native window using the system's web engine (Edge WebView2 on Windows, WebKit on
     macOS, GTK WebKit or Qt on Linux), with native open/save dialogs
  2. a Chromium-family browser (Edge, Chrome, Chromium, Brave) in app mode: its own window with no
     tabs or address bar, and its own profile
  3. the default browser
  4. none: the server keeps running and the address is shown (--server asks for this directly)
Closing the window (1 or 2) quits the app, and so does Settings → Quit.

From source:   .venv/bin/python -m app.desktop        (Windows: .venv\\Scripts\\python -m app.desktop)
Desktop build: Sedjem / Sedjem.exe (see desktop/build.py)
"""
import argparse
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from . import engines, exports, report, updates
from . import transcript as T
from . import session
from .session import Session
from . import history
from .config import FROZEN, ROOT, Config
from .server import NotLocal, make_server, slug

WM_CLASS = "Sedjem"  # the window class on Linux, which the desktop matches to sedjem.desktop for the icon
LAUNCHER = """[Desktop Entry]
Type=Application
Name=Sedjem
GenericName=Meeting transcription
Comment=Transcribe recordings with speaker labels, on this computer or with a hosted service
Exec={exec}
Icon={icon}
Terminal=false
Categories=AudioVideo;Audio;Office;
StartupNotify=true
StartupWMClass={wm_class}
"""


def app_icon():
    """The icon file for the window: an .ico on Windows (from source; the built .exe carries its own), else PNG."""
    ico = ROOT / "desktop" / "sedjem.ico"
    if os.name == "nt":
        return str(ico) if ico.exists() else None
    png = ROOT / "app" / "static" / "icon-512.png"
    return str(png) if png.exists() else None


def launch_command():
    """How the desktop menu should start the desktop app: the build itself, or Python from source."""
    if FROZEN:
        return f'"{sys.executable}"'
    return f"sh -c 'cd \"{ROOT}\" && exec \"{sys.executable}\" -m app.desktop'"


def install_launcher():
    """Linux: keep ~/.local/share/applications/sedjem.desktop current, so the application menu lists Sedjem
    and the taskbar shows its icon for the app's window. Returns the file, or None elsewhere."""
    if not sys.platform.startswith("linux"):
        return None
    apps = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "applications"
    dest = apps / "sedjem.desktop"
    text = LAUNCHER.format(exec=launch_command(), icon=app_icon() or "audio-x-generic", wm_class=WM_CLASS)
    try:
        old = apps / "tafrigh.desktop"  # the app was called Tafrigh before: drop its entry, if it is ours
        if old.is_file() and "StartupWMClass=Tafrigh" in old.read_text(encoding="utf-8", errors="replace"):
            old.unlink()
        if not dest.exists() or dest.read_text(encoding="utf-8") != text:
            apps.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
            if shutil.which("update-desktop-database"):
                subprocess.run(["update-desktop-database", str(apps)], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return dest

RECORDINGS = ("Recordings (*.mp3;*.m4a;*.wav;*.ogg;*.opus;*.flac;*.aac;*.amr;*.wma;*.mp4;*.mkv;*.mov;*.webm;*.avi)",
              "All files (*.*)")


def running_port(cfg, preferred):
    """The port of this user's Sedjem already running on this data folder (then we just show it): the preferred one,
    or the one it noted in <storage>/port. It proves that it knows the secret (app/session.py), so a program that only
    took the port is never shown a window or sent the secret. None if none is running."""
    return session.running_port(cfg.storage, preferred)


def pick_port(preferred, wait=3.0):
    """The preferred port, or any free one if something else keeps it (0, the default: a new free one at each start,
    app/session.py). A Sedjem that was just closed can hold it for a moment, so it is tried again for up to `wait`
    seconds."""
    if not preferred:
        return 0
    deadline = time.monotonic() + wait
    while True:
        with socket.socket() as s:
            if os.name != "nt":  # as the server binds it: the closed app's lingering connections don't block it
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", preferred))
                return preferred
            except OSError:
                if time.monotonic() >= deadline:
                    break
        time.sleep(0.2)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Api:
    """Functions the page can call as window.pywebview.api.* (native window only).
    Attributes starting with _ are not exposed to the page."""

    def __init__(self, app):
        self._app, self._window = app, None

    def session(self):
        """The secret that lets this window's page into the server (app/session.py): the page keeps it in its
        sessionStorage and sends it as a header. It is never in an address or a command line."""
        return self._app.session.secret

    def _dialog(self, kind):
        import webview
        if hasattr(webview, "FileDialog"):  # pywebview 5+
            return getattr(webview.FileDialog, kind)
        return getattr(webview, f"{kind}_DIALOG")

    def pick_file(self):
        result = self._window.create_file_dialog(self._dialog("OPEN"), allow_multiple=False, file_types=RECORDINGS)
        return str(result[0]) if result else None

    def pick_folder(self):
        """A folder picked here, for Settings → Assistants (the folders assistants may transcribe files from): its
        path, or None. The page saves it with the other settings."""
        result = self._window.create_file_dialog(self._dialog("FOLDER"))
        folder = (result[0] if result else None) if isinstance(result, (list, tuple)) else result
        return str(folder) if folder else None

    def save_export(self, job_id, fmt, version=None):
        store = self._app.store
        job = store.get(job_id)
        if job is None or fmt not in T.EXPORTS:
            return None
        data = store.transcript(job_id)
        lines = data["lines"] if data else engines.partial_lines(store.dir(job_id))
        edited = bool(data and data.get("edited"))
        name = f"{slug(job.get('title'))}{'' if version is None else f'-v{int(version)}'}.{fmt}"
        if version is not None:  # a version picked in History (0 is the model's output)
            found = history.at(store, job_id, int(version), self._app.cfg)
            if found is None:
                return None
            job, lines, edited = found
        result = self._window.create_file_dialog(self._dialog("SAVE"), save_filename=name)
        path = result[0] if isinstance(result, (list, tuple)) else result
        if not path:
            return None
        details = report.details(job, lines, edited)  # as the browser download
        Path(path).write_text(T.EXPORTS[fmt][0](job, lines, details), encoding="utf-8")
        self._app.usage.exported(fmt)  # one more export of this format in the usage counts (app/usage.py)
        return str(path)

    def _chosen(self, ids, fmt):
        """The transcripts the Export dialog chose, with a transcript (app/exports.py), or [] for anything amiss."""
        if fmt not in T.EXPORTS or not isinstance(ids, list):
            return []
        try:
            return exports.chosen(self._app.store, [x for x in ids if isinstance(x, str)])
        except KeyError:
            return []

    def save_many(self, ids, fmt, combined=False):
        """The Export dialog's zip, or its one combined file, saved where the user picks here: the path, or None."""
        jobs = self._chosen(ids, fmt)
        if not jobs:
            return None
        one = bool(combined) and fmt in exports.COMBINED
        name = f"{exports.name(jobs, slug)}.{fmt if one else 'zip'}"
        result = self._window.create_file_dialog(self._dialog("SAVE"), save_filename=name)
        path = result[0] if isinstance(result, (list, tuple)) else result
        if not path:
            return None
        data = exports.combined(jobs, fmt).encode() if one else exports.zipped(exports.files(jobs, fmt, slug))
        Path(path).write_bytes(data)
        self._app.usage.exported(fmt, len(jobs))
        return str(path)

    def export_folder(self, ids, fmt):
        """The transcripts chosen in the Export dialog, a file for each (app/exports.py), written into a folder picked
        here: {"folder", "count"}, or None when none was picked or there is nothing to export."""
        jobs = self._chosen(ids, fmt)
        if not jobs:
            return None
        result = self._window.create_file_dialog(self._dialog("FOLDER"))
        folder = result[0] if isinstance(result, (list, tuple)) else result
        if not folder:
            return None
        count = exports.write(folder, exports.files(jobs, fmt, slug))
        self._app.usage.exported(fmt, count)
        return {"folder": str(folder), "count": count}

    def open_url(self, url):
        if isinstance(url, str) and url.startswith("https://"):
            webbrowser.open(url)

    def quit(self):
        self._window.destroy()


class SessionApi:
    """The bridge of a window that a second start opens on a copy that is already running: the secret, so that its
    page can be let in, and links. It has no pick_file or save_export, so the page uses the browser's ways."""

    def __init__(self, secret):
        self._secret, self._window = secret, None

    def session(self):
        return self._secret

    open_url = Api.open_url


def webview2_available():
    """Windows: whether the Edge WebView2 runtime (and .NET 4.6.2+) is installed. Without it pywebview
    quietly falls back to the Internet Explorer engine, which can't run this app. Elsewhere: True."""
    if os.name != "nt":
        return True
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full") as k:
            if winreg.QueryValueEx(k, "Release")[0] < 394802:
                return False
    except OSError:
        return False
    client = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"  # the WebView2 runtime
    for root, path in ((winreg.HKEY_CURRENT_USER, rf"SOFTWARE\{client}"),
                       (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\{client}"),
                       (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\{client}")):
        try:
            with winreg.OpenKey(root, path) as k:
                if str(winreg.QueryValueEx(k, "pv")[0]) not in ("", "0.0.0.0"):
                    return True
        except OSError:
            continue
    return False


def show_address(url, login):
    """No window of any kind could be opened: keep serving and say where (a windowed build has no console). The
    page is let in by opening login, <storage>/open-sedjem.html (app/session.py), which only this user can read."""
    text = (f"Sedjem is running at {url}\n\nOpen this file in a web browser: {login}\n"
            "It opens Sedjem and lets that browser in. To stop Sedjem, use Settings → Quit.")
    print(text, flush=True)
    if os.name == "nt" and getattr(sys, "frozen", False):
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "Sedjem", 0x40)  # MB_ICONINFORMATION


def show_error(text):
    """Why the app can't start: on the console or in sedjem.log, and in a message box in the Windows build."""
    print(text, file=sys.stderr, flush=True)
    if os.name == "nt" and getattr(sys, "frozen", False):
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "Sedjem", 0x10)  # MB_ICONERROR


def native_window(url, app, title="Sedjem", secret=None):
    """Show the app in a pywebview window until it is closed. False if no GUI backend is available. Without app (a
    copy that is already running), secret is that copy's, so the window's page can still be let in."""
    try:
        import webview
    except ImportError:
        return False
    if not webview2_available():
        print("no WebView2 runtime; using a browser window", file=sys.stderr)
        return False
    api = Api(app) if app else SessionApi(secret) if secret else None
    window = webview.create_window(title, url, js_api=api, width=1280, height=860, min_size=(820, 560),
                                   text_select=True)
    if api:
        api._window = window
    if app:
        app.on_quit = window.destroy
    try:
        storage = str(app.cfg.storage / "webview") if app else None
        webview.start(private_mode=False, storage_path=storage, icon=app_icon())
    except Exception as e:  # e.g. Linux without GTK WebKit or Qt: fall back to a browser window
        print(f"no native window ({type(e).__name__}: {e}); using a browser window", file=sys.stderr)
        if app:
            app.on_quit = None
        return False
    return True


def browser_candidates():
    names = ["msedge", "microsoft-edge", "microsoft-edge-stable", "google-chrome", "google-chrome-stable",
             "chrome", "chromium", "chromium-browser", "brave-browser"]
    found = [shutil.which(n) for n in names]
    if os.name == "nt":
        for base in (os.environ.get("PROGRAMFILES(X86)"), os.environ.get("PROGRAMFILES"), os.environ.get("LOCALAPPDATA")):
            if base:
                found += [str(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
                          str(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")]
    elif sys.platform == "darwin":
        found += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    return [p for p in found if p and os.path.isfile(p)]


def app_mode_browser(url, profile):
    """A Chromium-family browser showing only the app (--app), or None if there is none."""
    # its own profile makes it a separate browser process, so --class names only this window (Linux). Only X11
    # honours --class: on Wayland the browser names the window after its address, the taskbar can't match it
    # to sedjem.desktop and shows the browser's icon, so there the window is drawn through XWayland.
    extra = [f"--class={WM_CLASS}"] if sys.platform.startswith("linux") else []
    if extra and os.environ.get("WAYLAND_DISPLAY") and os.environ.get("DISPLAY"):
        extra.append("--ozone-platform=x11")
    for exe in browser_candidates():
        try:
            return subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
                                     "--no-default-browser-check", "--window-size=1280,860", *extra],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            continue
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sedjem", description="Sedjem: transcribe recordings with speaker labels.")
    ap.add_argument("--browser", action="store_true", help="use a browser window instead of the native one")
    ap.add_argument("--no-window", "--server", action="store_true",
                    help="only run the server (open the printed address in any browser)")
    ap.add_argument("--port", type=int, help="default: [server] port in app/config.toml, or any free port")
    ap.add_argument("--self-test", action="store_true", help="check this installation and exit (see app/selftest.py)")
    ap.add_argument("--window", action="store_true", help="with --self-test: also open and close a window")
    ap.add_argument("--report", help="with --self-test: write the results as JSON to this file")
    ap.add_argument("--models", action="store_true",
                    help="with --self-test: also download whisper-medium (~820 MB) and transcribe a speech sample")
    ap.add_argument("--mcp", action="store_true",
                    help="answer an assistant (Claude, Cursor, VS Code) over MCP on stdin and stdout; see app/mcp.py")
    args = ap.parse_args(argv)
    if args.mcp:  # before anything prints or opens: stdout carries only the assistant's messages
        from .mcp import main as serve
        sys.exit(serve())
    if args.self_test:
        from .selftest import run
        sys.exit(run(window=args.window, report=args.report, models=args.models))

    cfg = Config()
    cfg.apply_installer_choice()  # the usage counts' box of the Windows installer, once (desktop/installer.iss)
    install_launcher()
    if sys.stderr is None:  # a windowed build has no console: keep messages in a log file
        cfg.storage.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(cfg.storage / "sedjem.log", "a", encoding="utf-8", buffering=1)
    updates.restarted()  # started again by an update (app/updates.py): once the copy before has stopped
    preferred = args.port if args.port is not None else cfg.server["port"]  # 0: a new free port at each start
    running = running_port(cfg, preferred)
    if running:  # already running (e.g. started twice): just show it
        url = f"http://127.0.0.1:{running}/"
        session = Session(cfg.storage)  # the running copy's secret, in the same data folder (app/session.py)
        if args.browser or not native_window(url, None, secret=session.secret):
            login = session.login_page(running)  # a browser tab is let in by this page, not by its address
            if not app_mode_browser(login.as_uri(), cfg.storage / "browser-profile") and not webbrowser.open(login.as_uri()):
                show_address(url, login)
        return

    try:
        server, app = make_server(cfg, port=pick_port(preferred))
    except NotLocal as e:  # [server] host would let other computers in
        show_error(str(e))
        sys.exit(1)
    app.desktop = True
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    threading.Thread(target=server.serve_forever, daemon=True, name="sedjem-server").start()
    app.updates.start()  # checks for updates in the background, if the user agreed to it
    app.usage.start()  # sends the anonymous usage counts, while they are allowed (app/usage.py)
    app.remote.start()  # runs jobs for other computers, if that is switched on (app/remote.py)
    stopped = threading.Event()  # make_server noted the port in <storage>/port, for a second start (app/session.py)
    login = app.session.login_page(server.server_address[1])  # lets a browser tab in (app/session.py)
    try:
        if args.no_window:
            print(f"Sedjem is running at {url} (stop with Settings → Quit or Ctrl+C)\n"
                  f"Open {login} in a web browser to use it from there.", flush=True)
            app.on_quit = stopped.set
            while not stopped.wait(1):
                pass
            return
        if not args.browser and native_window(url, app):
            return
        profile = cfg.storage / "browser-profile"
        profile.mkdir(parents=True, exist_ok=True)
        browser = app_mode_browser(login.as_uri(), profile)
        if browser:
            app.on_quit = lambda: (browser.terminate(), stopped.set())
            t0 = time.time()
            browser.wait()
            if time.time() - t0 > 5:  # the window was closed by the user
                return
            # the browser handed the window to an instance that was already running: wait for Quit
        elif not webbrowser.open(login.as_uri()):  # also after an update: the old tab's secret is no longer good
            show_address(url, login)
        print(f"Sedjem is running at {url} (close with Settings → Quit or Ctrl+C)", flush=True)
        app.on_quit = stopped.set
        while not stopped.wait(1):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        app.runner.shutdown()
        app.remote.stop()
        server.shutdown()
        app.session.stopped(server.server_address[1])  # before the port is free, when an update's new copy goes on
        server.server_close()


if __name__ == "__main__":
    main()
