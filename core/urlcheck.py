"""Adres denetimi (Qt yok): bir indirme adresinin açılıp açılmadığına, boyutuna ve dosya mı sayfa mı olduğuna bakar."""
from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0.0.0 Safari/537.36")
_HTML = ("text/html", "application/xhtml+xml")


def probe(url: str, headers: dict | None = None, timeout: float = 12.0) -> dict:
    """
    Adrese bir baytlık Range isteği atar. Dönen sözlük: ok, error, size (bilinmiyorsa 0), name, page (web sayfası mı).
    Ağ hatası fırlatmaz; ok=False ve anlaşılır mesaj verir.
    """
    sent = {"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "identity"}
    for key, value in (headers or {}).items():
        if key.lower() not in ("range", "accept-encoding"):
            sent[key] = value
    sent["Range"] = "bytes=0-0"
    try:
        with requests.get(url, headers=sent, stream=True, timeout=(10, timeout), allow_redirects=True) as r:
            if r.status_code not in (200, 206):
                return {"ok": False, "error": f"Sunucu HTTP {r.status_code} döndürdü."}
            ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
            disposition = r.headers.get("Content-Disposition", "")
            page = (ctype in _HTML and not re.match(r"\s*attachment", disposition, re.I)
                    and not urlparse(r.url).path.lower().endswith((".html", ".htm")))
            size = 0
            match = re.search(r"/(\d+)\s*$", r.headers.get("Content-Range", ""))
            if match:
                size = int(match.group(1))
            elif r.status_code == 200 and r.headers.get("Content-Length", "").isdigit():
                size = int(r.headers["Content-Length"])
            name = ""
            named = re.search(r'filename\*?\s*=\s*(?:[^\']*\'\')?"?([^";]+)"?', disposition, re.I)
            if named:
                name = unquote(named.group(1).strip())
            return {"ok": True, "size": size, "name": name or unquote(urlparse(r.url).path.rsplit("/", 1)[-1]),
                    "page": page}
    except requests.RequestException as e:
        return {"ok": False, "error": f"Siteye ulaşılamadı: {e}"}
