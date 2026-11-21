---
id: AgDR-0023
timestamp: 2026-11-21T11:36:01Z
agent: claude-code
model: claude-opus-5-5
trigger: user-prompt
status: executed
---

# Google Drive folders listed from Drive's embedded folder view, in natural name order

> In the context of the Link field, which takes a Google Drive file shared with anyone who has the link and is now to take a shared folder as a course, facing a yt-dlp folder extractor that still fails on every Drive folder, a Drive API that needs a key or a sign-in, and folders that hold subfolders, documents and files named "Lecture 2" and "Lecture 10", I decided to list a folder with Sedjem's own request to Drive's embedded folder view, read with the standard library's html.parser, to keep its audio and video files in natural name order, to skip and count its subfolders and other files, and to hand each file picked to the existing Drive file path as a lecture of a course named after the folder, to achieve folders as courses now, with no new host, no key and no change to how a file is downloaded, accepting a page that Google can change without notice, no sizes or lengths before the download, and a listing that should move back to yt-dlp once its folder extractor works again.

## Context

- Issue [#136](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/136): a link to a Drive folder shared with
  anyone who has the link lists its audio and video files, and the ones picked become a course, as a YouTube
  playlist does. [AgDR 21](AgDR-0021-google-drive-links-through-yt-dlp.md) left folders out because yt-dlp's
  GoogleDrive:Folder extractor fails with "expected string or bytes-like object, got 'bool'"
  ([yt-dlp#10484](https://github.com/yt-dlp/yt-dlp/issues/10484), open). yt-dlp 2026.10.26 is still its newest
  release, and `requirements.txt` pins it.
- `GET https://drive.google.com/embeddedfolderview?id=<folder id>` answers 200 with plain HTML for a folder shared
  with anyone who has the link, with no sign-in and no key. Checked on 21 November 2026 against real public folders:
  - The page's `<title>` is the folder's name. Each entry is a `div` of class `flip-entry` with the id
    `entry-<id>`, a link, an icon and a `div` of class `flip-entry-title` with the name, inside a `div` of class
    `flip-entries` (present, and empty, for an empty folder).
  - A file links to `drive.google.com/file/d/<id>/view?usp=drive_web`, and its icon is
    `drive-thirdparty.googleusercontent.com/16/type/<MIME type>`, such as `video/webm`. A Google Docs file links to
    `docs.google.com/document/d/<id>/edit`, with the type `application/vnd.google-apps.document`.
  - A subfolder links to `drive.google.com/drive/folders/<id>`. It has no MIME type in the page: its icon is a `div`
    with `aria-label="Folder"`.
  - A folder of 100 files listed all 100 entries on one page. The view takes no page token, so a larger folder
    could be cut at a limit that wasn't found.
  - The view has the date each entry last changed, but no size and no length.
  - A made-up id answered 404. A file's id answered 200 with the file's name as the title and no entries. A
    `resourcekey` that the folder doesn't need was ignored.
- The owner decided: Sedjem's own listing through the embedded view, recorded here with its trade-offs; subfolders
  skipped and counted, a subfolder's own link added as its own course; natural name order, which the numbering
  follows.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Drive's embedded folder view, asked by the app and read with `html.parser` | One request to `drive.google.com`, already a Drive host the app uses; no key, no sign-in; the page gives each entry's id, name and type, and the folder's name; the files then go through the Drive file path that already has its allowlists, limits and messages | Undocumented, so Google can change the page without notice; no sizes or lengths; a limit on the number of entries may exist and wasn't found; Sedjem keeps the parser up to date until yt-dlp reads folders again |
| Wait for yt-dlp's folder extractor (yt-dlp#10484) | Nothing of Sedjem's own to keep up; yt-dlp follows Drive's changes | No date for a fix or a release; folders stay out of reach until then |
| The batch API that yt-dlp's extractor calls, with the key it finds in the folder's page | Gives sizes and Drive's own natural order | Needs a key scraped from a page, a multipart request to `clients6.google.com`, a new host, and the very request format that is failing in yt-dlp now |
| The Drive API with an API key or a Google sign-in | Documented and stable; sizes, lengths and pages of results | A key registered with Google and shipped in the app, or a sign-in flow; more than the issue asks for |

## Decision

Chosen: Drive's embedded folder view, read by the app, because it answers for any folder shared with anyone who has
the link, with no key and no new host, gives what a course needs (the folder's name, and each file's id, name and
type), and leaves the file downloads to the path that AgDR 21 already checked; the other ways either wait on yt-dlp
or add a key, a host or a sign-in. The owner chose the embedded view, skipping subfolders with a count, and natural
name order; the agent chose the parser, the checks on the request and the answer, how the files are judged, the
course's id and what the course leaves out.

- `app/drive.py` takes the folder link forms that people paste (`drive/folders/<id>`, with `u/<n>/` or `mobile/`,
  with or without `?usp=`) and reads the folder's id and any `resourcekey` (letters, digits, `_` and `-` only). It
  asks `https://drive.google.com/embeddedfolderview` with the id and that key alone, follows no redirect, and reads
  at most 8 MB of an HTML answer. A 400, 401, 403 or 404, or a redirect to Google's sign-in, says "This folder isn't
  shared with anyone who has the link. Sedjem can't sign in to Google Drive yet.", as a private file says it; 429
  says Drive is limiting the folder; any other answer, or a page without the `flip-entries` list, is refused with a
  message that names it. yt-dlp never sees a folder's link.
- The page is read with `html.parser`, tag by tag, never with a regular expression over it. Each entry's link is read
  as a pasted link is (`parse`), so only a Drive file's id can become a lecture, and only Drive's own hosts are
  asked for it later.
- A subfolder is known by its link or its Folder icon, and skipped. A file is audio or video when Drive gives it an
  `audio/` or `video/` type, or, when Drive gives no type or only `application/octet-stream`, when its name ends in
  the extension of a recording the app takes. Anything else, a Google Docs file among them, is another file,
  skipped. The page says how many of each were skipped, for example "2 subfolders and 3 other files skipped".
- The files are sorted in natural name order: runs of digits compared as numbers (Unicode digits, such as Arabic
  ones, too) and the rest without regard to case. The first 300 are listed, as for a playlist, with a note when there
  were more.
- The files picked become one course: its id is the folder's id, its title the folder's name, its link the folder's
  link, and each lecture's number its place in the list (a file left out keeps its number free, as a playlist's
  video does). A Drive folder's id and a YouTube playlist's id share the courses' ids; the ones seen do not overlap
  (Drive's begin with 0 or 1, playlists' with letters such as PL), and an assistant tells them apart by the lecture's
  `drive_id`. Course ids may now be up to 100 characters long, as Drive's ids may.
- Each lecture is a Drive file job (`drive_id`, `source_url`) with the course, so it is downloaded and transcribed by
  the AgDR 21 path with its allowlists, limits and messages. A Drive course has no files in the courses folder, as a
  Drive file has none, and adds no usage count: `courses_created` and the library's `courses` range stay YouTube's.
- The MCP tools need no route of their own: `inspect_link` and `transcribe_link` call the same API, so a folder works
  there under the same switches, and the course is listed among the courses with its folder's link.

## Consequences

- Google can change or remove the embedded view without notice. Then a folder's link says that Drive's answer
  wasn't the folder's list of files, and nothing is added; files keep working through yt-dlp. The tests use a copy
  of the real page's markup, so they can't see such a change; a check by hand against a real folder can.
- The view has no sizes or lengths, so the new transcription page gives the time before *Start* only as a share of
  each recording's length, and the size limits apply when each file downloads, as for a file's link.
- A folder with more entries than the view shows on one page would be cut without a sign; 100 came whole, and no
  larger public folder was tried.
- A `resourcekey` in the link is passed on to the listing, but no folder that needs one was tried; its files are read
  by their ids alone, as a file's link is, so a file that needs its own key is refused as not shared.
- No private folder was tried for real: a made-up id answered 404, and a private one is taken to answer the same, as
  Drive does for files.
- When a yt-dlp release reads Drive folders again (yt-dlp#10484), the listing should move back to yt-dlp's folder
  extractor, with its hosts in Drive's lists, keeping the natural order, the skipped counts and the course as they are
  here; this record would then be superseded.
