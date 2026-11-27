"""Eksik araçların (yt-dlp, ffmpeg, Deno) ilk açılışta indirildiği pencere (çevrimiçi kurulum paketi için)."""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout

from core import toolsetup
from utils.helpers import format_size


class ToolsDialog(QDialog):
    _progress = Signal(str, int, int)
    _finished = Signal(object)

    def __init__(self, names: list, parent=None):
        super().__init__(parent)
        self._names = list(names)
        self._cancel = threading.Event()
        self._running = False
        self.setWindowTitle("Gerekli araçlar indiriliyor")
        self.setMinimumWidth(520)

        title = QLabel("Gerekli araçlar indiriliyor")
        title.setObjectName("appTitle")
        hint = QLabel("İndirme yöneticisinin çalışması için şu araçlar gerekli: " + ", ".join(self._names)
                      + ". Bir kez indirilir (yaklaşık 150 MB), sonra güncel tutulur.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.status = QLabel("Başlıyor…")
        self.status.setObjectName("info")
        self.status.setWordWrap(True)
        self.retry_btn = QPushButton("Tekrar dene")
        self.retry_btn.setObjectName("primary")
        self.retry_btn.hide()
        self.close_btn = QPushButton("Vazgeç")
        self.close_btn.setObjectName("ghost")
        for b in (self.retry_btn, self.close_btn):
            b.setCursor(Qt.PointingHandCursor)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.close_btn)
        buttons.addWidget(self.retry_btn)

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 20)
        root.setSpacing(12)
        for w in (title, hint, self.bar, self.status):
            root.addWidget(w)
        root.addLayout(buttons)

        self._progress.connect(self._on_progress)
        self._finished.connect(self._on_finished)
        self.retry_btn.clicked.connect(self._start)
        self.close_btn.clicked.connect(self._close_clicked)
        QTimer.singleShot(0, self._start)

    def _start(self) -> None:
        if self._running:
            return
        self._running = True
        self._cancel.clear()
        self.retry_btn.hide()
        self.close_btn.setText("Vazgeç")

        def work() -> None:
            errors = toolsetup.install(self._names, lambda label, d, t: self._progress.emit(label, d, t), self._cancel)
            try:
                self._finished.emit(errors)
            except RuntimeError:
                pass

        threading.Thread(target=work, daemon=True, name="tools-setup").start()

    def _on_progress(self, label: str, done: int, total: int) -> None:
        if total:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(done * 1000 / total))
            self.status.setText(f"{label} indiriliyor… {format_size(done)} / {format_size(total)}")
        else:
            self.bar.setRange(0, 0)
            self.status.setText(f"{label} indiriliyor… {format_size(done)}")

    def _on_finished(self, errors) -> None:
        self._running = False
        remaining = toolsetup.missing()
        if self._cancel.is_set():
            self.reject()
        elif not errors and not remaining:
            self.accept()
        else:
            self.bar.setRange(0, 1000)
            self.status.setText("\n".join(errors) or "Bazı araçlar hâlâ eksik.")
            self.retry_btn.show()
            self.close_btn.setText("Kapat")

    def _close_clicked(self) -> None:
        self._cancel.set()
        if not self._running:
            self.reject()

    def reject(self) -> None:
        self._cancel.set()
        super().reject()
