"""
Yerel alıcı sunucu (Qt yok): tarayıcı eklentisinden gelen "şunu indir" isteklerini kabul eder.

Güvenlik (bir web sitesi bu sunucuya indirme başlatamamalı):
  * Yalnızca 127.0.0.1'e bağlanır; Host başlığı 127.0.0.1/localhost:PORT olmalı (DNS rebinding engeli).
  * Her istekte özel "X-IDA-Client" başlığı zorunludur. Web sayfaları özel başlığı ancak ön kontrolle
    (CORS preflight) gönderebilir ve ön kontrole yalnızca eklenti kökenleri (chrome-extension://) onaylanır.
  * Origin başlığı varsa bir eklenti kökeni olmalıdır; sıradan web sitelerinin istekleri 403 alır.
Sunucu isteği doğrular ve on_request(tür, veri) geri çağırımına verir (sunucunun iş parçacığından çağrılır).
"""
from __future__ import annotations

import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from core import handoff, incoming, prefetch
from utils import paths
from utils.logger import get_logger
from utils.version import __version__

log = get_logger(__name__)

APP_VERSION = "1"
DEFAULT_PORT = 17653
CLIENT_HEADER = "X-IDA-Client"
_EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://")
_MAX_BODY = 1_000_000


def version_key(text: str) -> tuple:
    """"1.2.0" -> (1, 2, 0): sürüm karşılaştırması için."""
    parts = []
    for piece in str(text or "0").split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def bundled_extension_version() -> str:
    """Kurulumla gelen eklentinin sürümü (extension/manifest.json); okunamazsa boş."""
    try:
        return str(json.loads((paths.EXTENSION_DIR / "manifest.json").read_text(encoding="utf-8")).get("version", ""))
    except (OSError, ValueError):
        return ""


def _text(value, limit: int) -> str:
    return "" if value is None else str(value)[:limit]


def _check_url(value) -> str:
    url = _text(value, 4096).strip()
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("Geçersiz adres")
    return url


def clean_download(data: dict) -> dict:
    """Eklentiden gelen indirme isteğini doğrular ve temizler (geçersizse ValueError)."""
    try:
        size = int(data.get("fileSize"))
    except (TypeError, ValueError):
        size = -1
    return {
        "url": _check_url(data.get("url")),
        "filename": _text(data.get("filename"), 255).strip(),
        "fileSize": size if size > 0 else -1,
        "mime": _text(data.get("mime"), 100),
        "referrer": _text(data.get("referrer"), 4096),
        "cookies": _text(data.get("cookies"), 50_000),
        "userAgent": _text(data.get("userAgent"), 500),
    }


def clean_youtube(data: dict) -> dict:
    return {"url": _check_url(data.get("url")), "playlist": bool(data.get("playlist")), "listed": bool(data.get("listed")),
            "jar": _clean_jar(data.get("jar"))}


def clean_prefetch(data: dict) -> dict:
    return {"url": _check_url(data.get("url")), "jar": _clean_jar(data.get("jar"))}


def clean_prefetch_cancel(data: dict) -> dict:
    return {"id": _text(data.get("id"), 64).strip()}


def _int(value) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def clean_webvideo(data: dict) -> dict:
    """Eklentinin sitede bulduğu video adaylarını doğrular ve temizler (sayfa adresi geçersizse ValueError)."""
    items = []
    for raw in (data.get("items") or [])[:40]:
        if not isinstance(raw, dict):
            continue
        try:
            url = _check_url(raw.get("url"))
        except ValueError:
            continue
        kind = raw.get("kind") if raw.get("kind") in ("file", "hls", "dash") else "file"
        items.append({
            "url": url, "kind": kind, "mime": _text(raw.get("mime"), 100), "size": _int(raw.get("size")),
            "referrer": _text(raw.get("referrer"), 4096), "score": int(float(raw.get("score") or 0)),
            "ad": bool(raw.get("ad")), "title": _text(raw.get("title"), 300), "poster": _text(raw.get("poster"), 2000),
            "duration": _int(raw.get("duration")), "width": _int(raw.get("width")), "height": _int(raw.get("height")),
            "cookies": _text(raw.get("cookies"), 6000), "source": _text(raw.get("source"), 20),
        })
    jar = []
    for c in (data.get("jar") or [])[:300]:
        if isinstance(c, dict) and c.get("name") is not None and c.get("domain"):
            jar.append({"domain": _text(c.get("domain"), 255), "path": _text(c.get("path") or "/", 1024),
                        "secure": bool(c.get("secure")), "hostOnly": bool(c.get("hostOnly")),
                        "name": _text(c.get("name"), 255), "value": _text(c.get("value"), 4096),
                        "expires": _int(c.get("expires"))})
    return {"pageUrl": _check_url(data.get("pageUrl")), "pageTitle": _text(data.get("pageTitle"), 300),
            "image": _text(data.get("image"), 2000), "userAgent": _text(data.get("userAgent"), 500),
            "items": items, "jar": jar, "listed": bool(data.get("listed"))}


def _clean_jar(raw) -> list:
    """Eklentiden gelen çerez listesini doğrular (en fazla 400 çerez)."""
    jar = []
    for c in (raw or [])[:400]:
        if isinstance(c, dict) and c.get("name") is not None and c.get("domain"):
            jar.append({"domain": _text(c.get("domain"), 255), "path": _text(c.get("path") or "/", 1024),
                        "secure": bool(c.get("secure")), "hostOnly": bool(c.get("hostOnly")),
                        "httpOnly": bool(c.get("httpOnly")), "name": _text(c.get("name"), 255),
                        "value": _text(c.get("value"), 4096), "expires": _int(c.get("expires"))})
    return jar


def clean_links(data: dict) -> dict:
    """Eklentinin sayfadan topladığı dosya bağlantılarını doğrular (en fazla 3000, yinelenenler atılır)."""
    items, seen = [], set()
    for raw in (data.get("items") or [])[:3000]:
        if not isinstance(raw, dict):
            continue
        try:
            url = _check_url(raw.get("url"))
        except ValueError:
            continue
        if url in seen:
            continue
        seen.add(url)
        items.append({"url": url, "text": _text(raw.get("text"), 160).strip(),
                      "ext": _text(raw.get("ext"), 12).lower()})
    return {"pageUrl": _check_url(data.get("pageUrl")), "pageTitle": _text(data.get("pageTitle"), 300),
            "userAgent": _text(data.get("userAgent"), 500), "cookies": _text(data.get("cookies"), 50_000),
            "items": items}


def clean_handoff_taken(data: dict) -> dict:
    return {"ticket": _text(data.get("ticket"), 64).strip()}


class _Httpd(ThreadingHTTPServer):
    daemon_threads = True
    # Windows'ta SO_REUSEADDR aynı porta ikinci bir dinleyicinin bağlanmasına izin verir; orada kapalı olmalı
    allow_reuse_address = os.name != "nt"
    owner: "LocalServer"

    def handle_error(self, request, client_address):
        pass  # istemci bağlantıyı kapatınca oluşan hataları sustur


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "IDA"

    def log_message(self, *args):
        pass

    # ------------------------------------------------------------------ yardımcılar
    def _extension_origin(self) -> Optional[str]:
        origin = self.headers.get("Origin")
        return origin if origin and origin.startswith(_EXTENSION_ORIGINS) else None

    def _host_ok(self) -> bool:
        port = self.server.owner.port
        return (self.headers.get("Host") or "").lower() in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _send(self, code: int, body: Optional[dict] = None) -> None:
        payload = b"" if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        origin = self._extension_origin()
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Vary", "Origin")
        if body is not None:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _guard(self) -> bool:
        """Ortak güvenlik kontrolü. Geçmezse cevabı kendisi gönderir ve False döner."""
        if not self._host_ok():
            self._send(403, {"ok": False, "error": "host"})
            return False
        if self.headers.get("Origin") and not self._extension_origin():
            self._send(403, {"ok": False, "error": "origin"})
            return False
        client = self.headers.get(CLIENT_HEADER)
        if not client:
            self._send(403, {"ok": False, "error": "client"})
            return False
        self.server.owner.note_client(client)
        return True

    # ------------------------------------------------------------------ yöntemler
    def do_OPTIONS(self):
        origin = self._extension_origin()
        if not self._host_ok() or not origin:
            self._send(403, {"ok": False, "error": "origin"})
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", f"Content-Type, {CLIENT_HEADER}")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self._guard():
            return
        if self.path.split("?")[0] == "/api/ping":
            self._send(200, {"ok": True, "app": "IDA", "version": __version__,
                             "extensionVersion": self.server.owner.extension_version})
        elif self.path.split("?")[0] == "/api/analysis":      # önceden analiz durumu: none | running | done | error
            query = parse_qs(urlparse(self.path).query)
            self._send(200, {"ok": True, "state": prefetch.PREFETCH.state((query.get("id") or [""])[0])})
        elif self.path.split("?")[0] == "/api/handoff":      # indirme bileti durumu: pending | running | done | failed ...
            query = parse_qs(urlparse(self.path).query)
            self._send(200, {"ok": True, **handoff.HANDOFF.state((query.get("id") or [""])[0])})
        else:
            self._send(404, {"ok": False, "error": "yol"})

    def _handle_upload(self) -> None:
        """Tarayıcının blob:/data: ile verdiği (adresi olmayan) dosyayı parça parça alır: POST /api/upload?id=&index=&last=&name="""
        query = parse_qs(urlparse(self.path).query)

        def field(name: str, limit: int = 255) -> str:
            return _text((query.get(name) or [""])[0], limit)

        upload_id = field("id", 40)
        try:
            index = int(field("index", 12))
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            index, length = -1, -1
        if not re.fullmatch(r"[a-f0-9]{8,32}", upload_id) or index < 0 or not 0 <= length <= incoming.MAX_CHUNK:
            self._send(400, {"ok": False, "error": "istek"})
            return
        data = self.rfile.read(length) if length else b""
        try:
            incoming.INCOMING.append(upload_id, index, data)
            if field("last", 2) == "1":
                path = incoming.INCOMING.finish(upload_id)
                self.server.owner.on_request("upload", {
                    "path": str(path), "filename": field("name"), "mime": field("mime", 100),
                    "referrer": field("referrer", 2000), "size": path.stat().st_size,
                })
        except ValueError as e:
            incoming.INCOMING.abort(upload_id)
            self._send(409, {"ok": False, "error": str(e)})
            return
        except Exception:
            log.exception("Gelen dosya işlenemedi")
            incoming.INCOMING.abort(upload_id)
            self._send(500, {"ok": False, "error": "iç hata"})
            return
        self._send(200, {"ok": True})

    def do_POST(self):
        if not self._guard():
            return
        path = self.path.split("?")[0]
        if path == "/api/upload":
            self._handle_upload()
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > _MAX_BODY:
            self._send(413, {"ok": False, "error": "boyut"})
            return
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("json")
        except ValueError:
            self._send(400, {"ok": False, "error": "json"})
            return
        try:
            if path == "/api/download":
                kind, payload = "download", clean_download(data)
            elif path == "/api/youtube":
                kind, payload = "youtube", clean_youtube(data)
            elif path == "/api/prefetch":
                kind, payload = "prefetch", clean_prefetch(data)
            elif path == "/api/prefetch-cancel":
                kind, payload = "prefetch_cancel", clean_prefetch_cancel(data)
            elif path == "/api/webvideo":
                kind, payload = "webvideo", clean_webvideo(data)
            elif path == "/api/links":
                kind, payload = "links", clean_links(data)
            elif path == "/api/handoff-taken":
                kind, payload = "handoff_taken", clean_handoff_taken(data)
            else:
                self._send(404, {"ok": False, "error": "yol"})
                return
        except ValueError as e:
            self._send(400, {"ok": False, "error": str(e)})
            return
        try:
            result = self.server.owner.on_request(kind, payload)
        except Exception:
            log.exception("İstek işlenemedi")
            self._send(500, {"ok": False, "error": "iç hata"})
            return
        reply = {"ok": True}
        if isinstance(result, dict):
            reply.update(result)
        self._send(200, reply)


class LocalServer:
    """127.0.0.1 üzerinde dinleyen küçük HTTP sunucusu (kendi iş parçacığında)."""

    def __init__(self, port: int, on_request: Callable[[str, dict], None], host: str = "127.0.0.1",
                 on_client: Optional[Callable[[str], None]] = None):
        self.port = int(port)
        self.on_request = on_request
        self.on_client = on_client            # eklenti sürümünü ilk gördüğünde (ya da değişince) çağrılır
        self.extension_version = ""           # kurulumla gelen eklentinin sürümü (ping cevabında bildirilir)
        self._seen_client = ""
        self._host = host
        self._httpd: Optional[_Httpd] = None
        self._thread: Optional[threading.Thread] = None

    def note_client(self, header: str) -> None:
        """X-IDA-Client: "extension/1.2.0" başlığından eklenti sürümünü ayıklar."""
        if header == self._seen_client or self.on_client is None:
            return
        self._seen_client = header
        if header.startswith("extension/"):
            try:
                self.on_client(header.split("/", 1)[1])
            except Exception:
                log.exception("İstemci bildirimi işlenemedi")

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self) -> None:
        """Dinlemeye başlar. Port doluysa OSError fırlatır."""
        if self._httpd is not None:
            return
        httpd = _Httpd((self._host, self.port), _Handler)
        httpd.owner = self
        self._httpd = httpd
        self._thread = threading.Thread(
            target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True, name="ida-server")
        self._thread.start()
        log.info("Yerel sunucu açıldı: %s:%d", self._host, self.port)

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is None:
            return
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:
            log.exception("Yerel sunucu kapatılamadı")
        if self._thread is not None:
            self._thread.join(2)
            self._thread = None
        log.info("Yerel sunucu kapandı")
