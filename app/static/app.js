
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }[c]));
}


// --------------------------------------------------
// LOAD SUPPORTED SEGMENTS
// --------------------------------------------------

async function loadSegments() {
  const select = document.getElementById("segment");

  try {
    const response = await fetch("/api/segments");

    if (!response.ok) {
      throw new Error(`Request failed (${response.status})`);
    }

    const data = await response.json();
    const segments = data.segments || [];

    const previousValue = select.value;

    select.innerHTML = segments.map(segment =>
      `<option value="${escapeHtml(segment)}">${escapeHtml(segment)}</option>`
    ).join("");

    if (segments.includes(previousValue)) {
      select.value = previousValue;
    } else if (segments.includes("CM")) {
      select.value = "CM";
    }

  } catch (error) {
    console.error("Could not load segments:", error);
  }
}


// --------------------------------------------------
// LOAD DOWNLOAD HISTORY
// --------------------------------------------------

async function loadDownloads() {
  const body = document.getElementById("downloads");

  try {
    const response = await fetch("/api/downloads");

    if (!response.ok) {
      throw new Error(`Request failed (${response.status})`);
    }

    const data = await response.json();
    const downloads = data.downloads;

    if (!Array.isArray(downloads)) {
      throw new Error("Invalid download history response.");
    }

    if (!downloads.length) {
      body.innerHTML =
        '<tr><td colspan="5" class="muted">No download records yet.</td></tr>';
      return;
    }

    body.innerHTML = downloads.map(item => `
      <tr>
        <td>${escapeHtml(item.segment)}</td>
        <td>${escapeHtml(item.exchange_date || "—")}</td>
        <td>${escapeHtml(item.file_name)}</td>
        <td>${escapeHtml(item.status)}</td>
        <td>${escapeHtml(item.downloaded_at || "—")}</td>
      </tr>
    `).join("");

  } catch (error) {
    console.error("Download history error:", error);

    body.innerHTML =
      '<tr><td colspan="5" class="muted">Could not load history.</td></tr>';
  }
}


// --------------------------------------------------
// LIST NSE FOLDER
// --------------------------------------------------

async function listNSEFolder() {
  const button = document.getElementById("listFolders");
  const message = document.getElementById("message");
  const body = document.getElementById("folderItems");

  const segment = document.getElementById("segment").value;
  const folderPath =
    document.getElementById("folderPath").value.trim() || "/";

  button.disabled = true;

  message.textContent =
    "Connecting to NSE LIVE and requesting folder contents…";

  body.innerHTML =
    '<tr><td colspan="4" class="muted">Waiting for NSE response…</td></tr>';

  try {
    const response = await fetch("/api/nse/list", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        segment: segment,
        folder_path: folderPath
      })
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(
        data.detail || `Request failed (${response.status})`
      );
    }

    const items = Array.isArray(data.items) ? data.items : [];

    document.getElementById("listingCaption").textContent =
      `${data.segment} · ${data.folder_path} · ${items.length} item(s)`;

    if (!items.length) {
      body.innerHTML =
        '<tr><td colspan="4" class="muted">NSE returned an empty folder.</td></tr>';
    } else {
      body.innerHTML = items.map(item => `
        <tr>
          <td>${escapeHtml(item.type || "—")}</td>
          <td>${escapeHtml(item.name || "—")}</td>
          <td>${escapeHtml(item.folderPath || "—")}</td>
          <td>${escapeHtml(item.lastUpdated || "—")}</td>
        </tr>
      `).join("");
    }

    message.textContent =
      "NSE LIVE folder listing completed. No files were downloaded.";

  } catch (error) {
    console.error("NSE listing error:", error);

    message.textContent = `NSE request failed: ${error.message}`;

    body.innerHTML =
      '<tr><td colspan="4" class="muted">No listing available.</td></tr>';

  } finally {
    button.disabled = false;
  }
}


// --------------------------------------------------
// EVENT LISTENERS
// --------------------------------------------------

document.getElementById("refresh")
  .addEventListener("click", loadDownloads);

document.getElementById("listFolders")
  .addEventListener("click", listNSEFolder);


// --------------------------------------------------
// INITIALISE APPLICATION
// --------------------------------------------------

async function initialiseApp() {
  await loadSegments();
  await loadDownloads();
}

initialiseApp();
