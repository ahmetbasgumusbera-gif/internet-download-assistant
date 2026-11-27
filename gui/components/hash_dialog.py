"""Dosya doğrulama penceresi: dosyanın karma değerini hesaplar ve yapıştırılan değerle karşılaştırır (hiçbir şeyi engellemez/silmez)."""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton, QVBoxLayout,
)

from core import hashcheck
from gui import theme

_COMBO_QSS = f"""
QComboBox {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; border-radius: 10px; padding: 8px 12px;
             color: {theme.TEXT}; }}
QComboBox QAbstractItemView {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER};
             selection-background-color: {theme.SURFACE_2}; color: {theme.TEXT}; outline: none; }}
"""


class HashDialog(QDialog):
    _progress = Signal(int)
    _finished = Signal(str, str)      # (hesaplanan değer, hata)

    def __init__(self, files: list, parent=None):
        super().__init__(parent)
        self._cancel = threading.Event()
        self._expected = ""
        self._algo = ""
        self.setWindowTitle("Dosya doğrula")
        self.setMinimumWidth(620)

        title = QLabel("Dosya doğrula")
        title.setObjectName("appTitle")
        hint = QLabel("İndirme sayfasında verilen MD5 / SHA-1 / SHA-256 / SHA-512 değerini yapıştır. "
                      "Bu yalnızca bir kontroldür: hiçbir dosya silinmez ya da engellenmez.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)

        self.file_combo = QComboBox()
        self.file_combo.setStyleSheet(_COMBO_QSS)
        for label, path in files:
            self.file_combo.addItem(label, str(path))
        browse = QPushButton("Dosya seç…")
        browse.setObjectName("ghost")
        browse.setCursor(Qt.PointingHandCursor)
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.file_combo, 1)
        row.addWidget(browse)

        self.hash_edit = QLineEdit()
        self.hash_edit.setPlaceholderText("Beklenen karma değeri (algoritma uzunluktan anlaşılır)")
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.result = QLabel("")
        self.result.setWordWrap(True)

        self.run_btn = QPushButton("  Doğrula")
        self.run_btn.setObjectName("primary")
        self.run_btn.setCursor(Qt.PointingHandCursor)
        close = QPushButton("Kapat")
        close.setObjectName("ghost")
        close.setCursor(Qt.PointingHandCursor)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(close)
        buttons.addWidget(self.run_btn)

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 20)
        root.setSpacing(12)
        for widget in (title, hint):
            root.addWidget(widget)
        root.addLayout(row)
        root.addWidget(self.hash_edit)
        root.addWidget(self.progress)
        root.addWidget(self.result)
        root.addLayout(buttons)

        self.run_btn.clicked.connect(self._start)
        close.clicked.connect(self.close)
        self.hash_edit.returnPressed.connect(self._start)
        self._progress.connect(self.progress.setValue)
        self._finished.connect(self._on_finished)

    def _say(self, text: str, color: str) -> None:
        self.result.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.result.setText(text)

    def _browse(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(self, "Doğrulanacak dosyayı seç")
        if chosen:
            self.file_combo.addItem(Path(chosen).name, chosen)
            self.file_combo.setCurrentIndex(self.file_combo.count() - 1)

    def _start(self) -> None:
        path = self.file_combo.currentData()
        if not path or not Path(path).is_file():
            self._say("Önce bir dosya seç.", theme.AMBER)
            return
        self._algo, self._expected = hashcheck.parse_expected(self.hash_edit.text())
        if not self._expected:
            self._say("Geçerli bir karma değeri bulunamadı (32, 40, 64 ya da 128 karakterlik onaltılık değer).", theme.AMBER)
            return
        self._cancel.clear()
        self.run_btn.setEnabled(False)
        self.progress.setValue(0)
        self._say(f"{self._algo.upper()} hesaplanıyor…", theme.TEXT_MUTED)

        def work() -> None:
            try:
                value = hashcheck.file_hash(
                    Path(path), self._algo,
                    progress=lambda done, size: self._progress.emit(int(done * 100 / size) if size else 100),
                    cancel=self._cancel)
                self._finished.emit(value, "")
            except InterruptedError:
                return
            except OSError as e:
                self._finished.emit("", str(e))

        threading.Thread(target=work, daemon=True, name="hash-check").start()

    def _on_finished(self, value: str, error: str) -> None:
        self.run_btn.setEnabled(True)
        if error:
            self._say(f"Dosya okunamadı: {error}", theme.RED)
        elif value == self._expected:
            self.progress.setValue(100)
            self._say(f"✓ Eşleşiyor ({self._algo.upper()}): dosya bozulmamış.", theme.GREEN)
        else:
            self._say(f"✗ Eşleşmiyor.\nBeklenen: {self._expected}\nHesaplanan: {value}", theme.RED)

    def closeEvent(self, event) -> None:
        self._cancel.set()
        super().closeEvent(event)
