// İnternet Download Assistant - arka plan (service worker)
// 1) Tarayıcının başlattığı indirmeyi, "Farklı Kaydet" penceresi açılmadan önce yakalayıp uygulamaya yollar
//    (uygulama kapalıysa önce ida:// ile açılır; açılamazsa tarayıcı normal şekilde indirir).
// 2) Doğrudan dosya bağlantısına tıklamayı (links.js) uygulamaya iletir; tarayıcı indirmeyi hiç başlatmaz.
// 3) Sağ tık menüsü: "İnternet Download Assistant ile indir" (bağlantı / video / ses / resim).
// 4) YouTube kutusundan gelen istekleri (video ya da oynatma listesi) uygulamaya iletir; ayrıca videoyu önceden analiz ettirir.

const DEFAULTS = { enabled: true, port: 17653, autoLaunch: true, launchedOnce: false,
  browserFallback: true, shareYoutubeCookies: true };
const HEADERS = {
  "Content-Type": "application/json",
  "X-IDA-Client": `extension/${chrome.runtime.getManifest().version}`,
};
const LAUNCH_URL = "ida://start";
const LAUNCH_WAIT_MS = 25000;       // uygulamanın açılıp sunucunun hazır olması için en fazla bekleme
const LAUNCH_RETRY_AFTER_MS = 60000; // açma başarısız olursa bu süre boyunca tekrar denenmez

let offlineUntil = 0;        // uygulama kapalıysa kısa süre tekrar sorma (indirmeler gecikmesin)
let launching = null;        // devam eden "uygulamayı aç" işlemi (aynı anda tek bir tane)
let launchBlockedUntil = 0;

async function settings() {
  return { ...DEFAULTS, ...(await chrome.storage.local.get(DEFAULTS)) };
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// Uygulamaya istek atar. Uygulama kapalıysa { ok: false, offline: true } döner (hata fırlatmaz).
async function call(path, body, timeoutMs = 1500) {
  const { port } = await settings();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`http://127.0.0.1:${port}${path}`, {
      method: body ? "POST" : "GET",
      headers: HEADERS,
      body: body ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
    const result = await response.json();
    offlineUntil = 0;
    return result;
  } catch (error) {
    offlineUntil = Date.now() + 5000;
    return { ok: false, offline: true, error: String(error) };
  } finally {
    clearTimeout(timer);
  }
}

// ---------------------------------------------------------------- uygulamayı otomatik aç
// ida:// bağlantısı Windows'ta uygulamaya bağlıdır (uygulama her açılışta kaydeder). Uygulama kapalıysa onu açar,
// açıksa (ama sunucu kapalıysa) çalışan kopyaya "sunucuyu aç" der. Sunucu cevap verene kadar beklenir.
async function launchApp() {
  const { launchedOnce } = await settings();
  let tabId;
  try {
    // İlk seferde Chrome "uygulamayı aç?" diye sorabilir: sekme görünür olsun ki kutu kaçmasın.
    const tab = await chrome.tabs.create({ url: LAUNCH_URL, active: !launchedOnce });
    tabId = tab && tab.id;
  } catch (error) { /* sekme açılamadı */ }

  let ok = false;
  const deadline = Date.now() + LAUNCH_WAIT_MS;
  while (Date.now() < deadline) {
    await sleep(600);
    const reply = await call("/api/ping", null, 800);
    if (reply && reply.ok) { ok = true; break; }
  }
  if (tabId !== undefined) {
    try { await chrome.tabs.remove(tabId); } catch (error) {}
  }
  if (ok) chrome.storage.local.set({ launchedOnce: true });
  return ok;
}

function ensureApp() {
  if (!launching) {
    launching = launchApp().finally(() => { launching = null; });
  }
  return launching;
}

// Normal istek; uygulama kapalıysa (ve ayar açıksa) önce uygulamayı açıp isteği tekrarlar.
async function callWithLaunch(path, body, timeoutMs = 1500) {
  const first = await call(path, body, timeoutMs);
  if (!(first && first.offline)) return first;
  const { autoLaunch } = await settings();
  if (!autoLaunch || Date.now() < launchBlockedUntil) return first;
  const started = await ensureApp();
  if (!started) {
    launchBlockedUntil = Date.now() + LAUNCH_RETRY_AFTER_MS;
    return first;
  }
  return call(path, body, 3000);
}

async function cookieHeader(url) {
  try {
    const cookies = await chrome.cookies.getAll({ url });
    return cookies.map((c) => `${c.name}=${c.value}`).join("; ");
  } catch (error) {
    return "";
  }
}

const baseName = (path) => (path || "").split(/[\\/]/).pop();
const isWebUrl = (url) => /^https?:\/\//i.test(url || "");

// İndirmeyi başlatan sayfa bilinmiyorsa (tarayıcı referrer vermedi) o an etkin sekmenin adresi kaynak sayfa sayılır
async function activeTabUrl() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    return tab && isWebUrl(tab.url) ? tab.url : "";
  } catch (error) {
    return "";
  }
}
const isLocalUrl = (url) => /^(blob|data):/i.test(url || "");
const isYouTube = (url) => /^https?:\/\/((www|m|music)\.)?(youtube\.com|youtu\.be)\//i.test(url || "");

function nameFromUrl(url) {
  try {
    const last = new URL(url).pathname.split("/").pop();
    const name = decodeURIComponent(last);
    return name.includes(".") ? name : "";
  } catch (error) {
    return "";
  }
}

async function buildPayload(url, extra = {}) {
  return {
    url,
    filename: "",
    fileSize: -1,
    mime: "",
    referrer: "",
    cookies: await cookieHeader(url),
    userAgent: navigator.userAgent,
    ...extra,
  };
}

function flashBadge(ok, tabId) {
  const details = tabId !== undefined && tabId >= 0 ? { tabId } : {};
  chrome.action.setBadgeBackgroundColor({ color: ok ? "#22c55e" : "#ef4444", ...details });
  chrome.action.setBadgeText({ text: ok ? "✓" : "!", ...details });
  setTimeout(() => chrome.action.setBadgeText({ text: "", ...details }), 2500);
}

// ---------------------------------------------------------------- blob: / data: indirmeleri
// Bazı siteler (Pinterest vb.) "İndir" düğmesinde dosyayı sayfada üretip blob: adresiyle indirtir. Bu adres yalnızca o
// sekmede geçerlidir, uygulama internetten çekemez. Bu yüzden dosyayı tarayıcıda okuyup uygulamaya parça parça yollarız.
const UPLOAD_CHUNK = 4 * 1024 * 1024;
const blobSessions = new Map();   // yükleme kimliği -> { meta, finish }

const MIME_EXT = { "image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp", "image/svg+xml": "svg",
  "video/mp4": "mp4", "video/webm": "webm", "audio/mpeg": "mp3", "application/pdf": "pdf", "text/plain": "txt",
  "application/zip": "zip" };

function localName(item) {
  const name = baseName(item.filename);
  if (name) return name;
  const ext = MIME_EXT[(item.mime || "").split(";")[0].trim().toLowerCase()];
  return ext ? `indirilen.${ext}` : "indirilen_dosya";
}

async function postChunk(id, index, last, bytes, meta) {
  const { port } = await settings();
  const query = new URLSearchParams({ id, index: String(index), last: last ? "1" : "0" });
  if (last) {
    query.set("name", meta.name || "");
    query.set("mime", meta.mime || "");
    query.set("referrer", (meta.referrer || "").slice(0, 1500));
  }
  try {
    const response = await fetch(`http://127.0.0.1:${port}/api/upload?${query}`, {
      method: "POST",
      headers: { "X-IDA-Client": HEADERS["X-IDA-Client"], "Content-Type": "application/octet-stream" },
      body: bytes,
    });
    const result = await response.json();
    if (!(result && result.ok)) {
      console.warn("[IDA] uygulama yüklemeyi reddetti: HTTP", response.status, result && result.error,
        "(uygulamayı tamamen kapatıp yeniden aç: eski sürüm /api/upload bilmez)");
    }
    return Boolean(result && result.ok);
  } catch (error) {
    console.warn("[IDA] uygulamaya yükleme başarısız:", String(error));
    return false;
  }
}

async function uploadBlob(id, blob, meta) {
  let index = 0;
  for (let offset = 0; ; offset += UPLOAD_CHUNK) {
    const last = offset + UPLOAD_CHUNK >= blob.size;
    const bytes = new Uint8Array(await blob.slice(offset, offset + UPLOAD_CHUNK).arrayBuffer());
    if (!(await postChunk(id, index, last, bytes, meta))) return false;
    if (last) return true;
    index += 1;
  }
}

// blob: adresi: o sayfada (tüm sekmelerin tüm çerçevelerinde) blobs.js dosyayı bilen çerçeve parçaları bize yollar
async function uploadFromPage(id, url, meta) {
  const tabs = await chrome.tabs.query({ url: ["http://*/*", "https://*/*"] });
  let finish;
  const finished = new Promise((resolve) => { finish = resolve; });
  blobSessions.set(id, { meta, finish });
  let acked = false;
  await Promise.all(tabs.map((tab) =>
    chrome.tabs.sendMessage(tab.id, { type: "read-blob", url, id })
      .then((reply) => { if (reply && reply.ok) acked = true; })
      .catch(() => {})));
  let ok = false;
  if (!acked) {
    console.warn("[IDA] hiçbir sayfa bu blob dosyasını bilmiyor: dosyanın açıldığı sekmeyi Ctrl+Shift+R ile yenile",
      "(eklenti yenilenince açık sekmelere betik girmez)");
  }
  if (acked) {
    ok = await Promise.race([finished, sleep(10 * 60 * 1000).then(() => false)]);
  }
  blobSessions.delete(id);
  return ok;
}

async function captureLocal(item) {
  const ping = await callWithLaunch("/api/ping", null, 1200);
  if (!(ping && ping.ok)) {
    console.warn("[IDA] uygulamaya ulaşılamadı, blob dosyası aktarılamıyor");
    return false;
  }
  console.info("[IDA] blob/data indirmesi yakalandı:", String(item.finalUrl || item.url).slice(0, 80));
  const id = crypto.randomUUID().replace(/-/g, "");
  const meta = { name: localName(item), mime: item.mime || "", referrer: item.referrer || "" };
  const url = item.finalUrl || item.url;
  try {
    if (/^data:/i.test(url)) {
      const blob = await (await fetch(url)).blob();
      return await uploadBlob(id, blob, meta);
    }
    return await uploadFromPage(id, url, meta);
  } catch (error) {
    return false;
  }
}

// ---------------------------------------------------------------- 1) tarayıcı indirmelerini yakala
// Dosya adı belirlenirken (kaydetme penceresi daha AÇILMADAN) devreye girilir. Uygulama isteği aldıysa suggest()
// hiç çağrılmaz ve indirme iptal edilir; böylece tarayıcının "Farklı Kaydet" penceresi açılmaz.
// Uygulama kapalıysa önce açılır (indirme bu sırada bekler); açılamazsa suggest() ile tarayıcı olağan akışına döner.
const onFilename = chrome.downloads.onDeterminingFilename || {
  addListener: (fn) => chrome.downloads.onCreated.addListener((item) => fn(item, () => {})),   // Firefox
};
onFilename.addListener((item, suggest) => {
  handleDownload(item, suggest);
  return true; // suggest() eşzamansız çağrılacak
});

// Tarayıcının kendi indirmesini bırakır (uygulama devraldı): yalnızca sürüyorsa iptal eder, sonra listeden siler.
async function dropBrowserDownload(id) {
  try {
    const [current] = await chrome.downloads.search({ id });
    if (current && current.state === "in_progress") await chrome.downloads.cancel(id);
  } catch (error) {}
  try { await chrome.downloads.erase({ id }); } catch (error) {}
}

async function handleDownload(item, suggest) {
  let answered = false;
  const answer = async () => {
    if (answered) return;
    answered = true;
    // indirme bu arada iptal olduysa / kesildiyse suggest() "Download must be in progress" uyarısı verir: önce durumuna bak
    try {
      const [current] = await chrome.downloads.search({ id: item.id });
      if (current && current.state !== "in_progress") return;
    } catch (error) {}
    try { suggest(); } catch (error) { /* indirme zaten iptal edilmiş olabilir */ }
  };
  try {
    const { enabled, autoLaunch } = await settings();
    const url = item.finalUrl || item.url;
    if (!enabled || item.byExtensionId) return answer();
    const local = isLocalUrl(url);
    if (!local && !isWebUrl(url)) {
      console.info("[IDA] indirme atlandı (desteklenmeyen adres):", String(url).slice(0, 60));
      return answer();
    }
    const appKnownDown = Date.now() < offlineUntil && !launching &&
      (!autoLaunch || Date.now() < launchBlockedUntil);
    if (appKnownDown) return answer();

    if (local) {   // blob: / data: -> adresi tarayıcıya özel; dosyayı biz okuyup uygulamaya yollarız
      const taken = await captureLocal(item);
      console.info("[IDA] blob/data indirmesi:", taken ? "uygulamaya aktarıldı" : "aktarılamadı, tarayıcı indirecek");
      if (taken) {
        answered = true;
        await dropBrowserDownload(item.id);
        return;
      }
      return answer();
    }

    const size = item.totalBytes > 0 ? item.totalBytes : (item.fileSize > 0 ? item.fileSize : -1);
    const payload = await buildPayload(url, {
      filename: baseName(item.filename),
      fileSize: size,
      mime: item.mime || "",
      referrer: item.referrer || (await activeTabUrl()),
    });
    const result = await callWithLaunch("/api/download", payload, 1200);
    if (result && result.ok) {
      answered = true; // suggest() çağrılmaz: tarayıcı kaydetme penceresi açmadan indirme iptal olur
      if (result.ticket) watchHandoff(result.ticket, url);
      await dropBrowserDownload(item.id);
      return;
    }
  } catch (error) {
    // beklenmeyen hata: tarayıcı kendi indirmesine devam etsin
  }
  answer();
}

// ---------------------------------------------------------------- 3) sağ tık menüsü
// ---------------------------------------------------------------- engellenen indirmeyi tarayıcıya devretme
// Uygulama indirmeyi alınca bir "bilet" verir. Uygulamadaki indirme 401/403/429 ile başarısız olursa (siteler python
// istemcilerini TLS parmak iziyle engelleyebilir, tarayıcıyı engellemez) aynı adres tarayıcıya indirtilir.
const HANDOFF_MAX_MS = 45 * 60 * 1000;

async function watchHandoff(ticket, url) {
  const { browserFallback } = await settings();
  if (browserFallback === false || !ticket) return;
  const started = Date.now();
  while (Date.now() - started < HANDOFF_MAX_MS) {
    await sleep(1500);
    try { await chrome.runtime.getPlatformInfo(); } catch (error) {}   // hizmet işçisini uyanık tutar
    const reply = await call("/api/handoff?id=" + encodeURIComponent(ticket), null, 1500);
    if (!reply || reply.offline || !reply.ok) return;
    if (reply.state === "failed") {
      if (!reply.browserHelpful) return;
      try {
        await chrome.downloads.download({ url, conflictAction: "uniquify", saveAs: false });
        await call("/api/handoff-taken", { ticket }, 1500);
        console.info("[IDA] indirme tarayıcıya devredildi:", String(url).slice(0, 100));
      } catch (error) {
        console.warn("[IDA] tarayıcıya devretme başarısız:", String(error));
      }
      return;
    }
    if (["done", "cancelled", "gone", "unknown"].includes(reply.state)) return;
  }
}

// ---------------------------------------------------------------- YouTube oturum çerezleri
// Üyelere özel / özel / yaş sınırlı videolar için tarayıcıdaki oturumun çerezleri uygulamaya (yalnızca 127.0.0.1) gönderilir.
let ytJarCache = { at: 0, jar: [] };

async function youtubeJar() {
  const { shareYoutubeCookies } = await settings();
  if (shareYoutubeCookies === false) return [];
  if (Date.now() - ytJarCache.at < 60000) return ytJarCache.jar;
  const jar = new Map();
  for (const domain of ["youtube.com", "google.com"]) {
    try {
      for (const c of await chrome.cookies.getAll({ domain })) {
        jar.set(`${c.domain}|${c.path}|${c.name}`, {
          domain: c.domain, path: c.path, secure: Boolean(c.secure), hostOnly: Boolean(c.hostOnly),
          httpOnly: Boolean(c.httpOnly), name: c.name, value: c.value, expires: Math.floor(c.expirationDate || 0),
        });
      }
    } catch (error) {}
  }
  ytJarCache = { at: Date.now(), jar: [...jar.values()].slice(0, 400) };
  return ytJarCache.jar;
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    void chrome.runtime.lastError;
    chrome.contextMenus.create({
      id: "ida-download",
      title: "İnternet Download Assistant ile indir",
      contexts: ["link", "video", "audio", "image"],
    });
    chrome.contextMenus.create({     // sayfadaki reklam engelleyiciler bu menüyü gizleyemez
      id: "ida-videos",
      title: "Bu sayfadaki videoları İnternet Download Assistant ile bul",
      contexts: ["page", "frame", "video"],
    });
    chrome.contextMenus.create({
      id: "ida-links",
      title: "Bu sayfadaki dosya bağlantılarını İnternet Download Assistant'a topla",
      contexts: ["page"],
    });
  });
});

// Sayfadaki videoları uygulamada listeler (sağ tık menüsü, kısayol, açılır pencere ve sayfadaki kutu bunu kullanır)
async function openWebVideos(tabId, listed) {
  const payload = await collectWebVideos(tabId);
  payload.listed = listed === true;
  return callWithLaunch("/api/webvideo", payload, 6000);
}

chrome.commands.onCommand.addListener(async (command, tab) => {
  if (command !== "open-videos" || !tab || tab.id === undefined) return;
  const result = await openWebVideos(tab.id).catch(() => null);
  flashBadge(Boolean(result && result.ok), tab.id);
  setTimeout(() => wvNotify(tab.id), 2800);      // simgedeki sayıyı geri getir
});

// Sayfadaki dosya bağlantılarını toplayıp uygulamada seçim penceresi olarak açar
async function openPageLinks(tabId) {
  let reply = null;
  try { reply = await chrome.tabs.sendMessage(tabId, { type: "collect-links" }, { frameId: 0 }); } catch (error) {}
  if (!reply || !reply.ok) return { ok: false };
  if (!reply.items.length) return { ok: false, empty: true };
  const tab = await chrome.tabs.get(tabId);
  const payload = {
    pageUrl: tab.url, pageTitle: reply.title || tab.title || "", userAgent: navigator.userAgent,
    cookies: await cookieHeader(tab.url), items: reply.items,
  };
  return callWithLaunch("/api/links", payload, 8000);
}

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId === "ida-links" && tab && tab.id !== undefined) {
    const result = await openPageLinks(tab.id).catch(() => null);
    flashBadge(Boolean(result && result.ok), tab.id);
    return;
  }
  if (info.menuItemId === "ida-videos" && tab && tab.id !== undefined) {
    const result = await openWebVideos(tab.id).catch(() => null);
    flashBadge(Boolean(result && result.ok), tab.id);
    setTimeout(() => wvNotify(tab.id), 2800);
    return;
  }
  if (info.menuItemId !== "ida-download") return;
  const url = info.linkUrl || info.srcUrl;
  if (!isWebUrl(url)) return;
  const result = isYouTube(url)
    ? await callWithLaunch("/api/youtube", { url, jar: await youtubeJar() })
    : await callWithLaunch("/api/download", await buildPayload(url, { referrer: info.pageUrl || "", filename: nameFromUrl(url) }));
  if (result && result.ok && result.ticket) watchHandoff(result.ticket, url);
  flashBadge(Boolean(result && result.ok), tab && tab.id);
});

// ---------------------------------------------------------------- 2) ve 4) mesajlar
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message) return false;
  if (message.type === "youtube") {
    (async () => {
      const jar = await youtubeJar();
      sendResponse(await callWithLaunch("/api/youtube", { url: message.url, playlist: Boolean(message.playlist),
        listed: message.listed === true, jar }));
    })();
    return true;
  }
  if (message.type === "download") { // links.js: doğrudan dosya bağlantısına tıklandı
    (async () => {
      const { enabled } = await settings();
      if (!enabled || !isWebUrl(message.url)) return sendResponse({ ok: false, disabled: true });
      const payload = await buildPayload(message.url, {
        referrer: message.referrer || "",
        filename: nameFromUrl(message.url),
      });
      const result = await callWithLaunch("/api/download", payload);
      if (result && result.ok && result.ticket) watchHandoff(result.ticket, message.url);
      sendResponse(result);
    })();
    return true;
  }
  if (message.type === "page-info") {       // webvideo.js: sayfadaki <video> öğeleri ve sayfa bilgisi
    if (sender.tab && sender.tab.id >= 0) wvOnPageInfo(sender.tab.id, sender.frameId || 0, message);
    return false;
  }
  if (message.type === "stream-found") {    // blobs.js: sayfanın aldığı HLS/DASH bildirim dosyası (içerikten algılandı)
    if (sender.tab && sender.tab.id >= 0) wvOnStreamFound(sender.tab.id, message);
    return false;
  }
  if (message.type === "collect-links") {     // açılır pencere: sayfadaki dosya bağlantılarını uygulamaya gönder
    (async () => {
      try { sendResponse(await openPageLinks(message.tabId)); } catch (error) { sendResponse({ ok: false }); }
    })();
    return true;
  }
  if (message.type === "wv-count") {         // açılır pencere: bu sayfada kaç video algılandı
    (async () => {
      try {
        const tab = await wvTabLoaded(message.tabId);
        sendResponse({ count: wvBuildItems(tab).filter((i) => !i.ad && i.score >= WV_MIN_SCORE).length });
      } catch (error) { sendResponse({ count: 0 }); }
    })();
    return true;
  }
  if (message.type === "wv-open") {         // sitedeki videoları uygulamada listele (kutu ya da açılır pencere)
    (async () => {
      const tabId = message.tabId !== undefined ? message.tabId : (sender.tab && sender.tab.id);
      if (tabId === undefined) return sendResponse({ ok: false });
      try {
        sendResponse(await openWebVideos(tabId, message.listed === true));
      } catch (error) {
        console.warn("[IDA] video listesi gönderilemedi:", String(error));
        sendResponse({ ok: false, error: String(error) });
      }
    })();
    return true;
  }
  if (message.type === "blob-chunk") {     // blobs.js: blob: dosyasının bir parçası
    (async () => {
      const session = blobSessions.get(message.id);
      if (!session) return sendResponse({ ok: false });
      if (message.error) { session.finish(false); return sendResponse({ ok: false }); }
      let bytes;
      try {
        bytes = Uint8Array.from(atob(message.data), (c) => c.charCodeAt(0));
      } catch (error) { session.finish(false); return sendResponse({ ok: false }); }
      const ok = await postChunk(message.id, message.index, message.last, bytes, session.meta);
      if (!ok) session.finish(false);
      else if (message.last) session.finish(true);
      sendResponse({ ok });
    })();
    return true;
  }
  if (message.type === "ping") {          // açılır pencere durumu: bu çağrı uygulamayı AÇMAZ
    call("/api/ping").then(sendResponse);
    return true;
  }
  // YouTube kutusunun otomatik analizi: bu çağrılar uygulamayı ASLA açmaz (kapalıysa sessizce vazgeçilir)
  if (message.type === "prefetch") {
    if (Date.now() < offlineUntil) return sendResponse({ ok: false, offline: true }), false;
    youtubeJar().then((jar) => call("/api/prefetch", { url: message.url, jar }, 2500)).then(sendResponse);
    return true;
  }
  if (message.type === "prefetch-cancel") {
    if (Date.now() < offlineUntil) return sendResponse({ ok: false, offline: true }), false;
    call("/api/prefetch-cancel", { id: message.id || "" }, 1500).then(sendResponse);
    return true;
  }
  if (message.type === "analysis-status") {
    call("/api/analysis?id=" + encodeURIComponent(message.id || ""), null, 1500).then(sendResponse);
    return true;
  }
  return false;
});

if (typeof importScripts === "function") importScripts("sniffer.js");   // Firefox'ta manifest betikleri yükler   // sitelerdeki video algılama (ağ trafiği + sayfa bilgisi)
