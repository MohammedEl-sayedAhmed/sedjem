---
id: AgDR-0013
timestamp: 2026-10-30T18:35:00Z
agent: claude-code
model: claude-opus-5-5
trigger: user-prompt
status: executed
---

# The locks kept current by a quick check and one command, not by Dependabot

> In the context of Dependabot's weekly pull requests for the requirement files, facing Dependabot making hashed locks only from `.in` files that hold the pins, which would move the pins out of the files that setups from source and older versions of Update now install, I decided to keep Dependabot on the requirement files and have the quick checks fail a pull request whose locks weren't made again, with the command that makes them, to achieve locks that change only in a commit someone made and pushed, accepting one command and a push on each of Dependabot's pull requests for the Python packages.

## Context

- GitHub's documentation of the ecosystems Dependabot supports says that for pip and pip-compile it "supports
  updates to any `.txt` file", and lists pip-compile 7.5.3 and uv 0.11, without saying how it treats hashes or
  `uv pip compile`.
- Dependabot's source code ([dependabot-core](https://github.com/dependabot/dependabot-core), `python/` and `uv/`)
  shows the rest. The pip ecosystem makes a compiled file again by running pip-compile in its own container on
  Linux, keeping `--generate-hashes`. The uv ecosystem runs `uv pip compile` again, keeping `--universal`,
  `--generate-hashes` and `--python-version` from the file's header. Both start from `.in` files: only an `.in`
  file gets the new version before the compile, so the pins have to live in `.in` files.
- Sedjem's pins are in `requirements.txt`, `requirements-desktop.txt` and `requirements-dev.txt`, which README,
  `app.cmd` and *Update now* install. An *Update now* that already runs on a computer installs every top-level
  `requirements*.txt` that changed, and any of them that names a package installed there; if those files became
  locks, it would install the desktop and the test packages into setups that never had them.
- GitHub doesn't start workflows for what a workflow pushes with its own token. Its documentation says, since May
  2026, that a pull request such a workflow opens or updates gets runs that wait until someone with write access
  selects "Approve workflows to run". A workflow that Dependabot's pull request starts has a read-only token by
  default.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Dependabot's uv ecosystem, with the pins moved into `.in` files and the requirement files made from them | Dependabot makes the locks, hashes included, in its own pull request | The requirement files become locks, which setups from source and running copies' Update now would install, the test packages included; Dependabot's engines group and its PyAV rule move to a new ecosystem |
| A workflow that makes the locks on Dependabot's branch and pushes them | No command to run | Its push with the workflow's token waits for an approval click, or needs a personal token or an app as a secret; a job with write access next to a resolution that can build packages from source; can only be tried on Dependabot's next pull request |
| The quick checks fail with the command, and a person runs it on the branch | Nothing new to trust or to configure; tested here; the check also catches a requirement changed by hand | One command and a push for each of Dependabot's pull requests for the Python packages |

## Decision

Chosen: the quick check and the command, because it is the smallest change that keeps every lock correct, adds no
token, secret or job with write access, and leaves Dependabot's configuration, the engines group of PR #40
included, as it is. `python .github/scripts/locks.py check` runs first in the `checks` job and names the command
when a lock wasn't made from the files as they are.

## Consequences

- Dependabot's pull requests for the Python packages are red at `checks` until someone runs
  `python .github/scripts/locks.py` on their branch and pushes the locks; that push starts the checks like any
  other. `@dependabot recreate` drops the commit, and the locks have to be made again.
- Dependabot proposes no update for the packages only a lock names. pip-audit on the locks says when one has a known
  vulnerability, and `python .github/scripts/locks.py --upgrade-package NAME` moves it.
- If the command becomes a chore, the workflow above is the next step: one job without write access makes the
  locks and uploads them, a second one pushes them, and the maintainer approves the runs of that push.

## Artifacts

- `.github/workflows/tests.yml`, the `checks` job's Locks step, and `.github/scripts/locks.py check`
- `docs/14-pipeline.md`, Locks
