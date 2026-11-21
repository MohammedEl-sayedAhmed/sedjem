"""The secret between the app's local server and its own window (app/server.py checks it on every /api request).

The server listens on this computer only, but any program on the computer can connect to it, including one that
another user account runs. So every API request must carry a secret that only this user's own window has:

- the page sends it in the X-Sedjem-Token header. It keeps it in sessionStorage, which belongs to one tab and one
  origin, port included, and ends with the tab; a cookie would also go to any other server on 127.0.0.1, whatever its
  port. Exports are fetched with the header; audio and video, which a media element loads by its address, carry
  ?sig=, a signature that is good for that job's media only (sign());
- the desktop window's page gets it from the bridge (app/desktop.py, Api.session);
- a browser tab the app opens loads <storage>/open-sedjem.html, a page only this user can read, which posts the
  secret to /api/login; the answer, from the app's own origin, stores it and goes on to the app. So the secret is
  never in a command line or an address the app opens;
- tests and scripts send the header, and so does the MCP server when an assistant starts, follows or cancels a
  transcription (app/mcp.py, through running()).

Before the app trusts a server on a port as its own copy (starting a second time), the server proves that it knows
the secret for that port without showing it (proof()); a program that took the port first can't, even by asking the
real server on another port.

The server takes a new port and makes a new secret each time it starts, once it has the port (rotate()), and keeps
them in <storage>/port and <storage>/session.key, which only this user can read (on Windows the mode is ignored and
the data folder's own permissions keep them, as they keep secrets.json); a second start reads them from there
(running_port()). So a secret taken from a tab of a copy that has stopped is worth nothing, and after an update
restart the app opens its login page again. A new port also means a new origin: a program that held the port
earlier, while Sedjem wasn't running, may have left a service worker for that origin in the browser, which would see
the login; it can't know the port of the next start. The page's preferences, which localStorage would keep for one
origin only, are kept in the data folder instead (app/server.py, /api/prefs).
"""
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import tempfile
import urllib.request
from pathlib import Path

from .config import replace_file

HEADER = "X-Sedjem-Token"
STORAGE_KEY = "sedjem-token"  # the page's sessionStorage key (app/static/app.js)
KEY_FILE = "session.key"
PORT_FILE = "port"  # the port of the copy that is running on this data folder, for a second start
LOGIN_FILE = "open-sedjem.html"
HEX64 = re.compile(r"[0-9a-f]{64}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a redirect is answered as the error it is: the secret is never sent on to another address


# Requests to the app on this computer: never through a proxy that the environment names (http_proxy would get the
# secret, and 127.0.0.1 would mean the proxy's own computer), and never on to where a redirect points
LOCAL = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)


def write_private(path, text):
    """Write text to path through a temporary file that only this user can read, then replace path with it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")  # a new file, mode 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        replace_file(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def running(storage, timeout=2):
    """(port, secret) of this user's Sedjem that is running on this data folder, for another program of the user's
    own that uses its API, such as the MCP server (app/mcp.py); None when none is. It reads <storage>/port and
    <storage>/session.key, writes nothing, and gives the secret only once the server on that port has proved that it
    knows it (Session.answers): a program that took the port never gets the secret."""
    storage = Path(storage)
    try:
        port = int((storage / PORT_FILE).read_text(encoding="utf-8"))
        secret = (storage / KEY_FILE).read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None
    if not 0 < port < 65536 or not HEX64.fullmatch(secret) or not Session(storage, secret).answers(port, timeout):
        return None
    return port, secret


def running_port(storage, *ports):
    """The port of this user's Sedjem that is already running on this data folder: one of ports, or the one it noted
    in <storage>/port, and only if it proves that it knows the secret in <storage>/session.key. None if none is."""
    try:
        ports += (int((Path(storage) / PORT_FILE).read_text(encoding="utf-8")),)
    except (OSError, ValueError):
        pass
    session = Session(storage)
    return next((p for p in dict.fromkeys(ports) if p and 0 < p < 65536 and session.answers(p)), None)


class Session:
    def __init__(self, storage, secret=None):
        """The secret in use on this data folder, read from <storage>/session.key (or a new one if there is none).
        Reading it never changes it, so a second start leaves the copy that is running alone. secret: one already
        read (running()), so nothing is read or made."""
        self.storage = Path(storage)
        self.path = self.storage / KEY_FILE
        self.secret = secret if secret is not None else self._load()

    def rotate(self, port):
        """The server, once it has its port: a new secret, and the port noted for a second start."""
        self.secret = self._new()
        write_private(self.storage / PORT_FILE, f"{int(port)}\n")

    def stopped(self, port):
        """The server on port has stopped: forget the port, unless another copy has noted its own since."""
        try:
            if int((self.storage / PORT_FILE).read_text(encoding="utf-8")) == int(port):
                (self.storage / PORT_FILE).unlink()
        except (OSError, ValueError):
            pass

    def _load(self):
        """The saved secret, or a new one if there is none or it isn't one."""
        try:
            saved = self.path.read_text(encoding="utf-8").strip()
            if HEX64.fullmatch(saved):
                return saved
        except OSError:
            pass
        return self._new()

    def _new(self):
        """A new secret, 64 hex digits, saved where only this user can read it."""
        secret = secrets.token_hex(32)
        write_private(self.path, secret + "\n")
        return secret

    def ok(self, value):
        """Whether value is the secret, compared in constant time."""
        return isinstance(value, str) and hmac.compare_digest(value.encode(), self.secret.encode())

    def allows(self, headers):
        """Whether a request carries the secret in the header."""
        return self.ok(headers.get(HEADER))

    def sign(self, jid, kind="media"):
        """The ?sig= for a job's audio and video, which a media element loads by its address: an HMAC of the job and
        the kind, so it is good for that job's media only, and it shows nothing of the secret."""
        return hmac.new(self.secret.encode(), f"sedjem-{kind}:{jid}".encode(), hashlib.sha256).hexdigest()

    def signed(self, value, jid, kind="media"):
        """Whether value is the signature for that job's media, compared in constant time."""
        return isinstance(value, str) and hmac.compare_digest(value.encode(), self.sign(jid, kind).encode())

    def proof(self, nonce, port):
        """The answer of the server on port to a client's random nonce: an HMAC of both, keyed with the secret. It
        shows nothing of the secret, and it is good for that port only."""
        message = f"sedjem-ping:{int(port)}:{nonce}".encode()
        return hmac.new(self.secret.encode(), message, hashlib.sha256).hexdigest()

    def answers(self, port, timeout=2):
        """Whether this app's own copy, with this secret, answers on port: /api/ping with a new nonce, and a check of
        the proof. A program that only took the port, or passes the nonce on to Sedjem on another port, fails."""
        nonce = secrets.token_hex(16)
        try:
            with LOCAL.open(f"http://127.0.0.1:{int(port)}/api/ping?nonce={nonce}", timeout=timeout) as r:
                found = json.load(r)
        except (OSError, ValueError):
            return False
        if not isinstance(found, dict):
            return False
        proof = found.get("proof")
        return (found.get("app") == "sedjem" and isinstance(proof, str) and HEX64.fullmatch(proof) is not None
                and hmac.compare_digest(proof.encode(), self.proof(nonce, port).encode()))

    def login_page(self, port):
        """Write <storage>/open-sedjem.html, a page only this user can read that posts the secret to the server on
        this port, and return its path, for a browser to open."""
        action = html.escape(f"http://127.0.0.1:{int(port)}/api/login")
        page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Opening Sedjem</title>
<meta name="referrer" content="no-referrer"></head>
<body><p>Opening Sedjem…</p>
<form id="open" method="post" action="{action}">
<input type="hidden" name="secret" value="{html.escape(self.secret)}">
<noscript><button type="submit">Open Sedjem</button></noscript>
</form>
<script>document.getElementById("open").submit();</script>
</body></html>
"""
        path = self.storage / LOGIN_FILE
        write_private(path, page)
        return path

    def landing_page(self):
        """What /api/login answers, from the app's own origin: store the secret for this tab, then go to the app."""
        value = json.dumps(self.secret)
        key = json.dumps(STORAGE_KEY)
        return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Sedjem</title><meta name="referrer" content="no-referrer"></head>
<body><script>try {{ sessionStorage.setItem({key}, {value}); }} catch (e) {{}}
location.replace("/");</script></body></html>
"""
