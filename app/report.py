"""How a transcript was made: what each job records about itself, and how it is shown.

A job records the recording in the prepare step (job["source"], from sysinfo.recording) and the rest
when it starts running (run_facts). details() gathers everything known about a job into four groups,
recording, model, run and computer, with raw values; the API and the JSON export give it as it is.
groups() turns it into labelled lines for the job page and the Markdown export, and summary() into the
few lines at the top of the text and WebVTT exports. Older jobs lack some fields; their lines are left out.
"""
import re
from pathlib import Path

import sysinfo

from . import __version__

LANGUAGES = {"ar": "Arabic + English", "en": "English", "auto": "Auto-detect"}
ENGINES = {"whisper": "Whisper (faster-whisper)", "cohere": "Cohere (transcribe.cpp)", "llama": "llama-server"}
BACKENDS = {"cuda": "CUDA", "vulkan": "Vulkan"}
POWER = {"performance": "Performance", "balanced": "Balanced", "power-saver": "Power-saver"}
CONTAINERS = {"mov,mp4,m4a,3gp,3g2,mj2": "MP4 (QuickTime)", "matroska,webm": "Matroska / WebM", "wav": "WAV",
              "flac": "FLAC", "mp3": "MP3", "ogg": "Ogg"}
VIDEO = {"h264": "H.264", "hevc": "H.265", "vp8": "VP8", "vp9": "VP9", "av1": "AV1", "mpeg4": "MPEG-4"}


# ---------------------------------------------------------------------------------------------
# What a job records
# ---------------------------------------------------------------------------------------------

def model_file(cfg, model):
    """The file or folder a local model runs from (its name only), picked as engines.local_command does."""
    if model.get("engine") == "whisper":
        return Path(cfg.whisper_source(model) or model["whisper_model"]).name
    if model.get("engine") == "cohere":
        return Path(model["cohere_model"]).name
    return None


def run_facts(cfg, model, gpus=None):
    """What a job records as it starts running: the engine, the model's file (local) or its service and API
    model (hosted), the speed settings, the app's version, and this computer (gpus: the graphics cards the
    app's GPU check found)."""
    facts = {"engine": model.get("engine"), "app_version": __version__, "machine": sysinfo.machine(gpus)}
    try:
        if model.get("kind") == "local":
            facts.update(model_file=model_file(cfg, model), threads=cfg.setting("threads"),
                         gpu_setting=cfg.setting("device"))
        else:
            facts.update(service=model.get("service"), api_model=model.get("api_model"))
    except Exception:  # noqa: BLE001 — a detail is never a reason for a job to fail
        pass
    return facts


# ---------------------------------------------------------------------------------------------
# The details
# ---------------------------------------------------------------------------------------------

def _dict(value):
    return value if isinstance(value, dict) else {}


def _extension(name):
    return (Path(name).suffix.lstrip(".").lower() or None) if name else None


def details(job, lines=None, edited=None):
    """Everything known about how a job's transcript was made: {"recording", "model", "run", "computer"},
    each a dict of raw values, None where unknown. lines: the transcript, for the number of speakers found;
    edited: whether it was corrected by hand."""
    opts, source, machine = _dict(job.get("options")), _dict(job.get("source")), _dict(job.get("machine"))
    name = source.get("name") or job.get("source_name")
    found = None if lines is None else len({x.get("speaker") for x in lines if x.get("speaker") is not None})
    return {
        "recording": {**{k: source.get(k) for k in sysinfo.RECORDING_KEYS}, "name": name,
                      "extension": source.get("extension") or _extension(name),
                      "duration": source.get("duration") or job.get("audio_s"),  # older jobs: the converted audio
                      "link": job.get("source_url")},  # a YouTube video (app/youtube.py)
        "model": {"id": job.get("model"), "title": job.get("model_title"), "kind": job.get("kind"),
                  "engine": job.get("engine"), "file": job.get("model_file"), "service": job.get("service"),
                  "api_model": job.get("api_model"), "captions": _dict(job.get("captions")).get("name")},
        "run": {"status": job.get("status"), "device": job.get("device"), "power": job.get("power"),
                "threads": job.get("threads"), "gpu_setting": job.get("gpu_setting"),
                "created": job.get("created"), "queued": job.get("queued"), "started": job.get("started"),
                "finished": job.get("finished"), "seconds": job.get("seconds"), "rtf": job.get("rtf"),
                "peak_memory_mb": job.get("peak_rss_mb"), "language": opts.get("language"),
                "detected_language": job.get("detected_language"), "speakers": opts.get("speakers"),
                "speakers_found": found, "voiceprint_model": job.get("voiceprint_model"),
                "vocabulary": opts.get("prompt") or None, "edited": edited, "app_version": job.get("app_version"),
                "computer_recorded": bool(machine)},
        "computer": {k: machine.get(k) for k in sysinfo.MACHINE_KEYS},
    }


# ---------------------------------------------------------------------------------------------
# Wording
# ---------------------------------------------------------------------------------------------

def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _join(parts, sep=", "):
    return sep.join(str(x) for x in parts if x) or None


def clock(t):
    """1:02:05, or 02:05 under an hour (as transcript.clock)."""
    if _num(t) is None:
        return None
    t = int(t)
    h, m, s = t // 3600, t // 60 % 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def duration(t):
    """45 s, 19 min, 1 h 5 min (as human() in app.js)."""
    if _num(t) is None:
        return None
    t = round(t)
    if t < 60:
        return f"{t} s"
    m = round(t / 60)
    if m < 60:
        return f"{m} min"
    return f"{m // 60} h {m % 60} min" if m % 60 else f"{m // 60} h"


def size(n):
    """Bytes as 850 KB, 48 MB, 1.2 GB (as bytes() in app.js)."""
    if _num(n) is None:
        return None
    units, i = ["B", "KB", "MB", "GB"], 0
    while n >= 1000 and i < len(units) - 1:
        n, i = n / 1000, i + 1
    return f"{n:.{1 if i and n < 10 else 0}f} {units[i]}"


def memory(mb):
    if _num(mb) is None:
        return None
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{round(mb)} MB"


def when(iso):
    """2026-09-25T22:01:05+03:00 -> 2026-09-25 22:01, the clock time where it ran."""
    return str(iso)[:16].replace("T", " ") if iso else None


def gpu_name(name):
    """"Intel(R) Iris(R) Xe Graphics (ADL GT2)" -> "Intel Iris Xe Graphics" (as gpuName in app.js)."""
    name = re.sub(r"\s*\([^)]*\)\s*$", "", re.sub(r"\((R|TM)\)", "", str(name or ""), flags=re.I))
    return " ".join(name.split())


def device_label(device):
    """Where a local model ran, from the job's device (as deviceLabel in app.js): "Processor", or
    "Graphics card: Intel Iris Xe Graphics (Vulkan)"."""
    if not device:
        return None
    kind, colon, rest = str(device).partition(":")
    if not colon:
        return "Processor (the graphics card failed, so it finished there)" if kind.startswith("cpu (") else "Processor"
    name = gpu_name(re.sub(r"\s*\((int8_float16|float16)\)\s*$", "", rest))
    return f"Graphics card: {name} ({BACKENDS.get(kind, kind)})"


def codec_label(codec, name):
    """aac with "AAC (Advanced Audio Coding)" -> AAC; pcm_s16le -> PCM 16-bit."""
    m = re.fullmatch(r"pcm_([suf])(\d+)(le|be)?", str(codec or ""))
    if m:
        return f"PCM {m.group(2)}-bit" + (" float" if m.group(1) == "f" else "")
    return str(name or "").split(" (")[0].strip() or (str(codec).upper() if codec else None)


def audio_text(rec):
    """AAC, 44.1 kHz, stereo"""
    rate, channels = _num(rec.get("sample_rate")), _num(rec.get("channels"))
    return _join([codec_label(rec.get("codec"), rec.get("codec_name")), rate and f"{rate / 1000:g} kHz",
                  channels and {1: "mono", 2: "stereo"}.get(channels, f"{channels} channels")])


NOT_DETECTED = "Not detected"


def computer_name(pc):
    """LENOVO ThinkPad L14 Gen 3; the maker isn't repeated when the model starts with it (HP EliteBook 840)."""
    maker, model = pc.get("manufacturer"), pc.get("model")
    if maker and model and model.lower().startswith(maker.split()[0].lower()):
        return model
    return _join([maker, model], " ")


def processor(pc):
    """12th Gen Intel Core i5-1245U, 12 threads"""
    cpu = " ".join(re.sub(r"\((R|TM|C)\)", "", str(pc.get("cpu") or ""), flags=re.I).split())
    threads = _num(pc.get("threads"))
    return _join([cpu, threads and f"{threads} threads"])


def took(run):
    """19 min (0.31× real time)"""
    seconds, rtf = _num(run.get("seconds")), _num(run.get("rtf"))
    if seconds is None:
        return None
    return duration(seconds) + (f" ({rtf:.2f}× real time)" if rtf else "")


def ran_on(d):
    """The graphics card or processor for a local model, the service for a hosted one."""
    if d["model"].get("kind") == "hosted":
        return d["model"].get("service") or d["model"].get("title")
    return device_label(d["run"].get("device"))


def language(run):
    """Arabic + English (detected: ara)"""
    code, detected = run.get("language"), run.get("detected_language")
    return _join([LANGUAGES.get(code, code), detected and f"(detected: {detected})"], " ")


def speakers(run):
    """No labels; Auto-detect (3 found); 3 (2 found)"""
    setting, found = run.get("speakers"), _num(run.get("speakers_found"))
    if setting in (None, "", "none"):
        return "No labels" if setting == "none" else None
    text = "Auto-detect" if setting == "auto" else str(setting)
    return text if found is None else f"{text} ({found or 'none'} found)"


def threads_used(run, pc):
    """10 of 12"""
    used, available = _num(run.get("threads")), _num(pc.get("threads"))
    return (f"{used} of {available}" if available else str(used)) if used else None


def graphics(gpus):
    """The graphics cards the app found; None when it hadn't looked."""
    if not isinstance(gpus, list):
        return None
    return _join([gpu_name(g) for g in gpus]) or "None found"


def model_name(model):
    """whisper-medium code-switching; ElevenLabs Scribe (scribe_v2)"""
    name = model.get("title") or model.get("id")
    api = model.get("api_model") if model.get("kind") == "hosted" else None
    return f"{name} ({api})" if name and api else name


def groups(d):
    """The details as labelled lines for people: [{"title", "rows": [{"label", "value", "more"}]}], in the
    order Recording, Model, Run, Computer. The job page shows the "more" lines only under More details.
    Unknown values are left out, and so is a group with nothing known. The computer's are the exception: when
    the app recorded them but couldn't read one, its row says so ("Not detected") rather than guessing.
    Older jobs recorded no computer, so they have no Computer group."""
    rec, model, run, pc = d["recording"], d["model"], d["run"], d["computer"]
    hosted = model.get("kind") == "hosted"
    out = []

    def group(title, *rows):  # a row's value is text, or None/False when unknown
        rows = [{"label": label, "value": " ".join(str(value).split()), "more": more}
                for label, value, more in rows if value]
        if rows:
            out.append({"title": title, "rows": rows})

    video, version = rec.get("video"), run.get("app_version")
    group("Recording",
          ("File", rec.get("name"), False),
          ("YouTube", rec.get("link"), False),
          ("Audio", audio_text(rec), False),
          ("Container", CONTAINERS.get(rec.get("format")) or rec.get("format_name") or rec.get("format"), True),
          ("Bit rate", _num(rec.get("bit_rate")) and f"{round(rec['bit_rate'] / 1000)} kb/s", True),
          ("Size", size(rec.get("size")), True),
          ("Length", clock(rec.get("duration")), True),
          ("Video", video and VIDEO.get(video, str(video).upper()), True))
    group("Model",
          ("Name", model_name(model), False),
          ("Service", hosted and model.get("service"), False),
          ("Captions", model.get("captions"), False),
          ("ID", model.get("id"), True),
          ("Engine", not hosted and ENGINES.get(model.get("engine"), model.get("engine")), True),
          ("File", model.get("file"), True))
    group("Run",
          ("Ran on", ran_on(d), False),
          ("Took", took(run), False),
          ("Language", language(run), False),
          ("Speakers", speakers(run), False),
          ("Vocabulary", run.get("vocabulary"), False),
          ("Edited", run.get("edited") and "Yes, corrected by hand", False),
          ("Power mode", POWER.get(run.get("power"), run.get("power")), True),
          ("Threads", threads_used(run, pc), True),
          ("Use GPU", {"auto": "Yes", "cpu": "No"}.get(run.get("gpu_setting")), True),
          ("Added", when(run.get("created")), True),
          ("Queued", when(run.get("queued")), True),
          ("Started", when(run.get("started")), True),
          ("Finished", when(run.get("finished")), True),
          ("Peak memory", memory(run.get("peak_memory_mb")), True),
          ("Voiceprints", run.get("voiceprint_model"), True),
          ("App", version and f"Sedjem {version}", True))
    def found(value):  # a computer detail the app looked for but couldn't read
        return value or (NOT_DETECTED if run.get("computer_recorded") else None)

    group("Computer",  # for a hosted model this computer only sent the audio, so it all goes under More
          ("Computer", found(computer_name(pc)), hosted),
          ("Processor", found(processor(pc)), hosted),
          ("Memory", found(_num(pc.get("ram_gb")) and f"{pc['ram_gb']} GB"), True),
          ("Graphics", found(graphics(pc.get("gpus"))), True),
          ("System", found(_join([pc.get("os"), pc.get("arch")])), True))
    return out


def summary(d, title=None):
    """A few lines about how the transcript was made, for the top of a text export and a WebVTT note:
    title, recording, model and time taken, where it ran, and when (or the status, if it didn't finish)."""
    rec, model, run, pc = d["recording"], d["model"], d["run"], d["computer"]
    hosted, done = model.get("kind") == "hosted", run.get("status") == "done"
    version = run.get("app_version")
    rows = [("Title", title),
            ("Recording", _join([rec.get("name"), clock(rec.get("duration")), audio_text(rec)])),
            ("Model", _join([model_name(model), took(run) and f"took {took(run)}"])),
            ("Ran on", _join([None if hosted else computer_name(pc), ran_on(d)])),
            ("Status", None if done else run.get("status")),
            ("Made", done and _join([when(run.get("finished")), version and f"with Sedjem {version}"], " "))]
    return [f"{label}: {' '.join(str(value).split())}" for label, value in rows if value]


def markdown(d):
    """The Details section of the Markdown export."""
    out = ["## Details", ""]
    for g in groups(d):
        out += [f"### {g['title']}", "", *(f"- {r['label']}: {r['value']}" for r in g["rows"]), ""]
    return "\n".join(out)
