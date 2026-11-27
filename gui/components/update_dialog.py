"""Yeni güncelleme penceresi: sürüm notları, Şimdi güncelle / Daha sonra güncelle / İptal ve indirme çubuğu."""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout

from core import appupdate
from utils.helpers import format_size


class UpdateDialog(QDialog):
    """action: install (Setup indirildi, path hazır) | page (kurulum desteklenmiyor: sürüm sayfası) | later | skip."""
    _progress = Signal(int, int)
    _finished = Signal(str, str)

    def __init__(self, info: dict, current: str, busy: int = 0, can_install: bool = True, parent=None):
        super().__init__(parent)
        self._info = info
        self._can_install = can_install
        self._cancel = threading.Event()
        self._downloading = False
        self.action = ""
        self.path = ""
        self.setWindowTitle("Yeni güncelleme mevcut")
        self.setMinimumWidth(560)

        title = QLabel("Yeni güncelleme mevcut")
        title.setObjectName("appTitle")
        sub = QLabel(f"Sürüm {info['version']} yayınlandı (kurulu: {current})")
        sub.setObjectName("hint")
        self.notes = QPlainTextEdit(info.get("notes") or "Bu sürüm için not yazılmamış.")
        self.notes.setReadOnly(True)
        self.notes.setMaximumHeight(170)

        self.note = QLabel("")
        self.note.setObjectName("bannerWarn")
        self.note.setWordWrap(True)
        if not can_install:
            self.note.setText("Bu bir geliştirme kopyası: Setup kurulmaz. \"Şimdi güncelle\" sürüm sayfasını açar.")
        elif busy:
            self.note.setText(f"{busy} indirme sürüyor: güncelleme sırasında duraklatılır, uygulama açılınca "
                              "(\"Açılışta sürdür\" ayarı açıksa) kaldığı yerden devam eder.")
        self.note.setVisible(bool(self.note.text()))

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.hide()
        self.status = QLabel("")
        self.status.setObjectName("info")
        self.status.setWordWrap(True)

        self.now_btn = QPushButton("  Şimdi güncelle")
        self.now_btn.setObjectName("primary")
        self.later_btn = QPushButton("Daha sonra güncelle")
        self.later_btn.setObjectName("ghost")
        self.cancel_btn = QPushButton("İptal")
        self.cancel_btn.setObjectName("ghost")
        self.cancel_btn.setToolTip("Bu sürümü atla: yeni bir sürüm çıkana kadar sormaz")
        for b in (self.now_btn, self.later_btn, self.cancel_btn):
            b.setCursor(Qt.PointingHandCursor)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.later_btn)
        buttons.addWidget(self.now_btn)

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 20)
        root.setSpacing(12)
        for w in (title, sub, self.notes, self.note, self.bar, self.status):
            root.addWidget(w)
        root.addLayout(buttons)

        self.now_btn.clicked.connect(self._now)
        self.later_btn.clicked.connect(self._later)
        self.cancel_btn.clicked.connect(self._cancel_clicked)
        self._progress.connect(self._on_progress)
        self._finished.connect(self._on_finished)

    # ------------------------------------------------------------------ düğmeler
    def _later(self) -> None:
        self.action = "later"
        self.reject()

    def _cancel_clicked(self) -> None:
        if self._downloading:                     # indirme sırasında: indirmeyi durdur, pencere açık kalır
            self._cancel.set()
            return
        self.action = "skip"
        self.reject()

    def _now(self) -> None:
        if not self._can_install:
            self.action = "page"
            self.accept()
            return
        self._downloading = True
        self._cancel.clear()
        self.now_btn.setEnabled(False)
        self.later_btn.setEnabled(False)
        self.cancel_btn.setText("Vazgeç")
        self.bar.setValue(0)
        self.bar.show()
        self.status.setText("İndiriliyor…")

        def work() -> None:
            try:
                path = appupdate.download(self._info, progress=lambda d, t: self._progress.emit(d, t),
                                          cancel=self._cancel)
                result = (str(path), "")
            except InterruptedError:
                result = ("", "__cancel__")
            except appupdate.UpdateError as e:
                result = ("", str(e))
            except Exception as e:
                result = ("", f"Güncelleme indirilemedi: {e}")
            try:
                self._finished.emit(*result)
            except RuntimeError:
                pass                              # pencere kapanmış

        threading.Thread(target=work, daemon=True, name="app-update-download").start()

    # ------------------------------------------------------------------ sonuçlar
    def _on_progress(self, done: int, total: int) -> None:
        if total:
            self.bar.setValue(int(done * 1000 / total))
            self.status.setText(f"İndiriliyor… {format_size(done)} / {format_size(total)}")
        else:
            self.bar.setRange(0, 0)

    def _reset(self) -> None:
        self._downloading = False
        self.bar.setRange(0, 1000)
        self.bar.hide()
        self.now_btn.setEnabled(True)
        self.later_btn.setEnabled(True)
        self.cancel_btn.setText("İptal")

    def _on_finished(self, path: str, error: str) -> None:
        if error == "__cancel__":
            self._reset()
            self.status.setText("İndirme durduruldu.")
            return
        if error:
            self._reset()
            self.status.setText(error)
            return
        self.path = path
        self.action = "install"
        self.status.setText("İndirildi, doğrulandı. Uygulama kapanıp güncellenecek…")
        self.accept()

    def reject(self) -> None:
        self._cancel.set()
        super().reject()
