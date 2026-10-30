# Agent decision records

This folder holds agent decision records (AgDRs): decisions an AI coding agent made while building Sedjem, when it
picked between real options. Each says what the situation was, which options there were with their pros and cons,
which one was chosen and why, and what follows from it. Decisions the maintainer made are architecture decision
records, in [docs/adr](../adr/README.md); [ADR 1](../adr/0001-record-decisions-as-adrs-and-agdrs.md) says which kind
goes where.

## The format

The records follow the Agent Decision Record specification by me2resh,
[github.com/me2resh/agent-decision-record](https://github.com/me2resh/agent-decision-record) (its `SPEC.md` and
`agdr-template.md`), which is licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Sedjem uses it with one change: its docs don't use bold,
so the Y-statement is one plain blockquote line and the decision reads "Chosen: X, because ..." without emphasis.

In short:

- A file is named `AgDR-NNNN-slug.md`, numbered in order, with a lowercase, hyphenated slug of at most 50
  characters.
- The frontmatter has `id` (the file's `AgDR-NNNN`), `timestamp` (ISO 8601 with the time and its zone, here UTC),
  `agent` (the tool, such as `claude-code`), `model` (the model in use), `trigger` (`user-prompt`, `hook`,
  `automation` or `self-initiated`) and `status` (`proposed`, `executed`, `superseded` or `deprecated`), and
  `supersedes` when a record replaces an earlier one.
- The first line after the title is the Y-statement: "In the context of ..., facing ..., I decided ... to achieve
  ..., accepting ...", every part of it specific.
- Options Considered is a table of at least two real options with their pros and cons, and Decision starts
  "Chosen:" with the reason after "because". Context, Consequences and Artifacts follow when they have something to
  say.

## When an agent writes one

An agent working on Sedjem writes an AgDR, in the same pull request as the change, when it picks between real
options: a library to add, a pattern, an architecture, a convention, or a tool or check to leave out. It doesn't
write one for trivial choices, such as names or formatting, for following a convention the project already has, or
for a bug fix with one obvious solution. A record isn't edited once it is executed: a later decision that replaces
it gets a new record with `supersedes`, and the old one's status becomes `superseded`.

The records below were written on 30 October 2026, after the decisions, from the commits, pull requests and docs
they cite; each timestamp is that of the commit that carried the decision out, in UTC. Records 2 to 5 come from
[PR #34](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/34) and record 6 from
[PR #30](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/30); PR #30 merged the same day. Records 7 to 11 come
from [PR #39](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/39), running jobs on another computer. Records
12 to 14 were written with the change they record, which pins everything the pipeline installs by hash
([ADR 14](../adr/0014-everything-the-builds-install-is-pinned-by-hash.md)), each stamped with the time it was
decided.

## Index

| # | Title | Status | Summary |
|---|---|---|---|
| AgDR-0001 | [Update now restarts on a checkout already past the offer](AgDR-0001-update-now-restarts-on-a-checkout-past-the-offer.md) | executed | A checkout pulled by hand past the offered commit starts again on its own code, with the changed requirements installed, instead of failing as diverged. |
| AgDR-0002 | [Release notes drafted from commit titles into the notes file](AgDR-0002-release-notes-drafted-from-commit-titles.md) | executed | A draft grouped by each title's area goes into .github/release-notes/, is edited in a commit, and replaces GitHub's generated notes. |
| AgDR-0003 | [GitHub's secret scanning instead of gitleaks](AgDR-0003-github-secret-scanning-instead-of-gitleaks.md) | executed | GitHub's own scanning and push protection cover leaked keys; gitleaks would mostly flag the tests' stand-in keys. |
| AgDR-0004 | [No labeler, so no workflow runs on pull_request_target](AgDR-0004-no-labeler-and-no-pull-request-target.md) | executed | A fork's pull request never runs with the repository's secrets or a write token. |
| AgDR-0005 | [No pip cache in the workflow that publishes](AgDR-0005-no-pip-cache-in-the-publishing-workflow.md) | executed | After CodeQL's cache-poisoning alert, the Desktop build downloads its packages afresh; the test jobs keep their cache. |
| AgDR-0006 | [Small lines on the website's number cards, as monotone curves drawn at build time](AgDR-0006-monotone-sparklines-drawn-at-build-time.md) | executed | A monotone cubic through the days never overshoots a value, and build.py draws it as SVG, so the page needs no chart script. |
| AgDR-0007 | [Remote runs over HTTPS on a port of their own, to a typed address](AgDR-0007-remote-runs-over-stdlib-https-on-their-own-port.md) | executed | A standard-library HTTPS server on port 8766, polled by the sending computer, apart from the app's server on 127.0.0.1; the user types the other computer's address, and nothing announces it. |
| AgDR-0008 | [Pairing by a proof derived from a one-time code](AgDR-0008-pairing-by-a-proof-derived-from-a-one-time-code.md) | executed | Both sides prove they know an eight-character code under a PBKDF2 key salted with the certificate and a nonce, so pairing also authenticates the server, without a PAKE or a fingerprint to compare by eye. |
| AgDR-0009 | [A pinned certificate, and a token for each computer](AgDR-0009-pinned-certificate-and-a-token-for-each-computer.md) | executed | The certificate's fingerprint, pinned at pairing, is compared before anything is sent, and each computer has a revocable token kept hashed, instead of an authority or client certificates. |
| AgDR-0010 | [Serving off by default, private addresses only, with limits and quotas](AgDR-0010-serving-off-by-default-with-limits-and-quotas.md) | executed | The port opens only when turned on, refuses addresses outside private ranges before TLS, and caps connections, recordings and each computer's jobs and space; private isn't taken as trusted. |
| AgDR-0011 | [Jobs run for another computer kept apart from the library, and not resumed](AgDR-0011-served-jobs-kept-apart-and-not-resumed.md) | executed | A store of their own joined to the one job runner, so they share the local queue but never show in the library; deleted when collected or after a day, and marked interrupted after a restart. |
| AgDR-0012 | [Locks made by uv's universal resolution, checked for each system](AgDR-0012-locks-from-uv-universal-checked-per-system.md) | executed | One lock for each use, made on any computer, resolved again for Linux and Windows to add what the universal resolution missed, such as sherpa-onnx-core; proxy-tools built with a locked setuptools. |
| AgDR-0013 | [The locks kept current by a quick check and one command, not by Dependabot](AgDR-0013-locks-kept-current-by-a-check-not-dependabot.md) | executed | Dependabot makes hashed locks only from .in files, so it stays on the requirement files, and the quick checks fail with the command until the locks are made again. |
| AgDR-0014 | [Setups from source and Update now keep the requirement files](AgDR-0014-source-setups-keep-the-requirement-files.md) | executed | Copies that run from source and older versions that update stay as they are; the locks cover what is built and checked. |
