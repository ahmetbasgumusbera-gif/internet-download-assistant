// Eklentinin dünyasında (her çerçevede) çalışır: blobs_main.js'in bildirdiği son birkaç Blob'u hatırlar.
// Arka plan, tarayıcı bir blob: indirmesi başlattığında (örn. Pinterest "İndir") bu sayfalara "şu adresi oku" der;
// adresi bilen çerçeve dosyayı parça parça arka plana (oradan uygulamaya) yollar.
(() => {
  const known = new Map();      // blob adresi -> Blob (en son MAX tanesi)
  const MAX = 8;
  const CHUNK = 1500 * 1024;    // base64 olunca ~2 MB

  window.addEventListener("message", (event) => {
    if (event.source !== window) return;
    const data = event.data;
    if (!data || typeof data.url !== "string") return;
    if (data.__ida === "stream") {          // sniff_main.js: sayfanın aldığı HLS/DASH bildirim dosyası
      try {
        chrome.runtime.sendMessage({ type: "stream-found", url: data.url, kind: data.kind, master: Boolean(data.master),
          frameUrl: location.href }, () => void chrome.runtime.lastError);
      } catch (error) {}
      return;
    }
    if (data.__ida !== "blob" || !data.blob) return;
    known.set(data.url, data.blob);
    while (known.size > MAX) known.delete(known.keys().next().value);
  });

  const toBase64 = (piece) => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
    reader.onerror = reject;
    reader.readAsDataURL(piece);
  });

  const send = (message) => new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(message, (reply) => resolve(chrome.runtime.lastError ? null : reply));
    } catch (error) {
      resolve(null);
    }
  });

  async function stream(blob, id) {
    let index = 0;
    try {
      for (let offset = 0; ; offset += CHUNK) {
        const data = await toBase64(blob.slice(offset, offset + CHUNK));
        const last = offset + CHUNK >= blob.size;
        const reply = await send({ type: "blob-chunk", id, index, last, data });
        if (!reply || !reply.ok || last) return;
        index += 1;
      }
    } catch (error) {
      send({ type: "blob-chunk", id, error: true });
    }
  }

  try {
    chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
      if (!message || message.type !== "read-blob") return false;
      const blob = known.get(message.url);
      if (!blob) return false;               // bu çerçevede yok: cevap verme, doğru çerçeve cevap versin
      sendResponse({ ok: true, size: blob.size });
      stream(blob, message.id);
      return false;
    });
  } catch (error) { /* eklenti yeniden yüklenmiş olabilir */ }
})();
