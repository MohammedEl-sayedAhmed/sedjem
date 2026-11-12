---
id: AgDR-0017
timestamp: 2026-11-12T12:16:47Z
agent: claude-code
model: claude-opus-5-5
trigger: user-prompt
status: executed
---

# The Release workflow starts the website's build in a job of its own

> In the context of releases made with the Release workflow, facing a website that kept the previous release's downloads because nothing started its build, I decided to add a job to `release.yml` that follows the whole build, whatever winget's result, and starts the Pages workflow on main when the release is a published stable one, to achieve a site that offers each release's downloads without a run by hand, accepting that the site waits until winget has ended, up to 15 minutes.

## Context

- After 0.3.3 was published on 9 November 2026, the website offered the 0.3.2 downloads for about 7.5 hours, until a
  Pages run was started by hand ([issue #81](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/81)).
- The Release workflow calls `desktop.yml` as a reusable workflow, so the run is "Release", and `pages.yml`'s
  `workflow_run` on "Desktop build" does not fire. The release is published with the workflow's token, so
  `release: published` starts nothing either.
- winget runs after the release is published. It failed for 0.3.3 because the first submission is not merged yet,
  and that failed the whole run. Pages follows successful runs only.
- A job that starts another workflow needs `actions: write`. A job of a called workflow gets no more than its
  caller grants, and `desktop.yml` has two callers: the Release workflow and the nightly preview.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| A job in `release.yml` after the build, whatever its result, that starts Pages when the release is a published stable one | Only this job gets `actions: write`; `desktop.yml` and the nightly preview do not change; a failed winget does not stop it | The site waits until winget has ended, up to 15 minutes |
| A step at the end of the publishing job in `desktop.yml`, for a stable release | The site starts a few minutes sooner | The job that publishes the release gets `actions: write`, and so do both callers, the nightly preview too; a tag pushed by hand would start Pages twice |
| Add "Release" to `pages.yml`'s `workflow_run` list | No new job | Pages follows successful runs only, so a failed winget still stops it; following failed runs would also build after a failed check or build |

## Decision

Chosen: a job in `release.yml`, because it gives the one permission to the one job that needs it and leaves the
publishing job and the nightly preview as they are. The `site` job needs the tag and the build jobs, and runs unless
the run was cancelled or the tag was not made. It reads `vX.Y.Z` through the API. When that is a published stable
release, it runs `gh workflow run pages.yml --ref main`, as `pages.yml`'s `on-main` job does for a release published
by a person. When the release is not published, or is a pre-release, it starts nothing and leaves a notice. When the
API or its answer cannot be read, it fails, so that a missed build shows in the run. The API's errors are kept apart
from its answer, so that a warning is never read as part of the release.

## Consequences

- The Pages workflow starts at most 15 minutes after a stable release is published, once winget has ended, also
  when winget fails. The site shows the release once that build is deployed, a few minutes later.
- A run whose release was not published, such as a refused approval, starts no build.
- A tag pushed by hand still reaches the site through the Desktop build's success, as before.
- `tests/test_pipeline.py` lists the job among those that may write, and checks its condition and its script.

## Artifacts

- `.github/workflows/release.yml`, the `site` job
- `.github/workflows/pages.yml`, its comments
- `docs/14-pipeline.md`, the table and "Cutting a release"
- `tests/test_pipeline.py`
