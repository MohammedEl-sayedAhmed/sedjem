/* Sedjem: assistants over MCP (the server side is app/assistants.py; app/mcp.py is the server an assistant
   starts). Settings → Assistants has what assistants can see (two switches, and what is hidden one by one),
   whether they may save new versions of transcripts (off until turned on), the setting that connects each
   assistant, and what they asked, with the versions they saved standing out. A transcription's page says whether
   assistants can read it and hides it or shows it again; a lecture's course bar does the same for its whole
   course. Loaded before app.js: it only defines things, and uses app.js's helpers (S, SET, api, $, $$, esc,
   when, toast, ICON, brandTile, renderSettings) and youtube.js's YT_ICON when they run. */
"use strict";

const AI_ICON = {
  eye: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>',
  eyeOff: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.6 5.1A10 10 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-2.4 3.2M6.6 6.6C3.7 8.4 2 12 2 12s3.6 7 10 7a9.6 9.6 0 0 0 5.4-1.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2M3 3l18 18"/></svg>',
};
// The two switches: a transcription follows one of them by its kind (app/assistants.py kind); and saving
const AI_SWITCH = { mcp_courses: "Courses and YouTube videos", mcp_recordings: "Meetings and other recordings",
  mcp_write: "Let assistants save new versions of transcripts" };
const AI_SAVE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>';
// What GET /api/assistants answered: {settings, hidden, log, snippets}. Fetched each time the tab opens.
const AI = { data: null, error: "", loading: false };

// ---------------------------------------------------------------------------------------------
// Settings → Assistants
// ---------------------------------------------------------------------------------------------
function assistantsPanel() {
  const set = S.status.settings || {}, d = AI.data;
  const sw = (key, hint, on = set[key] !== false) => `<label class="set-row">
      <div class="set-text"><b>${esc(AI_SWITCH[key])}</b><span class="hint">${esc(hint)}</span></div>
      <input type="checkbox" class="switch" data-setting="${key}" ${on ? "checked" : ""}></label>`;
  const rest = d ? aiHiddenSection(d.hidden) + aiConnectSection(d.snippets) + aiLogSection(d.log)
    : `<p class="hint">${AI.error ? esc(`Couldn't load the rest: ${AI.error}`) : "Loading…"}</p>`;
  return `<h3>Assistants</h3>
    <p class="hint ai-intro">Assistants such as Claude Desktop, Claude Code, Cursor and VS Code can read and search these
      transcripts through MCP, also while Sedjem is closed. What an assistant reads goes to its model, like anything
      else shared in a chat with it. Assistants only read, unless saving is turned on below; starting transcriptions comes later.</p>
    <section class="ai-sec" aria-labelledby="ai-see"><h4 id="ai-see">What assistants can see</h4>
      <div class="set-list">${sw("mcp_courses", "Lectures of YouTube playlists, and YouTube videos added on their own.")}
        ${sw("mcp_recordings", "Every other transcription: meetings, calls and other files.")}</div></section>
    <section class="ai-sec" aria-labelledby="ai-change"><h4 id="ai-change">What assistants can change</h4>
      <div class="set-list">${sw("mcp_write", "Off unless you turn it on. An assistant then saves a corrected version when you ask it to. A save never deletes anything: it is a new version, and History can undo it.", set.mcp_write === true)}</div></section>
    ${rest}`;
}

function aiHiddenSection(hidden) {
  const rows = hidden.map((h) => `<div class="set-item"><div class="set-item-row">
      ${brandTile(null, h.kind === "course" ? YT_ICON.course : ICON.wave, true)}
      <div class="set-item-text">${h.title ? `<b><bdi>${esc(h.title)}</bdi></b>` : `<b><code class="mono">${esc(h.key)}</code></b>`}
        <span>${esc(h.detail || "")}</span></div>
      <div class="set-item-actions"><button type="button" class="btn btn-sm" data-ai-show="${esc(h.key)}">Show again</button></div>
    </div></div>`).join("");
  return `<section class="ai-sec" aria-labelledby="ai-hidden"><h4 id="ai-hidden" tabindex="-1">Hidden from assistants</h4>
    ${rows ? `<div class="set-items ai-hidden">${rows}</div>`
      : '<p class="ai-empty">Nothing is hidden. A transcription\'s page can hide it from assistants, and a lecture\'s course bar its whole course.</p>'}</section>`;
}

function aiConnectSection(snippets) {
  const blocks = snippets.map((s, i) => `<div class="ai-snippet">
      <div class="ai-snippet-head"><div class="set-text"><b>${esc(s.client)}</b>
        <span class="hint">${s.kind === "command" ? "Run it in" : "Add it to"} ${esc(s.where)}</span></div>
        <button type="button" class="btn btn-sm" data-ai-copy="${i}" aria-label="Copy the setting for ${esc(s.client)}">${ICON.copy} Copy</button></div>
      <pre class="ai-code" tabindex="0" aria-label="The setting for ${esc(s.client)}"><code>${esc(s.text)}</code></pre></div>`).join("");
  return `<section class="ai-sec" aria-labelledby="ai-connect"><h4 id="ai-connect">Connect an assistant</h4>
    <p class="hint ai-lead">Add the setting for your assistant, then restart it; where a file already lists other servers,
      sedjem goes next to them. Sedjem doesn't need to be running for assistants to read.</p>
    <div class="ai-snippets">${blocks}</div></section>`;
}

function aiLogSection(log) {
  const items = log.map((e) => {
    const names = (e.titles || []).map((t) => t ?? "a deleted transcription");
    const shown = names.slice(0, 3), more = names.length - shown.length;
    const titles = names.length ? `<span class="ai-titles">${shown.map((t) => `<bdi>${esc(t)}</bdi>`).join(", ")}${more ? ` and ${more} more` : ""}</span>` : "";
    // a version an assistant saved stands out: that it was a save, the new version's number, and the transcript
    const saved = e.saved ? `<span class="pill ai-saved-mark">${AI_SAVE_ICON} Saved version ${esc(e.version)}</span> ` : "";
    return `<li${e.saved ? ' class="ai-saved"' : ""}><time datetime="${esc(e.time)}">${esc(when(e.time))}</time>
      <div class="ai-entry">${saved}<b>${esc(e.client || "An assistant")}</b> <span class="ai-what">${esc(e.text)}</span> ${titles}</div></li>`;
  }).join("");
  return `<section class="ai-sec" aria-labelledby="ai-log">
    <div class="ai-sec-head"><h4 id="ai-log" tabindex="-1">What assistants asked</h4>
      <button type="button" class="btn btn-sm btn-ghost btn-danger" id="aiClear" ${log.length ? "" : "disabled"}>Clear</button></div>
    ${items ? `<ol class="ai-log">${items}</ol>` : '<p class="ai-empty">Nothing yet. What assistants read and search shows here, the newest first.</p>'}</section>`;
}

// A button busy with its request: aria-disabled rather than disabled, which would take the focus away from it
async function aiBusy(b, request) {
  if (b.getAttribute("aria-disabled") === "true") return false;
  b.setAttribute("aria-disabled", "true");
  const ok = await request();
  if (!ok) b.removeAttribute("aria-disabled");
  return ok;
}

function bindAssistants() {
  $$("#settings [data-ai-show]").forEach((b, i) => (b.onclick = async () => {
    if (!(await aiBusy(b, () => aiSetHidden(b.dataset.aiShow, false)))) return;
    const left = $$("#settings [data-ai-show]");  // the focus goes on to the next one, or to the heading
    (left[Math.min(i, left.length - 1)] || $("#ai-hidden"))?.focus();
  }));
  $$("#settings [data-ai-copy]").forEach((b) => (b.onclick = () => aiCopy(AI.data?.snippets[+b.dataset.aiCopy])));
  const clear = $("#aiClear");
  if (clear) clear.onclick = aiClearLog;
}

// The switches, what is hidden and what assistants asked, as they are now; each time the tab opens
async function loadAssistants() {
  if (AI.loading) return;
  AI.loading = true;
  try { AI.data = await api.get("/api/assistants"); AI.error = ""; }
  catch (e) { AI.error = e.message; }
  finally { AI.loading = false; }
  if ($("#settings").open && SET.tab === "assistants") renderSettings();
}

// The clipboard where the browser allows it, else a text box out of sight and the copy command. The box goes
// inside the open dialog: everything outside a modal dialog is inert and can't be selected.
async function aiCopy(s) {
  if (!s) return;
  try {
    try { await navigator.clipboard.writeText(s.text); }
    catch {
      const back = document.activeElement, box = document.createElement("textarea");
      box.value = s.text;
      box.className = "ai-copy-box";
      box.setAttribute("readonly", "");
      box.setAttribute("aria-hidden", "true");
      ($("#settings[open]") || document.body).append(box);
      box.select();
      const copied = document.execCommand("copy");
      box.remove();
      back?.focus();
      if (!copied) throw new Error("the browser doesn't allow it here; select the text and copy it");
    }
    toast(`Copied for ${s.client}`);
  } catch (e) { toast(`Could not copy: ${e.message}`, "error"); }
}

async function aiClearLog() {
  if (!confirm("Clear the list of what assistants asked?")) return;
  try {
    AI.data = await api.post("/api/assistants/log/clear");
    renderSettings();
    $("#ai-log")?.focus();
    toast("Cleared");
  } catch (e) { toast(e.message, "error"); }
}

// Hides a transcription ("job:<id>") or a course ("course:<playlist id>") from assistants, or shows it again.
// Settings and the job page follow at once. Returns whether it was saved.
async function aiSetHidden(key, hide) {
  try { AI.data = await api.post("/api/assistants/hide", { key, hidden: hide }); }
  catch (e) { toast(e.message, "error"); return false; }
  const keys = AI.data.hidden.map((h) => h.key);
  if (S.status?.settings) S.status.settings.mcp_hidden = keys;
  aiFollow(AI.data.settings, keys);
  if ($("#settings").open && SET.tab === "assistants") renderSettings();
  const course = key.startsWith("course:");
  toast(hide ? (course ? "Course hidden from assistants" : "Hidden from assistants")
    : (course ? "Course shown to assistants" : "Shown to assistants"));
  return true;
}

// ---------------------------------------------------------------------------------------------
// A transcription's page: the Assistants panel, and the button in a lecture's course bar (youtube.js)
// ---------------------------------------------------------------------------------------------
function aiButton(id, key, hide, label) {
  return `<button type="button" class="btn btn-sm" id="${id}" data-ai-key="${esc(key)}" data-ai-hide="${hide}">${hide ? AI_ICON.eyeOff : AI_ICON.eye} ${label}</button>`;
}

// S.ai: what GET /api/jobs/<id> said (app/server.py job_assistants); reason says why it is hidden
function aiPanel() {
  const a = S.ai;
  if (!a) return "";
  const own = a.hidden ? aiButton("aiToggle", a.key, false, "Show to assistants") : aiButton("aiToggle", a.key, true, "Hide from assistants");
  let text, actions;
  if (a.reason === "switch") {
    text = `Hidden from assistants: ${AI_SWITCH[a.switch]} are switched off in Settings.`;
    actions = `<button type="button" class="btn btn-sm" data-open-settings="assistants">Open Settings</button>${a.hidden ? own : ""}`;
  } else if (a.reason === "course") {
    text = a.hidden ? "Hidden from assistants, with its course and on its own." : "Hidden from assistants with its course.";
    actions = aiButton("aiCourseShow", a.course_key, false, "Show course to assistants") + (a.hidden ? own : "");
  } else {
    text = a.reason === "hidden" ? "Hidden from assistants." : "Visible to assistants connected to Sedjem.";
    actions = own;
  }
  return `<div class="panel ai-panel" id="aiPanel"><h3>Assistants</h3>
    <p class="ai-state${a.visible ? "" : " off"}">${a.visible ? AI_ICON.eye : AI_ICON.eyeOff}<span>${esc(text)}</span></p>
    <div class="ai-actions">${actions}</div></div>`;
}

function aiCourseButton(j) {
  const a = S.ai;
  if (!a || !a.course_key || !j.course) return "";
  return a.course_hidden ? aiButton("aiCourse", a.course_key, false, "Show course to assistants")
    : aiButton("aiCourse", a.course_key, true, "Hide course from assistants");
}

function bindAi() {
  $$("#view [data-ai-key]").forEach((b) => (b.onclick = () => aiBusy(b, () => aiSetHidden(b.dataset.aiKey, b.dataset.aiHide === "true"))));
}

// The page's state after a change made on it or in Settings, worked out as the server does it: the switch for
// its kind first, then its course, then its own mark.
function aiFollow(settings, keys) {
  const a = S.ai;
  if (!a || S.route.name !== "job" || !settings) return;
  const hidden = new Set(keys || []);
  a.switch_on = settings[a.switch] !== false;
  a.hidden = hidden.has(a.key);
  a.course_hidden = !!a.course_key && hidden.has(a.course_key);
  a.hidden_key = a.course_hidden ? a.course_key : a.hidden ? a.key : null;
  a.reason = !a.switch_on ? "switch" : a.course_hidden ? "course" : a.hidden ? "hidden" : null;
  a.visible = !a.reason;
  const focused = document.activeElement?.id || "";
  const panel = $("#aiPanel"), course = $("#aiCourse");
  if (panel) panel.outerHTML = aiPanel();
  if (course && S.job) course.outerHTML = aiCourseButton(S.job);
  bindAi();
  if (/^ai/.test(focused)) ($(`#${focused}`) || $("#aiToggle"))?.focus();  // the button that took its place
}
