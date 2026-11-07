#!/usr/bin/env python3
"""Cut evaluation excerpts from a private meetings dataset (layout below) into data/private-meetings/.

The dataset folder holds one directory per meeting with partNN.mp3 audio and partNN.named.txt
reference transcripts ("Name: text" per turn), plus a manifest.json at the top:
  {"meetings": [{"speakers": ["Name A", ...],
                 "parts": [{"audio": "<meeting>/part01.mp3", "named": "<meeting>/part01.named.txt",
                            "duration_s": 2400.0}, ...]}, ...]}
(paths relative to the dataset folder). An optional partNN.elevenlabs.txt next to a .named.txt
("[[S#]] text" per turn, the same words with ElevenLabs' own speaker tags) turns on the
ElevenLabs-label baseline in bench/score_meetings.py.

This takes the first --minutes of each meeting's first part as 16 kHz mono WAV (m1.wav, m2.wav,
... in manifest order) and writes a manifest with the reference path, the speaker names and the
number of people who speak in the excerpt (people with at least 20 words in the matching share of
the reference). data/ is git-ignored: nothing from the meetings enters the repository.

Usage: .venv/bin/python bench/prepare_private_meetings.py <dataset folder> [--minutes 10]
"""
import argparse
import json
import os
import subprocess
from collections import Counter
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from eval_meetings import reference_turns, words_of  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "private-meetings"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", type=Path)
    ap.add_argument("--minutes", type=float, default=10)
    args = ap.parse_args()
    manifest = json.loads((args.dataset / "manifest.json").read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    seconds = args.minutes * 60
    with open(OUT / "manifest.jsonl", "w") as out:
        for n, meeting in enumerate(manifest["meetings"], 1):
            part = meeting["parts"][0]
            wav = OUT / f"m{n}.wav"
            if not wav.exists():
                tmp = wav.with_name(wav.stem + ".tmp.wav")
                subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-t", str(seconds),
                                "-i", str(args.dataset / part["audio"]), "-ac", "1", "-ar", "16000",
                                "-c:a", "pcm_s16le", "-f", "wav", str(tmp)], check=True)
                os.replace(tmp, wav)
            reference = args.dataset / part["named"]
            turns = reference_turns(reference, speakers=meeting["speakers"])
            words = words_of(turns)
            share = words[: int(len(words) * min(1.0, seconds / part["duration_s"]))]
            people = Counter(p for _, p in share)
            n_speakers = sum(1 for c in people.values() if c >= 20)
            out.write(json.dumps({"id": f"m{n}", "wav16k": str(wav.relative_to(ROOT)),
                                  "reference": str(reference), "speakers": meeting["speakers"],
                                  "n_speakers": n_speakers, "excerpt_s": min(seconds, part["duration_s"]),
                                  "part_s": part["duration_s"], "meeting_speakers": len(meeting["speakers"])},
                                 ensure_ascii=False) + "\n")
            print(f"m{n}: {min(seconds, part['duration_s']) / 60:.0f} min excerpt, {n_speakers} speakers "
                  f"with 20+ words (meeting has {len(meeting['speakers'])} named)")


if __name__ == "__main__":
    main()
