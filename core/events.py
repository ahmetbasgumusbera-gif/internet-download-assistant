"""
Motor ile dış dünya (yönetici, arayüz) arasındaki geri çağırım sözleşmesi.

ÖNEMLİ: Geri çağırımlar motorun işçi thread'lerinden çağrılır.
Arayüz katmanı bunları Qt sinyaline çevirerek ana thread'e taşıyacak.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

from core.models import DownloadTask


def _noop(task: DownloadTask) -> None:
    pass


@dataclass
class EngineCallbacks:
    on_status: Callable[[DownloadTask], None] = _noop    # durum değişti
    on_progress: Callable[[DownloadTask], None] = _noop  # ilerleme güncellendi


class ProgressThrottle:
    """İlerleme bildirimlerini saniyede en fazla 1/min_interval kez geçirir."""

    def __init__(self, min_interval: float = 0.25):
        self._min = min_interval
        self._last = float("-inf")
        self._lock = threading.Lock()

    def ready(self) -> bool:
        with self._lock:
            now = time.monotonic()
            if now - self._last >= self._min:
                self._last = now
                return True
            return False