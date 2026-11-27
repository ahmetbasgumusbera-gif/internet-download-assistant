"""
Tarayıcının adresi olmayan (blob: / data:) indirmelerini parça parça aldığımız geçici klasör (Qt yok).
Eklenti dosyayı sayfanın içinden okuyup /api/upload ile parça parça yollar; son parça gelince dosya <kimlik>.bin olur.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Optional

from utils import paths
from utils.logger import get_logger

log = get_logger(__name__)

MAX_CHUNK = 8 * 1024 * 1024        # tek parça en fazla bu kadar
MAX_TOTAL = 8 * 1024 ** 3          # tek dosya en fazla bu kadar
STALE_SECONDS = 7 * 24 * 3600          # yarım kalmış / sahipsiz dosyalar bu süreden sonra silinir


class Incoming:
    def __init__(self, directory: Optional[Path] = None):
        self._dir = Path(directory) if directory else paths.DATA_DIR / "incoming"
        self._lock = threading.Lock()
        self._next: dict[str, int] = {}      # kimlik -> beklenen sıradaki parça numarası

    @property
    def directory(self) -> Path:
        return self._dir

    def _part(self, uid: str) -> Path:
        return self._dir / f"{uid}.part"

    def append(self, uid: str, index: int, data: bytes) -> None:
        """index sırayla gelmelidir (0, 1, 2...); değilse ValueError."""
        with self._lock:
            if index != self._next.get(uid, 0):
                raise ValueError("parça sırası")
            self._dir.mkdir(parents=True, exist_ok=True)
            part = self._part(uid)
            if index and part.exists() and part.stat().st_size + len(data) > MAX_TOTAL:
                raise ValueError("dosya çok büyük")
            with open(part, "ab" if index else "wb") as f:
                f.write(data)
            self._next[uid] = index + 1

    def finish(self, uid: str) -> Path:
        """Yüklemeyi tamamlar ve bitmiş dosyanın yolunu verir."""
        with self._lock:
            if uid not in self._next:
                raise ValueError("bilinmeyen yükleme")
            part = self._part(uid)
            final = part.with_suffix(".bin")
            os.replace(part, final)
            self._next.pop(uid, None)
            return final

    def abort(self, uid: str) -> None:
        with self._lock:
            self._next.pop(uid, None)
        for suffix in (".part", ".bin"):
            try:
                (self._dir / f"{uid}{suffix}").unlink()
            except OSError:
                pass

    def cleanup(self) -> None:
        """Eski (sahipsiz) geçici dosyaları siler."""
        try:
            now = time.time()
            for f in self._dir.glob("*"):
                if f.is_file() and now - f.stat().st_mtime > STALE_SECONDS:
                    f.unlink()
        except OSError:
            pass


INCOMING = Incoming()
