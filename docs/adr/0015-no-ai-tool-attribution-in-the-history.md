---
status: Accepted
date: 2026-11-07
deciders: MohammedEl-sayedAhmed
---

# 15. No AI-tool attribution in the history

## Context

AI coding tools work on Sedjem, and `AGENTS.md` tells them how. Some of them add their own credit to what they
make: a co-author trailer in a commit message, a "Generated with" line in a pull request description, a robot emoji.
Once such a line is in a commit on main it stays in the history, and a squash merge can copy it from the pull
request into the commit that lands. Clipman, the maintainer's other project, keeps this out of its history with git
hooks and a check on every pull request, and Sedjem's pipeline was compared with Clipman's in
[issue #57](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/57). The maintainer decided that Sedjem keeps the
same rule.

The rule is about the record, not about the tools. Using an AI tool to write code, tests or docs is allowed, and a
change is judged on what it does, whoever or whatever wrote it.

## Decision

The history of Sedjem and its pull requests carry no AI-tool attribution: no co-author, assisted-by or reviewed-by
trailer that names an assistant or uses an address of its maker, no "Generated with", "Created by" or "Powered by"
note, no link to a tool's chat or share pages, and no robot emoji. This holds for every contributor, the maintainer
included.

The check `footprints` enforces it on every pull request, and when its title or description is edited
([`.github/workflows/footprints.yml`](../../.github/workflows/footprints.yml)). It scans each commit's message and
trailers and the lines the commit adds, then the title and the description, with the patterns in
[`.github/scripts/check_footprints.py`](../../.github/scripts/check_footprints.py), and fails on a match. These are
the choices in it:

- The description of a pull request that a bot opened is skipped, since Dependabot quotes the release notes of other
  projects. Its commits and its title are scanned like any other.
- Only the script and its test may name the patterns. They are the two files the scan skips. A doc that needs to talk
  about the rule describes it, as this record does.
- The patterns are narrow on purpose. A tool's name alone is no match, so a doc may mention a library or a vendor.
  A match needs an attribution: a trailer, a verb such as "generated with", a maker's address, a tool's link or the
  emoji.
- Invisible characters are removed before a scan. A footprint written with look-alike letters is not found: the
  check is there for a slip, not against a person who hides one.

`AGENTS.md` states the rule for the agents. The check is one of the seven that main requires
([ADR 16](0016-branch-protection-with-seven-checks-and-a-linear-history.md)).

## Consequences

- A pull request with an attribution cannot merge until the lines are removed from the title or the description, or
  the commits are reworded and pushed again. A rewrite of history on a branch is the price.
- The title and the description are scanned again when they are edited, so an edit made after the first green run is
  checked too. The text that GitHub puts into a squash commit comes from them, and they are the last place a credit
  can get in.
- A change that adds a pattern to the script, or edits its test, is not scanned for its own lines, only for the rest.
  Review has to look at the allowed files.
- A pull request can edit the script and the workflow that run on it, so the check stops a slip and nothing more.
  A branch rule that needs a review of changes to those files would be the next step, and it needs a second person.
- A false match is possible, for example a sentence that says a text was written by a person whose name is also a
  tool's name. It is fixed by rewording, or by narrowing the pattern in a change of its own.
