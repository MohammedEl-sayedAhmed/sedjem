# The desktop app (Windows and Linux)

Sedjem ([the app](09-app.md)) also runs as a desktop application, with its own window, a Start menu
or application menu entry, models downloaded from inside the app, and a Windows build and installer.
It is the same code, the local server and the web interface, shown in a native window, so every
feature works the same way in the browser and on the desktop.

It builds, installs and runs on both systems, and the checks below pass; how it updates itself is
described in [updates](12-updates.md). Code signing and Linux packages are not done yet (see the end
of this page).

## Run it

| | From source | Desktop build |
|---|---|---|
| Linux | `./app.sh --desktop` | `dist/Sedjem/Sedjem` |
| Windows | `app.cmd` (after the one-time setup below) | Start menu → Sedjem (installer), or `Sedjem.exe` |

To set up on Windows from source, once: install Python 3.12, then in the project folder run
`py -3.12 -m venv .venv` and `.venv\Scripts\pip install -r requirements.txt -r requirements-desktop.txt`.
`app.cmd` starts Sedjem without a console, so when nothing opens, `.venv\Scripts\python -m app.desktop`
shows the reason.

Sedjem picks the first of these that works:

1. A native window (pywebview) using the system's web engine: Edge WebView2 on Windows (part of
   Windows 10 and 11), WebKit on macOS, GTK WebKit or Qt on Linux. It adds native *Open* and *Save*
   dialogs: a recording picked with *choose a file* is read in place rather than copied, and exports
   are saved where you choose. On Windows, Sedjem first checks that the WebView2 runtime is
   installed, because without it pywebview would quietly fall back to the old Internet Explorer
   engine, which can't run the app.
2. An app-mode browser window: Edge, Chrome, Chromium or Brave with `--app` (no tabs or address bar)
   and a profile of its own. On Linux this is used when neither GTK WebKit nor Qt is available, as on
   the test laptop.
3. A tab in the default browser.
4. If no window of any kind can be opened, the server keeps running and Sedjem shows its address
   (in a message box on Windows), so any browser can use it.

On Linux the desktop app keeps `~/.local/share/applications/sedjem.desktop` current, so Sedjem is
in the application menu and its window shows the Sedjem icon: the browser window gets the window class
`Sedjem` (`--class`), which the entry names. Only X11 keeps that class, so on a Wayland desktop the
browser window is drawn through XWayland; otherwise the taskbar would show the browser's icon. On
Windows the built `Sedjem.exe` carries the icon, and from source the window takes it from
`desktop/sedjem.ico`.

Closing the window quits the app, and so does *Quit Sedjem* in *Settings*. `--browser` skips the native window,
`--server` (or `--no-window`) runs only the server, and `--port N` picks the port (the default is
8765, or any free one). The Windows installer also adds a *Sedjem (browser window)* shortcut, which
starts it with `--browser`. `Sedjem --mcp` opens nothing: it is the command an assistant such as
Claude Desktop runs to read the transcriptions over MCP, answering on standard input and output
([assistants](11-assistants.md)).

On Linux the native window needs Python bindings for one of the two engines. On KDE the simplest
is Qt: `pip install "pywebview[qt]"` (about 150 MB). On GNOME it is GTK WebKit, which needs the
system package `gir1.2-webkit2-4.1` and PyGObject in the virtual environment. Without either, the
app-mode browser window is used, which works just as well apart from the native dialogs.

## Where things are kept

| | Data folder (transcriptions, keys, downloaded models, `sedjem.log`) |
|---|---|
| From source | the project folder, as before (`app_data/`, `models/`) |
| Windows build | `%LOCALAPPDATA%\Sedjem` |
| Linux build | `~/.local/share/sedjem` |
| macOS build | `~/Library/Application Support/Sedjem` |

`SEDJEM_HOME=<folder>` overrides it. Uninstalling keeps this folder.

## Models are downloaded in the app

A fresh installation has no local models. *Settings → Models*, or the button on a
model's card, downloads them:

| Model | Download | Source (pinned revision) |
|---|---|---|
| whisper-medium code-switching | 778 MB | Hugging Face `Seif-Eldeen-Sameh/whisper-medium-arabic-codeswitched-ct2`, plus the Whisper tokenizer |
| Cohere Transcribe Arabic | 1.6 GB | Hugging Face `handy-computer/cohere-transcribe-arabic-07-2026-gguf` |
| Whisper large-v3 | 3.1 GB | Hugging Face `Systran/faster-whisper-large-v3` (a copy already in the Hugging Face cache is used instead) |
| Voiceprint model (speaker labels) | 40 MB | sherpa-onnx release (TitaNet-small), fetched together with the first model |

Every file is pinned in `app/config.toml` by URL (at a fixed revision), size and SHA-256. A download
resumes after a dropped connection (HTTP range requests) and only becomes the real file once the size
and checksum match. Files can be deleted again from the same screen. The hosted models (ElevenLabs,
Speechmatics) need no download, only an API key.

## Build it

```sh
pip install -r requirements.txt -r requirements-desktop.txt
python desktop/build.py              # dist/Sedjem/ (Sedjem.exe on Windows); --console to debug
dist/Sedjem/Sedjem --self-test     # on Windows add --report selftest.json (a windowed app has no console)
iscc desktop/installer.iss           # Windows only (Inno Setup 6): dist/Sedjem-<version>-setup.exe
```

Build on the system you are building for, since PyInstaller doesn't cross-compile. The build is one
folder, about 470 MB on Linux (CTranslate2, onnxruntime, sherpa-onnx, the FFmpeg libraries,
transcribe.cpp with its CPU and Vulkan backends, numpy); models and NVIDIA's CUDA libraries are not
included, and the app downloads them when asked. The same executable
runs the model worker (`Sedjem --transcribe …`), so each transcription still gets its own process,
and the assistants' MCP server (`Sedjem --mcp`).

The Tests workflow ([`.github/workflows/tests.yml`](../.github/workflows/tests.yml)) runs the tests on
Windows and Linux for every push and pull request, and on Windows also sets Sedjem up from source
exactly as above (`py -3.12 -m venv .venv`, `pip install`, then `app.cmd`) and checks that it starts.

The GitHub Actions workflow [`.github/workflows/desktop.yml`](../.github/workflows/desktop.yml)
builds and checks the app on Windows and Linux. It runs the unit tests, a self-test from source, the
build, and a self-test of the build; on Windows also the real-model test, a native-window test and
the installer, which it uploads and keeps for 7 days. It runs when started by hand
(*Actions → Desktop build → Run workflow*, or `gh workflow run desktop.yml`), for a version tag
(`git tag v0.3.0 && git push origin v0.3.0`; the version must match `app/__init__.py`) and for the
nightly previews below, so it doesn't use Actions minutes on every push. A tag or a preview also
publishes the Windows installer, the Linux archive and `SHA256SUMS.txt` on GitHub, as a release or a
pre-release.

## Releases and nightly previews

Each build carries a stamp, `app/_build.json`, which tells Sedjem what it is running when it looks
for [updates](12-updates.md): the version, the commit it was built from and that commit's date, the
channel (`stable` for a release, `preview` for a nightly preview) and when it was built.
`desktop/build.py` writes it for the build and removes it again, so a copy run from source never has
one; from source, Sedjem reads the commit from `.git`. The workflow sets the version and the channel
with `SEDJEM_VERSION` and `SEDJEM_CHANNEL`; without them they come from `app/__init__.py`.

The Windows installer has an update mode for Sedjem's own updates:
`Sedjem-<version>-setup.exe /SILENT /SUPPRESSMSGBOXES /NORESTART /CLOSEAPPLICATIONS /UPDATE=1`
installs over the existing installation without asking anything, closes Sedjem if it is still
running, and starts the new version when it is done. It first removes the program files of the
version it replaces (the `_internal` folder), so nothing of the old version is left beside the new
one, and it never touches the data folder. A silent installation without `/UPDATE=1` still starts
nothing. The Desktop build workflow tries this on Windows: it installs the new installer into a
temporary folder, installs it again over itself in update mode, and checks that the old files are
gone, the data folder is kept, Sedjem started from the installed folder and answers, and the
uninstaller still removes the program.

The Nightly preview workflow ([`.github/workflows/preview.yml`](../.github/workflows/preview.yml))
runs once a day. It takes the newest commit on main whose Tests workflow passed on Windows and Linux,
and builds it only when neither the newest release nor the newest preview contains it already. The
build goes through the Desktop build workflow, with all its checks, and is published as a
pre-release with the same files as a release and notes that link to its commit. Its version is the
next patch after the newest release and the date: after 0.2.0 comes `0.2.1-preview.20261021`, and a
second preview on the same day is `0.2.1-preview.20261021.2`, so a preview sorts after the release
before it and before the next one. Previews are removed together with their tags after a week,
except the newest, and releases are never touched. `gh workflow run preview.yml` runs it at once. A
preview tag pushed by hand is published the same way, once the Desktop build workflow has checked
that it follows the newest release. The script that decides all this,
[`.github/scripts/preview_plan.py`](../.github/scripts/preview_plan.py), describes the rules at its
top.

## Self-test

`Sedjem --self-test [--window] [--models] [--report file.json]` checks an installation without
touching your data. It starts the model process and has it import everything a local run needs,
checks that the interface and API answer, then uploads a generated tone, converts it to 16 kHz FLAC
and has it "transcribed" by a stand-in for the ElevenLabs API on 127.0.0.1. It also converts a
generated MPEG-4 clip to the H.264 video the job page shows, which fails if the build's FFmpeg has no
H.264 encoder. It starts the assistants' server (`Sedjem --mcp`) the way an assistant does, on a data
folder with one transcription, and checks over pipes that it answers in both versions of the MCP
protocol with nothing else on its output. With `--window`, a native window opens, loads the app and closes. With `--models`
(about 820 MB of downloads into a temporary folder), whisper-medium and the voiceprint model are fetched with the app's own downloader, and a
public 11-second speech sample (JFK, from the openai/whisper repository) is transcribed with speaker
labels. On the test laptop that gave *"and so my fellow americans, ask not what your country can do
for you…"* in 16 s.

## What changed to make it cross-platform

- No ffmpeg program is needed. Recordings are converted with PyAV, using the FFmpeg libraries that
  faster-whisper already ships, which takes about 7 s for 30 minutes of audio on the test laptop.
- Model runs start in their own process group on Linux, or with `CREATE_NO_WINDOW` on Windows, and
  are stopped the right way on each, so *Cancel* works on both.
- Text files are UTF-8 everywhere, including the model process's output. Windows would otherwise use
  a code page and fail on Arabic.
- Windows file locking: replacing a file that another thread is reading is retried, and progress
  updates that collide are skipped.
- `transcribe.py` works without the Unix-only `resource` module (peak memory is then not recorded).

## Verified so far

On the Linux test laptop:
- The unit tests: the app's API, the hosted-service parsers, downloads (resume, a server that ignores
  ranges, a checksum mismatch, cancel), paths, the worker command and the forced-alignment path.
- The desktop build: `--self-test` passes, and it transcribed the public demo clip with Cohere (51 s)
  and whisper-medium (65 s), with 7 speakers told apart by voice, the same results as from source.
- A real model download (the 40 MB voiceprint model) through the build, with its checksum. Every
  other pinned URL was checked: the small files downloaded and verified, the large ones by their
  published size.

On Windows (GitHub Actions, `windows-latest`, 25 September 2026) all checks passed: the unit tests,
the self-test from source and of the built `Sedjem.exe`, the real-model test (whisper-medium and the
voiceprint model downloaded in 15 s, and the speech sample transcribed in 13 s: *"and so my fellow
americans, ask not what your country can do for you…"*), the native window (it opened, loaded the
app and closed, through Edge WebView2), and the installer (`Sedjem-0.1.0-setup.exe`, 77 MB). The
first Windows run found one real bug: reading a job's status file while another thread replaced it
failed on Windows, never on Linux. Such reads are now retried. The whole run took about 4 minutes.

Not yet tested on Windows: the native open and save dialogs, and a long local transcription by
hand.

## Not done yet

- Code signing. Unsigned installers trigger a Windows SmartScreen warning ("Windows protected your
  PC"; choose *More info*, then *Run anyway*).
- Smaller updates. Each update downloads the whole installer or archive, not only the files that
  changed ([updates](12-updates.md)).
- macOS. pywebview and PyInstaller support it, but it hasn't been built or tested by the project.
- Linux packages. There is no AppImage or .deb yet.
- Windows before 10 may lack WebView2, and the installer doesn't bundle its bootstrapper; Sedjem
  then opens in a browser window.
- GPU on real NVIDIA hardware. Cohere runs on any GPU through the bundled Vulkan backend, and Whisper on
  NVIDIA GPUs once the NVIDIA libraries are downloaded ([the command line](05-command-line.md#gpu)). The
  Vulkan path was measured on an integrated Intel GPU; the CUDA path is covered by tests with stand-ins
  but hasn't run on an NVIDIA card yet.
- Size: about 470 MB unpacked, which could shrink by dropping the unused CPU variants of
  transcribe.cpp.
