"""Start the app: .venv/bin/python -m app  (or ./app.sh). Stop with Ctrl+C or Quit in the app."""
import argparse
import json
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

from . import updates
from .config import Config
from .server import make_server

def running_here(port):
    """True if this app already answers on the port."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as r:
            return json.load(r).get("app") == "sedjem"
    except (OSError, ValueError):
        return False


def main():
    if "--desktop" in sys.argv[1:]:  # ./app.sh --desktop: the desktop window (app/desktop.py)
        from .desktop import main as desktop
        return desktop([a for a in sys.argv[1:] if a != "--desktop"])
    ap = argparse.ArgumentParser(prog="app", description="Sedjem: transcribe recordings with speaker labels.")
    ap.add_argument("--port", type=int, help="default: [server] port in app/config.toml")
    ap.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
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
    restarted = updates.restarted()  # started again by an update: the same port, and the open page reloads itself
    port = restarted or args.port or cfg.server["port"]
    url = f"http://127.0.0.1:{port}/"
    try:
        server, app = make_server(cfg, port=port)
    except OSError:
        if running_here(port):  # already running (e.g. started from the app menu): just open it
            print(f"Sedjem is already running at {url}")
            if not args.no_browser:
                webbrowser.open(url)
            return
        sys.exit(f"port {port} is in use by another program; start with --port N")
    app.updates.start()  # checks for updates in the background, if the user agreed to it
    app.usage.start()  # sends the anonymous usage counts, while they are allowed (app/usage.py)
    print(f"Sedjem is running at {url}  (data: {cfg.storage}; stop with Ctrl+C)", flush=True)
    if cfg.server.get("open_browser", True) and not args.no_browser and not restarted:
        threading.Timer(0.6, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.runner.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
