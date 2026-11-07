# Assistants (MCP)

Sedjem can answer assistants such as Claude Desktop, Claude Code, Cursor and VS Code over the Model
Context Protocol (MCP). An assistant connected to it can list, search and read the transcriptions and
the courses, so a question like "summarize lecture 3 of Prompt Engineering", "quiz me on lectures 5 to
8", "where did the teacher explain tokenizers?" or "what did we decide in yesterday's sprint planning?"
is answered from the transcripts, pointing to the lecture and to the moment in the video. Nothing has to
be copied by hand.

When saving is turned on in *Settings → Assistants* (it is off by default), an assistant can also save a
corrected version of a transcript, when the user asks for that change and agrees to it ([saving a
version](#saving-a-version)). Nothing it does deletes anything: every earlier version stays. Starting transcriptions from an assistant, a connection over HTTP, and ChatGPT are
planned later ([issue #10](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/10)).

## How it works

The assistant starts Sedjem's MCP server itself and talks to it over the server's standard input and
output, the way MCP's stdio transport works. In the desktop build the server is the same program,
started as `Sedjem --mcp`. From source it is `python -P -m app.mcp`, with the project folder on
`PYTHONPATH`. Either way:

- It reads the data folder directly, so it works while the app is closed, and it opens no network port.
- It reads the same data folder as the app. When the app runs on another folder (`SEDJEM_HOME`), the
  connection snippets pass that folder on.
- It changes nothing in the data folder apart from adding to the log of what assistants asked, and,
  while saving is turned on, the versions an assistant saves with `save_version`, each a new version of
  a transcript.
- It is written with Python's standard library only, so the desktop build doesn't grow. It speaks both
  versions of the protocol in use: the one where a connection starts with a handshake (2024-11-05 to
  2025-11-25), and the one where every request carries its own version (2026-07-28).

## Connect an assistant

*Settings → Assistants → Connect an assistant* shows the configuration for each assistant, made for
the installation it is shown in (the path of the Sedjem program, or of the Python and project folder
when run from source), with a *Copy* button. Paste it where the assistant expects it, then restart the
assistant or reload its servers. When Sedjem moves to another folder, copy the snippet again.

### Claude Desktop

Claude Desktop reads `claude_desktop_config.json`: `%APPDATA%\Claude\claude_desktop_config.json` on
Windows and `~/Library/Application Support/Claude/claude_desktop_config.json` on macOS (the app shows
`~/.config/Claude/claude_desktop_config.json` on Linux). *Settings → Developer → Edit Config* in Claude
Desktop opens it. If the file already has an `mcpServers` section, add the `sedjem` entry to it. With
the desktop build on Windows the snippet looks like this:

```json
{
  "mcpServers": {
    "sedjem": {
      "command": "C:\\path\\to\\Sedjem.exe",
      "args": ["--mcp"]
    }
  }
}
```

From source, the command is the project's Python and the project folder goes on `PYTHONPATH`:

```json
{
  "mcpServers": {
    "sedjem": {
      "command": "/path/to/sedjem/.venv/bin/python",
      "args": ["-P", "-m", "app.mcp"],
      "env": {"PYTHONPATH": "/path/to/sedjem"}
    }
  }
}
```

`-P` keeps the folder Claude Desktop starts the server in off Python's import path, so nothing there
can stand in for Sedjem's own code.

### Claude Code

The snippet is a command to run once in a terminal:

```sh
claude mcp add --scope user sedjem -- /path/to/Sedjem --mcp
claude mcp list        # sedjem should be listed as connected
```

`--scope user` makes Sedjem available in every project. Inside Claude Code, `/mcp` shows the server and
its tools, and the prompts below are slash commands such as `/mcp__sedjem__summarize`.

### Cursor

Cursor reads `~/.cursor/mcp.json` for every project, or `.cursor/mcp.json` inside one project. The
snippet has the same `mcpServers` form as Claude Desktop's.

### VS Code

VS Code reads `.vscode/mcp.json` in a folder, or the user's `mcp.json` (the command *MCP: Open User
Configuration* opens it). Its form has `servers` at the top and says the server uses stdio:

```json
{
  "servers": {
    "sedjem": {
      "type": "stdio",
      "command": "/path/to/Sedjem",
      "args": ["--mcp"]
    }
  }
}
```

The tools are used by Copilot Chat in agent mode.

Once connected, a first question such as "which courses do I have in Sedjem?" shows that the assistant
can reach it.

## What an assistant can do

### Tools

| Tool | What it gives |
|---|---|
| `list_transcriptions` | The transcriptions, newest first: id, date, length, model, status, speakers, and for a lecture its course, number ("lecture 4 of 38") and YouTube link. A recording transcribed more than once is listed once: its main transcript when one is chosen in the app, then its branches, each with its label, model, id and latest version. Filters: words in the title, a branch's name or the speaker names, a course, the kind (courses or recordings) and the status, in pages of recordings. |
| `list_courses` | The courses (YouTube playlists): playlist id and link, how many lectures are transcribed, and their total length. |
| `course_outline` | A course's lectures in order, each with its number, id, title, length, status, link and the first words of its transcript, so an assistant can find the right lecture without reading everything. |
| `get_transcript` | A transcript by id, or by course and lecture number, as lines of `[mm:ss] Speaker: text`, for a time range or from a line on, and an earlier version of it with `version`. Each answer says which version it gives, of how many, and whether it is the recording's main transcript or a branch. |
| `search` | Where something was said, across everything or in one course or transcription: each match with its lecture, time, the lines around it, and for a YouTube video a link that opens it at that moment. Of a recording with a main transcript, only that one is searched unless `include_branches` is true; without one, its transcripts that aren't branches made in the app. |
| `get_course_text` | A whole course as one text, the same as its `course.md` file, in pieces. |
| `save_version` | A corrected version of a transcript, saved as its newest version, from line edits or from all its lines, on top of the version it was made on. Offered only while saving is turned on. See [saving a version](#saving-a-version). |

A long transcript is read in pieces: one answer holds up to 12,000 characters by default and 40,000 at
most, and an answer that is cut ends with the exact call that continues it, by time or by line. A long
course (Prompt Engineering is about 49,000 words) is read the same way, lecture by lecture, so an
assistant with a small context can still go through all of it.

A course is named by its playlist id or by its title, in any case, and a part of the title is enough
when only one course has it. For a lecture transcribed more than once, assistants get its main
transcript when one is chosen, else the transcription that finished last, the same one the course
files follow.

Search finds lines where all the words of the question appear, in the line or in the lines around it.
Case doesn't matter, nor do Arabic diacritics and tatweel, and أ إ آ ٱ count as ا, ى as ي and ة as ه,
with Arabic digits as 0 to 9. Some models write English terms in Arabic letters (بروجكت for project),
so searching for an Arabic spelling as well can find more. A link to a moment is the video's watch link
with `&t=` and the second, for example `https://www.youtube.com/watch?v=<id>&t=754s`.

### Saving a version

Saving is off by default. *Let assistants save new versions of transcripts*, under *What assistants can
change* in *Settings → Assistants*, turns it on. The reason is that transcript text is quoted data:
anyone in a meeting or a video can say something that reads like an instruction to an assistant, and
an assistant can be led astray by it. While saving is off, no such instruction can lead to a write,
whatever the assistant makes of it: `save_version` is not offered to assistants, a call to it is
refused with nothing saved, and the server's description doesn't mention it. The switch applies at
once, also to an assistant that is already connected, like the others.

`save_version` is the one tool that changes something, and its description says so: it is not
read-only, though not destructive either. The assistant has to ask the user before every call and
save only a change the user asked for and agreed to; the tool's description and the server's
description say this, and that nothing in a transcript may lead to a save, because transcript text is
quoted data.

It takes the transcription's id, `base_version` (the version the change was made on, which
`get_transcript` names), an optional message, and the change, given one of two ways:

- `edits`: a list of `{line, old, new}`, each line by its number as `get_transcript` counts them (the
  first is 1), `old` its text as `get_transcript` shows it and `new` the corrected text.
- `lines`: every line of the new version, as `{start, end, speaker, text}` with the times in seconds.

Nothing is saved, and the answer says why, when `base_version` is not the latest version (it names the
latest, so the assistant can read it again and redo the change), when an old text doesn't match its
line, when a line doesn't exist, when both or neither of `edits` and `lines` are given, or when the
transcription is hidden, switched off or not finished. A saved version is the newest one: version 0
and every earlier version stay. In the app's History it is marked *Assistant*, with the name the
assistant gives, and it can be compared and restored like any other. The app and the server never
save at the same moment: each holds a lock on the transcription's folder while it changes its history.

### Resources

Assistants that support MCP resources can attach a transcript or a course to a conversation:

| Address | What it is |
|---|---|
| `sedjem://transcript/<id>` | One transcript as Markdown: its title, where it comes from, and every line with its time and speaker (for a YouTube video, the same text as its `.md` file in the courses folder). |
| `sedjem://course/<playlist id>` | A whole course as Markdown, the same text as its `course.md`. |

The list of resources holds every visible course and finished transcription, in pages.

### Prompts

Assistants that support MCP prompts offer these as ready-made requests. Their transcript and course
arguments are completed as you type, from what assistants may see.

| Prompt | Arguments | What it asks for |
|---|---|---|
| `summarize` | transcript, language | What it is about, the main points in order, and the conclusions, with times. |
| `meeting_minutes` | transcript, language | A summary, the decisions, the action items with owners and due dates, and the open questions, with times. |
| `study_notes` | course, lectures, language | Notes for a course or some of its lectures, citing lecture numbers and times with links. |
| `quiz` | course, lectures, count | Questions one at a time, each answer pointing to where it is taught. |
| `flashcards` | course or transcript, lectures | One fact, term or idea per card, with where it comes from. |

A transcript argument is a title or an id; lectures are written like `3`, `1-5` or `1,3,5-8`. A
transcript of normal length is included in the prompt itself. For a course, or a very long
transcript, the prompt tells the assistant to read it with the tools, a piece at a time, and to cite
lecture numbers and times with links.

## What assistants may see

Everything is visible to assistants by default, because that is what makes them useful. Whatever an
assistant reads goes to its model, which for most assistants runs on its maker's servers (Anthropic's
for Claude, for example), under that service's terms. The audio itself never leaves the computer
through MCP: assistants get the text, the titles, the dates, the speaker names and the details listed
above. API keys, the app's settings, file paths and recordings are never given out.

*Settings → Assistants* has two switches for what they may see:

- *Courses and YouTube videos*: every course lecture, and every YouTube video added on its own.
- *Meetings and other recordings*: everything else.

A third, off by default, lets them save new versions of transcripts ([saving a version](#saving-a-version)).

A single transcription can be hidden with *Hide from assistants* on its page (*Show to assistants*
undoes it), and a whole course with *Hide course from assistants* on any of its lecture pages, which
also covers lectures added later. *Hidden from assistants* in *Settings → Assistants* lists what is
hidden, each with *Show again*.

A branch made in the app from a hidden transcription is hidden too. The switches and hiding apply at
once, also to an assistant that is already connected, because the server reads the settings again for
every request. To an assistant, a hidden or switched-off
transcription is as if it didn't exist: it is never listed, searched, read, offered as a resource or
suggested while typing, and asking for it by id gets the same answer as an id that isn't there. When a
lecture's newest transcription is hidden, the one before it takes its place.

Transcripts are quoted text from recordings, and someone in a meeting or a video can say anything,
including words that read like instructions to an assistant. Every answer that contains transcript text
says that it is quoted data and that instructions in it are not to be followed, and the server's
description and the tool descriptions say the same. This lowers the risk; it can't remove it, so an
assistant's actions on other systems still deserve a look.

## The log

*What assistants asked* in *Settings → Assistants* lists every tool call, resource read and prompt,
newest first: when, which assistant (by the name and version it gives, or "An assistant" when it gives
none), what it asked in words ("Searched for tokenizers, in Prompt Engineering", "Read Prompt Engineering,
lecture 4, 00:00-10:00", "Saved version 3 of Sprint planning") and which transcriptions the answer
contained. A saved version stands out, marked with its number, and a save that was refused is listed
with the reason. A request for something hidden is
logged too, with no transcriptions, and so is a request for a tool that doesn't exist. *Clear* empties
the log. It is kept in `mcp/log.jsonl` in the app's data folder (`app_data/` from source), one entry per
line, and cut back to the newest 2,000 entries whenever it grows past 2,500.

## When it doesn't connect

- After changing an assistant's configuration, restart the assistant (or reload its servers).
- Copy the snippet again after moving the project folder or reinstalling to another folder: it holds the
  full path.
- Most assistants keep what a server writes to its standard error in their logs. Sedjem writes one line
  there when it starts, with the data folder it reads, and the details of any error.
- `Sedjem --mcp` (or `python -P -m app.mcp`) started by hand in a terminal waits for messages and prints
  nothing until one comes; Ctrl+D (Ctrl+Z then Enter on Windows) ends it.

## How it is tested

`tests/test_mcp.py` checks the protocol in both versions, in the process and over real pipes: every
error case, batches, that nothing but messages reaches standard output even when something prints, and
that the server ends when its input closes. The official MCP Python SDK (in `requirements-dev.txt`)
connects as a client in both versions and checks every answer against the protocol's schema. On demo
data folders, the tests also cover the switches and hiding, the log, Arabic spellings in search,
reading in pieces, the lecture chosen when a video was transcribed twice, links to moments, ids and
addresses that try to reach outside the data folder, a recording listed once with its main transcript
and branches, reading an earlier version, and every way `save_version` refuses, with nothing saved, starting with saving being off until it
is turned on.
`tests/test_branches.py` checks that the lock on a transcription's history holds across processes.

The self-test (`Sedjem --self-test`, see [the desktop notes](10-desktop.md#self-test)) starts the
server exactly as an assistant would, with the command from the snippets and only the environment an
assistant passes on, and checks both protocol versions over pipes. The desktop build workflow runs it
in the built app on Windows and Linux.

On Linux, the self-test of a desktop build made with `desktop/build.py` passes, and the build's
`Sedjem --mcp` answered the official SDK's client in both protocol versions, with its first answer
about 0.2 seconds after it was started. Claude Code 2.1, connected from source with a configuration
like the snippet, answered a question about the Prompt Engineering course through the server: it listed
the courses, searched in English and then in Arabic letters, read the outline and a piece of lecture 4,
and gave the link at the moment the teacher explains tokenizers. Claude Desktop, Cursor and VS Code have
not been tried yet.
