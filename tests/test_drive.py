"""Tests for Google Drive links (app/drive.py): the links taken and refused, yt-dlp limited to Drive's file extractor
and its downloads to Drive's hosts, a file that isn't shared, a download (past the page saying a large file can't be
scanned for viruses), a folder listed from Drive's embedded view without yt-dlp (its entries, subfolders and other
files, natural name order, an empty or private folder, the answers that aren't a listing), and a file or a folder added
through the app's API: their jobs, a folder's course, what assistants see of them, and the usage counts that leave
them out. Three stand-ins (tests/drive_support.py): yt-dlp answering with info dicts as Drive's extractor gives them,
the real yt-dlp talking to a stand-in for Google's servers, and Drive's folder view answering the app's own opener.
Nothing is fetched from the network.
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
from app import assistants, drive, engines, server, usage  # noqa: E402
from app import mcp as sedjem_mcp  # noqa: E402
from app.config import Config  # noqa: E402
from app.engines import Cancelled  # noqa: E402
from app.server import make_server  # noqa: E402
from drive_support import AUDIO, FakeDrive, FolderPages, GoogleServers, drive_id, folder_page  # noqa: E402

FILE, FOLDER, DRIVE_COURSE = drive_id(1), drive_id(1, "d"), drive_id(9, "d")
OPENER = drive.OPENER  # the app's own, before a test puts the stand-in in its place
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

    def test_a_folders_link(self):
        """The forms people paste, with or without ?usp= and a resource key; yt-dlp is never given one (check_url)."""
        for url in FOLDER_LINKS:
            self.assertEqual(drive.parse(url), ("folder", FOLDER), url)
            self.assertEqual(drive.folder_link(url), (FOLDER, None), url)
            with self.assertRaises(drive.DriveError, msg=url) as e:
                drive.check_url(url)
            self.assertEqual(str(e.exception), drive.NOT_A_FILE)
        keyed = f"https://drive.google.com/drive/folders/{FOLDER}?resourcekey=0-AbC_d-9&usp=sharing"
        self.assertEqual(drive.folder_link(keyed), (FOLDER, "0-AbC_d-9"))
        self.assertEqual(drive.listing_url(FOLDER, "0-AbC_d-9"),
                         f"https://drive.google.com/embeddedfolderview?id={FOLDER}&resourcekey=0-AbC_d-9")
        self.assertEqual(drive.folder_link(f"https://drive.google.com/drive/folders/{FOLDER}?resourcekey="), (FOLDER, None))
        for url in (f"https://drive.google.com/drive/folders/{FOLDER}?resourcekey=0-a%26id%3Dx",  # nothing else rides in
                    drive.file_url(FILE)):
            with self.assertRaises(drive.DriveError, msg=url) as e:
                drive.folder_link(url)
            self.assertEqual(str(e.exception), drive.NOT_DRIVE, url)

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

    def test_names_and_extensions(self):
        for name, ext in (("Lecture.mp4", "mp4"), ("clip.3gp", "3gp"), ("Talk.M4A", "m4a"), ("notes.pdf", "pdf"),
                          ("Week 1.2", ""), ("Meeting 2026.11.19", ""), ("Lecture 1. Intro", ""), ("readme", "")):
            self.assertEqual(drive.extension(name), ext, name)
        plays = {"duration": 12.5, "formats": [{"format_id": "source"}]}
        copy = {"duration": None, "formats": [{"format_id": "18"}, {"format_id": "source"}]}
        silent = {"duration": None, "formats": [{"format_id": "source"}]}
        self.assertTrue(drive.clearly_not_media("notes.pdf", silent))
        self.assertTrue(drive.clearly_not_media("photo.JPG", silent))
        for name, info in (("notes.pdf", plays), ("notes.pdf", copy), ("Week 1.2", silent), ("Meeting 2026.11.19", silent),
                           ("talk.xyz", silent), ("recording", silent), ("Lecture.mp4", silent)):
            self.assertFalse(drive.clearly_not_media(name, info), (name, info))

    def test_errors_read_well(self):
        for message in (f"ERROR: [GoogleDrive] {FILE}: Unable to download JSON metadata: HTTP Error 403: Forbidden "
                        "(caused by <HTTPError 403: Forbidden>)",
                        f"ERROR: [GoogleDrive] {FILE}: Unable to download JSON metadata: HTTP Error 404: Not Found"):
            self.assertEqual(drive.clean_error(Exception(message)), drive.PRIVATE)
        self.assertEqual(drive.clean_error(Exception(  # as real Drive answered a popular file, 19 Nov 2026
            f"ERROR: [GoogleDrive] {FILE}: Unable to download JSON metadata: HTTP Error 429: Too Many Requests "
            "(caused by <HTTPError 429: Too Many Requests>)")), drive.BUSY)
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


class Folders(unittest.TestCase):
    """A folder listed from Drive's embedded view (app/drive.py list_folder, inspect_folder): the app's own opener asks
    a stand-in (FolderPages) that answers with pages shaped as Drive's real ones."""

    def setUp(self):
        self.pages = FolderPages()
        self.patch = mock.patch.object(drive, "OPENER", self.pages.opener())
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def refused(self, url=None):
        with self.assertRaises(drive.DriveError) as e:
            drive.inspect_folder(url or drive.folder_url(FOLDER))
        return str(e.exception)

    def test_its_audio_and_video_files_in_natural_name_order(self):
        files = [("Lecture 10 - Recursion.mp4", "video/mp4"), ("lecture 2 - Loops.MP3", "audio/mpeg"),
                 ("Lecture 1 - Intro.webm", "video/webm"), ("Lecture 1 - Intro (notes).pdf", "application/pdf"),
                 ("Week 3.m4a", "application/octet-stream"),  # a type Drive didn't know: the extension decides
                 ("setup.bin", "application/octet-stream"), ("Q&A <live>.ogg", "audio/ogg"), ("Talk", "")]
        entries = [("file", drive_id(i), name, mime) for i, (name, mime) in enumerate(files)]
        entries += [("folder", drive_id(20, "d"), "Exercises", ""), ("doc", drive_id(21), "Syllabus", "application/vnd.google-apps.document"),
                    ("file", drive_id(0), "Lecture 10 - Recursion.mp4", "video/mp4")]  # the same file twice: once
        self.pages.folder(FOLDER, "Physics & Maths", entries)
        found = drive.inspect_folder(f"https://drive.google.com/drive/u/1/folders/{FOLDER}?usp=drive_link")
        self.assertEqual((found["site"], found["kind"], found["id"], found["title"], found["url"], found["count"],
                          found["duration"], found["cut"]),
                         ("drive", "folder", FOLDER, "Physics & Maths", drive.folder_url(FOLDER), 5, None, False))
        self.assertEqual([(e["index"], e["name"], e["title"]) for e in found["entries"]],
                         [(1, "Lecture 1 - Intro.webm", "Lecture 1 - Intro"), (2, "lecture 2 - Loops.MP3", "lecture 2 - Loops"),
                          (3, "Lecture 10 - Recursion.mp4", "Lecture 10 - Recursion"), (4, "Q&A <live>.ogg", "Q&A <live>"),
                          (5, "Week 3.m4a", "Week 3")], "natural name order, the names' entities read")
        self.assertEqual([(e["id"], e["url"], e["type"], e["available"]) for e in found["entries"]][:2],
                         [(drive_id(2), drive.file_url(drive_id(2)), "video/webm", True),
                          (drive_id(1), drive.file_url(drive_id(1)), "audio/mpeg", True)])
        self.assertEqual(found["skipped"], {"folders": 1, "files": 4}, "the subfolder; the pdf, bin, Docs file and the "
                                                                       "file with no type and no extension")
        self.assertEqual(self.pages.requests, [(f"https://drive.google.com/embeddedfolderview?id={FOLDER}",
                                                {"Host": "drive.google.com", "User-agent": "Sedjem", "Accept": "text/html"})],
                         "one request, to Drive's own page, with nothing of the link but the id")

    def test_names_in_natural_order(self):
        names = ["Lecture 10.mp4", "lecture 9.mp4", "Lecture 1.mp4", "Lecture 01b.mp4", "Intro.mp4", "محاضرة ١٠.mp3",
                 "محاضرة ٢.mp3", "Part 2 - 10.mp4", "Part 2 - 9.mp4", "9" * 600 + ".mp4"]
        self.assertEqual(sorted(names, key=lambda n: (drive.natural_key(n), n)),
                         ["9" * 600 + ".mp4", "Intro.mp4", "Lecture 1.mp4", "Lecture 01b.mp4", "lecture 9.mp4",
                          "Lecture 10.mp4", "Part 2 - 9.mp4", "Part 2 - 10.mp4", "محاضرة ٢.mp3", "محاضرة ١٠.mp3"])

    def test_what_counts_as_audio_or_video(self):
        for name, mime in (("a.mp4", "video/mp4"), ("a", "audio/x-m4a"), ("notes.pdf", "video/quicktime"),
                           ("talk.opus", ""), ("talk.MKV", "application/octet-stream")):
            self.assertTrue(drive.is_media(name, mime), (name, mime))
        for name, mime in (("a.mp4", "application/pdf"), ("a.mp3", "text/plain"), ("talk", ""), ("talk.bin", ""),
                           ("Week 1.2", "application/octet-stream"), ("x.mp4", "application/vnd.google-apps.folder")):
            self.assertFalse(drive.is_media(name, mime), (name, mime))
        for src, mime in (("https://drive-thirdparty.googleusercontent.com/16/type/video/webm", "video/webm"),
                          ("https://drive-thirdparty.googleusercontent.com/16/type/application/vnd.google-apps.document",
                           "application/vnd.google-apps.document"),
                          ("https://drive-thirdparty.googleusercontent.com/16/type/Audio/MPEG", "audio/mpeg"),
                          ("https://lh3.googleusercontent.com/drive-storage/AJQWtBO=s190", ""),  # a thumbnail
                          ("http://drive-thirdparty.googleusercontent.com/16/type/video/mp4", ""),
                          ("https://evil.example/16/type/video/mp4", ""),
                          ("https://drive-thirdparty.googleusercontent.com/16/type/video", ""),
                          ("https://drive-thirdparty.googleusercontent.com/16/type/video/mp4%20x", ""), ("", ""), (None, "")):
            self.assertEqual(drive.icon_type(src), mime, src)

    def test_a_subfolder_is_known_by_its_icon_too(self):
        """A subfolder links to …/drive/folders/<id>, and its icon says Folder (it names no type, as real ones don't):
        either is enough."""
        sub = drive_id(30, "d")
        page = folder_page("Course", [("folder", sub, "Week 1", ""), ("file", drive_id(31), "Lecture.mp4", "video/mp4")])
        page = page.replace(f"https://drive.google.com/drive/folders/{sub}", f"https://drive.google.com/open?id={sub}")
        self.pages.answers[FOLDER] = (200, {"Content-Type": "text/html; charset=utf-8"}, page)
        found = drive.inspect_folder(drive.folder_url(FOLDER))
        self.assertEqual((found["count"], found["skipped"]), (1, {"folders": 1, "files": 0}))

    def test_the_counts_of_what_was_skipped(self):
        self.assertEqual(drive.skipped_words({"folders": 2, "files": 1}), "2 subfolders and 1 other file")
        self.assertEqual(drive.skipped_words({"folders": 1, "files": 0}), "1 subfolder")
        self.assertEqual(drive.skipped_words({"folders": 0, "files": 3}), "3 other files")
        self.assertEqual(drive.skipped_words({}), "")

    def test_an_empty_folder_and_one_without_audio_or_video(self):
        self.pages.folder(FOLDER, "Empty", [])
        self.assertEqual(self.refused(), "this folder is empty")
        self.pages.folder(FOLDER, "Papers", [("folder", drive_id(40, "d"), "2025", ""), ("folder", drive_id(41, "d"), "2026", ""),
                                             ("file", drive_id(42), "paper.pdf", "application/pdf")])
        self.assertEqual(self.refused(), "this folder has no audio or video files (2 subfolders and 1 other file skipped)")

    def test_a_private_or_removed_folder(self):
        """Drive answers a made-up id with 404; a folder that isn't shared gets the same message, as a file does."""
        self.pages.folder(FOLDER, "Mine", [("file", drive_id(50), "a.mp4", "video/mp4")])
        self.pages.private.add(FOLDER)
        self.assertEqual(self.refused(), drive.PRIVATE_FOLDER)
        self.assertEqual(self.refused(drive.folder_url(drive_id(99, "d"))), drive.PRIVATE_FOLDER)
        self.assertEqual(drive.PRIVATE_FOLDER,
                         "This folder isn't shared with anyone who has the link. Sedjem can't sign in to Google Drive yet.")
        for status in (401, 403):
            self.pages.answers[FOLDER] = (status, {"Content-Type": "text/html"}, "No")
            self.assertEqual(self.refused(), drive.PRIVATE_FOLDER, status)
        self.pages.answers[FOLDER] = (302, {"Location": "https://accounts.google.com/ServiceLogin?continue=x"}, "")
        self.assertEqual(self.refused(), drive.PRIVATE_FOLDER, "sent to sign in")

    def test_a_redirect_is_never_followed(self):
        self.assertTrue(any(isinstance(h, drive._NoRedirect) for h in OPENER.handlers), "the app's opener has the guard")
        for to in ("https://evil.example/embeddedfolderview?id=x", "https://drive.google.com/embeddedfolderview?id=other"):
            self.pages.requests.clear()
            self.pages.answers[FOLDER] = (302, {"Location": to}, "")
            self.assertEqual(self.refused(), "Google Drive didn't list the folder: it answered 302", to)
            self.assertEqual(len(self.pages.requests), 1, "nothing asked of the address it was sent to")

    def test_answers_that_arent_a_list(self):
        html_type = {"Content-Type": "text/html; charset=utf-8"}
        for answer, error in (((429, html_type, "Too many"), drive.BUSY_FOLDER),
                              ((500, html_type, "Oops"), "Google Drive didn't list the folder: it answered 500"),
                              ((200, {"Content-Type": "application/json"}, "{}"), drive.NOT_LISTED),
                              ((200, html_type, "<html><title>Sign in - Google Accounts</title><form></form></html>"),
                               drive.NOT_LISTED),  # a page of another kind, though it came with 200
                              (urllib.error.URLError("timed out"), "Google Drive couldn't be reached: timed out")):
            self.pages.answers[FOLDER] = answer
            self.assertEqual(self.refused(), error, answer)

    def test_a_page_too_long_to_read(self):
        self.pages.folder(FOLDER, "Big", [("file", drive_id(i), f"Lecture {i}.mp4", "video/mp4") for i in range(60)])
        with mock.patch.object(drive, "MAX_PAGE", 20_000):
            self.assertEqual(self.refused(), drive.TOO_LONG)
        with mock.patch.object(drive, "MAX_FILES", 25):
            found = drive.inspect_folder(drive.folder_url(FOLDER))
        self.assertEqual((found["count"], found["cut"], found["entries"][-1]["name"]), (25, True, "Lecture 24.mp4"),
                         "the first files in natural order, and a note that there are more")

    def test_a_resource_key_is_passed_on(self):
        self.pages.folder(FOLDER, "Old share", [("file", drive_id(60), "Lecture.mp4", "video/mp4")])
        drive.inspect_folder(f"https://drive.google.com/drive/folders/{FOLDER}?resourcekey=0-Old_Key-1&usp=sharing")
        self.assertEqual(self.pages.requests[0][0],
                         f"https://drive.google.com/embeddedfolderview?id={FOLDER}&resourcekey=0-Old_Key-1")


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

    def test_a_name_without_an_extension(self):
        """A name such as "Week 1.2" has no extension: it is taken whole, and the file is saved as source.bin; only a
        file whose name is clearly another kind of file, and that Drive doesn't play, is refused."""
        week = self.fake.file(drive_id(13), "Week 1.2", seconds=2)
        dated = self.fake.file(drive_id(14), "Meeting 2026.11.19", kind=AUDIO)
        named = self.fake.file(drive_id(15), "Recording.pdf", kind=AUDIO)  # misnamed, but Drive knows its length
        for fid, title in ((week, "Week 1.2"), (dated, "Meeting 2026.11.19"), (named, "Recording")):
            self.assertEqual(drive.inspect(self.cfg, drive.file_url(fid))["title"], title, fid)
        got = drive.download(self.cfg, {"drive_id": week, "source_url": drive.file_url(week)}, self.dir, lambda *a: None,
                             lambda: False)
        self.assertEqual((got["source_name"], [p.name for p in self.dir.iterdir()]), ("Week 1.2", ["source.bin"]))

    def test_a_folder_never_reaches_yt_dlp(self):
        pages = FolderPages()
        pages.folder(FOLDER, "Course", [("file", self.video, "01 Introduction.mp4", "video/mp4")])
        with mock.patch.object(drive, "_ydl", side_effect=AssertionError("yt-dlp was asked")), \
                mock.patch.object(drive, "OPENER", pages.opener()):
            for url in FOLDER_LINKS:
                found = drive.inspect(self.cfg, url)
                self.assertEqual((found["kind"], found["id"], found["count"]), ("folder", FOLDER, 1), url)

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

    def test_a_download_redirected_elsewhere_is_stopped(self):
        """The link was read from Drive's hosts, but the download's own request is sent on to another site: its
        answer is refused before anything of it is written. The request reached that site (yt-dlp follows the
        redirect); only what it sends is refused."""
        fid = self.google.file(drive_id(25), "Talk.mp4", b"x" * 5000, moved="evil.example")
        self.assertEqual(drive.inspect(self.cfg, drive.file_url(fid))["title"], "Talk")
        with self.assertRaises(drive.DriveError) as e:
            drive.download(self.cfg, {"drive_id": fid, "source_url": drive.file_url(fid)}, self.tmp, lambda *a: None,
                           lambda: False)
        self.assertEqual(str(e.exception), "Google Drive sent the download to another site (evil.example), so nothing "
                                           "of it was kept")
        self.assertEqual(list(self.tmp.glob("source.*")), [])
        self.assertIn("evil.example", self.hosts(), "(what the check can't prevent: the request itself)")

    def stopped(self, fid, error, **patches):
        """The download of fid stops with `error`, after a part of it was written, and leaves no file behind."""
        deleted, unlink = [], Path.unlink

        def spy(path, missing_ok=False):
            if path.parent == self.tmp and path.is_file():
                deleted.append((path.name, path.stat().st_size))
            return unlink(path, missing_ok=missing_ok)
        with mock.patch.object(drive, "free_space", patches.get("free_space", drive.free_space)), \
                mock.patch.object(Path, "unlink", spy):
            with self.assertRaisesRegex(drive.DriveError, error):
                drive.download(self.cfg, {"drive_id": fid, "source_url": drive.file_url(fid)}, self.tmp,
                               lambda *a: None, lambda: False)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), [], "nothing is left")
        self.assertTrue(any(name.endswith(".part") and size for name, size in deleted), f"a part was deleted: {deleted}")

    def test_over_the_limit_the_part_is_deleted(self):
        self.cfg.max_upload = 100_000
        sized = self.google.file(drive_id(26), "Big.mp4", b"y" * 300_000)
        unsized = self.google.file(drive_id(27), "Big unsized.mp4", b"y" * 300_000, sized=False)
        self.stopped(sized, "larger than the 0 GB limit")
        self.stopped(unsized, "larger than the 0 GB limit")

    def test_a_size_that_understates_the_file_is_not_trusted(self):
        """An answer that says 1 000 bytes and sends 300 000: the limit holds on the bytes that come."""
        self.cfg.max_upload = 100_000
        fid = self.google.file(drive_id(31), "Understated.mp4", b"u" * 300_000, stated=1000)
        self.stopped(fid, "larger than the 0 GB limit")

    def test_too_little_free_space_the_part_is_deleted(self):
        asked = []

        def full(folder):
            asked.append(folder)
            return 5e8  # less than the margin
        sized = self.google.file(drive_id(28), "Sized.mp4", b"z" * 300_000)
        unsized = self.google.file(drive_id(29), "Unsized.mp4", b"z" * 300_000, sized=False)
        self.stopped(sized, "not enough free disk space", free_space=full)
        self.stopped(unsized, "not enough free disk space", free_space=full)
        self.assertEqual(len(asked), 2, "once for each: the sized one as it starts, the other at its first check")

    def test_without_a_size_the_free_space_is_checked_as_it_comes(self):
        free = iter([5e9, 5e9, 5e8])  # enough at first, then less than the margin
        asked = []

        def shrinking(folder):
            asked.append(folder)
            return next(free)
        fid = self.google.file(drive_id(30), "Long.mp4", b"w" * 300_000, sized=False)
        with mock.patch.object(drive, "CHECK_EVERY", 100_000):
            self.stopped(fid, "not enough free disk space", free_space=shrinking)
        self.assertEqual(len(asked), 3, "at the start, then each 100 000 bytes")

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
        cls.sound = fake.file(drive_id(51), "Interview 2026.11.19", kind=AUDIO, seconds=2)
        cls.later = fake.file(drive_id(52), "Unshared later.mp4", seconds=2)
        cls.lectures = [fake.file(drive_id(53), "Lecture 10 - Filters.mp4", seconds=2),
                        fake.file(drive_id(54), "Lecture 2 - Signals.m4a", kind=AUDIO, seconds=2),
                        fake.file(drive_id(55), "Lecture 1 - Welcome.mp4", seconds=2)]
        cls.pages = pages = FolderPages()
        cls.folder = pages.folder(drive_id(56, "d"), "Signals and systems", [
            ("file", drive_id(53), "Lecture 10 - Filters.mp4", "video/mp4"), ("folder", drive_id(57, "d"), "Exams", ""),
            ("file", drive_id(54), "Lecture 2 - Signals.m4a", "audio/x-m4a"),
            ("file", drive_id(58), "Slides.pdf", "application/pdf"),
            ("file", drive_id(55), "Lecture 1 - Welcome.mp4", "video/mp4")])
        cls.ran = []

        def hosted(cfg, model, job_dir, job, on_progress, cancelled):
            cls.ran.append(job["drive_id"])
            return {"lines": [{**LINE[0], "text": f"Spoken words of {job['title']}"}], "meta": {}}
        cls.patches = [mock.patch.object(drive, "_ydl", fake.ydl), mock.patch.object(engines, "run_hosted", hosted),
                       mock.patch.object(drive, "OPENER", pages.opener())]
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

    def test_2_a_sound_file_whose_name_has_no_extension(self):
        status, r = self.add(f"https://drive.google.com/open?id={self.sound}")
        self.assertEqual(status, 201, r)
        self.assertEqual(r["jobs"][0]["title"], "Interview 2026.11.19", "the whole name: it has no extension")
        done = self.wait(r["jobs"][0]["id"])
        self.assertEqual((done["job"]["status"], done["has_video"]), ("done", False), done["job"].get("error"))

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
        for url, error in ((drive.file_url(drive_id(70)), drive.PRIVATE), (FOLDER_LINKS[0], drive.PRIVATE_FOLDER),
                           ("https://vimeo.com/1", server.NOT_A_LINK), ("https://drive.google.com/drive/my-drive", drive.NOT_DRIVE)):
            self.assertEqual(self.call("POST", "/api/link/inspect", {"url": url}), (400, {"error": error}), url)
        self.assertEqual(self.add(FOLDER_LINKS[1]), (400, {"error": drive.PRIVATE_FOLDER}), "not shared, or removed")
        forged = drive.file_url(drive_id(71))  # an id that isn't Drive's never becomes a job
        self.app.inspected[forged] = (time.time(), {"site": "drive", "kind": "file", "id": drive_id(71), "title": "x",
                                                    "url": forged, "count": 1, "entries": [{"id": "../../etc", "title": "x",
                                                                                            "name": "x.mp4", "index": 1,
                                                                                            "available": True}]})
        status, r = self.add(forged)
        self.assertEqual((status, r["error"]), (400, "a file has an id Google Drive doesn't use"))


    def test_5_a_folder_becomes_a_course(self):
        url = f"https://drive.google.com/drive/folders/{self.folder}?usp=sharing"
        status, found = self.call("POST", "/api/link/inspect", {"url": url})
        self.assertEqual((status, found["kind"], found["title"], found["count"], found["skipped"]),
                         (200, "folder", "Signals and systems", 3, {"folders": 1, "files": 1}), found)
        self.assertEqual([e["id"] for e in found["entries"]], [self.lectures[2], self.lectures[1], self.lectures[0]],
                         "natural name order: lecture 1, 2, then 10")
        self.ran.clear()
        with mock.patch.object(self.app.usage, "count") as count:
            status, r = self.add(url, entries=[self.lectures[0], self.lectures[2]], stream=True, captions=True)
        self.assertEqual(status, 201, r)
        self.assertEqual(count.call_args_list, [], "no usage count: not a YouTube course")
        course = {"id": self.folder, "title": "Signals and systems", "url": drive.folder_url(self.folder), "count": 3}
        self.assertEqual([(j["title"], j["source_name"], j["drive_id"], j["source_url"], j["course"], j["youtube_id"],
                           j["stream"]) for j in r["jobs"]],
                         [("Lecture 1 - Welcome", "Lecture 1 - Welcome.mp4", self.lectures[2], drive.file_url(self.lectures[2]),
                           {**course, "index": 1}, None, None),
                          ("Lecture 10 - Filters", "Lecture 10 - Filters.mp4", self.lectures[0], drive.file_url(self.lectures[0]),
                           {**course, "index": 3}, None, None)],
                         "the picked files, numbered in the folder's order, with the folder as their course")
        done = [self.wait(j["id"]) for j in r["jobs"]]
        self.assertEqual([d["job"]["status"] for d in done], ["done", "done"], [d["job"].get("error") for d in done])
        self.assertEqual(self.ran, [self.lectures[2], self.lectures[0]], "downloaded and transcribed in the folder's order")
        time.sleep(0.3)
        self.assertFalse(any((self.tmp / "courses").rglob("*")), "a Drive course has no files in the courses folder")
        status, files = self.call("POST", "/api/export", {"ids": [j["id"] for j in r["jobs"]], "format": "txt", "as": "files"})
        self.assertEqual((status, [f["path"] for f in files["files"]]),
                         (200, ["Signals-and-systems/01-Lecture-1---Welcome.txt", "Signals-and-systems/03-Lecture-10---Filters.txt"]),
                         "exported as a course")
        self.assertEqual(usage.library_facts(self.app.store.list())["courses"], "0", "only YouTube courses are counted")
        status, again = self.add(url, entries=[self.lectures[1]])
        self.assertEqual((status, again["jobs"][0]["course"]), (201, {**course, "index": 2}), "the same course again")
        self.wait(again["jobs"][0]["id"])

    def test_6_a_private_folder(self):
        hidden = self.pages.folder(drive_id(59, "d"), "Private course", [("file", drive_id(53), "Lecture.mp4", "video/mp4")])
        self.pages.private.add(hidden)
        self.assertEqual(self.call("POST", "/api/link/inspect", {"url": drive.folder_url(hidden)}),
                         (400, {"error": drive.PRIVATE_FOLDER}))
        self.assertEqual(self.add(drive.folder_url(hidden)), (400, {"error": drive.PRIVATE_FOLDER}))
        forged = drive.folder_url(drive_id(71, "d"))  # an id that isn't Drive's never becomes a course
        self.app.inspected[forged] = (time.time(), {"site": "drive", "kind": "folder", "id": "../../x", "title": "x",
                                                    "url": forged, "count": 1, "entries": [{"id": drive_id(53), "title": "x",
                                                                                            "name": "x.mp4", "index": 1,
                                                                                            "available": True}]})
        self.assertEqual(self.add(forged), (400, {"error": "a file has an id Google Drive doesn't use"}))


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

    def lecture(self, jid, index, fid, title):
        """A finished lecture of a Drive folder's course, written as the app writes it."""
        course = {"id": DRIVE_COURSE, "title": "Signals and systems", "url": drive.folder_url(DRIVE_COURSE), "count": 3,
                  "index": index}
        folder = self.home / "app_data" / "jobs" / jid
        folder.mkdir(parents=True)
        job = {"id": jid, "title": title, "created": f"2026-10-20T09:00:0{index}+03:00", "model": "cohere",
               "model_title": "Cohere Transcribe Arabic", "kind": "local", "status": "done", "audio_s": 30.0,
               "speaker_names": {}, "finished": f"2026-10-20T09:10:0{index}+03:00", "drive_id": fid,
               "source_url": drive.file_url(fid), "course": course}
        (folder / "job.json").write_text(json.dumps(job), encoding="utf-8")
        (folder / "transcript.json").write_text(json.dumps({"lines": [{**LINE[0], "text": f"Words of {title}"}]}),
                                                encoding="utf-8")

    def test_assistants_see_a_drive_folder_as_a_course(self):
        self.lecture("20261020-090000-e101", 1, drive_id(91), "Lecture 1 - Welcome")
        self.lecture("20261020-090000-e103", 3, drive_id(93), "Lecture 10 - Filters")
        folder_link = drive.folder_url(DRIVE_COURSE)
        text = self.tool("list_courses")
        self.assertIn(f"- Signals and systems\n  course id {DRIVE_COURSE} · {folder_link}\n  2 of 3 lectures transcribed",
                      text, "listed among the courses, with the folder's link")
        self.assertNotIn("youtube.com", text)
        text = self.tool("course_outline", course="signals and systems")
        self.assertIn(f"course id {DRIVE_COURSE} · folder {folder_link}", text)
        self.assertIn("3 lectures in the folder, 2 transcribed", text)
        self.assertIn(f"3. Lecture 10 - Filters · 00:30 · id 20261020-090000-e103 · done\n   {drive.file_url(drive_id(93))}", text)
        self.assertIn("Not added to Sedjem: lecture 2.", text)
        text = self.tool("get_course_text", course=DRIVE_COURSE)
        self.assertIn(f"Folder: {folder_link}", text)
        self.assertIn("in the folder's order. Times in brackets are minutes:seconds into each file.", text)
        self.assertIn(f"## Lecture 3: Lecture 10 - Filters (00:30)\n\nFile: {drive.file_url(drive_id(93))}", text)
        self.assertNotIn("Playlist", text)
        self.assertIn(f"  course Signals and systems ({DRIVE_COURSE}), lecture 1 of 3 · {drive.file_url(drive_id(91))}",
                      self.tool("list_transcriptions", kind="course"))
        self.assertIn("Lecture 10 - Filters", self.tool("get_transcript", course=DRIVE_COURSE, lecture=3))
        assistants.set_hidden(self.mcp.cfg, f"course:{DRIVE_COURSE}", True)  # hidden as a YouTube course is
        self.assertIn("There are no courses", self.tool("list_courses"))
        self.assertTrue(assistants.HIDDEN_KEY.match(f"course:{drive_id(1, 'd' * 60)}"), "a long Drive id fits")

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
