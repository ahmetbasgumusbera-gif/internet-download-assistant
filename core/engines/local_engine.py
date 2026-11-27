"""
Yerel dosya "motoru": adresi olmayan (tarayıcının blob:/data: ile verdiği) ve uygulamaya parça parça aktarılmış
bir dosyayı, kullanıcının seçtiği klasöre taşıyıp listeye tamamlanmış indirme olarak ekler. Ağ kullanmaz.
options: source_path (geçici dosya), sort_by_category (yönetici ekler).
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Optional

from core.engines.base import BaseEngine
from core.engines.http_engine import DownloadError
from core.events import EngineCallbacks
from core.models import DownloadTask, TaskStatus
from utils.helpers import category_folder, reserve_unique_path, sanitize_filename
from utils.logger import get_logger

log = get_logger(__name__)


class LocalEngine(BaseEngine):
    name = "local"

    def __init__(self, task: DownloadTask, callbacks: Optional[EngineCallbacks] = None,
                 connections: int = 8, limiter=None):
        super().__init__(task, callbacks)

    @classmethod
    def can_handle(cls, url: str) -> bool:
        return False        # yalnızca motor adıyla ("local") seçilir

    def probe(self) -> None:
        pass

    def start(self) -> None:
        task = self.task
        try:
            src = Path(task.options.get("source_path") or "")
            if not src.is_file():
                raise DownloadError("Geçici dosya bulunamadı (silinmiş olabilir).")
            name = sanitize_filename(task.file_name or src.name)
            target_dir = task.save_dir
            if task.options.get("sort_by_category"):
                target_dir = category_folder(target_dir, name)
            target_dir.mkdir(parents=True, exist_ok=True)
            final = reserve_unique_path(target_dir / name)
            try:
                os.replace(src, final)
            except OSError:                      # başka sürücü: kopyala ve sil
                try:
                    shutil.copy2(src, final)
                    src.unlink()
                except OSError:
                    try:
                        final.unlink()
                    except OSError:
                        pass
                    raise
            size = final.stat().st_size
            task.file_name = final.name
            task.final_path = final
            task.file_size = size
            task.downloaded = size
            task.speed = 0.0
            self._notify_progress()
            self._set_status(TaskStatus.COMPLETED)
        except DownloadError as e:
            self._set_status(TaskStatus.ERROR, str(e))
        except OSError as e:
            log.error("Yerel dosya kaydedilemedi: %s", e)
            self._set_status(TaskStatus.ERROR, f"Dosya kaydedilemedi: {e}")

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def cancel(self) -> None:
        if self.task.status.is_finished:
            return
        self.discard_partial()
        self.task.downloaded = 0
        self._set_status(TaskStatus.CANCELLED)

    def discard_partial(self) -> None:
        try:
            Path(self.task.options.get("source_path") or "").unlink()
        except (OSError, ValueError):
            pass
