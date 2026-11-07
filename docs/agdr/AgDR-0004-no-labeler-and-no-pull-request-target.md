---
id: AgDR-0004
timestamp: 2026-10-30T15:14:17Z
agent: claude-code
model: claude-opus-5-5
trigger: user-prompt
status: executed
---

# No labeler, so no workflow runs on pull_request_target

> In the context of the workflows of the pipeline for issue #14, adapted from clipman, which labels pull requests with a labeler, facing a labeler that needs the pull_request_target event to label pull requests from forks, an event that runs with the repository's secrets and a write token, I decided to leave the labeler out and run every workflow on pull_request to achieve pull requests from forks that run with a read-only token and no secrets, accepting that pull requests are labelled by hand.

## Context

- The repository is public, so anyone can open a pull request from a fork.
- With `pull_request`, a fork's pull request gets a read-only token and no secrets. With `pull_request_target`, the
  workflow runs in the context of the base repository, with its secrets and a token that can write.
- The repository has two secrets, `WINGET_TOKEN` and `STATS_TOKEN`.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| A labeler on `pull_request_target`, as clipman has | Every pull request labelled by the paths it changes, forks included | A workflow with secrets and a write token runs for any fork's pull request; a mistake in it can expose them |
| No labeler, and every workflow on `pull_request` | A fork's pull request gets a read-only token and no secrets; a test keeps it that way | Labels are added by hand |

## Decision

Chosen: no labeler, because it would need `pull_request_target`, and with that any fork's pull request would run a
workflow holding the repository's secrets and a token that can write. Without it, no workflow gives a fork's pull
request either.

## Consequences

- `tests/test_pipeline.py` fails if any workflow uses `pull_request_target`, and checks which workflows use each
  secret: `WINGET_TOKEN` only for publishing to winget, `STATS_TOKEN` only in the stats workflow.
- Pull requests carry no automatic labels.

## Artifacts

- [PR #34](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/34), not merged at the time of writing; its
  description lists the labeler among the parts of clipman's pipeline left out, because it needs
  `pull_request_target`
- `docs/14-pipeline.md` on that branch, Security, and `tests/test_pipeline.py`, from the commit "Docs: the pipeline,
  from a change to a release"
