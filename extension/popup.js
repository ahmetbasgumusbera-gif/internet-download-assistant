const DEFAULTS = { enabled: true, port: 17653, autoLaunch: true, captureViewable: false, sniffVideos: true,
  browserFallback: true, shareYoutubeCookies: true };
const $ = (id) => document.getElementById(id);

const versionKey = (text) => String(text || "0").split(".").map((n) => parseInt(n, 10) || 0);
function isOlder(mine, latest) {
  const a = versionKey(mine), b = versionKey(latest);
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    if ((a[i] || 0) !== (b[i] || 0)) return (a[i] || 0) < (b[i] || 0);
  }
  return false;
}

async function showStatus() {
  const response = await chrome.runtime.sendMessage({ type: "ping" });
  const online = Boolean(response && response.ok);
  const latest = online && response.extensionVersion;
  const note = $("update");
  if (latest && isOlder(chrome.runtime.getManifest().version, latest)) {
    note.textContent = `Eklentinin yeni sürümü var (v${latest}). chrome://extensions sayfasında eklentinin ↻ düğmesine bas.`;
    note.style.display = "block";
  } else {
    note.style.display = "none";
  }
  $("dot").classList.toggle("on", online);
  $("status").textContent = online
    ? "Uygulamaya bağlı"
    : "Uygulama kapalı ya da sunucu açık değil";
}

async function collectLinks() {
  const out = $("linksResult");
  out.textContent = "Taranıyor…";
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const reply = tab ? await chrome.runtime.sendMessage({ type: "collect-links", tabId: tab.id }) : null;
  if (reply && reply.ok) out.textContent = "Uygulamaya gönderildi ✓";
  else if (reply && reply.empty) out.textContent = "Bu sayfada indirilebilir dosya bağlantısı bulunamadı.";
  else out.textContent = "Gönderilemedi (uygulama kapalı ya da sayfayı Ctrl+Shift+R ile yenile).";
}

async function findVideos() {
  const out = $("findResult");
  out.textContent = "Gönderiliyor…";
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const reply = tab ? await chrome.runtime.sendMessage({ type: "wv-open", tabId: tab.id }) : null;
  out.textContent = reply && reply.ok ? "Uygulamaya gönderildi ✓" : "Gönderilemedi (uygulama kapalı ya da bu sayfa desteklenmiyor).";
}

async function showBox() {
  const out = $("showResult");
  out.textContent = "";
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const reply = await chrome.tabs.sendMessage(tab.id, { type: "showBox" });
    if (reply && reply.ok) out.textContent = "Kutu gösterildi (sağ alt köşe).";
    else if (reply && reply.fullscreen) out.textContent = "Tam ekranda gizli; tam ekrandan çık.";
    else if (reply && !reply.video && !reply.list) out.textContent = "Bu sayfada video yok. Bir videoyu aç.";
    else out.textContent = "Kutu gösterilemedi.";
  } catch (error) {
    out.textContent = "Bu sayfada eklenti kodu çalışmıyor. YouTube sekmesini yenile (Ctrl+Shift+R).";
  }
}

(async () => {
  $("version").textContent = "v" + chrome.runtime.getManifest().version;
  const values = { ...DEFAULTS, ...(await chrome.storage.local.get(DEFAULTS)) };
  $("enabled").checked = values.enabled;
  $("port").value = values.port;
  $("enabled").addEventListener("change", (e) => chrome.storage.local.set({ enabled: e.target.checked }));
  $("captureViewable").checked = values.captureViewable;
  $("captureViewable").addEventListener("change", (e) => chrome.storage.local.set({ captureViewable: e.target.checked }));
  $("autoLaunch").checked = values.autoLaunch;
  $("autoLaunch").addEventListener("change", (e) => chrome.storage.local.set({ autoLaunch: e.target.checked }));
  $("port").addEventListener("change", async (e) => {
    const port = Math.min(65535, Math.max(1024, parseInt(e.target.value, 10) || DEFAULTS.port));
    e.target.value = port;
    await chrome.storage.local.set({ port });
    showStatus();
  });
  $("browserFallback").checked = values.browserFallback;
  $("browserFallback").addEventListener("change", (e) => chrome.storage.local.set({ browserFallback: e.target.checked }));
  $("shareYoutubeCookies").checked = values.shareYoutubeCookies;
  $("shareYoutubeCookies").addEventListener("change", (e) => chrome.storage.local.set({ shareYoutubeCookies: e.target.checked }));
  $("sniffVideos").checked = values.sniffVideos;
  $("sniffVideos").addEventListener("change", (e) => chrome.storage.local.set({ sniffVideos: e.target.checked }));
  $("collectLinks").addEventListener("click", collectLinks);
  $("findVideos").addEventListener("click", findVideos);
  $("show").addEventListener("click", showBox);
  showStatus();
  const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (activeTab) {
    const found = await chrome.runtime.sendMessage({ type: "wv-count", tabId: activeTab.id });
    if (found && found.count) $("findVideos").textContent = `Bu sayfadaki videoları bul (${found.count} algılandı)`;
  }
})();
