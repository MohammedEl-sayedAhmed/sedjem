"""Browser tests of YouTube links (app/static/youtube.js): a playlist link checked on the new transcription
page, its videos shown with the ones that have captions made by people, some of them added and transcribed;
a course in the sidebar with its lectures; a lecture's page with its link, the lectures before and after it,
and its course folder; the note when no JavaScript runtime is found; and the narrow layout in the dark theme.
yt-dlp and the hosted service are replaced by stand-ins (tests/youtube_support.py), so nothing leaves this
computer. Headless Chromium against a Sedjem server with demo transcripts (see tests/ui_support.py).
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui_youtube.py" -v
"""
import time
import unittest
from unittest import mock

from ui_support import UiTestCase  # (puts the project on the path)
from youtube_support import FakeYouTube, vtt

from app import engines, server, youtube

COURSE = {"id": "PLpython", "title": "Python for beginners", "url": "https://www.youtube.com/playlist?list=PLpython",
          "count": 3, "folder": "Python for beginners"}
ONE, TWO, THREE, ALONE = "20261019-100000-c001", "20261019-100000-c002", "20261019-100000-c003", "20261019-090000-d001"
CAPTIONS = {"model": "youtube-captions", "model_title": "YouTube captions (made by people)", "kind": "captions",
            "captions": {"lang": "en", "name": "English"}}
LECTURE = lambda n: {"course": {**COURSE, "index": n}, "youtube_id": f"vidpython0{n}",
                     "source_url": f"https://www.youtube.com/watch?v=vidpython0{n}", "created": "2026-10-19T10:00:00+03:00"}
DENO = {"name": "Deno", "version": "2.9.5", "downloaded": True}
REFUSED = "Failed to load resource: the server responded with a status of 400"  # how the browser logs a refusal


class YouTubeLinks(UiTestCase):
    jobs = ({"jid": ONE, "title": "Introduction", **LECTURE(1)},
            {"jid": TWO, "title": "Variables", **LECTURE(2), **CAPTIONS},
            {"jid": THREE, "title": "Loops", **LECTURE(3), "status": "failed",
             "error": "YouTube: Private video. Sign in if you've been granted access"},
            {"jid": ALONE, "title": "A talk on its own", "youtube_id": "vidalone001",
             "source_url": "https://www.youtube.com/watch?v=vidalone001", "created": "2026-10-19T09:00:00+03:00"})

    @classmethod
    def setUpClass(cls):
        cls.fake = fake = FakeYouTube()
        cues = vtt((1, 3, "Welcome to the course."), (3, 5, "Today we install Python."))
        fake.video("vidnew00001", "Setting up", seconds=2, captions={"en": cues}, language="en")
        fake.video("vidnew00002", "First steps", seconds=2)
        fake.video("vidnew00003", "Next steps", seconds=2, auto=["en"])
        fake.playlist("PLnewcourse", "Data science", ["vidnew00001", "vidnew00002", "vidnew00003"], gone=["[Private video]"])
        cls.runtime = DENO
        cls.ran = []

        def hosted(cfg, model, job_dir, job, on_progress, cancelled):
            cls.ran.append(job["youtube_id"])
            return {"lines": [{"start": 0.0, "end": 1.5, "speaker": "1", "text": f"Words of {job['title']}"}], "meta": {}}
        cls.patches = [mock.patch.object(youtube, "_ydl", fake.ydl), mock.patch.object(youtube, "runtime", lambda cfg: cls.runtime),
                       mock.patch.object(engines, "run_hosted", hosted), mock.patch.object(server, "show_folder")]
        cls.shown = [p.start() for p in cls.patches][-1]  # the file manager isn't opened
        try:
            super().setUpClass()
        except BaseException:
            for p in cls.patches:
                p.stop()
            raise
        cls.app.cfg.save_key("elevenlabs", "test-key")

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        for p in cls.patches:
            p.stop()

    def texts(self, selector):
        return self.page.eval_on_selector_all(selector, "els => els.map(e => e.innerText.trim())")

    def test_a_course_shows_once_with_its_lectures(self):
        self.open()
        self.page.wait_for_selector("#jobList .job-item")
        course = self.page.locator("#jobList .job-item.course")
        self.assertEqual(course.count(), 1)
        self.assertIn("Python for beginners", course.inner_text())
        self.assertIn("2 of 3 done", course.inner_text())
        self.assertFalse(self.page.is_visible("#jobList .lec"), "the lectures are folded away")
        course.locator("summary").click()
        self.assertEqual(self.texts("#jobList .lec .lt"), ["Introduction", "Variables", "Loops"])
        self.assertEqual(self.page.eval_on_selector_all("#jobList .lec-state", "els => els.map(e => e.className)"),
                         ["lec-state done", "lec-state done", "lec-state bad"])
        self.assertEqual(self.page.locator("#jobList a.job-item", has_text="A talk on its own").count(), 1, "a video on its own")
        self.assertEqual(self.page.locator("#jobList .job-item", has_text="Introduction").count(), 1, "lectures only in their course")

        self.page.click("#jobList .lec >> text=Variables")
        self.page.wait_for_url(f"**/#/job/{TWO}")
        self.page.wait_for_selector("#transcript .line .text")
        self.assertIn("lecture 2 of 3", self.page.inner_text(".course-bar"))
        self.assertEqual(self.page.get_attribute(".course-bar a:has-text('Previous')", "href"), f"#/job/{ONE}")
        self.assertEqual(self.page.get_attribute(".course-bar a:has-text('Next')", "href"), f"#/job/{THREE}")
        self.assertEqual(self.page.get_attribute(".meta .yt-link", "href"), "https://www.youtube.com/watch?v=vidpython02")
        self.assertIn("YouTube captions (made by people)", self.page.inner_text(".meta"))
        self.assertIn("Made by people, English", self.page.inner_text(".job-details"))
        self.assertEqual(self.page.get_attribute("#jobList .lec.on", "aria-current"), "page")
        self.assertIn("active", course.get_attribute("class"))

        self.page.click(".course-bar a:has-text('Next')")
        self.page.wait_for_url(f"**/#/job/{THREE}")
        self.page.wait_for_selector(".error-card")
        self.assertIn("Private video", self.page.inner_text(".error-card"))
        self.assertEqual(self.page.locator(".course-bar span.btn[aria-disabled='true']", has_text="Next").count(), 1, "the last one")
        self.shown.reset_mock()
        self.page.click("#openCourse")
        end = time.time() + 10
        while not self.shown.called and time.time() < end:
            time.sleep(0.05)
        self.assertEqual(self.shown.call_args[0][0], str(self.app.cfg.courses), "nothing written yet: the courses folder")

        self.open(f"#/job/{ALONE}")
        self.page.wait_for_selector(".course-bar")
        self.assertIn("Videos folder", self.page.inner_text(".course-bar"))
        self.assertEqual(self.page.inner_text("#openCourse"), "Open folder")

    def test_a_playlist_is_checked_and_added(self):
        self.open()
        self.page.click("[data-src=youtube]")
        self.page.fill("#ytUrl", "https://vimeo.com/1")
        self.page.click("#ytForm button[type=submit]")
        self.page.wait_for_selector(".yt-error")
        self.assertEqual(self.page.inner_text(".yt-error"), "paste the link of a YouTube video or playlist")
        self.assertEqual(len([e for e in self.errors if e.startswith(REFUSED)]), 1)
        self.errors = [e for e in self.errors if not e.startswith(REFUSED)]
        self.page.fill("#ytUrl", "https://www.youtube.com/playlist?list=PLnewcourse")
        self.page.click("#ytForm button[type=submit]")
        self.page.wait_for_selector(".yt-list")
        self.assertIn("Data science", self.page.inner_text(".yt-chosen"))
        self.assertIn("1 of 3 videos has captions made by people", self.page.inner_text(".yt-caps"))
        self.assertEqual(self.texts(".yt-list .t"), ["Setting up", "First steps", "Next steps", "[Private video]"])
        self.assertEqual(self.texts(".yt-list .pill.cc"), ["EN"], "the automatic captions of Next steps don't count")
        self.assertTrue(self.page.is_disabled(".yt-list input[data-yt-entry^='gone']"))
        self.assertEqual(self.page.inner_text("#startBtn"), "Start 3 transcriptions")
        self.page.uncheck("[data-yt-entry=vidnew00003]")
        self.assertEqual(self.page.inner_text("#startBtn"), "Start 2 transcriptions")
        self.assertFalse(self.page.is_visible("#titleInput"), "titles come from the videos")
        self.page.click(".model-card[data-model=elevenlabs]")
        self.assertTrue(self.page.is_disabled("#startBtn"), "a hosted model needs the upload confirmed")
        self.assertIn("uploads the sound of each video it transcribes", self.page.inner_text(".consent"))
        self.page.check("#confirmUpload")
        self.page.click("#startBtn")
        self.page.wait_for_selector(".course-bar")
        self.assertIn("Data science · lecture 1 of 4", self.page.inner_text(".course-bar"))
        jobs = []
        end = time.time() + 60
        while time.time() < end:
            jobs = [j for j in self.api("GET", "/api/jobs")["json"]["jobs"] if (j.get("course") or {}).get("id") == "PLnewcourse"]
            if len(jobs) == 2 and all(j["status"] == "done" for j in jobs):
                break
            time.sleep(0.2)
        by_index = {j["course"]["index"]: j for j in jobs}
        self.assertEqual((by_index[1]["model_title"], by_index[2]["model_title"]),
                         ("YouTube captions (made by people)", "ElevenLabs Scribe"))
        self.assertEqual(self.ran, ["vidnew00002"], "only the video without captions went to the model")
        self.page.wait_for_selector("#jobList .job-item.course:has-text('Data science') .s:has-text('Done')")
        self.page.wait_for_selector("#transcript .line .text")
        self.assertEqual(self.texts("#transcript .line .text"), ["Welcome to the course.", "Today we install Python."])

    def test_without_a_javascript_runtime(self):
        type(self).runtime = None
        try:
            self.open()
            self.page.click("[data-src=youtube]")
            self.page.wait_for_selector(".yt-note")
            self.assertIn("YouTube asks for a JavaScript runtime, and none was found", self.page.inner_text(".yt-note"))
            if self.app.cfg.download_items().get("deno"):
                self.assertTrue(self.page.is_visible(".yt-note [data-dl=deno]"), "Deno can be downloaded from here")
        finally:
            type(self).runtime = DENO
        self.page.reload()
        self.page.wait_for_function("() => typeof S !== 'undefined' && S.status")
        self.page.click("[data-src=youtube]")
        self.assertFalse(self.page.is_visible(".yt-note"))

    def test_narrow_and_dark(self):
        self.page.set_viewport_size({"width": 390, "height": 800})
        self.open()
        self.page.evaluate("document.documentElement.dataset.theme = 'dark'")
        self.page.click("[data-src=youtube]")
        self.page.fill("#ytUrl", "https://www.youtube.com/playlist?list=PLnewcourse")
        self.page.click("#ytForm button[type=submit]")
        self.page.wait_for_selector(".yt-list")
        wide = self.page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        self.assertLessEqual(wide, 0, "no sideways scrolling")
        bg = self.page.evaluate("() => getComputedStyle(document.querySelector('.yt-list')).backgroundColor")
        self.assertEqual(bg, "rgb(22, 26, 32)", "the dark theme's surface")
        self.open(f"#/job/{TWO}")
        self.page.wait_for_selector(".course-bar")
        self.assertLessEqual(self.page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth"), 0)


if __name__ == "__main__":
    unittest.main()
