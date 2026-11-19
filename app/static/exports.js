// Exporting many transcripts at once (#9): the Export dialog, opened with Export… at the foot of the sidebar or with
// Export course on a lecture's page. Every transcription with a transcript has a tick box, a course's lectures under
// the course (whose box ticks them all); then one format for all, and a zip, one combined file (text, Markdown or
// JSON), or, where the browser or the desktop window can pick one, a folder (POST /api/export, app/exports.py).
const EXP = { picked: new Set(), query: "", format: "txt", combined: false, busy: false, listed: "" };
const EXP_FORMATS = [["txt", "Text"], ["md", "Markdown"], ["srt", "SRT subtitles"], ["vtt", "WebVTT subtitles"], ["json", "JSON"]];
const EXP_COMBINED = new Set(["txt", "md", "json"]);  // subtitles keep a file each
const expCanFolder = () => !!desktopApi()?.export_folder || typeof window.showDirectoryPicker === "function";

// What the dialog lists: each course with its lectures in playlist order (youtube.js), then the other transcriptions,
// newest first, each with its model, since a recording can have several
function expGroups() {
  const done = S.jobs.filter((j) => j.status === "done");
  const courses = courseGroups(done).map((g) => ({ id: g.id, course: g.course.title || "Course", jobs: g.lectures }));
  return [...courses, ...done.filter((j) => !j.course).map((j) => ({ id: j.id, jobs: [j] }))];
}
const expJobs = () => expGroups().flatMap((g) => g.jobs);
const expIds = () => expJobs().filter((j) => EXP.picked.has(j.id)).map((j) => j.id);  // in the dialog's order

function openExportMany(courseId) {
  closeMenus();
  const course = courseId && expGroups().find((g) => g.id === courseId);
  Object.assign(EXP, { picked: new Set(course ? course.jobs.map((j) => j.id) : []), query: "", busy: false });
  const dlg = diffDialog("exportMany", "Export transcriptions");
  dlg.classList.add("exp-dlg");
  if (!$("#expForm")) {
    dlg.insertAdjacentHTML("beforeend", `<form class="dlg-foot exp-foot" id="expForm">
      <label class="exp-format">Format <select id="expFormat">${EXP_FORMATS.map(([v, l]) => `<option value="${v}">${l}</option>`).join("")}</select></label>
      <label class="exp-combined"><input type="checkbox" id="expCombined"> One combined file<span id="expWhy"></span></label>
      <span class="exp-grow"></span>
      <button type="button" class="btn" id="expCancel">Cancel</button>
      <button type="button" class="btn" id="expFolder">${ICON.folder} Into a folder…</button>
      <button type="submit" class="btn btn-primary" id="expGo">${ICON.download} <span id="expGoText">Export</span></button></form>`);
    $("#expCancel").onclick = () => dlg.close();
    $("#expFormat").onchange = (e) => { EXP.format = e.target.value; expSync(); };
    $("#expCombined").onchange = (e) => { EXP.combined = e.target.checked; expSync(); };
    $("#expFolder").onclick = () => exportToFolder();
    $("#expForm").onsubmit = (e) => { e.preventDefault(); exportMany(); };
  }
  $("#expFormat").value = EXP.format;
  $("#expCombined").checked = EXP.combined;
  $("#exportManyBody").innerHTML = `<div class="exp-tools">
      <label class="exp-all"><input type="checkbox" id="expAll"> All</label>
      <input type="search" id="expSearch" placeholder="Search" aria-label="Search the transcriptions" autocomplete="off">
    </div>
    <div class="exp-list" id="expList" role="group" aria-label="Transcriptions"></div>`;
  $("#expSearch").oninput = (e) => { EXP.query = e.target.value.trim().toLowerCase(); drawExpList(); };
  $("#expAll").onchange = (e) => {
    const shown = expShown().flatMap((g) => g.jobs);
    shown.forEach((j) => (e.target.checked ? EXP.picked.add(j.id) : EXP.picked.delete(j.id)));
    drawExpList();
  };
  drawExpList();
  dlg.showModal();
  (course ? $("#expGo") : $("#expSearch")).focus();
}

// The groups the search leaves: a course whose title matches keeps all its lectures, else the lectures that match
function expShown() {
  const q = EXP.query, hit = (t) => !q || (t || "").toLowerCase().includes(q);
  return expGroups().map((g) => (g.course && hit(g.course) ? g : { ...g, jobs: g.jobs.filter((j) => hit(j.title)) }))
    .filter((g) => g.jobs.length);
}

function expRow(j, lecture) {
  const sub = [lecture ? null : j.model_title || j.model, j.audio_s ? clock(j.audio_s) : null].filter(Boolean).join(" · ");
  return `<label class="exp-row"><input type="checkbox" data-exp="${esc(j.id)}"${EXP.picked.has(j.id) ? " checked" : ""}>
    ${lecture ? `<span class="exp-n">${esc(j.course.index)}</span>` : ""}<span class="exp-t" dir="${mixDir(j.title)}">${esc(j.title || "Untitled")}</span>
    ${sub ? `<span class="exp-sub">${esc(sub)}</span>` : ""}</label>`;
}

// The transcriptions came in after the dialog opened, or one finished or went: the list again (not at every check,
// which would move the focus and the scroll)
function expRefresh() {
  if (!$("#exportMany")?.open || expJobs().map((j) => j.id).join() === EXP.listed) return;
  drawExpList();
}

function drawExpList() {
  const groups = expShown();
  EXP.listed = expJobs().map((j) => j.id).join();
  $("#expList").innerHTML = groups.map((g) => (g.course
    ? `<div class="exp-course"><label class="exp-row exp-head"><input type="checkbox" data-exp-course="${esc(g.id)}">
        <span class="exp-t" dir="${mixDir(g.course)}">${YT_ICON.course}${esc(g.course)}</span><span class="exp-sub">${g.jobs.length} lecture${g.jobs.length === 1 ? "" : "s"}</span></label>
        <div class="exp-lectures">${g.jobs.map((j) => expRow(j, true)).join("")}</div></div>`
    : expRow(g.jobs[0], false))).join("")
    || `<p class="hint exp-none">${expJobs().length ? "No transcription matches." : "No transcription is done yet, so there is nothing to export."}</p>`;
  $$("#expList [data-exp]").forEach((b) => (b.onchange = () => { b.checked ? EXP.picked.add(b.dataset.exp) : EXP.picked.delete(b.dataset.exp); expSync(); }));
  $$("#expList [data-exp-course]").forEach((b) => (b.onchange = () => {
    const g = groups.find((x) => x.id === b.dataset.expCourse);
    g.jobs.forEach((j) => (b.checked ? EXP.picked.add(j.id) : EXP.picked.delete(j.id)));
    drawExpList();
    $(`#expList [data-exp-course="${CSS.escape(g.id)}"]`)?.focus();  // the list is new: the focus stays on the course's box
  }));
  expSync();
}

// The boxes of whole courses and of All, ticked, empty or in between, and the buttons with the count
function expSync() {
  const groups = expShown(), state = (jobs, box) => {
    const n = jobs.filter((j) => EXP.picked.has(j.id)).length;
    box.checked = n > 0 && n === jobs.length;
    box.indeterminate = n > 0 && n < jobs.length;
  };
  groups.filter((g) => g.course).forEach((g) => state(g.jobs, $(`#expList [data-exp-course="${CSS.escape(g.id)}"]`)));
  const all = $("#expAll");
  if (all) { state(groups.flatMap((g) => g.jobs), all); all.disabled = !groups.length; }
  const n = expIds().length, combine = EXP_COMBINED.has(EXP.format);
  $("#expCombined").disabled = !combine;
  $("#expWhy").textContent = combine ? "" : " (not for subtitles)";
  const one = EXP.combined && combine;
  $("#expGoText").textContent = !n ? "Export" : one ? `Export ${n} in one file` : `Export ${n} as a zip`;
  $("#expGo").disabled = !n || EXP.busy;
  $("#expFolder").hidden = one || !expCanFolder();
  $("#expFolder").disabled = !n || EXP.busy;
}

// The name the server gives the zip or the combined file (app/exports.py name): the course's, when every one is a
// lecture of the same course
function expName(ids, ext) {
  const jobs = expJobs().filter((j) => ids.includes(j.id)), courses = new Set(jobs.map((j) => j.course?.id));
  const course = courses.size === 1 && jobs[0].course ? jobs[0].course.title : "";
  return `${course ? fileSlug(course) : "sedjem-transcripts"}.${ext}`;
}

async function expFetch(body) {  // POST /api/export with the page's secret, as api.req, but for a file
  const opt = () => ({ method: "POST", body: JSON.stringify(body),
    headers: { "X-Sedjem": "1", "Content-Type": "application/json", ...authHeaders() } });
  let r = await fetch("/api/export", opt());
  if (r.status === 401 && await signInOnce()) r = await fetch("/api/export", opt());
  if (r.status === 401) noAccess();
  if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.error || `${r.status} ${r.statusText}`); }
  return r;
}

function expDone(text) {
  $("#exportMany").close();
  toast(text);
}

// A zip, or one combined file: saved through the desktop window's dialog there, else like a single export, with
// the browser's save picker where it has one
async function exportMany() {
  const ids = expIds(), one = EXP.combined && EXP_COMBINED.has(EXP.format), fmt = EXP.format;
  if (!ids.length || EXP.busy) return;
  const d = desktopApi(), name = expName(ids, one ? fmt : "zip"), count = `${ids.length} transcript${ids.length === 1 ? "" : "s"}`;
  if (d && d.save_many) {
    try { const saved = await d.save_many(ids, fmt, one); if (saved) expDone(`Exported ${count} to ${saved}`); } catch (e) { toast(e.message, "error"); }
    return;
  }
  let handle = null;
  if (window.showSaveFilePicker) {
    try {  // ask first, while the click still counts as the user's action
      const [description, mime] = one ? EXPORT_TYPES[fmt] : ["Zip archive", "application/zip"];
      handle = await window.showSaveFilePicker({ suggestedName: name, types: [{ description, accept: { [mime]: [`.${one ? fmt : "zip"}`] } }] });
    } catch (e) {
      if (e.name === "AbortError") return;
      handle = null;  // the picker isn't allowed here: download instead
    }
  }
  EXP.busy = true;
  expSync();
  try {
    const blob = await (await expFetch({ ids, format: fmt, combined: one })).blob();
    if (handle) {
      const out = await handle.createWritable();
      await out.write(blob);
      await out.close();
    } else downloadBlob(blob, name);
    expDone(`Exported ${count}`);
  } catch (e) { toast(`Could not export: ${e.message}`, "error"); }
  finally { EXP.busy = false; if ($("#exportMany").open) expSync(); }
}

// A file each, into a folder: picked and written by the desktop window, else by the browser (Chrome and Edge),
// which asks for the folder first and then writes what the server gives it, a course's lectures in a folder of
// their own
async function exportToFolder() {
  const ids = expIds(), fmt = EXP.format;
  if (!ids.length || EXP.busy) return;
  const d = desktopApi();
  if (d && d.export_folder) {
    try { const r = await d.export_folder(ids, fmt); if (r) expDone(`Saved ${r.count} file${r.count === 1 ? "" : "s"} in ${r.folder}`); } catch (e) { toast(e.message, "error"); }
    return;
  }
  let dir;
  try { dir = await window.showDirectoryPicker({ mode: "readwrite" }); } catch (e) {
    if (e.name !== "AbortError") toast(`Could not open the folder: ${e.message}`, "error");
    return;
  }
  EXP.busy = true;
  expSync();
  try {
    const { files } = await (await expFetch({ ids, format: fmt, as: "files" })).json();
    for (const f of files) {
      const parts = f.path.split("/");
      let here = dir;
      for (const p of parts.slice(0, -1)) here = await here.getDirectoryHandle(p, { create: true });
      const out = await (await here.getFileHandle(parts.at(-1), { create: true })).createWritable();
      await out.write(f.text);
      await out.close();
    }
    expDone(`Saved ${files.length} file${files.length === 1 ? "" : "s"} in ${dir.name}`);
  } catch (e) { toast(`Could not save: ${e.message}`, "error"); }
  finally { EXP.busy = false; if ($("#exportMany").open) expSync(); }
}
