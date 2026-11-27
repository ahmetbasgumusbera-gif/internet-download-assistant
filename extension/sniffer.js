// İnternet Download Assistant - sitelerdeki video algılama (background.js içinden importScripts ile yüklenir).
//
// Kaynaklar: (1) ağ trafiği (webRequest, yalnızca izler): video dosyaları, HLS (.m3u8) ve DASH (.mpd) bildirimleri;
// (2) sayfadaki <video> öğeleri ve sayfa bilgisi (og:video, JSON-LD) - webvideo.js bildirir.
// Her adaya PUAN verilir (süreye değil, birçok işarete bakılır): oynatılan/büyük video öğesiyle eşleşme, sayfanın bildirdiği video,
// dosya boyutu, reklam alanı adları... Reklama benzeyenler "ad" olarak işaretlenir; uygulama bunları ayrı grupta gösterir.
// Parça (.ts, .m4s...) istekleri adaylara alınmaz: yalnızca dosyanın kendisi ve bildirim (playlist) dosyaları.

const WV_MIN_SCORE = 25;          // kutunun görünmesi için gereken en düşük puan (reklam olmayan aday)
const WV_MAX_ITEMS = 80;          // sekme başına saklanan en fazla aday
const MB = 1024 * 1024;

const WV_SEGMENT = /\.(ts|m4s|m4f|cmfv|cmfa|aac|mp3|m4a|vtt|webvtt|srt)$/i;
const WV_AD_HOST = /(doubleclick|googlesyndication|googleadservices|imasdk|adnxs|serving-sys|moatads|adsrvr|smartadserver|spotxchange|springserve|teads|innovid|tubemogul|adform|criteo|pubmatic|rubiconproject|openx|amazon-adsystem|adcolony|unityads|applovin|vungle|ironsrc|mopub|mgid|taboola|outbrain|exoclick|trafficjunky|juicyads|adsterra|popads|propellerads|adskeeper|vidazoo|primis|connatix|anyclip|jwpltx)/i;
const WV_AD_LABEL = /(^|\.)(ads?|adserver|adservice|adv|advert|adtech)\d*\./i;
const WV_AD_PATH = /(^|[\/._-])(ads?|adserver|advert|adverts|preroll|midroll|postroll|vast|vpaid|sponsor|promo|commercial)([\/._-]|$)/i;
const WV_AD_QUERY = /[?&](adformat|ad_type|adunit|adtagurl|vasturl|adid)=/i;

let wvEnabled = true;
try {
  chrome.storage.local.get({ sniffVideos: true }, (values) => { wvEnabled = values.sniffVideos !== false; });
  chrome.storage.onChanged.addListener((changes) => {
    if (changes.sniffVideos) wvEnabled = changes.sniffVideos.newValue !== false;
  });
} catch (error) { /* ilk yükleme */ }

// ---------------------------------------------------------------- sekme durumu (hizmet işçisi uyusa da kaybolmasın: storage.session)
const wvTabs = new Map();      // tabId -> { pageUrl, resetAt, items: Map(key -> aday), frames: Map(frameId -> bilgi), ready }
const wvTimers = new Map();

function wvNew(pageUrl) {
  return { pageUrl: pageUrl || "", resetAt: Date.now(), items: new Map(), frames: new Map(), ready: null };
}

async function wvTabLoaded(tabId) {
  let tab = wvTabs.get(tabId);
  if (!tab) { tab = wvNew(""); wvTabs.set(tabId, tab); }
  if (!tab.ready) {
    tab.ready = (async () => {
      try {
        const key = "wv:" + tabId;
        const stored = (await chrome.storage.session.get(key))[key];
        if (stored) {
          if (!tab.pageUrl) tab.pageUrl = stored.pageUrl || "";
          for (const item of stored.items || []) if (!tab.items.has(item.key)) tab.items.set(item.key, item);
          for (const [id, frame] of stored.frames || []) if (!tab.frames.has(id)) tab.frames.set(id, frame);
        }
      } catch (error) { /* oturum deposu yoksa bellekle devam */ }
    })();
  }
  await tab.ready;
  return tab;
}

function wvSaveSoon(tabId) {
  clearTimeout(wvTimers.get("save" + tabId));
  wvTimers.set("save" + tabId, setTimeout(async () => {
    const tab = wvTabs.get(tabId);
    if (!tab) return;
    try {
      await chrome.storage.session.set({ ["wv:" + tabId]: {
        pageUrl: tab.pageUrl, items: [...tab.items.values()], frames: [...tab.frames.entries()],
      } });
    } catch (error) {}
  }, 500));
}

function wvReset(tabId, url) {
  const tab = wvNew(url);
  tab.ready = Promise.resolve();
  wvTabs.set(tabId, tab);
  wvSaveSoon(tabId);
  wvNotifySoon(tabId);
}

const wvNoHash = (url) => String(url || "").split("#")[0];

try {
  // yeni sayfa yüklenince (yenileme, bağlantı) önceki adaylar silinir
  chrome.webRequest.onBeforeRequest.addListener((details) => {
    if (details.tabId >= 0) wvReset(details.tabId, details.url);
  }, { urls: ["http://*/*", "https://*/*"], types: ["main_frame"] });

  chrome.webRequest.onHeadersReceived.addListener(wvOnHeaders,
    { urls: ["http://*/*", "https://*/*"], types: ["media", "xmlhttprequest", "other"] }, ["responseHeaders"]);

  // sayfa yenilenmeden adres değişirse (tek sayfalık siteler) eski adaylar geçersizdir
  chrome.tabs.onUpdated.addListener((tabId, change) => {
    if (!change.url) return;
    const tab = wvTabs.get(tabId);
    if (tab && tab.pageUrl && wvNoHash(tab.pageUrl) !== wvNoHash(change.url) && Date.now() - tab.resetAt > 3000) {
      wvReset(tabId, change.url);
    }
  });

  chrome.tabs.onRemoved.addListener((tabId) => {
    wvTabs.delete(tabId);
    try { chrome.storage.session.remove("wv:" + tabId); } catch (error) {}
  });
} catch (error) {
  console.warn("[IDA] video algılama başlatılamadı (webRequest izni?):", String(error));
}

// ---------------------------------------------------------------- ağ trafiğinden aday çıkarma
function wvHeader(details, name) {
  const header = (details.responseHeaders || []).find((h) => h.name.toLowerCase() === name);
  return header ? String(header.value || "") : "";
}

function wvKindOf(url) {
  let path = "";
  try { path = new URL(url).pathname.toLowerCase(); } catch (error) { return ""; }
  if (/\.m3u8$/.test(path)) return "hls";
  if (/\.mpd$/.test(path)) return "dash";
  return "file";
}

function wvClassify(details) {
  if (details.statusCode >= 400 || details.statusCode === 204) return null;
  let path;
  try { path = new URL(details.url).pathname.toLowerCase(); } catch (error) { return null; }
  if (/googlevideo\.com|youtube\.com|ytimg\.com/i.test(details.url)) return null;
  const ctype = wvHeader(details, "content-type").split(";")[0].trim().toLowerCase();
  if (WV_SEGMENT.test(path) || ctype === "video/mp2t" || ctype === "video/iso.segment") return null;
  let kind = "";
  if (/\.m3u8$/.test(path) || /mpegurl/.test(ctype)) kind = "hls";
  else if (/\.mpd$/.test(path) || /dash\+xml/.test(ctype)) kind = "dash";
  else if (ctype.startsWith("video/") || /\.(mp4|m4v|webm|mkv|mov|avi|flv|wmv|ogv|3gp|mpg|mpeg|f4v)$/.test(path)) kind = "file";
  if (!kind) return null;
  let size = 0;
  const range = /\/(\d+)\s*$/.exec(wvHeader(details, "content-range"));
  if (range) size = Number(range[1]);
  else size = Number(wvHeader(details, "content-length")) || 0;
  if (kind === "file" && size && size < 200 * 1024) return null;   // küçük önizleme/animasyon
  return { url: details.url, kind, mime: ctype, size };
}

async function wvOnHeaders(details) {
  if (!wvEnabled || details.tabId < 0) return;
  const info = wvClassify(details);
  if (!info) return;
  const tab = await wvTabLoaded(details.tabId);
  const key = wvNoHash(info.url);
  const known = tab.items.get(key);
  if (known) {
    if (info.size > known.size) known.size = info.size;
  } else {
    if (tab.items.size >= WV_MAX_ITEMS) tab.items.delete(tab.items.keys().next().value);   // en eskisini at
    tab.items.set(key, { ...info, key, referrer: details.documentUrl || tab.pageUrl, seen: Date.now() });
    console.info("[IDA] video adayı:", info.kind, Math.round((info.size || 0) / MB) + " MB", info.url.slice(0, 110));
  }
  wvSaveSoon(details.tabId);
  wvNotifySoon(details.tabId);
}

// ---------------------------------------------------------------- içerik tabanlı algılama (sniff_main.js)
// Sayfanın aldığı yanıtın içinde "#EXTM3U" / "<MPD" görüldü: adı ne olursa olsun (master.txt, playlist.php...) akış bildirimidir.
async function wvOnStreamFound(tabId, message) {
  if (!wvEnabled || !/^https?:/i.test(message.url || "")) return;
  const tab = await wvTabLoaded(tabId);
  const key = wvNoHash(message.url);
  const kind = message.kind === "dash" ? "dash" : "hls";
  const known = tab.items.get(key);
  const master = Boolean(message.master);
  const variant = kind === "hls" && !master;          // ana (master) listeye bağlı alt liste: yt-dlp ana listeden hepsini bulur
  if (known) {
    known.kind = kind; known.master = master; known.variant = variant;
  } else {
    if (tab.items.size >= WV_MAX_ITEMS) tab.items.delete(tab.items.keys().next().value);
    tab.items.set(key, { url: message.url, key, kind, mime: "", size: 0, referrer: message.frameUrl || tab.pageUrl,
      seen: Date.now(), source: "hook", master, variant });
    console.info("[IDA] akış bildirimi (içerikten):", kind, master ? "ana liste" : "alt liste", message.url.slice(0, 110));
  }
  wvSaveSoon(tabId);
  wvNotifySoon(tabId);
}

// ---------------------------------------------------------------- sayfa içeriği (webvideo.js)
async function wvOnPageInfo(tabId, frameId, message) {
  if (!wvEnabled) return;
  const tab = await wvTabLoaded(tabId);
  tab.frames.set(frameId, { url: message.url, videos: message.videos || [], meta: message.meta || null, at: Date.now() });
  if (frameId === 0 && !tab.pageUrl) tab.pageUrl = message.url;
  wvSaveSoon(tabId);
  wvNotifySoon(tabId);
}

// ---------------------------------------------------------------- puanlama
function wvIsAd(url) {
  let u;
  try { u = new URL(url); } catch (error) { return false; }
  return WV_AD_HOST.test(u.hostname) || WV_AD_LABEL.test(u.hostname) || WV_AD_PATH.test(u.pathname) || WV_AD_QUERY.test(u.search);
}

const wvCleanTitle = (text) => String(text || "").replace(/^\(\d+\)\s*/, "").replace(/\s+/g, " ").trim().slice(0, 200);

function wvScore(item, ctx) {
  let score = 25;
  const own = ctx.videos.find((v) => (v.srcs || []).includes(item.url));
  const player = own || (item.kind !== "file" ? ctx.mse : null);   // akış oynatıcıları video öğesine blob: verir
  if (player) {
    score += player.playing ? 40 : (player.played ? 25 : 15);
    if (own) score += 10;
    if (player.area && player.area < 250 * 140) score -= 30;       // kenar çubuğu / önizleme boyutu
  }
  if (ctx.declared.has(item.key)) score += 30;                     // sayfa "ana video bu" diyor (og:video / JSON-LD)
  const size = item.size || 0;
  if (item.kind === "file" && size) {
    if (size >= 20 * MB) score += 20;
    else if (size >= 5 * MB) score += 10;
    else if (size < MB) score -= 20;
  }
  const duration = (player && player.duration) || 0;
  if (duration >= 600) score += 15;
  else if (duration >= 120) score += 5;
  else if (duration > 0 && duration < 45) score -= 10;             // kısa olması yalnızca hafif eksi (kesin kural değil)
  const ad = wvIsAd(item.url);
  if (ad) score -= 60;
  return {
    url: item.url, key: item.key, kind: item.kind, mime: item.mime || "", size, referrer: item.referrer || ctx.pageUrl,
    score, ad, source: item.source || "network",
    title: ctx.pageTitle,
    poster: (player && player.poster) || ctx.meta.image || "",
    duration: Math.round(duration), width: player ? player.w : 0, height: player ? player.h : 0,
  };
}

function wvBuildItems(tab) {
  const videos = [];
  for (const [frameId, frame] of tab.frames) {
    for (const v of frame.videos || []) videos.push({ ...v, frameId, frameUrl: frame.url });
  }
  const top = tab.frames.get(0);
  const meta = (top && top.meta && top.meta.title !== undefined ? top.meta : null)
    || [...tab.frames.values()].map((f) => f.meta).find((m) => m && m.title) || {};
  const mse = videos.filter((v) => v.mse).sort((a, b) => (b.playing - a.playing) || (b.area - a.area))[0] || null;
  const declared = new Set();
  for (const u of [meta.ogVideo, ...(meta.jsonld || []).map((j) => j.contentUrl)]) {
    if (u && /^https?:/i.test(u)) declared.add(wvNoHash(u));
  }
  const map = new Map();
  for (const item of tab.items.values()) map.set(item.key, { ...item, source: item.source || "network" });
  if ([...map.values()].some((i) => i.kind === "hls" && i.master)) {      // ana liste varsa alt listeleri ayrıca gösterme
    for (const [key, i] of [...map]) if (i.variant) map.delete(key);
  }
  const addPage = (url, frameUrl) => {
    if (!/^https?:/i.test(url || "")) return;
    const key = wvNoHash(url);
    if (!map.has(key)) map.set(key, { url, key, kind: wvKindOf(url), mime: "", size: 0, referrer: frameUrl || tab.pageUrl, source: "page" });
  };
  for (const v of videos) for (const src of v.srcs || []) addPage(src, v.frameUrl);
  for (const u of declared) addPage(u, tab.pageUrl);
  const ctx = { videos, mse, declared, meta, pageTitle: wvCleanTitle(meta.title || ""), pageUrl: tab.pageUrl };
  return [...map.values()].map((item) => wvScore(item, ctx));
}

// ---------------------------------------------------------------- sayfadaki kutuya durum bildirme
function wvNotifySoon(tabId) {
  clearTimeout(wvTimers.get("note" + tabId));
  wvTimers.set("note" + tabId, setTimeout(() => wvNotify(tabId), 500));
}

async function wvNotify(tabId) {
  if (!wvEnabled) return;
  const tab = await wvTabLoaded(tabId);
  const usable = wvBuildItems(tab).filter((i) => !i.ad && i.score >= WV_MIN_SCORE).sort((a, b) => b.score - a.score);
  console.info("[IDA] video kutusu:", usable.length, "aday, en iyi puan:", usable[0] ? usable[0].score : "-");
  // Araç çubuğu simgesinde sayı: sayfadaki reklam engelleyiciler bunu gizleyemez
  try {
    await chrome.action.setBadgeText({ tabId, text: usable.length ? String(usable.length) : "" });
    if (usable.length) {
      await chrome.action.setBadgeBackgroundColor({ tabId, color: "#16a34a" });
      await chrome.action.setTitle({ tabId, title: `İnternet Download Assistant - ${usable.length} video algılandı (tıkla)` });
    }
  } catch (error) {}
  try {
    await chrome.tabs.sendMessage(tabId, { type: "wv-state", count: usable.length, title: usable[0] ? usable[0].title : "" },
      { frameId: 0 });
  } catch (error) {
    console.info("[IDA] kutu mesajı sekmeye ulaşmadı (sayfayı Ctrl+Shift+R ile yenile):", String(error));
  }
}

// ---------------------------------------------------------------- uygulamaya gönderilecek paket
async function collectWebVideos(tabId) {
  const tab = await wvTabLoaded(tabId);
  const info = await chrome.tabs.get(tabId);
  const items = wvBuildItems(tab).sort((a, b) => b.score - a.score).slice(0, 40);
  const top = tab.frames.get(0);
  const meta = (top && top.meta) || [...tab.frames.values()].map((f) => f.meta).find((m) => m && m.title) || {};

  const jar = new Map();
  const addCookies = async (url) => {
    try {
      for (const c of await chrome.cookies.getAll({ url })) {
        jar.set(`${c.domain}|${c.path}|${c.name}`, {
          domain: c.domain, path: c.path, secure: Boolean(c.secure), hostOnly: Boolean(c.hostOnly),
          name: c.name, value: c.value, expires: Math.floor(c.expirationDate || 0),
        });
      }
    } catch (error) {}
  };
  await addCookies(info.url);
  for (const [index, item] of items.entries()) {
    delete item.key;
    if (index < 15) {
      const header = await cookieHeader(item.url);
      item.cookies = header.length <= 6000 ? header : "";
    }
    if (item.kind !== "file") await addCookies(item.url);   // HLS/DASH'i uygulama yt-dlp ile indirir: çerez dosyası gerekir
  }
  return {
    pageUrl: info.url,
    pageTitle: wvCleanTitle(meta.title || info.title || ""),
    image: meta.image || "",
    userAgent: navigator.userAgent,
    items,
    jar: [...jar.values()].slice(0, 300),
  };
}
