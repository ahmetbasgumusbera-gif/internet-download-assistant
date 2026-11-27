"""Listeler sekmesi için "Zamanlayarak indir" penceresi: başlama saati ve mod (hepsi aynı anda / sırayla)."""
from __future__ import annotations

from PySide6.QtCore import QTime, Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QTimeEdit, QVBoxLayout,
)

from gui.components.schedule_dialog import _QSS


class ListScheduleDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Zamanlayarak indir")
        self.setMinimumWidth(480)
        self.setStyleSheet(_QSS)

        title = QLabel("Zamanlayarak indir")
        title.setObjectName("appTitle")
        hint = QLabel("Seçtiğin saatte listede bekleyen indirmeler başlar. Uygulama o sırada kapalıysa, "
                      "bir sonraki açılışta başlar.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)

        self.time_edit = QTimeEdit()
        self.time_edit.setDisplayFormat("HH:mm")
        parsed = QTime.fromString(str(config.get("schedule_time")), "HH:mm")
        self.time_edit.setTime(parsed if parsed.isValid() else QTime(2, 0))

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Hepsi aynı anda", "parallel")
        self.mode_combo.addItem("Sırayla", "sequential")
        self.mode_combo.setCurrentIndex(1 if config.get("list_schedule_mode") == "sequential" else 0)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(12)
        for i, (text, widget) in enumerate((("Başlama saati", self.time_edit), ("Nasıl indirilsin", self.mode_combo))):
            label = QLabel(text)
            label.setObjectName("sectionTitle")
            grid.addWidget(label, i, 0)
            grid.addWidget(widget, i, 1, Qt.AlignLeft)

        save = QPushButton("  Zamanla")
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

    def values(self) -> tuple[str, str]:
        return self.time_edit.time().toString("HH:mm"), self.mode_combo.currentData() or "parallel"
