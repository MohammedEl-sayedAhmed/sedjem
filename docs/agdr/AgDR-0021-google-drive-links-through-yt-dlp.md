---
id: AgDR-0021
timestamp: 2026-11-19T08:29:01Z
agent: claude-code
model: claude-opus-5-5
trigger: user-prompt
status: executed
---

# Google Drive files through yt-dlp's Drive extractor, with allowlists of their own, and folders later

> In the context of the Link field on the new transcription page, which now takes a Google Drive file shared with anyone who has the link as well as a YouTube video or playlist, facing files that have to be downloaded without signing in to Google, large files that Drive puts behind a page saying it can't scan them for viruses, and a yt-dlp folder extractor that fails on every Drive folder today, I decided to read and download Drive files with yt-dlp's GoogleDrive extractor only, through the same helpers as YouTube links but with Drive's own lists of the links taken, the extractor run and the hosts a download may come from, and to recognise a Drive folder's link and refuse it with a clear message before yt-dlp sees it, to achieve Drive files that share the code and the queue of YouTube links while YouTube's lists stay as they were and yt-dlp's generic extractor never runs, accepting that folders wait for yt-dlp to read them again and that files that aren't shared with anyone who has the link can't be read until Sedjem can sign in.

## Context

- Issue [#2](https://github.com/MohammedEl-sayedAhmed/sedjem/issues/2): transcribe a video or audio file from a
  Google Drive share link like a file on the computer, a shared folder perhaps as a course. Files shared with anyone
  who has the link download without signing in; private files would need a Google sign-in or the Drive API.
- The owner chose one link field for YouTube and Drive, with the same flow: check the link, show what was found, add
  it.
- YouTube links already use yt-dlp (`app/youtube.py`), limited to YouTube's link hosts, YouTube's extractors and
  YouTube's stream hosts, so a link can lead nowhere else and the generic extractor never reads another page.
- yt-dlp 2026.10.26, its newest release, has two Drive extractors. GoogleDrive reads a file's name and length from
  Drive's playback service and offers the file itself as its `source` format, after answering the virus-scan page's
  form; its other formats are copies Drive made to play a video. GoogleDrive:Folder fails on Drive folders today with
  "expected string or bytes-like object, got 'bool'", which is yt-dlp's open issue
  [#10484](https://github.com/yt-dlp/yt-dlp/issues/10484).
- Checks with real public links on 19 November 2026: the large Big Buck Bunny file (`0ByeS4oOUV-49Zzh4R1J6R09zazQ`)
  got past the virus-scan page and its 186 MB source began to download (stopped on purpose after 3.9 MB); yt-dlp's
  own test files `1IP0o8dHcQrIHGgVyp0Ofvx2cGfLzyO1x` and `1RAGWRgzn85TXCaCk4gxnwF6TGUaZatzE`, now removed, answer 404
  and gave the message for a file that isn't shared; folders failed as above.
- An uploaded file is converted to audio, a video's copy to play beside the transcript is made, and the upload is
  deleted unless it is kept. A Drive file is the same kind of thing, so it can take that path from `source.<ext>`.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| yt-dlp's GoogleDrive extractor, with Drive's own lists of link hosts, extractors and download hosts; folder links refused until yt-dlp reads folders again | Shares `_ydl` and `_extract` with YouTube links, and yt-dlp is already in every build; yt-dlp follows Drive's changes, the virus-scan page among them, which passed on a real large file; the generic extractor never runs | No folders for now, so a course on Drive is added one file at a time; Drive's changes reach Sedjem only with a yt-dlp update |
| Sedjem's own Drive downloader, with urllib, folders included | No wait for yt-dlp's folder fix; full control of the virus-scan page and the requests made | Drive's download and listing endpoints are undocumented and change, as #10484 shows; Sedjem would have to follow every change itself, and keep the folder API's key and request format up to date |
| The Drive API with a Google sign-in | Documented and stable; reads folders, private files and shared drives | Needs an OAuth client registered with Google, its consent screen and its review, and a sign-in flow in a local app; keeps a token for the user's Drive; more than the issue asks for now |

## Decision

Chosen: yt-dlp's GoogleDrive extractor with allowlists of their own, and folders later, because it reuses the YouTube
code path and the dependency the app already ships, it handles the virus-scan page on a real large file, and it keeps
Drive's requests to the hosts that Drive itself uses, without a sign-in; a folder extractor of Sedjem's own would take
on the very upkeep that yt-dlp's is failing at now. The owner chose the one link field, and files now with folders
later after the real-link checks; the agent chose yt-dlp over its own downloader and the Drive API, the separate
module and the lists.

- `app/drive.py` holds Drive's lists: links to files on `drive.google.com` and `docs.google.com` only, rebuilt from
  the file id (`…/file/d/<id>/view`; `open?id=` and `uc?id=` links are read as files); the extractor `googledrive`
  only, so neither the generic nor the folder extractor runs; downloads only from `drive.google.com`,
  `docs.google.com`, `drive.usercontent.google.com` and `*.googleusercontent.com`. YouTube's lists don't change.
- `app/youtube.py`'s `_extract` takes the site it reads for (a `Site` with its link check, extractor keys, page hosts,
  error and wording), so Drive uses the same checks with its own values.
- A Drive folder's link (`…/drive/folders/<id>`, also with `u/0/` or `mobile/`) is recognised and refused with
  "Google Drive folders can't be read yet. Add the files one by one, each with its own link.", before yt-dlp sees it;
  the form shows it as it shows any link's error.
- A job from Drive has `source_url` and `drive_id`, and no course. Its download is the `source` format alone, saved
  as `source.<ext>`, stopped once it is larger than the upload limit or the free disk space allows. From there the
  job is an uploaded file: no streaming, no captions, no files in the courses folder.
- A file that isn't shared, or was removed, is refused with "This file isn't shared with anyone who has the link.
  Sedjem can't sign in to Google Drive yet."
- No usage count is added, and the YouTube counts leave Drive jobs out.

## Consequences

- Drive is a new outside host. The privacy policy names the playback service and the download hosts, and the threat
  model has a section on Drive links.
- Folders come back in a later change once yt-dlp's folder extractor works again (#10484), as a course like a
  YouTube playlist, as the owner asked; until then a folder's files are added one by one.
- The tests run yt-dlp's real GoogleDrive extractor and downloader, the virus-scan page included, against a stand-in
  for Google's servers, so a change in yt-dlp that breaks them fails a test.
- Private files and shared drives that need a sign-in stay out of reach until a later change adds one, which would
  be the third option above.
