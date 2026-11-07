"""Browser test of the two YouTube halves together: a video added from a link (app/youtube.py, with the
yt-dlp stand-in of tests/youtube_support.py) plays in the job page's video panel (app/video.py, app.js),
and its transcript, here its captions made by people, follows the playback. Offline: nothing is fetched.
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui*.py" -v
"""
import time
import unittest
from unittest import mock

from ui_support import UiTestCase  # (puts the project on the path)
from youtube_support import FakeYouTube, vtt

from app import youtube


class YouTubeVideo(UiTestCase):
    @classmethod
    def setUpClass(cls):
        fake = FakeYouTube()
        fake.video("vidplay0001", "Installing Python", seconds=6, language="en",
                   captions={"en": vtt((0.5, 2.5, "Welcome to the course."), (3, 5.5, "Today we install Python."))})
        cls.patches = [mock.patch.object(youtube, "_ydl", fake.ydl),
                       mock.patch.object(youtube, "runtime", lambda cfg: {"name": "Deno", "version": "2.9.5", "downloaded": True})]
        for p in cls.patches:
            p.start()
        try:
            super().setUpClass()
        except BaseException:
            for p in cls.patches:
                p.stop()
            raise
        cls.app.cfg.save_key("elevenlabs", "test-key")  # a ready model; captions are used, so it never runs

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        for p in cls.patches:
            p.stop()

    def test_a_downloaded_video_plays_beside_its_transcript(self):
        self.open()
        r = self.api("POST", "/api/youtube", {"url": "https://youtu.be/vidplay0001", "captions": True,
                                             "model": "elevenlabs", "confirm_upload": True})
        self.assertIn(r["status"], (200, 201), r)
        jid = r["json"]["jobs"][0]["id"]
        end, got = time.time() + 60, {"job": {}}
        while time.time() < end and got["job"].get("status") != "done":
            got = self.api("GET", f"/api/jobs/{jid}")["json"]
            time.sleep(0.2)
        job = got["job"]
        self.assertEqual(job.get("status"), "done", job)
        self.assertTrue(got["has_video"] and job.get("video"), "the download is the job's video")
        self.assertEqual(job["model_title"], "YouTube captions (made by people)")

        self.open(f"#/job/{jid}")
        self.page.wait_for_selector("#transcript .line .text")
        self.page.wait_for_function("() => { const v = document.querySelector('video'); return v && v.readyState >= 2; }")
        size = self.page.evaluate("() => { const v = document.querySelector('video'); return [v.videoWidth, v.videoHeight]; }")
        self.assertEqual(size, [job["video"]["width"], job["video"]["height"]])
        self.page.evaluate("() => { const v = document.querySelector('video'); v.muted = true; v.currentTime = 4; }")
        self.page.wait_for_function("() => document.querySelector('#transcript .line.current')?.innerText"
                                    ".includes('Today we install Python.')")
        self.page.click("#transcript .line:has-text('Welcome to the course.') .ts")  # a line's time seeks the video
        self.page.wait_for_function("() => document.querySelector('video').currentTime < 2.5")


if __name__ == "__main__":
    unittest.main()
