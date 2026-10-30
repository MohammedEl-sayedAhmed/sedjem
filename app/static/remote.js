/* Sedjem: running jobs on another computer on the same network (the server side is app/remote.py). Settings → Other
   computers pairs this computer with another, lists the paired ones, and has the switch that lets other computers send
   jobs here, with its one-time pairing code and the computers allowed. The New transcription form gets a choice of
   where to run, the models installed on the computer picked (asked from it), and the tick asked the first time a
   recording goes to each computer. A job run elsewhere says where in its progress and its details, and one whose
   computer stopped answering offers to run here instead. Loaded before app.js: it only defines things, and uses
   app.js's helpers (S, SET, api, $, $$, esc, when, human, bytes, clock, toast, ICON, model, renderSettings, renderNew,
   refreshStatus, refreshJobs, closeMenus) when they run. */
"use strict";

// GET /api/remote's answer, fetched each time the tab opens; the pairing form as typed; and what each paired computer
// said about itself when the form asked (id -> {loading} or GET /api/remote/computers/<id>'s answer)
const RM = { data: null, error: "", loading: false, timer: null, address: "", code: "", pairing: false, asked: {} };

// ---------------------------------------------------------------------------------------------
// Settings → Other computers
// ---------------------------------------------------------------------------------------------
function remotePanel() {
  const d = RM.data, on = S.status.settings?.remote_serve === true;
  const rest = d ? rmSendSection(d.computers) + rmServeSection(d.serve, on)
    : `<p class="hint">${RM.error ? esc(`Couldn't load the rest: ${RM.error}`) : "Loading…"}</p>`;
  return `<h3>Other computers</h3>
    <p class="hint rm-intro">Transcribe on another computer of yours on the same network, such as one with an NVIDIA
      graphics card. The recording travels only over your local network, encrypted, to the computer you paired with,
      which transcribes it with its own models and gives the transcript back. Model downloads stay on the computer that
      runs them.</p>
    ${rest}`;
}

function rmSendSection(computers) {
  const rows = computers.map((c) => `<div class="set-item"><div class="set-item-row">
      <span class="rm-ico">${ICON.network}</span>
      <div class="set-item-text"><b><bdi>${esc(c.name)}</bdi></b>
        <span>${esc(c.address)} · paired ${esc(when(c.paired))}${c.consented ? "" : " · asks before the first recording"}</span></div>
      <div class="set-item-actions"><button type="button" class="btn btn-sm btn-ghost btn-danger" data-rm-forget="${esc(c.id)}" aria-label="Remove ${esc(c.name)}">Remove</button></div>
    </div></div>`).join("");
  return `<section class="rm-sec" aria-labelledby="rm-send"><h4 id="rm-send" tabindex="-1">Send jobs to another computer</h4>
    ${rows ? `<div class="set-items rm-computers">${rows}</div>`
      : '<p class="rm-empty">No computer is paired yet. On the other computer, turn on Run jobs for other computers and show a pairing code there; then type its address and the code here.</p>'}
    <form class="rm-pair" id="rmPair" autocomplete="off">
      <label class="rm-field"><span>Address of the other computer</span>
        <input type="text" id="rmAddress" value="${esc(RM.address)}" placeholder="192.168.1.20 or a Tailscale name" spellcheck="false" required></label>
      <label class="rm-field rm-code-field"><span>Pairing code</span>
        <input type="text" id="rmCode" value="${esc(RM.code)}" placeholder="7KQ2-M9XD" maxlength="12" spellcheck="false" autocapitalize="characters" required></label>
      <button type="submit" class="btn btn-primary" id="rmPairBtn" ${RM.pairing ? 'aria-disabled="true"' : ""}>${RM.pairing ? "Pairing…" : "Pair"}</button>
    </form></section>`;
}

function rmServeSection(serve, on) {
  const switchRow = `<div class="set-list"><label class="set-row">
      <div class="set-text"><b>Run jobs for other computers</b><span class="hint">Off unless you turn it on. Computers
        you pair with can then send recordings here, to be transcribed with this computer's models and graphics card.
        Sedjem listens on port ${esc(serve.port)} and answers only private addresses: home and office networks, and
        Tailscale (100.64.0.0/10). Each recording is deleted here once its transcript has been collected, or after a day.</span></div>
      <input type="checkbox" class="switch" data-setting="remote_serve" ${on ? "checked" : ""}></label></div>`;
  if (!on) return `<section class="rm-sec" aria-labelledby="rm-serve"><h4 id="rm-serve">Run jobs for other computers</h4>${switchRow}</section>`;
  const where = serve.addresses.length
    ? `On the other computer, type ${serve.addresses.length > 1 ? "one of its addresses" : "its address"}: ${serve.addresses.map((a) => `<code class="mono">${esc(a)}</code>`).join(" ")}`
    : "No address on a private network was found: check that it is connected to one";
  const code = serve.code ? `<div class="rm-code" role="status">
      <div class="rm-code-text" id="rmCodeText" aria-label="Pairing code ${esc(serve.code.code.split("").join(" "))}">${esc(serve.code.code)}</div>
      <div class="set-text"><span class="hint">Type it on the other computer, in Settings, Other computers, with this computer's
        address. It works once, for <span id="rmCodeLeft">${esc(rmLeft(serve.code.seconds))}</span>.</span></div>
      <button type="button" class="btn btn-sm" id="rmCloseCode">Close the code</button></div>`
    : `<button type="button" class="btn" id="rmShowCode">${ICON.key} Show a pairing code</button>`;
  const clients = serve.clients.map((c) => `<div class="set-item"><div class="set-item-row">
      <span class="rm-ico">${ICON.laptop}</span>
      <div class="set-item-text"><b><bdi>${esc(c.name)}</bdi></b>
        <span>Paired ${esc(when(c.paired))}${c.used ? ` · last sent a recording ${esc(when(c.used))}` : ""}${c.active ? ` · ${c.active} job${c.active === 1 ? "" : "s"} running here now` : ""}</span></div>
      <div class="set-item-actions"><button type="button" class="btn btn-sm btn-ghost btn-danger" data-rm-revoke="${esc(c.id)}" aria-label="Remove ${esc(c.name)}">Remove</button></div>
    </div></div>`).join("");
  return `<section class="rm-sec" aria-labelledby="rm-serve"><h4 id="rm-serve">Run jobs for other computers</h4>${switchRow}
    ${serve.error ? `<p class="rm-error" role="alert">${ICON.info} ${esc(serve.error)}</p>` : `
    <p class="hint rm-here">This computer is <b><bdi>${esc(serve.name)}</bdi></b>. ${where}</p>
    <div class="rm-code-box">${code}</div>
    ${serve.fingerprint ? `<p class="hint rm-print">Its certificate, which the other computer checks every time: <code class="mono">${esc(serve.fingerprint)}</code></p>` : ""}`}
    <h4 class="rm-sub">Computers that can send jobs here</h4>
    ${clients ? `<div class="set-items rm-clients">${clients}</div>` : '<p class="rm-empty">None yet. Show a pairing code to add one.</p>'}
  </section>`;
}

const rmLeft = (s) => (s >= 90 ? `${Math.round(s / 60)} minutes` : s > 0 ? `${s} seconds` : "a moment");

function bindRemote() {
  const form = $("#rmPair");
  if (form) {
    $("#rmAddress").oninput = (e) => (RM.address = e.target.value);
    $("#rmCode").oninput = (e) => (RM.code = e.target.value);
    form.onsubmit = (e) => { e.preventDefault(); rmPair(); };
  }
  const show = $("#rmShowCode");
  if (show) show.onclick = () => rmCall("POST", "/api/remote/code", "#rmCloseCode");
  const close = $("#rmCloseCode");
  if (close) close.onclick = () => rmCall("DELETE", "/api/remote/code", "#rmShowCode");
  $$("#settings [data-rm-revoke]").forEach((b) => (b.onclick = () => {
    const name = b.closest(".set-item").querySelector("b").textContent;
    if (confirm(`Stop ${name} from sending jobs here? Its jobs here stop, and it has to pair again to send more.`))
      rmCall("DELETE", `/api/remote/clients/${b.dataset.rmRevoke}`, "#rm-serve");
  }));
  $$("#settings [data-rm-forget]").forEach((b) => (b.onclick = async () => {
    const name = b.closest(".set-item").querySelector("b").textContent;
    if (!confirm(`Unpair from ${name}? This computer stops sending it jobs, and it is told to forget this one.`)) return;
    if (await rmCall("DELETE", `/api/remote/computers/${b.dataset.rmForget}`, "#rm-send")) {
      delete RM.asked[b.dataset.rmForget];
      if (S.form.runOn === b.dataset.rmForget) Object.assign(S.form, { runOn: null, model: null, confirm: false });
      await refreshStatus();
      if (S.route.name === "new") renderNew();
    }
  }));
  rmTick();
}

// The code's time left, counted down while it shows; the tab is drawn again when it runs out
function rmTick() {
  clearInterval(RM.timer);
  const code = RM.data?.serve?.code;
  if (!code || !$("#rmCodeLeft")) return;
  const end = Date.now() + code.seconds * 1000;
  RM.timer = setInterval(() => {
    const left = Math.round((end - Date.now()) / 1000), el = $("#rmCodeLeft");
    if (!el || !$("#settings").open) return clearInterval(RM.timer);
    if (left <= 0) { clearInterval(RM.timer); loadRemote(); return; }
    el.textContent = rmLeft(left);
  }, 1000);
}

// A change on the tab: the answer is the tab's new state; the focus goes to focusAfter, as the button it was on is gone
async function rmCall(method, path, focusAfter) {
  try {
    RM.data = await api.req(method, path);
    RM.error = "";
  } catch (e) { toast(e.message, "error"); return false; }
  if ($("#settings").open && SET.tab === "remote") { renderSettings(); $(`#settings ${focusAfter}`)?.focus(); }
  return true;
}

async function rmPair() {
  if (RM.pairing) return;
  RM.pairing = true;
  renderSettings();
  try {
    RM.data = await api.post("/api/remote/pair", { address: RM.address.trim(), code: RM.code.trim() });
    const c = RM.data.computers[RM.data.computers.length - 1];
    Object.assign(RM, { address: "", code: "" });
    if (c) delete RM.asked[c.id];  // asked again when it is picked
    await refreshStatus();
    if (S.route.name === "new") renderNew();
    toast(`Paired with ${c?.name || "the other computer"}`);
  } catch (e) { toast(e.message, "error"); }
  finally { RM.pairing = false; }
  if ($("#settings").open && SET.tab === "remote") { renderSettings(); $("#rmAddress")?.focus(); }
}

// Both sides as they are now; each time the tab opens, and after its switch changes
async function loadRemote() {
  if (RM.loading) return;
  RM.loading = true;
  try { RM.data = await api.get("/api/remote"); RM.error = ""; }
  catch (e) { RM.error = e.message; }
  finally { RM.loading = false; }
  if ($("#settings").open && SET.tab === "remote") renderSettings();
}

// ---------------------------------------------------------------------------------------------
// New transcription: where it runs
// ---------------------------------------------------------------------------------------------
const rmComputers = () => S.status?.remote?.computers || [];
// The paired computer picked on the form, while it is still paired (null: this computer)
const rmChoice = () => (S.form.runOn && rmComputers().find((c) => c.id === S.form.runOn)) || null;

// A model as the paired computer listed it, shaped like this computer's models for the form
function rmModel(c, id) {
  const m = (RM.asked[c.id]?.models || []).find((x) => x.id === id);
  return m ? { ...m, kind: "remote", ready: true, computer: c } : null;
}

// The choice of where to run, above the models, once a computer is paired
function rmWhere() {
  const list = rmComputers();
  if (!list.length) return "";
  const rc = rmChoice();
  return `<div class="field rm-where-field"><span class="field-label" id="rmWhereLabel">Run on</span>
    <div class="segmented rm-where" role="group" aria-labelledby="rmWhereLabel">
      <button type="button" data-run-on="" aria-pressed="${!rc}">${ICON.laptop} This computer</button>
      ${list.map((c) => `<button type="button" data-run-on="${esc(c.id)}" aria-pressed="${rc?.id === c.id}">${ICON.network}<bdi>${esc(c.name)}</bdi></button>`).join("")}
    </div></div>`;
}

// The models installed on the paired computer, asked from it when it is picked
function rmGrid(c) {
  const a = RM.asked[c.id];
  if (!a || a.loading) {
    if (!a) rmAsk(c.id);
    return `<p class="rm-empty" role="status">Asking <bdi>${esc(c.name)}</bdi> which models it has…</p>`;
  }
  if (!a.online) {
    const why = a.unreachable ? `<b><bdi>${esc(c.name)}</bdi> isn't answering.</b> It may be off or asleep, or Sedjem isn't
      running there with Run jobs for other computers on. A job sent now runs on this computer instead, if it has the model.`
      : `<b>Couldn't use <bdi>${esc(c.name)}</bdi>:</b> ${esc(a.error || "it gave no answer")}.`;
    return `<div class="rm-empty rm-off" role="status"><p>${why}</p>
      <button type="button" class="btn btn-sm" data-rm-ask="${esc(c.id)}">${ICON.redo} Ask again</button></div>`;
  }
  if (!a.models.length) return `<p class="rm-empty"><bdi>${esc(c.name)}</bdi> has no models installed yet. Download one there, in Settings, Models.</p>`;
  return a.models.map((m) => rmModelCard(rmModel(c, m.id), c)).join("");
}

function rmModelCard(m, c) {
  const sel = S.form.model === m.id;
  return `<div class="model-card" role="radio" tabindex="0" aria-checked="${sel}" data-rm-model="${esc(m.id)}">
    <div class="mc-badges"><span class="pill accent">${ICON.network}<span>On <bdi>${esc(c.name)}</bdi></span></span>${m.hub ? '<span class="pill">From Hugging Face</span>' : ""}</div>
    <div class="mc-head">${modelTile(m)}<div class="mc-title">${esc(m.title)}</div></div>
    <div class="mc-tag">${esc(m.tagline || "")}</div>
    <ul>${(m.facts || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>
    <div class="mc-foot">${rmEstimate(m, formSeconds(), true)}</div>
  </div>`;
}

// How long it should take there: its own measure of the model's speed
function rmEstimate(m, seconds, short = false) {
  const c = m.computer, sp = m.speed || {}, a = RM.asked[c.id] || {};
  const where = `${sp.runs_on === "gpu" ? `on ${esc(c.name)}'s graphics card` : `on ${esc(c.name)}`}`;
  const busy = a.busy ? ` It has ${a.busy} job${a.busy === 1 ? "" : "s"} to finish first.` : "";
  if (!sp.rtf) return `Runs ${where}${short ? "" : `, plus sending the recording.${busy}`}`;
  if (!seconds) return `About <b>${sp.rtf}×</b> the recording's length ${where}${short ? "" : `.${busy}`}`;
  return `About <b>${human(seconds * sp.rtf)}</b> ${where}${short ? "" : `${sp.measured ? " (measured on its earlier runs)" : ""}, plus sending the recording.${busy}`}`;
}

// What the paired computer's speaker labels will be short of
function rmSpeakerLimits(m, speakers) {
  const a = RM.asked[m.computer.id] || {};
  return speakers !== "none" && a.speakers && !a.speakers.ready
    ? ` <b>${esc(m.computer.name)} has no speaker model downloaded, so it will skip speaker labels.</b>` : "";
}

// The tick the first time a recording goes to a computer; after that, the same words without it
function rmConsent(c) {
  const f = S.form;
  const text = `<b>The recording will be sent to <bdi>${esc(c.name)}</bdi> on your network.</b> It travels only over your
    local network, encrypted, to the computer you paired with, which transcribes it with its own models. It is deleted
    there once the transcript is back here, or after a day.`;
  return `<div class="card consent rm-consent">${ICON.network}<div><p>${text}</p>
    ${c.consented ? "" : `<label><input type="checkbox" id="confirmSend" ${f.confirm ? "checked" : ""}> Send recordings to <bdi>${esc(c.name)}</bdi></label>`}
  </div></div>`;
}

async function rmAsk(id) {
  RM.asked[id] = { loading: true };
  try { RM.asked[id] = await api.get(`/api/remote/computers/${id}`); }
  catch (e) { RM.asked[id] = { online: false, error: e.message }; }
  const c = rmChoice(), f = S.form;
  if (c?.id === id && RM.asked[id].online && !rmModel(c, f.model)) f.model = RM.asked[id].models[0]?.id || null;
  if (S.route.name === "new") renderNew();
}

function bindRemoteForm() {
  const f = S.form;
  $$("[data-run-on]").forEach((b) => (b.onclick = () => {
    const id = b.dataset.runOn || null;
    if (id === (f.runOn || null)) return;
    Object.assign(f, { runOn: id, confirm: false, model: null });
    if (id && RM.asked[id]?.online) f.model = RM.asked[id].models[0]?.id || null;
    else if (id && !RM.asked[id]?.loading) delete RM.asked[id];  // asked again: it may have come on since
    renderNew();
  }));
  $$("[data-rm-ask]").forEach((b) => (b.onclick = () => rmAsk(b.dataset.rmAsk)));
  $$(".model-card[data-rm-model]").forEach((card) => {
    const pick = () => { if (f.model !== card.dataset.rmModel) { f.model = card.dataset.rmModel; renderNew(); } };
    card.onclick = pick;
    card.onkeydown = (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), pick());
  });
  const cs = $("#confirmSend");
  if (cs) cs.onchange = () => { f.confirm = cs.checked; renderNew(); };
}

// ---------------------------------------------------------------------------------------------
// A job run on a paired computer: its progress, and what to do when that computer stopped answering
// ---------------------------------------------------------------------------------------------
const RM_STAGES = {
  starting: "Asking {} to take it", sending: "Sending the recording to {}", "remote-preparing": "{} is preparing the audio",
  "remote-queued": "Waiting for {} to finish its previous job", decoding: "{} is reading the audio",
  loading: "{} is loading the model", speakers: "{} is telling speakers apart by voice", transcribing: "Transcribing on {}",
  collecting: "Fetching the transcript from {}", reconnecting: "{} isn't answering; trying again",
};
const rmName = (j) => j.remote?.name || "the other computer";
const rmSent = (j) => j.kind === "remote" && j.remote && !j.remote.fallback;

function rmStage(j) {
  if (!rmSent(j) || !["queued", "running"].includes(j.status)) return null;
  const name = rmName(j);
  if (j.status === "queued") return { label: `Waiting to send to ${name}`, pct: null };
  const label = (RM_STAGES[j.stage] || "Working on {}").replace("{}", name);
  if (j.stage === "sending" && j.total) return { label, pct: (j.done || 0) / j.total * 100, detail: `${bytes(j.done || 0)} of ${bytes(j.total)}` };
  if (j.stage === "transcribing" && j.total) return { label, pct: (j.done || 0) / j.total * 100, detail: `part ${j.done} of ${j.total}` };
  if (j.stage === "remote-preparing" && j.total) return { label, pct: Math.min(100, (j.done || 0) / j.total * 100), detail: `${Math.round(Math.min(100, (j.done || 0) / j.total * 100))}%` };
  return { label, pct: null };
}

const rmNote = (j) => `Running on ${esc(rmName(j))}, on your network. You can leave this page; it keeps going while the app is open.`;

// The failed card's buttons for a job sent to another computer: again there, and here instead when it stopped
// answering (with the same model, when this computer has it)
function rmFailedActions(j) {
  const here = model(j.model)?.ready;
  const again = rmComputers().some((c) => c.id === j.remote.id)
    ? `<button class="btn btn-sm" data-rm-rerun="${esc(j.remote.id)}">${ICON.redo}<span>Try again on <bdi>${esc(rmName(j))}</bdi></span></button>` : "";
  const instead = here ? `<button class="btn btn-sm${j.remote_lost ? " btn-primary" : ""}" data-rerun="${esc(j.model)}">${ICON.laptop} Run here instead</button>`
    : j.remote_lost ? `<span class="hint">${esc(j.model_title || j.model)} isn't on this computer: download it in Settings, or pick another model under Run again with.</span>` : "";
  return `<div class="rm-actions">${j.remote_lost ? instead + again : again + instead}</div>`;
}

function bindRemoteJob() {
  $$("[data-rm-rerun]").forEach((b) => (b.onclick = async () => {
    try {
      const r = await api.post(`/api/jobs/${S.job.id}/rerun`, { model: S.job.model, run_on: b.dataset.rmRerun });
      await refreshJobs();
      location.hash = `#/job/${r.id}`;
    } catch (e) { toast(e.message, "error"); }
  }));
}
