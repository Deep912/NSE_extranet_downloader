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
      <tr>
        <td>${escapeHtml(item.segment)}</td>
        <td>${escapeHtml(item.exchange_date || "—")}</td>
        <td>${escapeHtml(item.file_name)}</td>
        <td>${escapeHtml(item.status)}</td>
        <td>${escapeHtml(item.downloaded_at || "—")}</td>
      </tr>`).join("");
  } catch {
    body.innerHTML = '<tr><td colspan="5">Could not load history.</td></tr>';
  }
}
function escapeHtml(value){
  return String(value).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
document.getElementById("refresh").addEventListener("click", loadDownloads);
loadDownloads();
