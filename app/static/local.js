/* Sedjem: recordings on this computer as a course (the server side is app/local.py). On the new transcription page,
   a folder's full path typed in the path field, or a folder picked with Choose folder in the desktop app, lists the
   folder's recordings in natural name order with what was skipped, as a Google Drive folder's files are listed
   (drive.js). Several files dropped or picked together are listed in the same way, never only the first. The ones
   picked become one course: a folder's is named after the folder, and files picked together get the name typed in
   the form. A folder's recordings, and files picked in the desktop app, are read where they are; files from the
   browser are uploaded one after another, as one file is. Loaded before app.js: it only defines things, and uses
   app.js's helpers (S, api, $, $$, esc, bytes, mixDir, toast, renderNew, refreshJobs, refreshStatus, showUpload,
   ICON) and youtube.js's YT_ICON when they run. */
"use strict";

// What is listed: found, what POST /api/local/inspect answered ("kind" "folder" or "files"); picked, the ids of the
// recordings to transcribe; files, the browser's files (an entry's id is its place in this list), else null; name, the
// course's name for files picked together; adding, while they are added; sent, once an upload failed after others
// were added: {course (its id), title, count, numbers (entry id -> lecture number of each one added)}, so that Start
// adds the rest to the same course instead of uploading those again.
const LOCAL = { found: null, picked: new Set(), files: null, name: "", adding: false, sent: null };

const localPicked = () => (LOCAL.found ? LOCAL.found.entries.filter((e) => LOCAL.picked.has(e.id)) : []);
const localSent = (id) => !!LOCAL.sent?.numbers.has(id);

function localClear() {
  Object.assign(LOCAL, { found: null, picked: new Set(), files: null, name: "", adding: false, sent: null });
}

// A listing to show in place of the file: every recording picked to start with, and for files picked together the
// name the server suggests ("Files added on 23 Nov 2026"), to change.
function localShow(found, files = null) {
  const f = S.form;
  if (f.previewUrl) URL.revokeObjectURL(f.previewUrl);
  Object.assign(f, { file: null, path: null, seconds: null, previewUrl: null });
  Object.assign(LOCAL, { found, files, name: found.kind === "files" ? found.title : "", adding: false, sent: null,
                         picked: new Set(found.entries.map((e) => e.id)) });
  renderNew();
}

// Several files from the browser's file field or a drop: the server lists their names as it lists a folder's files,
// with the folders dropped with them counted. folders: how many folders were dropped.
async function localUploads(files, folders = 0) {
  if (!files.length) {
    toast("A folder can't be dropped here: type its full path under “Or use a file or folder”, and its recordings are read where they are", "error");
    return;
  }
  try {
    localShow(await api.post("/api/local/inspect", { names: files.map((x) => x.name), folders }), files);
  } catch (e) { toast(e.message, "error"); }
}

// Paths picked together in the desktop app's file picker.
async function localPaths(paths) {
  try { localShow(await api.post("/api/local/inspect", { paths })); } catch (e) { toast(e.message, "error"); }
}

// The desktop app's Choose folder: the folder picked is used as a typed path is (app.js usePath).
async function localPickFolder() {
  let path;
  try { path = await desktopApi().pick_folder(); } catch (e) { toast(e.message, "error"); return; }
  if (path) await usePath(path);
}

// What a listing skipped, in words, as the server says it (app/local.py words): "2 subfolders, 1 other file and 1
// link to a file outside the folder", or "".
function localSkipped(s) {
  const n = s?.folders || 0, m = s?.files || 0, k = s?.links || 0;
  const parts = [n ? `${n} ${LOCAL.found?.kind === "folder" ? "sub" : ""}folder${n === 1 ? "" : "s"}` : "",
    m ? `${m} other file${m === 1 ? "" : "s"}` : "",
    k ? `${k} link${k === 1 ? " to a file" : "s to files"} outside the folder` : ""].filter(Boolean);
  return parts.length > 1 ? `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}` : parts.join("");
}

// The listing: what it is, the recordings numbered in natural name order, each with its name and size, and what was
// skipped. A folder's numbers are its list's, as a Drive folder's (a recording left out keeps its number free); files
// picked together are numbered 1, 2, 3 among the ones picked. A recording already added before an upload failed is
// marked as added, with its number, and can't be picked again.
function localFound(f) {
  const n = f.entries.length, picked = localPicked(), upload = !!LOCAL.files, folder = f.kind === "folder";
  const skipped = localSkipped(f.skipped), what = `${n} recording${n === 1 ? "" : "s"}`;
  const [name, sub] = folder ? [f.title, `Folder on this computer · ${what}`]
    : [what, upload ? "Picked together, to upload" : "Picked together on this computer"];
  const how = upload ? "Each is uploaded to the app, as a single file is." : "Each is read where it is, not copied.";
  const after = folder && f.skipped.folders ? " A subfolder can be added as a course of its own, by its own path."
    : !folder && f.skipped.folders ? " A folder is added by its full path, so that its recordings are read where they are." : "";
  const first = LOCAL.sent ? LOCAL.sent.numbers.size : 0;
  const place = new Map([...(LOCAL.sent?.numbers || []), ...picked.map((e, i) => [e.id, first + i + 1])]);
  const off = LOCAL.adding ? "disabled" : "";
  return `<div class="yt-found local-found"><div class="chosen yt-chosen">
      <div class="ico">${folder ? ICON.folder : YT_ICON.course}</div>
      <div class="grow"><div class="name" dir="${mixDir(name)}">${esc(name)}</div><div class="sub">${esc(sub)}</div></div>
      <button class="btn btn-sm" id="localChange" ${off}>Change</button></div>
    <p class="hint yt-caps">${folder ? "The recordings picked become a course named after the folder, numbered in this order."
      : "The recordings picked become one course, numbered in this order."} ${how}</p>
    ${skipped ? `<p class="hint local-skipped">${esc(skipped)} skipped.${after}</p>` : ""}
    ${f.cut ? `<p class="hint">Only the first ${n} recordings ${folder ? "of this folder " : ""}are shown.</p>` : ""}
    <div class="yt-list-head"><span>${picked.length} of ${n} picked</span>
      <button type="button" class="linkish" id="localAll" ${off}>All</button><button type="button" class="linkish" id="localNone" ${off}>None</button></div>
    <ol class="yt-list" aria-label="Recordings">${f.entries.map((e) => localRow(e, folder ? e.index : place.get(e.id), off)).join("")}</ol></div>`;
}

function localRow(e, number, off) {
  const size = LOCAL.files ? LOCAL.files[Number(e.id)]?.size : e.size, sent = localSent(e.id);
  return `<li><label class="${sent ? "off" : ""}">
      <input type="checkbox" data-local-entry="${esc(e.id)}" ${LOCAL.picked.has(e.id) ? "checked" : ""} ${sent ? "disabled" : off} aria-label="${esc(e.name)}">
      <span class="n">${esc(number ?? "")}</span><span class="t" dir="${mixDir(e.name)}" title="${esc(e.name)}">${esc(e.name)}</span>
      <span></span><span class="len">${size != null ? bytes(size) : ""}</span>
      ${sent ? `<span class="why">Added to the course</span>` : ""}</label></li>`;
}

// In the Options card, in place of the title: a folder's course is named after the folder; files picked together
// get the name typed here.
function localNameField() {
  if (LOCAL.found.kind === "folder") {
    return `<div class="field"><span class="field-label">Titles</span><p class="hint">Each transcription is named after its file, and the course after the folder; you can rename a transcription later.</p></div>`;
  }
  const sent = LOCAL.sent, name = sent ? sent.title : LOCAL.name;
  return `<div class="field">
      <label for="courseName">Course name</label>
      <input type="text" id="courseName" dir="${mixDir(name)}" data-mixdir value="${esc(name)}" placeholder="${esc(LOCAL.found.title)}" ${LOCAL.adding || sent ? "disabled" : ""}>
      <p class="hint">${sent ? "The rest are added to this course, which has the recordings added before." : "Each transcription is named after its file."}</p></div>`;
}

function bindLocal() {
  const change = $("#localChange");
  if (change) change.onclick = () => { localClear(); renderNew(); $("#drop")?.focus(); };
  $$("[data-local-entry]").forEach((box) => (box.onchange = () => {
    const id = box.dataset.localEntry;
    if (box.checked) LOCAL.picked.add(id); else LOCAL.picked.delete(id);
    renderNew();
    $$("[data-local-entry]").find((b) => b.dataset.localEntry === id)?.focus();
  }));
  const all = $("#localAll"), none = $("#localNone");
  if (all) all.onclick = () => { LOCAL.picked = new Set(LOCAL.found.entries.filter((e) => !localSent(e.id)).map((e) => e.id)); renderNew(); };
  if (none) none.onclick = () => { LOCAL.picked = new Set(); renderNew(); };
  const name = $("#courseName");
  if (name) name.oninput = () => (LOCAL.name = name.value);
  const pick = $("#pickFolder");
  if (pick) pick.onclick = localPickFolder;
}

// Start: one transcription for each recording picked, in the order of the list, as one course. A folder's, or paths
// picked in the desktop app, in one request (read where they are); the browser's files uploaded one after another,
// the first answer giving the course's id to the rest. Opens the first.
async function localStart(params) {
  const picked = localPicked(), f = LOCAL.found;
  const title = LOCAL.sent ? LOCAL.sent.title : LOCAL.name.trim() || f.title;
  LOCAL.adding = true;
  renderNew();
  try {
    let jobs;
    if (LOCAL.files) jobs = await localUpload(params, picked, title);
    else {
      const body = f.kind === "folder" ? { path: f.path, entries: picked.map((e) => e.id) }
        : { paths: picked.map((e) => e.id), title };
      jobs = (await api.post("/api/local", { ...params, ...body })).jobs;
    }
    localClear();
    S.uploading = null;
    S.form.confirm = false;  // the upload, or the paired computer, is confirmed again for the next one
    toast(jobs.length === 1 ? "Added: it is transcribed next" : `${jobs.length} recordings added: they are transcribed in turn`);
    if (params.run_on) await refreshStatus();  // that computer isn't asked about again (remote.js)
    await refreshJobs();
    location.hash = `#/job/${jobs[0].id}`;
  } catch (e) {
    LOCAL.adding = false;
    S.uploading = null;
    toast(e.message, "error");
    await refreshJobs();  // the recordings added before an upload failed are in the list
    renderNew();
  }
}

// Each one added leaves the picked ones and is kept in LOCAL.sent, so that after a failed upload, Start goes on with
// the same course and the next numbers.
async function localUpload(params, picked, title) {
  const jobs = [], sent = LOCAL.sent || { course: "", title, count: 0, numbers: new Map() };
  sent.count = Math.max(sent.count, sent.numbers.size + picked.length);
  for (const e of picked) {
    const file = LOCAL.files[Number(e.id)], index = sent.numbers.size + 1;
    S.uploading = { loaded: 0, total: file.size, n: index, of: sent.count };
    renderNew();
    try {
      // its modified time is the recording's date when the file's tags have none (sysinfo.recording)
      const job = await api.upload(file, { ...params, title: "", name: file.name, modified: file.lastModified,
        course_title: sent.title, course_index: index, course_count: sent.count, ...(sent.course ? { course: sent.course } : {}) }, showUpload);
      sent.course = job.course.id;
      sent.numbers.set(e.id, index);
      LOCAL.sent = sent;
      LOCAL.picked.delete(e.id);
      jobs.push(job);
    } catch (err) {
      const n = sent.numbers.size;
      throw new Error(n ? `${file.name} couldn't be uploaded (${err.message}). The ${n} recording${n === 1 ? "" : "s"} before it ${n === 1 ? "was" : "were"} added; Start adds the rest to the same course` : err.message);
    }
  }
  return jobs;
}
