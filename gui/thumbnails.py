"""
Küçük resim yükleyici: adresten resmi arka planda indirir, bellekte önbelleğe alır ve hazır olunca
ana thread'de geri çağırım yapar. Arayüzü hiçbir zaman bekletmez; ağ hatasında sessizce vazgeçer.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

import requests
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QImage, QPixmap

_MAX_CACHE = 120


class ThumbnailLoader(QObject):
    _loaded = Signal(str, object)          # adres, QImage ya da None
    _instance: Optional["ThumbnailLoader"] = None

    @classmethod
    def instance(cls) -> "ThumbnailLoader":
        """Ana (arayüz) thread'inde ilk çağrıda oluşturulur."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        super().__init__()
        self._cache: dict[str, Optional[QPixmap]] = {}
        self._pending: dict[str, list[Callable[[Optional[QPixmap]], None]]] = {}
        self._loaded.connect(self._on_loaded)

    def request(self, url: str, callback: Callable[[Optional[QPixmap]], None]) -> None:
        """Resim hazırsa hemen, değilse indirilince callback(pixmap ya da None) çağrılır."""
        if not url:
            return
        if url in self._cache:
            callback(self._cache[url])
            return
        waiting = self._pending.setdefault(url, [])
        waiting.append(callback)
        if len(waiting) == 1:
            threading.Thread(target=self._fetch, args=(url,), daemon=True, name="thumb").start()

    def _fetch(self, url: str) -> None:
        image = None
        try:
            if url.startswith("file:"):
                from pathlib import Path as _Path
                from urllib.parse import unquote as _unq, urlparse as _urlp
                from urllib.request import url2pathname as _u2p
                data = _Path(_u2p(_unq(_urlp(url).path))).read_bytes()
            else:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.content
            candidate = QImage.fromData(data)
            image = candidate if not candidate.isNull() else None
        except Exception:
            image = None
        try:
            self._loaded.emit(url, image)
        except RuntimeError:
            pass   # uygulama kapanırken nesne yok edilmiş olabilir

    def _on_loaded(self, url: str, image) -> None:
        pixmap = QPixmap.fromImage(image) if image is not None else None
        if len(self._cache) >= _MAX_CACHE:
            self._cache.pop(next(iter(self._cache)))
        self._cache[url] = pixmap
        for callback in self._pending.pop(url, []):
            try:
                callback(pixmap)
            except RuntimeError:
                pass   # kart/sayfa bu arada silinmiş


def cover_crop(pixmap: QPixmap, width: int, height: int, radius: int = 10) -> QPixmap:
    """Resmi alanı dolduracak şekilde ölçekleyip ortadan kırpar ve köşelerini yuvarlar."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QPainter, QPainterPath

    ratio = 2
    scaled = pixmap.scaled(width * ratio, height * ratio, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    out = QPixmap(width * ratio, height * ratio)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, width * ratio, height * ratio), radius * ratio, radius * ratio)
    painter.setClipPath(path)
    painter.drawPixmap((width * ratio - scaled.width()) // 2, (height * ratio - scaled.height()) // 2, scaled)
    painter.end()
    out.setDevicePixelRatio(ratio)
    return out