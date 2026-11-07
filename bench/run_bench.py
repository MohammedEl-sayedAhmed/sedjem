#!/usr/bin/env python3
"""Run one engine over a test-set manifest and save the hypotheses with timings.

By default each clip goes through the same path as transcribe.py without --speakers: Silero
VAD splits it into chunks of at most 25 s, each chunk (+0.5 s of silence) goes to the engine,
and the texts are joined. --mode clip passes the whole clip to the current engine instead.
The committed *-clip results come from the first round (before 2026-09-25 05:00) and were made
by older code (Whisper decoded without word timestamps or gap-fill; prompt runs were named
-prompt-clip). They are kept for reference; the settings check below refuses to resume them.

Usage:
  .venv/bin/python bench/run_bench.py <set> <audio> --engine whisper|llama|cohere [--language ar|auto]
    set:   perle | arzen | mixat | meetings   (data/<set>/manifest.jsonl)
    audio: wav16k | phone | g711
The llama engine needs bench/serve.sh running. As in transcribe.py, Whisper large-v3 gets the
Egyptian style hint unless --prompt is given (--prompt '' turns it off).

Writes results/<set>.<audio>.<model>.<config>.jsonl plus a .meta.json with every setting;
<config> is the language, then -p<hash> for a prompt and -clip for --mode clip. Sets other than
the public ones (perle, arzen, mixat) are private recordings: their output goes to the
git-ignored results/meetings/bench/ instead. Clips already in the file are skipped, but only if
the recorded settings match.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from score import PUBLIC_SETS  # noqa: E402
from transcribe import COHERE_MODEL, DEFAULT_WHISPER, ENGINES, STYLE_PROMPT, TAIL, split_at_pauses  # noqa: E402

AUDAR_INSTRUCTION = "فرّغ الكلام العربي التالي."
LABELS = {STYLE_PROMPT: "hint", AUDAR_INSTRUCTION: "official instruction"}


def git_state():
    rev = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "transcribe.py", "speakers.py",
                            "bench"], capture_output=True, text=True).stdout.strip()
    return rev + ("-dirty" if dirty else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("set")
    ap.add_argument("audio")
    ap.add_argument("--engine", choices=list(ENGINES), required=True)
    ap.add_argument("--language", default="ar", help="ar, en or auto")
    ap.add_argument("--prompt", help="style hint passed to the engine (default: as in transcribe.py)")
    ap.add_argument("--mode", choices=["pipeline", "clip"], default="pipeline")
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--whisper-model", default=DEFAULT_WHISPER)
    ap.add_argument("--cohere-model", default=COHERE_MODEL)
    ap.add_argument("--threads", type=int, default=10)
    ap.add_argument("--device", choices=["cpu", "gpu"], default="cpu",
                    help="cpu (default, as the published results) or gpu (see transcribe.py --device)")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    args.cuda_libs = None
    if args.engine == "cohere":
        if args.prompt:
            ap.error("--prompt has no effect with --engine cohere")
        args.language = "ar"  # the Cohere engine uses Arabic for 'auto' too

    engine = ENGINES[args.engine](args)
    prompt = getattr(engine, "prompt", None) or getattr(engine, "context", None) or ""  # what the engine uses
    config = (args.language + (f"-p{hashlib.sha1(prompt.encode()).hexdigest()[:6]}" if prompt else "")
              + ("-clip" if args.mode == "clip" else "") + ("-gpu" if args.device == "gpu" else ""))
    out_dir = ROOT / "results" if args.set in PUBLIC_SETS else ROOT / "results" / "meetings" / "bench"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.set}.{args.audio}.{engine.name}.{config}.jsonl"
    meta = {"engine": args.engine, "model": engine.name, "language": args.language, "prompt": prompt or None,
            "mode": args.mode, "threads": args.threads}
    if args.device == "gpu":  # older result files have no device: they ran on the CPU
        meta["device"] = getattr(engine, "device", "gpu")
    if prompt:
        meta["label"] = ({"ar": "Arabic forced", "auto": "auto-detect"}.get(args.language, args.language)
                         + " + " + LABELS.get(prompt, "custom prompt"))
    meta_path = out_path.with_suffix(".meta.json")
    old = json.loads(meta_path.read_text()) if out_path.exists() and meta_path.exists() else None
    if old:
        diff = {k: (old.get(k), v) for k, v in meta.items() if old.get(k) != v}
        if diff:
            sys.exit(f"{out_path.name} was made with other settings {diff}; move it away to start over")

    done = set()
    if out_path.exists():
        for line in open(out_path):
            try:
                done.add(json.loads(line)["id"])
            except (json.JSONDecodeError, KeyError):  # a line cut off by an interrupted run
                pass
    items = [json.loads(line) for line in open(ROOT / "data" / args.set / "manifest.jsonl")][: args.limit]
    todo = [item for item in items if item["id"] not in done]
    if not todo:
        print(f"{out_path.name}: all {len(items)} clips already done")
        return
    code = git_state()
    if old and old.get("code"):  # resuming: keep the revision(s) that made the earlier clips
        code = old["code"] if code in old["code"].split(" + ") else f"{old['code']} + {code}"
    meta["code"] = code
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    with open(out_path, "a") as out:
        for item in todo:
            audio, sr = sf.read(ROOT / item[args.audio], dtype="float32")
            t0 = time.time()
            if args.mode == "clip":
                hyp = engine(audio)
            else:
                speech = audio[: len(audio) - len(TAIL)]
                hyp = " ".join(t for t in (engine(np.concatenate([speech[a:b], TAIL]))
                                           for a, b in split_at_pauses(speech)) if t)
            took = time.time() - t0
            out.write(json.dumps({"id": item["id"], "hyp": hyp, "seconds": round(took, 2),
                                  "audio_s": round(len(audio) / sr, 2)}, ensure_ascii=False) + "\n")
            out.flush()
            print(f"{item['id']}  {took:5.1f}s  {hyp[:90]}", flush=True)


if __name__ == "__main__":
    main()
