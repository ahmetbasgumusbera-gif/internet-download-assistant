"""
Ayarlar penceresi. Değerler ConfigManager'a yazılır; yönetici apply_settings() ile haberdar edilir.
"""
from __future__ import annotations

import json
from pathlib import Path

import threading

from PySide6.QtCore import QSize, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel,
    QComboBox, QLineEdit, QPushButton, QSpinBox, QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from core import backup, convert, netconf, report, youtube
from gui import theme
from gui.icons import get_icon
from core import tools
from utils import autostart, paths
from utils.version import __version__
from gui.components.toggle_switch import ToggleSwitch
from gui.dismiss import DISMISS


class SettingsDialog(QDialog):
    _ffmpeg_text = Signal(str)
    _ffmpeg_done = Signal(bool, str)
    _version_ready = Signal(str)
    _update_done = Signal(bool, str)
    changed = Signal()                  # bir ayar değişti ve kaydedildi (anında uygulanır)
    _dirty = False

    def __init__(self, config, parent=None, ytdlp_version_fn=None, ytdlp_update_fn=None,
                 ffmpeg_version_fn=None, ffmpeg_update_fn=None):
        super().__init__(parent)
        self._config = config
        self._version_fn = ytdlp_version_fn or youtube.ytdlp_version
        self._update_fn = ytdlp_update_fn or youtube.update_ytdlp
        self.toggles: dict[str, QPushButton] = {}
        self._ffmpeg_version_fn = ffmpeg_version_fn or tools.ffmpeg_version
        self._ffmpeg_update_fn = ffmpeg_update_fn or tools.update_ffmpeg
        self.setWindowTitle("Ayarlar")
        self.setModal(False)
        self.setMinimumWidth(540)

        # ---- indirme klasörü
        self.dir_edit = QLineEdit()
        self.dir_edit.setPlaceholderText(str(paths.default_download_dir()))
        self.dir_edit.setText(str(config.get("download_dir")))
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

        # ---- sayısal ayarlar
        self.concurrent_spin = self._make_spin(1, 10, config.get("max_concurrent_downloads"))
        self.connections_spin = self._make_spin(1, 32, config.get("connections_per_download"))
        current_limit = config.get("speed_limit_kbps")
        self.speed_spin = self._make_spin(64, 10_000_000, current_limit or 1024,
                                          step=256, suffix=" KB/s")
        self.unlimited_chip = QPushButton("Sınırsız")
        self.unlimited_chip.setObjectName("chip")
        self.unlimited_chip.setCheckable(True)
        self.unlimited_chip.setCursor(Qt.PointingHandCursor)
        self.unlimited_chip.setToolTip("İşaretliyse hız sınırı uygulanmaz")
        speed_row = self._stepper(self.speed_spin)
        speed_row.insertWidget(3, self.unlimited_chip)
        self.unlimited_chip.toggled.connect(lambda checked: self._set_speed_enabled(speed_row, not checked))
        self.unlimited_chip.setChecked(current_limit == 0)
        self._set_speed_enabled(speed_row, current_limit != 0)

        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(14)
        grid.setColumnStretch(1, 1)
        rows = (
            ("İndirme klasörü", dir_row),
            ("Aynı anda indirme", self._stepper(self.concurrent_spin)),
            ("İndirme başına bağlantı", self._stepper(self.connections_spin)),
            ("Toplam hız sınırı", speed_row),
            ("yt-dlp (YouTube)", self._build_ytdlp_row()),
            ("📁  Türüne göre klasörle", self._toggle_row(
                "sort_into_categories", "Biten dosyayı Videolar, Müzik, Belgeler gibi klasörlere koyar")),
            ("🛡  Tehlikeli dosya uyarısı", self._toggle_row(
                "warn_dangerous_files", "Çalıştırılabilir dosyalarda (.exe, .msi, .apk…) kartta uyarı gösterir")),
            ("▶  Açılışta sürdür", self._toggle_row(
                "auto_resume_on_start", "Kapanırken devam eden indirmeler uygulama açılınca kendiliğinden sürer")),
            ("📋  Pano takibi", self._toggle_row(
                "clipboard_watch", "Kopyaladığın indirme bağlantısını önerir")),
            ("🔌  Sunucuyu otomatik aç", self._toggle_row(
                "server_autostart", "Uygulama açılınca tarayıcı eklentisi için yerel sunucu da açılır")),
            ("🪟  Windows ile başlat", self._toggle_row(
                "start_with_windows", "Bilgisayar açılınca uygulama arka planda (tepside) başlar")),
            ("🔄  Araçları otomatik güncelle", self._toggle_row(
                "auto_update_tools", "yt-dlp günde bir, Deno ayda bir arka planda güncellenir (indirme sürerken beklenir)")),
            ("🎬  Videoyu önceden analiz et", self._toggle_row(
                "youtube_prefetch", "YouTube videosu tarayıcıda açılınca, İndir'e basmadan arka planda analiz edilir")),
            ("🍪  YouTube oturum çerezleri", self._toggle_row(
                "youtube_use_cookies",
                "Tarayıcı eklentisi, YouTube'daki açık oturumunun çerezlerini uygulamaya gönderir: üyelere özel, özel ve "
                "yaş sınırlı videolar indirilebilir (çerezler yalnızca bu bilgisayarda tutulur)")),
            ("🔔  Bitiş bildirimi", self._toggle_row(
                "notify_on_complete", "İndirme bitince üstte şerit ve (pencere tepsideyse) Windows bildirimi gösterir")),
            ("🔝  Bitince pencereyi öne getir", self._toggle_row(
                "notify_raise_window", "Tüm indirmeler bitince uygulama penceresini öne getirir")),
            ("🔁  Otomatik yeniden deneme", self._toggle_row(
                "auto_retry", "Geçici hatalarda (bağlantı kopması, 429/5xx) 5 ve 15 dakika sonra (en fazla 2 kez) kendiliğinden dener")),
            ("⚡  Bağlantı sayısını ayarla", self._toggle_row(
                "auto_tune_connections", "Her siteyle ölçülen hıza göre kullanılan bağlantı sayısını kendiliğinden seçer "
                                         "(yukarıdaki sayı başlangıçtır; hız artıyorsa 2 katına kadar çıkabilir)")),
            ("🆕  Uygulama güncellemeleri", self._toggle_row(
                "auto_update_app", "Yeni sürüm yayınlanınca açılışta ve birkaç saatte bir haber verir; "
                                   "güncelleme sen onaylayınca iner ve kurulur")),
            ("Sürüm", self._build_appupdate_row()),
            ("📦  Arşivleri otomatik aç", self._toggle_row(
                "auto_extract", "Biten zip, rar, 7z, tar arşivlerini kendi adlı klasöre çıkarır "
                                "(rar ve 7z için 7-Zip ya da güncel Windows 11 gerekir)")),
            ("📈  Canlı hız grafiği", self._toggle_row(
                "show_speed_graph", "Durum çubuğunda anlık toplam indirme hızını çizer")),
            ("🎨  Tema", self._build_theme_row()),
            ("🪟  Yüzen mini kutu", self._toggle_row(
                "floating_box", "Ekranda küçük, hep üstte bir kutu: bağlantıyı üstüne sürükleyip bırakınca indirme başlar")),
            ("🌍  Proxy", self._build_proxy_row()),
            ("🔁  Otomatik dönüştürme", self._build_convert_row()),
            ("📎  Dönüştürünce orijinali koru", self._toggle_row(
                "convert_keep_original", "Dönüştürülen dosyanın ilk hâli de silinmez")),
            ("🧾  Doğrulama dosyası", self._toggle_row(
                "auto_verify_hash", "Sunucuda dosyanın yanında .sha256 / .sha1 / .md5 varsa indirme bitince otomatik doğrulanır")),
            ("💾  Yedekleme", self._build_backup_row()),
            ("🐞  Hata raporu", self._build_report_row()),
            ("🌐  Dil / Language", self._build_language_row()),
            ("Tarayıcı eklentisi kurulumu", self._build_browser_row()),
            ("🩺  Tanı aracı", self._build_diag_row()),
            ("Klasör ve ad kuralları", self._build_rules_row()),
            ("ffmpeg", self._build_ffmpeg_row()),
            ("Tarayıcı eklentisi", self._build_extension_row()),
        )
        pages: dict[str, list] = {}
        for text, field in rows:
            pages.setdefault(self._category_of(text), []).append((text, field))
        self.tabs = QTabWidget()
        for name in ("Genel", "Arayüz", "YouTube", "Tarayıcı", "Araçlar ve güncelleme"):
            items = pages.get(name)
            if not items:
                continue
            page = QWidget()
            page.setObjectName("listHost")
            page_grid = QGridLayout(page)
            page_grid.setContentsMargins(4, 14, 4, 4)
            page_grid.setHorizontalSpacing(20)
            page_grid.setVerticalSpacing(14)
            page_grid.setColumnStretch(1, 1)
            for i, (text, field) in enumerate(items):
                label = QLabel(text)
                label.setObjectName("sectionTitle")
                page_grid.addWidget(label, i, 0, Qt.AlignVCenter | Qt.AlignLeft)
                page_grid.addLayout(field, i, 1)
            page_grid.setRowStretch(len(items), 1)
            self.tabs.addTab(page, name)

        hint = QLabel(
            "Klasör ve bağlantı sayısı yeni başlayan indirmelerde geçerli olur. "
            "Hız sınırı hemen uygulanır ve tüm indirmelerin toplamını kapsar."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)

        runtime = youtube.js_runtime_status()
        self.runtime_label = QLabel(
            f"JavaScript ortamı: {runtime} bulundu" if runtime
            else "JavaScript ortamı bulunamadı. YouTube için Deno önerilir:  winget install DenoLand.Deno")
        self.runtime_label.setObjectName("hint")
        self.runtime_label.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("Kapat")
        buttons.rejected.connect(self.reject)

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 24, 26, 20)
        root.setSpacing(18)
        title = QLabel("Ayarlar")
        title.setObjectName("appTitle")
        root.addWidget(title)
        root.addWidget(self.tabs, 1)
        root.addWidget(hint)
        root.addWidget(self.runtime_label)
        version_label = QLabel(f"İnternet Download Assistant v{__version__}")
        version_label.setObjectName("hint")
        root.addWidget(version_label)
        root.addSpacing(2)
        root.addWidget(buttons)
        self._wire_live()

    # ------------------------------------------------------------------ yardımcılar
    @staticmethod
    def _make_spin(low: int, high: int, value: int, step: int = 1,
                   suffix: str = "", special: str = "") -> QSpinBox:
        spin = QSpinBox()
        spin.setRange(low, high)
        spin.setSingleStep(step)
        spin.setFixedWidth(150)
        if suffix:
            spin.setSuffix(suffix)
        if special:
            spin.setSpecialValueText(special)
        spin.setValue(value)
        return spin

    @staticmethod
    def _stepper(spin: QSpinBox) -> QHBoxLayout:
        """Sayı kutusunun yanına − / + düğmeleri ekler (yerleşik oklar koyu temada görünmez)."""
        def button(icon_name: str, direction: int) -> QToolButton:
            b = QToolButton()
            b.setObjectName("iconBtn")
            b.setIcon(get_icon(icon_name, size=18))
            b.setIconSize(QSize(18, 18))
            b.setFixedSize(38, 38)
            b.setCursor(Qt.PointingHandCursor)
            b.setAutoRepeat(True)
            b.clicked.connect(lambda: spin.setValue(spin.value() + direction * spin.singleStep()))
            return b

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(spin)
        row.addWidget(button("minus", -1))
        row.addWidget(button("plus", +1))
        row.addStretch(1)
        return row

    def _toggle_row(self, key: str, tooltip: str) -> QHBoxLayout:
        """Açık/Kapalı düğmeli bir ayar satırı (değer Kaydet'e basınca yazılır)."""
        chip = ToggleSwitch()
        chip.setToolTip(tooltip)
        chip.toggled.connect(lambda on, c=chip: c.setText("Açık" if on else "Kapalı"))
        chip.setChecked(bool(self._config.get(key)))
        chip.setText("Açık" if chip.isChecked() else "Kapalı")
        self.toggles[key] = chip
        row = QHBoxLayout()
        row.addWidget(chip)
        row.addStretch(1)
        return row

    def _build_rules_row(self) -> QHBoxLayout:
        from gui.components.rules_dialog import RulesDialog
        button = QPushButton(" Düzenle…")
        button.setObjectName("ghost")
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip("Uzantıya ya da siteye göre klasör, dosya adı temizleme")
        button.clicked.connect(lambda: self._open_child(RulesDialog(self._config, self)))
        row = QHBoxLayout()
        row.addWidget(button)
        row.addStretch(1)
        return row

    def _build_ffmpeg_row(self) -> QHBoxLayout:
        self.ffmpeg_label = QLabel("sürüm: kontrol ediliyor…")
        self.ffmpeg_label.setObjectName("hint")
        self.ffmpeg_btn = QPushButton(" Güncelle")
        self.ffmpeg_btn.setObjectName("ghost")
        self.ffmpeg_btn.setCursor(Qt.PointingHandCursor)
        self.ffmpeg_btn.setToolTip("Yeni bir ffmpeg indirir (~90 MB). Nadiren gerekir; otomatik güncellenmez.")
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(self.ffmpeg_label, 1)
        row.addWidget(self.ffmpeg_btn)
        self._ffmpeg_text.connect(self.ffmpeg_label.setText)
        self._ffmpeg_done.connect(self._on_ffmpeg_done)
        self.ffmpeg_btn.clicked.connect(self._start_ffmpeg_update)
        self._run_async(lambda: self._ffmpeg_text.emit(f"sürüm: {self._ffmpeg_version_fn() or 'bulunamadı'}"))
        return row

    def _start_ffmpeg_update(self) -> None:
        self.ffmpeg_btn.setEnabled(False)
        self.ffmpeg_label.setText("indiriliyor…")

        def progress(done: int, total: int) -> None:
            text = f"indiriliyor… {done / 1e6:.0f} / {total / 1e6:.0f} MB" if total else f"indiriliyor… {done / 1e6:.0f} MB"
            self._ffmpeg_text.emit(text)

        self._run_async(lambda: self._ffmpeg_done.emit(*self._ffmpeg_update_fn(progress=progress)))

    def _on_ffmpeg_done(self, ok: bool, message: str) -> None:
        self.ffmpeg_btn.setEnabled(True)
        self.ffmpeg_label.setText(message)

    @staticmethod
    def _category_of(text: str) -> str:
        """Ayar satırının hangi sekmede görüneceği (başlıktaki anahtar sözcüğe göre; bilinmeyen = Genel)."""
        table = (
            ("YouTube", ("yt-dlp", "önceden analiz", "oturum çerezleri")),
            ("Tarayıcı", ("Sunucuyu otomatik aç", "Tarayıcı eklentisi")),
            ("Araçlar ve güncelleme", ("Araçları otomatik güncelle", "ffmpeg", "Uygulama güncellemeleri", "Sürüm",
                                       "Tanı aracı", "Yedekleme", "Hata raporu")),
            ("Arayüz", ("Pano takibi", "Bitiş bildirimi", "Bitince pencereyi", "Tehlikeli dosya", "hız grafiği",
                        "Windows ile başlat", "Dil / Language", "Tema", "Yüzen mini kutu")),
        )
        for category, keys in table:
            if any(key in text for key in keys):
                return category
        return "Genel"

    def _build_language_row(self) -> QHBoxLayout:
        self.lang_combo = QComboBox()
        self.lang_combo.addItem("Sistem dili (otomatik)", "auto")
        self.lang_combo.addItem("Türkçe", "tr")
        self.lang_combo.addItem("English", "en")
        self.lang_combo.setCurrentIndex(max(0, self.lang_combo.findData(str(self._config.get("language") or "auto"))))
        self.lang_combo.setStyleSheet(
            f"QComboBox {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; border-radius: 10px; "
            f"padding: 8px 12px; min-width: 220px; color: {theme.TEXT}; }}"
            f"QComboBox QAbstractItemView {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; "
            f"selection-background-color: {theme.SURFACE_2}; color: {theme.TEXT}; outline: none; }}")
        report = QPushButton(" Eksik çevirileri kaydet")
        report.setObjectName("ghost")
        report.setCursor(Qt.PointingHandCursor)
        report.setToolTip("Çevrilmemiş metinlerin listesini tanı raporu klasörüne yazar")

        def save_missing() -> None:
            from core import diagnostics, i18n
            path = diagnostics.report_dir(self._config) / "IDA-ceviri-eksik.txt"
            path.write_text("\n".join(i18n.missing()) or "(eksik yok)", encoding="utf-8-sig")
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

        report.clicked.connect(save_missing)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(self.lang_combo)
        row.addStretch(1)
        row.addWidget(report)
        return row

    def _combo(self, width: int) -> QComboBox:
        box = QComboBox()
        box.setStyleSheet(theme.combo_qss(width))
        return box

    def _build_theme_row(self) -> QHBoxLayout:
        self.theme_combo = self._combo(120)
        for label, code in (("Koyu", "dark"), ("Açık", "light")):
            self.theme_combo.addItem(label, code)
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(str(self._config.get("theme")))))
        self.accent_combo = self._combo(120)
        for label, code in (("Mor", "purple"), ("Mavi", "blue"), ("Pembe", "pink"), ("Yeşil", "green"),
                            ("Turuncu", "orange")):
            self.accent_combo.addItem(label, code)
        self.accent_combo.setCurrentIndex(max(0, self.accent_combo.findData(str(self._config.get("accent")))))
        self.theme_note = QLabel("Yeniden başlatınca uygulanır")
        self.theme_note.setObjectName("hint")
        self.theme_note.hide()
        self._theme_at_open = (str(self._config.get("theme")), str(self._config.get("accent")))
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(self.theme_combo)
        row.addWidget(self.accent_combo)
        row.addWidget(self.theme_note)
        row.addStretch(1)
        return row

    def _build_proxy_row(self) -> QVBoxLayout:
        self.proxy_edit = QLineEdit(str(self._config.get("proxy_url") or ""))
        self.proxy_edit.setPlaceholderText("boşsa sistem ayarı  ·  http://127.0.0.1:8080  ya da  socks5://127.0.0.1:1080")
        self.proxy_hint = QLabel("İndirmeler ve yt-dlp bu proxy üzerinden gider (kullanıcı adı/şifre: http://kullanici:sifre@host:port).")
        self.proxy_hint.setObjectName("hint")
        self.proxy_hint.setWordWrap(True)
        col = QVBoxLayout()
        col.setSpacing(4)
        col.addWidget(self.proxy_edit)
        col.addWidget(self.proxy_hint)
        return col

    def _build_convert_row(self) -> QHBoxLayout:
        self.convert_combo = self._combo(190)
        self.convert_combo.addItem("Kapalı", "")
        for code, label in convert.CHOICES[1:]:
            self.convert_combo.addItem(label, code)
        self.convert_combo.setCurrentIndex(max(0, self.convert_combo.findData(str(self._config.get("auto_convert") or ""))))
        hint = QLabel("yalnızca video dosyalarına (YouTube dışı) uygulanır · ffmpeg gerekir")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(self.convert_combo)
        row.addWidget(hint, 1)
        return row

    def _build_backup_row(self) -> QHBoxLayout:
        label = QLabel("ayarlar, geçmiş ve istatistikler (kayıtlı kimlik bilgilerini içerebilir: güvenli sakla)")
        label.setObjectName("hint")
        label.setWordWrap(True)
        make = QPushButton(" Yedekle…")
        restore = QPushButton(" Geri yükle…")
        for b in (make, restore):
            b.setObjectName("ghost")
            b.setCursor(Qt.PointingHandCursor)
        make.clicked.connect(self._do_backup)
        restore.clicked.connect(self._do_restore)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(label, 1)
        row.addWidget(make)
        row.addWidget(restore)
        return row

    def _do_backup(self) -> None:
        from datetime import datetime
        from PySide6.QtWidgets import QMessageBox
        start = str(Path.home() / f"IDA-yedek-{datetime.now():%Y%m%d}.zip")
        path, _ = QFileDialog.getSaveFileName(self, "Yedeği kaydet", start, "Zip (*.zip)")
        if not path:
            return
        try:
            names = backup.create(Path(path))
        except (OSError, backup.BackupError) as e:
            QMessageBox.warning(self, "Yedekleme", f"Yedek alınamadı: {e}")
            return
        QMessageBox.information(self, "Yedekleme", f"Yedek kaydedildi ({len(names)} dosya):\n{path}")

    def _do_restore(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        path, _ = QFileDialog.getOpenFileName(self, "Yedeği seç", str(Path.home()), "Zip (*.zip)")
        if not path:
            return
        try:
            backup.stage_restore(Path(path))
        except backup.BackupError as e:
            QMessageBox.warning(self, "Yedekleme", str(e))
            return
        QMessageBox.information(self, "Yedekleme", "Yedek hazırlandı. Uygulamayı kapatıp yeniden açınca uygulanır.")

    def _build_report_row(self) -> QHBoxLayout:
        label = QLabel("sistem bilgisi ve günlük özetiyle GitHub'da hata bildirimi taslağı açar (göndermeden önce sen görürsün)")
        label.setObjectName("hint")
        label.setWordWrap(True)
        button = QPushButton(" Hata bildir…")
        button.setObjectName("ghost")
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(self._do_report)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(label, 1)
        row.addWidget(button)
        return row

    def _do_report(self) -> None:
        from PySide6.QtWidgets import QApplication
        title, full, short = report.manual_report()
        QApplication.clipboard().setText(full)
        QDesktopServices.openUrl(QUrl(report.issue_url(title, short)))

    def _after_save(self, before: dict) -> None:
        now = (str(self._config.get("theme")), str(self._config.get("accent")))
        self.theme_note.setVisible(now != self._theme_at_open)
        text = self.proxy_edit.text().strip()
        bad = bool(text) and not netconf.normalize(text)
        self.proxy_hint.setText("⚠ Geçersiz proxy adresi: http://, https://, socks4:// ya da socks5:// ile başlamalı." if bad
                                else "İndirmeler ve yt-dlp bu proxy üzerinden gider (kullanıcı adı/şifre: http://kullanici:sifre@host:port).")
        self.proxy_hint.setStyleSheet(f"color: {theme.RED};" if bad else "")

    def _build_browser_row(self) -> QHBoxLayout:
        label = QLabel("kurulu tarayıcıları bulur, eklenti sayfasını açar")
        label.setObjectName("hint")
        button = QPushButton(" Tarayıcılara ekle…")
        button.setObjectName("ghost")
        button.setCursor(Qt.PointingHandCursor)

        def run() -> None:
            from gui.components.browser_help_dialog import BrowserHelpDialog
            self._open_child(BrowserHelpDialog(paths.EXTENSION_DIR, str(getattr(self.parent(), "_ext_seen", "") or ""), self))

        button.clicked.connect(run)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(label, 1)
        row.addWidget(button)
        return row

    @staticmethod
    def _alive(widget) -> bool:
        try:
            return widget.isVisible()
        except RuntimeError:
            return False

    def _open_child(self, dialog) -> None:
        """Alt pencereyi (tanı, tarayıcı, kurallar) modalsız açar; aynı türden biri açıksa onu öne getirir."""
        children = self.__dict__.setdefault("_child_dialogs", [])
        children[:] = [d for d in children if self._alive(d)]
        for existing in children:
            if type(existing) is type(dialog):
                dialog.deleteLater()
                existing.raise_()
                existing.activateWindow()
                return
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        DISMISS.register(dialog)
        children.append(dialog)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _build_diag_row(self) -> QHBoxLayout:
        label = QLabel("hız ve uyumluluk testleri, rapor dosyası")
        label.setObjectName("hint")
        button = QPushButton(" Tanı aracını aç…")
        button.setObjectName("ghost")
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip("Çeşitli indirmeleri otomatik dener ve sonuçları bir rapora yazar")

        def run() -> None:
            from gui.components.diagnostics_dialog import DiagnosticsDialog
            self._open_child(DiagnosticsDialog(self._config, self))

        button.clicked.connect(run)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(label, 1)
        row.addWidget(button)
        return row

    def _build_appupdate_row(self) -> QHBoxLayout:
        label = QLabel(f"kurulu sürüm: v{__version__}")
        label.setObjectName("hint")
        button = QPushButton(" Şimdi denetle")
        button.setObjectName("ghost")
        button.setCursor(Qt.PointingHandCursor)

        def run() -> None:
            check = getattr(self.parent(), "check_app_update", None)
            if check is not None:
                check(True)

        button.clicked.connect(run)
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(label, 1)
        row.addWidget(button)
        return row

    def _build_extension_row(self) -> QHBoxLayout:
        version = ""
        try:
            version = json.loads((paths.EXTENSION_DIR / "manifest.json").read_text(encoding="utf-8")).get("version", "")
        except (OSError, ValueError):
            pass
        label = QLabel(f"kurulumla gelen sürüm: {version}" if version else "eklenti klasörü bulunamadı")
        label.setObjectName("hint")
        button = QPushButton(" Klasörü aç")
        button.setObjectName("ghost")
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip("Tarayıcıda chrome://extensions > Geliştirici modu > Paketlenmemiş öğe yükle ile bu klasörü seçin")
        button.setEnabled(paths.EXTENSION_DIR.is_dir())
        button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.EXTENSION_DIR))))
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(label, 1)
        row.addWidget(button)
        return row

    def _build_ytdlp_row(self) -> QHBoxLayout:
        self.ytdlp_label = QLabel("sürüm: kontrol ediliyor…")
        self.ytdlp_label.setObjectName("hint")
        self.ytdlp_update_btn = QPushButton(" Güncelle")
        self.ytdlp_update_btn.setObjectName("ghost")
        self.ytdlp_update_btn.setCursor(Qt.PointingHandCursor)
        self.ytdlp_update_btn.setToolTip("YouTube sık değiştiği için yt-dlp'yi güncel tutmak gerekir")
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(self.ytdlp_label, 1)
        row.addWidget(self.ytdlp_update_btn)
        self._version_ready.connect(lambda v: self.ytdlp_label.setText(f"sürüm: {v}"))
        self._update_done.connect(self._on_update_done)
        self.ytdlp_update_btn.clicked.connect(self._start_update)
        self._run_async(lambda: self._version_ready.emit(self._version_fn()))
        return row

    @staticmethod
    def _run_async(fn) -> None:
        def guarded() -> None:
            try:
                fn()
            except RuntimeError:
                pass          # pencere bu arada kapanmış olabilir
        threading.Thread(target=guarded, daemon=True, name="settings-async").start()

    def _start_update(self) -> None:
        self.ytdlp_update_btn.setEnabled(False)
        self.ytdlp_label.setText("güncelleniyor… (birkaç dakika sürebilir)")
        self._run_async(lambda: self._update_done.emit(*self._update_fn()))

    def _on_update_done(self, ok: bool, message: str) -> None:
        self.ytdlp_update_btn.setEnabled(True)
        self.ytdlp_label.setText(message)
    
    def _set_speed_enabled(self, row: QHBoxLayout, enabled: bool) -> None:
        """Sınırsız seçiliyken sayı kutusu ve − / + düğmeleri pasif olur."""
        self.speed_spin.setEnabled(enabled)
        for index in (1, 2):
            row.itemAt(index).widget().setEnabled(enabled)

    def _browse(self) -> None:
        start = self.dir_edit.text().strip() or str(paths.default_download_dir())
        chosen = QFileDialog.getExistingDirectory(self, "İndirme klasörünü seç", start)
        if chosen:
            self.dir_edit.setText(str(Path(chosen)))

    @property
    def dirty(self) -> bool:
        """Pencere açıkken en az bir ayar gerçekten değişti mi?"""
        return self._dirty

    def _snapshot(self) -> dict:
        keys = ["download_dir", "max_concurrent_downloads", "connections_per_download", "speed_limit_kbps", "language", "proxy_url", "auto_convert", "theme", "accent",
                *self.toggles]
        return {key: self._config.get(key) for key in keys}

    def _wire_live(self) -> None:
        """Her değişiklik kısa bir beklemeden sonra kendiliğinden kaydedilir ve uygulanır."""
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(350)
        self._debounce.timeout.connect(self._save)
        self.dir_edit.editingFinished.connect(self._debounce.start)
        for spin in (self.concurrent_spin, self.connections_spin, self.speed_spin):
            spin.valueChanged.connect(lambda _v: self._debounce.start())
        self.unlimited_chip.toggled.connect(lambda _on: self._debounce.start())
        self.lang_combo.currentIndexChanged.connect(lambda _i: self._debounce.start())
        for combo in (self.theme_combo, self.accent_combo, self.convert_combo):
            combo.currentIndexChanged.connect(lambda _i: self._debounce.start())
        self.proxy_edit.editingFinished.connect(self._debounce.start)
        for chip in self.toggles.values():
            chip.toggled.connect(lambda _on: self._debounce.start())

    def done(self, result: int) -> None:
        if self._debounce.isActive():                  # bekleyen değişiklik varsa kapanmadan önce kaydedilir
            self._debounce.stop()
            self._save()
        super().done(result)

    def _save(self) -> None:
        before = self._snapshot()
        self._config.set("language", str(self.lang_combo.currentData() or "auto"), autosave=False)
        self._config.set("proxy_url", self.proxy_edit.text().strip(), autosave=False)
        self._config.set("auto_convert", str(self.convert_combo.currentData() or ""), autosave=False)
        self._config.set("theme", str(self.theme_combo.currentData() or "dark"), autosave=False)
        self._config.set("accent", str(self.accent_combo.currentData() or "purple"), autosave=False)
        self._config.set("download_dir", self.dir_edit.text().strip(), autosave=False)
        self._config.set("max_concurrent_downloads", self.concurrent_spin.value(), autosave=False)
        self._config.set("connections_per_download", self.connections_spin.value(), autosave=False)
        self._config.set("speed_limit_kbps",
                         0 if self.unlimited_chip.isChecked() else self.speed_spin.value(),
                         autosave=False)
        autostart_before = bool(self._config.get("start_with_windows"))
        for key, chip in self.toggles.items():
            self._config.set(key, chip.isChecked(), autosave=False)
        self._config.save()
        self._after_save(before)
        autostart_after = bool(self._config.get("start_with_windows"))
        if autostart_after != autostart_before:      # kayıt defterine yalnızca ayar değişince dokunulur
            autostart.set_enabled(autostart_after)
        if self._snapshot() != before:
            self._dirty = True
            self.changed.emit()