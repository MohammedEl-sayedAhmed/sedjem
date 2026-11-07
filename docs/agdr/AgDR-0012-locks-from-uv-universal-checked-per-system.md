---
id: AgDR-0012
timestamp: 2026-10-30T18:30:00Z
agent: claude-code
model: claude-opus-5-5
trigger: user-prompt
status: executed
---

# Locks made by uv's universal resolution, checked for each system

> In the context of locking every Python package the workflows install, for Linux and Windows on Python 3.12, facing pip-compile resolving only for the system it runs on and uv's universal resolution missing sherpa-onnx-core, which sherpa-onnx's wheels need, I decided to make one lock for each use with `uv pip compile --universal --generate-hashes`, resolve it again for each system with the lock's versions and add what a system needs, to achieve locks made on one computer that install on both systems, accepting a check of our own beside uv's resolution and locks of up to 130 KB with the hash of every file.

## Context

- The maintainer asked for every package the builds install to be pinned by hash
  ([ADR 14](../adr/0014-everything-the-builds-install-is-pinned-by-hash.md)). The Tests and Desktop build workflows
  run on ubuntu-24.04 and windows-latest with Python 3.12, and Windows needs packages that Linux doesn't: pywin32
  for mcp, pythonnet and clr-loader for pywebview, pefile and pywin32-ctypes for PyInstaller, colorama.
- pip-tools' pip-compile resolves for the system it runs on, so a lock for Windows would have to be made on
  Windows. uv, already used by the Tests workflow, resolves for other systems (`--python-platform`) and for all at
  once (`--universal`, one lock with environment markers).
- A universal lock of the three requirement files (uv 0.13.0) had 83 packages. Installed with
  `pip install --require-hashes` into a new Python 3.12 environment on Linux, it failed: the wheel of sherpa-onnx
  1.13.8 needs sherpa-onnx-core 1.13.8, which the lock lacked. The source archive of sherpa-onnx 1.13.8 names no
  dependency in its metadata (version 2.1), its manylinux x86_64 wheel names sherpa-onnx-core, and uv's resolution
  for Linux, for Windows and for macOS each includes it, while the universal one doesn't. Compared with the
  resolutions for Linux and for Windows, sherpa-onnx-core was the lock's only difference.
- proxy-tools 0.1.0, which pywebview needs on every system, has only a source archive. With `--require-hashes`, pip
  checked the archive's hash and then built it in an isolated environment for which it installed
  `setuptools>=40.8.0`, the newest, without a hash (pip 25.0.1, seen with `-v`).

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| pip-tools' pip-compile with --generate-hashes | Dependabot's pip support runs it again for compiled files | Resolves for the system it runs on: a lock for Windows has to be made on Windows, and Dependabot would make it on Linux |
| uv's universal resolution alone | One lock with markers for both systems, made on any computer | Missed sherpa-onnx-core, so the lock didn't install on Linux or Windows |
| uv, a lock for each system (--python-platform) | Each system's own wheels are read | Two locks for each use, versions that may differ between the systems, and workflows that pick a lock by system |
| uv's universal resolution, then each system resolved with the lock's versions, and what's missing added | One lock for each use that installs on both systems, and a check that would find the next package like sherpa-onnx | A check of our own in the script, and three resolutions to make a lock |

## Decision

Chosen: uv's universal resolution, checked for each system, because it keeps one lock for each use that installs on
both systems from a computer of either, and turns a package whose files disagree, as sherpa-onnx's do, into a line
the script adds and says why, instead of an install that fails in CI. `.github/scripts/locks.py` makes four locks:
`ci.txt` from the three requirement files, `source.txt` from the two the docs install on Windows, at `ci.txt`'s
versions, `setuptools.txt` for building proxy-tools, and `tools.txt` for ruff, uv and pip-audit. pip installs the
locks of the app with `--no-build-isolation` after `setuptools.txt`, and uv with `--require-build-hashes`
against it, so that proxy-tools is built only with a setuptools whose hash is in a lock.

## Consequences

- With the check, sherpa-onnx-core is in `ci.txt` and `source.txt`, with a comment that says why. In new Python 3.12
  environments, pip installed `setuptools.txt`, then `ci.txt` with `--no-build-isolation`, on Linux (69 packages,
  `pip check` clean); uv installed `source.txt`, `ci.txt` (75 packages) and `tools.txt` (31) with
  `--python-platform x86_64-pc-windows-msvc` and `--require-hashes`, with Windows' wheels and proxy-tools built
  with the locked setuptools. The Windows jobs of the Tests and Desktop build workflows are the first installs on
  Windows itself.
- Each lock lists the hash of every file of each version, for every system, so `ci.txt` is about 130 KB.
- uv's `--require-build-hashes` is a preview in uv 0.13 and prints a warning saying so.
- A package that comes only as source code has to be named in `BUILT` in `tests/test_pipeline.py`, which checks
  that every install of a lock that has one builds it with the locked setuptools.
- A third system, such as macOS, would be one more entry in the script's `SYSTEMS`.

## Artifacts

- `.github/scripts/locks.py`, `.github/locks/`, and the Install steps of `.github/workflows/tests.yml` and
  `.github/workflows/desktop.yml`
- `docs/14-pipeline.md`, Locks
