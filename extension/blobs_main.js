// Sayfanın kendi dünyasında çalışır: sayfa URL.createObjectURL ile bir dosya (Blob) üretirse, adresiyle birlikte
// eklentinin (blobs.js) görmesi için iletir. Böylece site "İndir" düğmesinde blob: adresi kullansa ve adresi hemen
// iptal etse bile (revokeObjectURL) uygulama dosyayı alabilir.
(() => {
  const original = URL.createObjectURL;
  URL.createObjectURL = function (object) {
    const url = original.apply(this, arguments);
    try {
      if (object && typeof object.size === "number" && typeof object.slice === "function") {
        window.postMessage({ __ida: "blob", url, blob: object }, "*");
      }
    } catch (error) { /* sayfayı asla bozma */ }
    return url;
  };
})();
