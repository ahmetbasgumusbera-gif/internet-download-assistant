"""İndirme adresini yenile penceresi: geçerli adres gösterilir; yeni adres yapıştırılır ya da kaynak sayfaya gidilir."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout


class RefreshUrlDialog(QDialog):
    def __init__(self, url: str, has_page: bool, parent=None):
        super().__init__(parent)
        self.action = ""
        self.setWindowTitle("İndirme adresini yenile")
        self.setMinimumWidth(640)

        title = QLabel("İndirme adresini yenile")
        title.setObjectName("appTitle")
        hint = QLabel("Adres süresi dolduysa eski adresi silip yenisini yapıştır: aynı dosyaysa indirme kaldığı yerden "
                      "devam eder, farklıysa uyarılırsın. Ya da kaynak sayfaya git ve indirmeyi orada yeniden başlat; "
                      "eklenti yeni adresi yakalayıp indirmeyi sürdürür.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)

        self.edit = QLineEdit(url)
        self.edit.setPlaceholderText("https://...")
        self.edit.setCursorPosition(0)

        apply_btn = QPushButton("  Bu adresi uygula")
        apply_btn.setObjectName("primary")
        apply_btn.setCursor(Qt.PointingHandCursor)
        page_btn = QPushButton("Kaynak sayfaya git")
        page_btn.setObjectName("ghost")
        page_btn.setCursor(Qt.PointingHandCursor)
        page_btn.setEnabled(has_page)
        page_btn.setToolTip("İndirmeyi başlattığın sayfayı tarayıcıda açar" if has_page
                            else "Bu indirme için kayıtlı bir kaynak sayfa yok")
        cancel = QPushButton("Vazgeç")
        cancel.setObjectName("ghost")
        cancel.setCursor(Qt.PointingHandCursor)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(page_btn)
        buttons.addWidget(apply_btn)

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 20)
        root.setSpacing(12)
        root.addWidget(title)
        root.addWidget(hint)
        root.addWidget(self.edit)
        root.addLayout(buttons)

        apply_btn.clicked.connect(lambda: self._done("apply"))
        page_btn.clicked.connect(lambda: self._done("browser"))
        cancel.clicked.connect(self.reject)
        self.edit.returnPressed.connect(lambda: self._done("apply"))

    def _done(self, action: str) -> None:
        self.action = action
        self.accept()

    def url(self) -> str:
        return self.edit.text().strip()
