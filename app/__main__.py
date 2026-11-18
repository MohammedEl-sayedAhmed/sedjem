"""Start the app: .venv/bin/python -m app  (or ./app.sh). Stop with Ctrl+C or Quit in the app."""
import argparse
import sys
import threading
import webbrowser
from pathlib import Path

from . import updates
from .config import Config
from .server import NotLocal, make_server
from .session import Session, running_port


def open_window(cfg, login):
    """Open the login page (app/session.py) in a window with Sedjem's own browser profile, which never opens a page
    from a port that Sedjem didn't check first, or else in the default browser; False if neither opened."""
    from .desktop import app_mode_browser  # (it imports more than a start without a window needs)
    profile = cfg.storage / "browser-profile"
    profile.mkdir(parents=True, exist_ok=True)
    return bool(app_mode_browser(login.as_uri(), profile)) or webbrowser.open(login.as_uri())


def main():
    if "--desktop" in sys.argv[1:]:  # ./app.sh --desktop: the desktop window (app/desktop.py)
        from .desktop import main as desktop
        return desktop([a for a in sys.argv[1:] if a != "--desktop"])
    ap = argparse.ArgumentParser(prog="app", description="Sedjem: transcribe recordings with speaker labels.")
    ap.add_argument("--port", type=int, help="default: [server] port in app/config.toml; 0, a new free one at each start")
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    ap.add_argument("--desktop", action="store_true", help="open the desktop window instead (see app/desktop.py)")
    ap.add_argument("--config", type=Path, help="another config file instead of app/config.toml")
    ap.add_argument("--install-launcher", action="store_true",
                    help="add Sedjem to the desktop's application menu (~/.local/share/applications)")
    args = ap.parse_args()
    if args.install_launcher:
        from .desktop import install_launcher  # the desktop app also keeps it current when it starts
        dest = install_launcher()
        print(f"added {dest}; remove that file to undo" if dest else "only on Linux desktops")
        return
    cfg = Config(args.config) if args.config else Config()
    updates.restarted()  # started again by an update: once the copy before has stopped (the port is new, as always)
    port = args.port if args.port is not None else cfg.server["port"]  # 0: a new free port at each start
    running = running_port(cfg.storage, port)  # it proves that it knows the secret (app/session.py)
    if running:  # already running (e.g. started from the app menu): just open it
        login = Session(cfg.storage).login_page(running)  # lets the window in (app/session.py)
        print(f"Sedjem is already running at http://127.0.0.1:{running}/; open {login} in a web browser to use it")
        if not args.no_browser:
            open_window(cfg, login)
        return
    try:
        server, app = make_server(cfg, port=port)
    except NotLocal as e:  # [server] host would let other computers in
        sys.exit(str(e))
    except OSError:
        sys.exit(f"port {port} is in use by another program; start with --port 0 for a free one")
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    app.updates.start()  # checks for updates in the background, if the user agreed to it
    app.usage.start()  # sends the anonymous usage counts, while they are allowed (app/usage.py)
    app.remote.start()  # runs jobs for other computers, if that is switched on (app/remote.py)
    # A window is let in by this page, which only this user can read, and not by the address (app/session.py)
    login = app.session.login_page(port)
    print(f"Sedjem is running at {url}  (data: {cfg.storage}; stop with Ctrl+C)\n"
          f"Open {login} in a web browser to use it.", flush=True)
    # also after an update: the port and the secret are new, so the window that was open can't follow
    if cfg.server.get("open_browser", True) and not args.no_browser:
        threading.Timer(0.6, open_window, [cfg, login]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.runner.shutdown()
        app.remote.stop()
        app.session.stopped(port)  # before the port is free, which is when an update's new copy goes on
        server.server_close()


if __name__ == "__main__":
    main()
