---
id: AgDR-0005
timestamp: 2026-10-30T15:22:44Z
agent: claude-code
model: claude-opus-5-5
trigger: automation
status: executed
---

# No pip cache in the workflow that publishes

> In the context of the Desktop build workflow, which builds and publishes releases, facing CodeQL's actions/cache-poisoning alert on the pip cache it restored after checking out the commit being released, I decided to drop the pip cache from that workflow and keep it in the test jobs to achieve releases built only from packages downloaded afresh, accepting that each desktop build downloads its packages again.

## Context

- [Issue #14](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/14) asks for quick runs, with caches for pip.
- The Desktop build publishes releases, and on main it restores pip's cache after checking out the commit it
  builds.
- CodeQL, added in the same pull request, flagged that restore with its actions/cache-poisoning query: a cache
  written by an earlier run could be restored into a release.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Keep the pip cache in the Desktop build | Faster builds | A cache written by an earlier run can reach a release; the alert stays open |
| No pip cache in the Desktop build, and the test jobs keep theirs | Nothing from an earlier run reaches a release; the alert is resolved; the test runs stay quick | Every desktop build downloads its packages |

## Decision

Chosen: no pip cache in the Desktop build, because it is the workflow that publishes, and a restored cache could
carry an earlier run's packages into a release. The test jobs publish nothing, so they keep their cache.

## Consequences

- Each desktop build installs from freshly downloaded packages.
- Desktop builds don't get faster from the cache that issue #14 asked for.
- `tests/test_pipeline.py` checks that no step of the Desktop build restores a cache.

## Artifacts

- [PR #34](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/34), not merged at the time of writing: the commit
  "Desktop build: no pip cache in the workflow that publishes, so no earlier run's cache reaches a release"
- `docs/14-pipeline.md` on that branch, What runs when
