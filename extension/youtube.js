// YouTube sayfalarında küçük, taşınabilir "İndir" kutusu (v1.3.0).
// v1.2.1: video/Shorts değişince başlık ve link doğru yenilenir (başlık oEmbed'den, video kimliğine göre).
// v1.3.0: video açılınca uygulama videoyu İndir'e basmadan önce arka planda analiz eder; durum İndir düğmesinin yanında görünür.
(() => {
  const HOST_ID = "ida-" + Math.random().toString(36).slice(2, 10);
  const POS_KEY = "ytBoxPos";
  const IDLE_MS = 8000; /* 8 sn sonra küçülür ve saydamlaşır; fare üstüne gelince açılır */ //   // soluklaşma fiilen kapalı
  const OEMBED = "https://www.youtube.com/oembed?format=json&url=";
  const ANA_START_DELAY = 600;   // video değiştikten sonra analizi başlatmadan önce kısa bekleme (hızlı geçişlerde boşuna başlamasın)
  const ANA_POLL_MS = 700;

  // Eklenti yenilendiyse sayfada kalmış eski/yetim kutuları temizle
  document.querySelectorAll("[data-ida-box]").forEach((n) => n.remove());

  let host = null;
  let parts = {};
  let dismissedFor = "";
  let shownKey = "";
  let idleTimer = 0;
  let pos = null;

  // başlık durumu
  let titleKey = "";
  let titleText = "";
  let titleResolved = false;
  let titleBusy = false;
  let titleTries = 0;
  let prevTitle = "";
  let statusText = "";
  let statusTimer = 0;
  const titleCache = new Map();

  // otomatik analiz durumu
  let anaId = "";            // şu an analiz edilen / edilecek video kimliği
  let anaState = "none";     // none | running | done
  let anaToken = 0;          // her video değişiminde artar: eski sonuçlar atılır
  let anaStartTimer = 0;
  let anaPollTimer = 0;
  let anaHideTimer = 0;

  const log = (...a) => console.info("[IDA]", ...a);
  log("YouTube betiği yüklendi:", location.href);

  const params = () => new URLSearchParams(location.search);
  const isShorts = () => location.pathname.startsWith("/shorts/");
  const videoId = () => {
    if (location.pathname === "/watch") return params().get("v") || "";
    return isShorts() ? (location.pathname.split("/")[2] || "") : "";
  };
  const listId = () => {
    const id = params().get("list") || "";
    return id.startsWith("RD") ? "" : id;
  };
  const videoUrl = () => (isShorts() ? `${location.origin}/shorts/${videoId()}` : `https://www.youtube.com/watch?v=${videoId()}`);
  const listUrl = () => `https://www.youtube.com/playlist?list=${listId()}`;
  const pageTitle = () => document.title.replace(/^\(\d+\)\s*/, "").replace(/\s*-\s*YouTube( Music)?$/, "").trim();
  const isFullscreen = () => Boolean(document.fullscreenElement || document.webkitFullscreenElement);
  const validPos = (p) => p && Number.isFinite(p.x) && Number.isFinite(p.y) && p.x >= 0 && p.x <= 1 && p.y >= 0 && p.y <= 1;

  function state() {
    const video = Boolean(videoId());
    const list = Boolean(listId()) && (video || location.pathname === "/playlist");
    return {
      video, list,
      key: video ? videoId() : (list ? `pl:${listId()}` : ""),
      id: video ? videoId() : listId(),
    };
  }

  // Arka plana (background.js) mesaj; eklenti yenilenmişse ya da cevap yoksa null döner.
  function bg(message) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(message, (response) => {
          if (chrome.runtime.lastError) resolve(null);
          else resolve(response || null);
        });
      } catch (error) {
        resolve(null);
      }
    });
  }

  const CSS = `
    .box { display: flex; align-items: stretch; gap: 6px; max-width: 380px; padding: 8px 8px 8px 4px;
      border-radius: 14px; background: rgba(12, 13, 22, .92); border: 1px solid rgba(167, 139, 250, .55);
      box-shadow: 0 6px 24px rgba(0, 0, 0, .45); color: #e8eaf6;
      font: 13px/1.3 'Segoe UI', Roboto, Arial, sans-serif; user-select: none; box-sizing: border-box; }
    .grip { width: 16px; display: flex; align-items: center; justify-content: center; cursor: grab;
      color: #8c92b8; font-size: 15px; letter-spacing: -2px; touch-action: none; }
    .grip:active { cursor: grabbing; }
    .body { display: flex; flex-direction: column; gap: 6px; min-width: 0; flex: 1; }
    .title { font-size: 12px; color: #aab0d4; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 290px; }
    .actions { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
    button.act { border: none; cursor: pointer; padding: 7px 12px; border-radius: 999px; font: 600 12px/1 'Segoe UI', Roboto, Arial, sans-serif;
      color: #0c0d16; background: linear-gradient(90deg, #a78bfa, #22d3ee); white-space: nowrap; }
    button.act.list { background: transparent; color: #c4b5fd; border: 1px solid rgba(167, 139, 250, .55); }
    button.act:hover { filter: brightness(1.12); }
    button.close { align-self: flex-start; border: none; background: transparent; color: #8c92b8; cursor: pointer; font-size: 16px; line-height: 1; padding: 0 2px; }
    button.close:hover { color: #e8eaf6; }
    .ana { display: none; align-items: center; gap: 6px; font-size: 12px; font-weight: 600; color: #aab0d4; white-space: nowrap; }
    .ana.show { display: inline-flex; }
    .ana .spin { width: 12px; height: 12px; box-sizing: border-box; border-radius: 50%;
      border: 2px solid rgba(170, 176, 212, .3); border-top-color: #22d3ee; animation: ida-spin .8s linear infinite; }
    .ana .tick { display: none; color: #4ade80; font-size: 15px; font-weight: 800; line-height: 1; }
    .ana.done { color: #4ade80; }
    .ana.done .spin { display: none; }
    .ana.done .tick { display: inline; }
    @keyframes ida-spin { to { transform: rotate(360deg); } }
    .dot { display: none; width: 32px; height: 32px; align-items: center; justify-content: center; cursor: pointer;
      font-size: 18px; font-weight: 700; color: #0c0d16; }
    .box { transition: opacity .35s ease; }
    .box.compact { opacity: .45; padding: 0; gap: 0; border-radius: 999px; border: 2px solid rgba(255, 255, 255, .55);
      background: linear-gradient(135deg, #a78bfa, #22d3ee); box-shadow: 0 4px 16px rgba(34, 211, 238, .45); }
    .box.compact .grip, .box.compact .body, .box.compact .close { display: none; }
    .box.compact .dot { display: flex; }
  `;

  function build() {
    host = document.createElement("div");
    host.id = HOST_ID;
    host.setAttribute("data-ida-box", "yt");
    // all:initial -> YouTube'un CSS'i kutuyu etkileyemez
    host.style.cssText = "all: initial; position: fixed; z-index: 2147483000; display: none; opacity: 1;" +
      " transition: opacity .35s ease; right: 24px; bottom: 24px;";
    const root = host.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = CSS;
    const box = document.createElement("div");
    box.className = "box";

    const dot = document.createElement("div");
    dot.className = "dot";
    dot.textContent = "⬇";
    dot.title = "İnternet Download Assistant";
    const grip = document.createElement("div");
    grip.className = "grip";
    grip.textContent = "⋮⋮";
    grip.title = "Sürükleyerek taşı";
    const body = document.createElement("div");
    body.className = "body";
    const title = document.createElement("div");
    title.className = "title";
    const actions = document.createElement("div");
    actions.className = "actions";
    const videoBtn = document.createElement("button");
    videoBtn.className = "act video";
    videoBtn.textContent = "⬇ Bu videoyu indir";
    const queueBtn = document.createElement("button");
    queueBtn.className = "act list";
    queueBtn.textContent = "＋ Listeye ekle";
    queueBtn.title = "İndirmeyi başlatmadan uygulamadaki Listeler sekmesine ekler (varsayılan ayarlarla)";
    const ana = document.createElement("span");
    ana.className = "ana";
    const spin = document.createElement("span");
    spin.className = "spin";
    const tick = document.createElement("span");
    tick.className = "tick";
    tick.textContent = "✓";
    const anaText = document.createElement("span");
    ana.append(spin, tick, anaText);
    const listBtn = document.createElement("button");
    listBtn.className = "act list";
    listBtn.textContent = "☰ Oynatma listesini indir";
    const close = document.createElement("button");
    close.className = "close";
    close.textContent = "×";
    close.title = "Bu video için gizle";

    actions.append(videoBtn, queueBtn, ana, listBtn);
    body.append(title, actions);
    box.append(dot, grip, body, close);
    root.append(style, box);
    document.documentElement.appendChild(host);
    parts = { box, grip, title, videoBtn, queueBtn, listBtn, close, dot, ana, anaText };

    videoBtn.addEventListener("click", () => send("video"));
    queueBtn.addEventListener("click", () => send("queue"));
    listBtn.addEventListener("click", () => send("list"));
    close.addEventListener("click", () => { dismissedFor = shownKey; host.style.display = "none"; });
    dot.addEventListener("click", wake);
    host.addEventListener("mouseenter", () => { clearTimeout(idleTimer); setCompact(false); });
    host.addEventListener("mouseleave", wake);
    enableDrag(grip);
    log("YouTube kutusu oluşturuldu");
    renderAna();

    try {
      chrome.storage.local.get(POS_KEY, (values) => {
        const saved = values && values[POS_KEY];
        pos = validPos(saved) ? saved : null;
        if (!pos) { try { chrome.storage.local.remove(POS_KEY); } catch (e) {} }
        applyPos();
      });
    } catch (error) { /* eklenti yeniden yüklenmiş olabilir */ }
  }

  function clampIntoView() {
    if (!host || host.style.display === "none") return;
    const r = host.getBoundingClientRect();
    if (r.width === 0) return;
    if (r.right > window.innerWidth + 1 || r.bottom > window.innerHeight + 1 || r.left < -1 || r.top < -1) {
      place(r.left, r.top);
    }
  }

  function setCompact(compact) {
    if (!host) return;
    parts.box.classList.toggle("compact", compact);
    requestAnimationFrame(clampIntoView);   // boyut değişince ekran dışına taşmasın
  }

  function wake() {
    if (!host) return;
    setCompact(false);
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => setCompact(true), IDLE_MS);
  }

  function place(x, y) {
    const rect = host.getBoundingClientRect();
    const left = Math.min(Math.max(0, x), Math.max(0, window.innerWidth - rect.width));
    const top = Math.min(Math.max(0, y), Math.max(0, window.innerHeight - rect.height));
    Object.assign(host.style, { left: `${left}px`, top: `${top}px`, right: "auto", bottom: "auto" });
  }

  function applyPos() {
    if (!host) return;
    if (!pos) {
      Object.assign(host.style, { right: "24px", bottom: "24px", left: "auto", top: "auto" });
    } else if (host.style.display !== "none") {
      place(pos.x * window.innerWidth, pos.y * window.innerHeight);   // gizliyken ölçme: genişlik 0 çıkar
    }
    requestAnimationFrame(clampIntoView);
  }

  function enableDrag(grip) {
    grip.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      grip.setPointerCapture(event.pointerId);
      const rect = host.getBoundingClientRect();
      const dx = event.clientX - rect.left;
      const dy = event.clientY - rect.top;
      const move = (e) => place(e.clientX - dx, e.clientY - dy);
      const up = () => {
        grip.removeEventListener("pointermove", move);
        grip.removeEventListener("pointerup", up);
        const r = host.getBoundingClientRect();
        pos = { x: r.left / window.innerWidth, y: r.top / window.innerHeight };
        try { chrome.storage.local.set({ [POS_KEY]: pos }); } catch (error) {}
      };
      grip.addEventListener("pointermove", move);
      grip.addEventListener("pointerup", up);
    });
  }

  // ------------------------------------------------------------------ başlık
  function renderTitle() {
    if (!parts.title) return;
    const text = statusText || titleText || "Başlık alınıyor…";
    parts.title.textContent = text;
    parts.title.title = text;
  }

  function flash(text, ms = 3000) {
    statusText = text;
    renderTitle();
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => { statusText = ""; renderTitle(); }, ms);
  }

  async function fetchOembed(id) {
    const target = OEMBED + encodeURIComponent("https://www.youtube.com/watch?v=" + id);
    const response = await fetch(target, { credentials: "omit" });
    if (!response.ok) throw new Error("oembed " + response.status);
    const data = await response.json();
    return String(data.title || "").trim();
  }

  function domTitle(s) {
    try {
      if (s.video && !isShorts()) {
        const flexy = document.querySelector("ytd-watch-flexy");
        if (flexy && flexy.getAttribute("video-id") === s.id) {
          const h1 = document.querySelector("ytd-watch-metadata h1, h1.ytd-watch-metadata");
          const text = h1 && h1.textContent.trim();
          if (text) return text;
        }
      } else if (s.video && isShorts()) {
        const active = document.querySelector("ytd-reel-video-renderer[is-active]");
        const el = active && active.querySelector("h2, yt-formatted-string.ytd-reel-player-header-renderer");
        const text = el && el.textContent.trim();
        if (text) return text;
      }
    } catch (error) { /* sayfa yapısı değişmiş olabilir */ }
    return "";
  }

  function docTitleFallback() {
    const text = pageTitle();
    return text && text !== "YouTube" && text !== prevTitle ? text : "";
  }

  async function resolveTitle(s) {
    if (titleResolved || titleBusy || !s.key) return;
    const key = s.key;
    titleBusy = true;
    titleTries += 1;
    let found = "";
    try {
      if (s.video) found = await fetchOembed(s.id);
    } catch (error) { /* ağ ya da çapraz köken: yedeklere geç */ }
    if (!found) found = domTitle(s);
    if (!found && titleTries >= 2) found = docTitleFallback();
    if (!found && titleTries >= 6) found = s.video ? "YouTube videosu" : "Oynatma listesi";
    titleBusy = false;
    if (key !== titleKey) return;
    if (found) {
      titleCache.set(key, found);
      titleText = found;
      titleResolved = true;
      renderTitle();
    }
  }

  function startTitleFor(s) {
    if (titleResolved && titleText) prevTitle = titleText;
    titleKey = s.key;
    titleText = titleCache.get(s.key) || "";
    titleResolved = Boolean(titleText);
    titleTries = 0;
    titleBusy = false;
    statusText = "";
    renderTitle();
  }

  // ------------------------------------------------------------------ otomatik (önceden) analiz
  function renderAna() {
    if (!parts.ana) return;
    parts.ana.classList.toggle("show", anaState !== "none");
    parts.ana.classList.toggle("done", anaState === "done");
    parts.anaText.textContent = anaState === "done" ? "analiz tamamlandı" : "(analiz ediliyor)";
  }

  function setAna(next) {
    anaState = next;
    renderAna();
  }

  // Sayfadaki video değişti mi? Değiştiyse eskisini hemen iptal eder, yenisini (kısa beklemeyle) başlatır.
  // Video sayfasından çıkılınca (ana sayfa, arama vb.) yalnızca iptal eder.
  function syncAnalysis() {
    const s = state();
    const want = s.video ? s.id : "";
    if (want === anaId) return;
    const old = anaId;
    anaId = want;
    anaToken += 1;
    clearTimeout(anaStartTimer);
    clearTimeout(anaPollTimer);
    clearTimeout(anaHideTimer);
    setAna("none");
    if (old) bg({ type: "prefetch-cancel", id: old });
    if (!want) return;
    const token = anaToken;
    anaStartTimer = setTimeout(() => startAnalysis(token, want), ANA_START_DELAY);
  }

  async function startAnalysis(token, id) {
    if (token !== anaToken || state().id !== id) return;
    const reply = await bg({ type: "prefetch", url: videoUrl() });
    if (token !== anaToken) return;
    if (!reply || !reply.ok || !reply.started) return;   // uygulama kapalı ya da özellik kapalı: sessizce geç
    setAna("running");
    pollAnalysis(token, id);
  }

  async function pollAnalysis(token, id) {
    const reply = await bg({ type: "analysis-status", id });
    if (token !== anaToken) return;
    if (reply && reply.ok && reply.state === "running") {
      anaPollTimer = setTimeout(() => pollAnalysis(token, id), ANA_POLL_MS);
      return;
    }
    if (reply && reply.ok && reply.state === "done") {
      setAna("done");
      anaHideTimer = setTimeout(() => { if (token === anaToken) setAna("none"); }, 3000);
      return;
    }
    setAna("none");   // hata / iptal / bilinmiyor: göstergeyi kaldır (tıklayınca uygulama zaten ayrıntıyı gösterir)
  }

  // ------------------------------------------------------------------ gönderme
  function send(kind) {
    const s = state();
    if (!s.key) return;
    const message = kind === "list"
      ? { type: "youtube", url: listUrl(), playlist: true }
      : { type: "youtube", url: videoUrl(), playlist: false, listed: kind === "queue" };
    const keyAtClick = s.key;
    flash("Gönderiliyor…", 30000);
    wake();
    try {
      chrome.runtime.sendMessage(message, (response) => {
        const same = state().key === keyAtClick;   // bu arada video değiştiyse mesajı gösterme
        if (same) {
          if (chrome.runtime.lastError || !response) flash("Eklenti yeniden yüklendi, sayfayı yenile");
          else if (response.ok) flash("Uygulamaya gönderildi ✓");
          else flash("Uygulama kapalı: sunucuyu aç");
        }
        wake();
      });
    } catch (error) {
      flash("Sayfayı yenile");
    }
  }

  function refresh() {
    try {
      syncAnalysis();   // kutu görünür olsun olmasın: analiz sayfadaki videoya bağlıdır
      const s = state();
      const visible = (s.video || s.list) && !isFullscreen() && dismissedFor !== s.key;
      if (!visible) {
        if (host) host.style.display = "none";
        return;
      }
      if (!host) build();
      else if (!host.isConnected) document.documentElement.appendChild(host);   // sayfa kutuyu düşürdüyse geri ekle

      const wasHidden = host.style.display === "none";
      parts.videoBtn.style.display = s.video ? "" : "none";
      parts.queueBtn.style.display = s.video ? "" : "none";
      parts.listBtn.style.display = s.list ? "" : "none";
      if (s.video) parts.videoBtn.title = videoUrl();
      if (s.list) parts.listBtn.title = listUrl();

      if (shownKey !== s.key) {
        shownKey = s.key;
        startTitleFor(s);
      }
      if (!titleResolved) resolveTitle(s);

      host.style.setProperty("display", "block", "important");
      host.style.setProperty("visibility", "visible", "important");
      host.style.setProperty("position", "fixed", "important");
      host.style.setProperty("opacity", "1", "important");
      if (wasHidden) {
        applyPos();
        wake();
      }
    } catch (error) {
      console.warn("[IDA] refresh hatası:", error);
    }
  }

  // Eklenti penceresinden "Kutuyu göster": her şeyi sıfırlayıp zorla gösterir
  try {
    chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
      if (!message || message.type !== "showBox") return false;
      dismissedFor = "";
      pos = null;
      shownKey = "";
      try { chrome.storage.local.remove(POS_KEY); } catch (error) {}
      if (host) host.style.display = "none";
      refresh();
      const s = state();
      sendResponse({
        ok: Boolean(host && host.style.display !== "none"),
        video: s.video, list: s.list, fullscreen: isFullscreen(),
      });
      return false;
    });
  } catch (error) { /* eklenti yeniden yüklenmiş olabilir */ }

  document.addEventListener("yt-navigate-finish", () => { dismissedFor = ""; refresh(); });
  document.addEventListener("yt-page-data-updated", refresh);
  window.addEventListener("popstate", refresh);
  document.addEventListener("fullscreenchange", refresh);
  window.addEventListener("resize", () => { applyPos(); });
  // sekme kapanırken / sayfadan ayrılırken analiz iptal edilsin
  window.addEventListener("pagehide", () => { if (anaId) bg({ type: "prefetch-cancel", id: anaId }); });
  setInterval(refresh, 800);   // yedek: Shorts kaydırması ve adres/başlık değişimini yakalar
  refresh();
})();
