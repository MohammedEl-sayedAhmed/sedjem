/* Sedjem: YouTube links (the server side is app/youtube.py and app/courses.py). On the new transcription
   page a link to a video or a playlist is checked, its videos are shown with the ones that have captions made
   by people, and Start adds one transcription per video. A playlist is a course: the sidebar shows it once
   with its lectures, and a lecture's page has the link, the lectures before and after it, and its course
   folder. Loaded before app.js: it only defines things, and uses app.js's helpers (S, api, $, esc, clock,
   human, mixDir, toast) when they run. */
"use strict";

const YT_ICON = {
  video: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="5" width="19" height="14" rx="3.5"/><path d="m10 9.2 5 2.8-5 2.8z" fill="currentColor"/></svg>',
  captions: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2.5" y="5" width="19" height="14" rx="2.5"/><path d="M10.5 10.2a2.2 2.2 0 1 0 0 3.6M17 10.2a2.2 2.2 0 1 0 0 3.6"/></svg>',
  course: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 5.5A1.5 1.5 0 0 1 5.5 4H19v14H5.5A1.5 1.5 0 0 0 4 19.5z"/><path d="M4 19.5A1.5 1.5 0 0 0 5.5 21H19v-3M9 8h6"/></svg>',
  prev: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 6-6 6 6 6"/></svg>',
  next: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6 6 6-6 6"/></svg>',
};
const YT_LANG = { ar: "Arabic", en: "English" };
// A job's link, only as the app makes it (a job.json changed by hand can't put another address in the page).
const ytHref = (url) => (/^https:\/\/www\.youtube\.com\/watch\?v=[\w-]{11}$/.test(String(url || "")) ? url : "");
const ytLang = (code) => YT_LANG[String(code || "").split("-")[0].toLowerCase()] || code;

// The link being checked or added. found: what POST /api/youtube/inspect answered; picked: the ids of the
// videos to transcribe; captions: use the captions people made, where a video has them.
const YT = { url: "", checked: "", busy: false, adding: false, found: null, error: "", picked: new Set(), captions: true };

// The icon for how a transcript was made: on this computer, by a hosted service, or from YouTube's captions.
const kindIcon = (j) => (j.kind === "captions" ? YT_ICON.captions : j.kind === "hosted" ? ICON.cloud : ICON.laptop);
// The small icon in the list: none for a local recording, as before.
const listIcon = (j) => (j.kind === "captions" ? YT_ICON.captions : j.kind === "hosted" ? ICON.cloud : j.youtube_id ? YT_ICON.video : "");

// ---------------------------------------------------------------------------------------------
// The new transcription page
// ---------------------------------------------------------------------------------------------
function ytCard() {
  const y = YT;
  const form = `<form class="yt-form" id="ytForm">
      <input type="text" inputmode="url" id="ytUrl" value="${esc(y.url)}" placeholder="https://www.youtube.com/playlist?list=…" spellcheck="false" autocomplete="off" aria-label="YouTube link" ${y.busy || y.adding ? "disabled" : ""}>
      <button class="btn" type="submit" ${y.busy || y.adding ? "disabled" : ""}>${y.busy ? "Checking…" : "Check"}</button></form>`;
  const intro = `<p class="hint">A link to a video or a playlist. Each video is downloaded (up to 720p, to play beside its transcript) and transcribed in turn; a playlist becomes a course, with its transcripts saved as files you can read or paste into a chat assistant. Download only what YouTube's terms allow you to.</p>`;
  return `${y.found ? ytFound(y.found) : form + intro}
    <div id="ytNote">${ytNote()}</div>
    ${y.error ? `<div class="yt-error" role="alert">${esc(y.error)}</div>` : ""}`;
}

// No JavaScript runtime: YouTube may hold back some videos. Deno can be downloaded like a model.
function ytNote() {
  const st = S.status, d = st?.deno;
  if (!st || st.youtube?.runtime) return "";
  return `<div class="yt-note">${ICON.info}<div><b>YouTube asks for a JavaScript runtime, and none was found.</b>
    Without one, some videos may not download. ${d ? "Download Deno for Sedjem, or install Deno or Node.js yourself." : "Install Deno or Node.js, then start Sedjem again."}
    ${d ? `<div class="yt-dl">${downloadBlock("deno", d)}</div>` : ""}</div></div>`;
}

function ytRefreshNote() {
  const el = $("#ytNote");
  if (el) el.innerHTML = ytNote();
}

function ytFound(f) {
  const usable = f.entries.filter((e) => e.available);
  const caps = usable.filter((e) => e.use);
  const unchecked = usable.filter((e) => e.captions == null).length;
  const picked = ytPicked();
  const head = `<div class="chosen yt-chosen">
      <div class="ico">${f.kind === "playlist" ? YT_ICON.course : YT_ICON.video}</div>
      <div class="grow"><div class="name" dir="${mixDir(f.title)}">${esc(f.title)}</div>
        <div class="sub">${[f.kind === "playlist" ? `Playlist · ${f.count} video${f.count === 1 ? "" : "s"}` : "Video", f.uploader, f.duration ? human(f.duration) : ""].filter(Boolean).map(esc).join(" · ")}</div></div>
      <button class="btn btn-sm" id="ytChange" ${YT.adding ? "disabled" : ""}>Change</button></div>`;
  let capsText;
  if (f.kind === "video") {
    capsText = caps.length ? `It has captions made by people in ${esc(ytLang(caps[0].use))}, so they can be its transcript and no model needs to run. The video is still downloaded, to play beside it.`
      : f.entries[0].captions?.length ? "Its captions made by people are in another language than the video, so the model below transcribes it."
        : "It has no captions made by people (YouTube's automatic ones are never used), so the model below transcribes it.";
  } else {
    capsText = `${caps.length} of ${usable.length} video${usable.length === 1 ? "" : "s"} ${caps.length === 1 ? "has" : "have"} captions made by people in their own language (Arabic or English); those can be their transcripts, and the model below transcribes the rest.`
      + (unchecked ? ` ${unchecked} video${unchecked === 1 ? " was" : "s were"} not checked yet; that happens when ${unchecked === 1 ? "it is" : "each is"} downloaded.` : "");
  }
  const list = f.kind === "playlist" ? `<div class="yt-list-head"><span>${picked.length} of ${usable.length} picked</span>
      <button type="button" class="linkish" id="ytAll">All</button><button type="button" class="linkish" id="ytNone">None</button></div>
    <ol class="yt-list" aria-label="Videos">${f.entries.map(ytRow).join("")}</ol>` : "";
  return `<div class="yt-found">${head}
    <p class="hint yt-caps">${capsText}</p>
    ${caps.length ? `<label class="toggle"><input type="checkbox" id="ytCaptions" ${YT.captions ? "checked" : ""}> Use the captions made by people where a video has them</label>` : ""}
    ${f.cut ? `<p class="hint">Only the first ${f.count} videos of this playlist are shown.</p>` : ""}
    ${list}</div>`;
}

function ytRow(e) {
  const on = YT.picked.has(e.id);
  const why = e.problem || (e.live ? "A live stream" : !e.available ? "Not available" : "");
  return `<li><label class="${e.available ? "" : "off"}">
      <input type="checkbox" data-yt-entry="${esc(e.id)}" ${on ? "checked" : ""} ${e.available ? "" : "disabled"} aria-label="${esc(e.title)}">
      <span class="n">${e.index}</span><span class="t" dir="${mixDir(e.title)}" title="${esc(e.title)}">${esc(e.title)}</span>
      ${e.use ? `<span class="pill cc" title="Captions made by people in ${esc(ytLang(e.use))}">${YT_ICON.captions} ${esc(String(e.use).split("-")[0].toUpperCase())}</span>` : "<span></span>"}
      <span class="len">${e.duration ? clock(e.duration) : ""}</span>
      ${why ? `<span class="why">${esc(why)}</span>` : ""}</label></li>`;
}

const ytPicked = () => (YT.found ? YT.found.entries.filter((e) => e.available && YT.picked.has(e.id)) : []);

// The picked videos the model will transcribe (the rest have captions to use), and their length (the estimate).
const ytForModel = () => ytPicked().filter((e) => !(YT.captions && e.use));
const ytSeconds = () => ytForModel().reduce((t, e) => t + (e.duration || 0), 0) || null;

function bindYoutube() {
  const form = $("#ytForm");
  if (form) {
    const input = $("#ytUrl");
    input.oninput = () => (YT.url = input.value);
    form.onsubmit = (e) => { e.preventDefault(); ytCheck(); };
  }
  const change = $("#ytChange");
  if (change) change.onclick = () => { Object.assign(YT, { found: null, error: "", picked: new Set() }); renderNew(); $("#ytUrl")?.focus(); };
  const caps = $("#ytCaptions");
  if (caps) caps.onchange = () => { YT.captions = caps.checked; renderNew(); };
  $$("[data-yt-entry]").forEach((box) => (box.onchange = () => {
    if (box.checked) YT.picked.add(box.dataset.ytEntry); else YT.picked.delete(box.dataset.ytEntry);
    renderNew();
  }));
  const all = $("#ytAll"), none = $("#ytNone");
  if (all) all.onclick = () => { YT.picked = new Set(YT.found.entries.filter((e) => e.available).map((e) => e.id)); renderNew(); };
  if (none) none.onclick = () => { YT.picked = new Set(); renderNew(); };
}

async function ytCheck() {
  const url = YT.url.trim();
  if (!url || YT.busy) return;
  Object.assign(YT, { busy: true, error: "", found: null });
  renderNew();
  try {
    const found = await api.post("/api/youtube/inspect", { url });
    Object.assign(YT, { found, checked: url, picked: new Set(found.entries.filter((e) => e.available).map((e) => e.id)) });
  } catch (e) { YT.error = e.message; }
  YT.busy = false;
  if (S.route.name === "new") renderNew();
}

// Start: one transcription per picked video, in playlist order. Opens the first.
async function ytStart(params) {
  const picked = ytPicked();
  YT.adding = true;
  renderNew();
  try {
    const r = await api.post("/api/youtube", { ...params, url: YT.checked, captions: YT.captions, entries: picked.map((e) => e.id) });
    Object.assign(YT, { url: "", checked: "", found: null, picked: new Set(), adding: false });
    S.form.confirm = false;  // the upload is confirmed again for the next one
    toast(r.jobs.length === 1 ? "Added: it downloads first, then it is transcribed" : `${r.jobs.length} videos added: they download and are transcribed in turn`);
    await refreshJobs();
    location.hash = `#/job/${r.jobs[0].id}`;
  } catch (e) {
    YT.adding = false;
    toast(e.message, "error");
    renderNew();
  }
}

// ---------------------------------------------------------------------------------------------
// Courses: the sidebar and the job page
// ---------------------------------------------------------------------------------------------

// The courses in the list, by playlist: {id, course, main (its newest job), jobs, lectures: the newest job
// of each lecture, in playlist order}.
function courseGroups(jobs) {
  const by = new Map();
  for (const j of jobs) {
    if (!j.course) continue;
    if (!by.has(j.course.id)) by.set(j.course.id, { id: j.course.id, course: j.course, main: j, jobs: [] });
    by.get(j.course.id).jobs.push(j);
  }
  for (const g of by.values()) {
    const newest = new Map();
    for (const j of g.jobs) if (!newest.has(j.course.index)) newest.set(j.course.index, j);  // the list is newest first
    g.lectures = [...newest.values()].sort((a, b) => a.course.index - b.course.index);
  }
  return [...by.values()];
}

const COURSE_OPEN = {};  // course id -> its lectures shown in the sidebar (kept across the list's refreshes)

function courseItem(g) {
  const here = S.route.name === "job" && g.jobs.some((j) => j.id === S.route.id);
  const open = COURSE_OPEN[g.id] ?? here;
  const done = g.lectures.filter((j) => j.status === "done").length;
  const busy = g.lectures.find((j) => ACTIVE.has(j.status));
  const failed = g.lectures.some((j) => ["failed", "interrupted"].includes(j.status));
  const share = g.lectures.reduce((t, j) => t + (j.status === "done" ? 100 : ACTIVE.has(j.status) ? jobPercent(j) : 0), 0) / g.lectures.length;
  const total = g.lectures.reduce((t, j) => t + (j.audio_s || 0), 0);
  const state = busy ? statusLabel(busy) : failed ? `<span class="s" style="color:var(--danger)">Failed</span>`
    : done === g.lectures.length ? statusLabel({ status: "done" }) : `<span class="s" style="color:var(--muted)">${done}/${g.lectures.length}</span>`;
  const first = g.lectures.find((j) => S.route.name === "job" && j.id === S.route.id) || g.lectures[0];
  return `<div class="job-item course${here ? " active" : ""}">
    <a class="t" href="#/job/${first.id}" dir="${mixDir(g.course.title)}">${esc(g.course.title || "Course")}</a>${state}
    <span class="d">${YT_ICON.course.replace("<svg", '<svg style="width:13px;height:13px"')}${done} of ${g.lectures.length} done${total ? ` · ${clock(total)}` : ""}</span>
    ${busy ? `<div class="bar"><i style="width:${share.toFixed(1)}%"></i></div>` : ""}
    <details class="lectures" data-course="${esc(g.id)}"${open ? " open" : ""}><summary>${g.lectures.length} lecture${g.lectures.length === 1 ? "" : "s"}</summary>
      <ol>${g.lectures.map((j) => lectureLink(j, S.route.name === "job" && S.route.id === j.id)).join("")}</ol></details>
  </div>`;
}

function lectureLink(j, on) {
  const state = j.status === "done" ? "done" : ACTIVE.has(j.status) ? "busy" : ["failed", "interrupted"].includes(j.status) ? "bad" : "off";
  const word = { done: "Done", busy: "In progress", bad: "Failed", off: j.status === "cancelled" ? "Cancelled" : "Waiting" }[state];
  return `<li><a class="lec${on ? " on" : ""}" href="#/job/${j.id}"${on ? ' aria-current="page"' : ""} title="${esc(`${j.title} · ${word}`)}">
    <span class="n">${esc(j.course.index)}</span><span class="lt" dir="${mixDir(j.title)}">${esc(j.title || "Untitled")}</span>
    <span class="lec-state ${state}" aria-label="${word}"></span></a></li>`;
}

function bindCourses() {
  $$("#jobList details.lectures").forEach((d) => (d.ontoggle = () => (COURSE_OPEN[d.dataset.course] = d.open)));
}

// The job page of a YouTube video: its course, the lectures before and after it, the folder of its files, and
// a lecture's button that hides its whole course from assistants (assistants.js).
function courseBar(j) {
  if (!j.youtube_id) return "";
  const folder = `<button type="button" class="btn btn-sm" id="openCourse">${ICON.folder} ${j.course ? "Open course folder" : "Open folder"}</button>`;
  if (!j.course) return `<nav class="course-bar" aria-label="YouTube video"><span class="course-label">${YT_ICON.video} A video on its own, saved with the others in the Videos folder</span>${folder}</nav>`;
  const g = courseGroups(S.jobs).find((x) => x.id === j.course.id);
  const lectures = g ? g.lectures : [];
  const prev = [...lectures].reverse().find((x) => x.course.index < j.course.index);
  const next = lectures.find((x) => x.course.index > j.course.index);
  const step = (x, label, icon, after) => x
    ? `<a class="btn btn-sm" href="#/job/${esc(x.id)}" title="${esc(`${x.course.index}. ${x.title}`)}">${after ? "" : icon}${label}${after ? icon : ""}</a>`
    : `<span class="btn btn-sm" aria-disabled="true">${after ? "" : icon}${label}${after ? icon : ""}</span>`;
  return `<nav class="course-bar" aria-label="Course">
    <span class="course-label">${YT_ICON.course}<span><b dir="${mixDir(j.course.title)}">${esc(j.course.title)}</b> · lecture ${esc(j.course.index)} of ${esc(j.course.count)}</span></span>
    <span class="course-steps">${step(prev, "Previous", YT_ICON.prev, false)}${step(next, "Next", YT_ICON.next, true)}</span>${folder}${aiCourseButton(j)}</nav>`;
}

function bindCourseBar(j) {
  const open = $("#openCourse");
  if (open) open.onclick = async () => {
    try { await api.post("/api/open-folder", { job: j.id }); } catch (e) { toast(e.message, "error"); }
  };
}
