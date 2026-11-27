"""
Çok kutulu bağlantı girişi. Kutu sayısı "Aynı anda indirme" ayarını izler.
Birden fazla bağlantı yapıştırılırsa sonraki boş kutulara dağıtılır.
"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLineEdit, QScrollArea, QVBoxLayout, QWidget

URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

MAX_VISIBLE_ROWS = 4   # bundan fazla kutu varsa liste kaydırılır


class LinkFields(QWidget):
    submitted = Signal()   # herhangi bir kutuda Enter'a basıldı

    def __init__(self, count: int = 3, parent=None):
        super().__init__(parent)
        self.fields: list[QLineEdit] = []

        self._host = QWidget()
        self._host.setObjectName("listHost")
        self._layout = QVBoxLayout(self._host)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setWidget(self._host)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._scroll)

        self.set_count(count)

    # ------------------------------------------------------------------ kutu sayısı
    def set_count(self, count: int) -> None:
        """Kutu sayısını ayarlar. Azaltırken dolu kutuların içeriği korunur."""
        count = max(1, int(count))
        while len(self.fields) < count:
            self._add_field()
        while len(self.fields) > count:
            self._remove_last_field()
        self._apply_height()

    def _add_field(self) -> None:
        field = QLineEdit()
        field.setObjectName("linkField")
        field.setClearButtonEnabled(True)
        index = len(self.fields)
        field.setPlaceholderText(
            "İndirmek istediğiniz bağlantıyı buraya yapıştırın" if index == 0 else f"Bağlantı {index + 1}"
        )
        field.returnPressed.connect(self.submitted.emit)
        field.textChanged.connect(lambda _text, f=field: self._on_text_changed(f))
        self._layout.addWidget(field)
        self.fields.append(field)

    def _remove_last_field(self) -> None:
        # Silinecek kutuda yazı varsa, boş bir kutuya taşı (içerik kaybolmasın)
        field = self.fields[-1]
        text = field.text().strip()
        if text:
            for other in self.fields[:-1]:
                if not other.text().strip():
                    other.setText(text)
                    break
        self.fields.pop()
        self._layout.removeWidget(field)
        field.deleteLater()

    def _apply_height(self) -> None:
        first = self.fields[0]
        first.ensurePolished()
        row = first.sizeHint().height()
        visible = min(len(self.fields), MAX_VISIBLE_ROWS)
        spacing = self._layout.spacing()
        self._scroll.setFixedHeight(visible * row + (visible - 1) * spacing + 2)
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarAsNeeded if len(self.fields) > MAX_VISIBLE_ROWS else Qt.ScrollBarAlwaysOff
        )

    # ------------------------------------------------------------------ yapıştırma dağıtımı
    def _on_text_changed(self, field: QLineEdit) -> None:
        urls = URL_PATTERN.findall(field.text())
        if len(urls) < 2:
            return
        start = self.fields.index(field)
        slots = [start] + [
            j for j in range(start + 1, len(self.fields)) if not self.fields[j].text().strip()
        ]
        for slot, url in zip(slots, urls):
            self._set_text_silently(self.fields[slot], url)
        leftover = urls[len(slots):]
        if leftover:  # kutular yetmedi: kalanlar son kutuya eklenir (yine de hepsi indirilir)
            last = self.fields[slots[-1]]
            self._set_text_silently(last, " ".join([last.text()] + leftover))

    @staticmethod
    def _set_text_silently(field: QLineEdit, text: str) -> None:
        field.blockSignals(True)
        field.setText(text)
        field.blockSignals(False)

    # ------------------------------------------------------------------ yardımcılar
    def focus_first_empty(self) -> None:
        for field in self.fields:
            if not field.text().strip():
                field.setFocus()
                return
        self.fields[0].setFocus()