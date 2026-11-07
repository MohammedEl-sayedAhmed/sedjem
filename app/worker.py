"""Run transcribe.py for the app, in its own process.

From source the app starts `python -m app.worker <transcribe.py arguments>`; the desktop build
starts itself with `--transcribe <arguments>`, which lands here too. Output goes to the job's log
as UTF-8 (a windowed Windows build has no console, and Arabic text would not fit a code page).
With --speaker-timeline it runs only the voice step (speakers.py), for hosted models that give no
speaker labels.

`--convert-whisper SRC DST` converts a Whisper checkpoint in Transformers format for faster-whisper
(models added from Hugging Face, app/hub.py).
"""
import os
import sys


def setup_output():
    log = os.environ.get("SEDJEM_LOG")
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is None:  # no console: write straight to the log
            setattr(sys, name, open(log or os.devnull, "a", encoding="utf-8", buffering=1))
        else:
            try:
                stream.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
            except (AttributeError, ValueError):
                pass


def check_imports():
    """For the self-test: import everything a local run needs and report versions."""
    import importlib
    out = {}
    for mod in ("numpy", "av", "soundfile", "ctranslate2", "faster_whisper", "sherpa_onnx", "transcribe_cpp",
                "speakers", "transcribe", "yt_dlp", "yt_dlp_ejs"):
        try:
            m = importlib.import_module(mod)
            out[mod] = getattr(m, "__version__", "ok")
        except Exception as e:  # noqa: BLE001 — reported, not raised
            out[mod] = f"FAILED: {type(e).__name__}: {e}"
    print(repr(out))
    return 0 if not any(str(v).startswith("FAILED") for v in out.values()) else 1


def speaker_timeline(argv):
    """The voice step alone, for hosted models that give no speaker labels: prints the centres (in
    seconds) of the voiceprint windows and their speakers as one line of JSON."""
    import argparse
    import json

    import soundfile as sf

    import speakers
    ap = argparse.ArgumentParser(prog="app.worker --speaker-timeline")
    ap.add_argument("audio", help="the job's 16 kHz mono FLAC")
    ap.add_argument("--speakers", type=int, default=0, help="the number of speakers, or 0 to estimate it")
    ap.add_argument("--voiceprint-model", default=str(speakers.MODEL))
    ap.add_argument("--threads", type=int, default=4)
    args = ap.parse_args(argv)
    audio, rate = sf.read(args.audio, dtype="float32")
    if rate != speakers.SR or audio.ndim != 1:
        sys.exit(f"{args.audio}: expected 16 kHz mono audio")
    timeline = speakers.diarize(audio, args.speakers or None, args.threads, args.voiceprint_model)
    print(json.dumps({"centers": [round(float(c), 3) for c in timeline.centers],
                      "labels": [int(s) for s in timeline.labels]}))
    return 0


def convert_whisper(src, dst):
    """Convert a Whisper checkpoint in Transformers format (a downloaded folder) to a float16 CTranslate2
    model in dst, which appears only once complete. Needs transformers and torch (not in the desktop build)."""
    import importlib.util
    missing = [m for m in ("transformers", "torch", "ctranslate2") if importlib.util.find_spec(m) is None]
    if missing:
        print(f"converting needs {' and '.join(missing)}, which this installation doesn't have")
        return 1
    import shutil
    from pathlib import Path

    import ctranslate2.converters
    import transformers
    src, dst = Path(src), Path(dst)
    if not (src / "tokenizer.json").exists():  # faster-whisper reads tokenizer.json; made from vocab.json and merges.txt
        transformers.AutoTokenizer.from_pretrained(str(src)).backend_tokenizer.save(str(src / "tokenizer.json"))
    if not (src / "preprocessor_config.json").exists():  # the number of mel bins (128 for large-v3)
        mels = transformers.AutoConfig.from_pretrained(str(src)).num_mel_bins
        transformers.WhisperFeatureExtractor(feature_size=mels).save_pretrained(str(src))
    tmp = dst.with_name(dst.name + ".part")
    shutil.rmtree(tmp, ignore_errors=True)
    converter = ctranslate2.converters.TransformersConverter(
        str(src), copy_files=["tokenizer.json", "preprocessor_config.json"], load_as_float16=True)
    converter.convert(str(tmp), quantization="float16", force=True)
    shutil.rmtree(dst, ignore_errors=True)
    os.replace(tmp, dst)
    print(f"converted {src} to {dst}")
    return 0


def run(argv):
    setup_output()
    if argv[:1] == ["--check-imports"]:
        sys.exit(check_imports())
    if argv[:1] == ["--speaker-timeline"]:
        sys.exit(speaker_timeline(argv[1:]))
    if argv[:1] == ["--convert-whisper"] and len(argv) == 3:
        sys.exit(convert_whisper(argv[1], argv[2]))
    import transcribe
    sys.argv = ["transcribe.py", *argv]
    transcribe.main()


if __name__ == "__main__":
    run(sys.argv[1:])
