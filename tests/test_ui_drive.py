"""Browser tests of Google Drive links (app/static/drive.js, on youtube.js's Link tab): a Drive file checked on the
new transcription page and shown, without YouTube's streaming choice, then added and transcribed; a Drive folder's
audio and video files listed in natural name order with what was skipped, the ones picked added as a course that the
sidebar groups and whose lecture pages have the course bar; a private folder refused with the reason, as any link's
error is shown; a Drive file in the sidebar with the single files, and its page's link to the file; and the narrow
layout in the dark theme. yt-dlp, Drive's folder view and the hosted service are replaced by stand-ins
(tests/drive_support.py), so nothing leaves this computer. Headless Chromium against a Sedjem server with demo
transcripts (see tests/ui_support.py).
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui_drive.py" -v
"""
import time
import unittest
from unittest import mock

from ui_support import UiTestCase  # (puts the project on the path)
from drive_support import FakeDrive, FolderPages, drive_id
from youtube_support import FakeYouTube

from app import drive, engines, youtube

ALONE = "20261020-090000-a004"
NEW_FILE, FOLDER, PRIVATE_FOLDER = drive_id(20), drive_id(2, "d"), drive_id(3, "d")
FOLDER_LINK = f"https://drive.google.com/drive/folders/{FOLDER}?usp=sharing"
PRIVATE_LINK = f"https://drive.google.com/drive/u/0/folders/{PRIVATE_FOLDER}"
LECTURES = [drive_id(21), drive_id(22), drive_id(23)]  # Lecture 10, 2 and 1, as Drive's page lists them
REFUSED = "Failed to load resource: the server responded with a status of 400"  # how the browser logs a refusal


class DriveLinks(UiTestCase):
    jobs = ({"jid": ALONE, "title": "Interview with the dean", "drive_id": drive_id(9),
             "source_url": drive.file_url(drive_id(9)), "created": "2026-10-20T09:00:00+03:00"},)

    @classmethod
    def setUpClass(cls):
        cls.fake = fake = FakeDrive()
        fake.file(NEW_FILE, "Guest lecture on sampling.mp4", seconds=2)
        for fid, name in zip(LECTURES, ("Lecture 10 - Filters.mp4", "Lecture 2 - Signals.mp4", "Lecture 1 - Welcome.mp4")):
            fake.file(fid, name, seconds=2)
        cls.pages = FolderPages()
        cls.pages.folder(FOLDER, "Signals and systems", [
            ("file", LECTURES[0], "Lecture 10 - Filters.mp4", "video/mp4"), ("folder", drive_id(24, "d"), "Exams", ""),
            ("file", LECTURES[1], "Lecture 2 - Signals.mp4", "video/mp4"), ("file", drive_id(25), "Slides.pdf", "application/pdf"),
            ("file", LECTURES[2], "Lecture 1 - Welcome.mp4", "video/mp4"),
            ("doc", drive_id(26), "Reading list", "application/vnd.google-apps.document")])
        cls.pages.private.add(PRIVATE_FOLDER)
        cls.ran = []

        def hosted(cfg, model, job_dir, job, on_progress, cancelled):
            cls.ran.append(job["drive_id"])
            return {"lines": [{"start": 0.0, "end": 1.5, "speaker": "1", "text": f"Words of {job['title']}"}], "meta": {}}
        cls.youtube = FakeYouTube()
        cls.youtube.video("vidafterdr1", "After a Drive file", seconds=2)
        cls.patches = [mock.patch.object(drive, "_ydl", fake.ydl), mock.patch.object(engines, "run_hosted", hosted),
                       mock.patch.object(drive, "OPENER", cls.pages.opener()),
                       mock.patch.object(youtube, "_ydl", cls.youtube.ydl),
                       mock.patch.object(youtube, "runtime", lambda cfg: {"name": "Deno", "version": "2.9.5", "downloaded": True})]
        for p in cls.patches:
            p.start()
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

    def check(self, url, wait=".drive-found"):
        self.page.click("[data-src=link]")
        self.page.fill("#ytUrl", url)
        self.page.click("#ytForm button[type=submit]")
        self.page.wait_for_selector(wait)

    def test_the_link_tab_takes_a_drive_file(self):
        self.open()
        self.assertEqual(self.page.inner_text("[data-src=link]"), "Link")
        self.page.click("[data-src=link]")
        self.assertEqual(self.page.get_attribute("#ytUrl", "placeholder"), "A YouTube or Google Drive link")
        self.assertEqual(self.page.inner_text("#ytIntro"),
                         "A YouTube video or playlist, or a Google Drive file or folder shared with anyone who has the link.")
        self.assertEqual(self.page.inner_text(".yt-rules"),
                         "Download only what YouTube's terms and the file's owner allow you to.")
        self.check(f"https://drive.google.com/file/d/{NEW_FILE}/view?usp=sharing")
        self.assertEqual(self.page.inner_text(".yt-chosen .name"), "Guest lecture on sampling.mp4", "the name, once")
        self.assertEqual(self.page.inner_text(".yt-chosen .sub"), "Google Drive file · 2 s")
        self.assertIn("downloaded, then transcribed like a file on this computer", self.page.inner_text(".yt-caps"))
        self.assertEqual(self.page.locator(".yt-video").count(), 0, "no streaming choice: that is YouTube's")
        self.assertIn("named after the file", self.page.inner_text(".card:has(#lang)"))
        self.page.click(".model-card[data-model=elevenlabs]")
        self.assertIn("uploads the sound of the file", self.page.inner_text(".consent"))
        self.page.check("#confirmUpload")
        self.assertEqual(self.page.inner_text("#startBtn"), "Start transcription")
        self.ran.clear()
        self.page.click("#startBtn")
        self.page.wait_for_url("**/#/job/*")
        jid = self.page.evaluate("() => S.route.id")
        end, job = time.time() + 60, {}
        while time.time() < end and job.get("status") != "done":
            job = self.api("GET", f"/api/jobs/{jid}")["json"]["job"]
            time.sleep(0.2)
        self.assertEqual((job.get("status"), job.get("drive_id"), job.get("course")), ("done", NEW_FILE, None), job)
        self.assertEqual(self.ran, [NEW_FILE])
        self.page.wait_for_selector("#transcript .line .text")
        self.assertEqual(self.texts("#transcript .line .text"), ["Words of Guest lecture on sampling"])
        self.assertEqual(self.page.get_attribute(".meta .yt-link", "href"), drive.file_url(NEW_FILE))
        self.assertEqual(self.page.locator(".course-bar").count(), 0, "a file on its own has no course")

        self.open()  # (the same page: the form as the Drive file left it)
        self.page.click("[data-src=link]")
        self.page.wait_for_selector("#ytUrl")
        self.assertEqual(self.page.input_value("#ytUrl"), "")
        self.assertEqual(self.page.locator(".yt-video").count(), 1, "the next link may be YouTube's: its choice is back")
        self.page.fill("#ytUrl", "https://youtu.be/vidafterdr1")
        self.page.click("#ytForm button[type=submit]")
        self.page.wait_for_selector(".yt-caps")
        self.assertIn("After a Drive file", self.page.inner_text(".yt-chosen"))
        self.assertTrue(self.page.is_visible(".yt-video"), "a YouTube video's choice, after a Drive file was added")

    def test_a_drive_folder_lists_its_files_and_adds_a_course(self):
        self.open()
        self.check(FOLDER_LINK, ".yt-list")
        self.assertEqual(self.page.inner_text(".yt-chosen .name"), "Signals and systems")
        self.assertEqual(self.page.inner_text(".yt-chosen .sub"), "Google Drive folder · 3 audio or video files")
        self.assertEqual(self.texts(".yt-list .t"), ["Lecture 1 - Welcome.mp4", "Lecture 2 - Signals.mp4",
                                                     "Lecture 10 - Filters.mp4"], "natural name order, names as on Drive")
        self.assertEqual(self.texts(".yt-list .n"), ["1", "2", "3"])
        self.assertEqual(self.page.inner_text(".drive-skipped"),
                         "1 subfolder and 2 other files skipped. A subfolder can be added as a course of its own, with its own link.")
        self.assertIn("become a course named after the folder", self.page.inner_text(".yt-caps"))
        self.assertEqual(self.page.locator(".yt-video").count(), 0, "no streaming choice: that is YouTube's")
        self.assertEqual(self.page.inner_text(".yt-list-head span"), "3 of 3 picked")
        self.assertEqual(self.page.inner_text("#startBtn"), "Start 3 transcriptions")
        self.page.uncheck(f"[data-yt-entry='{LECTURES[1]}']")
        self.assertEqual(self.page.inner_text(".yt-list-head span"), "2 of 3 picked")
        self.assertIn("Each transcription is named after its file", self.page.inner_text(".card:has(#lang)"))
        self.page.click(".model-card[data-model=elevenlabs]")
        self.assertIn("uploads the sound of each file it transcribes", self.page.inner_text(".consent"))
        self.page.check("#confirmUpload")
        self.ran.clear()
        self.page.click("#startBtn")
        self.page.wait_for_selector(".course-bar")
        self.assertIn("Signals and systems · lecture 1 of 3", self.page.inner_text(".course-bar"))
        jobs, end = [], time.time() + 60
        while time.time() < end:
            jobs = [j for j in self.api("GET", "/api/jobs")["json"]["jobs"] if (j.get("course") or {}).get("id") == FOLDER]
            if len(jobs) == 2 and all(j["status"] == "done" for j in jobs):
                break
            time.sleep(0.2)
        self.assertEqual(sorted((j["course"]["index"], j["title"], j["drive_id"], j["status"]) for j in jobs),
                         [(1, "Lecture 1 - Welcome", LECTURES[2], "done"), (3, "Lecture 10 - Filters", LECTURES[0], "done")])
        self.assertEqual(self.ran, [LECTURES[2], LECTURES[0]], "transcribed in the folder's order")
        course = self.page.locator("#jobList .job-item.course", has_text="Signals and systems")
        self.page.wait_for_selector("#jobList .job-item.course:has-text('Signals and systems') .s:has-text('Done')")
        self.assertIn("2 of 2 done", course.inner_text())
        self.assertTrue(course.locator("details.lectures").get_attribute("open") is not None, "open on its lecture's page")
        self.assertEqual(self.texts("#jobList .job-item.course:has-text('Signals and systems') .lec .lt"),
                         ["Lecture 1 - Welcome", "Lecture 10 - Filters"], "its lectures grouped in the sidebar")
        self.assertEqual(self.texts("#jobList .job-item.course:has-text('Signals and systems') .lec .n"), ["1", "3"])
        self.page.click(".course-bar a:has-text('Next')")
        self.page.wait_for_function("() => document.querySelector('.course-bar')?.innerText.includes('lecture 3 of 3')")
        self.page.wait_for_selector("#transcript .line .text")
        self.assertEqual(self.texts("#transcript .line .text"), ["Words of Lecture 10 - Filters"])
        self.assertEqual(self.page.get_attribute(".meta .yt-link", "href"), drive.file_url(LECTURES[0]))
        self.assertEqual(self.page.locator("#openCourse").count(), 0, "no course folder: its files aren't written there")
        self.assertEqual(self.page.locator("#exportCourse").count(), 1, "exported as a course")

    def test_a_private_drive_folder_is_refused_with_the_reason(self):
        self.open()
        self.page.click("[data-src=link]")
        self.assertEqual(self.page.locator(".yt-video").count(), 1, "before a link is checked: YouTube's choice")
        self.check(PRIVATE_LINK, ".yt-error")
        self.assertEqual(self.page.inner_text("#ytForm + .yt-error"),
                         "This folder isn't shared with anyone who has the link. Sedjem can't sign in to Google Drive yet.",
                         "right under the field")
        self.assertEqual(self.page.get_attribute(".yt-error", "role"), "alert")
        self.assertEqual(self.page.locator(".yt-video").count(), 0, "a Drive link: no YouTube choice")
        self.assertEqual(self.page.input_value("#ytUrl"), PRIVATE_LINK, "the link stays, to be changed")
        self.assertTrue(self.page.is_disabled("#startBtn"))
        self.assertEqual(len([e for e in self.errors if e.startswith(REFUSED)]), 1)
        self.errors = [e for e in self.errors if not e.startswith(REFUSED)]

    def test_a_drive_file_sits_with_the_single_files(self):
        self.open()
        self.page.wait_for_selector("#jobList .job-item")
        item = self.page.locator("#jobList a.job-item", has_text="Interview with the dean")
        self.assertEqual(item.count(), 1)
        item.click()
        self.page.wait_for_selector("#transcript .line .text")
        self.assertEqual(self.page.locator(".course-bar").count(), 0)
        self.assertIn("On Google Drive", self.page.inner_text(".meta"))
        self.assertNotIn("On YouTube", self.page.inner_text(".meta"))
        self.assertEqual(self.page.get_attribute(".meta .yt-link", "href"), drive.file_url(drive_id(9)))

    def test_narrow_and_dark(self):
        self.page.set_viewport_size({"width": 390, "height": 800})
        self.open()
        self.page.evaluate("document.documentElement.dataset.theme = 'dark'")
        self.check(drive.file_url(NEW_FILE))
        self.assertLessEqual(self.page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth"), 0,
                             "no sideways scrolling")
        self.page.click("#ytChange")
        self.check(FOLDER_LINK, ".yt-list")
        self.assertLessEqual(self.page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth"), 0)
        self.page.click("#ytChange")
        self.check(PRIVATE_LINK, ".yt-error")
        self.assertLessEqual(self.page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth"), 0)
        self.errors = [e for e in self.errors if not e.startswith(REFUSED)]


if __name__ == "__main__":
    unittest.main()
