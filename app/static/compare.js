/* Sedjem: the transcripts of one recording (the server side is app/compare.py). The sidebar shows a
   recording once with its transcripts, the job page switches between them, and the compare view lines two
   or three of them up by time, marks the words that differ, and saves the parts picked from each as a new
   version of one of them (its History has the versions). Loaded before app.js: it only defines things, and uses app.js's helpers (S, api, $, esc,
   clock, mixDir, the player) when they run. */
"use strict";

const CMP_ICON = {
  columns: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v16"/></svg>',
  check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12.5 4.5 4.5L19 7.5"/></svg>',
  close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6 6 18"/></svg>',
  plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>',
};

// The compare view's state. picks: the stretches picked from a transcript, [{start, end, from}], in order
// (a later one wins where they overlap; everything else comes from the base). speakers: the user's changes
// to the speaker matching, {job id: {speaker: base speaker}}.
const CMP = {
  ids: [], base: null, data: null, picks: [], undo: [], speakers: {}, onlyDiff: false,
  lastKeep: null, playFrom: 0, playTo: null, current: -1, loading: 0, bar: "",
};

const jobIcon = (j) => kindIcon(j);  // youtube.js: a laptop, a cloud, or captions from YouTube
const joinWords = (items) => (items.length < 2 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`);
const STATUS_WORD = { done: "Done", failed: "Failed", cancelled: "Cancelled", interrupted: "Interrupted", queued: "Queued" };

// ---------------------------------------------------------------------------------------------
// Groups: the sidebar and the switcher on the job page
// ---------------------------------------------------------------------------------------------

// The transcriptions by recording, in the list's order: newest first, by each recording's newest.
function jobGroups(jobs) {
  const by = new Map();
  for (const j of jobs) {
    const key = j.group || j.id;
    if (!by.has(key)) by.set(key, []);
    by.get(key).push(j);
  }
  return [...by.values()].map((list) => ({ jobs: list, main: list[0] }));
}

// The transcripts of a job's recording, oldest first.
function groupOf(id) {
  const me = S.jobs.find((j) => j.id === id);
  return me ? S.jobs.filter((j) => (j.group || j.id) === (me.group || me.id)).reverse() : [];
}

// A transcript's label in a group: its model, numbered when the model was used more than once.
function verLabels(list) {
  const seen = {}, count = {};
  for (const j of list) count[j.model_title || j.model] = (count[j.model_title || j.model] || 0) + 1;
  return new Map(list.map((j) => {
    const name = j.model_title || j.model;
    seen[name] = (seen[name] || 0) + 1;
    return [j.id, count[name] > 1 ? `${name} ${seen[name]}` : name];
  }));
}

function verPill(j, on, label) {
  const tip = [j.model_title || j.model, STATUS_WORD[j.status] || "In progress", when(j.created)].join(" · ");
  const bad = ["failed", "interrupted"].includes(j.status) ? " bad" : "";
  return `<a class="pill ver${on ? " on" : ""}${bad}" href="#/job/${j.id}" title="${esc(tip)}"${on ? ' aria-current="page"' : ""}>`
    + `${ACTIVE.has(j.status) ? '<span class="dot pulse"></span>' : ""}<span>${esc(label)}</span></a>`;
}

// A recording with several transcripts in the sidebar: the title opens the newest, a pill each (oldest
// first) opens that one.
function groupItem(g) {
  const open = (j) => S.route.name === "job" && S.route.id === j.id;
  const here = g.jobs.some(open) || (S.route.name === "compare" && g.jobs.some((j) => S.route.ids.includes(j.id)));
  const busy = g.jobs.find((j) => ACTIVE.has(j.status));
  const j = g.main, list = [...g.jobs].reverse(), labels = verLabels(list);
  const bar = busy ? `<div class="bar"><i style="width:${jobPercent(busy).toFixed(1)}%"></i></div>` : "";
  return `<div class="job-item grouped${here ? " active" : ""}">
    <a class="t" href="#/job/${j.id}" dir="${mixDir(j.title || "Untitled")}">${esc(j.title || "Untitled")}</a>${statusLabel(busy || j)}
    <span class="d"><span class="vers">${list.map((x) => verPill(x, open(x), labels.get(x.id))).join("")}</span>${j.audio_s ? `<span class="len">${clock(j.audio_s)}</span>` : ""}</span>
    ${bar}</div>`;
}

// The switcher on the job page: the recording's transcripts, and Compare once two of them are done.
function groupBar(j) {
  CMP.bar = groupBarInner(j);
  return `<div id="groupBar">${CMP.bar}</div>`;
}

function groupBarInner(j) {
  const list = groupOf(j.id);
  if (list.length < 2) return "";
  const done = list.filter((x) => x.status === "done");
  const pair = j.status === "done" ? [j.id, ...done.filter((x) => x.id !== j.id).slice(-1).map((x) => x.id)]
    : done.slice(-2).map((x) => x.id);
  const cmp = pair.length === 2 ? `<a class="btn btn-sm" id="compareBtn" href="#/compare/${pair.join(",")}">${CMP_ICON.columns} Compare</a>` : "";
  const labels = verLabels(list);
  return `<nav class="versions" aria-label="Transcripts of this recording"><span class="versions-label">Transcripts of this recording</span>
    ${list.map((x) => verPill(x, x.id === j.id, labels.get(x.id))).join("")}${cmp}</nav>`;
}

// After the list of transcriptions was fetched again (only when something changed, so focus stays put)
function refreshGroupBar() {
  const el = document.getElementById("groupBar");
  if (!el || S.route.name !== "job" || !S.job) return;
  const html = groupBarInner(S.job);
  if (html !== CMP.bar) el.innerHTML = CMP.bar = html;
}

// ---------------------------------------------------------------------------------------------
// The compare view: #/compare/<id>,<id>[,<id>]
// ---------------------------------------------------------------------------------------------
async function renderCompare(ids) {
  view.classList.add("wide");
  S.lines = [];  // the player follows no single transcript here
  if (!CMP.ids.some((id) => ids.includes(id))) {  // another recording: start afresh
    Object.assign(CMP, { base: null, data: null, picks: [], undo: [], speakers: {}, lastKeep: null });
  }
  CMP.ids = ids;
  CMP.picks = CMP.picks.filter((p) => ids.includes(p.from));
  if (!ids.includes(CMP.base)) { CMP.base = ids[0]; CMP.speakers = {}; }
  if (ids.length < 2 || ids.length > 3 || new Set(ids).size !== ids.length) {
    view.innerHTML = `<div class="welcome"><h1>Compare transcripts</h1><p>Choose two or three transcripts of one recording.</p></div>`;
    return;
  }
  if (!CMP.data) view.innerHTML = `<div class="welcome">Lining up the transcripts…</div>`;
  await cmpLoad();
}

async function cmpLoad() {
  const token = ++CMP.loading;
  try {
    const data = await api.get(`/api/compare?ids=${CMP.ids.join(",")}&base=${CMP.base}`);
    if (token !== CMP.loading || S.route.name !== "compare") return;
    CMP.data = data;
    cmpDraw();
  } catch (e) {
    if (token !== CMP.loading || S.route.name !== "compare") return;
    view.innerHTML = `<div class="welcome"><h1>Can't compare these</h1><p>${esc(e.message)}</p>
      <p><a href="#/job/${esc(CMP.ids[0])}">Back to the transcript</a></p></div>`;
  }
}

// Leaving the compare view (called by route() first): ask before dropping unsaved picks. Another choice of
// columns keeps them.
function leaveCompare(hash) {
  if (S.route.name !== "compare" || /^#\/compare\//.test(hash)) return true;
  if (CMP.picks.length && !confirm("Leave without saving the text you picked?")) {
    history.replaceState(null, "", `#/compare/${S.route.ids.join(",")}`);
    return false;
  }
  view.classList.remove("wide");
  Object.assign(CMP, { ids: [], data: null, picks: [], undo: [], speakers: {}, playTo: null, current: -1 });
  return true;
}

const cmpJob = (id) => CMP.data.jobs.find((j) => j.id === id);
const cmpMap = (id) => ({ ...(CMP.data.mapping[id] || {}), ...(CMP.speakers[id] || {}) });

// Speakers as in the base: a line's speaker in the base's numbering, and its name there. A speaker the base
// doesn't have keeps its own transcript's name, as in the saved version.
function cmpSpeaker(id, sid) {
  if (sid == null || id === CMP.data.base) return sid;
  return cmpMap(id)[sid] ?? sid;
}
function cmpName(t) {
  const d = CMP.data, base = cmpJob(d.base);
  if (base.speaker_names[t]) return base.speaker_names[t];
  if (!base.speakers.some((s) => s.id === t)) {
    for (const j of d.jobs) {
      if (j.id === d.base) continue;
      const map = cmpMap(j.id);
      const own = Object.keys(map).find((s) => map[s] === t && j.speaker_names[s]);
      if (own) return j.speaker_names[own];
    }
  }
  return `Speaker ${t}`;
}
const cmpOwnName = (j, sid) => j.speaker_names[sid] || `Speaker ${sid}`;

// Which transcript's text is kept at time t (as combine() in app/compare.py): the last pick over it, else the base.
function cmpOwner(t) {
  for (let i = CMP.picks.length - 1; i >= 0; i--) {
    const p = CMP.picks[i];
    if (p.start <= t && t < p.end) return p.from;
  }
  return CMP.data.base;
}
const cmpMids = (row) => row.cells.flat().map((x) => (x.start + x.end) / 2);
const cmpRowIs = (row, id) => cmpMids(row).every((t) => cmpOwner(t) === id);

// The stretches kept from transcripts other than the base: [[start, end, id]] (as timeline() in compare.py).
function cmpTimeline() {
  let segs = [];
  for (const p of CMP.picks) {
    const rest = [];
    for (const [a, b, o] of segs) {
      if (a < p.start) rest.push([a, Math.min(b, p.start), o]);
      if (b > p.end) rest.push([Math.max(a, p.end), b, o]);
    }
    segs = [...rest, [p.start, p.end, p.from]].sort((x, y) => x[0] - y[0]);
  }
  const out = [];
  for (const s of segs) {
    const last = out[out.length - 1];
    if (last && last[2] === s[2] && last[1] === s[0]) last[1] = s[1]; else out.push([...s]);
  }
  return out.filter((s) => s[2] !== CMP.data.base);
}
function cmpBySource() {
  const by = new Map();
  for (const [a, b, o] of cmpTimeline()) {
    if (!by.has(o)) by.set(o, []);
    by.get(o).push([a, Math.min(b, CMP.data.audio_s || b)]);
  }
  return by;
}

function cmpDraw() {
  const d = CMP.data, n = d.ids.length, base = cmpJob(d.base);
  const differ = d.rows.filter((r) => !r.same).length;
  const more = groupOf(d.base).filter((j) => j.status === "done" && !d.ids.includes(j.id));
  const add = n < 3 && more.length ? `<div class="menu-wrap"><button type="button" class="btn btn-sm" data-menu="cmpAdd">${CMP_ICON.plus} Add a transcript ${ICON.chevron}</button>
    <div class="menu right" id="cmpAdd">${more.map((j) => `<a href="#/compare/${[...d.ids, j.id].join(",")}">${jobIcon(j)} ${esc(j.model_title || j.model)}<span class="sub">${esc(when(j.created))}</span></a>`).join("")}</div></div>` : "";
  const choices = d.jobs.map((j) => `<option value="${j.id}"${j.id === d.ids.find((x) => x !== d.base) ? " selected" : ""}>${esc(j.model_title || j.model)}</option>`).join("");
  view.innerHTML = `
  <div class="cmp-head">
    <h1>Compare transcripts</h1>
    <p class="lead"><span dir="${mixDir(base.title || "")}">${esc(base.title || "Untitled")}</span>${d.audio_s ? ` · ${clock(d.audio_s)}` : ""}. Choose the text to keep in each row, or for a stretch of time; the base is kept everywhere else. Click a row to hear it.</p>
  </div>
  ${cmpSpeakersPanel()}
  <div class="cmp-tools">
    <label class="toggle"><input type="checkbox" id="cmpOnlyDiff" ${CMP.onlyDiff ? "checked" : ""}> Only rows that differ</label>
    <span class="count">${differ} of ${d.rows.length} row${d.rows.length === 1 ? "" : "s"} differ</span>
    <span class="spacer"></span>
    <form class="cmp-range" id="cmpRange">
      <span>Keep</span><select id="cmpRangeFrom" aria-label="Transcript to keep">${choices}</select>
      <label for="cmpRangeStart">from</label><input type="text" id="cmpRangeStart" placeholder="0:00" autocomplete="off" spellcheck="false">
      <label for="cmpRangeEnd">to</label><input type="text" id="cmpRangeEnd" placeholder="${clock(Math.min(90, d.audio_s || 90))}" autocomplete="off" spellcheck="false">
      <button type="submit" class="btn btn-sm">Keep</button>
    </form>
    ${add}
  </div>
  <div class="cmp-grid${CMP.onlyDiff ? " only-diff" : ""}" id="cmpGrid" style="--n:${n}">
    <div class="cmp-cols"><div class="cmp-corner"></div>${d.jobs.map(cmpColHead).join("")}</div>
    ${d.rows.map(cmpRow).join("") || `<div class="empty-transcript">These transcripts have no lines yet.</div>`}
  </div>
  <div class="editbar cmp-bar" id="cmpBar"></div>`;
  CMP.current = -1;
  cmpBind();
  cmpUpdate();
  showPlayer({ id: d.base, title: base.title });
}

function cmpColHead(j) {
  const d = CMP.data, isBase = j.id === d.base;
  return `<div class="cmp-col">
    <a class="cmp-col-title" href="#/job/${j.id}" title="Open this transcript">${jobIcon(j)}<span>${esc(j.model_title || j.model)}</span></a>
    <div class="cmp-col-sub">${esc(when(j.created))}${j.speakers.length ? ` · ${j.speakers.length} speaker${j.speakers.length === 1 ? "" : "s"}` : ""}</div>
    <div class="cmp-col-actions">${isBase ? `<span class="pill accent" title="Its text is kept wherever you choose nothing else">Base</span>`
      : `<button type="button" class="pill" data-base="${j.id}">Use as base</button>`}
      ${d.ids.length > 2 ? `<button type="button" class="icon-btn" data-drop="${j.id}" title="Take it out of the comparison" aria-label="Take ${esc(j.model_title || j.model)} out of the comparison">${CMP_ICON.close}</button>` : ""}</div>
  </div>`;
}

function cmpRow(row, r) {
  return `<div class="cmp-row${row.same ? " same" : ""}" data-r="${r}">
    <button type="button" class="ts" data-play="${r}" title="Play ${clock(row.start)}–${clock(row.end)}">${clock(row.start)}</button>
    ${row.cells.map((cell, k) => cmpCell(cell, k, r)).join("")}
  </div>`;
}

function cmpCell(cell, k, r) {
  const d = CMP.data, id = d.ids[k], j = cmpJob(id);
  let prev;
  const lines = cell.map((x) => {
    const who = cmpSpeaker(id, x.speaker);
    const head = who != null && who !== prev
      ? `<div class="who"${id !== d.base ? ` title="${esc(cmpOwnName(j, x.speaker))} in ${esc(j.model_title || j.model)}"` : ""}>${esc(cmpName(who))}</div>` : "";
    prev = who;
    const text = x.spans.map(([t, c]) => (c ? `<mark class="d${c}">${esc(t)}</mark>` : esc(t))).join(" ");
    return `<div class="cmp-line" style="--c:${spkColor(who)}">${head}<p class="text" dir="${mixDir(x.text)}">${text}</p></div>`;
  }).join("");
  return `<div class="cmp-cell" data-label="${esc(j.model_title || j.model)}">
    ${lines || `<p class="cmp-nothing">Nothing here</p>`}
    <button type="button" class="pill keep" data-keep="${r}:${k}" aria-pressed="false" aria-label="Keep ${esc(j.model_title || j.model)} at ${clock(d.rows[r].start)}">Keep</button>
  </div>`;
}

function cmpSpeakersPanel() {
  const d = CMP.data, base = cmpJob(d.base);
  const rows = d.jobs.filter((j) => j.id !== d.base && j.speakers.length).map((j) => {
    const map = cmpMap(j.id);
    const items = j.speakers.map((s) => {
      const t = map[s.id];
      const known = base.speakers.some((b) => b.id === t);
      const together = ((d.overlap[j.id] || {})[s.id] || {})[t];
      const options = base.speakers.map((b) => `<option value="${esc(b.id)}"${b.id === t ? " selected" : ""}>${esc(cmpName(b.id))}</option>`).join("")
        + (known ? "" : `<option value="${esc(t)}" selected>${esc(cmpName(t))} (new)</option>`) + `<option value="new">New speaker</option>`;
      return `<div class="cmp-map-item"><span class="sw" style="background:${spkColor(t)}"></span><span>${esc(cmpOwnName(j, s.id))}</span>
        <span class="arrow" aria-hidden="true">→</span>
        <select data-map="${esc(j.id)}:${esc(s.id)}" aria-label="${esc(cmpOwnName(j, s.id))} of ${esc(j.model_title || j.model)} is">${options}</select>
        <span class="share">${together && s.talk ? `${Math.round(together / s.talk * 100)}% at the same time` : human(s.talk)}</span></div>`;
    }).join("");
    return `<div class="cmp-map-row"><div class="cmp-map-model">${jobIcon(j)} ${esc(j.model_title || j.model)}</div><div class="cmp-map-list">${items}</div></div>`;
  }).join("");
  if (!rows) return "";
  return `<div class="panel cmp-speakers"><h3>Speakers</h3>
    <p class="hint">Each model numbers the speakers its own way. They are matched to ${esc(base.model_title)}'s by who talks at the same time, and the new version uses ${esc(base.model_title)}'s names. Change a match if it is wrong.</p>
    ${rows}</div>`;
}

// Mark what is kept (lines, cells, the Keep buttons) and update the bar, without drawing the rows again.
function cmpUpdate() {
  const d = CMP.data, rowEls = $$("#cmpGrid .cmp-row");
  d.rows.forEach((row, r) => {
    const cellEls = rowEls[r].querySelectorAll(".cmp-cell");
    row.cells.forEach((cell, k) => {
      const id = d.ids[k], el = cellEls[k], all = cmpRowIs(row, id);
      el.querySelectorAll(".cmp-line").forEach((line, n) => line.classList.toggle("kept", cmpOwner((cell[n].start + cell[n].end) / 2) === id));
      el.classList.toggle("kept", all);
      const b = el.querySelector(".keep");
      b.setAttribute("aria-pressed", String(all));
      b.innerHTML = all ? `${CMP_ICON.check} Kept` : "Keep";
    });
  });
  const base = cmpJob(d.base), by = cmpBySource();
  const text = by.size ? `${esc(base.model_title)}, with ${joinWords([...by].map(([id, list]) => `${esc(cmpJob(id).model_title)} in ${list.length} place${list.length === 1 ? "" : "s"}`))}`
    : `Everything from ${esc(base.model_title)} so far`;
  $("#cmpBar").innerHTML = `<span id="cmpSummary">${text}</span>
    <button type="button" class="btn btn-sm" id="cmpUndo" ${CMP.undo.length ? "" : "disabled"}>Undo</button>
    <button type="button" class="btn btn-sm" id="cmpClear" ${CMP.picks.length ? "" : "disabled"}>Clear</button>
    <button type="button" class="btn btn-sm btn-primary" id="cmpSave" ${by.size ? "" : "disabled"}>Save as a new version</button>`;
  $("#cmpUndo").onclick = () => { CMP.picks = CMP.undo.pop() || []; cmpUpdate(); };
  $("#cmpClear").onclick = () => cmpChange(() => (CMP.picks = []));
  $("#cmpSave").onclick = cmpSaveDialog;
}

function cmpChange(fn) {
  const before = CMP.picks.slice();
  fn();
  if (JSON.stringify(before) !== JSON.stringify(CMP.picks)) CMP.undo.push(before);
  cmpUpdate();
}

// Keep a row from transcript k; with Shift, every row from the last one kept in that column. Pressing a
// row's Keep that is already on puts the row back to the base.
function cmpKeep(r, k, shift) {
  const d = CMP.data, id = d.ids[k], last = CMP.lastKeep;
  cmpChange(() => {
    if (shift && last && last.k === k && last.r !== r) {
      const from = d.rows[Math.min(last.r, r)].from, to = d.rows[Math.max(last.r, r)].to;
      CMP.picks = CMP.picks.filter((p) => !(p.start >= from && p.end <= to));
      CMP.picks.push({ start: from, end: to, from: id });
      return;
    }
    const row = d.rows[r];
    const target = id !== d.base && cmpRowIs(row, id) ? d.base : id;
    CMP.picks = CMP.picks.filter((p) => !(p.start === row.from && p.end === row.to));
    if (!cmpRowIs(row, target)) CMP.picks.push({ start: row.from, end: row.to, from: target });
  });
  CMP.lastKeep = { r, k };
}

// 1:05, 1:02:05, 65 or 65.5 -> seconds (NaN if it isn't a time)
function cmpParseTime(s) {
  const m = String(s || "").trim().match(/^(?:(\d+):)?(?:(\d+):)?(\d+(?:[.,]\d+)?)$/);
  if (!m) return NaN;
  return [m[1], m[2], m[3]].filter((x) => x != null).reduce((t, x) => t * 60 + parseFloat(x.replace(",", ".")), 0);
}

function cmpPlay(r) {
  const row = CMP.data.rows[r];
  CMP.playFrom = row.start;
  CMP.playTo = row.end;
  audio.currentTime = row.start;
  audio.play().catch(() => {});  // e.g. the browser wants a click on the page first
}

function cmpBind() {
  const grid = $("#cmpGrid");
  grid.onclick = (e) => {
    const keep = e.target.closest("[data-keep]"), base = e.target.closest("[data-base]"), drop = e.target.closest("[data-drop]");
    if (keep) { const [r, k] = keep.dataset.keep.split(":").map(Number); cmpKeep(r, k, e.shiftKey); return; }
    if (base) { CMP.base = base.dataset.base; CMP.speakers = {}; cmpLoad(); return; }
    if (drop) { location.hash = `#/compare/${CMP.ids.filter((x) => x !== drop.dataset.drop).join(",")}`; return; }
    const row = e.target.closest(".cmp-row");
    if (row && !e.target.closest("a") && !String(getSelection()).trim()) cmpPlay(Number(row.dataset.r));
  };
  $("#cmpOnlyDiff").onchange = (e) => { CMP.onlyDiff = e.target.checked; grid.classList.toggle("only-diff", CMP.onlyDiff); };
  $$("select[data-map]").forEach((sel) => (sel.onchange = () => {
    const [id, sid] = sel.dataset.map.split(":");
    let t = sel.value;
    if (t === "new") {  // the next number no one uses yet
      const used = [...cmpJob(CMP.data.base).speakers.map((s) => s.id), ...CMP.data.ids.flatMap((x) => (x === CMP.data.base ? [] : Object.values(cmpMap(x))))];
      t = String(Math.max(0, ...used.map(Number)) + 1);
    }
    CMP.speakers[id] = { ...(CMP.speakers[id] || {}), [sid]: t };
    cmpDraw();
  }));
  $$("#view [data-menu]").forEach((b) => (b.onclick = (e) => { e.stopPropagation(); toggleMenu(b.dataset.menu); }));
  $("#cmpRange").onsubmit = (e) => {
    e.preventDefault();
    const from = $("#cmpRangeFrom").value, a = cmpParseTime($("#cmpRangeStart").value), b = cmpParseTime($("#cmpRangeEnd").value);
    if (!(a >= 0 && b > a)) { toast("Give a start and an end time, like 1:05 and 2:30.", "error"); return; }
    cmpChange(() => CMP.picks.push({ start: a, end: b, from }));
    toast(`Keeping ${cmpJob(from).model_title} from ${clock(a)} to ${clock(b)}`);
  };
}

// Saving: the combination becomes a new version of the base transcript, reviewed as a diff first like an
// edit (diffDialog, changesHtml in app.js), with an optional message. It can be restored or undone in History.
function cmpBody() {
  const d = CMP.data;
  return {
    ids: d.ids, base: d.base, picks: CMP.picks,
    speakers: Object.fromEntries(d.ids.filter((id) => id !== d.base).map((id) => [id, cmpMap(id)])),
  };
}

async function cmpSaveDialog() {
  let r;
  try { r = await api.post("/api/combine/preview", cmpBody()); } catch (e) { toast(e.message, "error"); return; }
  const base = cmpJob(CMP.data.base), dlg = diffDialog("cmpReview", "Review the new version");
  if (!$("#cmpReviewForm")) {
    dlg.insertAdjacentHTML("beforeend", `<form class="dlg-foot review-foot" id="cmpReviewForm">
      <input type="text" id="cmpReviewMsg" maxlength="500" dir="auto" data-mixdir placeholder="Message (optional)" aria-label="Message for this version (optional)">
      <button type="button" class="btn" data-close>Back</button>
      <button type="submit" class="btn btn-primary" id="cmpSaveGo">Save version</button></form>`);
    $("#cmpReviewForm [data-close]").onclick = () => dlg.close();
    $("#cmpReviewForm").onsubmit = (e) => { e.preventDefault(); cmpSave(dlg); };
  }
  const list = [...cmpBySource()].map(([id, spans]) => `<li>${esc(cmpJob(id).model_title)}: ${spans.map(([a, b]) => `${clock(a)}–${clock(b)}`).join(", ")}</li>`).join("");
  $("#cmpReviewBody").innerHTML = `<p class="cmp-sum">This is saved as version ${r.version + 1} of the ${esc(base.model_title)} transcript. It keeps that text except here:</p>
    <ul class="cmp-sum-list">${list}</ul>
    <p class="hint diff-base">Compared with version ${r.version}, the current one. The earlier versions stay in its History, so you can go back.</p>
    ${changesHtml(r.changes, true)}`;
  bindDiff($("#cmpReviewBody"), r.changes);
  $("#cmpSaveGo").disabled = false;
  $("#cmpReviewMsg").value = "";
  dlg.showModal();
  $("#cmpReviewMsg").focus();
}

async function cmpSave(dlg) {
  const go = $("#cmpSaveGo");
  if (go.disabled) return;
  go.disabled = true;
  try {
    const r = await api.post("/api/combine", { ...cmpBody(), message: $("#cmpReviewMsg").value.trim() });
    dlg.close();
    CMP.picks = [];
    CMP.undo = [];
    await refreshJobs();
    toast(`Saved as version ${r.version}`);
    location.hash = `#/job/${r.job.id}`;
  } catch (e) {
    go.disabled = false;
    toast(e.message, "error");
  }
}

// The row being played is marked, and a row's stretch stops at its end.
function cmpMarkPlaying(t) {
  const rows = CMP.data ? CMP.data.rows : [];
  let lo = 0, hi = rows.length - 1, at = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (rows[mid].start <= t) { at = mid; lo = mid + 1; } else hi = mid - 1;
  }
  if (at >= 0 && t > rows[at].end + 0.5) at = -1;
  if (at === CMP.current) return;
  CMP.current = at;
  document.querySelectorAll("#cmpGrid .cmp-row.current").forEach((el) => el.classList.remove("current"));
  if (at >= 0) document.querySelector(`#cmpGrid .cmp-row[data-r="${at}"]`)?.classList.add("current");
}

document.getElementById("audio").addEventListener("timeupdate", (e) => {
  if (typeof S === "undefined" || S.route.name !== "compare") return;
  const a = e.target;
  if (CMP.playTo != null && a.currentTime >= CMP.playTo) { CMP.playTo = null; a.pause(); }
  else if (CMP.playTo != null && a.currentTime < CMP.playFrom - 0.5) CMP.playTo = null;  // moved back: play on
  cmpMarkPlaying(a.currentTime);
});
document.getElementById("audio").addEventListener("pause", () => (CMP.playTo = null));
window.addEventListener("beforeunload", (e) => {
  if (typeof S !== "undefined" && S.route.name === "compare" && CMP.picks.length) { e.preventDefault(); e.returnValue = ""; }
});
