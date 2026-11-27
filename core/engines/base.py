"""
Tüm indirme motorlarının uyacağı ortak arayüz.
Yönetici (DownloadManager) hangi motor olduğunu bilmeden bu arayüzle çalışır.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from core.events import EngineCallbacks
from core.models import DownloadTask, TaskStatus
from utils.logger import get_logger

log = get_logger(__name__)


class BaseEngine(ABC):
    name = "base"

    def __init__(self, task: DownloadTask, callbacks: Optional[EngineCallbacks] = None):
        self.task = task
        self.callbacks = callbacks or EngineCallbacks()

    # ---------- motorun uyması gereken sözleşme ----------
    @classmethod
    @abstractmethod
    def can_handle(cls, url: str) -> bool:
        """Bu motor verilen URL'yi indirebilir mi?"""

    @abstractmethod
    def probe(self) -> None:
        """Dosya adı/boyutu gibi bilgileri öğrenip task'a yazar. Hata olursa istisna fırlatır."""

    @abstractmethod
    def start(self) -> None:
        """İndirmeyi çalıştırır. BLOKE EDİCİDİR: bitene kadar dönmez (worker thread'de çağrılır).
        Sonuç task.status içinde: COMPLETED, ERROR, CANCELLED veya PAUSED."""

    @abstractmethod
    def pause(self) -> None:
        """Geçici duraklatır (bağlantılar kapanır, ilerleme korunur)."""

    @abstractmethod
    def resume(self) -> None:
        """pause() ile duraklatılmış indirmeyi sürdürür."""

    @abstractmethod
    def cancel(self) -> None:
        """İptal eder ve geçici dosyaları siler."""

    @abstractmethod
    def stop(self) -> None:
        """Uygulama kapanırken: durdurur ama geçici dosyaları korur (sonra devam edilebilir)."""

    def discard_partial(self) -> None:
        """Çalışmayan bir görevin yarım kalmış geçici dosyalarını siler. Varsayılan: bir şey yapmaz."""

    # ---------- alt sınıfların kullandığı yardımcılar ----------
    def _set_status(self, status: TaskStatus, error: str = "") -> None:
        self.task.status = status
        self.task.error = error
        try:
            self.callbacks.on_status(self.task)
        except Exception:
            log.exception("on_status geri çağırımı hata verdi")

    def _notify_progress(self) -> None:
        try:
            self.callbacks.on_progress(self.task)
        except Exception:
            log.exception("on_progress geri çağırımı hata verdi")