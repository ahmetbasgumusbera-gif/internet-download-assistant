"""Yüzen mini kutu: küçük, hep üstte, sürüklenebilir. Üstüne bağlantı bırakınca indirme başlar; ilerleme halkası gösterir."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QMenu, QWidget

from gui import theme
from gui.components.link_fields import URL_PATTERN
from gui.icons import _draw
from utils.helpers import format_speed

_HINT = "Bağlantıyı buraya sürükleyip bırakarak indir. Tıklayınca ana pencere açılır."


class MiniBox(QWidget):
    dropped = Signal(list)
    activated = Signal()
    moved = Signal(int, int)
    hide_requested = Signal()
    SIZE = 68

    def __init__(self):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.setAcceptDrops(True)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(_HINT)
        self._percent = -1.0
        self._key = None
        self._over = False
        self._grab = None
        self._moved = False

    def set_state(self, speed: float, percent: float) -> None:
        key = (round(speed / 1024), int(percent))
        if key == self._key:
            return
        self._key, self._percent = key, percent
        self.setToolTip(f"↓ {format_speed(speed)}  ·  %{int(percent)}" if speed > 0 else _HINT)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(6, 6, -6, -6)
        glow = QColor(theme.CYAN)
        glow.setAlpha(110 if self._over else 45)
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(r.adjusted(-4, -4, 4, 4))
        gradient = QLinearGradient(r.topLeft(), r.bottomRight())
        gradient.setColorAt(0, QColor(theme.PURPLE))
        gradient.setColorAt(1, QColor(theme.CYAN))
        p.setBrush(gradient)
        p.drawEllipse(r)
        if self._percent >= 0:
            pen = QPen(QColor("#ffffff"), 3)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawArc(r.adjusted(3, 3, -3, -3), 90 * 16, -int(360 * 16 * self._percent / 100))
        p.save()
        p.translate(self.width() / 2 - 13, self.height() / 2 - 13)
        p.scale(26 / 24.0, 26 / 24.0)
        _draw("download", p, QColor(theme.BG))
        p.restore()

    def dragEnterEvent(self, event) -> None:
        mime = event.mimeData()
        if mime.hasUrls() or mime.hasText():
            self._over = True
            self.update()
            event.acceptProposedAction()

    def dragLeaveEvent(self, _event) -> None:
        self._over = False
        self.update()

    def dropEvent(self, event) -> None:
        self._over = False
        self.update()
        mime = event.mimeData()
        urls = [u.toString() for u in mime.urls() if u.scheme() in ("http", "https")]
        if not urls and mime.hasText():
            urls = URL_PATTERN.findall(mime.text())
        if urls:
            event.acceptProposedAction()
            self.dropped.emit(urls)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self._grab = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._moved = False

    def mouseMoveEvent(self, event) -> None:
        if self._grab is not None and event.buttons() & Qt.LeftButton:
            target = event.globalPosition().toPoint() - self._grab
            if self._moved or (target - self.frameGeometry().topLeft()).manhattanLength() > 3:
                self._moved = True
                self.move(target)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._grab is not None:
            self._grab = None
            if self._moved:
                self.moved.emit(self.x(), self.y())
            else:
                self.activated.emit()

    def contextMenuEvent(self, event) -> None:
        menu = QMenu(self)
        menu.addAction("Aç", self.activated.emit)
        menu.addAction("Gizle", self.hide_requested.emit)
        menu.exec(event.globalPos())
