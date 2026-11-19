"""Browser tests of exporting many transcripts at once (#9, app/static/exports.js): the Export dialog from the sidebar
lists the finished transcriptions, a course's lectures under it; All, a course's box and the search; a zip, one
combined file (not for subtitles), and a folder, through a stand-in for the browser's folder picker; and Export
course on a lecture's page, which opens the dialog with the course ticked.
Headless Chromium against a Sedjem server with demo transcripts (see tests/ui_support.py).
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui_export_many.py" -v
"""
import io
import json
import unittest
import zipfile

from playwright.sync_api import expect
from ui_support import UiTestCase

MEETING, CALL = "20261019-100000-a001", "20261019-090000-b001"
LEC1, LEC2, LEC3 = "20261019-110000-c001", "20261019-110000-c002", "20261019-110000-c003"
COURSE = {"id": "PLpython", "title": "Python for beginners", "url": "https://www.youtube.com/playlist?list=PLpython",
          "count": 3, "folder": "Python for beginners"}
LECTURE = lambda n: {"course": {**COURSE, "index": n}, "youtube_id": f"vidpython0{n}",  # noqa: E731
                     "source_url": f"https://www.youtube.com/watch?v=vidpython0{n}", "created": "2026-10-19T11:00:00+03:00"}
NO_PICKER = "window.showSaveFilePicker = undefined;"  # a browser that downloads, as Firefox
# stands in for the browser's Save dialog (showSaveFilePicker) and keeps the name and the text written
PICKER = """window.showSaveFilePicker = async (opts) => ({ name: opts.suggestedName, createWritable: async () => {
  const parts = [];
  return { write: async (b) => parts.push(b),
           close: async () => { window.__saved = { name: opts.suggestedName, text: await new Blob(parts).text() }; } };
} });"""
# A folder picker of the browser's kind (showDirectoryPicker), kept in memory: what is written lands in window.__written
FOLDER = """(() => {
  window.__written = {};
  const dir = (prefix) => ({ name: prefix ? prefix.slice(0, -1) : "Exports",
    getDirectoryHandle: async (n) => dir(prefix + n + "/"),
    getFileHandle: async (n) => ({ createWritable: async () => {
      let text = ""; return { write: async (t) => { text += t; }, close: async () => { window.__written[prefix + n] = text; } }; } }) });
  window.showDirectoryPicker = async () => dir("");
})();"""


class ExportMany(UiTestCase):
    jobs = ({"jid": MEETING, "title": "Sprint planning", "created": "2026-10-19T10:00:00+03:00"},
            {"jid": CALL, "title": "Client call", "created": "2026-10-19T09:00:00+03:00"},
            {"jid": LEC1, "title": "Variables", **LECTURE(1)},
            {"jid": LEC2, "title": "Loops", **LECTURE(2)},
            {"jid": LEC3, "title": "Functions", **LECTURE(3)})

    def setUp(self):
        super().setUp()
        self.page.add_init_script(NO_PICKER)

    def open_dialog(self, early=False):
        self.open()
        if not early:
            expect(self.page.locator("#jobList .lec")).to_have_count(3)  # the transcriptions have come in
        self.page.click("#exportManyBtn")
        self.page.wait_for_selector("#exportMany[open] #expList")

    def go(self):
        return self.page.locator("#expGo")

    def test_the_list_all_and_a_zip(self):
        self.open_dialog()
        expect(self.page.locator("#expList [data-exp]")).to_have_count(5)
        expect(self.page.locator("#expList .exp-head .exp-t")).to_have_text("Python for beginners")
        expect(self.page.locator("#expList .exp-lectures .exp-t")).to_have_text(["Variables", "Loops", "Functions"])
        expect(self.go()).to_be_disabled()
        self.page.check("#expAll")
        expect(self.go()).to_have_text("Export 5 as a zip")
        expect(self.page.locator("#expList [data-exp-course]")).to_be_checked()
        with self.page.expect_download() as dl:
            self.go().click()
        self.assertEqual(dl.value.suggested_filename, "sedjem-transcripts.zip")
        with zipfile.ZipFile(io.BytesIO(open(dl.value.path(), "rb").read())) as z:
            self.assertEqual(sorted(z.namelist()), ["Client-call.txt", "Python-for-beginners/01-Variables.txt",
                                                    "Python-for-beginners/02-Loops.txt",
                                                    "Python-for-beginners/03-Functions.txt", "Sprint-planning.txt"])
        expect(self.page.locator("#exportMany")).not_to_have_attribute("open", "")
        self.page.wait_for_selector(".toast >> text=Exported 5 transcripts")

    def test_a_course_box_the_search_and_one_combined_file(self):
        self.page.add_init_script(PICKER)  # after NO_PICKER, so this one counts
        self.open_dialog()
        self.page.focus("#expList [data-exp-course]")
        self.page.keyboard.press("Space")  # by keyboard: the focus stays on the course's box after the list is new
        self.assertTrue(self.page.evaluate("() => document.activeElement.matches('[data-exp-course]')"))
        expect(self.go()).to_have_text("Export 3 as a zip")
        self.page.uncheck(f"#expList [data-exp='{LEC2}']")
        self.assertTrue(self.page.eval_on_selector("#expList [data-exp-course]", "b => b.indeterminate"))
        self.page.fill("#expSearch", "sprint")
        expect(self.page.locator("#expList [data-exp]")).to_have_count(1)
        self.page.check(f"#expList [data-exp='{MEETING}']")
        self.page.fill("#expSearch", "")
        expect(self.go()).to_have_text("Export 3 as a zip")  # what the search hid stays ticked
        self.page.select_option("#expFormat", "srt")
        expect(self.page.locator("#expCombined")).to_be_disabled()  # subtitles keep a file each
        expect(self.page.locator("#expWhy")).to_have_text(" (not for subtitles)")
        self.page.select_option("#expFormat", "json")
        self.page.check("#expCombined")
        expect(self.go()).to_have_text("Export 3 in one file")
        expect(self.page.locator("#expFolder")).to_be_hidden()
        self.go().click()
        self.page.wait_for_function("() => window.__saved")
        saved = self.page.evaluate("() => window.__saved")
        self.assertEqual(saved["name"], "sedjem-transcripts.json")  # the name the save picker suggested
        self.assertEqual([x["title"] for x in json.loads(saved["text"])], ["Variables", "Functions", "Sprint planning"])

    def test_into_a_folder(self):
        self.page.add_init_script(FOLDER)
        self.open_dialog()
        self.page.check(f"#expList [data-exp='{CALL}']")
        self.page.check(f"#expList [data-exp='{LEC1}']")
        self.page.select_option("#expFormat", "md")
        expect(self.page.locator("#expFolder")).to_be_visible()
        self.page.click("#expFolder")
        self.page.wait_for_selector(".toast >> text=Saved 2 files in Exports")
        written = self.page.evaluate("() => window.__written")
        self.assertEqual(sorted(written), ["Client-call.md", "Python-for-beginners/01-Variables.md"])
        self.assertTrue(written["Client-call.md"].startswith("# Client call"))

    def test_export_course_from_a_lecture(self):
        self.open(f"#/job/{LEC2}")
        self.page.click("#exportCourse")
        self.page.wait_for_selector("#exportMany[open] #expList")
        expect(self.go()).to_have_text("Export 3 as a zip")
        expect(self.page.locator(f"#expList [data-exp='{MEETING}']")).not_to_be_checked()
        with self.page.expect_download() as dl:
            self.go().click()
        self.assertEqual(dl.value.suggested_filename, "Python-for-beginners.zip")

    def test_opened_before_the_transcriptions_came_in(self):
        self.page.route("**/api/jobs", lambda route: (self.page.wait_for_timeout(1500), route.continue_()))
        self.open_dialog(early=True)
        expect(self.page.locator("#expList [data-exp]")).to_have_count(5)
        self.page.unroute_all(behavior="ignoreErrors")

    def test_dark_and_narrow(self):
        self.page.set_viewport_size({"width": 380, "height": 760})
        self.page.emulate_media(color_scheme="dark")
        self.open()
        self.page.evaluate("() => document.documentElement.dataset.theme = 'dark'")
        self.page.evaluate("() => openExportMany()")
        self.page.wait_for_selector("#exportMany[open] #expList")
        width = self.page.eval_on_selector("#exportMany", "d => d.getBoundingClientRect().width")
        self.assertLessEqual(width, 380)
        self.assertFalse(self.page.evaluate("() => document.documentElement.scrollWidth > innerWidth"), "no sideways scroll")


if __name__ == "__main__":
    unittest.main()
