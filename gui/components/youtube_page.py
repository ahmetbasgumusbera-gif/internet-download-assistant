"""
YouTube sekmesi: linki analiz eder, videonun küçük resmini/başlığını gösterir ve seçenekleri
(çözünürlük, FPS, biçim, ses, ekstralar) görsel olarak sunar.

Sayfa indirmeyi kendisi başlatmaz: kullanıcı "Listeye ekle ve indir"e basınca download_requested
sinyali ile {url, info, selection} sözlüğünü verir; asıl işi ana pencere/yönetici yapar.
"""
from __future__ import annotations

import dataclasses
import threading
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QProgressBar,
    QPushButton, QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

from core import prefetch, youtube
from core.youtube import AnalysisError, PlaylistInfo, Selection, VideoInfo
from gui import theme
from gui.components.playlist_panel import PlaylistPanel
from gui.icons import get_icon
from gui.thumbnails import ThumbnailLoader, cover_crop

_PAGE_IDLE, _PAGE_LOADING, _PAGE_ERROR, _PAGE_RESULT, _PAGE_PLAYLIST = range(5)
_RES_BADGES = {2160: ("4K", theme.PURPLE), 1440: ("2K", theme.PURPLE_SOFT),
               1080: ("Full HD", theme.CYAN), 720: ("HD", theme.CYAN)}
_FAMILY_NAMES = {"h264": "H.264", "vp9": "VP9", "av1": "AV1", "other": "—"}


class _Tile(QPushButton):
    """Seçilebilir karo: başlık, alt bilgi ve isteğe bağlı rozetler."""

    def __init__(self, title: str, sub: str, badges=()):
        super().__init__()
        self.setObjectName("tile")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 12, 8)
        layout.setSpacing(1)
        top = QHBoxLayout()
        top.setSpacing(6)
        name = QLabel(title)
        name.setObjectName("tileTitle")
        top.addWidget(name)
        for text, color in badges:
            badge = QLabel(text)
            badge.setObjectName("tileBadge")
            badge.setStyleSheet(f"color: {color}; background: {_rgba(color, 40)};")
            top.addWidget(badge)
        top.addStretch(1)
        layout.addLayout(top)
        sub_label = QLabel(sub)
        sub_label.setObjectName("tileSub")
        layout.addWidget(sub_label)
        for label in self.findChildren(QLabel):
            label.setAttribute(Qt.WA_TransparentForMouseEvents)


def _rgba(hex_color: str, alpha: int) -> str:
    h = hex_color.lstrip("#")
    return f"rgba({int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)}, {alpha})"


def _clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()


class YouTubePage(QWidget):
    download_requested = Signal(object)    # {"url": str, "info": VideoInfo, "selection": Selection}
    goto_downloads = Signal()
    playlist_download_requested = Signal(object)   # {"info": PlaylistInfo, "entries": [...], "selection": Selection}
    _list_done = Signal(str, object, str)       # (adres, VideoInfo | None, hata): kutudan "Listeye ekle" sırası
    list_failed = Signal(str)
    _analysis_done = Signal(int, object, str)   # (istek no, VideoInfo | None, hata mesajı)

    def __init__(self, config, analyze_fn: Optional[Callable] = None,
                 js_runtime_fn: Optional[Callable] = None, playlist_fn: Optional[Callable] = None, parent=None):
        super().__init__(parent)
        self._config = config
        self._playlist_fn = playlist_fn or youtube.analyze_playlist
        self._analyze_fn = analyze_fn or prefetch.analyze_cached
        self._js_runtime_fn = js_runtime_fn or youtube.js_runtime_status

        self._info: Optional[VideoInfo] = None
        self._sel = Selection()
        self._token = 0
        self._last_url = ""
        self._thumb_url = ""
        self._updating = False

        self._auto_list = False
        self._build_ui()
        self._analysis_done.connect(self._on_analysis_done)
        self._list_jobs: list = []
        self._queued_urls: set = set()
        self._list_busy = False
        self._list_done.connect(self._on_list_done)

    # ================================================================== kurulum
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 14, 0, 0)
        root.setSpacing(12)

        # ---- link satırı
        self.link_edit = QLineEdit()
        self.link_edit.setObjectName("linkField")
        self.link_edit.setPlaceholderText("YouTube bağlantısını buraya yapıştırın")
        self.link_edit.setClearButtonEnabled(True)
        self.analyze_btn = QPushButton("  Analiz et")
        self.analyze_btn.setObjectName("primary")
        self.analyze_btn.setFixedWidth(150)
        self.analyze_btn.setCursor(Qt.PointingHandCursor)
        self.analyze_btn.setIcon(get_icon("retry", color=theme.BG, hover=theme.BG, disabled=theme.TEXT_DIM, size=18))
        self.auto_chip = QPushButton("Otomatik indir")
        self.auto_chip.setObjectName("chip")
        self.auto_chip.setCheckable(True)
        self.auto_chip.setCursor(Qt.PointingHandCursor)
        self.auto_chip.setToolTip("Açıksa: linki yapıştırınca varsayılan ayarla (1080p, MP4, küçük resim gömülü) "
                                  "hiçbir şeye basmadan indirilir")
        self.auto_chip.setChecked(bool(self._config.get("youtube_auto_download")))
        link_row = QHBoxLayout()
        link_row.setSpacing(12)
        link_row.addWidget(self.link_edit, 1)
        link_row.addWidget(self.auto_chip)
        link_row.addWidget(self.analyze_btn)
        root.addLayout(link_row)

        self.runtime_banner = QLabel(
            "En iyi uyumluluk için JavaScript çalışma ortamı (Deno) gerekli. "
            "Kurulum:  winget install DenoLand.Deno")
        self.runtime_banner.setObjectName("bannerWarn")
        self.runtime_banner.setWordWrap(True)
        self.runtime_banner.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.runtime_banner)

        # eklendi bildirimi: kaydırma alanının dışında, böylece sayfa kaydırılmış olsa da görünür
        self.added_bar = QFrame()
        added = QHBoxLayout(self.added_bar)
        added.setContentsMargins(0, 0, 0, 0)
        self.added_label = QLabel("")
        self.added_label.setObjectName("bannerOk")
        self.added_label.setWordWrap(True)
        self.goto_btn = QPushButton("İndirmelere git")
        self.goto_btn.setObjectName("ghost")
        self.goto_btn.setCursor(Qt.PointingHandCursor)
        added.addWidget(self.added_label, 1)
        added.addWidget(self.goto_btn)
        self.goto_btn.clicked.connect(self.goto_downloads.emit)
        self.added_bar.hide()
        root.addWidget(self.added_bar)

        # ---- durum sayfaları
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_idle())
        self.stack.addWidget(self._build_loading())
        self.stack.addWidget(self._build_error())
        self.stack.addWidget(self._build_result())
        self.playlist_panel = PlaylistPanel()
        self.playlist_panel.download_requested.connect(self._on_playlist_requested)
        self.playlist_panel.cancelled.connect(lambda: self.clear())
        self.stack.addWidget(self.playlist_panel)
        root.addWidget(self.stack, 1)

        # ---- alt özet çubuğu (yalnızca sonuç varken)
        self.summary_bar = QFrame()
        self.summary_bar.setObjectName("summaryBar")
        bar = QHBoxLayout(self.summary_bar)
        bar.setContentsMargins(18, 12, 12, 12)
        self.summary_label = QLabel("")
        self.summary_label.setObjectName("summaryText")
        self.add_btn = QPushButton("  Videoyu indir")
        self.add_btn.setObjectName("primary")
        self.add_btn.setMinimumWidth(230)
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.setIcon(get_icon("download", color=theme.BG, hover=theme.BG, disabled=theme.TEXT_DIM, size=20))
        self.cancel_btn = QPushButton("Vazgeç")
        self.cancel_btn.setObjectName("ghost")
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.setToolTip("Analiz sonucunu temizle")
        self.list_btn = QPushButton("Listeye ekle")
        self.list_btn.setObjectName("ghost")
        self.list_btn.setCursor(Qt.PointingHandCursor)
        self.list_btn.setToolTip("Hemen indirmez: Listeler sekmesine ekler")
        bar.addWidget(self.summary_label, 1)
        bar.addWidget(self.cancel_btn)
        bar.addWidget(self.list_btn)
        bar.addWidget(self.add_btn)
        root.addWidget(self.summary_bar)

        self.link_edit.textChanged.connect(self._on_text_changed)
        self.link_edit.returnPressed.connect(lambda: self.analyze(self.link_edit.text()))
        self.analyze_btn.clicked.connect(lambda: self.analyze(self.link_edit.text()))
        self.auto_chip.toggled.connect(lambda on: self._config.set("youtube_auto_download", bool(on)))
        self.add_btn.clicked.connect(self._on_add_clicked)
        self.list_btn.clicked.connect(self._on_list_clicked)
        self.cancel_btn.clicked.connect(lambda: self.clear())

        self.refresh_runtime_banner()
        self._show_page(_PAGE_IDLE)

    def _build_idle(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(6)
        icon = QLabel()
        icon.setPixmap(get_icon("download", color=theme.BORDER_HOVER, size=44).pixmap(44, 44))
        icon.setAlignment(Qt.AlignCenter)
        title = QLabel("Bir YouTube bağlantısı yapıştırın")
        title.setObjectName("emptyTitle")
        title.setAlignment(Qt.AlignCenter)
        hint = QLabel("youtube.com, youtu.be, shorts ve music.youtube.com bağlantıları desteklenir.\n"
                      "Yapıştırınca video otomatik analiz edilir.")
        hint.setObjectName("emptyHint")
        hint.setAlignment(Qt.AlignCenter)
        for w in (icon, title, hint):
            lay.addWidget(w)
        return page

    def _build_loading(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(12)
        self.loading_label = QLabel("Video analiz ediliyor…")
        self.loading_label.setObjectName("emptyTitle")
        self.loading_label.setAlignment(Qt.AlignCenter)
        bar = QProgressBar()
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        bar.setFixedWidth(260)
        lay.addWidget(self.loading_label)
        lay.addWidget(bar, 0, Qt.AlignCenter)
        return page

    def _build_error(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setAlignment(Qt.AlignTop)
        lay.setSpacing(12)
        self.error_label = QLabel("")
        self.error_label.setObjectName("bannerWarn")
        self.error_label.setWordWrap(True)
        self.retry_btn = QPushButton("Tekrar dene")
        self.retry_btn.setObjectName("ghost")
        self.retry_btn.setCursor(Qt.PointingHandCursor)
        self.playlist_btn = QPushButton("İlk videoyu analiz et")
        self.playlist_btn.setObjectName("ghost")
        self.playlist_btn.setCursor(Qt.PointingHandCursor)
        self.playlist_all_btn = QPushButton("Tüm listeyi indir…")
        self.playlist_all_btn.setObjectName("primary")
        self.playlist_all_btn.setCursor(Qt.PointingHandCursor)
        row = QHBoxLayout()
        row.addWidget(self.retry_btn)
        row.addWidget(self.playlist_all_btn)
        row.addWidget(self.playlist_btn)
        row.addStretch(1)
        lay.addWidget(self.error_label)
        lay.addLayout(row)
        self.retry_btn.clicked.connect(lambda: self.analyze(self._last_url or self.link_edit.text(), force=True))
        self.playlist_btn.clicked.connect(lambda: self.analyze(self._last_url, playlist_first=True, force=True))
        self.playlist_all_btn.clicked.connect(lambda: self.analyze_playlist(self._last_url))
        return page

    def _build_result(self) -> QWidget:
        content = QWidget()
        content.setObjectName("listHost")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(10)

        # video kartı
        card = QFrame()
        card.setObjectName("card")
        card.setAttribute(Qt.WA_StyledBackground, True)
        card_lay = QHBoxLayout(card)
        card_lay.setContentsMargins(12, 12, 16, 12)
        card_lay.setSpacing(16)
        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(200, 112)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setStyleSheet(f"background: {theme.SURFACE_2}; border-radius: 12px; color: {theme.TEXT_DIM};")
        info_col = QVBoxLayout()
        info_col.setSpacing(6)
        self.title_label = QLabel("")
        self.title_label.setObjectName("videoTitle")
        self.title_label.setWordWrap(True)
        self.meta_label = QLabel("")
        self.meta_label.setObjectName("videoMeta")
        self.badges_row = QHBoxLayout()
        self.badges_row.setSpacing(6)
        info_col.addWidget(self.title_label)
        info_col.addWidget(self.meta_label)
        info_col.addStretch(1)
        info_col.addLayout(self.badges_row)
        card_lay.addWidget(self.thumb_label)
        card_lay.addLayout(info_col, 1)
        lay.addWidget(card)

        # ne indirilecek
        lay.addWidget(self._section("Ne indirmek istiyorsun?"))
        self.mode_buttons: dict[str, QPushButton] = {}
        mode_row = QHBoxLayout()
        mode_row.setSpacing(0)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for i, (key, text) in enumerate((("video", "Video + ses"), ("audio", "Sadece ses"), ("video_only", "Sessiz video"))):
            btn = self._seg_button(text, i, 3)
            btn.clicked.connect(lambda _=False, k=key: self._set_mode(k))
            group.addButton(btn)
            mode_row.addWidget(btn)
            self.mode_buttons[key] = btn
        mode_row.addStretch(1)
        lay.addLayout(mode_row)

        # karolar
        self.tiles_title = self._section("Çözünürlük")
        lay.addWidget(self.tiles_title)
        self.tiles_host = QWidget()
        self.tiles_grid = QGridLayout(self.tiles_host)
        self.tiles_grid.setContentsMargins(0, 0, 0, 0)
        self.tiles_grid.setSpacing(10)
        lay.addWidget(self.tiles_host)
        self.audio_note = QLabel("YouTube sesi zaten sıkıştırılmış gelir: MP3 320 kbps kaliteyi artırmaz, "
                                 "yalnızca dosyayı büyütür.")
        self.audio_note.setObjectName("hint")
        self.audio_note.setWordWrap(True)
        lay.addWidget(self.audio_note)

        # fps / kapsayıcı / codec
        self.options_host = QWidget()
        opts = QHBoxLayout(self.options_host)
        opts.setContentsMargins(0, 0, 0, 0)
        opts.setSpacing(26)
        self.fps_buttons = self._seg_group(opts, "Kare hızı", [("30", "30 FPS"), ("60", "60 FPS")], self._set_fps)
        self.container_buttons = self._seg_group(
            opts, "Kapsayıcı", [("mp4", "MP4"), ("mkv", "MKV"), ("webm", "WEBM")], self._set_container)
        self.codec_buttons = self._seg_group(
            opts, "Codec", [("compat", "Uyumlu (H.264)"), ("efficient", "Verimli (AV1)")], self._set_codec)
        opts.addStretch(1)
        lay.addWidget(self.options_host)
        self.codec_hint = QLabel("")
        self.codec_hint.setObjectName("hint")
        lay.addWidget(self.codec_hint)

        self.lang_host = QWidget()
        lang_row = QVBoxLayout(self.lang_host)
        lang_row.setContentsMargins(0, 0, 0, 0)
        lang_row.setSpacing(6)
        lang_row.addWidget(self._section("Ses dili"))
        self.lang_combo = QComboBox()
        self.lang_combo.setStyleSheet(
            f"QComboBox {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; border-radius: 10px; "
            f"padding: 8px 12px; min-width: 260px; color: {theme.TEXT}; }}"
            f"QComboBox QAbstractItemView {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; "
            f"selection-background-color: {theme.SURFACE_2}; color: {theme.TEXT}; outline: none; }}")
        lang_row.addWidget(self.lang_combo, 0, Qt.AlignLeft)
        self.lang_host.hide()
        lay.addWidget(self.lang_host)
        self.lang_combo.currentIndexChanged.connect(self._on_lang)

        # ekstralar
        lay.addWidget(self._section("Ekstralar"))
        extras = QHBoxLayout()
        extras.setSpacing(8)
        self.thumb_chip = self._chip("Küçük resmi göm")
        self.chapters_chip = self._chip("Bölümleri koru")
        self.subs_chip = QPushButton("Altyazı: kapalı")
        self.subs_chip.setObjectName("chip")
        self.subs_chip.setCursor(Qt.PointingHandCursor)
        self.range_chip = self._chip("Zaman aralığı")
        for chip in (self.thumb_chip, self.chapters_chip, self.subs_chip, self.range_chip):
            extras.addWidget(chip)
        extras.addStretch(1)
        lay.addLayout(extras)

        self.range_host = QWidget()
        rng = QHBoxLayout(self.range_host)
        rng.setContentsMargins(0, 0, 0, 0)
        rng.setSpacing(10)
        self.start_edit = QLineEdit()
        self.start_edit.setObjectName("linkField")
        self.start_edit.setPlaceholderText("Başlangıç (örn. 1:30)")
        self.end_edit = QLineEdit()
        self.end_edit.setObjectName("linkField")
        self.end_edit.setPlaceholderText("Bitiş (örn. 3:45, boşsa sona kadar)")
        self.range_error = QLabel("")
        self.range_error.setObjectName("hint")
        self.range_error.setStyleSheet(f"color: {theme.RED};")
        rng.addWidget(self.start_edit, 1)
        rng.addWidget(self.end_edit, 1)
        rng.addWidget(self.range_error, 1)
        self.range_host.hide()
        lay.addWidget(self.range_host)
        lay.addStretch(1)

        self.thumb_chip.toggled.connect(lambda on: self._set_attr("embed_thumbnail", on))
        self.chapters_chip.toggled.connect(lambda on: self._set_attr("chapters", on))
        self.range_chip.toggled.connect(self._on_range_toggled)
        self.subs_chip.clicked.connect(self._show_subs_menu)
        self.start_edit.textChanged.connect(self._on_range_edited)
        self.end_edit.textChanged.connect(self._on_range_edited)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        return scroll

    # ---- küçük kurucular
    @staticmethod
    def _section(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionLabel")
        return label

    @staticmethod
    def _chip(text: str) -> QPushButton:
        chip = QPushButton(text)
        chip.setObjectName("chip")
        chip.setCheckable(True)
        chip.setCursor(Qt.PointingHandCursor)
        return chip

    @staticmethod
    def _seg_button(text: str, index: int, count: int) -> QPushButton:
        btn = QPushButton(text)
        btn.setObjectName("seg")
        btn.setCheckable(True)
        btn.setCursor(Qt.PointingHandCursor)
        radius = ""
        if index == 0:
            radius += "border-top-left-radius: 10px; border-bottom-left-radius: 10px;"
        if index == count - 1:
            radius += "border-top-right-radius: 10px; border-bottom-right-radius: 10px;"
        btn.setStyleSheet(f"QPushButton#seg {{ {radius} {'border-left: none;' if index else ''} }}")
        return btn

    def _seg_group(self, parent_layout, title: str, items, handler) -> dict[str, QPushButton]:
        column = QVBoxLayout()
        column.setSpacing(6)
        column.addWidget(self._section(title))
        row = QHBoxLayout()
        row.setSpacing(0)
        group = QButtonGroup(self)
        group.setExclusive(True)
        buttons: dict[str, QPushButton] = {}
        for i, (key, text) in enumerate(items):
            btn = self._seg_button(text, i, len(items))
            btn.clicked.connect(lambda _=False, k=key: handler(k))
            group.addButton(btn)
            row.addWidget(btn)
            buttons[key] = btn
        row.addStretch(1)
        column.addLayout(row)
        parent_layout.addLayout(column)
        return buttons

    # ================================================================== genel arayüz
    def refresh_runtime_banner(self) -> None:
        self.runtime_banner.setVisible(self._js_runtime_fn() is None)

    def analyze(self, url: str, playlist_first: bool = False, force: bool = False, auto_list: bool = False) -> None:
        self._auto_list = bool(auto_list)
        """Linki analiz eder (arka planda). Aynı link zaten analiz edildiyse force olmadan tekrar etmez."""
        url = (url or "").strip()
        if not url:
            return
        if self.link_edit.text().strip() != url:
            self.link_edit.blockSignals(True)
            self.link_edit.setText(url)
            self.link_edit.blockSignals(False)
        if not force and url == self._last_url and self._info is not None and not playlist_first:
            self._show_page(_PAGE_RESULT)
            return

        self._last_url = url
        if not youtube.is_youtube_url(url):
            self._show_error("Bu bir YouTube bağlantısı değil. Diğer dosyalar için İndirmeler sekmesini kullanın.")
            return
        if youtube.is_playlist_only(url) and not playlist_first:
            self._show_error("Bu bir oynatma listesi bağlantısı. Tüm listeyi seçerek indirebilir "
                             "ya da yalnızca ilk videosunu analiz edebilirsiniz.", playlist=True)
            return

        self._token += 1
        token = self._token
        self._info = None
        self.analyze_btn.setEnabled(False)
        self.loading_label.setText("Video analiz ediliyor…")
        self._show_page(_PAGE_LOADING)

        def work() -> None:
            info, error = None, ""
            try:
                info = self._analyze_fn(url, playlist_first=playlist_first)
            except AnalysisError as e:
                error = str(e)
            except Exception as e:  # beklenmeyen: arayüz asla donmasın
                error = f"Analiz başarısız: {e}"
            try:
                self._analysis_done.emit(token, info, error)
            except RuntimeError:
                pass   # sayfa kapanırken

        threading.Thread(target=work, daemon=True, name="yt-analyze").start()

    def add_to_list(self, url: str) -> None:
        """Tarayıcı kutusundaki "Listeye ekle": video sırayla, varsayılan ayarla listeye eklenir (art arda basılırsa kaybolmaz)."""
        url = (url or "").strip()
        if not url or url in self._queued_urls:
            return                                     # aynı video zaten sırada
        self._queued_urls.add(url)
        self._list_jobs.append(url)
        self._pump_list()

    def _pump_list(self) -> None:
        if self._list_busy or not self._list_jobs:
            return
        self._list_busy = True
        url = self._list_jobs.pop(0)

        def work() -> None:
            info, error = None, ""
            try:
                info = self._analyze_fn(url, playlist_first=False)
            except AnalysisError as e:
                error = str(e)
            except Exception as e:
                error = f"Analiz başarısız: {e}"
            try:
                self._list_done.emit(url, info, error)
            except RuntimeError:
                pass

        threading.Thread(target=work, daemon=True, name="yt-list").start()

    def _on_list_done(self, url: str, info, error: str) -> None:
        self._list_busy = False
        self._queued_urls.discard(url)
        if info is None or isinstance(info, PlaylistInfo):
            self.list_failed.emit(error or "Video analiz edilemedi.")
        else:
            self.download_requested.emit({"url": info.url or url, "info": info,
                                          "selection": youtube.default_selection(info), "listed": True})
        self._pump_list()

    def analyze_playlist(self, url: str) -> None:
        """Oynatma listesini (video başlıklarıyla) analiz eder; sonuç liste panelinde seçilir."""
        url = (url or "").strip()
        if not url:
            return
        if self.link_edit.text().strip() != url:
            self.link_edit.blockSignals(True)
            self.link_edit.setText(url)
            self.link_edit.blockSignals(False)
        self._last_url = url
        if not youtube.is_youtube_url(url) or not youtube.playlist_id(url):
            self._show_error("Bu bir YouTube oynatma listesi bağlantısı değil.")
            return

        self._token += 1
        token = self._token
        self._info = None
        self.analyze_btn.setEnabled(False)
        self.loading_label.setText("Oynatma listesi analiz ediliyor…")
        self._show_page(_PAGE_LOADING)

        def work() -> None:
            info, error = None, ""
            try:
                info = self._playlist_fn(url)
            except AnalysisError as e:
                error = str(e)
            except Exception as e:  # beklenmeyen: arayüz asla donmasın
                error = f"Analiz başarısız: {e}"
            try:
                self._analysis_done.emit(token, info, error)
            except RuntimeError:
                pass   # sayfa kapanırken

        threading.Thread(target=work, daemon=True, name="yt-playlist").start()

    @property
    def selection(self) -> Selection:
        return self._sel

    @property
    def info(self) -> Optional[VideoInfo]:
        return self._info

    # ================================================================== analiz sonucu
    def _on_text_changed(self, text: str) -> None:
        text = text.strip()
        if youtube.is_youtube_url(text) and text != self._last_url:
            QTimer.singleShot(0, lambda t=text: self.analyze(t))   # yapıştırınca otomatik analiz

    def _on_analysis_done(self, token: int, info, error: str) -> None:
        if token != self._token:
            return          # eski bir istek: yeni analiz başladıysa yok say
        self.analyze_btn.setEnabled(True)
        if info is None:
            self._show_error(error or "Analiz başarısız.")
            return
        if isinstance(info, PlaylistInfo):
            self.playlist_panel.set_info(info)
            self.added_bar.hide()
            self._show_page(_PAGE_PLAYLIST)
            return
        self._info = info
        self._sel = youtube.default_selection(info)
        self._populate_card(info)
        self.added_bar.hide()
        self._rebuild()
        self._show_page(_PAGE_RESULT)
        if self._auto_list:
            self._auto_list = False
            self._emit_download(auto=True, listed=True)
        elif self.auto_chip.isChecked():
            self._emit_download(auto=True)

    def _populate_card(self, info: VideoInfo) -> None:
        self.title_label.setText(info.title)
        bits = [b for b in (info.channel, self._views(info.view_count), info.upload_date) if b]
        self.meta_label.setText("   ·   ".join(bits))
        self._set_badges(info)
        self._set_thumbnail(info)

    @staticmethod
    def _views(count: Optional[int]) -> str:
        if not count:
            return ""
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f} Mn görüntüleme".replace(".", ",")
        if count >= 1_000:
            return f"{count / 1_000:.0f} B görüntüleme"
        return f"{count} görüntüleme"

    def _set_badges(self, info: VideoInfo) -> None:
        _clear_layout(self.badges_row)
        heights = youtube.available_heights(info)
        badges = []
        if heights:
            top = heights[0]
            badges.append((_RES_BADGES.get(top, (f"{top}p", theme.TEXT_MUTED))[0], theme.PURPLE))
            if 60 in youtube.fps_options(info, top) or any(60 in youtube.fps_options(info, h) for h in heights):
                badges.append(("60 FPS", theme.CYAN))
            if any(youtube.has_hdr(info, h) for h in heights):
                badges.append(("HDR", theme.AMBER))
        if info.subtitle_langs:
            badges.append(("Altyazı var", theme.GREEN))
        if info.has_chapters:
            badges.append(("Bölümler", theme.TEXT_MUTED))
        if info.from_playlist:
            badges.append(("Listenin ilk videosu", theme.AMBER))
        for text, color in badges:
            label = QLabel(text)
            label.setObjectName("badge")
            label.setStyleSheet(f"QLabel#badge {{ color: {color}; background: {_rgba(color, 34)}; "
                                f"border-radius: 9px; padding: 3px 10px; font-size: 11px; font-weight: 600; }}")
            self.badges_row.addWidget(label)
        self.badges_row.addStretch(1)

    def _set_thumbnail(self, info: VideoInfo) -> None:
        self._thumb_url = info.thumbnail_url
        self.thumb_label.clear()
        self.thumb_label.setText("···")
        if not info.thumbnail_url:
            return
        url = info.thumbnail_url

        def on_ready(pixmap: Optional[QPixmap]) -> None:
            if pixmap is not None and url == self._thumb_url:
                self.thumb_label.setPixmap(cover_crop(pixmap, 200, 112, 12))

        ThumbnailLoader.instance().request(url, on_ready)

    # ================================================================== seçim değişiklikleri
    def _set_mode(self, mode: str) -> None:
        if self._updating or self._info is None:
            return
        self._sel.mode = mode
        if mode != "audio" and self._sel.height is None:
            self._sel.height = (youtube.available_heights(self._info) or [None])[0]
        self._rebuild()

    def _select_height(self, height: int) -> None:
        self._sel.height = height
        options = youtube.fps_options(self._info, height)
        if self._sel.fps not in options:
            self._sel.fps = 30 if 30 in options else (60 if options else None)
        self._rebuild()

    def _select_audio(self, audio: str, kbps: int) -> None:
        self._sel.audio = audio
        self._sel.audio_kbps = kbps
        self._rebuild()

    def _set_fps(self, key: str) -> None:
        if not self._updating:
            self._sel.fps = int(key)
            self._rebuild()

    def _set_container(self, key: str) -> None:
        if not self._updating:
            self._sel.container = key
            self._rebuild()

    def _set_codec(self, key: str) -> None:
        if not self._updating:
            self._sel.codec = key
            self._rebuild()

    def _set_attr(self, name: str, value) -> None:
        if not self._updating and self._info is not None:
            setattr(self._sel, name, bool(value))
            self._refresh_summary()

    def _on_lang(self, _index: int) -> None:
        if self._updating or self._info is None:
            return
        self._sel.audio_lang = self.lang_combo.currentData() or ""
        self._rebuild()

    def _on_range_toggled(self, on: bool) -> None:
        if self._updating:
            return
        self.range_host.setVisible(on)
        if not on:
            self._sel.start = self._sel.end = ""
            self.range_error.setText("")
            self._refresh_summary()

    def _on_range_edited(self) -> None:
        if self._updating or self._info is None:
            return
        self._sel.start = self.start_edit.text().strip()
        self._sel.end = self.end_edit.text().strip()
        try:
            youtube.validate_range(self._sel.start, self._sel.end, self._info.duration)
            self.range_error.setText("")
        except ValueError as e:
            self.range_error.setText(str(e))
        self._refresh_summary()

    def _show_subs_menu(self) -> None:
        info = self._info
        if info is None or self._sel.mode != "video":
            return
        menu = QMenu(self)
        manual = {c for c, _ in info.subtitle_langs}
        entries = [(c, n) for c, n in info.subtitle_langs]
        entries += [(c, f"{n} (otomatik)") for c, n in info.auto_caption_langs if c not in manual]
        if not entries:
            menu.addAction("Bu videoda altyazı yok").setEnabled(False)
        for code, name in entries[:25]:
            action = menu.addAction(f"{name}  [{code}]")
            action.setCheckable(True)
            action.setChecked(code in self._sel.subtitles)
            action.toggled.connect(lambda on, c=code: self._toggle_subtitle(c, on))
        menu.addSeparator()
        embed = menu.addAction("Videoya göm (kapalıysa ayrı .srt dosyası)")
        embed.setCheckable(True)
        embed.setChecked(self._sel.sub_embed)
        embed.toggled.connect(lambda on: setattr(self._sel, "sub_embed", bool(on)))
        menu.exec(self.subs_chip.mapToGlobal(self.subs_chip.rect().bottomLeft()))

    def _toggle_subtitle(self, code: str, on: bool) -> None:
        subs = [c for c in self._sel.subtitles if c != code]
        if on:
            subs.append(code)
        self._sel.subtitles = subs
        self._rebuild()

    # ================================================================== arayüzü seçime göre yenileme
    def _rebuild(self) -> None:
        """Karoları ve denetimleri geçerli seçime göre baştan kurar."""
        info, sel = self._info, self._sel
        if info is None:
            return
        self._updating = True
        try:
            audio_mode = sel.mode == "audio"
            for key, btn in self.mode_buttons.items():
                btn.setChecked(key == sel.mode)

            self.tiles_title.setText("Ses biçimi" if audio_mode else "Çözünürlük")
            self._build_tiles()
            self.audio_note.setVisible(audio_mode)
            self.options_host.setVisible(not audio_mode)
            self.codec_hint.setVisible(not audio_mode)

            if not audio_mode:
                available = youtube.fps_options(info, sel.height or 0)
                for value, btn in self.fps_buttons.items():
                    btn.setEnabled(int(value) in available)
                    btn.setChecked(sel.fps == int(value))
                    btn.setToolTip("" if int(value) in available else "Bu çözünürlükte bu kare hızı yok")
                for key, btn in self.container_buttons.items():
                    btn.setChecked(sel.container == key)
                for key, btn in self.codec_buttons.items():
                    btn.setChecked(sel.codec == key)
                    btn.setEnabled(sel.container != "webm")
                picked = youtube.pick_video(info, sel)
                container = youtube.effective_container(info, sel)
                note = f"Kullanılacak video codec: {_FAMILY_NAMES.get(picked.family, '—')}" if picked else ""
                if container != sel.container:
                    note += f"   ·   WEBM bu videoyla uyumsuz, MKV kullanılacak"
                self.codec_hint.setText(note)

            langs = youtube.audio_languages(info)
            self.lang_host.setVisible(len(langs) > 1 and sel.mode != "video_only")
            self.lang_combo.blockSignals(True)
            self.lang_combo.clear()
            self.lang_combo.addItem("Otomatik (orijinal ses)", "")
            for code, label in langs:
                self.lang_combo.addItem(label, code)
            self.lang_combo.setCurrentIndex(max(0, self.lang_combo.findData(sel.audio_lang)))
            self.lang_combo.blockSignals(False)
            self.thumb_chip.setChecked(sel.embed_thumbnail)
            self.chapters_chip.setChecked(sel.chapters and info.has_chapters)
            self.chapters_chip.setEnabled(info.has_chapters)
            self.thumb_chip.setEnabled(True)
            self.subs_chip.setEnabled(sel.mode == "video")
            self.subs_chip.setText("Altyazı: " + (", ".join(sel.subtitles) if sel.subtitles else "kapalı"))
            self.range_chip.setChecked(self.range_host.isVisible() or bool(sel.start or sel.end))
        finally:
            self._updating = False
        self._refresh_summary()

    def _build_tiles(self) -> None:
        info, sel = self._info, self._sel
        _clear_layout(self.tiles_grid)
        group = QButtonGroup(self.tiles_host)
        group.setExclusive(True)

        if sel.mode == "audio":
            has_m4a = any(f.ext == "m4a" for f in info.audio_formats)
            has_opus = any(f.acodec.lower().startswith("opus") for f in info.audio_formats)
            specs = []
            if has_m4a:
                specs.append(("m4a", 0, "Orijinal (M4A)", "Dönüştürmesiz · en iyi kalite", [("ÖNERİLEN", theme.GREEN)]))
            if has_opus:
                specs.append(("opus", 0, "Orijinal (OPUS)", "Dönüştürmesiz · küçük dosya", []))
            for kbps in (320, 192, 128):
                specs.append(("mp3", kbps, f"MP3 {kbps} kbps", "Her yerde çalar", []))
            specs.append(("wav", 0, "WAV", "Kayıpsız · çok büyük dosya", []))
            specs.append(("flac", 0, "FLAC", "Kayıpsız sıkıştırma", []))
            for i, (audio, kbps, title, sub, badges) in enumerate(specs):
                probe = dataclasses.replace(sel, audio=audio, audio_kbps=kbps or sel.audio_kbps)
                size = youtube.estimate_size(info, probe)
                tile = _Tile(title, f"{sub} · {self._size_text(size)}", badges)
                tile.setChecked(sel.audio == audio and (audio != "mp3" or sel.audio_kbps == kbps))
                tile.clicked.connect(lambda _=False, a=audio, k=kbps or sel.audio_kbps: self._select_audio(a, k))
                group.addButton(tile)
                self.tiles_grid.addWidget(tile, i // 2, i % 2)
            return

        for i, height in enumerate(youtube.available_heights(info)):
            badges = []
            if height in _RES_BADGES:
                badges.append(_RES_BADGES[height])
            if youtube.has_hdr(info, height):
                badges.append(("HDR", theme.AMBER))
            probe = dataclasses.replace(sel, height=height)
            options = youtube.fps_options(info, height)
            if sel.fps not in options and options:
                probe.fps = 30 if 30 in options else 60
            size = youtube.estimate_size(info, probe)
            tile = _Tile(f"{height}p", self._size_text(size), badges)
            tile.setChecked(sel.height == height)
            tile.clicked.connect(lambda _=False, h=height: self._select_height(h))
            group.addButton(tile)
            self.tiles_grid.addWidget(tile, i // 4, i % 4)

    @staticmethod
    def _size_text(size: Optional[int]) -> str:
        if not size:
            return "boyut bilinmiyor"
        return "~" + youtube._human_size(size)

    def _refresh_summary(self) -> None:
        if self._info is None:
            return
        self.summary_label.setText(youtube.selection_summary(self._info, self._sel))

    # ================================================================== indirme isteği
    def _on_list_clicked(self) -> None:
        self._emit_download(auto=False, listed=True)

    def _on_add_clicked(self) -> None:
        self._emit_download(auto=False)

    def _emit_download(self, auto: bool, listed: bool = False) -> None:
        info, sel = self._info, self._sel
        if info is None:
            return
        try:
            youtube.validate_range(sel.start, sel.end, info.duration)
        except ValueError as e:
            self.range_error.setText(str(e))
            return
        payload = {"url": info.url or self._last_url, "info": info, "selection": dataclasses.replace(
            sel, subtitles=list(sel.subtitles))}
        payload["listed"] = listed
        self.download_requested.emit(payload)
        short = youtube.selection_summary(info, sel, with_size=False)
        prefix = ("Varsayılan ayarla otomatik eklendi" if auto
                  else ("Listeler sekmesine eklendi" if listed else "Listeye eklendi"))
        self.clear(f"{prefix}: {short}")          # temiz sayfa: yeni bir bağlantı yapıştırılabilir

    def clear(self, message: str = "") -> None:
        """Analiz sonucunu temizleyip sayfayı başlangıç durumuna döndürür. message verilirse onay şeridi gösterilir."""
        self._token += 1                          # çalışan bir analiz varsa sonucu yok sayılır
        self._info = None
        self._sel = Selection()
        self._last_url = ""
        self._thumb_url = ""
        self.link_edit.blockSignals(True)
        self.link_edit.clear()
        self.link_edit.blockSignals(False)
        self.analyze_btn.setEnabled(True)
        self._show_page(_PAGE_IDLE)
        if message:
            self.added_label.setText(message)
            self.added_bar.show()

    def _on_playlist_requested(self, payload: dict) -> None:
        self.playlist_download_requested.emit(payload)
        self.clear(f"{len(payload['entries'])} video listeye eklendi")

    def _show_error(self, message: str, playlist: bool = False) -> None:
        self.analyze_btn.setEnabled(True)
        self._auto_list = False
        self.error_label.setText(message)
        self.playlist_btn.setVisible(playlist)
        self.playlist_all_btn.setVisible(playlist)
        self.retry_btn.setVisible(not playlist)
        self._show_page(_PAGE_ERROR)

    def _show_page(self, page: int) -> None:
        self.stack.setCurrentIndex(page)
        self.summary_bar.setVisible(page == _PAGE_RESULT)
        if page != _PAGE_RESULT:
            self.added_bar.hide()