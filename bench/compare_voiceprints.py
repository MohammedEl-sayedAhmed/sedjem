#!/usr/bin/env python3
"""Compare speaker-embedding (voiceprint) models for speaker labels on the private meeting excerpts.

For each excerpt in data/private-meetings/manifest.jsonl it takes the words and timings that
transcribe.py saved (results/meetings/whisper/<id>.*.speakers.words.json), re-labels every word
with each voiceprint model, once with the true number of speakers and once letting the
clustering estimate it, and scores the labels against the reference with WDER
(bench/eval_meetings.py). The words stay the same, so only the speaker step differs.

Writes results/meetings/voiceprints.json (git-ignored) and prints a table with no meeting content.
Models missing from models/diarization/ are skipped with their download link (the README setup
fetches only the default, TitaNet-small).

Usage: .venv/bin/python bench/compare_voiceprints.py
"""
import json
import sys
import time
from pathlib import Path

from faster_whisper import decode_audio

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))
import speakers  # noqa: E402
from eval_meetings import evaluate, reference_turns  # noqa: E402
from score import normalize  # noqa: E402

DIAR = ROOT / "models" / "diarization"
RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
MODELS = {
    "WeSpeaker ResNet34": "wespeaker_en_voxceleb_resnet34_LM.onnx",
    "WeSpeaker ResNet152": "wespeaker_en_voxceleb_resnet152_LM.onnx",
    "TitaNet-large": "nemo_en_titanet_large.onnx",
    "TitaNet-small": "nemo_en_titanet_small.onnx",
    "3D-Speaker CAM++": "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx",
}


def available_models():
    models = {}
    for name, file in MODELS.items():
        if (DIAR / file).exists():
            models[name + (" (default)" if file == speakers.MODEL.name else "")] = file
        else:
            print(f"{name}: {DIAR / file} not found, skipped (download: {RELEASE}{file})", file=sys.stderr)
    if not models:
        sys.exit("no voiceprint models found")
    return models


def main():
    items = [json.loads(line) for line in open(ROOT / "data" / "private-meetings" / "manifest.jsonl")]
    models = available_models()
    rows = []
    for item in items:
        found = sorted((ROOT / "results" / "meetings" / "whisper").glob(f"{item['id']}.*.speakers.words.json"))
        if not found:
            print(f"{item['id']}: no whisper words yet, skipped", file=sys.stderr)
            continue
        words = json.loads(found[0].read_text())
        audio = decode_audio(str(ROOT / item["wav16k"]), sampling_rate=speakers.SR)
        turns = reference_turns(item["reference"], speakers=item["speakers"])
        for name, file in models.items():
            t0 = time.time()
            prints = speakers.voiceprints(audio, 4, DIAR / file)
            took = time.time() - t0
            for mode, n in (("given", item["n_speakers"]), ("estimated", None)):
                tl = speakers.timeline(*prints, n)
                hyp = [(t, f"spk{tl.speaker_at((s + e) / 2)}") for s, e, w, _ in words for t in normalize(w).split()]
                r = evaluate(turns, hyp, prefix=True)
                rows.append({"meeting": item["id"], "model": name, "mode": mode, "wder": r["wder"],
                             "true_speakers": item["n_speakers"], "found_speakers": len(set(tl.labels.tolist())),
                             "voiceprint_s": round(took, 1), "aligned_words": r["aligned_words"]})
                print(f"{item['id']} {name:30s} {mode:9s} WDER {r['wder']:.1%}  speakers "
                      f"{len(set(tl.labels.tolist()))}/{item['n_speakers']}  ({took:.0f} s)", flush=True)
    out = ROOT / "results" / "meetings" / "voiceprints.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=1))
    print("\n| Voiceprint model | WDER, speaker count given | WDER, count estimated | count estimated right |")
    print("|---|---|---|---|")
    for name in models:
        g = [r for r in rows if r["model"] == name and r["mode"] == "given"]
        e = [r for r in rows if r["model"] == name and r["mode"] == "estimated"]
        if not g:
            continue
        wavg = lambda rs: sum(r["wder"] * r["aligned_words"] for r in rs) / sum(r["aligned_words"] for r in rs)
        right = sum(r["found_speakers"] == r["true_speakers"] for r in e)
        print(f"| {name} | {wavg(g):.1%} | {wavg(e):.1%} | {right}/{len(e)} |")


if __name__ == "__main__":
    main()
