"""Tanı aracı penceresi: testleri başlatır, canlı ilerlemeyi gösterir ve raporu seçilen klasöre yazar."""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from core import diagnostics
from gui.explorer import reveal_in_explorer


class DiagnosticsDialog(QDialog):
    _line = Signal(str)
    _done = Signal(str, str)          # rapor yolu, hata

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self._config = config
        self._cancel = threading.Event()
        self._running = False
        self._report_path = ""
        self._report_text = ""
        self.setWindowTitle("Tanı aracı")
        self.resize(720, 640)

        title = QLabel("Tanı aracı")
        title.setObjectName("appTitle")
        hint = QLabel("Sistemi, araçları ve ağı denetler; çeşitli indirmeleri (zip, exe, hız/bağlantı karşılaştırması, "
                      "YouTube video ve altyazı) gerçek motorlarla dener ve sonucu bir rapora yazar. Birkaç dakika sürer ve "
                      "yaklaşık 150 MB veri kullanır. Test dosyaları geçici klasöre iner ve silinir.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)

        self.links = QPlainTextEdit()
        self.links.setPlaceholderText("İsteğe bağlı: kendi test bağlantıların (Drive, rar, exe, zip...) - her satıra bir tane. "
                                      "Her biri için en fazla 30 MB denenir.")
        self.links.setMaximumHeight(80)

        self.dir_edit = QLineEdit(str(config.get("diag_dir") or ""))
        self.dir_edit.setPlaceholderText("Boşsa: kurulum klasörü\\tani")
        browse = QPushButton(" Seç…")
        browse.setObjectName("ghost")
        browse.setCursor(Qt.PointingHandCursor)
        browse.clicked.connect(self._browse)
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Rapor klasörü"))
        folder_row.addWidget(self.dir_edit, 1)
        folder_row.addWidget(browse)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)

        self.start_btn = QPushButton("  Testi başlat")
        self.start_btn.setObjectName("primary")
        self.stop_btn = QPushButton("Durdur")
        self.stop_btn.setObjectName("ghost")
        self.stop_btn.setEnabled(False)
        self.show_btn = QPushButton("Raporu klasörde göster")
        self.show_btn.setObjectName("ghost")
        self.show_btn.setEnabled(False)
        self.copy_btn = QPushButton("Raporu kopyala")
        self.copy_btn.setObjectName("ghost")
        self.copy_btn.setEnabled(False)
        close = QPushButton("Kapat")
        close.setObjectName("ghost")
        for b in (self.start_btn, self.stop_btn, self.show_btn, self.copy_btn, close):
            b.setCursor(Qt.PointingHandCursor)
        buttons = QHBoxLayout()
        buttons.addWidget(self.show_btn)
        buttons.addWidget(self.copy_btn)
        buttons.addStretch(1)
        buttons.addWidget(close)
        buttons.addWidget(self.stop_btn)
        buttons.addWidget(self.start_btn)

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 20)
        root.setSpacing(10)
        root.addWidget(title)
        root.addWidget(hint)
        root.addWidget(self.links)
        root.addLayout(folder_row)
        root.addWidget(self.log, 1)
        root.addLayout(buttons)

        self.start_btn.clicked.connect(self._start)
        self.stop_btn.clicked.connect(self._cancel.set)
        self.show_btn.clicked.connect(self._show)
        self.copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(self._report_text))
        close.clicked.connect(self.close)
        self._line.connect(self.log.appendPlainText)
        self._done.connect(self._on_done)

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Rapor klasörünü seç", self.dir_edit.text().strip())
        if chosen:
            self.dir_edit.setText(str(Path(chosen)))

    def _start(self) -> None:
        if self._running:
            return
        self._config.set("diag_dir", self.dir_edit.text().strip())
        extra = self.links.toPlainText().splitlines()
        self._cancel.clear()
        self._running = True
        self.log.clear()
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.show_btn.setEnabled(False)
        self.copy_btn.setEnabled(False)

        def say(text: str) -> None:
            try:
                self._line.emit(text)
            except RuntimeError:
                pass

        def work() -> None:
            path, error = "", ""
            try:
                text = diagnostics.run_all(self._config, say, self._cancel, extra)
                self._report_text = text
                path = str(diagnostics.save_report(text, self._config))
            except Exception as e:
                error = str(e)
            try:
                self._done.emit(path, error)
            except RuntimeError:
                pass

        threading.Thread(target=work, daemon=True, name="diagnostics").start()

    def _on_done(self, path: str, error: str) -> None:
        self._running = False
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if error:
            self.log.appendPlainText(f"Tanı başarısız: {error}")
            return
        self._report_path = path
        self.log.appendPlainText("")
        self.log.appendPlainText(self._report_text)
        self.log.appendPlainText(f"Rapor kaydedildi: {path}")
        self.show_btn.setEnabled(True)
        self.copy_btn.setEnabled(True)

    def _show(self) -> None:
        if self._report_path and not reveal_in_explorer(Path(self._report_path)):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self._report_path).parent)))

    def closeEvent(self, event) -> None:
        self._cancel.set()
        super().closeEvent(event)
