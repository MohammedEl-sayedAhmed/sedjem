---
id: AgDR-0001
timestamp: 2026-10-30T02:27:16Z
agent: claude-code
model: claude-opus-5-5
trigger: user-prompt
status: executed
---

# Update now restarts on a checkout already past the offer

> In the context of Update now for Sedjem run from a git checkout, facing a checkout pulled by hand past the offered commit while Sedjem kept running, which Update now refused as diverged, I decided to start Sedjem again on the checkout's code without moving it, after installing the requirement files changed since the commit the process runs, to achieve an update that finishes when the checkout already has what was offered, accepting that Sedjem may then run commits newer than the one the check offered.

## Context

- From source, Update now fast-forwards a clean checkout of main to the offered commit, installs the requirement
  files that changed, and restarts ([3199189](https://github.com/MohammedEl-sayedAhmed/sedjem/commit/3199189)). It
  only ever fast-forwards ([issue #11](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/11)).
- On 30 October 2026 the check offered 0.3.1, main was then pulled in the folder a running Sedjem used, and Update
  now said the checkout had commits the offer lacked and should be updated by hand, although it already had the
  offered commit.
- A checkout whose history really diverged from main still has to be refused.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Refuse as diverged and ask for an update by hand, as before | No new path in the updater | The checkout already had the offered commit, so the refusal was wrong and updating by hand had nothing to do |
| Start again on the checkout's code without moving it, after installing the requirement files changed since the running commit | Update now finishes; the requirements match the code that starts; a checkout that really diverged is still refused | Sedjem may start on commits newer than the offered one |

## Decision

Chosen: starting again on the checkout's code, because the offered commit is already in HEAD, so the checkout holds
everything the update would bring, and a fast-forward can't move it back to an older commit. Comparing the
requirement files with the commit the process runs, rather than with the offered one, also installs what changed in
the commits pulled by hand.

## Consequences

- A checkout pulled past the offer updates with one click, and the running process catches up with the code on
  disk.
- A process that already runs the pulled code is refused, with nothing restarted, as there is nothing to install.
- Sedjem may start on commits the check didn't offer, which may not have passed their tests yet.

## Artifacts

- Commit [b36e5ad](https://github.com/MohammedEl-sayedAhmed/sedjem/commit/b36e5ad),
  [PR #27](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/27)
- `app/updates.py`, and three tests in `tests/test_updates.py`
- [Updates, from source](../12-updates.md#from-source)
