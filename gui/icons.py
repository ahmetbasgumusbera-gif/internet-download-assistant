"""
Kodla çizilen vektör ikonlar. Dış dosya (png/svg) gerekmez, her ölçekte keskin görünür
ve renkleri temadan gelir. 24x24'lük bir ızgarada tanımlıdır.

Kullanım:  button.setIcon(get_icon("pause"))
"""
from __future__ import annotations

import math
from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
)

from gui import theme

_GRID = 24.0
_SCALE = 2  # yüksek DPI için pixmap çözünürlük çarpanı


def _pen(painter: QPainter, color: QColor, width: float = 2.0) -> None:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)


def _draw(name: str, p: QPainter, c: QColor) -> None:
    if name == "download":
        _pen(p, c)
        p.drawLine(QPointF(12, 4), QPointF(12, 15))
        p.drawPolyline(QPolygonF([QPointF(7, 10.5), QPointF(12, 15.5), QPointF(17, 10.5)]))
        p.drawLine(QPointF(5, 20), QPointF(19, 20))

    elif name == "play":
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        path = QPainterPath()
        path.moveTo(8, 5.5)
        path.lineTo(18.5, 12)
        path.lineTo(8, 18.5)
        path.closeSubpath()
        p.drawPath(path)

    elif name == "pause":
        p.setPen(Qt.NoPen)
        p.setBrush(c)
        p.drawRoundedRect(QRectF(6.5, 5, 4, 14), 1.4, 1.4)
        p.drawRoundedRect(QRectF(13.5, 5, 4, 14), 1.4, 1.4)

    elif name == "close":
        _pen(p, c, 2.2)
        p.drawLine(QPointF(6.5, 6.5), QPointF(17.5, 17.5))
        p.drawLine(QPointF(17.5, 6.5), QPointF(6.5, 17.5))

    elif name == "retry":
        _pen(p, c)
        rect = QRectF(5, 5, 14, 14)
        start, span = 50, 290
        path = QPainterPath()
        path.arcMoveTo(rect, start)
        path.arcTo(rect, start, span)
        p.drawPath(path)
        # ok ucu: yayın bittiği noktada, gidiş yönüne bakacak şekilde
        theta = math.radians(start + span)
        cx, cy, r = 12.0, 12.0, 7.0
        end = QPointF(cx + r * math.cos(theta), cy - r * math.sin(theta))
        heading = math.atan2(-math.cos(theta), -math.sin(theta))
        for delta in (math.radians(150), math.radians(-150)):
            a = heading + delta
            p.drawLine(end, QPointF(end.x() + 4.2 * math.cos(a), end.y() + 4.2 * math.sin(a)))

    elif name == "folder":
        _pen(p, c, 1.9)
        path = QPainterPath()
        path.moveTo(3, 7)
        path.quadTo(3, 5.5, 4.5, 5.5)
        path.lineTo(9, 5.5)
        path.lineTo(11.5, 8)
        path.lineTo(19.5, 8)
        path.quadTo(21, 8, 21, 9.5)
        path.lineTo(21, 17.5)
        path.quadTo(21, 19, 19.5, 19)
        path.lineTo(4.5, 19)
        path.quadTo(3, 19, 3, 17.5)
        path.closeSubpath()
        p.drawPath(path)

    elif name == "trash":
        _pen(p, c, 1.9)
        p.drawLine(QPointF(4.5, 7), QPointF(19.5, 7))
        p.drawPolyline(QPolygonF([QPointF(9, 7), QPointF(9, 4.5), QPointF(15, 4.5), QPointF(15, 7)]))
        p.drawPolyline(QPolygonF([QPointF(6.5, 7), QPointF(7.5, 19), QPointF(16.5, 19), QPointF(17.5, 7)]))
        p.drawLine(QPointF(10.2, 10.5), QPointF(10.2, 16))
        p.drawLine(QPointF(13.8, 10.5), QPointF(13.8, 16))

    elif name == "sliders":
        _pen(p, c, 1.9)
        for y, knob in ((7, 9), (12, 15.5), (17, 8)):
            p.drawLine(QPointF(4, y), QPointF(knob - 3.2, y))
            p.drawLine(QPointF(knob + 3.2, y), QPointF(20, y))
            p.drawEllipse(QPointF(knob, y), 2.3, 2.3)

    elif name == "plus":
        _pen(p, c, 2.2)
        p.drawLine(QPointF(12, 6), QPointF(12, 18))
        p.drawLine(QPointF(6, 12), QPointF(18, 12))

    elif name == "minus":
        _pen(p, c, 2.2)
        p.drawLine(QPointF(6, 12), QPointF(18, 12))

    elif name == "check":
        _pen(p, c, 2.4)
        p.drawPolyline(QPolygonF([QPointF(5.5, 12.5), QPointF(10, 17), QPointF(18.5, 7.5)]))


@lru_cache(maxsize=None)
def _pixmap(name: str, color: str, size: int) -> QPixmap:
    pm = QPixmap(QSize(size, size) * _SCALE)
    pm.setDevicePixelRatio(_SCALE)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.scale(size / _GRID, size / _GRID)
    _draw(name, painter, QColor(color))
    painter.end()
    return pm


def get_icon(name: str, color: str = theme.TEXT_MUTED, hover: str = theme.TEXT,
             disabled: str = theme.TEXT_DIM, size: int = 20) -> QIcon:
    """Normal, üzerine gelince (Active) ve pasif (Disabled) hâlleriyle bir ikon üretir."""
    icon = QIcon()
    icon.addPixmap(_pixmap(name, color, size), QIcon.Normal)
    icon.addPixmap(_pixmap(name, hover, size), QIcon.Active)
    icon.addPixmap(_pixmap(name, disabled, size), QIcon.Disabled)
    return icon


def app_icon() -> QIcon:
    """Uygulama simgesi: neon gradyanlı yuvarlak kare üzerinde aşağı ok."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        grad = QLinearGradient(0, 0, size, size)
        grad.setColorAt(0, QColor(theme.PURPLE))
        grad.setColorAt(1, QColor(theme.CYAN))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.24, size * 0.24)
        p.scale(size / _GRID, size / _GRID)
        _draw("download", p, QColor(theme.BG))
        p.end()
        icon.addPixmap(pm)
    return icon