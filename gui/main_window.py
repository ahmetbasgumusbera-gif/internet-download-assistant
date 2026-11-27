"""
Ana pencere. Yalnızca arayüz mantığı içerir; indirme işleri DownloadManager'a devredilir.
Yönetici olayları ManagerBridge sinyalleri ile (ana thread'de) alınır.
"""
from __future__ import annotations

import subprocess
import os
from pathlib import Path
import threading
from datetime import datetime, timedelta
from urllib.parse import unquote, urlparse

from PySide6.QtCore import QEvent, QMimeData, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QCursor, QDesktopServices, QDrag, QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QMenu, QSystemTrayIcon
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
QCheckBox, QMessageBox, QPushButton, QScrollArea, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from core import appupdate, handoff, prefetch, probewarm, resolver, toolsetup, updater, urlcheck, webvideo, youtube, ytauth
from core import extract as _archive
from core import i18n, netconf
from gui import i18n_qt
from core.server import bundled_extension_version, version_key
from core.manager import DownloadManager
from core.models import DownloadTask, TaskStatus
from core.youtube import build_args, estimate_size, selection_summary
from gui import theme
from gui.bridge import ManagerBridge
from gui.components.download_item import DownloadItemWidget, ElidedLabel
from gui.components.link_fields import URL_PATTERN, LinkFields
from gui.components.settings_dialog import SettingsDialog
from gui.components.webvideo_dialog import WebVideoDialog
from gui.components.youtube_page import YouTubePage
from gui.icons import app_icon, get_icon
from gui.components.capture_dialog import CaptureDialog
from core.speed import limit_text, step_limit
import shutil
from core import rules as _rules, stats as _stats
from gui.components.stats_dialog import StatsDialog
from gui.explorer import reveal_in_explorer
from gui.net_monitor import NetMonitor
from PySide6.QtCore import QRect
from gui.components.rubber_band import SelectionBand
from utils.helpers import format_size
from gui.components.dialog_fit import fit_dialog
from gui.components.hash_dialog import HashDialog
from gui.components.links_dialog import LinksDialog
from gui.components.schedule_dialog import ScheduleDialog
from gui.components.neon_title import NeonTitle
from gui.components.speed_graph import SpeedGraph
from gui.dismiss import DISMISS
from gui.components.server_pill import ServerPill
from gui.server_controller import ServerController
from gui.winutil import bring_to_front
from utils.helpers import format_speed, get_category, sanitize_filename
from utils import paths as _paths
from utils.version import __version__ as _APP_VERSION
from utils.logger import get_logger

log = get_logger(__name__)

_ACTIVE = (TaskStatus.QUEUED, TaskStatus.PROBING, TaskStatus.DOWNLOADING,
           TaskStatus.MERGING, TaskStatus.PAUSED)
_RUNNING = (TaskStatus.PROBING, TaskStatus.DOWNLOADING, TaskStatus.MERGING)

FILTER_ALL, FILTER_ACTIVE, FILTER_DONE = "all", "active", "done"
_MIME = "application/x-ida-task"


def _clipboard_candidate(text: str) -> str:
    """Panodaki metinden indirilebilir görünen ilk bağlantıyı döndürür (yoksa boş): dosya uzantılı,
    tanınan paylaşım (Drive/Dropbox/GitHub) ya da YouTube. Sıradan web sayfaları önerilmez."""
    text = (text or "").strip()
    if not text or len(text) > 2000:
        return ""
    urls = URL_PATTERN.findall(text)
    if not urls:
        return ""
    url = urls[0].rstrip(".,;")
    if youtube.is_youtube_url(url):
        return url
    try:
        if resolver.resolve(url).source:
            return url
    except resolver.UnsupportedLink:
        return ""
    return url if get_category(unquote(urlparse(url).path)) != "Diğer" else ""


class MainWindow(QMainWindow):
    _extract_done = Signal(str, str)            # çıkarılan klasör (başarısızsa boş), ileti
    _ext_seen = ""                              # eklentinin son bildirdiği sürüm
    _update_found = Signal(object)              # (sürüm bilgisi | None, elle mi, hata)
    _update_dialog_open = False
    _app_checking = False
    _url_checked = Signal(str, str, object)     # görev, yapıştırılan adres, denetim sonucu
    def __init__(self, manager: DownloadManager, config, bridge: ManagerBridge, tray: bool = False,
                 auto_updates: bool = False):
        super().__init__()
        self._manager = manager
        self._config = config
        self._bridge = bridge

        self._cards: dict[str, DownloadItemWidget] = {}
        self._statuses: dict[str, TaskStatus] = {}
        self._speeds: dict[str, float] = {}
        self._filter = FILTER_ALL
        self._view = "downloads"        # downloads | lists (aynı liste, farklı süzgeç)
        self._listed = set()            # Listeler sekmesine ait görevler
        self._staged = set()            # listede bekleyen (henüz başlatılmamış) görevler
        self._retried = set()           # elle "Tekrar dene"ye basılanlar
        self._refresh_wait = None       # {"id", "until"}: tarayıcıdan yeni adres bekleniyor
        self._closing = False
        self._clip_url = ""      # önerilen (şeritte gösterilen) bağlantı
        self._clip_last = ""     # son işlenen pano adayı (aynı kopyalamanın tekrarını süzer)
        self._quitting = False   # tepsiden "Çıkış" seçildi: pencere gerçekten kapanır
        self._tray = None
        self._tray_hinted = False
        self._extension_notified = False
        self._tools_updating = False
        self._capture_queue: list[dict] = []
        self._capture_dialog = None
        self._webvideo_dialog = None
        self._links_dialog = None
        self._after_box = None
        self._rows = {}                 # görev id -> satır (seçim kutusu + kart + ⋯)
        self._checks = {}
        self._mores = {}
        self._sel_mode = False
        self._after_ids = None          # "bitince" yalnızca bu indirmeler için (None: tümü)
        self._after_action = "none"
        self._groups = {}               # grup kimliği -> {header, toggle, title, summary, check, collapsed}
        self._task_group = {}           # görev id -> grup kimliği
        self._drag_start = None
        self._band = None               # fareyle toplu seçim kutusu
        self._band_origin = None
        self._band_base = set()
        self._band_moved = False
        self._done_batch: list[str] = []
        self._was_busy = False
        self._completed_since_busy = 0
        self._done_timer = QTimer(self)
        self._done_timer.setSingleShot(True)
        self._done_timer.timeout.connect(self._flush_done)
        self._banner_timer = QTimer(self)
        self._banner_timer.setSingleShot(True)
        self._banner_timer.timeout.connect(lambda: self.done_banner.hide())
        self._server = ServerController(config, self)
        self._mini = None               # yüzen mini kutu (Ayarlar > Yüzen mini kutu)

        self.setWindowTitle("İnternet Download Assistant")
        self.setWindowIcon(app_icon())
        avail = QApplication.primaryScreen().availableGeometry()
        self.resize(min(960, int(avail.width() * 0.92)), min(780, int(avail.height() * 0.9)))
        self.setMinimumSize(min(720, avail.width() - 40), min(480, avail.height() - 80))

        self._build_ui()
        self._connect_signals()
        self._connect_server(tray)
        if auto_updates:
            self._start_tool_updates()
            self._start_app_update_checks()
            QTimer.singleShot(2500, self._ensure_tools)

        # Önceki oturumdan gelen kayıtlar (eskiden yeniye eklenir, en yeni üstte kalır)
        for task in manager.tasks():
            self._add_card(task)
        self._refresh_all()

        self._ticker = QTimer(self)
        self._ticker.setInterval(500)
        self._ticker.timeout.connect(self._refresh_status_bar)
        self._ticker.start()

        self.list_host.setAcceptDrops(True)
        self.setAcceptDrops(True)
        self.list_host.installEventFilter(self)
        delete_key = QShortcut(QKeySequence.Delete, self.scroll)
        delete_key.setContext(Qt.WidgetWithChildrenShortcut)
        delete_key.activated.connect(lambda: self._selected_ids() and self._clear_finished())
        refresh_key = QShortcut(QKeySequence.Refresh, self)
        refresh_key.activated.connect(self._refresh_view)
        select_key = QShortcut(QKeySequence.SelectAll, self.scroll)
        select_key.setContext(Qt.WidgetWithChildrenShortcut)
        select_key.activated.connect(lambda: self._toggle_all(True))
        self.scroll.viewport().installEventFilter(self)
        self._config.set("schedule_enabled", False)   # saate göre zamanlayıcı kaldırıldı
        _rules.configure(bool(self._config.get("clean_names")), str(self._config.get("folder_rules")))
        self._disk_warned = -1
        self._disk_timer = QTimer(self)
        self._disk_timer.setInterval(20_000)
        self._disk_timer.timeout.connect(self._disk_budget_check)
        self._disk_timer.start()
        self._net = NetMonitor(self)
        self._net.changed.connect(self._on_net_changed)
        if tray:                                # yalnızca gerçek uygulamada (testlerde ağ izlenmez)
            self._net.start()
        self._sched_timer = QTimer(self)
        self._sched_timer.setInterval(15_000)
        self._sched_timer.timeout.connect(self._schedule_tick)
        self._sched_timer.start()
        self._list_timer = QTimer(self)
        self._list_timer.setInterval(15_000)
        self._list_timer.timeout.connect(self._list_schedule_tick)
        self._list_timer.start()
        QTimer.singleShot(2000, self._list_schedule_tick)
        self._apply_schedule()                 # kapanmadan önce kurulan zamanlayıcıyı geri yükler

    # ================================================================= arayüz kurulumu
    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(26, 22, 26, 20)
        root.setSpacing(16)

        # ---- başlık
        title = NeonTitle("İnternet Download Assistant")
        self.server_pill = ServerPill()
        title_line = QHBoxLayout()
        title_line.setSpacing(14)
        title_line.addWidget(title, 0, Qt.AlignVCenter)
        title_line.addWidget(self.server_pill, 0, Qt.AlignVCenter)
        title_line.addStretch(1)
        subtitle = QLabel("Hızlı, parçalı ve kaldığı yerden devam eden indirmeler")
        subtitle.setObjectName("appSubtitle")
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title_box.addLayout(title_line)
        title_box.addWidget(subtitle)

        self.settings_btn = QPushButton(" Ayarlar")
        self.settings_btn.setObjectName("ghost")
        self.settings_btn.setIcon(get_icon("sliders", size=18))
        self.settings_btn.setCursor(Qt.PointingHandCursor)

        header = QHBoxLayout()
        header.addLayout(title_box, 1)
        header.addWidget(self.settings_btn, 0, Qt.AlignTop)
        root.addLayout(header)

        self.done_banner = QLabel("")
        self.done_banner.setObjectName("bannerOk")
        self.done_banner.hide()
        root.addWidget(self.done_banner)

        # ---- sekmeler: İndirmeler | YouTube
        self.tab_downloads = QPushButton("İndirmeler")
        self.tab_lists = QPushButton("Listeler")
        self.tab_youtube = QPushButton("YouTube")
        tab_row = QHBoxLayout()
        tab_row.setSpacing(0)
        self._tab_group = QButtonGroup(self)
        self._tab_group.setExclusive(True)
        for btn in (self.tab_downloads, self.tab_lists, self.tab_youtube):
            btn.setObjectName("tab")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            self._tab_group.addButton(btn)
            tab_row.addWidget(btn)
        tab_row.addStretch(1)
        self.tab_downloads.setChecked(True)
        tab_bar = QFrame()
        tab_bar.setObjectName("tabBar")
        tab_bar.setLayout(tab_row)
        root.addWidget(tab_bar)

        downloads_page = QWidget()
        dl = QVBoxLayout(downloads_page)
        dl.setContentsMargins(0, 4, 0, 0)
        dl.setSpacing(16)
        self.youtube_page = YouTubePage(self._config)
        self.stack = QStackedWidget()
        self.stack.addWidget(downloads_page)
        self.stack.addWidget(self.youtube_page)

        # ---- bağlantı girişi: kutu sayısı "Aynı anda indirme" ayarı kadar
        self.link_fields = LinkFields(self._config.get("max_concurrent_downloads"))

        self.add_btn = QPushButton("  İndir")
        self.add_btn.setObjectName("primary")
        self.add_btn.setIcon(get_icon("download", color=theme.BG, hover=theme.BG, disabled=theme.TEXT_DIM, size=20))
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.setDefault(True)
        self.add_btn.setFixedWidth(150)

        self.chip_parallel = QPushButton("Aynı anda")
        self.chip_parallel.setObjectName("chip")
        self.chip_parallel.setToolTip("Ayarlardaki sayı kadar indirme birlikte çalışır")
        self.chip_sequential = QPushButton("Sırayla")
        self.chip_sequential.setObjectName("chip")
        self.chip_sequential.setToolTip("İndirmeler tek tek, sırayla iner")
        mode_group = QButtonGroup(self)
        mode_group.setExclusive(True)
        for chip in (self.chip_parallel, self.chip_sequential):
            chip.setCheckable(True)
            chip.setCursor(Qt.PointingHandCursor)
            mode_group.addButton(chip)
        sequential = self._config.get("download_mode") == "sequential"
        self.chip_sequential.setChecked(sequential)
        self.chip_parallel.setChecked(not sequential)

        side = QVBoxLayout()
        side.setSpacing(8)
        side.addWidget(self.add_btn)
        self.list_add_btn = QPushButton("Listeye ekle")
        self.list_add_btn.setObjectName("ghost")
        self.list_add_btn.setCursor(Qt.PointingHandCursor)
        self.list_add_btn.setToolTip("Hemen indirmeden Listeler sekmesine ekler")
        side.addWidget(self.list_add_btn)
        side.addWidget(self.chip_parallel)
        side.addWidget(self.chip_sequential)
        side.addStretch(1)

        self.input_box = QWidget()
        input_row = QHBoxLayout(self.input_box)
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(12)
        input_row.addWidget(self.link_fields, 1, Qt.AlignTop)
        input_row.addLayout(side)
        dl.addWidget(self.input_box)

        self.message_label = QLabel("")
        self.message_label.setObjectName("message")
        self.message_label.hide()
        dl.addWidget(self.message_label)

        # ---- pano önerisi: diyalogsuz şerit
        self.clip_bar = QFrame()
        clip_row = QHBoxLayout(self.clip_bar)
        clip_row.setContentsMargins(0, 0, 0, 0)
        clip_row.setSpacing(8)
        self.clip_label = ElidedLabel(Qt.ElideMiddle)
        self.clip_label.setObjectName("banner")
        self.clip_add_btn = QPushButton("Kutuya ekle")
        self.clip_close_btn = QPushButton("Kapat")
        for b in (self.clip_add_btn, self.clip_close_btn):
            b.setObjectName("ghost")
            b.setCursor(Qt.PointingHandCursor)
        clip_row.addWidget(self.clip_label, 1)
        clip_row.addWidget(self.clip_add_btn)
        clip_row.addWidget(self.clip_close_btn)
        self.clip_bar.hide()
        dl.addWidget(self.clip_bar)

        # ---- filtre ve toplu işlemler
        self.chip_all = self._make_chip("Tümü", FILTER_ALL)
        self.chip_active = self._make_chip("Aktif", FILTER_ACTIVE)
        self.chip_done = self._make_chip("Tamamlanan", FILTER_DONE)
        self.chip_all.setChecked(True)
        group = QButtonGroup(self)
        group.setExclusive(True)
        for chip in (self.chip_all, self.chip_active, self.chip_done):
            group.addButton(chip)

        self.pause_all_btn = QPushButton(" Duraklat")
        self.pause_all_btn.setObjectName("ghost")
        self.pause_all_btn.setIcon(get_icon("pause", size=16))
        self.resume_all_btn = QPushButton(" Sürdür")
        self.resume_all_btn.setObjectName("ghost")
        self.resume_all_btn.setIcon(get_icon("play", size=16))
        self.clear_btn = QPushButton(" Temizle")
        self.clear_btn.setObjectName("ghost")
        self.clear_btn.setIcon(get_icon("check", size=16))
        self.pause_all_btn.setToolTip("Tüm indirmeleri duraklat")
        self.resume_all_btn.setToolTip("Duraklatılan tüm indirmeleri sürdür")
        self.clear_btn.setToolTip("Tamamlanan ve iptal edilenleri listeden kaldır (dosyalara dokunulmaz)")
        for b in (self.pause_all_btn, self.resume_all_btn, self.clear_btn):
            b.setCursor(Qt.PointingHandCursor)

        self.tools_btn = QPushButton(" Bitince…")
        self.tools_btn.setObjectName("ghost")
        self.tools_btn.setCursor(Qt.PointingHandCursor)
        tools_menu = QMenu(self.tools_btn)
        tools_menu.aboutToShow.connect(lambda m=tools_menu: self._fill_after_menu(m))
        self.tools_btn.setToolTip("İndirme tamamlandığında yapılacak işlem (kart seçiliyse yalnızca onlar için)")
        self.tools_btn.setMenu(tools_menu)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        toolbar.addWidget(self.chip_all)
        toolbar.addWidget(self.chip_active)
        toolbar.addWidget(self.chip_done)
        self.select_box = QCheckBox("Tümünü seç")
        self.select_box.setCursor(Qt.PointingHandCursor)
        self.select_box.clicked.connect(self._toggle_all)
        toolbar.addSpacing(8)
        toolbar.addWidget(self.select_box)
        toolbar.addStretch(1)
        toolbar.addWidget(self.pause_all_btn)
        toolbar.addWidget(self.resume_all_btn)
        toolbar.addWidget(self.clear_btn)
        toolbar.addWidget(self.tools_btn)
        self.refresh_btn = QPushButton(" Yenile")
        self.refresh_btn.setObjectName("ghost")
        self.refresh_btn.setIcon(get_icon("retry", size=16))
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.setToolTip("Listeyi yenile: kartlar, dosyaların varlığı, hız ve ilerleme baştan çizilir (F5)")
        self.refresh_btn.clicked.connect(self._refresh_view)
        toolbar.addWidget(self.refresh_btn)
        dl.addLayout(toolbar)
        self._build_lists_bar(dl)
        self._build_search_row(dl)

        # ---- kart listesi
        self.list_host = QWidget()
        self.list_host.setObjectName("listHost")
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 6, 0)
        self.list_layout.setSpacing(10)
        self.list_layout.addStretch(1)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setWidget(self.list_host)

        # boş durum
        self.empty_box = QWidget()
        empty_layout = QVBoxLayout(self.empty_box)
        empty_layout.setAlignment(Qt.AlignCenter)
        empty_layout.setSpacing(6)
        empty_icon = QLabel()
        empty_icon.setPixmap(get_icon("download", color=theme.BORDER_HOVER, size=44).pixmap(44, 44))
        empty_icon.setAlignment(Qt.AlignCenter)
        self.empty_title = QLabel("Henüz indirme yok")
        self.empty_title.setObjectName("emptyTitle")
        self.empty_title.setAlignment(Qt.AlignCenter)
        self.empty_hint = QLabel("Bir bağlantı yapıştırıp “İndir”e basın")
        self.empty_hint.setObjectName("emptyHint")
        self.empty_hint.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(empty_icon)
        empty_layout.addWidget(self.empty_title)
        empty_layout.addWidget(self.empty_hint)

        list_area = QVBoxLayout()
        list_area.addWidget(self.scroll, 1)
        list_area.addWidget(self.empty_box, 1)
        dl.addLayout(list_area, 1)

        # ---- durum çubuğu
        bar = QFrame()
        bar.setObjectName("statusBar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(16, 10, 16, 10)
        self.status_text = QLabel("")
        self.status_text.setObjectName("statusText")
        self.speed_label = QLabel("")
        self.speed_label.setObjectName("statusSpeed")
        bar_layout.addWidget(self.status_text, 1)
        self.speed_graph = SpeedGraph()
        self.speed_graph.setVisible(bool(self._config.get("show_speed_graph")))
        bar_layout.addWidget(self.speed_graph, 0)
        bar_layout.addWidget(self.speed_label, 0)
        self.speed_btn = QPushButton("Hız sınırı")
        self.speed_btn.setObjectName("chip")
        self.speed_btn.setCursor(Qt.PointingHandCursor)
        self.speed_btn.setToolTip("Genel (ve seçili indirmelerin) hız sınırını arttır / düşür / kaldır")
        self.speed_btn.clicked.connect(self._open_speed_menu)
        bar_layout.addWidget(self.speed_btn, 0)
        root.addWidget(self.stack, 1)
        root.addWidget(bar)

    def _make_chip(self, text: str, key: str) -> QPushButton:
        chip = QPushButton(text)
        chip.setObjectName("chip")
        chip.setCheckable(True)
        chip.setCursor(Qt.PointingHandCursor)
        chip.clicked.connect(lambda: self._set_filter(key))
        return chip

    def _connect_signals(self) -> None:
        self.add_btn.clicked.connect(self._on_add)
        self.link_fields.submitted.connect(self._on_add)
        self.chip_parallel.clicked.connect(lambda: self._set_mode("parallel"))
        self.chip_sequential.clicked.connect(lambda: self._set_mode("sequential"))
        self.settings_btn.clicked.connect(self._open_settings)
        self.pause_all_btn.clicked.connect(self._pause_selected)
        self.resume_all_btn.clicked.connect(self._resume_all)
        self.clear_btn.clicked.connect(self._clear_finished)
        self.tab_downloads.clicked.connect(lambda: self._select_tab(0))
        self.tab_youtube.clicked.connect(lambda: self._select_tab(1))
        self.tab_lists.clicked.connect(lambda: self._select_tab(2))
        self.list_add_btn.clicked.connect(lambda: self._on_add(True))
        self.youtube_page.download_requested.connect(self._on_youtube_download)
        self.youtube_page.goto_downloads.connect(lambda: self._select_tab(0))
        self.youtube_page.list_failed.connect(lambda msg: self._show_message(f"Listeye eklenemedi: {msg}"))

        self._bridge.task_added.connect(self._on_task_added)
        self._bridge.task_updated.connect(self._on_task_updated)
        self._bridge.task_removed.connect(self._on_task_removed)

        self.clip_add_btn.clicked.connect(self._clip_accept)
        self.clip_close_btn.clicked.connect(lambda: self.clip_bar.hide())
        QApplication.clipboard().dataChanged.connect(self._on_clipboard_changed)
        self._url_checked.connect(self._on_url_checked)
        self._update_found.connect(self._on_update_found)
        self._extract_done.connect(self._on_extract_done)

    # ================================================================= kullanıcı eylemleri
    @property
    def url_input(self) -> QLineEdit:
        """İlk bağlantı kutusu (kısayol)."""
        return self.link_fields.fields[0]

    def _on_add(self, listed: bool = False) -> None:
        listed = listed is True
        known = {t.id for t in self._manager.tasks()}
        added, duplicates, invalid = 0, 0, []
        unsupported: list[str] = []
        youtube_url = ""
        waiting_youtube = 0

        for field in self.link_fields.fields:
            text = field.text().strip()
            if not text:
                continue
            urls = URL_PATTERN.findall(text) or [text]
            keep: list[str] = []
            for url in urls:
                if youtube.is_youtube_url(url):
                    if not youtube_url:
                        youtube_url = url            # ilk YouTube linki YouTube sekmesinde açılır
                    else:
                        keep.append(url)             # diğerleri kutuda sırasını bekler
                        waiting_youtube += 1
                    continue
                if not self._dup_ok(url):
                    continue                         # zaten indirilmiş ve kullanıcı vazgeçti
                try:
                    task_id = self._manager.add(url, listed=listed)
                except resolver.UnsupportedLink as e:
                    unsupported.append(str(e))       # tanınan ama desteklenmeyen (ör. Drive klasörü)
                    keep.append(url)
                    continue
                except ValueError:
                    invalid.append(url)
                    keep.append(url)
                    continue
                except RuntimeError:
                    return
                if task_id in known:
                    duplicates += 1
                else:
                    added += 1
                    known.add(task_id)
            field.setText(" ".join(keep))            # eklenenin kutusu boşalır; geçersiz/bekleyenler kalır

        if not (added or duplicates or invalid or unsupported or youtube_url):
            return                                   # tüm kutular boştu

        if (invalid or unsupported) and not (added or duplicates or youtube_url):
            self._show_message(unsupported[0] if unsupported
                               else "Geçerli bir bağlantı değil. Adres http:// veya https:// ile başlamalı.")
            return

        notes = []
        if unsupported:
            notes.append(unsupported[0])
        if duplicates:
            notes.append(f"{duplicates} bağlantı zaten listede")
        if invalid:
            notes.append(f"{len(invalid)} geçersiz bağlantı kutuda bırakıldı")
        if waiting_youtube:
            notes.append(f"{waiting_youtube} YouTube bağlantısı daha kutuda bekliyor (tek tek açılır)")
        self.link_fields.focus_first_empty()
        if listed and (added or duplicates) and not youtube_url:
            self._select_tab(2)
        if youtube_url:
            self._open_youtube(youtube_url, " · ".join(notes))
        else:
            self._show_message(" · ".join(notes))

    def _select_tab(self, index: int) -> None:
        """0 = İndirmeler, 1 = YouTube, 2 = Listeler (0 ve 2 aynı sayfayı farklı süzgeçle gösterir)."""
        view = "lists" if index == 2 else "downloads"
        changed = view != self._view
        self._view = view
        self.stack.setCurrentIndex(1 if index == 1 else 0)
        {0: self.tab_downloads, 1: self.tab_youtube, 2: self.tab_lists}[index].setChecked(True)
        self.input_box.setVisible(view == "downloads")
        self.lists_bar.setVisible(view == "lists")
        if changed:
            self._toggle_all(False)
            self._refresh_all()

    def _select_tab_old(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        (self.tab_downloads, self.tab_youtube)[index].setChecked(True)

    def _open_youtube(self, url: str, note: str = "") -> None:
        """YouTube linkini YouTube sekmesine taşır ve analizi başlatır."""
        self._select_tab(1)
        self.youtube_page.analyze(url)
        text = "YouTube bağlantısı algılandı, YouTube sekmesine geçildi"
        self._show_message(f"{text} · {note}" if note else text, transient=not note)

    def _on_youtube_download(self, payload: dict) -> None:
        """YouTube sekmesinden gelen seçimi normal bir indirme görevi olarak kuyruğa ekler."""
        info, sel = payload["info"], payload["selection"]
        try:
            args = build_args(info, sel)
        except ValueError as e:
            self._show_message(str(e))
            return
        options = {
            "args": args,
            "expected_size": estimate_size(info, sel),
            "summary": selection_summary(info, sel),
            "summary_short": selection_summary(info, sel, with_size=False),
            "mode": sel.mode,
            "playlist_item": info.from_playlist,
            "info_file": getattr(info, "info_file", ""),
        }
        try:
            self._manager.add(payload["url"], engine="ytdlp", options=options,
                              title=info.title, thumbnail_url=info.thumbnail_url,
                              listed=bool(payload.get("listed")))
        except (ValueError, RuntimeError) as e:
            self._show_message(f"Eklenemedi: {e}")
    def _set_mode(self, mode: str) -> None:
        self._config.set("download_mode", mode)
        self._manager.apply_settings()
        text = "Sırayla: indirmeler tek tek iner" if mode == "sequential" else "Aynı anda: indirmeler birlikte çalışır"
        self._show_message(text, transient=True)

    def _on_cancel(self, task_id: str) -> None:
        task = self._manager.get_task(task_id)
        if task is None:
            return
        if task.downloaded > 0:
            answer = QMessageBox.question(
                self, "İptal onayı",
                "İndirmeyi iptal etmek istediğinize emin misiniz?\n\n"
                "Şimdiye kadar inen parçalar silinecek.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        self._manager.cancel(task_id)

    def _open_folder(self, task_id: str) -> None:
        task = self._manager.get_task(task_id)
        if task is None:
            return
        target = task.final_path
        if target and Path(target).exists() and os.name == "nt":
            if not reveal_in_explorer(Path(target)):
                subprocess.Popen(["explorer", "/select,", str(Path(target))])
            return
        folder = Path(target).parent if target else task.save_dir
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _resume_all(self) -> None:
        selected = self._selected_ids()
        if selected:                      # seçilenler: duraklatılmış olan sürer, hatalı/iptal edilen yeniden denenir
            for task_id in selected:
                status = self._statuses.get(task_id)
                if status == TaskStatus.PAUSED:
                    self._manager.resume(task_id)
                elif status in (TaskStatus.ERROR, TaskStatus.CANCELLED):
                    self._manager.retry(task_id)
            return
        for task in self._manager.tasks():
            if task.status == TaskStatus.PAUSED:
                self._manager.resume(task.id)

    def _clear_finished(self) -> None:
        selected = self._selected_ids()
        if selected:
            busy = [i for i in selected if self._statuses.get(i) in _ACTIVE]
            text = f"Seçili {len(selected)} indirme listeden silinsin mi?"
            if busy:
                text += f"\n\nDevam eden {len(busy)} indirme iptal edilir. İndirilmiş dosyalara dokunulmaz."
            menu = QMenu(self)
            menu.addAction("Dosyaları sil" if len(selected) > 1 else "Dosyayı sil",
                           lambda: self._delete_selected(selected, True))
            menu.addAction("Yalnızca indirme geçmişinden sil",
                           lambda: self._delete_selected(selected, False))
            menu.exec(self.clear_btn.mapToGlobal(self.clear_btn.rect().bottomLeft()))
            return
        for task in self._manager.tasks():
            if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
                self._manager.remove(task.id)

    def _open_settings(self) -> None:
        dialog = SettingsDialog(self._config, self)
        fit_dialog(dialog)
        existing = getattr(self, "_settings_dialog", None)
        try:
            if existing is not None and existing.isVisible():
                dialog.deleteLater()
                bring_to_front(existing)
                return
        except RuntimeError:
            pass
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.changed.connect(self._apply_live_settings)
        dialog.finished.connect(lambda _r, d=dialog: self._settings_closed(d))
        DISMISS.register(dialog)                     # dışarı tıklayınca kapanır
        self._settings_dialog = dialog
        dialog.show()
        bring_to_front(dialog)

    def _set_filter(self, key: str) -> None:
        self._filter = key
        self._refresh_all()

    # ================================================================= pano ve davranış ayarları
    def _on_clipboard_changed(self) -> None:
        if not self._config.get("clipboard_watch"):
            return
        self._consider_clipboard(QApplication.clipboard().text())

    def _consider_clipboard(self, text: str) -> None:
        """Panodaki metin indirilebilir bir bağlantıysa diyalogsuz bir öneri şeridi gösterir."""
        url = _clipboard_candidate(text)
        if url == self._clip_last:
            return                                   # aynı kopyalamanın tekrarlanan bildirimi
        self._clip_last = url
        if not url:
            self.clip_bar.hide()
            return
        if any(t.url == url or t.options.get("original_url") == url for t in self._manager.tasks()):
            return                                   # zaten listede
        self._clip_url = url
        self.clip_label.set_full_text(f"Panodaki bağlantı:  {url}")
        self.clip_bar.show()

    def _clip_accept(self) -> None:
        """Önerilen bağlantıyı ilk boş kutuya yazar (indirmeyi başlatmaz)."""
        url = self._clip_url
        self.clip_bar.hide()
        if not url:
            return
        self._select_tab(0)
        for field in self.link_fields.fields:
            if not field.text().strip():
                field.setText(url)
                return
        last = self.link_fields.fields[-1]
        last.setText(f"{last.text().strip()} {url}")

    def _apply_behavior_settings(self) -> None:
        """Ayarlar kaydedilince davranış ayarlarını açık arayüze uygular."""
        ytauth.configure(bool(self._config.get("youtube_use_cookies")),
                         str(self._config.get("youtube_cookies_browser") or ""))
        _rules.configure(bool(self._config.get("clean_names")), str(self._config.get("folder_rules")))
        self.speed_graph.setVisible(bool(self._config.get("show_speed_graph")))
        warn = bool(self._config.get("warn_dangerous_files"))
        for card in self._cards.values():
            card.set_warn_dangerous(warn)
        if not self._config.get("clipboard_watch"):
            self.clip_bar.hide()
        netconf.configure(str(self._config.get("proxy_url") or ""))
        self.sync_mini_box()

    # ================================================================= yönetici olayları
    def _on_task_added(self, task: DownloadTask) -> None:
        QTimer.singleShot(1500, self._disk_budget_check)
        if task.id not in self._cards:
            self._add_card(task)
        self._refresh_all()

    def _on_task_updated(self, task: DownloadTask) -> None:
        handoff.HANDOFF.on_task(task)
        card = self._cards.get(task.id)
        if card is None:
            # Kayıt arada silindiyse (geç gelen olay) kartı yeniden oluşturma
            if self._manager.get_task(task.id) is not None:
                self._add_card(task)
                self._refresh_all()
            return
        previous = self._statuses.get(task.id)
        if previous != TaskStatus.COMPLETED and task.status == TaskStatus.COMPLETED:
            self._on_task_completed(task)
        card.update_task(task)
        self._statuses[task.id] = task.status
        self._speeds[task.id] = task.speed if task.status == TaskStatus.DOWNLOADING else 0.0
        if (task.options or {}).get("staged"):
            self._staged.add(task.id)
        else:
            self._staged.discard(task.id)
        if task.status == TaskStatus.ERROR and task.id in self._retried:
            self._retried.discard(task.id)      # elle deneme de başarısız oldu: yeniden yakalama önerilir
            QTimer.singleShot(300, lambda tid=task.id: self._offer_recapture(tid))
        elif task.status in (TaskStatus.DOWNLOADING, TaskStatus.COMPLETED, TaskStatus.CANCELLED):
            self._retried.discard(task.id)
        if previous != task.status:
            self._refresh_all()      # durum değişince filtre/sayaçlar güncellenir

    def _on_task_removed(self, task_id: str) -> None:
        handoff.HANDOFF.on_removed(task_id)
        card = self._cards.pop(task_id, None)
        self._statuses.pop(task_id, None)
        self._speeds.pop(task_id, None)
        self._forget_group(task_id)
        self._listed.discard(task_id)
        self._staged.discard(task_id)
        self._retried.discard(task_id)
        row = self._rows.pop(task_id, None)
        self._checks.pop(task_id, None)
        self._mores.pop(task_id, None)
        if row is not None:
            self.list_layout.removeWidget(row)
            row.deleteLater()               # kart satırın çocuğudur: birlikte silinir
        elif card is not None:
            self.list_layout.removeWidget(card)
            card.deleteLater()
        self._refresh_all()

    # ================================================================= liste yönetimi
    def _add_card(self, task: DownloadTask) -> None:
        card = DownloadItemWidget(task)
        card.set_warn_dangerous(bool(self._config.get("warn_dangerous_files")))
        card.pause_requested.connect(self._manager.pause)
        card.resume_requested.connect(self._manager.resume)
        card.retry_requested.connect(self._on_retry)
        card.remove_requested.connect(self._on_card_remove)
        card.cancel_requested.connect(self._on_cancel)
        card.open_folder_requested.connect(self._open_folder)
        card.error_requested.connect(self._show_error_details)
        card.setProperty("taskId", task.id)
        card.installEventFilter(self)
        card.setContextMenuPolicy(Qt.CustomContextMenu)
        card.customContextMenuRequested.connect(lambda pos, tid=task.id, c=card: self._card_menu(tid, c, pos))
        self._cards[task.id] = card
        if (task.options or {}).get("listed"):
            self._listed.add(task.id)
        if (task.options or {}).get("staged"):
            self._staged.add(task.id)
        self._statuses[task.id] = task.status
        self._speeds[task.id] = 0.0
        row = self._make_row(task.id, card)
        self._rows[task.id] = row
        self.list_layout.insertWidget(self.list_layout.count() - 1 if task.id in self._listed else 0, row)
        self._join_group(task)     # en yeni üstte (alttaki stretch hep sonda kalır)

    def _matches_filter(self, status: TaskStatus) -> bool:
        if self._filter == FILTER_ACTIVE:
            return status in _ACTIVE
        if self._filter == FILTER_DONE:
            return status == TaskStatus.COMPLETED
        return True

    def _refresh_all(self) -> None:
        shown = 0
        matched = set()
        for task_id, card in self._cards.items():
            visible = (self._matches_filter(self._statuses[task_id]) and self._matches_search(task_id)
                       and ((task_id in self._listed) == (self._view == "lists")))
            card.setVisible(visible)
            row = self._rows.get(task_id)
            if row is not None:
                gid = self._task_group.get(task_id)
                collapsed = bool(gid) and self._groups[gid]["collapsed"]
                row.setVisible(visible and not collapsed)
                if gid and visible:
                    matched.add(gid)
            shown += visible

        self._refresh_groups(matched)
        has_cards = bool(self._cards)
        self.scroll.setVisible(shown > 0)
        self.empty_box.setVisible(shown == 0)
        if not has_cards:
            self.empty_title.setText("Henüz indirme yok")
            self.empty_hint.setText("Bir bağlantı yapıştırıp “İndir”e basın")
        else:
            self.empty_title.setText("Bu filtrede indirme yok")
            self.empty_hint.setText("Üstteki “Tümü” sekmesinden hepsini görebilirsiniz")

        statuses = list(self._statuses.values())
        self.pause_all_btn.setEnabled(any(s in (TaskStatus.QUEUED, *_RUNNING) for s in statuses))
        self.resume_all_btn.setEnabled(any(s == TaskStatus.PAUSED for s in statuses))
        self.clear_btn.setEnabled(any(s in (TaskStatus.COMPLETED, TaskStatus.CANCELLED) for s in statuses))
        self._refresh_status_bar()

    def _refresh_status_bar(self) -> None:
        statuses = list(self._statuses.values())
        running = sum(1 for s in statuses if s in _RUNNING)
        queued = sum(1 for i, s in self._statuses.items() if s == TaskStatus.QUEUED and i not in self._staged)
        paused = sum(1 for s in statuses if s == TaskStatus.PAUSED)
        done = sum(1 for s in statuses if s == TaskStatus.COMPLETED)
        failed = sum(1 for s in statuses if s == TaskStatus.ERROR)
        busy = running + queued
        d_busy = sum(1 for i, s in self._statuses.items()
                     if i not in self._listed and s in (TaskStatus.QUEUED, *_RUNNING))
        l_busy = sum(1 for i in self._listed if self._statuses.get(i) in _ACTIVE)
        self.tab_downloads.setText(f"İndirmeler ({d_busy})" if d_busy else "İndirmeler")
        self.tab_lists.setText(f"Listeler ({l_busy})" if l_busy else "Listeler")
        self._refresh_lists_bar()
        if self._view == "lists":
            self.empty_title.setText("Liste boş")
            self.empty_hint.setText("YouTube sekmesinden, yakalama penceresinden ya da ana ekrandan “Listeye ekle”yi kullan")

        parts = [f"{running} aktif", f"{queued} sırada"]
        if paused:
            parts.append(f"{paused} duraklatıldı")
        parts.append(f"{done} tamamlandı")
        hold = self._hold_text()
        if hold:
            parts.insert(0, f"⏰ {hold}")
        if failed:
            parts.append(f"{failed} hatalı")
        self.status_text.setText("   ·   ".join(parts))

        total_speed = sum(self._speeds.values())
        self.speed_label.setText(f"↓ {format_speed(total_speed)}" if total_speed > 0 else "")
        self.speed_graph.push(total_speed)
        if self._mini is not None:
            self._mini.set_state(total_speed, self._overall_percent())
        self._update_progress_title(total_speed)
        self._refresh_selection_ui()
        self._check_scoped_after()
        self._check_all_done(running, queued, paused)

    def _show_message(self, text: str, transient: bool = False) -> None:
        if not text:
            self.message_label.hide()
            return
        self.message_label.setText(text)
        self.message_label.show()
        if transient:
            QTimer.singleShot(2500, self.message_label.hide)

    # ================================================================= araç güncellemeleri ve eklenti sürümü
    def _start_tool_updates(self) -> None:
        """yt-dlp ve Deno'yu arka planda (açılıştan 30 sn sonra, sonra 3 saatte bir) gerekiyorsa günceller."""
        self._tool_timer = QTimer(self)
        self._tool_timer.setInterval(3 * 60 * 60 * 1000)
        self._tool_timer.timeout.connect(self._tool_update_tick)
        self._tool_timer.start()
        QTimer.singleShot(30_000, self._tool_update_tick)

    def _tool_update_tick(self) -> None:
        if self._tools_updating or self._closing:
            return
        self._tools_updating = True

        def work() -> None:
            try:
                updater.run_scheduled(self._config, busy=self._downloads_busy)
            except Exception:
                log.exception("Araç güncellemesi başarısız")
            finally:
                self._tools_updating = False

        threading.Thread(target=work, daemon=True, name="tool-update").start()

    def _downloads_busy(self) -> bool:
        return any(s in _RUNNING for s in list(self._statuses.values()))

    def _on_extension_seen(self, version: str) -> None:
        self._ext_seen = version
        """Eklenti kendini tanıttı: kurulumla gelenden eskiyse küçük bir bildirim gösterir (oturumda bir kez)."""
        bundled = bundled_extension_version()
        if self._extension_notified or not bundled or version_key(version) >= version_key(bundled):
            return
        self._extension_notified = True
        text = (f"Tarayıcı eklentisi güncel değil (v{version} → v{bundled}). "
                "chrome://extensions sayfasında eklentinin ↻ düğmesine basın.")
        self._show_message(text)
        if self._tray is not None and not self.isVisible():
            self._tray.showMessage("İnternet Download Assistant", text, QSystemTrayIcon.Information, 6000)

    # ================================================================= sunucu, tepsi ve yakalama
    @property
    def has_tray(self) -> bool:
        return self._tray is not None

    def start_server(self) -> None:
        self._server.start()

    def show_and_raise(self) -> None:
        bring_to_front(self)

    def _connect_server(self, tray: bool) -> None:
        self._server.state_changed.connect(self._on_server_state)
        self._server.download_captured.connect(self._on_capture)
        self._server.youtube_captured.connect(self._on_capture_youtube)
        self.server_pill.clicked.connect(self._server.toggle)
        self.youtube_page.playlist_download_requested.connect(self._on_playlist_download)
        self._server.extension_seen.connect(self._on_extension_seen)
        self._server.upload_captured.connect(self._on_upload_captured)
        self._server.webvideo_captured.connect(self._on_webvideo)
        self._server.handoff_taken.connect(self._on_handoff_taken)
        self._server.links_captured.connect(self._on_links)
        if tray:
            self._setup_tray()
        self._on_server_state(self._server.state, self._server.message)

    def _on_server_state(self, state: str, message: str) -> None:
        self.server_pill.set_state(state, message)
        if self._tray is not None:
            running = state in ("running", "starting")
            self._tray_server_action.setText("Sunucuyu kapat" if running else "Sunucuyu aç")
        if state == "error":
            self._show_message(message)

    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QSystemTrayIcon(app_icon(), self)
        menu = QMenu()
        open_action = menu.addAction("Aç")
        self._tray_server_action = menu.addAction("Sunucuyu aç")
        menu.addSeparator()
        menu.addAction("Tümünü duraklat").triggered.connect(lambda _=False: self._manager.pause_all())
        menu.addAction("Tümünü sürdür").triggered.connect(lambda _=False: self._tray_resume_all())
        self._tray_speed_menu = menu.addMenu("Genel hız sınırı")
        self._tray_after_menu = menu.addMenu("Bitince")
        menu.aboutToShow.connect(self._refresh_tray_menu)
        menu.addSeparator()
        quit_action = menu.addAction("Çıkış")
        open_action.triggered.connect(self.show_and_raise)
        self._tray_server_action.triggered.connect(self._server.toggle)
        quit_action.triggered.connect(self._quit_from_tray)
        tray.setContextMenu(menu)
        tray.setToolTip("İnternet Download Assistant")
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        QApplication.setQuitOnLastWindowClosed(False)   # pencere tepsiye saklanınca uygulama kapanmasın
        self._tray, self._tray_menu = tray, menu

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_and_raise()

    def _tray_notice(self) -> None:
        if self._tray is not None and not self._tray_hinted:
            self._tray_hinted = True
            self._tray.showMessage("İnternet Download Assistant",
                                   "Arka planda çalışıyor. Açmak için tepsideki simgeye tıklayın.",
                                   QSystemTrayIcon.Information, 4000)

    def _quit_from_tray(self) -> None:
        self._quitting = True
        self.close()

    def _warm_probe(self, payload: dict) -> None:
        """Yakalanan indirmenin sondalamasını kullanıcı İndir'e basmadan arka planda yapar (başlangıç gecikmesini saklar)."""
        url = payload.get("url") or ""
        if payload.get("localPath") or not url.lower().startswith(("http://", "https://")):
            return
        try:
            if resolver.resolve(url).source:
                return                              # Drive / Dropbox vb.: kendi akışı var
        except resolver.UnsupportedLink:
            return
        name = payload.get("filename") or ""
        if not name:                                # yakalama penceresinin varsayılan adıyla aynı olmalı (önbellek anahtarı)
            tail = unquote(urlparse(url).path.rsplit("/", 1)[-1])
            name = tail if "." in tail else ""
        headers = {}
        if payload.get("referrer"):
            headers["Referer"] = payload["referrer"]
        if payload.get("cookies"):
            headers["Cookie"] = payload["cookies"]
        if payload.get("userAgent"):
            headers["User-Agent"] = payload["userAgent"]
        probewarm.warm(url, headers, name)

    def _on_capture(self, payload: dict) -> None:
        """Tarayıcı eklentisi bir indirme yakaladı: onay penceresini gösterir (art arda gelenler sıraya girer)."""
        if self._try_refresh_capture(payload):
            return                                # bekleyen bir indirmenin adresi yenilendi
        if payload.get("quick"):                  # site videosu: pencere açmadan doğrudan listeye
            self._add_captured(payload, {
                "file_name": payload.get("filename") or "", "listed": True,
                "save_dir": _rules.pick_dir(payload.get("url") or "", payload.get("filename") or "",
                                            Path(self._config.download_dir()))})
            return
        self._warm_probe(payload)
        self._capture_queue.append(payload)
        if self._capture_dialog is None:
            self._next_capture()

    def _next_capture(self) -> None:
        if not self._capture_queue:
            self._capture_dialog = None
            return
        payload = self._capture_queue.pop(0)
        bring_to_front(self)
        default_dir = _rules.pick_dir(payload.get("url") or "", payload.get("filename") or "",
                                      Path(self._config.download_dir()))
        dialog = CaptureDialog(payload, default_dir, self)
        self._capture_dialog = dialog
        dialog.finished.connect(lambda result, d=dialog, p=payload: self._capture_finished(d, p, result))
        dialog.open()
        QTimer.singleShot(0, lambda d=dialog: self._raise_dialog(d))

    def _capture_finished(self, dialog, payload: dict, result: int) -> None:
        values = dialog.values() if result else None
        dialog.deleteLater()
        self._capture_dialog = None
        if values is not None:
            self._add_captured(payload, values)
        elif payload.get("ticket"):
            handoff.HANDOFF.finish(payload["ticket"], "cancelled")   # kullanıcı vazgeçti: tarayıcı indirmesi de iptal kalır
        elif payload.get("localPath"):                 # vazgeçildi: geçici kopya silinir
            try:
                Path(payload["localPath"]).unlink()
            except OSError:
                pass
        QTimer.singleShot(0, self._next_capture)

    def _on_webvideo(self, data: dict) -> None:
        """Eklenti bir sitede video(lar) buldu: seçim penceresi açılır (öncekisi varsa yenisiyle değişir)."""
        page = webvideo.parse_page(data)
        bring_to_front(self)
        if self._webvideo_dialog is not None:
            self._webvideo_dialog.close()
        dialog = WebVideoDialog(page, self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.direct_requested.connect(self._on_capture)          # doğrudan dosya: onay penceresi + HTTP motoru
        dialog.stream_requested.connect(self._on_stream_download)  # HLS/DASH/sayfa: yt-dlp
        dialog.destroyed.connect(lambda *_args, d=dialog: self._webvideo_closed(d))
        self._webvideo_dialog = dialog
        QTimer.singleShot(80, lambda d=dialog: self._raise_dialog(d))
        if data.get("listed"):
            QTimer.singleShot(0, dialog.auto_list)
        dialog.show()

    def _on_handoff_taken(self, ticket: str) -> None:
        """Eklenti, sunucunun engellediği indirmeyi tarayıcıya devretti: uygulamadaki hatalı kart kaldırılır."""
        task_id = handoff.HANDOFF.task_of(ticket)
        if task_id:
            self._manager.remove(task_id)
        self._show_message("Sunucu engellediği için indirme tarayıcıya devredildi", transient=True)

    def _webvideo_closed(self, dialog) -> None:
        if self._webvideo_dialog is dialog:
            self._webvideo_dialog = None

    def _on_stream_download(self, spec: dict) -> None:
        """Seçilen akışı (yt-dlp argümanlarıyla) indirme görevi olarak kuyruğa ekler."""
        options = {"args": spec["args"], "summary": spec["summary"], "summary_short": spec["summary"],
                   "mode": "video", "playlist_item": bool(spec.get("playlistItem")), "generic": True, "page_url": spec.get("pageUrl") or ""}
        try:
            self._manager.add(spec["url"], engine="ytdlp", options=options, title=spec["title"],
                              thumbnail_url=spec.get("thumbnail") or "", listed=bool(spec.get("listed")))
        except (ValueError, RuntimeError) as e:
            self._show_message(f"Eklenemedi: {e}")
            return
        self._select_tab(0)
        if spec.get("listed"):
            self._select_tab(2)
        self._show_message("Video listeye eklendi", transient=True)

    def _on_upload_captured(self, upload: dict) -> None:
        """Eklenti, adresi olmayan (blob:/data:) bir dosyayı parça parça verdi: aynı onay penceresiyle sorulur."""
        referrer = upload.get("referrer") or ""
        self._on_capture({
            "url": referrer or "Tarayıcıdan (yerel veri)", "referrer": referrer,
            "filename": upload.get("filename") or "", "fileSize": int(upload.get("size") or 0),
            "mime": upload.get("mime") or "", "localPath": upload["path"],
        })

    def _add_local(self, payload: dict, values: dict) -> None:
        source = Path(payload["localPath"])
        name = values["file_name"] or payload.get("filename") or "indirilen_dosya"
        try:
            self._manager.add(f"local://{source.stem}", save_dir=values["save_dir"], file_name=name, engine="local",
                              options={"source_path": str(source)}, listed=bool(values.get("listed")))
        except (ValueError, RuntimeError):
            self._show_message("Dosya listeye eklenemedi.")
            return
        self._select_tab(0)
        if values.get("listed"):
            self._select_tab(2)
        self._show_message("Dosya listeye eklendi", transient=True)

    def _add_captured(self, payload: dict, values: dict) -> None:
        if payload.get("localPath"):
            self._add_local(payload, values)
            return
        headers = {}
        if payload.get("referrer"):
            headers["Referer"] = payload["referrer"]
        if payload.get("cookies"):
            headers["Cookie"] = payload["cookies"]
        listed = bool(values.get("listed") or payload.get("listed"))
        options = {"page_url": payload["referrer"]} if payload.get("referrer") else {}
        _convert = values.get("convert_to") or payload.get("convertTo") or ""
        if _convert:
            options["convert_to"] = str(_convert)
        if payload.get("userAgent"):
            headers["User-Agent"] = payload["userAgent"]
        known = {t.id for t in self._manager.tasks()}
        if not self._dup_ok(payload["url"], values["file_name"] or payload.get("filename") or "",
                            int(payload.get("fileSize") or 0)):
            if payload.get("ticket"):
                handoff.HANDOFF.finish(payload["ticket"], "cancelled")
            return
        try:
            task_id = self._manager.add(payload["url"], save_dir=values["save_dir"],
                                        file_name=values["file_name"], headers=headers,
                                        options=options, listed=listed,
                                        thumbnail_url=payload.get("thumbnail") or "")
        except resolver.UnsupportedLink as e:
            self._show_message(str(e))
            return
        except ValueError:
            self._show_message("Geçerli bir bağlantı değil.")
            return
        except RuntimeError:
            return
        if payload.get("ticket"):
            handoff.HANDOFF.attach(payload["ticket"], task_id)
        self._select_tab(0)
        if listed:
            self._select_tab(2)
        self._show_message("Bu indirme zaten listede" if task_id in known else "İndirme listeye eklendi",
                           transient=True)

    def _on_capture_youtube(self, url: str, playlist: bool = False, listed: bool = False) -> None:
        """Tarayıcıdaki YouTube kutusundan gelen adres: YouTube sekmesinde açılır ve analiz edilir."""
        if not listed:
            bring_to_front(self)
            self._select_tab(1)
        if playlist:
            self.youtube_page.analyze_playlist(url)
        else:
            if listed:
                self.youtube_page.add_to_list(url)       # art arda gelenler sırayla işlenir
            else:
                self.youtube_page.analyze(url, force=True)

    def _on_playlist_download(self, payload: dict) -> None:
        """Liste panelinde seçilen videoları, ortak ayarla ve listenin adıyla bir alt klasöre ekler."""
        info, entries, sel = payload["info"], payload["entries"], payload["selection"]
        args = youtube.playlist_args(sel)
        summary = youtube.playlist_summary(sel)
        folder = Path(self._config.download_dir()) / sanitize_filename(
            info.title, fallback="Oynatma listesi", max_len=80)
        added = 0
        for index, entry in enumerate(entries):
            options = {"args": args, "summary": summary, "summary_short": summary,
                       "mode": sel.mode, "playlist_item": False,
                       "group": info.title, "group_id": info.id or info.title}
            try:
                self._manager.add(entry.url, save_dir=folder, engine="ytdlp", options=options, title=entry.title,
                                  thumbnail_url=entry.thumbnail_url if index < 40 else "")
                added += 1
            except (ValueError, RuntimeError):
                continue
        self._show_message(f"{added} video listeye eklendi  ·  klasör: {folder.name}", transient=True)

    # ================================================================= bildirimler, zamanlayıcı, sıralama, araçlar
    def _on_task_completed(self, task: DownloadTask) -> None:
        self._completed_since_busy += 1
        self._maybe_auto_extract(task)
        self._was_busy = True
        if self._config.get("notify_on_complete"):
            self._done_batch.append((task.title or task.file_name or "dosya")[:80])
            self._done_timer.start(1200)         # art arda bitenler tek bildirimde toplanır

    def _flush_done(self) -> None:
        names, self._done_batch = self._done_batch, []
        if not names:
            return
        text = f"✓ Tamamlandı: {names[0]}" if len(names) == 1 else f"✓ {len(names)} dosya tamamlandı"
        self.done_banner.setText(text)
        self.done_banner.show()
        self._banner_timer.start(9000)
        if self._tray is not None and not (self.isVisible() and self.isActiveWindow()):
            self._tray.showMessage("İndirme tamamlandı", text, QSystemTrayIcon.Information, 5000)

    def _update_progress_title(self, speed: float) -> None:
        base = "İnternet Download Assistant"
        live = [t for t in self._manager.tasks() if t.status in _RUNNING and t.file_size]
        title, tip = base, base
        if live:
            total = sum(t.file_size for t in live)
            got = sum(t.downloaded for t in live)
            pct = int(got * 100 / total) if total else 0
            title = f"%{pct} · {base}"
            tip = f"{base}\n%{pct}  ·  ↓ {format_speed(speed)}"
        if self.windowTitle() != title:
            self.setWindowTitle(title)
        if self._tray is not None:
            self._tray.setToolTip(tip)

    def _check_all_done(self, running: int, queued: int, paused: int) -> None:
        if running + queued + self._manager.pending_retries() > 0:
            self._was_busy = True
            return
        if not self._was_busy:
            return
        self._was_busy = False
        if self._completed_since_busy == 0:
            return                               # yalnızca duraklatma: bitmiş sayılmaz
        self._completed_since_busy = 0
        self._all_done(paused)

    def _all_done(self, paused: int) -> None:
        if self._config.get("notify_raise_window"):
            bring_to_front(self)
        action = self._config.get("after_all_action")
        if paused == 0 and action in ("sleep", "shutdown", "restart"):
            self._config.set("after_all_action", "none")     # tek seferlik
            self._confirm_power_action(action)

    def _confirm_power_action(self, action: str) -> None:
        label = {"sleep": "uykuya alınacak", "restart": "yeniden başlatılacak"}.get(action, "kapatılacak")
        if os.name != "nt":
            self._show_message(f"Tüm indirmeler bitti ({label} ama bu sistemde desteklenmiyor).")
            return
        box = QMessageBox(QMessageBox.Question, "İndirmeler bitti",
                          f"Tüm indirmeler tamamlandı. Bilgisayar 30 saniye içinde {label}.", QMessageBox.NoButton, self)
        cancel = box.addButton("İptal", QMessageBox.RejectRole)
        now_btn = box.addButton("Şimdi yap", QMessageBox.AcceptRole)
        box.setDefaultButton(cancel)
        box.setAttribute(Qt.WA_DeleteOnClose)

        def go(button) -> None:
            if button is not now_btn:
                return
            flags = 0x08000000
            if action == "restart":
                subprocess.Popen(["shutdown", "/r", "/t", "10"], creationflags=flags)
            elif action == "shutdown":
                subprocess.Popen(["shutdown", "/s", "/t", "10"], creationflags=flags)
            else:
                subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], creationflags=flags)

        box.buttonClicked.connect(go)
        timer = QTimer(box)
        timer.setSingleShot(True)
        timer.timeout.connect(now_btn.click)
        timer.start(30_000)
        self._after_box = box
        bring_to_front(self)
        box.open()

    # ---- zamanlayıcı
    def _schedule_at(self):
        try:
            return datetime.fromisoformat(self._config.get("schedule_at"))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _next_occurrence(hhmm: str) -> datetime:
        try:
            hour, minute = (int(x) for x in hhmm.split(":"))
        except ValueError:
            hour, minute = 2, 0
        now = datetime.now()
        at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return at if at > now else at + timedelta(days=1)

    def _hold_text(self) -> str:
        if not self._manager.is_on_hold():
            return ""
        at = self._schedule_at()
        return f"{at:%H:%M}'de başlayacak" if at else "Zamanlayıcı bekliyor"

    def _apply_schedule(self) -> None:
        at = self._schedule_at()
        if self._config.get("schedule_enabled") and at is not None:
            if datetime.now() < at:
                self._manager.set_hold(True)
                return
            self._schedule_tick()
            return
        self._manager.set_hold(False)

    def _schedule_tick(self) -> None:
        if not self._config.get("schedule_enabled"):
            return
        at = self._schedule_at()
        if at is None or datetime.now() < at:
            return
        self._config.set("schedule_enabled", False)
        self._config.set("schedule_at", "")
        self._manager.set_hold(False)
        self.done_banner.setText("⏰ Zamanlı indirmeler başladı")
        self.done_banner.show()
        self._banner_timer.start(9000)
        if self._tray is not None and not self.isVisible():
            self._tray.showMessage("İnternet Download Assistant", "Zamanlı indirmeler başladı.",
                                   QSystemTrayIcon.Information, 4000)

    def _open_schedule(self) -> None:
        dialog = ScheduleDialog(self._config, self)
        if not dialog.exec():
            return
        enabled, hhmm, action = dialog.values()
        self._config.set("after_all_action", action)
        self._config.set("schedule_time", hhmm)
        self._config.set("schedule_enabled", enabled)
        self._config.set("schedule_at", self._next_occurrence(hhmm).isoformat(timespec="seconds") if enabled else "")
        self._apply_schedule()
        self._show_message(f"Zamanlayıcı kuruldu: {self._hold_text()}" if enabled else "Zamanlayıcı kapalı",
                           transient=True)

    # ---- sıra düzenleme
    def _card_menu(self, task_id: str, card, pos) -> None:
        menu = self._build_card_menu(task_id)
        if menu is not None:
            menu.exec(card.mapToGlobal(pos))

    def _card_menu_legacy(self, task_id: str, card, pos) -> None:
        status = self._statuses.get(task_id)
        if status not in (TaskStatus.QUEUED, TaskStatus.PAUSED):
            return
        menu = QMenu(self)
        if status == TaskStatus.QUEUED:
            menu.addAction("Sıranın başına al", lambda: self._move_task(task_id, "top"))
            menu.addAction("Bir yukarı", lambda: self._move_task(task_id, "up"))
            menu.addAction("Bir aşağı", lambda: self._move_task(task_id, "down"))
            menu.addAction("Sıranın sonuna al", lambda: self._move_task(task_id, "bottom"))
        else:
            menu.addAction("Sürdür ve sıranın başına al", lambda: self._resume_first(task_id))
        menu.exec(card.mapToGlobal(pos))

    def _move_task(self, task_id: str, where: str) -> None:
        if self._manager.move_in_queue(task_id, where):
            self._sync_queue_order()

    def _resume_first(self, task_id: str) -> None:
        self._manager.resume(task_id)
        self._manager.move_in_queue(task_id, "top")
        QTimer.singleShot(200, self._sync_queue_order)

    def _sync_queue_order(self) -> None:
        """Sıradaki kartları, kendi yerlerini koruyarak çalışma sırasına göre (üstteki önce) dizer."""
        cards = [self._rows[i] for i in self._manager.queue_order() if i in self._rows]
        if len(cards) < 2:
            return
        slots = sorted(self.list_layout.indexOf(c) for c in cards)
        if [self.list_layout.indexOf(c) for c in cards] == slots:
            return
        for c in cards:
            self.list_layout.removeWidget(c)
        for slot, c in zip(slots, cards):
            self.list_layout.insertWidget(slot, c)

    def _drop_card(self, src_id: str, y: int) -> None:
        if self._statuses.get(src_id) != TaskStatus.QUEUED:
            return
        for tid, card in self._rows.items():
            if tid == src_id or self._statuses.get(tid) != TaskStatus.QUEUED or not card.isVisible():
                continue
            geo = card.geometry()
            if geo.top() <= y <= geo.bottom():
                if self._manager.place_in_queue(src_id, tid, y < geo.center().y()):
                    self._sync_queue_order()
                return

    def dragEnterEvent(self, event) -> None:
        mime = event.mimeData()
        if mime.hasFormat(_MIME):
            return                                  # kartları sıralama sürüklemesi: liste kendisi işler
        if mime.hasUrls() or mime.hasText():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        """Tarayıcıdan bırakılan bağlantı ya da metin: bağlantı kutularına konur ve normal "İndir" akışı çalışır."""
        mime = event.mimeData()
        if mime.hasFormat(_MIME):
            return
        urls = [u.toString() for u in mime.urls() if u.scheme() in ("http", "https")]
        if not urls and mime.hasText():
            urls = URL_PATTERN.findall(mime.text())
        if not urls:
            return
        event.acceptProposedAction()
        self._select_tab(0)
        for url in urls:
            target = next((f for f in self.link_fields.fields if not f.text().strip()), None)
            if target is None:
                last = self.link_fields.fields[-1]
                last.setText(f"{last.text().strip()} {url}".strip())
            else:
                target.setText(url)
        self._on_add()

    def eventFilter(self, obj, event) -> bool:
        etype = event.type()
        if self._band_event(obj, etype, event):
            return True
        if obj is self.list_host:
            if etype in (QEvent.DragEnter, QEvent.DragMove) and event.mimeData().hasFormat(_MIME):
                event.acceptProposedAction()
                return True
            if etype == QEvent.Drop and event.mimeData().hasFormat(_MIME):
                self._drop_card(bytes(event.mimeData().data(_MIME)).decode(), int(event.position().y()))
                event.acceptProposedAction()
                return True
            return False
        task_id = obj.property("taskId") if isinstance(obj, QWidget) else None
        if task_id:
            if etype == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._drag_start = (task_id, event.globalPosition().toPoint())
            elif etype == QEvent.MouseButtonRelease:
                self._drag_start = None
            elif etype == QEvent.MouseMove and self._drag_start and event.buttons() & Qt.LeftButton:
                start_id, origin = self._drag_start
                moved = (event.globalPosition().toPoint() - origin).manhattanLength()
                far = moved >= QApplication.startDragDistance() * 2
                if far and self._statuses.get(start_id) == TaskStatus.COMPLETED and start_id in self._selected_ids():
                    self._drag_start = None
                    self._start_file_drag(obj, start_id)
                elif far and self._statuses.get(start_id) == TaskStatus.QUEUED:
                    self._drag_start = None
                    drag = QDrag(obj)
                    mime = QMimeData()
                    mime.setData(_MIME, start_id.encode())
                    drag.setMimeData(mime)
                    drag.setPixmap(obj.grab().scaledToWidth(360))
                    drag.exec(Qt.MoveAction)
        return super().eventFilter(obj, event)

    # ---- kopya denetimi, dosya sürükleme, disk bütçesi, ağ, istatistik
    def _dup_ok(self, url: str, name: str = "", size: int = 0) -> bool:
        found = self._manager.find_completed(url, name, size)
        if found is None:
            return True
        box = QMessageBox(QMessageBox.Question, "Bu dosya zaten indirilmiş",
                          f"Bu dosya daha önce indirilmiş:\n{found.final_path}\n\nYine de yeniden indirilsin mi?",
                          QMessageBox.NoButton, self)
        again = box.addButton("Yine de indir", QMessageBox.AcceptRole)
        show = box.addButton("Dosyayı göster", QMessageBox.ActionRole)
        box.addButton("Vazgeç", QMessageBox.RejectRole)
        box.setDefaultButton(show)
        box.exec()
        clicked = box.clickedButton()
        if clicked is show:
            self._open_folder(found.id)
            return False
        return clicked is again

    def _start_file_drag(self, widget, task_id: str) -> None:
        """Tamamlanan indirmenin dosyasını (kopyalama olarak) tarayıcıya / klasöre sürüklemek için."""
        ids = [task_id]
        chosen = self._selected_ids()
        if task_id in chosen:            # seçili kartlardan biri tutulduysa seçilen tamamlanmışların hepsi sürüklenir
            ids = [i for i in chosen if self._statuses.get(i) == TaskStatus.COMPLETED] or [task_id]
        urls = []
        for i in ids:
            t = self._manager.get_task(i)
            if t is not None and t.final_path and Path(t.final_path).is_file():
                urls.append(QUrl.fromLocalFile(str(t.final_path)))
        if not urls:
            return
        drag = QDrag(widget)
        mime = QMimeData()
        mime.setUrls(urls)
        drag.setMimeData(mime)
        drag.setPixmap(widget.grab().scaledToWidth(360))
        drag.exec(Qt.CopyAction)

    def _band_select(self, rect, base=None) -> None:
        """rect ile kesişen görünür satırları (ve base kümesindekileri) seçer, diğerlerinin seçimini kaldırır."""
        base = base or set()
        for tid, box in self._checks.items():
            row = self._rows.get(tid)
            hit = row is not None and row.isVisible() and row.geometry().intersects(rect)
            want = hit or tid in base
            if box.isChecked() != want:
                box.setChecked(want)

    def _band_event(self, obj, etype, event) -> bool:
        """Boş alanda (ya da kartta Alt ile) basılı tutup sürükleyince saydam seçim kutusu çizer ve değdiği kartları seçer."""
        if etype == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            if self._band_origin is not None:
                return False
            pos = self.list_host.mapFromGlobal(event.globalPosition().toPoint())
            start = False
            if obj is self.list_host or obj is self.scroll.viewport() or obj is self.empty_box:
                start = True                       # herhangi bir boşluk
            elif isinstance(obj, QWidget) and obj.property("isRow"):
                start = True                       # satırın kendi boşlukları (kart dışı)
            elif isinstance(obj, QWidget) and obj.property("taskId"):
                tid = obj.property("taskId")
                if event.modifiers() & Qt.AltModifier:
                    start = True
                else:   # seçili olmayan (ve sıralanabilir olmayan) kartta basılı tutmak da seçim kutusunu başlatır
                    start = tid not in self._selected_ids() and self._statuses.get(tid) != TaskStatus.QUEUED
            if not start:
                return False
            self._band_origin = pos
            self._band_moved = False
            self._band_base = set(self._selected_ids()) if event.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier) else set()
            self._drag_start = None
            if self._band is None:
                self._band = SelectionBand(self.list_host)
            self._band.setGeometry(QRect(pos, pos))
            return True
        if self._band_origin is None:
            return False
        if etype == QEvent.MouseMove:
            gp = event.globalPosition().toPoint()
            pos = self.list_host.mapFromGlobal(gp)
            if not self._band_moved and (pos - self._band_origin).manhattanLength() < 5:
                return True
            self._band_moved = True
            rect = QRect(self._band_origin, pos).normalized()
            self._band.setGeometry(rect)
            self._band.show()
            self._band.raise_()
            self._band_select(rect, self._band_base)
            viewport = self.scroll.viewport()
            y = viewport.mapFromGlobal(gp).y()
            bar = self.scroll.verticalScrollBar()
            if y < 20:
                bar.setValue(bar.value() - 24)
            elif y > viewport.height() - 20:
                bar.setValue(bar.value() + 24)
            return True
        if etype == QEvent.MouseButtonRelease:
            if not self._band_moved and not self._band_base:
                self._toggle_all(False)          # boşluğa tek tıklama seçimi temizler
            if self._band is not None:
                self._band.hide()
            self._band_origin = None
            self._band_moved = False
            return True
        return False

    def _disk_budget_check(self) -> None:
        need = 0
        for t in self._manager.tasks():
            if t.status in _ACTIVE:
                size = t.file_size or int(t.options.get("expected_size") or 0)
                need += max(0, int(size) - int(t.downloaded or 0))
        if need <= 0:
            self._disk_warned = -1
            return
        folder = Path(self._config.download_dir())
        while not folder.exists() and folder != folder.parent:
            folder = folder.parent
        try:
            free = shutil.disk_usage(folder).free
        except OSError:
            return
        if need * 1.05 > free:
            key = need // (256 * 1024 * 1024)
            if key != self._disk_warned:
                self._disk_warned = key
                self._show_message(f"⚠ Kuyruktaki indirmeler yaklaşık {format_size(need)} yer istiyor, "
                                   f"hedef sürücüde {format_size(free)} boş. Yer açmazsan indirmeler yarıda kalabilir.")
        else:
            self._disk_warned = -1

    def _on_net_changed(self, online: bool) -> None:
        if online:
            count = self._manager.net_resume()
            text = "🌐 İnternet geri geldi: duraklatılan indirmeler sürdürüldü"
        else:
            count = self._manager.net_pause()
            text = "⚠ İnternet bağlantısı kesildi: indirmeler bağlantı gelince kendiliğinden sürecek"
        if count:
            self.done_banner.setText(text)
            self.done_banner.show()
            self._banner_timer.start(9000)

    def _open_stats(self) -> None:
        path = self._manager.stats_file()
        dialog = StatsDialog(_stats.summary(path), self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    # ---- seçim, hız, "bitince", kart menüsü
    _AFTER = (("Hiçbir şey yapma", "none"), ("Bilgisayarı uykuya al", "sleep"),
              ("Bilgisayarı kapat", "shutdown"), ("Bilgisayarı yeniden başlat", "restart"))
    _AFTER_SHORT = {"sleep": "Uyut", "shutdown": "Kapat", "restart": "Yeniden başlat", "open": "Aç"}

    # ---- geçmiş arama
    def _build_search_row(self, parent_layout) -> None:
        from PySide6.QtWidgets import QComboBox
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Ara: ad, başlık ya da bağlantı…")
        self.search_edit.setClearButtonEnabled(True)
        self.date_combo = QComboBox()
        for label, days in (("Tüm tarihler", 0), ("Bugün", 1), ("Son 7 gün", 7), ("Son 30 gün", 30)):
            self.date_combo.addItem(label, days)
        self.date_combo.setStyleSheet(
            f"QComboBox {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; border-radius: 10px; "
            f"padding: 8px 12px; min-width: 130px; color: {theme.TEXT}; }}"
            f"QComboBox QAbstractItemView {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; "
            f"selection-background-color: {theme.SURFACE_2}; color: {theme.TEXT}; outline: none; }}")
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.search_edit, 1)
        row.addWidget(self.date_combo)
        stats_btn = QPushButton("İstatistik")
        stats_btn.setObjectName("chip")
        stats_btn.setCursor(Qt.PointingHandCursor)
        stats_btn.clicked.connect(self._open_stats)
        row.addWidget(stats_btn)
        parent_layout.addLayout(row)
        self.search_edit.textChanged.connect(lambda _t: self._refresh_all())
        self.date_combo.currentIndexChanged.connect(lambda _i: self._refresh_all())

    def _matches_search(self, task_id: str) -> bool:
        needle = self.search_edit.text().strip().lower()
        days = int(self.date_combo.currentData() or 0)
        if not needle and not days:
            return True
        task = self._manager.get_task(task_id)
        if task is None:
            return True
        if needle:
            hay = " ".join([task.title or "", task.file_name or "", task.url or "",
                            str(task.final_path or "")]).lower()
            if needle not in hay:
                return False
        if days:
            when = task.finished_at or task.created_at
            start = (datetime.now() - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
            if when < start:
                return False
        return True

    # ---- gruplama (oynatma listeleri)
    def _make_group_header(self, gid: str, name: str) -> dict:
        header = QFrame()
        header.setObjectName("summaryBar")
        lay = QHBoxLayout(header)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(10)
        check = QCheckBox()
        check.setToolTip("Gruptaki tüm indirmeleri seç")
        toggle = QToolButton()
        toggle.setObjectName("moreBtn")
        toggle.setText("▾")
        toggle.setCursor(Qt.PointingHandCursor)
        toggle.setFixedSize(30, 30)
        title = QLabel(name)
        title.setObjectName("fileName")
        summary = QLabel("")
        summary.setObjectName("info")
        lay.addWidget(check)
        lay.addWidget(toggle)
        lay.addWidget(title, 1)
        lay.addWidget(summary)
        group = {"header": header, "toggle": toggle, "title": title, "summary": summary,
                 "check": check, "collapsed": False}
        toggle.clicked.connect(lambda _=False, g=gid: self._toggle_group(g))
        check.toggled.connect(lambda on, g=gid: self._check_group(g, on))
        return group

    def _group_members(self, gid: str) -> list:
        return [t for t, g in self._task_group.items() if g == gid]

    def _join_group(self, task: DownloadTask) -> None:
        name = (task.options or {}).get("group")
        if not name:
            return
        gid = task.options.get("group_id") or name
        self._task_group[task.id] = gid
        if gid not in self._groups:
            self._groups[gid] = self._make_group_header(gid, name)
        self._regroup(gid)

    def _regroup(self, gid: str) -> None:
        """Grubun başlığını ve satırlarını listede ardışık, başlık üstte olacak biçimde dizer."""
        rows = [self._rows[t] for t in self._group_members(gid) if t in self._rows]
        if not rows:
            return
        header = self._groups[gid]["header"]
        self.list_layout.removeWidget(header)
        rows.sort(key=lambda r: self.list_layout.indexOf(r))
        top = min(self.list_layout.indexOf(r) for r in rows)
        for r in rows:
            self.list_layout.removeWidget(r)
        self.list_layout.insertWidget(top, header)
        for i, r in enumerate(rows, 1):
            self.list_layout.insertWidget(top + i, r)

    def _forget_group(self, task_id: str) -> None:
        gid = self._task_group.pop(task_id, None)
        if gid and not self._group_members(gid):
            group = self._groups.pop(gid, None)
            if group is not None:
                self.list_layout.removeWidget(group["header"])
                group["header"].deleteLater()

    def _toggle_group(self, gid: str) -> None:
        group = self._groups.get(gid)
        if group is not None:
            group["collapsed"] = not group["collapsed"]
            self._refresh_all()

    def _check_group(self, gid: str, on: bool) -> None:
        for t in self._group_members(gid):
            box = self._checks.get(t)
            if box is not None:
                box.setChecked(bool(on))

    def _refresh_groups(self, matched: set) -> None:
        for gid, group in self._groups.items():
            ids = self._group_members(gid)
            done = sum(1 for t in ids if self._statuses.get(t) == TaskStatus.COMPLETED)
            running = sum(1 for t in ids if self._statuses.get(t) in _RUNNING)
            failed = sum(1 for t in ids if self._statuses.get(t) == TaskStatus.ERROR)
            parts = [f"{len(ids)} video", f"{done} tamamlandı"]
            if running:
                parts.append(f"{running} iniyor")
            if failed:
                parts.append(f"{failed} hatalı")
            group["summary"].setText("   ·   ".join(parts))
            group["toggle"].setText("▸" if group["collapsed"] else "▾")
            group["header"].setVisible(gid in matched)
            check = group["check"]
            check.blockSignals(True)
            check.setChecked(bool(ids) and all(self._checks[t].isChecked() for t in ids if t in self._checks))
            check.blockSignals(False)

    # ---- tepsi hızlı menüsü
    def _tray_resume_all(self) -> None:
        for task in self._manager.tasks():
            if task.status == TaskStatus.PAUSED:
                self._manager.resume(task.id)

    def _refresh_tray_menu(self) -> None:
        current = int(self._config.get("speed_limit_kbps"))
        speed = self._tray_speed_menu
        speed.clear()
        speed.setTitle(f"Genel hız sınırı: {limit_text(current)}")
        speed.addAction("Arttır").triggered.connect(lambda _=False: self._set_global_limit(step_limit(current, True)))
        speed.addAction("Düşür").triggered.connect(lambda _=False: self._set_global_limit(step_limit(current, False)))
        speed.addAction("Maksimum hız (sınırsız)").triggered.connect(lambda _=False: self._set_global_limit(0))
        after = self._tray_after_menu
        after.clear()
        action = self._config.get("after_all_action")
        for label, value in self._AFTER:
            act = after.addAction(("✓  " if value == action else "     ") + label)
            act.triggered.connect(lambda _=False, v=value: self._set_after(v, []))

    def _make_row(self, task_id: str, card) -> QWidget:
        row = QWidget()
        row.setObjectName("listHost")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(12, 0, 0, 0)       # sol boşluk: buradan sürükleyerek toplu seçim başlar
        lay.setSpacing(8)
        box = QCheckBox()
        box.setToolTip("Seç (toplu işlemler için)")
        box.setCursor(Qt.PointingHandCursor)
        box.toggled.connect(lambda _on: self._refresh_selection_ui())
        more = QToolButton()
        more.setObjectName("moreBtn")
        more.setText("⋯")
        more.setCursor(Qt.PointingHandCursor)
        more.setFixedSize(34, 34)
        more.setToolTip("Diğer işlemler")
        more.clicked.connect(lambda _=False, tid=task_id, b=more: self._show_more(tid, b))
        lay.addWidget(box, 0, Qt.AlignVCenter)
        lay.addWidget(card, 1)
        lay.addWidget(more, 0, Qt.AlignVCenter)
        row.installEventFilter(self)
        row.setProperty("isRow", True)
        self._checks[task_id] = box
        self._mores[task_id] = more
        return row

    def _selected_ids(self) -> list:
        return [tid for tid, box in self._checks.items() if box.isChecked() and tid in self._cards]

    def _toggle_all(self, checked: bool) -> None:
        for tid, box in self._checks.items():
            row = self._rows.get(tid)
            box.setChecked(bool(checked) and row is not None and not row.isHidden())

    def _refresh_selection_ui(self) -> None:
        selected = self._selected_ids()
        n = len(selected)
        was, self._sel_mode = self._sel_mode, bool(n)
        if was and not n:
            self._refresh_all()           # seçim bitti: düğmeler genel duruma döner
            return
        visible = [t for t, r in self._rows.items() if not r.isHidden()]
        self.select_box.blockSignals(True)
        self.select_box.setText(f"{n} seçili" if n else "Tümünü seç")
        self.select_box.setChecked(bool(visible) and all(self._checks[t].isChecked() for t in visible))
        self.select_box.blockSignals(False)
        active = [i for i in selected if self._statuses.get(i) in _ACTIVE]
        if n:
            statuses = [self._statuses.get(i) for i in selected]
            self.pause_all_btn.setEnabled(any(s in (TaskStatus.QUEUED, *_RUNNING) for s in statuses))
            self.resume_all_btn.setEnabled(any(s in (TaskStatus.PAUSED, TaskStatus.ERROR, TaskStatus.CANCELLED)
                                               for s in statuses))
            self.clear_btn.setEnabled(True)
            self.clear_btn.setText(" Sil")
        else:
            self.clear_btn.setText(" Temizle")
        self.tools_btn.setEnabled(not n or bool(active))
        if self._after_ids:
            self.tools_btn.setText(f" Bitince: {self._AFTER_SHORT.get(self._after_action, '')}")
        else:
            action = self._config.get("after_all_action")
            self.tools_btn.setText(f" Bitince: {self._AFTER_SHORT[action]}" if action in self._AFTER_SHORT
                                   else " Bitince…")
        self.speed_btn.setText(f"Hız sınırı: {limit_text(int(self._config.get('speed_limit_kbps')))}")

    # ---- bitince yapılacaklar
    def _after_target_ids(self) -> list:
        return [i for i in self._selected_ids() if self._statuses.get(i) in _ACTIVE]

    def _fill_after_menu(self, menu) -> None:
        menu.clear()
        ids = self._after_target_ids()
        head = menu.addAction(f"Seçili {len(ids)} indirme tamamlanınca:" if ids else "Tüm indirmeler tamamlanınca:")
        head.setEnabled(False)
        if ids:
            current = self._after_action if self._after_ids and set(ids) == self._after_ids else "none"
        else:
            current = self._config.get("after_all_action")
        options = list(self._AFTER) + ([("Dosyayı aç", "open")] if ids else [])
        for label, value in options:
            act = menu.addAction(("✓  " if value == current else "     ") + label)
            act.triggered.connect(lambda _=False, v=value, i=list(ids): self._set_after(v, i))

    def _set_after(self, value: str, ids: list) -> None:
        if ids:
            self._after_ids = set(ids) if value != "none" else None
            self._after_action = value
        else:
            self._config.set("after_all_action", value)
        self._refresh_selection_ui()

    def _check_scoped_after(self) -> None:
        if not self._after_ids:
            return
        known = [i for i in self._after_ids if i in self._statuses]
        if not known or any(self._statuses[i] in _ACTIVE for i in known):
            if not known:
                self._after_ids = None
            return
        completed = any(self._statuses[i] == TaskStatus.COMPLETED for i in known)
        action = self._after_action
        self._after_ids, self._after_action = None, "none"
        if completed and action == "open":
            self._open_completed(known)
        elif completed and action != "none":
            self._confirm_power_action(action)

    # ---- hız
    def _open_speed_menu(self) -> None:
        menu = QMenu(self)
        current = int(self._config.get("speed_limit_kbps"))
        menu.addAction(f"Genel hız sınırı: {limit_text(current)}").setEnabled(False)
        menu.addAction("Arttır", lambda: self._set_global_limit(step_limit(current, True)))
        menu.addAction("Düşür", lambda: self._set_global_limit(step_limit(current, False)))
        menu.addAction("Maksimum hız (sınırsız)", lambda: self._set_global_limit(0))
        selected = [i for i in self._selected_ids() if self._statuses.get(i) in _ACTIVE]
        if selected:
            menu.addSeparator()
            menu.addAction(f"Seçili {len(selected)} indirme").setEnabled(False)
            menu.addAction("Arttır", lambda: [self._task_speed(i, "up") for i in selected])
            menu.addAction("Düşür", lambda: [self._task_speed(i, "down") for i in selected])
            menu.addAction("Maksimum hız (sınırsız)", lambda: [self._task_speed(i, "max") for i in selected])
        pos = self.speed_btn.mapToGlobal(self.speed_btn.rect().topLeft())
        pos.setY(pos.y() - menu.sizeHint().height())
        menu.exec(pos)

    def _set_global_limit(self, kbps: int) -> None:
        self._config.set("speed_limit_kbps", int(kbps))
        self._manager.apply_settings()
        self._show_message(f"Genel hız sınırı: {limit_text(int(kbps))}", transient=True)
        self._refresh_selection_ui()

    def _task_speed(self, task_id: str, mode: str) -> None:
        current = self._manager.get_task_limit(task_id)
        if current == 0 and mode in ("up", "down") and task_id in self._listed:
            running = self._manager.get_task(task_id)
            measured = int((running.speed if running else 0) / 1024)
            if measured > 0:
                current = max(64, measured)        # sınırsızdan çıkarken şu anki hızdan başla
        new = 0 if mode == "max" else step_limit(current, mode == "up")
        self._manager.set_task_limit(task_id, new)
        self._show_message(f"Bu indirme için hız sınırı: {limit_text(new)}", transient=True)

    # ---- kart menüsü (⋯ ve sağ tık)
    def _show_more(self, task_id: str, button) -> None:
        menu = self._build_card_menu(task_id)
        if menu is not None:
            menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def _build_card_menu(self, task_id: str):
        task = self._manager.get_task(task_id)
        if task is None:
            return None
        status = task.status
        menu = QMenu(self)
        if status == TaskStatus.QUEUED:
            menu.addAction("Sıranın başına al", lambda: self._move_task(task_id, "top"))
            menu.addAction("Bir yukarı", lambda: self._move_task(task_id, "up"))
            menu.addAction("Bir aşağı", lambda: self._move_task(task_id, "down"))
            menu.addAction("Sıranın sonuna al", lambda: self._move_task(task_id, "bottom"))
        elif status == TaskStatus.PAUSED:
            menu.addAction("Sürdür ve sıranın başına al", lambda: self._resume_first(task_id))
        if status == TaskStatus.CANCELLED:      # hata kartında yeniden dene simgesi zaten var
            menu.addAction("Yeniden dene", lambda: self._manager.retry(task_id))
        if status == TaskStatus.COMPLETED and task.engine != "local":
            menu.addAction("Yeniden indir (baştan)", lambda: self._redownload(task_id))
        if status in (TaskStatus.QUEUED, TaskStatus.PAUSED, TaskStatus.PROBING, TaskStatus.DOWNLOADING,
                      TaskStatus.MERGING, TaskStatus.ERROR):
            sub = menu.addMenu(f"Hız sınırı: {limit_text(self._manager.get_task_limit(task_id))}")
            sub.addAction("Arttır", lambda: self._task_speed(task_id, "up"))
            sub.addAction("Düşür", lambda: self._task_speed(task_id, "down"))
            sub.addAction("Maksimum hız (sınırsız)", lambda: self._task_speed(task_id, "max"))
        if menu.actions():
            menu.addSeparator()
        if status == TaskStatus.COMPLETED and task.final_path and _archive.is_archive(task.final_path):
            menu.addAction("Arşivi aç (kendi adlı klasöre)…", lambda: self._extract_task(task_id))
        menu.addAction("Dosyayı doğrula (hash)…", lambda: self._hash_for(task_id))
        if task.engine == "http" and status != TaskStatus.COMPLETED:
            menu.addAction("İndirme adresini yenile", lambda: self._refresh_url_for(task_id))
        elif task.engine == "ytdlp" and status == TaskStatus.ERROR:
            menu.addAction("İndirme adresini yenile", lambda: self._manager.retry(task_id))
        menu.addAction("Bağlantıyı kopyala",
                       lambda: QApplication.clipboard().setText(task.options.get("original_url") or task.url))
        if task.engine == "http" and status != TaskStatus.COMPLETED:
            menu.addAction("Yedek adres ekle…", lambda: self._add_mirror_for(task_id))
            menu.addAction("Kullanıcı adı / şifre gir…", lambda: self._ask_auth_for(task_id))
        return menu

    def _redownload(self, task_id: str) -> None:
        task = self._manager.get_task(task_id)
        if task is None:
            return
        options = {k: v for k, v in task.options.items()
                   if k not in ("original_url", "speed_limit_kbps", "sort_by_category")}
        ytdlp = task.engine == "ytdlp"
        if ytdlp and not task.options.get("generic") and youtube.is_youtube_url(task.url):
            self._select_tab(1)                       # çözünürlük / ses / fps seçimi baştan sorulur
            self.youtube_page.analyze(task.url, force=True)
            return
        try:
            self._manager.add(task.url if ytdlp else (task.options.get("original_url") or task.url),
                              save_dir=task.save_dir, headers=dict(task.headers or {}),
                              engine="ytdlp" if ytdlp else "", options=options,
                              title=task.title, thumbnail_url=task.thumbnail_url)
        except (ValueError, RuntimeError) as e:
            self._show_message(f"Eklenemedi: {e}")
            return
        self._show_message("İndirme baştan listeye eklendi", transient=True)

    def _hash_for(self, task_id: str) -> None:
        task = self._manager.get_task(task_id)
        if task is None:
            return
        reason = ""
        if task.engine == "ytdlp":
            reason = "YouTube ve akış videoları için yayımlanmış bir karma değeri bulunmaz"
        elif task.status != TaskStatus.COMPLETED:
            reason = "indirme henüz tamamlanmadı"
        elif not task.final_path or not Path(task.final_path).is_file():
            reason = "dosya diskte bulunamadı (taşınmış ya da silinmiş olabilir)"
        if reason:
            QMessageBox.information(self, "Hash doğrulama",
                                    f"Bu indirme hash kontrolü için uygun değil: {reason}.")
            return
        try:
            path = Path(task.final_path)
            dialog = HashDialog([(task.file_name or path.name, path)], self)
            dialog.setAttribute(Qt.WA_DeleteOnClose)
            dialog.show()
        except Exception as e:
            log.exception("Hash penceresi açılamadı")
            QMessageBox.warning(self, "Hash doğrulama", f"Doğrulama başlatılamadı: {e}")

    def _delete_selected(self, ids: list, files: bool) -> None:
        """files=True: tamamlanmış dosyalar diskten de silinir (onayla). False: yalnızca uygulamadaki kayıt."""
        if files:
            names = [str(t.final_path) for t in (self._manager.get_task(i) for i in ids)
                     if t is not None and t.status == TaskStatus.COMPLETED and t.final_path
                     and Path(t.final_path).is_file()]
            text = (f"{len(names)} dosya diskten kalıcı olarak silinecek ve listeden kaldırılacak."
                    if names else f"Seçili {len(ids)} indirme listeden kaldırılacak (silinecek dosya yok).")
            if QMessageBox.question(self, "Silme onayı", text, QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) != QMessageBox.Yes:
                return
            failed = 0
            for path in names:
                try:
                    Path(path).unlink()
                except OSError:
                    failed += 1
            if failed:
                self._show_message(f"{failed} dosya silinemedi (açık ya da korumalı olabilir).")
        for task_id in ids:
            self._manager.remove(task_id)

    def _on_card_remove(self, task_id: str) -> None:
        """Karttaki çöp kutusu: indirilmiş dosya varsa "yalnızca geçmişten" mi "dosyayı da" mı diye sorar."""
        task = self._manager.get_task(task_id)
        if task is None:
            return
        has_file = (task.status == TaskStatus.COMPLETED and task.final_path
                    and Path(task.final_path).is_file())
        if not has_file:
            partial = " Yarım kalan parçalar da silinir." if task.downloaded > 0 else ""
            answer = QMessageBox.question(self, "Silme onayı", f"Bu indirme listeden silinsin mi?{partial}",
                                          QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer == QMessageBox.Yes:
                self._manager.remove(task_id)
            return
        menu = QMenu(self)
        menu.addAction("Dosyayı da sil (diskten kalıcı olarak)", lambda: self._delete_selected([task_id], True))
        menu.addAction("Yalnızca indirme geçmişinden sil", lambda: self._delete_selected([task_id], False))
        menu.exec(QCursor.pos())

    def _open_completed(self, ids) -> None:
        from utils.helpers import is_dangerous
        skipped = 0
        for task_id in ids:
            task = self._manager.get_task(task_id)
            if task is None or task.status != TaskStatus.COMPLETED or not task.final_path:
                continue
            path = Path(task.final_path)
            if not path.is_file():
                continue
            if is_dangerous(path.name):
                skipped += 1               # çalıştırılabilir dosyalar kendiliğinden açılmaz
                continue
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        if skipped:
            self._show_message(f"{skipped} çalıştırılabilir dosya güvenlik için otomatik açılmadı.")

    def _build_lists_bar(self, parent_layout) -> None:
        self.lists_bar = QFrame()
        self.lists_bar.setObjectName("summaryBar")
        row = QHBoxLayout(self.lists_bar)
        row.setContentsMargins(16, 10, 10, 10)
        row.setSpacing(10)
        self.lists_info = QLabel("Liste boş")
        self.lists_info.setObjectName("info")
        self.list_start_btn = QPushButton("  Listeyi indir")
        self.list_start_btn.setObjectName("primary")
        self.list_start_btn.setIcon(get_icon("download", color=theme.BG, hover=theme.BG,
                                             disabled=theme.TEXT_DIM, size=20))
        self.list_start_btn.setCursor(Qt.PointingHandCursor)
        self.list_start_btn.setMinimumWidth(200)
        menu = QMenu(self.list_start_btn)
        menu.aboutToShow.connect(lambda m=menu: self._fill_list_menu(m))
        self.list_start_btn.setMenu(menu)
        row.addWidget(self.lists_info, 1)
        row.addWidget(self.list_start_btn)
        self.lists_bar.hide()
        parent_layout.addWidget(self.lists_bar)

    def _fill_list_menu(self, menu) -> None:
        menu.clear()
        chosen = [i for i in self._selected_ids() if i in self._listed]
        head = menu.addAction(f"Seçili {len(chosen)} indirme" if chosen else "Listedeki tüm bekleyenler")
        head.setEnabled(False)
        menu.addAction("1 · Hepsini aynı anda indir", lambda: self._start_list("parallel"))
        menu.addAction("2 · Sırayla indir", lambda: self._start_list("sequential"))
        menu.addAction("3 · Zamanlayarak indir…", self._schedule_list)
        if str(self._config.get("list_schedule_at") or ""):
            menu.addSeparator()
            menu.addAction("Zamanlayıcıyı iptal et", self._cancel_list_schedule)

    def _start_list(self, mode: str, use_selection: bool = True) -> None:
        """Listedeki bekleyenleri başlatır (kart seçiliyse yalnızca onları). mode: parallel | sequential."""
        pool = [i for i in self._selected_ids() if i in self._listed] if use_selection else []
        pool = pool or [i for i in self._listed if i in self._statuses]
        staged = []
        for tid in pool:
            task = self._manager.get_task(tid)
            if task is not None and task.options.get("staged"):
                staged.append(tid)
        if not staged:
            self._show_message("Başlatılacak bekleyen indirme yok.", transient=True)
            return
        order = {tid: n for n, tid in enumerate(self._manager.queue_order())}
        staged.sort(key=lambda t: order.get(t, 10 ** 9))
        self._config.set("list_mode", mode)
        started = self._manager.start_staged(staged)
        how = "sırayla" if mode == "sequential" else "aynı anda"
        self._show_message(f"{started} indirme {how} başlıyor", transient=True)

    def _schedule_list(self) -> None:
        from gui.components.list_schedule_dialog import ListScheduleDialog
        dialog = ListScheduleDialog(self._config, self)
        if not dialog.exec():
            return
        hhmm, mode = dialog.values()
        self._config.set("schedule_time", hhmm)
        self._config.set("list_schedule_mode", mode)
        self._config.set("list_schedule_at", self._next_occurrence(hhmm).isoformat(timespec="seconds"))
        self._refresh_lists_bar()
        self._show_message(f"Liste {hhmm}'de başlayacak", transient=True)

    def _cancel_list_schedule(self) -> None:
        self._config.set("list_schedule_at", "")
        self._refresh_lists_bar()
        self._show_message("Liste zamanlayıcısı iptal edildi", transient=True)

    def _list_schedule_tick(self) -> None:
        raw = str(self._config.get("list_schedule_at") or "")
        if not raw:
            return
        try:
            at = datetime.fromisoformat(raw)
        except ValueError:
            self._config.set("list_schedule_at", "")
            return
        if datetime.now() < at:
            return
        mode = str(self._config.get("list_schedule_mode") or "parallel")
        self._config.set("list_schedule_at", "")
        self._start_list(mode, use_selection=False)
        self.done_banner.setText("⏰ Zamanlı liste indirmesi başladı")
        self.done_banner.show()
        self._banner_timer.start(9000)

    def _refresh_lists_bar(self) -> None:
        ids = [i for i in self._listed if i in self._statuses]
        waiting = sum(1 for i in ids if i in self._staged)
        running = sum(1 for i in ids if self._statuses[i] in _RUNNING)
        queued = sum(1 for i in ids if self._statuses[i] == TaskStatus.QUEUED and i not in self._staged)
        done = sum(1 for i in ids if self._statuses[i] == TaskStatus.COMPLETED)
        parts = [f"{waiting} beklemede", f"{running} iniyor"]
        if queued:
            parts.append(f"{queued} sırada")
        parts.append(f"{done} tamamlandı")
        raw = str(self._config.get("list_schedule_at") or "")
        if raw:
            try:
                at = datetime.fromisoformat(raw)
                how = "sırayla" if self._config.get("list_schedule_mode") == "sequential" else "hepsi aynı anda"
                parts.insert(0, f"⏰ {at:%H:%M}'de başlayacak ({how})")
            except ValueError:
                pass
        self.lists_info.setText("   ·   ".join(parts))
        self.list_start_btn.setEnabled(waiting > 0)

    # ---- hata kurtarma
    def _on_retry(self, task_id: str) -> None:
        """Kartın "Tekrar dene" düğmesi. Bu deneme de hata verirse yeniden yakalama önerilir."""
        self._retried.add(task_id)
        self._manager.retry(task_id)

    def _page_for(self, task) -> str:
        """İndirmenin geldiği kaynak sayfa: paylaşım linki, yakalandığı sayfa ya da Referer."""
        options = task.options or {}
        for candidate in (options.get("original_url"), options.get("page_url"), (task.headers or {}).get("Referer")):
            if candidate and str(candidate).lower().startswith(("http://", "https://")):
                return str(candidate)
        return ""

    def _offer_recapture(self, task_id: str) -> None:
        task = self._manager.get_task(task_id)
        if task is None or task.status != TaskStatus.ERROR or task.engine != "http":
            return
        if "boş alan" in (task.error or "") or not self._page_for(task):
            return
        answer = QMessageBox.question(
            self, "İndirme alınamadı",
            "Bu indirme yeniden denendi ama başlatılamadı. Adresin süresi dolmuş olabilir.\n\n"
            "İndirmeyi yeniden yakalamak ister misin? Seni dosyanın indirildiği sayfaya götüreyim; "
            "orada indirmeyi başlatınca adres yenilenir ve indirme kaldığı yerden devam eder.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if answer == QMessageBox.Yes:
            self._open_refresh_page(task_id)

    def _refresh_url_for(self, task_id: str) -> None:
        """⋯ > İndirme adresini yenile: geçerli adres gösterilir; yeni adres yapıştırılır ya da kaynak sayfaya gidilir."""
        task = self._manager.get_task(task_id)
        if task is None or task.status == TaskStatus.COMPLETED:
            return
        if task.engine != "http":
            if task.status in (TaskStatus.ERROR, TaskStatus.CANCELLED):
                self._manager.retry(task_id)
            else:
                self._show_message("Bu indirme türünde adres yenileme yalnızca yeniden denemedir.", transient=True)
            return
        from gui.components.refresh_url_dialog import RefreshUrlDialog
        dialog = RefreshUrlDialog(task.url, bool(self._page_for(task)), self)
        if not dialog.exec():
            return
        if dialog.action == "browser":
            self._open_refresh_page(task_id)
        else:
            self._apply_pasted_url(task_id, dialog.url())

    def _apply_pasted_url(self, task_id: str, url: str) -> None:
        if not url.lower().startswith(("http://", "https://")):
            QMessageBox.warning(self, "Geçersiz adres", "Adres http:// veya https:// ile başlamalı.")
            return
        task = self._manager.get_task(task_id)
        if task is None:
            return
        headers = dict(task.headers or {})
        self._show_message("Adres denetleniyor…")

        def work() -> None:
            info = urlcheck.probe(url, headers)
            try:
                self._url_checked.emit(task_id, url, info)
            except RuntimeError:
                pass

        threading.Thread(target=work, daemon=True, name="url-check").start()

    def _on_url_checked(self, task_id: str, url: str, info: dict) -> None:
        self._show_message("")
        task = self._manager.get_task(task_id)
        if task is None:
            return
        if not info.get("ok"):
            QMessageBox.warning(self, "Adrese ulaşılamadı",
                                f"{info.get('error') or 'Adres açılamadı.'}\n\nİndirme değiştirilmedi.")
            return
        size, known = int(info.get("size") or 0), int(task.file_size or 0)
        if info.get("page") or (size > 0 and known > 0 and size != known):
            reason = ("Bu adres bir web sayfasına çıkıyor, dosyaya değil." if info.get("page") else
                      f"Bu adres farklı bir dosya gibi görünüyor (boyut {format_size(size)}, beklenen {format_size(known)}).")
            if self._page_for(task):
                answer = QMessageBox.question(
                    self, "Aynı dosya değil",
                    f"{reason}\n\nGerçek siteye gidip indirmeyi oradan yeniden yakalamak ister misin?",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                if answer == QMessageBox.Yes:
                    self._open_refresh_page(task_id)
            else:
                QMessageBox.warning(self, "Aynı dosya değil", f"{reason}\n\nİndirme değiştirilmedi.")
            return
        if self._statuses.get(task_id) in (*_RUNNING, TaskStatus.QUEUED):
            self._manager.pause(task_id)
        if self._manager.refresh_url(task_id, url):
            self._show_message("İndirme adresi yenilendi: kaldığı yerden devam ediyor", transient=True)
        else:
            self._show_message("Adres uygulanamadı: indirme şu an bu işleme uygun değil.")

    def _open_refresh_page(self, task_id: str) -> None:
        task = self._manager.get_task(task_id)
        if task is None:
            return
        page = self._page_for(task)
        if not page:
            self._show_message("Bu indirme için kayıtlı bir kaynak sayfa yok.", transient=True)
            return
        self._refresh_wait = {"id": task_id, "until": datetime.now() + timedelta(minutes=10)}
        QDesktopServices.openUrl(QUrl(page))
        self._show_message("Sayfa tarayıcıda açıldı: indirmeyi orada yeniden başlat. "
                           "Adres kendiliğinden yenilenip indirme kaldığı yerden sürecek (10 dk içinde).")

    def _try_refresh_capture(self, payload: dict) -> bool:
        """Adres yenileme bekleniyorsa yakalanan yeni adresi bekleyen indirmeye bağlar (onay penceresi açılmaz)."""
        wait = self._refresh_wait
        if not wait:
            return False
        if datetime.now() > wait["until"]:
            self._refresh_wait = None
            return False
        if payload.get("localPath"):
            return False
        headers = {}
        if payload.get("referrer"):
            headers["Referer"] = payload["referrer"]
        if payload.get("cookies"):
            headers["Cookie"] = payload["cookies"]
        if payload.get("userAgent"):
            headers["User-Agent"] = payload["userAgent"]
        task_id = wait["id"]
        known_task = self._manager.get_task(task_id)
        new_size = int(payload.get("fileSize") or 0)
        known_size = int(known_task.file_size or 0) if known_task else 0
        if known_task is not None and new_size > 0 and known_size > 0 and new_size != known_size:
            answer = QMessageBox.question(
                self, "Farklı dosya",
                f"Yakalanan dosya ({format_size(new_size)}) bu indirmeyle ({format_size(known_size)}) aynı görünmüyor. "
                "Yine de bu adresi uygulayayım mı?\n\n"
                "Hayır dersen doğru dosyayı tarayıcıda başlatmanı beklemeye devam ederim.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                if payload.get("ticket"):
                    handoff.HANDOFF.finish(payload["ticket"], "cancelled")
                return True
        self._refresh_wait = None
        if not self._manager.refresh_url(task_id, payload["url"], headers):
            return False
        if payload.get("ticket"):
            handoff.HANDOFF.attach(payload["ticket"], task_id)
        self._show_message("İndirme adresi yenilendi: kaldığı yerden devam ediyor", transient=True)
        return True

    # ---- uygulama güncellemesi (GitHub Releases)
    def _start_app_update_checks(self) -> None:
        self._app_timer = QTimer(self)
        self._app_timer.setInterval(6 * 60 * 60 * 1000)
        self._app_timer.timeout.connect(self._app_update_tick)
        self._app_timer.start()
        QTimer.singleShot(45_000, self._app_update_tick)

    def _app_update_tick(self) -> None:
        if self._closing or not self._config.get("auto_update_app") or not _paths.FROZEN:
            return                        # geliştirme sürümü (python main.py) kendiliğinden denetlemez
        self.check_app_update(False)

    def check_app_update(self, manual: bool = False) -> None:
        """Arka planda en son sürümü sorar. manual=True: güncel olsa da hata olsa da sonucu söyler."""
        if self._app_checking:
            return
        self._app_checking = True
        if manual:
            self._show_message("Güncelleme denetleniyor…")

        def work() -> None:
            info, error = None, ""
            try:
                info = appupdate.check(_APP_VERSION)
            except appupdate.UpdateError as e:
                error = str(e)
            except Exception as e:
                error = f"Güncelleme denetimi başarısız: {e}"
            self._app_checking = False
            try:
                self._update_found.emit((info, manual, error))
            except RuntimeError:
                pass

        threading.Thread(target=work, daemon=True, name="app-update-check").start()

    def _on_update_found(self, payload) -> None:
        info, manual, error = payload
        if manual:
            self._show_message("")
        if error:
            if manual:
                QMessageBox.warning(self, "Güncelleme denetimi", error)
            return
        if info is None:
            if manual:
                self._show_message(f"Uygulama güncel (v{_APP_VERSION})", transient=True)
            return
        if not manual and info["version"] == str(self._config.get("update_skipped_version") or ""):
            return
        if self._update_dialog_open:
            return
        from gui.components.update_dialog import UpdateDialog
        self._update_dialog_open = True
        try:
            bring_to_front(self)
            busy = sum(1 for s in self._statuses.values() if s in _RUNNING)
            dialog = UpdateDialog(info, _APP_VERSION, busy, _paths.FROZEN, self)
            dialog.exec()
        finally:
            self._update_dialog_open = False
        if dialog.action == "skip":
            self._config.set("update_skipped_version", info["version"])
        elif dialog.action == "install":
            self._install_update(dialog.path)
        elif dialog.action == "page":
            QDesktopServices.openUrl(QUrl(info["page"]))

    def _install_update(self, path: str) -> None:
        """Setup'ı uygulama kapandıktan sonra sessizce çalıştıracak yardımcıyı başlatır, sonra uygulamayı kapatır."""
        if not appupdate.launch_installer(Path(path)):
            self._show_message(f"Kurulum başlatılamadı. Dosyayı elle çalıştırabilirsin: {path}")
            return
        self._quit_from_tray()

    # ---- arşivler
    def _maybe_auto_extract(self, task: DownloadTask) -> None:
        if (self._config.get("auto_extract") and task.final_path and task.engine != "ytdlp"
                and _archive.is_archive(task.final_path)):
            self._start_extract(Path(task.final_path))

    def _extract_task(self, task_id: str) -> None:
        task = self._manager.get_task(task_id)
        if task is None or not task.final_path or not Path(task.final_path).is_file():
            self._show_message("Dosya diskte bulunamadı.", transient=True)
            return
        self._start_extract(Path(task.final_path))

    def _start_extract(self, path: Path) -> None:
        self._show_message(f"Arşiv açılıyor: {path.name}…")

        def work() -> None:
            dest, text = "", ""
            try:
                dest = str(_archive.extract(path))
                text = path.name
            except _archive.ExtractError as e:
                text = str(e)
            except Exception as e:
                text = f"Arşiv açılamadı: {e}"
            try:
                self._extract_done.emit(dest, text)
            except RuntimeError:
                pass

        threading.Thread(target=work, daemon=True, name="extract").start()

    def _on_extract_done(self, dest: str, text: str) -> None:
        self._show_message("")
        if dest:
            self.done_banner.setText(f"📦 Arşiv açıldı: {Path(dest).name}   ({dest})")
            self.done_banner.show()
            self._banner_timer.start(9000)
        else:
            self._show_message(text)

    # ---- eksik araçlar (çevrimiçi kurulum paketi)
    def _ensure_tools(self) -> None:
        missing = toolsetup.missing()
        if not missing or self._closing:
            return
        from gui.components.tools_dialog import ToolsDialog
        ToolsDialog(missing, self).exec()
        self.youtube_page.refresh_runtime_banner()

    def _raise_dialog(self, widget) -> None:
        """Açık bir pencereyi (ayarlar açıkken bile) en öne getirir."""
        try:
            if widget.isVisible():
                bring_to_front(widget)
        except RuntimeError:
            pass

    def _apply_live_settings(self) -> None:
        self._manager.apply_settings()
        self.link_fields.set_count(self._config.get("max_concurrent_downloads"))
        self.youtube_page.refresh_runtime_banner()
        self._apply_behavior_settings()
        self._apply_language()

    def _settings_closed(self, dialog) -> None:
        dirty = bool(getattr(dialog, "dirty", False))
        if getattr(self, "_settings_dialog", None) is dialog:
            self._settings_dialog = None
        if dirty:
            self._show_message("Ayarlar kaydedildi", transient=True)

    def _show_error_details(self, task_id: str) -> None:
        task = self._manager.get_task(task_id)
        if task is None or not task.error:
            return
        from gui.components.error_dialog import ErrorDialog
        ErrorDialog(task.title or task.file_name or task.url, task.error, "Windows Defender" in task.error, self).exec()

    def _refresh_view(self) -> None:
        """Listeyi baştan çizer: yönetici ile eşitler, kartları günceller, tamamlanan dosyaların varlığını denetler."""
        tasks = {t.id: t for t in self._manager.tasks()}
        for tid in [i for i in self._cards if i not in tasks]:
            self._on_task_removed(tid)                  # yöneticide olmayan eski kart
        for tid, task in tasks.items():
            if tid not in self._cards:
                self._add_card(task)
                continue
            card = self._cards[tid]
            card.update_task(task)
            self._statuses[tid] = task.status
            self._speeds[tid] = task.speed if task.status == TaskStatus.DOWNLOADING else 0.0
            options = task.options or {}
            (self._staged.add if options.get("staged") else self._staged.discard)(tid)
            (self._listed.add if options.get("listed") else self._listed.discard)(tid)
            if task.status == TaskStatus.COMPLETED and task.final_path and not Path(task.final_path).is_file():
                card.info_label.set_full_text("⚠ Dosya diskte bulunamadı (taşınmış ya da silinmiş olabilir)",
                                              tooltip=True)
                card.info_label.setProperty("state", "warn")
                card.info_label.style().unpolish(card.info_label)
                card.info_label.style().polish(card.info_label)
        self._sync_queue_order()
        self._refresh_all()
        self._show_message("Liste yenilendi", transient=True)

    def _apply_language(self) -> None:
        """Ayarlardaki dili açık tüm pencerelere anında uygular (sistem dili seçiliyse Windows diline bakılır)."""
        code = i18n.resolve(str(self._config.get("language") or "auto"))
        if code == i18n.language():
            return
        i18n_qt.apply_language(code, i18n.language())
        self._refresh_all()
        self._refresh_status_bar()

    def sync_mini_box(self) -> None:
        """Ayardaki "Yüzen mini kutu"yu açar / kapatır."""
        want = bool(self._config.get("floating_box")) and not self._closing
        if want and self._mini is None:
            from gui.components.mini_box import MiniBox
            box = MiniBox()
            box.dropped.connect(self._on_mini_drop)
            box.activated.connect(self.show_and_raise)
            box.moved.connect(lambda x, y: self._config.set("mini_box_pos", f"{x},{y}"))
            box.hide_requested.connect(self._hide_mini_box)
            geo = QApplication.primaryScreen().availableGeometry()
            x, y = geo.right() - box.width() - 24, geo.bottom() - box.height() - 90
            try:
                sx, sy = (int(v) for v in str(self._config.get("mini_box_pos")).split(","))
                if geo.contains(sx, sy):
                    x, y = sx, sy
            except ValueError:
                pass
            box.move(x, y)
            box.show()
            self._mini = box
        elif not want and self._mini is not None:
            self._mini.close()
            self._mini.deleteLater()
            self._mini = None

    def _hide_mini_box(self) -> None:
        self._config.set("floating_box", False)
        self.sync_mini_box()

    def _on_mini_drop(self, urls: list) -> None:
        self._select_tab(0)
        for url in urls:
            target = next((f for f in self.link_fields.fields if not f.text().strip()), None)
            if target is None:
                last = self.link_fields.fields[-1]
                last.setText(f"{last.text().strip()} {url}".strip())
            else:
                target.setText(url)
        self._on_add()
        if any(youtube.is_youtube_url(u) for u in urls):
            self.show_and_raise()

    def _overall_percent(self) -> float:
        live = [t for t in self._manager.tasks() if t.status in _RUNNING and t.file_size]
        total = sum(t.file_size for t in live)
        return (sum(t.downloaded for t in live) * 100.0 / total) if total else -1.0

    def offer_crash_report(self) -> None:
        """Önceki oturumda yakalanmamış hata olduysa GitHub'da bildirmeyi önerir."""
        from core import report
        trace = report.read_crash()
        if not trace:
            return
        answer = QMessageBox.question(
            self, "Önceki oturumda hata",
            "Uygulama önceki oturumda beklenmedik bir hata yaşadı.\n\nGitHub'da bildirmek ister misin? "
            "(Göndermeden önce taslağı görüp düzenleyebilirsin; tam rapor panoya kopyalanır.)",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        report.clear_crash()
        if answer == QMessageBox.Yes:
            title, full, short = report.crash_report(trace)
            QApplication.clipboard().setText(full)
            QDesktopServices.openUrl(QUrl(report.issue_url(title, short)))

    def _add_mirror_for(self, task_id: str) -> None:
        from PySide6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, "Yedek adres",
                                        "Aynı dosyanın yedek adresi (indirme hata verirse sırayla denenir):")
        if not ok or not text.strip():
            return
        if self._manager.add_mirror(task_id, text.strip()):
            self._show_message("Yedek adres eklendi", transient=True)
        else:
            self._show_message("Yedek adres eklenemedi (geçerli bir http/https adresi olmalı).")

    def _ask_auth_for(self, task_id: str) -> None:
        from PySide6.QtWidgets import QInputDialog
        user, ok = QInputDialog.getText(self, "Kimlik bilgisi", "Kullanıcı adı (boş bırakırsan kaldırılır):")
        if not ok:
            return
        password = ""
        if user.strip():
            password, ok = QInputDialog.getText(self, "Kimlik bilgisi", "Şifre:", QLineEdit.Password)
            if not ok:
                return
        self._manager.set_auth(task_id, user.strip(), password)

    def _pause_selected(self) -> None:
        selected = self._selected_ids()
        if selected:
            for task_id in selected:
                self._manager.pause(task_id)
        else:
            self._manager.pause_all()

    # ---- sayfadaki bağlantılar, hash
    def _on_links(self, data: dict) -> None:
        if self._links_dialog is not None:
            self._links_dialog.close()
        dialog = LinksDialog(data, self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.add_requested.connect(lambda urls, d=data: self._add_links(urls, d))
        self._links_dialog = dialog
        QTimer.singleShot(80, lambda d=dialog: self._raise_dialog(d))
        bring_to_front(self)
        dialog.show()

    def _add_links(self, urls: list, data: dict) -> None:
        page = data.get("pageUrl") or ""
        host = urlparse(page).hostname
        added = 0
        for url in urls:
            headers = {}
            if page:
                headers["Referer"] = page
            if data.get("userAgent"):
                headers["User-Agent"] = data["userAgent"]
            if data.get("cookies") and urlparse(url).hostname == host:   # çerez yalnızca aynı siteye gider
                headers["Cookie"] = data["cookies"]
            try:
                self._manager.add(url, headers=headers)
                added += 1
            except (ValueError, RuntimeError):
                continue
        self._select_tab(0)
        self._show_message(f"{added} bağlantı listeye eklendi", transient=True)

    def _open_hash(self) -> None:
        files = []
        for task in reversed(self._manager.tasks()):
            if task.status == TaskStatus.COMPLETED and task.final_path and Path(task.final_path).is_file():
                files.append((task.file_name or Path(task.final_path).name, Path(task.final_path)))
        dialog = HashDialog(files, self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    # ================================================================= kapanış
    def closeEvent(self, event) -> None:
        if self._closing:
            event.accept()
            return
        if self._tray is not None and self._server.state == "running" and not self._quitting:
            event.ignore()                           # sunucu açıkken pencere kapanınca tepsiye küçül
            self.hide()
            self._tray_notice()
            return
        self._closing = True
        self._server.stop()
        prefetch.PREFETCH.cancel_all()
        if self._mini is not None:
            self._mini.close()
        self.status_text.setText("Kapatılıyor… aktif indirmeler kaydediliyor")
        self.speed_label.setText("")
        QApplication.processEvents()
        try:
            self._manager.shutdown()
        except Exception:
            log.exception("Kapanışta yönetici durdurulamadı")
        event.accept()
        if self._tray is not None:
            self._tray.hide()
            QTimer.singleShot(0, QApplication.quit)