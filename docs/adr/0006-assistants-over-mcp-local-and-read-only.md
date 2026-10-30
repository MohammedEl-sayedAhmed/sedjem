---
status: Accepted
date: 2026-10-20
deciders: MohammedEl-sayedAhmed
---

# 6. Assistants over MCP, on the computer and read-only

## Context

Course files were made to be pasted into an assistant ([ADR 5](0005-youtube-captions-made-by-people-no-summaries.md)).
[Issue #10](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/10) asked instead for assistants such as Claude,
Cursor and VS Code to read and search the transcripts themselves over the Model Context Protocol, so that a
question about a lecture or a meeting is answered from the transcripts with nothing copied by hand. The issue set
safety rules: local only by default, transcripts returned as quoted data and never as instructions, and no keys,
settings or outside paths exposed. ChatGPT connects only to servers reachable on the internet, which would expose
the computer, so the issue put it last, off by default, with a review of its own.

## Decision

The first phase, from [b3c6881](https://github.com/MohammedEl-sayedAhmed/sedjem/commit/b3c6881)
([assistants](../11-assistants.md)):

- The assistant starts Sedjem's MCP server itself and talks to it over standard input and output (`Sedjem --mcp`
  in the desktop build, `python -m app.mcp` from source). The server reads the data folder directly, works while the
  app is closed, and opens no network port.
- It lists, searches and reads transcriptions and courses, in pieces with times and links to moments in a video,
  and offers prompts such as a summary, study notes and meeting minutes. It changes nothing but its own log.
- Settings → Assistants decides what assistants may see, by kind and by transcription or course, and shows a log
  of what they asked.
- It uses Python's standard library only and speaks both protocol versions in use.
- Writing, starting transcriptions, a connection over HTTP and ChatGPT are later phases.

## Consequences

- A connected assistant can answer from the transcripts without any copying, and what it reads goes to its model,
  usually on its maker's servers. Hiding a transcription from assistants is how to keep it out.
- It never gets the audio, the keys or the settings.
- No port is opened, so nothing else on the network can reach the server.
- The desktop build doesn't grow, and the self-test starts the server the way an assistant would.
- The one write that came later, saving a corrected version, is off until the user turns it on
  ([ADR 8](0008-branches-and-one-main-transcript-per-recording.md)).
