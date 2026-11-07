"""Tests for the video on the job page (app/video.py and its use in app/jobs.py and app/server.py): which
recordings get one, copied or converted, what is skipped and why, the endpoint with byte ranges, reruns,
deleting, and a video.mp4 that was downloaded with the recording. The recordings are made in code
(tests/media.py); the hosted service is a local stand-in.
Run: .venv/bin/python -m unittest discover -s tests -p "test_video.py" -v
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import quote

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import av  # noqa: E402

from app import engines, video  # noqa: E402
from app.config import Config  # noqa: E402
from app.server import make_server  # noqa: E402
from media import make_clip  # noqa: E402


def streams(path):
    """[(type, codec, width, height)] of a file, its rotation and its length in seconds."""
    with av.open(str(path)) as c:
        found = [(s.type, s.codec_context.name, getattr(s.codec_context, "width", None),
                  getattr(s.codec_context, "height", None)) for s in c.streams]
        turn = next((f.rotation for f in c.decode(video=0)), None) if c.streams.video else None
        return found, turn, c.duration / av.time_base


def frames(path):
    with av.open(str(path)) as c:
        return sum(1 for p in c.demux(video=0) if p.size)


def faststart(path):
    data = Path(path).read_bytes()
    return 0 <= data.find(b"moov") < data.find(b"mdat")


class MakeVideo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="sedjem-video-"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def make(self, src, **kw):
        dst = self.tmp / f"{src.name}.out.mp4"
        return video.make(src, dst, kw.get("progress", lambda t: None), kw.get("cancelled", lambda: False)), dst

    def test_an_h264_encoder_is_found(self):
        self.assertIn(video.encoder(), video.ENCODERS, "PyAV's FFmpeg should have libx264")

    def test_audio_only_has_no_video(self):
        wav = self.tmp / "call.wav"
        sf.write(wav, (0.1 * np.sin(np.arange(16000) / 5)).astype(np.float32), 16000)
        self.assertFalse(video.has_picture(wav))
        self.assertIsNone(video.plan(wav))
        shown, dst = self.make(wav)
        self.assertIsNone(shown)
        self.assertFalse(dst.exists())
        self.assertFalse(video.has_picture(self.tmp / "missing.mp4"))

    def test_an_album_cover_is_not_a_picture(self):
        mp3 = self.tmp / "podcast.mp3"
        with av.open(str(mp3), "w") as c:
            a = c.add_stream("libmp3lame", rate=44100)
            a.layout = "mono"
            cover = c.add_stream("mjpeg", rate=1)
            cover.width, cover.height, cover.pix_fmt = 64, 64, "yuvj420p"
            cover.disposition = av.stream.Disposition.attached_pic
            frame = av.VideoFrame.from_ndarray(np.zeros((64, 64, 3), np.uint8), format="rgb24").reformat(format="yuvj420p")
            frame.pts = 0
            c.mux(cover.encode(frame))
            c.mux(cover.encode(None))
            sound = av.AudioFrame.from_ndarray(np.zeros((1, 44100), np.float32), format="fltp", layout="mono")
            sound.sample_rate, sound.pts = 44100, 0
            c.mux(a.encode(sound))
            c.mux(a.encode(None))
        self.assertEqual(streams(mp3)[0][1][:2], ("video", "mjpeg"))
        self.assertFalse(video.has_picture(mp3))
        self.assertIsNone(video.plan(mp3))

    def test_a_browser_ready_mp4_is_copied_as_it_is(self):
        src = make_clip(self.tmp / "zoom.mp4", 320, 180, seconds=2)
        p = video.plan(src)
        self.assertTrue(p["copy_video"] and p["copy_audio"])
        shown, dst = self.make(src)
        self.assertEqual(shown, {"width": 320, "height": 180})
        self.assertEqual(video.size(dst), shown)
        self.assertTrue(faststart(dst), "the index is at the start, so the browser can seek at once")
        with av.open(str(src)) as a, av.open(str(dst)) as b:
            self.assertEqual([bytes(x) for x in a.demux(video=0) if x.size], [bytes(x) for x in b.demux(video=0) if x.size],
                             "the same H.264 packets")
        found, _, seconds = streams(dst)
        self.assertEqual([x[:2] for x in found], [("video", "h264"), ("audio", "aac")])
        self.assertAlmostEqual(seconds, 2.0, delta=0.1)

    def test_h264_in_matroska_keeps_the_picture_and_converts_the_sound(self):
        src = make_clip(self.tmp / "obs.mkv", 320, 180, acodec="libopus")
        p = video.plan(src)
        self.assertEqual((p["copy_video"], p["copy_audio"]), (True, False))
        shown, dst = self.make(src)
        self.assertEqual(shown, {"width": 320, "height": 180})
        found, _, seconds = streams(dst)
        self.assertEqual([x[:2] for x in found], [("video", "h264"), ("audio", "aac")])
        self.assertAlmostEqual(seconds, 2.0, delta=0.1)

    def test_other_codecs_are_converted_to_h264_and_aac(self):
        src = make_clip(self.tmp / "call.webm", 321, 241, vcodec="libvpx-vp9", acodec="libopus")
        self.assertFalse(video.plan(src)["copy_video"])
        progress = []
        shown, dst = self.make(src, progress=progress.append)
        self.assertEqual(shown, {"width": 320, "height": 240}, "an even size, as H.264 in 4:2:0 needs")
        found, _, seconds = streams(dst)
        self.assertEqual(found, [("video", "h264", 320, 240), ("audio", "aac", None, None)])
        with av.open(str(dst)) as c:
            self.assertEqual(c.streams.video[0].codec_context.pix_fmt, "yuv420p")
        self.assertTrue(faststart(dst))
        self.assertEqual(frames(dst), 20)
        self.assertAlmostEqual(seconds, 2.0, delta=0.1)
        self.assertTrue(progress and all(0 <= t <= 2.1 for t in progress))

    def test_mpeg4_and_mp3_in_avi(self):
        src = make_clip(self.tmp / "old.avi", 320, 180, vcodec="mpeg4", acodec="libmp3lame")
        shown, dst = self.make(src)
        self.assertEqual(shown, {"width": 320, "height": 180})
        found, _, seconds = streams(dst)
        self.assertEqual([x[:2] for x in found], [("video", "h264"), ("audio", "aac")])
        self.assertAlmostEqual(seconds, 2.0, delta=0.15)

    def test_a_taller_video_is_made_720_high(self):
        src = make_clip(self.tmp / "phone.mov", 960, 1080, seconds=1)
        p = video.plan(src)
        self.assertFalse(p["copy_video"])
        shown, dst = self.make(src)
        self.assertEqual(shown, {"width": 640, "height": 720})
        self.assertEqual(streams(dst)[0][0], ("video", "h264", 640, 720))

    def test_more_than_30_frames_a_second_are_thinned(self):
        src = make_clip(self.tmp / "screen.mp4", 320, 180, seconds=2, fps=60)
        self.assertFalse(video.plan(src)["copy_video"])
        shown, dst = self.make(src)
        self.assertEqual(frames(dst), 60)
        self.assertAlmostEqual(streams(dst)[2], 2.0, delta=0.1)

    def test_a_turned_phone_video_stays_upright(self):
        src = make_clip(self.tmp / "portrait.mp4", 1280, 720, seconds=1, rotate=90)
        shown, dst = self.make(src)
        self.assertEqual(shown, {"width": 404, "height": 720}, "720 high as it is shown")
        found, turn, _ = streams(dst)
        self.assertEqual((found[0], turn), (("video", "h264", 720, 404), 90))
        self.assertEqual(video.size(dst), shown)

    def test_without_an_h264_encoder_only_copying_works(self):
        webm = make_clip(self.tmp / "noenc.webm", 160, 120, vcodec="libvpx-vp9", acodec="libopus", seconds=1)
        mp4 = make_clip(self.tmp / "noenc.mp4", 160, 120, seconds=1)
        with mock.patch.object(video, "_found", [""]):
            with self.assertRaises(video.Skipped) as e:
                self.make(webm)
            self.assertIn("no H.264 encoder", str(e.exception))
            self.assertEqual(self.make(mp4)[0], {"width": 160, "height": 120})
        self.assertFalse((self.tmp / "noenc.webm.out.mp4").exists())

    def test_without_the_disk_space_it_is_skipped(self):
        src = make_clip(self.tmp / "full.mp4", 160, 120, seconds=1)
        free = mock.Mock(free=50_000_000)
        with mock.patch.object(video.shutil, "disk_usage", return_value=free):
            with self.assertRaises(video.Skipped) as e:
                self.make(src)
        self.assertIn("not enough free disk space", str(e.exception))

    def test_cancelled_leaves_nothing_behind(self):
        src = make_clip(self.tmp / "stop.webm", 160, 120, vcodec="libvpx-vp9", acodec="libopus", seconds=1)
        with self.assertRaises(engines.Cancelled):
            self.make(src, cancelled=lambda: True)
        self.assertEqual(list(self.tmp.glob("stop.webm.out*")), [])


# ---------------------------------------------------------------------------------------------
# Through the app
# ---------------------------------------------------------------------------------------------

REPLY = {"language_code": "ara", "text": "تمام test", "transcription_id": "tr_1", "words": [
    {"text": "تمام", "start": 0.1, "end": 0.5, "type": "word", "speaker_id": "speaker_0"},
    {"text": "test", "start": 0.9, "end": 1.2, "type": "word", "speaker_id": "speaker_1"}]}


class FakeElevenLabs(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.reply(REPLY)

    def do_DELETE(self):
        self.reply({})


class VideoJobs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="sedjem-videojobs-"))
        cls.mock = ThreadingHTTPServer(("127.0.0.1", 0), FakeElevenLabs)
        threading.Thread(target=cls.mock.serve_forever, daemon=True).start()
        storage = cls.tmp / "app_data"
        storage.mkdir()
        (storage / "config.toml").write_text(
            f'[[models]]\nid = "elevenlabs"\nbase_url = "http://127.0.0.1:{cls.mock.server_address[1]}"\n')
        cls.cfg = Config(storage=storage, home=cls.tmp)
        cls.cfg.save_key("elevenlabs", "test-key")
        with mock.patch.object(engines, "gpu_info", return_value={"devices": [], "cuda_devices": 0, "cuda_libs": None}):
            cls.server, cls.app = make_server(cls.cfg, port=0)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.mp4 = make_clip(cls.tmp / "Weekly sync.mp4", 320, 180, seconds=2)
        cls.webm = make_clip(cls.tmp / "Design review.webm", 240, 136, vcodec="libvpx-vp9", acodec="libopus", seconds=2)
        cls.wav = cls.tmp / "Call.wav"
        sf.write(cls.wav, (0.2 * np.sin(2 * np.pi * 300 * np.arange(32000) / 16000)).astype(np.float32), 16000)

    @classmethod
    def tearDownClass(cls):
        cls.app.runner.shutdown()
        cls.server.shutdown()
        cls.server.server_close()
        cls.mock.shutdown()
        cls.mock.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def call(self, method, path, body=None, raw=None, headers=None):
        h = {"X-Sedjem": "1", **(headers or {})}
        data = raw
        if body is not None:
            data, h["Content-Type"] = json.dumps(body).encode(), "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                payload = r.read()
                return r.status, (json.loads(payload) if "json" in r.headers.get("Content-Type", "") else payload), r.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}"), e.headers

    def upload(self, path, ctype="video/mp4"):
        status, job, _ = self.call("POST", f"/api/jobs?model=elevenlabs&speakers=auto&language=ar&name={quote(path.name)}"
                                          "&confirm_upload=1", raw=path.read_bytes(), headers={"Content-Type": ctype})
        self.assertEqual(status, 201, job)
        return job["id"]

    def wait(self, jid, video_too=True, timeout=60):
        """The job once its transcript is done, and (video_too) its video made or skipped."""
        end = time.time() + timeout
        while time.time() < end:
            _, r, _ = self.call("GET", f"/api/jobs/{jid}")
            j = r["job"]
            if j["status"] in ("done", "failed", "cancelled") and not (video_too and j.get("video_prep")):
                return r
            time.sleep(0.1)
        self.fail(f"job {jid} still {j['status']}, video {j.get('video_prep')}")

    def folder(self, jid):
        return self.cfg.storage / "jobs" / jid

    def test_an_uploaded_video_gets_its_video_and_ranges(self):
        jid = self.upload(self.mp4)
        r = self.wait(jid)
        j = r["job"]
        self.assertEqual(j["status"], "done", r)
        self.assertEqual(j["video"], {"width": 320, "height": 180})
        self.assertIsNone(j.get("video_skipped"))
        self.assertTrue(r["has_video"] and r["has_audio"])
        self.assertEqual(j["source"]["video"], "h264")
        self.assertFalse(list(self.folder(jid).glob("source.*")), "the upload is deleted once the video is made")
        movie = (self.folder(jid) / "video.mp4").read_bytes()
        status, body, headers = self.call("GET", f"/api/jobs/{jid}/video")
        self.assertEqual((status, headers["Content-Type"], headers["Accept-Ranges"]), (200, "video/mp4", "bytes"))
        self.assertEqual(body, movie)
        status, body, headers = self.call("GET", f"/api/jobs/{jid}/video", headers={"Range": "bytes=100-199"})
        self.assertEqual((status, body, headers["Content-Range"]), (206, movie[100:200], f"bytes 100-199/{len(movie)}"))
        status, body, headers = self.call("GET", f"/api/jobs/{jid}/video", headers={"Range": "bytes=-50"})
        self.assertEqual((status, body), (206, movie[-50:]))
        status, body, headers = self.call("GET", f"/api/jobs/{jid}/video", headers={"Range": f"bytes={len(movie)}-"})
        self.assertEqual((status, headers["Content-Range"]), (416, f"bytes */{len(movie)}"))
        status, body, _ = self.call("GET", f"/api/jobs/{jid}/audio", headers={"Range": "bytes=0-3"})
        self.assertEqual((status, body), (206, b"fLaC"), "the audio is served by the same code")
        rows = {x["label"]: x["value"] for g in r["detail_groups"] for x in g["rows"]}
        self.assertEqual(rows["Video"], "H.264, on the job page at 320×180")

    def test_an_audio_only_job_is_as_before(self):
        jid = self.upload(self.wav, "audio/wav")
        r = self.wait(jid)
        self.assertEqual(r["job"]["status"], "done", r)
        self.assertNotIn("video", r["job"])
        self.assertNotIn("video_prep", r["job"])
        self.assertFalse(r["has_video"])
        self.assertFalse((self.folder(jid) / "video.mp4").exists())
        self.assertFalse(list(self.folder(jid).glob("source.*")))
        self.assertEqual(self.call("GET", f"/api/jobs/{jid}/video")[0], 404)

    def test_a_file_used_in_place_is_converted_and_left_alone(self):
        before = self.webm.stat()
        status, job, _ = self.call("POST", "/api/jobs", {"path": str(self.webm), "model": "elevenlabs", "confirm_upload": True})
        self.assertEqual(status, 201, job)
        r = self.wait(job["id"])
        self.assertEqual(r["job"]["video"], {"width": 240, "height": 136})
        found, _, _ = streams(self.folder(job["id"]) / "video.mp4")
        self.assertEqual([x[:2] for x in found], [("video", "h264"), ("audio", "aac")])
        after = self.webm.stat()
        self.assertEqual((after.st_size, after.st_mtime), (before.st_size, before.st_mtime))

    def test_a_rerun_shares_the_video(self):
        jid = self.upload(self.mp4)
        self.wait(jid)
        status, new, _ = self.call("POST", f"/api/jobs/{jid}/rerun", {"model": "elevenlabs", "confirm_upload": True})
        self.assertEqual(status, 201, new)
        r = self.wait(new["id"])
        self.assertEqual(r["job"]["video"], {"width": 320, "height": 180})
        self.assertTrue(r["has_video"])
        a, b = self.folder(jid) / "video.mp4", self.folder(new["id"]) / "video.mp4"
        self.assertEqual(a.stat().st_ino, b.stat().st_ino, "the same file on disk")

    def test_a_rerun_made_while_the_video_is_made_gets_it_after(self):
        go, real = threading.Event(), video.make

        def slow(*a, **kw):
            go.wait(20)
            return real(*a, **kw)
        with mock.patch.object(video, "make", side_effect=slow):
            jid = self.upload(self.mp4)
            r = self.wait(jid, video_too=False)
            self.assertTrue(r["job"]["video_prep"], "the transcript is done before the video")
            self.assertFalse(r["has_video"])
            self.assertTrue(list(self.folder(jid).glob("source.*")), "the upload stays until the video is made")
            _, new, _ = self.call("POST", f"/api/jobs/{jid}/rerun", {"model": "elevenlabs", "confirm_upload": True})
            self.assertNotIn("video", self.wait(new["id"], video_too=False)["job"])
            go.set()
            self.assertEqual(self.wait(jid)["job"]["video"], {"width": 320, "height": 180})
        r = self.wait(new["id"])
        self.assertEqual(r["job"]["video"], {"width": 320, "height": 180})
        self.assertEqual((self.folder(jid) / "video.mp4").stat().st_ino, (self.folder(new["id"]) / "video.mp4").stat().st_ino)

    def test_deleting_stops_the_video(self):
        seen = threading.Event()

        def endless(source, dest, on_progress, cancelled):
            on_progress(0.5)
            end = time.time() + 20
            while not cancelled() and time.time() < end:
                time.sleep(0.02)
            seen.set()
            raise engines.Cancelled()
        with mock.patch.object(video, "make", side_effect=endless):
            jid = self.upload(self.mp4)
            self.wait(jid, video_too=False)
            end = time.time() + 10
            while self.app.runner.making != jid and time.time() < end:
                time.sleep(0.02)
            t0 = time.time()
            status, _, _ = self.call("DELETE", f"/api/jobs/{jid}")
            self.assertEqual(status, 200)
            self.assertLess(time.time() - t0, 5)
        self.assertTrue(seen.is_set(), "the video was told to stop")
        self.assertFalse(self.folder(jid).exists())
        self.assertIsNone(self.app.runner.making)

    def test_without_an_encoder_the_job_goes_on_and_says_why(self):
        with mock.patch.object(video, "_found", [""]):
            jid = self.upload(self.webm, "video/webm")
            r = self.wait(jid)
        j = r["job"]
        self.assertEqual(j["status"], "done", r)
        self.assertIsNone(j["video"])
        self.assertIn("no H.264 encoder", j["video_skipped"])
        self.assertFalse(r["has_video"])
        self.assertIn("No video on the job page: this FFmpeg build has no H.264 encoder",
                      (self.folder(jid) / "log.txt").read_text(encoding="utf-8"))
        self.assertFalse(list(self.folder(jid).glob("source.*")), "the upload is deleted all the same")
        rows = {x["label"]: x["value"] for g in r["detail_groups"] for x in g["rows"]}
        self.assertTrue(rows["Video"].startswith("VP9, not on the job page: this FFmpeg build has no H.264 encoder"))

    def test_a_downloaded_video_mp4_is_used_as_it_is(self):
        """A job whose folder already has video.mp4 (a download): the audio comes from it, the file stays."""
        runner = self.app.runner
        job = runner.new_job(title="Lecture", source_name="lecture.mp4", model_id="elevenlabs",
                             options={"speakers": "auto", "language": "ar", "prompt": ""})
        movie = make_clip(self.folder(job["id"]) / "video.mp4", 320, 180, seconds=2)
        before = movie.stat()
        runner.submit(job["id"])
        r = self.wait(job["id"])
        self.assertEqual(r["job"]["status"], "done", r)
        self.assertEqual(r["job"]["video"], {"width": 320, "height": 180}, "filled in from the file")
        self.assertAlmostEqual(r["job"]["audio_s"], 2.0, delta=0.1)
        after = movie.stat()
        self.assertEqual((after.st_ino, after.st_size, after.st_mtime_ns), (before.st_ino, before.st_size, before.st_mtime_ns))
        self.assertTrue(r["has_video"])

    def test_a_video_left_unfinished_is_made_after_a_restart(self):
        runner = self.app.runner
        job = runner.new_job(title="Restarted", source_name=self.webm.name, model_id="elevenlabs",
                             options={"speakers": "auto", "language": "ar", "prompt": ""}, source_path=str(self.webm))
        folder = self.folder(job["id"])
        sf.write(folder / "audio.flac", np.zeros(16000, np.float32), 16000)
        self.app.store.update(job["id"], status="done", stage=None, video_prep={"done": 0.5, "total": 2.0})
        runner.recover()
        r = self.wait(job["id"])
        self.assertEqual(r["job"]["video"], {"width": 240, "height": 136})
        self.assertIsNone(r["job"].get("video_prep"))


if __name__ == "__main__":
    unittest.main()
