"""İnternet izleyici: arka planda bağlantıyı yoklar, durum değişince (iki ardışık başarısızlıktan sonra) changed(bool) yayar."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal

from core import netwatch


class NetMonitor(QObject):
    changed = Signal(bool)

    def __init__(self, parent=None, interval: float = 6.0):
        super().__init__(parent)
        self._interval = interval
        self._stop = threading.Event()
        self._thread = None
        self._online = True

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="net-monitor")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        fails = 0
        while not self._stop.wait(self._interval):
            if netwatch.is_online():
                fails = 0
                if not self._online:
                    self._online = True
                    self._emit(True)
            else:
                fails += 1
                if fails >= 2 and self._online:
                    self._online = False
                    self._emit(False)

    def _emit(self, online: bool) -> None:
        try:
            self.changed.emit(online)
        except RuntimeError:
            pass
