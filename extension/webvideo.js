// Her sitede (YouTube hariç) çalışır:
//  * sayfadaki <video> öğelerini ve sayfa bilgisini (başlık, görsel, og:video, JSON-LD) arka plana bildirir,
//  * en üst çerçevede, video algılanınca küçük bir "Videoyu indir" kutusu gösterir (tıklayınca uygulamada liste açılır).
(() => {
  if (window.__idaWebVideo) return;
  window.__idaWebVideo = true;
  const isTop = window === window.top;
  const POS_KEY = "wvBoxPos";

  // ------------------------------------------------------------------ sayfa bilgisi
  const text = (selector, attr = "content") => {
    const el = document.querySelector(selector);
    return el ? String(el.getAttribute(attr) || "").trim() : "";
  };

  function isoSeconds(value) {
    const m = /^P(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)$/.exec(String(value || ""));
    return m ? (Number(m[1] || 0) * 3600 + Number(m[2] || 0) * 60 + Number(m[3] || 0)) : 0;
  }

  function firstImage(value) {
    if (!value) return "";
    if (typeof value === "string") return value;
    if (Array.isArray(value)) return firstImage(value[0]);
    return firstImage(value.url || value.contentUrl);
  }

  function collectVideoObjects(node, out) {
    if (!node || out.length >= 5) return;
    if (Array.isArray(node)) { node.forEach((n) => collectVideoObjects(n, out)); return; }
    if (typeof node !== "object") return;
    const type = [].concat(node["@type"] || []);
    if (type.includes("VideoObject")) {
      out.push({
        name: String(node.name || "").trim(), contentUrl: String(node.contentUrl || ""),
        thumbnail: firstImage(node.thumbnailUrl), duration: isoSeconds(node.duration),
      });
    }
    if (node["@graph"]) collectVideoObjects(node["@graph"], out);
  }

  let metaCache = null;
  let metaAt = 0;
  function readMeta() {
    if (metaCache && Date.now() - metaAt < 3000) return metaCache;
    const objects = [];
    for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
      const raw = script.textContent || "";
      if (raw.length > 200000) continue;
      try { collectVideoObjects(JSON.parse(raw), objects); } catch (error) { /* bozuk JSON */ }
    }
    const ld = objects[0] || {};
    metaCache = {
      title: ld.name || text('meta[property="og:title"]') || text('meta[name="twitter:title"]') || document.title || "",
      image: ld.thumbnail || text('meta[property="og:image"]') || text('meta[name="twitter:image"]'),
      ogVideo: text('meta[property="og:video:secure_url"]') || text('meta[property="og:video:url"]') || text('meta[property="og:video"]'),
      jsonld: objects,
    };
    metaAt = Date.now();
    return metaCache;
  }

  function scanVideos() {
    const list = [];
    for (const v of Array.from(document.querySelectorAll("video")).slice(0, 10)) {
      const rect = v.getBoundingClientRect();
      const srcs = new Set();
      if (v.currentSrc) srcs.add(v.currentSrc);
      if (v.src) srcs.add(v.src);
      v.querySelectorAll("source[src]").forEach((s) => srcs.add(s.src));
      const all = [...srcs];
      list.push({
        srcs: all.filter((u) => /^https?:/i.test(u)),
        mse: all.some((u) => u.startsWith("blob:")),          // akış oynatıcıları (hls.js, dash.js) blob: kullanır
        poster: v.poster || "",
        duration: Number.isFinite(v.duration) ? Math.round(v.duration) : 0,
        w: v.videoWidth || 0, h: v.videoHeight || 0,
        area: Math.round(Math.max(0, rect.width) * Math.max(0, rect.height)),
        playing: !v.paused && !v.ended && v.readyState > 2,
        played: v.currentTime > 0,
      });
    }
    return list;
  }

  let lastSent = "";
  function report(force = false) {
    let message;
    try {
      const videos = scanVideos();
      if (!isTop && videos.length === 0) return;      // videosuz alt çerçeveler sessiz kalır
      message = { type: "page-info", url: location.href, videos, meta: isTop || videos.length ? readMeta() : null };
    } catch (error) {
      return;
    }
    const signature = JSON.stringify(message);
    if (!force && signature === lastSent) return;
    lastSent = signature;
    try { chrome.runtime.sendMessage(message, () => void chrome.runtime.lastError); } catch (error) {}
  }

  let reportTimer = 0;
  const reportSoon = () => { clearTimeout(reportTimer); reportTimer = setTimeout(report, 400); };
  for (const type of ["play", "playing", "loadedmetadata", "durationchange"]) {
    document.addEventListener(type, reportSoon, true);   // medya olayları kabarmaz: yakalama aşamasında dinlenir
  }
  setTimeout(() => report(true), 1500);
  setInterval(() => { if (document.querySelector("video")) report(); }, 3000);

  if (!isTop) return;

  // ------------------------------------------------------------------ küçük kutu (yalnızca en üst çerçeve)
  let host = null;
  let parts = {};
  let state = { count: 0, title: "" };
  let dismissedUrl = "";
  let statusTimer = 0;
  let pos = null;
  let idleTimer = 0;
  const IDLE_MS = 8000;                // 8 sn sonra küçülür ve saydamlaşır

  const CSS = `
    .box { display: flex; align-items: center; gap: 8px; max-width: 340px; padding: 8px 10px 8px 6px; border-radius: 14px;
      background: rgba(12, 13, 22, .92); border: 1px solid rgba(167, 139, 250, .55); box-shadow: 0 6px 24px rgba(0, 0, 0, .45);
      color: #e8eaf6; font: 13px/1.3 'Segoe UI', Roboto, Arial, sans-serif; user-select: none; box-sizing: border-box; }
    .grip { width: 14px; align-self: stretch; display: flex; align-items: center; justify-content: center; cursor: grab;
      color: #8c92b8; font-size: 15px; letter-spacing: -2px; touch-action: none; }
    .grip:active { cursor: grabbing; }
    .body { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
    .title { font-size: 11px; color: #aab0d4; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 230px; }
    button.act { border: none; cursor: pointer; padding: 7px 12px; border-radius: 999px; font: 600 12px/1 'Segoe UI', Roboto, Arial, sans-serif;
      color: #0c0d16; background: linear-gradient(90deg, #a78bfa, #22d3ee); white-space: nowrap; }
    button.act:hover { filter: brightness(1.12); }
    .count { margin-left: 6px; padding: 1px 6px; border-radius: 999px; background: rgba(12, 13, 22, .25); }
    button.close { border: none; background: transparent; color: #8c92b8; cursor: pointer; font-size: 16px; line-height: 1; padding: 0 2px; }
    button.close:hover { color: #e8eaf6; }
    .dot { display: none; width: 32px; height: 32px; align-items: center; justify-content: center; cursor: pointer;
      font-size: 17px; font-weight: 700; color: #0c0d16; }
    .box { transition: opacity .35s ease; }
    .box.compact { opacity: .45; padding: 0; gap: 0; border-radius: 999px; border: 2px solid rgba(255, 255, 255, .55);
      background: linear-gradient(135deg, #a78bfa, #22d3ee); }
    .box.compact .grip, .box.compact .body, .box.compact .close { display: none; }
    .box.compact .dot { display: flex; }
  `;

  function build() {
    document.querySelectorAll('[data-ida-box="wv"]').forEach((n) => n.remove());   // eklenti yenilendiyse eski kutu
    host = document.createElement("div");
    host.id = "ida-" + Math.random().toString(36).slice(2, 10);
    host.setAttribute("data-ida-box", "wv");
    host.style.cssText = "all: initial; position: fixed; z-index: 2147483000; display: none; right: 24px; bottom: 24px;";
    const root = host.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = CSS;
    const box = document.createElement("div");
    box.className = "box";
    const grip = document.createElement("div");
    grip.className = "grip";
    grip.textContent = "⋮⋮";
    grip.title = "Sürükleyerek taşı";
    const body = document.createElement("div");
    body.className = "body";
    const title = document.createElement("div");
    title.className = "title";
    const button = document.createElement("button");
    button.className = "act";
    const count = document.createElement("span");
    count.className = "count";
    const label = document.createElement("span");
    button.append(label, count);
    const queue = document.createElement("button");
    queue.className = "act";
    queue.textContent = "＋ Listeye ekle";
    queue.title = "En iyi videoyu indirmeden Listeler sekmesine ekler";
    queue.style.background = "transparent";
    queue.style.color = "#c4b5fd";
    queue.style.border = "1px solid rgba(167, 139, 250, .55)";
    const close = document.createElement("button");
    close.className = "close";
    close.textContent = "×";
    close.title = "Bu sayfada gizle";
    body.append(title, button, queue);
    const dot = document.createElement("div");
    dot.className = "dot";
    dot.textContent = "⬇";
    dot.title = "İnternet Download Assistant";
    box.append(dot, grip, body, close);
    root.append(style, box);
    document.documentElement.appendChild(host);
    parts = { box, dot, grip, title, button, label, count, close };
    host.addEventListener("mouseenter", () => { clearTimeout(idleTimer); setCompact(false); });
    host.addEventListener("mouseleave", wake);
    dot.addEventListener("click", wake);
    button.addEventListener("click", () => open(false));
    queue.addEventListener("click", () => open(true));
    close.addEventListener("click", () => { dismissedUrl = location.href; render(); });
    enableDrag(grip);
    try {
      chrome.storage.local.get(POS_KEY, (values) => {
        const saved = values && values[POS_KEY];
        pos = saved && Number.isFinite(saved.x) && Number.isFinite(saved.y) ? saved : null;
        applyPos();
      });
    } catch (error) {}
  }

  function place(x, y) {
    const rect = host.getBoundingClientRect();
    const left = Math.min(Math.max(0, x), Math.max(0, window.innerWidth - rect.width));
    const top = Math.min(Math.max(0, y), Math.max(0, window.innerHeight - rect.height));
    Object.assign(host.style, { left: `${left}px`, top: `${top}px`, right: "auto", bottom: "auto" });
  }

  function applyPos() {
    if (!host || !pos || host.style.display === "none") return;
    place(pos.x * window.innerWidth, pos.y * window.innerHeight);
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

  function setCompact(on) {
    if (!host || !parts.box) return;
    parts.box.classList.toggle("compact", on);
    requestAnimationFrame(applyPos);
  }

  function wake() {
    if (!host) return;
    setCompact(false);
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => setCompact(true), IDLE_MS);
  }

  function render() {
    const visible = state.count > 0 && dismissedUrl !== location.href && !document.fullscreenElement;
    if (!visible) {
      if (host) host.style.display = "none";
      return;
    }
    if (!host) build();
    else if (!host.isConnected) document.documentElement.appendChild(host);
    parts.label.textContent = "⬇ Videoyu indir";
    parts.count.textContent = String(state.count);
    parts.count.style.display = state.count > 1 ? "" : "none";
    parts.title.textContent = parts.title.title = state.title || document.title || "";
    const wasHidden = host.style.display === "none" || !host.style.display;
    host.style.setProperty("display", "block", "important");
    if (wasHidden) wake();
    applyPos();
  }

  function flash(message, ms = 3000) {
    if (!parts.title) return;
    parts.title.textContent = message;
    clearTimeout(statusTimer);
    statusTimer = setTimeout(render, ms);
  }

  function open(listed) {
    flash("Gönderiliyor…", 30000);
    wake();
    try {
      chrome.runtime.sendMessage({ type: "wv-open", listed: listed === true }, (reply) => {
        if (chrome.runtime.lastError || !reply) flash("Eklenti yeniden yüklendi, sayfayı yenile");
        else if (reply.ok) flash("Uygulamaya gönderildi ✓");
        else flash("Uygulama kapalı: sunucuyu aç");
      });
    } catch (error) {
      flash("Sayfayı yenile");
    }
  }

  try {
    chrome.runtime.onMessage.addListener((message) => {
      if (message && message.type === "wv-state") {
        state = { count: message.count || 0, title: message.title || "" };
        render();
      }
      return false;
    });
  } catch (error) {}
  document.addEventListener("fullscreenchange", render);
  window.addEventListener("resize", applyPos);
})();
