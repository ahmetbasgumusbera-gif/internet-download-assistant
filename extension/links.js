// Doğrudan dosya bağlantılarına tıklamayı, tarayıcı indirmeyi başlatmadan uygulamaya yönlendirir.
// Böylece tarayıcının "Farklı Kaydet" penceresi hiç açılmaz.
//  * ALWAYS listesi: tarayıcının zaten indirdiği türler (arşiv, kurulum, ofis belgesi, e-kitap, tasarım...). Hep yönlendirilir.
//  * VIEWABLE listesi: tarayıcının normalde sayfada AÇTIĞI türler (pdf, resim, ses, video, txt...). Tıklamada yalnızca
//    eklenti penceresindeki "Tarayıcıda açılan dosyaları da yakala" açıksa yönlendirilir; aksi hâlde bağlantı olağan
//    şekilde açılır. (Tarayıcı bunları gerçekten İNDİRİRSE - kaydet düğmesi, indirme özniteliği vb. - background.js zaten yakalar.)
//  * Ctrl / Shift / Alt + tık ya da orta tık: tarayıcının kendi davranışı.
//  * Uygulama kapalıysa ve açılamazsa tıklama tarayıcıya geri verilir.
(() => {
  const ALWAYS = new Set((
    // arşiv ve disk kalıpları
    "zip rar 7z tar gz tgz bz2 tbz2 xz txz zst lz lzma cab z arj wim iso img vhd vhdx vmdk ova " +
    // kurulum / uygulama / betik
    "exe msi msix appx apk aab xapk apks dmg pkg deb rpm appimage run bat cmd ps1 sh vbs reg whl jar msu msp " +
    // belgeler
    "doc docx dot dotx xls xlsx xlsm ppt pptx pps ppsx odt ods odp rtf csv tsv pages numbers key xps " +
    // e-kitap
    "epub azw azw3 mobi fb2 djvu cbz cbr " +
    // tasarım ve ham fotoğraf
    "psd ai eps indd xcf sketch fig tif tiff heic heif raw cr2 cr3 nef arw dng orf rw2 " +
    // tarayıcının çoğunlukla oynatamadığı ses/video
    "mkv avi flv wmv mov m4v mpg mpeg 3gp ogv m2ts vob wma amr aiff mid midi ape m4b mka ac3 " +
    // yazı tipi ve veri
    "ttf otf sql sqlite db bak"
  ).split(" "));

  const VIEWABLE = new Set((
    "pdf jpg jpeg jfif png gif webp svg bmp ico avif txt json xml md py js mjs yaml yml log " +
    "mp3 wav aac m4a ogg oga opus flac weba mp4 webm"
  ).split(" "));

  let enabled = true;
  let captureViewable = false;

  try {
    chrome.storage.local.get({ enabled: true, captureViewable: false }, (values) => {
      enabled = values.enabled !== false;
      captureViewable = values.captureViewable === true;
    });
    chrome.storage.onChanged.addListener((changes) => {
      if (changes.enabled) enabled = changes.enabled.newValue !== false;
      if (changes.captureViewable) captureViewable = changes.captureViewable.newValue === true;
    });
  } catch (error) { /* eklenti yeniden yüklenmiş olabilir */ }

  const extensionOf = (pathname) => {
    const match = /\.([a-z0-9]{1,8})$/i.exec(pathname || "");
    return match ? match[1].toLowerCase() : "";
  };

  function wanted(pathname) {
    const ext = extensionOf(pathname);
    if (!ext) return false;
    return ALWAYS.has(ext) || (captureViewable && VIEWABLE.has(ext));
  }

  function collectLinks() {
    const out = [];
    const seen = new Set();
    for (const a of document.querySelectorAll("a[href]")) {
      let url;
      try { url = new URL(a.href, location.href); } catch (error) { continue; }
      if (!/^https?:$/.test(url.protocol)) continue;
      const ext = extensionOf(url.pathname);
      if (!ext || !(ALWAYS.has(ext) || VIEWABLE.has(ext))) continue;
      if (seen.has(url.href)) continue;
      seen.add(url.href);
      out.push({ url: url.href, ext, text: (a.textContent || a.title || "").replace(/\s+/g, " ").trim().slice(0, 160) });
      if (out.length >= 3000) break;
    }
    return out;
  }

  try {
    chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
      if (!message || message.type !== "collect-links") return false;
      sendResponse({ ok: true, title: document.title, items: collectLinks() });
      return false;
    });
  } catch (error) { /* eklenti yeniden yüklenmiş olabilir */ }

  function handBack(link) {
    link.dataset.idaSkip = "1";
    link.click();
    setTimeout(() => { delete link.dataset.idaSkip; }, 500);
  }

  document.addEventListener("click", (event) => {
    if (!enabled || event.defaultPrevented || event.button !== 0) return;
    if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const link = event.target instanceof Element ? event.target.closest("a[href]") : null;
    if (!link || link.dataset.idaSkip) return;
    let url;
    try { url = new URL(link.href, location.href); } catch (error) { return; }
    if (!/^https?:$/.test(url.protocol) || !wanted(url.pathname)) return;

    event.preventDefault();
    event.stopImmediatePropagation();
    try {
      chrome.runtime.sendMessage({ type: "download", url: url.href, referrer: location.href }, (response) => {
        if (chrome.runtime.lastError || !response || !response.ok) handBack(link);
      });
    } catch (error) {
      handBack(link);
    }
  }, true);
})();
