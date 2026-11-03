
async function loadDownloads(){
  const body = document.getElementById("downloads");
  try {
    const response = await fetch("/api/downloads");
    const data = await response.json();
    if (!data.downloads.length) {
      body.innerHTML = '<tr><td colspan="5" class="muted">No download records yet.</td></tr>';
      return;
    }
    body.innerHTML = data.downloads.map(item => `
      <tr><td>${escapeHtml(item.segment)}</td><td>${escapeHtml(item.exchange_date || "—")}</td>
      <td>${escapeHtml(item.file_name)}</td><td>${escapeHtml(item.status)}</td>
      <td>${escapeHtml(item.downloaded_at || "—")}</td></tr>`).join("");
  } catch {
    body.innerHTML = '<tr><td colspan="5">Could not load history.</td></tr>';
  }
}

async function listNSEFolder(){
  const button = document.getElementById("listFolders");
  const message = document.getElementById("message");
  const body = document.getElementById("folderItems");
  const folderPath = document.getElementById("folderPath").value.trim() || "/";
  button.disabled = true;
  message.textContent = "Connecting to NSE LIVE and requesting folder contents…";
  body.innerHTML = '<tr><td colspan="4" class="muted">Waiting for NSE response…</td></tr>';
  try {
    const response = await fetch("/api/nse/list", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({segment: "CM", folder_path: folderPath})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `Request failed (${response.status})`);
    const items = data.items || [];
    document.getElementById("listingCaption").textContent = `${data.segment} · ${data.folder_path} · ${items.length} item(s)`;
    if (!items.length) {
      body.innerHTML = '<tr><td colspan="4" class="muted">NSE returned an empty folder.</td></tr>';
    } else {
      body.innerHTML = items.map(item => `<tr>
        <td>${escapeHtml(item.type || "—")}</td><td>${escapeHtml(item.name || "—")}</td>
        <td>${escapeHtml(item.folderPath || "—")}</td><td>${escapeHtml(item.lastUpdated || "—")}</td>
      </tr>`).join("");
    }
    message.textContent = "NSE LIVE folder listing completed. No files were downloaded.";
  } catch (error) {
    message.textContent = `NSE request failed: ${error.message}`;
    body.innerHTML = '<tr><td colspan="4" class="muted">No listing available.</td></tr>';
  } finally {
    button.disabled = false;
  }
}

function escapeHtml(value){
  return String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

document.getElementById("refresh").addEventListener("click", loadDownloads);
document.getElementById("listFolders").addEventListener("click", listNSEFolder);
loadDownloads();
