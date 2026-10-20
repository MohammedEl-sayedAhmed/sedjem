/* Sedjem — local transcription app (no build step, no external requests). */
"use strict";

// ---------------------------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------------------------
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const view = $("#view");

function clock(t) {
  t = Math.max(0, Math.floor(t || 0));
  const h = Math.floor(t / 3600), m = Math.floor(t / 60) % 60, s = t % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}` : `${m}:${String(s).padStart(2, "0")}`;
}
function human(t) {
  if (t == null || !isFinite(t)) return "";
  t = Math.round(t);
  if (t < 60) return `${t} s`;
  const m = Math.round(t / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60), r = m % 60;
  return r ? `${h} h ${r} min` : `${h} h`;
}
function bytes(n) {
  if (n == null) return "";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1000 && i < u.length - 1) { n /= 1000; i++; }
  return `${n.toFixed(i && n < 10 ? 1 : 0)} ${u[i]}`;
}
function when(iso) {
  if (!iso) return "";
  const d = new Date(iso), now = new Date();
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return `Today ${time}`;
  const y = new Date(now); y.setDate(now.getDate() - 1);
  if (d.toDateString() === y.toDateString()) return `Yesterday ${time}`;
  return d.toLocaleDateString([], { day: "numeric", month: "short", year: d.getFullYear() === now.getFullYear() ? undefined : "numeric" }) + ` ${time}`;
}
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

const ICON = {
  laptop: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="5" width="16" height="11" rx="2"/><path d="M2 19h20"/></svg>',
  cloud: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.5 19a4.5 4.5 0 0 0 .5-8.97A6 6 0 0 0 6.34 9.5 4.75 4.75 0 0 0 7 19z"/></svg>',
  upload: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>',
  wave: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 10v4M8 6v12M12 9v6M16 4v16M20 10v4"/></svg>',
  clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>',
  users: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7M21.5 20a6.5 6.5 0 0 0-4-6"/></svg>',
  cpu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4"/></svg>',
  download: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 4v12M7 11l5 5 5-5M4 20h16"/></svg>',
  copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1"/></svg>',
  redo: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5"/></svg>',
  edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
  trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14"/></svg>',
  stop: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>',
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
  up: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 15 6-6 6 6"/></svg>',
  down: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>',
  chevron: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="width:14px;height:14px"><path d="m6 9 6 6 6-6"/></svg>',
  bolt: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2 4 14h7l-1 8 9-12h-7z"/></svg>',
  layers: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/></svg>',
  gpu: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="6" width="20" height="12" rx="2"/><circle cx="9" cy="12" r="2.5"/><circle cx="16" cy="12" r="2.5"/><path d="M6 18v2M18 18v2"/></svg>',
  folder: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>',
  external: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
  lock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  link: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1"/><path d="M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1"/></svg>',
  info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
  key: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="15" r="4"/><path d="M11 12l8-8M16 7l3 3M14 9l2 2"/></svg>',
  assistant: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.5 12a8.5 8.5 0 0 1-12.4 7.6L3.5 20.5l1-4.5A8.5 8.5 0 1 1 20.5 12z"/><path d="M12 9l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z" fill="currentColor" stroke-width="1.2"/></svg>',
  update: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M12 16.5v-9M8 11.5l4-4 4 4"/></svg>',
  play: '<path d="M8 5.5v13a1 1 0 0 0 1.5.9l10-6.5a1 1 0 0 0 0-1.7l-10-6.5A1 1 0 0 0 8 5.5z"/>',
  pause: '<rect x="6" y="5" width="4" height="14" rx="1.2"/><rect x="14" y="5" width="4" height="14" rx="1.2"/>',
};

function toast(msg, kind = "") {
  const box = $("#toasts"), el = document.createElement("div");
  el.className = `toast ${kind}`;
  el.textContent = msg;
  box.append(el);
  raiseToasts(box);
  setTimeout(() => {
    el.remove();
    if (!box.children.length && box.hidePopover) try { box.hidePopover(); } catch { /* already hidden */ }
  }, kind === "error" ? 7000 : 3500);
}

// Show the toasts in the top layer (a popover), above a modal dialog and its blurred backdrop. Showing it
// again puts it above a dialog opened after it. Browsers without popovers keep it under dialogs.
function raiseToasts(box) {
  if (!box.showPopover) return;
  try {
    if (box.matches(":popover-open")) box.hidePopover();
    box.showPopover();
  } catch { /* not supported here */ }
}

// ---------------------------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------------------------
const api = {
  async req(method, path, body) {
    const opt = { method, headers: { "X-Sedjem": "1" } };
    if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    const r = await fetch(path, opt);
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.error || `${r.status} ${r.statusText}`);
    return data;
  },
  get: (p) => api.req("GET", p),
  post: (p, b = {}) => api.req("POST", p, b),
  patch: (p, b) => api.req("PATCH", p, b),
  del: (p) => api.req("DELETE", p),
  upload(file, params, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/jobs?" + new URLSearchParams(params));
      xhr.setRequestHeader("X-Sedjem", "1");
      xhr.setRequestHeader("Content-Type", file.type && file.type !== "application/json" ? file.type : "application/octet-stream");
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded, e.total);
      xhr.onload = () => {
        let data = {};
        try { data = JSON.parse(xhr.responseText); } catch (e) { /* not JSON */ }
        xhr.status < 300 ? resolve(data) : reject(new Error(data.error || `${xhr.status} ${xhr.statusText}`));
      };
      xhr.onerror = () => reject(new Error("the upload failed (connection closed)"));
      xhr.send(file);
      S.uploadXhr = xhr;
    });
  },
};

// ---------------------------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------------------------
const S = {
  status: null, jobs: [], route: { name: "new" },
  job: null, lines: [], edited: false, partial: false, hasAudio: false, hasVideo: false, log: "",
  editing: false, dirty: false, search: "", matchIndex: -1, follow: true,
  form: { file: null, path: null, seconds: null, model: null, speakers: null, language: null, prompt: "", title: "", confirm: false, source: "file" },
  uploading: null, uploadXhr: null, lastUserScroll: 0, currentLine: -1, jobPoll: null, lineSig: "",
};
const ACTIVE = new Set(["preparing", "queued", "running"]);
const canPlay = () => S.hasAudio || S.hasVideo;
const model = (id) => (S.status?.models || []).find((m) => m.id === id);
const spkColor = (sid) => (sid ? `var(--s${((parseInt(sid, 10) - 1) % 8 + 8) % 8 + 1})` : "var(--faint)");
const spkName = (sid) => (sid == null ? "" : shownNames()[sid] || `Speaker ${sid}`);
// Inside the desktop app's native window: open/save dialogs and the system browser for links.
const desktopApi = () => (window.pywebview && window.pywebview.api) || null;
// A line's direction from its mix of scripts rather than its first letter: Egyptian speech often opens
// with an English word ("order", "the project") and goes on in Arabic, which dir="auto" lays out left to
// right. Mostly Arabic words (a word with an Arabic prefix like الـdata counts as Arabic) means rtl.
const AR_LETTER = /[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]/;
const LATIN_LETTER = /[A-Za-z\u00C0-\u024F]/;
function mixDir(text) {
  let ar = 0, la = 0;
  for (const w of String(text || "").split(/\s+/)) {
    if (AR_LETTER.test(w)) ar++;
    else if (LATIN_LETTER.test(w)) la++;
  }
  if (!ar && !la) return "auto";
  return ar && ar >= 0.3 * (ar + la) ? "rtl" : "ltr";
}
const EXPORT_TYPES = { txt: ["Text", "text/plain"], srt: ["Subtitles", "application/x-subrip"], vtt: ["Web subtitles", "text/vtt"],
  md: ["Markdown", "text/markdown"], json: ["JSON", "application/json"] };
// the same file name the server suggests (server.py slug): letters, digits, _ and -, spaces to -
const fileSlug = (t) => (t || "").replace(/[^\p{L}\p{N}_\s-]/gu, "").trim().replace(/\s+/g, "-").slice(0, 80) || "transcript";
const DL_ACTIVE = new Set(["queued", "downloading", "verifying", "unpacking", "converting"]);
const downloading = () => (S.status?.models || []).some((m) => m.download && DL_ACTIVE.has(m.download.state))
  || [S.status?.voiceprints, S.status?.cuda, S.status?.deno].some((d) => d && DL_ACTIVE.has(d.state));
// "Intel(R) Iris(R) Xe Graphics (ADL GT2)" -> "Intel Iris Xe Graphics"; short: "Iris Xe"
const gpuName = (n) => (n || "").replace(/\((R|TM)\)/gi, "").replace(/\s*\([^)]*\)\s*$/, "").replace(/\s+/g, " ").trim();
const gpuShort = (n) => gpuName(n).replace(/\b(Intel|NVIDIA|AMD|Radeon\(TM\)|Graphics|Laptop|GPU)\b/gi, "").replace(/\s+/g, " ").trim() || gpuName(n);
function deviceLabel(d) {
  if (!d) return "";
  const [kind, ...rest] = String(d).split(":");
  if (!rest.length) return kind.startsWith("cpu (") ? "Processor (the graphics card failed, so it finished there)" : "Processor";
  const name = gpuName(rest.join(":").replace(/\s*\((int8_float16|float16)\)\s*$/, ""));
  return `Graphics card: ${name} (${kind === "cuda" ? "CUDA" : kind === "vulkan" ? "Vulkan" : kind})`;
}

function downloadBlock(id, d, compact = false) {
  if (!d) return "";
  if (DL_ACTIVE.has(d.state)) {
    const pct = d.total ? Math.min(100, d.done / d.total * 100) : 0;
    const what = { verifying: "Checking the download", queued: "Waiting", unpacking: "Unpacking", converting: "Converting for faster-whisper" }[d.state]
      || `Downloading ${Math.round(pct)}%`;
    return `<div class="dl"><div class="progress"><i style="width:${pct.toFixed(1)}%"></i></div>
      <div class="dl-row"><span>${what}${d.total ? ` · ${bytes(d.done)} of ${bytes(d.total)}` : ""}</span>
      <button type="button" class="linkish" data-dl-cancel="${esc(id)}">Cancel</button></div></div>`;
  }
  const left = Math.max(0, d.missing - d.partial);
  const error = d.state === "error" ? `<div class="mc-missing">${esc(d.error || "The download failed")}</div>` : "";
  if (d.installed) return compact ? "" : `<span class="pill ok">Downloaded · ${bytes(d.size)}</span>`;
  const convert = !left && model(id)?.hub?.kind === "transformers";  // downloaded, not converted yet
  return `${error}<button type="button" class="btn btn-sm btn-primary" data-dl="${esc(id)}">${ICON.download} ${convert ? "Convert" : `${d.partial ? "Resume" : "Download"} (${bytes(left)})`}</button>`;
}

async function startDownload(id) {
  try { S.status = await api.post(`/api/downloads/${id}`); renderTop(); refreshDownloadViews(); scheduleStatus(); }
  catch (e) { toast(e.message, "error"); }
}
async function cancelDownload(id) {
  try { S.status = await api.post(`/api/downloads/${id}/cancel`); refreshDownloadViews(); } catch (e) { toast(e.message, "error"); }
}
function refreshDownloadViews() {
  if (S.route.name === "new") { renderModelGrid(); ytRefreshNote(); }
  // A redraw rebuilds Settings and would wipe what is being typed there (a key, a link); the progress
  // shows at the next status check after the field is left.
  const typing = document.activeElement?.closest("#settings")
    && document.activeElement.matches("input:not([type=checkbox], [type=radio], [type=range]), textarea");
  if ($("#settings").open && !typing) renderSettings();
}

// ---------------------------------------------------------------------------------------------
// Top bar, sidebar
// ---------------------------------------------------------------------------------------------
function renderTop() {
  const st = S.status;
  if (!st) return;
  const ready = st.models.filter((m) => m.ready);
  const local = ready.filter((m) => m.kind === "local").length, hosted = ready.filter((m) => m.kind === "hosted").length;
  const pills = [`<span class="pill accent" title="${esc(ready.map((m) => m.title).join(", "))}">${ICON.laptop} ${local} local${hosted ? ` · ${ICON.cloud} ${hosted} hosted` : ""} ready</span>`];
  if (st.power === "power-saver") pills.push(`<button class="pill warn" data-open-settings="power" title="Local models run 3–5× slower in power-saver mode">${ICON.bolt} Power-saver: slower</button>`);
  else if (st.power === "performance") pills.push(`<span class="pill ok">${ICON.bolt} Performance mode</span>`);
  const gpu = st.gpu?.devices?.[0];
  if (gpu && st.settings?.device !== "cpu") pills.push(`<button class="pill ok" data-open-settings="speed" title="Local models use ${esc(gpuName(gpu.name))} where they can">${ICON.gpu} GPU: ${esc(gpuShort(gpu.name))}</button>`);
  const active = S.jobs.filter((j) => ACTIVE.has(j.status)).length;
  if (active) pills.push(`<span class="pill accent"><span class="dot pulse"></span>${active} in progress</span>`);
  if (downloading()) pills.push(`<button class="pill accent" data-open-settings="models">${ICON.download} Downloading</button>`);
  pills.push(updPill());  // a newer version is out (updates.js)
  $("#topStatus").innerHTML = pills.join("");
  $("#storageInfo").textContent = `${S.jobs.length} transcription${S.jobs.length === 1 ? "" : "s"} · ${st.storage.free_gb} GB free`;
}

function statusLabel(j) {
  switch (j.status) {
    case "done": return `<span class="s" style="color:var(--ok)">Done</span>`;
    case "failed": return `<span class="s" style="color:var(--danger)">Failed</span>`;
    case "cancelled": return `<span class="s" style="color:var(--faint)">Cancelled</span>`;
    case "interrupted": return `<span class="s" style="color:var(--warn)">Interrupted</span>`;
    case "queued": return `<span class="s" style="color:var(--muted)">Queued</span>`;
    default: return `<span class="s" style="color:var(--accent)"><span class="dot pulse" style="display:inline-block"></span></span>`;
  }
}

function jobPercent(j) {
  if (j.status === "preparing" && j.total) return Math.min(1, (j.done || 0) / j.total) * 30;  // downloading or converting
  if (j.status === "running" && j.stage === "transcribing" && j.total) return 5 + (j.done || 0) / j.total * 95;
  if (j.status === "running" && j.stage === "uploading" && j.total) return (j.done || 0) / j.total * 50;
  if (j.status === "running") return 5;
  return 0;
}

function renderSidebar() {
  const q = $("#jobSearch").value.trim().toLowerCase();
  const hit = (j) => !q || (j.title || "").toLowerCase().includes(q) || (j.model_title || "").toLowerCase().includes(q)
    || (j.course?.title || "").toLowerCase().includes(q);
  // a recording once, with its transcripts (compare.js); a course once, with its lectures (youtube.js)
  const recordings = jobGroups(S.jobs.filter((j) => !j.course)).filter((r) => r.jobs.some(hit));
  const items = [...recordings, ...courseGroups(S.jobs).filter((c) => c.jobs.some(hit)).map((c) => ({ ...c, main: c.jobs[0] }))]
    .sort((a, b) => b.main.created.localeCompare(a.main.created) || b.main.id.localeCompare(a.main.id));
  if (!items.length) {
    $("#jobList").innerHTML = `<div class="empty-list">${S.jobs.length ? "No matches" : "Your transcriptions will appear here."}</div>`;
    return;
  }
  const today = new Date().toDateString();
  let group = null;
  $("#jobList").innerHTML = items.map((r) => {
    const j = r.main;
    const g = new Date(j.created).toDateString() === today ? "Today" : "Earlier";
    const head = g !== group ? `<div class="job-group">${(group = g)}</div>` : "";
    if (r.course) return head + courseItem(r);
    if (r.jobs.length > 1) return head + groupItem(r);
    const activeCls = S.route.name === "job" && S.route.id === j.id ? " active" : "";
    const bar = ACTIVE.has(j.status) ? `<div class="bar"><i style="width:${jobPercent(j).toFixed(1)}%"></i></div>` : "";
    return `${head}<a class="job-item${activeCls}" href="#/job/${j.id}">
      <span class="t" dir="${mixDir(j.title || "Untitled")}">${esc(j.title || "Untitled")}</span>${statusLabel(j)}
      <span class="d">${listIcon(j).replace("<svg", '<svg style="width:13px;height:13px"')}${esc(j.model_title || j.model)}${j.audio_s ? ` · ${clock(j.audio_s)}` : ""}</span>
      ${bar}</a>`;
  }).join("");
  bindCourses();
  refreshGroupBar();
}

// ---------------------------------------------------------------------------------------------
// New transcription
// ---------------------------------------------------------------------------------------------
function formDefaults() {
  const d = S.status.defaults, f = S.form;
  if (!f.model) {
    const pick = model(d.model)?.ready ? d.model : (S.status.models.find((m) => m.ready) || {}).id;
    f.model = pick || d.model;
  }
  if (f.speakers == null) f.speakers = String(d.speakers ?? "auto");
  if (f.language == null) f.language = d.language || "ar";
}

// The length the model will transcribe: the recording's, or the picked YouTube videos' without captions.
const formSeconds = () => (S.form.source === "youtube" ? ytSeconds() : S.form.seconds);

function estimateText(m, seconds, short = false) {
  if (!m) return "";
  if (m.kind === "hosted") return short ? "Usually a few minutes, plus the upload" : `${esc(m.service)} usually takes a few minutes, plus the upload`;
  const sp = m.speed || { rtf: m.rtf };
  const slow = S.status.power === "power-saver" && !sp.measured;  // a measured speed already includes it
  const where = sp.runs_on === "gpu" ? " on this computer's graphics card" : " on this computer";
  if (!seconds) return `About <b>${sp.rtf}×</b> the recording's length${where}${slow ? " (more in power-saver mode)" : ""}`;
  const t = seconds * (sp.rtf || 1) * (slow ? 3.5 : 1);
  if (short) return `About <b>${human(t)}</b> on this computer${slow ? " (power-saver)" : ""}`;
  return `About <b>${human(t)}</b>${where}${sp.measured ? " (measured on earlier runs)" : ""}${slow ? " in power-saver mode. Performance mode (Settings) is about 3–5× faster" : ""}`;
}

function speakerHint(m, speakers) {
  if (speakers === "none") return "One block of text with timestamps, no names.";
  if (m?.speakers_hint) return esc(m.speakers_hint);
  if (m?.id === "speechmatics") return "Speechmatics works out the number of speakers itself; any choice here other than No labels turns labels on.";
  if (m?.id === "elevenlabs" && speakers !== "auto") return "ElevenLabs treats the number as the most speakers to find.";
  return "Labels Speaker 1, Speaker 2… by voice. Give the real number of people if you know it; auto-detect works well for larger meetings.";
}

function modelCard(m) {
  const f = S.form, sel = f.model === m.id;
  const badges = m.kind === "local"
    ? `<span class="pill accent">${ICON.laptop} On this computer</span>${m.hub ? '<span class="pill">From Hugging Face</span>' : ""}`
    : `<span class="pill cloud">${ICON.cloud} Uploads to ${esc(m.service)}</span>`;
  let state = "";
  if (!m.ready && m.kind === "hosted") state = `<span class="mc-missing">Needs an API key — <button type="button" class="linkish" data-open-settings="${esc(m.id)}">add it</button></span>`;
  else if (!m.ready && m.download) {
    const unconverted = m.hub?.kind === "transformers" && !m.download.missing;  // downloaded; the conversion is still to do
    state = `<span class="mc-missing">${unconverted ? "Downloaded, not converted yet" : "Not downloaded yet"}</span>${downloadBlock(m.id, m.download)}`;
  }
  else if (!m.ready) state = `<span class="mc-missing">${esc(m.reason)}</span>`;
  const foot = m.ready ? `<div class="mc-foot">${estimateText(m, formSeconds(), true)}</div>` : `<div class="mc-foot">${state}</div>`;
  return `<div class="model-card${m.ready ? "" : " unavailable"}" role="radio" tabindex="0" aria-checked="${sel && m.ready}" data-model="${esc(m.id)}">
    <div class="mc-badges">${badges}</div>
    <div class="mc-head">${modelTile(m)}<div class="mc-title">${esc(m.title)}</div></div>
    <div class="mc-tag">${esc(m.tagline || "")}</div>
    <ul>${(m.facts || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
    ${foot}
  </div>`;
}

function renderNew() {
  stopJobPoll();
  hidePlayer();
  formDefaults();
  const f = S.form, m = model(f.model), st = S.status;
  const src = f.file ? { name: f.file.name, size: f.file.size } : f.path;
  const speakerOpts = [["none", "No labels"], ["auto", "Auto-detect"], ["2", "2"], ["3", "3"], ["4", "4"], ["5", "5"], ["6", "6"]];
  const custom = !speakerOpts.some(([v]) => v === f.speakers);
  const hosted = m && m.kind === "hosted";
  const yt = f.source === "youtube", picked = yt ? ytPicked().length : 0;
  const ok = (yt ? picked > 0 && !YT.busy && !YT.adding : src && !S.uploading) && m && m.ready && (!hosted || f.confirm);
  const startLabel = yt ? (YT.adding ? "Adding…" : picked > 1 ? `Start ${picked} transcriptions` : "Start transcription")
    : S.uploading ? "Uploading…" : "Start transcription";
  view.innerHTML = `
  <h1>New transcription</h1>
  <p class="lead">Audio or video recordings of meetings and calls, in Egyptian Arabic with English terms, or a YouTube video or course. Local models keep everything on this computer.</p>

  <div class="card">
    <div class="step-head src-head"><span class="step-num">1</span> ${yt ? "Video or playlist" : "Recording"}
      <div class="segmented src-kind" role="group" aria-label="Where the recording comes from">
        <button type="button" data-src="file" aria-pressed="${!yt}" ${S.uploading || YT.adding ? "disabled" : ""}>${ICON.upload} A file</button>
        <button type="button" data-src="youtube" aria-pressed="${yt}" ${S.uploading || YT.adding ? "disabled" : ""}>${YT_ICON.video} YouTube link</button></div></div>
    ${yt ? ytCard() : src ? `
      <div class="chosen">
        <div class="ico">${ICON.wave}</div>
        <div class="grow">
          <div class="name" dir="auto">${esc(src.name)}</div>
          <div class="sub">${[bytes(src.size), f.seconds ? clock(f.seconds) : "", f.path ? "read in place (not copied)" : ""].filter(Boolean).join(" · ")}</div>
          ${f.file && f.previewUrl ? `<audio controls preload="metadata" src="${f.previewUrl}"></audio>` : ""}
        </div>
        <button class="btn btn-sm" id="changeFile" ${S.uploading ? "disabled" : ""}>Change</button>
      </div>` : `
      <div class="dropzone" id="drop" tabindex="0" role="button" aria-label="Choose a recording">
        ${ICON.upload}
        <div class="big">Drop a recording here</div>
        <div class="small">or <span class="linkish">choose a file</span> — mp3, m4a, wav, ogg, mp4, mkv, webm… up to ${st ? "4" : ""} GB</div>
      </div>
      <details class="path">
        <summary>Or use a file already on this computer (no upload, no copy)</summary>
        <div class="row"><input type="text" id="pathInput" placeholder="/home/you/Videos/meeting.mp4" spellcheck="false"><button class="btn" id="pathBtn">Use file</button></div>
      </details>`}
    <input type="file" id="fileInput" accept="audio/*,video/*,.mkv,.opus,.flac,.amr,.3gp" hidden>
  </div>

  <div class="card">
    <div class="step-head"><span class="step-num">2</span> Model <span class="aside">Local models are free and private; hosted ones are usually more accurate</span></div>
    <div class="model-grid" id="modelGrid" role="radiogroup" aria-label="Model">${st.models.map(modelCard).join("")}</div>
  </div>

  <div class="card">
    <div class="step-head"><span class="step-num">3</span> Options</div>
    <div class="field">
      <span class="field-label">Speakers</span>
      <div class="segmented" id="spk">${speakerOpts.map(([v, t]) => `<button type="button" data-v="${v}" aria-pressed="${f.speakers === v}">${t}</button>`).join("")}<input type="number" id="spkN" min="1" max="20" placeholder="7+" value="${custom ? esc(f.speakers) : ""}" aria-label="Number of speakers"></div>
      <p class="hint">${speakerHint(m, f.speakers)}${!st.speakers_ready && m?.kind === "local" ? " <b>The voiceprint model is not downloaded, so local models will skip speaker labels.</b>" : ""}</p>
    </div>
    <div class="grid2">
      <div class="field">
        <span class="field-label">Language</span>
        <div class="segmented" id="lang">${[["ar", "Arabic + English"], ["en", "English"], ["auto", "Auto-detect"]].map(([v, t]) => `<button type="button" data-v="${v}" aria-pressed="${f.language === v}">${t}</button>`).join("")}</div>
        <p class="hint">${m?.id === "cohere" && f.language === "auto" ? "Cohere can't detect the language, so it will use Arabic." : "Arabic + English is right for code-switched meetings."}</p>
      </div>
      ${yt ? `<div class="field"><span class="field-label">Titles</span><p class="hint">Each transcription is named after its video; you can rename it later.</p></div>` : `<div class="field">
        <label for="titleInput">Title</label>
        <input type="text" id="titleInput" dir="${mixDir(f.title)}" data-mixdir value="${esc(f.title)}" placeholder="${esc(src ? src.name.replace(/\.[^.]+$/, "") : "Meeting title")}">
      </div>`}
    </div>
    ${m?.prompt ? `<div class="field">
      <label for="promptInput">Vocabulary <span class="opt">optional</span></label>
      <textarea id="promptInput" dir="auto" rows="2" placeholder="Names and terms, comma-separated: Jira, GitHub, backend, deployment">${esc(f.prompt)}</textarea>
      <p class="hint">${esc(m.prompt_hint) || { elevenlabs: "Sent as key terms (up to 5 words each). ElevenLabs charges about 20% more for requests with key terms.",
        speechmatics: "Sent as custom vocabulary (up to 6 words per term)." }[m.id] || "Given to Whisper as a hint. Plausible but untested; leave empty if unsure."}</p>
    </div>` : ""}
  </div>

  ${hosted && m.ready ? `<div class="card consent">
    ${ICON.cloud}
    <div>
      <p><b>This model uploads ${yt ? "the sound of each video it transcribes" : "the recording"} to ${esc(m.service)}.</b> It leaves this computer and is processed on their servers under their terms. ${esc(m.privacy) || (m.id === "elevenlabs" ? "ElevenLabs may use it for training unless you opted out (Profile → Data use)." : "Speechmatics does not train on it unless you opted in; the app deletes the job there after fetching the transcript.")}</p>
      <label><input type="checkbox" id="confirmUpload" ${f.confirm ? "checked" : ""}> Upload ${yt ? "the sound" : "this recording"} to ${esc(m.service)}</label>
    </div>
  </div>` : ""}

  <div class="start-row">
    <div class="estimate">${m?.ready ? (yt && picked && !ytForModel().length ? "No model needs to run: every picked video has captions made by people" : estimateText(m, formSeconds())) : ""}</div>
    <button class="btn btn-primary btn-lg" id="startBtn" ${ok ? "" : "disabled"}>${startLabel}</button>
  </div>
  ${S.uploading ? `<div class="card" style="margin-top:14px"><div class="row" style="display:flex;justify-content:space-between;margin-bottom:8px"><b>Uploading to the app</b><span class="hint" id="uploadText" style="margin:0">${bytes(S.uploading.loaded)} of ${bytes(S.uploading.total)}</span></div><div class="progress"><i style="width:${(S.uploading.loaded / S.uploading.total * 100).toFixed(1)}%"></i></div><p class="hint">Copying the file into the app's folder on this computer — nothing is sent anywhere else yet.</p></div>` : ""}
  `;
  bindNew();
}

function renderModelGrid() {
  const grid = $("#modelGrid");
  if (!grid) return;
  grid.innerHTML = S.status.models.map(modelCard).join("");
  bindModelCards();
}

function bindModelCards() {
  const f = S.form;
  $$(".model-card").forEach((card) => {
    const pick = (e) => {
      if (e.target.closest("[data-open-settings], [data-dl], [data-dl-cancel]")) return;
      const m = model(card.dataset.model);
      if (!m.ready) { if (m.kind === "hosted") openSettings(m.id); return; }
      if (f.model !== m.id) { f.model = m.id; f.confirm = false; renderNew(); }
    };
    card.onclick = pick;
    card.onkeydown = (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), pick(e));
  });
}

async function usePath(p) {
  try {
    const info = await api.post("/api/probe", { path: p });
    Object.assign(S.form, { file: null, path: info, seconds: info.seconds, previewUrl: null });
    renderNew();
  } catch (e) { toast(e.message, "error"); }
}

async function chooseFile(input) {
  const d = desktopApi();
  if (d && d.pick_file) {  // native dialog: the file is read in place, not copied
    try { const p = await d.pick_file(); if (p) await usePath(p); } catch (e) { toast(e.message, "error"); }
    return;
  }
  input.click();
}

function setFile(file) {
  const f = S.form;
  if (f.previewUrl) URL.revokeObjectURL(f.previewUrl);
  Object.assign(f, { file, path: null, seconds: null, previewUrl: URL.createObjectURL(file) });
  const a = new Audio();
  a.preload = "metadata";
  a.onloadedmetadata = () => { if (isFinite(a.duration)) { f.seconds = a.duration; if (S.route.name === "new") renderNew(); } };
  a.src = f.previewUrl;
  renderNew();
}

function bindNew() {
  const f = S.form;
  const input = $("#fileInput");
  input.onchange = () => input.files[0] && setFile(input.files[0]);
  const drop = $("#drop");
  if (drop) {
    drop.onclick = () => chooseFile(input);
    drop.onkeydown = (e) => (e.key === "Enter" || e.key === " ") && chooseFile(input);
    drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
    drop.ondragleave = () => drop.classList.remove("over");
    drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); e.dataTransfer.files[0] && setFile(e.dataTransfer.files[0]); };
  }
  const change = $("#changeFile");
  if (change) change.onclick = () => { if (f.previewUrl) URL.revokeObjectURL(f.previewUrl); Object.assign(f, { file: null, path: null, seconds: null, previewUrl: null }); renderNew(); };
  const pathBtn = $("#pathBtn");
  if (pathBtn) {
    const use = () => { const p = $("#pathInput").value.trim(); if (p) usePath(p); };
    pathBtn.onclick = use;
    $("#pathInput").onkeydown = (e) => e.key === "Enter" && use();
  }
  bindModelCards();
  $$("#spk button").forEach((b) => (b.onclick = () => { f.speakers = b.dataset.v; renderNew(); }));
  const spkN = $("#spkN");
  spkN.oninput = () => { const n = parseInt(spkN.value, 10); if (n >= 1 && n <= 20) { f.speakers = String(n); $$("#spk button").forEach((b) => b.setAttribute("aria-pressed", "false")); } };
  $$("#lang button").forEach((b) => (b.onclick = () => { f.language = b.dataset.v; renderNew(); }));
  const title = $("#titleInput");
  if (title) title.oninput = (e) => (f.title = e.target.value);
  $$("[data-src]").forEach((b) => (b.onclick = () => {
    if (f.source === b.dataset.src || (!f.source && b.dataset.src === "file")) return;
    f.source = b.dataset.src;
    renderNew();
    if (f.source === "youtube") $("#ytUrl")?.focus();
  }));
  bindYoutube();
  const pr = $("#promptInput");
  if (pr) pr.oninput = (e) => (f.prompt = e.target.value);
  const cu = $("#confirmUpload");
  if (cu) cu.onchange = () => { f.confirm = cu.checked; renderNew(); };
  $("#startBtn").onclick = start;
}

async function start() {
  const f = S.form, m = model(f.model);
  const params = { model: m.id, speakers: f.speakers, language: f.language, prompt: m.prompt ? f.prompt : "",
    title: f.title.trim(), confirm_upload: m.kind === "hosted" && f.confirm ? "1" : "0" };
  if (f.source === "youtube") return ytStart(params);  // youtube.js
  try {
    let job;
    if (f.path) {
      job = await api.post("/api/jobs", { ...params, path: f.path.path });
    } else {
      S.uploading = { loaded: 0, total: f.file.size };
      renderNew();
      job = await api.upload(f.file, { ...params, name: f.file.name }, (loaded, total) => {
        S.uploading = { loaded, total };
        const bar = $(".progress i"), text = $("#uploadText");
        if (bar) bar.style.width = `${(loaded / total * 100).toFixed(1)}%`;
        if (text) text.textContent = `${bytes(loaded)} of ${bytes(total)}`;
      });
    }
    S.uploading = null;
    if (f.previewUrl) URL.revokeObjectURL(f.previewUrl);
    S.form = { file: null, path: null, seconds: null, model: f.model, speakers: f.speakers, language: f.language, prompt: f.prompt, title: "", confirm: false, source: f.source };
    await refreshJobs();
    location.hash = `#/job/${job.id}`;
  } catch (e) {
    S.uploading = null;
    toast(e.message, "error");
    renderNew();
  }
}

// ---------------------------------------------------------------------------------------------
// Job view
// ---------------------------------------------------------------------------------------------
const STAGES = {
  "waiting-download": "Waiting to download from YouTube",
  downloading: "Downloading from YouTube",
  waiting: "Waiting to prepare the audio",
  converting: "Preparing the audio",
  starting: "Starting",
  decoding: "Reading the audio",
  loading: "Loading the model",
  speakers: "Telling speakers apart by voice",
  transcribing: "Transcribing",
  uploading: "Uploading",
  remote: "Transcribing on the service",
};

function stageInfo(j) {
  const m = model(j.model);
  if (j.status === "queued") return { label: j.kind === "local" ? "Waiting for the previous local job" : "Waiting to start", pct: null };
  if (j.status === "preparing") {
    if (j.stage === "downloading") {  // bytes, from YouTube (app/youtube.py)
      const pct = j.total ? Math.min(100, (j.done || 0) / j.total * 100) : null;
      return { label: STAGES.downloading, pct, detail: j.total ? `${bytes(j.done || 0)} of ${bytes(j.total)}` : j.done ? bytes(j.done) : "" };
    }
    const pct = j.stage === "converting" && j.total ? (j.done || 0) / j.total * 100 : null;
    return { label: STAGES[j.stage] || "Preparing", pct, detail: pct != null ? `${Math.round(pct)}%` : "" };
  }
  let label = STAGES[j.stage] || "Working", pct = null, detail = "";
  if (j.stage === "transcribing" && j.total) {
    pct = (j.done || 0) / j.total * 100;
    detail = `part ${j.done} of ${j.total}`;
  } else if (j.stage === "uploading" && j.total) {
    pct = (j.done || 0) / j.total * 100;
    label = `Uploading to ${m?.service || "the service"}`;
    detail = `${bytes(j.done)} of ${bytes(j.total)}`;
  } else if (j.stage === "remote") {
    label = `${m?.service || "The service"} is transcribing`;
  }
  return { label, pct, detail };
}

function etaText(j) {
  if (j.status !== "running" || j.elapsed == null) return "";
  let eta = null;
  if (j.stage === "transcribing" && j.total && j.done > 0) eta = j.elapsed / j.done * (j.total - j.done);
  return `${human(j.elapsed)} elapsed${eta != null ? ` · about ${human(eta)} left` : ""}`;
}

function speakerStats() {
  const talk = {};
  let total = 0;
  for (const x of S.lines) {
    if (x.speaker == null) continue;
    const d = Math.max(0, x.end - x.start);
    talk[x.speaker] = (talk[x.speaker] || 0) + d;
    total += d;
  }
  const ids = Object.keys(talk).sort((a, b) => parseInt(a, 10) - parseInt(b, 10));
  return { ids, talk, total };
}

function renderJob() {
  const j = S.job;
  if (!j) { view.innerHTML = `<div class="welcome">Loading…</div>`; return; }
  const m = model(j.model);
  const active = ACTIVE.has(j.status);
  const opts = j.options || {};
  const stats = speakerStats();
  const meta = [
    `<span>${kindIcon(j)} ${esc(j.model_title || j.model)}</span>`,
    ytHref(j.source_url) ? `<a class="yt-link" href="${esc(ytHref(j.source_url))}" target="_blank" rel="noopener noreferrer" title="${esc(j.source_url)}">${YT_ICON.video} On YouTube</a>` : "",
    j.audio_s ? `<span>${ICON.clock} ${clock(j.audio_s)}</span>` : "",
    stats.ids.length ? `<span>${ICON.users} ${stats.ids.length} speaker${stats.ids.length === 1 ? "" : "s"}</span>` : "",
    j.seconds ? `<span>${ICON.cpu} took ${human(j.seconds)}${j.rtf ? ` (${Number(j.rtf).toFixed(2)}× real time)` : ""}</span>` : "",
    `<span>${esc(when(j.created))}</span>`,
  ].filter(Boolean).join("");
  const otherModels = (S.status?.models || []).filter((x) => x.ready);
  const exportMenu = ["txt", "srt", "vtt", "md", "json"].map((fmt) => `<a href="/api/jobs/${j.id}/export/${fmt}" data-fmt="${fmt}" download>${ICON.download} ${{ txt: "Text (.txt)", srt: "Subtitles (.srt)", vtt: "Web subtitles (.vtt)", md: "Markdown (.md)", json: "JSON (.json)" }[fmt]}</a>`).join("");
  const rerunMenu = otherModels.map((x) => `<button data-rerun="${esc(x.id)}">${x.kind === "hosted" ? ICON.cloud : ICON.laptop} ${esc(x.title)}<span class="sub">${x.id === j.model ? "same" : x.kind === "hosted" ? "uploads" : "local"}</span></button>`).join("");
  const hasLines = S.lines.length > 0;
  const slot = videoSlot();  // the video, or its progress while it is being made

  let statusCard = "";
  if (active) {
    const si = stageInfo(j);
    statusCard = `<div class="card progress-card" id="progressCard">
      <div class="row"><span class="stage" id="stageLabel">${esc(si.label)}</span><span class="hint" id="stageDetail" style="margin:0">${esc(si.detail || "")}</span><span class="times" id="stageTimes">${etaText(j)}</span></div>
      <div class="progress${si.pct == null ? " indeterminate" : ""}" id="stageBar"><i style="width:${(si.pct || 0).toFixed(1)}%"></i></div>
      <p class="note">${["downloading", "waiting-download"].includes(j.stage) ? `The video is downloaded first (up to 720p, to play beside the transcript), then ${j.course ? "transcribed; the other lectures follow in playlist order" : "transcribed"}. You can leave this page; it keeps going while the app is open.`
        : j.kind === "local" ? "Running on this computer. You can leave this page; it keeps going while the app is open." : `Sent to ${esc(m?.service || "the service")}. You can leave this page.`}</p>
      <div style="margin-top:12px"><button class="btn btn-sm btn-danger" id="cancelBtn">${ICON.stop} Cancel</button></div>
    </div>`;
  } else if (["failed", "interrupted", "cancelled"].includes(j.status)) {
    const title = { failed: "The transcription failed", interrupted: "The transcription was interrupted", cancelled: "The transcription was cancelled" }[j.status];
    statusCard = `<div class="card ${j.status === "failed" ? "error-card" : ""}">
      <div class="step-head" style="margin-bottom:6px">${esc(title)}</div>
      ${j.error ? `<div dir="auto">${esc(j.error).replace(/\n/g, "<br>")}</div>` : ""}
      ${hasLines ? `<p class="hint">The transcript below is what was finished before it stopped.</p>` : ""}
      ${S.log ? `<details style="margin-top:8px"><summary class="hint" style="cursor:pointer">Show log</summary><pre class="log">${esc(S.log)}</pre></details>` : ""}
      <div style="margin-top:12px"><button class="btn btn-sm" data-rerun="${esc(j.model)}">${ICON.redo} Try again</button></div>
    </div>`;
  }

  view.innerHTML = `
  <div class="job-head">
    <div class="title-row">
      <h1 class="job-title" id="jobTitle" contenteditable="plaintext-only" spellcheck="false" dir="${mixDir(j.title || "Untitled")}" data-mixdir title="Click to rename">${esc(j.title || "Untitled")}</h1>
      ${S.version != null ? `<button class="pill accent ver-pill" id="versionPill" title="The current version. Show the history">v${S.version}</button>` : ""}
    </div>
    <div class="meta">${meta}</div>
    ${courseBar(j)}
    ${groupBar(j)}
    <div class="actions">
      <div class="menu-wrap"><button class="btn" data-menu="exportMenu" ${hasLines ? "" : "disabled"}>${ICON.download} Export ${ICON.chevron}</button><div class="menu" id="exportMenu">${exportMenu}</div></div>
      <button class="btn" id="copyBtn" ${hasLines ? "" : "disabled"}>${ICON.copy} Copy text</button>
      <div class="menu-wrap"><button class="btn" data-menu="rerunMenu" ${S.hasAudio ? "" : "disabled"}>${ICON.redo} Run again with ${ICON.chevron}</button><div class="menu" id="rerunMenu">${rerunMenu || '<button disabled>No other model is ready</button>'}</div></div>
      <button class="btn${S.editing ? " btn-primary" : ""}" id="editBtn" ${hasLines && !active ? "" : "disabled"}>${ICON.edit} ${S.editing ? "Done editing" : "Edit"}</button>
      <button class="btn" id="historyBtn" ${S.version != null ? "" : "disabled"}>${HISTORY_ICON} History</button>
      <span class="spacer"></span>
      <button class="btn btn-ghost btn-danger" id="deleteBtn">${ICON.trash} Delete</button>
    </div>
  </div>
  ${statusCard}
  ${j.download_note ? `<div class="card yt-notice">${ICON.info}<span>${esc(j.download_note)}.</span></div>` : ""}
  ${hasLines || !active || slot ? `
  <div class="job-body${slot ? " has-video" : ""}">
    <div>
      <div class="tools">
        <div class="search-wrap">${ICON.search}<input type="search" id="lineSearch" placeholder="Search the transcript" value="${esc(S.search)}" aria-label="Search the transcript"></div>
        <span class="count" id="matchCount"></span>
        <button class="icon-btn" id="prevMatch" title="Previous match" aria-label="Previous match">${ICON.up}</button>
        <button class="icon-btn" id="nextMatch" title="Next match" aria-label="Next match">${ICON.down}</button>
        ${canPlay() ? `<label class="toggle"><input type="checkbox" id="followToggle" ${S.follow ? "checked" : ""}> Follow playback</label>` : ""}
      </div>
      <div class="transcript" id="transcript">${renderLines()}</div>
      ${S.dirty ? EDITBAR : ""}
    </div>
    <div class="side-col">
      ${slot}
      ${stats.ids.length ? `<div class="panel"><h3>Speakers</h3>${stats.ids.map((sid) => speakerRow(sid, stats)).join("")}</div>` : ""}
      ${aiPanel()}
      ${detailsPanel()}
    </div>
  </div>` : ""}`;
  placeVideo();
  bindJob();
  updateMatches();
  if (canPlay()) showPlayer(j, S.hasVideo); else hidePlayer();
}

function speakerRow(sid, stats) {
  const share = stats.total ? stats.talk[sid] / stats.total * 100 : 0;
  const others = stats.ids.filter((x) => x !== sid);
  return `<div class="spk" style="--c:${spkColor(sid)}">
    <span class="sw"></span><input type="text" dir="auto" data-name="${esc(sid)}" value="${esc(shownNames()[sid] || "")}" placeholder="Speaker ${esc(sid)}" aria-label="Name for speaker ${esc(sid)}">
    <div class="share-bar"><i style="width:${share.toFixed(1)}%"></i></div>
    <div class="share"><span>${human(stats.talk[sid])} · ${Math.round(share)}%</span>${others.length ? `<div class="menu-wrap">
      <button type="button" class="merge-btn" data-menu="merge-${esc(sid)}" aria-haspopup="menu" aria-label="Merge ${esc(spkName(sid))} into another speaker" ${ACTIVE.has(S.job.status) ? "disabled" : ""}>Merge into ${ICON.chevron}</button>
      <div class="menu merge-menu" id="merge-${esc(sid)}" role="menu">${others.map((o) => `<button type="button" role="menuitem" data-merge-from="${esc(sid)}" data-merge-into="${esc(o)}"><span class="sw" style="background:${spkColor(o)}"></span>${esc(spkName(o))}</button>`).join("")}</div>
    </div>` : ""}</div>
  </div>`;
}

// How the transcript was made (recording, model, run, computer), worded by the server (app/report.py).
// The main lines show; the rest are under More details. Older transcriptions simply have fewer lines.
// The lines that name hardware or a system get the vendors' logos (brands.js).
// The Details panel: where and how fast it ran on top, then the recording, the model and the computer,
// each with the brand's logo where there is one; every value is under "All details" and in Copy details.
function detailsPanel() {
  const groups = S.detailGroups || [];
  if (!groups.length) return "";
  const d = S.details;
  const rows = (more) => groups.map((g) => {
    const list = g.rows.filter((r) => more === null || r.more === more);
    return list.length ? `<h4>${esc(g.title)}</h4><dl class="kv">${list.map((r) => `<dt>${esc(r.label)}</dt><dd dir="${mixDir(r.value)}">${esc(r.value)}</dd>`).join("")}</dl>` : "";
  }).join("");
  const copy = `<button type="button" class="btn btn-sm" id="copyDetails">${ICON.copy} Copy details</button>`;
  if (!d || !d.run) {  // an older job: the plain list
    const more = rows(true);
    return `<div class="panel job-details"><h3>Details</h3>${rows(false)}
      ${more ? `<details class="more" id="moreDetails"${S.moreDetails ? " open" : ""}><summary>More details</summary>${more}</details>` : ""}${copy}</div>`;
  }
  const rec = d.recording || {}, mod = d.model || {}, run = d.run || {}, pc = d.computer || {};
  const value = (label) => { for (const g of groups) for (const r of g.rows) if (r.label === label) return r.value; return ""; };
  const [kind, ...rest] = String(run.device || "").split(":");
  let device, where, fallback;
  if (mod.kind === "captions") { device = "YouTube captions"; where = `Made by people${mod.captions ? `, ${mod.captions}` : ""}; no model ran`; fallback = YT_ICON.captions; }
  else if (mod.kind === "hosted") { device = mod.service || mod.title; where = "Hosted service"; fallback = ICON.cloud; }
  else if (rest.length) {
    device = gpuName(rest.join(":").replace(/\s*\((int8_float16|float16)\)\s*$/, ""));
    where = `Graphics card · ${{ vulkan: "Vulkan", cuda: "CUDA", metal: "Metal", rocm: "ROCm" }[kind] || kind}`;
    fallback = ICON.gpu;
  } else { device = gpuName(pc.cpu) || "Processor"; where = kind.startsWith("cpu (") ? "Processor (the graphics card failed)" : "Processor"; fallback = ICON.cpu; }
  const stats = [
    run.rtf != null ? [`${Number(run.rtf).toFixed(2)}×`, "real time"] : null,
    run.seconds != null ? [human(run.seconds), rec.duration ? `for ${clock(rec.duration)}` : "took"] : null,
    run.peak_memory_mb ? [bytes(run.peak_memory_mb * 1e6), "memory"] : null,
  ].filter(Boolean);
  const audio = value("Audio").split(/,\s*/).filter(Boolean);
  const recChips = [rec.extension && rec.extension.toUpperCase(), ...audio, rec.size && bytes(rec.size)].filter(Boolean);
  const quant = /(?:^|[-_.])((?:I?Q\d(?:_[A-Z0-9]+)*)|F16|BF16|F32)(?=\.gguf$)/i.exec(mod.file || "");
  const engine = { cohere: "transcribe.cpp", gguf: "transcribe.cpp", whisper: "faster-whisper", llama: "llama.cpp" }[mod.engine] || mod.service || "";
  const modChips = [engine, quant && `GGUF ${quant[1].toUpperCase()}`, mod.api_model].filter(Boolean);
  const chips = (list) => list.length ? `<div class="chips">${list.map((c) => `<span class="chip" dir="auto">${esc(c)}</span>`).join("")}</div>` : "";
  // a detail of the computer the app looked for but couldn't read says so, rather than being left out or guessed
  const recorded = !!run.computer_recorded;
  const found = (text, what, icon, sub) => text ? line(text, icon, sub)
    : recorded ? `<div class="det-row">${logoTile("", icon, true)}<div class="det-sub">${esc(what)}: not detected</div></div>` : "";
  const line = (text, icon, sub) => text ? `<div class="det-row">${logoTile(text, icon, true)}<div><div dir="${mixDir(text)}">${esc(text)}</div>${sub ? `<div class="det-sub">${esc(sub)}</div>` : ""}</div></div>` : "";
  const maker = pc.manufacturer && typeof brandFor === "function" && brandFor(pc.manufacturer)
    ? brandTitle(brandFor(pc.manufacturer)) : pc.manufacturer;  // "LENOVO" -> "Lenovo"
  const machine = [maker, pc.model].filter(Boolean).join(" ");
  const os = String(pc.os || "").replace(/\s*\(.*\)\s*$/, "");
  const said = [value("Language"), value("Speakers") && `Speakers: ${value("Speakers")}`].filter(Boolean).join(" · ");
  return `<div class="panel job-details fancy"><h3>Details</h3>
    <div class="det-hero">
      <div class="det-dev">${logoTile(device, fallback)}<div><div class="det-main" dir="${mixDir(device)}">${esc(device)}</div><div class="det-sub">${esc(where)}</div></div></div>
      ${stats.length ? `<div class="det-stats">${stats.map(([v, l]) => `<div><b>${esc(v)}</b><span>${esc(l)}</span></div>`).join("")}</div>` : ""}
    </div>
    ${rec.name ? `<section class="det-sec"><h4>${ICON.wave} Recording</h4><div class="det-main" dir="auto">${esc(rec.name)}</div>${chips(recChips)}</section>` : ""}
    <section class="det-sec"><h4>${ICON.layers} Model</h4><div class="det-row det-model">${modelTile(model(mod.id) || mod)}<div class="det-main">${esc(mod.title || S.job.model_title || "")}</div></div>${chips(modChips)}${said ? `<div class="det-sub">${esc(said)}</div>` : ""}</section>
    ${recorded || machine || pc.cpu ? `<section class="det-sec"><h4>${ICON.laptop} Computer</h4>
      ${found(machine, "Computer model", ICON.laptop, "")}${found(gpuName(pc.cpu), "Processor", ICON.cpu, pc.threads ? `${pc.threads} threads${pc.ram_gb ? ` · ${Math.round(pc.ram_gb)} GB RAM` : ""}` : "")}${found(os, "System", ICON.laptop, pc.arch || "")}</section>` : ""}
    <details class="more" id="moreDetails"${S.moreDetails ? " open" : ""}><summary>All details</summary>${rows(null)}</details>
    ${copy}</div>`;
}

// A brand's logo on a tile (brands.js), or the given icon when no brand is named in the text.
function logoTile(text, icon, small = false) {
  return brandTile(typeof brandFor === "function" ? brandFor(text) : null, icon, small);
}

// A model's logo: its maker's (NVIDIA, Qwen, Mistral, Cohere, Google…), else where it comes from: the
// hosted service's, or Hugging Face's for a model downloaded from there. A hosted service with no logo
// the app may show (Groq) gets its initial.
function modelBrand(m) {
  if (!m || typeof brandsFor !== "function") return null;
  const org = (m.hub?.repo || "").split("/")[0];
  return brandsFor([m.service, m.title, org].filter(Boolean).join(" · "))[0] || (m.kind === "hosted" ? null : "huggingface");
}
function modelTile(m, small = true) {
  if (m?.kind === "captions") return brandTile(null, YT_ICON.captions, small);  // YouTube captions: no model
  const slug = modelBrand(m);
  if (slug || m?.kind !== "hosted") return brandTile(slug, ICON.layers, small);
  const name = m.title || m.service || "?";
  return `<span class="logo-tile${small ? " small" : ""} plain mono" title="${esc(name)}" aria-hidden="true">${esc(name.trim()[0].toUpperCase())}</span>`;
}

// The brand's own logo file on a light tile (and its dark version on a dark tile in the dark theme), else
// its single-colour Simple Icons glyph on a tile tinted with its colour.
function brandTile(slug, icon, small = false) {
  const cls = `logo-tile${small ? " small" : ""}`;
  const logo = slug && typeof LOGOS !== "undefined" && LOGOS[slug];
  if (logo) {
    const t = esc(logo.title);
    const img = (file, which) => `<img class="${which}" src="${logoSrc(file)}" alt="${t}" draggable="false">`;
    return `<span class="${cls} img${logo.dark ? " has-dark" : ""}${logo.fill ? " fill" : ""}${logo.wide ? " wide" : ""}" title="${t}">${img(logo.file, "on-light")}${logo.dark ? img(logo.dark, "on-dark") : ""}</span>`;
  }
  const b = slug && typeof BRANDS !== "undefined" && BRANDS[slug];
  if (!b) return `<span class="${cls} plain">${icon}</span>`;
  const plain = Object.keys(SURFACES).filter((t) => contrast(b.hex, SURFACES[t]) < 3).map((t) => ` plain-${t}`).join("");
  return `<span class="${cls}${plain}" style="--brand:#${b.hex}" title="${esc(b.title)}"><svg viewBox="0 0 24 24" role="img" aria-label="${esc(b.title)}"><path d="${b.path}"/></svg></span>`;
}

function detailsText() {
  const groups = S.detailGroups.map((g) => [g.title, ...g.rows.map((r) => `${r.label}: ${r.value}`)].join("\n"));
  return [S.job.title || "Untitled", ...groups].join("\n\n") + "\n";
}

function highlight(text) {
  const q = S.search.trim();
  if (!q) return esc(text);
  const re = new RegExp(q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
  let out = "", last = 0;
  for (const m of text.matchAll(re)) {
    out += esc(text.slice(last, m.index)) + `<mark>${esc(m[0])}</mark>`;
    last = m.index + m[0].length;
  }
  return out + esc(text.slice(last));
}

function renderLines() {
  const j = S.job;
  if (!S.lines.length) {
    if (ACTIVE.has(j.status)) return `<div class="empty-transcript">The transcript appears here as it is made.</div>`;
    return `<div class="empty-transcript">No speech was found in this recording.</div>`;
  }
  const ids = speakerStats().ids;
  let prev = null;
  const html = S.lines.map((x, i) => {
    const first = !prev || prev.speaker !== x.speaker || x.start - prev.end > 20;
    prev = x;
    let who = "";
    if (S.editing) {
      const opts = ids.map((sid) => `<option value="${esc(sid)}" ${sid === x.speaker ? "selected" : ""}>${esc(spkName(sid))}</option>`).join("");
      who = `<div class="who"><select data-line="${i}" aria-label="Speaker"><option value="" ${x.speaker == null ? "selected" : ""}>No speaker</option>${opts}<option value="__new">New speaker…</option></select></div>`;
    } else if (first && x.speaker != null) {
      who = `<div class="who">${esc(spkName(x.speaker))}</div>`;
    }
    return `<div class="line${first ? " first" : ""}" data-i="${i}" style="--c:${spkColor(x.speaker)}">
      <button class="ts" data-t="${x.start}" title="Play from here" ${canPlay() ? "" : "disabled"}>${clock(x.start)}</button>
      ${who}<p class="text" dir="${mixDir(x.text)}" data-mixdir${S.editing ? ' contenteditable="plaintext-only"' : ""}>${S.editing ? esc(x.text) : highlight(x.text)}</p>
    </div>`;
  }).join("");
  const note = S.partial && ACTIVE.has(j.status) ? `<div class="partial-note">Transcript so far — it updates as the model works.</div>` : "";
  return html + note;
}

function bindJob() {
  const j = S.job;
  const title = $("#jobTitle");
  title.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); title.blur(); } if (e.key === "Escape") { title.textContent = drafting() ? S.draft.title : j.title; title.blur(); } };
  title.onblur = async () => {
    const t = title.textContent.trim();
    if (drafting()) return draftTitle(title, t);  // saved with the other edits, after the review
    if (t && t !== j.title) {
      try { const r = await api.patch(`/api/jobs/${j.id}`, { title: t }); S.job.title = t; S.version = r.version; showVersion(); refreshJobs(); } catch (e) { toast(e.message, "error"); }
    } else title.textContent = j.title;
  };
  $$("[data-menu]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); toggleMenu(b.dataset.menu); }));
  $$("[data-rerun]").forEach((b) => (b.onclick = () => rerun(b.dataset.rerun)));
  $$("#exportMenu a").forEach((a) => (a.onclick = (e) => saveExport(e, a)));
  const copy = $("#copyBtn");
  if (copy) copy.onclick = () => copyText();  // the text only; Copy details has the rest
  $$("#historyBtn, #versionPill").forEach((b) => (b.onclick = openHistory));
  const copyDetails = $("#copyDetails");
  if (copyDetails) copyDetails.onclick = async () => {
    try { await navigator.clipboard.writeText(detailsText()); toast("Details copied"); } catch (e) { toast("Could not copy: " + e.message, "error"); }
  };
  const moreDetails = $("#moreDetails");
  if (moreDetails) moreDetails.ontoggle = () => (S.moreDetails = moreDetails.open);  // stays open across re-renders
  const edit = $("#editBtn");
  if (edit) edit.onclick = () => (S.editing && S.dirty ? openReview() : startEditing(!S.editing));  // Done: review first
  const cancel = $("#cancelBtn");
  if (cancel) cancel.onclick = async () => {
    if (!confirm("Stop this transcription?")) return;
    try { await api.post(`/api/jobs/${j.id}/cancel`); await loadJob(j.id); } catch (e) { toast(e.message, "error"); }
  };
  $("#deleteBtn").onclick = async () => {
    if (!confirm(`Delete “${j.title}”? Its audio and transcript are removed from this computer.`)) return;
    try { await api.del(`/api/jobs/${j.id}`); toast("Deleted"); await refreshJobs(); location.hash = "#/new"; } catch (e) { toast(e.message, "error"); }
  };
  $$("[data-name]").forEach((inp) => {
    const save = async () => {
      if (drafting()) return draftName(inp);  // saved with the other edits, after the review
      const names = { ...(S.job.speaker_names || {}), [inp.dataset.name]: inp.value.trim() };
      try { const r = await api.patch(`/api/jobs/${j.id}`, { speaker_names: names }); applyJob(r); renderTranscriptOnly(); showVersion(); } catch (e) { toast(e.message, "error"); }
    };
    inp.onchange = save;
    inp.onkeydown = (e) => e.key === "Enter" && inp.blur();
  });
  $$("[data-merge-into]").forEach((b) => (b.onclick = async () => {
    const from = b.dataset.mergeFrom, into = b.dataset.mergeInto;
    closeMenus();
    if (!confirm(`Merge ${spkName(from)} into ${spkName(into)}? All their lines move over.`)) return;
    if (drafting()) return draftMerge(from, into);  // part of the edits, saved after the review
    try { const r = await api.patch(`/api/jobs/${j.id}`, { merge: { from, into } }); applyJob(r); renderJob(); toast("Speakers merged"); } catch (e) { toast(e.message, "error"); }
  }));
  const search = $("#lineSearch");
  if (search) {
    search.oninput = debounce(() => { S.search = search.value; S.matchIndex = -1; renderTranscriptOnly(); }, 150);
    search.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); stepMatch(e.shiftKey ? -1 : 1); } };
    $("#nextMatch").onclick = () => stepMatch(1);
    $("#prevMatch").onclick = () => stepMatch(-1);
  }
  const follow = $("#followToggle");
  if (follow) follow.onchange = () => (S.follow = follow.checked);
  bindCourseBar(j);  // youtube.js
  bindAi();  // assistants.js: hide it, or its course, from assistants
  bindTranscript();
  bindEditbar();
}

function bindTranscript() {
  const tr = $("#transcript");
  if (!tr) return;
  tr.onclick = (e) => {
    const ts = e.target.closest(".ts");
    if (ts && canPlay()) { media.currentTime = parseFloat(ts.dataset.t); media.play().catch(() => {}); }
  };
  if (S.editing) {
    tr.oninput = () => markDirty();
    $$("select[data-line]", tr).forEach((sel) => (sel.onchange = () => {
      if (sel.value === "__new") {
        const ids = speakerStats().ids.map((x) => parseInt(x, 10));
        const next = String((ids.length ? Math.max(...ids) : 0) + 1);
        sel.insertAdjacentHTML("beforeend", `<option value="${next}">Speaker ${next}</option>`);
        sel.value = next;
      }
      sel.closest(".line").style.setProperty("--c", spkColor(sel.value || null));
      markDirty();
    }));
  }
}

function markDirty() {
  if (S.dirty) return;
  S.dirty = true;
  const col = $("#transcript").parentElement;
  col.insertAdjacentHTML("beforeend", EDITBAR);
  bindEditbar();
}

async function rerun(modelId) {
  const m = model(modelId);
  if (!m) return;
  closeMenus();
  let confirmUpload = false;
  if (m.kind === "hosted") {
    if (!confirm(`Upload this recording to ${m.service} to transcribe it? It leaves this computer.`)) return;
    confirmUpload = true;
  }
  try {
    const r = await api.post(`/api/jobs/${S.job.id}/rerun`, { model: modelId, confirm_upload: confirmUpload });
    await refreshJobs();
    location.hash = `#/job/${r.id}`;
  } catch (e) { toast(e.message, "error"); }
}

function renderTranscriptOnly() {
  const tr = $("#transcript");
  if (!tr) return renderJob();
  tr.innerHTML = renderLines();
  S.currentLine = -1;
  bindTranscript();
  updateMatches();
  syncCurrentLine(true);
}

function updateMatches() {
  const count = $("#matchCount");
  if (!count) return;
  const marks = $$("#transcript mark");
  count.textContent = S.search.trim() ? `${marks.length} match${marks.length === 1 ? "" : "es"}` : "";
}

function stepMatch(dir) {
  const marks = $$("#transcript mark");
  if (!marks.length) return;
  marks.forEach((m) => m.classList.remove("focus"));
  S.matchIndex = (S.matchIndex + dir + marks.length) % marks.length;
  const m = marks[S.matchIndex];
  m.classList.add("focus");
  m.scrollIntoView({ block: "center", behavior: "smooth" });
  $("#matchCount").textContent = `${S.matchIndex + 1} of ${marks.length}`;
}

function applyJob(r) {
  S.job = r.job;
  S.lines = r.lines || [];
  S.edited = r.edited;
  S.partial = r.partial;
  S.hasAudio = r.has_audio;
  S.hasVideo = !!r.has_video;  // video.mp4, played instead of the audio (app/video.py)
  S.log = r.log || "";
  S.detailGroups = r.detail_groups || [];
  S.details = r.details || null;
  S.version = r.version ?? null;  // the current version in the history (app/history.py)
  S.ai = r.assistants || null;  // whether assistants may read it, and why not (assistants.js)
}

async function loadJob(id) {
  try {
    const r = await api.get(`/api/jobs/${id}`);
    if (S.route.name !== "job" || S.route.id !== id) return;
    const sameJob = S.job && S.job.id === id;
    const wasActive = sameJob && ACTIVE.has(S.job.status), wasMaking = sameJob && !!S.job.video_prep;
    const sig = `${r.lines.length}|${r.edited}|${r.job.status}|${r.has_video}|${!!r.job.video_prep}`;
    applyJob(r);
    if (sameJob && (wasActive || wasMaking) && sig === S.lineSig) {
      if (ACTIVE.has(r.job.status)) updateProgress();  // only the progress moved: keep scroll position and focus
      updateVideoPrep();
    } else if (!S.editing || !sameJob) {
      S.lineSig = sig;
      renderJob();
    }
    S.lineSig = sig;
    if (ACTIVE.has(r.job.status) || r.job.video_prep) startJobPoll(id); else stopJobPoll();
    if (wasActive && !ACTIVE.has(r.job.status)) {
      refreshJobs();
      if (r.job.status === "done") toast("Transcription finished");
    }
    if (wasMaking && r.has_video) toast("The video is ready");
  } catch (e) {
    stopJobPoll();
    view.innerHTML = `<div class="welcome"><h1>Not found</h1><p>${esc(e.message)}</p><p><a href="#/new">New transcription</a></p></div>`;
  }
}

function updateProgress() {
  const j = S.job, si = stageInfo(j);
  const label = $("#stageLabel");
  if (!label) return renderJob();
  label.textContent = si.label;
  $("#stageDetail").textContent = si.detail || "";
  $("#stageTimes").textContent = etaText(j);
  const bar = $("#stageBar");
  bar.classList.toggle("indeterminate", si.pct == null);
  bar.firstElementChild.style.width = `${(si.pct || 0).toFixed(1)}%`;
}

function startJobPoll(id) {
  if (S.jobPoll) return;
  S.jobPoll = setInterval(() => loadJob(id), 1200);
}
function stopJobPoll() {
  clearInterval(S.jobPoll);
  S.jobPoll = null;
}

// ---------------------------------------------------------------------------------------------
// Export and copy, of the current version or of one picked in History
// ---------------------------------------------------------------------------------------------
// The native window's own Save dialog, the file picker in Chrome and Edge, or else the link's download.
async function saveExport(e, a, version) {
  const j = S.job, fmt = a.dataset.fmt, d = desktopApi();
  if (d && d.save_export) {
    e.preventDefault();
    closeMenus();
    try { const saved = await d.save_export(j.id, fmt, version ?? null); if (saved) toast(`Saved ${saved}`); } catch (err) { toast(err.message, "error"); }
    return;
  }
  if (!window.showSaveFilePicker) return;
  e.preventDefault();
  closeMenus();
  let handle;
  try {  // ask first, while the click still counts as the user's action
    const [description, mime] = EXPORT_TYPES[fmt];
    handle = await window.showSaveFilePicker({ suggestedName: `${fileSlug(j.title)}${version == null ? "" : `-v${version}`}.${fmt}`, types: [{ description, accept: { [mime]: [`.${fmt}`] } }] });
  } catch (err) {
    if (err.name !== "AbortError") location.href = a.href;  // the picker isn't allowed here: download instead
    return;
  }
  try {
    const r = await fetch(a.href);
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    const out = await handle.createWritable();
    await out.write(await r.blob());
    await out.close();
    toast(`Saved ${handle.name}`);
  } catch (err) { toast(`Could not save: ${err.message}`, "error"); }
}

async function copyText(version) {
  try {
    const r = await fetch(`/api/jobs/${S.job.id}/export/txt?details=0${version == null ? "" : `&version=${version}`}`);
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    await navigator.clipboard.writeText(await r.text());
    toast(version == null ? "Transcript copied" : `Version ${version} copied`);
  } catch (e) { toast("Could not copy: " + e.message, "error"); }
}

// ---------------------------------------------------------------------------------------------
// Versions (app/history.py). Version 1 is the model's output. The edits of one session in edit mode
// are reviewed as a diff and saved as one version; quick changes outside edit mode are saved at once.
// ---------------------------------------------------------------------------------------------
const HISTORY_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 1 0 2.6-6.4L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l3 2"/></svg>';
const CLOSE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>';
const KIND_LABEL = { "model output": "Model output", edit: "Edit", rename: "Rename", merge: "Merge", restore: "Restore", combine: "Combined" };
const EDITBAR = `<div class="editbar"><span>Unsaved changes</span><button class="btn btn-sm" id="discardBtn">Discard</button><button class="btn btn-sm btn-primary" id="saveBtn">Review and save</button></div>`;
const HIST = { jid: null, versions: [], view: null, editing: null };  // the History dialog
const REVIEW = { draft: null };  // the edits being reviewed before they are saved
const latestVersion = () => (HIST.versions.length ? HIST.versions[HIST.versions.length - 1].n : -1);  // (0: the model's output)
const drafting = () => S.editing && S.draft;
const diffName = (names, sid) => (sid == null ? "No speaker" : (names || {})[sid] || `Speaker ${sid}`);

// Speaker names as shown: while editing, with the renames made in this session.
function shownNames() {
  return (drafting() ? S.draft.names : S.job?.speaker_names) || {};
}

function startEditing(on) {
  Object.assign(S, { editing: on, dirty: false, draft: on ? { names: { ...(S.job.speaker_names || {}) }, title: S.job.title } : null });
  renderJob();
}

function bindEditbar() {
  const save = $("#saveBtn");
  if (!save) return;
  save.onclick = openReview;
  $("#discardBtn").onclick = () => confirm("Discard your unsaved edits?") && startEditing(true);
}

function showVersion() {  // the "v3" next to the title, after a change that doesn't redraw the page
  const pill = $("#versionPill");
  if (pill && S.version != null) pill.textContent = `v${S.version}`;
}

// While editing, a new title, names and merges wait for the review with the edited lines.
function draftTitle(el, title) {
  if (!title) el.textContent = S.draft.title;
  else if (title !== S.draft.title) { S.draft.title = title; markDirty(); }
}
function draftName(inp) {
  const sid = inp.dataset.name, name = inp.value.trim().slice(0, 60);
  if (name) S.draft.names[sid] = name; else delete S.draft.names[sid];
  $$(`select[data-line] option[value="${sid}"]`).forEach((o) => (o.textContent = spkName(sid)));
  $$(`[data-merge-into="${sid}"]`).forEach((b) => (b.lastChild.textContent = spkName(sid)));
  markDirty();
}
function draftMerge(src, dst) {
  $$("#transcript select[data-line]").forEach((sel) => {
    if (sel.value === src) { sel.value = dst; sel.closest(".line").style.setProperty("--c", spkColor(dst)); }
  });
  markDirty();
}

function collectDraft() {
  const lines = S.lines.map((x, i) => {
    const el = $(`#transcript .line[data-i="${i}"]`);
    if (!el) return x;
    const text = el.querySelector(".text").innerText.replace(/\s+/g, " ").trim();
    const sel = el.querySelector("select[data-line]");
    return { ...x, text, speaker: sel ? sel.value || null : x.speaker };
  });
  return { lines, speaker_names: S.draft.names, title: S.draft.title };
}

function diffDialog(id, title) {  // the History and review dialogs, made on first use
  let dlg = document.getElementById(id);
  if (dlg) return dlg;
  document.body.insertAdjacentHTML("beforeend", `<dialog id="${id}" class="diff-dlg" aria-labelledby="${id}Title">
    <div class="dlg-head"><h2 id="${id}Title">${title}</h2><button class="icon-btn" data-close aria-label="Close">${CLOSE_ICON}</button></div>
    <div class="dlg-body" id="${id}Body"></div></dialog>`);
  dlg = document.getElementById(id);
  $("[data-close]", dlg).onclick = () => dlg.close();
  return dlg;
}
window.addEventListener("hashchange", () => $$("dialog.diff-dlg[open]").forEach((d) => d.close()));

// Rows like git diff: removed lines (−) in red, added lines (+) in green, and inside a changed line
// the words that differ. With only, each change keeps one line around it and the rest is counted.
function diffRows(ch, only) {
  const rows = ch.lines, out = [];
  const near = (i) => rows.slice(Math.max(0, i - 1), i + 2).some((r) => r.op !== "same");
  const words = (w, side, tag) => w.filter(([op]) => op === "=" || op === side).map(([op, t]) => (op === "=" ? esc(t) : `<${tag}>${esc(t)}</${tag}>`)).join(" ");
  const row = (kind, sign, x, who, text) => `<div class="drow d-${kind}" style="--c:${spkColor(x.speaker)}"><span class="ts">${clock(x.start)}</span><span class="sign" aria-hidden="true">${sign}</span><div class="who">${who}</div><p class="text" dir="${mixDir(x.text)}">${text}</p></div>`;
  let skipped = 0;
  const gap = () => { if (skipped) out.push(`<div class="dsep">${skipped} unchanged line${skipped === 1 ? "" : "s"}</div>`); skipped = 0; };
  rows.forEach((r, i) => {
    if (only && !near(i)) { skipped++; return; }
    gap();
    const x = r.line, before = ch.names_before, after = ch.names_after;
    if (r.op === "same") out.push(row("same", "", x, esc(diffName(after, x.speaker)), esc(x.text)));
    else if (r.op === "added") out.push(row("add", "+", x, esc(diffName(after, x.speaker)), esc(x.text)));
    else if (r.op === "removed") out.push(row("del", "−", x, esc(diffName(before, x.speaker)), esc(x.text)));
    else {  // changed: the line as it was, then as it is
      const o = r.old, moved = o.speaker !== x.speaker, a = esc(diffName(before, o.speaker)), b = esc(diffName(after, x.speaker));
      out.push(row("del", "−", o, moved ? `<del>${a}</del>` : a, r.words ? words(r.words, "-", "del") : esc(o.text)));
      out.push(row("add", "+", x, moved ? `<ins>${b}</ins>` : b, r.words ? words(r.words, "+", "ins") : esc(x.text)));
    }
  });
  gap();
  return out.join("") || `<div class="dsep">No lines</div>`;
}

// The changes between two versions (history.compare): the counts, a new title and names, the lines.
function changesHtml(ch, only) {
  const facts = [
    ch.title ? `<li>Title: <del dir="${mixDir(ch.title[0])}">${esc(ch.title[0])}</del> → <ins dir="${mixDir(ch.title[1])}">${esc(ch.title[1])}</ins></li>` : "",
    ...ch.renamed.map((x) => `<li><del dir="${mixDir(x.from)}">${esc(x.from)}</del> → <ins dir="${mixDir(x.to)}">${esc(x.to)}</ins></li>`),
  ].join("");
  const lines = ch.lines.some((x) => x.op !== "same");
  return `<div class="diff-stat">${esc(ch.stat)}</div>
    ${facts ? `<ul class="diff-facts">${facts}</ul>` : ""}
    ${lines ? `<label class="toggle diff-only"><input type="checkbox" data-diff-only ${only ? "checked" : ""}> Only the changed lines, with one line around each</label>
      <div class="diff">${diffRows(ch, only)}</div>` : `<p class="hint">No lines changed.</p>`}`;
}

function bindDiff(box, ch) {
  const only = $("[data-diff-only]", box);
  if (only) only.onchange = () => { $(".diff", box).innerHTML = diffRows(ch, only.checked); };
}

// ---- reviewing the edits of a session before they are saved -------------------------------------
function reviewDialog() {
  const dlg = diffDialog("review", "Review your changes");
  if (!$("#reviewForm")) {
    dlg.insertAdjacentHTML("beforeend", `<form class="dlg-foot review-foot" id="reviewForm">
      <input type="text" id="reviewMsg" maxlength="500" dir="auto" data-mixdir placeholder="Message (optional)" aria-label="Message for this version (optional)">
      <button type="button" class="btn" id="reviewBack">Back to editing</button>
      <button type="submit" class="btn btn-primary">Save version</button></form>`);
    $("#reviewBack").onclick = () => dlg.close();
    $("#reviewForm").onsubmit = (e) => { e.preventDefault(); saveReviewed($("#reviewMsg").value.trim()); };
  }
  return dlg;
}

async function openReview() {
  if (!drafting()) return;
  const draft = collectDraft();
  let r;
  try { r = await api.post(`/api/jobs/${S.job.id}/diff`, draft); } catch (e) { toast(e.message, "error"); return; }
  const ch = r.changes;
  if (!ch.title && !ch.renamed.length && !ch.lines.some((x) => x.op !== "same")) {
    startEditing(false);
    toast("Nothing changed, so no new version");
    return;
  }
  REVIEW.draft = draft;
  const dlg = reviewDialog();
  $("#reviewBody").innerHTML = `<p class="hint diff-base">Compared with version ${r.version}, the current one</p>${changesHtml(ch, true)}`;
  bindDiff($("#reviewBody"), ch);
  $("#reviewMsg").value = "";
  dlg.showModal();
  $("#reviewMsg").focus();
}

async function saveReviewed(message) {
  const before = S.version;
  try {
    const r = await api.patch(`/api/jobs/${S.job.id}`, { ...REVIEW.draft, message });
    $("#review").close();
    Object.assign(S, { editing: false, dirty: false, draft: null });
    applyJob(r);
    renderJob();
    refreshJobs();
    toast(r.version !== before ? `Saved as version ${r.version}` : "Nothing changed, so no new version");
  } catch (e) { toast(e.message, "error"); }
}

// ---- History ------------------------------------------------------------------------------------
async function openHistory() {
  if (!S.job || S.version == null) return;
  const dlg = diffDialog("history", "History");
  Object.assign(HIST, { jid: S.job.id, view: null, editing: null });
  $("#historyBody").innerHTML = `<p class="hint">Loading…</p>`;
  if (!dlg.open) dlg.showModal();
  await loadVersions();
}

async function loadVersions() {
  try { HIST.versions = (await api.get(`/api/jobs/${HIST.jid}/versions`)).versions; } catch (e) { toast(e.message, "error"); return; }
  HIST.view = null;
  renderVersions();
}

function kindPill(v) {
  return v.kind === "model output" ? `<span class="pill accent">Original</span>` : `<span class="pill">${esc(KIND_LABEL[v.kind] || v.kind)}</span>`;
}

function versionActions(v, withView) {
  const menu = `verExport${v.n}`;
  const links = Object.entries(EXPORT_TYPES).map(([fmt, [label]]) => `<a href="/api/jobs/${HIST.jid}/export/${fmt}?version=${v.n}" data-fmt="${fmt}" data-version="${v.n}" download>${ICON.download} ${label} (.${fmt})</a>`).join("");
  return `${withView ? `<button class="btn btn-sm" data-ver-view="${v.n}">View</button>` : ""}
    ${v.n === latestVersion() ? "" : `<button class="btn btn-sm" data-ver-restore="${v.n}">Restore</button>`}
    <div class="menu-wrap"><button class="btn btn-sm" data-menu="${menu}">${ICON.download} Export ${ICON.chevron}</button><div class="menu" id="${menu}">${links}</div></div>`;
}

function versionRow(v) {
  const current = v.n === latestVersion(), editing = HIST.editing === v.n;
  const message = editing
    ? `<form class="ver-edit" data-ver-form="${v.n}"><input type="text" maxlength="500" dir="${mixDir(v.message)}" data-mixdir value="${esc(v.message)}" placeholder="Message (optional)" aria-label="Message for version ${v.n}"><button type="submit" class="btn btn-sm btn-primary">Save</button><button type="button" class="btn btn-sm" data-ver-cancel>Cancel</button></form>`
    : v.message ? `<div class="ver-msg" dir="${mixDir(v.message)}">${esc(v.message)}</div>` : "";
  return `<div class="ver${current ? " current" : ""}" data-n="${v.n}">
    <div class="ver-head"><b class="ver-n">v${v.n}</b>${kindPill(v)}${current ? `<span class="pill ok">Current</span>` : ""}<span class="ver-time">${esc(when(v.time))}</span></div>
    ${message}
    <div class="ver-sum" dir="${mixDir(v.summary)}">${esc(v.summary || "")}</div>
    ${editing ? "" : `<div class="ver-actions">${versionActions(v, true)}<button class="btn btn-sm btn-ghost" data-ver-msg="${v.n}">${v.message ? "Edit message" : "Add message"}</button></div>`}
  </div>`;
}

function renderVersions() {
  $("#historyBody").innerHTML = `<p class="hint hist-lead">Each saved change is kept as a version. Restoring an older one saves it again as the newest version, so nothing is lost.</p>
    <div class="ver-list">${[...HIST.versions].reverse().map(versionRow).join("") || `<p class="hint">No versions yet.</p>`}</div>`;
  bindHistory();
}

async function viewVersion(n, against) {
  try { HIST.view = await api.get(`/api/jobs/${HIST.jid}/versions/${n}${against == null ? "" : `?against=${against}`}`); } catch (e) { toast(e.message, "error"); return; }
  renderVersionView();
}

// One version, read-only: its differences from the version before it, or from any other version.
function renderVersionView() {
  const r = HIST.view, v = r.version, ch = r.changes;
  const options = [...HIST.versions].reverse().filter((x) => x.n !== v.n).map((x) => `<option value="${x.n}" ${x.n === r.against ? "selected" : ""}>version ${x.n}${x.n === v.parent ? " (the one before)" : ""}</option>`);
  if (v.parent == null) options.unshift(`<option value="" ${r.against == null ? "selected" : ""}>nothing</option>`);
  const plain = { lines: r.lines.map((line) => ({ op: "same", line })), names_after: r.speaker_names };
  $("#historyBody").innerHTML = `
    <div class="ver-view-head"><button class="btn btn-sm btn-ghost" id="verBack">← All versions</button><span class="spacer"></span>
      <button class="btn btn-sm" id="verCopy">${ICON.copy} Copy text</button>${versionActions(v, false)}</div>
    <div class="ver-head"><b class="ver-title">Version ${v.n}</b>${kindPill(v)}${v.n === latestVersion() ? `<span class="pill ok">Current</span>` : ""}<span class="ver-time">${esc(when(v.time))}</span></div>
    ${v.message ? `<div class="ver-msg" dir="${mixDir(v.message)}">${esc(v.message)}</div>` : ""}
    <div class="ver-sum" dir="${mixDir(v.summary)}">${esc(v.summary || "")}</div>
    ${options.length > (v.parent == null ? 1 : 0) ? `<label class="ver-against">Compare with <select id="verAgainst">${options.join("")}</select></label>` : ""}
    ${ch ? changesHtml(ch, true) : `<div class="diff">${diffRows(plain, false)}</div>`}`;
  bindHistory();
  if (ch) bindDiff($("#historyBody"), ch);
  const against = $("#verAgainst");
  if (against) against.onchange = () => viewVersion(v.n, against.value === "" ? null : +against.value);
}

function fitMenu(id, box) {  // open upwards when the menu would run past the bottom of the dialog
  const m = document.getElementById(id);
  m.classList.remove("up");
  if (m.classList.contains("open") && m.getBoundingClientRect().bottom > box.getBoundingClientRect().bottom) m.classList.add("up");
}

function bindHistory() {
  const body = $("#historyBody");
  $$("[data-ver-view]", body).forEach((b) => (b.onclick = () => viewVersion(+b.dataset.verView)));
  $$("[data-ver-restore]", body).forEach((b) => (b.onclick = () => restoreVersion(+b.dataset.verRestore)));
  $$("[data-ver-msg]", body).forEach((b) => (b.onclick = () => { HIST.editing = +b.dataset.verMsg; renderVersions(); }));
  $$("[data-menu]", body).forEach((b) => (b.onclick = (e) => { e.stopPropagation(); toggleMenu(b.dataset.menu); fitMenu(b.dataset.menu, body); }));
  $$(".menu a[data-fmt]", body).forEach((a) => (a.onclick = (e) => saveExport(e, a, +a.dataset.version)));
  const form = $("[data-ver-form]", body);
  if (form) {
    const input = $("input", form);
    form.onsubmit = (e) => { e.preventDefault(); saveMessage(+form.dataset.verForm, input.value); };
    $("[data-ver-cancel]", form).onclick = () => { HIST.editing = null; renderVersions(); };
    input.focus();
  }
  const back = $("#verBack", body);
  if (back) back.onclick = () => { HIST.view = null; renderVersions(); };
  const copy = $("#verCopy", body);
  if (copy) copy.onclick = () => copyText(HIST.view.version.n);
}

async function saveMessage(n, message) {
  try { HIST.versions = (await api.patch(`/api/jobs/${HIST.jid}/versions/${n}`, { message: message.trim() })).versions; } catch (e) { toast(e.message, "error"); return; }
  HIST.editing = null;
  renderVersions();
  toast(message.trim() ? "Message saved" : "Message removed");
}

async function restoreVersion(n) {
  const next = latestVersion() + 1, unsaved = S.dirty ? " Your unsaved edits will be discarded." : "";
  if (!confirm(`Restore version ${n}? The transcript goes back to how it was then, saved as version ${next}. All versions stay in the history.${unsaved}`)) return;
  try {
    const r = await api.post(`/api/jobs/${HIST.jid}/versions/${n}/restore`, {});
    if (S.job && S.job.id === HIST.jid) { Object.assign(S, { editing: false, dirty: false, draft: null }); applyJob(r); renderJob(); }
    refreshJobs();
    toast(r.version >= next ? `Version ${n} restored as version ${r.version}` : "Nothing changed: the transcript already matches that version");
  } catch (e) { toast(e.message, "error"); return; }
  await loadVersions();
}

// ---------------------------------------------------------------------------------------------
// Player: the bar at the bottom plays the job's video when the recording has one (shown in the job
// page's video panel), else the hidden <audio>. Everything that follows playback uses `media`.
// ---------------------------------------------------------------------------------------------
const audio = $("#audio"), video = $("#video"), videoPanel = $("#videoPanel");
let media = audio;

function showPlayer(j, withVideo = false) {
  const el = withVideo ? video : audio, src = `/api/jobs/${j.id}/${withVideo ? "video" : "audio"}`;
  if (media !== el) {  // e.g. the video became ready while the audio played: go on from the same moment
    const was = media, same = was.src.includes(`/api/jobs/${j.id}/`), t = was.currentTime, playing = !was.paused;
    media = el;
    was.pause();
    was.removeAttribute("src");
    was.load();
    if (!el.src.endsWith(src)) el.src = src;
    el.playbackRate = el.defaultPlaybackRate = parseFloat($("#rate").value);
    if (same && t) el.currentTime = t;
    if (same && playing) el.play().catch(() => {});
    S.currentLine = -1;
    setPlayIcon();
    $("#durTime").textContent = clock(isFinite(el.duration) ? el.duration : 0);
  } else if (!el.src.endsWith(src)) { el.src = src; S.currentLine = -1; }
  $("#nowTitle").textContent = j.title || "";
  $("#player").hidden = false;
}
function hidePlayer() {
  if (!media.paused) media.pause();
  $("#player").hidden = true;
}
function setPlayIcon() {
  $("#playIcon").innerHTML = media.paused ? ICON.play : ICON.pause;
  $("#playBtn").setAttribute("aria-label", media.paused ? "Play" : "Pause");
  videoPanel.classList.toggle("paused", video.paused);
}
const playPause = () => (media.paused ? media.play().catch(() => {}) : media.pause());
const skip = (s) => (media.currentTime = Math.min(media.duration || 0, Math.max(0, media.currentTime + s)));

function lineAt(t) {
  let lo = 0, hi = S.lines.length - 1, best = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (S.lines[mid].start <= t) { best = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return best >= 0 && t <= S.lines[best].end + 1.5 ? best : -1;
}

function syncCurrentLine(force = false) {
  const i = lineAt(media.currentTime);
  if (i === S.currentLine && !force) return;
  $$("#transcript .line.current").forEach((el) => el.classList.remove("current"));
  S.currentLine = i;
  if (i < 0) return;
  const el = $(`#transcript .line[data-i="${i}"]`);
  if (!el) return;
  el.classList.add("current");
  if (S.follow && !media.paused && Date.now() - S.lastUserScroll > 4000 && !S.editing) el.scrollIntoView({ block: "center", behavior: "smooth" });
}

// the same handlers on both elements; only the one in use is heard
const onMedia = (type, fn) => [audio, video].forEach((el) => el.addEventListener(type, (e) => el === media && fn(e)));
onMedia("timeupdate", () => {
  $("#curTime").textContent = clock(media.currentTime);
  if (isFinite(media.duration) && document.activeElement !== $("#seek")) $("#seek").value = Math.round(media.currentTime / media.duration * 1000);
  syncCurrentLine();
});
onMedia("loadedmetadata", () => ($("#durTime").textContent = clock(media.duration)));
onMedia("play", setPlayIcon);
onMedia("pause", setPlayIcon);
$("#playBtn").onclick = playPause;
$("#backBtn").onclick = () => skip(-5);
$("#fwdBtn").onclick = () => skip(5);
$("#seek").oninput = (e) => { if (isFinite(media.duration)) media.currentTime = e.target.value / 1000 * media.duration; };
$("#rate").onchange = (e) => { media.playbackRate = media.defaultPlaybackRate = parseFloat(e.target.value); };

// The video panel: a click plays or pauses, a double click (or its button) fills the screen, where the
// browser's own controls show. It can be made larger (remembered) or float in picture-in-picture.
const VIDEO_ICON = {
  larger: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>',
  smaller: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7"/></svg>',
  pip: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="4" width="20" height="16" rx="2"/><rect x="12" y="12" width="7" height="5" rx="1" fill="currentColor"/></svg>',
  full: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3"/></svg>',
};
const videoLarge = () => { try { return localStorage.getItem("sedjem-video-large") === "1"; } catch { return false; } };
function setVideoLarge(on) {
  try { localStorage.setItem("sedjem-video-large", on ? "1" : "0"); } catch { /* private mode */ }
  const body = $(".job-body");
  if (body) body.classList.toggle("video-large", on);
  const b = $("#videoSize");
  b.innerHTML = on ? VIDEO_ICON.smaller : VIDEO_ICON.larger;
  b.setAttribute("aria-pressed", String(on));
  b.setAttribute("aria-label", on ? "Smaller video" : "Larger video");
  b.title = on ? "Smaller video" : "Larger video";
}
function fullScreen() {
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  else if (video.requestFullscreen) video.requestFullscreen().catch((e) => toast(`Can't fill the screen: ${e.message}`, "error"));
}
function pictureInPicture() {
  (document.pictureInPictureElement === video ? document.exitPictureInPicture() : video.requestPictureInPicture())
    .catch((e) => toast(`Can't show it in its own window: ${e.message}`, "error"));
}
$("#videoSize").onclick = () => setVideoLarge(!videoLarge());
$("#videoFull").innerHTML = VIDEO_ICON.full;
$("#videoFull").onclick = fullScreen;
$("#videoPip").innerHTML = VIDEO_ICON.pip;
$("#videoPip").hidden = !document.pictureInPictureEnabled;
$("#videoPip").onclick = pictureInPicture;
video.onclick = () => video.controls || playPause();  // (with its own controls, full screen, the browser does it)
video.ondblclick = () => video.controls || fullScreen();
document.addEventListener("fullscreenchange", () => (video.controls = document.fullscreenElement === video));
setVideoLarge(videoLarge());

// The job page's slot for the video panel or, while the video is still being made, a box of the same
// shape with its progress. The panel is one element kept across renders, so the video plays on when the
// page is drawn again (placeVideo moves it into the new slot).
function videoSlot() {
  const v = S.job?.video, prep = S.job?.video_prep;
  const ar = (v && v.width && v.height ? v.width / v.height : 16 / 9).toFixed(4);
  if (S.hasVideo) return `<div id="videoSlot" data-ar="${ar}"></div>`;
  if (!prep) return "";
  const busy = prep.done == null || !prep.total;
  return `<div class="video-panel video-prep" style="--ar:${ar}">
    <div class="video-frame"><div class="video-prep-text"><b>Preparing the video</b><span id="videoPrepText">${videoPrepText(prep)}</span>
      <div class="progress${busy ? " indeterminate" : ""}" id="videoPrepBar"><i style="width:${videoPrepPct(prep).toFixed(1)}%"></i></div></div></div></div>`;
}
const videoPrepPct = (p) => (p.done != null && p.total ? Math.min(100, p.done / p.total * 100) : 0);
const videoPrepText = (p) => `${p.done == null ? "Waiting to start" : `${Math.round(videoPrepPct(p))}%`} · the transcript doesn't wait for it`;
function placeVideo() {
  const slot = $("#videoSlot");
  if (!slot) return;
  videoPanel.style.setProperty("--ar", slot.dataset.ar);
  slot.replaceWith(videoPanel);
  videoPanel.hidden = false;
  $(".job-body").classList.toggle("video-large", videoLarge());
}
function updateVideoPrep() {
  const prep = S.job?.video_prep, text = $("#videoPrepText"), bar = $("#videoPrepBar");
  if (!prep || !text) return;
  text.textContent = videoPrepText(prep);
  bar.classList.toggle("indeterminate", prep.done == null || !prep.total);
  bar.firstElementChild.style.width = `${videoPrepPct(prep).toFixed(1)}%`;
}
["wheel", "touchmove", "keydown"].forEach((ev) => $("#main").addEventListener(ev, () => (S.lastUserScroll = Date.now()), { passive: true }));

// ---------------------------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------------------------
// Settings: one tab at a time (Models, Add models, Hosted services, Speed, Assistants, About, Updates), so nothing
// needs a long scroll; a hosted service's key form opens only for the service being edited.
const SET = { tab: "models", add: "recommended", openKey: null, details: {}, animate: null };  // details: the open row of each tab
const SET_TABS = [["models", "laptop", "Models"], ["add", "plus", "Add models"], ["hosted", "cloud", "Hosted services"],
  ["speed", "bolt", "Speed"], ["assistants", "assistant", "Assistants"], ["about", "info", "About"], ["updates", "update", "Updates"]];

function openSettings(focus) {
  if (!S.status) return;  // still starting up
  if (focus === "speed" || focus === "power") SET.tab = "speed";
  else if (focus === "assistants" || focus === "updates") SET.tab = focus;
  else if (focus && model(focus)?.kind === "hosted") { Object.assign(SET, { tab: "hosted", openKey: focus }); SET.details.hosted = null; }
  renderSettings(focus);
  $("#settings").showModal();
  if (SET.tab === "assistants") loadAssistants();  // what assistants asked since it was last open
  if (SET.tab === "updates") loadUpdates();  // updates.js
}

function setStatus(kind, text, icon = "") {
  return `<span class="status ${kind}">${icon}${esc(text)}</span>`;
}

// What a row says about its model at a glance, and what it shows when opened. Every value comes from the
// config, the catalog, the Hugging Face importer or the app's own checks (/api/status); what isn't known is
// left out of the row and said to be not stated when the row is opened.
const ENGINE_NAME = { whisper: "faster-whisper", cohere: "transcribe.cpp", gguf: "transcribe.cpp" };
const ENGINE_LONG = { whisper: "faster-whisper (CTranslate2)", cohere: "transcribe.cpp", gguf: "transcribe.cpp" };
const KIND_NAME = { gguf: "GGUF for transcribe.cpp", ct2: "faster-whisper (CTranslate2)", transformers: "Transformers, converted for faster-whisper after the download" };
const GPU_SHORT = { any: "Any graphics card", nvidia: "NVIDIA graphics only" };
const gpuKind = (m) => (m.engine === "whisper" ? "nvidia" : "any");  // as the server decides: Whisper needs CUDA, transcribe.cpp uses Vulkan
const makerOf = (m) => m.maker || (m.hub?.repo ? m.hub.repo.split("/")[0] : "") || m.service || "";
const hourly = (m) => ((m.facts || []).join(" ").match(/\$\d+(?:\.\d+)?\/h/) || [])[0];
const priceFact = (m) => (m.facts || []).find((f) => /\$\d/.test(f));

function factsLine(parts) {
  const p = parts.filter(Boolean);
  return p.length ? `<div class="set-facts" title="${esc(p.join(" · "))}">${p.map(esc).join('<i aria-hidden="true">·</i>')}</div>` : "";
}
function localFacts(m) {  // the maker of an added model is in the "from" link just above
  return [m.hub ? "" : makerOf(m), ENGINE_NAME[m.engine], m.about?.licence, GPU_SHORT[gpuKind(m)]];
}
function hostedFacts(m) {
  return [m.service, m.about?.api_model, hourly(m), (m.facts || []).slice(-1)[0]];
}

function detailsButton(tab, id, title) {
  const open = SET.details[tab] === id;
  return `<button type="button" class="btn btn-sm btn-ghost set-more-btn" data-details="${esc(id)}" data-details-tab="${tab}"
    aria-expanded="${open}" aria-controls="det-${tab}-${esc(id)}" aria-label="Details" title="${open ? "Hide" : "Show"} the details of ${esc(title)}">${ICON.chevron}</button>`;
}
// rows: [label, html] pairs (html null: not stated), or a string for a heading inside the list
function detailsBox(tab, id, title, rows) {
  const body = rows.filter(Boolean).map((r) => typeof r === "string" ? `<dt class="set-kv-head">${esc(r)}</dt>`
    : `<dt>${esc(r[0])}</dt><dd>${r[1] == null || r[1] === "" ? '<span class="unknown">Not stated</span>' : r[1]}</dd>`).join("");
  return `<div class="set-details${SET.animate === `${tab}:${id}` ? " enter" : ""}" id="det-${tab}-${esc(id)}" role="region" aria-label="Details of ${esc(title)}">
    <dl class="kv set-kv">${body}</dl></div>`;
}
const extLink = (href, text) => `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${text}</a>`;
function hfSource(repo, revision) {
  if (!repo) return null;
  const link = extLink(`https://huggingface.co/${repo}`, esc(repo));
  return revision ? `${link}<br><span class="sub">pinned to revision ${extLink(`https://huggingface.co/${repo}/tree/${revision}`, `<code>${esc(revision.slice(0, 7))}</code>`)}</span>` : link;
}
const bullets = (items) => items.length ? `<ul class="set-kv-list">${items.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : null;

// Whether the model uses the graphics card here, from the app's GPU check and the Speed settings
function gpuHere(m) {
  const st = S.status, gpu = st.gpu || {}, kind = gpuKind(m), best = (gpu.devices || [])[0];
  let here;
  if (!st.gpu && st.gpu_probing) here = "The app is still looking for a graphics card on this computer.";
  else if (st.settings?.device === "cpu") here = "On this computer it runs on the processor: the graphics card is switched off under Speed.";
  else if (m.speed?.runs_on === "gpu") here = kind === "any" && best ? `On this computer it runs on ${esc(gpuName(best.name))}.` : "On this computer it runs on the NVIDIA graphics card.";
  else if (kind === "nvidia" && gpu.cuda_devices) here = "On this computer it runs on the processor until NVIDIA's libraries are downloaded (under Speed).";
  else if (kind === "nvidia") here = "No NVIDIA graphics card was found on this computer, so it runs on the processor.";
  else here = "No graphics card that can help was found on this computer, so it runs on the processor.";
  return `${esc(GPU_USE[kind])}.<br><span class="sub">${here}</span>`;
}

// The project's own measurements, as the config's facts give them (built-in models only). The facts they
// come from are taken out of the list, so the rest can follow as "Also".
const MEASURED = [
  [/^([\d.]+%) WER on the public Egyptian set$/, "WER on Perle", (x) => `${x[1]} (Perle's 40 Egyptian clips)`],
  [/^English kept: ([\d.]+%)(?: \/ ([\d.]+%))?$/, "English kept", (x) => `${x[1]} of English terms on Perle${x[2] ? `, ${x[2]} on the meeting excerpts` : ""}`],
  [/^([\d.]+%) different from ElevenLabs on real meetings$/, "Real meetings", (x) => `${x[1]} of words differ from ElevenLabs' transcript`],
];
function measured(m, facts) {
  if (m.hub) return [];
  const rows = [];
  for (const [re, label, text] of MEASURED) {
    const i = facts.findIndex((f) => re.test(f));
    if (i >= 0) { rows.push([label, esc(text(facts[i].match(re)))]); facts.splice(i, 1); }
  }
  if (m.rtf) rows.push(["Speed", esc(`${m.rtf}× the recording's length on the test laptop's processor`
    + (m.rtf_gpu ? `, ${m.rtf_gpu}× on its integrated graphics` : ""))]);
  return rows;
}

function localDetails(m, d, cached) {
  const a = m.about || {}, facts = (m.facts || []).slice();
  const q = a.file && /\.gguf$/i.test(a.file) ? quant(a.file) : "";
  const tests = measured(m, facts);
  if (m.speed?.measured) tests.push(["Measured here", esc(`${m.speed.rtf}× the recording's length, over the last runs on this computer`)]);
  return [
    ["Made by", esc(m.hub ? `${makerOf(m)}, on Hugging Face` : m.maker)],
    ["From", hfSource(a.source?.repo, a.source?.revision)],
    m.hub?.label ? ["Kind", esc(m.hub.label)] : null,
    ["Engine", esc(ENGINE_LONG[m.engine])],
    ["File", a.file ? `<code>${esc(a.file)}</code>${q && q !== a.file ? ` <span class="sub">(${esc(q)})</span>` : ""}` : null],
    ["Download", d?.size ? esc(bytes(d.size)) : null],
    ["On disk", d?.installed ? esc(bytes(d.on_disk || d.size)) : cached ? "Uses the copy in the Hugging Face cache" : "Not downloaded"],
    ["Licence", a.licence ? esc(a.licence) : null],
    ["Graphics card", gpuHere(m)],
    "In the project's tests",
    ...(tests.length ? tests : [["Results", a.evidence ? esc(a.evidence) : null]]),
    "In words",
    m.hub ? ["Family", esc(m.tagline)] : ["In short", esc(m.tagline)],
    ["Good for", a.good_for ? esc(a.good_for) : null],
    !m.hub && facts.length ? ["Also", bullets(facts)] : null,
  ];
}

function hostedDetails(m) {
  const a = m.about || {}, facts = (m.facts || []).slice();
  const take = (f) => { const i = facts.indexOf(f); if (i >= 0) facts.splice(i, 1); return f; };
  const price = priceFact(m), oneLiner = facts.slice(-1)[0];
  const speakers = facts.find((f) => /speaker labels/i.test(f));
  const key = m.key_source === "app" ? "Saved in the app" : m.key_source === "environment" ? "Taken from the environment" : "No key yet";
  return [
    ["Provider", esc(m.service)],
    ["API model", a.api_model ? `<code>${esc(a.api_model)}</code>` : null],
    a.language ? ["Language setting", `<code>${esc(a.language)}</code>`] : null,
    ["Price", price ? esc(take(price)) : null],
    ["Privacy and training", m.privacy ? `${esc(take(oneLiner))}.<br><span class="sub">${esc(m.privacy)}</span>` : oneLiner ? esc(take(oneLiner)) : null],
    ["Speaker labels", m.speakers_hint ? esc(m.speakers_hint) : speakers ? esc(take(speakers)) : null],
    m.prompt_hint ? ["Your terms", esc(m.prompt_hint)] : null,
    ["API key", `${key}${m.key_url ? `<br><span class="sub">Get one at ${extLink(m.key_url, esc(m.key_url.replace(/^https:\/\//, "")))}</span>` : ""}`],
    m.region != null ? ["Region", m.region ? `<code>${esc(m.region)}</code>` : null] : null,
    "In words",
    ["In short", esc(m.tagline)],
    facts.length ? ["Also", bullets(facts)] : null,
  ];
}

// Opens a row's details (closing the one open before on that tab) or closes them, with a short animation
function toggleDetails(tab, id) {
  const closing = SET.details[tab] === id;
  const done = () => {
    SET.details[tab] = closing ? null : id;
    if (!closing && tab === "hosted") SET.openKey = null;  // the details take the place of the key form
    SET.animate = closing ? null : `${tab}:${id}`;
    renderSettings();
    SET.animate = null;
    $(`#settings [data-details="${CSS.escape(id)}"]`)?.focus({ preventScroll: true });
    if (!closing) $(`#det-${CSS.escape(tab)}-${CSS.escape(id)}`)?.scrollIntoView({ block: "nearest", behavior: REDUCED() ? "auto" : "smooth" });
  };
  const box = closing && $(`#det-${CSS.escape(tab)}-${CSS.escape(id)}`);
  if (box && !REDUCED()) { box.classList.add("leave"); setTimeout(done, 160); } else done();
}
const REDUCED = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

function renderSettings(focus) {
  const st = S.status;
  const hosted = st.models.filter((m) => m.kind === "hosted");
  const local = st.models.filter((m) => m.kind === "local" && m.download);
  const items = [...local.map((m) => ({ id: m.id, title: m.title, d: m.download, hub: m.hub, m })),
    ...(st.voiceprints ? [{ id: "voiceprints", title: "Voiceprint model", d: st.voiceprints, note: "Tells the speakers apart by voice" }] : []),
    ...(st.deno ? [{ id: "deno", title: "Deno", d: st.deno, icon: YT_ICON.video,
      note: st.youtube?.runtime && !st.deno.installed ? `For YouTube links; ${st.youtube.runtime.name} is used for now` : "Answers YouTube's check, for YouTube links" }] : [])];
  const readyLocal = items.filter((x) => x.d.installed || model(x.id)?.ready).length;
  const keys = hosted.filter((m) => m.key_source).length;
  const counts = { models: `${readyLocal} of ${items.length}`, hosted: `${keys} of ${hosted.length}`, updates: st.update?.available ? "New" : "" };
  if (!SET_TABS.some(([t]) => t === SET.tab)) SET.tab = "models";

  const nav = `<nav class="set-nav" role="tablist" aria-label="Settings sections">${SET_TABS.map(([t, icon, label]) =>
    `<button type="button" role="tab" id="tab-${t}" data-set-tab="${t}" aria-selected="${SET.tab === t}" aria-controls="set-panel" tabindex="${SET.tab === t ? 0 : -1}">
      ${ICON[icon]}<span>${label}</span>${counts[t] ? `<small>${counts[t]}</small>` : ""}</button>`).join("")}</nav>`;
  const panel = { models: modelsPanel, add: addPanel, hosted: hostedPanel, speed: speedPanel, assistants: assistantsPanel,
    about: aboutPanel, updates: updatesPanel }[SET.tab](items, hosted);
  // Redrawing the same tab (download progress every 1.5 s, a row opened) keeps the panel where it was scrolled,
  // the row of tabs on a narrow screen too, and the focus on a tab or switch drawn again
  const old = $("#set-panel"), top = old?.getAttribute("aria-labelledby") === `tab-${SET.tab}` ? old.scrollTop : 0;
  const left = $("#settings .set-nav")?.scrollLeft || 0, was = document.activeElement;
  const again = was?.closest?.("#settingsBody") ? (was.id ? `#${CSS.escape(was.id)}` : was.dataset.setting ? `[data-setting="${was.dataset.setting}"]` : "") : "";
  $("#settingsBody").innerHTML = `<div class="set-layout">${nav}
    <div class="set-panel" id="set-panel" role="tabpanel" aria-labelledby="tab-${SET.tab}">${panel}</div></div>`;
  if (top) $("#set-panel").scrollTop = top;
  if (left) $("#settings .set-nav").scrollLeft = left;
  if (again) $(`#settingsBody ${again}`)?.focus({ preventScroll: true });
  bindSettings(focus);
}

// Models on this computer: one row each, with a status that says what is there, a line of key facts, and
// everything known about the model when the row is opened
function modelsPanel(items) {
  const rows = items.map(({ id, title, d, hub, m, note, icon }) => {
    const cached = !d.installed && model(id)?.ready;  // found elsewhere, e.g. the Hugging Face cache
    const busy = DL_ACTIVE.has(d.state);
    let status, sub, actions = "";
    if (busy) { status = ""; sub = "Downloading"; }
    else if (d.installed) {
      status = setStatus("ok", "Downloaded", ICON.check);
      sub = `${bytes(d.on_disk || d.size)} on this computer`;
      actions = `<button type="button" class="btn btn-sm btn-ghost btn-danger" data-dl-remove="${esc(id)}">Delete</button>`;
    } else if (cached) {
      status = setStatus("shared", "Shared copy", ICON.link);
      sub = "Uses the copy already in the Hugging Face cache, so nothing is downloaded";
    } else {
      status = d.state === "error" ? setStatus("error", "Download failed") : setStatus("none", "Not downloaded");
      sub = `${bytes(d.size)} to download`;
      actions = downloadBlock(id, d, true);
    }
    if (note) sub = `${note} · ${sub}`;
    const from = hub ? ` · from <a href="https://huggingface.co/${esc(hub.repo)}" target="_blank" rel="noopener noreferrer">${esc(hub.repo)}</a>${hub.label ? ` · ${esc(hub.label)}` : ""}` : "";
    const forget = hub && !busy ? `<button type="button" class="btn btn-sm btn-ghost" data-forget-model="${esc(id)}">Remove from the app</button>` : "";
    const open = m && SET.details.models === id;
    return `<div class="set-item${open ? " expanded" : ""}">
      <div class="set-item-row"${m ? ` data-row="${esc(id)}"` : ""}>${m ? modelTile(m) : brandTile(null, icon || ICON.users, true)}
        <div class="set-item-text"><b>${esc(title)}</b><span>${esc(sub)}${from}</span></div>
        ${status}<div class="set-item-actions">${actions}${forget}${m ? detailsButton("models", id, title) : ""}</div>
        ${m ? factsLine(localFacts(m)) : ""}</div>
      ${busy ? `<div class="set-item-more">${downloadBlock(id, d, true)}</div>` : ""}
      ${open ? detailsBox("models", id, title, localDetails(m, d, cached)) : ""}</div>`;
  }).join("");
  return `<h3>Models on this computer</h3>
    <div class="set-items">${rows || '<p class="hint">No downloadable models are configured.</p>'}</div>
    <p class="hint">Downloads resume if the connection drops and are checked against a pinned checksum before use.
      <button type="button" class="linkish" data-set-tab="add">Find more models</button></p>`;
}

function addPanel() {
  const seg = `<div class="segmented" role="group" aria-label="Where from">${[["recommended", "Recommended"], ["hub", "From Hugging Face"]].map(([k, l]) =>
    `<button type="button" data-set-add="${k}" aria-pressed="${SET.add === k}">${l}</button>`).join("")}</div>`;
  return `<div class="set-head"><h3>Add models</h3>${seg}</div>
    ${SET.add === "hub" ? hubBlock() : catalogBlock() || '<p class="hint">No recommended models are listed.</p>'}`;
}

function hostedPanel(items, hosted) {
  const rows = hosted.map((m) => {
    const open = SET.openKey === m.id;
    const status = m.key_source === "environment" ? setStatus("shared", "From the environment", ICON.check)
      : m.key_source === "app" ? setStatus("ok", "Key saved", ICON.check) : setStatus("none", "No key");
    const toggle = `<button type="button" class="btn btn-sm" data-key-toggle="${esc(m.id)}" aria-expanded="${open}">${open ? "Cancel" : m.key_source ? "Change" : `${ICON.key} Add key`}</button>`;
    const form = open ? `<div class="set-item-more">
      <form class="row" data-key-form="${esc(m.id)}"><input type="text" name="username" value="${esc(m.id)}" autocomplete="off" hidden>
        <input type="password" data-key="${esc(m.id)}" placeholder="${m.key_source ? "Replace the key" : "Paste your API key"}" aria-label="${esc(m.service)} API key" autocomplete="new-password" spellcheck="false">
        ${m.region != null ? `<input type="text" data-region value="${esc(m.region)}" placeholder="Region, e.g. westeurope" aria-label="${esc(m.service)} region" title="The region of your resource: the key works only there" autocomplete="off" spellcheck="false" class="region">` : ""}
        <button class="btn btn-primary" type="submit">Save</button>
        ${m.key_source === "app" ? `<button class="btn btn-ghost btn-danger" type="button" data-clear-key="${esc(m.id)}">Remove</button>` : ""}</form>
      ${m.key_url ? `<p class="hint">Get a key: <a href="${esc(m.key_url)}" target="_blank" rel="noopener noreferrer">${esc(m.key_url.replace(/^https:\/\//, ""))}</a></p>` : ""}
    </div>` : "";
    const expanded = SET.details.hosted === m.id;
    return `<div class="set-item${open ? " open" : ""}${expanded ? " expanded" : ""}" id="key-${esc(m.id)}">
      <div class="set-item-row" data-row="${esc(m.id)}">${modelTile(m)}
        <div class="set-item-text"><b>${esc(m.title)}</b><span>${esc(m.tagline || m.service || "")}</span></div>
        ${status}<div class="set-item-actions">${toggle}${detailsButton("hosted", m.id, m.title)}</div>
        ${factsLine(hostedFacts(m))}</div>${form}
      ${expanded ? detailsBox("hosted", m.id, m.title, hostedDetails(m)) : ""}</div>`;
  }).join("");
  return `<h3>Hosted services</h3>
    <p class="hint">More accurate than the local models on hard recordings, with your own API key. A recording is uploaded only when you pick one of these and confirm.</p>
    <div class="set-items">${rows}</div>
    <p class="hint">${ICON.lock.replace("<svg", '<svg style="width:13px;height:13px;vertical-align:-2px"')} Keys are saved in <code class="mono">${esc(S.status.storage.dir)}/secrets.json</code>, readable only by your user, and are sent only to their own service.</p>`;
}

function speedPanel() {
  const st = S.status, set = st.settings || {}, gpu = st.gpu || {};
  const devices = gpu.devices || [];
  const best = devices[0];
  const nvidia = (gpu.cuda_devices || 0) > 0;
  const cudaReady = !!gpu.cuda_libs || !!st.cuda?.installed;
  let gpuText;
  if (!st.gpu && st.gpu_probing) gpuText = "Looking for a graphics card…";
  else if (gpu.error) gpuText = `Couldn't check the graphics card: ${esc(gpu.error)}`;
  else if (!devices.length && !nvidia) gpuText = "No graphics card that can help was found, so everything runs on the processor.";
  else if (set.device === "cpu") gpuText = `Off: everything runs on the processor. Found: ${devices.map((d) => esc(gpuName(d.name))).join(", ")}.`;
  else {
    const mem = (d) => d.type === "igpu" ? "shared memory" : d.memory ? `${Math.round(d.memory / 2 ** 30)} GB` : "";
    gpuText = `${devices.map((d) => `<b>${esc(gpuName(d.name))}</b>${mem(d) ? ` (${mem(d)})` : ""}`).join(", ")}<br>`
      + (best ? `Cohere runs on ${esc(gpuName(best.name))} through Vulkan. ` : "")
      + (nvidia ? (cudaReady ? "Whisper models run on the NVIDIA GPU through CUDA." : "Whisper models can run on the NVIDIA GPU once NVIDIA's libraries are downloaded:")
        : "Whisper models run on the processor (they can only use NVIDIA GPUs).");
  }
  const cudaRow = nvidia && st.cuda && set.device !== "cpu" && !cudaReady
    ? `<div class="set-sub">${downloadBlock("cuda", st.cuda)}<span class="hint" style="margin:0">cuBLAS from NVIDIA, ${bytes(st.cuda.on_disk)} on disk</span></div>` : "";
  const p = st.power;
  const powerRows = p ? `
      <div class="set-row">
        <div class="set-text"><b>Power mode</b><span class="hint">Local models run about 3–5× slower in power-saver mode.</span></div>
        <div class="seg">${["performance", "balanced", "power-saver"].map((x) => `<button type="button" class="${p === x ? "on" : ""}" data-power="${x}">${x === "performance" ? ICON.bolt + " " : ""}${x[0].toUpperCase() + x.slice(1)}</button>`).join("")}</div>
      </div>
      <label class="set-row">
        <div class="set-text"><b>Performance mode while transcribing</b><span class="hint">Switch to performance while a local model runs, and back to ${esc(p === "performance" ? "the previous mode" : p)} when it's done.</span></div>
        <input type="checkbox" class="switch" data-setting="performance_while_running" ${set.performance_while_running ? "checked" : ""}>
      </label>` : "";
  const threads = set.threads || 10, cores = st.cpu_threads || threads;
  return `<section id="set-speed">
      <h3>Speed</h3>
      <div class="set-list">
        <label class="set-row">
          <div class="set-text"><b>${ICON.gpu} Use the graphics card</b><span class="hint">${gpuText}</span></div>
          <input type="checkbox" class="switch" data-setting="device" ${set.device !== "cpu" ? "checked" : ""} ${!devices.length && !nvidia ? "disabled" : ""}>
        </label>
        ${cudaRow}
        ${powerRows}
        <div class="set-row">
          <div class="set-text"><b>Processor threads</b><span class="hint">How many of this computer's ${cores} threads the local models use.</span></div>
          <div class="num"><input type="number" min="1" max="${cores}" value="${Math.min(threads, cores)}" data-setting="threads" aria-label="Processor threads"><span>of ${cores}</span></div>
        </div>
      </div>
    </section>`;
}

function aboutPanel() {
  const st = S.status;
  return `<h3>About</h3>
      <div class="about">
        <img src="/static/icon.svg" alt="" width="44" height="44">
        <div class="about-text">
          <b>Sedjem ${esc(st.version || "")}</b>
          <span>Transcripts of Egyptian Arabic–English meetings, made on your own computer.</span>
          <span>By Mohammed El-sayed Ahmed. Free software under the AGPL-3.0; commercial licences are available.</span>
          <span class="links"><a href="https://github.com/MohammedEl-sayedAhmed/sedjem" target="_blank" rel="noopener noreferrer">${ICON.external} Source code</a>
            <a href="https://github.com/MohammedEl-sayedAhmed/sedjem/releases" target="_blank" rel="noopener noreferrer">${ICON.external} Releases</a>
            <a href="https://github.com/MohammedEl-sayedAhmed/sedjem/blob/main/LICENSE" target="_blank" rel="noopener noreferrer">${ICON.external} Licence</a></span>
        </div>
      </div>
      <div class="set-list">
        <div class="set-row">
          <div class="set-text"><b>Your data</b><span class="hint">${st.storage.jobs} transcription${st.storage.jobs === 1 ? "" : "s"} · ${bytes(st.storage.used_mb * 1e6)} · ${st.storage.free_gb} GB free on this disk</span>
            <code class="mono path">${esc(st.storage.dir)}</code></div>
          <button type="button" class="btn btn-sm" id="openFolder">${ICON.folder} Open folder</button>
        </div>
      </div>
      <p class="hint">Models are kept in <code class="mono">${esc(st.home)}/models</code>. The transcripts of YouTube videos and courses are saved as files in <code class="mono">${esc(st.youtube?.courses || "")}</code>. The port, defaults and model list are set in <code class="mono">app/config.toml</code>; your own changes can go in <code class="mono">${esc(st.storage.dir)}/config.toml</code>.</p>`;
}

function bindSettings(focus) {
  const threads = S.status.settings?.threads || 10, cores = S.status.cpu_threads || threads;
  $$("[data-set-tab]").forEach((b) => (b.onclick = () => {
    SET.tab = b.dataset.setTab;
    renderSettings();
    $(`#tab-${SET.tab}`)?.focus();
    if (SET.tab === "assistants") loadAssistants();
    if (SET.tab === "updates") loadUpdates();
  }));
  const tabs = $$(".set-nav [role=tab]");
  tabs.forEach((b, i) => (b.onkeydown = (e) => {
    const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
    if (!step) return;
    e.preventDefault();
    tabs[(i + step + tabs.length) % tabs.length].click();
  }));
  $$("[data-set-add]").forEach((b) => (b.onclick = () => { SET.add = b.dataset.setAdd; renderSettings(); }));
  $$("[data-details]").forEach((b) => (b.onclick = () => toggleDetails(b.dataset.detailsTab, b.dataset.details)));
  $$("#settings [data-row]").forEach((row) => (row.onclick = (e) => {  // a click anywhere on the row but its controls
    if (e.target.closest("button, a, input, select, label, form, .dl") || getSelection().toString()) return;
    row.querySelector("[data-details]")?.click();
  }));
  $$("[data-key-toggle]").forEach((b) => (b.onclick = () => {
    SET.openKey = SET.openKey === b.dataset.keyToggle ? null : b.dataset.keyToggle;
    if (SET.openKey) SET.details.hosted = null;  // the key form takes the place of the details
    renderSettings();
    if (SET.openKey) $(`[data-key="${SET.openKey}"]`)?.focus();
  }));
  $$("[data-key-form]").forEach((form) => (form.onsubmit = (e) => { e.preventDefault(); saveKey(form.dataset.keyForm, $("[data-key]", form).value, $("[data-region]", form)?.value); }));
  $$("[data-clear-key]").forEach((b) => (b.onclick = () => confirm("Remove the saved key?") && saveKey(b.dataset.clearKey, "")));
  $$("[data-power]").forEach((b) => (b.onclick = async () => {
    try { await api.post("/api/power", { profile: b.dataset.power }); await refreshStatus(); renderSettings(); toast(`Power mode: ${b.dataset.power}`); } catch (e) { toast(e.message, "error"); }
  }));
  $$("input[data-setting]").forEach((el) => (el.onchange = async () => {
    const key = el.dataset.setting;
    const value = key === "device" ? (el.checked ? "auto" : "cpu") : key === "threads" ? parseInt(el.value, 10) : el.checked;
    if (key === "threads" && !(value >= 1 && value <= cores)) { el.value = threads; return; }
    try { S.status = await api.post("/api/settings", { [key]: value }); renderTop(); renderSettings(); if (S.route.name === "new") renderModelGrid(); toast("Saved"); }
    catch (e) { toast(e.message, "error"); }
  }));
  const open = $("#openFolder");
  if (open) open.onclick = async () => { try { await api.post("/api/open-folder"); } catch (e) { toast(e.message, "error"); } };
  bindHub();
  bindAssistants();  // assistants.js
  bindUpdates();  // updates.js
  $$("[data-forget-model]").forEach((b) => (b.onclick = () => forgetModel(b.dataset.forgetModel)));
  if (focus && focus !== "speed" && focus !== "power") { const el = $(`[data-key="${focus}"]`); if (el) setTimeout(() => el.focus(), 50); }
}

// Adding models from Hugging Face (Settings → Models). The state lives here, not in the dialog, which is
// rebuilt on every status poll while a download runs; the caret in the link field is put back too.
const HUB = { url: "", busy: false, found: null, error: "", caret: null, pick: {} };
const GPU_USE = { any: "Can use any graphics card (Vulkan)", nvidia: "Can use NVIDIA graphics cards only (CUDA)" };
const quant = (file) => (String(file).match(/[-_.]((?:I?Q\d\w*?)|BF16|F16|F32)\.gguf$/i) || [null, file])[1];

// Recommended models (app/catalog.toml): built in, added, or added here in one click through the importer.
function catalogBlock() {
  const list = S.status?.catalog || [];
  if (!list.length) return "";
  return `<div id="catalog"><p class="hint" style="margin-top:0">Chosen for Egyptian Arabic–English meetings and calls. Added ones download like the built-in models.</p>
    <div class="set-items">${list.map(catalogRow).join("")}</div></div>`;
}

function catalogRow(c) {
  const builtin = c.builtin ? model(c.builtin) : null;
  const file = (c.added && c.added_file) || HUB.pick[c.key] || c.files?.[0]?.file;
  const size = builtin ? builtin.download?.size : c.files ? c.files.find((f) => f.file === file)?.size : c.size;
  const pick = c.files?.length > 1 && !c.added && !builtin
    ? `<select data-catalog-file="${esc(c.key)}" aria-label="File for ${esc(c.name)}">${c.files.map((f) => `<option value="${esc(f.file)}"${f.file === file ? " selected" : ""}>${esc(quant(f.file))} (${bytes(f.size)})</option>`).join("")}</select>` : "";
  const action = builtin ? setStatus("none", "Built in")
    : c.added ? setStatus("ok", `Added${c.added_file && c.files?.length > 1 ? `: ${quant(c.added_file)}` : ""}`, ICON.check)
    : c.problem ? "" : `<button type="button" class="btn btn-sm btn-primary" data-catalog-add="${esc(c.key)}" ${HUB.busy ? "disabled" : ""}>${ICON.download} Add (${bytes(size)})</button>`;
  const engine = builtin ? ENGINE_NAME[builtin.engine] : c.kind === "gguf" ? "transcribe.cpp" : "faster-whisper";
  const maker = builtin ? makerOf(builtin) : String(c.repo || "").split("/")[0];
  const facts = [maker, engine, size ? bytes(size) : "", c.licence, GPU_SHORT[c.gpu]];
  const tile = modelTile(builtin || { kind: "local", title: c.name, hub: { repo: c.repo } });
  const open = SET.details.add === c.key;
  return `<div class="set-item catalog-item${open ? " expanded" : ""}">
    <div class="set-item-row" data-row="${esc(c.key)}">${tile}<div class="set-item-text"><b>${esc(c.name)}</b><span>${esc(c.good_for)}</span></div>
      <div class="set-item-actions">${pick}${action}${detailsButton("add", c.key, c.name)}</div>${factsLine(facts)}</div>
    ${c.problem ? `<div class="set-item-more"><p class="mc-missing">${esc(c.problem)}</p></div>` : ""}
    ${open ? detailsBox("add", c.key, c.name, catalogDetails(c, builtin)) : ""}
  </div>`;
}

function catalogDetails(c, builtin) {
  const a = builtin?.about || {};
  const size = c.size || builtin?.download?.size;
  return [
    ["Made by", esc(builtin ? builtin.maker : `${String(c.repo).split("/")[0]}, on Hugging Face`)],
    ["From", builtin ? hfSource(a.source?.repo, a.source?.revision) : hfSource(c.repo, c.revision)],
    ["Kind", esc(builtin ? `Built in, runs with ${ENGINE_LONG[builtin.engine]}` : `${KIND_NAME[c.kind] || c.kind}${c.architecture ? ` (${c.architecture})` : ""}`)],
    c.files ? ["Files", bullets(c.files.map((f) => `${f.file} (${bytes(f.size)})`))] : ["Download", size ? esc(bytes(size)) : null],
    ["Licence", c.licence ? esc(c.licence) : null],
    ["Graphics card", GPU_USE[c.gpu] ? esc(GPU_USE[c.gpu]) : null],
    ["Good for", c.good_for ? esc(c.good_for) : null],
    ["In the project's tests", c.evidence ? esc(c.evidence) : null],
  ];
}

function hubBlock() {
  const h = HUB, input = $("#hubUrl");
  h.caret = input && document.activeElement === input ? [input.selectionStart, input.selectionEnd] : null;
  return `<div class="hub">
    <p class="hint" style="margin:0 0 8px"><b>Add a model from Hugging Face.</b> Paste the link to its page, or its name (org/name). Whisper models for faster-whisper or in Transformers format, and GGUF speech models for transcribe.cpp, can be added.</p>
    <form class="row" id="hubForm"><input type="text" id="hubUrl" value="${esc(h.url)}" placeholder="https://huggingface.co/org/name" spellcheck="false" autocomplete="off" aria-label="Hugging Face link or model name">
      <button class="btn" type="submit" ${h.busy ? "disabled" : ""}>${h.busy ? "Checking…" : "Check"}</button></form>
    ${h.found ? hubFound(h.found) : ""}
    ${h.error ? `<div class="hub-found error" role="alert">${esc(h.error)}</div>` : ""}
  </div>`;
}

function hubFound(f) {
  const file = f.choices && f.choices.length > 1
    ? `<select id="hubFile" aria-label="Model file" ${HUB.busy ? "disabled" : ""}>${f.choices.map((c) => `<option value="${esc(c.file)}"${c.file === f.file ? " selected" : ""}>${esc(c.file)} (${bytes(c.size)})</option>`).join("")}</select>`
    : `<code>${esc(f.file)}</code>`;
  const action = f.problem ? `<p class="mc-missing" style="margin:0">${esc(f.problem)}</p>`
    : f.added ? `<span class="pill ok">Already in the app</span>`
    : `<button type="button" class="btn btn-sm btn-primary" id="hubAdd" ${HUB.busy ? "disabled" : ""}>${ICON.download} Add and download (${bytes(f.size)})</button>`;
  return `<div class="hub-found"><dl class="kv">
      <dt>Model</dt><dd><a href="https://huggingface.co/${esc(f.repo)}" target="_blank" rel="noopener noreferrer">${esc(f.repo)}</a></dd>
      <dt>Kind</dt><dd>${esc(f.label)}</dd>
      ${f.architecture ? `<dt>Architecture</dt><dd><code>${esc(f.architecture)}</code></dd>` : ""}
      ${f.file ? `<dt>File</dt><dd>${file}</dd>` : ""}
      <dt>Size</dt><dd>${bytes(f.size)}${f.kind === "transformers" ? ", then converted on this computer" : ""}</dd>
      <dt>Licence</dt><dd>${esc(f.licence || "Not stated on the model page")}</dd>
      <dt>Revision</dt><dd><code>${esc(f.revision.slice(0, 7))}</code></dd>
    </dl>${action}</div>`;
}

function bindHub() {
  // the Recommended list and the link form are separate views of Add models: each is bound when it is shown
  $$("[data-catalog-file]").forEach((sel) => (sel.onchange = () => { HUB.pick[sel.dataset.catalogFile] = sel.value; rerenderHub(); }));
  $$("[data-catalog-add]").forEach((b) => (b.onclick = () => catalogAdd(b.dataset.catalogAdd)));
  const form = $("#hubForm"), input = $("#hubUrl");
  if (!form) return;
  input.oninput = () => (HUB.url = input.value);
  form.onsubmit = (e) => { e.preventDefault(); hubCheck(); };
  if (HUB.caret) { input.focus(); input.setSelectionRange(...HUB.caret); }
  const file = $("#hubFile");
  if (file) file.onchange = () => hubCheck(file.value);
  const add = $("#hubAdd");
  if (add) add.onclick = hubAdd;
}

const rerenderHub = () => $("#settings").open && renderSettings();

async function hubCheck(file) {
  const url = HUB.url.trim();
  if (!url || HUB.busy) return;
  Object.assign(HUB, { busy: true, error: "" }, file ? {} : { found: null });
  rerenderHub();
  try { HUB.found = await api.post("/api/hub/inspect", file ? { url, file } : { url }); }
  catch (e) { Object.assign(HUB, { found: null, error: e.message }); }
  HUB.busy = false;
  rerenderHub();
}

// Adds a model and starts its download; returns the error message, if any.
async function addModel(body, name) {
  Object.assign(HUB, { busy: true, error: "" });
  rerenderHub();
  try {
    S.status = await api.post("/api/hub/add", body);
    toast(`Added ${name}. The download has started.`);
    renderTop(); scheduleStatus();
    if (S.route.name === "new") renderNew();
    return null;
  } catch (e) { return e.message; }
  finally { HUB.busy = false; }
}

async function hubAdd() {
  const f = HUB.found;
  if (!f || HUB.busy) return;
  const error = await addModel({ url: HUB.url.trim(), file: f.file || undefined, revision: f.revision }, f.repo);
  Object.assign(HUB, error ? { error } : { url: "", found: null });
  rerenderHub();
}

async function catalogAdd(key) {
  const c = (S.status?.catalog || []).find((x) => x.key === key);
  if (!c || HUB.busy) return;
  const file = c.files ? HUB.pick[key] || c.files[0].file : undefined;
  const error = await addModel({ url: c.repo, file, revision: c.revision }, c.name);
  if (error) toast(error, "error");
  rerenderHub();
}

async function forgetModel(id) {
  const m = model(id);
  if (!confirm(`Remove ${m ? m.title : id} from the app? Its files are deleted from this computer. You can add it again later.`)) return;
  try {
    S.status = await api.del(`/api/models/${id}`);
    if (S.form.model === id) S.form.model = null;
    renderTop(); refreshDownloadViews();
    if (S.route.name === "new") renderNew();
    toast("Removed");
  } catch (e) { toast(e.message, "error"); }
}

async function removeDownload(id) {
  if (!confirm("Delete this model's files from this computer? You can download them again later.")) return;
  try { S.status = await api.del(`/api/downloads/${id}`); renderTop(); refreshDownloadViews(); toast("Deleted"); }
  catch (e) { toast(e.message, "error"); }
}

async function saveKey(id, key, region) {
  key = key.trim();
  const body = { model: id };
  if (region !== undefined) body.region = region.trim();
  if (key || region === undefined) body.key = key;  // with a region field, an empty key box keeps the saved key
  try {
    await api.post("/api/keys", body);
    await refreshStatus();
    SET.openKey = null;
    renderSettings();
    toast(key ? "Key saved" : region !== undefined ? "Region saved" : "Key removed");
    if (S.route.name === "new") renderNew();
  } catch (e) { toast(e.message, "error"); }
}

$$("#settings [data-close]").forEach((b) => (b.onclick = () => $("#settings").close()));
// What was changed in Settings (a model added or downloaded, a key saved) shows on the page behind at once,
// without waiting for the next status check.
$("#settings").addEventListener("close", async () => {
  await refreshStatus();
  if (S.route.name === "new") renderNew();
  aiFollow(S.status?.settings, S.status?.settings?.mcp_hidden);  // a switch changed, or something shown again
});
$("#settingsBtn").onclick = () => openSettings();
$("#quitBtn").onclick = async () => {
  const active = S.jobs.filter((j) => ACTIVE.has(j.status)).length;
  if (!confirm(active ? `${active} transcription${active > 1 ? "s are" : " is"} still running and will be stopped. Quit Sedjem?` : "Quit Sedjem? You can start it again with ./app.sh.")) return;
  try { await api.post("/api/quit"); } catch (e) { /* the server is going away */ }
  document.body.innerHTML = `<div class="welcome" style="padding-top:20vh"><h1>Sedjem has stopped</h1><p>Start it again with <code>./app.sh</code> in the project folder.</p></div>`;
};

// ---------------------------------------------------------------------------------------------
// Menus, theme, routing, polling
// ---------------------------------------------------------------------------------------------
function toggleMenu(id) {
  const m = document.getElementById(id);
  const open = m.classList.contains("open");
  closeMenus();
  if (!open) m.classList.add("open");
}
function closeMenus() { $$(".menu.open").forEach((m) => m.classList.remove("open")); }
document.addEventListener("click", (e) => {
  if (!e.target.closest(".menu")) closeMenus();
  const dl = e.target.closest("[data-dl]"), dlc = e.target.closest("[data-dl-cancel]"), dlr = e.target.closest("[data-dl-remove]");
  if (dl) { e.preventDefault(); e.stopPropagation(); startDownload(dl.dataset.dl); return; }
  if (dlc) { e.preventDefault(); e.stopPropagation(); cancelDownload(dlc.dataset.dlCancel); return; }
  if (dlr) { e.preventDefault(); e.stopPropagation(); removeDownload(dlr.dataset.dlRemove); return; }
  const ext = e.target.closest('a[target="_blank"]');
  if (ext && desktopApi()) { e.preventDefault(); desktopApi().open_url(ext.href); }
  const s = e.target.closest("[data-open-settings]");
  if (s) { e.preventDefault(); e.stopPropagation(); openSettings(s.dataset.openSettings); }
});

$("#themeBtn").onclick = () => {
  const dark = document.documentElement.dataset.theme !== "dark";
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  try { localStorage.setItem("sedjem-theme", dark ? "dark" : "light"); } catch (e) { /* private mode */ }
};
$("#menuToggle").onclick = () => $("#sidebar").classList.toggle("open");
$("#jobSearch").oninput = renderSidebar;

// keep the direction right while a line, title or name is being typed
document.addEventListener("input", (e) => {
  const el = e.target.closest && e.target.closest("[data-mixdir]");
  if (el) el.dir = mixDir(el.value ?? el.textContent);
});
document.addEventListener("keydown", (e) => {
  const typing = e.target.closest("input, textarea, select, button, [contenteditable], video[controls]");
  if (e.key === "Escape") closeMenus();
  if (typing || $("#player").hidden || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === " ") { e.preventDefault(); playPause(); }
  if (e.key === "ArrowLeft") skip(-5);
  if (e.key === "ArrowRight") skip(5);
});

window.addEventListener("beforeunload", (e) => {
  if (S.dirty || S.uploading) { e.preventDefault(); e.returnValue = ""; }
});

function route() {
  const h = location.hash;
  const m = h.match(/^#\/job\/([\w-]+)$/), c = h.match(/^#\/compare\/([\w,-]+)$/);
  if (!leaveCompare(h)) return;  // unsaved picks in the compare view (compare.js)
  if (S.dirty && !(m && S.job && m[1] === S.job.id) && !confirm("Discard your unsaved changes?")) {
    history.replaceState(null, "", `#/job/${S.job.id}`);
    return;
  }
  S.dirty = false;
  $("#sidebar").classList.remove("open");
  if (m) {
    const changed = S.route.id !== m[1];
    S.route = { name: "job", id: m[1] };
    if (changed) { S.job = null; S.lines = []; S.editing = false; S.search = ""; S.lineSig = ""; stopJobPoll(); renderJob(); }
    loadJob(m[1]);
  } else if (c) {
    S.route = { name: "compare", ids: c[1].split(",") };
    stopJobPoll();
    renderCompare(S.route.ids);
  } else {
    S.route = { name: "new" };
    renderNew();
  }
  renderSidebar();
}

async function refreshStatus() {
  try { S.status = await api.get("/api/status"); renderTop(); updStatus(); } catch (e) { /* server restarting */ }
}

let statusTimer = null;
function scheduleStatus() {
  clearTimeout(statusTimer);
  statusTimer = setTimeout(async () => {
    const was = downloading();
    await refreshStatus();
    if (was || downloading()) refreshDownloadViews();
    if (was && !downloading() && S.route.name === "new") renderNew();
    scheduleStatus();
  }, downloading() ? 1500 : 20000);
}
async function refreshJobs() {
  try {
    S.jobs = (await api.get("/api/jobs")).jobs;
    renderSidebar();
    renderTop();
  } catch (e) { /* server restarting */ }
}

let jobsTimer = null;
function scheduleJobs() {
  clearTimeout(jobsTimer);
  const busy = S.jobs.some((j) => ACTIVE.has(j.status));
  jobsTimer = setTimeout(async () => { await refreshJobs(); scheduleJobs(); }, busy ? 2000 : 10000);
}

(async function init() {
  await refreshStatus();
  if (!S.status) { view.innerHTML = `<div class="welcome"><h1>Can't reach the app</h1><p>Start it with <code>./app.sh</code>.</p></div>`; return; }
  await refreshJobs();
  window.addEventListener("hashchange", route);
  route();
  scheduleJobs();
  scheduleStatus();
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
})();
