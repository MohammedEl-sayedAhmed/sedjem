"""What this computer is, and what a recording is, for the details kept with every transcript.

machine() describes the computer: maker and model, operating system, processor, threads, memory, and
the graphics cards the caller found (the app passes its own GPU check; nothing is probed here).
recording() describes an audio or video file through PyAV: container, codec, sample rate, channels,
bit rate, size, length, and when it was made. Neither ever raises; whatever can't be found is None.
transcribe.py writes both into its .meta.json and the app keeps them with each job. Nothing here is
sent anywhere; the anonymous usage counts (app/usage.py) send only the processor's maker, the memory
and the number of threads, in broad ranges.
"""
import functools
import os
import platform
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

DMI = Path("/sys/class/dmi/id")  # Linux: the firmware's description of the computer
OS_RELEASE = Path("/etc/os-release")
KDE_ABOUT = Path("/etc/xdg/kcm-about-distrorc")  # the name KDE's About page shows: Kubuntu's os-release says Ubuntu
SESSIONS = (Path("/usr/share/wayland-sessions"), Path("/usr/share/xsessions"))
# XDG_CURRENT_DESKTOP entries ("ubuntu:GNOME", "KDE", "X-Cinnamon") and the desktops' own names
DESKTOPS = {"kde": "KDE Plasma", "gnome": "GNOME", "x-cinnamon": "Cinnamon", "cinnamon": "Cinnamon", "xfce": "Xfce",
            "mate": "MATE", "lxqt": "LXQt", "lxde": "LXDE", "budgie": "Budgie", "pantheon": "Pantheon", "unity": "Unity",
            "cosmic": "COSMIC", "deepin": "Deepin", "enlightenment": "Enlightenment", "sway": "Sway",
            "hyprland": "Hyprland", "i3": "i3"}
CPUINFO = Path("/proc/cpuinfo")
MACHINE_KEYS = ("manufacturer", "model", "os", "cpu", "threads", "ram_gb", "arch", "gpus")
RECORDING_KEYS = ("name", "extension", "format", "format_name", "codec", "codec_name", "sample_rate", "channels",
                  "bit_rate", "size", "duration", "video", "recorded")
# The tags that say when a recording was made, most trusted first: Apple's (with the local offset), the creation
# time of MP4, MOV, M4A and Matroska files (in UTC), and the date of WAV and MP3 files
MADE_TAGS = ("com.apple.quicktime.creationdate", "creation_time", "date")
FIRST_YEAR = 1990  # an earlier date is a clock that was never set (QuickTime counts from 1904, Unix from 1970)
# What firmware and Windows write when the maker left a field empty
PLACEHOLDERS = {"", "none", "n/a", "na", "not applicable", "not specified", "not available", "default string",
                "default", "oem", "o.e.m.", "system product name", "system version", "system manufacturer",
                "system name", "unknown", "invalid", "undefined", "0", "1.0", "x.x", "type1productconfigid"}


def _safe(fn, *args):
    """fn(*args), or None if it fails in any way."""
    try:
        return fn(*args)
    except Exception:  # noqa: BLE001 — a detail that can't be read is just unknown
        return None


def _clean(value):
    """A firmware or registry string with its spacing tidied, or None for an empty or placeholder value."""
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return None if text.lower() in PLACEHOLDERS or text.lower().startswith("to be filled") else text


def model_name(vendor, product, version):
    """The computer's model. Lenovo keeps it in the version field (the product name is a type number such
    as 21C2S3G307); other makers put it in the product name (HUAWEI MCLF-X)."""
    vendor, product, version = _clean(vendor), _clean(product), _clean(version)
    if vendor and vendor.lower().startswith("lenovo") and version:
        return version
    return product or version


def windows_name(product, version, build, ubr):
    """"Windows 11 Pro 24H2 (build 26100.4061)". Windows 11 still says Windows 10 in its ProductName, so
    the build number decides (Windows 11 starts at build 22000)."""
    name = _clean(product) or "Windows"
    if str(build or "").isdigit() and int(build) >= 22000:
        name = name.replace("Windows 10", "Windows 11")
    name = " ".join(x for x in (name, _clean(version)) if x)
    if build:
        name += f" (build {build}{'' if ubr is None else f'.{ubr}'})"
    return name


# ---------------------------------------------------------------------------------------------
# Linux
# ---------------------------------------------------------------------------------------------

def _read(path):
    return Path(path).read_text(encoding="utf-8", errors="replace").strip()


def _linux_desktop():
    """The desktop of this session, from XDG_CURRENT_DESKTOP, with Plasma's version from its session file
    ("KDE Plasma 5.27"). None outside a desktop session, e.g. over SSH."""
    names = [DESKTOPS[e.strip().lower()] for e in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":")
             if e.strip().lower() in DESKTOPS]
    if not names:
        return None
    if names[0] != "KDE Plasma":
        return names[0]
    for folder in SESSIONS:
        for f in sorted(folder.glob("plasma*.desktop")):
            m = re.search(r"^X-KDE-PluginInfo-Version=(\d+\.\d+)", _safe(_read, f) or "", re.M)
            if m:
                return f"KDE Plasma {m.group(1)}"
    major = os.environ.get("KDE_SESSION_VERSION", "")
    return f"KDE Plasma {major}" if major.isdigit() else "KDE Plasma"


def _linux_os():
    fields = dict(re.findall(r"""^(\w+)=["']?(.*?)["']?[ \t]*$""", _safe(_read, OS_RELEASE) or "", re.M))
    name = fields.get("PRETTY_NAME") or " ".join(x for x in (fields.get("NAME"), fields.get("VERSION")) if x)
    desktop = _safe(_linux_desktop)
    # Kubuntu and the other KDE editions keep their base's os-release ("Ubuntu 24.04.4 LTS"); KDE's About page
    # takes its name from its own file, so use that name too: "Kubuntu 24.04.4 LTS"
    about = re.search(r"^Name=(.+)$", _safe(_read, KDE_ABOUT) or "", re.M)
    base = fields.get("NAME")
    if about and (desktop or "KDE").startswith("KDE") and base and name.startswith(base) \
            and about.group(1).strip().lower() not in name.lower():
        name = about.group(1).strip() + name[len(base):]
    if name and desktop:
        name = f"{name}, {desktop}"
    kernel = _safe(platform.release)
    if not name:
        return f"Linux {kernel}" if kernel else None
    return f"{name} (Linux {kernel})" if kernel else name


def _linux_cpu():
    text = _safe(_read, CPUINFO) or ""
    for key in ("model name", "Hardware", "cpu model", "Processor"):  # x86 first, then what ARM and others use
        m = re.search(rf"^{key}\s*:[ \t]*(.+)$", text, re.M)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return None


def _linux():
    vendor, product, version = (_safe(_read, DMI / f) for f in ("sys_vendor", "product_name", "product_version"))
    return {"manufacturer": _clean(vendor), "model": model_name(vendor, product, version), "os": _safe(_linux_os),
            "cpu": _safe(_linux_cpu), "ram": _safe(lambda: os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))}


# ---------------------------------------------------------------------------------------------
# Windows: the registry, and GlobalMemoryStatusEx for the memory
# ---------------------------------------------------------------------------------------------

def _registry(path, *names):
    """Values under HKEY_LOCAL_MACHINE\\path, None for each one that is missing."""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
    except (ImportError, OSError):
        return [None] * len(names)
    with key:
        return [_safe(lambda n: winreg.QueryValueEx(key, n)[0], name) for name in names]


def _windows_ram():
    import ctypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + [
            (name, ctypes.c_ulonglong) for name in ("ullTotalPhys", "ullAvailPhys", "ullTotalPageFile",
                                                    "ullAvailPageFile", "ullTotalVirtual", "ullAvailVirtual",
                                                    "ullAvailExtendedVirtual")]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(status)
    ok = ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(status))
    return status.ullTotalPhys if ok else None


def _windows():
    vendor, product, version = _registry(r"HARDWARE\DESCRIPTION\System\BIOS", "SystemManufacturer",
                                         "SystemProductName", "SystemVersion")
    cpu = _registry(r"HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString")[0]
    name, display, release, build, ubr = _registry(r"SOFTWARE\Microsoft\Windows NT\CurrentVersion", "ProductName",
                                                   "DisplayVersion", "ReleaseId", "CurrentBuild", "UBR")
    return {"manufacturer": _clean(vendor), "model": model_name(vendor, product, version),
            "os": _safe(windows_name, name, display or release, build, ubr), "cpu": _clean(cpu),
            "ram": _safe(_windows_ram)}


# ---------------------------------------------------------------------------------------------
# macOS: sysctl
# ---------------------------------------------------------------------------------------------

def _sysctl(name):
    r = subprocess.run(["sysctl", "-n", name], capture_output=True, text=True, timeout=5)
    return (r.stdout.strip() or None) if r.returncode == 0 else None


def _mac():
    version, kernel = _safe(lambda: platform.mac_ver()[0]), _safe(platform.release)
    memory = _safe(_sysctl, "hw.memsize")
    system = (f"macOS {version}" + (f" (Darwin {kernel})" if kernel else "")) if version else None
    return {"manufacturer": "Apple", "model": _clean(_safe(_sysctl, "hw.model")), "os": system,
            "cpu": _clean(_safe(_sysctl, "machdep.cpu.brand_string")),
            "ram": int(memory) if str(memory or "").isdigit() else None}


# ---------------------------------------------------------------------------------------------
# The public helpers
# ---------------------------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _machine():
    probe = _windows if sys.platform == "win32" else _mac if sys.platform == "darwin" else _linux
    found = _safe(probe) or {}
    ram = found.get("ram")
    return {"manufacturer": found.get("manufacturer"), "model": found.get("model"), "os": found.get("os"),
            "cpu": _clean(found.get("cpu")), "threads": _safe(os.cpu_count),
            "ram_gb": round(ram / 2 ** 30, 1) if isinstance(ram, int) and ram > 0 else None,
            "arch": _clean(_safe(platform.machine))}


def machine(gpus=None):
    """This computer: {"manufacturer", "model", "os", "cpu", "threads", "ram_gb", "arch", "gpus"}, e.g.
    LENOVO / ThinkPad L14 Gen 3 / Ubuntu 24.04.4 LTS (Linux 7.0.0-31-generic) / 12th Gen Intel(R) Core(TM)
    i5-1245U / 12 / 15.3 / x86_64. gpus: the graphics card names the caller found, or None when it hasn't
    looked. Read once per process."""
    try:
        return {**_machine(), "gpus": None if gpus is None else [str(g) for g in gpus if g]}
    except Exception:  # noqa: BLE001
        return dict.fromkeys(MACHINE_KEYS)


def _cover_art(stream):
    """An album-cover picture stored as a video stream (common in mp3 and m4a files)."""
    import av
    try:
        return bool(stream.disposition & av.stream.Disposition.attached_pic)
    except Exception:  # noqa: BLE001 — an older PyAV: judge by the codec
        return stream.codec_context.name in ("mjpeg", "png", "bmp", "gif", "webp")


def _made(value):
    """When a recording was made, from a tag's text (ISO 8601: a date, or a date and time) or a datetime, as the
    app writes times (local_time); None for anything else, such as a bare year, a clock that was never set or a
    time still to come. A time without an offset is taken as this computer's."""
    try:
        d = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).strip())
        d = d.astimezone()
    except (ValueError, OverflowError, OSError):
        return None
    if d.year < FIRST_YEAR or d.timestamp() > time.time() + 86400:
        return None
    return d.isoformat(timespec="seconds")


def _probe(path, info):
    import av
    with av.open(str(path)) as c:
        audio = next(iter(c.streams.audio), None)
        video = next((s for s in c.streams.video if not _cover_art(s)), None)
        info.update(format=_safe(lambda: c.format.name), format_name=_safe(lambda: c.format.long_name))
        tags = [_safe(lambda: dict(c.metadata)) or {}] + [_safe(lambda s=s: dict(s.metadata)) or {} for s in c.streams]
        made = next((m for key in MADE_TAGS for t in tags if (m := _made(t.get(key)))), None)
        if made:  # else the file's modified time stays
            info["recorded"] = made
        if video is not None:
            info["video"] = _safe(lambda: getattr(video.codec_context.codec, "canonical_name", None)
                                  or video.codec_context.name)
        seconds = _safe(lambda: c.duration / av.time_base if c.duration else None)
        if audio is not None:
            cc = audio.codec_context
            info.update(codec=_safe(lambda: getattr(cc.codec, "canonical_name", None) or cc.name),
                        codec_name=_safe(lambda: cc.codec.long_name),
                        sample_rate=_safe(lambda: cc.sample_rate or audio.sample_rate or None),
                        channels=_safe(lambda: len(cc.layout.channels) or None))
            # the audio's own bit rate; the container's total only when there is no video in it
            info["bit_rate"] = _safe(lambda: audio.bit_rate or cc.bit_rate
                                     or (c.bit_rate if video is None else None) or None)
            if seconds is None:
                seconds = _safe(lambda: float(audio.duration * audio.time_base) if audio.duration else None)
        info["duration"] = round(seconds, 2) if seconds else None


def recording(path, name=None):
    """An audio or video file: {"name", "extension", "format" and "format_name" (the container, e.g. "wav",
    "WAV / WAVE (Waveform Audio)"), "codec" and "codec_name" (the audio's, e.g. "aac", "AAC (Advanced Audio
    Coding)"), "sample_rate", "channels", "bit_rate" (bits per second), "size" (bytes), "duration" (seconds),
    "video" (its codec, if there is a video track), "recorded" (when it was made: the time in its tags, else
    the file's modified time, which an upload keeps from the browser)}. name: the file's own name when it is
    stored under another one (an upload is saved as source.<ext>)."""
    info = dict.fromkeys(RECORDING_KEYS)
    try:
        path = Path(path)
        name = str(name or path.name)
        info.update(name=name, extension=Path(name).suffix.lstrip(".").lower() or None,
                    size=_safe(lambda: path.stat().st_size),
                    recorded=_safe(lambda: _made(datetime.fromtimestamp(path.stat().st_mtime))))
        _probe(path, info)
    except Exception:  # noqa: BLE001 — not a media file, or unreadable: what was found so far stays
        pass
    return info


def peak_memory_mb():
    """The most memory this process has used so far, in MB (None if unknown)."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):  # PROCESS_MEMORY_COUNTERS
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                    (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize",
                                                         "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                                                         "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                                                         "PagefileUsage", "PeakPagefileUsage")]

            kernel32 = ctypes.WinDLL("kernel32")  # its own copy, so these signatures stay local
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            kernel32.K32GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            counters = Counters()
            counters.cb = ctypes.sizeof(counters)
            if not kernel32.K32GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
                return None
            return round(counters.PeakWorkingSetSize / 2 ** 20)
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # kilobytes; bytes on macOS
        return round(peak / (2 ** 20 if sys.platform == "darwin" else 1024))
    except Exception:  # noqa: BLE001
        return None


def local_time(t=None):
    """A time.time() value (default: now) as local ISO 8601 with the UTC offset, the way the app writes
    times: 2026-09-25T22:01:05+03:00."""
    return datetime.fromtimestamp(time.time() if t is None else t).astimezone().isoformat(timespec="seconds")
