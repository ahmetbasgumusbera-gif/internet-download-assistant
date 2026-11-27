"""Sayfadaki dosya bağlantıları penceresi: eklentinin topladığı bağlantıları süzüp seçtirir; seçilenler add_requested ile döner."""
from __future__ import annotations

from collections import Counter
from urllib.parse import unquote, urlparse

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
)

from gui import theme
from gui.components.playlist_panel import _LIST_QSS

_COMBO_QSS = f"""
QComboBox {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; border-radius: 10px; padding: 8px 12px;
             min-width: 170px; color: {theme.TEXT}; }}
QComboBox QAbstractItemView {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER};
             selection-background-color: {theme.SURFACE_2}; color: {theme.TEXT}; outline: none; }}
"""


class LinksDialog(QDialog):
    add_requested = Signal(object)      # seçilen adreslerin listesi

    def __init__(self, data: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sayfadaki dosyalar")
        self.resize(840, 640)
        self.setMinimumSize(640, 460)
        host = urlparse(data.get("pageUrl") or "").hostname or ""

        title = QLabel("Sayfadaki dosyalar")
        title.setObjectName("appTitle")
        sub = QLabel(f"{data.get('pageTitle') or 'Başlıksız sayfa'}   ·   {host}")
        sub.setObjectName("hint")

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Ada ya da adrese göre süz…")
        self.type_combo = QComboBox()
        self.type_combo.setStyleSheet(_COMBO_QSS)
        items = data.get("items") or []
        counts = Counter(i.get("ext", "") for i in items)
        self.type_combo.addItem(f"Tüm türler ({len(items)})", "")
        for ext, n in counts.most_common():
            self.type_combo.addItem(f"{ext or '?'} ({n})", ext)
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(self.filter_edit, 1)
        top.addWidget(self.type_combo)

        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet(_LIST_QSS)
        for entry in items:
            url = entry["url"]
            name = entry.get("text") or unquote(urlparse(url).path.rsplit("/", 1)[-1]) or url
            item = QListWidgetItem(f"{(entry.get('ext') or '?').upper():<6} {name}")
            item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            item.setCheckState(Qt.Unchecked)
            item.setToolTip(url)
            item.setData(Qt.UserRole, url)
            item.setData(Qt.UserRole + 1, entry.get("ext", ""))
            item.setData(Qt.UserRole + 2, f"{name} {url}".lower())
            self.list_widget.addItem(item)

        self.count_label = QLabel("")
        self.count_label.setObjectName("hint")
        sel_visible = self._button("Görünenleri seç")
        sel_none = self._button("Hiçbirini seçme")
        tools = QHBoxLayout()
        tools.setSpacing(8)
        tools.addWidget(sel_visible)
        tools.addWidget(sel_none)
        tools.addStretch(1)
        tools.addWidget(self.count_label)

        self.add_btn = QPushButton("  Seçilenleri listeye ekle")
        self.add_btn.setObjectName("primary")
        self.add_btn.setCursor(Qt.PointingHandCursor)
        close = self._button("Kapat")
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(close)
        buttons.addWidget(self.add_btn)

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 18)
        root.setSpacing(10)
        root.addWidget(title)
        root.addWidget(sub)
        root.addLayout(top)
        root.addLayout(tools)
        root.addWidget(self.list_widget, 1)
        root.addLayout(buttons)

        self.filter_edit.textChanged.connect(self._apply)
        self.type_combo.currentIndexChanged.connect(lambda _i: self._apply())
        self.list_widget.itemChanged.connect(lambda _i: self._refresh())
        sel_visible.clicked.connect(lambda: self._set_visible(Qt.Checked))
        sel_none.clicked.connect(lambda: self._set_visible(Qt.Unchecked, everything=True))
        self.add_btn.clicked.connect(self._emit)
        close.clicked.connect(self.close)
        self._apply()

    @staticmethod
    def _button(text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("chip" if text != "Kapat" else "ghost")
        btn.setCursor(Qt.PointingHandCursor)
        return btn

    def _apply(self) -> None:
        needle = self.filter_edit.text().strip().lower()
        ext = self.type_combo.currentData() or ""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            ok = (not ext or item.data(Qt.UserRole + 1) == ext) and (not needle or needle in item.data(Qt.UserRole + 2))
            item.setHidden(not ok)
        self._refresh()

    def _set_visible(self, state, everything: bool = False) -> None:
        self.list_widget.blockSignals(True)
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if everything or not item.isHidden():
                item.setCheckState(state)
        self.list_widget.blockSignals(False)
        self._refresh()

    def _checked(self) -> list:
        return [self.list_widget.item(i).data(Qt.UserRole) for i in range(self.list_widget.count())
                if self.list_widget.item(i).checkState() == Qt.Checked]

    def _refresh(self) -> None:
        shown = sum(1 for i in range(self.list_widget.count()) if not self.list_widget.item(i).isHidden())
        chosen = len(self._checked())
        self.count_label.setText(f"{shown} gösteriliyor  ·  {chosen} seçili")
        self.add_btn.setEnabled(chosen > 0)

    def _emit(self) -> None:
        urls = self._checked()
        if urls:
            self.add_requested.emit(urls)
            self.close()
