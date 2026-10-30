"""Running transcriptions on another computer on the same network (issue #5): one Sedjem sends a recording to another
that it is paired with, which transcribes it with its own models and graphics card and gives the transcript back.

The serving side, "Run jobs for other computers" in Settings, is off by default. Switched on, it listens on a port of
its own ([remote] port, 8766), apart from the app's server, which stays on 127.0.0.1. It speaks HTTPS only, with a
self-signed certificate made once (remote/cert.pem), and takes connections only from private addresses: RFC 1918,
link-local, IPv6 unique-local and Tailscale's 100.64.0.0/10. A connection from any other address is closed before
anything on it is read.

Pairing. The serving side shows a one-time code of eight letters and digits (40 bits), which works once and for ten
minutes. The sending side connects, reads the certificate, and proves that it knows the code with an HMAC under a key
made (PBKDF2) from the code, the certificate's SHA-256 fingerprint and a random nonce of its own, so nothing about the
key can be worked out in advance. Only then does the serving side, which makes the same key for that attempt, answer,
with a token for that computer alone and its own proof under the same key. A computer in between shows another
certificate, so its key differs: it can't pass the check either way, and the only code-dependent value it sees can't
be guessed from in time. The sending side then keeps the fingerprint and checks it on every connection, before it sends
anything. Five wrong codes void the code, and an address with ten failures (codes or tokens) in ten minutes is refused
for the rest of those ten minutes.

Every request carries the token. The serving side keeps only its SHA-256 (remote/clients.json), compared in constant
time, and runs only what Sedjem itself would: a job with one of its installed models, on an uploaded FLAC file of
limited size, length, rate and channels, through its usual queue, in a folder apart from its own library (remote/jobs,
app/jobs.py Joined). Each computer has a quota of jobs and disk space here, whatever their state, uploads included. It
never reads a path it is sent, and serves nothing but a job's state and transcript, to the computer that sent it, with
no path of this computer in its errors. A job's folder is deleted once its transcript is collected, or a day after it
ends.

The sending side keeps the token with the API keys (secrets.json) and the paired computers in remote/computers.json,
and treats everything that comes back as data: the transcript and the facts of the run are checked and cut to size.
"""
import hashlib
import hmac
import http.client
import ipaddress
import json
import math
import os
import re
import secrets
import shutil
import socket
import socketserver
import ssl
import threading
import time
import urllib.parse
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import sysinfo

from . import engines
from .config import replace_file
from .jobs import ACTIVE, ID_RE, now, write_json

PORT = 8766  # the serving side's port, unless [remote] port says otherwise
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford's base 32: no I, L, O or U, so nothing reads as another
CODE_SECONDS = 600  # a pairing code works for ten minutes,
CODE_TRIES = 5  # and five wrong tries void it
FAILURES, WINDOW = 10, 600  # an address with ten failed codes or tokens in ten minutes is refused
ITERATIONS = 100_000  # PBKDF2 rounds for the pairing key
KEEP_SECONDS = 24 * 3600  # a job never collected is deleted a day after it ended
MAX_UPLOAD = 2 * 1024 ** 3  # 16 kHz mono FLAC of speech is about 50 MB an hour; silence is far smaller, hence:
LONGEST_HOURS = 48  # a recording sent here is decoded only this far, and its rate and channels are capped
MAX_RATE, MAX_CHANNELS = 48000, 2
MAX_JOBS = 10  # one computer's jobs here, whatever their state (until collected or deleted after a day),
CLIENT_BYTES = 3 * MAX_UPLOAD  # and the disk space they and its uploads in flight may take
FREE_FLOOR = 1024 ** 3  # an upload stops when the disk would have less than this left
MAX_CONNECTIONS, MAX_PER_ADDRESS = 32, 4
HANDSHAKE_SECONDS, IDLE_SECONDS = 10, 60
CONNECT_SECONDS = 5  # the sending side: how long a computer has to answer when a job starts
RETRY_SECONDS = 60  # how long it keeps trying a computer that stopped answering during a job
POLL_SECONDS = 1.0
LINES_BATCH = 256 * 1024  # the lines so far come in batches of about this many bytes
RESULT_LONGEST = 32 << 20  # the longest answer with a transcript the sending side reads
NETWORKS = [ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",  # RFC 1918
    "169.254.0.0/16", "fe80::/10",  # link-local
    "fc00::/7",  # IPv6 unique-local (Tailscale's own IPv6 addresses are in fd7a:115c:a1e0::/48)
    "100.64.0.0/10")]  # shared address space, which Tailscale gives its computers
MODEL_ID = re.compile(r"^[\w.-]{1,100}$")
REF = re.compile(r"^[0-9a-f]{12}$")  # a paired computer's id, on either side
FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
STATUSES = ("preparing", "queued", "running", "done", "failed", "cancelled", "interrupted")
# What the serving side tells about a finished run, and what the sending side keeps of it: text facts with their
# longest length, and numbers
TEXT_FACTS = {"engine": 40, "model_file": 200, "gpu_setting": 10, "power": 40, "device": 200, "voiceprint_model": 200,
              "app_version": 40, "detected_language": 20}
NUMBER_FACTS = ("threads", "peak_rss_mb")


class RemoteError(Exception):
    """A job sent to another computer can't go on; the message says why, for the job page."""


class Unreachable(RemoteError):
    """The other computer didn't answer: off, asleep, not on this network, or not serving."""


class Lost(RemoteError):
    """The other computer stopped answering after it took the job."""


class Denied(Exception):
    """The serving side's answer other than 200: (status, message)."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------------------------
# Addresses, names, codes
# ---------------------------------------------------------------------------------------------

def private(ip):
    """Whether an address is on a local network (NETWORKS). An IPv4 address seen through IPv6 (::ffff:192.168.1.5)
    counts as itself, and a link-local IPv6 address may name its interface (fe80::1%eth0)."""
    try:
        ip = ipaddress.ip_address(str(ip).split("%")[0])
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return any(ip.version == n.version and ip in n for n in NETWORKS)


def clean_name(text, default="Computer"):
    """A computer's name as shown: printable, on one line, at most 60 characters."""
    name = " ".join("".join(c for c in str(text or "") if c.isprintable()).split())[:60].strip()
    return name or default


def new_code():
    return "".join(secrets.choice(ALPHABET) for _ in range(8))


def normal_code(text):
    """A code as typed (7kq2-m9xd, with O for 0 or I for 1) as the serving side made it, or None."""
    code = re.sub(r"[\s-]", "", str(text or "")).upper().translate(str.maketrans("OIL", "011"))
    return code if re.fullmatch(f"[{ALPHABET}]{{8}}", code) else None


def shown_code(code):
    return f"{code[:4]}-{code[4:]}"


def pair_key(code, fingerprint, nonce):
    """The pairing key both sides make for one attempt, from the code, the certificate the sending side sees and the
    sending side's nonce: with the nonce in the salt, no table of keys for all the codes can be made in advance."""
    salt = b"sedjem-pair-2:" + bytes.fromhex(fingerprint) + bytes.fromhex(nonce)
    return hashlib.pbkdf2_hmac("sha256", code.encode(), salt, ITERATIONS)


def proof(key, *parts):
    return hmac.new(key, "\n".join(str(p) for p in parts).encode(), hashlib.sha256).hexdigest()


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def shown_fingerprint(fingerprint):
    """AB:CD:EF:..., as certificates' fingerprints are usually written."""
    return ":".join(fingerprint[i:i + 2] for i in range(0, len(fingerprint), 2)).upper()


def parse_address(text):
    """(host, port) from what was typed on the sending side: 192.168.1.20, 192.168.1.20:8766, [fd7a::1]:8766, or a name
    (office-pc.local, office-pc.tail1234.ts.net). Raises RemoteError."""
    text = re.sub(r"^https?://", "", str(text or "").strip(), flags=re.I).rstrip("/")
    host, port = text, None
    m = re.fullmatch(r"\[([^\]]+)\](?::(\d{1,5}))?", text)
    if m:
        host, port = m.group(1), m.group(2)
    elif text.count(":") == 1:
        host, port = text.split(":")
    try:
        ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", host):
            raise RemoteError("Give the other computer's address, such as 192.168.1.20 or its Tailscale name")
    if port is not None and not port.isdigit():
        raise RemoteError("A port is a number from 1 to 65535")
    port = int(port) if port else PORT
    if not 1 <= port <= 65535:
        raise RemoteError("A port is a number from 1 to 65535")
    return host, port


def shown_address(host, port):
    host = f"[{host}]" if ":" in host else host
    return host if port == PORT else f"{host}:{port}"


def own_addresses():
    """This computer's private addresses, to show on the serving side. Connecting a UDP socket only picks the route:
    no packet is sent."""
    found = set()
    for target in ("10.255.255.255", "192.168.255.255", "100.100.100.100"):  # the last one: Tailscale's own address
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((target, 9))
                found.add(s.getsockname()[0])
        except OSError:
            pass
    try:
        found.update(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None))
    except OSError:
        pass
    order = lambda a: (ipaddress.ip_address(a.split("%")[0]) in NETWORKS[-1] if ":" not in a else True, ":" in a, a)
    return sorted((a for a in found if private(a) and "%" not in a), key=order)


def certificate(folder):
    """The serving side's certificate: (certificate file, key file, SHA-256 fingerprint of the certificate), made once
    with a new P-256 key. The sending side pins the fingerprint, so the certificate needs no authority and no name."""
    folder = Path(folder)
    cert, key = folder / "cert.pem", folder / "key.pem"
    if not (cert.is_file() and key.is_file()):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        folder.mkdir(parents=True, exist_ok=True)
        private_key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Sedjem")])
        start = datetime.now().astimezone()
        made = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(private_key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(start)
                .not_valid_after(start.replace(year=start.year + 20))
                .sign(private_key, hashes.SHA256()))
        tmp = key.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # as secrets.json (app/config.py save_key)
        with os.fdopen(fd, "wb") as f:
            f.write(private_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                              serialization.NoEncryption()))
        replace_file(tmp, key)
        tmp = cert.with_suffix(".tmp")
        tmp.write_bytes(made.public_bytes(serialization.Encoding.PEM))
        replace_file(tmp, cert)
    der = ssl.PEM_cert_to_DER_cert(cert.read_text(encoding="ascii"))
    return cert, key, hashlib.sha256(der).hexdigest()


def streaminfo_problem(head):
    """Why the start of an upload isn't a FLAC recording this side takes, or None. FLAC starts with "fLaC" and its
    STREAMINFO block: the rate, the channels and the number of samples (0 when not known; the decoding stops at
    LONGEST_HOURS anyway, as a header can say anything)."""
    if len(head) < 42 or head[:4] != b"fLaC" or head[4] & 0x7F != 0 or int.from_bytes(head[5:8], "big") != 34:
        return "the recording isn't FLAC"
    rate = (head[18] << 12) | (head[19] << 4) | (head[20] >> 4)
    channels = ((head[20] >> 1) & 7) + 1
    samples = ((head[21] & 0x0F) << 32) | int.from_bytes(head[22:26], "big")
    if not 0 < rate <= MAX_RATE or channels > MAX_CHANNELS:
        return f"the recording has to be at most {MAX_RATE // 1000} kHz and {MAX_CHANNELS} channels"
    if samples / rate > LONGEST_HOURS * 3600:
        return f"the recording is longer than the {LONGEST_HOURS} hours that are accepted"
    return None


def served_error(job):
    """What the serving side says of a job of another computer's that didn't finish, in fixed words: its own error can
    name this computer's paths."""
    return {"failed": "the model failed there" if job.get("started") else
            f"the recording couldn't be read there, or it was longer than {LONGEST_HOURS} hours",
            "interrupted": "Sedjem was closed there while it ran", "cancelled": "it was cancelled there"}.get(job.get("status"))


def folder_size(folder):
    try:
        return sum(f.stat().st_size for f in Path(folder).rglob("*") if f.is_file())
    except OSError:
        return 0


def read_file(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_private(path, data):
    """A JSON file only this user can read (on Windows the data folder is per-user)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:6]}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    replace_file(tmp, path)


# ---------------------------------------------------------------------------------------------
# What comes back from another computer: data, checked and cut to size
# ---------------------------------------------------------------------------------------------

def text(value, longest):
    return " ".join(value.split())[:longest] if isinstance(value, str) else None


def number(value):
    try:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
    except OverflowError:  # an integer too large for a float (10**400)
        return None
    return value if ok else None


def clean_lines(raw, longest=200_000):
    """A transcript's lines from another computer, as the app keeps them; raises RemoteError if they aren't lines."""
    if not isinstance(raw, list) or len(raw) > longest:
        raise RemoteError("the other computer sent a transcript Sedjem can't read")
    out = []
    for x in raw:
        start, end = (number(x.get(k)) if isinstance(x, dict) else None for k in ("start", "end"))
        speaker = x.get("speaker") if isinstance(x, dict) else None
        if start is None or end is None or not isinstance(x.get("text"), str) \
                or not (speaker is None or isinstance(speaker, str) and re.fullmatch(r"\d{1,3}", speaker)):
            raise RemoteError("the other computer sent a transcript Sedjem can't read")
        if x["text"].strip():
            out.append({"start": float(start), "end": float(max(start, end)), "speaker": speaker,
                        "text": x["text"].strip()[:20000]})
    return out


def clean_machine(raw):
    """The computer a job ran on (sysinfo.machine's keys), or None."""
    if not isinstance(raw, dict):
        return None
    out = {}
    for k in sysinfo.MACHINE_KEYS:
        v = raw.get(k)
        if k == "gpus":
            out[k] = [t for t in (text(g, 200) for g in v[:8]) if t] if isinstance(v, list) else None
        else:
            out[k] = number(v) if k in ("threads", "ram_gb") else text(v, 200)
    return out


def clean_facts(raw):
    """The facts of a run on another computer, as the job keeps them (report.run_facts and the model's own)."""
    raw = raw if isinstance(raw, dict) else {}
    facts = {k: text(raw.get(k), n) for k, n in TEXT_FACTS.items()}
    facts.update({k: number(raw.get(k)) for k in NUMBER_FACTS})
    facts["machine"] = clean_machine(raw.get("machine"))
    return {k: v for k, v in facts.items() if v is not None}


def clean_hello(raw):
    """What a paired computer says about itself: its name, version, computer, speaker labels, how many jobs it has on,
    and the local models installed there."""
    if not isinstance(raw, dict) or raw.get("app") != "sedjem":
        raise RemoteError("the other computer's answer isn't Sedjem's")
    models = []
    for m in raw.get("models")[:100] if isinstance(raw.get("models"), list) else []:
        if not isinstance(m, dict) or not isinstance(m.get("id"), str) or not MODEL_ID.fullmatch(m["id"]):
            continue
        speed = m.get("speed") if isinstance(m.get("speed"), dict) else {}
        facts = m.get("facts") if isinstance(m.get("facts"), list) else []
        models.append({"id": m["id"], "title": text(m.get("title"), 120) or m["id"], "engine": text(m.get("engine"), 40),
                       "tagline": text(m.get("tagline"), 200), "maker": text(m.get("maker"), 120),
                       "facts": [t for t in (text(f, 200) for f in facts[:8]) if t], "hub": m.get("hub") is True,
                       "prompt": m.get("prompt") is True, "prompt_hint": text(m.get("prompt_hint"), 400),
                       "speakers_hint": text(m.get("speakers_hint"), 400),
                       "speed": {"rtf": number(speed.get("rtf")), "measured": speed.get("measured") is True,
                                 "runs_on": speed.get("runs_on") if speed.get("runs_on") in ("gpu", "cpu") else None}})
    speakers = raw.get("speakers") if isinstance(raw.get("speakers"), dict) else {}
    busy = number(raw.get("busy"))
    return {"name": clean_name(raw.get("name")), "version": text(raw.get("version"), 40), "models": models,
            "speakers": {"ready": speakers.get("ready") is True, "model": text(speakers.get("model"), 120)},
            "machine": clean_machine(raw.get("machine")), "busy": int(min(busy, 1000)) if busy is not None else 0}


def clean_state(raw):
    """A job's state on the other computer."""
    if not isinstance(raw, dict) or raw.get("status") not in STATUSES:
        raise RemoteError("the other computer sent a job state Sedjem can't read")
    stage = raw.get("stage") if isinstance(raw.get("stage"), str) and re.fullmatch(r"[a-z-]{1,40}", raw["stage"]) else None
    count = number(raw.get("count"))
    return {"status": raw["status"], "stage": stage, "done": number(raw.get("done")), "total": number(raw.get("total")),
            "elapsed": number(raw.get("elapsed")), "error": text(raw.get("error"), 2000) if raw.get("error") else None,
            "lines": raw.get("lines"), "count": int(count) if isinstance(count, int) else None}


# ---------------------------------------------------------------------------------------------
# The sending side: talking to a paired computer
# ---------------------------------------------------------------------------------------------

class CertificateChanged(RemoteError):
    pass


def client_context():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.check_hostname = False  # the certificate is self-signed and names no host:
    ctx.verify_mode = ssl.CERT_NONE  # its fingerprint is checked instead, before anything is sent
    return ctx


class Connection(http.client.HTTPSConnection):
    """HTTPS to one address of a paired computer. Its certificate's fingerprint is compared with the one pinned at
    pairing as soon as the connection is made, before a request goes out. Only pairing, which says so, records the
    fingerprint without a check: anything else without a valid pinned one is refused."""

    def __init__(self, ip, port, pinned, timeout, pairing=False):
        if not pairing and not (isinstance(pinned, str) and FINGERPRINT.fullmatch(pinned)):
            raise RemoteError("this computer has no certificate pinned for that one: pair with it again")
        super().__init__(ip, port, timeout=timeout, context=client_context())
        self.pinned, self.pairing, self.seen = pinned, pairing, None

    def connect(self):
        super().connect()
        self.seen = hashlib.sha256(self.sock.getpeercert(binary_form=True)).hexdigest()
        if not self.pairing and not hmac.compare_digest(self.seen, self.pinned):
            self.close()
            raise CertificateChanged("the computer at that address isn't the one paired with: its certificate is "
                                     "another one, so nothing was sent. If it was set up again, pair with it again")


def resolve(host, port):
    """The private addresses a computer's name or address stands for. The recording only goes to the local network,
    so a name that points elsewhere is refused (RemoteError), and one not found is Unreachable."""
    try:
        found = [info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]
    except (OSError, UnicodeError):
        raise Unreachable(f"{host} wasn't found on the network")
    usable = [a for a in dict.fromkeys(found) if private(a)]
    if not usable:
        raise RemoteError(f"{host} is at {found[0]}, which isn't a private address on your network, so Sedjem won't "
                          f"send it anything")
    return usable


def connect(host, port, pinned, timeout, pairing=False):
    """A connection made to the first of the computer's private addresses that answers."""
    problem = None
    for ip in resolve(host, port):
        conn = Connection(ip, port, pinned, timeout, pairing)
        try:
            conn.connect()
            return conn
        except CertificateChanged:
            raise
        except (OSError, ssl.SSLError) as e:
            conn.close()
            problem = e
    raise Unreachable(reason(problem))


def reason(e):
    """Why a connection failed, in a few words."""
    if isinstance(e, (TimeoutError, socket.timeout)):
        return "it didn't answer in time"
    if isinstance(e, ConnectionRefusedError):
        return "nothing is listening on its port"
    if isinstance(e, ssl.SSLError):
        return "the secure connection failed"
    return str(getattr(e, "strerror", None) or e or "the connection failed")


class Peer:
    """A paired computer as the sending side talks to it: every request opens a connection that checks the pinned
    certificate and carries the token."""

    def __init__(self, computer, token):
        self.computer, self.token, self.name = computer, token, computer["name"]

    def request(self, method, path, body=None, timeout=30, longest=1 << 20, connect_timeout=None):
        """(status, the answer as a dict). Raises Unreachable when the connection fails, CertificateChanged, and
        RemoteError for an answer that isn't JSON or is too long."""
        conn = connect(self.computer["address"], self.computer["port"], self.computer["fingerprint"],
                       connect_timeout or timeout)
        try:
            conn.sock.settimeout(timeout)
            headers = {"Authorization": f"Bearer {self.token}", "Connection": "close"}
            if isinstance(body, (dict, list)):
                body, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
            conn.request(method, path, body=body, headers=headers)
            return self.answer(conn, longest)
        except (OSError, http.client.HTTPException) as e:
            raise Unreachable(reason(e))
        finally:
            conn.close()

    def answer(self, conn, longest=1 << 20):
        r = conn.getresponse()
        raw = r.read(longest + 1)
        if len(raw) > longest:
            raise RemoteError("the other computer's answer was too long")
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            raise RemoteError("the other computer's answer isn't Sedjem's")
        if not isinstance(data, dict):
            raise RemoteError("the other computer's answer isn't Sedjem's")
        if r.status == 401:
            raise RemoteError(f"{self.name} no longer takes jobs from this computer: the pairing was removed there. "
                              f"Pair with it again in Settings, Other computers")
        if r.status >= 400:
            raise RemoteError(f"{self.name}: {text(data.get('error'), 300) or f'HTTP {r.status}'}")
        return data

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def upload(self, path, audio, on_sent, cancelled):
        """Send audio.flac, in blocks with progress, and return the answer."""
        size = Path(audio).stat().st_size
        conn = connect(self.computer["address"], self.computer["port"], self.computer["fingerprint"], CONNECT_SECONDS)
        try:
            conn.sock.settimeout(60)
            conn.putrequest("POST", path)
            for k, v in (("Authorization", f"Bearer {self.token}"), ("Content-Type", "audio/flac"),
                         ("Content-Length", str(size)), ("Connection", "close")):
                conn.putheader(k, v)
            conn.endheaders()
            sent = 0
            with open(audio, "rb") as f:
                while chunk := f.read(1 << 20):
                    if cancelled():
                        raise engines.Cancelled()
                    conn.send(chunk)
                    sent += len(chunk)
                    on_sent(sent, size)
            return self.answer(conn)
        except (OSError, http.client.HTTPException) as e:
            raise Unreachable(reason(e))
        finally:
            conn.close()

    def forget(self):
        """Tell it to drop this computer's token; never raises."""
        try:
            self.request("POST", "/v1/unpair", timeout=5, connect_timeout=3)
        except RemoteError:
            pass


def wait(seconds, cancelled):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cancelled():
            raise engines.Cancelled()
        time.sleep(min(0.1, max(0.0, end - time.monotonic())))


# ---------------------------------------------------------------------------------------------
# Both sides, for one app
# ---------------------------------------------------------------------------------------------

class Remote:
    """The computers this one sends jobs to (remote/computers.json, their tokens in secrets.json), and the serving
    side while it is switched on: its listener, the open pairing code, the computers allowed to send here
    (remote/clients.json) and the jobs run for them (app.served)."""

    def __init__(self, app):
        self.app, self.cfg = app, app.cfg
        self.folder = self.cfg.storage / "remote"
        self.lock = threading.RLock()
        self.listener = None  # the HTTPS server, while serving is on
        self.error = None  # why it couldn't listen
        self.code = None  # the open pairing code: {"code", "expires", "tries"}
        self.failures = {}  # address -> times of failed codes and tokens (under failures_lock, which the listener's
        self.failures_lock = threading.Lock()  # thread takes: never self.lock, which apply holds while it stops it)
        self.uploads = {}  # upload in flight -> (computer id, bytes reserved), under self.lock
        self.listed = {}  # computer id -> what it last said about itself (clean_hello), for new jobs
        self.refused = 0  # connections closed for their address, since the start
        self.started = False

    # ---- settings and state ---------------------------------------------------------------------------
    def port(self):
        return int((self.cfg.data.get("remote") or {}).get("port", PORT))

    def serving(self):
        return self.cfg.setting("remote_serve") is True

    def name(self):
        """This computer's name as the other computer shows it: [remote] name, else the computer's own."""
        return clean_name((self.cfg.data.get("remote") or {}).get("name") or socket.gethostname())

    def start(self):
        """At the app's start (its entry points call it): jobs left from before are tidied, the sweep that deletes old
        ones starts, and the serving side listens if it is switched on."""
        if self.started:
            return
        self.started = True
        self.tidy(restarted=True)
        threading.Thread(target=self.sweep, daemon=True, name="sedjem-remote-sweep").start()
        self.apply()

    def apply(self):
        """Listen or stop, as the setting says. Switched off, the jobs being run for other computers stop and are
        deleted, and an open pairing code closes. The listener is stopped after the lock is let go: its thread may be
        waiting for it."""
        stopping = None
        with self.lock:
            if self.serving() and self.listener is None:
                self.listen()
            elif not self.serving() and self.listener is not None:
                stopping, self.listener = self.listener, None
            if not self.serving():
                self.code, self.error = None, None
        if stopping is not None:
            stopping.shutdown()
            stopping.server_close()
            threading.Thread(target=self.drop_served, daemon=True).start()

    def listen(self):
        try:
            cert, key, _ = certificate(self.folder)
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_3
            ctx.load_cert_chain(cert, key)
            listener = Listener(("::" if Listener.address_family == socket.AF_INET6 else "0.0.0.0", self.port()),
                                type("BoundHandler", (Handler,), {"remote": self}), ctx, self)
        except OSError as e:
            self.error = (f"Port {self.port()} is in use by another program: set another one as [remote] port in "
                          f"config.toml" if getattr(e, "errno", None) in (98, 10048) else f"Couldn't listen: {e}")
            return
        self.listener, self.error = listener, None
        threading.Thread(target=listener.serve_forever, daemon=True, name="sedjem-remote").start()

    def stop(self):
        """Stop listening (the app is quitting, or serving was switched off)."""
        with self.lock:
            listener, self.listener = self.listener, None
        if listener is not None:
            listener.shutdown()
            listener.server_close()

    def listening_port(self):
        return self.listener.server_address[1] if self.listener else None

    # ---- the serving side: pairing --------------------------------------------------------------------
    def open_code(self):
        """A new one-time pairing code (the one before stops working)."""
        with self.lock:
            if self.listener is None:
                raise Denied(409, "Turn on Run jobs for other computers first")
            self.code = {"code": new_code(), "expires": time.time() + CODE_SECONDS, "tries": 0}
            return self.code_view()

    def close_code(self):
        with self.lock:
            self.code = None

    def code_view(self):
        code = self.code
        if code is None or time.time() > code["expires"]:
            return None
        return {"code": shown_code(code["code"]), "seconds": round(code["expires"] - time.time()),
                "expires": datetime.fromtimestamp(code["expires"]).astimezone().isoformat(timespec="seconds")}

    def failed(self, ip):
        with self.failures_lock:
            cut = time.time() - WINDOW
            self.failures = {a: [t for t in ts if t > cut] for a, ts in self.failures.items() if any(t > cut for t in ts)}
            self.failures.setdefault(ip, []).append(time.time())

    def blocked(self, ip):
        with self.failures_lock:
            return sum(1 for t in self.failures.get(ip, ()) if t > time.time() - WINDOW) >= FAILURES

    def clients(self):
        found = read_file(self.folder / "clients.json").get("clients")
        return [c for c in found if isinstance(c, dict) and REF.fullmatch(str(c.get("id")))] if isinstance(found, list) else []

    def save_clients(self, clients):
        write_private(self.folder / "clients.json", {"clients": clients})

    def pair_request(self, ip, p):
        """POST /v1/pair: {"name", "nonce", "proof"} from a computer that has the code. Answers {"name", "token",
        "proof"}; the token is for that computer alone."""
        name, nonce, given = clean_name(p.get("name")), str(p.get("nonce") or ""), str(p.get("proof") or "")
        if not (re.fullmatch(r"[0-9a-f]{64}", nonce) and re.fullmatch(r"[0-9a-f]{64}", given)):
            self.failed(ip)
            raise Denied(400, "not a pairing request")
        with self.lock:
            code = self.code
            if code is None or time.time() > code["expires"]:
                self.code = None
                self.failed(ip)
                raise Denied(403, "No pairing code is open on that computer. Show a new one there, in Settings, "
                                  "Other computers")
            _, _, fingerprint = certificate(self.folder)
            key = pair_key(code["code"], fingerprint, nonce)  # for this attempt: at most CODE_TRIES per code
            if not hmac.compare_digest(proof(key, "client", nonce, p.get("name")), given):
                code["tries"] += 1
                if code["tries"] >= CODE_TRIES:
                    self.code = None
                self.failed(ip)
                raise Denied(403, "The code is wrong" + (", and it was tried too often: show a new one there" if
                                                         self.code is None else ". Check it and the address"))
            self.code = None
            token = secrets.token_urlsafe(32)
            client = {"id": uuid.uuid4().hex[:12], "name": name, "token_sha256": digest(token), "paired": now(),
                      "used": None}
            self.save_clients(self.clients() + [client])
            mine = self.name()
            return {"app": "sedjem", "name": mine, "token": token, "proof": proof(key, "server", nonce, token, mine)}

    def client_for(self, header, ip):
        """The paired computer a request's token belongs to; every stored token is compared, in constant time."""
        m = re.fullmatch(r"Bearer ([A-Za-z0-9_-]{20,200})", header or "")
        given = digest(m.group(1)) if m else "0" * 64
        found = None
        for c in self.clients():
            if hmac.compare_digest(str(c.get("token_sha256") or ""), given) and m:
                found = c
        if found is None:
            self.failed(ip)
            raise Denied(401, "this computer isn't paired here")
        return found

    def revoke(self, cid):
        """A computer no longer allowed to send jobs here; its jobs here stop and are deleted."""
        with self.lock:
            clients = self.clients()
            if not any(c["id"] == cid for c in clients):
                raise Denied(404, "no such computer")
            self.save_clients([c for c in clients if c["id"] != cid])
        threading.Thread(target=self.drop_served, args=(cid,), daemon=True).start()

    # ---- the serving side: jobs -----------------------------------------------------------------------
    def hello(self):
        """GET /v1/hello: this computer, its speaker labels, how many jobs it has on, and its installed local models."""
        app, cfg = self.app, self.cfg
        jobs = app.store.list()
        keys = ("id", "title", "engine", "tagline", "maker", "facts", "prompt", "prompt_hint", "speakers_hint", "speed")
        models = []
        for m in cfg.models.values():
            if m["kind"] == "local" and cfg.availability(m)[0]:
                info = app.model_info(m, jobs)
                models.append({**{k: info.get(k) for k in keys}, "hub": bool(info.get("hub"))})
        speaker = cfg.speaker_model()
        busy = sum(1 for j in jobs + app.served.list() if j["status"] in ACTIVE and j.get("kind") == "local")
        return {"app": "sedjem", "name": self.name(), "version": app.updates.running()["version"], "models": models,
                "speakers": {"ready": speaker is not None, "model": speaker and speaker["title"]},
                "machine": sysinfo.machine(app.gpu_names()), "busy": busy}

    def accept(self, client, params, length, body):
        """POST /v1/jobs?model=&speakers=&language=&prompt=: the body is the recording as FLAC. A job is made with one
        of this computer's installed models, in the served store, and queued like the app's own."""
        from .server import ApiError, parse_options  # (it imports this module)
        app, cfg = self.app, self.cfg
        model = cfg.models.get(params.get("model") or "")
        if model is None or model["kind"] != "local" or not cfg.availability(model)[0]:
            raise Denied(400, f"that model isn't installed on {self.name()}")
        try:
            options = parse_options(params, cfg, model)
        except ApiError as e:
            raise Denied(400, str(e))
        if length <= 0:
            raise Denied(400, "empty upload")
        if length > MAX_UPLOAD:
            raise Denied(413, f"the recording is larger than the {MAX_UPLOAD // 1024 ** 3} GB that is accepted")
        head = body.read(42)  # "fLaC" and the STREAMINFO block, which says the rate, the channels and the length
        problem = streaminfo_problem(head)
        if problem:
            raise Denied(400, problem)
        ticket = self.reserve(client, length)
        try:
            job = app.runner.new_job(title=f"From {client['name']}", source_name="audio.flac", model_id=model["id"],
                                     options=options, served={"client": client["id"], "name": client["name"]},
                                     video_skipped="the recording came from another computer")
            try:
                with open(app.served.dir(job["id"]) / "source.flac", "wb") as f:
                    f.write(head)
                    left, written = length - len(head), 0
                    while left > 0:
                        chunk = body.read(min(1 << 20, left))
                        if not chunk:
                            raise Denied(400, "upload interrupted")
                        f.write(chunk)
                        left, written = left - len(chunk), written + len(chunk)
                        if written >= 64 << 20:  # the disk is checked as it fills
                            written = 0
                            if shutil.disk_usage(cfg.storage).free < FREE_FLOOR:
                                raise Denied(507, "the disk there is nearly full")
                with self.lock:  # still allowed: switching off or removing the computer meanwhile drops it
                    if self.listener is None or not any(c["id"] == client["id"] for c in self.clients()):
                        raise Denied(401, "this computer isn't paired here")
                    clients = self.clients()
                    for c in clients:
                        if c["id"] == client["id"]:
                            c["used"] = now()
                    self.save_clients(clients)
                    app.runner.submit(job["id"])
            except BaseException:
                app.served.delete(job["id"])
                raise
        finally:
            with self.lock:
                self.uploads.pop(ticket, None)
        return {"id": job["id"]}

    def reserve(self, client, length):
        """Room for an upload of length bytes: within the computer's quota of jobs and space here (its jobs whatever
        their state, and its uploads in flight), and with the disk keeping FREE_FLOOR free once every upload in flight
        is in. The space is reserved until the upload ends; returns its ticket."""
        need = 2 * length  # the upload, and the audio made from it
        with self.lock:
            mine = [j for j in self.app.served.list() if (j.get("served") or {}).get("client") == client["id"]]
            flying = [b for c, b in self.uploads.values() if c == client["id"]]
            if len(mine) + len(flying) >= MAX_JOBS:
                raise Denied(429, f"this computer already has {MAX_JOBS} jobs there; wait until they are collected")
            held = sum(folder_size(self.app.served.dir(j["id"])) for j in mine) + sum(flying)
            if held + need > CLIENT_BYTES:
                raise Denied(507, f"this computer's jobs there would take more than the "
                                  f"{CLIENT_BYTES // 1024 ** 3} GB it may use")
            reserved = sum(b for _, b in self.uploads.values())
            if shutil.disk_usage(self.cfg.storage).free - reserved - need < FREE_FLOOR:
                raise Denied(507, "not enough free disk space there")
            ticket = uuid.uuid4().hex
            self.uploads[ticket] = (client["id"], need)
            return ticket

    def served_job(self, client, jid):
        job = self.app.served.get(jid)
        if not job or (job.get("served") or {}).get("client") != client["id"]:
            raise Denied(404, "no such job")
        return job

    def state(self, client, jid, after):
        """GET /v1/jobs/<id>: how far it is; with ?after=n also the lines so far from line n, as many as fit in about
        LINES_BATCH bytes, and how many there are ("count"). Errors are said in fixed words: what this computer's own
        error says (its paths, say) stays here."""
        job = self.served_job(client, jid)
        out = {k: job.get(k) for k in ("status", "stage", "done", "total", "elapsed")}
        out["error"] = served_error(job)
        if after is not None and job["status"] == "running":
            lines = engines.partial_lines(self.app.served.dir(jid))
            batch, size = [], 0
            for x in lines[after:]:
                size += len(json.dumps(x, ensure_ascii=False))
                if batch and size > LINES_BATCH:
                    break
                batch.append(x)
            out.update(lines=batch, count=len(lines))
        return out

    def result(self, client, jid):
        """GET /v1/jobs/<id>/result: the transcript and the facts of the run, once it is done."""
        job = self.served_job(client, jid)
        data = self.app.served.transcript(jid)
        if job["status"] != "done" or data is None:
            raise Denied(409, "the job hasn't finished")
        return {"lines": data["lines"], "facts": {k: job.get(k) for k in (*TEXT_FACTS, *NUMBER_FACTS, "machine")}}

    def cancel(self, client, jid):
        self.served_job(client, jid)
        self.app.runner.cancel(jid)
        return {"cancelled": jid}

    def delete(self, client, jid):
        """DELETE /v1/jobs/<id>: the transcript was collected, or the job is no longer wanted: it goes."""
        self.served_job(client, jid)
        self.remove(jid)
        return {"deleted": jid}

    def unpair(self, client):
        with self.lock:
            self.save_clients([c for c in self.clients() if c["id"] != client["id"]])
        threading.Thread(target=self.drop_served, args=(client["id"],), daemon=True).start()
        return {"unpaired": True}

    def remove(self, jid, wait_s=15):
        """Stop a served job if it runs, then delete its folder."""
        runner, served = self.app.runner, self.app.served
        if (served.get(jid) or {}).get("status") in ACTIVE:
            runner.cancel(jid)
            end = time.time() + wait_s
            while (served.get(jid) or {}).get("status") in ACTIVE and time.time() < end:
                time.sleep(0.1)
        served.delete(jid)

    def drop_served(self, cid=None):
        """Delete the jobs run for other computers (one computer's, or all)."""
        for job in self.app.served.list():
            if cid is None or (job.get("served") or {}).get("client") == cid:
                self.remove(job["id"])

    def tidy(self, restarted=False):
        """Delete the served jobs no one collected a day after they ended (or all, while serving is off). After a
        restart, the ones that were waiting or running are marked interrupted: the app doesn't run them again."""
        served = self.app.served
        for job in served.list():
            if restarted and job["status"] in ACTIVE:
                job = served.update(job["id"], status="interrupted", stage=None, finished=now(),
                                    error="Sedjem was closed on this computer while it ran") or job
            if job["status"] in ACTIVE:
                continue
            try:
                ended = datetime.fromisoformat(job.get("finished") or job["created"]).timestamp()
            except (TypeError, ValueError):
                ended = 0
            if not self.serving() or time.time() - ended > KEEP_SECONDS:
                served.delete(job["id"])

    def sweep(self, every=600):
        while True:
            time.sleep(every)
            try:
                self.tidy()
            except Exception:  # noqa: BLE001 — the next sweep tries again
                pass

    # ---- the sending side -----------------------------------------------------------------------------
    def computers(self):
        """The paired computers; an entry without a valid id, address, port and pinned fingerprint is left out."""
        found = read_file(self.folder / "computers.json").get("computers")
        return [c for c in found if isinstance(c, dict) and REF.fullmatch(str(c.get("id")))
                and FINGERPRINT.fullmatch(str(c.get("fingerprint"))) and isinstance(c.get("address"), str)
                and isinstance(c.get("port"), int) and 1 <= c["port"] <= 65535] if isinstance(found, list) else []

    def save_computers(self, computers):
        self.folder.mkdir(parents=True, exist_ok=True)
        write_json(self.folder / "computers.json", {"computers": computers})

    def computer(self, cid):
        found = next((c for c in self.computers() if c["id"] == cid), None)
        if found is None:
            raise RemoteError("This computer is no longer paired with that one")
        return found

    def peer(self, computer):
        token = self.cfg.secrets().get(f"remote:{computer['id']}", "")
        if not token:
            raise RemoteError(f"This computer has no key for {computer['name']}: pair with it again in Settings, "
                              f"Other computers")
        return Peer(computer, token)

    def pair(self, address, code):
        """Pair with the computer at address, which shows code: check each other with the code (see the top), keep
        its fingerprint, and keep the token it gives with the API keys. Returns the paired computer."""
        host, port = parse_address(address)
        code = normal_code(code)
        if code is None:
            raise RemoteError("A pairing code is eight letters and digits, such as 7KQ2-M9XD")
        conn = connect(host, port, None, CONNECT_SECONDS, pairing=True)
        try:
            fingerprint, nonce, mine = conn.seen, secrets.token_hex(32), self.name()
            key = pair_key(code, fingerprint, nonce)
            conn.sock.settimeout(30)
            conn.request("POST", "/v1/pair", body=json.dumps({"name": mine, "nonce": nonce,
                                                              "proof": proof(key, "client", nonce, mine)}).encode(),
                         headers={"Content-Type": "application/json", "Connection": "close"})
            r = conn.getresponse()
            raw = r.read(1 << 16)
        except (OSError, http.client.HTTPException) as e:
            raise Unreachable(reason(e))
        finally:
            conn.close()
        try:
            data = json.loads(raw)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise RemoteError("The computer at that address didn't answer as Sedjem does")
        if r.status != 200:
            raise RemoteError(text(data.get("error"), 300) or f"Pairing was refused (HTTP {r.status})")
        token, name = data.get("token"), data.get("name")
        if not (isinstance(token, str) and re.fullmatch(r"[A-Za-z0-9_-]{20,200}", token) and isinstance(name, str)
                and hmac.compare_digest(proof(key, "server", nonce, token, name), str(data.get("proof") or ""))):
            raise RemoteError("The other computer couldn't show that it knows the code, so it wasn't paired. Another "
                              "computer may be in between: check the address")
        with self.lock:
            computers = self.computers()
            # paired again, or set up again there: one entry, which keeps its id (jobs and the form name it), and the
            # consent given for it while it is the same computer
            same = lambda c: c["fingerprint"] == fingerprint or (c["address"], c["port"]) == (host, port)
            before = [c for c in computers if same(c)]
            for old in before:
                try:
                    self.peer(old).forget()
                except RemoteError:
                    pass
                self.cfg.save_key(f"remote:{old['id']}", "")
            computer = {"id": before[0]["id"] if before else uuid.uuid4().hex[:12], "name": clean_name(name),
                        "address": host, "port": port, "fingerprint": fingerprint, "paired": now(),
                        "consented": any(c["fingerprint"] == fingerprint and c.get("consented") is True for c in before)}
            self.cfg.save_key(f"remote:{computer['id']}", token)
            self.save_computers([c for c in computers if not same(c)] + [computer])
        return computer

    def forget(self, cid):
        """Unpair from a computer: it is told to drop this computer's token (if it answers), and the token goes here."""
        computer = self.computer(cid)
        try:
            self.peer(computer).forget()
        except RemoteError:
            pass
        with self.lock:
            self.save_computers([c for c in self.computers() if c["id"] != cid])
            self.cfg.save_key(f"remote:{cid}", "")
            self.listed.pop(cid, None)

    def consent(self, cid):
        """The recording may go to this computer from now on: the tick on the form was given once."""
        with self.lock:
            computers = self.computers()
            for c in computers:
                if c["id"] == cid:
                    c["consented"] = True
            self.save_computers(computers)

    def ask(self, cid):
        """What a paired computer says about itself now (clean_hello), or {"online": False, "error"}."""
        try:
            computer = self.computer(cid)
            found = clean_hello(self.peer(computer).get("/v1/hello", timeout=15, connect_timeout=CONNECT_SECONDS))
        except RemoteError as e:
            self.listed.pop(cid, None)
            return {"online": False, "error": str(e), "unreachable": isinstance(e, Unreachable)}
        self.listed[cid] = found
        return {"online": True, **found}

    def listed_model(self, cid, model_id):
        """A model as the paired computer last listed it, else None."""
        return next((m for m in (self.listed.get(cid) or {}).get("models", []) if m["id"] == model_id), None)

    def send(self, job, folder, on_progress, cancelled):
        """Run a job on the paired computer it names (job["remote"]), as engines' runners do: on_progress(dict) with
        the stage, and {"lines", "meta"} at the end. Raises Unreachable when that computer doesn't answer at the start
        (nothing was sent), Lost when it stops answering for RETRY_SECONDS once it has the job, engines.Cancelled, and
        RemoteError for anything else. The job there is deleted at the end, however it ends."""
        computer = self.computer(job["remote"]["id"])
        peer, name = self.peer(computer), computer["name"]
        hello = clean_hello(peer.get("/v1/hello", timeout=15, connect_timeout=CONNECT_SECONDS))
        self.listed[computer["id"]] = hello
        if not any(m["id"] == job["model"] for m in hello["models"]):
            raise RemoteError(f"{job.get('model_title') or job['model']} is no longer installed on {name}")
        opts = job.get("options") or {}
        query = urllib.parse.urlencode({"model": job["model"], "speakers": opts.get("speakers", "auto"),
                                        "language": opts.get("language", "ar"), "prompt": opts.get("prompt") or ""})
        audio = Path(folder) / "audio.flac"
        if audio.stat().st_size > MAX_UPLOAD:
            raise RemoteError(f"the recording is larger than the {MAX_UPLOAD // 1024 ** 3} GB that {name} accepts")
        if (job.get("audio_s") or 0) > LONGEST_HOURS * 3600:
            raise RemoteError(f"the recording is longer than the {LONGEST_HOURS} hours that {name} accepts")
        sent = lambda done, total: on_progress({"stage": "sending", "done": done, "total": total})
        rid = self.retrying(lambda: peer.upload(f"/v1/jobs?{query}", audio, sent, cancelled), name, on_progress, cancelled)["id"]
        if not (isinstance(rid, str) and ID_RE.fullmatch(rid)):
            raise RemoteError(f"{name} answered with a job Sedjem can't follow")
        engines_dir = Path(folder) / "engine"
        try:
            asked, have = 0.0, []  # the lines so far, asked for in batches after the ones already here
            while True:
                if cancelled():  # the DELETE below stops it there
                    raise engines.Cancelled()
                with_lines = time.monotonic() - asked > 4
                query = f"?after={len(have)}" if with_lines else ""
                state = clean_state(self.retrying(lambda: peer.get(f"/v1/jobs/{rid}{query}"), name, on_progress,
                                                  cancelled))
                if state["status"] == "done":
                    break
                if state["status"] in ("failed", "cancelled", "interrupted"):
                    raise RemoteError(f"On {name}: {state['error'] or 'the job ' + state['status']}")
                if state["lines"] is not None and state["count"] is not None:
                    try:
                        have = have + clean_lines(state["lines"]) if state["count"] >= len(have) else []
                        engines_dir.mkdir(exist_ok=True)
                        write_json(engines_dir / "remote.json", have)  # engines.partial_lines shows them meanwhile
                    except RemoteError:
                        have = []
                    if len(have) >= state["count"]:  # all of them: the next batch in a few seconds
                        asked = time.monotonic()
                stage = {"preparing": "remote-preparing", "queued": "remote-queued"}.get(state["status"]) \
                    or state["stage"] or "starting"
                on_progress({"stage": stage, "done": state["done"], "total": state["total"], "elapsed": state["elapsed"]})
                wait(POLL_SECONDS, cancelled)
            on_progress({"stage": "collecting"})
            found = self.retrying(lambda: peer.get(f"/v1/jobs/{rid}/result", timeout=120, longest=RESULT_LONGEST),
                                  name, on_progress, cancelled)
            lines = clean_lines(found.get("lines"))
            return {"lines": lines, "meta": clean_facts(found.get("facts"))}
        finally:
            try:
                peer.request("DELETE", f"/v1/jobs/{rid}", timeout=10, connect_timeout=3)
            except RemoteError:
                pass  # deleted there a day after it ended

    def retrying(self, ask, name, on_progress, cancelled):
        """ask(), tried again every two seconds while the computer doesn't answer, for up to RETRY_SECONDS; then Lost.
        Anything else it raises goes through."""
        first = None
        while True:
            try:
                return ask()
            except Unreachable as e:
                first = first or time.monotonic()
                if time.monotonic() - first > RETRY_SECONDS:
                    raise Lost(f"{name} stopped answering while it had this job ({e}). It may have gone to sleep or "
                               f"lost the network. Run it here instead, or try again once {name} is back.")
                on_progress({"stage": "reconnecting"})
                wait(2, cancelled)

    # ---- for the app's API ----------------------------------------------------------------------------
    def summary(self):
        """For /api/status: whether this computer serves, and the computers it can send to."""
        return {"serving": self.serving(),
                "computers": [{k: c.get(k) for k in ("id", "name", "consented")} for c in self.computers()]}

    def view(self):
        """Settings, Other computers: both sides as they are now."""
        port = self.listening_port() or self.port()
        _, _, fingerprint = certificate(self.folder) if self.listener else (None, None, None)
        served = self.app.served.list() if self.listener else []
        clients = [{"id": c["id"], "name": c.get("name"), "paired": c.get("paired"), "used": c.get("used"),
                    "active": sum(1 for j in served if (j.get("served") or {}).get("client") == c["id"]
                                  and j["status"] in ACTIVE)} for c in self.clients()]
        return {"serve": {"on": self.serving(), "listening": self.listener is not None, "error": self.error,
                          "port": port, "name": self.name(), "code": self.code_view(), "clients": clients,
                          "addresses": [shown_address(a, port) for a in own_addresses()] if self.listener else [],
                          "fingerprint": shown_fingerprint(fingerprint) if fingerprint else None},
                "computers": [{"id": c["id"], "name": c.get("name"), "address": shown_address(c["address"], c["port"]),
                               "paired": c.get("paired"), "consented": c.get("consented") is True}
                              for c in self.computers()]}


# ---------------------------------------------------------------------------------------------
# The serving side's HTTPS server
# ---------------------------------------------------------------------------------------------

class Listener(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """The serving side: all interfaces, IPv4 and IPv6 where the system has both, TLS on each connection once its
    address has been checked. (socketserver's own server, not http.server's, which looks up its own name at the
    start.)"""
    daemon_threads = True
    allow_reuse_address = os.name != "nt"  # on Windows the option would let another program take the port too
    request_queue_size = 16
    address_family = socket.AF_INET6 if socket.has_dualstack_ipv6() else socket.AF_INET

    def __init__(self, address, handler, context, remote):
        self.context, self.remote = context, remote
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        self.open, self.open_lock = {}, threading.Lock()  # address -> connections open from it
        super().__init__(address, handler)

    def server_bind(self):
        if self.address_family == socket.AF_INET6:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def verify_request(self, request, client_address):
        """Only private addresses get as far as TLS, and not one refused for its failures: any other is closed before
        anything is read from it."""
        ip = client_address[0]
        if private(ip) and not self.remote.blocked(ip):
            return True
        self.remote.refused += 1
        return False

    def process_request(self, request, client_address):
        """At most MAX_CONNECTIONS at once, and MAX_PER_ADDRESS from one address, so one computer can't hold them all."""
        ip = client_address[0]
        with self.open_lock:
            crowded = self.open.get(ip, 0) >= MAX_PER_ADDRESS
            if not crowded:
                self.open[ip] = self.open.get(ip, 0) + 1
        if crowded or not self.slots.acquire(blocking=False):
            if not crowded:
                self.left(ip)
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            self.left(ip)
            raise

    def left(self, ip):
        with self.open_lock:
            self.open[ip] -= 1
            if not self.open[ip]:
                del self.open[ip]

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()
            self.left(client_address[0])

    def finish_request(self, request, client_address):
        request.settimeout(HANDSHAKE_SECONDS)
        try:
            conn = self.context.wrap_socket(request, server_side=True)
        except (OSError, ssl.SSLError):
            return
        try:
            conn.settimeout(IDLE_SECONDS)
            self.RequestHandlerClass(conn, client_address, self)
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def handle_error(self, request, client_address):  # a connection that broke: nothing to report
        pass


class Handler(BaseHTTPRequestHandler):
    """The serving side's requests. Every one but POST /v1/pair needs a paired computer's token; every answer is
    JSON and closes the connection."""
    server_version = "Sedjem"
    protocol_version = "HTTP/1.1"
    remote: Remote  # set by Remote.listen

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def reply(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def read(self, n):
        """Up to n bytes of the request's body (never past its end)."""
        chunk = self.rfile.read(min(n, self.left)) if self.left > 0 else b""
        self.left -= len(chunk)
        return chunk

    def drain(self, most=256 << 20):
        """Read what is left of a body before an error is answered: closing on unread data resets the connection,
        and the other side would lose the answer. A larger body is not read; the connection is just closed."""
        try:
            while 0 < self.left <= most and self.read(1 << 20):
                pass
        except OSError:
            pass

    def body_json(self, longest=1 << 14):
        if self.left > longest:
            raise Denied(413, "request too large")
        try:
            data = json.loads(self.read(self.left) or b"{}")
        except ValueError:
            raise Denied(400, "invalid JSON")
        if not isinstance(data, dict):
            raise Denied(400, "invalid JSON")
        return data

    def dispatch(self, method):
        remote, ip = self.remote, self.client_address[0]
        try:
            self.left = max(0, int(self.headers.get("Content-Length") or 0))
        except ValueError:
            self.left = 0
        path, _, query = self.path.partition("?")
        params = {k: v[-1] for k, v in urllib.parse.parse_qs(query).items()}
        try:
            if method == "POST" and path == "/v1/pair":
                return self.reply(200, remote.pair_request(ip, self.body_json()))
            client = remote.client_for(self.headers.get("Authorization"), ip)  # before anything else
            m = re.fullmatch(r"/v1/jobs/(\d{8}-\d{6}-[0-9a-f]{4})(/result|/cancel)?", path)
            if method == "GET" and path == "/v1/hello":
                return self.reply(200, remote.hello())
            if method == "POST" and path == "/v1/jobs":
                if self.headers.get("Content-Type") != "audio/flac":
                    raise Denied(415, "send the recording as audio/flac")
                return self.reply(201, remote.accept(client, params, self.left, self))
            if method == "POST" and path == "/v1/unpair":
                return self.reply(200, remote.unpair(client))
            if m and method == "GET" and not m.group(2):
                after = params.get("after")
                after = int(after) if after and after.isdigit() and len(after) < 9 else None
                return self.reply(200, remote.state(client, m.group(1), after))
            if m and method == "GET" and m.group(2) == "/result":
                return self.reply(200, remote.result(client, m.group(1)))
            if m and method == "POST" and m.group(2) == "/cancel":
                return self.reply(200, remote.cancel(client, m.group(1)))
            if m and method == "DELETE" and not m.group(2):
                return self.reply(200, remote.delete(client, m.group(1)))
            raise Denied(404, "not found")
        except Denied as e:
            self.drain()
            self.reply(e.status, {"error": str(e)})
        except (OSError, ssl.SSLError):
            self.close_connection = True
        except Exception as e:  # noqa: BLE001 — answered, never dropped
            self.reply(500, {"error": type(e).__name__})
