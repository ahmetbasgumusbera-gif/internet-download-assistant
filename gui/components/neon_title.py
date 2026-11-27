"""Neon başlık: renk geçişli, hafif parlayan yazı. Kodla çizilir (dış dosya yok)."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from core.i18n import tr
from gui import theme


class NeonTitle(QWidget):
    PAD = 5   # parlama için yazının çevresinde bırakılan boşluk

    def __init__(self, text: str, pixel_size: int = 25, parent=None):
        super().__init__(parent)
        self._src = text
        self._text = text
        self._font = QFont()
        self._font.setFamilies(["Segoe UI Variable Display", "Segoe UI", "Inter", "Roboto"])
        self._font.setPixelSize(pixel_size)
        self._font.setWeight(QFont.Weight.ExtraBold)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

    def retranslate(self) -> None:
        self._text = tr(self._src)
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self._font)
        return QSize(fm.horizontalAdvance(self._text) + 2 * self.PAD, fm.height() + 2 * self.PAD)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fm = QFontMetrics(self._font)
        path = QPainterPath()
        path.addText(self.PAD, self.PAD + fm.ascent(), self._font, self._text)

        # dıştan içe: geniş-soluk -> dar-parlak halkalar (neon parıltısı)
        for width, alpha in ((7, 14), (4.5, 26), (2.5, 44)):
            pen = QPen(QColor(34, 211, 238, alpha), width)
            pen.setJoinStyle(Qt.RoundJoin)
            p.strokePath(path, pen)

        gradient = QLinearGradient(0, 0, max(1, self.width()), 0)
        gradient.setColorAt(0.0, QColor(theme.PURPLE_SOFT))
        gradient.setColorAt(1.0, QColor(theme.CYAN))
        p.fillPath(path, QBrush(gradient))
