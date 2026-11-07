---
id: AgDR-0015
timestamp: 2026-11-07T15:30:00Z
agent: claude-code
model: claude-sonnet-5-5
trigger: user-prompt
status: executed
---

# A CodeQL ratchet on a baseline branch that the workflow writes through the API

> In the context of CodeQL results that main cannot require while 23 alerts are open, facing every pull request failing a required CodeQL check for findings it did not add, I decided to compare each pull request's findings with a baseline of main's findings kept on a `security-baseline` branch that the workflow writes through the GitHub API, to achieve a required check that fails only on new findings and that passes while the baseline does not exist yet, accepting a small script and a branch to look after.

## Context

- 23 CodeQL alerts are open on main, 22 of them high ([issue #58](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/58)).
  CodeQL as it is reports them on every run, so a required check would fail every pull request until all are fixed
  or dismissed.
- Clipman, the maintainer's other project, solved the same problem with a ratchet
  ([its ADR 2](https://github.com/MohammedEl-sayedAhmed/clipman/blob/main/docs/adr/0002-baseline-ratchet-for-codeql.md)
  and [ADR 8](https://github.com/MohammedEl-sayedAhmed/clipman/blob/main/docs/adr/0008-ratchet-fingerprint-strategy.md)):
  the SARIF files of main's scan become a set of fingerprints on an orphan branch, a pull request fails on a
  fingerprint outside it, and a second workflow reverts pushes to the branch. Its first fingerprint, rule and line,
  broke when lines moved; it moved to the hash that CodeQL puts in `partialFingerprints.primaryLocationLineHash`.
- Sedjem's other branch that a workflow writes, `stats`, is written through the API with no checkout and no stored
  token (`.github/workflows/stats.yml`), and its tests pin that.
- A push or API call made with a job's `GITHUB_TOKEN` starts no workflow.
- The ratchet's own pull request is the first one the check ever sees, and the branch cannot exist before main has
  run the workflow once.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Clipman's design as it is: a job checks out the branch, rewrites a JSON file with an inline script and pushes with git | Proven in Clipman; no new design | The branch is checked out in a job with `contents: write` and a credential that git keeps; the logic is a heredoc that no test runs; it needs a retry loop around `git push` |
| The same fingerprints, but kept in `baseline.json` and written through the git data API by a tested script, with no checkout of the branch | No credential kept by git, as with the stats branch; the logic is a script with unit tests; the write is a fast-forward that fails when the branch moved | A script of about 200 lines to look after; the fingerprint is a CodeQL implementation detail that could change |
| GitHub's own comparison of alerts per ref, through the code-scanning API (or the ruleset rule that requires code scanning results) | No state to keep and no branch; uses GitHub's own matching | Not tried here. The results of a pull request arrive after the upload has been processed, so a job would have to wait for them; the alerts of a fork's pull request may be unreadable with its read-only token; alerts dismissed in the Security tab change what counts as new; the check would be out of the repository's own tests |
| Require CodeQL as it is | Nothing to build | Blocked by 23 open alerts: every pull request fails until they are fixed (issue 58) |

## Decision

Chosen: the same fingerprints kept in `baseline.json` on the `security-baseline` branch, written through the git data
API by `.github/scripts/codeql_ratchet.py`, because it keeps the one design that already worked for the maintainer,
needs no stored credential, as with `stats`, and puts the logic where unit tests can run it. These are the details:

- A fingerprint is the rule plus CodeQL's hash of the code around the finding, so a finding survives lines added above
  it; without the hash it falls back to rule, file and line. Counts are kept, so a copy of a known finding is new.
- The job `codeql-ratchet` is the required check. It runs also when an analysis failed and then fails, because GitHub
  counts a skipped required check as passed.
- The job `update-baseline` writes the branch after a push to main, on the weekly run and on a run by hand on main. The
  weekly run matters: new queries can report findings that main already had, and without a refresh every pull request
  would fail on them until the next merge, which the failing check forbids. A write from an older commit never replaces
  a newer baseline, and writes happen one at a time.
- `codeql-baseline-guard.yml` restores the files when anyone else pushes to the branch, and opens or updates an issue.
  A push made with the job's token starts no workflow, so every run of the guard is someone else's push; it skips
  github-actions[bot] only to rule out a loop. GitHub reads a push's workflows from the pushed commit, so each
  baseline commit is built on the tree of the main commit it comes from, plus `baseline.json`; an orphan branch with
  one file would not run the guard. The guard does not see a push that removes its own file from the branch, nor a
  branch deletion, since GitHub starts no push workflow for a deletion.
- Bootstrap: while the branch does not exist, `codeql-ratchet` passes with a notice and compares nothing. That is the
  state of this pull request, and of every pull request until the first push to main after it merges. Then the
  maintainer sets the repository variable `CODEQL_RATCHET_ARMED` to `true`, and from then on a missing branch fails
  the check. A branch without `baseline.json` fails at any time, since only the CodeQL workflow writes the branch,
  and always with the file.
- The findings are not hidden. The alerts stay in the Security tab; the baseline only decides whether a pull request
  passes.

## Consequences

- A pull request that adds a CodeQL finding fails `codeql-ratchet` and names the file and line; one that adds none
  passes.
- Fixing a finding lowers the baseline at the next write, so main cannot get worse unnoticed. The 23 open alerts are
  still fixed or dismissed on their own schedule.
- Until the branch exists the check proves nothing. The maintainer should make it required after the first push to
  main has written the branch.
- A ruleset on `security-baseline`, with no bypass actor, blocks deleting the branch and pushing to it with force.
  It never blocks the CodeQL workflow or the guard, since both only add commits on top. A ruleset that would also
  stop other pushes needs GitHub Actions as a bypass actor, which GitHub accepts only in a repository that an
  organisation owns, so such pushes stay possible here: the guard workflow puts them back. If the branch went
  missing all the same, the next push to main, the weekly run or a run by hand on main writes it again, and until
  then, with `CODEQL_RATCHET_ARMED` set, every pull request fails the check.
- A pull request can change the script or the workflow that judges it. The check stops a slip, not a person who
  rewrites it.
- If CodeQL changes its fingerprint, the baseline no longer matches. The next write on main repairs it, and until
  then pull requests fail on findings that main has.

## Artifacts

- `.github/workflows/codeql.yml`, `.github/workflows/codeql-baseline-guard.yml`
- `.github/scripts/codeql_ratchet.py`, `tests/test_codeql_ratchet.py`, `tests/test_pipeline.py`
- `docs/14-pipeline.md`, The CodeQL ratchet
