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
[PR #30](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/30), neither merged when they were written; record 6
stays proposed until its code is on main.

## Index

| # | Title | Status | Summary |
|---|---|---|---|
| AgDR-0001 | [Update now restarts on a checkout already past the offer](AgDR-0001-update-now-restarts-on-a-checkout-past-the-offer.md) | executed | A checkout pulled by hand past the offered commit starts again on its own code, with the changed requirements installed, instead of failing as diverged. |
| AgDR-0002 | [Release notes drafted from commit titles into the notes file](AgDR-0002-release-notes-drafted-from-commit-titles.md) | executed | A draft grouped by each title's area goes into .github/release-notes/, is edited in a commit, and replaces GitHub's generated notes. |
| AgDR-0003 | [GitHub's secret scanning instead of gitleaks](AgDR-0003-github-secret-scanning-instead-of-gitleaks.md) | executed | GitHub's own scanning and push protection cover leaked keys; gitleaks would mostly flag the tests' stand-in keys. |
| AgDR-0004 | [No labeler, so no workflow runs on pull_request_target](AgDR-0004-no-labeler-and-no-pull-request-target.md) | executed | A fork's pull request never runs with the repository's secrets or a write token. |
| AgDR-0005 | [No pip cache in the workflow that publishes](AgDR-0005-no-pip-cache-in-the-publishing-workflow.md) | executed | After CodeQL's cache-poisoning alert, the Desktop build downloads its packages afresh; the test jobs keep their cache. |
| AgDR-0006 | [Small lines on the website's number cards, as monotone curves drawn at build time](AgDR-0006-monotone-sparklines-drawn-at-build-time.md) | proposed | A monotone cubic through the days never overshoots a value, and build.py draws it as SVG, so the page needs no chart script. |
