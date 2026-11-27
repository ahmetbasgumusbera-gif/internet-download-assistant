"""Fareyle toplu seçim için saydam mavi seçim kutusu (kendi çizimi: stil sayfasına bağlı değil)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class SelectionBand(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(34, 211, 238, 45))
        p.setPen(QPen(QColor(34, 211, 238, 200), 1))
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 4, 4)
