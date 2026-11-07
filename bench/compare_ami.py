#!/usr/bin/env python3
"""Speaker labels on a public set of meetings: Sedjem's speaker models on the AMI meeting corpus.

AMI (CC BY 4.0) is 100 hours of English meetings, mostly of four people, many of them not native
speakers. This takes the 16 meetings of its standard test set, recorded with headset microphones mixed
into one channel (the "ihm" set of diarizers-community/ami on Hugging Face, prepared from
pyannote/AMI-diarization-setup, whose reference turns come from the word timings), and scores:

  TitaNet-small, TitaNet-large, 3D-Speaker CAM++
                                speakers.py's voiceprints and clustering, as Sedjem uses them
  Nemotron, as the app          speakers.activity_timeline(): one speaker at a time
  Nemotron, overlap included    the model's own output: everyone above 0.5, overlaps too

with DER, the share of the reference's speaking time that is missed, falsely detected or given to the
wrong person (summed over speakers, so overlap counts once per voice), on 10 ms frames, with no
forgiveness collar around the turns and with overlap scored. Speakers are matched to the reference people
in the way that agrees best. Each runs with the number of people given and estimated.

Who said each word, as Sedjem uses the labels: the speech model transcribes wherever someone talks, and each
word gets the speaker of the timeline at that moment, also where a model hears no speech. So for the systems
that give one speaker at a time, every 10 ms of the reference's speech is labelled that way, and "wrong speaker
on speech" is the share of it whose label (matched to the people as for DER) isn't someone talking then.

Overlap, where AMI marks it word by word: the share of the reference's speaking time in stretches where
two or more people talk, how much of the speaker time in those stretches each system misses (one speaker
at a time misses at least one voice), and for Nemotron with overlap how much of what it hears as overlap
is overlap (precision) and how much of the overlap it hears (recall).

Downloads about 0.9 GB into data/ami/ (git-ignored; 1.9 GB once unpacked) and writes
results/ami-diarization.json (scores only, kept in git); Nemotron's probabilities are kept in
data/ami/nemotron/, so --reuse scores them again without running it. Reading the downloaded files needs
pyarrow, so it runs in the environment of bench/check_nemotron.py with pyarrow added
(uv pip install --python .venv-bench pyarrow). The 16 meetings took about an hour on the test laptop.

Usage: .venv-bench/bin/python bench/compare_ami.py [--meetings N] [--reuse]
"""
import argparse
import itertools
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
from fetch_models import fetch_file, lookup  # noqa: E402

DATA = ROOT / "data" / "ami"
REPO = "datasets/diarizers-community/ami"
REVISION = "8cdaae2eaf968f3b000b6eb1204ab9b8db006ed0"
FILES = [f"ihm/test-0000{i}-of-00003.parquet" for i in range(3)]
DIAR = ROOT / "models" / "diarization"
NEMOTRON = DIAR / "Nemotron-3-Diarization"
VOICEPRINTS = {"TitaNet-small": "nemo_en_titanet_small.onnx", "TitaNet-large": "nemo_en_titanet_large.onnx",
               "3D-Speaker CAM++": "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"}
SYSTEMS = [*VOICEPRINTS, "Nemotron, as the app", "Nemotron, overlap included"]
FRAME_S = 0.01


def prepare():
    """data/ami/<meeting>.flac (or .wav) and <meeting>.json (the reference turns), from the test set."""
    import pyarrow.parquet as pq
    DATA.mkdir(parents=True, exist_ok=True)
    meetings = sorted(DATA.glob("*.json"))
    if len(meetings) == 16:
        return meetings
    for name in FILES:
        repo, path, size, sha256, _ = lookup(f"diarizers-community/ami:{name}", kind="datasets", revision=REVISION)
        (DATA / "download" / Path(path).parent).mkdir(parents=True, exist_ok=True)
        fetch_file(f"datasets/{repo}", path, size, sha256, folder=DATA / "download", revision=REVISION)
        for k, row in enumerate(pq.read_table(DATA / "download" / path).to_pylist()):
            audio = row["audio"]
            meeting = Path(audio["path"]).name.split(".")[0] if audio.get("path") else f"{Path(path).stem}-{k}"
            ext = Path(audio["path"]).suffix if audio.get("path") else ".wav"
            (DATA / f"{meeting}{ext}").write_bytes(audio["bytes"])
            turns = [[round(a, 3), round(b, 3), s] for a, b, s in
                     zip(row["timestamps_start"], row["timestamps_end"], row["speakers"])]
            (DATA / f"{meeting}.json").write_text(json.dumps({"audio": f"{meeting}{ext}", "turns": turns}))
    return sorted(DATA.glob("*.json"))


def frames(turns, n):
    """(frames, people) booleans: who talks in each 10 ms frame, and the people's names."""
    people = sorted({s for _, _, s in turns})
    out = np.zeros((n, len(people)), bool)
    for a, b, s in turns:
        out[int(round(a / FRAME_S)):int(round(b / FRAME_S)), people.index(s)] = True
    return out, people


def timeline_frames(tl, n):
    """A Timeline as frames: inside its speech segments, the speaker at each frame; one at a time."""
    labels = sorted({int(s) for s in tl.labels})
    out = np.zeros((n, max(1, len(labels))), bool)
    t = (np.arange(n) + 0.5) * FRAME_S
    for a, b in tl.segments:
        for i in range(int(a / FRAME_S), min(n, int(np.ceil(b / FRAME_S)))):
            s = tl.speaker_at(t[i])
            if s is not None:
                out[i, labels.index(s)] = True
    return out


def der(ref, hyp):
    """Missed, false alarm and confusion, in seconds of speaker time, and the reference's total, with
    hypothesis speakers matched one to one to reference people so that they agree most."""
    hyp = hyp[:, hyp.any(0)] if hyp.any() else hyp[:, :0]
    agree = hyp.T.astype(np.int64) @ ref.astype(np.int64)  # frames each pair talks together
    nh, nr = agree.shape
    best, best_pairs = -1, []
    if nh <= nr:
        options = ([(h, r) for h, r in zip(range(nh), p)] for p in itertools.permutations(range(nr), nh))
    else:
        options = ([(h, r) for r, h in zip(range(nr), p)] for p in itertools.permutations(range(nh), nr))
    for pairs in options:
        score = sum(agree[h, r] for h, r in pairs)
        if score > best:
            best, best_pairs = score, pairs
    n_ref, n_hyp = ref.sum(1), hyp.sum(1)
    correct = np.zeros(len(ref), np.int64)
    for h, r in best_pairs:
        correct += hyp[:, h] & ref[:, r]
    miss = np.maximum(n_ref - n_hyp, 0).sum()
    false = np.maximum(n_hyp - n_ref, 0).sum()
    confusion = (np.minimum(n_ref, n_hyp) - correct).sum()
    return {k: round(float(v) * FRAME_S, 2) for k, v in
            (("missed", miss), ("false_alarm", false), ("confusion", confusion), ("total", n_ref.sum()))}


def labels_at(tl, n):
    """The speaker a word gets at each 10 ms frame (Timeline.speaker_at for every frame at once); 0 for none."""
    if not len(tl.centers):
        return np.zeros(n, int)
    t, c = (np.arange(n) + 0.5) * FRAME_S, tl.centers
    i = np.searchsorted(c, t)
    lo, hi = np.clip(i - 1, 0, len(c) - 1), np.clip(i, 0, len(c) - 1)
    return tl.labels[np.where((i > 0) & (i < len(c)) & (t - c[lo] < c[hi] - t), lo, hi)]


def wrong_on_speech(ref, labels):
    """Seconds of the reference's speech whose label (one speaker per frame, matched one to one to the reference
    people so that they agree most) isn't someone talking then, and seconds of speech."""
    speech = ref.any(1)
    ids = sorted(set(labels[speech].tolist()) - {0})
    if not ids:
        return {"wrong_on_speech": round(float(speech.sum()) * FRAME_S, 2), "speech": round(float(speech.sum()) * FRAME_S, 2)}
    hyp = np.stack([labels == h for h in ids], 1) & speech[:, None]
    agree = hyp.T.astype(np.int64) @ ref.astype(np.int64)
    nh, nr = agree.shape
    if nh <= nr:
        options = ([(h, r) for h, r in zip(range(nh), p)] for p in itertools.permutations(range(nr), nh))
    else:
        options = ([(h, r) for r, h in zip(range(nr), p)] for p in itertools.permutations(range(nh), nr))
    pairs = max(options, key=lambda ps: sum(agree[h, r] for h, r in ps))
    right = sum(int(agree[h, r]) for h, r in pairs)
    return {"wrong_on_speech": round((int(speech.sum()) - right) * FRAME_S, 2), "speech": round(float(speech.sum()) * FRAME_S, 2)}


def overlap(ref, hyp):
    """Seconds of reference overlap (two or more people), of speaker time in it and of that missed, and of
    hypothesis overlap and the part of it that is reference overlap."""
    hyp = hyp[:, hyp.any(0)] if hyp.any() else hyp[:, :0]
    n_ref, n_hyp = ref.sum(1), hyp.sum(1)
    both, heard = n_ref >= 2, n_hyp >= 2
    return {k: round(float(v) * FRAME_S, 2) for k, v in
            (("overlap", both.sum()), ("overlap_speaker", n_ref[both].sum()),
             ("overlap_missed", np.maximum(n_ref - n_hyp, 0)[both].sum()),
             ("heard_overlap", heard.sum()), ("heard_overlap_right", (heard & both).sum()))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meetings", type=int, help="only the first N meetings")
    ap.add_argument("--reuse", action="store_true", help="score Nemotron's saved probabilities without running it")
    args = ap.parse_args()
    meetings = prepare()[:args.meetings]
    model = nemotron.Model(NEMOTRON)
    voiceprints = {name: DIAR / f for name, f in VOICEPRINTS.items() if (DIAR / f).exists()}
    rows = []
    for path in meetings:
        info = json.loads(path.read_text())
        audio = decode_audio(str(DATA / info["audio"]), sampling_rate=speakers.SR)
        n = int(len(audio) / speakers.SR / FRAME_S)
        ref, people = frames(info["turns"], n)
        prints, took = {}, {}
        for name, onnx in voiceprints.items():
            t0 = time.perf_counter()
            prints[name] = speakers.voiceprints(audio, 4, onnx)
            took[name] = time.perf_counter() - t0
        saved = DATA / "nemotron" / f"{path.stem}.npy"
        if args.reuse and saved.exists():
            probs = np.load(saved).astype(np.float32)[:n]
            took["Nemotron"] = json.loads(saved.with_suffix(".json").read_text())["seconds"]
        else:
            t0 = time.perf_counter()
            probs = model.probabilities(audio)[:n]
            took["Nemotron"] = time.perf_counter() - t0
            saved.parent.mkdir(exist_ok=True)
            np.save(saved, probs.astype(np.float16))
            saved.with_suffix(".json").write_text(json.dumps({"seconds": round(took["Nemotron"], 1)}))
        if len(probs) < n:
            probs = np.pad(probs, ((0, n - len(probs)), (0, 0)))
        for mode, count in (("given", len(people)), ("estimated", None)):
            heard = probs > 0.5
            talk = heard.sum(0)
            keep = [int(s) for s in np.argsort(-talk, kind="stable") if talk[s]][:count]
            systems = {name: (speakers.timeline(*prints[name], count), took[name]) for name in voiceprints}
            systems["Nemotron, as the app"] = (speakers.activity_timeline(probs, count), took["Nemotron"])
            systems["Nemotron, overlap included"] = (heard[:, keep] if keep else heard[:, :0], took["Nemotron"])
            for system, (out, secs) in systems.items():
                hyp = out if isinstance(out, np.ndarray) else timeline_frames(out, n)
                r = {**der(ref, hyp), **overlap(ref, hyp)}
                if not isinstance(out, np.ndarray):  # one speaker at a time: label the speech as words are
                    r.update(wrong_on_speech(ref, labels_at(out, n)))
                found = int(hyp.any(0).sum())
                rows.append({"meeting": path.stem, "system": system, "mode": mode, "people": len(people),
                             "found": found, "seconds": round(secs, 1), "audio_s": round(len(audio) / speakers.SR, 1),
                             **r})
                print(f"{path.stem} {system:28s} {mode:9s} DER {(r['missed'] + r['false_alarm'] + r['confusion']) / r['total']:6.1%}"
                      f"  (missed {r['missed'] / r['total']:.1%}, false alarm {r['false_alarm'] / r['total']:.1%},"
                      f" confusion {r['confusion'] / r['total']:.1%})  speakers {found}/{len(people)}  {secs:.0f} s",
                      flush=True)
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "ami-diarization.json").write_text(json.dumps(rows, indent=1))
    once = [r for r in rows if r["system"] == SYSTEMS[0] and r["mode"] == "given"]
    hours = sum(r["audio_s"] for r in once) / 3600
    speech = sum(r["total"] for r in once)
    print(f"\n{len(meetings)} meetings, {hours:.1f} h; two or more people talk in "
          f"{sum(r['overlap'] for r in once) / max(1, sum(r['total'] - r['overlap_speaker'] + r['overlap'] for r in once)):.1%} "
          f"of the speech, {sum(r['overlap_speaker'] for r in once) / max(1, speech):.1%} of the speaker time\n")
    print("| Speaker labels | wrong speaker on speech, count given | estimated | DER, count given | DER, count estimated "
          "| missed / false alarm / confusion (estimated) | missed in overlap (estimated) | count estimated right "
          "| seconds per 10 minutes |")
    print("|---|---|---|---|---|---|---|---|---|")
    for system in SYSTEMS:
        cells = []
        for mode in ("given", "estimated"):
            rs = [r for r in rows if r["system"] == system and r["mode"] == mode]
            if not rs:
                break
            cells.append(sum(r["missed"] + r["false_alarm"] + r["confusion"] for r in rs) / sum(r["total"] for r in rs))
        if len(cells) < 2:
            continue
        rs = [r for r in rows if r["system"] == system and r["mode"] == "estimated"]
        total = sum(r["total"] for r in rs)
        parts = " / ".join(f"{sum(r[k] for r in rs) / total:.1%}" for k in ("missed", "false_alarm", "confusion"))
        missed_ov = sum(r["overlap_missed"] for r in rs) / max(1, sum(r["overlap_speaker"] for r in rs))
        right = sum(r["found"] == r["people"] for r in rs)
        speed = sum(r["seconds"] for r in rs) / sum(r["audio_s"] for r in rs) * 600
        wrong = []
        for mode in ("given", "estimated"):
            ws = [r for r in rows if r["system"] == system and r["mode"] == mode and "speech" in r]
            wrong.append(f"{sum(r['wrong_on_speech'] for r in ws) / sum(r['speech'] for r in ws):.1%}" if ws else "-")
        print(f"| {system} | {wrong[0]} | {wrong[1]} | {cells[0]:.1%} | {cells[1]:.1%} | {parts} | {missed_ov:.1%} "
              f"| {right} of {len(rs)} | {speed:.0f} |")
    rs = [r for r in rows if r["system"] == "Nemotron, overlap included" and r["mode"] == "estimated"]
    if rs:
        heard, right = sum(r["heard_overlap"] for r in rs), sum(r["heard_overlap_right"] for r in rs)
        print(f"\nNemotron's overlap: precision {right / max(1, heard):.1%}, recall "
              f"{right / max(1, sum(r['overlap'] for r in rs)):.1%} (count estimated).")


if __name__ == "__main__":
    with threadpool_limits(limits=4, user_api="blas"):  # as the app runs the speaker step (speakers.diarize)
        main()
