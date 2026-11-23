"""Tests for the secret between the app's local server and its own window (app/session.py): the key file that only
the user can read and a new secret and port at each start, a second start that finds the copy running, the
X-Sedjem-Token header, the media signature, /api/login and the page it answers with, /api/ping and its proof, the
page's preferences, the routes that need no secret, and every other /api route refusing a request without it, as
another program or user account on the computer would send. A program that took the app's port first must not be
sent the secret.
Run: .venv/bin/python -m unittest tests.test_session -v
"""
import http.client
import io
import json
import os
import re
import shutil
import socket
import stat
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import assistants, desktop, server, session as sessions  # noqa: E402
from app import __main__ as cli  # noqa: E402
from app.config import Config  # noqa: E402
from app.session import HEADER, LOGIN_FILE, STORAGE_KEY, Session, write_private  # noqa: E402

POSIX = os.name != "nt"  # file modes are ignored on Windows, where the data folder is in the user's own profile
# A value for each kind of group in the route table, so that every route can be called
SAMPLES = ((server.ID, "20260101-000000-abcd"), (r"(hf-[\w-]+)", "hf-x"), (r"([\w-]+)", "x"),
           (r"([0-9a-f]{12})", "0" * 12), (r"(\w+)", "txt"), (r"(\d+)", "0"))


def concrete(pattern):
    """A path that the route's pattern matches."""
    path = pattern
    for group, sample in SAMPLES:
        path = path.replace(group, sample)
    return path.replace("\\.", ".")


class Impostor(BaseHTTPRequestHandler):
    """A program that took the app's port first and says it is Sedjem, with answer. relay, when set, is the port of
    the real server, whose answer it passes on."""
    relay = None
    answer = {"app": "sedjem", "proof": "0" * 64}

    def do_GET(self):
        if self.relay:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.relay}{self.path}", timeout=5) as r:
                body = r.read()
        else:
            body = json.dumps(self.answer).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class KeyFile(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="sedjem-session-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def test_new_once_the_server_has_its_port_and_read_by_a_second_start(self):
        first = Session(self.dir)
        first.rotate(40001)  # the server, once it has its port
        self.assertRegex(first.secret, r"^[0-9a-f]{64}$")
        self.assertEqual(Session(self.dir).secret, first.secret)  # a second start reads the one in use...
        self.assertEqual(Session(self.dir).secret, first.secret)  # ...and leaves it alone
        self.assertEqual((self.dir / "port").read_text(encoding="utf-8"), "40001\n")
        again = Session(self.dir)
        again.rotate(40002)  # the server starting again, on a new port
        self.assertNotEqual(again.secret, first.secret)
        self.assertEqual(Session(self.dir).secret, again.secret)
        self.assertFalse(again.ok(first.secret))  # a secret from a copy that stopped is worth nothing
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["port", "session.key"])  # no temporary files
        if POSIX:
            for name in ("port", "session.key"):
                self.assertEqual(stat.S_IMODE(os.stat(self.dir / name).st_mode), 0o600, name)

    def test_a_copy_that_stops_forgets_only_its_own_port(self):
        s = Session(self.dir)
        s.rotate(40001)
        s.stopped(40002)  # another copy's port
        self.assertTrue((self.dir / "port").exists())
        s.stopped(40001)
        self.assertFalse((self.dir / "port").exists())
        s.stopped(40001)  # nothing left to forget

    def test_writers_at_the_same_time_each_finish(self):
        errors = []

        def write(i):
            try:
                write_private(self.dir / "x", f"{i}\n" * 2000)
            except Exception as e:  # noqa: BLE001 (any failure is the finding)
                errors.append(e)

        threads = [threading.Thread(target=write, args=(i,)) for i in range(8)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(errors, [])
        self.assertEqual([p.name for p in self.dir.iterdir()], ["x"])

    def test_a_damaged_key_is_replaced(self):
        (self.dir / "session.key").write_text("not a key\n", encoding="utf-8")
        self.assertRegex(Session(self.dir).secret, r"^[0-9a-f]{64}$")

    def test_a_key_file_others_could_read_is_written_private_again(self):
        if not POSIX:
            self.skipTest("file modes")
        path = self.dir / "session.key"
        path.write_text("x\n", encoding="utf-8")
        os.chmod(path, 0o644)
        Session(self.dir)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_what_lets_a_request_in(self):
        s = Session(self.dir)
        self.assertTrue(s.allows({HEADER: s.secret}))
        for headers in ({}, {HEADER: ""}, {HEADER: "0" * 64}, {"Cookie": f"sedjem={s.secret}"}):
            with self.subTest(headers=headers):
                self.assertFalse(s.allows(headers))
        self.assertFalse(s.ok(None))

    def test_the_media_signature_is_for_one_job(self):
        s = Session(self.dir)
        sig = s.sign("20260101-000000-abcd")
        self.assertTrue(s.signed(sig, "20260101-000000-abcd"))
        self.assertFalse(s.signed(sig, "20260101-000000-abce"))
        self.assertFalse(s.signed(sig, "20260101-000000-abcd", "export"))
        self.assertFalse(s.signed(None, "20260101-000000-abcd"))
        self.assertNotIn(s.secret, sig)

    def test_the_proof_is_for_one_port_and_shows_nothing_of_the_secret(self):
        s = Session(self.dir)
        proof = s.proof("ab" * 16, 8765)
        self.assertRegex(proof, r"^[0-9a-f]{64}$")
        self.assertNotIn(s.secret, proof)
        self.assertNotEqual(proof, s.proof("ab" * 16, 8766))
        self.assertNotEqual(proof, s.proof("cd" * 16, 8765))
        self.assertNotEqual(proof, Session(Path(tempfile.mkdtemp(dir=self.dir))).proof("ab" * 16, 8765))

    def test_the_login_page(self):
        s = Session(self.dir)
        page = s.login_page(8765)
        self.assertEqual(page, self.dir / LOGIN_FILE)
        text = page.read_text(encoding="utf-8")
        self.assertIn('action="http://127.0.0.1:8765/api/login"', text)
        self.assertIn(f'value="{s.secret}"', text)
        self.assertIn('name="referrer" content="no-referrer"', text)
        if POSIX:
            self.assertEqual(stat.S_IMODE(os.stat(page).st_mode), 0o600)

    def test_the_landing_page_stores_the_secret_for_the_page_and_goes_on(self):
        s = Session(self.dir)
        text = s.landing_page()
        self.assertIn(f"sessionStorage.setItem({json.dumps(STORAGE_KEY)}, {json.dumps(s.secret)})", text)
        self.assertNotIn("localStorage", text)  # a port that another program takes later would read it
        self.assertIn('location.replace("/")', text)


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = Path(tempfile.mkdtemp(prefix="sedjem-session-server-"))
        cls.server, cls.app = server.make_server(Config(home=cls.home), port=0)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.home, ignore_errors=True)

    def call(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            c.request(method, path, body, {"Host": f"127.0.0.1:{self.port}", **(headers or {})})
            r = c.getresponse()
            return r.status, dict(r.getheaders()), r.read()
        finally:
            c.close()

    def test_every_api_route_needs_the_secret(self):
        """What another program on the computer sends: the right host and header, but no secret."""
        checked = 0
        for verb, pattern, _fn in server.ROUTES:
            if not pattern.startswith("/api/") or pattern in ("/api/ping", "/api/login"):
                continue
            path = concrete(pattern)
            self.assertIsNotNone(re.fullmatch(pattern, path), (pattern, path))
            with self.subTest(route=f"{verb} {pattern}"):
                status = self.call(verb, path, "{}" if verb != "GET" else None,
                                   {"X-Sedjem": "1", "Content-Type": "application/json"})[0]
                self.assertEqual(status, 401)
            checked += 1
        self.assertGreater(checked, 40)

    def test_what_needs_no_secret(self):
        status, _, body = self.call("GET", "/api/ping")
        self.assertEqual((status, json.loads(body)), (200, {"app": "sedjem"}))
        self.assertEqual(self.call("GET", "/")[0], 200)  # the page itself holds no data
        self.assertEqual(self.call("GET", "/static/app.js")[0], 200)

    def test_no_install_offer(self):
        """An installed web app would open the address without the login page, and it is a way for a page from that
        port to stay in the browser; so there is no manifest and no service worker."""
        self.assertEqual(self.call("GET", "/sw.js")[0], 404)
        self.assertEqual(self.call("GET", "/static/manifest.webmanifest")[0], 404)
        self.assertNotIn(b"manifest", self.call("GET", "/")[2])

    def test_the_page_preferences_are_kept_in_the_data_folder(self):
        auth = {HEADER: self.app.session.secret, "X-Sedjem": "1", "Content-Type": "application/json"}

        def patch(body):
            status, _, raw = self.call("PATCH", "/api/prefs", json.dumps(body), auth)
            return status, json.loads(raw)

        self.addCleanup(patch, {"sedjem-theme": None, "sedjem-sort": None})
        status, kept = patch({"sedjem-theme": "dark", "sedjem-sort": "oldest"})
        self.assertEqual(status, 200)
        self.assertEqual((kept["sedjem-theme"], kept["sedjem-sort"]), ("dark", "oldest"))
        saved = json.loads((self.app.cfg.storage / "page.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["sedjem-theme"], "dark")
        status, _, raw = self.call("GET", "/api/prefs", headers={HEADER: self.app.session.secret})
        self.assertEqual(json.loads(raw)["sedjem-sort"], "oldest")
        self.assertIn(b'<html lang="en" data-theme="dark">', self.call("GET", "/")[2])  # no flash of the other theme
        self.assertNotIn("sedjem-sort", patch({"sedjem-sort": None})[1])  # null forgets it
        for bad in ({"sedjem-token": "x"}, {"sedjem-theme": 1}, {"sedjem-theme": "x" * 1001}, ["sedjem-theme"],
                    {"sedjem-sort": "\ud800"}):  # (a lone surrogate: JSON has it, UTF-8 can't write it)
            with self.subTest(bad=bad):
                self.assertEqual(patch(bad)[0], 400)
        patch({"sedjem-theme": '"><script>alert(1)</script>'})  # kept, as text, but never put into the page
        self.assertIn(b'<html lang="en">', self.call("GET", "/")[2])

    def test_the_port_is_the_apps_alone(self):
        """No other program, another account's included, can bind the port too and take some of its connections: on
        Windows, SO_REUSEADDR would let it (the server opens the port with SO_EXCLUSIVEADDRUSE there)."""
        self.assertEqual(server.Server.allow_reuse_address, os.name != "nt")
        if os.name == "nt":
            self.assertEqual(self.server.socket.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE), 1)
        with socket.socket() as other:
            other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            with self.assertRaises(OSError):
                other.bind(("127.0.0.1", self.port))

    def test_a_length_that_isnt_a_number(self):
        form = {"Origin": "null", "Content-Type": "application/x-www-form-urlencoded", "Content-Length": "abc"}
        self.assertEqual(self.call("POST", "/api/login", "secret=x", form)[0], 400)

    def test_ping_proves_it_knows_the_secret_for_its_port(self):
        nonce = "0123456789abcdef" * 2
        status, _, body = self.call("GET", f"/api/ping?nonce={nonce}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["proof"], self.app.session.proof(nonce, self.port))
        self.assertEqual(self.call("GET", "/api/ping?nonce=not-hex")[0], 400)

    def test_only_the_header_lets_a_request_in(self):
        secret = self.app.session.secret
        self.assertEqual(self.call("GET", "/api/status", headers={HEADER: secret})[0], 200)
        self.assertEqual(self.call("GET", "/api/status", headers={HEADER: "0" * 64})[0], 401)
        self.assertEqual(self.call("GET", f"/api/status?token={secret}")[0], 401)  # never in an address
        self.assertEqual(self.call("GET", "/api/status", headers={"Cookie": f"sedjem={secret}"})[0], 401)

    def test_the_media_signature_opens_that_jobs_audio_and_video_only(self):
        jid, other = "20260101-000000-abcd", "20260101-000000-abce"
        sig = self.app.session.sign(jid)
        for kind in ("audio", "video"):
            with self.subTest(kind=kind):
                self.assertNotEqual(self.call("GET", f"/api/jobs/{jid}/{kind}?sig={sig}")[0], 401)  # 404: no such job
                self.assertEqual(self.call("GET", f"/api/jobs/{other}/{kind}?sig={sig}")[0], 401)
                self.assertEqual(self.call("GET", f"/api/jobs/{jid}/{kind}?sig={'0' * 64}")[0], 401)
        for path in (f"/api/jobs/{jid}", f"/api/jobs/{jid}/export/txt", "/api/status"):
            with self.subTest(path=path):
                self.assertEqual(self.call("GET", f"{path}?sig={sig}")[0], 401)
        self.assertEqual(self.call("DELETE", f"/api/jobs/{jid}/audio?sig={sig}", headers={"X-Sedjem": "1"})[0], 401)

    def test_login_from_the_page_the_app_opens(self):
        """open-sedjem.html is a local file, so its form comes with Origin: null. The answer is the app's own page
        that stores the secret and goes on."""
        status, headers, body = self.call("POST", "/api/login", f"secret={self.app.session.secret}",
                                          {"Content-Type": "application/x-www-form-urlencoded", "Origin": "null"})
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Set-Cookie", headers)
        self.assertEqual(body.decode(), self.app.session.landing_page())

    def test_login_refuses(self):
        form = {"Content-Type": "application/x-www-form-urlencoded"}
        self.assertEqual(self.call("POST", "/api/login", "secret=nope", form)[0], 401)
        self.assertEqual(self.call("POST", "/api/login", json.dumps({"secret": self.app.session.secret}),
                                   {"Content-Type": "application/json"})[0], 415)
        self.assertEqual(self.call("POST", "/api/login", "x" * 5000, form)[0], 413)
        evil = {**form, "Origin": "http://evil.example"}
        self.assertEqual(self.call("POST", "/api/login", f"secret={self.app.session.secret}", evil)[0], 403)
        self.assertEqual(self.call("GET", "/api/login")[0], 404)

    def test_only_login_takes_a_null_origin(self):
        headers = {"Origin": "null", HEADER: self.app.session.secret}
        self.assertEqual(self.call("GET", "/api/status", headers=headers)[0], 403)

    def test_a_second_start_finds_this_copy_by_the_port_it_noted(self):
        storage = self.app.cfg.storage
        self.assertEqual((storage / "port").read_text(encoding="utf-8").strip(), str(self.port))
        self.assertEqual(sessions.running_port(storage), self.port)
        self.assertEqual(sessions.running_port(storage, 0, self.port), self.port)
        self.assertEqual(desktop.running_port(self.app.cfg, 0), self.port)

    def test_starting_again_opens_this_copy_and_leaves_its_secret_alone(self):
        """A second ./app.sh while this copy runs: it opens the login page for this copy, and changes neither the
        secret nor the noted port, so this copy and its windows keep working."""
        storage = self.app.cfg.storage
        before = ((storage / "session.key").read_text(encoding="utf-8"), (storage / "port").read_text(encoding="utf-8"))
        opened = []
        started = AssertionError("a second start must not start a server of its own")
        with mock.patch.object(cli, "Config", return_value=self.app.cfg), \
                mock.patch.object(cli, "make_server", side_effect=started), \
                mock.patch.object(cli, "open_window", side_effect=lambda cfg, login: opened.append(login)), \
                mock.patch.object(sys, "argv", ["app"]), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            cli.main()
        self.assertIn("already running", out.getvalue())
        self.assertEqual(len(opened), 1)
        self.assertIn(f"http://127.0.0.1:{self.port}/api/login", opened[0].read_text(encoding="utf-8"))
        after = ((storage / "session.key").read_text(encoding="utf-8"), (storage / "port").read_text(encoding="utf-8"))
        self.assertEqual(after, before)
        self.assertEqual(self.call("GET", "/api/status", headers={HEADER: self.app.session.secret})[0], 200)

    def test_a_start_that_cant_have_the_port_leaves_the_secret_alone(self):
        key = (self.app.cfg.storage / "session.key").read_text(encoding="utf-8")
        with mock.patch.object(server, "Server", side_effect=OSError("taken")), self.assertRaises(OSError):
            server.make_server(self.app.cfg, port=self.port)
        self.assertEqual((self.app.cfg.storage / "session.key").read_text(encoding="utf-8"), key)

    def test_a_program_that_took_the_port_is_not_trusted(self):
        """It says it is Sedjem, passes the real server's proof on, or answers what isn't a proof: a second start must
        not send it the secret, and must not fail on it."""
        session = Session(self.app.cfg.storage)
        cases = {"says it is Sedjem": {}, "passes the real proof on": {"relay": self.port},
                 "a proof that isn't ASCII": {"answer": {"app": "sedjem", "proof": "\u00e9" * 64}},
                 "a proof that isn't text": {"answer": {"app": "sedjem", "proof": 1}},
                 "an answer that isn't an object": {"answer": ["sedjem"]}}
        for name, attrs in cases.items():
            fake = ThreadingHTTPServer(("127.0.0.1", 0), type("Handler", (Impostor,), attrs))
            threading.Thread(target=fake.serve_forever, daemon=True).start()
            other = Path(tempfile.mkdtemp(prefix="sedjem-session-other-"))  # the same secret, the port noted is the fake's
            try:
                with self.subTest(name):
                    self.assertFalse(session.answers(fake.server_address[1]))
                    shutil.copy(self.app.cfg.storage / "session.key", other / "session.key")
                    (other / "port").write_text(str(fake.server_address[1]), encoding="utf-8")
                    self.assertIsNone(sessions.running_port(other))
            finally:
                fake.shutdown()
                fake.server_close()
                shutil.rmtree(other, ignore_errors=True)


class Desktop(unittest.TestCase):
    def test_app_sh_opens_the_login_page_in_sedjems_own_profile_first(self):
        home = Path(tempfile.mkdtemp(prefix="sedjem-session-window-"))
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        cfg, login = mock.Mock(storage=home), home / "open-sedjem.html"
        with mock.patch.object(desktop, "app_mode_browser", return_value=object()) as own, \
                mock.patch.object(cli.webbrowser, "open") as default:
            self.assertTrue(cli.open_window(cfg, login))
        own.assert_called_once_with(login.as_uri(), home / "browser-profile")
        default.assert_not_called()
        with mock.patch.object(desktop, "app_mode_browser", return_value=None), \
                mock.patch.object(cli.webbrowser, "open", return_value=True) as default:
            self.assertTrue(cli.open_window(cfg, login))  # no Chromium-family browser: the default one
        default.assert_called_once_with(login.as_uri())

    def test_port_0_is_a_new_free_port_at_each_start(self):
        self.assertEqual(desktop.pick_port(0), 0)
        self.assertEqual(Config().server["port"], 0)

    def test_the_window_of_a_second_start_has_only_the_secret_and_links(self):
        api = desktop.SessionApi("s" * 64)
        self.assertEqual(api.session(), "s" * 64)
        self.assertTrue(callable(api.open_url))
        self.assertFalse(hasattr(api, "pick_files") or hasattr(api, "save_export") or hasattr(api, "pick_folder"))  # the page uses the browser's

    def test_the_desktop_window_picks_a_folder_for_assistants(self):
        """Settings → Assistants in the desktop window adds a folder with the system's folder picker."""
        api = desktop.Api(mock.Mock())
        api._window, api._dialog = mock.Mock(), lambda kind: kind
        api._window.create_file_dialog.return_value = ["/home/you/Recordings"]
        self.assertEqual(api.pick_folder(), "/home/you/Recordings")
        self.assertEqual(api._window.create_file_dialog.call_args.args, ("FOLDER",))
        for nothing in (None, [], ""):
            api._window.create_file_dialog.return_value = nothing
            self.assertIsNone(api.pick_folder(), "none picked")

    def test_the_desktop_window_picks_one_recording_or_several(self):
        """The new transcription page's file picker: one recording, or several at once to make a course
        (app/local.py), each by its path, with the recordings the app takes as its filter."""
        api = desktop.Api(mock.Mock())
        api._window, api._dialog = mock.Mock(), lambda kind: kind
        api._window.create_file_dialog.return_value = ("/home/you/Lectures/Lecture 1.mp3", "/home/you/Lectures/Lecture 2.mp3")
        self.assertEqual(api.pick_files(), ["/home/you/Lectures/Lecture 1.mp3", "/home/you/Lectures/Lecture 2.mp3"])
        call = api._window.create_file_dialog.call_args
        self.assertEqual((call.args, call.kwargs["allow_multiple"]), (("OPEN",), True))
        self.assertEqual(call.kwargs["file_types"], desktop.RECORDINGS)
        self.assertEqual(desktop.RECORDINGS[0], "Recordings (" + ";".join(f"*{t}" for t in assistants.RECORDING_TYPES) + ")",
                         "the one list of the recordings the app takes")
        api._window.create_file_dialog.return_value = ["/home/you/call.m4a"]
        self.assertEqual(api.pick_files(), ["/home/you/call.m4a"])
        api._window.create_file_dialog.return_value = "/home/you/call.m4a"  # (a dialog that gives one path as text)
        self.assertEqual(api.pick_files(), ["/home/you/call.m4a"])
        for nothing in (None, [], (), ""):
            api._window.create_file_dialog.return_value = nothing
            self.assertEqual(api.pick_files(), [], "none picked")

    def test_the_address_shown_names_the_login_page(self):
        with mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            desktop.show_address("http://127.0.0.1:8765/", Path("/home/you/sedjem/app_data/open-sedjem.html"))
        self.assertIn("open-sedjem.html", out.getvalue())


if __name__ == "__main__":
    unittest.main()
