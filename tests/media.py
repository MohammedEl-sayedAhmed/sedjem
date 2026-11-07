"""Small recordings made in code for the tests (PyAV): a few seconds of a moving picture and a tone, in
whatever container, codecs, size and frame rate a test needs. Nothing is downloaded or kept."""
from fractions import Fraction

import numpy as np


def make_clip(path, width=320, height=180, seconds=2.0, fps=10, vcodec="libx264", acodec="aac", rate=48000,
              rotate=0, audio=True, pix_fmt="yuv420p"):
    """Write a test video to path (the container follows its extension) and return path."""
    import av
    with av.open(str(path), "w") as c:
        v = c.add_stream(vcodec, rate=fps)
        v.width, v.height, v.pix_fmt = width, height, pix_fmt
        if vcodec == "libx264":
            v.options = {"preset": "ultrafast"}
        if rotate:
            v.set_display_rotation(rotate)
        a = None
        if audio:
            a = c.add_stream(acodec, rate=rate)
            a.layout = "stereo"
        yy, xx = np.mgrid[0:height, 0:width]
        for i in range(int(seconds * fps)):
            img = np.zeros((height, width, 3), np.uint8)
            img[..., 0] = (xx * 255 // width + i * 9) % 256
            img[..., 1] = yy * 255 // height
            img[..., 2] = (i * 25) % 256  # a different colour every frame, to tell them apart
            frame = av.VideoFrame.from_ndarray(img, format="rgb24")
            frame.pts, frame.time_base = i, Fraction(1, fps)
            c.mux(v.encode(frame))
        c.mux(v.encode(None))
        if a is not None:
            n = int(seconds * rate)
            tone = (0.2 * np.sin(2 * np.pi * 330 * np.arange(n) / rate)).astype(np.float32)
            step = 960
            for start in range(0, n, step):
                chunk = np.ascontiguousarray(np.stack([tone[start:start + step]] * 2))
                frame = av.AudioFrame.from_ndarray(chunk, format="fltp", layout="stereo")
                frame.sample_rate, frame.pts, frame.time_base = rate, start, Fraction(1, rate)
                c.mux(a.encode(frame))
            c.mux(a.encode(None))
    return path
