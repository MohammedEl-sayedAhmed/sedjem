---
status: Accepted
date: 2026-10-30
deciders: MohammedEl-sayedAhmed
---

# 14. Everything the builds install is pinned by hash

## Context

Clipman, the maintainer's other project, pins every GitHub Action to a full commit SHA, with the version in a
comment, so that a moved tag or a compromised release can't change what its pipeline runs
([its ADR 3](https://github.com/MohammedEl-sayedAhmed/clipman/blob/main/docs/adr/0003-sha-pin-github-actions.md)).
Sedjem's workflows did the same for their actions, and `tests/test_pipeline.py` checked it; actionlint and
wingetcreate were checked against their SHA-256. The maintainer asked for this to be handled here too, for
everything, not only the actions.

On 30 October 2026 the rest was open:

- The Python packages were pinned by version at the top level only, without hashes, and the packages they need in
  turn not at all. The Desktop build bundled whatever pip resolved on the day it ran, so a compromised release of
  any of them would have shipped inside Sedjem.
- `python -m pip install --upgrade pip` installed the newest pip in the Tests and Desktop build workflows.
- ruff, pip-audit and uv were installed by version, without hashes.
- When the Windows runner had no Inno Setup, `choco install innosetup` installed one with no version and no
  checksum.

## Decision

Nothing the workflows install or run may change without a commit that changes it:

- Every Python package the workflows install comes from a lock in `.github/locks/`, with the SHA-256 of each of
  its files, the packages' own dependencies included, for Linux and Windows on Python 3.12. They are installed
  with `--require-hashes`. `.github/scripts/locks.py` makes the locks from the requirement files, which stay what
  people edit and what Dependabot updates, and the quick checks fail when a lock wasn't made again after a change.
  [AgDR-0012](../agdr/AgDR-0012-locks-from-uv-universal-checked-per-system.md) says how the locks are made, and
  [AgDR-0013](../agdr/AgDR-0013-locks-kept-current-by-a-check-not-dependabot.md) how they stay current.
- pip isn't upgraded: the runner's own is used, or the one the tools' lock pins by hash.
- A program a run downloads is checked against its SHA-256 before it runs: actionlint, wingetcreate, Inno Setup
  6.7.1 from its official downloads when the runner has none, and the newest release's installer against that
  release's `SHA256SUMS.txt`. Scripts are fetched only from this repository, at the run's commit.
- `tests/test_pipeline.py` checks that every pip and uv install is from a lock with hashes, that nothing is
  upgraded, that no other installer runs, and that every download has its check.
- Setups from source and *Update now* keep installing the requirement files
  ([AgDR-0014](../agdr/AgDR-0014-source-setups-keep-the-requirement-files.md)).

## Consequences

- A compromised release of any package, a dependency's dependency included, can't reach a build or a test until a
  commit changes the lock, and that commit's diff shows the versions and the hashes.
- Every change to a requirement file needs `python .github/scripts/locks.py` and the locks in the same commit.
  Dependabot's pull requests for the Python packages fail the quick checks until that is done on their branch.
- The packages only a lock names get no pull request from Dependabot. pip-audit's runs on the locks, on every push
  and once a week, say when one has a known vulnerability.
- The runner image's own software, such as Python with its pip, Chrome and the Inno Setup it comes with, is still
  GitHub's and changes with the image.
- Build provenance for the release files (GitHub's attest-build-provenance action) is left for a change of its
  own, since only a real release can try it.
