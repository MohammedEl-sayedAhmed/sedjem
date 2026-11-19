"""Stand-ins for Google Drive in the Drive tests (tests/test_drive.py, tests/test_ui_drive.py), so nothing is fetched
from the network.

FakeDrive replaces app/drive.py's _ydl with a yt-dlp that answers with info dicts in the shape yt-dlp's GoogleDrive
extractor gives them, and downloads small media files made here with PyAV (tests/youtube_support.py):

  fake = FakeDrive()
  fake.file(FILE_ID, "Lecture 1.mp4", kind="both", seconds=3)
  with mock.patch.object(drive, "_ydl", fake.ydl): ...

GoogleServers goes one level down: the real yt-dlp, with Drive's real extractor, talking to a request handler that
answers as Google's servers do (Drive's playback API, and the download with its virus-scan page), so the extractor's
own code runs, offline:

  google = GoogleServers()
  google.file(FILE_ID, "Big lecture.mp4", data, scan=True)
  with mock.patch.object(drive, "_ydl", google.ydl(drive._ydl)): ...
"""
import io
import json
import re
import threading
import time
import urllib.parse
from pathlib import Path

from youtube_support import media

AUDIO = "audio"  # the kinds youtube_support.media makes
VIDEO = "both"


def drive_id(n, prefix="f"):
    """A made-up Drive id of 33 characters, as Drive's are."""
    return f"1{prefix}{n:031d}"


class FakeDrive:
    """Files by id, and the yt-dlp stand-in that serves them (ydl)."""

    def __init__(self):
        self.files = {}
        self.private = set()       # ids Drive won't show to someone who isn't signed in
        self.no_download = set()   # files whose owner turned downloading off: no "source" format
        self.elsewhere = {}        # file id -> a host its download is sent to instead of Drive's
        self.hold = threading.Event()
        self.hold.set()            # clear it to keep downloads running until it is set again
        self.started = threading.Event()
        self.downloads = []        # file ids, in the order they were downloaded

    def file(self, fid, name, kind=VIDEO, seconds=3, size=None):
        """A file in Drive: kind is youtube_support.media's ("both" for a video, "audio"), or None for one that
        isn't audio or video; size, what Drive says of it when the download starts. Returns its id."""
        self.files[fid] = {"name": name, "kind": kind, "seconds": seconds, "size": size}
        return fid

    def ydl(self, cfg, **extra):  # what app.drive._ydl returns
        return FakeYDL(self)


class FakeYDL:
    def __init__(self, fake):
        self.fake, self.hooks = fake, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False, process=True):
        m = re.fullmatch(r"/file/d/([\w-]+)/view", urllib.parse.urlsplit(url).path)
        if not m:
            raise Exception(f"ERROR: Unsupported URL: {url}")
        fid = m.group(1)
        if fid in self.fake.private or fid not in self.fake.files:
            raise Exception(f"ERROR: [GoogleDrive] {fid}: Unable to download JSON metadata: HTTP Error 403: Forbidden "
                            "(caused by <HTTPError 403: Forbidden>)")
        f = self.fake.files[fid]
        host = self.fake.elsewhere.get(fid, "drive.usercontent.google.com")
        formats = [{"format_id": "18", "ext": "mp4", "url": f"https://rr1---sn-test.c.drive.google.com/videoplayback?id={fid}"}]
        if fid not in self.fake.no_download:
            formats.append({"format_id": "source", "ext": f["name"].rsplit(".", 1)[-1], "quality": 1,
                            "url": f"https://{host}/download?id={fid}&export=download&confirm=t"})
        return {"id": fid, "title": f["name"], "duration": f["seconds"], "formats": formats, "subtitles": {},
                "extractor_key": "GoogleDrive", "webpage_url": url}

    def add_progress_hook(self, fn):
        self.hooks.append(fn)

    def dl(self, name, fmt):
        f = self.fake.files[fmt["id"]]
        data = media(f["kind"] or AUDIO, f["seconds"])
        total = f["size"] or len(data)
        self.fake.downloads.append(fmt["id"])
        self.fake.started.set()
        for hook in self.hooks:
            hook({"status": "downloading", "downloaded_bytes": len(data) // 2, "total_bytes": total})
        while not self.fake.hold.is_set():  # a download that takes a while (cancelling it is tested)
            for hook in self.hooks:
                hook({"status": "downloading", "downloaded_bytes": len(data) // 2, "total_bytes": total})
            time.sleep(0.05)
        Path(name).write_bytes(data)
        for hook in self.hooks:
            hook({"status": "finished", "downloaded_bytes": len(data), "total_bytes": total})
        return True


# ---- Google's servers, for the real yt-dlp ------------------------------------------------------------

SCAN_PAGE = """<!DOCTYPE html><html><head><title>Google Drive - Virus scan warning</title></head><body>
<div class="uc-main"><p class="uc-warning-caption">Google Drive can't scan this file for viruses.</p>
<p class="uc-warning-subcaption">{name} is too large for Google to scan for viruses. Would you still like to
download this file?</p>
<form id="download-form" action="https://drive.usercontent.google.com/download" method="get">
<input type="submit" id="uc-download-link" value="Download anyway"/>
<input type="hidden" name="id" value="{fid}"><input type="hidden" name="export" value="download">
<input type="hidden" name="confirm" value="t"><input type="hidden" name="uuid" value="0a1b2c3d"></form></div>
</body></html>"""


class GoogleServers:
    """What Google's servers answer for Drive files, to the real yt-dlp: files by id ({name, data, scan, host}), ids
    that aren't shared, and every request made, as (method, url)."""

    def __init__(self):
        self.files = {}
        self.private = set()   # ids Drive won't show to someone who isn't signed in
        self.requests = []

    def file(self, fid, name, data, scan=False, host="drive.usercontent.google.com"):
        """scan: the first answer to the download is the page saying the file is too large to scan for viruses.
        host: where the download is sent on to (another host than Google's is refused by app/drive.py)."""
        self.files[fid] = {"name": name, "data": data, "scan": scan, "host": host}
        return fid

    def ydl(self, real):
        """A stand-in for app/drive.py's _ydl: the real one (`real`), with this as yt-dlp's only request handler."""
        from yt_dlp.networking.common import RequestHandler

        servers = self

        class GoogleRH(RequestHandler):
            _SUPPORTED_URL_SCHEMES = ("http", "https")
            _SUPPORTED_PROXY_SCHEMES = None
            _SUPPORTED_FEATURES = None

            def _check_extensions(self, extensions):
                extensions.clear()

            def _send(self, request):
                return servers.answer(request)

        def make(cfg, **extra):
            ydl = real(cfg, **extra)
            director = ydl._request_director
            director.close()  # nothing can reach the network
            director.add_handler(GoogleRH(logger=director.logger))
            return ydl
        return make

    def answer(self, request):
        from yt_dlp.networking.common import Response
        from yt_dlp.networking.exceptions import HTTPError

        url, method = request.url, request.method
        self.requests.append((method, url))
        p = urllib.parse.urlsplit(url)
        q = urllib.parse.parse_qs(p.query)

        def reply(body, status=200, headers=None, at=url, reason="OK"):
            body = body.encode() if isinstance(body, str) else body
            r = Response(io.BytesIO(body), at, {"Content-Length": str(len(body)), **(headers or {})}, status, reason)
            if status >= 400:
                raise HTTPError(r)
            return r

        m = re.fullmatch(r"/v1/drive/media/([\w-]+)/playback", p.path)
        if p.hostname == "content-workspacevideo-pa.googleapis.com" and m:  # a file's details
            f = self.files.get(m.group(1))
            if not f or m.group(1) in self.private:
                return reply('{"error": {"code": 403, "message": "The caller does not have permission"}}', 403,
                             reason="Forbidden")
            return reply(json.dumps({"mediaMetadata": {"title": f["name"], "duration": "3.5s"}}),
                         headers={"Content-Type": "application/json"})
        if p.hostname == "drive.usercontent.google.com" and p.path == "/download":
            fid = (q.get("id") or [""])[0]
            f = self.files.get(fid)
            if method == "POST":  # the virus-scan page's form, sent
                f = next((x for x in self.files.values() if x["scan"]), None)
            elif f and f["scan"]:
                return reply(SCAN_PAGE.format(name=f["name"], fid=fid), headers={"Content-Type": "text/html"})
            if not f:
                return reply("Not found", 404, reason="Not Found")
            to = f"https://{f['host']}/download/{fid or 'confirmed'}/{urllib.parse.quote(f['name'])}"
            return reply(b"", headers={"Content-Disposition": f'attachment; filename="{f["name"]}"'}, at=to)
        m = re.fullmatch(r"/download/[\w-]+/(.+)", p.path)
        if m:  # the file itself, where the download was sent on to
            f = next(x for x in self.files.values() if x["name"] == urllib.parse.unquote(m.group(1)))
            data, status = f["data"], 200
            r = re.fullmatch(r"bytes=(\d+)-(\d*)", request.headers.get("Range", ""))
            if r:
                start, end = int(r.group(1)), int(r.group(2) or len(f["data"]) - 1)
                data, status = f["data"][start:end + 1], 206
            return reply(data, status, {"Content-Type": "application/octet-stream"})
        return reply("Not found", 404, reason="Not Found")
