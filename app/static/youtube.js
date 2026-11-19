/* Sedjem: YouTube links (the server side is app/youtube.py and app/courses.py). On the new transcription
   page, the Link tab checks a link to a video or a playlist, shows its videos with the ones that have captions
   made by people, and Start adds one transcription per video, with the video downloaded or streamed from YouTube.
   The same tab takes a Google Drive file, which drive.js shows.
   A playlist is a course: the sidebar shows it once with its lectures, and a lecture's page has the link, the
   lectures before and after it, and its course folder. A streamed video's page plays it in YouTube's own
   player (YouTubeMedia). Loaded before app.js: it only defines things, and uses app.js's helpers (S, api, $,
   esc, clock, human, mixDir, toast, prefPut) when they run. */
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

// The link being checked or added. found: what POST /api/link/inspect answered (found.site is "youtube" or
// "drive"); tried: the link last checked, found or not; picked: the ids of the videos or files to transcribe;
// captions: use the captions people made, where a YouTube video has them.
const YT = { url: "", checked: "", tried: "", busy: false, adding: false, found: null, error: "", picked: new Set(), captions: true };
// Whether the link found, or the one last checked, is Google Drive's: then YouTube's own choices aren't shown.
const ytOnDrive = () => (YT.found ? YT.found.site === "drive" : driveLink(YT.tried));

// Download each video, or only its sound and stream the video from YouTube: a page preference (app.js's PREFS),
// "download" until the user picks.
const YT_VIDEO_KEY = "sedjem-youtube-video";
const ytStream = () => { try { return localStorage.getItem(YT_VIDEO_KEY) === "stream"; } catch { return false; } };
const YT_VIDEO = [["download", "Download the video", "It plays without internet."],
  ["stream", "Stream it from YouTube", "Only the sound is downloaded. The video plays from YouTube."]];

// The icon for how a transcript was made: on this computer, on a paired one, by a hosted service, or from YouTube's captions.
const kindIcon = (j) => (j.kind === "captions" ? YT_ICON.captions : j.kind === "hosted" ? ICON.cloud : j.kind === "remote" ? ICON.network : ICON.laptop);
// The small icon in the list: none for a local recording, as before.
const listIcon = (j) => (j.kind === "captions" ? YT_ICON.captions : j.kind === "hosted" ? ICON.cloud : j.kind === "remote" ? ICON.network
  : j.youtube_id ? YT_ICON.video : j.drive_id ? DRIVE_ICON.file : "");

// ---------------------------------------------------------------------------------------------
// The new transcription page
// ---------------------------------------------------------------------------------------------
function ytCard() {
  const y = YT, drive = ytOnDrive();
  const form = `<form class="yt-form" id="ytForm">
      <input type="text" inputmode="url" id="ytUrl" value="${esc(y.url)}" placeholder="A YouTube or Google Drive link" spellcheck="false" autocomplete="off" aria-label="Link" aria-describedby="ytIntro" ${y.busy || y.adding ? "disabled" : ""}>
      <button class="btn" type="submit" ${y.busy || y.adding ? "disabled" : ""}>${y.busy ? "Checking…" : "Check"}</button></form>`;
  // a refused link's reason, right under the field; then what to paste, and the rules apart
  const error = y.error ? `<div class="yt-error" role="alert">${esc(y.error)}</div>` : "";
  const intro = `<p class="hint" id="ytIntro">A YouTube video or playlist, or a Google Drive file shared with anyone who has the link.</p>
    <p class="hint yt-rules">Download only what YouTube's terms and the file's owner allow you to.</p>`;
  // a Drive file has no streaming choice and needs no JavaScript runtime: those are YouTube's
  return `${y.found ? (drive ? driveFound(y.found) : ytFound(y.found)) : form + error + intro}
    ${drive ? "" : ytVideoChoice()}
    <div id="ytNote">${drive ? "" : ytNote()}</div>`;
}

// No JavaScript runtime: YouTube may hold back some videos. Deno can be downloaded like a model.
function ytNote() {
  const st = S.status, d = st?.deno;
  if (!st || st.youtube?.runtime) return "";
  return `<div class="yt-note">${ICON.info}<div><b>YouTube asks for a JavaScript runtime, and none was found.</b>
    Without one, some videos may not download. ${d ? "Download Deno for Sedjem, or install Deno or Node.js yourself." : "Install Deno or Node.js, then start Sedjem again."}
    ${d ? `<div class="yt-dl">${downloadBlock("deno", d)}</div>` : ""}</div></div>`;
}

// The video: downloaded (up to 720p, so it plays without internet), or streamed from YouTube when it plays.
function ytVideoChoice() {
  const on = ytStream() ? "stream" : "download";
  return `<fieldset class="yt-video"${YT.adding ? " disabled" : ""}><legend class="field-label">The video</legend>
    <div class="yt-video-opts">${YT_VIDEO.map(([v, title, hint]) => `<label class="yt-video-opt">
      <input type="radio" name="ytVideo" value="${v}" aria-describedby="ytVideoHint-${v}" ${on === v ? "checked" : ""}>
      <span><span class="yt-video-title">${title}</span><span class="hint" id="ytVideoHint-${v}">${hint}</span></span></label>`).join("")}</div></fieldset>`;
}

function ytRefreshNote() {
  const el = $("#ytNote");
  if (el) el.innerHTML = ytOnDrive() ? "" : ytNote();
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
    capsText = caps.length ? `It has captions made by people in ${esc(ytLang(caps[0].use))}, so they can be its transcript and no model needs to run. ${ytStream() ? "Its sound is still downloaded, to play if YouTube's player can't." : "The video is still downloaded, to play beside it."}`
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
  if (change) change.onclick = () => { Object.assign(YT, { found: null, error: "", tried: "", picked: new Set() }); renderNew(); $("#ytUrl")?.focus(); };
  const caps = $("#ytCaptions");
  if (caps) caps.onchange = () => { YT.captions = caps.checked; renderNew(); };
  $$("input[name=ytVideo]").forEach((r) => (r.onchange = () => {
    prefPut(YT_VIDEO_KEY, r.value);
    renderNew();
    $(`input[name=ytVideo][value=${r.value}]`)?.focus();
  }));
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
  Object.assign(YT, { busy: true, error: "", found: null, tried: url });
  renderNew();
  try {
    const found = await api.post("/api/link/inspect", { url });
    Object.assign(YT, { found, checked: url, picked: new Set(found.entries.filter((e) => e.available).map((e) => e.id)) });
  } catch (e) { YT.error = e.message; }
  YT.busy = false;
  if (S.route.name === "new") renderNew();
}

// Start: one transcription per picked video, in playlist order, or one for a Drive file. Opens the first.
async function ytStart(params) {
  const picked = ytPicked(), drive = YT.found?.site === "drive";  // (a Drive file: no captions or streaming)
  YT.adding = true;
  renderNew();
  try {
    const r = await api.post("/api/link", { ...params, url: YT.checked, entries: picked.map((e) => e.id),
      ...(drive ? {} : { captions: YT.captions, stream: ytStream() }) });
    Object.assign(YT, { url: "", checked: "", tried: "", found: null, picked: new Set(), adding: false });
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

// The courses in the list, by playlist: {id, course, main (its newest job), jobs, lectures: the transcript of
// each lecture that opens it, in playlist order: its main transcript, else its newest that isn't a branch (as
// the course files, app/courses.py)}.
function courseGroups(jobs) {
  const by = new Map();
  for (const j of jobs) {
    if (!j.course) continue;
    if (!by.has(j.course.id)) by.set(j.course.id, { id: j.course.id, course: j.course, main: j, jobs: [] });
    by.get(j.course.id).jobs.push(j);
  }
  for (const g of by.values()) {
    const pick = new Map();
    for (const j of g.jobs) {  // the list is newest first
      const had = pick.get(j.course.index);
      if (!had || (j.main && !had.main) || (!had.main && had.branch && !j.branch)) pick.set(j.course.index, j);
    }
    g.lectures = [...pick.values()].sort((a, b) => a.course.index - b.course.index);
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
// a lecture's button that hides its whole course from assistants (assistants.js). A Drive file is like an uploaded
// file, and has none.
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
    <span class="course-steps">${step(prev, "Previous", YT_ICON.prev, false)}${step(next, "Next", YT_ICON.next, true)}</span>${folder}
    <button type="button" class="btn btn-sm" id="exportCourse">${ICON.download} Export course</button>${aiCourseButton(j)}</nav>`;
}

function bindCourseBar(j) {
  const exp = $("#exportCourse");
  if (exp) exp.onclick = () => openExportMany(j.course.id);  // the Export dialog with the course ticked (exports.js)
  const open = $("#openCourse");
  if (open) open.onclick = async () => {
    try { await api.post("/api/open-folder", { job: j.id }); } catch (e) { toast(e.message, "error"); }
  };
}

// ---------------------------------------------------------------------------------------------
// A streamed video's page: YouTube's own player
// ---------------------------------------------------------------------------------------------
// A video added with "Stream it from YouTube" has only its sound on this computer, so its page plays the video in
// YouTube's embedded player, from youtube-nocookie.com, loaded when it first plays (until then nothing is asked of
// YouTube, and the panel shows a button that plays it). The page drives the player by postMessage, as YouTube's
// IFrame API does, without loading that API's script: it says "listening", then sends commands (playVideo,
// pauseVideo, seekTo, setPlaybackRate) and reads the player's onReady, infoDelivery (its time and state),
// onStateChange and onError. It reads only messages from that frame with YouTube's origin. To the bar at the
// bottom, YouTubeMedia looks like a media element (currentTime, duration, paused, playbackRate, play(), pause(), and
// the timeupdate, play, pause and loadedmetadata events), so the transcript follows it as it follows the audio. When
// the player doesn't answer within YT_WAIT, or reports an error (no internet, or a video that YouTube doesn't let
// other sites play), it fires "fail", and the page plays the downloaded sound instead (app.js).
const YT_ORIGIN = "https://www.youtube-nocookie.com";
const YT_ID = /^[A-Za-z0-9_-]{11}$/;
const YT_WAIT = 8000;
const YT_FAILED = new Set();  // jobs whose player couldn't load on this page: their sound plays instead
const YT_FALLBACK = "YouTube's player couldn't load, so the sound plays from this computer";
let ytFrames = 0;

// The video a job's page streams: its YouTube id when it was added with streaming and has no video.mp4, else "".
const ytStreamed = (r) => (r.job?.stream && !r.has_video && YT_ID.test(String(r.job.youtube_id || "")) ? r.job.youtube_id : "");
const ytEmbed = (id) => (YT_ID.test(String(id || ""))
  ? `${YT_ORIGIN}/embed/${id}?enablejsapi=1&playsinline=1&rel=0&origin=${encodeURIComponent(location.origin)}` : "");
// The note on a page whose player failed, with a way on: try YouTube's player again (app.js), or open the video on
// YouTube in the browser.
function ytFallbackNote(j) {
  if (!j.stream || !YT_FAILED.has(j.id)) return "";
  const href = ytHref(j.source_url);
  return `<div class="card yt-notice yt-fallback" id="ytFallback" role="status">${ICON.info}<span class="yt-fallback-text">${YT_FALLBACK}.</span>
    <span class="yt-fallback-actions"><button type="button" class="btn btn-sm" id="ytRetry">Try again</button>
    ${href ? `<a class="btn btn-sm" href="${esc(href)}" target="_blank" rel="noopener noreferrer">${YT_ICON.video} Open on YouTube</a>` : ""}</span></div>`;
}

class YouTubeMedia extends EventTarget {
  constructor(box) {
    super();
    this.box = box;  // where the player's frame goes: the video panel's frame (app.js)
    this.frame = null;
    this.job = "";
    this.defaultPlaybackRate = 1;
    this.timers = {};
    this.clear();
    window.addEventListener("message", (e) => this.message(e));
  }

  clear() {  // nothing loaded: paused at 0, at the speed chosen (as a media element takes its defaultPlaybackRate)
    Object.assign(this, { url: "", id: 0, ready: false, state: -1, stopped: true, base: 0, at: 0, seek: null,
                          rate: this.defaultPlaybackRate, length: NaN });
  }

  // ---- as a media element ----
  get src() { return this.url; }
  get paused() { return this.stopped; }
  get duration() { return this.length; }
  get currentTime() {  // the player's last time, moved on by the clock while it plays (as YouTube's API does)
    const t = this.base + (this.moving() ? (performance.now() - this.at) / 1000 * this.rate : 0);
    return this.length > 0 ? Math.min(t, this.length) : t;
  }
  set currentTime(t) {
    this.base = Math.max(0, Number(t) || 0);
    this.at = performance.now();
    this.seekTo();
    if (this.stopped) this.command("pauseVideo");  // YouTube's seekTo starts a video that hasn't played yet
    this.emit("timeupdate");
  }
  get playbackRate() { return this.rate; }
  set playbackRate(r) {
    this.mark();
    this.rate = Number(r) || 1;
    this.command("setPlaybackRate", [this.rate]);
  }
  play() {
    this.start();
    if (this.stopped) { this.mark(); this.stopped = false; this.emit("play"); }
    this.command("playVideo");
    return Promise.resolve();
  }
  pause() {
    if (!this.stopped) { this.mark(); this.stopped = true; this.emit("pause"); }
    this.command("pauseVideo");
    this.tick();
  }
  removeAttribute(name) {  // removeAttribute("src") unloads it, as it does a media element
    if (name !== "src") return;
    this.stop();
    this.frame?.remove();
    Object.assign(this, { frame: null, job: "" });
    this.clear();
  }
  load() {}

  // ---- the player ----
  // A job's player, not loaded yet: its frame is made when it first plays (start), so YouTube is asked only then.
  // Until then the panel shows a button that plays it. seconds: the length of the downloaded sound, until YouTube says.
  open(url, job, seconds) {
    this.removeAttribute("src");
    Object.assign(this, { url, job, length: seconds > 0 ? seconds : NaN });
    this.emit("loadedmetadata");  // the bar shows the new video's length, and its time from 0
    this.emit("timeupdate");
  }
  start() {  // the player's frame, in the panel
    if (this.frame || !this.url) return;
    this.id = ++ytFrames;
    const f = document.createElement("iframe");
    f.title = "YouTube video player";
    f.allow = "autoplay; encrypted-media; picture-in-picture; fullscreen";
    f.referrerPolicy = "strict-origin-when-cross-origin";  // YouTube refuses to play in a frame that sends no referrer
    f.addEventListener("load", () => this.listen());
    f.src = this.url;
    this.frame = f;
    this.box.append(f);
    this.wait();
  }
  unload() {  // the frame goes, and the player waits to be played again from where it was (its page was left)
    this.stop();
    this.frame?.remove();
    this.mark();
    Object.assign(this, { frame: null, ready: false, state: -1, seek: null });
    if (!this.stopped) { this.stopped = true; this.emit("pause"); }
  }
  wait() {  // fails unless the player is ready within YT_WAIT
    clearTimeout(this.timers.wait);
    this.timers.wait = setTimeout(() => this.fail(), YT_WAIT);
  }
  listen() {  // the frame loaded: say "listening" until the player answers (again if a move made it load again)
    if (this.ready) {
      this.mark();
      Object.assign(this, { ready: false, state: -1 });
      this.tick();
      this.wait();
    }
    clearInterval(this.timers.listen);
    const hello = () => this.post({ event: "listening" });
    hello();
    this.timers.listen = setInterval(hello, 250);
  }
  message(e) {
    if (!this.frame || e.source !== this.frame.contentWindow || e.origin !== YT_ORIGIN) return;
    let m = e.data;
    if (typeof m === "string") { try { m = JSON.parse(m); } catch { return; } }
    if (!m || typeof m !== "object") return;
    if (m.event === "onReady") this.onReady();
    else if (m.event === "infoDelivery" || m.event === "initialDelivery") this.info(m.info);
    else if (m.event === "onStateChange") this.info({ playerState: m.info });
    else if (m.event === "onError") this.fail();
  }
  onReady() {  // the player answered: what was asked for before it was ready is done now
    if (this.ready) return;
    this.ready = true;
    clearTimeout(this.timers.wait);
    clearInterval(this.timers.listen);
    this.command("addEventListener", ["onStateChange"]);
    this.command("addEventListener", ["onError"]);
    if (this.rate !== 1) this.command("setPlaybackRate", [this.rate]);
    if (this.base > 0) this.seekTo();
    if (!this.stopped) this.command("playVideo");
    else if (this.base > 0) this.command("pauseVideo");
  }
  seekTo() {  // until the player's time comes near it (or for a second), the times it sends from before are left out
    this.seek = { to: this.base, at: performance.now() };
    this.command("seekTo", [this.base, true]);
  }
  info(i) {  // what the player says about itself: its time, state, length and speed
    if (!i || typeof i !== "object") return;
    const now = performance.now(), d = Number(i.duration), r = Number(i.playbackRate), t = Number(i.currentTime);
    if (d > 0 && d !== this.length) { this.length = d; this.emit("loadedmetadata"); }
    if (r > 0 && r !== this.rate) { this.mark(); this.rate = r; }
    let fresh = "currentTime" in i && isFinite(t);
    if (fresh && this.seek) {
      if (Math.abs(t - this.seek.to) < 1.5 || now - this.seek.at > 1000) this.seek = null;
      else fresh = false;
    }
    if (fresh) Object.assign(this, { base: t, at: now });
    if (typeof i.playerState === "number") this.setState(i.playerState);
    if (fresh) this.emit("timeupdate");
  }
  setState(n) {  // 1 plays, 2 is paused and 0 ended; buffering (3), cued (5) and unstarted (-1) change nothing
    this.mark();
    this.state = n;
    const stopped = n === 1 ? false : n === 0 || n === 2 ? true : this.stopped;
    if (stopped !== this.stopped) { this.stopped = stopped; this.emit(stopped ? "pause" : "play"); }
    this.tick();
  }
  fail() {
    if (!this.frame) return;
    this.stop();
    this.emit("fail");
  }

  // ---- helpers ----
  moving() { return this.state === 1 && !this.stopped; }
  mark() { Object.assign(this, { base: this.currentTime, at: performance.now() }); }
  tick() {  // timeupdate four times a second while it plays, as a media element sends it
    const on = this.moving();
    if (on && !this.timers.tick) this.timers.tick = setInterval(() => this.emit("timeupdate"), 250);
    if (!on && this.timers.tick) { clearInterval(this.timers.tick); this.timers.tick = null; }
  }
  stop() {
    clearTimeout(this.timers.wait);
    clearInterval(this.timers.listen);
    clearInterval(this.timers.tick);
    this.timers = {};
  }
  command(func, args = []) {  // dropped until the player is ready, which then does what was asked (onReady)
    if (this.ready) this.post({ event: "command", func, args });
  }
  post(m) {
    try {
      this.frame?.contentWindow?.postMessage(JSON.stringify({ ...m, id: this.id, channel: "widget" }), YT_ORIGIN);
    } catch { /* the frame went away */ }
  }
  emit(type) { this.dispatchEvent(new Event(type)); }
}
