"""Browser tests of Google Drive links (app/static/drive.js, on youtube.js's Link tab): a Drive file checked on the
new transcription page and shown, without YouTube's streaming choice, then added and transcribed; a Drive folder's
link refused with the reason, as any link's error is shown; a Drive file in the sidebar with the single files, and its
page's link to the file; and the narrow layout in the dark theme. yt-dlp and the hosted service are replaced by
stand-ins (tests/drive_support.py), so nothing leaves this computer. Headless Chromium against a Sedjem server with
demo transcripts (see tests/ui_support.py).
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui_drive.py" -v
"""
import time
import unittest
from unittest import mock

from ui_support import UiTestCase  # (puts the project on the path)
from drive_support import FakeDrive, drive_id

from app import drive, engines

ALONE = "20261020-090000-a004"
NEW_FILE, FOLDER = drive_id(20), drive_id(2, "d")
FOLDER_LINK = f"https://drive.google.com/drive/folders/{FOLDER}?usp=sharing"
REFUSED = "Failed to load resource: the server responded with a status of 400"  # how the browser logs a refusal


class DriveLinks(UiTestCase):
    jobs = ({"jid": ALONE, "title": "Interview with the dean", "drive_id": drive_id(9),
             "source_url": drive.file_url(drive_id(9)), "created": "2026-10-20T09:00:00+03:00"},)

    @classmethod
    def setUpClass(cls):
        cls.fake = fake = FakeDrive()
        fake.file(NEW_FILE, "Guest lecture on sampling.mp4", seconds=2)
        cls.ran = []

        def hosted(cfg, model, job_dir, job, on_progress, cancelled):
            cls.ran.append(job["drive_id"])
            return {"lines": [{"start": 0.0, "end": 1.5, "speaker": "1", "text": f"Words of {job['title']}"}], "meta": {}}
        cls.patches = [mock.patch.object(drive, "_ydl", fake.ydl), mock.patch.object(engines, "run_hosted", hosted)]
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
                         "A YouTube video or playlist, or a Google Drive file shared with anyone who has the link.")
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

    def test_a_drive_folder_is_refused_with_the_reason(self):
        self.open()
        self.page.click("[data-src=link]")
        self.assertEqual(self.page.locator(".yt-video").count(), 1, "before a link is checked: YouTube's choice")
        self.check(FOLDER_LINK, ".yt-error")
        self.assertEqual(self.page.inner_text("#ytForm + .yt-error"),
                         "Google Drive folders can't be read yet. Add the files one by one, each with its own link.",
                         "right under the field")
        self.assertEqual(self.page.get_attribute(".yt-error", "role"), "alert")
        self.assertEqual(self.page.locator(".yt-video").count(), 0, "a Drive link: no YouTube choice")
        self.assertEqual(self.page.input_value("#ytUrl"), FOLDER_LINK, "the link stays, to be changed")
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
        self.check(FOLDER_LINK, ".yt-error")
        self.assertLessEqual(self.page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth"), 0)
        self.errors = [e for e in self.errors if not e.startswith(REFUSED)]


if __name__ == "__main__":
    unittest.main()
