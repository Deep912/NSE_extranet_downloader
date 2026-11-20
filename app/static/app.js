"use strict";

// ==========================================================
// HELPERS
// ==========================================================

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

async function api(url, body, method) {
  const options = { method: method || (body === undefined ? "GET" : "POST") };

  if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }

  const response = await fetch(url, options);
  let data = null;

  try {
    data = await response.json();
  } catch {
    // Non-JSON error page; use the status below.
  }

  if (!response.ok) {
    const detail = data && data.detail;
    const error = new Error(
      typeof detail === "string" ? detail : `Request failed (HTTP ${response.status})`
    );
    error.status = response.status;
    throw error;
  }

  return data;
}

const store = {
  get(key, fallback) {
    try {
      const value = localStorage.getItem(`nse.${key}`);
      return value === null ? fallback : JSON.parse(value);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try { localStorage.setItem(`nse.${key}`, JSON.stringify(value)); } catch { /* ignore */ }
  },
};

// "2026-11-13" (date input) -> "13-11-2026" (API)
function toNseDate(isoDate) {
  const [y, m, d] = isoDate.split("-");
  return `${d}-${m}-${y}`;
}

function localIsoDate(offsetDays = 0) {
  const day = new Date();
  day.setDate(day.getDate() + offsetDays);
  const pad = (n) => String(n).padStart(2, "0");
  return `${day.getFullYear()}-${pad(day.getMonth() + 1)}-${pad(day.getDate())}`;
}

const number = (n) => (Number(n) || 0).toLocaleString();

function fileSize(bytes) {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function localTime(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

// Shorter form for tables: "18 Nov, 05:00 pm" (year only if not this year).
function shortTime(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const options = { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" };
  if (date.getFullYear() !== new Date().getFullYear()) options.year = "numeric";
  return date.toLocaleString(undefined, options);
}

function seconds(total) {
  total = Math.max(0, Math.round(total));
  if (total < 60) return `${total}s`;
  return `${Math.floor(total / 60)}m ${String(total % 60).padStart(2, "0")}s`;
}

function duration(startIso, endIso) {
  if (!startIso || !endIso) return "—";
  return seconds((new Date(endIso) - new Date(startIso)) / 1000);
}

function relativeDay(iso) {
  if (!iso) return "—";
  const date = new Date(iso);
  const today = new Date();
  const tomorrow = new Date();
  tomorrow.setDate(today.getDate() + 1);
  const time = date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

  if (date.toDateString() === today.toDateString()) return `today ${time}`;
  if (date.toDateString() === tomorrow.toDateString()) return `tomorrow ${time}`;
  return `${date.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })} ${time}`;
}

// NSE paths: "/" for the root, "/Reports/Sub" otherwise.
function normalisePath(path) {
  const parts = String(path || "").split("/").map(p => p.trim()).filter(Boolean);
  return "/" + parts.join("/");
}

const joinPath = (base, name) => normalisePath(`${base}/${name}`);


// ==========================================================
// FEEDBACK: toasts + loading buttons
// ==========================================================

function toast(message, kind = "info", timeout = 4500) {
  const element = document.createElement("div");
  element.className = `toast ${kind}`;
  element.setAttribute("role", kind === "bad" ? "alert" : "status");
  element.innerHTML = `<span class="msg">${escapeHtml(message)}</span>
                       <button type="button" aria-label="Dismiss">×</button>`;

  const close = () => {
    element.classList.add("leaving");
    setTimeout(() => element.remove(), 200);
  };

  element.querySelector("button").addEventListener("click", close);
  $("#toasts").append(element);

  // Keep at most 4 on screen.
  const all = $$("#toasts .toast:not(.leaving)");
  if (all.length > 4) all[0].remove();

  if (timeout) setTimeout(close, timeout);
}

// Show a spinner and busy label on a button while fn() runs.
async function withLoading(button, busyLabel, fn) {
  const label = button.textContent;
  button.classList.add("loading");
  button.disabled = true;
  button.textContent = busyLabel;

  try {
    return await fn();
  } finally {
    button.classList.remove("loading");
    button.textContent = label;
    button.disabled = false;
    applyBusy();
  }
}


// ==========================================================
// MASCOT
// ==========================================================

const cat = (face, extra = ["", "", ""]) => [
  ` /\\_/\\  ${extra[0]}`,
  `${face}  ${extra[1]}`,
  ` > ^ <  ${extra[2]}`,
].join("\n");

const MOODS = {
  sleep:   { every: 1100, frames: [cat("( -.- )", ["", " z", ""]), cat("( -.- )", ["", " zZ", ""])] },
  work:    { every: 320,  frames: [cat("( o.o )", ["", "", " ~"]), cat("( o.o )", ["", "", "~ "]), cat("( O.O )", ["", "", " ~"])] },
  sniff:   { every: 450,  frames: [cat("( o.o )", ["", " ?", ""]), cat("( o.o )", ["", "  ?", ""])] },
  happy:   { every: 700,  frames: [cat("( ^.^ )"), cat("( ^o^ )", ["", "", " *"])] },
  worried: { every: 0,    frames: [cat("( o.O )", ["", " !", ""])] },
  sad:     { every: 0,    frames: [cat("( x.x )")] },
};

let moodTimer = null;

function setMood(mood, message) {
  const { every, frames } = MOODS[mood];
  let frame = 0;

  clearInterval(moodTimer);
  $("#cat").textContent = frames[0];
  $("#catSays").textContent = message;

  if (every && frames.length > 1) {
    moodTimer = setInterval(() => {
      frame = (frame + 1) % frames.length;
      $("#cat").textContent = frames[frame];
    }, every);
  }
}


// ==========================================================
// STATE
// ==========================================================

const SEGMENT_ORDER = ["CM", "FO", "CD", "CO", "SLB"];
const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const state = {
  segments: [],
  selected: new Set(store.get("segments", ["CM"])),
  busy: false,
  localBusy: false,   // a download started from this page
  remoteBusy: false,  // NSE used by a schedule or another tab
  browsePath: "/",
  browseLoaded: false,
  selectedRun: null,
  runs: [],
  schedules: [],
  defaults: null,
  editingId: null,
  serverOffsetMs: 0,
  timezone: "",
  lists: {},              // saved folder lists per segment
  canEdit: true,          // may change schedules + folder lists
  adminConfigured: false,
  isAdmin: false,
  browseItems: [],
};

// Controls that need NSE are disabled while anything else uses it.
function applyBusy() {
  const busy = state.localBusy || state.remoteBusy;
  state.busy = busy;

  $$(
    "#startDownload, #segmentChips button, [data-day], #date, input[name='scope'], #scopeFolder, " +
    "#browseSegment, #browsePath, #browseGo, #useFolder, #crumbs button, #browseItems button"
  ).forEach(element => {
    if (!element.classList.contains("loading")) element.disabled = busy;
  });

  updateDeleteRunButton();
}


// ==========================================================
// ADMIN
// ==========================================================

function renderAdmin() {
  const button = $("#adminBtn");

  button.classList.toggle("admin-on", state.isAdmin);
  button.disabled = !state.adminConfigured;

  if (!state.adminConfigured) {
    button.textContent = "No admin set";
    button.title = "Set ADMIN_PASSWORD in the .env file and restart to protect schedules.";
  } else if (state.isAdmin) {
    button.textContent = "Admin ✓ · Log out";
    button.title = "You can change schedules and folder lists.";
  } else {
    button.textContent = "Admin login";
    button.title = "Log in to change schedules and folder lists.";
  }

  $("#addSchedule").disabled = !state.canEdit;
  $("#addSchedule").title = state.canEdit ? "" : "Only the admin can add schedules.";

  // Admin-only controls (delete, clean up) are hidden for others.
  document.body.classList.toggle("no-edit", !state.canEdit);
}

async function loadAdmin() {
  try {
    const data = await api("/api/admin");
    state.adminConfigured = data.configured;
    state.isAdmin = data.is_admin;
    state.canEdit = data.can_edit;
  } catch {
    // keep previous values
  }

  renderAdmin();
}

// Refresh everything that depends on edit rights.
async function refreshRights() {
  await loadAdmin();
  loadSchedules();
  renderListPanel();
  renderBrowseItems();
}

function handleAuthError(error) {
  if (error.status === 401) {
    toast("Only the admin can do that. Use “Admin login” at the top.", "warn");
    refreshRights();
    return true;
  }
  return false;
}

$("#adminBtn").addEventListener("click", async (event) => {
  if (state.isAdmin) {
    await withLoading(event.currentTarget, "Logging out…", async () => {
      await api("/api/admin/logout", {});
      toast("Logged out. Schedules are view-only now.", "info");
    });
    refreshRights();
    return;
  }

  $("#adminPassword").value = "";
  $("#adminError").textContent = "";
  $("#adminDialog").showModal();
  $("#adminPassword").focus();
});

$("#adminCancel").addEventListener("click", () => $("#adminDialog").close());

$("#adminForm").addEventListener("submit", async (event) => {
  event.preventDefault();

  await withLoading($("#adminSubmit"), "Checking…", async () => {
    try {
      await api("/api/admin/login", { password: $("#adminPassword").value });
      $("#adminDialog").close();
      toast("Logged in as admin.", "good");
      setMood("happy", "hello, admin.");
      refreshRights();
    } catch (error) {
      $("#adminError").textContent = error.message;
      $("#adminPassword").select();
    }
  });
});


// ==========================================================
// STOP
// ==========================================================

async function stopCurrent(button) {
  await withLoading(button, "Stopping…", async () => {
    try {
      await api("/api/stop", {});
      state.stopping = true;
      toast("Stopping after the current file. Files already downloaded are kept.", "warn", 6000);
      setMood("worried", "ok, stopping after this file…");
      $("#downloadTimer").textContent = "Stopping after the current file…";
    } catch (error) {
      if (!handleAuthError(error)) toast(error.message, error.status === 409 ? "info" : "bad");
    }
  });

  // Keep it disabled until the run actually ends.
  button.disabled = true;
  pollStatus();
}

$("#stopDownload").addEventListener("click", (event) => stopCurrent(event.currentTarget));
$("#bannerStop").addEventListener("click", (event) => stopCurrent(event.currentTarget));


// ==========================================================
// SAVED FOLDER LISTS
// ==========================================================

async function loadLists() {
  try {
    const data = await api("/api/folder-lists");
    state.lists = data.lists || {};
  } catch {
    state.lists = {};
  }

  renderListScope();
  renderListPanel();
  renderBrowseItems();
}

// Summary under the Download card when "Folder list" is chosen.
function renderListScope() {
  const segments = state.segments.filter(s => state.selected.has(s));

  $("#listScope").innerHTML = segments.map(segment => {
    const folders = state.lists[segment] || [];
    return `
      <div class="seg-line">
        <b>${escapeHtml(segment)}</b>
        ${folders.length
          ? `<span class="paths">${escapeHtml(folders.join(", "))}</span>`
          : `<span class="empty-seg">empty list, will be skipped</span>`}
      </div>`;
  }).join("") + `<button type="button" class="link" id="manageLists">Manage lists in Browse NSE →</button>`;
}

$("#listScope").addEventListener("click", (event) => {
  if (event.target.id === "manageLists") showTab("browse");
});

// Sidebar in Browse NSE for the segment being browsed.
function renderListPanel() {
  const segment = $("#browseSegment").value;
  if (!segment) return;

  const folders = state.lists[segment] || [];

  $("#listTitle").textContent = `Folder list · ${segment}`;
  $("#addCurrentFolder").classList.toggle("hidden", !state.canEdit);
  $("#addCurrentFolder").disabled = state.browsePath === "/" || folders.includes(state.browsePath);

  $("#listHelp").textContent = state.canEdit
    ? "Used by the “Folder list” scope in downloads and schedules. Subfolders are included."
    : "Used by the “Folder list” scope. Only the admin can change it.";

  $("#listItems").innerHTML = folders.length
    ? folders.map(folder => `
        <li>
          <span class="path">${escapeHtml(folder)}</span>
          <button type="button" class="secondary" data-open-list="${escapeHtml(folder)}" title="Open in browser">Open</button>
          ${state.canEdit
            ? `<button type="button" class="danger" data-remove-list="${escapeHtml(folder)}" title="Remove from list">✕</button>`
            : ""}
        </li>`).join("")
    : `<li class="empty-li">No folders yet.${state.canEdit ? " Open a folder and press “+ This folder”, or use “+ List” on a row." : ""}</li>`;
}

async function changeList(method, segment, folder, button) {
  const run = async () => {
    try {
      const data = method === "add"
        ? await api("/api/folder-lists", { segment, folder_path: folder })
        : await api(`/api/folder-lists?segment=${encodeURIComponent(segment)}&folder_path=${encodeURIComponent(folder)}`, undefined, "DELETE");

      state.lists[segment] = data.folders;
      toast(method === "add" ? `Added ${folder} to the ${segment} list.` : `Removed ${folder} from the ${segment} list.`,
        method === "add" ? "good" : "info");
    } catch (error) {
      if (!handleAuthError(error)) toast(error.message, "bad");
    }
  };

  if (button) await withLoading(button, "…", run);
  else await run();

  renderListScope();
  renderListPanel();
  renderBrowseItems();
}

$("#addCurrentFolder").addEventListener("click", (event) =>
  changeList("add", $("#browseSegment").value, state.browsePath, event.currentTarget));

$("#listItems").addEventListener("click", (event) => {
  const remove = event.target.closest("[data-remove-list]");
  const open = event.target.closest("[data-open-list]");

  if (remove) changeList("remove", $("#browseSegment").value, remove.dataset.removeList, remove);
  if (open) openFolder(open.dataset.openList);
});


// ==========================================================
// TABS
// ==========================================================

function showTab(name) {
  if (!["runs", "browse", "log"].includes(name)) name = "runs";

  for (const [tab, id] of [["runs", "Runs"], ["browse", "Browse"], ["log", "Log"]]) {
    $(`#tab${id}`).setAttribute("aria-selected", String(name === tab));
    $(`#panel${id}`).classList.toggle("hidden", name !== tab);
  }
  $("#runsTools").classList.toggle("hidden", name !== "runs");
  $("#logTools").classList.toggle("hidden", name !== "log");

  store.set("tab", name);

  if (name === "browse" && !state.browseLoaded && !state.busy) openFolder("/");
  if (name === "log") {
    logState.problems = 0;
    renderLogBadge();
    loadLog(true);
  }
}

$("#tabRuns").addEventListener("click", () => showTab("runs"));
$("#tabBrowse").addEventListener("click", () => showTab("browse"));
$("#tabLog").addEventListener("click", () => showTab("log"));


// ==========================================================
// LOG
// ==========================================================

const logState = { last: 0, lines: [], loading: false, problems: 0, file: null };
const LOG_KEEP = 2000;

function logTabOpen() {
  return !$("#panelLog").classList.contains("hidden");
}

function renderLogBadge() {
  const badge = $("#logProblems");
  badge.textContent = logState.problems ? String(logState.problems) : "";
  badge.classList.toggle("hidden", !logState.problems || logTabOpen());
}

function logLineHtml(line, previous) {
  const day = line.time.slice(0, 10);
  const header = !previous || previous.time.slice(0, 10) !== day
    ? `<div class="log-day">${escapeHtml(day)}</div>` : "";

  return `${header}<div class="log-line ${escapeHtml(line.level)}">` +
    `<span class="t">${escapeHtml(line.time.slice(11, 19))}</span>` +
    `<span class="lv">${escapeHtml(line.level === "WARNING" ? "WARN" : line.level)}</span>` +
    `<span class="m">${escapeHtml(line.message)}</span></div>`;
}

function renderLog() {
  const view = $("#logView");
  const onlyProblems = $("#logProblemsOnly").checked;
  const lines = onlyProblems
    ? logState.lines.filter(l => l.level !== "INFO" && l.level !== "DEBUG")
    : logState.lines;

  if (!lines.length) {
    view.innerHTML = `<div class="log-empty">${onlyProblems ? "No warnings or errors." : "Nothing logged yet."}</div>`;
    return;
  }

  view.innerHTML = lines.map((line, i) => logLineHtml(line, lines[i - 1])).join("");
  if ($("#logFollow").checked) view.scrollTop = view.scrollHeight;
}

async function loadLog(full = false) {
  if (logState.loading) return;
  logState.loading = true;

  try {
    const after = full && !logState.lines.length ? 0 : logState.last;
    const data = await api(`/api/logs?after=${after}&limit=${LOG_KEEP}`);

    // The app restarted: its line numbers start again from 1, so start
    // over on the next poll.
    if (data.last < logState.last) {
      logState.lines = [];
      logState.last = 0;
      return;
    }

    const fresh = data.lines || [];
    logState.file = data.file;

    if (logState.last) {
      logState.problems += fresh.filter(l => l.level === "WARNING" || l.level === "ERROR" || l.level === "CRITICAL").length;
    }

    logState.lines = logState.lines.concat(fresh).slice(-LOG_KEEP);
    logState.last = data.last;

    $("#logInfo").textContent = logState.file
      ? `The last ${LOG_KEEP} lines since the app started. Every line is also saved to ${logState.file} (a new file each day, kept 30 days).`
      : `The last ${LOG_KEEP} lines since the app started.`;

    if (logTabOpen()) {
      logState.problems = 0;
      if (fresh.length || full) renderLog();
    }
    renderLogBadge();
  } catch (error) {
    if (logTabOpen()) $("#logView").innerHTML = `<div class="log-empty">Could not load the log: ${escapeHtml(error.message)}</div>`;
  } finally {
    logState.loading = false;
  }
}

$("#logProblemsOnly").addEventListener("change", renderLog);
$("#logFollow").addEventListener("change", () => {
  if ($("#logFollow").checked) $("#logView").scrollTop = $("#logView").scrollHeight;
});
// Scrolling up pauses "Follow"; scrolling back to the bottom resumes it.
$("#logView").addEventListener("scroll", () => {
  const view = $("#logView");
  const atBottom = view.scrollHeight - view.scrollTop - view.clientHeight < 24;
  if ($("#logFollow").checked !== atBottom) $("#logFollow").checked = atBottom;
});


// ==========================================================
// APP STATUS, CLOCK, SEGMENTS
// ==========================================================

async function checkHealth() {
  const pill = $("#apiStatus");

  try {
    const data = await api("/health");
    pill.className = "pill ok";
    pill.innerHTML = `<i></i> App running · v${escapeHtml(data.version)}`;
  } catch {
    pill.className = "pill down";
    pill.innerHTML = "<i></i> App not reachable";
  }
}

function tickClock() {
  const now = new Date(Date.now() + state.serverOffsetMs);
  const time = now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  $("#clockPill").textContent = `${time}${state.timezone ? ` · ${state.timezone}` : ""}`;
}

async function loadSegments() {
  try {
    const data = await api("/api/segments");
    const available = data.segments || [];

    state.segments = [
      ...SEGMENT_ORDER.filter(s => available.includes(s)),
      ...available.filter(s => !SEGMENT_ORDER.includes(s)),
    ];
  } catch {
    state.segments = SEGMENT_ORDER;
  }

  state.selected = new Set([...state.selected].filter(s => state.segments.includes(s)));
  if (!state.selected.size) state.selected.add(state.segments[0]);

  renderSegmentChips();

  const options = state.segments
    .map(s => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("");

  $("#browseSegment").innerHTML = options;
  $("#browseSegment").value = state.segments.includes("CM") ? "CM" : state.segments[0];
  $("#runsSegment").innerHTML = `<option value="">All segments</option>${options}`;
  $("#cSegment").innerHTML = `<option value="">All segments</option>${options}`;

  renderCrumbs(state.browsePath);
}

function renderSegmentChips() {
  const allOn = state.segments.every(s => state.selected.has(s));

  $("#segmentChips").innerHTML =
    state.segments.map(s => `
      <button type="button" class="chip" data-segment="${escapeHtml(s)}"
              aria-pressed="${state.selected.has(s)}">${escapeHtml(s)}</button>`).join("") +
    `<button type="button" class="chip" data-segment="*" aria-pressed="${allOn}">All</button>`;

  applyBusy();
}

$("#segmentChips").addEventListener("click", (event) => {
  const chip = event.target.closest("[data-segment]");
  if (!chip || state.busy) return;

  const segment = chip.dataset.segment;

  if (segment === "*") {
    const allOn = state.segments.every(s => state.selected.has(s));
    state.selected = new Set(allOn ? [] : state.segments);
  } else if (state.selected.has(segment)) {
    state.selected.delete(segment);
  } else {
    state.selected.add(segment);
  }

  store.set("segments", [...state.selected]);
  renderSegmentChips();
  renderListScope();
});


// ==========================================================
// DOWNLOAD
// ==========================================================

$("#date").value = localIsoDate(0);

$$("[data-day]").forEach(button => {
  button.addEventListener("click", () => {
    $("#date").value = localIsoDate(Number(button.dataset.day));
    const label = Number(button.dataset.day) === 0 ? "today" : "yesterday";
    $("#catSays").textContent = `ok, ${label}: ${toNseDate($("#date").value)}.`;
  });
});

function currentScope() {
  return document.querySelector("input[name='scope']:checked").value;
}

function showScope() {
  $("#folderScope").classList.toggle("hidden", currentScope() !== "folder");
  $("#listScope").classList.toggle("hidden", currentScope() !== "list");
  store.set("scope", currentScope());
  renderListScope();
}

$$("input[name='scope']").forEach(radio => {
  radio.checked = radio.value === store.get("scope", "segment");
  radio.addEventListener("change", showScope);
});

$("#scopeFolder").value = store.get("folder", "/Reports");
$("#scopeFolder").addEventListener("change", () => store.set("folder", $("#scopeFolder").value));
showScope();

function progressRow(segment, status, body = "") {
  const spinner = status === "running" ? '<span class="spinner" aria-hidden="true"></span>' : "";
  return `
    <li data-row="${escapeHtml(segment)}">
      <span class="seg">${escapeHtml(segment)}</span>
      <div>
        <span class="badge ${escapeHtml(status)}">${escapeHtml(status)}</span>
        ${spinner}${body}
      </div>
    </li>`;
}

function resultBody(result, took) {
  const failedFiles = (result.downloads || []).filter(d => d.status === "failed");
  const failedFolders = result.failed_folders || [];

  const problems = [
    ...failedFolders.map(f => `${f.folder_path || "/"}: ${f.error}`),
    ...failedFiles.map(f => `${f.file_name}: ${f.error}`),
  ];

  const shown = problems.slice(0, 4).map(p => `<li>${escapeHtml(p)}</li>`).join("");
  const more = problems.length > 4 ? `<li>+${problems.length - 4} more (see Runs)</li>` : "";

  return `
    <span class="muted small">${escapeHtml(took)}</span>
    <div class="counts">
      <span><b>${number(result.downloaded)}</b> new</span>
      <span><b>${number(result.already_downloaded)}</b> had</span>
      <span><b>${number(result.other_date)}</b> other dates</span>
      <span><b>${number(result.failed)}</b> failed</span>
      <span><b>${number(result.folders_scanned)}</b> folders</span>
    </div>
    ${problems.length ? `<ul class="errors">${shown}${more}</ul>` : ""}`;
}

function setRow(segment, html) {
  const row = document.querySelector(`[data-row="${CSS.escape(segment)}"]`);
  if (row) row.outerHTML = html;
}

async function startDownload() {
  if (state.busy) return;

  const isoDate = $("#date").value;
  const segments = state.segments.filter(s => state.selected.has(s));

  if (!isoDate) {
    setMood("worried", "pick a date first.");
    toast("Pick an exchange date first.", "warn");
    $("#date").focus();
    return;
  }

  if (!segments.length) {
    setMood("worried", "pick at least one segment.");
    toast("Select at least one segment.", "warn");
    return;
  }

  const date = toNseDate(isoDate);
  const scope = currentScope();
  const folderPath = scope === "folder" ? normalisePath($("#scopeFolder").value) : "/";
  const button = $("#startDownload");

  if (scope === "list" && !segments.some(s => (state.lists[s] || []).length)) {
    setMood("worried", "those folder lists are empty.");
    toast("The folder list is empty for the selected segment(s). Add folders in Browse NSE.", "warn");
    return;
  }
  const started = Date.now();

  state.localBusy = true;
  state.stopping = false;
  applyBusy();

  const stopButton = $("#stopDownload");
  stopButton.classList.remove("hidden");
  stopButton.disabled = false;

  const timer = setInterval(() => {
    if (!state.stopping) {
      $("#downloadTimer").textContent = `Running for ${seconds((Date.now() - started) / 1000)}…`;
    }
  }, 1000);
  $("#downloadTimer").textContent = "Starting…";

  $("#downloadProgress").innerHTML = segments.map(s => progressRow(s, "waiting")).join("");

  const totals = { downloaded: 0, already: 0, failed: 0, errors: 0 };
  let stopped = false;

  await withLoading(button, `Downloading ${segments.length > 1 ? `${segments.length} segments` : segments[0]}…`, async () => {
    for (const segment of segments) {
      // Stop pressed between segments: don't start the next one.
      if (stopped || state.stopping) {
        stopped = true;
        setRow(segment, progressRow(segment, "cancelled", `<span class="muted small">not started (stopped)</span>`));
        continue;
      }

      const listFolders = state.lists[segment] || [];
      const where = scope === "list"
        ? `${segment} (${listFolders.length} folder${listFolders.length === 1 ? "" : "s"})`
        : folderPath === "/" ? segment : `${segment}${folderPath}`;
      const segmentStart = Date.now();

      if (scope === "list" && !listFolders.length) {
        setRow(segment, progressRow(segment, "skipped",
          `<span class="muted small">folder list is empty</span>`));
        continue;
      }

      setMood("work", `fetching ${where} for ${date}…`);
      setRow(segment, progressRow(segment, "running", `<span class="muted small">listing folders and downloading…</span>`));

      try {
        const result = scope === "list"
          ? await api("/api/nse/download-list", { segment, date })
          : await api("/api/nse/download-folder", { segment, folder_path: folderPath, date });

        totals.downloaded += result.downloaded || 0;
        totals.already += result.already_downloaded || 0;
        totals.failed += result.failed || 0;

        setRow(segment, progressRow(segment, result.status,
          resultBody(result, seconds((Date.now() - segmentStart) / 1000))));

        if (result.status === "cancelled") stopped = true;

      } catch (error) {
        totals.errors += 1;
        setRow(segment, progressRow(segment, "failed",
          `<ul class="errors"><li>${escapeHtml(error.message)}</li></ul>`));
      }

      loadRuns();
    }
  });

  clearInterval(timer);
  stopButton.classList.add("hidden");
  state.localBusy = false;
  state.stopping = false;
  applyBusy();

  $("#downloadTimer").textContent = stopped
    ? `Stopped after ${seconds((Date.now() - started) / 1000)}.`
    : `Finished in ${seconds((Date.now() - started) / 1000)}.`;

  if (stopped) {
    setMood("sleep", `stopped. kept ${number(totals.downloaded)} new file(s).`);
    toast(`Stopped. ${number(totals.downloaded)} new file(s) were kept. Run it again to continue where it left off.`, "warn", 7000);
  } else if (totals.errors === segments.length) {
    setMood("sad", "couldn't complete any segment. check the errors.");
    toast(`Download for ${date} failed. See the errors under the Download button.`, "bad", 8000);
  } else if (totals.failed || totals.errors) {
    setMood("worried", `${number(totals.downloaded)} new, some failed. run it again to retry.`);
    toast(`${number(totals.downloaded)} new file(s), but ${number(totals.failed + totals.errors)} failed. Running it again retries only those.`, "warn", 8000);
  } else if (totals.downloaded) {
    setMood("happy", `done! ${number(totals.downloaded)} new, ${number(totals.already)} already here.`);
    toast(`Done: ${number(totals.downloaded)} new file(s) for ${date}.`, "good");
  } else {
    setMood("happy", `nothing new for ${date}. ${number(totals.already)} already here.`);
    toast(`Nothing new for ${date}. ${number(totals.already)} file(s) already downloaded.`, "good");
  }
}

$("#startDownload").addEventListener("click", startDownload);


// ==========================================================
// BROWSE
// ==========================================================

function renderCrumbs(path) {
  const parts = path.split("/").filter(Boolean);
  let html = `<button type="button" data-path="/">${escapeHtml($("#browseSegment").value || "root")}</button>`;
  let current = "";

  for (const part of parts) {
    current += `/${part}`;
    html += `<span>/</span><button type="button" data-path="${escapeHtml(current)}">${escapeHtml(part)}</button>`;
  }

  $("#crumbs").innerHTML = html;
  applyBusy();
}

async function openFolder(path) {
  if (state.busy) {
    toast("NSE is busy with a download. Browsing will work again when it finishes.", "warn");
    return;
  }

  // One listing at a time (one NSE login): remember the newest request
  // and open it when the current listing finishes.
  if (state.browseLoading) {
    state.browsePending = path;
    return;
  }

  state.browseLoading = true;

  try {
    await listFolder(path);
  } finally {
    state.browseLoading = false;
  }

  if (state.browsePending !== null && state.browsePending !== undefined) {
    const next = state.browsePending;
    state.browsePending = null;
    openFolder(next);
  }
}

async function listFolder(path) {

  const segment = $("#browseSegment").value;
  path = normalisePath(path);

  state.browsePath = path;
  state.browseLoaded = true;
  $("#browsePath").value = path;
  renderCrumbs(path);

  state.browseItems = null;
  renderListPanel();

  $("#browseItems").innerHTML =
    `<tr><td colspan="3" class="empty-cell"><span class="spinner"></span>Asking NSE for ${escapeHtml(segment)}${escapeHtml(path === "/" ? "" : path)}…</td></tr>`;
  setMood("sniff", `sniffing around ${segment}${path === "/" ? "" : path}…`);

  await withLoading($("#browseGo"), "Opening…", async () => {
    try {
      const data = await api("/api/nse/list", { segment, folder_path: path });
      state.browseItems = Array.isArray(data.items) ? data.items : [];
      renderBrowseItems();

      const folders = state.browseItems.filter(isFolder).length;
      const files = state.browseItems.length - folders;

      $("#browseInfo").textContent =
        `${segment}${path === "/" ? "" : path} · ${folders} folder(s) · ${files} file(s). Times are local; hover for UTC.`;
      setMood("sleep", `${folders} folder(s), ${files} file(s) here. back to napping.`);

    } catch (error) {
      $("#browseItems").innerHTML = `<tr><td colspan="3" class="empty-cell note">${escapeHtml(error.message)}</td></tr>`;
      setMood("worried", "NSE didn't answer that one.");
      toast(error.message, error.status === 409 ? "warn" : "bad", 7000);
    }
  });
}

const isFolder = (item) => String(item.type).toLowerCase() === "folder";

function renderBrowseItems() {
  const items = state.browseItems;
  if (!Array.isArray(items)) return;

  const segment = $("#browseSegment").value;
  const list = state.lists[segment] || [];

  const folders = items.filter(isFolder).sort((a, b) => a.name.localeCompare(b.name));
  const files = items.filter(i => !isFolder(i))
    .sort((a, b) => String(b.lastUpdated).localeCompare(String(a.lastUpdated)));

  $("#browseItems").innerHTML = (folders.length || files.length) ? [
    ...folders.map(f => {
      const path = joinPath(state.browsePath, f.name);
      const parent = list.find(folder => path.startsWith(`${folder}/`));
      const action = list.includes(path)
        ? `<span class="in-list">✓ in list</span>`
        : parent
          ? `<span class="in-list muted" title="Already included through ${escapeHtml(parent)}">✓ via ${escapeHtml(parent)}</span>`
        : state.canEdit
          ? `<button type="button" class="secondary row-btn" data-add-folder="${escapeHtml(path)}" title="Add to the ${escapeHtml(segment)} folder list">+ List</button>`
          : "";
      return `
        <tr>
          <td><button type="button" class="folder-link" data-folder="${escapeHtml(f.name)}">${escapeHtml(f.name)}</button></td>
          <td class="muted" title="UTC: ${escapeHtml(f.lastUpdated)}">${escapeHtml(localTime(f.lastUpdated))}</td>
          <td class="num">${action}</td>
        </tr>`;
    }),
    ...files.map(f => `
      <tr>
        <td class="file-name">${escapeHtml(f.name)}</td>
        <td class="muted" title="UTC: ${escapeHtml(f.lastUpdated)}">${escapeHtml(localTime(f.lastUpdated))}</td>
        <td class="num" data-file-cell="${escapeHtml(f.name)}">
          <button type="button" class="secondary row-btn" data-download-file="${escapeHtml(f.name)}" title="Download just this file">Download</button>
        </td>
      </tr>`),
  ].join("") : `<tr><td colspan="3" class="empty-cell">This folder is empty.</td></tr>`;

  applyBusy();
}

async function downloadOneFile(button, fileName) {
  const segment = $("#browseSegment").value;
  const folder = state.browsePath;

  state.localBusy = true;
  applyBusy();
  setMood("work", `fetching ${fileName}…`);

  await withLoading(button, "Downloading…", async () => {
    try {
      const result = await api("/api/nse/download-file", { segment, folder_path: folder, file_name: fileName });

      // Find the record so the file can be saved to this PC.
      let saveLink = "";
      try {
        const record = await api(`/api/downloads?run_id=${result.run_id}&limit=1`);
        const id = record.downloads && record.downloads[0] && record.downloads[0].id;
        if (id) saveLink = ` <a class="save-link" href="/api/downloads/${id}/file" download>Save copy</a>`;
      } catch { /* the download itself worked */ }

      const cell = document.querySelector(`[data-file-cell="${CSS.escape(fileName)}"]`);
      const label = result.status === "downloaded" ? "✓ saved" : "✓ already had";
      if (cell) cell.innerHTML = `<span class="saved-ok">${label}</span>${saveLink}`;

      toast(`${fileName}: ${result.status === "downloaded" ? "downloaded" : "already downloaded"} (dated ${result.exchange_date}).`, "good");
      setMood("happy", `got ${fileName}.`);
      loadRuns();

    } catch (error) {
      toast(`${fileName}: ${error.message}`, error.status === 409 ? "warn" : "bad", 7000);
      setMood("worried", "that file didn't come through.");
    }
  });

  state.localBusy = false;
  applyBusy();
}

$("#browseGo").addEventListener("click", () => openFolder($("#browsePath").value));
$("#browsePath").addEventListener("keydown", (event) => {
  if (event.key === "Enter") openFolder($("#browsePath").value);
});
$("#browseSegment").addEventListener("change", () => {
  renderListPanel();
  openFolder("/");
});

$("#crumbs").addEventListener("click", (event) => {
  const crumb = event.target.closest("[data-path]");
  if (crumb) openFolder(crumb.dataset.path);
});

$("#browseItems").addEventListener("click", (event) => {
  const link = event.target.closest("[data-folder]");
  const add = event.target.closest("[data-add-folder]");
  const file = event.target.closest("[data-download-file]");

  if (link) openFolder(joinPath(state.browsePath, link.dataset.folder));
  if (add) changeList("add", $("#browseSegment").value, add.dataset.addFolder, add);
  if (file) downloadOneFile(file, file.dataset.downloadFile);
});

$("#useFolder").addEventListener("click", () => {
  const segment = $("#browseSegment").value;
  const whole = state.browsePath === "/";

  state.selected = new Set([segment]);
  store.set("segments", [...state.selected]);
  renderSegmentChips();

  document.querySelector(`input[name='scope'][value='${whole ? "segment" : "folder"}']`).checked = true;
  $("#scopeFolder").value = state.browsePath;
  store.set("folder", state.browsePath);
  showScope();

  const target = `${segment}${whole ? " (whole segment)" : state.browsePath}`;
  setMood("happy", `ok, next download: ${target}.`);
  toast(`Download card set to ${target}. Pick a date and press Download.`, "good");
});


// ==========================================================
// RUNS
// ==========================================================

function runScope(run) {
  let roots = [];
  try { roots = JSON.parse(run.roots || "[]"); } catch { /* ignore */ }
  if (!roots.length) return run.mode || "—";
  if (roots.length === 1 && roots[0] === "/") return "whole segment";
  return roots.join(", ");
}

const MODE_TAGS = { scheduled: "scheduled", list: "folder list", file: "single file", retry: "retry" };

function runFailed(run) {
  return (run.files_failed || 0) + (run.folders_failed || 0);
}

function renderRuns() {
  const segment = $("#runsSegment").value;
  const problemsOnly = $("#problemsOnly").checked;

  const runs = state.runs.filter(run =>
    (!segment || run.segment === segment) &&
    (!problemsOnly || run.status !== "completed")
  );

  $("#runsCount").textContent = state.runs.length ? number(state.runs.length) : "";

  $("#runs").innerHTML = runs.length ? runs.map(run => {
    const failed = runFailed(run);
    return `
      <tr class="clickable ${run.id === state.selectedRun ? "selected" : ""}" data-run="${run.id}">
        <td class="muted">${run.id}</td>
        <td>${escapeHtml(run.exchange_date || "—")}</td>
        <td><b>${escapeHtml(run.segment)}</b></td>
        <td class="file-name">${escapeHtml(runScope(run))}${
          MODE_TAGS[run.mode] ? `<span class="tag">${MODE_TAGS[run.mode]}</span>` : ""}</td>
        <td><span class="badge ${escapeHtml(run.status)}">${escapeHtml(run.status)}</span></td>
        <td class="num">${number(run.files_found)}</td>
        <td class="num">${number(run.files_downloaded)}</td>
        <td class="num">${number(run.files_skipped)}</td>
        <td class="num" title="${escapeHtml(run.error_message || "")}">${failed ? `<b>${number(failed)}</b>` : "0"}</td>
        <td class="muted" title="${escapeHtml(localTime(run.started_at))}">${escapeHtml(shortTime(run.started_at))}</td>
        <td class="muted">${escapeHtml(duration(run.started_at, run.finished_at))}</td>
      </tr>`;
  }).join("") : `<tr><td colspan="11" class="empty-cell">${
    state.runs.length ? "No runs match these filters." : "No runs yet. Your first download will show up here."
  }</td></tr>`;
}

async function loadRuns() {
  try {
    const data = await api("/api/runs?limit=200");
    state.runs = data.runs || [];
    renderRuns();
    updateDeleteRunButton();
  } catch (error) {
    $("#runs").innerHTML = `<tr><td colspan="11" class="empty-cell note">${escapeHtml(error.message)}</td></tr>`;
  }
}

// Failed folders with their reasons (runs from before this was saved: null).
function runFolderFailures(run) {
  try {
    const list = JSON.parse(run.failed_folders || "null");
    return Array.isArray(list) && list.length ? list : null;
  } catch {
    return null;
  }
}

async function loadRunFiles() {
  const run = state.runs.find(r => r.id === state.selectedRun);
  if (!run) return;

  const failedOnly = $("#failedOnly").checked;
  const query = `run_id=${run.id}&limit=500${failedOnly ? "&status=failed" : ""}`;

  $("#runFilesTitle").textContent = `Run #${run.id} · ${run.segment} · ${run.exchange_date || ""}`;
  $("#runFileRows").innerHTML = `<tr><td colspan="6" class="empty-cell"><span class="spinner"></span>Loading files…</td></tr>`;

  try {
    const data = await api(`/api/downloads?${query}`);
    const files = data.downloads || [];

    const folders = runFolderFailures(run);
    const folderNote = folders
      ? folders.map(f => `
        <tr class="folder-fail">
          <td class="file-name">/${escapeHtml(String(f.folder_path || "").replace(/\/$/, ""))}</td>
          <td class="muted">folder</td>
          <td><span class="badge failed">failed</span></td>
          <td class="num">—</td>
          <td class="note">${escapeHtml(f.error || "")}</td>
          <td class="admin-only"></td>
        </tr>`).join("")
      : run.folders_failed
        ? `<tr><td colspan="6" class="note">${escapeHtml(run.error_message)}</td></tr>` : "";

    $("#runFilesTitle").textContent =
      `Run #${run.id} · ${run.segment} · ${run.exchange_date || ""} · ${files.length} file(s)`;

    $("#runFileRows").innerHTML = folderNote + (files.length ? files.map(f => `
      <tr>
        <td class="file-name">${escapeHtml(f.file_name)}</td>
        <td class="file-name muted">/${escapeHtml(String(f.folder_path || "").replace(/\/$/, ""))}</td>
        <td><span class="badge ${escapeHtml(f.status)}">${escapeHtml(f.status.replace("_", " "))}</span></td>
        <td class="num">${escapeHtml(fileSize(f.file_size))}</td>
        <td class="note">${f.status !== "failed"
          ? `<a class="save-link" href="/api/downloads/${f.id}/file" download title="Save a copy to this PC">Save copy</a>`
          : escapeHtml(f.error_message || "")}</td>
        <td class="num admin-only">
          <button type="button" class="danger row-x" data-delete-file="${f.id}" data-name="${escapeHtml(f.file_name)}" title="Delete this record">✕</button>
        </td>
      </tr>`).join("")
      : `<tr><td colspan="6" class="empty-cell">${failedOnly
          ? "No failed files in this run."
          : "No files recorded for this run. Each file shows the latest run that processed it."}</td></tr>`);

  } catch (error) {
    $("#runFileRows").innerHTML = `<tr><td colspan="6" class="empty-cell note">${escapeHtml(error.message)}</td></tr>`;
  }
}

$("#runs").addEventListener("click", (event) => {
  const row = event.target.closest("[data-run]");
  if (!row) return;

  state.selectedRun = Number(row.dataset.run);
  $$("#runs tr").forEach(tr => tr.classList.toggle("selected", tr === row));
  updateDeleteRunButton();
  loadRunFiles();
});

function updateDeleteRunButton() {
  const run = state.runs.find(r => r.id === state.selectedRun);
  $("#deleteRunBtn").disabled = !run || run.status === "running";

  const retry = $("#retryRunBtn");
  if (!retry.classList.contains("loading")) {
    retry.disabled = !run || run.status === "running" || !runFailed(run) || state.busy;
  }
}


// ---------- retry failed ----------

async function retryRun() {
  const run = state.runs.find(r => r.id === state.selectedRun);
  if (!run || state.busy) return;

  state.localBusy = true;
  applyBusy();
  setMood("work", `retrying what failed in run #${run.id}…`);

  await withLoading($("#retryRunBtn"), "Retrying…", async () => {
    try {
      const result = await api(`/api/runs/${run.id}/retry`, {});
      const stillFailed = result.failed || 0;

      toast(
        `Retry of run #${run.id} (new run #${result.run_id}): ${number(result.downloaded)} new, ` +
        `${number(result.already_downloaded)} already had, ${number(stillFailed)} still failed.`,
        stillFailed ? "warn" : "good", 8000,
      );
      setMood(stillFailed ? "worried" : "happy",
        stillFailed ? "some still failed. NSE may need a few minutes." : "all fixed now.");

      await loadRuns();
      state.selectedRun = result.run_id;
      renderRuns();
      loadRunFiles();

    } catch (error) {
      toast(`Retry of run #${run.id}: ${error.message}`, error.status === 409 ? "warn" : "bad", 8000);
      setMood("worried", "the retry didn't go through.");
    }
  });

  state.localBusy = false;
  applyBusy();
}

$("#retryRunBtn").addEventListener("click", retryRun);


// ---------- delete confirmation ----------

let pendingDelete = null;

function openDeleteDialog({ title, message, action }) {
  pendingDelete = action;
  $("#deleteTitle").textContent = title;
  $("#deleteMessage").textContent = message;
  $("#deleteFiles").checked = false;
  $("#deleteError").textContent = "";
  $("#deleteDialog").showModal();
}

function describeRemoved(result) {
  const parts = [];
  if (result.runs) parts.push(`${result.runs} run(s)`);
  parts.push(`${result.file_records} file record(s)`);
  let text = `Deleted ${parts.join(" and ")}`;
  if (result.files) {
    text += `, ${result.files.deleted} file(s) removed from disk (${fileSize(result.files.bytes)})`;
    if (result.files.missing) text += `, ${result.files.missing} already missing`;
  }
  return `${text}.`;
}

$("#deleteCancel").addEventListener("click", () => $("#deleteDialog").close());

$("#deleteForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!pendingDelete) return;

  await withLoading($("#deleteConfirm"), "Deleting…", async () => {
    try {
      const result = await pendingDelete($("#deleteFiles").checked);
      $("#deleteDialog").close();
      toast(describeRemoved(result), "good", 6000);
      await loadRuns();
      if (state.runs.some(r => r.id === state.selectedRun)) {
        loadRunFiles();
      } else {
        state.selectedRun = null;
        updateDeleteRunButton();
        $("#runFilesTitle").textContent = "Files";
        $("#runFileRows").innerHTML = `<tr><td colspan="6" class="empty-cell">Click a run above to see its files.</td></tr>`;
      }
    } catch (error) {
      $("#deleteError").textContent = error.message;
      if (error.status === 401) refreshRights();
    }
  });
});

$("#deleteRunBtn").addEventListener("click", () => {
  const run = state.runs.find(r => r.id === state.selectedRun);
  if (!run) return;

  openDeleteDialog({
    title: `Delete run #${run.id}?`,
    message: `${run.segment} · ${run.exchange_date || ""} · ${runScope(run)}. ` +
      "The run and the file records it last updated are removed from history.",
    action: (deleteFiles) => api(`/api/runs/${run.id}?delete_files=${deleteFiles}`, undefined, "DELETE"),
  });
});

$("#runFileRows").addEventListener("click", (event) => {
  const button = event.target.closest("[data-delete-file]");
  if (!button) return;

  openDeleteDialog({
    title: "Delete file record?",
    message: `${button.dataset.name}: the record is removed from history.`,
    action: (deleteFiles) => api(`/api/downloads/${button.dataset.deleteFile}?delete_files=${deleteFiles}`, undefined, "DELETE"),
  });
});


// ---------- clean up ----------

function cleanupPayload(dryRun) {
  return {
    segment: $("#cSegment").value || null,
    date_from: $("#cFrom").value,
    date_to: $("#cTo").value,
    delete_files: $("#cFiles").checked,
    dry_run: dryRun,
  };
}

function resetCleanupPreview() {
  $("#cDelete").disabled = true;
  $("#cPreview").textContent = "Press “Preview” to see what would be deleted.";
}

$("#cleanupBtn").addEventListener("click", () => {
  if (!$("#cFrom").value) $("#cFrom").value = localIsoDate(-30);
  if (!$("#cTo").value) $("#cTo").value = localIsoDate(-7);
  $("#cError").textContent = "";
  resetCleanupPreview();
  $("#cleanupDialog").showModal();
});

["#cSegment", "#cFrom", "#cTo", "#cFiles"].forEach(id =>
  $(id).addEventListener("change", resetCleanupPreview));

$("#cCancel").addEventListener("click", () => $("#cleanupDialog").close());

$("#cPreviewBtn").addEventListener("click", (event) =>
  withLoading(event.currentTarget, "Checking…", async () => {
    $("#cError").textContent = "";
    try {
      const preview = await api("/api/history/cleanup", cleanupPayload(true));
      const files = $("#cFiles").checked
        ? ` and <b>${number(preview.files_on_record)}</b> file(s) on disk (about ${escapeHtml(fileSize(preview.bytes))})`
        : "";
      $("#cPreview").innerHTML = (preview.runs || preview.file_records)
        ? `Will delete <b>${number(preview.runs)}</b> run(s), <b>${number(preview.file_records)}</b> file record(s)${files}.`
        : "Nothing matches. Nothing will be deleted.";
      $("#cDelete").disabled = !(preview.runs || preview.file_records);
    } catch (error) {
      $("#cError").textContent = error.message;
      if (error.status === 401) refreshRights();
    }
  })
);

$("#cleanupForm").addEventListener("submit", async (event) => {
  event.preventDefault();

  await withLoading($("#cDelete"), "Deleting…", async () => {
    try {
      const result = await api("/api/history/cleanup", cleanupPayload(false));
      $("#cleanupDialog").close();
      toast(describeRemoved(result), "good", 7000);
      state.selectedRun = null;
      updateDeleteRunButton();
      loadRuns();
    } catch (error) {
      $("#cError").textContent = error.message;
    }
  });

  $("#cDelete").disabled = true;
});

$("#failedOnly").addEventListener("change", loadRunFiles);
$("#runsSegment").addEventListener("change", renderRuns);
$("#problemsOnly").addEventListener("change", renderRuns);

$("#refreshRuns").addEventListener("click", (event) =>
  withLoading(event.currentTarget, "Refreshing…", async () => {
    await loadRuns();
    if (state.selectedRun) await loadRunFiles();
    toast("Runs refreshed.", "info", 2000);
  })
);


// ==========================================================
// STATUS POLLING (scheduled runs, other tabs)
// ==========================================================

async function pollStatus() {
  let status;

  try {
    status = await api("/api/status");
  } catch {
    $("#apiStatus").className = "pill down";
    $("#apiStatus").innerHTML = "<i></i> App not reachable";
    return;
  }

  if ($("#apiStatus").classList.contains("down")) checkHealth();

  if (status.server_time) {
    state.serverOffsetMs = new Date(status.server_time) - Date.now();
    state.timezone = status.timezone || "";
  }

  const wasRemote = state.remoteBusy;
  const remote = status.busy && !state.localBusy;

  $("#busyBanner").classList.toggle("hidden", !remote);

  if (status.busy && status.current) {
    const label = status.current.kind === "scheduled" ? "Scheduled run" : "NSE in use";
    $("#busyText").textContent = status.current.stopping
      ? `Stopping after the current file: ${status.current.description}`
      : `${label}: ${status.current.description}`;

    // Stopping a scheduled run needs the admin (when one is set).
    const stopBtn = $("#bannerStop");
    const allowed = status.current.kind !== "scheduled" || state.canEdit;
    if (!stopBtn.classList.contains("loading")) {
      stopBtn.disabled = !allowed || Boolean(status.current.stopping);
    }
    stopBtn.title = allowed ? "Stop after the current file" : "Only the admin can stop a scheduled run.";
  }

  if (remote !== wasRemote) {
    state.remoteBusy = remote;
    applyBusy();

    if (remote) {
      setMood("work", status.current.kind === "scheduled"
        ? "a scheduled run is fetching files…"
        : "someone else is using NSE right now…");
      if (status.current.kind === "scheduled") toast(`Scheduled run started: ${status.current.description}`, "info");
    } else {
      setMood("happy", "background run finished. see Runs.");
      toast("Background run finished. Runs updated.", "good");
      loadRuns();
    }

    loadSchedules();
  } else if (remote && status.current) {
    $("#catSays").textContent = status.current.description;
  }
}


// ==========================================================
// SCHEDULES
// ==========================================================

function describeDays(days) {
  const key = days.join(",");
  if (key === "0,1,2,3,4") return "Weekdays";
  if (key === "0,1,2,3,4,5,6") return "Every day";
  if (key === "5,6") return "Weekends";
  return days.map(d => DAY_NAMES[d]).join(", ");
}

function describeDates(daysBack) {
  return daysBack === 0 ? "that day only" : `that day + ${daysBack} back`;
}

function describeRetries(s) {
  if (!s.retry_count) return "no retries";
  return `${s.retry_count}× retry / ${s.retry_delay_minutes} min`;
}

function scheduleCard(s) {
  const scope = s.scope === "folder" ? s.folders.join(", ")
    : s.scope === "list" ? "folder list"
    : "whole segment";
  const locked = state.canEdit ? "" : 'disabled title="Only the admin can change schedules"';

  const next = s.enabled
    ? (s.next_run_at
        ? `Next: <b>${escapeHtml(relativeDay(s.next_run_at))}</b>${s.next_run_kind === "retry" ? " (retry)" : ""}`
        : "No upcoming run")
    : "Paused";

  const status = s.queued && !["running", "waiting"].includes(s.last_status) ? "queued" : s.last_status;
  const last = status
    ? `<span class="badge ${escapeHtml(status)}">${escapeHtml(status)}</span> ${escapeHtml(s.last_summary || "")}` +
      (s.last_run_finished && !["running", "waiting", "queued"].includes(status)
        ? ` <span class="muted">· ${escapeHtml(relativeDay(s.last_run_finished))}</span>` : "")
    : `<span class="muted">Has not run yet.</span>`;

  return `
    <div class="schedule ${s.enabled ? "" : "off"}" data-schedule="${s.id}">
      <div class="top">
        <span class="switch" title="${s.enabled ? "Pause" : "Enable"} this schedule">
          <input type="checkbox" data-action="toggle" ${s.enabled ? "checked" : ""} ${locked} aria-label="Enable ${escapeHtml(s.name)}">
          <span></span>
        </span>
        <span class="clock">${escapeHtml(s.time)}</span>
        <span class="name" title="${escapeHtml(s.name)}">${escapeHtml(s.name)}</span>
        <div class="buttons">
          <button type="button" class="secondary" data-action="run" ${s.queued ? "disabled" : ""} title="Run this schedule now">Run</button>
          <button type="button" class="secondary" data-action="edit" ${locked}>Edit</button>
          <button type="button" class="danger" data-action="delete" ${locked || 'title="Delete schedule"'}>✕</button>
        </div>
      </div>
      <div class="meta">
        ${escapeHtml(describeDays(s.days))} · ${escapeHtml(s.segments.join(", "))} · ${escapeHtml(scope)}<br>
        ${escapeHtml(describeDates(s.days_back))} · ${escapeHtml(describeRetries(s))} · catch-up ${s.catch_up ? "on" : "off"}
      </div>
      <div class="next">${next}</div>
      <div class="last">${last}</div>
    </div>`;
}

async function loadSchedules() {
  try {
    const data = await api("/api/schedules");
    state.schedules = data.schedules || [];
    state.defaults = data.defaults;

    const rights = !state.adminConfigured
      ? " · open to everyone"
      : state.isAdmin ? " · admin" : " · view only";

    $("#schedulerInfo").textContent = (data.scheduler_running
      ? `Scheduler running · ${data.timezone}`
      : "Scheduler is off (NSE_SCHEDULER=off)") + rights;
    $("#schedulerInfo").title = !state.adminConfigured
      ? "Set ADMIN_PASSWORD in .env so only the admin can change schedules."
      : "";

    $("#scheduleList").innerHTML = state.schedules.length
      ? state.schedules.map(scheduleCard).join("")
      : `<div class="empty">
           <pre aria-hidden="true">${escapeHtml(cat("( -.- )", ["", " z", ""]))}</pre>
           <span>No schedules yet. Press <b>+ Add</b> and files arrive on their own, for example every weekday at 23:00.</span>
         </div>`;

  } catch (error) {
    $("#scheduleList").innerHTML = `<p class="note">${escapeHtml(error.message)}</p>`;
  }
}

function schedulePayload(s, overrides = {}) {
  return {
    name: s.name, enabled: s.enabled, time: s.time, days: s.days,
    segments: s.segments, scope: s.scope, folders: s.folders,
    days_back: s.days_back, retry_count: s.retry_count,
    retry_delay_minutes: s.retry_delay_minutes, catch_up: s.catch_up,
    ...overrides,
  };
}

function saveSchedule(id, payload) {
  return id ? api(`/api/schedules/${id}`, payload, "PUT") : api("/api/schedules", payload);
}

$("#scheduleList").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-action]");
  const card = event.target.closest("[data-schedule]");
  if (!button || !card) return;

  const schedule = state.schedules.find(s => s.id === Number(card.dataset.schedule));
  if (!schedule) return;

  const action = button.dataset.action;

  if (action === "edit") {
    openScheduleDialog(schedule);

  } else if (action === "delete") {
    if (!window.confirm(`Delete the schedule "${schedule.name}"?\nPast runs stay in the history.`)) return;

    await withLoading(button, "…", async () => {
      try {
        await api(`/api/schedules/${schedule.id}`, undefined, "DELETE");
        toast(`Deleted "${schedule.name}".`, "info");
        setMood("sleep", `deleted "${schedule.name}".`);
      } catch (error) {
        if (!handleAuthError(error)) toast(error.message, "bad");
      }
    });
    loadSchedules();

  } else if (action === "run") {
    await withLoading(button, "Queuing…", async () => {
      try {
        await api(`/api/schedules/${schedule.id}/run`, {});
        toast(`"${schedule.name}" queued. It starts as soon as NSE is free.`, "good");
        setMood("work", `queued "${schedule.name}"…`);
      } catch (error) {
        toast(error.message, error.status === 409 ? "warn" : "bad");
      }
    });
    loadSchedules();
    setTimeout(pollStatus, 700);
  }
});

$("#scheduleList").addEventListener("change", async (event) => {
  const toggle = event.target.closest("[data-action='toggle']");
  const card = event.target.closest("[data-schedule]");
  if (!toggle || !card) return;

  const schedule = state.schedules.find(s => s.id === Number(card.dataset.schedule));
  toggle.disabled = true;

  try {
    await saveSchedule(schedule.id, schedulePayload(schedule, { enabled: toggle.checked }));
    toast(toggle.checked ? `"${schedule.name}" is on.` : `"${schedule.name}" is paused.`, toggle.checked ? "good" : "info");
    setMood(toggle.checked ? "happy" : "sleep", toggle.checked ? `"${schedule.name}" is on.` : `"${schedule.name}" paused.`);
  } catch (error) {
    toggle.checked = !toggle.checked;
    if (!handleAuthError(error)) toast(error.message, "bad");
  }

  loadSchedules();
});


// ---------- editor dialog ----------

const editor = { days: new Set(), segments: new Set() };

function fillSelect(select, options, value) {
  select.innerHTML = options.map(([v, label]) => `<option value="${v}">${escapeHtml(label)}</option>`).join("");
  select.value = String(value);
}

function editorScope() {
  return document.querySelector("input[name='sScope']:checked").value;
}

function renderEditorChips() {
  $("#sDays").innerHTML = DAY_NAMES.map((name, i) => `
    <button type="button" class="chip" data-day-index="${i}" aria-pressed="${editor.days.has(i)}">${name}</button>`).join("");

  const allOn = state.segments.every(s => editor.segments.has(s));
  $("#sSegments").innerHTML =
    state.segments.map(s => `
      <button type="button" class="chip" data-seg="${escapeHtml(s)}" aria-pressed="${editor.segments.has(s)}">${escapeHtml(s)}</button>`).join("") +
    `<button type="button" class="chip" data-seg="*" aria-pressed="${allOn}">All</button>`;

  updateHint();
}

function updateHint() {
  const days = [...editor.days].sort();
  const time = $("#sTime").value || "--:--";
  const back = Number($("#sDaysBack").value || 0);
  const segs = state.segments.filter(s => editor.segments.has(s));

  $("#sFoldersWrap").classList.toggle("hidden", editorScope() !== "folder");
  $("#sListInfo").classList.toggle("hidden", editorScope() !== "list");

  if (editorScope() === "list") {
    $("#sListInfo").textContent = segs.length
      ? "Uses each segment's saved list at run time: " + segs.map(s => {
          const n = (state.lists[s] || []).length;
          return `${s} ${n ? `(${n} folder${n > 1 ? "s" : ""})` : "(empty, will fail)"}`;
        }).join(", ") + ". Change the lists in Browse NSE."
      : "";
  }

  $("#sHint").textContent = (!days.length || !segs.length) ? "" :
    `${describeDays(days)} at ${time}: download ${segs.join(", ")} ` +
    `for ${back ? `that day and the ${back} day${back > 1 ? "s" : ""} before` : "that day"}. ` +
    "Files already downloaded are skipped, so re-checking earlier days is cheap and catches late uploads.";
}

function openScheduleDialog(schedule = null) {
  const s = schedule || state.defaults || {
    name: "Nightly", enabled: true, time: "23:00", days: [0, 1, 2, 3, 4], segments: ["CM"],
    scope: "segment", folders: [], days_back: 1, retry_count: 2, retry_delay_minutes: 15, catch_up: true,
  };

  state.editingId = schedule ? schedule.id : null;
  editor.days = new Set(s.days);
  editor.segments = new Set(s.segments);

  $("#scheduleDialogTitle").textContent = schedule ? `Edit "${schedule.name}"` : "Add schedule";
  $("#sName").value = s.name;
  $("#sEnabled").checked = s.enabled;
  $("#sTime").value = s.time;
  $("#sFolders").value = (s.folders || []).join("\n");
  $("#sCatchUp").checked = s.catch_up;
  document.querySelector(`input[name='sScope'][value='${s.scope}']`).checked = true;

  fillSelect($("#sDaysBack"),
    [0, 1, 2, 3, 4, 5, 6, 7].map(n => [n, n === 0 ? "That day only" : `That day + ${n} back`]), s.days_back);
  fillSelect($("#sRetries"),
    [0, 1, 2, 3, 4, 5].map(n => [n, n === 0 ? "No retries" : `${n} time${n > 1 ? "s" : ""}`]), s.retry_count);
  fillSelect($("#sRetryDelay"),
    [5, 10, 15, 30, 60, 120].map(n => [n, n < 60 ? `${n} minutes` : `${n / 60} hour${n > 60 ? "s" : ""}`]),
    [5, 10, 15, 30, 60, 120].includes(s.retry_delay_minutes) ? s.retry_delay_minutes : 15);

  $("#sError").textContent = "";
  renderEditorChips();
  $("#scheduleDialog").showModal();
  $("#sName").focus();
}

$("#addSchedule").addEventListener("click", () => openScheduleDialog());
$("#sCancel").addEventListener("click", () => $("#scheduleDialog").close());

$("#sDays").addEventListener("click", (event) => {
  const chip = event.target.closest("[data-day-index]");
  if (!chip) return;
  const day = Number(chip.dataset.dayIndex);
  editor.days.has(day) ? editor.days.delete(day) : editor.days.add(day);
  renderEditorChips();
});

$$("[data-days]").forEach(button => {
  button.addEventListener("click", () => {
    editor.days = new Set(button.dataset.days === "weekdays" ? [0, 1, 2, 3, 4] : [0, 1, 2, 3, 4, 5, 6]);
    renderEditorChips();
  });
});

$("#sSegments").addEventListener("click", (event) => {
  const chip = event.target.closest("[data-seg]");
  if (!chip) return;
  const seg = chip.dataset.seg;

  if (seg === "*") {
    const allOn = state.segments.every(s => editor.segments.has(s));
    editor.segments = new Set(allOn ? [] : state.segments);
  } else {
    editor.segments.has(seg) ? editor.segments.delete(seg) : editor.segments.add(seg);
  }
  renderEditorChips();
});

["#sTime", "#sDaysBack"].forEach(id => $(id).addEventListener("input", updateHint));
$$("input[name='sScope']").forEach(r => r.addEventListener("change", updateHint));

$("#scheduleForm").addEventListener("submit", async (event) => {
  event.preventDefault();

  const payload = {
    name: $("#sName").value.trim() || "Schedule",
    enabled: $("#sEnabled").checked,
    time: $("#sTime").value,
    days: [...editor.days].sort(),
    segments: state.segments.filter(s => editor.segments.has(s)),
    scope: editorScope(),
    folders: editorScope() === "folder"
      ? $("#sFolders").value.split("\n").map(f => f.trim()).filter(Boolean).map(normalisePath)
      : [],
    days_back: Number($("#sDaysBack").value),
    retry_count: Number($("#sRetries").value),
    retry_delay_minutes: Number($("#sRetryDelay").value),
    catch_up: $("#sCatchUp").checked,
  };

  $("#sError").textContent = "";

  await withLoading($("#sSave"), "Saving…", async () => {
    try {
      await saveSchedule(state.editingId, payload);
      $("#scheduleDialog").close();
      toast(`Saved "${payload.name}" (${describeDays(payload.days)} at ${payload.time}).`, "good");
      setMood("happy", `saved. I'll wake up at ${payload.time}.`);
      loadSchedules();
    } catch (error) {
      $("#sError").textContent = error.message;
      if (error.status === 401) refreshRights();
    }
  });
});


// ==========================================================
// START
// ==========================================================

setMood("sleep", "zzz… pick a date and segments, I'll fetch the files.");
renderCrumbs("/");
checkHealth();
Promise.all([loadSegments(), loadAdmin()]).then(() => {
  loadLists();
  loadSchedules();
  showTab(store.get("tab", "runs"));
});
loadRuns();
pollStatus();
tickClock();

setInterval(pollStatus, 4000);
setInterval(tickClock, 1000);
// New log lines: every 2 s while the Log tab is open, otherwise every 10 s
// (to count new warnings on the tab).
setInterval(() => { if (logTabOpen()) loadLog(); }, 2000);
setInterval(() => { if (!logTabOpen()) loadLog(); }, 10000);
loadLog();
setInterval(() => { if (!state.busy) loadSchedules(); }, 60000);
