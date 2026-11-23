/* Sedjem: Google Drive links (the server side is app/drive.py). The Link tab of the new transcription page
   (youtube.js) checks a link; driveFound shows what was found: a Drive file, or a Drive folder's audio and video
   files in natural name order, picked as a playlist's videos are (youtube.js binds the list), with what was skipped.
   Start adds a transcription for the file, or one for each file picked, which together make a course named after
   the folder. A Drive link, found or refused, hides YouTube's own choices (driveLink).
   A job's page links to its file. Loaded before app.js: it only defines things, and uses app.js's helpers (esc,
   human, mixDir) and youtube.js's state (YT, ytPicked) when they run. */
"use strict";

const DRIVE_ICON = {
  file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="m10 11.5 4.5 2.5-4.5 2.5z" fill="currentColor"/></svg>',
  folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V17a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="m10.5 11 4 2.25-4 2.25z" fill="currentColor"/></svg>',
};
// Whether a pasted link is Google Drive's, by its host, as the server reads it (app/drive.py HOSTS; a link without
// its scheme is taken as https://).
const DRIVE_HOSTS = new Set(["drive.google.com", "docs.google.com"]);
function driveLink(url) {
  const u = String(url || "").trim();
  for (const s of [u, `https://${u}`]) {
    try {
      const p = new URL(s);
      if (p.protocol === "http:" || p.protocol === "https:") return DRIVE_HOSTS.has(p.hostname.toLowerCase());
    } catch { /* not a link: try it with https:// */ }
  }
  return false;
}

// A job's link, only as the app makes it (app/drive.py file_url), so a job.json changed by hand can't put another
// address in the page.
const driveHref = (url) => (/^https:\/\/drive\.google\.com\/file\/d\/[\w-]{28,100}\/view$/.test(String(url || "")) ? url : "");

// The file found: its name once, as Drive shows it (the transcription is named after it without the extension).
// A folder's files: driveFolder.
function driveFound(f) {
  if (f.kind === "folder") return driveFolder(f);
  const sub = ["Google Drive file", f.duration ? human(f.duration) : ""];
  return `<div class="yt-found drive-found"><div class="chosen yt-chosen">
      <div class="ico">${DRIVE_ICON.file}</div>
      <div class="grow"><div class="name" dir="${mixDir(f.name)}">${esc(f.name)}</div>
        <div class="sub">${sub.filter(Boolean).map(esc).join(" · ")}</div></div>
      <button class="btn btn-sm" id="ytChange" ${YT.adding ? "disabled" : ""}>Change</button></div>
    <p class="hint yt-caps">The file is downloaded, then transcribed like a file on this computer.</p></div>`;
}

// What a folder's listing skipped, in words, as the server says it (app/drive.py skipped_words): "2 subfolders and
// 1 other file", or "".
function driveSkipped(s) {
  const n = s?.folders || 0, m = s?.files || 0;
  return [n ? `${n} subfolder${n === 1 ? "" : "s"}` : "", m ? `${m} other file${m === 1 ? "" : "s"}` : ""].filter(Boolean).join(" and ");
}

// A folder found: its name, its audio and video files numbered in natural name order (Drive gives no lengths), each
// with its name as Drive shows it, and how many subfolders and other files were skipped.
function driveFolder(f) {
  const n = f.entries.length, skipped = driveSkipped(f.skipped);
  return `<div class="yt-found drive-found"><div class="chosen yt-chosen">
      <div class="ico">${DRIVE_ICON.folder}</div>
      <div class="grow"><div class="name" dir="${mixDir(f.title)}">${esc(f.title)}</div>
        <div class="sub">Google Drive folder · ${n} audio or video file${n === 1 ? "" : "s"}</div></div>
      <button class="btn btn-sm" id="ytChange" ${YT.adding ? "disabled" : ""}>Change</button></div>
    <p class="hint yt-caps">The files picked become a course named after the folder, numbered in this order. Each is downloaded, then transcribed like a file on this computer.</p>
    ${skipped ? `<p class="hint drive-skipped">${esc(skipped)} skipped.${f.skipped.folders ? " A subfolder can be added as a course of its own, with its own link." : ""}</p>` : ""}
    ${f.cut ? `<p class="hint">Only the first ${n} audio or video files of this folder are shown.</p>` : ""}
    <div class="yt-list-head"><span>${ytPicked().length} of ${n} picked</span>
      <button type="button" class="linkish" id="ytAll">All</button><button type="button" class="linkish" id="ytNone">None</button></div>
    <ol class="yt-list" aria-label="Files">${f.entries.map(driveRow).join("")}</ol></div>`;
}

function driveRow(e) {
  return `<li><label>
      <input type="checkbox" data-yt-entry="${esc(e.id)}" ${YT.picked.has(e.id) ? "checked" : ""} aria-label="${esc(e.name)}">
      <span class="n">${esc(e.index)}</span><span class="t" dir="${mixDir(e.name)}" title="${esc(e.name)}">${esc(e.name)}</span>
      <span></span><span class="len"></span></label></li>`;
}
