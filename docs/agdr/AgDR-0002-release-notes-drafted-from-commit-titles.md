---
id: AgDR-0002
timestamp: 2026-10-30T15:14:16Z
agent: claude-code
model: claude-opus-5-5
trigger: user-prompt
status: executed
---

# Release notes drafted from commit titles into the notes file

> In the context of the Release workflow built for issue #14, facing a Desktop build that published GitHub's generated notes whenever a release had no notes file, I decided to have `.github/scripts/release.py` draft the notes from the titles of the commits since the previous release, grouped by the area each title starts with, into `.github/release-notes/vX.Y.Z.md`, and to publish that file as it is, to achieve notes that cover every commit and are edited in an ordinary commit before the release, accepting that a release run without an edited file publishes the raw draft.

## Context

- [Issue #14](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/14) asks for release notes drafted from the
  commit titles since the last release, grouped by the area each title starts with, and edited before publishing.
- The notes of 0.1.0 to 0.3.1 were written by hand in `.github/release-notes/`. On main, the Desktop build publishes
  that file when there is one, and otherwise asks GitHub to generate notes, which list the merged pull requests.
- Changes also reach main without a pull request, and the repository's commit titles start with their area
  ("Updates: ...", "Website: ...").

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Keep GitHub's generated notes when there is no file | Nothing to write or maintain; links each pull request | Leaves out what was pushed straight to main; not grouped by area; nothing to edit before it is published |
| Draft from the commit titles by area into the notes file, and publish the file | Every commit since the previous release counts; grouped as issue #14 asks; edited and reviewed as a commit; a dry run and tests | Only as good as the commit titles; one more script to maintain |

## Decision

Chosen: the draft written into the notes file, because it is what issue #14 asks for, the commit titles already
carry their area, and the hand-written notes already live in that file, which the Desktop build used first. A file
that is there is used as it is, so hand-written notes still win, and editing the draft is a commit like any other.

## Consequences

- Cutting a release starts with `python3 .github/scripts/release.py notes X.Y.Z`, an edit and a commit.
- Commit titles have to keep starting with their area. Areas that don't reach the app, such as tests, docs, the
  website and the pipeline, are listed last.
- Every release has notes, since a draft is always made.
- A release run before the draft is edited publishes it as drafted.

## Artifacts

- [PR #34](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/34), not merged at the time of writing: the commit
  "Release: a workflow run by hand that checks, tags, builds and publishes", with `.github/scripts/release.py`,
  `.github/workflows/release.yml` and `tests/test_release.py`
- `docs/14-pipeline.md` on that branch, Cutting a release
