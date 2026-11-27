// Sayfanın kendi dünyasında çalışır (tüm çerçevelerde): sayfanın fetch / XMLHttpRequest ile aldığı yanıtların İÇİNE bakar.
// Bazı siteler HLS/DASH bildirim dosyalarını .m3u8 yerine "master.txt" gibi başka adla ve text/plain türüyle sunar; adrese ve
// içerik türüne bakan algılama bunları kaçırır. Burada yanıtın ilk baytlarında "#EXTM3U" (HLS) ya da "<MPD" (DASH) aranır.
// Yalnızca adres ve tür bildirilir (blobs.js aracılığıyla arka plana); yanıtlar değiştirilmez.
(() => {
  if (window.__idaStreamHook) return;
  window.__idaStreamHook = true;

  const SKIP_URL = /\.(ts|m4s|m4f|mp4|webm|jpe?g|png|gif|webp|svg|ico|js|mjs|css|woff2?|ttf|vtt|srt|aac|mp3|m4a|key|html?)(\?|#|$)/i;
  const SKIP_TYPE = /^(image|video|audio|font)\/|javascript|css|html|json/i;
  const MAX_LENGTH = 2 * 1024 * 1024;     // bildirim dosyaları küçüktür; büyük yanıtlara bakılmaz
  const seen = new Set();

  function classify(head) {
    if (/^\s*#EXTM3U/.test(head)) return { kind: "hls", master: /#EXT-X-STREAM-INF/.test(head) };
    if (/<MPD[\s>]/.test(head)) return { kind: "dash", master: true };
    return null;
  }

  function announce(rawUrl, head) {
    try {
      const url = new URL(rawUrl, location.href).href;
      if (!/^https?:/i.test(url) || seen.has(url)) return;
      const info = classify(head);
      if (!info) return;
      seen.add(url);
      window.postMessage({ __ida: "stream", url, kind: info.kind, master: info.master }, "*");
    } catch (error) { /* sayfayı asla bozma */ }
  }

  function wanted(url, type, length) {
    if (!url || SKIP_URL.test(String(url))) return false;
    if (type && SKIP_TYPE.test(type)) return false;
    return !(length && length > MAX_LENGTH);
  }

  async function readHead(reader) {
    let text = "";
    const decoder = new TextDecoder();
    for (let i = 0; i < 4 && text.length < 600; i++) {
      const { value, done } = await reader.read();
      if (done || !value) break;
      text += decoder.decode(value.subarray(0, 4096), { stream: true });
    }
    reader.cancel().catch(() => {});
    return text;
  }

  // fetch
  const nativeFetch = window.fetch;
  if (typeof nativeFetch === "function") {
    window.fetch = function (...args) {
      const promise = nativeFetch.apply(this, args);
      promise.then((response) => {
        try {
          const type = response.headers.get("content-type") || "";
          const length = Number(response.headers.get("content-length") || 0);
          if (!response.ok || !response.body || !wanted(response.url, type, length)) return;
          const reader = response.clone().body.getReader();   // sayfanın kendi okuması etkilenmez
          readHead(reader).then((head) => announce(response.url, head)).catch(() => {});
        } catch (error) {}
      }, () => {});
      return promise;
    };
  }

  // XMLHttpRequest
  const nativeOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url) {
    try { this.__idaUrl = url; } catch (error) {}
    return nativeOpen.apply(this, arguments);
  };
  const nativeSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function () {
    try { this.addEventListener("load", () => inspectXhr(this)); } catch (error) {}
    return nativeSend.apply(this, arguments);
  };

  function inspectXhr(xhr) {
    try {
      if (xhr.status < 200 || xhr.status >= 300) return;
      const url = xhr.responseURL || xhr.__idaUrl;
      const type = xhr.getResponseHeader("content-type") || "";
      if (!wanted(url, type, Number(xhr.getResponseHeader("content-length") || 0))) return;
      let head = "";
      if (xhr.responseType === "" || xhr.responseType === "text") {
        head = String(xhr.responseText || "").slice(0, 4096);
      } else if (xhr.responseType === "arraybuffer" && xhr.response && xhr.response.byteLength <= MAX_LENGTH) {
        head = new TextDecoder().decode(new Uint8Array(xhr.response, 0, Math.min(4096, xhr.response.byteLength)));
      }
      if (head) announce(url, head);
    } catch (error) {}
  }
})();
