"""Tests for Google Drive links (app/drive.py): the links taken and refused, a folder's link refused before yt-dlp
sees it, yt-dlp limited to Drive's file extractor and its downloads to Drive's hosts, a file that isn't shared, a
download (past the page saying a large file can't be scanned for viruses), and a file added through the app's API: its
job, what assistants see of it, and the usage counts that leave it out. Two stand-ins (tests/drive_support.py): yt-dlp
answering with info dicts as Drive's extractor gives them, and the real yt-dlp talking to a stand-in for Google's
servers. Nothing is fetched from the network.
Run: .venv/bin/python -m unittest tests.test_drive -v
"""
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import drive, engines, server, usage  # noqa: E402
from app import mcp as sedjem_mcp  # noqa: E402
from app.config import Config  # noqa: E402
from app.engines import Cancelled  # noqa: E402
from app.server import make_server  # noqa: E402
from drive_support import AUDIO, FakeDrive, GoogleServers, drive_id  # noqa: E402

FILE, FOLDER = drive_id(1), drive_id(1, "d")
FOLDER_LINKS = (f"https://drive.google.com/drive/folders/{FOLDER}?usp=sharing", f"drive.google.com/drive/u/0/folders/{FOLDER}",
                f"https://drive.google.com/drive/mobile/folders/{FOLDER}?usp=drive_link",
                f"http://docs.google.com/drive/folders/{FOLDER}#x")
LINE = [{"start": 0.0, "end": 1.5, "speaker": "1", "text": "Spoken words"}]


class Links(unittest.TestCase):
    def test_links_are_rebuilt_from_their_parts(self):
        file = drive.file_url(FILE)
        for url in (f"https://drive.google.com/file/d/{FILE}/view?usp=sharing", f"drive.google.com/file/d/{FILE}",
                    f"https://drive.google.com/file/d/{FILE}/edit", f"https://drive.google.com/file/u/1/d/{FILE}/view",
                    f"https://docs.google.com/file/d/{FILE}/preview", f"HTTPS://DRIVE.GOOGLE.COM:443/file/d/{FILE}/view",
                    f"https://drive.google.com/uc?id={FILE}&export=download",
                    f"https://drive.google.com/open?id={FILE}&authuser=0"):
            self.assertEqual(drive.check_url(url), file, url)

    def test_a_folder_is_recognised_and_refused(self):
        for url in FOLDER_LINKS:
            self.assertEqual(drive.parse(url), ("folder", FOLDER), url)
            with self.assertRaises(drive.DriveError, msg=url) as e:
                drive.check_url(url)
            self.assertEqual(str(e.exception),
                             "Google Drive folders can't be read yet. Add the files one by one, each with its own link.")

    def test_only_drive_itself(self):
        for url in ("", "https://vimeo.com/1", "file:///etc/passwd", "javascript:alert(1)", f"ftp://drive.google.com/file/d/{FILE}",
                    f"https://drive.google.com@evil.example/file/d/{FILE}",       # the host is evil.example
                    f"https://user:pw@drive.google.com/file/d/{FILE}/view",       # a user name in the address
                    f"https://drive.google.com.evil.example/file/d/{FILE}", f"https://drivegoogle.com/file/d/{FILE}",
                    f"https://drive.google.co/file/d/{FILE}", f"https://evil.example/?u=https://drive.google.com/file/d/{FILE}",
                    f"https://drive.google.com:8080/file/d/{FILE}", f"https://drive.google.com:99999/file/d/{FILE}",
                    f"https://drive.usercontent.google.com/download?id={FILE}",  # a download address, not a link to paste
                    f"https://www.youtube.com/watch?v={FILE[:11]}", "https://drive.google.com/drive/my-drive",
                    "https://drive.google.com/", "https://drive.google.com/file/d/short/view",
                    f"https://drive.google.com/file/d/{FILE}/view/and/more", "https://drive.google.com/open?id=../../etc/x",
                    f"https://drive.google.com/drive/folders/{FOLDER}/../x", "https://drive.google.com/uc?export=download"):
            with self.assertRaises(drive.DriveError, msg=url):
                drive.check_url(url)
        with self.assertRaisesRegex(drive.DriveError, "Google Docs file"):
            drive.check_url(f"https://docs.google.com/document/d/{FILE}/edit")

    def test_only_drives_file_extractor(self):
        """The real yt-dlp, offline: with the app's settings it has Drive's file extractor and no other, not even
        Drive's folder one."""
        with drive._ydl(Config(home=tempfile.gettempdir())) as ydl:
            self.assertEqual({ie.IE_NAME for ie in ydl._ies.values()}, {"GoogleDrive"})
            for url in ("https://evil.example/page.html", "https://www.youtube.com/watch?v=jNQXAC9IVRw", FOLDER_LINKS[0]):
                with self.assertRaises(Exception, msg=url):
                    ydl.extract_info(url, download=False, process=False)

    def test_errors_read_well(self):
        for message in (f"ERROR: [GoogleDrive] {FILE}: Unable to download JSON metadata: HTTP Error 403: Forbidden "
                        "(caused by <HTTPError 403: Forbidden>)",
                        f"ERROR: [GoogleDrive] {FILE}: Unable to download JSON metadata: HTTP Error 404: Not Found"):
            self.assertEqual(drive.clean_error(Exception(message)), drive.PRIVATE)
        self.assertEqual(drive.clean_error(Exception("ERROR: [GoogleDrive] x123456: The read operation timed out")),
                         "The read operation timed out")


class Allowlists(unittest.TestCase):
    def test_only_the_file_itself_and_only_from_drive(self):
        source = lambda url, **kw: {"format_id": "source", "ext": "mp4", "url": url, **kw}
        for url in (f"https://drive.usercontent.google.com/download?id={FILE}&export=download&confirm=t",
                    "https://doc-0s-1c-docs.googleusercontent.com/docs/securesc/abc/def?e=download",
                    f"https://drive.google.com/uc?id={FILE}&export=download", "https://docs.google.com/uc?id=x"):
            self.assertEqual(drive.pick_source([source(url)])["url"], url, url)
        for url in ("https://evil.example/v.mp4", "https://googleusercontent.com.evil.example/v.mp4",
                    "https://drive.usercontent.google.com.evil.example/download", "https://user@drive.usercontent.google.com/x",
                    "https://drive.usercontent.google.com:8443/download", "ftp://drive.usercontent.google.com/download",
                    "https://rr1---sn-test.c.drive.google.com/videoplayback", "https://www.youtube.com/watch?v=jNQXAC9IVRw"):
            self.assertIsNone(drive.pick_source([source(url)]), url)
        transcoded = {"format_id": "18", "ext": "mp4", "url": "https://drive.usercontent.google.com/videoplayback"}
        self.assertIsNone(drive.pick_source([transcoded]), "a copy Drive made to play the video is never taken")
        self.assertIsNone(drive.pick_source([source("https://drive.usercontent.google.com/x", protocol="m3u8_native")]))


class InspectAndDownload(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sedjem-drive-"))
        self.cfg = Config(home=self.tmp)
        self.fake = fake = FakeDrive()
        self.video = fake.file(drive_id(10), "01 Introduction.mp4", seconds=2)
        self.pdf = fake.file(drive_id(11), "notes.pdf", kind=None)
        self.sound = fake.file(drive_id(12), "03 Loops.MP3", kind=AUDIO)
        self.patch = mock.patch.object(drive, "_ydl", fake.ydl)
        self.patch.start()
        self.dir = self.tmp / "job"
        self.dir.mkdir()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_file(self):
        found = drive.inspect(self.cfg, f"https://drive.google.com/file/d/{self.video}/view?usp=drive_link")
        self.assertEqual((found["site"], found["kind"], found["title"], found["name"], found["count"], found["duration"]),
                         ("drive", "file", "01 Introduction", "01 Introduction.mp4", 1, 2))
        self.assertEqual([(e["id"], e["url"], e["available"]) for e in found["entries"]],
                         [(self.video, drive.file_url(self.video), True)])
        self.assertEqual(drive.inspect(self.cfg, f"https://drive.google.com/open?id={self.sound}")["title"], "03 Loops")
        with self.assertRaisesRegex(drive.DriveError, "isn't audio or video"):
            drive.inspect(self.cfg, drive.file_url(self.pdf))

    def test_a_folder_never_reaches_yt_dlp(self):
        with mock.patch.object(drive, "_ydl", side_effect=AssertionError("yt-dlp was asked")):
            for url in FOLDER_LINKS:
                with self.assertRaisesRegex(drive.DriveError, "^Google Drive folders can't be read yet", msg=url):
                    drive.inspect(self.cfg, url)

    def test_what_isnt_shared(self):
        self.fake.private.add(self.video)
        for fid in self.video, drive_id(99):  # not shared, and removed (or never there)
            with self.assertRaises(drive.DriveError, msg=fid) as e:
                drive.inspect(self.cfg, drive.file_url(fid))
            self.assertEqual(str(e.exception),
                             "This file isn't shared with anyone who has the link. Sedjem can't sign in to Google Drive yet.")

    def test_a_download_is_the_file_itself(self):
        seen = []
        job = {"drive_id": self.video, "source_url": drive.file_url(self.video), "title": "01 Introduction"}
        got = drive.download(self.cfg, job, self.dir, lambda done, total: seen.append((done, total)), lambda: False)
        self.assertEqual(got, {"source_name": "01 Introduction.mp4"})
        self.assertEqual([p.name for p in self.dir.iterdir()], ["source.mp4"], "as an upload: no video.mp4 yet")
        self.assertEqual(self.fake.downloads, [self.video])
        self.assertTrue(seen and all(total for _, total in seen))
        job = {"drive_id": self.sound, "source_url": drive.file_url(self.sound)}
        self.assertEqual(drive.download(self.cfg, job, self.dir, lambda *a: None, lambda: False)["source_name"], "03 Loops.MP3")
        self.assertTrue((self.dir / "source.mp3").exists())

    def test_a_download_only_from_drive(self):
        self.fake.elsewhere[self.video] = "evil.example"
        self.fake.no_download.add(self.sound)
        for fid in self.video, self.sound:
            with self.assertRaisesRegex(drive.DriveError, "doesn't offer this file for download", msg=fid):
                drive.inspect(self.cfg, drive.file_url(fid))
            with self.assertRaisesRegex(drive.DriveError, "doesn't offer this file for download", msg=fid):
                drive.download(self.cfg, {"drive_id": fid, "source_url": drive.file_url(fid)}, self.dir,
                               lambda *a: None, lambda: False)
        for job in ({"drive_id": self.sound, "source_url": "https://evil.example/v.mp4"},
                    {"drive_id": self.video, "source_url": drive.file_url(self.sound)},  # another file
                    {"drive_id": "../../etc/passwd", "source_url": drive.file_url(self.sound)},
                    {"drive_id": FOLDER, "source_url": FOLDER_LINKS[0]}):
            with self.assertRaises(drive.DriveError, msg=job):
                drive.download(self.cfg, job, self.dir, lambda *a: None, lambda: False)
        self.assertEqual(self.fake.downloads, [], "nothing was downloaded")

    def test_over_the_upload_limit(self):
        self.cfg.max_upload = 1000  # bytes: every made-up file is larger
        job = {"drive_id": self.video, "source_url": drive.file_url(self.video)}
        with self.assertRaisesRegex(drive.DriveError, "larger than the 0 GB limit"):
            drive.download(self.cfg, job, self.dir, lambda *a: None, lambda: False)
        self.assertEqual(list(self.dir.iterdir()), [], "nothing is kept")

    def test_cancelled_while_downloading(self):
        self.fake.hold.clear()
        stop = threading.Event()
        threading.Timer(0.3, stop.set).start()
        try:
            with self.assertRaises(Cancelled):
                drive.download(self.cfg, {"drive_id": self.video, "source_url": drive.file_url(self.video)}, self.dir,
                               lambda *a: None, stop.is_set)
        finally:
            self.fake.hold.set()
        self.assertFalse((self.dir / "source.mp4").exists())


class RealExtractor(unittest.TestCase):
    """yt-dlp's own GoogleDrive extractor and downloader against a stand-in for Google's servers
    (tests/drive_support.py)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sedjem-drive-real-"))
        self.cfg = Config(home=self.tmp)
        self.google = GoogleServers()
        self.patch = mock.patch.object(drive, "_ydl", self.google.ydl(drive._ydl))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def hosts(self):
        return {urllib.parse.urlsplit(u).hostname for _, u in self.google.requests}

    def test_a_large_file_past_the_virus_scan_page(self):
        data = bytes(range(256)) * 2000
        fid = self.google.file(drive_id(20), "Week 1 recording.mp4", data, scan=True)
        found = drive.inspect(self.cfg, drive.file_url(fid))
        self.assertEqual((found["kind"], found["title"], found["duration"]), ("file", "Week 1 recording", 3.5))
        self.google.requests.clear()
        seen = []
        got = drive.download(self.cfg, {"drive_id": fid, "source_url": drive.file_url(fid)}, self.tmp,
                             lambda done, total: seen.append((done, total)), lambda: False)
        self.assertEqual(got["source_name"], "Week 1 recording.mp4")
        self.assertEqual((self.tmp / "source.mp4").read_bytes(), data)
        self.assertEqual(seen[-1], (len(data), len(data)))
        steps = [(m, u.split("?")[0]) for m, u in self.google.requests]
        self.assertEqual(steps[1:], [("GET", "https://drive.usercontent.google.com/download"),   # the page...
                                     ("POST", "https://drive.usercontent.google.com/download"),  # ...answered
                                     ("GET", "https://drive.usercontent.google.com/download/confirmed/Week%201%20recording.mp4")])
        self.assertTrue(self.hosts() <= {"content-workspacevideo-pa.googleapis.com", "drive.usercontent.google.com"})

    def test_a_download_sent_on_to_googleusercontent(self):
        fid = self.google.file(drive_id(21), "Talk.m4a", b"sound" * 100, host="doc-0s-1c-docs.googleusercontent.com")
        drive.download(self.cfg, {"drive_id": fid, "source_url": drive.file_url(fid)}, self.tmp, lambda *a: None, lambda: False)
        self.assertEqual((self.tmp / "source.m4a").read_bytes(), b"sound" * 100)

    def test_a_download_sent_elsewhere_is_refused(self):
        fid = self.google.file(drive_id(22), "Talk.mp4", b"x" * 100, host="evil.example")
        with self.assertRaisesRegex(drive.DriveError, "doesn't offer this file for download"):
            drive.inspect(self.cfg, drive.file_url(fid))
        with self.assertRaisesRegex(drive.DriveError, "doesn't offer this file for download"):
            drive.download(self.cfg, {"drive_id": fid, "source_url": drive.file_url(fid)}, self.tmp, lambda *a: None,
                           lambda: False)
        self.assertNotIn("evil.example", self.hosts(), "nothing was fetched from there")
        self.assertEqual(list(self.tmp.glob("source.*")), [])

    def test_a_file_that_isnt_shared(self):
        fid = self.google.file(drive_id(23), "Private.mp4", b"x")
        self.google.private.add(fid)
        for url in (drive.file_url(fid), drive.file_url(drive_id(24))):  # not shared, and not there
            with self.assertRaises(drive.DriveError) as e:
                drive.inspect(self.cfg, url)
            self.assertEqual(str(e.exception), drive.PRIVATE)


class Api(unittest.TestCase):
    """Through the app's HTTP API and its queues, with the hosted service replaced by a stand-in."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="sedjem-drive-api-"))
        cls.cfg = Config(home=cls.tmp)
        cls.cfg.save_key("elevenlabs", "test-key")
        cls.fake = fake = FakeDrive()
        cls.video = fake.file(drive_id(50), "Week 1 lecture.mp4", seconds=2)
        cls.sound = fake.file(drive_id(51), "Interview.m4a", kind=AUDIO, seconds=2)
        cls.later = fake.file(drive_id(52), "Unshared later.mp4", seconds=2)
        cls.ran = []

        def hosted(cfg, model, job_dir, job, on_progress, cancelled):
            cls.ran.append(job["drive_id"])
            return {"lines": [{**LINE[0], "text": f"Spoken words of {job['title']}"}], "meta": {}}
        cls.patches = [mock.patch.object(drive, "_ydl", fake.ydl), mock.patch.object(engines, "run_hosted", hosted)]
        for p in cls.patches:
            p.start()
        cls.server, cls.app = make_server(cls.cfg, port=0)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for p in cls.patches:
            p.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, method, path, body=None):
        h = {"X-Sedjem": "1", "X-Sedjem-Token": self.app.session.secret, "Content-Type": "application/json"}
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def wait(self, jid, until=("done", "failed", "cancelled"), timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            r = self.call("GET", f"/api/jobs/{jid}")[1]
            if r["job"]["status"] in until and not r["job"].get("video_prep"):
                return r
            time.sleep(0.1)
        self.fail(f"job {jid} is still {r['job']['status']} ({r['job'].get('stage')})")

    def add(self, url, **body):
        return self.call("POST", "/api/link", {"url": url, "model": "elevenlabs", "confirm_upload": True, **body})

    def settled(self, check, timeout=30):
        end = time.time() + timeout
        while not check():
            if time.time() > end:
                self.fail("the job never caught up")
            time.sleep(0.05)

    def test_1_a_file_becomes_a_transcription(self):
        url = f"https://drive.google.com/file/d/{self.video}/view?usp=sharing"
        status, found = self.call("POST", "/api/link/inspect", {"url": url})
        self.assertEqual((status, found["site"], found["title"]), (200, "drive", "Week 1 lecture"), found)
        status, r = self.add(url, captions=True, stream=True)
        self.assertEqual(status, 201, r)
        (job,) = r["jobs"]
        self.assertEqual((job["title"], job["source_name"], job["drive_id"], job["source_url"], job["course"],
                          job["youtube_id"], job["stream"]),
                         ("Week 1 lecture", "Week 1 lecture.mp4", self.video, drive.file_url(self.video), None, None, None),
                         "no course, no streaming")
        done = self.wait(job["id"])
        self.assertEqual(done["job"]["status"], "done", done["job"].get("error"))
        self.assertEqual(self.ran, [self.video], "it went to the model: no captions")
        self.assertTrue(done["has_video"], "a video file plays beside its transcript, as an uploaded one does")
        folder = self.cfg.storage / "jobs" / job["id"]
        self.settled(lambda: not list(folder.glob("source.*")))  # the download goes, as an upload does
        rows = {row["label"]: row["value"] for g in done["detail_groups"] for row in g["rows"]}
        self.assertEqual(rows["Google Drive"], drive.file_url(self.video))
        self.assertNotIn("YouTube", rows)
        time.sleep(0.3)
        self.assertFalse(any((self.tmp / "courses").rglob("*")), "like an uploaded file: nothing in the courses folder")
        self.assertEqual(self.call("POST", "/api/open-folder", {"job": job["id"]})[0], 400)
        time.sleep(1.1)  # job times are kept to the second
        status, again = self.call("POST", f"/api/jobs/{job['id']}/rerun", {"model": "elevenlabs", "confirm_upload": True})
        self.assertEqual((status, again["drive_id"]), (201, self.video), "still the same file")
        self.wait(again["id"])

    def test_2_a_sound_file(self):
        status, r = self.add(f"https://drive.google.com/open?id={self.sound}")
        self.assertEqual(status, 201, r)
        done = self.wait(r["jobs"][0]["id"])
        self.assertEqual((done["job"]["status"], done["has_video"]), ("done", False))

    def test_3_a_file_that_stops_being_shared_and_is_tried_again(self):
        self.assertEqual(self.call("POST", "/api/link/inspect", {"url": drive.file_url(self.later)})[0], 200)
        self.fake.private.add(self.later)
        status, r = self.add(drive.file_url(self.later))  # what the check found is used: the job is made
        self.assertEqual(status, 201, r)
        failed = self.wait(r["jobs"][0]["id"])
        self.assertEqual((failed["job"]["status"], failed["job"]["error"]), ("failed", "Google Drive: " + drive.PRIVATE))
        self.fake.private.discard(self.later)
        status, again = self.call("POST", f"/api/jobs/{failed['job']['id']}/rerun", {"model": "elevenlabs", "confirm_upload": True})
        self.assertEqual((status, again["id"]), (201, failed["job"]["id"]), "the same job, downloaded again")
        self.assertEqual(self.wait(again["id"])["job"]["status"], "done")

    def test_4_what_is_refused(self):
        self.fake.private.add(drive_id(70))
        for url, error in ((drive.file_url(drive_id(70)), drive.PRIVATE), (FOLDER_LINKS[0], drive.FOLDERS_LATER),
                           ("https://vimeo.com/1", server.NOT_A_LINK), ("https://drive.google.com/drive/my-drive", drive.NOT_DRIVE)):
            self.assertEqual(self.call("POST", "/api/link/inspect", {"url": url}), (400, {"error": error}), url)
        self.assertEqual(self.add(FOLDER_LINKS[1]), (400, {"error": drive.FOLDERS_LATER}))
        forged = drive.file_url(drive_id(71))  # an id that isn't Drive's never becomes a job
        self.app.inspected[forged] = (time.time(), {"site": "drive", "kind": "file", "id": drive_id(71), "title": "x",
                                                    "url": forged, "count": 1, "entries": [{"id": "../../etc", "title": "x",
                                                                                            "name": "x.mp4", "index": 1,
                                                                                            "available": True}]})
        status, r = self.add(forged)
        self.assertEqual((status, r["error"]), (400, "a file has an id Google Drive doesn't use"))


class Elsewhere(unittest.TestCase):
    """What the rest of the app makes of a Drive job: assistants (app/mcp.py) and the usage counts (app/usage.py)."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="sedjem-drive-mcp-"))
        self.jid = jid = "20261019-090000-e003"
        folder = self.home / "app_data" / "jobs" / jid
        folder.mkdir(parents=True)
        job = {"id": jid, "title": "Interview", "created": "2026-10-19T09:00:00+03:00", "model": "cohere",
               "model_title": "Cohere Transcribe Arabic", "kind": "local", "status": "done", "audio_s": 20.0,
               "speaker_names": {}, "finished": "2026-10-19T09:00:00+03:00", "drive_id": drive_id(90),
               "source_url": drive.file_url(drive_id(90))}
        (folder / "job.json").write_text(json.dumps(job), encoding="utf-8")
        (folder / "transcript.json").write_text(json.dumps({"lines": LINE}), encoding="utf-8")
        self.mcp = sedjem_mcp.Server(Config(home=self.home))

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def tool(self, name, **args):
        meta = {sedjem_mcp.VERSION_KEY: "2026-07-28", sedjem_mcp.CAPABILITIES_KEY: {}}
        r = self.mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                             "params": {"name": name, "arguments": args, "_meta": meta}})["result"]
        self.assertFalse(r.get("isError"), r)
        return r["content"][0]["text"]

    def test_assistants_see_a_drive_file_with_its_link(self):
        self.assertIn(f"  Google Drive file · {drive.file_url(drive_id(90))}", self.tool("list_transcriptions"))
        self.assertIn("Google Drive file", self.tool("get_transcript", id=self.jid))
        self.assertNotIn("&t=", self.tool("search", query="Spoken"), "Drive links can't open a file at a moment")
        self.assertIn("There are no courses", self.tool("list_courses"), "a Drive file is no course")

    def test_the_usage_counts_leave_drive_out(self):
        u = usage.Usage(Config(home=self.home), version=lambda: "0.3.1", facts=lambda: {})
        with mock.patch.object(u, "count") as count:
            u.job_ended({"status": "failed", "drive_id": drive_id(1)}, stage="download")
            u.job_ended({"status": "failed", "youtube_id": "abcdefghijk"}, stage="download")
            u.job_ended({"status": "done", "kind": "captions", "drive_id": drive_id(1)})
        self.assertEqual([c.kwargs for c in count.call_args_list], [{"failed": 1}, {"failed": 1, "failed_download": 1}, {}],
                         "a failed Drive download isn't a failed (YouTube) download, and a Drive job isn't a YouTube one")


if __name__ == "__main__":
    unittest.main()
