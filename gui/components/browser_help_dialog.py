"""Tarayıcı eklentisi yardımcısı: kurulu tarayıcıları listeler, eklenti sayfasını açar, klasör yolunu verir."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from core import browsers


class BrowserHelpDialog(QDialog):
    def __init__(self, extension_dir: Path, seen_version: str = "", parent=None):
        super().__init__(parent)
        self._dir = Path(extension_dir)
        self.setWindowTitle("Tarayıcı eklentisi")
        self.setMinimumWidth(620)

        title = QLabel("Tarayıcı eklentisi")
        title.setObjectName("appTitle")
        status = QLabel(f"Eklenti uygulamaya bağlı (v{seen_version})" if seen_version
                        else "Eklenti henüz uygulamaya bağlanmadı (ya da bu oturumda kullanılmadı).")
        status.setObjectName("bannerOk" if seen_version else "bannerWarn")
        status.setWordWrap(True)
        steps = QLabel(
            "1. Aşağıdan tarayıcını seç: eklentiler sayfası açılır.\n"
            "2. Sağ üstten \"Geliştirici modu\"nu aç.\n"
            "3. \"Paketlenmemiş öğe yükle\" de ve şu klasörü seç (\"Yolu kopyala\" ile panoya alabilirsin):\n"
            f"    {self._dir}\n"
            "4. Uygulamada sunucu rozeti yeşilken eklenti hazırdır.")
        steps.setObjectName("hint")
        steps.setWordWrap(True)
        steps.setTextInteractionFlags(Qt.TextSelectableByMouse)
        note = QLabel("Tarayıcılar mağaza dışı eklentilerin kendiliğinden kurulmasına izin vermez; sarı uyarılı otomatik "
                      "kurulum için eklentinin Chrome Web Store / Edge Eklentileri'nde yayınlanması gerekir.")
        note.setObjectName("hint")
        note.setWordWrap(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 20)
        root.setSpacing(10)
        root.addWidget(title)
        root.addWidget(status)
        root.addWidget(steps)

        found = browsers.detect()
        if not found:
            root.addWidget(QLabel("Kurulu tarayıcı bulunamadı."))
        for browser in found:
            row = QHBoxLayout()
            label = QLabel(browser.name + ("" if browser.supported else "   (bu sürümde desteklenmiyor)"))
            label.setObjectName("fileName")
            button = QPushButton("Eklenti sayfasını aç")
            button.setObjectName("ghost")
            button.setCursor(Qt.PointingHandCursor)
            button.setEnabled(browser.supported)
            button.clicked.connect(lambda _=False, b=browser: self._open_browser(b))
            row.addWidget(label, 1)
            row.addWidget(button)
            root.addLayout(row)
        root.addWidget(note)

        copy_btn = QPushButton("Yolu kopyala")
        open_btn = QPushButton("Klasörü aç")
        close_btn = QPushButton("Kapat")
        for b in (copy_btn, open_btn, close_btn):
            b.setObjectName("ghost")
            b.setCursor(Qt.PointingHandCursor)
        copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(str(self._dir)))
        open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._dir))))
        close_btn.clicked.connect(self.close)
        buttons = QHBoxLayout()
        buttons.addWidget(copy_btn)
        buttons.addWidget(open_btn)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

    def _open_browser(self, browser) -> None:
        if browser.name == "Firefox":
            from PySide6.QtWidgets import QMessageBox
            from core import firefox_ext
            from utils import paths
            try:
                folder = firefox_ext.build(self._dir, paths.DATA_DIR / "extension_firefox")
            except Exception as e:
                QMessageBox.warning(self, "Firefox", f"Firefox eklentisi hazırlanamadı: {e}")
                return
            manifest = folder / "manifest.json"
            QApplication.clipboard().setText(str(manifest))
            QMessageBox.information(
                self, "Firefox",
                "Firefox için eklenti hazırlandı. Açılan sayfada “Geçici eklenti yükle…” de ve şu dosyayı seç "
                f"(yolu panoya kopyaladım):\n\n{manifest}\n\nNot: geçici eklentiler Firefox kapanınca kalkar; "
                "kalıcı kurulum için eklentinin Mozilla'ya (AMO) imzalatılması gerekir.")
        browsers.open_extensions_page(browser)
