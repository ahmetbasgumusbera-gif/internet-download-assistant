"""Modern kaydırmalı açma/kapama anahtarı. text() "Açık"/"Kapalı" tutulabilir ama çizilmez (eski kodla uyumlu)."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QAbstractButton

from gui import theme


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t))


class ToggleSwitch(QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(44, 24)
        self._t = 0.0
        self._hover = False
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._anim.valueChanged.connect(self._on_anim)
        self.toggled.connect(self._animate)

    def sizeHint(self) -> QSize:
        return QSize(44, 24)

    def setChecked(self, on: bool) -> None:
        super().setChecked(on)
        self._anim.stop()
        self._t = 1.0 if self.isChecked() else 0.0     # programla değişimde animasyonsuz
        self.update()

    def _animate(self, on: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(float(self._t))
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def _on_anim(self, value) -> None:
        self._t = float(value)
        self.update()

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        off = QColor(theme.BORDER_HOVER)
        grad = QLinearGradient(r.left(), 0, r.right(), 0)
        grad.setColorAt(0, _mix(off, QColor(theme.PURPLE), self._t))
        grad.setColorAt(1, _mix(off, QColor(theme.CYAN), self._t))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        x = r.left() + 3 + (r.width() - d - 6) * self._t
        p.setBrush(QColor("#ffffff") if self._hover or self._t > 0.5 else QColor("#d8dbef"))
        p.drawEllipse(QRectF(x, r.top() + 3, d, d))
