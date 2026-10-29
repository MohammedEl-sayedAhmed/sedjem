/* Sedjem: the anonymous usage counts (the server side is app/usage.py; docs/13-usage-counts.md). Their switch in
   Settings → About, and the notice with the same switch in the first-start dialog (updates.js updAsk), which says
   that they are on. A copy without a key for Aptabase shows neither. Loaded before app.js: it only defines things,
   and uses app.js's helpers (S, api, $, esc, extLink, toast, renderTop, renderSettings) when they run. */
"use strict";

const USAGE_DOC = "https://github.com/MohammedEl-sayedAhmed/sedjem/blob/main/docs/13-usage-counts.md";
const USAGE_TEXT = "A few numbers a month, such as the version, the system and how many transcriptions with which models; never recordings, transcripts or names.";

// Settings → About: the switch, off and disabled when the environment turns the counts off
function usageRow() {
  const u = S.status?.usage;
  if (!u?.available) return "";
  const text = u.blocked ? `Off: ${u.blocked} is set in the environment Sedjem was started from.` : USAGE_TEXT;
  return `<label class="set-row">
      <div class="set-text"><b>Send anonymous usage counts</b><span class="hint">${esc(text)} ${extLink(USAGE_DOC, "What is sent")}</span></div>
      <input type="checkbox" class="switch" id="usageSwitch" data-setting="usage_counts" ${u.on ? "checked" : ""}${u.blocked ? " disabled" : ""}>
    </label>`;
}

// The notice in the first-start dialog: that the counts are on, what they are and how to turn them off, with the
// switch, which is saved as soon as it is turned
function usageNotice() {
  return `<div class="usage-ask">
      <label class="set-row">
        <div class="set-text"><b>Send anonymous usage counts</b><span class="hint" id="usageAskText">On unless you turn it off, here or later in Settings → About. ${esc(USAGE_TEXT)} ${extLink(USAGE_DOC, "What is sent")}</span></div>
        <input type="checkbox" class="switch" id="usageAsk" checked>
      </label>
    </div>`;
}

function bindUsageNotice() {
  const sw = $("#usageAsk");
  if (sw) sw.onchange = () => usageSave(sw.checked, true).catch((e) => toast(e.message, "error"));
}

// The switch's state saved as the answer: from the dialog's switch (said in a toast) or with its buttons
async function usageSave(on, say) {
  S.status = await api.post("/api/settings", { usage_counts: on });
  renderTop();
  if ($("#settings").open) renderSettings();
  if (say) toast(on ? "Anonymous usage counts are on" : "No usage counts. Settings → About can turn them on again");
}
