"""Add a model from Hugging Face (Settings → Add models): work out what a repository
holds, pin its revision and files, and make a model entry that downloads and runs like the built-in ones.

What a repository can hold, read from the Hugging Face API without a login:
  ct2           Whisper for faster-whisper (CTranslate2): model.bin, config.json, vocabulary.json/.txt
  gguf          a GGUF speech model run by transcribe.cpp; its model family (general.architecture) is
                read from the file's header with an HTTP Range request before anything is downloaded
  transformers  a Whisper checkpoint in Transformers format (config.json with model_type "whisper",
                *.safetensors or pytorch_model.bin), converted to CTranslate2 after the download by the
                model worker (app/worker.py --convert-whisper; it needs transformers and torch)

Every file is pinned to the commit (<repo>/resolve/<sha>/<path>) and checked by its SHA-256 (LFS files)
or its git blob SHA-1 (small files). Added models are saved in <storage>/models.json with ids "hf-...",
and their files go under models/hf/<org>--<name>/. app/catalog.toml lists recommended models, which are
added the same way from a pinned revision.
"""
import functools
import hashlib
import importlib.util
import json
import os
import posixpath
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import tomllib
import urllib.parse

import requests

from . import engines
from .config import FROZEN, ROOT, _read_json, replace_file

BASE = "https://huggingface.co"  # the tests point this at a local stand-in
HOSTS = ("huggingface.co", "www.huggingface.co", "hf.co")
TIMEOUT = (15, 60)
NAME = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9_.-]{0,94}[A-Za-z0-9_])?")  # one half of org/name
LOCK = threading.Lock()  # models.json and cfg.models

# The model families in transcribe.cpp's library (general.architecture), with a readable name.
FAMILIES = {
    "whisper": "Whisper", "cohere_asr": "Cohere Transcribe", "parakeet": "NVIDIA Parakeet", "canary": "NVIDIA Canary",
    "canary2": "NVIDIA Canary 2", "canary_qwen": "NVIDIA Canary-Qwen", "moonshine": "Moonshine",
    "moonshine_streaming": "Moonshine Streaming", "qwen3_asr": "Qwen3-ASR", "granite_speech": "IBM Granite Speech",
    "granite_speech_nar": "IBM Granite Speech NAR", "voxtral": "Voxtral", "voxtral_realtime": "Voxtral Realtime",
    "sensevoice": "SenseVoice", "conformer": "Conformer",
}
# The Whisper tokenizer app/config.toml pins for whisper-medium: added to a faster-whisper model that
# has no tokenizer.json, which faster-whisper would otherwise fetch from the internet on every start.
WHISPER_TOKENIZER = {
    "url": "https://huggingface.co/openai/whisper-tiny/resolve/169d4a4341b33bc18d8881c4b69c2e104e1cc0af/tokenizer.json",
    "size": 2480466, "sha256": "27fc476bfe7f17299480be2273fc0608e4d5a99aba2ab5dec5374b4482d1a566"}
CT2_WHISPER_KEYS = {"lang_ids", "suppress_ids", "suppress_ids_begin", "alignment_heads"}  # config.json of faster-whisper models
TRANSFORMERS_FILES = ("config.json", "generation_config.json", "preprocessor_config.json", "tokenizer.json",
                      "tokenizer_config.json", "vocab.json", "merges.txt", "added_tokens.json",
                      "special_tokens_map.json", "normalizer.json")
CONVERT_NEEDS = ("transformers", "torch", "ctranslate2")
LICENCES = {"mit": "MIT", "apache-2.0": "Apache-2.0", "cc-by-4.0": "CC-BY-4.0", "cc-by-nc-4.0": "CC-BY-NC-4.0",
            "cc-by-sa-4.0": "CC-BY-SA-4.0", "gpl-3.0": "GPL-3.0", "openrail": "OpenRAIL"}

NO_NETWORK = "Couldn't reach Hugging Face. Check the internet connection and try again."
CANT_CONVERT = ("This Whisper model is in Transformers format and has to be converted for faster-whisper, which "
                "needs the transformers and torch packages. This installation of Sedjem doesn't have them. Look "
                "for a faster-whisper (CTranslate2) version of the model, or run Sedjem from source with "
                "transformers and torch installed.")


class HubError(Exception):
    """A problem to show as it is, in plain words."""


# ---------------------------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------------------------

def parse_link(text):
    """(repo, revision or None, path or None) from a model link or "org/name". Accepted: huggingface.co/<repo>,
    .../tree/<rev>[/<folder>], .../blob/<rev>/<file>, .../resolve/<rev>/<file>, .../commit/<sha>."""
    text = (text or "").strip()
    if not text:
        raise HubError("Paste a link to a model on Hugging Face, or its name (org/name).")
    if "://" in text or text.split("/", 1)[0].lower() in HOSTS:
        url = urllib.parse.urlsplit(text if "://" in text else f"https://{text}")
        if url.scheme not in ("http", "https") or (url.hostname or "").lower() not in HOSTS:
            raise HubError("That isn't a Hugging Face link. It should start with https://huggingface.co/.")
        parts = [urllib.parse.unquote(p) for p in url.path.split("/") if p]  # split first: a revision may hold %2F
    else:
        parts = [p for p in text.split("/") if p]
    if parts[:1] in (["datasets"], ["spaces"]):
        raise HubError("That link is to a dataset or a Space, not to a model.")
    if len(parts) < 2:
        raise HubError("A model's name has two parts, org/name (for example openai/whisper-small).")
    repo = check_repo("/".join(parts[:2]))
    rest, rev, path = parts[2:], None, None
    if rest[:1] in (["tree"], ["blob"], ["resolve"], ["commit"]):
        if len(rest) < 2:
            raise HubError("That link is cut short. Copy the address of the model's page again.")
        rev = check_revision(rest[1])
        path = check_path("/".join(rest[2:])) if len(rest) > 2 and rest[0] != "commit" else None
    return repo, rev, path


def check_repo(repo):
    org, _, name = repo.partition("/")
    if not (NAME.fullmatch(org) and NAME.fullmatch(name)) or "--" in repo or ".." in repo:
        raise HubError(f"“{repo}” isn't a Hugging Face model name. It looks like org/name.")
    return repo


def check_revision(rev):
    if not re.fullmatch(r"[\w.\-/]{1,200}", rev or "") or ".." in rev:
        raise HubError(f"“{rev}” isn't a branch, tag or commit name.")
    return rev


def check_path(path):
    """A file path inside a repository: relative, no "..", nothing a file system would read differently."""
    parts = str(path or "").split("/")
    if (not path or len(path) > 500 or "\\" in path or ":" in path or any(ord(c) < 32 for c in path)
            or any(p in ("", ".", "..") for p in parts)):
        raise HubError(f"“{path}” isn't a usable file name.")
    return path


# ---------------------------------------------------------------------------------------------
# The Hugging Face API
# ---------------------------------------------------------------------------------------------

def get(http, url, **kw):
    try:
        return http.get(url, timeout=TIMEOUT, **kw)
    except requests.RequestException:
        raise HubError(NO_NETWORK) from None


def json_of(r, kind):
    """The body of an API answer, if it is JSON of the expected kind (dict or list)."""
    try:
        data = r.json()
    except ValueError:
        data = None
    if not isinstance(data, kind):
        raise HubError("Hugging Face sent an answer Sedjem doesn't understand. Try again later.")
    return data


def revision_info(http, repo, ref):
    """The model's API record at a branch, tag or commit: sha, gated, private, cardData, tags, ..."""
    r = get(http, f"{BASE}/api/models/{repo}/revision/{urllib.parse.quote(ref, safe='')}")
    if r.status_code == 404 and "invalid rev" in r.text.lower():
        raise HubError(f"{repo} has no branch, tag or commit called “{ref}”.")
    if r.status_code in (401, 403, 404):  # Hugging Face answers 401 for a private repository too
        raise HubError(f"There is no public model called {repo} on Hugging Face. Check the name. Private models "
                       "can't be added, because Sedjem doesn't log in to Hugging Face.")
    if r.status_code >= 400:
        raise HubError(f"Hugging Face answered with an error (HTTP {r.status_code}). Try again later.")
    info = json_of(r, dict)
    if info.get("gated"):
        raise HubError(f"{repo} is gated: Hugging Face asks you to log in and accept its terms before "
                       "downloading, and Sedjem doesn't log in. Look for an open copy of the model.")
    if info.get("private") or info.get("disabled"):
        raise HubError(f"{repo} is private or disabled on Hugging Face.")
    if not re.fullmatch(r"[0-9a-f]{40}", str(info.get("sha", ""))):
        raise HubError("Hugging Face didn't say which commit that is, so the files can't be pinned.")
    if str(info.get("id", "")).lower() != repo.lower():  # the canonical spelling of the name, if it gives one
        info["id"] = repo
    check_repo(info["id"])
    return info


def tree(http, repo, sha):
    """{path: {"path", "size", "oid", "lfs": {"oid", "size"}}} for every file at the commit."""
    url, files = f"{BASE}/api/models/{repo}/tree/{sha}?recursive=true", {}
    for _ in range(50):  # the listing comes in pages
        r = get(http, url)
        if r.status_code >= 400:
            raise HubError(f"Hugging Face didn't list the files of {repo} (HTTP {r.status_code}). Try again later.")
        for f in json_of(r, list):
            try:
                if f.get("type") == "file" and check_path(f.get("path")) and int(f.get("size")) >= 0:
                    files[f["path"]] = f
            except (AttributeError, HubError, TypeError, ValueError):  # a name we wouldn't store safely: leave it out
                pass
        url = r.links.get("next", {}).get("url")
        if not url or not url.startswith(BASE + "/"):
            break
    return files


def resolve_url(repo, sha, path):
    return f"{BASE}/{repo}/resolve/{sha}/{urllib.parse.quote(path)}"


def small_json(http, repo, sha, f):
    """A small JSON file from the repository (config.json), as a dict."""
    if f["size"] > 5_000_000:
        raise HubError(f"{f['path']} is too large to be a model's settings file.")
    r = get(http, resolve_url(repo, sha, f["path"]))
    if r.status_code >= 400:
        raise HubError(f"Couldn't read {f['path']} from {repo} (HTTP {r.status_code}).")
    return json_of(r, dict)


def pinned(repo, sha, f, dest):
    """A download entry for the app's downloader: fixed URL, size, and the checksum Hugging Face lists."""
    out = {"url": resolve_url(repo, sha, f["path"]), "path": dest, "size": int(f["size"])}
    lfs = f.get("lfs") or {}
    if re.fullmatch(r"[0-9a-f]{64}", str(lfs.get("oid", ""))):  # stored with LFS: the SHA-256 of the content
        out.update(size=int(lfs.get("size") or f["size"]), sha256=lfs["oid"])
    elif re.fullmatch(r"[0-9a-f]{40}", str(f.get("oid", ""))):  # a small file: its git blob id
        out["git_sha1"] = f["oid"]
    else:
        raise HubError(f"Hugging Face gave no checksum for {f['path']}, so the download couldn't be checked.")
    return out


def licence(info):
    card = info.get("cardData") or {}
    lic = card.get("license") or next((t.split(":", 1)[1] for t in info.get("tags") or []
                                       if str(t).startswith("license:")), None)
    if isinstance(lic, list):
        lic = ", ".join(map(str, lic))
    if lic == "other" and card.get("license_name"):
        lic = card["license_name"]
    return LICENCES.get(str(lic).lower(), str(lic)) if lic else None


# ---------------------------------------------------------------------------------------------
# GGUF headers: magic "GGUF", uint32 version, uint64 tensor count, uint64 key count, then keys
# (uint64 length + bytes), each with a uint32 value type and the value. general.architecture comes first.
# ---------------------------------------------------------------------------------------------

GGUF_SIZES = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}  # value type -> bytes
GGUF_STRING, GGUF_ARRAY = 8, 9
HEADER_READS = (1 << 16, 1 << 20, 1 << 24)  # how much of the file to read, in turn, until the key is found


class Truncated(ValueError):
    """The bytes end before the answer: read more of the file."""


def gguf_architecture(data):
    """general.architecture from the first bytes of a GGUF file. Raises Truncated if more bytes are
    needed, ValueError if it isn't a GGUF file or has no such key."""
    if data[:4] != b"GGUF":
        raise ValueError("it isn't a GGUF file")
    version, _tensors, n_keys = _unpack("<IQQ", data, 4)
    if version < 2:
        raise ValueError(f"GGUF version {version} is too old")
    pos = 24
    for _ in range(n_keys):
        key, pos = _string(data, pos)
        (vtype,) = _unpack("<I", data, pos)
        pos += 4
        if key == b"general.architecture":
            if vtype != GGUF_STRING:
                raise ValueError("general.architecture isn't text")
            return _string(data, pos)[0].decode("utf-8", "replace")
        pos = _skip(data, pos, vtype)
    if pos > len(data):
        raise Truncated()
    raise ValueError("it doesn't name its model family (general.architecture)")


def _unpack(fmt, data, pos):
    try:
        return struct.unpack_from(fmt, data, pos)
    except struct.error:
        raise Truncated() from None


def _string(data, pos):
    (n,) = _unpack("<Q", data, pos)
    if n > 1 << 30:
        raise ValueError("its header is damaged")
    if pos + 8 + n > len(data):
        raise Truncated()
    return data[pos + 8:pos + 8 + n], pos + 8 + n


def _skip(data, pos, vtype, depth=0):
    """The position after a value of the given type."""
    if vtype in GGUF_SIZES:
        return pos + GGUF_SIZES[vtype]
    if vtype == GGUF_STRING:
        return _string(data, pos)[1]
    if vtype == GGUF_ARRAY and depth < 3:
        etype, count = _unpack("<IQ", data, pos)
        pos += 12
        if etype in GGUF_SIZES:
            return pos + count * GGUF_SIZES[etype]
        for _ in range(count):  # strings or nested arrays; ends in Truncated when the bytes run out
            pos = _skip(data, pos, etype, depth + 1)
        return pos
    raise ValueError(f"its header has an unknown value type ({vtype})")


def read_head(http, url, n):
    """The first n bytes of a file, with a Range request (a server that ignores it is cut off after n)."""
    try:
        with http.get(url, headers={"Range": f"bytes=0-{n - 1}", "Accept-Encoding": "identity"}, stream=True,
                      timeout=TIMEOUT) as r:
            if r.status_code not in (200, 206):
                raise HubError(f"Hugging Face answered HTTP {r.status_code} for {url.rsplit('/', 1)[-1]}.")
            data = bytearray()
            for chunk in r.iter_content(1 << 16):
                data += chunk
                if len(data) >= n:
                    break
            return bytes(data[:n])
    except requests.RequestException:
        raise HubError(NO_NETWORK) from None


def remote_architecture(http, url, name):
    for n in HEADER_READS:
        data = read_head(http, url, n)
        try:
            return gguf_architecture(data)
        except Truncated:
            if len(data) < n:  # that was the whole file
                raise HubError(f"{name} ends inside its header, so it isn't a usable GGUF file.") from None
        except ValueError as e:
            raise HubError(f"{name} can't be used: {e}.") from None
    raise HubError(f"Couldn't find the model family in the header of {name}.")


# ---------------------------------------------------------------------------------------------
# What a repository holds
# ---------------------------------------------------------------------------------------------

def is_gguf(path):
    name = posixpath.basename(path).lower()
    return (name.endswith(".gguf") and not name.startswith("mmproj")  # a projector, used next to a model
            and not re.search(r"-\d{5}-of-\d{5}\.gguf$", name))  # one part of a split file


def weights(here):
    """The weight files of a Transformers checkpoint, safetensors first (one file, or shards with an index)."""
    for single, shard, index in (("model.safetensors", r"model-\d{5}-of-\d{5}\.safetensors", "model.safetensors.index.json"),
                                 ("pytorch_model.bin", r"pytorch_model-\d{5}-of-\d{5}\.bin", "pytorch_model.bin.index.json")):
        if single in here:
            return [single]
        shards = sorted(n for n in here if re.fullmatch(shard, n))
        if shards and index in here:
            return [index, *shards]
    return []


def find(cfg, link, file=None, revision=None, http=None):
    """Everything about the model a link points to (nothing is saved). file picks one of several GGUF
    files; revision pins the commit an earlier look found."""
    repo, ref, path = parse_link(link)
    path = check_path(file) if file else path
    ref = check_revision(revision) if revision else ref or "main"
    own = http is None
    http = http or requests.Session()
    try:
        info = revision_info(http, repo, ref)
        repo, sha = info["id"], info["sha"]
        found = {"repo": repo, "revision": sha, "ref": ref, "licence": licence(info), "problem": None,
                 "id": model_id(cfg, repo), "file": None, "choices": None, "architecture": None}
        found.update(detect(cfg, http, info, tree(http, repo, sha), path))
        return found
    finally:
        if own:
            http.close()


def detect(cfg, http, info, files, path):
    repo, sha = info["id"], info["sha"]
    org, name = repo.split("/")
    dest = f"models/hf/{org}--{name}"
    folder, pick = "", None
    if path in files:
        pick, folder = path, posixpath.dirname(path)
    elif path:
        folder = path.strip("/")
        if not any(p.startswith(folder + "/") for p in files):
            raise HubError(f"{repo} has no file or folder called {path}.")
    here = {posixpath.basename(p): f for p, f in files.items() if posixpath.dirname(p) == folder}
    ggufs = [p for p in files if is_gguf(p) and (pick or not folder or p.startswith(folder + "/"))]
    if pick and is_gguf(pick):
        return gguf(http, repo, sha, files, ggufs, pick, dest)

    is_ct2 = "model.bin" in here and bool({"vocabulary.json", "vocabulary.txt"} & here.keys())
    config = small_json(http, repo, sha, here["config.json"]) if "config.json" in here and (is_ct2 or weights(here)) else {}
    if is_ct2 and CT2_WHISPER_KEYS & config.keys():
        return ct2(repo, sha, here, dest)
    if ggufs:
        return gguf(http, repo, sha, files, ggufs, pick, dest)
    if config and weights(here):
        return transformers(cfg, info, here, folder, dest, config)
    if is_ct2 and config:
        raise HubError(f"{repo} is a CTranslate2 model, but not a Whisper one. Sedjem runs CTranslate2 models "
                       "through faster-whisper, which needs Whisper.")
    if "adapter_config.json" in here:
        raise HubError(f"{repo} is an adapter (LoRA) for another model, not a whole model, so it can't run on its own.")
    raise HubError(f"Sedjem didn't find a model it can run in {repo}. It can add Whisper models for faster-whisper "
                   "(model.bin), Whisper models in Transformers format, and GGUF speech models for transcribe.cpp.")


def ct2(repo, sha, here, dest):
    vocabulary = "vocabulary.json" if "vocabulary.json" in here else "vocabulary.txt"
    names = [n for n in ("config.json", vocabulary, "tokenizer.json", "preprocessor_config.json") if n in here]
    files = [pinned(repo, sha, here[n], f"{dest}/{n}") for n in names]
    if "tokenizer.json" not in here:  # faster-whisper would fetch it from the internet at every start
        files.append({**WHISPER_TOKENIZER, "path": f"{dest}/tokenizer.json"})
    files.append(pinned(repo, sha, here["model.bin"], f"{dest}/model.bin"))  # last: the model counts as ready once it's there
    return {"kind": "ct2", "label": "faster-whisper (CTranslate2)", "architecture": "whisper", "files": files,
            "model_size": here["model.bin"]["size"]}


def gguf(http, repo, sha, files, ggufs, pick, dest):
    size = lambda p: files[p]["size"]  # noqa: E731
    choice = pick if pick in ggufs else None
    for quant in ("q4_k_m", "q5_k_m", "q8_0"):  # a good balance of size and accuracy, in this order
        choice = choice or min((p for p in ggufs if quant in posixpath.basename(p).lower()), key=size, default=None)
    choice = choice or min(ggufs, key=size)
    name = posixpath.basename(choice)
    arch = remote_architecture(http, resolve_url(repo, sha, choice), name)
    if arch not in FAMILIES:
        raise HubError(f"{name} is a GGUF model of the family “{arch}”, which transcribe.cpp can't run (it may be a "
                       f"language model rather than a speech model). transcribe.cpp runs {', '.join(FAMILIES)}.")
    return {"kind": "gguf", "label": f"GGUF for transcribe.cpp ({FAMILIES[arch]})", "architecture": arch,
            "file": choice, "choices": [{"file": p, "size": size(p)} for p in sorted(ggufs, key=size)],
            "files": [pinned(repo, sha, files[choice], f"{dest}/{name}")], "model_size": size(choice)}


def transformers(cfg, info, here, folder, dest, config):
    repo, sha = info["id"], info["sha"]
    if config.get("model_type") != "whisper":
        raise HubError(f"{repo} is a Transformers model of the type “{config.get('model_type')}”. Sedjem can "
                       "convert only Whisper models from Transformers.")
    if "tokenizer.json" not in here and not {"vocab.json", "merges.txt"} <= here.keys():
        raise HubError(f"{repo} has no tokenizer files (tokenizer.json, or vocab.json and merges.txt), so it can't be converted.")
    names = [n for n in TRANSFORMERS_FILES if n in here] + weights(here)
    files = [pinned(repo, sha, here[n], f"{dest}-src/{n}") for n in names]
    size = sum(here[n]["size"] for n in weights(here))
    params = (info.get("safetensors") or {}).get("total") if not folder else None
    return {"kind": "transformers", "label": "Whisper in Transformers format, converted after the download",
            "architecture": "whisper", "files": files, "problem": None if can_convert(cfg) else CANT_CONVERT,
            "model_size": int(params) * 2 if params else size // 2,  # float16 after conversion
            "convert_space": size}  # at most as large as the download


def can_convert(cfg):
    """Whether the model worker can convert Transformers checkpoints: it needs transformers and torch, which a
    source install can have and the desktop build doesn't. Checked without importing them, once per start."""
    return _can_convert(cfg.python())


@functools.lru_cache(maxsize=None)
def _can_convert(python):
    if FROZEN or python == sys.executable:
        return all(importlib.util.find_spec(m) for m in CONVERT_NEEDS)
    code = f"import importlib.util as u, sys; sys.exit(0 if all(u.find_spec(m) for m in {CONVERT_NEEDS!r}) else 1)"
    try:
        return subprocess.run([python, "-c", code], capture_output=True, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# ---------------------------------------------------------------------------------------------
# Model entries, saved in <storage>/models.json and loaded by Config next to app/config.toml's
# ---------------------------------------------------------------------------------------------

def model_id(cfg, repo):
    """"hf-<org>--<name>" in lower case; never the id of another model (built-in or added)."""
    org, name = (re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") for s in repo.split("/"))
    mid = f"hf-{org}--{name}"
    other = cfg.models.get(mid)
    if other and str((other.get("hub") or {}).get("repo", "")).lower() != repo.lower():
        mid += "-" + hashlib.sha1(repo.lower().encode()).hexdigest()[:6]
    return mid


def size_text(n):
    """Like the interface's bytes(): 775 MB, 1.6 GB."""
    unit = "B"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            break
        n /= 1000
    return f"{n:.1f} {unit}" if unit != "B" and n < 10 else f"{n:.0f} {unit}"


def summary(found):
    """What /api/hub/inspect returns."""
    keys = ("id", "repo", "revision", "ref", "kind", "label", "architecture", "file", "choices", "licence", "problem")
    return {**{k: found[k] for k in keys}, "size": sum(f["size"] for f in found["files"])}


def entry(found):
    """A model entry like the local ones in app/config.toml, with "hub" saying where it came from."""
    repo, kind = found["repo"], found["kind"]
    org, name = repo.split("/")
    folder, size = f"models/hf/{org}--{name}", sum(f["size"] for f in found["files"])
    gb = found["model_size"] / 1e9  # speed estimates until the first run here measures it (App.speed)
    kind_fact = {"ct2": "faster-whisper (CTranslate2)", "transformers": "Transformers, converted for faster-whisper",
                 "gguf": f"GGUF, {found['architecture']}: {posixpath.basename(found['file'] or '')}"}[kind]
    m = {"id": found["id"], "kind": "local", "title": name, "tagline": f"{FAMILIES[found['architecture']]} model",
         "facts": [repo, kind_fact, f"{size_text(size)} download",
                   f"Licence: {found['licence']}" if found["licence"] else "Licence not stated"],
         "files": found["files"], "prompt": kind != "gguf" or found["architecture"] == "whisper",  # Whisper GGUFs take terms too
         "hub": {k: found[k] for k in ("repo", "revision", "kind", "label", "file", "architecture")}}
    if kind == "gguf":
        m.update(engine="gguf", cohere_model=found["files"][0]["path"], rtf=max(0.05, round(0.2 * gb, 2)),
                 rtf_gpu=max(0.03, round(0.1 * gb, 2)))
    else:
        m.update(engine="whisper", whisper_model=folder, rtf=max(0.1, round(0.6 * gb, 2)))
    if kind == "transformers":
        m["convert"] = {"from": f"{folder}-src", "to": folder, "size": found["convert_space"]}
    return m


def inspect(cfg, link, file=None, revision=None, http=None):
    found = find(cfg, link, file, revision, http)
    return {**summary(found), "added": found["id"] in cfg.models}


def add(cfg, link, file=None, revision=None, http=None):
    """Look again (the server doesn't trust what the page sends), save the entry and return it."""
    found = find(cfg, link, file, revision, http)
    if found["problem"]:
        raise HubError(found["problem"])
    m = entry(found)
    need = sum(f["size"] for f in m["files"]) + (m.get("convert") or {}).get("size", 0)
    cfg.home.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(cfg.home).free < need + 500e6:
        raise HubError(f"There isn't enough free disk space for it: {size_text(need)} is needed.")
    with LOCK:
        if m["id"] in cfg.models:
            raise HubError(f"{found['repo']} is already in the app. To pick another file, remove it first.")
        _write(cfg, [x for x in saved(cfg) if x.get("id") != m["id"]] + [m])
        cfg.models = {**cfg.models, m["id"]: m}  # a new dict: other threads may be reading the old one
    return m


def saved(cfg):
    return [m for m in _read_json(cfg.added_models_path).get("models", []) if isinstance(m, dict)]


def _write(cfg, models):
    cfg.storage.mkdir(parents=True, exist_ok=True)
    tmp = cfg.added_models_path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"models": models}, ensure_ascii=False, indent=1), encoding="utf-8")
    replace_file(tmp, cfg.added_models_path)


def forget(cfg, model_id):
    """Remove an added model: its entry and its folders under models/hf (the caller checks that nothing uses it)."""
    with LOCK:
        m = cfg.models.get(model_id) or {}
        _write(cfg, [x for x in saved(cfg) if x.get("id") != model_id])
        cfg.models = {k: v for k, v in cfg.models.items() if k != model_id}
    root = cfg.path("models/hf").resolve()
    folders = {posixpath.dirname(f["path"]) for f in m.get("files", [])} | {m.get("whisper_model")} - {None, ""}
    for folder in folders | {f + ".part" for f in folders}:  # .part: a conversion cut short
        p = cfg.path(folder).resolve()
        if root in p.parents:  # only ever inside models/hf
            shutil.rmtree(p, ignore_errors=True)


# ---------------------------------------------------------------------------------------------
# Recommended models (app/catalog.toml)
# ---------------------------------------------------------------------------------------------

CATALOG = ROOT / "app" / "catalog.toml"
NEEDS_SOURCE = "Needs Sedjem run from source with transformers and torch, to convert it."


@functools.lru_cache(maxsize=None)
def recommended(path=CATALOG):
    return tomllib.loads(path.read_text(encoding="utf-8"))["models"]


def catalog(cfg):
    """The recommended models for the interface, each with what this installation has of it: "added" (the
    id and file of the model added from that repository) or a "problem" that keeps it from being added."""
    added = {str(m["hub"].get("repo", "")).lower(): m for m in cfg.models.values() if isinstance(m.get("hub"), dict)}
    out = []
    for c in recommended():
        if c.get("builtin") and c["builtin"] not in cfg.models:
            continue  # hidden in the config
        m = added.get(c.get("repo", "").lower()) or {}
        problem = NEEDS_SOURCE if c.get("kind") == "transformers" and not m and not can_convert(cfg) else None
        out.append({**c, "key": c.get("builtin") or c["repo"], "added": m.get("id"),
                    "added_file": m.get("hub", {}).get("file"), "problem": problem})
    return out


PINNED = re.compile(r"^https://huggingface\.co/([^/]+/[^/]+)/resolve/([0-9a-f]{7,40})/")


def about(model):
    """What Settings shows of a local model beyond its row, all from the data: the Hugging Face repository
    and pinned revision its files come from (for a built-in model, read from its first file's link), its
    licence (from the config, the model page or the catalog), and the catalog's words about it."""
    h = model.get("hub") if isinstance(model.get("hub"), dict) else {}
    source = {"repo": h["repo"], "revision": h.get("revision")} if h.get("repo") else None
    for f in model.get("files") or []:
        m = PINNED.match(str(f.get("url", ""))) if not source else None
        if m:
            source = {"repo": m[1], "revision": m[2]}
    repo = (source or {}).get("repo", "").lower()
    rec = next((c for c in recommended() if c.get("builtin") == model.get("id")
                or (h and repo and str(c.get("repo", "")).lower() == repo)), {})
    stated = next((str(f)[len("Licence: "):] for f in model.get("facts") or [] if str(f).startswith("Licence: ")), None)
    return {"source": source, "licence": model.get("licence") or stated or rec.get("licence"),
            "good_for": rec.get("good_for"), "evidence": rec.get("evidence")}


# ---------------------------------------------------------------------------------------------
# Conversion of a Transformers checkpoint, for downloads.py
# ---------------------------------------------------------------------------------------------

def convert(cfg, spec, cancelled):
    """Convert a downloaded Transformers Whisper checkpoint (spec: {"from", "to"}) to CTranslate2 in the model
    worker process, then delete the download. Raises downloads.Cancelled, or RuntimeError with the reason."""
    from .downloads import Cancelled

    src, dst = cfg.path(spec["from"]), cfg.path(spec["to"])
    log_path = src / "convert.log"
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(ROOT)}
    with open(log_path, "w", encoding="utf-8") as log:
        proc = engines.spawn(engines.worker_command(cfg) + ["--convert-whisper", str(src), str(dst)], log, env)
        while proc.poll() is None:
            if cancelled():
                engines.stop(proc)
                raise Cancelled()
            time.sleep(0.5)
    if proc.returncode < 0:
        raise RuntimeError(f"the conversion was stopped (signal {-proc.returncode}; out of memory?)")
    if proc.returncode != 0 or not (dst / "model.bin").exists():
        lines = [x.strip() for x in log_path.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]
        raise RuntimeError("the conversion failed: " + (lines[-1] if lines else f"exit code {proc.returncode}"))
    shutil.rmtree(src, ignore_errors=True)
