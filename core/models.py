"""
Çekirdeğin ortak veri modelleri. Qt bağımlılığı yoktur.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class TaskStatus(str, Enum):
    QUEUED = "queued"            # sırada
    PROBING = "probing"          # sunucudan bilgi alınıyor
    DOWNLOADING = "downloading"  # iniyor
    PAUSED = "paused"            # duraklatıldı (devam edilebilir)
    MERGING = "merging"          # parçalar birleştiriliyor
    COMPLETED = "completed"      # tamamlandı
    ERROR = "error"              # hata (temp korunur, tekrar denenebilir)
    CANCELLED = "cancelled"      # iptal edildi (temp silindi)

    @property
    def is_finished(self) -> bool:
        return self in (TaskStatus.COMPLETED, TaskStatus.ERROR, TaskStatus.CANCELLED)


@dataclass
class DownloadTask:
    url: str
    save_dir: Path
    file_name: str = ""                       # boşsa sunucudan/URL'den belirlenir
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    headers: dict = field(default_factory=dict)  # ek istek başlıkları (Referer, Cookie vb.)

    # Motor tarafından doldurulan alanlar
    file_size: Optional[int] = None
    status: TaskStatus = TaskStatus.QUEUED
    error: str = ""
    downloaded: int = 0
    speed: float = 0.0                        # byte/sn
    final_path: Optional[Path] = None
    created_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None
    engine: str = ""                          # indirmeyi yapan motorun adı (örn. "http")
    title: str = ""                           # kullanıcıya gösterilecek başlık (örn. YouTube video başlığı)
    thumbnail_url: str = ""                   # kartta gösterilecek küçük resim
    options: dict = field(default_factory=dict)  # motora özel ayarlar (örn. yt-dlp argümanları)

    def __post_init__(self) -> None:
        self.save_dir = Path(self.save_dir)

    @property
    def percent(self) -> float:
        if not self.file_size:
            return 0.0
        return min(100.0, self.downloaded / self.file_size * 100)

    @property
    def eta(self) -> Optional[float]:
        """Kalan süre (saniye). Hesaplanamıyorsa None."""
        if not self.file_size or self.speed <= 0:
            return None
        return max(0.0, (self.file_size - self.downloaded) / self.speed)