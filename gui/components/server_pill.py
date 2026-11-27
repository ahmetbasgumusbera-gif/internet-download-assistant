"""
Sunucu durum rozeti (kaybolmayan küçük hap): gri = kapalı, sarı = açılıyor, yeşil = açık, kırmızı = hata.
Renk geçişleri yumuşaktır. "Açılıyor" iken nokta yavaşça "nefes alır"; açıkken sabit, hafif parlar (yanıp sönme yok).
Tıklanınca clicked sinyali verir.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QSizePolicy, QWidget

from core.i18n import tr
from gui import theme

_LABELS = {"off": "Sunucu kapalı", "starting": "Açılıyor…", "running": "Sunucu açık", "error": "Sunucu hatası"}
_COLORS = {"off": theme.TEXT_DIM, "starting": theme.AMBER, "running": theme.GREEN, "error": theme.RED}


class ServerPill(QWidget):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = "off"
        self._color = QColor(_COLORS["off"])
        self._phase = 0.0
        self._hover = False

        self._font = QFont()
        self._font.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Inter", "Roboto"])
        self._font.setPixelSize(12)
        self._font.setWeight(QFont.Weight.DemiBold)

        self._color_anim = QVariantAnimation(self)
        self._color_anim.setDuration(450)
        self._color_anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._color_anim.valueChanged.connect(self._on_color)

        self._pulse = QVariantAnimation(self)
        self._pulse.setStartValue(0.0)
        self._pulse.setEndValue(1.0)
        self._pulse.setDuration(1700)
        self._pulse.setLoopCount(-1)
        self._pulse.valueChanged.connect(self._on_pulse)

        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.setToolTip("Tarayıcı eklentisi için yerel sunucu. Açmak/kapatmak için tıklayın.")

    # ------------------------------------------------------------------ durum
    @property
    def state(self) -> str:
        return self._state

    def retranslate(self) -> None:
        self.update()

    def set_state(self, state: str, message: str = "") -> None:
        if state not in _COLORS:
            state = "off"
        changed = state != self._state
        self._state = state
        if message:
            self.setToolTip(message)
        if changed:
            self._color_anim.stop()
            self._color_anim.setStartValue(QColor(self._color))
            self._color_anim.setEndValue(QColor(_COLORS[state]))
            self._color_anim.start()
            if state == "starting":
                self._phase = 0.0
                self._pulse.start()
            else:
                self._pulse.stop()
                self._phase = 0.0
        self.update()

    def _on_color(self, value) -> None:
        self._color = QColor(value)
        self.update()

    def _on_pulse(self, value) -> None:
        self._phase = float(value)
        self.update()

    # ------------------------------------------------------------------ ölçü ve etkileşim
    def sizeHint(self) -> QSize:
        fm = QFontMetrics(self._font)
        width = max(fm.horizontalAdvance(text) for text in _LABELS.values())
        return QSize(34 + width + 18, 32)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------ çizim
    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        radius = rect.height() / 2

        border = QColor(self._color)
        border.setAlpha(150 if self._hover else 85)
        p.setPen(QPen(border, 1))
        p.setBrush(QColor(theme.SURFACE_2 if self._hover else theme.SURFACE))
        p.drawRoundedRect(rect, radius, radius)

        cx, cy = rect.left() + 17, rect.center().y()
        if self._state != "off":
            breathe = 0.5 - 0.5 * math.cos(2 * math.pi * self._phase) if self._state == "starting" else 0.0
            glow = 8.0 + 4.0 * breathe
            halo = QColor(self._color)
            gradient = QRadialGradient(QPointF(cx, cy), glow)
            halo.setAlpha(int(70 + 90 * breathe))
            gradient.setColorAt(0.0, halo)
            halo.setAlpha(0)
            gradient.setColorAt(1.0, halo)
            p.setPen(Qt.NoPen)
            p.setBrush(gradient)
            p.drawEllipse(QPointF(cx, cy), glow, glow)

        p.setPen(Qt.NoPen)
        p.setBrush(self._color)
        p.drawEllipse(QPointF(cx, cy), 4.5, 4.5)

        p.setFont(self._font)
        p.setPen(QColor(theme.TEXT if self._state in ("running", "starting") else theme.TEXT_MUTED))
        text_rect = QRectF(rect.left() + 32, rect.top(), rect.width() - 40, rect.height())
        p.drawText(text_rect, int(Qt.AlignVCenter | Qt.AlignLeft), tr(_LABELS[self._state]))
