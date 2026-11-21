---
id: AgDR-0022
timestamp: 2026-11-20T15:55:00Z
agent: claude-code
model: claude-opus-5-5
trigger: user-prompt
status: executed
---

# Assistants start jobs through the running app, which checks the rules itself

> In the context of the MCP server, which assistants start over standard input and output and which so far only read the data folder and saved versions, facing assistants that now start transcriptions from YouTube and Google Drive links and from files on the computer, follow them and cancel them, while transcript text and web pages can hold words that try to steer an assistant, I decided to make the MCP server a client of the running app's own local API that sends the secret only after the server on the noted port has proved that it knows it, to have the app's server check the assistant rules again itself for every request that names an assistant and mark each job an assistant starts, to keep every switch for this off until the user turns it on, and to let an assistant cancel only a job that an assistant started, to achieve jobs that run in the one queue the app already has, under rules held in one trusted place, accepting that these tools work only while Sedjem is open and that the MCP server never starts it.

## Context

- Issue [#10](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/10), phase 2: assistants start transcriptions,
  behind switches. Phase 1 made the MCP server (`app/mcp.py`), which reads the data folder directly and works while
  the app is closed; `save_version` is its one tool that writes, and it is off until the user turns it on
  (`mcp_write`).
- The owner decided: tools for links (a YouTube video or playlist, a Google Drive file), for files inside folders the
  user allows, and for hosted models behind their own switch; every new switch off by default; an assistant may
  cancel only jobs that an assistant started; and when the app isn't running, a job tool says that Sedjem must be
  open, and the MCP server never starts it.
- Jobs run in the app: its job runner has the queues, the model workers, the downloads of links and the uploads to
  hosted services, and only one copy of it may run on a data folder.
- The app's local API already does each step: `POST /api/link/inspect` and `POST /api/link` for links,
  `POST /api/jobs` with a JSON `path` for a file read in place, and `POST /api/jobs/<id>/cancel`. Every request needs
  the secret in `<storage>/session.key`, and a second start trusts the server on `<storage>/port` only after it proves
  that it knows the secret ([AgDR 19](AgDR-0019-a-secret-between-the-server-and-its-window.md)).
- `POST /api/jobs` with a path reads any file the user can read, which is right for the user's own window but not for
  an assistant.
- CodeQL runs on the repository with a ratchet ([AgDR 15](AgDR-0015-codeql-ratchet-on-a-baseline-branch.md)), so a
  new path check must be one that its path-injection query recognises.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| The MCP server as a client of the running app's API: the proof of the secret first, then the requests with the secret, each naming the assistant; the app's server checks the rules again and marks the jobs | The jobs join the one queue, with the app's own checks of models, links, disk space and uploads; the rules hold in the server that runs the jobs, whatever a client sends; no new route and no second runner | The job tools work only while the app is open; the MCP server holds the secret for a moment, so a program that took the port must never get it |
| The MCP server writes job requests into the data folder, and the app picks them up | Works as a queue while the app is closed | A new file format and a watcher in the app; the app learns of a request late, and the assistant can't be told at once that it was refused; anything that can write the data folder could add requests |
| The MCP server starts the app when it isn't running, or runs the jobs itself | The tools work at any time | Against the owner's decision; a window would open from an assistant's request; a second runner would run models beside the app's and race it on the same jobs |
| The same client, with the rules checked only in the MCP server | Less code in the app's server | The rules would rest on the client: a bug or another client that names an assistant would skip them, and the switches could change between the check and the request |

## Decision

Chosen: the MCP server as a client of the running app's API, with the rules checked again by the app's server,
because it reuses the app's queue and every check the app already makes, it keeps the rules in the one process that
runs the jobs and reads the settings at that moment, and it adds no route, file format or runner. The owner chose
the tools, the switches off by default, the own-jobs-only cancel and the answer when the app is closed; the agent
chose the client, the checks in both places, the path check and the annotations.

- Settings: `mcp_links`, `mcp_files`, `mcp_folders` (absolute paths, at most 50, each at most 1,000 characters, no
  two the same) and `mcp_hosted`, all off or empty by default, checked and saved like the others. Paired computers are
  never offered to assistants.
- `app/session.py` `running()` reads the port and the secret without writing anything, sends `/api/ping` a random
  nonce, and gives the secret only when the answer is the proof for that port. A program that took the port, with a
  made-up proof or with the real server's passed on from another port, gets nothing but the ping. Requests to the app
  go to 127.0.0.1 through an opener that uses no proxy (`http_proxy` would get the secret) and follows no redirect.
  The secret is never put in an answer, an error or the log.
- The MCP server's tools: `list_models` and `job_status` (while either job switch is on), `inspect_link` and
  `transcribe_link` (`mcp_links`), `transcribe_file` (`mcp_files`) and `cancel_job` (while either is on). Each is
  listed only while its switch is on and refused while it is off, as `save_version` is; the server's instructions say
  what they allow only while one is on. Every look, start and cancel goes in the assistants' log with the job ids.
- Every request from these tools carries `assistant`, the client's name. For such a request the app's server checks
  the switch, refuses `run_on` and uploads, takes a file only inside an allowed folder, a hosted model only with
  `mcp_hosted`, and a cancel only of a job marked `assistant` that assistants may see. Each job it starts gets
  `"assistant": {"client": name}`, which the job's page shows. A request without `assistant`, from the user's own
  window, is not held to these rules.
- A file is checked in `app/assistants.py` `recording_path()`, the same in both processes: its path and each allowed
  folder are resolved with every symbolic link followed, the file must be inside a folder by its path's parts
  (`os.path.commonpath`), and again as text on the folder with a separator after it, the form CodeQL recognises; then
  it must be a file with a recording's extension. The real path is what the job keeps.
- Annotations: `list_models` and `job_status` are read-only; `inspect_link` is read-only and open-world;
  `transcribe_link` writes and is open-world; `transcribe_file` writes; `cancel_job` is destructive, since it stops a
  running job.
- When the app isn't running, or the proof fails, each job tool answers that Sedjem isn't open and asks the
  assistant to have the user open it. `job_status` asks for the app too, since only then are the statuses live, and
  then reads the jobs' `job.json` from the data folder as the reading tools do, instead of `GET /api/jobs/<id>`,
  which sends a whole transcript with each job.

## Consequences

- An assistant can start, follow and cancel transcriptions only while the user has the app open, and only where the
  user turned it on. Reading still works with the app closed.
- A job an assistant started shows that on its page, with the assistant's name, and the log lists each start and
  cancel.
- A program on the computer that has the secret could send `assistant` itself; it has the user's full API anyway, so
  the mark is about honest clients and the rules, not about other programs.
- A file is checked when its job is created; a file in an allowed folder that is replaced by a link afterwards is read
  as it is then, as a file the user picks in the app is.
- On macOS, where paths ignore letter case but the check compares them as given, a path typed in another case than
  its folder is refused rather than allowed.
- The usage counts don't count assistants' jobs apart from the user's; that can come later.
