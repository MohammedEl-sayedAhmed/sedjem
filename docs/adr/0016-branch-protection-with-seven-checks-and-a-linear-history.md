---
status: Accepted
date: 2026-11-07
deciders: MohammedEl-sayedAhmed
---

# 16. Branch protection with seven checks and a linear history

## Context

[ADR 13](0013-branch-protection-for-a-single-maintainer.md) protected main with three required checks, and named a
fourth, `checks`, that should join them. Since then `checks` has been added to the settings, and the pipeline has
grown. [Issue #57](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/57) compared it with the pipeline of
Clipman, the maintainer's other project, and found these gaps in what main requires:

- The dependency review runs on every pull request but is not required, so a pull request that adds a vulnerable
  dependency can merge.
- CodeQL is not required. It cannot be required as it is, since 23 alerts are open and every pull request would
  fail ([issue #58](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/58)). The ratchet of
  [AgDR-0015](../agdr/AgDR-0015-codeql-ratchet-on-a-baseline-branch.md) fails a pull request only on new findings, so
  it can be required.
- A branch need not be up to date with main when it merges, so two pull requests that each pass can break main
  together.
- Merged branches stay, and history can hold merge commits.
- Any run of the Release workflow publishes at once. The `release` environment exists, but without a reviewer.

Sedjem still has one maintainer, and GitHub does not count the author of a pull request as its reviewer, so
required reviews stay off for now.

## Decision

This replaces ADR 13. The repository's settings are changed to the following, and the branch protection API shows
them:

- A pull request can merge into main only when these checks have passed, and its branch is up to date with main:
  `checks`, `test (ubuntu-24.04)`, `test (windows-latest)`, `windows-source`, `dependency-review`, `codeql-ratchet`
  and `footprints`. The first four are the Tests workflow's, `dependency-review` is the job of that name in the
  Security workflow, `codeql-ratchet` is the CodeQL workflow's gate, and `footprints` is the check of
  [ADR 15](0015-no-ai-tool-attribution-in-the-history.md). `tests/test_pipeline.py` keeps the list and the jobs'
  names together.
- History on main stays linear. Squash merging is the only way to merge a pull request; merge commits and rebase
  merges are off.
- A branch is deleted after its pull request merges.
- main cannot be force-pushed or deleted.
- No review is required, and administrators are not bound, so the maintainer can still push a small fix to main
  directly ([the pipeline](../14-pipeline.md#before-main)).
- The `release` environment has the maintainer as its required reviewer. Only a stable release uses that environment:
  the Desktop build's release job names it when the channel is stable, and a nightly preview names none
  (`.github/workflows/desktop.yml`). Preventing self-review stays off, since the maintainer is the only reviewer.

The order of the change matters. The new workflows come to main first, and the settings follow. A required check that
no workflow produces yet would block every pull request, and `codeql-ratchet` does nothing useful until the first
push to main has written the `security-baseline` branch. Until then it passes with a notice. Once the branch
exists, the repository variable `CODEQL_RATCHET_ARMED` is set to `true`, so that a missing branch fails the check.
A ruleset on `security-baseline`, with no bypass actor, blocks deleting the branch and pushing to it with force; the
CodeQL workflow and the guard only add commits on top, so it never blocks them. A ruleset that would also stop
other pushes needs GitHub Actions as a bypass actor, which GitHub accepts only in a repository that an organisation
owns. The guard workflow puts such pushes back, and the armed check fails a pull request while the baseline is
missing.

## Consequences

- A pull request has to be updated with main before it merges, which starts its checks again. With one maintainer and
  few open pull requests this costs a click, and it closes the gap where two green pull requests break main together.
- `dependency-review` and `codeql-ratchet` run only for pull requests, so they never block a push to main, and the
  maintainer's direct pushes stay possible.
- `codeql-ratchet` runs also when a CodeQL analysis failed, and then fails, since GitHub counts a skipped required
  check as passed. A CodeQL outage therefore blocks merges until it ends or an administrator merges.
- `footprints` is required, so a Dependabot pull request must pass it too. Its description is skipped by the check,
  its commits are scanned.
- The first run of a stable release waits for approval. The maintainer approves their own run, so the step is a pause
  to read the notes and not a second pair of eyes; a second maintainer could change that.
- The Scorecard workflow ([AgDR-0016](../agdr/AgDR-0016-weekly-openssf-scorecard.md)) runs again when these rules
  change, so its next result reflects them.
- When a second maintainer joins, required reviews, code owners and self-review prevention can be turned on in a new
  record that supersedes this one.
