/* Sedjem: Google Drive links (the server side is app/drive.py). The Link tab of the new transcription page
   (youtube.js) checks a link; for a Drive file, driveFound shows what was found, and Start adds its transcription.
   A Drive folder's link is refused by the server for now, and the form shows why as it shows any link's error;
   a Drive link, found or refused, hides YouTube's own choices (driveLink).
   A job's page links to its file. Loaded before app.js: it only defines things, and uses app.js's helpers (esc,
   human, mixDir) and youtube.js's state (YT) when they run. */
"use strict";

const DRIVE_ICON = {
  file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/><path d="m10 11.5 4.5 2.5-4.5 2.5z" fill="currentColor"/></svg>',
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
function driveFound(f) {
  const sub = ["Google Drive file", f.duration ? human(f.duration) : ""];
  return `<div class="yt-found drive-found"><div class="chosen yt-chosen">
      <div class="ico">${DRIVE_ICON.file}</div>
      <div class="grow"><div class="name" dir="${mixDir(f.name)}">${esc(f.name)}</div>
        <div class="sub">${sub.filter(Boolean).map(esc).join(" · ")}</div></div>
      <button class="btn btn-sm" id="ytChange" ${YT.adding ? "disabled" : ""}>Change</button></div>
    <p class="hint yt-caps">The file is downloaded, then transcribed like a file on this computer.</p></div>`;
}
