---
status: Accepted
date: 2026-10-30
deciders: MohammedEl-sayedAhmed
---

# 13. Branch protection on main for a single maintainer

## Context

[Issue #14](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/14) found that main wasn't protected, so a change
could reach it untested. On 20 October a merge pushed straight to main failed in CI, on a date written in UTC and on
Windows' short names for temporary folders, which a local run on Linux at +03:00 couldn't show. A failing main also
holds back the *Latest changes* channel and the nightly preview
([ADR 7](0007-updates-asked-first-two-channels-tested-builds.md)). Sedjem has one maintainer, and GitHub doesn't
count the author of a pull request as its reviewer.

## Decision

As the repository's settings on GitHub show on 30 October 2026, main is protected so that:

- a pull request can merge only when the checks `test (ubuntu-24.04)`, `test (windows-latest)` and `windows-source`
  have passed;
- main can't be force-pushed or deleted;
- no review is required;
- administrators aren't bound, so the maintainer can still push to main directly.

[PR #34](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/34), not merged yet, adds a quick `checks` job that the
test jobs wait for. It should join the required checks in the settings: when it fails, the jobs that need it are
skipped, and GitHub counts a skipped required check as passed. The pull request also writes down the rule in `docs/14-pipeline.md`: larger changes, and every change an agent makes, go through a pull
request, and the maintainer may push small fixes straight to main.

## Consequences

- Nothing reaches main through a pull request without passing the tests on Linux and Windows, and history on main
  can't be rewritten.
- No second person checks a change; the checks and the maintainer's own review are what stand between a change and
  main.
- A direct push by the maintainer skips the gate, and the Tests workflow then runs on main after the fact.
- When a second maintainer joins, required reviews can be turned on in a new record that supersedes this one.
