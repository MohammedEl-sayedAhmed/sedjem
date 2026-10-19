"""A stand-in for yt-dlp in the YouTube tests (tests/test_youtube.py, tests/test_ui_youtube.py): made-up
videos and playlists with the info yt-dlp gives, and a download that writes small media files made here with
PyAV (H.264 video, AAC sound), so nothing is fetched from the network and no media is kept in the repository.

  fake = FakeYouTube()
  fake.video("vid00000001", "Lecture one", seconds=3, captions={"en": VTT})
  fake.playlist("PLcourse", "Course", ["vid00000001", ...])
  with mock.patch.object(youtube, "_ydl", fake.ydl): ...
"""
import io
import tempfile
import threading
import time
import urllib.parse
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

_media = {}
_media_lock = threading.Lock()


def media(kind, seconds=3):
    """The bytes of a small file made with PyAV: "video" (H.264 only, MP4), "audio" (AAC only, M4A), "both"
    (an MP4 with both) or "opus" (Opus sound only, WebM: no H.264 version). Made once per kind and length."""
    with _media_lock:
        if (kind, seconds) not in _media:
            with tempfile.TemporaryDirectory(prefix="sedjem-media-") as tmp:
                path = Path(tmp) / ("x.webm" if kind == "opus" else "x.mp4")
                write_media(path, kind, seconds)
                _media[kind, seconds] = path.read_bytes()
        return _media[kind, seconds]


def write_media(path, kind, seconds=3, width=160, height=90, fps=10, rate=16000):
    with av.open(str(path), "w", format="webm" if kind == "opus" else "mp4") as c:
        v = a = None
        if kind in ("video", "both"):
            v = c.add_stream("libx264", rate=fps)
            v.width, v.height, v.pix_fmt = width, height, "yuv420p"
        if kind in ("audio", "both", "opus"):
            a = c.add_stream("libopus" if kind == "opus" else "aac", rate=48000 if kind == "opus" else rate)
            a.layout = "mono"
        if v:
            for i in range(int(seconds * fps)):
                frame = av.VideoFrame.from_ndarray(np.full((height, width, 3), (i * 23) % 255, dtype=np.uint8), format="rgb24")
                for p in v.encode(frame):
                    c.mux(p)
            for p in v.encode(None):
                c.mux(p)
        if a:
            sr = a.rate
            t = np.arange(int(seconds * sr)) / sr
            samples = (0.2 * np.sin(2 * np.pi * 330 * t)).astype(np.float32)
            step = a.codec_context.frame_size or 1024
            for i in range(0, len(samples), step):
                frame = av.AudioFrame.from_ndarray(samples[None, i:i + step], format="fltp", layout="mono")
                frame.sample_rate, frame.pts, frame.time_base = sr, i, Fraction(1, sr)
                for p in a.encode(frame):
                    c.mux(p)
            for p in a.encode(None):
                c.mux(p)


def vtt(*cues):
    """A WebVTT file from (start, end, text) cues."""
    stamp = lambda t: f"{int(t // 3600):02d}:{int(t // 60 % 60):02d}:{t % 60:06.3f}"
    return "WEBVTT\nKind: captions\nLanguage: en\n\n" + "\n".join(f"{stamp(a)} --> {stamp(b)}\n{x}\n" for a, b, x in cues)


def formats(vid, h264=True, size=40000):
    """A YouTube-like list of formats: sound in AAC (and its "drc" copy) and Opus, H.264 video at 720p30,
    720p60 and 1080p, VP9 at 720p, and the 360p MP4 with both. Without h264: only VP9 and Opus."""
    f = lambda fid, ext, vcodec, acodec, **kw: {"format_id": fid, "ext": ext, "vcodec": vcodec, "acodec": acodec,
                                                 "protocol": "https", "url": f"https://fake.test/{vid}/{fid}",
                                                 "filesize": size, **kw}
    out = [f("251", "webm", "none", "opus", abr=130), f("247", "webm", "vp9", "none", height=720, width=1280, fps=30)]
    if h264:
        out += [f("140-drc", "m4a", "none", "mp4a.40.2", abr=129), f("140", "m4a", "none", "mp4a.40.2", abr=129),
                f("139", "m4a", "none", "mp4a.40.5", abr=48),
                f("136", "mp4", "avc1.4d401f", "none", height=720, width=1280, fps=30, tbr=1500),
                f("298", "mp4", "avc1.4d4020", "none", height=720, width=1280, fps=60, tbr=2500),
                f("137", "mp4", "avc1.640028", "none", height=1080, width=1920, fps=30, tbr=4000),
                f("18", "mp4", "avc1.42001E", "mp4a.40.2", height=360, width=640, fps=30, tbr=600),
                f("232", "mp4", "avc1.64001F", "none", height=720, width=1280, fps=30, tbr=1400, protocol="m3u8_native")]
    return out


class FakeYouTube:
    """Videos and playlists by id, and the yt-dlp stand-in that serves them (ydl)."""

    def __init__(self):
        self.videos, self.playlists, self.subs = {}, {}, {}
        self.fail = {}             # video id -> the error yt-dlp would raise for it
        self.hold = threading.Event()
        self.hold.set()            # clear it to keep downloads running until it is set again
        self.downloads = []        # (video id, format id), in the order they were downloaded
        self.started = threading.Event()

    def video(self, vid, title, seconds=3, captions=None, auto=None, language=None, h264=True, live="not_live"):
        """captions: {language code: WebVTT text} made by people; auto: YouTube's automatic ones."""
        subs = {}
        for code, text in (captions or {}).items():
            url = f"https://fake.test/{vid}/subs/{code}.vtt"
            self.subs[url] = text
            subs[code] = [{"ext": "json3", "url": url + ".json"}, {"ext": "vtt", "url": url, "name": code.upper()}]
        self.videos[vid] = {"id": vid, "title": title, "duration": seconds, "language": language, "uploader": "Teacher",
                            "live_status": live, "formats": formats(vid, h264), "subtitles": subs,
                            "automatic_captions": {c: [{"ext": "vtt", "url": f"https://fake.test/{vid}/auto/{c}"}]
                                                   for c in (auto or [])},
                            "seconds": seconds}
        return vid

    def playlist(self, pid, title, ids, gone=()):
        """gone: titles of entries YouTube lists without a video ("[Private video]")."""
        self.playlists[pid] = {"id": pid, "title": title, "ids": list(ids), "gone": list(gone)}
        return pid

    def ydl(self, cfg, **extra):  # what app.youtube._ydl returns
        from app import youtube
        return FakeYDL(self, {"logger": youtube._Log(), **extra})


class FakeYDL:
    def __init__(self, fake, params):
        self.fake, self.params, self.hooks = fake, params, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=False, process=True):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        if "list" in q and q["list"][0] in self.fake.playlists:
            p = self.fake.playlists[q["list"][0]]
            entries = [{"_type": "url", "ie_key": "Youtube", "id": vid, "url": f"https://www.youtube.com/watch?v={vid}",
                        "title": self.fake.videos[vid]["title"], "duration": self.fake.videos[vid]["duration"]}
                       for vid in p["ids"]]
            entries += [{"_type": "url", "ie_key": "Youtube", "id": f"gone{i:07d}", "title": t, "duration": None,
                         "url": f"https://www.youtube.com/watch?v=gone{i:07d}"} for i, t in enumerate(p["gone"])]
            return {"_type": "playlist", "id": p["id"], "title": p["title"], "uploader": "Teacher", "entries": iter(entries)}
        if "/@" in url:
            return {"_type": "playlist", "id": "UCchannel", "title": "A channel",
                    "entries": iter([{"_type": "url", "ie_key": "YoutubeTab", "id": "UCchannel", "title": "Videos"}])}
        vid = q.get("v", [urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]])[0]
        if vid in self.fake.fail:
            raise Exception(self.fake.fail[vid])
        if vid not in self.fake.videos:
            raise Exception(f"ERROR: [youtube] {vid}: Video unavailable")
        info = {k: v for k, v in self.fake.videos[vid].items() if k != "seconds"}
        return {**info, "formats": [dict(f) for f in info["formats"]]}

    def add_progress_hook(self, fn):
        self.hooks.append(fn)

    def dl(self, name, fmt):
        vid, seconds = fmt["id"], self.fake.videos[fmt["id"]]["seconds"]
        kind = "opus" if fmt["acodec"] == "opus" else "both" if fmt["acodec"] != "none" and fmt["vcodec"] != "none" \
            else "audio" if fmt["vcodec"] == "none" else "video"
        data = media(kind, seconds)
        self.fake.downloads.append((vid, fmt["format_id"]))
        self.fake.started.set()
        for hook in self.hooks:
            hook({"status": "downloading", "downloaded_bytes": len(data) // 2, "total_bytes": len(data)})
        while not self.fake.hold.is_set():  # a download that takes a while (cancelling it is tested)
            for hook in self.hooks:
                hook({"status": "downloading", "downloaded_bytes": len(data) // 2, "total_bytes": len(data)})
            time.sleep(0.05)
        Path(name).write_bytes(data)
        for hook in self.hooks:
            hook({"status": "finished", "downloaded_bytes": len(data), "total_bytes": len(data)})
        return True

    def urlopen(self, url):
        return io.BytesIO(self.fake.subs[url].encode())
