"""Tests for the desktop app's identity on Linux: the application-menu entry that gives the window its icon,
and the window class of the browser window; and how it picks its port when started again. No display is needed.
Run: .venv/bin/python -m unittest discover -s tests -v
"""
import json
import os
import socket
import sys
import tempfile
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import desktop  # noqa: E402


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
        class Status(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"app": "sedjem"}).encode()
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
                self.assertEqual(desktop.running_port(cfg, unused), server.server_address[1])
                (Path(d) / "port").write_text("not a port", encoding="utf-8")
                self.assertIsNone(desktop.running_port(cfg, unused))
        finally:
            server.shutdown()
            server.server_close()


class IconTests(unittest.TestCase):
    def test_window_icon_exists(self):
        icon = desktop.app_icon()
        if os.name == "nt" and icon is None:
            self.skipTest("no desktop/sedjem.ico in this checkout")
        self.assertTrue(Path(icon).is_file(), icon)


if __name__ == "__main__":
    unittest.main()
