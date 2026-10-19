"""Tests for YouTube links (app/youtube.py) and the courses folder (app/courses.py): reading a link, choosing
the streams, joining them into video.mp4 with PyAV, captions made by people as the transcript (WebVTT), a
playlist made into jobs in playlist order, the downloads cancelled, failing and tried again, and the course
files written when a lecture finishes and rewritten when it is edited or deleted. yt-dlp is replaced by a
stand-in that writes media made with PyAV (tests/youtube_support.py), so nothing is fetched from the network.
Run: .venv/bin/python -m unittest tests.test_youtube -v
"""
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import av

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import courses, engines, server, youtube  # noqa: E402
from app.config import Config  # noqa: E402
from app.engines import Cancelled  # noqa: E402
from app.jobs import Store  # noqa: E402
from app.server import make_server  # noqa: E402
from youtube_support import FakeYouTube, formats, vtt, write_media  # noqa: E402

TT = "https://www.youtube.com/api/timedtext?v=jNQXAC9IVRw&fmt=vtt"
DENO = {"name": "Deno", "version": "2.9.5", "downloaded": True}
VTT_EN = vtt((1.2, 3.36, "All right, so here we are, in front of the"), (3.36, 4.0, "elephants."),
             (5.3, 7.9, "the cool thing about these guys"), (7.9, 9.0, "is that they have long trunks"),
             (9.0, 9.0, "is that they have long trunks"), (14.4, 15.7, "and that's cool!"))
VTT_AR = vtt((0.5, 2.0, "أهلاً بكم في الدرس الأول"), (2.0, 4.0, "اليوم نتكلم عن ال variables."))


class Links(unittest.TestCase):
    def test_links_are_rebuilt_from_their_parts(self):
        watch = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
        for url, want in (
                (watch, watch), ("youtu.be/jNQXAC9IVRw?t=10", watch), ("https://m.youtube.com/shorts/jNQXAC9IVRw", watch),
                ("http://music.youtube.com/watch?v=jNQXAC9IVRw&feature=share#t=3", watch),
                ("https://www.youtube-nocookie.com/embed/jNQXAC9IVRw", watch), ("HTTPS://WWW.YOUTUBE.COM:443/watch?v=jNQXAC9IVRw", watch),
                ("https://www.youtube.com/watch?v=jNQXAC9IVRw&list=PL123abc&index=3", f"{watch}&list=PL123abc"),
                ("https://www.youtube.com/playlist?list=PL123abc&si=x", "https://www.youtube.com/playlist?list=PL123abc")):
            self.assertEqual(youtube.check_url(url), want, url)

    def test_only_youtube_itself(self):
        for url in ("", "https://vimeo.com/1", "file:///etc/passwd", "javascript:alert(1)", "ftp://www.youtube.com/watch?v=jNQXAC9IVRw",
                    "https://www.youtube.com@evil.example/watch?v=jNQXAC9IVRw",      # the host is evil.example
                    "https://user:pw@www.youtube.com/watch?v=jNQXAC9IVRw",           # a user name in the address
                    "https://youtube.com.evil.example/watch?v=jNQXAC9IVRw", "https://wwwyoutube.com/watch?v=jNQXAC9IVRw",
                    "https://www.youtube.co/watch?v=jNQXAC9IVRw", "https://evil.example/?u=https://www.youtube.com/",
                    "https://www.youtube.com:8080/watch?v=jNQXAC9IVRw", "https://www.youtube.com:99999/watch?v=jNQXAC9IVRw",
                    "https://www.youtube.com/redirect?q=https://evil.example/", "https://www.youtube.com/attribution_link?u=/x",
                    "https://www.youtube.com/watch?v=../../etc/x", "https://www.youtube.com/watch?v=jNQXAC9IVRw&list=../x",
                    "https://youtu.be/", "https://www.youtube.com/"):
            with self.assertRaises(youtube.YouTubeError, msg=url):
                youtube.check_url(url)
        with self.assertRaisesRegex(youtube.YouTubeError, "channel"):
            youtube.check_url("https://www.youtube.com/@teacher/videos")

    def test_only_youtube_extractors(self):
        """The real yt-dlp, offline: with the app's settings it has no other extractor, so a page elsewhere
        can't even be tried."""
        with youtube._ydl(Config(home=tempfile.gettempdir())) as ydl:
            names = {ie.IE_NAME for ie in ydl._ies.values()}
            self.assertEqual(names, {"youtube", "youtube:tab", "youtube:playlist"})
            with self.assertRaises(Exception):
                ydl.extract_info("https://evil.example/page.html", download=False, process=False)

    def test_errors_read_well(self):
        self.assertEqual(youtube.clean_error(Exception("ERROR: [youtube] jNQXAC9IVRw: Video unavailable. This video is private")),
                         "Video unavailable. This video is private")
        self.assertEqual(youtube.clean_error(Exception("ERROR: [youtube] abcdefghijk: Sign in to confirm you're not a bot. "
                                                       "Use --cookies-from-browser or --cookies for the authentication.")),
                         "Sign in to confirm you're not a bot")


class Formats(unittest.TestCase):
    def test_h264_up_to_720p_and_aac(self):
        video, audio, both, sound = youtube.pick_formats(formats("x"))
        self.assertEqual(video["format_id"], "136", "720p at 30 fps, downloaded directly: not 1080p, 60 fps, VP9 or HLS")
        self.assertEqual(audio["format_id"], "140", "AAC, not its drc copy")
        self.assertEqual(both["format_id"], "18")
        self.assertEqual(sound["format_id"], "251", "any codec will do when only the sound is kept")

    def test_the_original_language_first(self):
        dub = dict(formats("x")[3], format_id="140-1", language_preference=-1, abr=200)
        own = dict(formats("x")[3], format_id="140-0", language_preference=10, abr=100)
        self.assertEqual(youtube.pick_formats([dub, own])[1]["format_id"], "140-0")

    def test_only_streams_from_youtube(self):
        elsewhere = dict(formats("x")[5], format_id="136-x", tbr=9999, url="https://evil.example/v.mp4")
        tricky = dict(formats("x")[5], format_id="136-y", tbr=9998, url="https://googlevideo.com.evil.example/v.mp4")
        self.assertEqual(youtube.pick_formats([elsewhere, tricky, *formats("x")])[0]["format_id"], "136")
        self.assertEqual(youtube.human_captions({"subtitles": {"en": [{"ext": "vtt", "url": "https://evil.example/s.vtt"}]}}), {})

    def test_without_h264_only_the_sound(self):
        video, audio, both, sound = youtube.pick_formats(formats("x", h264=False))
        self.assertEqual((video, audio, both), (None, None, None))
        self.assertEqual(sound["format_id"], "251")
        self.assertEqual(youtube.pick_formats([dict(f, has_drm=True) for f in formats("x")]), (None, None, None, None))


class Captions(unittest.TestCase):
    def test_only_captions_made_by_people(self):
        info = {"subtitles": {"en": [{"ext": "vtt", "url": TT, "name": "English"}], "de": [{"ext": "vtt", "url": TT}],
                              "live_chat": [{"ext": "json", "url": TT}], "ar-EG": [{"ext": "srv3", "url": TT}]},
                "automatic_captions": {"ar": [{"ext": "vtt", "url": TT}]}}
        self.assertEqual(youtube.human_captions(info), {"en": "English"}, "German, a chat and no WebVTT are left out")
        self.assertEqual(youtube.pick_captions(info, "ar"), "en", "the automatic Arabic ones are never used")

    def test_in_the_video_language(self):
        track = [{"ext": "vtt", "url": TT}]
        both = {"subtitles": {"en-GB": track, "en": track, "ar": track}}
        self.assertEqual(youtube.pick_captions({**both, "language": "en"}, "ar"), "en")
        self.assertEqual(youtube.pick_captions({**both, "language": "ar"}, "en"), "ar")
        self.assertEqual(youtube.pick_captions({**both, "language": None}, "en"), "en", "unknown: the job's language")
        self.assertEqual(youtube.pick_captions({**both, "language": None}, "auto"), "ar")
        self.assertIsNone(youtube.pick_captions({"subtitles": {"en": track}, "language": "ar"}, "ar"),
                          "an Arabic video with English captions: a translation, not a transcript")
        self.assertIsNone(youtube.pick_captions({**both, "language": "fr"}, "ar"))

    def test_webvtt(self):
        text = ("﻿WEBVTT\r\nKind: captions\r\n\r\nSTYLE\r\n::cue { color: red }\r\n\r\nNOTE a comment\r\n\r\n"
                "intro\r\n00:01.000 --> 00:02.500 align:start position:0%\r\n<v Mona>Hello &amp; <i>welcome</i></v>\r\n"
                "<00:00:01.500><c>to</c> the class\r\n\r\n"
                "1:00:03,250 --> 1:00:04,000\r\n‏مرحباً&nbsp;بكم\r\n\r\n00:00:05.000 --> 00:00:06.000\r\n\r\n")
        self.assertEqual(youtube.parse_vtt(text), [
            {"start": 1.0, "end": 2.5, "text": "Hello & welcome to the class"},
            {"start": 3603.25, "end": 3604.0, "text": "مرحباً بكم"}])

    def test_cues_become_sentences(self):
        lines = youtube.caption_lines(youtube.parse_vtt(VTT_EN))
        self.assertEqual([x["text"] for x in lines], [
            "All right, so here we are, in front of the elephants.",
            "the cool thing about these guys is that they have long trunks",
            "and that's cool!"])
        self.assertEqual((lines[0]["start"], lines[0]["end"], lines[0]["speaker"]), (1.2, 4.0, "1"))
        long = [{"start": i * 4.0, "end": i * 4.0 + 4, "text": f"part {i}"} for i in range(20)]
        self.assertTrue(all(x["end"] - x["start"] <= 30 for x in youtube.caption_lines(long)), "no line over 30 s")


class Remux(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sedjem-remux-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, out, size):
        data = out.read_bytes()
        self.assertLess(data.find(b"moov"), data.find(b"mdat"), "the index first, so it plays while it loads")
        with av.open(str(out)) as c:
            kinds = {s.type: s.codec_context.name for s in c.streams}
            self.assertEqual(kinds, {"video": "h264", "audio": "aac"})
            self.assertAlmostEqual(c.duration / 1e6, 2, delta=0.2)
        self.assertEqual(size, (160, 90))

    def test_video_and_sound_joined(self):
        write_media(self.tmp / "v.mp4", "video", 2)
        write_media(self.tmp / "a.m4a", "audio", 2)
        self.check(self.tmp / "video.mp4", youtube.remux([self.tmp / "v.mp4", self.tmp / "a.m4a"], self.tmp / "video.mp4"))
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["a.m4a", "v.mp4", "video.mp4"])

    def test_one_file_with_both(self):
        write_media(self.tmp / "b.mp4", "both", 2)
        self.check(self.tmp / "video.mp4", youtube.remux([self.tmp / "b.mp4"], self.tmp / "video.mp4"))

    def test_cancelled(self):
        write_media(self.tmp / "b.mp4", "both", 2)
        with self.assertRaises(Cancelled):
            youtube.remux([self.tmp / "b.mp4"], self.tmp / "video.mp4", lambda: True)
        self.assertEqual([p.name for p in self.tmp.iterdir()], ["b.mp4"], "nothing half written is left")


class InspectAndDownload(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sedjem-yt-"))
        self.cfg = Config(home=self.tmp)
        self.fake = FakeYouTube()
        self.fake.video("vid00000001", "Intro", captions={"en": VTT_EN}, language="en")
        self.fake.video("vid00000002", "Loops", auto=["en"])
        self.fake.video("vid00000003", "Only VP9", h264=False)
        self.fake.playlist("PLcourse", "Python: a course", ["vid00000001", "vid00000002"], gone=["[Private video]"])
        self.patches = [mock.patch.object(youtube, "_ydl", self.fake.ydl),
                        mock.patch.object(youtube, "runtime", lambda cfg: DENO)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_playlist(self):
        found = youtube.inspect(self.cfg, "https://www.youtube.com/playlist?list=PLcourse")
        self.assertEqual((found["kind"], found["title"], found["count"], found["duration"]), ("playlist", "Python: a course", 3, 6))
        self.assertEqual([(e["index"], e["title"], e["available"], e["captions"]) for e in found["entries"]],
                         [(1, "Intro", True, ["en"]), (2, "Loops", True, []), (3, "[Private video]", False, None)])
        self.assertEqual([e["use"] for e in found["entries"]], ["en", None, None], "the captions a job would take")
        self.assertEqual(found["runtime"], DENO)
        self.fake.video("vid00000009", "شرح بالعربي", captions={"en": VTT_EN}, language="ar")
        alone = youtube.inspect(self.cfg, "https://www.youtube.com/watch?v=vid00000009")["entries"][0]
        self.assertEqual((alone["captions"], alone["use"]), (["en"], None), "English captions of an Arabic video: a translation")
        quick = youtube.inspect(self.cfg, "https://www.youtube.com/playlist?list=PLcourse", check_captions=False)
        self.assertEqual([e["captions"] for e in quick["entries"]], [None, None, None], "not looked up")

    def test_a_video_a_channel_and_a_live_stream(self):
        found = youtube.inspect(self.cfg, "https://youtu.be/vid00000001")
        self.assertEqual((found["kind"], found["count"], found["entries"][0]["captions"]), ("video", 1, ["en"]))
        with self.assertRaisesRegex(youtube.YouTubeError, "channel"):
            youtube.inspect(self.cfg, "https://www.youtube.com/@teacher")
        self.fake.video("vidlive0001", "Live", live="is_live")
        with self.assertRaisesRegex(youtube.YouTubeError, "live stream"):
            youtube.inspect(self.cfg, "https://www.youtube.com/watch?v=vidlive0001")
        with self.assertRaisesRegex(youtube.YouTubeError, "Video unavailable"):
            youtube.inspect(self.cfg, "https://www.youtube.com/watch?v=nosuchvideo")

    def test_what_does_not_come_from_youtube_is_refused(self):
        self.fake.video("vidforeign1", "Elsewhere")
        self.fake.foreign.add("vidforeign1")
        with self.assertRaisesRegex(youtube.YouTubeError, "did not lead to a YouTube"):
            youtube.inspect(self.cfg, "https://youtu.be/vidforeign1")
        self.fake.redirects["vidredirect"] = "https://evil.example/next"
        with self.assertRaisesRegex(youtube.YouTubeError, "paste the link"):
            youtube.inspect(self.cfg, "https://youtu.be/vidredirect")
        self.fake.redirects["vidredirec2"] = "https://www.youtube.com/watch?v=vid00000001"
        self.assertEqual(youtube.inspect(self.cfg, "https://youtu.be/vidredirec2")["entries"][0]["id"], "vid00000001",
                         "a link to another YouTube page is followed")
        self.fake.playlist("PLmixed", "Mixed", ["vid00000001"], extra=[
            {"_type": "url", "ie_key": "Generic", "id": "abcdefghijk", "url": "https://evil.example/a", "title": "Another extractor"},
            {"_type": "url", "ie_key": "Youtube", "id": "../../etc/x", "url": "https://www.youtube.com/watch?v=../../etc/x", "title": "Odd id"},
            {"_type": "url", "ie_key": "Youtube", "id": "abcdefghij2", "url": "https://evil.example/watch?v=abcdefghij2", "title": "Away"}])
        found = youtube.inspect(self.cfg, "https://www.youtube.com/playlist?list=PLmixed")
        self.assertEqual([(e["title"], e["available"], e["url"]) for e in found["entries"]], [
            ("Intro", True, "https://www.youtube.com/watch?v=vid00000001"), ("Another extractor", False, None),
            ("Odd id", False, None), ("Away", False, None)])
        for job in ({"source_url": "https://evil.example/v.mp4", "title": "changed in job.json"},
                    {"source_url": youtube.watch_url("vid00000001"), "youtube_id": "vid00000002", "title": "another id"}):
            with self.assertRaises(youtube.YouTubeError, msg=job["title"]):
                youtube.download(self.cfg, job, self.tmp, lambda *a: None, lambda: False)
        self.assertEqual(self.fake.downloads, [], "nothing was downloaded")

    def test_download_with_captions(self):
        seen = []
        job = {"source_url": youtube.watch_url("vid00000001"), "title": "Intro", "options": {"language": "ar"}}
        got = youtube.download(self.cfg, job, self.tmp, lambda done, total: seen.append((done, total)), lambda: False)
        self.assertEqual(got, {"video": {"width": 160, "height": 90}, "captions": {"lang": "en", "name": "EN"},
                               "download_note": None, "source_name": "Intro.mp4"})
        self.assertEqual(self.fake.downloads, [("vid00000001", "136"), ("vid00000001", "140")])
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir() if p.is_file()), ["captions.vtt", "video.mp4"])
        self.assertTrue(seen and all(total for _, total in seen))
        self.assertEqual(len(youtube.captions_transcript(self.tmp)), 3)

    def test_captions_not_wanted_and_only_automatic_ones(self):
        job = {"source_url": youtube.watch_url("vid00000001"), "title": "Intro", "use_captions": False}
        self.assertIsNone(youtube.download(self.cfg, job, self.tmp, lambda *a: None, lambda: False)["captions"])
        job = {"source_url": youtube.watch_url("vid00000002"), "title": "Loops"}
        self.assertIsNone(youtube.download(self.cfg, job, self.tmp, lambda *a: None, lambda: False)["captions"])
        self.assertFalse((self.tmp / "captions.vtt").exists())

    def test_no_h264_means_only_the_sound(self):
        job = {"source_url": youtube.watch_url("vid00000003"), "title": "Only VP9"}
        got = youtube.download(self.cfg, job, self.tmp, lambda *a: None, lambda: False)
        self.assertIsNone(got["video"])
        self.assertIn("only its sound", got["download_note"])
        self.assertEqual([p.name for p in self.tmp.iterdir() if p.is_file()], ["source.webm"])
        self.assertEqual(got["source_name"], "Only VP9.webm")

    def test_cancelled_while_downloading(self):
        self.fake.hold.clear()
        stop = threading.Event()
        threading.Timer(0.3, stop.set).start()
        job = {"source_url": youtube.watch_url("vid00000001"), "title": "Intro"}
        with self.assertRaises(Cancelled):
            youtube.download(self.cfg, job, self.tmp, lambda *a: None, stop.is_set)
        self.assertFalse((self.tmp / "video.mp4").exists())

    def test_the_downloaded_deno_is_used(self):
        self.assertEqual(youtube.js_runtimes(self.cfg)["deno"], {}, "not downloaded: yt-dlp looks for one itself")
        item = self.cfg.download_items().get("deno")
        if item is None:
            self.skipTest("no Deno download is listed for this system")
        member = next(iter(item["files"][0]["unpack"]))
        deno = self.cfg.path(item["files"][0]["path"]).parent / member.rsplit("/", 1)[-1]
        deno.parent.mkdir(parents=True)
        deno.write_bytes(b"")
        self.assertEqual(youtube.js_runtimes(self.cfg), {"deno": {"path": str(deno)}, "node": {}, "quickjs": {}})


class CourseFiles(unittest.TestCase):
    def test_safe_names(self):
        self.assertEqual(courses.safe_name('Week 1: "Intro" / basics?'), "Week 1 Intro basics")
        self.assertEqual(courses.safe_name("CON"), "CON_")
        self.assertEqual(courses.safe_name("  ...  "), "Untitled")
        self.assertEqual(courses.safe_name("Notes.\t"), "Notes")
        self.assertEqual(courses.number({"index": 3, "count": 120}), "003")

    def test_paragraphs_for_a_chat_assistant(self):
        lines = [{"start": 0, "end": 2, "speaker": "1", "text": "One."}, {"start": 2, "end": 4, "speaker": "1", "text": "Two."},
                 {"start": 9, "end": 10, "speaker": "1", "text": "After a pause."}]
        self.assertEqual(courses.paragraphs({}, lines), ["[00:00] One. Two.", "[00:09] After a pause."])
        two = lines + [{"start": 10, "end": 11, "speaker": "2", "text": "A question."}]
        self.assertEqual(courses.paragraphs({"speaker_names": {"2": "Omar"}}, two)[-1], "[00:10] Omar: A question.")


class CourseFilesStayInside(unittest.TestCase):
    """Titles and ids are parts of file and folder names: whatever they hold (.., slashes, a drive, a name
    Windows reserves, a job.json changed by hand), every file lands in a folder directly in the courses folder."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="sedjem-courses-"))
        self.cfg = Config(home=self.tmp)
        self.store = Store(self.cfg.storage)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lecture(self, n, title, youtube_id, course=None):
        job = {"id": f"20261019-100000-{n:04x}", "title": title, "youtube_id": youtube_id, "status": "done",
               "source_url": "https://www.youtube.com/watch?v=jNQXAC9IVRw", "created": "2026-10-19T10:00:00+03:00",
               "finished": "2026-10-19T10:01:00+03:00", "model_title": "A model", "audio_s": 3, "speaker_names": {},
               **({"course": course} if course else {})}
        self.store.create(job)
        self.store.save_transcript(job["id"], {"lines": [{"start": 0, "end": 1, "speaker": "1", "text": "hello"}]})
        courses.try_refresh(self.store, self.cfg, job)

    def test_names_cannot_leave_the_courses_folder(self):
        bad = {"id": "PLbad", "title": "../../outside", "folder": "../../escape", "url": "u", "count": 3}
        self.lecture(1, "../../../etc/passwd", "abcdefghijk", {**bad, "index": 1})
        self.lecture(2, "C:\\Windows\\System32\\drivers", "abcdefghij2", {**bad, "index": 2})
        self.lecture(3, "..", "abcdefghij3", {**bad, "index": 3})
        self.lecture(4, "CON", "..\\..\\x", None)
        self.lecture(5, "a/b\\c:d", "/etc/passwd", None)
        self.lecture(6, "NUL", "abcdefghij5", {"id": "PLdrive", "title": "D:", "folder": "C:\\", "url": "u", "count": 1, "index": 1})
        self.lecture(7, "Odd index", "abcdefghij7", {"id": "PLidx", "title": "Index", "folder": "Index", "url": "u", "count": 1,
                                                       "index": "../../x"})
        root = (self.tmp / "courses").resolve()
        written = [p for p in self.tmp.rglob("*") if p.is_file() and "app_data" not in p.relative_to(self.tmp).parts]
        for p in written:
            self.assertEqual(p.resolve().parent.parent, root, p)
        self.assertEqual(sorted(p.name for p in root.iterdir()), [".. .. escape", "C", "Videos"])
        self.assertEqual(sorted(p.name for p in (root / ".. .. escape").iterdir()), [
            "01 - .. .. .. etc passwd.md", "01 - .. .. .. etc passwd.srt", "02 - C Windows System32 drivers.md",
            "02 - C Windows System32 drivers.srt", "03 - Untitled.md", "03 - Untitled.srt", "course.md"])
        self.assertEqual(sorted(p.name for p in (root / "Videos").iterdir()), [
            "CON_ [.. .. x].md", "CON_ [.. .. x].srt", "a b c d [etc passwd].md", "a b c d [etc passwd].srt"])
        self.assertEqual(sorted(p.name for p in (root / "C").iterdir()), ["01 - NUL_.md", "01 - NUL_.srt", "course.md"])

    def test_inside(self):
        root = self.tmp / "courses"
        root.mkdir()
        self.assertEqual(courses.inside(root, root / "Course"), root / "Course")
        for path in (root / "..", root / "a" / "b", root, self.tmp / "elsewhere", root / ".." / "courses2"):
            with self.assertRaises(ValueError, msg=path):
                courses.inside(root, path)
        try:
            (root / "link").symlink_to(self.tmp, target_is_directory=True)
        except OSError:
            self.skipTest("no symbolic links here")
        with self.assertRaises(ValueError):
            courses.inside(root, root / "link")


class Api(unittest.TestCase):
    """Through the app's HTTP API and its queues, with the hosted service replaced by a stand-in."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="sedjem-yt-api-"))
        cls.cfg = Config(home=cls.tmp)
        cls.cfg.save_key("elevenlabs", "test-key")
        cls.fake = fake = FakeYouTube()
        fake.video("vid00000001", "Intro", seconds=3)
        fake.video("vid00000002", "Variables", seconds=2, captions={"en": VTT_EN}, auto=["ar"])
        fake.video("vid00000003", "Loops", seconds=2, auto=["en"])
        fake.video("vid00000004", "درس منفرد", seconds=2, captions={"ar": VTT_AR}, language="ar")
        fake.video("vid00000005", "Long one", seconds=2)
        fake.video("vid00000006", "Private later", seconds=2)
        fake.video("vidlive0001", "Live", live="is_upcoming")
        fake.playlist("PLcourse", "Python: a course", ["vid00000001", "vid00000002", "vid00000003"], gone=["[Private video]"])
        cls.ran = []

        def hosted(cfg, model, job_dir, job, on_progress, cancelled):
            cls.ran.append(job["youtube_id"])
            return {"lines": [{"start": 0.0, "end": 1.5, "speaker": "1", "text": f"Spoken words of {job['title']}"},
                              {"start": 1.6, "end": 1.9, "speaker": "2", "text": "A question."}], "meta": {}}
        cls.patches = [mock.patch.object(youtube, "_ydl", fake.ydl), mock.patch.object(youtube, "runtime", lambda cfg: DENO),
                       mock.patch.object(engines, "run_hosted", hosted)]
        for p in cls.patches:
            p.start()
        cls.server, cls.app = make_server(cls.cfg, port=0)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.course = cls.tmp / "courses" / "Python a course"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for p in cls.patches:
            p.stop()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, method, path, body=None, headers=None):
        h = {"X-Sedjem": "1", "Content-Type": "application/json", **(headers or {})}
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
            if r["job"]["status"] in until:
                return r
            time.sleep(0.1)
        self.fail(f"job {jid} is still {r['job']['status']} ({r['job'].get('stage')})")

    def add(self, url, **body):
        return self.call("POST", "/api/youtube", {"url": url, "model": "elevenlabs", "confirm_upload": True, **body})

    def test_1_a_playlist_becomes_a_course(self):
        url = "https://www.youtube.com/playlist?list=PLcourse"
        status, found = self.call("POST", "/api/youtube/inspect", {"url": url})
        self.assertEqual(status, 200, found)
        self.assertEqual([(e["title"], e["captions"], e["available"]) for e in found["entries"]],
                         [("Intro", [], True), ("Variables", ["en"], True), ("Loops", [], True), ("[Private video]", None, False)],
                         "only captions made by people count")
        status, r = self.add(url, language="en")
        self.assertEqual(status, 201, r)
        jobs = r["jobs"]
        self.assertEqual([(j["title"], j["course"]["index"]) for j in jobs], [("Intro", 1), ("Variables", 2), ("Loops", 3)])
        course = {k: v for k, v in jobs[0]["course"].items() if k != "index"}
        self.assertEqual(course, {"id": "PLcourse", "title": "Python: a course", "url": url, "count": 4, "folder": "Python a course"})
        done = [self.wait(j["id"]) for j in jobs]
        self.assertEqual([d["job"]["status"] for d in done], ["done"] * 3, [d["job"].get("error") for d in done])
        self.assertEqual(self.ran, ["vid00000001", "vid00000003"], "the video with captions made by people skips the model")
        self.assertEqual([v for v, _ in self.fake.downloads[::2]], ["vid00000001", "vid00000002", "vid00000003"], "in order")
        first, second = done[0], done[1]
        self.assertEqual((first["job"]["source_url"], first["job"]["youtube_id"], first["job"]["video"]),
                         ("https://www.youtube.com/watch?v=vid00000001", "vid00000001", {"width": 160, "height": 90}))
        folder = self.cfg.storage / "jobs" / jobs[0]["id"]
        self.assertEqual(sorted(p.name for p in folder.iterdir() if p.suffix in (".mp4", ".flac", ".m4a")),
                         ["audio.flac", "video.mp4"], "the streams are joined, then removed")
        self.assertEqual((second["job"]["model"], second["job"]["model_title"], second["job"]["kind"]),
                         ("youtube-captions", "YouTube captions (made by people)", "captions"))
        self.assertEqual(second["job"]["captions"], {"lang": "en", "name": "EN"})
        self.assertEqual(second["lines"][0], {"start": 1.2, "end": 4.0, "speaker": "1",
                                              "text": "All right, so here we are, in front of the elephants."})
        rows = {r["label"]: r["value"] for g in second["detail_groups"] for r in g["rows"]}
        self.assertEqual((rows["YouTube"], rows["Captions"]), ("https://www.youtube.com/watch?v=vid00000002", "EN"))
        self.assertEqual(self.call("GET", f"/api/jobs/{jobs[1]['id']}/versions")[1]["versions"][0]["summary"],
                         "3 lines from YouTube captions (made by people)")

        names = sorted(p.name for p in self.course.iterdir())
        self.assertEqual(names, ["01 - Intro.md", "01 - Intro.srt", "02 - Variables.md", "02 - Variables.srt",
                                 "03 - Loops.md", "03 - Loops.srt", "course.md"])
        intro = (self.course / "01 - Intro.md").read_text(encoding="utf-8")
        self.assertTrue(intro.startswith("# Intro\n\nVideo: https://www.youtube.com/watch?v=vid00000001  \n"
                                         "Course: Python: a course, lecture 1 of 4  \n"), intro)
        self.assertIn("[00:00] Speaker 1: Spoken words of Intro", intro)
        self.assertNotIn("Speaker 1", (self.course / "02 - Variables.md").read_text(encoding="utf-8"), "one speaker: no names")
        self.assertIn("00:00:00,000 --> 00:00:01,500\nSpeaker 1: Spoken words of Intro",
                      (self.course / "01 - Intro.srt").read_text(encoding="utf-8"))
        text = (self.course / "course.md").read_text(encoding="utf-8")
        self.assertIn("Transcripts of 3 of 4 lectures", text)
        heads = [x for x in text.splitlines() if x.startswith("## ")]
        self.assertEqual(heads, ["## Lecture 1: Intro (00:03)", "## Lecture 2: Variables (00:02)", "## Lecture 3: Loops (00:02)"])
        self.assertIn("[00:01] All right, so here we are, in front of the elephants. the cool thing", text)

    def test_2_edits_and_deletions_reach_the_course_files(self):
        jobs = sorted((j for j in self.call("GET", "/api/jobs")[1]["jobs"] if j.get("course")), key=lambda j: j["course"]["index"])
        intro, loops = jobs[0]["id"], jobs[2]["id"]
        self.call("PATCH", f"/api/jobs/{intro}", {"speaker_names": {"1": "Dr. Mona"}})
        self.assertIn("Dr. Mona: Spoken words of Intro", (self.course / "01 - Intro.md").read_text(encoding="utf-8"))
        self.assertIn("Dr. Mona: Spoken words", (self.course / "course.md").read_text(encoding="utf-8"))
        self.call("PATCH", f"/api/jobs/{intro}", {"title": "Introduction"})
        self.assertFalse((self.course / "01 - Intro.md").exists(), "the old name goes")
        self.assertTrue((self.course / "01 - Introduction.srt").exists())
        lines = self.call("GET", f"/api/jobs/{intro}")[1]["lines"]
        lines[0]["text"] = "Corrected words"
        self.call("PATCH", f"/api/jobs/{intro}", {"lines": lines})
        self.assertIn("Corrected words", (self.course / "01 - Introduction.md").read_text(encoding="utf-8"))
        self.call("POST", f"/api/jobs/{intro}/versions/0/restore", {})  # the model's lines, and the first title
        self.assertIn("Spoken words of Intro", (self.course / "01 - Intro.md").read_text(encoding="utf-8"))
        self.assertFalse((self.course / "01 - Introduction.md").exists())
        self.assertEqual(self.call("DELETE", f"/api/jobs/{loops}")[0], 200)
        self.assertFalse(any(p.name.startswith("03 - ") for p in self.course.iterdir()))
        self.assertNotIn("Lecture 3", (self.course / "course.md").read_text(encoding="utf-8"))
        time.sleep(1.1)  # job times are kept to the second: the re-run finishes in a later one
        status, rerun = self.call("POST", f"/api/jobs/{intro}/rerun", {"model": "elevenlabs", "confirm_upload": True})
        self.assertEqual(status, 201, rerun)
        self.assertEqual((rerun["course"]["index"], rerun["youtube_id"]), (1, "vid00000001"), "still the same lecture")
        self.wait(rerun["id"])
        self.call("PATCH", f"/api/jobs/{intro}", {"title": "Older transcript"})
        self.assertEqual(sorted(p.name for p in self.course.iterdir() if p.name.startswith("01")),
                         ["01 - Intro.md", "01 - Intro.srt"], "the files follow the transcript that finished last")

    def test_3_a_video_on_its_own(self):
        status, r = self.add("https://www.youtube.com/watch?v=vid00000004", language="ar")
        self.assertEqual(status, 201, r)
        job = self.wait(r["jobs"][0]["id"])
        self.assertEqual((job["job"]["status"], job["job"]["kind"], job["job"].get("course")), ("done", "captions", None))
        md = self.tmp / "courses" / "Videos" / "درس منفرد [vid00000004].md"
        self.assertIn("‏[00:00] أهلاً بكم في الدرس الأول اليوم نتكلم عن ال variables.", md.read_text(encoding="utf-8"))
        with mock.patch.object(server, "show_folder") as shown:
            status, opened = self.call("POST", "/api/open-folder", {"job": job["job"]["id"]})
        self.assertEqual((status, opened["opened"]), (200, str(md.parent)))
        shown.assert_called_once_with(str(md.parent))

    def test_4_cancel_fail_and_try_again(self):
        self.fake.hold.clear()
        self.fake.started.clear()
        try:
            status, r = self.add("https://www.youtube.com/watch?v=vid00000005", captions=False)
            jid = r["jobs"][0]["id"]
            self.assertTrue(self.fake.started.wait(30))
            self.assertEqual(self.call("GET", f"/api/jobs/{jid}")[1]["job"]["stage"], "downloading")
            self.call("POST", f"/api/jobs/{jid}/cancel")
            self.assertEqual(self.wait(jid)["job"]["status"], "cancelled")
        finally:
            self.fake.hold.set()
        self.assertEqual(self.call("POST", "/api/youtube/inspect", {"url": "https://youtu.be/vid00000006"})[0], 200)
        self.fake.fail["vid00000006"] = "ERROR: [youtube] vid00000006: Private video. Sign in if you've been granted access"
        status, r = self.add("https://youtu.be/vid00000006")  # what the check found is used: the job is made
        failed = self.wait(r["jobs"][0]["id"])
        self.assertEqual((failed["job"]["status"], failed["job"]["error"]),
                         ("failed", "YouTube: Private video. Sign in if you've been granted access"))
        del self.fake.fail["vid00000006"]
        status, again = self.call("POST", f"/api/jobs/{failed['job']['id']}/rerun", {"model": "elevenlabs", "confirm_upload": True})
        self.assertEqual((status, again["id"]), (201, failed["job"]["id"]), "the same job, downloaded again")
        self.assertEqual(self.wait(again["id"])["job"]["status"], "done")

    def test_5_what_is_refused(self):
        self.assertEqual(self.call("POST", "/api/youtube", {"url": "https://youtu.be/vid00000001", "model": "elevenlabs",
                                                            "confirm_upload": True}, headers={"X-Sedjem": ""})[0], 403)
        status, r = self.call("POST", "/api/youtube", {"url": "https://youtu.be/vid00000001", "model": "elevenlabs"})
        self.assertEqual((status, r["error"]), (400, "confirm that the recording may be uploaded to ElevenLabs"))
        self.assertEqual(self.call("POST", "/api/youtube/inspect", {"url": "https://vimeo.com/1"})[1]["error"],
                         "paste the link of a YouTube video or playlist")
        self.assertIn("channel", self.call("POST", "/api/youtube/inspect", {"url": "https://www.youtube.com/@teacher"})[1]["error"])
        self.assertIn("live stream", self.add("https://youtu.be/vidlive0001")[1]["error"])
        status, r = self.add("https://www.youtube.com/playlist?list=PLcourse", entries=[])
        self.assertEqual((status, r["error"]), (400, "choose at least one video that can be downloaded"))
        status, r = self.add("https://www.youtube.com/playlist?list=PLcourse", entries=["gone0000000"])
        self.assertEqual(status, 400, "a private video can't be picked")
        self.assertEqual(self.call("POST", "/api/open-folder", {"job": "20200101-000000-0000"})[0], 404)
        forged = "https://www.youtube.com/playlist?list=PLforged"  # an id that isn't YouTube's never becomes a job
        self.app.inspected[forged] = (time.time(), {"kind": "playlist", "id": "PLforged", "title": "Forged", "url": forged,
                                                    "count": 1, "entries": [{"id": "../../etc", "title": "x", "index": 1,
                                                                             "available": True, "url": "https://evil.example/"}]})
        status, r = self.add(forged)
        self.assertEqual((status, r["error"]), (400, "a video has an id YouTube doesn't use"))


if __name__ == "__main__":
    unittest.main()
