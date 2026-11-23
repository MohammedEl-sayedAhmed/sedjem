---
id: AgDR-0024
timestamp: 2026-11-23T14:29:57Z
agent: claude-code
model: claude-opus-5-5
trigger: user-prompt
status: executed
---

# Folders and files on this computer as courses, listed by the server and read in place

> In the context of the new transcription page, which reads one file in place by its path or uploads one file, and is now to take a folder on this computer or several files as one course, facing folders that hold subfolders, other files, hidden files and symbolic links, a browser folder picker that would upload a copy of every file, files from a browser that have no path, paired computers that a file read in place may be sent to, and assistants that see no more of a local path than a file's name, I decided to have the local server list a folder's direct entries by its real path, as a Drive folder's files are listed, skipping and counting a link to a file outside the folder, to have it list a browser's files by their names too, to read a folder's recordings and the desktop app's picked paths in place and upload a browser's files one after another, to give each course a random id and find a folder's course again by its real path, to number files picked together among the ones picked and name them "Files added on <day>" unless the user types a name, and to show assistants a course's title and never a path, to achieve folders and groups of files as courses with nothing copied, one list of extensions and one order, accepting a typed path sent to the local server, numbers that move when files are added to a folder between two adds, and a new course each time a group of files is added.

## Context

- Issue [#145](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/145): a folder of recordings on the user's
  computer becomes a course, as a YouTube playlist and a shared Google Drive folder
  ([AgDR 23](AgDR-0023-google-drive-folders-from-the-embedded-folder-view.md)) do. The page took one file: `bindNew`
  in `app/static/app.js` kept `files[0]` of the file field and of a drop, and dropped the others without a message.
- What was there: `POST /api/jobs` with `{"path": …}` reads a file in place by its path (`source_path`), and may send
  it to a paired computer (`run_on`): the job is prepared here and its sound is sent there. The desktop window's
  bridge had `pick_file` (one file) and `pick_folder` (for *Settings → Assistants*). `app/assistants.py` had
  `RECORDING_TYPES`, the recordings' extensions, and refuses an assistant's path that leads out of an allowed folder
  through a symbolic link, by comparing real paths. The desktop picker's filter (`app/desktop.py RECORDINGS`) was a
  second list of nearly the same extensions, without `.3gp`.
- The owner decided:
  - In the desktop app, *Choose folder* opens `pick_folder`. In a browser, the user types the folder's full path, as
    for a file. The server lists the folder and reads each file in place. The browser's own folder picker
    (`webkitdirectory`) is not used, because it uploads a copy of every file.
  - Files dropped or picked together are listed like a folder's files, and the ones picked become one course that the
    user can name. In a browser they are uploaded, as one file is; in the desktop app, paths read in place are
    preferred.
  - The listing is the same as for a Drive folder: recordings by extension, in natural name order, numbered in that
    order; subfolders and other files skipped and counted; a cut at 300 files. Subfolders are not read.
  - The course behaves as a Drive course does, unless there is a reason not to. The assistant tools' rules from
    [AgDR 22](AgDR-0022-assistants-start-jobs-through-the-running-app.md), paired computers and remote runs don't
    change.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| The local server lists a typed or picked folder by its real path, and lists a browser's files by their names; folders and the desktop app's paths are read in place, a browser's files uploaded | Nothing is copied from a folder; one list of extensions and one natural order, in Python, for folders, paths and a browser's files; each lecture is a file read in place, so a paired computer gets it as it gets a single file | A typed path goes to the local server, which reads any folder the user can read; a browser's files are still uploaded, one after another |
| The browser's folder picker (`webkitdirectory`) and the page's own listing | Works the same in any browser, with no path to type | Uploads a copy of every file, which the owner ruled out; a second list of extensions and a second natural order in JavaScript |
| Folders only in the desktop app, through `pick_folder` | No typed path in a browser | A browser can't add a folder at all; the issue asks for both |

## Decision

Chosen: the local server lists a folder by its real path and a browser's files by their names, because that reads a
folder in place as the owner asked, keeps the extensions and the order in one place, and makes each lecture a job
that the app already runs: a file read in place, or an upload. The owner chose the two ways to give a folder, the
listing rules and uploads for a browser's files; the agent chose the rest:

- `app/local.py` lists only the folder's direct entries, by the folder's real path (`os.path.realpath`). A file is a
  recording when its extension is in `RECORDING_TYPES` and its name doesn't start with a dot, so the `._` files that
  macOS leaves on other disks, and other hidden files, are counted among the other files. A symbolic link counts as
  what it leads to: a link to a folder is a subfolder, and a link to a file is that file only when the file is itself
  directly in the folder, then listed once under its own name. A link to a file anywhere else is skipped and counted
  on its own line ("1 link to a file outside the folder"), as `app/assistants.py` refuses a path that leads out of an
  allowed folder. A link that leads to nothing counts among the other files, wherever it would lead. Only links are
  followed one by one: the folder's other entries are already in its real path, so a folder of 100,000 entries is
  listed in about a third of a second. Each lecture's `source_path` is the file's real path.
- One path field takes a file or a folder (`POST /api/local/inspect` with `{"path"}`): a file is read on its own, as
  before, and a folder is listed. The path must be absolute, with `~` for the home folder. A folder that isn't there,
  can't be read, is empty or has no recordings says so; `POST /api/local` with a file's path, and `POST /api/jobs` or
  `POST /api/probe` with a folder's, say which one it is.
- A browser's files are listed by the server from their names (`{"names", "folders"}`), so the page has no copy of
  the extensions or of the natural order; the page counts a folder dropped with them, which it can't upload, and says
  that a folder is added by its path. Several files picked in the desktop app go as paths (`pick_files`, which
  replaces `pick_file` and takes one or several), so they are read in place. The desktop picker's filter is now built
  from `RECORDING_TYPES`, so there is one list of extensions, and it gains `.3gp`.
- The course id is `folder-` or `files-` and 20 random hexadecimal digits, rather than a hash of the folder's real
  path: a plain hash would let anyone who sees the id, an assistant among them, test whether a guessed path is the
  folder; a keyed hash would need a new secret kept in the data folder. A folder's course keeps the folder's real path
  in its job.json (`course.path`), and adding the same folder again finds that course by it and joins it. The id fits
  the hide key's pattern, so a course can be hidden from assistants.
- Files picked together make a new course each time they are added, with a new id, because a group has no identity
  of its own. They are numbered 1, 2, 3 among the ones picked, in natural name order, rather than by their places in
  the list, since a file left out of a group is not part of it; a folder's lectures keep the folder's numbers, as a
  Drive folder's do. The name is what the user types; the field starts with "Files added on 23 Nov 2026" (the day it
  is added), which the server gives as the listing's title and uses when a script sends no name. The common start of
  the files' names was not used: for names such as "Lecture 1" it gives "Lecture", which names nothing.
- A browser's files are uploaded one after another to `POST /api/jobs`, each with `course_title`, `course_index` and
  `course_count`; the server makes the course's id for the first, and the page sends it with the rest (`course`). A
  `files-` id only is taken there, so an upload can't join a folder's course. If an upload fails, the ones before it
  stay in the course, marked as added in the list, and *Start* adds the rest to the same course with the next numbers,
  so none is added twice.
- Each lecture is a job like a single file's: read in place (or uploaded), prepared here, and sent to a paired
  computer when `run_on` names one, with the consent asked once for that computer. A local course has no files in the
  courses folder and adds no usage count; the library's count of courses counts YouTube playlists only, as before.
- Assistants see a local course under *Courses and YouTube videos* (`mcp_courses`), as a Drive course is, since the
  user made it a course and can hide it with *Hide course from assistants*. They see its title (the folder's name, or
  the name typed) and each lecture's title (the file's name without its extension), as a single file's title, and
  never a path: the MCP server builds the course from its id, title, numbers and kind alone, says "a folder on the
  user's computer" or "files on the user's computer" where a link would be, and its course text says "Recordings from
  a folder on the user's computer" and "in the order of the files' names", with no link under a lecture. A course
  marked as local whose id isn't one of these is not taken for a YouTube playlist. Requests to `/api/local` and
  `/api/local/inspect` that name an assistant are refused, so assistants keep to `transcribe_file` and the allowed
  folders.

## Consequences

- A path typed in the page goes to the local server, which lists any folder the user can read, as `POST /api/probe`
  and `POST /api/jobs` already read any file. Only the user's own window can send it (the secret of
  [AgDR 19](AgDR-0019-a-secret-between-the-server-and-its-window.md)).
- A folder's numbers follow its list when it is added. Files added to the folder before it is added again can move
  later files to other numbers, so two lectures can share a number; the same happens to a playlist or a Drive folder
  that changes.
- A folder renamed or moved is a new course, since its real path changed. A course's job.json holds the folder's
  path, which the app's own page sees and assistants don't.
- The same group of files added twice makes two courses.
- The listing has sizes but no lengths, so the estimate before *Start* gives the time as a share of each recording's
  length, as for a Drive folder.
- The desktop window's pickers are tested with a stand-in window; a check by hand in the desktop app is still to be
  made.

## Artifacts

- `app/local.py`, `app/static/local.js`; changes in `app/server.py` (`/api/local/inspect`, `/api/local`, uploads with a
  course), `app/static/app.js`, `app/desktop.py` (`pick_files`, the filter), `app/mcp.py`, `app/courses.py` and
  `app/usage.py`.
- Tests: `tests/test_local.py`, `tests/test_ui_local_course.py`, and new tests in `tests/test_session.py` and
  `tests/test_remote.py`.
- Issue [#145](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/145).
