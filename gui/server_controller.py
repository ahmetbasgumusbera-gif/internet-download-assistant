"""
Yerel sunucunun arayüz tarafındaki yöneticisi: durum (kapalı / açılıyor / açık / hata) ve gelen isteklerin Qt sinyallerine çevrilmesi.
Sunucu isteği başka bir iş parçacığında alır; sinyaller arayüz iş parçacığına (kuyruklanarak) teslim edilir.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from core import handoff, incoming, prefetch, webvideo, ytauth
from core.server import DEFAULT_PORT, LocalServer, bundled_extension_version
from utils.logger import get_logger

log = get_logger(__name__)


class ServerController(QObject):
    state_changed = Signal(str, str)      # durum (off | starting | running | error), açıklama
    download_captured = Signal(object)    # eklentiden gelen indirme sözlüğü
    youtube_captured = Signal(str, bool, bool)  # YouTube adresi, oynatma listesi mi, doğrudan Listeler'e mi
    extension_seen = Signal(str)          # eklenti kendini tanıttı (sürüm)
    upload_captured = Signal(object)      # eklentinin blob:/data: dosyasını parça parça verdiği (bitmiş) yükleme
    webvideo_captured = Signal(object)    # eklentinin sitede bulduğu videolar (liste)

    links_captured = Signal(object)       # eklentinin sayfadan topladığı dosya bağlantıları
    handoff_taken = Signal(str)           # eklenti, engellenen indirmeyi tarayıcıya devraldı (bilet)

    MIN_STARTING_MS = 700                 # "açılıyor" animasyonu en az bu kadar görünsün

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self._config = config
        self._server: LocalServer | None = None
        self._state = "off"
        self._message = "Sunucu kapalı"

    @property
    def state(self) -> str:
        return self._state

    @property
    def message(self) -> str:
        return self._message

    def port(self) -> int:
        try:
            return int(self._config.get("server_port"))
        except (KeyError, TypeError, ValueError):
            return DEFAULT_PORT

    def start(self) -> None:
        if self._state in ("starting", "running"):
            return
        self._set_state("starting", "Sunucu açılıyor…")
        QTimer.singleShot(self.MIN_STARTING_MS, self._finish_start)

    def _finish_start(self) -> None:
        if self._state != "starting":
            return                        # bu arada kapatıldı
        port = self.port()
        incoming.INCOMING.cleanup()       # yarım kalmış eski geçici dosyaları sil
        webvideo.cleanup_cookie_files()   # bir haftadan eski site çerez dosyalarını sil
        server = LocalServer(port, self._on_request, on_client=self.extension_seen.emit)
        server.extension_version = bundled_extension_version()
        try:
            server.start()
        except OSError as e:
            log.error("Sunucu açılamadı (port %d): %s", port, e)
            self._set_state("error", f"Sunucu açılamadı: {port} numaralı port kullanımda olabilir")
            return
        self._server = server
        self._set_state("running", f"Sunucu açık  ·  127.0.0.1:{port}")

    def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.stop()
        if self._state != "off":
            self._set_state("off", "Sunucu kapalı")

    def toggle(self) -> None:
        if self._state in ("running", "starting"):
            self.stop()
        else:
            self.start()

    def _set_state(self, state: str, message: str) -> None:
        self._state, self._message = state, message
        self.state_changed.emit(state, message)

    def _on_request(self, kind: str, payload: dict):
        """Sunucunun iş parçacığından çağrılır: sinyal yayınlar ya da (önceden analiz) arka plan işini yönetir."""
        if kind in ("youtube", "prefetch") and payload.get("jar") and self._config.get("youtube_use_cookies"):
            ytauth.save_jar(payload["jar"])      # tarayıcıdaki YouTube oturumu: üyelere özel / özel / yaş sınırlı videolar
        if kind == "download":
            payload["ticket"] = handoff.HANDOFF.new_ticket()
            self.download_captured.emit(payload)
            return {"ticket": payload["ticket"]}
        elif kind == "youtube":
            self.youtube_captured.emit(payload["url"], bool(payload.get("playlist")), bool(payload.get("listed")))
        elif kind == "prefetch":
            started = bool(self._config.get("youtube_prefetch")) and prefetch.PREFETCH.start(payload["url"])
            return {"started": bool(started)}
        elif kind == "prefetch_cancel":
            prefetch.PREFETCH.cancel(payload.get("id", ""))
        elif kind == "upload":
            self.upload_captured.emit(payload)
        elif kind == "webvideo":
            self.webvideo_captured.emit(payload)
        elif kind == "links":
            self.links_captured.emit(payload)
        elif kind == "handoff_taken":
            self.handoff_taken.emit(payload["ticket"])
        return None
