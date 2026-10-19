"""The video on the job page: video.mp4 in the job folder, made from a recording that has a picture.

The file is H.264 video and AAC audio in MP4, at most 720 pixels high, with its index (the moov atom)
at the start so the browser plays and seeks before it has read all of it. A recording that already is
that is copied as it is (remuxed: seconds even for an hour); anything else is re-encoded with the FFmpeg
libraries that PyAV ships (libx264). Without an H.264 encoder, or without the disk space, the job goes
on without the video and says why.
"""
import shutil
import time
from fractions import Fraction
from pathlib import Path

import sysinfo

from . import engines
from .config import replace_file

NAME = "video.mp4"
MAX_HEIGHT = 720
MAX_FPS = 30
# H.264 encoders to try, in order: the software ones PyAV's wheels have, then the system's own
ENCODERS = ("libx264", "libopenh264", "h264_mf", "h264_videotoolbox")
COPY_FORMATS = ("mov", "mp4", "matroska", "webm")  # containers whose H.264 and AAC go into MP4 unchanged
BROWSER_PIX = ("yuv420p", "yuvj420p")  # what browsers decode in H.264 (not 4:2:2, 4:4:4 or 10 bit)


class Skipped(Exception):
    """The job goes on without a video; the message says why."""


def picture(container):
    """The video stream of an open recording, or None (an album cover in an mp3 or m4a doesn't count)."""
    return next((s for s in container.streams.video if not sysinfo._cover_art(s)), None)


def has_picture(path):
    import av
    try:
        with av.open(str(path)) as c:
            return picture(c) is not None
    except Exception:  # not a media file: the audio conversion reports it
        return False


def size(path):
    """{"width", "height"} of a video file as it is shown (turned upright), or None."""
    import av
    try:
        with av.open(str(path)) as c:
            v = picture(c)
            if v is None:
                return None
            w, h = v.codec_context.width, v.codec_context.height
            if abs(rotation(c, v)) == 90:
                w, h = h, w
            return {"width": w, "height": h}
    except Exception:
        return None


def rotation(container, stream):
    """How far a phone video is turned when shown (its display matrix), in degrees: 0, ±90 or 180."""
    try:
        for frame in container.decode(stream):
            return round((frame.rotation or 0) / 90) * 90
    except Exception:
        pass
    return 0


_found = []  # the H.264 encoder that works here, once looked for ("" if none)


def encoder():
    """The first H.264 encoder that opens and encodes a frame here, or None."""
    if not _found:
        _found.append(next((n for n in ENCODERS if _works(n)), ""))
    return _found[0] or None


def _works(name):
    import av
    try:
        cc = av.CodecContext.create(name, "w")
        cc.width, cc.height, cc.pix_fmt, cc.time_base = 64, 64, "yuv420p", Fraction(1, 30)
        frame = av.VideoFrame(64, 64, "yuv420p")
        frame.pts = 0
        cc.encode(frame)
        cc.encode(None)
        return True
    except Exception:  # not in this FFmpeg build, or no such hardware
        return False


def even(x):
    return max(2, int(x) // 2 * 2)


def plan(path):
    """What making the video takes, or None when the recording has no picture: {"copy_video",
    "copy_audio", "width", "height" (as stored), "turn" (the display rotation), "fps", "seconds",
    "bytes" (the recording's size)}."""
    import av
    with av.open(str(path)) as c:
        v = picture(c)
        if v is None:
            return None
        a = next(iter(c.streams.audio), None)
        cc = v.codec_context
        turn = rotation(c, v)
        w, h = cc.width, cc.height
        shown_h = w if abs(turn) == 90 else h
        fps = float(v.average_rate or v.guessed_rate or 0) or None
        copy_formats = set(c.format.name.split(","))
        copy_video = (cc.name == "h264" and cc.pix_fmt in BROWSER_PIX and shown_h <= MAX_HEIGHT
                      and bool(copy_formats & set(COPY_FORMATS)) and (fps or 0) <= MAX_FPS + 1)
        if not copy_video and shown_h > MAX_HEIGHT:
            scale = MAX_HEIGHT / shown_h
            w, h = even(w * scale), even(h * scale)
        elif not copy_video:
            w, h = even(w), even(h)
        copy_audio = a is not None and a.codec_context.name == "aac" and bool(copy_formats & set(COPY_FORMATS))
        seconds = c.duration / av.time_base if c.duration else None
        return {"copy_video": copy_video, "copy_audio": copy_audio, "width": w, "height": h, "turn": turn,
                "fps": fps, "seconds": seconds, "bytes": Path(path).stat().st_size}


def make(source, dest, on_progress, cancelled):
    """Write dest (video.mp4) from a recording. Returns {"width", "height"} as shown, or None when the
    recording has no picture. Raises Skipped when it can't be made here, engines.Cancelled if cancelled."""
    p = plan(source)
    if p is None:
        return None
    enc = None if p["copy_video"] else encoder()
    if not p["copy_video"] and not enc:
        raise Skipped("this FFmpeg build has no H.264 encoder to convert the video with")
    need = (p["bytes"] or 0) if p["copy_video"] else min(p["bytes"] or 0, (p["seconds"] or 0) * 2.5e6 / 8)
    free = shutil.disk_usage(dest.parent).free
    if free < need + 1e9:
        raise Skipped(f"not enough free disk space for the video (about {need / 1e9 + 1:.1f} GB needed, "
                      f"{free / 1e9:.1f} GB free)")
    tmp = dest.with_name(dest.stem + ".tmp.mp4")
    try:
        _write(source, tmp, p, enc, on_progress, cancelled)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    replace_file(tmp, dest)
    shown = (p["height"], p["width"]) if abs(p["turn"]) == 90 else (p["width"], p["height"])
    return {"width": shown[0], "height": shown[1]}


class Scaler:
    """Frames to the new size in yuv420p, through FFmpeg's scale filter (several times faster here than
    VideoFrame.reformat). It is set up from the first frame, and again if the frames change size (a
    recording that switches to a shared screen)."""

    def __init__(self, time_base, width, height):
        self.time_base, self.size, self.graph, self.shape = time_base, (width, height), None, None

    def setup(self, frame):
        import av
        self.graph, self.shape = av.filter.Graph(), (frame.width, frame.height, frame.format.name)
        nodes = [self.graph.add_buffer(width=frame.width, height=frame.height, format=frame.format.name,
                                       time_base=self.time_base),
                 self.graph.add("scale", f"{self.size[0]}:{self.size[1]}:flags=bicubic"),
                 self.graph.add("format", "yuv420p"), self.graph.add("buffersink")]
        for a, b in zip(nodes, nodes[1:]):
            a.link_to(b)
        self.graph.configure()

    def __call__(self, frame):
        import av
        if (frame.width, frame.height, frame.format.name) != self.shape:
            self.setup(frame)
        self.graph.push(frame)
        while True:
            try:
                yield self.graph.pull()
            except (av.error.BlockingIOError, av.error.EOFError):
                return


def _write(source, tmp, p, enc, on_progress, cancelled):
    import av
    with av.open(str(source)) as inp, av.open(str(tmp), "w", format="mp4",
                                               container_options={"movflags": "+faststart"}) as out:
        v_in = picture(inp)
        a_in = next(iter(inp.streams.audio), None)
        start = inp.start_time / av.time_base if inp.start_time else 0.0  # the file's timeline starts at 0
        if p["copy_video"]:
            v_out = out.add_stream_from_template(v_in)
        else:
            v_in.thread_type = "AUTO"  # decode on all cores
            v_out = out.add_stream(enc, rate=Fraction(min(p["fps"] or MAX_FPS, MAX_FPS)).limit_denominator(1001))
            v_out.width, v_out.height, v_out.pix_fmt = p["width"], p["height"], "yuv420p"
            v_out.codec_context.time_base = v_in.time_base  # every frame keeps its own time
            v_out.codec_context.gop_size = 2 * MAX_FPS  # a key frame every 2 s or so: quick to seek
            if enc == "libx264":
                v_out.options = {"preset": "veryfast", "crf": "24", "profile": "high"}
            else:
                v_out.bit_rate = 2_000_000
            if p["turn"]:
                v_out.set_display_rotation(p["turn"])  # shown upright, as the original
            scaler = Scaler(v_in.time_base, p["width"], p["height"])
        a_out = resampler = None
        if a_in is not None and p["copy_audio"]:
            a_out = out.add_stream_from_template(a_in)
        elif a_in is not None:
            rate = a_in.codec_context.sample_rate or 48000
            rate = rate if 8000 <= rate <= 48000 else 48000
            layout = "stereo" if len(a_in.codec_context.layout.channels) > 1 else "mono"
            a_out = out.add_stream("aac", rate=rate)
            a_out.layout, a_out.bit_rate = layout, 128_000 if layout == "stereo" else 96_000
            # AAC takes 1024 samples at a time; they are counted here rather than taken from the source, whose
            # packets may have times the encoder can't keep in order (an MP3 in an AVI file)
            resampler = av.AudioResampler(format="fltp", layout=layout, rate=rate, frame_size=1024)
        samples = None

        def sound(frame):  # a decoded piece of the sound, or None at the end, to AAC
            nonlocal samples
            if samples is None:
                samples = round(max(0.0, (frame.time or 0.0) - start) * rate) if frame else 0
            for f in resampler.resample(frame):
                f.pts, f.time_base = samples, Fraction(1, rate)
                samples += f.samples
                out.mux(a_out.encode(f))

        streams = [s for s in (v_in, a_in if a_out is not None else None) if s is not None]
        gap, last_t, last_report = 1 / MAX_FPS - 0.004, None, 0.0
        for packet in inp.demux(streams):
            if cancelled():
                raise engines.Cancelled()
            is_video = packet.stream is v_in
            dst = v_out if is_video else a_out
            copy = p["copy_video"] if is_video else p["copy_audio"]
            if copy:
                if packet.dts is None:  # the demuxer's last, empty packet
                    continue
                shift = round(start / packet.time_base)
                packet.pts = None if packet.pts is None else packet.pts - shift
                packet.dts -= shift
                packet.stream = dst
                out.mux(packet)
            else:
                try:
                    frames = packet.decode()
                except av.error.InvalidDataError:  # a damaged packet: skip it
                    continue
                for frame in frames:
                    if not is_video:
                        sound(frame)
                        continue
                    if frame.pts is None:
                        continue
                    frame.pts -= round(start / frame.time_base)
                    if last_t is not None and frame.time - last_t < gap:  # more than 30 frames a second: drop some
                        continue
                    last_t = frame.time
                    for small in scaler(frame):
                        out.mux(dst.encode(small))
            t = packet.pts * packet.time_base if packet.pts is not None and packet.time_base else None
            if t is not None and time.time() - last_report > 0.5:
                last_report = time.time()
                on_progress(max(0.0, float(t) - start))
        if not p["copy_video"]:
            out.mux(v_out.encode(None))
        if resampler is not None:
            sound(None)
            out.mux(a_out.encode(None))
