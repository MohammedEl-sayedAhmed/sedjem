"""Tests for the desktop app's identity on Linux: the application-menu entry that gives the window its icon,
and the window class of the browser window; and how it picks its port when started again. No display is needed.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import ast
import io
import json
import os
import re
import socket
import sys
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import desktop  # noqa: E402
from app.session import Session  # noqa: E402


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux desktop entries")
class LauncherTests(unittest.TestCase):
    def test_entry_names_the_window_class_and_icon_and_is_kept_current(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_DATA_HOME": d}):
            dest = desktop.install_launcher()
            self.assertEqual(dest, Path(d) / "applications" / "sedjem.desktop")
            text = dest.read_text(encoding="utf-8")
            self.assertIn(f"StartupWMClass={desktop.WM_CLASS}\n", text)
            self.assertIn(f"Icon={ROOT / 'app' / 'static' / 'icon-512.png'}\n", text)
            self.assertIn("-m app.desktop", text)
            dest.write_text("[Desktop Entry]\nName=old\n", encoding="utf-8")  # an older version is replaced
            desktop.install_launcher()
            self.assertEqual(dest.read_text(encoding="utf-8"), text)

    def test_browser_window_gets_the_class(self):
        with mock.patch.object(desktop, "browser_candidates", return_value=["/usr/bin/chromium"]), \
                mock.patch.object(desktop.subprocess, "Popen") as popen:
            desktop.app_mode_browser("http://127.0.0.1:8765/", "/tmp/profile")
        args = popen.call_args.args[0]
        self.assertIn(f"--class={desktop.WM_CLASS}", args)
        self.assertIn("--app=http://127.0.0.1:8765/", args)

    def test_on_wayland_the_window_goes_through_xwayland_to_keep_its_class(self):
        def launched(env):
            with mock.patch.object(desktop, "browser_candidates", return_value=["/usr/bin/chromium"]), \
                    mock.patch.object(desktop.subprocess, "Popen") as popen, mock.patch.dict(desktop.os.environ, env, clear=True), \
                    mock.patch.object(desktop.sys, "platform", "linux"):
                desktop.app_mode_browser("http://127.0.0.1:8765/", "/tmp/profile")
            return popen.call_args.args[0]
        self.assertIn("--ozone-platform=x11", launched({"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"}))
        self.assertNotIn("--ozone-platform=x11", launched({"DISPLAY": ":0"}), "plain X11 needs nothing more")
        self.assertNotIn("--ozone-platform=x11", launched({"WAYLAND_DISPLAY": "wayland-0"}), "no XWayland: stay on Wayland")


class PortTests(unittest.TestCase):
    """Starting the app again: it takes back its port from a copy that was just closed, and finds a copy
    that is still running even on another port."""

    def free_port(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def test_waits_for_the_port_of_a_copy_that_is_closing(self):
        port = self.free_port()
        held = socket.socket()
        held.bind(("127.0.0.1", port))
        held.listen()
        threading.Timer(0.5, held.close).start()
        self.assertEqual(desktop.pick_port(port, wait=5), port)

    def test_takes_another_port_if_the_port_stays_taken(self):
        port = self.free_port()
        with socket.socket() as held:
            held.bind(("127.0.0.1", port))
            held.listen()
            other = desktop.pick_port(port, wait=0.3)
        self.assertNotEqual(other, port)
        self.assertGreater(other, 0)

    def test_finds_a_running_copy_on_the_port_it_noted(self):
        proves = []  # the secret the stand-in proves, if any (a Sedjem of this user's own)

        class Status(BaseHTTPRequestHandler):
            def do_GET(self):
                answer = {"app": "sedjem"}
                nonce = parse_qs(urlsplit(self.path).query).get("nonce", [""])[0]
                if proves:
                    answer["proof"] = proves[0].proof(nonce, self.server.server_address[1])
                body = json.dumps(answer).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Status)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as d:
                cfg = types.SimpleNamespace(storage=Path(d))
                unused = self.free_port()
                self.assertIsNone(desktop.running_port(cfg, unused))
                (Path(d) / "port").write_text(str(server.server_address[1]), encoding="utf-8")
                self.assertIsNone(desktop.running_port(cfg, unused), "one that can't prove the secret is not ours")
                proves.append(Session(cfg.storage))
                self.assertEqual(desktop.running_port(cfg, unused), server.server_address[1])
                (Path(d) / "port").write_text("not a port", encoding="utf-8")
                self.assertIsNone(desktop.running_port(cfg, unused))
        finally:
            server.shutdown()
            server.server_close()


class LocalOnlyTests(unittest.TestCase):
    """The app has no login, so its server listens on this computer only."""

    def test_another_host_stops_the_start_and_says_why(self):
        from app.config import Config
        with tempfile.TemporaryDirectory() as d:
            storage = Path(d) / "app_data"
            storage.mkdir()
            (storage / "config.toml").write_text('[server]\nhost = "0.0.0.0"\n', encoding="utf-8")
            cfg = Config(home=d)
            with mock.patch.object(desktop, "Config", return_value=cfg), \
                    mock.patch.object(desktop, "install_launcher"), \
                    mock.patch.object(desktop, "running_port", return_value=None), \
                    mock.patch.object(desktop, "pick_port", return_value=0), \
                    mock.patch.object(desktop, "native_window") as window, \
                    mock.patch.object(desktop, "show_error") as shown, \
                    self.assertRaises(SystemExit) as stopped:
                desktop.main([])
        self.assertEqual(stopped.exception.code, 1)
        self.assertIn('host is "0.0.0.0"', shown.call_args.args[0])
        window.assert_not_called()

    def test_the_reason_goes_to_the_console_or_the_log(self):
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            desktop.show_error("the reason")
        self.assertEqual(err.getvalue(), "the reason\n")


class BuildTests(unittest.TestCase):
    def test_the_build_names_each_module_the_app_reads_from_the_top_folder(self):
        """The app imports a few modules from the repository's top folder, such as sysinfo.py and style_hint.py. The
        desktop build (desktop/build.py) names each one, as it names the model process's modules."""
        named = set(re.findall(r'"--hidden-import", "([\w.]+)"', (ROOT / "desktop" / "build.py").read_text(encoding="utf-8")))
        top = {p.stem for p in ROOT.glob("*.py")}
        used = set()
        for path in (ROOT / "app").glob("*.py"):
            for node in ast.parse(path.read_text(encoding="utf-8")).body:  # the imports at the top of each file
                if isinstance(node, ast.Import):
                    used |= {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    used.add(node.module.split(".")[0])
        self.assertIn("style_hint", used & top)
        self.assertLessEqual(used & top, named)


class IconTests(unittest.TestCase):
    def test_window_icon_exists(self):
        icon = desktop.app_icon()
        if os.name == "nt" and icon is None:
            self.skipTest("no desktop/sedjem.ico in this checkout")
        self.assertTrue(Path(icon).is_file(), icon)


if __name__ == "__main__":
    unittest.main()
