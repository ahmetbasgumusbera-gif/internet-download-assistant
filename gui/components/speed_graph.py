"""Canlı hız grafiği: son ~45 saniyenin toplam indirme hızını yumuşak bir çizgi ve dolguyla gösterir."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from gui import theme
from utils.helpers import format_speed


class SpeedGraph(QWidget):
    SAMPLES = 90                       # 0,5 sn aralıkla ~45 sn

    def __init__(self, parent=None):
        super().__init__(parent)
        self._samples = [0.0] * self.SAMPLES
        self.setFixedSize(150, 34)
        self.setToolTip("Canlı indirme hızı (son ~45 sn)")

    def push(self, bytes_per_sec: float) -> None:
        self._samples.append(max(0.0, float(bytes_per_sec or 0)))
        del self._samples[0]
        peak = max(self._samples)
        self.setToolTip(f"Canlı indirme hızı (son ~45 sn)  ·  en yüksek {format_speed(peak)}" if peak > 0
                        else "Canlı indirme hızı (son ~45 sn)")
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        area = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.SURFACE_2))
        p.drawRoundedRect(area, 9, 9)

        plot = area.adjusted(4, 4, -4, -4)
        scale = max(max(self._samples), 256 * 1024.0)       # boşta gürültü çizmesin
        n = len(self._samples)
        points = [QPointF(plot.left() + plot.width() * i / (n - 1),
                          plot.bottom() - plot.height() * min(1.0, s / scale)) for i, s in enumerate(self._samples)]

        line = QPainterPath(points[0])
        for a, b in zip(points, points[1:]):               # ardışık noktalar arası yumuşak geçiş
            mid = (a.x() + b.x()) / 2
            line.cubicTo(QPointF(mid, a.y()), QPointF(mid, b.y()), b)

        fill = QPainterPath(line)
        fill.lineTo(QPointF(plot.right(), plot.bottom()))
        fill.lineTo(QPointF(plot.left(), plot.bottom()))
        fill.closeSubpath()
        shade = QLinearGradient(0, plot.top(), 0, plot.bottom())
        top = QColor(theme.CYAN)
        top.setAlpha(95)
        bottom = QColor(theme.PURPLE)
        bottom.setAlpha(8)
        shade.setColorAt(0, top)
        shade.setColorAt(1, bottom)
        p.fillPath(fill, shade)

        stroke = QLinearGradient(plot.left(), 0, plot.right(), 0)
        stroke.setColorAt(0, QColor(theme.PURPLE))
        stroke.setColorAt(1, QColor(theme.CYAN))
        pen = QPen(stroke, 1.8)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(line)
        if self._samples[-1] > 0:                          # güncel hızın ucunda parlayan nokta
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.CYAN))
            p.drawEllipse(points[-1], 2.6, 2.6)
