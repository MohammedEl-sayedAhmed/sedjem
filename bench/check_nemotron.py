#!/usr/bin/env python3
"""Check nemotron.py (NumPy) against Transformers' Nemotron-3-Diarization on real recordings.

For each 16 kHz recording it runs both on the same audio and prints how far apart their speaker
probabilities are (largest and mean difference, and the share of 10 ms frames where they disagree on
who is talking), and the time each took. Nothing of the recordings is printed. With no files given it
uses the private meeting excerpts in data/private-meetings/.

It needs PyTorch and Transformers, which Sedjem doesn't use, so it runs in an environment of its own;
the model is the app's download (Settings → Models), models/diarization/Nemotron-3-Diarization/:

  uv venv .venv-bench --python 3.12
  uv pip install --python .venv-bench torch --index-url https://download.pytorch.org/whl/cpu
  uv pip install --python .venv-bench -r requirements.txt transformers==5.21.0 librosa==1.0.0

Usage: .venv-bench/bin/python bench/check_nemotron.py [audio ...] [--seconds N]
"""
import argparse
import sys
import time
from pathlib import Path

import numpy as np
import torch
from faster_whisper import decode_audio
from transformers import AutoProcessor, Nemotron3DiarizationForAudioFrameClassification

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import nemotron  # noqa: E402

MODEL = ROOT / "models" / "diarization" / "Nemotron-3-Diarization"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", nargs="*", help="recordings (default: data/private-meetings/*.wav)")
    ap.add_argument("--seconds", type=float, help="only the first N seconds of each")
    ap.add_argument("--threads", type=int, default=4)
    args = ap.parse_args()
    files = args.audio or sorted(str(p) for p in (ROOT / "data" / "private-meetings").glob("m*.wav"))
    torch.set_num_threads(args.threads)
    processor = AutoProcessor.from_pretrained(MODEL)
    reference = Nemotron3DiarizationForAudioFrameClassification.from_pretrained(MODEL).eval()
    ours = nemotron.Model(MODEL)
    worst = 0.0
    for i, path in enumerate(files, 1):
        audio = decode_audio(path, sampling_rate=16000)
        if args.seconds:
            audio = audio[:int(args.seconds * 16000)]
        t0 = time.perf_counter()
        with torch.inference_mode():
            want = reference(**processor(audio, sampling_rate=16000)).logits.sigmoid()[0].numpy()
        t1 = time.perf_counter()
        got = ours.probabilities(audio)
        t2 = time.perf_counter()
        diff = np.abs(got - want)
        flips = np.mean((got > 0.5) != (want > 0.5))
        worst = max(worst, float(diff.max()))
        print(f"recording {i}: {len(audio) / 16000:.0f} s, {len(got)} frames (Transformers {len(want)}); "
              f"largest difference {diff.max():.2e}, mean {diff.mean():.2e}; who talks differs in {flips:.3%} "
              f"of frame-speakers; Transformers {t1 - t0:.1f} s, NumPy {t2 - t1:.1f} s", flush=True)
    print(f"largest difference overall: {worst:.2e}")


if __name__ == "__main__":
    main()
