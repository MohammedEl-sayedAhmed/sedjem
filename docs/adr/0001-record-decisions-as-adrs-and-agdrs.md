---
status: Accepted
date: 2026-10-30
deciders: MohammedEl-sayedAhmed
---

# 1. Record decisions as ADRs and AgDRs

## Context

Sedjem has a number of choices that shape it beyond any one change: what may leave the computer, which model
runs by default, the licence, what the update check sends, what the usage counts hold. Their reasons are spread
over the docs, commit messages, pull requests and issues, so a later change can undo one without knowing why it
was made.

Much of the code is written by an AI coding agent working on a branch of its own (the history has many merges of
`worktree-agent-...` branches), and the pipeline in [PR #34](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/34)
has every change an agent makes go through a pull request. An agent also picks between options while it works, such
as a tool to add or to leave out, and its reasons stay in its session unless they are written down.

## Decision

Two kinds of record are kept in the repository, each in a folder of its own:

- [docs/adr/](README.md) holds architecture decision records (ADRs): decisions the maintainer made. Each has a
  frontmatter with `status`, `date` and `deciders`, and three sections, Context, Decision and Consequences. Files
  are named `NNNN-kebab-case-title.md` and numbered in order. A status is Proposed, Accepted, Deprecated or
  Superseded by NNNN.
- [docs/agdr/](../agdr/README.md) holds agent decision records (AgDRs): decisions an AI coding agent made while
  building Sedjem, in the format of the Agent Decision Record specification. Files are named
  `AgDR-NNNN-slug.md`.

An ADR is written when the maintainer settles something that later changes have to respect: what runs where, what
is sent and to whom, a default, the licence, how releases are made. An agent writes an AgDR when it picks between
real options (a library, a pattern, an architecture, a convention), not for trivial choices or for following a
convention the project already has. A record goes in the same pull request as the change it explains.

Once a record is accepted (or executed, for an AgDR), its text isn't changed. A decision that changes gets a new
record that says which one it replaces, and the old record's status becomes Superseded by the new number; that
status and its line in the index are the only edits an old record gets.

The first records were written on 30 October 2026, after the decisions. Each takes its date from the commit that
brought the decision in and cites the doc page, commit, issue or pull request that shows it.
`tests/test_decisions.py` checks the names, numbers, frontmatter, statuses and indexes of both folders, the
structure of each AgDR, and that neither folder uses bold or em dashes.

## Consequences

- The reason for a choice can be found next to the code, and a change that would undo one can cite the record it
  supersedes.
- The maintainer's decisions and an agent's are kept apart, so it stays clear who decided what.
- A significant change costs a short record more. Records written after the fact can only give the reasons the
  repository shows; where it shows none, there is no record.
- Old records stay as they were written, so reading one means checking whether a later record supersedes it.
