"""Browser tests of the video on the job page: a transcription of a recording with a picture shows it, and
the bar at the bottom plays it the way it plays the audio of the others (the current line, seeking from a
line, the keys), on a wide and a narrow screen. Audio-only transcriptions look as before.
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui_video.py" -v
"""
import unittest

from media import make_clip
from ui_support import UiTestCase

VIDEO, AUDIO, LATER = "20260925-240000-a1b2", "20260925-240100-b2c3", "20260925-240200-c3d4"


class VideoPage(UiTestCase):
    jobs = ({"jid": VIDEO, "title": "Design review", "video": (320, 180)}, {"jid": AUDIO, "title": "Phone call"},
            {"jid": LATER, "title": "Still converting"})

    def job_page(self, jid=VIDEO):
        self.open(f"#/job/{jid}")
        self.page.wait_for_selector(".line .text")

    def can_play_h264(self):
        if not self.page.evaluate("""() => document.createElement('video')
                .canPlayType('video/mp4; codecs="avc1.42E01E, mp4a.40.2"')"""):
            self.skipTest("this browser can't play H.264 (a Chromium without its proprietary codecs)")
        self.page.wait_for_function("() => video.readyState >= 1")

    def media(self):
        return self.page.evaluate("""() => { const v = video, a = audio;
            return { video: v.getAttribute('src'), audio: a.getAttribute('src'), inPage: document.getElementById('view').contains(v),
                     paused: v.paused, time: v.currentTime, current: [...document.querySelectorAll('#transcript .line.current')].map(e => +e.dataset.i) }; }""")

    def test_the_video_shows_in_the_side_column_and_the_bar_plays_it(self):
        self.job_page()
        panel = self.page.locator("#videoPanel")
        self.assertTrue(panel.is_visible())
        self.assertEqual(self.page.locator(".side-col > #videoPanel:first-child").count(), 1, "on top of the side column")
        m = self.media()
        self.assertTrue(m["inPage"])
        self.assertEqual(m["video"], f"/api/jobs/{VIDEO}/video")
        self.assertIsNone(m["audio"], "the hidden audio is not loaded as well")
        box = self.page.locator("#videoPanel .video-frame").bounding_box()
        self.assertAlmostEqual(box["width"] / box["height"], 16 / 9, delta=0.05)
        self.can_play_h264()
        self.assertEqual(self.page.locator("#durTime").inner_text(), "0:21")
        self.page.click("#playBtn")
        self.page.wait_for_selector("#playBtn[aria-label='Pause']")
        self.assertFalse(self.media()["paused"])
        self.page.click("#playBtn")
        self.page.wait_for_selector("#playBtn[aria-label='Play']")
        self.assertTrue(self.media()["paused"])
        self.page.click("#videoPanel video")  # a click on the picture plays and pauses too
        self.page.wait_for_selector("#playBtn[aria-label='Pause']")
        self.page.click("#videoPanel video")
        self.page.wait_for_selector("#videoPanel.paused")

    def test_the_transcript_follows_the_video(self):
        self.job_page()
        self.can_play_h264()
        self.page.click(".line[data-i='2'] .ts")  # 0:08
        self.page.wait_for_function("() => document.querySelector('#transcript .line[data-i=\"2\"]').classList.contains('current')")
        m = self.media()
        self.assertGreaterEqual(m["time"], 8.0)
        self.assertFalse(m["paused"], "clicking a line plays from there")
        self.page.evaluate("() => { const v = video; v.pause(); v.currentTime = 16; }")
        self.page.wait_for_function("() => document.querySelector('#transcript .line[data-i=\"4\"]').classList.contains('current')")
        self.assertEqual(self.media()["current"], [4])
        self.assertEqual(self.page.locator("#curTime").inner_text(), "0:16")
        seek = int(self.page.input_value("#seek"))
        self.assertAlmostEqual(seek, 16 / 21 * 1000, delta=15)
        self.page.click("#fwdBtn")  # the bar's buttons move the video
        self.page.wait_for_function("() => video.currentTime >= 20.9")
        self.page.locator("#rate").select_option("1.5")
        self.assertEqual(self.page.evaluate("() => video.playbackRate"), 1.5)

    def test_keys_and_a_redrawn_page_keep_the_video_playing(self):
        self.job_page()
        self.can_play_h264()
        self.page.locator("#transcript").click(position={"x": 400, "y": 5})  # nothing that types
        self.page.keyboard.press("Space")
        self.page.wait_for_function("() => !video.paused")
        self.page.evaluate("() => { window.__shown = document.querySelector('#videoPanel video'); renderJob(); }")
        self.page.wait_for_timeout(400)
        state = self.page.evaluate("() => ({ same: document.querySelector('#view #videoPanel video') === window.__shown,"
                                   " paused: window.__shown.paused, inPage: document.getElementById('view').contains(window.__shown) })")
        self.assertEqual(state, {"same": True, "paused": False, "inPage": True}, "drawing the page again doesn't stop it")
        self.page.keyboard.press("Space")
        self.page.wait_for_function("() => video.paused")
        before = self.media()["time"]
        self.page.keyboard.press("ArrowRight")
        self.page.wait_for_function(f"() => video.currentTime >= {before + 4.9}")

    def test_the_video_can_be_made_larger_and_it_is_remembered(self):
        self.page.set_viewport_size({"width": 1600, "height": 900})
        self.job_page()
        small = self.page.locator("#videoPanel").bounding_box()["width"]
        self.page.locator("#videoPanel").hover()
        self.assertEqual(self.page.get_attribute("#videoSize", "aria-pressed"), "false")
        self.page.click("#videoSize")
        self.assertEqual(self.page.get_attribute("#videoSize", "aria-pressed"), "true")
        large = self.page.locator("#videoPanel").bounding_box()["width"]
        self.assertGreater(large, small * 1.4)
        self.page.goto(f"{self.base}/#/job/{VIDEO}")  # the setting stays (in this browser)
        self.page.wait_for_selector(".job-body.video-large #videoPanel")
        self.page.click("#videoSize")
        self.assertEqual(self.page.locator(".job-body.video-large").count(), 0)
        self.assertFalse(self.page.evaluate("() => document.documentElement.scrollWidth > innerWidth"))

    def test_on_a_narrow_screen_the_video_stays_above_the_transcript(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.job_page()
        video = self.page.locator("#videoPanel").bounding_box()
        lines = self.page.locator("#transcript").bounding_box()
        self.assertLess(video["y"] + video["height"], lines["y"], "above the transcript")
        self.assertFalse(self.page.evaluate("() => document.documentElement.scrollWidth > innerWidth"))
        self.page.evaluate("() => document.getElementById('main').scrollTo(0, 600)")
        self.page.wait_for_timeout(100)
        top = self.page.locator("#main").bounding_box()["y"]
        video = self.page.locator("#videoPanel").bounding_box()
        self.assertAlmostEqual(video["y"], top, delta=2, msg="kept in view while the transcript scrolls")
        player = self.page.locator("#player").bounding_box()
        self.assertLess(video["y"] + video["height"], player["y"], "the bar doesn't cover it")
        x, y = video["x"] + video["width"] / 2, video["y"] + video["height"] / 2
        self.assertTrue(self.page.evaluate("([x, y]) => !!document.elementFromPoint(x, y).closest('#videoPanel')", [x, y]),
                        "nothing is drawn over it")

    def test_a_job_without_video_is_as_before(self):
        self.job_page(AUDIO)
        self.assertEqual(self.page.locator("#view #videoPanel").count(), 0)
        self.assertEqual(self.page.locator(".job-body.has-video, .video-prep").count(), 0)
        self.assertEqual(self.page.locator(".side-col").bounding_box()["width"], 250)
        m = self.media()
        self.assertEqual(m["audio"], f"/api/jobs/{AUDIO}/audio")
        self.assertIsNone(m["video"])
        self.page.click(".line[data-i='1'] .ts")
        self.page.wait_for_function("() => !audio.paused")
        self.page.click("#playBtn")
        # from a video job to an audio one and back: the bar switches what it plays
        self.job_page(VIDEO)
        self.assertEqual(self.media()["video"], f"/api/jobs/{VIDEO}/video")
        self.assertIsNone(self.media()["audio"])
        self.job_page(AUDIO)
        self.assertEqual((self.media()["audio"], self.media()["video"]), (f"/api/jobs/{AUDIO}/audio", None))

    def test_a_video_still_being_made_shows_its_progress_then_itself(self):
        store = self.app.store
        store.update(LATER, video_prep={"done": 5.0, "total": 20.0})
        self.job_page(LATER)
        box = self.page.locator(".video-prep")
        self.assertIn("Preparing the video", box.inner_text())
        self.assertIn("25%", box.inner_text())
        self.assertEqual(self.media()["audio"], f"/api/jobs/{LATER}/audio", "the audio plays meanwhile")
        store.update(LATER, video_prep={"done": 15.0, "total": 20.0})
        self.page.wait_for_function("() => document.getElementById('videoPrepText').textContent.startsWith('75%')")
        make_clip(store.dir(LATER) / "video.mp4", 320, 240, seconds=21.0)
        store.update(LATER, video_prep=None, video={"width": 320, "height": 240})
        self.page.wait_for_selector("#view #videoPanel")
        self.page.wait_for_selector(".toast >> text=The video is ready")
        self.assertEqual(self.page.locator(".video-prep").count(), 0)
        self.assertEqual(self.media()["video"], f"/api/jobs/{LATER}/video")
        box = self.page.locator("#videoPanel .video-frame").bounding_box()
        self.assertAlmostEqual(box["width"] / box["height"], 4 / 3, delta=0.05)

    def test_dark_theme(self):
        self.page.emulate_media(color_scheme="dark")
        self.job_page()
        self.assertEqual(self.page.evaluate("() => document.documentElement.dataset.theme"), "dark")
        colour = self.page.evaluate("() => getComputedStyle(document.querySelector('#videoPanel .video-frame')).backgroundColor")
        self.assertEqual(colour, "rgb(0, 0, 0)")
        self.page.locator("#videoPanel").hover()
        self.assertEqual(self.page.evaluate("() => getComputedStyle(document.querySelector('.video-tools')).opacity"), "1")


if __name__ == "__main__":
    unittest.main()
