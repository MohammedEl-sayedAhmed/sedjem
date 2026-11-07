---
id: AgDR-0011
timestamp: 2026-10-30T16:21:45Z
agent: claude-code
model: claude-opus-5-5
trigger: user-prompt
status: executed
---

# Jobs run for another computer kept apart from the library, and not resumed

> In the context of the computer that runs jobs sent by another, facing a brief that asks for its normal queue, models and graphics card while its own library stays its own, I decided to keep those jobs in a store of their own that the job runner joins to the app's own, so they go through the same local queue, delete them once their transcript is collected or a day after they end, and mark them interrupted after a restart instead of running them again to achieve one queue for the graphics card and a library, assistants and comparisons that never see another computer's recordings, accepting that a restart on either computer loses a job in progress.

## Context

- The brief asked that the serving computer run a sent job through its normal queue, with its own models, downloads
  and GPU, remove the recording once the result is collected or after a day, and keep its own library free of other
  computers' jobs.
- Jobs live in one folder each under `jobs/`, which the library, the assistants' MCP server, comparisons and the
  usage counts all read.
- The job runner has one local queue, so that two models never share the graphics card at once.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Keep sent jobs in `jobs/`, marked, and filter them out of the list | Nothing new in the runner | Every reader of `jobs/` (MCP, comparisons, courses) would need the filter, and one missed shows another computer's recording |
| A separate store with a runner and queue of its own | Fully apart | Two local queues: a sent job and the computer's own would share the graphics card |
| A separate store, `remote/jobs`, joined to the app's own in the one runner | Same queue and models; nothing that reads `jobs/` sees them | A small store that routes each job id to its folder |
| Run interrupted jobs again after a restart | A job survives a restart | The sending computer has usually given up by then, and would collect a job it already failed |

## Decision

Chosen: the separate store joined to the one runner, with interrupted jobs not run again, because it keeps one
queue for the graphics card while no other part of the app can reach the sent recordings, and because the sending
computer, which retries for a minute and then fails the job with Run here instead, has moved on by the time the
serving computer restarts.

## Consequences

- A sent job is listed nowhere on the serving computer; Settings shows only how many each computer has running.
- It is deleted when the sending computer collects or cancels it, a day after it ends otherwise, and when serving is
  turned off or that computer is removed.
- On the sending computer the job is of kind "remote", so another computer's speed doesn't change this one's time
  estimates, and the usage counts count the run once, on the computer that ran it.
- A restart of either app loses the job in progress: the serving side marks it interrupted, and the sending side
  marks its own interrupted, to be run again by hand.

## Artifacts

- Commit [ddea8ea](https://github.com/MohammedEl-sayedAhmed/sedjem/commit/ddea8ea),
  [PR #39](https://github.com/MohammedEl-sayedAhmed/sedjem/pull/39)
- `app/jobs.py` (Joined, Runner.send), `app/remote.py` (Remote.tidy, Remote.remove), and the cleanup tests in
  `tests/test_remote.py`
- [Running jobs on another computer, What is sent, and what stays](../15-remote-runs.md#what-is-sent-and-what-stays)
