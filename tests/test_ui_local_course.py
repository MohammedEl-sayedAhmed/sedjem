"""Browser tests of recordings on this computer as a course (app/static/local.js; the server side is app/local.py): a
folder's full path typed in the path field lists its recordings in natural name order with what was skipped, and the
ones picked become a course that the sidebar groups and whose lecture pages have the course bar; several files dropped
or picked together are listed in the same way, never only the first, and become one course with the name typed; in the
desktop window, the file picker's paths and Choose folder do the same, read in place; a folder that isn't there, or has
no recordings, says so; and the narrow layout in the dark theme. The hosted service is replaced by a stand-in, and the
recordings are short WAV files written here, so nothing leaves this computer. Headless Chromium against a Sedjem
server with demo transcripts (see tests/ui_support.py).
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui_local_course.py" -v
"""
import base64
import os
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import soundfile as sf

from ui_support import UiTestCase  # (puts the project on the path)

from app import engines

NAMES = ("Lecture 10 - Filters.wav", "Lecture 2 - Signals.wav", "Lecture 1 - Welcome.wav")


def wav(path, freq):
    t = np.arange(int(16000 * 0.6)) / 16000
    sf.write(path, (0.1 * np.sin(2 * np.pi * freq * t)).astype(np.float32), 16000)
    return path


class LocalCourses(UiTestCase):
    @classmethod
    def setUpClass(cls):
        cls.ran = []

        def hosted(cfg, model, job_dir, job, on_progress, cancelled):
            cls.ran.append(job["title"])
            return {"lines": [{"start": 0.0, "end": 0.5, "speaker": "1", "text": f"Words of {job['title']}"}], "meta": {}}
        cls.patch = mock.patch.object(engines, "run_hosted", hosted)
        cls.patch.start()
        try:
            super().setUpClass()
        except BaseException:
            cls.patch.stop()
            raise
        cls.app.cfg.save_key("elevenlabs", "test-key")
        cls.folder = cls.home / "Signals and systems"
        cls.folder.mkdir()
        for n, name in enumerate(NAMES):
            wav(cls.folder / name, 300 + 50 * n)
        (cls.folder / "Slides.pdf").write_text("slides")
        (cls.folder / "Exams").mkdir()
        cls.bare = cls.home / "Only notes"
        cls.bare.mkdir()
        (cls.bare / "notes.txt").write_text("notes")

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.patch.stop()

    def texts(self, selector):
        return self.page.eval_on_selector_all(selector, "els => els.map(e => e.innerText.trim())")

    def course_jobs(self, title, count):
        """The jobs of a course by its title, once count of them are done."""
        jobs, end = [], time.time() + 60
        while time.time() < end:
            jobs = [j for j in self.api("GET", "/api/jobs")["json"]["jobs"] if (j.get("course") or {}).get("title") == title]
            if len(jobs) == count and all(j["status"] == "done" for j in jobs):
                break
            time.sleep(0.2)
        return sorted(jobs, key=lambda j: j["course"]["index"])

    def type_path(self, path):
        self.page.click("details.path summary")
        self.page.fill("#pathInput", str(path))
        self.page.click("#pathBtn")

    def hosted(self):
        self.page.click(".model-card[data-model=elevenlabs]")
        self.page.check("#confirmUpload")

    def drop(self, files):
        """Drop files on the drop area, as a browser gives them: [(name, the path of its bytes, or its text)]."""
        payload = [(name, base64.b64encode(src.read_bytes() if isinstance(src, Path) else src.encode()).decode())
                   for name, src in files]
        handle = self.page.evaluate_handle("""(files) => {
            const dt = new DataTransfer();
            for (const [name, b64] of files) dt.items.add(new File([Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))], name));
            return dt;
        }""", payload)
        self.page.dispatch_event("#drop", "drop", {"dataTransfer": handle})

    def test_a_typed_folder_path_lists_its_recordings_and_adds_a_course(self):
        self.open()
        self.assertEqual(self.page.inner_text("details.path summary"),
                         "Or use a file or folder already on this computer (no upload, no copy)")
        self.assertEqual(self.page.locator("#pickFolder").count(), 0, "Choose folder is the desktop app's")
        self.type_path(self.folder)
        self.page.wait_for_selector(".local-found .yt-list")
        self.assertEqual(self.page.inner_text(".local-found .yt-chosen .name"), "Signals and systems")
        self.assertEqual(self.page.inner_text(".local-found .yt-chosen .sub"), "Folder on this computer · 3 recordings")
        self.assertEqual(self.texts(".yt-list .t"), ["Lecture 1 - Welcome.wav", "Lecture 2 - Signals.wav",
                                                     "Lecture 10 - Filters.wav"], "natural name order")
        self.assertEqual(self.texts(".yt-list .n"), ["1", "2", "3"])
        self.assertTrue(all(t.endswith("KB") for t in self.texts(".yt-list .len")), "each with its size")
        self.assertEqual(self.page.inner_text(".local-skipped"),
                         "1 subfolder and 1 other file skipped. A subfolder can be added as a course of its own, by its own path.")
        self.assertIn("become a course named after the folder", self.page.inner_text(".yt-caps"))
        self.assertIn("read where it is, not copied", self.page.inner_text(".yt-caps"))
        self.assertIn("the course after the folder", self.page.inner_text(".card:has(#lang)"))
        self.assertEqual(self.page.inner_text("#startBtn"), "Start 3 transcriptions")
        self.page.uncheck(".yt-list li:nth-child(2) input")
        self.assertEqual(self.page.inner_text(".yt-list-head span"), "2 of 3 picked")
        self.assertEqual(self.texts(".yt-list .n"), ["1", "2", "3"], "a folder's numbers stay")
        self.hosted()
        self.assertIn("uploads each recording it transcribes", self.page.inner_text(".consent"))
        self.ran.clear()
        self.page.click("#startBtn")
        self.page.wait_for_selector(".course-bar")
        self.assertIn("Signals and systems · lecture 1 of 3", self.page.inner_text(".course-bar"))
        jobs = self.course_jobs("Signals and systems", 2)
        self.assertEqual([(j["course"]["index"], j["title"], j["status"]) for j in jobs],
                         [(1, "Lecture 1 - Welcome", "done"), (3, "Lecture 10 - Filters", "done")])
        self.assertEqual(self.ran, ["Lecture 1 - Welcome", "Lecture 10 - Filters"], "in the folder's order")
        self.page.wait_for_selector("#jobList .job-item.course:has-text('Signals and systems') .s:has-text('Done')")
        self.assertEqual(self.texts("#jobList .job-item.course:has-text('Signals and systems') .lec .lt"),
                         ["Lecture 1 - Welcome", "Lecture 10 - Filters"], "its lectures grouped in the sidebar")
        self.page.click(".course-bar a:has-text('Next')")
        self.page.wait_for_function("() => document.querySelector('.course-bar')?.innerText.includes('lecture 3 of 3')")
        self.page.wait_for_selector("#transcript .line .text")
        self.assertEqual(self.texts("#transcript .line .text"), ["Words of Lecture 10 - Filters"])
        self.assertEqual(self.page.locator("#openCourse").count(), 0, "no course folder: its files aren't written there")
        self.assertEqual(self.page.locator("#exportCourse").count(), 1, "exported as a course")
        self.page.wait_for_selector(".course-bar #aiCourse")
        self.assertEqual(self.page.inner_text(".course-bar #aiCourse"), "Hide course from assistants")

    def test_several_files_dropped_are_listed_and_become_one_course(self):
        self.open()
        self.page.wait_for_selector("#drop")
        self.assertEqual(self.page.inner_text("#drop .big"), "Drop recordings here")
        self.drop([(NAMES[0], self.folder / NAMES[0]), ("Reading list.pdf", "a list"), (NAMES[1], self.folder / NAMES[1]),
                   (NAMES[2], self.folder / NAMES[2])])
        self.page.wait_for_selector(".local-found .yt-list")
        self.assertEqual(self.page.inner_text(".local-found .yt-chosen .name"), "3 recordings")
        self.assertEqual(self.page.inner_text(".local-found .yt-chosen .sub"), "Picked together, to upload")
        self.assertEqual(self.texts(".yt-list .t"), ["Lecture 1 - Welcome.wav", "Lecture 2 - Signals.wav",
                                                     "Lecture 10 - Filters.wav"], "all of them, in natural name order")
        self.assertEqual(self.page.inner_text(".local-skipped"), "1 other file skipped.", "the one left out, said")
        self.assertIn("uploaded to the app", self.page.inner_text(".yt-caps"))
        name = self.page.input_value("#courseName")
        self.assertRegex(name, r"^Files added on \d{1,2} [A-Z][a-z]{2} \d{4}$", "a name to change")
        self.page.uncheck(".yt-list li:nth-child(1) input")
        self.assertEqual(self.texts(".yt-list .n"), ["", "1", "2"], "files picked together are numbered among the picked")
        self.page.check(".yt-list li:nth-child(1) input")
        self.page.fill("#courseName", "Week 1 recordings")
        self.hosted()
        self.ran.clear()
        self.page.click("#startBtn")
        self.page.wait_for_selector(".course-bar")
        self.assertIn("Week 1 recordings · lecture 1 of 3", self.page.inner_text(".course-bar"))
        jobs = self.course_jobs("Week 1 recordings", 3)
        self.assertEqual([(j["course"]["index"], j["title"], j["status"], j["course"]["local"]) for j in jobs],
                         [(1, "Lecture 1 - Welcome", "done", "files"), (2, "Lecture 2 - Signals", "done", "files"),
                          (3, "Lecture 10 - Filters", "done", "files")])
        self.assertEqual(len({j["course"]["id"] for j in jobs}), 1, "one course")

    def test_one_file_dropped_is_a_file_on_its_own_and_a_folder_dropped_is_explained(self):
        self.open()
        self.page.wait_for_selector("#drop")
        self.drop([(NAMES[2], self.folder / NAMES[2])])
        self.page.wait_for_selector(".chosen .name")
        self.assertEqual(self.page.inner_text(".chosen .name"), NAMES[2], "one file: as before")
        self.assertEqual(self.page.locator(".local-found").count(), 0)
        self.page.click("#changeFile")
        self.drop([("Slides.pdf", "x"), ("notes.txt", "y")])
        self.page.wait_for_selector(".toast")
        self.assertIn("none of these files is a recording (2 other files skipped)", self.page.inner_text(".toast"))
        self.assertEqual(self.page.locator(".local-found").count(), 0)
        self.page.evaluate("() => document.querySelectorAll('.toast').forEach((t) => t.remove())")
        # a folder dropped: a browser gives it as a file whose entry is a folder (app.js), which can't be uploaded
        self.page.evaluate("() => useFiles([], 1)")
        self.page.wait_for_selector(".toast")
        self.assertIn("A folder can't be dropped here: type its full path", self.page.inner_text(".toast"))
        self.page.evaluate("() => useFiles([new File(['x'], 'Lecture 1.wav')], 1)")
        self.page.wait_for_selector(".local-skipped")
        self.assertEqual(self.page.inner_text(".local-skipped"),
                         "1 folder skipped. A folder is added by its full path, so that its recordings are read where they are.")
        self.errors = [e for e in self.errors if "status of 400" not in e]

    def test_files_picked_in_the_field_are_listed(self):
        self.open()
        self.page.set_input_files("#fileInput", [str(self.folder / n) for n in NAMES[:2]])
        self.page.wait_for_selector(".local-found .yt-list")
        self.assertEqual(self.texts(".yt-list .t"), ["Lecture 2 - Signals.wav", "Lecture 10 - Filters.wav"])
        self.page.click("#localChange")
        self.page.wait_for_selector("#drop")
        self.assertEqual(self.page.locator(".local-found").count(), 0)

    def test_messages_for_a_folder(self):
        self.open()
        missing = self.home / "Missing folder"
        self.type_path(missing)
        self.page.wait_for_selector(".toast")
        self.assertIn(f"there is no file or folder at {missing}", self.page.inner_text(".toast"))
        self.page.evaluate("() => document.querySelectorAll('.toast').forEach((t) => t.remove())")
        self.page.fill("#pathInput", str(self.bare))
        self.page.click("#pathBtn")
        self.page.wait_for_selector(".toast")
        self.assertIn("this folder has no recordings (1 other file skipped)", self.page.inner_text(".toast"))
        self.page.evaluate("() => document.querySelectorAll('.toast').forEach((t) => t.remove())")
        self.page.fill("#pathInput", str(self.folder / NAMES[2]))
        self.page.click("#pathBtn")
        self.page.wait_for_selector(".chosen .name")
        self.assertEqual(self.page.inner_text(".chosen .name"), NAMES[2], "a file's path: a file on its own, as before")
        self.assertIn("read in place (not copied)", self.page.inner_text(".chosen .sub"))
        self.errors = [e for e in self.errors if "status of 400" not in e]

    def test_the_desktop_windows_pickers(self):
        self.open()
        paths = [str(self.folder / n) for n in NAMES]
        self.page.evaluate("""([paths, folder]) => {
            window.pywebview = { api: { pick_files: async () => paths, pick_folder: async () => folder } };
            renderNew();
        }""", [paths, str(self.folder)])
        self.assertEqual(self.page.inner_text("#pickFolder"), "Choose folder")
        self.page.click("#drop")
        self.page.wait_for_selector(".local-found .yt-list")
        self.assertEqual(self.page.inner_text(".local-found .yt-chosen .sub"), "Picked together on this computer")
        self.assertIn("read where it is, not copied", self.page.inner_text(".yt-caps"))
        self.assertEqual(self.texts(".yt-list .t"), ["Lecture 1 - Welcome.wav", "Lecture 2 - Signals.wav",
                                                     "Lecture 10 - Filters.wav"])
        self.page.fill("#courseName", "Picked in the window")
        self.hosted()
        self.page.click("#startBtn")
        self.page.wait_for_selector(".course-bar")
        jobs = self.course_jobs("Picked in the window", 3)
        self.assertEqual([j["course"]["index"] for j in jobs], [1, 2, 3])
        detail = self.api("GET", f"/api/jobs/{jobs[0]['id']}")["json"]["job"]
        self.assertEqual(detail["source_path"], os.path.realpath(self.folder / NAMES[2]), "read in place")
        self.page.goto(f"{self.base}/#/new")
        self.page.wait_for_selector("#pickFolder")
        self.page.click("#pickFolder")
        self.page.wait_for_selector(".local-found .yt-list")
        self.assertEqual(self.page.inner_text(".local-found .yt-chosen .name"), "Signals and systems")

    def test_narrow_and_dark(self):
        self.page.set_viewport_size({"width": 390, "height": 800})
        self.open()
        self.page.evaluate("document.documentElement.dataset.theme = 'dark'")
        self.type_path(self.folder)
        self.page.wait_for_selector(".local-found .yt-list")
        self.assertLessEqual(self.page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth"), 0,
                             "no sideways scrolling")
        self.page.click("#localChange")
        self.drop([(NAMES[0], self.folder / NAMES[0]), (NAMES[1], self.folder / NAMES[1])])
        self.page.wait_for_selector("#courseName")
        self.assertLessEqual(self.page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth"), 0)


if __name__ == "__main__":
    unittest.main()
