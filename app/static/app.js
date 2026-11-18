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
}


// ==========================================================
// TABS
// ==========================================================

function showTab(name) {
  const runs = name === "runs";

  $("#tabRuns").setAttribute("aria-selected", String(runs));
  $("#tabBrowse").setAttribute("aria-selected", String(!runs));
  $("#panelRuns").classList.toggle("hidden", !runs);
  $("#panelBrowse").classList.toggle("hidden", runs);
  $("#runsTools").classList.toggle("hidden", !runs);

  store.set("tab", name);

  if (!runs && !state.browseLoaded && !state.busy) openFolder("/");
}

$("#tabRuns").addEventListener("click", () => showTab("runs"));
$("#tabBrowse").addEventListener("click", () => showTab("browse"));


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
  store.set("scope", currentScope());
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
  const folderPath = currentScope() === "folder" ? normalisePath($("#scopeFolder").value) : "/";
  const button = $("#startDownload");
  const started = Date.now();

  state.localBusy = true;
  applyBusy();

  const timer = setInterval(() => {
    $("#downloadTimer").textContent = `Running for ${seconds((Date.now() - started) / 1000)}…`;
  }, 1000);
  $("#downloadTimer").textContent = "Starting…";

  $("#downloadProgress").innerHTML = segments.map(s => progressRow(s, "waiting")).join("");

  const totals = { downloaded: 0, already: 0, failed: 0, errors: 0 };

  await withLoading(button, `Downloading ${segments.length > 1 ? `${segments.length} segments` : segments[0]}…`, async () => {
    for (const segment of segments) {
      const where = folderPath === "/" ? segment : `${segment}${folderPath}`;
      const segmentStart = Date.now();

      setMood("work", `fetching ${where} for ${date}…`);
      setRow(segment, progressRow(segment, "running", `<span class="muted small">listing folders and downloading…</span>`));

      try {
        const result = await api("/api/nse/download-folder", { segment, folder_path: folderPath, date });

        totals.downloaded += result.downloaded || 0;
        totals.already += result.already_downloaded || 0;
        totals.failed += result.failed || 0;

        setRow(segment, progressRow(segment, result.status,
          resultBody(result, seconds((Date.now() - segmentStart) / 1000))));

      } catch (error) {
        totals.errors += 1;
        setRow(segment, progressRow(segment, "failed",
          `<ul class="errors"><li>${escapeHtml(error.message)}</li></ul>`));
      }

      loadRuns();
    }
  });

  clearInterval(timer);
  $("#downloadTimer").textContent = `Finished in ${seconds((Date.now() - started) / 1000)}.`;
  state.localBusy = false;
  applyBusy();

  if (totals.errors === segments.length) {
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

  const segment = $("#browseSegment").value;
  path = normalisePath(path);

  state.browsePath = path;
  state.browseLoaded = true;
  $("#browsePath").value = path;
  renderCrumbs(path);

  $("#browseItems").innerHTML =
    `<tr><td colspan="2" class="empty-cell"><span class="spinner"></span>Asking NSE for ${escapeHtml(segment)}${escapeHtml(path === "/" ? "" : path)}…</td></tr>`;
  setMood("sniff", `sniffing around ${segment}${path === "/" ? "" : path}…`);

  await withLoading($("#browseGo"), "Opening…", async () => {
    try {
      const data = await api("/api/nse/list", { segment, folder_path: path });
      const items = Array.isArray(data.items) ? data.items : [];
      const isFolder = (item) => String(item.type).toLowerCase() === "folder";

      const folders = items.filter(isFolder).sort((a, b) => a.name.localeCompare(b.name));
      const files = items.filter(i => !isFolder(i))
        .sort((a, b) => String(b.lastUpdated).localeCompare(String(a.lastUpdated)));

      $("#browseItems").innerHTML = (folders.length || files.length) ? [
        ...folders.map(f => `
          <tr>
            <td><button type="button" class="folder-link" data-folder="${escapeHtml(f.name)}">${escapeHtml(f.name)}</button></td>
            <td class="muted" title="UTC: ${escapeHtml(f.lastUpdated)}">${escapeHtml(localTime(f.lastUpdated))}</td>
          </tr>`),
        ...files.map(f => `
          <tr>
            <td class="file-name">${escapeHtml(f.name)}</td>
            <td class="muted" title="UTC: ${escapeHtml(f.lastUpdated)}">${escapeHtml(localTime(f.lastUpdated))}</td>
          </tr>`),
      ].join("") : `<tr><td colspan="2" class="empty-cell">This folder is empty.</td></tr>`;

      $("#browseInfo").textContent =
        `${segment}${path === "/" ? "" : path} · ${folders.length} folder(s) · ${files.length} file(s). Times are local; hover for UTC.`;
      setMood("sleep", `${folders.length} folder(s), ${files.length} file(s) here. back to napping.`);

    } catch (error) {
      $("#browseItems").innerHTML = `<tr><td colspan="2" class="empty-cell note">${escapeHtml(error.message)}</td></tr>`;
      setMood("worried", "NSE didn't answer that one.");
      toast(error.message, error.status === 409 ? "warn" : "bad", 7000);
    }
  });
}

$("#browseGo").addEventListener("click", () => openFolder($("#browsePath").value));
$("#browsePath").addEventListener("keydown", (event) => {
  if (event.key === "Enter") openFolder($("#browsePath").value);
});
$("#browseSegment").addEventListener("change", () => openFolder("/"));

$("#crumbs").addEventListener("click", (event) => {
  const crumb = event.target.closest("[data-path]");
  if (crumb) openFolder(crumb.dataset.path);
});

$("#browseItems").addEventListener("click", (event) => {
  const link = event.target.closest("[data-folder]");
  if (link) openFolder(joinPath(state.browsePath, link.dataset.folder));
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
        <td class="file-name">${escapeHtml(runScope(run))}${run.mode === "scheduled" ? '<span class="tag">scheduled</span>' : ""}</td>
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
  } catch (error) {
    $("#runs").innerHTML = `<tr><td colspan="11" class="empty-cell note">${escapeHtml(error.message)}</td></tr>`;
  }
}

async function loadRunFiles() {
  const run = state.runs.find(r => r.id === state.selectedRun);
  if (!run) return;

  const failedOnly = $("#failedOnly").checked;
  const query = `run_id=${run.id}&limit=500${failedOnly ? "&status=failed" : ""}`;

  $("#runFilesTitle").textContent = `Run #${run.id} · ${run.segment} · ${run.exchange_date || ""}`;
  $("#runFileRows").innerHTML = `<tr><td colspan="5" class="empty-cell"><span class="spinner"></span>Loading files…</td></tr>`;

  try {
    const data = await api(`/api/downloads?${query}`);
    const files = data.downloads || [];

    const folderNote = run.folders_failed
      ? `<tr><td colspan="5" class="note">${escapeHtml(run.error_message)}</td></tr>` : "";

    $("#runFilesTitle").textContent =
      `Run #${run.id} · ${run.segment} · ${run.exchange_date || ""} · ${files.length} file(s)`;

    $("#runFileRows").innerHTML = folderNote + (files.length ? files.map(f => `
      <tr>
        <td class="file-name">${escapeHtml(f.file_name)}</td>
        <td class="file-name muted">/${escapeHtml(String(f.folder_path || "").replace(/\/$/, ""))}</td>
        <td><span class="badge ${escapeHtml(f.status)}">${escapeHtml(f.status.replace("_", " "))}</span></td>
        <td class="num">${escapeHtml(fileSize(f.file_size))}</td>
        <td class="note">${escapeHtml(f.error_message || "")}</td>
      </tr>`).join("")
      : `<tr><td colspan="5" class="empty-cell">${failedOnly
          ? "No failed files in this run."
          : "No files recorded for this run. Each file shows the latest run that processed it."}</td></tr>`);

  } catch (error) {
    $("#runFileRows").innerHTML = `<tr><td colspan="5" class="empty-cell note">${escapeHtml(error.message)}</td></tr>`;
  }
}

$("#runs").addEventListener("click", (event) => {
  const row = event.target.closest("[data-run]");
  if (!row) return;

  state.selectedRun = Number(row.dataset.run);
  $$("#runs tr").forEach(tr => tr.classList.toggle("selected", tr === row));
  loadRunFiles();
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
    $("#busyText").textContent = `${label}: ${status.current.description}`;
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
  const scope = s.scope === "folder" ? s.folders.join(", ") : "whole segment";

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
          <input type="checkbox" data-action="toggle" ${s.enabled ? "checked" : ""} aria-label="Enable ${escapeHtml(s.name)}">
          <span></span>
        </span>
        <span class="clock">${escapeHtml(s.time)}</span>
        <span class="name" title="${escapeHtml(s.name)}">${escapeHtml(s.name)}</span>
        <div class="buttons">
          <button type="button" class="secondary" data-action="run" ${s.queued ? "disabled" : ""} title="Run this schedule now">Run</button>
          <button type="button" class="secondary" data-action="edit">Edit</button>
          <button type="button" class="danger" data-action="delete" title="Delete schedule">✕</button>
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

    $("#schedulerInfo").textContent = data.scheduler_running
      ? `Scheduler running · ${data.timezone}`
      : "Scheduler is off (NSE_SCHEDULER=off)";

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
        toast(error.message, "bad");
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
    toast(error.message, "bad");
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
    }
  });
});


// ==========================================================
// START
// ==========================================================

setMood("sleep", "zzz… pick a date and segments, I'll fetch the files.");
renderCrumbs("/");
checkHealth();
loadSegments().then(() => {
  loadSchedules();
  showTab(store.get("tab", "runs"));
});
loadRuns();
pollStatus();
tickClock();

setInterval(pollStatus, 4000);
setInterval(tickClock, 1000);
setInterval(() => { if (!state.busy) loadSchedules(); }, 60000);
