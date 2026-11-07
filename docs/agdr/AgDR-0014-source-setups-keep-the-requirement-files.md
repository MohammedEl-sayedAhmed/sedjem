---
id: AgDR-0014
timestamp: 2026-10-30T18:40:00Z
agent: claude-code
model: claude-opus-5-5
trigger: user-prompt
status: executed
---

# Setups from source and Update now keep the requirement files

> In the context of the new locks for everything the workflows install, facing setups from source (README, app.sh, app.cmd) and Update now that install the requirement files on computers where Sedjem already runs, the maintainer's among them, I decided to leave them on the requirement files to achieve no change for a copy that runs from source or updates from an older version, accepting that a setup from source gets the newest versions of the dependencies' dependencies, as before.

## Context

- README and `docs/10-desktop.md` set Sedjem up with `pip install -r requirements.txt`, and on Windows
  `-r requirements-desktop.txt` too, with pip or uv; `app.sh` and `app.cmd` start what that set up.
- *Update now* on a checkout installs the requirement files that changed between the two commits, with uv or pip,
  before it moves the checkout (`install_requirements` in `app/updates.py`). The code that does it is the running
  version's, so a version already installed keeps doing what it does today, whatever the new commit says.
- It installs `requirements-desktop.txt` or `requirements-dev.txt` only when a package it names is installed there.
  A lock names many packages that every setup has, so that rule would install every lock everywhere.
- Linux setups differ: the GTK window needs a virtual environment made with `--system-site-packages`, and the Qt one
  installs `pywebview[qt]`, which no lock has.
- The maintainer's own Sedjem runs from source and updates this way, and Update now on Windows couldn't be tried
  from here.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Setups from source and Update now install the locks | The versions of the checks everywhere | Only versions after this one would do it; Update now would need to know which lock fits each setup; the Linux variants aren't in the locks; not tried on Windows |
| They keep the requirement files | Nothing changes for running copies or for older versions that update | A setup from source gets the newest versions of the dependencies' dependencies, as before |

## Decision

Chosen: they keep the requirement files, because the locks protect what every user downloads, the Windows installer
and the Linux build, and what the checks run, while a change to setups from source would reach copies that already
run, the maintainer's among them, through a path that couldn't be tried here.

## Consequences

- `app.sh`, `app.cmd`, `app/updates.py` and the setup steps in the docs are unchanged.
- The `windows-source` job installs `source.txt`, the two requirement files' lock, since every install in the
  workflows comes from a lock. A new release of a dependency's dependency that breaks a fresh
  `pip install -r requirements.txt` shows when the locks are made again with `--upgrade`, not on the next push.
- `docs/14-pipeline.md` says how to install the lock by hand on Windows, for those who want the versions of the
  checks.

## Artifacts

- `docs/14-pipeline.md`, Locks
- `.github/workflows/tests.yml`, the `windows-source` job
