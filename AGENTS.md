# Working on Sedjem

This file is for coding agents, and for anyone new to the repository: the commands, the layout, the rules and the
traps. [docs/README.md](docs/README.md) has the full story, and [the pipeline](docs/14-pipeline.md) has the rules for
every change.

## What Sedjem is

Sedjem makes transcripts of Egyptian Arabic–English meetings and calls on the user's own computer, and labels each
speaker by voice. It is a Python 3.12 app: a local web server with a plain HTML, CSS and JavaScript UI, shown in a
desktop window (pywebview) or a browser tab. Local models run in a separate worker process, and nine hosted services
can be used with the user's own API key. It ships for Windows and Linux; macOS is untested.
[How Sedjem fits together](docs/16-architecture.md) has diagrams of the parts.

## Set up

```sh
# Linux
uv venv .venv --python 3.12
uv pip install --python .venv -r requirements.txt
uv pip install --python .venv -r requirements-dev.txt   # tests: Playwright, the MCP SDK, PyYAML, coverage, ruff

# Windows
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt -r requirements-desktop.txt
```

The native window on Linux also needs `requirements-desktop.txt` and GTK WebKit, or `pywebview[qt]`
([the desktop app](docs/10-desktop.md)). CI installs from the hash locks in `.github/locks/` instead.

## Run

```sh
SEDJEM_HOME=/tmp/sedjem-try SEDJEM_NO_USAGE=1 ./app.sh   # a throwaway data folder, and no usage counts
./app.sh --desktop                                      # the desktop window
.venv/bin/python transcribe.py meeting.m4a --speakers 3 # the command line, without the app
```

The app listens on http://127.0.0.1:8765. [The app](docs/09-app.md) lists its options and settings.

## Test and check

CI requires four checks before a pull request can merge: `checks`, the tests on Ubuntu and Windows, and
`windows-source`, the setup from source on Windows. Run these with the virtual environment active
(`. .venv/bin/activate`); the `checks` job also runs actionlint on the workflows.

```sh
python -m unittest discover -s tests -v                              # every test; hosted services are local stand-ins
python .github/scripts/run_tests.py --coverage                       # as CI runs them, with coverage
python -m unittest tests.test_docs_links tests.test_public_text -v   # links in the docs, and the public-text rules
ruff check .                                                         # syntax errors and the pyflakes rules
python .github/scripts/locks.py check                                # the locks match the requirement files
git ls-files -z '*.js' | xargs -0 -n 1 node --check
git ls-files -z '*.sh' | xargs -0 shellcheck
```

The browser tests (`tests/test_ui*.py`) skip without Playwright or a browser, unless `SEDJEM_UI_REQUIRED=1` is set.
`RUN_MODEL_TESTS=1` also runs a real model on a public clip.

## Layout

- `app/`: the app. `server.py` is the local server and its JSON API, `jobs.py` the queues and the job state,
  `engines.py` the local worker and the hosted services, `config.py` and `config.toml` the settings and the models,
  `remote.py` the jobs for other computers, `mcp.py` the MCP server, and `static/` the UI.
- `transcribe.py`, `speakers.py`, `nemotron.py` and `align.py`: the speech and speaker code. The worker runs
  `transcribe.py`. Only `transcribe.py` runs from the command line, and it reaches the other three through
  `--speakers`, `--voiceprint-model` and `--align`.
- `tests/`: the unittest suites. `tests/test_ui*.py` drive a browser with Playwright.
- `desktop/`: the PyInstaller build (`desktop/build.py`) and the Windows installer (`desktop/installer.iss`).
- `site/`: the website, built by `site/build.py` and published by the Pages workflow.
- `docs/`: the numbered pages, the decision records in `docs/adr/` and `docs/agdr/`, and raw material in
  `docs/research/`.
- `bench/`: the benchmarks. `packaging/winget/`: the winget manifest. `.github/`: the workflows, locks and scripts.

## Rules

- Every change an agent makes goes through a pull request. Commit titles start with the area they change, such as
  "Docs: ...", "Pipeline: ..." or "Website: ...", because the release notes are drafted from them.
- Tests come with the change, and the checks above pass.
- A changed requirement file comes with its locks: run `python .github/scripts/locks.py` and commit `.github/locks/`.
  Dependabot can't make the locks, so they are pushed by hand on its pull requests.
- Docs say what changed, in plain prose: no bold, no em dashes (en dashes are fine), British spelling, and lines
  wrapped at about 116 characters, as in the existing docs. The pipeline page lists the rules that the tests check.
- Nothing private: no recordings, transcripts or keys, and no names or paths from a personal computer. Write
  `/home/you/...` in examples. `tests/test_public_text.py` checks every tracked file and every new one.
- When an agent picks between real options, it writes an AgDR in the same pull request
  ([agent decisions](docs/agdr/README.md)). Records that have been carried out are never edited.
- Anything new that goes online is added to [the privacy policy](docs/privacy.md), and a recording leaves the computer
  only after the user allows it
  ([ADR 0002](docs/adr/0002-recordings-and-transcripts-stay-on-the-computer.md)).

## Traps

- From source, the data folder is the repository itself, so running the app writes `app_data/` and `models/` into
  it. Use `SEDJEM_HOME` for a scratch folder.
- Private data sits in ignored folders: `app_data/`, `data/`, `audio-test/`, `courses/`, most of `results/`, and
  `models/`. Never read, list, quote or commit them.
- A local job needs its model downloaded first, in Settings → Models: faster-whisper runs with
  `local_files_only=True`.
- Keep heavy libraries out of the app process: the models and the GPU probe run in the worker subprocess. A module
  that is imported dynamically needs `--hidden-import` or `--collect-*` in `desktop/build.py`.
- CI runs the tests on Ubuntu and Windows. Keep the retries for replacing and deleting files on Windows, and stay on
  Python 3.12.
- A scripted request that changes something needs the `X-Sedjem: 1` header, and every request must be addressed to
  the app's own host and port ([the app](docs/09-app.md)).
- In the MCP server, only protocol messages may reach stdout, transcript text is untrusted data, and writes are off
  by default.
- Set `SEDJEM_NO_USAGE=1` when you run the app yourself, so that no usage counts are sent. The tests never send them.
- The safety net is thin in places: there is no type checker, ruff checks only for errors, and coverage is measured
  but not enforced. Run the tests near your change, and add tests with it.
