"""Küçük hata penceresi: uzun hata metnini kaydırmalı olarak tam gösterir (Windows Defender için ek açıklama ve düğme)."""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

from gui.components.download_item import ElidedLabel


class ErrorDialog(QDialog):
    def __init__(self, name: str, text: str, defender: bool = False, parent=None):
        super().__init__(parent)
        self.setWindowTitle("İndirme hatası")
        self.resize(480, 260)
        self.setMinimumSize(360, 200)

        title = QLabel("İndirme hatası")
        title.setObjectName("appTitle")
        subject = ElidedLabel(Qt.ElideMiddle)
        subject.setObjectName("hint")
        subject.set_full_text(name or "")
        box = QPlainTextEdit(text)
        box.setReadOnly(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 16)
        root.setSpacing(10)
        root.addWidget(title)
        root.addWidget(subject)
        root.addWidget(box, 1)

        buttons = QHBoxLayout()
        if defender:
            hint = QLabel("Windows Güvenlik > Virüs ve tehdit koruması > Koruma geçmişi bölümünden dosyaya izin "
                          "verebilirsin. IDA, Defender'a kendiliğinden istisna eklemez.")
            hint.setObjectName("hint")
            hint.setWordWrap(True)
            root.addWidget(hint)
            security = QPushButton("Windows Güvenlik'i aç")
            security.setObjectName("ghost")
            security.setCursor(Qt.PointingHandCursor)
            security.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("windowsdefender://threat/")))
            buttons.addWidget(security)
        copy = QPushButton("Kopyala")
        copy.setObjectName("ghost")
        copy.setCursor(Qt.PointingHandCursor)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(text))
        close = QPushButton("Kapat")
        close.setObjectName("ghost")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.accept)
        buttons.addStretch(1)
        buttons.addWidget(copy)
        buttons.addWidget(close)
        root.addLayout(buttons)
