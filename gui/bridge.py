"""
Çekirdek (Qt'siz, çok thread'li) ile arayüz arasındaki köprü.

DownloadManager olayları işçi thread'lerinden gelir. Burada Qt sinyaline çevrilir;
Qt, sinyal başka bir thread'den yayınlanınca alıcıya ana thread'de (kuyruğa alarak) teslim eder.
Böylece arayüz koduna hiçbir yerde kilit veya thread düşünmek gerekmez.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from core.manager import DownloadManager, ManagerListener


class ManagerBridge(QObject):
    task_added = Signal(object)     # DownloadTask (kopya)
    task_updated = Signal(object)   # DownloadTask (kopya)
    task_removed = Signal(str)      # görev id'si

    def __init__(self, manager: DownloadManager, parent=None):
        super().__init__(parent)
        manager.add_listener(ManagerListener(
            on_task_added=self.task_added.emit,
            on_task_updated=self.task_updated.emit,
            on_task_removed=self.task_removed.emit,
        ))