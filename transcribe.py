#!/usr/bin/env python3
"""Transcribe a meeting recording entirely on this machine.

The audio is split at pauses (Silero VAD) into chunks of at most 25 s, each
chunk is transcribed by the chosen engine, and the transcript is printed and
saved with timestamps to results/poc/.

With --speakers, speakers are told apart by voice (see speakers.py: a
voiceprint every 0.75 s, grouped by spectral clustering) and each line is
labelled Speaker 1, Speaker 2, ... in order of first appearance. Whisper
gives word timestamps, so every word gets the speaker whose voice is active
at that moment; the llama and cohere engines have no word timestamps and are
transcribed one speaker turn at a time, or, with --align, forced-aligned so that
their words get times too (align.py, experimental).

Engines:
  whisper  Whisper through faster-whisper (int8 on CPU): the code-switching fine-tune
           whisper-medium-arabic-codeswitched by default, or --whisper-model large-v3
  llama    a Qwen3-ASR-family GGUF (R2T2, base Qwen3-ASR, Audar-ASR) served by
           llama-server on 127.0.0.1: bench/serve.sh r2t2|qwen3asr|audar 8081
  cohere   Cohere Transcribe Arabic (GGUF) run in process by transcribe.cpp
  gguf     the same for any GGUF speech model transcribe.cpp runs (Parakeet, Canary, Whisper, ...),
           given with --cohere-model; the app uses it for models added from Hugging Face

Devices (--device auto, the default): Cohere runs on a GPU through transcribe.cpp (Vulkan, so
NVIDIA, AMD and Intel graphics all work), preferring a discrete GPU; Whisper runs on an NVIDIA GPU
through CTranslate2's CUDA support once NVIDIA's cuBLAS is available (--cuda-libs, or the pip
package nvidia-cublas-cu12). Anything that fails on the GPU falls back to the CPU. --device cpu
never touches the GPU; --device gpu fails instead of falling back. --gpu-info prints what is found.

Usage:
  .venv/bin/python transcribe.py path/to/call.wav
  .venv/bin/python transcribe.py meeting.wav --speakers 2
  .venv/bin/python transcribe.py meeting.wav --engine llama --port 8081
  .venv/bin/python transcribe.py meeting.wav --engine cohere --speakers 3 \
      --align models/mms-300m-1130-forced-aligner
"""
import argparse
import base64
import io
import json
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import requests
import soundfile as sf
from faster_whisper import WhisperModel, decode_audio
from faster_whisper.vad import VadOptions, get_speech_timestamps

import speakers
import sysinfo  # the computer, the input file and the peak memory, for the .meta.json

SR = 16000
MAX_CHUNK_S = 25
MAX_LINE_S = 30
TAIL = np.zeros(SR // 2, dtype=np.float32)  # R2T2 drops the last words without trailing silence
LANGUAGE_NAMES = {"ar": "Arabic", "en": "English"}
# An Egyptian sentence with English terms in Latin script. As Whisper large-v3's initial prompt it
# keeps Whisper in dialect (instead of rewriting into formal Arabic) and writes English terms in
# English: WER 47% -> 34% on ArzEn, 40% -> 22% on Perle. The code-switching fine-tune doesn't need it.
STYLE_PROMPT = "يعني احنا كنا بنتكلم عن ال project بتاعنا و ال deadline و ال meeting اللي جاي مع ال team."


def gets_style_hint(name):
    """Whether a Whisper model gets STYLE_PROMPT by default: large-v3, where it was measured (39.7% to 20.8% WER on
    Perle), in faster-whisper form ("…/large-v3") or as a GGUF file ("whisper-large-v3-Q8_0.gguf"); not turbo."""
    return re.search(r"large-v3(?![-_.]?turbo)", str(name).lower()) is not None
MODELS = Path(__file__).resolve().parent / "models"
COHERE_MODEL = str(MODELS / "cohere-transcribe-arabic-07-2026-gguf" / "cohere-transcribe-arabic-07-2026-Q4_K_M.gguf")
# Default: keeps English terms in English best and was the most complete on the test call (Cohere
# makes fewer errors on Arabic words). Falls back to large-v3 if not downloaded.
WHISPER_CS = MODELS / "whisper-medium-arabic-codeswitched-ct2"
DEFAULT_WHISPER = str(WHISPER_CS) if WHISPER_CS.exists() else "large-v3"
# NVIDIA's cuBLAS (and the NVRTC it may use) for Whisper on CUDA: where the app puts them, in load order
CUDA_LIBS = MODELS / "nvidia-cuda"
CUDA_NAMES = {"win32": ["nvrtc64_120_0.dll", "cublasLt64_12.dll", "cublas64_12.dll"],
              "linux": ["libnvrtc.so.12", "libcublasLt.so.12", "libcublas.so.12"]}


def split_at_pauses(audio):
    """Return [start, end] sample ranges: VAD speech segments merged up to MAX_CHUNK_S."""
    vad = VadOptions(min_silence_duration_ms=500, speech_pad_ms=200, max_speech_duration_s=MAX_CHUNK_S)
    chunks = []
    for s in get_speech_timestamps(audio, vad):
        if chunks and s["end"] - chunks[-1][0] <= MAX_CHUNK_S * SR:
            chunks[-1][1] = s["end"]
        else:
            chunks.append([s["start"], s["end"]])
    return chunks


def snap_to_pause(audio, t, radius=int(0.3 * SR), frame=SR // 50):
    """Move sample index t to the quietest 20 ms frame within +-radius."""
    lo, hi = max(0, t - radius), min(len(audio), t + radius)
    if hi - lo < 2 * frame:
        return t
    energy = [float(np.mean(audio[k:k + frame] ** 2)) for k in range(lo, hi - frame + 1, frame)]
    return lo + int(np.argmin(energy)) * frame + frame // 2


def speaker_pieces(audio, a, b, timeline, min_len=int(0.3 * SR)):
    """Split chunk [a, b) where the speaker changes; pieces shorter than min_len join a neighbour."""
    cuts = sorted({min(max(snap_to_pause(audio, int(t * SR)), a + 1), b - 1) for t in timeline.changes(a / SR, b / SR)})
    pieces = []
    for x, y in zip([a] + cuts, cuts + [b]):
        if pieces and (y - x < min_len or pieces[-1][1] - pieces[-1][0] < min_len):
            pieces[-1][1] = y
        else:
            pieces.append([x, y])
    return pieces


def speaker_lines(engine, audio, timeline, on_chunk=None):
    """Transcribe and attribute the text to speakers. Engines with word timestamps (Whisper)
    get each word labelled with the voice active at that moment; the others transcribe each
    pause-delimited chunk in pieces cut where the speaker changes, so no speech is skipped.
    Returns (lines, words); words is [start, end, word, speaker] per word, or [] for engines
    without word timestamps. on_chunk(i, n, lines) is called after every chunk."""
    lines, words = [], []

    def add(start, end, speaker, text):
        last = lines[-1] if lines else None
        if last and last["speaker"] == speaker and start - last["end"] < 1.5 and end - last["start"] < MAX_LINE_S:
            last["end"], last["text"] = round(end, 2), f"{last['text']} {text}"
        else:
            lines.append({"start": round(start, 2), "end": round(end, 2), "speaker": speaker, "text": text})

    chunks = split_at_pauses(audio)
    for i, (a, b) in enumerate(chunks, 1):
        if hasattr(engine, "words"):
            chunk = np.concatenate([audio[a:b], TAIL])
            if isinstance(engine, Aligned) and not timeline.changes(a / SR, b / SR):
                # one voice throughout: every word gets that speaker, so timing the words isn't needed
                items = engine.aligner.spread(engine(chunk), (b - a) / SR)
                engine.skipped += 1
            else:
                items = engine.words(chunk)
            for ws, we, word in items:
                s, e = a / SR + ws, a / SR + we
                speaker = timeline.speaker_over(s, e)
                add(s, e, speaker, word)
                words.append([round(s, 2), round(e, 2), word, speaker])
        else:
            for x, y in speaker_pieces(audio, a, b, timeline):
                text = engine(np.concatenate([audio[x:y], TAIL]))
                if text:
                    add(x / SR, y / SR, timeline.speaker_over(x / SR, y / SR), text)
        if on_chunk:
            on_chunk(i, len(chunks), lines)
    return lines, words


# ---------------------------------------------------------------------------------------------
# GPUs. transcribe.cpp (Cohere) reaches any GPU through Vulkan; CTranslate2 (Whisper) only NVIDIA
# GPUs through CUDA, and it needs NVIDIA's cuBLAS, which isn't bundled (the app downloads it).
# ---------------------------------------------------------------------------------------------

def gpu_devices():
    """The GPUs transcribe.cpp can run on, best first: discrete before integrated, then the most memory."""
    try:
        import transcribe_cpp
        devices = transcribe_cpp.backends()
    except Exception:  # noqa: BLE001 — no GPU runtime at all
        return []
    gpus = [d for d in devices if d.device_type in ("gpu", "igpu") and d.kind in ("vulkan", "cuda", "rocm", "metal")]
    return sorted(gpus, key=lambda d: (d.device_type != "gpu", -(d.memory_total or 0)))


def cuda_device_count():
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count()
    except Exception:  # noqa: BLE001 — no NVIDIA driver
        return 0


_cuda_libs = {}


def load_cuda_libs(folder=None):
    """Load NVIDIA's cuBLAS (and NVRTC, if there) by full path, so that CTranslate2's later load by name
    finds it: from `folder`, the app's download folder, or the pip package nvidia-cublas-cu12. Returns
    the folder used ("system" if cuBLAS was already findable), or None."""
    import ctypes

    if folder in _cuda_libs:
        return _cuda_libs[folder]
    names = CUDA_NAMES.get("win32" if os.name == "nt" else sys.platform)
    if not names:
        return None
    folders = [Path(folder)] if folder else []
    folders.append(CUDA_LIBS)
    try:
        import nvidia.cublas  # noqa: F401 — the pip packages' namespace
        import nvidia
        for root in nvidia.__path__:
            folders += [Path(root) / pkg / sub for pkg in ("cublas", "cuda_nvrtc") for sub in ("bin", "lib")]
    except ImportError:
        pass
    found = None
    for d in folders:
        if not (d / names[-1]).is_file():
            continue
        if os.name == "nt":
            os.add_dll_directory(str(d))
            os.environ["PATH"] = f"{d}{os.pathsep}{os.environ.get('PATH', '')}"
        try:
            for name in names:
                lib = next((x / name for x in [d, *folders] if (x / name).is_file()), None)
                if lib is None and name == names[0]:
                    continue  # NVRTC is optional
                ctypes.CDLL(str(lib), mode=getattr(ctypes, "RTLD_GLOBAL", 0))
            found = str(d)
            break
        except OSError as e:
            print(f"could not load cuBLAS from {d}: {e}", file=sys.stderr, flush=True)
    if found is None:
        try:
            ctypes.CDLL(names[-1])
            found = "system"
        except OSError:
            pass
    _cuda_libs[folder] = found
    return found


def whisper_on_gpu(args):
    """(WhisperModel on an NVIDIA GPU, a description), after one second of silence ran on it, or (None, None)."""
    if cuda_device_count() < 1 or not load_cuda_libs(args.cuda_libs):
        return None, None
    for compute in ("int8_float16", "float16"):
        try:
            model = WhisperModel(args.whisper_model, device="cuda", compute_type=compute, local_files_only=True)
            segments, _ = model.transcribe(np.zeros(SR, dtype=np.float32), language="ar", beam_size=1,
                                           without_timestamps=True, max_new_tokens=4)
            list(segments)
            return model, f"cuda: {nvidia_gpu_name()} ({compute})"
        except Exception as e:  # noqa: BLE001 — anything here means: use the CPU
            print(f"Whisper can't use the NVIDIA GPU with {compute} ({type(e).__name__}: {e})", file=sys.stderr,
                  flush=True)
    return None, None


def nvidia_gpu_name():
    return next((d.description for d in gpu_devices() if "nvidia" in d.description.lower()), "NVIDIA GPU")


def gpu_info(cuda_libs=None):
    """What the engines can use here, for --gpu-info and the app."""
    cuda = cuda_device_count()
    return {"devices": [{"name": d.description, "kind": d.kind, "type": d.device_type, "memory": d.memory_total}
                        for d in gpu_devices()],
            "cuda_devices": cuda, "cuda_libs": load_cuda_libs(cuda_libs) if cuda else None}


class Whisper:
    def __init__(self, args):
        self.name = "whisper-" + Path(args.whisper_model).name
        self.source, self.threads = args.whisper_model, args.threads
        self.model, self.device = (None, "cpu") if args.device == "cpu" else whisper_on_gpu(args)
        if self.model is None:
            if args.device == "gpu":
                sys.exit("--device gpu: Whisper needs an NVIDIA GPU with CUDA and cuBLAS (see --gpu-info)")
            self.model, self.device = self.cpu_model(), "cpu"
        self.language = None if args.language == "auto" else args.language
        default_prompt = STYLE_PROMPT if gets_style_hint(Path(args.whisper_model).name) else None
        self.prompt = default_prompt if args.prompt is None else (args.prompt or None)

    def cpu_model(self):
        return WhisperModel(self.source, device="cpu", compute_type="int8", cpu_threads=self.threads,
                            local_files_only=True)

    def __call__(self, audio):
        return " ".join(w for _, _, w in self.words(audio))

    def words(self, audio):
        """(start, end, word) with times in seconds from the start of `audio` (under 30 s,
        ending with the TAIL silence)."""
        try:
            segments, _ = self.model.transcribe(audio, language=self.language, beam_size=5,
                                                initial_prompt=self.prompt, condition_on_previous_text=False,
                                                without_timestamps=True, word_timestamps=True)
            # Keep only the first decoding window. With word timestamps faster-whisper would go on to
            # decode from the last word's end; on a chunk under 30 s that remainder is silence, where
            # Whisper invents "شكراً لكم". Not advancing the generator also skips that extra work.
            s = next(iter(segments), None)
        except RuntimeError as e:  # e.g. the GPU ran out of memory: carry on on the CPU
            if self.device == "cpu":
                raise
            print(f"Whisper failed on the GPU ({e}); continuing on the CPU", file=sys.stderr, flush=True)
            self.model, self.device = self.cpu_model(), "cpu (after a GPU error)"
            return self.words(audio)
        words = [(w.start, w.end, w.word.strip()) for w in (s.words or [])] if s is not None and s.seek == 0 else []
        words = [w for w in words if w[2]]
        # Whisper sometimes stops early and skips the end of a chunk; transcribe what's left.
        # Only recurse when that makes progress (the remainder starts well after the beginning).
        speech_end = len(audio) / SR - len(TAIL) / SR
        if words and words[-1][1] > 0.5 and speech_end - words[-1][1] > 2:
            start = words[-1][1] - 0.2
            words += [(a + start, b + start, w) for a, b, w in self.words(audio[int(start * SR):])]
        return words


class Llama:
    """Qwen3-ASR-family model behind llama-server's OpenAI-compatible chat API."""

    def __init__(self, args):
        base = f"http://127.0.0.1:{args.port}"
        self.name = Path(requests.get(f"{base}/props", timeout=10).json()["model_path"]).stem
        self.url = f"{base}/v1/chat/completions"
        # "language X<asr_text>" as the start of the reply forces the language; omit it to auto-detect.
        lang = LANGUAGE_NAMES.get(args.language, args.language)
        self.prefix = "" if args.language == "auto" else f"language {lang}<asr_text>"
        self.context = args.prompt or ""  # Qwen3-ASR reads the system turn as context

    def __call__(self, audio):
        wav = io.BytesIO()
        sf.write(wav, audio, SR, format="WAV", subtype="PCM_16")
        content = [{"type": "input_audio",
                    "input_audio": {"data": base64.b64encode(wav.getvalue()).decode(), "format": "wav"}}]
        # Same layout as the official Qwen3-ASR prompt: a system turn with the context, then the audio.
        messages = [{"role": "system", "content": self.context}, {"role": "user", "content": content}]
        if self.prefix:
            messages.append({"role": "assistant", "content": self.prefix})
        r = requests.post(self.url, json={"messages": messages, "temperature": 0, "max_tokens": 512},
                          timeout=600)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"].removeprefix(self.prefix)
        return re.sub(r"^\s*language\s+\S+?\s*<asr_text>", "", text).strip()


class Cohere:
    """Cohere Transcribe Arabic GGUF run in process by transcribe.cpp (no word timestamps)."""

    def __init__(self, args):
        import transcribe_cpp

        self.path, self.threads = Path(args.cohere_model), args.threads
        self.name = self.path.stem
        self.language = "ar" if args.language == "auto" else args.language
        self.model = None
        if args.device != "cpu":
            for dev in gpu_devices():  # the best GPU first; each is checked on a second of silence
                try:
                    self.model = transcribe_cpp.Model(self.path, backend=dev.kind, device=dev)
                    self.session = self.model.session(n_threads=args.threads)
                    try:
                        self.run(np.zeros(SR, dtype=np.float32))
                    except transcribe_cpp.errors.OutputTruncated:
                        pass  # it ran; silence can make the decoder ramble
                    self.device = f"{dev.kind}: {dev.description}"
                    break
                except Exception as e:  # noqa: BLE001 — a broken driver must not stop the run
                    print(f"Cohere can't use {dev.description} ({type(e).__name__}: {e})", file=sys.stderr, flush=True)
                    self.model = None
            if self.model is None and args.device == "gpu":
                sys.exit("--device gpu: no GPU that transcribe.cpp can use (see --gpu-info)")
        if self.model is None:
            self.use_cpu("cpu")
        if args.language == "auto" and self.model.capabilities.supports_language_detect:
            self.language = None  # this family can tell the language itself (Cohere can't: it gets Arabic)
        # A Whisper GGUF takes an initial prompt, as faster-whisper does: large-v3 gets the Egyptian style
        # hint unless --prompt is given (the app puts the hint before the user's terms). Other families have none.
        self.prompt = None
        if getattr(self.model, "arch", None) == "whisper":
            default = STYLE_PROMPT if gets_style_hint(self.path.name) else None
            self.prompt = default if args.prompt is None else (args.prompt or None)
        elif args.prompt:
            print(f"note: --prompt has no effect with {getattr(self.model, 'arch', 'these')} models", file=sys.stderr)

    def run(self, audio):
        """session.run with the language; a family that doesn't take that option runs without one from then on."""
        import transcribe_cpp

        prompt = getattr(self, "prompt", None)  # None during the warm-up run, before it is set
        extra = {"family": transcribe_cpp.WhisperRunOptions(initial_prompt=prompt)} if prompt else {}
        try:
            return self.session.run(audio, language=self.language, **extra)
        except (transcribe_cpp.errors.UnsupportedRequest, transcribe_cpp.errors.InvalidArgument) as e:
            if self.language is None:
                raise
            print(f"{self.name} refused the language {self.language!r} ({e}); trying without one", file=sys.stderr,
                  flush=True)
            language, self.language = self.language, None
            try:
                return self.session.run(audio, language=None, **extra)
            except (transcribe_cpp.errors.UnsupportedRequest, transcribe_cpp.errors.InvalidArgument):
                self.language = language  # the language wasn't the problem
                raise

    def use_cpu(self, label):
        import transcribe_cpp

        self.model = transcribe_cpp.Model(self.path, backend="cpu")
        self.session = self.model.session(n_threads=self.threads)
        self.device = label

    def __call__(self, audio):
        import transcribe_cpp

        try:
            text = self.run(np.asarray(audio, dtype=np.float32)).text
        except transcribe_cpp.errors.OutputTruncated as e:
            # The decoder hit its length cap, usually stuck repeating itself. Transcribe the halves
            # separately; if the audio is already short, keep what it said before the loop.
            if len(audio) > 4 * SR:
                cut = quietest_point(audio)
                return f"{self(audio[:cut])} {self(audio[cut:])}".strip()
            partial = getattr(e, "partial_result", None)
            text = trim_loop(partial.text if partial else "")
        except transcribe_cpp.errors.TranscribeError as e:  # e.g. out of GPU memory: carry on on the CPU
            if self.device == "cpu" or isinstance(e, transcribe_cpp.errors.InvalidArgument):
                raise
            print(f"Cohere failed on the GPU ({type(e).__name__}: {e}); continuing on the CPU", file=sys.stderr,
                  flush=True)
            self.use_cpu("cpu (after a GPU error)")
            return self(audio)
        # On hesitant phone speech it adds notes such as (تأتأة) "stutter" or (غير مفهوم) "unclear".
        return re.sub(r"\s*\([^()]*\)", "", text).strip()


def quietest_point(audio, frame=SR // 10):
    """Sample index of the quietest 100 ms frame in the middle third of `audio`."""
    lo, hi = len(audio) // 3 // frame, 2 * len(audio) // 3 // frame
    energy = [float(np.mean(audio[i * frame:(i + 1) * frame] ** 2)) for i in range(lo, hi)]
    return (lo + int(np.argmin(energy))) * frame


def trim_loop(text, max_n=8, min_repeats=3):
    """Cut a trailing loop: an n-gram repeated min_repeats+ times at the end is kept once."""
    words = text.split()
    for n in range(1, max_n + 1):
        gram, k = words[-n:], 1
        while len(words) >= n * (k + 1) and words[-n * (k + 1):-n * k] == gram:
            k += 1
        if k >= min_repeats:
            return " ".join(words[:len(words) - n * (k - 1)])
    return text


class Aligned:
    """An engine without word timestamps (cohere, llama) plus forced alignment (align.py): the text of
    each chunk is aligned to its audio, so every word gets a time and, with --speakers, its own
    speaker, as with Whisper. The audio is no longer cut at speaker changes."""

    def __init__(self, engine, aligner):
        self.engine, self.aligner = engine, aligner
        self.name = f"{engine.name}-aligned"  # its own output files, next to an unaligned run
        self.skipped = 0  # chunks with a single voice, where no alignment was needed

    def __getattr__(self, name):  # anything else (prompt, context) is the engine's
        if name in ("engine", "aligner"):
            raise AttributeError(name)
        return getattr(self.engine, name)

    def __call__(self, audio):
        return self.engine(audio)

    def words(self, audio):
        text = self.engine(audio)
        return self.aligner.words(audio, text) if text else []


ENGINES = {"whisper": Whisper, "llama": Llama, "cohere": Cohere, "gguf": Cohere}


def stamp(seconds):
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


def line(x):
    who = "" if x["speaker"] is None else f"Speaker {x['speaker']}: "
    return f"[{stamp(x['start'])}-{stamp(x['end'])}] {who}{x['text']}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio", nargs="?")
    ap.add_argument("--engine", choices=list(ENGINES), default="whisper")
    ap.add_argument("--device", choices=["auto", "cpu", "gpu"], default="auto",
                    help="auto (default): a GPU when one works, else the CPU; cpu; gpu: fail rather than use the CPU")
    ap.add_argument("--cuda-libs", metavar="DIR", help="folder with NVIDIA's cuBLAS for Whisper on CUDA "
                                                       "(default: models/nvidia-cuda, or the nvidia-cublas-cu12 package)")
    ap.add_argument("--gpu-info", action="store_true", help="print the GPUs the engines can use, as JSON, and exit")
    ap.add_argument("--language", default="ar", help="ar, en or auto (default: ar; Cohere can't detect, so auto = ar)")
    ap.add_argument("--prompt", help="style/vocabulary hint: Whisper's initial prompt (default for large-v3: "
                                     "an Egyptian code-switched sentence; '' for none) or Qwen3-ASR's context")
    ap.add_argument("--port", type=int, default=8081, help="llama-server port")
    ap.add_argument("--whisper-model", default=DEFAULT_WHISPER,
                    help="faster-whisper model name or local CTranslate2 folder (default: the "
                         "Arabic-English code-switching fine-tune if downloaded, else large-v3)")
    ap.add_argument("--cohere-model", default=COHERE_MODEL,
                    help="Cohere Transcribe GGUF for --engine cohere, or any transcribe.cpp GGUF for --engine gguf")
    ap.add_argument("--speakers", type=int, metavar="N",
                    help="label speakers: the number of speakers, or 0 to estimate it")
    ap.add_argument("--voiceprint-model", default=str(speakers.MODEL),
                    help="speaker model for --speakers: a speaker-embedding ONNX file (default: TitaNet-small) "
                         "or the Nemotron-3-Diarization folder")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "results" / "poc"),
                    help="output folder (default: results/poc)")
    ap.add_argument("--threads", type=int, default=10)
    ap.add_argument("--progress-file", help="keep this JSON file updated with the current stage (used by the app)")
    ap.add_argument("--align", metavar="MODEL_DIR",
                    help="cohere/llama: forced-align the text to get word times (align.py), so with --speakers "
                         "each word gets its own speaker instead of the audio being cut at speaker changes")
    args = ap.parse_args()
    if args.gpu_info:
        print(json.dumps(gpu_info(args.cuda_libs), ensure_ascii=False))
        return
    if not args.audio:
        ap.error("the audio file is required")
    if args.speakers is not None and args.speakers < 0:
        ap.error("--speakers must be 0 (estimate) or the number of speakers")

    def report(stage, **extra):
        if args.progress_file:  # written whole and renamed, so a reader never sees half a file
            tmp = args.progress_file + ".tmp"
            try:
                Path(tmp).write_text(json.dumps({"stage": stage, **extra}), encoding="utf-8")
                os.replace(tmp, args.progress_file)
            except OSError:  # on Windows the app may be reading it right now; the next update will do
                pass

    report("decoding")
    audio = decode_audio(args.audio, sampling_rate=SR)
    if not len(audio):
        sys.exit(f"{args.audio}: no audio")
    report("loading", audio_s=round(len(audio) / SR, 2))
    engine = ENGINES[args.engine](args)
    if args.align and not hasattr(engine, "words"):
        import align
        engine = Aligned(engine, align.Aligner(args.align, threads=args.threads))
    print(f"{args.audio}: {len(audio) / SR / 60:.1f} min, engine {engine.name} on {engine.device}\n")
    t0 = time.time()
    timeline = None
    if args.speakers is not None:
        report("speakers", audio_s=round(len(audio) / SR, 2))
        timeline = speakers.diarize(audio, args.speakers or None, min(args.threads, 4), args.voiceprint_model)
        if not len(timeline.centers):
            print("not enough speech for voiceprints; transcribing without speaker labels", flush=True)
            timeline = None
        else:
            talk, sims = timeline.summary()
            print("voices: " + ", ".join(f"Speaker {s} talks {t:.0f} s" for s, t in talk.items())
                  + f" (voiceprint similarity between speakers: {', '.join(f'{x:.2f}' for x in sims) or '-'})",
                  flush=True)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{Path(args.audio).stem}.{engine.name}" + ("" if timeline is None else ".speakers")

    def save(lines, words=()):
        (out / f"{stem}.txt").write_text("".join(line(x) + "\n" for x in lines), encoding="utf-8")
        (out / f"{stem}.json").write_text(json.dumps(lines, ensure_ascii=False, indent=1), encoding="utf-8")
        if words:  # word-level timings, so speaker models can be compared without re-transcribing
            (out / f"{stem}.words.json").write_text(json.dumps(words, ensure_ascii=False), encoding="utf-8")

    def progress(i, n, lines, quiet=False):
        save(lines)  # a crash or kill hours in keeps everything transcribed so far
        report("transcribing", done=i, total=n, elapsed=round(time.time() - t0, 1), audio_s=round(len(audio) / SR, 2))
        if not quiet:
            print(f"  chunk {i}/{n} ({time.time() - t0:.0f} s)", flush=True)

    report("transcribing", done=0, total=None, elapsed=round(time.time() - t0, 1), audio_s=round(len(audio) / SR, 2))
    lines, words = [], []
    if timeline is None:
        chunks = split_at_pauses(audio)
        for i, (a, b) in enumerate(chunks, 1):
            text = engine(np.concatenate([audio[a:b], TAIL]))
            if text:
                lines.append({"start": round(a / SR, 2), "end": round(b / SR, 2), "speaker": None, "text": text})
                print(line(lines[-1]), flush=True)
            progress(i, len(chunks), lines, quiet=True)
    else:
        lines, words = speaker_lines(engine, audio, timeline, progress)
        print()
        for x in lines:
            print(line(x))
    took = time.time() - t0
    save(lines, words)
    (out / f"{stem}.meta.json").write_text(json.dumps({
        "audio": str(args.audio), "audio_s": round(len(audio) / SR, 2), "engine": engine.name,
        "language": args.language, "prompt": getattr(engine, "prompt", None) or getattr(engine, "context", None),
        "speakers": args.speakers, "voiceprint_model": Path(args.voiceprint_model).name if timeline else None,
        "aligner": engine.aligner.name if isinstance(engine, Aligned) else None,
        "align_failures": engine.aligner.failures if isinstance(engine, Aligned) else None,
        "align_skipped_chunks": engine.skipped if isinstance(engine, Aligned) else None,
        "device": getattr(engine, "device", "cpu"),
        "seconds": round(took, 1), "rtf": round(took / (len(audio) / SR), 3),
        "peak_rss_mb": sysinfo.peak_memory_mb(),
        "started": sysinfo.local_time(t0), "finished": sysinfo.local_time(),  # what "seconds" times (model loaded)
        "source": sysinfo.recording(args.audio), "machine": sysinfo.machine(),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{took:.0f} s to transcribe {len(audio) / SR:.0f} s of audio "
          f"({took / (len(audio) / SR):.2f}x real time). Saved {out / stem}.txt")


if __name__ == "__main__":
    main()
