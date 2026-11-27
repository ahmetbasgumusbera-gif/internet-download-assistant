"""Zamanlayıcı penceresi: indirmelerin başlama saati ve tümü bitince yapılacak işlem."""
from __future__ import annotations

from PySide6.QtCore import QTime, Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QTimeEdit, QVBoxLayout,
)

from gui import theme

_QSS = f"""
QComboBox, QTimeEdit {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; border-radius: 10px;
             padding: 8px 12px; min-width: 200px; color: {theme.TEXT}; }}
QComboBox QAbstractItemView {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER};
             selection-background-color: {theme.SURFACE_2}; color: {theme.TEXT}; outline: none; }}
QTimeEdit::up-button, QTimeEdit::down-button {{ width: 0; border: none; }}
"""

ACTIONS = (("Hiçbir şey yapma", "none"), ("Bilgisayarı uykuya al", "sleep"), ("Bilgisayarı kapat", "shutdown"))


class ScheduleDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Zamanlayıcı")
        self.setMinimumWidth(500)
        self.setStyleSheet(_QSS)

        title = QLabel("Zamanlayıcı")
        title.setObjectName("appTitle")
        hint = QLabel("Açıkken, sıradaki indirmeler seçtiğin saate kadar bekler (yeni eklenenler dahil); çalışanlar "
                      "etkilenmez. Kapatma / uyku, tüm indirmeler bitince 30 saniyelik geri sayımla sorulur ve "
                      "duraklatılmış indirme varsa uygulanmaz.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)

        self.enable_chip = QPushButton()
        self.enable_chip.setObjectName("chip")
        self.enable_chip.setCheckable(True)
        self.enable_chip.setCursor(Qt.PointingHandCursor)
        self.enable_chip.toggled.connect(lambda on: self.enable_chip.setText("Açık" if on else "Kapalı"))
        self.enable_chip.setChecked(bool(config.get("schedule_enabled")))
        self.enable_chip.setText("Açık" if self.enable_chip.isChecked() else "Kapalı")

        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm")
        parsed = QTime.fromString(str(config.get("schedule_time")), "HH:mm")
        self.time_edit.setTime(parsed if parsed.isValid() else QTime(2, 0))

        self.action_combo = QComboBox()
        for label, value in ACTIONS:
            self.action_combo.addItem(label, value)
        index = self.action_combo.findData(config.get("after_all_action"))
        self.action_combo.setCurrentIndex(max(0, index))

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(12)
        for i, (text, widget) in enumerate((("Zamanlayıcı", self.enable_chip), ("Başlama saati", self.time_edit),
                                            ("Tümü bitince", self.action_combo))):
            label = QLabel(text)
            label.setObjectName("sectionTitle")
            grid.addWidget(label, i, 0)
            grid.addWidget(widget, i, 1, Qt.AlignLeft)

        save = QPushButton("  Kaydet")
        save.setObjectName("primary")
        save.setCursor(Qt.PointingHandCursor)
        save.clicked.connect(self.accept)
        cancel = QPushButton("Vazgeç")
        cancel.setObjectName("ghost")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(save)

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 20)
        root.setSpacing(14)
        root.addWidget(title)
        root.addWidget(hint)
        root.addLayout(grid)
        root.addLayout(buttons)

    def values(self) -> tuple[bool, str, str]:
        return (self.enable_chip.isChecked(), self.time_edit.time().toString("HH:mm"),
                self.action_combo.currentData() or "none")
