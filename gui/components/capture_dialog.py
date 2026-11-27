"""
Tarayıcı eklentisinin yakaladığı indirmeyi onaylatan küçük pencere (IDM tarzı): ad, boyut, adres ve kayıt klasörü.
"Vazgeç" derse indirme tarayıcıda yapılmaz ve uygulamaya da eklenmez; "İndir" derse ana listeye eklenir.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

from gui import theme
from gui.icons import get_icon
from core import convert, rules
from utils.helpers import format_size, is_dangerous


def _name_from_url(url: str) -> str:
    """Tarayıcı adı bildirmediyse adresin son parçasından tahmin eder (uzantı yoksa boş)."""
    name = unquote(urlparse(url).path.rsplit("/", 1)[-1])
    return name if "." in name else ""


class CaptureDialog(QDialog):
    def __init__(self, payload: dict, default_dir: Path, parent=None):
        super().__init__(parent)
        self._default_dir = Path(default_dir)
        self.setWindowTitle("İndirme yakalandı")
        self.setMinimumWidth(580)

        source = urlparse(payload.get("referrer") or payload.get("url") or "").hostname or ""
        title = QLabel("İndirme yakalandı")
        title.setObjectName("appTitle")
        subtitle = QLabel(f"Kaynak: {source}" if source else "Tarayıcıdan gönderildi")
        subtitle.setObjectName("hint")

        self._orig_name = payload.get("filename") or _name_from_url(payload.get("url") or "")
        self.name_edit = QLineEdit(self._orig_name)
        self.name_edit.setPlaceholderText("Dosya adı sunucudan belirlenecek")
        size = payload.get("fileSize") or -1
        self.size_label = QLabel(format_size(size) if size > 0 else "Bilinmiyor")
        self.url_edit = QLineEdit(payload.get("url") or "")
        self.url_edit.setReadOnly(True)
        self.url_edit.setCursorPosition(0)
        self.dir_edit = QLineEdit(str(self._default_dir))
        self.convert_combo = QComboBox()
        self.convert_combo.setStyleSheet(theme.combo_qss(220))
        for code, label in convert.CHOICES:
            self.convert_combo.addItem(label, code)

        browse = QPushButton(" Seç…")
        browse.setObjectName("ghost")
        browse.setIcon(get_icon("folder", size=18))
        browse.setCursor(Qt.PointingHandCursor)
        browse.setMinimumHeight(42)
        browse.clicked.connect(self._browse)
        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        dir_row.addWidget(self.dir_edit, 1)
        dir_row.addWidget(browse)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(1, 1)
        rows = (("Dosya adı", self.name_edit), ("Boyut", self.size_label),
                ("Adres", self.url_edit), ("Kaydedilecek klasör", dir_row), ("İndirince", self.convert_combo))
        for i, (text, field) in enumerate(rows):
            label = QLabel(text)
            label.setObjectName("sectionTitle")
            grid.addWidget(label, i, 0, Qt.AlignVCenter | Qt.AlignLeft)
            if isinstance(field, QHBoxLayout):
                grid.addLayout(field, i, 1)
            else:
                grid.addWidget(field, i, 1)

        self.warn_label = QLabel("⚠ Çalıştırılabilir dosya: kaynağına güvenmiyorsan indirme.")
        self.warn_label.setObjectName("bannerWarn")
        self.warn_label.setWordWrap(True)
        self.name_edit.textChanged.connect(self._update_warning)
        self._update_warning()

        self.download_btn = QPushButton("  İndir")
        self.download_btn.setObjectName("primary")
        self.download_btn.setIcon(get_icon("download", color="#0c0d16", hover="#0c0d16", size=20))
        self.download_btn.setCursor(Qt.PointingHandCursor)
        self.download_btn.setDefault(True)
        self.download_btn.setMinimumWidth(150)
        self.cancel_btn = QPushButton("Vazgeç")
        self._listed = False
        self.list_btn = QPushButton("Listeye ekle")
        self.list_btn.setObjectName("ghost")
        self.list_btn.setCursor(Qt.PointingHandCursor)
        self.list_btn.setToolTip("İndirmeyi başlatmadan Listeler sekmesine ekler")
        self.list_btn.setVisible(True)
        self.cancel_btn.setObjectName("ghost")
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.download_btn.clicked.connect(self.accept)
        self.list_btn.clicked.connect(self._add_to_list)
        self.cancel_btn.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.list_btn)
        buttons.addWidget(self.download_btn)

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 20)
        root.setSpacing(14)
        root.addWidget(title)
        root.addWidget(subtitle)
        root.addLayout(grid)
        root.addWidget(self.warn_label)
        root.addLayout(buttons)

    def _add_to_list(self) -> None:
        self._listed = True
        self.accept()

    def _update_warning(self) -> None:
        self.warn_label.setVisible(is_dangerous(self.name_edit.text().strip()))

    def _browse(self) -> None:
        start = self.dir_edit.text().strip() or str(self._default_dir)
        chosen = QFileDialog.getExistingDirectory(self, "Kaydedilecek klasörü seç", start)
        if chosen:
            self.dir_edit.setText(str(Path(chosen)))

    def values(self) -> dict:
        """Kullanıcının onayladığı değerler: {"file_name": ..., "save_dir": Path}."""
        text = self.dir_edit.text().strip()
        name = self.name_edit.text().strip()
        original_ext = rules.ext_of(self._orig_name)
        if name and original_ext and not rules.ext_of(name):
            name += original_ext                     # uzantı silindiyse geri eklenir (foto -> foto.jpg)
        return {"file_name": name,
                "save_dir": Path(text) if text else self._default_dir,
                "listed": self._listed,
                "convert_to": str(self.convert_combo.currentData() or "")}
