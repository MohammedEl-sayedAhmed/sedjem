#!/usr/bin/env python3
"""Build the test sets as 16 kHz mono WAV files plus JSONL manifests.

perle:    Egyptian Arabic-English rows of Perle-ai/ASR_Code_Switch (tech, work and daily-life
          sentences). The closest public match to the use case. Taken from its only split,
          "train", so models released after May 2026 may have trained on it.
arzen:    Egyptian Arabic-English conversational clips from the ArzEn validation split. Several
          Arabic fine-tunes were trained on ArzEn, so it overstates them.
mixat:    Emirati Arabic-English clips from the Mixat test split (prepared, not scored).
meetings: 2-minute excerpts of the local recordings in audio-test/, split at pauses into
          chunks of at most 25 s. No reference transcripts; never leaves this machine.

Every clip is written in three conditions, each ending with 0.5 s of silence (R2T2 drops
trailing words without it) so all models get identical input:
  wav16k  the original resampled to 16 kHz
  phone   resampled through 8 kHz (removes everything above 4 kHz, like the local recordings)
  g711    a telephone channel: 300-3400 Hz band-pass and G.711 mu-law coding at 8 kHz

The clips used are pinned in bench/samples/<set>.txt, so results stay reproducible; delete a
pin file to draw a new random sample (seed 42).

Usage: .venv/bin/python bench/prepare_data.py [perle] [arzen] [mixat] [meetings]
"""
import json
import os
import random
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf
from faster_whisper import decode_audio
from faster_whisper.vad import VadOptions, get_speech_timestamps

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = Path(__file__).resolve().parent / "samples"
MEETINGS = ROOT / "data" / "meetings"
SR = 16000
SEED = 42
TAIL = np.zeros(SR // 2, dtype=np.float32)
CONDITIONS = ("wav16k", "phone", "g711")
EVENT_TAGS = re.compile(r"\[(?:HES|LAUGHTER|HUM|NOISE)\]")
# Hugging Face datasets: (repo, split, cached row index under data/, fields kept per row or None to skip)
DATASETS = {
    "mixat": ("sqrk/mixat-tri", "test", "mixat/test_meta.json",
              lambda r: {"language": r["language"], "duration_ms": r["duration_ms"], "transcript": r["transcript"]}),
    "arzen": ("ahmedsamirtarjama/MBZUAI_ArzEn_wav", "validation", "arzen_val_meta.json",
              lambda r: {"text": r["text"]}),
    "perle": ("Perle-ai/ASR_Code_Switch", "train", "perle_egyptian_meta.json",
              lambda r: {"text": r["transcript"]} if r["language_pair"] == "Egyptian Arabic–English" else None),
}
EXCERPT_S = 120
MAX_CHUNK_S = 25


def run_ffmpeg(src, dst, filters, codec="pcm_s16le", rate=SR):
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src), "-ac", "1",
                    "-af", filters, "-ar", str(rate), "-c:a", codec, "-f", "wav", str(dst)], check=True)


def convert(raw, dst, condition):
    """Write one condition of a clip, padded with TAIL, atomically (an interrupted run leaves no
    half-written file behind that later runs would trust)."""
    tmp = dst.with_name(dst.stem + ".tmp.wav")
    if condition == "wav16k":
        run_ffmpeg(raw, tmp, "aresample=16000")
    elif condition == "phone":
        run_ffmpeg(raw, tmp, "aresample=8000,aresample=16000")
    else:  # g711: telephone band and mu-law coding at 8 kHz, then back to 16 kHz
        coded = dst.with_name(dst.stem + ".tmp8k.wav")
        run_ffmpeg(raw, coded, "highpass=f=300,lowpass=f=3400,aresample=8000", codec="pcm_mulaw", rate=8000)
        run_ffmpeg(coded, tmp, "aresample=16000")
        coded.unlink()
    audio, sr = sf.read(tmp, dtype="float32")
    sf.write(tmp, np.concatenate([audio, TAIL]), sr, subtype="PCM_16", format="WAV")
    os.replace(tmp, dst)


def rows_api(name, offset, length):
    repo, split = DATASETS[name][:2]
    url = (f"https://datasets-server.huggingface.co/rows?dataset={repo}&config=default&split={split}"
           f"&offset={offset}&length={length}")
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def audio_src(row):
    audio = row["audio"]
    return (audio[0] if isinstance(audio, list) else audio)["src"]


def load_index(name):
    """Rows of a test set (text + audio URL), fetched from the Hugging Face rows API once and cached."""
    path = ROOT / "data" / DATASETS[name][2]
    if not path.exists():
        keep, rows, offset = DATASETS[name][3], [], 0
        while True:
            page = rows_api(name, offset, 100)
            for x in page["rows"]:
                fields = keep(x["row"])
                if fields is not None:
                    rows.append({"idx": x["row_idx"], **fields, "audio_src": audio_src(x["row"])})
            offset += 100
            if offset >= page["num_rows_total"]:
                break
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    return json.loads(path.read_text())


def fetch_clip(name, row, dst):
    part = dst.with_name(dst.name + ".part")
    try:
        urllib.request.urlretrieve(row["audio_src"], part)
    except Exception:  # signed asset URLs expire; ask the rows API for a fresh one
        urllib.request.urlretrieve(audio_src(rows_api(name, row["idx"], 1)["rows"][0]["row"]), part)
    os.replace(part, dst)


def write_set(name, clips):
    """clips: [(clip id, raw path, duration, reference)] -> converted audio + manifest + pin file."""
    out_dir = ROOT / "data" / name
    for d in CONDITIONS:
        (out_dir / d).mkdir(parents=True, exist_ok=True)
    with open(out_dir / "manifest.jsonl", "w") as out:
        for cid, raw, duration, text in clips:
            entry = {"id": cid, "duration": round(duration, 2), "reference": text}
            for d in CONDITIONS:
                dst = out_dir / d / f"{cid}.wav"
                if not dst.exists():
                    convert(raw, dst, d)
                entry[d] = str(dst.relative_to(ROOT))
            out.write(json.dumps(entry, ensure_ascii=False) + "\n")
    SAMPLES.mkdir(exist_ok=True)
    (SAMPLES / f"{name}.txt").write_text("".join(c[0] + "\n" for c in clips))
    print(f"{name}: {len(clips)} clips, {sum(c[2] for c in clips) / 60:.1f} min")


def pinned(name):
    path = SAMPLES / f"{name}.txt"
    return path.read_text().split() if path.exists() else None


def prepare_mixat(n_clips=60):
    rows = {f"mixat{r['idx']:04d}": r for r in load_index("mixat")}
    ids = pinned("mixat")
    if ids is None:
        pool = [k for k, r in rows.items() if r["language"] == "CS" and 3000 <= r["duration_ms"] <= 30000]
        ids = sorted(random.Random(SEED).sample(pool, n_clips))
    (ROOT / "data" / "mixat" / "raw").mkdir(parents=True, exist_ok=True)
    clips = []
    for cid in ids:
        raw = ROOT / "data" / "mixat" / "raw" / f"{cid}.wav"
        if not raw.exists():
            fetch_clip("mixat", rows[cid], raw)
        clips.append((cid, raw, rows[cid]["duration_ms"] / 1000, rows[cid]["transcript"]))
    write_set("mixat", clips)


def prepare_arzen(n_clips=40, name="arzen"):
    """Egyptian Arabic-English clips (3-25 s) whose reference has both Arabic and English words."""
    rows = {f"{name}{r['idx']:04d}": r for r in load_index(name)}
    (ROOT / "data" / name / "raw").mkdir(parents=True, exist_ok=True)

    def raw_clip(cid):
        raw = ROOT / "data" / name / "raw" / f"{cid}.wav"
        if not raw.exists():
            fetch_clip(name, rows[cid], raw)
        return raw

    ids = pinned(name)
    if ids is None:  # draw a new sample; event tags like [HES] don't count as English
        mixed = [k for k, r in rows.items() if re.search(r"[A-Za-z]", EVENT_TAGS.sub(" ", r["text"]))
                 and re.search(r"[؀-ۿ]", r["text"])]
        ids = []
        for cid in random.Random(SEED).sample(mixed, min(len(mixed), 3 * n_clips)):
            if 3 <= sf.info(raw_clip(cid)).duration <= 25:
                ids.append(cid)
            if len(ids) == n_clips:
                break
        ids.sort()
    write_set(name, [(cid, raw_clip(cid), sf.info(raw_clip(cid)).duration, rows[cid]["text"]) for cid in ids])


def prepare_perle(n_clips=40):
    """Egyptian Arabic-English rows of Perle-ai/ASR_Code_Switch: tech and work sentences."""
    prepare_arzen(n_clips, name="perle")


def speech_windows(speech, n_samples, excerpt, step):
    """Yield (start, speech_seconds) for each candidate excerpt window."""
    mask = np.zeros(n_samples, dtype=bool)
    for s in speech:
        mask[s["start"]:s["end"]] = True
    for start in range(60 * SR, n_samples - excerpt, step):
        yield start, mask[start:start + excerpt].sum() / SR


def chunk_speech(speech, max_len):
    """Merge consecutive VAD segments into chunks no longer than max_len samples."""
    chunks = []
    for s in speech:
        if chunks and s["end"] - chunks[-1][0] <= max_len:
            chunks[-1][1] = s["end"]
        else:
            chunks.append([s["start"], s["end"]])
    return chunks


def prepare_meetings():
    """2-minute excerpts of every recording in audio-test/ (the whole file if it is short; else the
    window with the most speech), named rec1, rec2, ... because the file names carry call times."""
    MEETINGS.mkdir(parents=True, exist_ok=True)
    vad = VadOptions(min_silence_duration_ms=500, speech_pad_ms=200, max_speech_duration_s=MAX_CHUNK_S)
    excerpt = EXCERPT_S * SR
    seen = set()
    with open(MEETINGS / "manifest.jsonl", "w") as out:
        for path in sorted((ROOT / "audio-test").glob("*.wav")):
            if path.stat().st_size in seen:  # a byte-identical copy of an earlier file
                continue
            seen.add(path.stat().st_size)
            rec = f"rec{len(seen)}"
            audio = decode_audio(str(path), sampling_rate=SR)
            if len(audio) <= excerpt + 30 * SR:
                start = 0
            else:
                speech = get_speech_timestamps(audio, vad)
                start, _ = max(speech_windows(speech, len(audio), excerpt, 30 * SR), key=lambda w: w[1])
            clip = audio[start:start + excerpt]
            speech = get_speech_timestamps(clip, vad)
            chunks = chunk_speech(speech, MAX_CHUNK_S * SR)
            for i, (a, b) in enumerate(chunks):
                wav = MEETINGS / f"{rec}_{i:02d}.wav"
                sf.write(wav, np.concatenate([clip[a:b], TAIL]), SR, subtype="PCM_16")
                out.write(json.dumps({"id": f"{rec}_{i:02d}", "source": path.name,
                                      "start": round((start + a) / SR, 2), "end": round((start + b) / SR, 2),
                                      "wav16k": str(wav.relative_to(ROOT))}) + "\n")
            voiced = sum(s["end"] - s["start"] for s in speech) / SR
            print(f"{rec}: excerpt {start / SR:.0f}-{start / SR + EXCERPT_S:.0f} s, "
                  f"{voiced:.0f} s of speech, {len(chunks)} chunks")


if __name__ == "__main__":
    steps = {"mixat": prepare_mixat, "arzen": prepare_arzen, "perle": prepare_perle, "meetings": prepare_meetings}
    for name in sys.argv[1:] or ["perle", "arzen"]:
        steps[name]()
