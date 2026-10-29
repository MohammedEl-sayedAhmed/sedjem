#!/usr/bin/env python3
"""Compare NVIDIA Nemotron-3-Diarization with TitaNet-small for speaker labels on the private meeting excerpts.

Nemotron-3-Diarization (about 100M parameters, OpenMDW 1.1 licence) is one model that gives, every
10 ms, the probability that each of up to 8 speakers is talking, so two people talking at once both
show. It replaces all of speakers.py's steps: speech detection, voiceprints and clustering.

This runs it as the app does (nemotron.py, in NumPy, and speakers.activity_timeline) and labels the
same words as bench/compare_voiceprints.py (results/meetings/whisper/<id>.*.speakers.words.json): each
word gets the speaker of the timeline at its middle. TitaNet-small labels the same words again in the
same run. Both are scored with WDER (bench/eval_meetings.py), on all the words and on the words where
Nemotron hears two or more people at once, with the true number of speakers given and estimated.

Writes results/meetings/nemotron.json and Nemotron's probabilities to results/meetings/nemotron/
(git-ignored), and prints a table with no meeting content. --reuse scores the saved probabilities
again without running the model. The model is the app's download (Settings → Models), in
models/diarization/Nemotron-3-Diarization/.

Usage: .venv/bin/python bench/compare_nemotron.py [--reuse]
"""
import argparse
import json
import resource
import sys
import time
from pathlib import Path

import numpy as np
from faster_whisper import decode_audio

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
import nemotron  # noqa: E402
import speakers  # noqa: E402
from eval_meetings import evaluate, reference_turns  # noqa: E402
from score import normalize  # noqa: E402

MODEL = ROOT / "models" / "diarization" / "Nemotron-3-Diarization"
OUT = ROOT / "results" / "meetings" / "nemotron"
FRAME_S = 0.01  # Nemotron gives one probability per speaker every 10 ms
THREADS = 4  # as transcribe.py gives the voiceprints


def run_nemotron(items):
    """Nemotron's probabilities for each excerpt, saved with the time they took and the peak memory."""
    model = nemotron.Model(MODEL)
    OUT.mkdir(parents=True, exist_ok=True)
    for item in items:
        audio = decode_audio(str(ROOT / item["wav16k"]), sampling_rate=speakers.SR)
        t0 = time.perf_counter()
        probs = model.probabilities(audio)
        took = time.perf_counter() - t0
        np.save(OUT / f"{item['id']}.npy", probs.astype(np.float16))
        (OUT / f"{item['id']}.json").write_text(json.dumps({
            "seconds": round(took, 1), "audio_s": round(len(audio) / speakers.SR, 1),
            "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)}))
        print(f"{item['id']} Nemotron took {took:.0f} s", flush=True)


def score(turns, words, labels, overlapped):
    """WDER on all words and on the overlapped ones, with the counts to pool them."""
    hyp, word_of = [], []
    for k, ((_, _, w, *_), label) in enumerate(zip(words, labels)):
        for t in normalize(w).split():
            hyp.append((t, f"spk{label}"))
            word_of.append(k)
    r = evaluate(turns, hyp, prefix=True)
    over = [j for j in r["right_speaker"] if overlapped[word_of[j]]]
    return {"wder": r["wder"], "aligned_words": r["aligned_words"],
            "overlap_wrong": sum(not r["right_speaker"][j] for j in over), "overlap_words": len(over)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse", action="store_true", help="score the saved probabilities without running Nemotron")
    args = ap.parse_args()
    items = []
    for line in open(ROOT / "data" / "private-meetings" / "manifest.jsonl"):
        item = json.loads(line)
        found = sorted((ROOT / "results" / "meetings" / "whisper").glob(f"{item['id']}.*.speakers.words.json"))
        if found:
            items.append({**item, "words": found[0]})
        else:
            print(f"{item['id']}: no whisper words yet, skipped", file=sys.stderr)
    todo = [item for item in items if not (args.reuse and (OUT / f"{item['id']}.npy").exists())]
    if todo:
        run_nemotron(todo)

    rows = []
    for item in items:
        words = json.loads(item["words"].read_text())
        audio = decode_audio(str(ROOT / item["wav16k"]), sampling_rate=speakers.SR)
        turns = reference_turns(item["reference"], speakers=item["speakers"])
        probs = np.load(OUT / f"{item['id']}.npy").astype(np.float32)
        meta = json.loads((OUT / f"{item['id']}.json").read_text())
        active = (probs > 0.5).sum(1)
        overlapped = [active[min(int((s + e) / 2 / FRAME_S), len(probs) - 1)] >= 2 for s, e, *_ in words]
        t0 = time.perf_counter()
        prints = speakers.voiceprints(audio, THREADS)
        speakers.timeline(*prints)
        titanet_s = time.perf_counter() - t0
        common = {"meeting": item["id"], "true_speakers": item["n_speakers"],
                  "speech_s": round(float((active > 0).sum()) * FRAME_S, 1),
                  "overlap_s": round(float((active >= 2).sum()) * FRAME_S, 1)}
        for mode, n in (("given", item["n_speakers"]), ("estimated", None)):
            for model, tl, took in (("TitaNet-small", speakers.timeline(*prints, n), titanet_s),
                                    ("Nemotron-3-Diarization", speakers.activity_timeline(probs, n), meta["seconds"])):
                labels = [tl.speaker_over(s, e) for s, e, *_ in words]  # as transcribe.py does
                found = len(set(tl.labels.tolist()))
                r = score(turns, words, labels, overlapped)
                rows.append({**common, "model": model, "mode": mode, "found_speakers": found,
                             "seconds": round(took, 1), **r})
                print(f"{item['id']} {model:22s} {mode:9s} WDER {r['wder']:.1%}  speakers {found}/{item['n_speakers']}"
                      f"  overlapped words {r['overlap_wrong']}/{r['overlap_words']} wrong  ({took:.0f} s)", flush=True)
        talk = sorted(((probs > 0.5).sum(0) * FRAME_S).round(1).tolist(), reverse=True)
        print(f"{item['id']} Nemotron talk time per speaker (s): {talk}; overlap {common['overlap_s']} of "
              f"{common['speech_s']} s of speech", flush=True)
    out = ROOT / "results" / "meetings" / "nemotron.json"
    out.write_text(json.dumps(rows, indent=1))

    def pooled(rs, wrong, total):
        return sum(wrong(r) for r in rs) / max(1, sum(total(r) for r in rs))

    print("\n| Speaker model | WDER, speaker count given | WDER, count estimated | count estimated right "
          "| WDER on overlapped words, count given | seconds per excerpt |")
    print("|---|---|---|---|---|---|")
    for model in ("TitaNet-small", "Nemotron-3-Diarization"):
        g = [r for r in rows if r["model"] == model and r["mode"] == "given"]
        e = [r for r in rows if r["model"] == model and r["mode"] == "estimated"]
        if not g:
            continue
        wder = lambda r: r["wder"] * r["aligned_words"]  # noqa: E731
        right = sum(r["found_speakers"] == r["true_speakers"] for r in e)
        print(f"| {model} | {pooled(g, wder, lambda r: r['aligned_words']):.1%} "
              f"| {pooled(e, wder, lambda r: r['aligned_words']):.1%} | {right}/{len(e)} "
              f"| {pooled(g, lambda r: r['overlap_wrong'], lambda r: r['overlap_words']):.1%} "
              f"of {sum(r['overlap_words'] for r in g)} | {np.mean([r['seconds'] for r in g]):.0f} |")
    once = [r for r in rows if r["model"] == "Nemotron-3-Diarization" and r["mode"] == "given"]
    speech, overlap = sum(r["speech_s"] for r in once), sum(r["overlap_s"] for r in once)
    peak = max(json.loads((OUT / f"{item['id']}.json").read_text())["peak_rss_mb"] for item in items)
    print(f"\nNemotron hears two or more people at once in {overlap / max(speech, 1):.1%} of the speech; "
          f"its peak memory was {peak} MB.")


if __name__ == "__main__":
    main()
