---
status: Accepted
date: 2026-10-20
deciders: MohammedEl-sayedAhmed
---

# 7. Updates: asked first, two channels, and only tested builds

## Context

[Issue #11](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/11) asked for Sedjem to say when a newer version
is out and to update from the app, from stable releases or from the latest changes on main. Checking contacts
GitHub's API, which sees the computer's IP address. Sedjem keeps everything on the computer
([ADR 2](0002-recordings-and-transcripts-stay-on-the-computer.md)), and the issue asks before it checks. Sedjem runs
as a git checkout, as the Windows installer and as the Linux build, and each updates differently.

## Decision

From [3199189](https://github.com/MohammedEl-sayedAhmed/sedjem/commit/3199189),
[5b67856](https://github.com/MohammedEl-sayedAhmed/sedjem/commit/5b67856) and
[3872368](https://github.com/MohammedEl-sayedAhmed/sedjem/commit/3872368) ([updates](../12-updates.md)):

- On first start Sedjem asks once whether to check for updates automatically. With yes it checks in the
  background once a day; with no, only when asked. *Check now* works either way.
- Two channels: *Stable releases*, the default, and *Latest changes*, which offers the newest commit on main whose
  Tests workflow passed on both Windows and Linux. A commit whose tests failed or are still running is never
  offered.
- A desktop app on *Latest changes* follows nightly preview builds. A scheduled workflow builds one only from
  main's newest tested commit, publishes it as a pre-release, and removes previews older than a week; releases are
  never touched.
- Nothing is downloaded or installed without a click on *Update now*. A checkout is moved only by a fast-forward of
  a clean main. Downloads come only from this repository's releases and are checked against their
  `SHA256SUMS.txt`.
- The checks need no login, and the docs list what each sends to GitHub
  ([what is sent](../12-updates.md#what-is-sent-to-github)).

## Consequences

- Users can follow main between releases without running untested code.
- A failing main holds back the *Latest changes* channel and the nightly preview until a commit passes again, which
  makes a green main matter ([ADR 13](0013-branch-protection-for-a-single-maintainer.md)).
- GitHub sees the IP address of each check and, for some checks, the commit that runs.
- On Linux the build is unpacked beside the old one rather than replaced, and each update downloads the whole
  installer or archive ([not done yet](../10-desktop.md#not-done-yet)).
