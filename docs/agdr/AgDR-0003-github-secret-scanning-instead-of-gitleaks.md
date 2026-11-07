---
id: AgDR-0003
timestamp: 2026-10-30T15:14:17Z
agent: claude-code
model: claude-opus-5-5
trigger: user-prompt
status: executed
---

# GitHub's secret scanning instead of gitleaks

> In the context of the security checks of the pipeline for issue #14, adapted from clipman, the maintainer's other project, which runs gitleaks, facing the choice of a secret scanner for a public repository, I decided to rely on GitHub's own secret scanning and push protection and add no gitleaks job to achieve leak detection without a scanner that would mostly flag the tests' stand-in keys and the public key of the usage counts, accepting that the generic patterns gitleaks would add are not checked.

## Context

- The repository is public, and its tests use stand-in API keys for the hosted services.
- The usage counts' Aptabase app key is shipped in `app/config.toml` on purpose
  ([usage counts](../13-usage-counts.md)).
- GitHub's secret scanning and push protection are free for public repositories.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| A gitleaks job, as clipman has | Generic patterns on every push and pull request | Would mostly find the tests' stand-in keys and the public usage-counts key, which then need an allowlist kept up to date; one more job |
| GitHub's secret scanning and push protection only | Already looks for leaked keys, and push protection blocks a push with a known one; no workflow or allowlist to keep | Only GitHub's own patterns |

## Decision

Chosen: GitHub's secret scanning and push protection, because they already look for leaked keys at no cost, while
a separate scanner such as gitleaks would add generic patterns that here would mostly find the tests' stand-in keys
and the public key of the usage counts.

## Consequences

- No allowlist of fake keys to maintain, and no job that fails on them.
- A secret in a format that only a generic pattern would catch can go unnoticed.
- The protection lives in the repository's settings, not in a workflow file, so it isn't visible in a review.

## Artifacts

- [PR #34](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/34), not merged at the time of writing; its
  description lists gitleaks among the parts of clipman's pipeline left out
- `docs/14-pipeline.md` on that branch, Security, from the commit "Docs: the pipeline, from a change to a release"
