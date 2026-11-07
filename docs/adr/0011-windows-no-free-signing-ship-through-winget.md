---
status: Accepted
date: 2026-10-29
deciders: MohammedEl-sayedAhmed
---

# 11. Windows: no free code signing, and winget instead

## Context

The Windows installer isn't signed, so when it is downloaded with a browser, Windows SmartScreen warns that its
publisher is unknown ([not done yet](../10-desktop.md#not-done-yet)). SignPath Foundation signs open-source projects
at no cost, but accepts only projects without commercial dual-licensing, and Sedjem offers commercial licences beside
the AGPL ([ADR 4](0004-agpl-with-commercial-licences.md)). The pipeline's docs page, `docs/14-pipeline.md` in
[PR #34](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/34) (not merged yet), records this under Signing.

## Decision

- The commercial licences stay, so SignPath isn't used, and no paid certificate is bought for now. The pipeline has
  no signing step.
- Sedjem is published on winget, Windows' package manager, as `MohammedElsayedAhmed.Sedjem`, from
  [df14842](https://github.com/MohammedEl-sayedAhmed/sedjem/commit/df14842)
  ([PR #25](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/25), [winget](../10-desktop.md#winget)).
  `packaging/winget/make.py` writes the manifests, the first version goes to microsoft/winget-pkgs by hand, and
  later stable releases are sent by the Desktop build's winget job once the `WINGET_TOKEN` secret exists. Previews
  are never sent.
- The docs say only what has been seen to hold: a browser download of the unsigned installer warns, and Sedjem's own
  updates don't. Nothing is said about winget and SmartScreen, which nobody has tried, and
  `tests/test_windows_release.py` checks that.

## Consequences

- A browser download still shows SmartScreen's warning, and *More info*, then *Run anyway*, installs it.
- Until winget accepts the package, Sedjem installs only from the release page.
- Once there is a certificate, signing goes into the Desktop build before the installer is uploaded, so that the
  update tests run on the signed file.
