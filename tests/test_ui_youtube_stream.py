"""Browser tests of YouTube videos added with streaming (app/static/youtube.js): the job page plays the video in
YouTube's embedded player, driven by postMessage, in step with the transcript (a line's time seeks the player, and
the current line follows the player's time); when the player doesn't answer, or reports an error, the sound that was
downloaded with the job plays instead and the page says why; jobs that downloaded the video, and older ones, play as
before; and the form's choice between downloading the video and streaming it is a page preference, used for the jobs
it adds. YouTube's player is a stand-in page that Playwright serves for https://www.youtube-nocookie.com/embed/...,
which speaks the player's side of the messages, and yt-dlp is the stand-in of tests/youtube_support.py, so nothing
is fetched from the network.
Run: .venv/bin/python -m unittest discover -s tests -p "test_ui_youtube_stream.py" -v
"""
import json
import time
import unittest
import urllib.parse
from unittest import mock

from ui_support import UiTestCase  # (puts the project on the path)
from youtube_support import FakeYouTube, vtt

from app import youtube

EMBED = "https://www.youtube-nocookie.com/embed/**"
STREAMED, ERRED, SILENT, DOWNLOADED, OLDER, NEXT = (
    "20261101-100000-e001", "20261101-100000-e002", "20261101-100000-e003", "20261101-100000-e004",
    "20261101-100000-e005", "20261101-100000-e006")
VIDEO = lambda vid, **more: {"youtube_id": vid, "source_url": f"https://www.youtube.com/watch?v={vid}", **more}
DENO = {"name": "Deno", "version": "2.9.5", "downloaded": True}

# YouTube's player as the page sees it: it answers "listening" with onReady and its state, does the commands it is
# sent (kept in window.commands for the test), and while it plays, tells its time four times a second. setTime(t,
# state) makes it tell another time, as when the user moves YouTube's own controls.
PLAYER = """<!doctype html><html><head><title>Stand-in player</title><style>
html, body { margin: 0; height: 100%; background: radial-gradient(circle at 50% 40%, #3a3f47, #0b0c0e 70%);
  color: #e8eaed; font: 14px system-ui, sans-serif; display: grid; place-items: center; text-align: center; }
.play { width: 68px; height: 48px; border-radius: 12px; background: #c00; margin: 0 auto 10px; position: relative; }
.play::after { content: ""; position: absolute; left: 27px; top: 14px; border: 10px solid transparent;
  border-left: 16px solid #fff; border-right: 0; }
</style></head><body><div><div class="play"></div><div id="t">Stand-in for YouTube's player</div></div><script>
window.commands = []; window.hellos = 0;
let time = 0, state = 5, rate = 1, clock = null, id = null, ready = false;
const send = (m) => parent.postMessage(JSON.stringify({ ...m, id, channel: "widget" }), "*");
const info = () => send({ event: "infoDelivery", info: { currentTime: time, playerState: state, duration: 21,
                                                         playbackRate: rate } });
const run = (on) => { clearInterval(clock); if (on) clock = setInterval(() => { time += 0.25 * rate; info(); }, 250); };
window.setTime = (t, s) => { time = t; if (s !== undefined) { state = s; run(s === 1); } info(); };
addEventListener("message", (e) => {
  if (e.source !== parent) return;
  const m = JSON.parse(e.data);
  id = m.id;
  if (m.event === "listening") {
    hellos++;
    if (!ready) { ready = true; send({ event: "onReady" }); info(); }
    return;
  }
  if (m.event !== "command") return;
  commands.push([m.func, ...(m.args || [])]);
  if (m.func === "seekTo") { time = m.args[0]; info(); }
  if (m.func === "playVideo") { state = 1; run(true); info(); }
  if (m.func === "pauseVideo") { state = 2; run(false); info(); }
  if (m.func === "setPlaybackRate") { rate = m.args[0]; info(); }
});
</script></body></html>"""
# A player that loads but never answers, as when the computer has no internet and the frame shows an error page
SILENT_PLAYER = "<!doctype html><title>No answer</title><body style='background:#000'></body>"
# A player that YouTube doesn't let other sites show: ready, then error 150
REFUSING_PLAYER = """<!doctype html><title>Refused</title><body style="background:#000"><script>
addEventListener("message", (e) => {
  const m = JSON.parse(e.data);
  if (m.event !== "listening" || window.done) return;
  window.done = true;
  for (const x of [{ event: "onReady" }, { event: "onError", info: 150 }]) parent.postMessage(JSON.stringify(x), "*");
});
</script></body>"""
FALLBACK = "YouTube's player couldn't load, so the sound plays from this computer."


class StreamedVideo(UiTestCase):
    jobs = ({"jid": STREAMED, "title": "Streamed lecture", **VIDEO("vidstream01", stream=True)},
            {"jid": ERRED, "title": "Not embeddable", **VIDEO("vidstream02", stream=True)},
            {"jid": SILENT, "title": "Offline lecture", **VIDEO("vidstream03", stream=True)},
            {"jid": DOWNLOADED, "title": "Downloaded lecture", "video": (320, 180), **VIDEO("viddownl001")},
            {"jid": OLDER, "title": "Older lecture", **VIDEO("vidolder001")},
            {"jid": NEXT, "title": "Next streamed lecture", "seconds": 15.0, **VIDEO("vidstream04", stream=True)})

    @classmethod
    def setUpClass(cls):
        fake = FakeYouTube()
        fake.video("vidnewstrm1", "Streamed from the form", seconds=4, language="en",
                   captions={"en": vtt((0.5, 2.0, "Welcome to the course."), (2.5, 3.5, "Today we stream it."))})
        cls.fake = fake
        cls.patches = [mock.patch.object(youtube, "_ydl", fake.ydl), mock.patch.object(youtube, "runtime", lambda cfg: DENO)]
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

    def setUp(self):
        super().setUp()
        self.asked = []  # the embed addresses the page asked for

    def player(self, body=PLAYER):
        """Serve the stand-in for YouTube's player, for every video."""
        def fulfil(route):
            self.asked.append(route.request.url)
            route.fulfill(status=200, content_type="text/html; charset=utf-8", body=body)
        self.page.route(EMBED, fulfil)

    def job_page(self, jid):
        self.open(f"#/job/{jid}")
        self.page.wait_for_function(f"() => S.job?.id === '{jid}' && document.querySelector('#transcript .line .text')")

    def frame(self):
        self.page.wait_for_function("() => ytMedia.ready")
        return next(f for f in self.page.frames
                    if f.url.startswith("https://www.youtube-nocookie.com/embed/") and not f.is_detached())

    def wait_for_command(self, frame, *command):
        want = json.dumps(json.dumps(list(command), separators=(",", ":")))  # as JSON.stringify writes it
        frame.wait_for_function(f"() => window.commands.some(c => JSON.stringify(c) === {want})")

    def commands(self, frame):
        return frame.evaluate("() => window.commands")

    def current(self):
        return self.page.eval_on_selector_all("#transcript .line.current", "els => els.map(e => +e.dataset.i)")

    def test_the_video_streams_in_step_with_the_transcript(self):
        self.player()
        self.job_page(STREAMED)
        self.assertTrue(self.page.is_visible("#view #videoPanel.yt #ytStart"), "a button in the player's place")
        self.assertIn("Streamed from YouTube", self.page.inner_text("#videoPanel .video-tools"))
        self.assertFalse(self.page.is_visible("#videoPanel video"))
        self.assertIsNone(self.page.evaluate("() => audio.getAttribute('src')"), "the sound isn't loaded as well")
        self.assertEqual(self.page.locator(".side-col > #videoPanel:first-child").count(), 1, "on top of the side column")
        self.assertEqual(self.page.inner_text("#durTime"), "0:21")
        self.page.wait_for_timeout(300)
        self.assertEqual((self.page.locator("iframe").count(), self.asked), (0, []), "YouTube isn't asked until it plays")

        self.page.click(".line[data-i='2'] .ts")  # 0:08: the player loads, goes there and plays
        iframe = self.page.locator("#view #videoPanel.yt .video-frame iframe")
        self.assertEqual(iframe.count(), 1)
        self.assertFalse(self.page.is_visible("#ytStart"))
        origin = urllib.parse.quote(self.base, safe="")
        self.assertEqual(iframe.get_attribute("src"), "https://www.youtube-nocookie.com/embed/vidstream01"
                         f"?enablejsapi=1&playsinline=1&rel=0&origin={origin}")
        self.assertEqual(iframe.get_attribute("referrerpolicy"), "strict-origin-when-cross-origin")
        self.assertEqual(iframe.get_attribute("allow"), "autoplay; encrypted-media; picture-in-picture; fullscreen")
        self.assertTrue(iframe.get_attribute("title"))
        frame = self.frame()
        self.assertEqual(self.page.evaluate("() => [...document.scripts].filter(s => s.src && !s.src.startsWith(location.origin))"
                                            ".length + (window.onYouTubeIframeAPIReady === undefined ? 0 : 1)"), 0,
                         "no script from elsewhere: YouTube's API script isn't loaded")
        self.wait_for_command(frame, "seekTo", 8, True)
        self.wait_for_command(frame, "playVideo")
        self.page.wait_for_selector("#transcript .line[data-i='2'].current")
        self.page.wait_for_selector("#playBtn[aria-label='Pause']")
        self.page.wait_for_function("() => ytMedia.currentTime > 8.4", timeout=5000)  # the player's clock runs

        frame.evaluate("() => setTime(16, 2)")  # paused at 0:16 in YouTube's own controls: the transcript follows
        self.page.wait_for_selector("#transcript .line[data-i='4'].current")
        self.page.wait_for_selector("#playBtn[aria-label='Play']")
        self.assertEqual(self.current(), [4])
        self.assertEqual(self.page.inner_text("#curTime"), "0:16")
        self.assertAlmostEqual(int(self.page.input_value("#seek")), 16 / 21 * 1000, delta=15)
        frame.evaluate("() => setTime(19.5, 1)")  # and while it plays
        self.page.wait_for_selector("#transcript .line[data-i='5'].current")

        self.page.click("#playBtn")
        self.wait_for_command(frame, "pauseVideo")
        self.page.locator("#rate").select_option("1.5")
        self.wait_for_command(frame, "setPlaybackRate", 1.5)
        self.page.click("#backBtn")
        frame.wait_for_function("() => window.commands.some(c => c[0] === 'seekTo' && c[1] < 19)")

        # a message from anywhere but that frame is left alone
        self.page.evaluate("() => window.postMessage(JSON.stringify({ event: 'onError', info: 150 }), '*')")
        self.page.wait_for_timeout(300)
        self.assertEqual(self.page.locator("#view #videoPanel iframe").count(), 1)
        self.assertEqual(self.page.locator("#ytFallback").count(), 0)

        # the page drawn again keeps the same player: where the browser can move it (moveBefore), not loaded again
        if self.page.evaluate("() => !!Element.prototype.moveBefore"):
            self.page.evaluate("() => { window.__loads = 0; window.__frame = ytMedia.frame;"
                               " ytMedia.frame.addEventListener('load', () => window.__loads++); renderJob(); }")
            self.page.wait_for_timeout(500)
            kept = self.page.evaluate("() => ({ same: document.querySelector('#view #videoPanel iframe') === window.__frame,"
                                      " loads: window.__loads })")
            self.assertEqual(kept, {"same": True, "loads": 0}, "not loaded again")
            self.assertEqual(len(self.asked), 1)
        # in a browser that can't (WebKitGTK may not), it loads once more and plays on from where it was
        self.page.click("#playBtn")
        self.page.wait_for_function("() => !ytMedia.paused && ytMedia.state === 1")
        asked = len(self.asked)
        self.page.evaluate("() => { Element.prototype.moveBefore = undefined; window.__frame = ytMedia.frame;"
                           " window.__again = false; ytMedia.frame.addEventListener('load', () => (window.__again = true));"
                           " window.__at = ytMedia.currentTime; renderJob(); }")
        self.page.wait_for_function("() => window.__again && ytMedia.ready && !ytMedia.paused")
        self.assertTrue(self.page.evaluate("() => document.querySelector('#view #videoPanel iframe') === window.__frame"))
        frame = self.frame()
        self.wait_for_command(frame, "playVideo")
        at = self.page.evaluate("() => window.__at")
        self.assertTrue(any(c[0] == "seekTo" and c[1] >= at for c in self.commands(frame)), self.commands(frame))
        self.assertEqual(len(self.asked), asked + 1, "loaded once more")

        # left and opened again, it waits to be played, from where it was
        self.open("#/new")
        self.job_page(STREAMED)
        self.assertTrue(self.page.is_visible("#ytStart"))
        self.assertEqual(self.page.locator("iframe").count(), 0)
        at = self.page.evaluate("() => ytMedia.currentTime")
        self.assertGreater(at, 10)
        self.page.click("#ytStart")
        frame = self.frame()
        self.wait_for_command(frame, "playVideo")
        self.assertEqual([c for c in self.commands(frame) if c[0] != "addEventListener"],
                         [["setPlaybackRate", 1.5], ["seekTo", at, True], ["playVideo"]], "the bar's speed, too")

    def test_the_next_lecture_keeps_the_speed_and_starts_at_0(self):
        self.player()
        self.job_page(STREAMED)
        self.page.locator("#rate").select_option("1.5")
        self.page.click(".line[data-i='4'] .ts")  # 0:15
        frame = self.frame()
        self.wait_for_command(frame, "setPlaybackRate", 1.5)
        frame.evaluate("() => setTime(16, 2)")  # paused at 0:16
        self.page.wait_for_function("() => document.getElementById('curTime').textContent === '0:16'")
        self.job_page(NEXT)  # in the same page, as from the sidebar or the course bar
        bar = self.page.evaluate("() => ({ rate: document.getElementById('rate').value, speed: ytMedia.playbackRate,"
                                 " time: document.getElementById('curTime').textContent, seek: document.getElementById('seek').value,"
                                 " length: document.getElementById('durTime').textContent, at: ytMedia.currentTime })")
        self.assertEqual(bar, {"rate": "1.5", "speed": 1.5, "time": "0:00", "seek": "0", "length": "0:15", "at": 0})
        self.page.click("#ytStart")
        frame = self.frame()
        self.assertIn("/embed/vidstream04?", frame.url)
        self.wait_for_command(frame, "setPlaybackRate", 1.5)
        self.wait_for_command(frame, "playVideo")
        self.assertFalse(any(c[0] == "seekTo" for c in self.commands(frame)), "from the start")

    def test_without_an_answer_the_sound_plays_and_it_can_try_again(self):
        self.player(SILENT_PLAYER)
        self.job_page(SILENT)
        self.page.click(".line[data-i='2'] .ts")  # the player loads, and never answers
        self.page.wait_for_selector("#view #videoPanel iframe")
        self.page.wait_for_selector("#ytFallback", timeout=15_000)  # about 8 s
        self.assertEqual(self.page.locator("#ytFallback .yt-fallback-text").inner_text(), FALLBACK)
        self.assertEqual(self.page.locator("#view #videoPanel, iframe").count(), 0)
        self.assertEqual(self.page.locator(".job-body.has-video").count(), 0)
        self.assertEqual(self.page.evaluate("() => audio.getAttribute('src').split('?sig=')[0]"), f"/api/jobs/{SILENT}/audio")
        self.page.wait_for_function("() => !audio.paused && audio.currentTime >= 8", timeout=5000)
        self.assertEqual(self.current(), [2], "from the line that was clicked")
        self.page.evaluate("() => renderJob()")  # drawn again, it still plays the sound, and says why
        self.assertEqual(self.page.locator("#ytFallback .yt-fallback-text").inner_text(), FALLBACK)
        self.assertEqual(self.page.locator("iframe").count(), 0)
        link = self.page.locator("#ytFallback a")
        self.assertEqual((link.inner_text().strip(), link.get_attribute("href"), link.get_attribute("target"),
                          link.get_attribute("rel")),
                         ("Open on YouTube", "https://www.youtube.com/watch?v=vidstream03", "_blank", "noopener noreferrer"))

        self.player(PLAYER)  # now YouTube answers (the route added last is used first)
        self.page.click("#ytRetry")
        frame = self.frame()
        self.wait_for_command(frame, "playVideo")  # the sound was playing, so the video plays on from there
        self.assertTrue(any(c[0] == "seekTo" and c[1] >= 8 for c in self.commands(frame)), self.commands(frame))
        self.assertEqual(self.page.locator("#ytFallback").count(), 0)
        self.assertTrue(self.page.is_visible("#view .job-body.has-video #videoPanel.yt iframe"))
        self.assertTrue(self.page.evaluate("() => media === ytMedia && audio.paused && !audio.getAttribute('src')"))

    def test_an_error_from_the_player_plays_the_sound(self):
        self.player(REFUSING_PLAYER)
        self.job_page(ERRED)
        self.page.click("#playBtn")  # the bar's button loads the player too
        self.page.wait_for_selector("#ytFallback", timeout=5000)
        self.assertEqual(self.page.locator("#view #videoPanel, iframe").count(), 0)
        self.assertEqual(self.page.evaluate("() => audio.getAttribute('src').split('?sig=')[0]"), f"/api/jobs/{ERRED}/audio")
        self.page.wait_for_function("() => !audio.paused", timeout=5000)
        self.page.click(".line[data-i='1'] .ts")
        self.page.wait_for_function("() => !audio.paused && audio.currentTime >= 4")

    def test_downloaded_and_older_videos_are_as_before(self):
        self.player()
        self.job_page(DOWNLOADED)
        self.assertEqual(self.page.evaluate("() => video.getAttribute('src').split('?sig=')[0]"), f"/api/jobs/{DOWNLOADED}/video")
        self.assertEqual(self.page.locator("#videoPanel.yt, iframe").count(), 0)
        self.assertTrue(self.page.is_visible("#view #videoPanel video"))
        self.job_page(OLDER)
        self.assertEqual(self.page.evaluate("() => audio.getAttribute('src').split('?sig=')[0]"), f"/api/jobs/{OLDER}/audio")
        self.assertEqual(self.page.locator("#view #videoPanel, iframe").count(), 0)
        self.job_page(STREAMED)  # from a streamed video back to a downloaded one: the panel shows its video again
        self.page.click("#ytStart")
        self.frame()
        self.job_page(DOWNLOADED)
        self.assertEqual(self.page.locator("#videoPanel.yt, iframe").count(), 0)
        self.assertTrue(self.page.is_visible("#view #videoPanel video"))
        self.assertEqual(len(self.asked), 1, "only the streamed video asked YouTube")

    def test_narrow_and_dark(self):
        self.player()
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.emulate_media(color_scheme="dark")
        self.job_page(STREAMED)
        self.page.click("#ytStart")
        self.frame()
        panel = self.page.locator("#videoPanel").bounding_box()
        lines = self.page.locator("#transcript").bounding_box()
        self.assertLess(panel["y"] + panel["height"], lines["y"], "above the transcript")
        self.assertLessEqual(self.page.evaluate("() => document.documentElement.scrollWidth - innerWidth"), 0)
        bar = self.page.evaluate("() => getComputedStyle(document.querySelector('#videoPanel .video-tools')).backgroundColor")
        self.assertEqual(bar, "rgb(22, 26, 32)", "the dark theme's surface")
        player = self.page.locator("#player").bounding_box()
        for y in (0, 600):  # as the page opens, and kept in view while the transcript scrolls
            self.page.evaluate(f"() => document.getElementById('main').scrollTo(0, {y})")
            self.page.wait_for_timeout(150)
            if y == 0:  # the panel starts below the title and the buttons: the page scrolls it into view
                self.page.locator("#videoSource").scroll_into_view_if_needed()
            tools = self.page.locator("#videoPanel .video-tools").bounding_box()
            self.assertLessEqual(tools["y"] + tools["height"], player["y"], f"the tools row clear of the bar ({y})")
            x, ty = tools["x"] + 30, tools["y"] + tools["height"] / 2
            self.assertTrue(self.page.evaluate("([x, y]) => !!document.elementFromPoint(x, y).closest('.video-tools')",
                                               [x, ty]), f"nothing drawn over it ({y})")

    def test_the_choice_on_the_form_is_remembered_and_used(self):
        self.player()
        self.open()
        self.page.click("[data-src=link]")
        self.assertTrue(self.page.is_checked("input[name=ytVideo][value=download]"), "as before until the user picks")
        group = self.page.get_by_role("group", name="The video")
        self.assertEqual(group.get_by_role("radio").count(), 2, "a fieldset named by its legend")
        for v, hint in (("download", "It plays without internet."),
                        ("stream", "Only the sound is downloaded. The video plays from YouTube.")):
            described = self.page.get_attribute(f"input[name=ytVideo][value={v}]", "aria-describedby")
            self.assertEqual(self.page.inner_text(f"#{described}"), hint)
        self.assertNotIn("sedjem-youtube-video", self.prefs())
        self.page.click("label.yt-video-opt:has-text('Stream it from YouTube')")
        self.assertTrue(self.page.is_checked("input[name=ytVideo][value=stream]"))
        end = time.time() + 10
        while self.prefs().get("sedjem-youtube-video") != "stream" and time.time() < end:
            time.sleep(0.05)
        self.assertEqual(self.prefs().get("sedjem-youtube-video"), "stream", "kept in the data folder")
        self.page.evaluate("() => localStorage.clear()")  # as at the next start, on a new port: from the data folder
        self.page.reload()
        self.page.wait_for_function("() => typeof S !== 'undefined' && S.status")
        self.page.click("[data-src=link]")
        self.assertTrue(self.page.is_checked("input[name=ytVideo][value=stream]"), "remembered")

        self.page.fill("#ytUrl", "https://youtu.be/vidnewstrm1")
        self.page.click("#ytForm button[type=submit]")
        self.page.wait_for_selector(".yt-caps")
        self.assertIn("Its sound is still downloaded", self.page.inner_text(".yt-caps"))
        self.page.click(".model-card[data-model=elevenlabs]")
        self.page.check("#confirmUpload")
        self.page.click("#startBtn")
        self.page.wait_for_selector(".course-bar")
        jid = self.page.evaluate("() => S.route.id")
        end, job = time.time() + 60, {}
        while time.time() < end and job.get("status") != "done":
            job = self.api("GET", f"/api/jobs/{jid}")["json"]["job"]
            time.sleep(0.2)
        self.assertEqual((job.get("status"), job.get("stream"), job.get("video")), ("done", True, None), job)
        self.assertEqual(self.fake.downloads, [("vidnewstrm1", "140")], "only the sound was downloaded")
        self.page.wait_for_selector("#view #videoPanel.yt #ytStart")
        self.assertEqual(self.texts("#transcript .line .text"), ["Welcome to the course.", "Today we stream it."])

        self.open()
        self.page.click("[data-src=link]")
        self.page.click("label.yt-video-opt:has-text('Download the video')")
        end = time.time() + 10
        while self.prefs().get("sedjem-youtube-video") != "download" and time.time() < end:
            time.sleep(0.05)
        self.assertEqual(self.prefs().get("sedjem-youtube-video"), "download")

    def texts(self, selector):
        return self.page.eval_on_selector_all(selector, "els => els.map(e => e.innerText.trim())")


if __name__ == "__main__":
    unittest.main()
