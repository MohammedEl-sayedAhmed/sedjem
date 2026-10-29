#!/usr/bin/env python3
"""Speaker labels in Egyptian Arabic-English interviews: Sedjem's speaker models on ArzEn.

The ArzEn speech corpus (CC BY-SA 4.0; Hamed, Vu and Abdennadher, LREC 2020) is 38 informal interviews
held at the German University in Cairo, 12 hours of Egyptian Arabic mixed with English, at 16 kHz in one
channel. In each, one interviewee answers a fixed set of questions from the same two interviewers, a man
and a woman (in two recordings the annotation has only one of them). The corpus marks who speaks when
only through its transcription: utterance segments of up to 25 s, each with its speaker
(ASR_files/segments and utt2spk, in Kaldi's format). Those are the reference turns here. They never
overlap, and they mostly run from one cut to the next, so the pauses inside them, and often the silence
before the next speaker, count as speech.

This scores, on every recording, as bench/compare_ami.py does on AMI:

  TitaNet-small, TitaNet-large, 3D-Speaker CAM++
                                speakers.py's voiceprints and clustering, as Sedjem uses them
  Nemotron, as the app          speakers.activity_timeline(): one speaker at a time
  Nemotron, overlap included    the model's own output: everyone above 0.5, overlaps too

with DER on 10 ms frames: the share of the reference's speaking time that is missed, falsely detected or
given to the wrong person, with speakers matched to the reference people in the way that agrees best.
Because the reference counts pauses as speech, its missed part is inflated, so DER is also given with a
0.25 s collar (the frames within 0.25 s of where a reference speaker starts or stops are not scored), and
the confusion part is the fairest read. Closest to how Sedjem uses the labels is "wrong speaker on speech"
(compare_ami.wrong_on_speech): every 10 ms of the reference's speech labelled as a word there would be,
also where a model hears no one, and the share of it given to someone who isn't talking then. Each runs
with the number of speakers given and estimated. The time is that of the whole speaker step: the
voiceprints and the clustering, or the model and the timeline. The clustering runs with NumPy's matrix
library on 4 threads, as nemotron.py does: with its default of every core it took 17 to 57 s on a
17-minute interview instead of 2 to 3 s. Silero VAD's speech (speakers.speech_segments, which
TitaNet-small's step runs anyway) and the speech Nemotron hears (anyone above 0.5) are set against the
reference too, per speaker, to show how much of it is pauses.

The corpus is a download after a form (sites.google.com/view/arzen-corpus/resources), unzipped into
data/arzen-corpus/ (git-ignored). Writes results/arzen-diarization.json (scores only, kept in git) and
prints a table with no transcript content. Nemotron's probabilities are kept in data/arzen-corpus/nemotron/,
so --reuse scores them again without running it. The 38 recordings took about 85 minutes on the test laptop.

Usage: .venv-bench/bin/python bench/compare_arzen.py [--recordings N] [--reuse]
"""
import argparse
import collections
import json
import sys
import time
from pathlib import Path

import numpy as np
from faster_whisper import decode_audio
from threadpoolctl import threadpool_limits

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
import nemotron  # noqa: E402
import speakers  # noqa: E402
from compare_ami import DIAR, FRAME_S, VOICEPRINTS, der, frames, labels_at, timeline_frames, wrong_on_speech  # noqa: E402

DATA = ROOT / "data" / "arzen-corpus" / "ArzEn_SpeechCorpus_1.0"
NEMOTRON = ROOT / "models" / "diarization" / "Nemotron-3-Diarization"
COLLARS = (0.0, 0.25)  # seconds either side of a reference boundary that are not scored
SYSTEMS = (*VOICEPRINTS, "Nemotron, as the app", "Nemotron, overlap included")
SAVED = ROOT / "data" / "arzen-corpus" / "nemotron"


def fields(path):
    return [line.split() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def recordings():
    """(recording, audio file, reference turns, the interviewee's split) for each recording: the corpus's
    utterance segments with their speakers, on the audio file that wav.scp names for them. The split lists
    name a recording's speakers, and put every interviewer in train."""
    asr = DATA / "ASR_files"
    audio = {rec: Path(path).name for rec, path in fields(asr / "wav.scp")}
    speaker = dict(fields(asr / "utt2spk"))
    split = {}
    for name in ("train", "dev", "test"):
        for (rec,) in fields(DATA / "splits" / name):
            if "-S" in rec:
                split[rec.rsplit("-", 1)[0]] = name
    turns = collections.defaultdict(list)
    for utt, rec, a, b in fields(asr / "segments"):
        turns[audio[rec]].append([float(a), float(b), speaker[utt]])
    return [(Path(name).stem, DATA / "recordings" / name, sorted(ts), split.get(Path(name).stem))
            for name, ts in sorted(turns.items())]


def outside_collar(ref, collar_s):
    """Frames farther than collar_s from every reference boundary, wherever someone starts or stops
    talking (two touching segments of one speaker make none)."""
    if not collar_s:
        return np.ones(len(ref), bool)
    k = int(round(collar_s / FRAME_S))
    edge = np.zeros((1, ref.shape[1]), bool)
    padded = np.concatenate([edge, ref, edge])
    changes = np.flatnonzero((padded[1:] != padded[:-1]).any(1))  # frame i differs from frame i - 1
    marks = np.zeros(len(ref) + 1, np.int64)
    np.add.at(marks, np.maximum(changes - k, 0), 1)
    np.add.at(marks, np.minimum(changes + k, len(ref)), -1)
    return np.cumsum(marks)[:len(ref)] == 0


def secs(on):
    """Seconds of the frames that are on."""
    return round(float(on.sum()) * FRAME_S, 1)


def share(r):
    return (r["missed"] + r["false_alarm"] + r["confusion"]) / r["total"]


def pooled(rs):
    return sum(r["missed"] + r["false_alarm"] + r["confusion"] for r in rs) / sum(r["total"] for r in rs)


def parts(rs):
    total = sum(r["total"] for r in rs)
    return " / ".join(f"{sum(r[k] for r in rs) / total:.1%}" for k in ("missed", "false_alarm", "confusion"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recordings", type=int, help="only the first N recordings")
    ap.add_argument("--reuse", action="store_true", help="score Nemotron's saved probabilities without running it")
    args = ap.parse_args()
    items = recordings()[:args.recordings]
    model = nemotron.Model(NEMOTRON)
    onnx = {name: DIAR / f for name, f in VOICEPRINTS.items() if (DIAR / f).exists()}
    refs, rows = [], []
    for name, path, turns, split in items:
        audio = decode_audio(str(path), sampling_rate=speakers.SR)
        audio_s = round(len(audio) / speakers.SR, 1)
        n = int(len(audio) / speakers.SR / FRAME_S)
        ref, people = frames(turns, n)
        prints, took = {}, {}
        for system, f in onnx.items():
            t0 = time.perf_counter()
            with threadpool_limits(limits=4, user_api="blas"):
                prints[system] = speakers.voiceprints(audio, 4, f)
            took[system] = time.perf_counter() - t0
        saved = SAVED / f"{name}.npy"
        if args.reuse and saved.exists():
            probs = np.load(saved).astype(np.float32)[:n]
            took["Nemotron"] = json.loads(saved.with_suffix(".json").read_text())["seconds"]
        else:
            t0 = time.perf_counter()
            probs = model.probabilities(audio)[:n]
            took["Nemotron"] = time.perf_counter() - t0
            SAVED.mkdir(parents=True, exist_ok=True)
            np.save(saved, probs.astype(np.float16))
            saved.with_suffix(".json").write_text(json.dumps({"seconds": round(took["Nemotron"], 1)}))
        if len(probs) < n:
            probs = np.pad(probs, ((0, n - len(probs)), (0, 0)))
        scored = {c: outside_collar(ref, c) for c in COLLARS}
        hop = int(speakers.SR * FRAME_S)
        heard = {"vad": np.zeros(n, bool), "nemotron": probs.max(1) > 0.5}
        for a, b in prints["TitaNet-small"][2]:  # Silero VAD's speech, in samples
            heard["vad"][a // hop:-(-b // hop)] = True
        talk = ref.any(1)
        refs.append({"recording": name, "interviewee_split": split, "audio_s": audio_s,
                     "speakers": {p: {"reference_s": secs(ref[:, k]),
                                      **{f"{d}_hears_s": secs(ref[:, k] & h) for d, h in heard.items()}}
                                  for k, p in enumerate(people)},
                     "reference_s": secs(talk), "overlap_s": secs(ref.sum(1) > 1),
                     "scored_with_collar_s": secs(talk & scored[COLLARS[-1]]),
                     **{f"reference_not_{d}_s": secs(talk & ~h) for d, h in heard.items()},
                     **{f"{d}_not_reference_s": secs(h & ~talk) for d, h in heard.items()}})
        for mode, count in (("given", len(people)), ("estimated", None)):
            systems = {}
            for system in onnx:
                c0 = time.perf_counter()
                with threadpool_limits(limits=4, user_api="blas"):
                    tl = speakers.timeline(*prints[system], count)
                systems[system] = (tl, took[system] + time.perf_counter() - c0, took[system])
            c0 = time.perf_counter()
            app = speakers.activity_timeline(probs, count)
            systems["Nemotron, as the app"] = (app, took["Nemotron"] + time.perf_counter() - c0, took["Nemotron"])
            above = probs > 0.5
            above_s = above.sum(0)
            keep = [int(s) for s in np.argsort(-above_s, kind="stable") if above_s[s]][:count]
            systems["Nemotron, overlap included"] = (above[:, keep] if keep else above[:, :0], took["Nemotron"],
                                                     took["Nemotron"])
            for system, (out, secs_, model_s) in systems.items():
                hyp = out if isinstance(out, np.ndarray) else timeline_frames(out, n)
                labels = None if isinstance(out, np.ndarray) else labels_at(out, n)
                found = int(hyp.any(0).sum())
                shown = []
                for collar in COLLARS:
                    keep_frames = scored[collar]
                    r = der(ref[keep_frames], hyp[keep_frames])
                    if labels is not None:  # one speaker at a time: label the speech as words are
                        r.update(wrong_on_speech(ref[keep_frames], labels[keep_frames]))
                    rows.append({"recording": name, "system": system, "mode": mode, "collar_s": collar,
                                 "people": len(people), "found": found, "seconds": round(secs_, 1),
                                 "model_seconds": round(model_s, 1), "audio_s": audio_s, **r})
                    shown.append(f"{share(r):6.1%} (missed {r['missed'] / r['total']:.1%}, false alarm "
                                 f"{r['false_alarm'] / r['total']:.1%}, confusion {r['confusion'] / r['total']:.1%})")
                print(f"{name} {system:26s} {mode:9s} DER {shown[0]}, with collar {shown[1]}"
                      f"  speakers {found}/{len(people)}  {secs_:.0f} s", flush=True)
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "arzen-diarization.json").write_text(
        json.dumps({"recordings": refs, "scores": rows}, indent=1))

    total = {k: sum(r[k] for r in refs) for k in refs[0] if k.endswith("_s")}
    print(f"\n{len(refs)} recordings, {total['audio_s'] / 3600:.1f} h. The reference has "
          f"{total['reference_s'] / 3600:.1f} h of speech and {total['overlap_s']:.0f} s of overlap. Silero VAD "
          f"hears no speech in {total['reference_not_vad_s'] / total['reference_s']:.1%} of it and Nemotron no one "
          f"in {total['reference_not_nemotron_s'] / total['reference_s']:.1%}; outside it they hear "
          f"{total['vad_not_reference_s'] / 60:.1f} and {total['nemotron_not_reference_s'] / 60:.1f} min. The "
          f"{COLLARS[-1]} s collar leaves {total['scored_with_collar_s'] / total['reference_s']:.0%} of it scored.\n")
    print("| Speaker labels | Collar | wrong speaker on speech, count given | estimated | DER, count given "
          "| DER, count estimated | missed / false alarm / confusion, count given | missed / false alarm / confusion, "
          "count estimated | count estimated right | seconds per 10 minutes |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for collar in COLLARS:
        for system in SYSTEMS:
            given, est = ([r for r in rows if r["system"] == system and r["collar_s"] == collar and r["mode"] == m]
                          for m in ("given", "estimated"))
            right = sum(r["found"] == r["people"] for r in est)
            speed = sum(r["seconds"] for r in est) / sum(r["audio_s"] for r in est) * 600
            wrong = [f"{sum(r['wrong_on_speech'] for r in rs) / sum(r['speech'] for r in rs):.1%}"
                     if rs and "speech" in rs[0] else "-" for rs in (given, est)]
            print(f"| {system} | {f'{collar} s' if collar else 'none'} | {wrong[0]} | {wrong[1]} | {pooled(given):.1%} "
                  f"| {pooled(est):.1%} | {parts(given)} | {parts(est)} | {right} of {len(est)} | {speed:.0f} |")
    print()
    for system in SYSTEMS:
        counts = collections.Counter((r["found"], r["people"]) for r in rows
                                     if r["system"] == system and r["mode"] == "estimated" and not r["collar_s"])
        print(f"{system}, speakers found with the count estimated: "
              + ", ".join(f"{f} of {p} in {k}" for (f, p), k in sorted(counts.items())))


if __name__ == "__main__":
    main()
