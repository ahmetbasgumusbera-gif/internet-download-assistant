"""Klasör ve ad kuralları penceresi."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from core import rules

_HELP = ("Her satıra bir kural yaz (# ile başlayan satır yorumdur):\n"
         "  ext: iso, img => D:\\ISO            uzantıya göre klasör\n"
         "  site: example.com => D:\\Oyunlar    siteye göre klasör\n"
         "  strip: Oyunindir vip               dosya adından bu metni sil\n"
         "Kural eşleşmezse normal indirme klasörü kullanılır. Yakalanan indirmelerde klasörü onay penceresinde yine değiştirebilirsin.")


class RulesDialog(QDialog):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self._config = config
        self.setWindowTitle("Klasör ve ad kuralları")
        self.resize(640, 480)
        title = QLabel("Klasör ve ad kuralları")
        title.setObjectName("appTitle")
        hint = QLabel(_HELP)
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        self.editor = QPlainTextEdit(str(config.get("folder_rules")))
        self.editor.setPlaceholderText("ext: iso => D:\\ISO")
        self.clean_box = QCheckBox("Dosya adındaki [site.com], www.site.com - gibi etiketleri temizle")
        self.clean_box.setChecked(bool(config.get("clean_names")))
        save = QPushButton("  Kaydet")
        save.setObjectName("primary")
        save.setCursor(Qt.PointingHandCursor)
        cancel = QPushButton("Vazgeç")
        cancel.setObjectName("ghost")
        cancel.setCursor(Qt.PointingHandCursor)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 18)
        root.setSpacing(10)
        for w in (title, hint):
            root.addWidget(w)
        root.addWidget(self.editor, 1)
        root.addWidget(self.clean_box)
        root.addLayout(buttons)
        save.clicked.connect(self._save)
        cancel.clicked.connect(self.reject)

    def _save(self) -> None:
        self._config.set("folder_rules", self.editor.toPlainText(), autosave=False)
        self._config.set("clean_names", self.clean_box.isChecked(), autosave=False)
        self._config.save()
        rules.configure(self.clean_box.isChecked(), self.editor.toPlainText())
        self.accept()
