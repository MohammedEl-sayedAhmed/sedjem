/* Sedjem: updates (the server side is app/updates.py). On first start a question: may Sedjem ask GitHub once a
   day whether a newer version is out. When one is out (or, on the Latest channel, newer tested changes on main),
   a pill in the top bar and a notice, once, lead to Settings → Updates: the running version, the automatic check
   and the channel, what's new (the release notes, or the list of changes), and Update now with its progress.
   While Sedjem restarts, the page waits for it and reloads. Loaded before app.js: it only defines things, and
   uses app.js's helpers (S, SET, ACTIVE, api, $, $$, esc, when, bytes, toast, raiseToasts, ICON, setStatus,
   renderSettings, openSettings, refreshStatus) when they run. */
"use strict";

// data: what GET /api/updates (or a POST) answered last; the rest is what this page is doing with it
const UPD = { data: null, error: "", loading: null, checking: false, installing: false, asked: false, seen: null,
  started: false, timer: null, restart: null, sig: "" };
const UPD_NOTICED = "sedjem-update-noticed";  // the version or commit the notice was shown for (localStorage)
const UPD_FROM = "sedjem-update-from";  // what ran before Update now, to say "Updated to …" once it is back
const UPD_BUSY = new Set(["downloading", "verifying", "installing"]);
const UPD_RESTART_LIMIT = 120000;  // how long the page waits for Sedjem to come back
const UPD_KIND = { source: "Runs from the source code", windows: "Installed with the Windows installer",
  linux: "The Linux build, unpacked from its archive", other: "Another kind of install" };

function updGet(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}
function updPut(key, value) {
  try { if (value == null) localStorage.removeItem(key); else localStorage.setItem(key, value); } catch { /* private mode */ }
}
const updShort = (sha) => String(sha || "").slice(0, 7);
const updHref = (url) => (/^https:\/\//i.test(String(url || "")) ? String(url) : "");  // a link only to an https address
const updLink = (href, html, attrs = "") => `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer"${attrs}>${html}</a>`;
const updCap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : "");
// A release is known by its version, the Latest channel's changes by their newest commit
const updKey = (a) => (a.channel === "latest" ? a.commit || a.version : a.version || a.commit) || "";
const updTitle = (a) => a.title || (a.channel === "latest" ? `${a.ahead || 0} new change${a.ahead === 1 ? "" : "s"}` : `Sedjem ${a.version}`);
// What is out on the chosen channel (one found on the other channel, before a switch, doesn't count)
const updAvailable = (d) => (d?.available && (!d.available.channel || d.available.channel === d.channel) ? d.available : null);
const updTabOpen = () => $("#settings").open && SET.tab === "updates";

function updDay(iso) {
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });
}
// "2 hours ago", for "Checked 2 hours ago"
function updAgo(iso) {
  const s = (Date.now() - Date.parse(iso)) / 1000;
  if (!isFinite(s)) return "";
  const n = (v, unit) => `${v} ${unit}${v === 1 ? "" : "s"} ago`;
  if (s < 60) return "just now";
  if (s < 3600) return n(Math.floor(s / 60), "minute");
  if (s < 86400) return n(Math.floor(s / 3600), "hour");
  return n(Math.floor(s / 86400), "day");
}

// ---------------------------------------------------------------------------------------------
// The top bar, the first-start question and the notice
// ---------------------------------------------------------------------------------------------
// The pill (app.js renderTop): what /api/status says is out; a click opens Settings → Updates
function updPill() {
  const u = S.status?.update;
  if (!u?.available || !u.label) return "";
  return `<button class="pill accent" data-open-settings="updates" title="See what's new">${ICON.update} ${esc(u.label)}</button>`;
}

// After each status check (app.js refreshStatus): the first-start question while it is due, else the notice
// once for something new. Neither opens over another dialog; the next check after it is closed tries again.
function updStatus() {
  if (!UPD.started) { UPD.started = true; updAfterRestart(); }
  const u = S.status?.update;
  if (!u || UPD.restart) return;
  if (u.ask && !UPD.asked) { updAsk(); return; }
  if (!u.available) { UPD.seen = null; return; }
  if (UPD.seen === u.label || $("dialog[open]")) return;
  UPD.seen = u.label;
  loadUpdates().then(() => {
    const a = updAvailable(UPD.data);
    if (a && !$("dialog[open]")) updNotice(a);
  });
}

// Answered, the question doesn't come back (Settings → Updates changes the answer); put away with Escape, it
// is asked again at the next start.
function updAsk() {
  let dlg = $("#updAsk");
  if (dlg?.open || $("dialog[open]")) return;
  if (!dlg) {
    document.body.insertAdjacentHTML("beforeend", `<dialog id="updAsk" class="upd-ask" aria-labelledby="updAskTitle" aria-describedby="updAskText">
      <div class="dlg-head"><h2 id="updAskTitle">Check for updates automatically?</h2></div>
      <div class="dlg-body"><p id="updAskText">Once a day, Sedjem can ask GitHub whether a newer version is out. GitHub sees
        this computer's IP address; nothing else is sent. You can change this in Settings → Updates, where Check now
        always works.</p></div>
      <div class="dlg-foot"><button type="button" class="btn" id="updAskNo">No</button>
        <button type="button" class="btn btn-primary" id="updAskYes">Yes, check daily</button></div></dialog>`);
    dlg = $("#updAsk");
    $("#updAskYes").onclick = () => updAnswer(true);
    $("#updAskNo").onclick = () => updAnswer(false);
    dlg.addEventListener("cancel", () => (UPD.asked = true));
  }
  dlg.showModal();
}

async function updAnswer(on) {
  const b = $(on ? "#updAskYes" : "#updAskNo");
  if (b.getAttribute("aria-disabled") === "true") return;
  b.setAttribute("aria-disabled", "true");
  try { updSetData(await api.post("/api/updates/settings", { enabled: on })); }
  catch (e) { toast(e.message, "error"); b.removeAttribute("aria-disabled"); return; }
  UPD.asked = true;
  if (S.status?.update) S.status.update.ask = false;
  $("#updAsk").close();
  toast(on ? "Sedjem checks for updates once a day" : "No automatic checks. Settings → Updates can check now");
}

// The notice, once for each new version or commit (remembered in this browser), and not for a skipped one.
// It sits with the toasts and stays until it is answered.
function updNotice(a) {
  const key = updKey(a);
  if (!key || a.skipped || updGet(UPD_NOTICED) === key) return;
  updPut(UPD_NOTICED, key);
  const box = $("#toasts");
  $("#updNotice")?.remove();
  box.insertAdjacentHTML("beforeend", `<div class="toast upd-notice" id="updNotice">
    <div class="upd-notice-text">${ICON.update}<b>${esc(a.channel === "latest" ? `${updTitle(a)} on main` : `${updTitle(a)} is out`)}</b></div>
    <div class="upd-notice-actions"><button type="button" class="btn btn-sm btn-ghost" id="updLater">Later</button>
      <button type="button" class="btn btn-sm btn-primary" id="updWhatsNew">What's new</button></div></div>`);
  raiseToasts(box);
  $("#updWhatsNew").onclick = () => { updHideNotice(); openSettings("updates"); };
  $("#updLater").onclick = updHideNotice;
}

function updHideNotice() {
  const notice = $("#updNotice"), box = $("#toasts");
  if (!notice) return;
  notice.remove();
  if (!box.children.length && box.hidePopover) try { box.hidePopover(); } catch { /* already hidden */ }
}

// ---------------------------------------------------------------------------------------------
// The state, as the server has it
// ---------------------------------------------------------------------------------------------
// GET /api/updates: when Settings → Updates opens, when something new is out, and every second while an update
// is being installed. A server gone in the middle of an install is restarting.
function loadUpdates() {
  if (!UPD.loading) UPD.loading = (async () => {
    try { updSetData(await api.get("/api/updates")); UPD.error = ""; }
    catch (e) {
      UPD.error = e.message;
      if (UPD_BUSY.has(UPD.data?.install?.state)) updWaitForRestart();
    }
    UPD.loading = null;
    updRedraw();
  })();
  return UPD.loading;
}

// A new answer from the server: kept, and followed (the progress polled, the restart waited for). What happens
// to an update while Settings is closed shows as a toast.
function updSetData(d) {
  const was = UPD.data?.install?.state, now = d.install?.state;
  UPD.data = d;
  if (now === "failed") updPut(UPD_FROM, null);  // nothing changed, so no "Updated to" later
  if (was && now !== was && !updTabOpen()) {
    if (now === "failed") toast(`The update failed: ${d.install.error || "something went wrong"}`, "error");
    if (now === "done") toast(`${d.available?.version ? `Sedjem ${d.available.version}` : "The new version"} is unpacked${d.install.folder ? ` in ${d.install.folder}` : " beside this one"}`);
  }
  if (now === "restarting") updWaitForRestart();
  updSchedule();
}

function updSchedule() {
  clearTimeout(UPD.timer);
  UPD.timer = null;
  const d = UPD.data;
  if (!d || UPD.restart) return;
  const installing = UPD_BUSY.has(d.install?.state);
  if (installing || (d.checking && updTabOpen())) UPD.timer = setTimeout(loadUpdates, installing ? 1000 : 1500);
}

// Settings → Updates drawn again when it is open and something changed. When only the download's bytes moved,
// the bar and its text change in place, so the bar glides on.
function updRedraw(force) {
  if (!updTabOpen()) return;
  if (!force && UPD.sig === updSig()) {
    const i = UPD.data?.install, bar = $("#updProgress .progress i");
    if (bar && i) { bar.style.width = `${(updPct(i) || 0).toFixed(1)}%`; $("#updProgressText").textContent = updBytes(i); }
    return;
  }
  renderSettings();
}
// What the tab shows, but for the bytes downloaded so far
const updSig = () => JSON.stringify([UPD.data && { ...UPD.data, install: UPD.data.install && { ...UPD.data.install, done: 0 } },
  UPD.error, UPD.checking]);

// ---------------------------------------------------------------------------------------------
// Settings → Updates
// ---------------------------------------------------------------------------------------------
function updatesPanel() {
  const d = UPD.data;
  UPD.sig = updSig();
  if (!d) return `<h3>Updates</h3><p class="hint">${UPD.error ? esc(`Couldn't load the updates: ${UPD.error}`) : "Loading…"}</p>`;
  const a = updAvailable(d), checking = UPD.checking || d.checking;
  if (a && !a.skipped) { updPut(UPD_NOTICED, updKey(a)); updHideNotice(); }  // seen here: no notice for it later
  const channels = [["stable", "Stable releases"], ["latest", "Latest changes"]].map(([c, label]) =>
    `<button type="button" id="updChannel-${c}" data-upd-channel="${c}" class="${d.channel === c ? "on" : ""}" aria-pressed="${d.channel === c}">${label}</button>`).join("");
  return `<h3>Updates</h3>
    ${updRunning(d.running || {})}
    <div class="set-list">
      <label class="set-row">
        <div class="set-text"><b>Check for updates automatically</b><span class="hint">Once a day, Sedjem asks GitHub whether a
          newer version is out. GitHub sees this computer's IP address; nothing else is sent.</span></div>
        <input type="checkbox" class="switch" id="updAuto" ${d.enabled ? "checked" : ""}>
      </label>
      <div class="set-row">
        <div class="set-text"><b>Channel</b><span class="hint">${esc(updChannelHint(d.running?.kind))}</span></div>
        <div class="seg" role="group" aria-label="Channel">${channels}</div>
      </div>
      <div class="set-row">${updCheckText(d)}
        <button type="button" class="btn btn-sm" id="updCheck"${checking ? ' aria-disabled="true"' : ""}>${ICON.redo} ${checking ? "Checking…" : "Check now"}</button>
      </div>
    </div>
    <section class="upd-sec" aria-labelledby="upd-new"><h4 id="upd-new">What's new</h4>${updNew(d, a)}</section>`;
}

// The version running: its commit, a preview build's channel, and the kind of install in words
function updRunning(r) {
  const facts = [r.build_channel === "preview" ? "Preview build" : "",
    r.commit ? `commit <code class="mono" title="${esc(r.commit)}">${esc(updShort(r.commit))}</code>` : "",
    r.date ? esc(updDay(r.date)) : ""].filter(Boolean);
  return `<div class="about upd-running">
      <img src="/static/icon.svg" alt="" width="44" height="44">
      <div class="about-text"><b>Sedjem ${esc(r.version || S.status?.version || "")}</b>
        ${facts.length ? `<span>${facts.join(" · ")}</span>` : ""}
        <span>${esc(UPD_KIND[r.kind] || UPD_KIND.other)}</span></div>
    </div>`;
}

const updChannelHint = (kind) => `Stable releases come with their release notes. Latest changes are ${kind === "source"
  ? "the newest changes on main whose tests passed, before they are released" : "nightly preview builds of main, made only when its tests passed"}.`;

function updErrorText(e) {
  if (e === "offline") return "Couldn't check (offline)";
  if (e === "rate limited") return "Couldn't check (GitHub's hourly limit was reached; try again later)";
  return `Couldn't check: ${e}`;
}

// "Checked 2 hours ago", or why the last check didn't work; checked is the last check that did
function updCheckText(d) {
  const main = d.error ? updErrorText(d.error) : d.checked ? `Checked ${updAgo(d.checked)}` : "Not checked yet";
  const lines = [d.error && d.checked ? `Last checked ${updAgo(d.checked)}` : "", d.note || "",
    d.enabled ? "Sedjem checks once a day while it runs." : "Automatic checks are off. Check now asks GitHub once."];
  return `<div class="set-text"><b id="updWhen"${d.error ? ' class="upd-error"' : ""}${d.checked ? ` title="${esc(when(d.checked))}"` : ""}>${esc(main)}</b>
    ${lines.filter(Boolean).map((t) => `<span class="hint">${esc(t)}</span>`).join("")}</div>`;
}

function updNew(d, a) {
  if (!a) {
    const found = d.found ?? (d.available ? "update" : d.checked ? "up to date" : null);
    let text = "Not checked yet. Check now asks GitHub whether a newer version is out.", ok = false;
    if (found === "ahead" || found === "diverged") text = "Nothing newer to update to.";
    else if (found === "up to date") {
      ok = true;
      text = d.channel === "latest" ? "Sedjem is up to date with the newest tested changes on main."
        : `Sedjem is up to date: no release is newer than ${d.running?.version || "this one"}.`;
    } else if (d.error) text = "Nothing is known yet: the check didn't work.";
    return `<div class="upd-empty${ok ? " ok" : ""}">${ok ? ICON.check : ICON.info}<span>${esc(text)}</span></div>`;
  }
  const sub = a.channel === "latest"
    ? [a.version ? `build ${a.version}` : "", a.date ? `newest change ${when(a.date)}` : ""].filter(Boolean).join(" · ")
    : a.date ? `Released ${updDay(a.date)}` : "";
  const body = a.changes?.length && (a.channel === "latest" || !a.notes) ? updChanges(a)
    : a.notes ? `<div class="upd-notes">${updMarkdown(a.notes)}</div>` : '<p class="hint">No notes came with it.</p>';
  return `<div class="upd-card">
      <div class="upd-card-head"><div class="set-text"><b>${esc(updTitle(a))}</b>${sub ? `<span class="hint">${esc(updCap(sub))}</span>` : ""}</div>
        ${a.skipped ? setStatus("none", "Skipped") : ""}</div>
      ${updActions(d, a)}${updInstallBox(d, a)}${updBlocked(a)}${body}
    </div>`;
}

// Update now (unless it can't run here), Download (the release or compare page) and Skip this version
function updActions(d, a) {
  const st = d.install?.state;
  if (UPD_BUSY.has(st) || st === "restarting") return "";  // the progress shows instead
  const href = updHref(a.url);
  const now = a.action && a.action !== "download" && !a.blocked && st !== "failed" && st !== "done"
    ? `<button type="button" class="btn btn-sm btn-primary" id="updInstall">${ICON.download} Update now</button>` : "";
  return `<div class="upd-actions">${now}${href ? updLink(href, `${ICON.external} Download`, ' class="btn btn-sm" id="updDownload"') : ""}
    <button type="button" class="btn btn-sm btn-ghost" id="updSkip" data-skip="${!a.skipped}">${a.skipped ? "Don't skip" : "Skip this version"}</button></div>`;
}

const updPct = (i) => (i.state === "downloading" && i.total ? Math.min(100, (i.done || 0) / i.total * 100) : null);
const updBytes = (i) => (i.state !== "downloading" ? "" : i.total ? `${bytes(i.done || 0)} of ${bytes(i.total)}` : i.done ? bytes(i.done) : "");
function updStep(i, action) {
  if (i.state === "downloading") return action === "git" ? "Fetching the changes" : "Downloading";
  if (i.state === "verifying") return action === "git" ? "Checking the changes" : "Checking the download";
  if (i.state === "installing") return { git: "Updating the checkout", installer: "Running the installer", archive: "Unpacking" }[action] || "Installing";
  return i.state === "restarting" ? "Restarting" : updCap(i.state);
}

// Update now under way, failed (with Try again) or done (the Linux build, unpacked beside this one)
function updInstallBox(d, a) {
  const i = d.install;
  if (!i) return "";
  if (i.state === "failed") return `<div class="upd-failed" role="alert"><div class="set-text"><b>The update failed</b>
      <span>${esc(i.error || "Something went wrong.")}</span></div>
      <button type="button" class="btn btn-sm" id="updRetry">${ICON.redo} Try again</button></div>`;
  if (i.state === "done") {
    const what = esc(a.version ? `Sedjem ${a.version}` : "The new version");
    return `<div class="upd-done" role="status">${ICON.check}<span>${i.folder
      ? `${what} is unpacked in <code class="mono">${esc(i.folder)}</code>, which opened in the file manager. Quit this one and start the new one from there.`
      : `${what} is unpacked beside this one. Quit this one and start the new one from its folder.`}</span></div>`;
  }
  const pct = updPct(i);
  return `<div class="upd-progress" id="updProgress" role="status">
      <div class="upd-progress-row"><b>${esc(updStep(i, a.action))}</b><span id="updProgressText">${esc(updBytes(i))}</span></div>
      <div class="progress${pct == null ? " indeterminate" : ""}"><i style="width:${(pct || 0).toFixed(1)}%"></i></div></div>`;
}

// Why Update now can't run here, and the commands that do it by hand, each with its own Copy
function updBlocked(a) {
  if (!a.blocked) return "";
  const cmds = (a.commands || []).map((c, i) => `<div class="upd-cmd"><pre tabindex="0"><code>${esc(c)}</code></pre>
      <button type="button" class="btn btn-sm btn-ghost" data-upd-copy="${i}" aria-label="Copy: ${esc(c)}" title="Copy">${ICON.copy}<span>Copy</span></button></div>`).join("");
  return `<div class="upd-blocked">${ICON.info}<div class="upd-blocked-text"><b>${esc(a.blocked)}</b>
      ${cmds ? `<span>Update by hand with these commands instead:</span><div class="upd-cmds">${cmds}</div>` : ""}</div></div>`;
}

// The Latest channel: the changes on main, newest first, each with its date and a link to the commit
function updChanges(a) {
  const changes = a.changes || [];
  const rows = changes.map((c) => {
    const sha = `<code class="mono">${esc(updShort(c.sha))}</code>`, href = updHref(c.url);
    return `<li><span class="upd-change-title" dir="auto">${esc(c.title || "")}</span>
      <span class="upd-change-meta">${c.date ? `<time datetime="${esc(c.date)}">${esc(when(c.date))}</time>` : ""}${href ? updLink(href, sha, ` title="The change on GitHub"`) : sha}</span></li>`;
  }).join("");
  const more = (a.ahead || 0) - changes.length, href = updHref(a.url);
  return `<ol class="upd-changes">${rows}</ol>${more > 0 ? `<p class="upd-more">${href ? updLink(href, `and ${more} more`) : `and ${more} more`}</p>` : ""}`;
}

// Release notes are Markdown. Headings, lists, links, code, quotes, bold and italics are drawn; anything else
// shows as text. Every piece is escaped, only http(s) addresses become links (opened in a new tab), and an
// image stays a link: the page loads nothing from elsewhere.
function updMarkdown(md) {
  const out = [], lists = [];  // lists: the lists open around the line, the innermost last
  let para = [], item = null, gap = false;  // item: the lines of the list item being read; gap: a blank line before
  const endPara = () => { if (para.length) out.push(`<p>${updInline(para.join(" "))}</p>`); para = []; };
  const endItem = () => { if (item) out.push(updInline(item.join(" "))); item = null; };
  const endLists = () => { endItem(); while (lists.length) out.push(`</li></${lists.pop().tag}>`); };
  const lines = String(md ?? "").replace(/\r\n?/g, "\n").replace(/\t/g, "    ").split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i], text = line.trim();
    let m;
    if (!text) { endPara(); gap = true; continue; }
    if ((m = /^ {0,3}(`{3,}|~{3,})/.exec(line))) {
      endPara(); endLists();
      const code = [];
      while (++i < lines.length && !lines[i].trim().startsWith(m[1])) code.push(lines[i]);
      out.push(`<pre><code>${esc(code.join("\n"))}</code></pre>`);
    } else if (/^ {0,3}([-*_])( *\1){2,} *$/.test(line)) {
      endPara(); endLists();
      out.push("<hr>");
    } else if ((m = /^ {0,3}(#{1,6}) +(.*?)(?: +#+)? *$/.exec(line))) {
      endPara(); endLists();
      const tag = m[1].length <= 2 ? "h5" : "h6";
      out.push(`<${tag}>${updInline(m[2])}</${tag}>`);
    } else if (/^ {0,3}>/.test(line)) {
      endPara(); endLists();
      const quote = [];
      for (; i < lines.length && /^ {0,3}>/.test(lines[i]); i++) quote.push(lines[i].replace(/^ {0,3}> ?/, ""));
      i--;
      out.push(`<blockquote>${updMarkdown(quote.join("\n"))}</blockquote>`);
    } else if ((m = /^( *)([-*+]|\d{1,9}[.)]) +(.*)$/.exec(line))) {
      endPara(); endItem();
      const indent = m[1].length, tag = /\d/.test(m[2]) ? "ol" : "ul", start = parseInt(m[2], 10);
      while (lists.length && lists[lists.length - 1].indent > indent + 1) out.push(`</li></${lists.pop().tag}>`);
      const top = lists[lists.length - 1];
      if (top && indent <= top.indent + 1 && top.tag === tag) out.push("</li><li>");  // the next item of this list
      else {  // a list, one inside the item above, or another kind of list at this level
        if (top && indent <= top.indent + 1) out.push(`</li></${lists.pop().tag}>`);
        out.push(`<${tag}${tag === "ol" && start > 1 ? ` start="${start}"` : ""}><li>`);
        lists.push({ tag, indent });
      }
      item = [m[3]];
    } else if (lists.length && (!gap || /^ {2,}/.test(line))) {
      (item || (item = [])).push(text);  // more of the item: a wrapped line, or a paragraph indented under it
    } else {
      endLists();
      para.push(text);
    }
    gap = false;
  }
  endPara(); endLists();
  return out.join("");
}

// One line of Markdown. Code spans and links are set aside first, so that **bold** and *italics* can go around
// them; what is left is escaped before the stars and underscores are read.
const UPD_INLINE = /`([^`]+)`|!?\[([^\]]*)\]\(\s*(https?:\/\/[^\s)]+)\s*\)|<(https?:\/\/[^\s>]+)>|(https?:\/\/[^\s<>()[\]]*[^\s<>()[\].,;:!?'"*_])/g;
function updInline(text) {
  const kept = [];
  const keep = (html) => `\u0000${kept.push(html) - 1}\u0000`;
  const label = (s) => updEmph(esc(s).replace(/`([^`]+)`/g, "<code>$1</code>"));
  const marked = String(text).replace(/\u0000/g, "").replace(UPD_INLINE, (all, code, name, href, angled, bare) =>
    keep(code != null ? `<code>${esc(code)}</code>` : href ? updLink(href, label(name || href)) : updLink(angled || bare, esc(angled || bare))));
  return updEmph(esc(marked)).replace(/\u0000(\d+)\u0000/g, (all, n) => kept[n]);
}
const updEmph = (html) => html
  .replace(/\*\*(?=\S)(.*?\S)\*\*/g, "<strong>$1</strong>").replace(/(^|\W)__(?=\S)(.*?\S)__(?!\w)/g, "$1<strong>$2</strong>")
  .replace(/(^|[^\w*])\*(?=[^\s*])(.*?[^\s*])\*(?![\w*])/g, "$1<em>$2</em>")
  .replace(/(^|\W)_(?=[^\s_])(.*?[^\s_])_(?!\w)/g, "$1<em>$2</em>");

// ---------------------------------------------------------------------------------------------
// What the buttons do
// ---------------------------------------------------------------------------------------------
function bindUpdates() {
  const auto = $("#updAuto");
  if (!auto) return;
  auto.onchange = () => updSettings({ enabled: auto.checked });
  $$("#settings [data-upd-channel]").forEach((b) => (b.onclick = () => b.classList.contains("on") || updSettings({ channel: b.dataset.updChannel })));
  $("#updCheck").onclick = updCheck;
  const install = $("#updInstall"), retry = $("#updRetry"), skip = $("#updSkip");
  if (install) install.onclick = () => updInstall(true);
  if (retry) retry.onclick = () => updInstall(false);
  if (skip) skip.onclick = () => updSkip(skip.dataset.skip === "true");
  $$("#settings [data-upd-copy]").forEach((b) => (b.onclick = () => updCopy(updAvailable(UPD.data)?.commands?.[+b.dataset.updCopy])));
}

// The switch and the channel, saved at once; the pill follows
async function updSettings(change) {
  try { updSetData(await api.post("/api/updates/settings", change)); toast("Saved"); }
  catch (e) { toast(e.message, "error"); }
  updRedraw(true);
  refreshStatus();
}

// Check now: busy until GitHub has answered (the server waits up to about 15 s for it)
async function updCheck() {
  if (UPD.checking) return;
  UPD.checking = true;
  updRedraw(true);
  try { updSetData(await api.post("/api/updates/check")); }
  catch (e) { toast(e.message, "error"); }
  UPD.checking = false;
  updRedraw(true);
  refreshStatus();
}

async function updSkip(skip) {
  try { updSetData(await api.post("/api/updates/skip", { skip })); }
  catch (e) { toast(e.message, "error"); return; }
  updRedraw(true);
  toast(skip ? "Skipped. Sedjem tells you again when something newer is out" : "No longer skipped");
  refreshStatus();
}

// What Update now does, for this kind of install, asked before it starts
function updConfirm(a) {
  const what = a.version ? `Sedjem ${a.version}` : `the newest changes on main (${updShort(a.commit)})`;
  return {
    git: `Update to ${what}? Sedjem runs git pull to move this checkout forward to it (fast-forward only), installs the requirements again if they changed, and restarts.`,
    installer: `Update to ${what}? Sedjem downloads its installer from GitHub, checks it against the release's checksums and runs it. Sedjem closes while it installs and opens again when it's done. Windows SmartScreen may warn about the installer, since it isn't signed yet.`,
    archive: `Download ${what}? Sedjem downloads the Linux archive from GitHub, checks it against the release's checksums and unpacks it beside this one, then shows the folder. This one keeps running until you quit it.`,
  }[a.action] || `Update to ${what}?`;
}

// Update now (asked first), or Try again after a failure. Before a restart the page notes what runs now, to
// say what it was updated to once it is back (in the new window too: the desktop app keeps this storage).
async function updInstall(ask) {
  const d = UPD.data, a = updAvailable(d);
  if (!a || UPD.installing) return;
  const restarts = a.action === "git" || a.action === "installer";
  const running = S.jobs.filter((j) => ACTIVE.has(j.status)).length;
  if (restarts && running) {
    toast(`${running} transcription${running > 1 ? "s are" : " is"} still running. Sedjem restarts to update, so wait until ${running > 1 ? "they finish" : "it finishes"} or stop ${running > 1 ? "them" : "it"} first.`, "error");
    return;
  }
  if (ask && !confirm(updConfirm(a))) return;
  if (restarts) updPut(UPD_FROM, JSON.stringify({ version: d.running?.version, commit: d.running?.commit, at: Date.now() }));
  UPD.installing = true;
  try { updSetData(await api.post("/api/updates/install")); }
  catch (e) { updPut(UPD_FROM, null); toast(e.message, "error"); }
  finally { UPD.installing = false; }
  updRedraw(true);
}

// The clipboard where the browser allows it, else a text box out of sight and the copy command. The box goes
// inside the open dialog: everything outside a modal dialog is inert and can't be selected.
async function updCopy(text) {
  if (!text) return;
  try {
    try { await navigator.clipboard.writeText(text); }
    catch {
      const back = document.activeElement, box = document.createElement("textarea");
      box.value = text;
      box.className = "upd-copy-box";
      box.setAttribute("readonly", "");
      box.setAttribute("aria-hidden", "true");
      ($("#settings[open]") || document.body).append(box);
      box.select();
      const copied = document.execCommand("copy");
      box.remove();
      back?.focus();
      if (!copied) throw new Error("the browser doesn't allow it here; select the text and copy it");
    }
    toast("Command copied");
  } catch (e) { toast(`Could not copy: ${e.message}`, "error"); }
}

// ---------------------------------------------------------------------------------------------
// Restarting
// ---------------------------------------------------------------------------------------------
// Sedjem restarting after Update now: the page waits for it, over everything, and reloads once it answers
// again. An answer counts when Sedjem went away first, or when it no longer says it is restarting (the new one
// came up between two checks). After two minutes the page says so and offers to reload.
async function updWaitForRestart() {
  if (UPD.restart) return;
  const r = (UPD.restart = { start: Date.now(), gone: false });
  clearTimeout(UPD.timer);
  updHideNotice();
  const dlg = updRestartDialog();
  if (!dlg.open) dlg.showModal();
  while (Date.now() - r.start < UPD_RESTART_LIMIT) {
    await new Promise((done) => setTimeout(done, 1000));
    if (await updBack(r)) { location.reload(); return; }
  }
  r.over = true;
  const source = UPD.data?.running?.kind === "source";
  $("#updRestartTitle").textContent = "Sedjem didn't come back";
  $("#updRestartText").textContent = `It hasn't answered for two minutes. ${source ? "Start it again with ./app.sh in the project folder" : "Open Sedjem again"}, then reload this page.`;
  $("#updRestartBar").hidden = true;
  $("#updReload").hidden = false;
  $("#updReload").focus();
}

async function updBack(r) {
  try { await api.get("/api/status"); } catch { r.gone = true; return false; }
  if (r.gone) return true;
  try { return (await api.get("/api/updates")).install?.state !== "restarting"; } catch { r.gone = true; return false; }
}

function updRestartDialog() {
  let dlg = $("#updRestart");
  if (dlg) return dlg;
  const installer = updAvailable(UPD.data)?.action === "installer";
  document.body.insertAdjacentHTML("beforeend", `<dialog id="updRestart" class="upd-restart" aria-labelledby="updRestartTitle" aria-describedby="updRestartText">
    <img src="/static/icon.svg" alt="" width="52" height="52">
    <h2 id="updRestartTitle">Sedjem is restarting…</h2>
    <p id="updRestartText">${installer ? "The installer is updating it; it opens again when it's done." : "This page reloads when it's back."}</p>
    <div class="progress indeterminate" id="updRestartBar"><i></i></div>
    <button type="button" class="btn btn-primary" id="updReload" hidden>Reload</button></dialog>`);
  dlg = $("#updRestart");
  // it stays until Sedjem is back (the browser may still close it on a second Escape: it opens again)
  dlg.addEventListener("cancel", (e) => UPD.restart?.over || e.preventDefault());
  dlg.addEventListener("close", () => UPD.restart && !UPD.restart.over && dlg.showModal());
  $("#updReload").onclick = () => location.reload();
  return dlg;
}

// Once, when the page starts: back after Update now, it says what it was updated to (or that the update failed)
async function updAfterRestart() {
  let from = null;
  try { from = JSON.parse(updGet(UPD_FROM) || "null"); } catch { /* not ours */ }
  if (!from) return;
  updPut(UPD_FROM, null);
  if (!(Date.now() - from.at < 86400000)) return;  // Update now was a day ago or more: not what brought this start
  await loadUpdates();
  const r = UPD.data?.running, i = UPD.data?.install;
  if (!r) return;
  if (r.version && r.version !== from.version) toast(`Updated to ${r.version}`);
  else if (r.commit && from.commit && r.commit !== from.commit) toast(`Updated to the latest changes (${updShort(r.commit)})`);
  else if (i?.state === "failed") toast(`The update failed: ${i.error || "something went wrong"}`, "error");
}
