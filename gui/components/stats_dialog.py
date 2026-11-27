"""İstatistik penceresi: indirilen miktar, ortalama hız, en çok kullanılan siteler."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from utils.helpers import format_size, format_speed


class StatsDialog(QDialog):
    def __init__(self, data: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("İstatistik")
        self.setMinimumWidth(480)
        title = QLabel("İndirme istatistikleri")
        title.setObjectName("appTitle")
        since = QLabel(f"Kayıt başlangıcı: {data['since']}" if data["since"] else "Henüz kayıtlı indirme yok.")
        since.setObjectName("hint")

        rows = [
            ("Bu ay", f"{format_size(data['month_bytes'])}  ·  {data['month_count']} dosya"),
            ("Son 7 gün", format_size(data["week_bytes"])),
            ("Toplam", f"{format_size(data['total_bytes'])}  ·  {data['total_count']} dosya"),
            ("Ortalama hız", format_speed(data["avg_speed"])),
        ]
        grid = QGridLayout()
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(10)
        for i, (label, value) in enumerate(rows):
            a = QLabel(label)
            a.setObjectName("sectionTitle")
            b = QLabel(value)
            b.setObjectName("fileName")
            grid.addWidget(a, i, 0)
            grid.addWidget(b, i, 1)

        top = QLabel("En çok indirilen siteler")
        top.setObjectName("sectionTitle")
        lines = [f"{h}   ·   {format_size(size)}   ·   {count} dosya" for h, size, count in data["top"]] or ["-"]
        top_list = QLabel("\n".join(lines))
        top_list.setObjectName("info")

        close = QPushButton("Kapat")
        close.setObjectName("ghost")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(close)

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 20)
        root.setSpacing(12)
        root.addWidget(title)
        root.addWidget(since)
        root.addLayout(grid)
        root.addWidget(top)
        root.addWidget(top_list)
        root.addLayout(buttons)
