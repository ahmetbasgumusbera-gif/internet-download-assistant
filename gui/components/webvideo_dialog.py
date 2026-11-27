"""
"Sitedeki videolar" penceresi: tarayıcı eklentisinin bulduğu videoları (ad, görsel, tür, boyut) listeler; kullanıcı seçer.
  * Doğrudan dosya  -> direct_requested(payload): ana pencerenin onay penceresine gider (HTTP motoru indirir).
  * HLS / DASH / sayfa -> yt-dlp ile analiz edilir; kalite, ses dili (dublaj), altyazı seçeneği sunulur -> stream_requested(spec).
"""
from __future__ import annotations

import threading
from typing import Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QProgressBar,
    QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from core import convert, webvideo
from core.youtube import AnalysisCancelled, AnalysisError
from gui import theme
from gui.components.download_item import ElidedLabel
from gui.components.playlist_panel import _LIST_QSS
from gui.thumbnails import ThumbnailLoader, cover_crop
from utils.helpers import format_size

_PAGE_LIST, _PAGE_LOADING, _PAGE_OPTIONS, _PAGE_ERROR = range(4)

_COMBO_QSS = f"""
QComboBox {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; border-radius: 10px; padding: 8px 12px;
             min-width: 300px; color: {theme.TEXT}; }}
QComboBox:hover {{ border-color: {theme.BORDER_HOVER}; }}
QComboBox QAbstractItemView {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER};
             selection-background-color: {theme.SURFACE_2}; color: {theme.TEXT}; outline: none; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
"""

_KIND_TEXT = {"hls": "HLS akışı", "dash": "DASH akışı"}


def _duration(seconds: int) -> str:
    if not seconds:
        return ""
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class _Row(QWidget):
    """Listede tek aday: görsel, ad, ayrıntı ve rozet."""

    def __init__(self, c: webvideo.WebCandidate, badge: str = "", dim: bool = False):
        super().__init__()
        self.thumb = QLabel("▶")
        self.thumb.setFixedSize(96, 54)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet(f"background: {theme.SURFACE_2}; border-radius: 8px; color: {theme.TEXT_DIM};")
        name = ElidedLabel(Qt.ElideMiddle)
        name.setObjectName("fileName")
        name.set_full_text(c.title or "Başlıksız video")
        kind = _KIND_TEXT.get(c.kind, f"Doğrudan dosya ({c.extension.upper()})")
        bits = [kind]
        if c.size:
            bits.append(format_size(c.size))
        if c.duration:
            bits.append(_duration(c.duration))
        if c.width and c.height:
            bits.append(f"{c.width}×{c.height}")
        host = c.url.split("/")[2] if c.url.count("/") >= 2 else ""
        if host:
            bits.append(host)
        detail = ElidedLabel(Qt.ElideRight)
        detail.setObjectName("info")
        detail.set_full_text("   ·   ".join(bits))
        column = QVBoxLayout()
        column.setSpacing(3)
        column.addWidget(name)
        column.addWidget(detail)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(12)
        row.addWidget(self.thumb)
        row.addLayout(column, 1)
        if badge:
            label = QLabel(badge)
            label.setObjectName("badge")
            color = theme.GREEN if badge == "Önerilen" else theme.AMBER
            label.setStyleSheet(f"QLabel#badge {{ color: {color}; background: rgba(255,255,255,18); "
                                f"border-radius: 9px; padding: 3px 10px; font-size: 11px; font-weight: 600; }}")
            row.addWidget(label)
        if dim:
            self.setStyleSheet("QLabel { color: " + theme.TEXT_DIM + "; }")
        if c.poster:
            def ready(pixmap) -> None:
                if pixmap is not None:
                    self.thumb.setStyleSheet("background: transparent;")
                    self.thumb.setText("")
                    self.thumb.setPixmap(cover_crop(pixmap, 96, 54, 8))
            ThumbnailLoader.instance().request(c.poster, ready)


class WebVideoDialog(QDialog):
    direct_requested = Signal(object)     # onay penceresine gidecek indirme sözlüğü (ana pencerenin _on_capture biçimi)
    stream_requested = Signal(object)     # yt-dlp indirmesi: {"url", "args", "title", "thumbnail", "summary", "playlistItem"}
    _analysis_done = Signal(int, object, str)

    def __init__(self, page: webvideo.WebPage, parent=None):
        super().__init__(parent)
        self._page = page
        self._candidates: list[webvideo.WebCandidate] = []
        self._token = 0
        self._cancel: Optional[threading.Event] = None
        self._ctx: dict = {}
        self._auto = False
        self._info: Optional[webvideo.StreamInfo] = None
        self.setWindowTitle("Sitedeki videolar")
        self.resize(820, 640)
        self.setMinimumSize(640, 480)

        host = page.page_url.split("/")[2] if page.page_url.count("/") >= 2 else page.page_url
        title = QLabel("Sitedeki videolar")
        title.setObjectName("appTitle")
        subtitle = ElidedLabel(Qt.ElideMiddle)
        subtitle.setObjectName("hint")
        subtitle.set_full_text(f"{page.title or 'Başlıksız sayfa'}   ·   {host}")

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_list())
        self.stack.addWidget(self._build_loading())
        self.stack.addWidget(self._build_options())
        self.stack.addWidget(self._build_error())

        root = QVBoxLayout(self)
        root.setContentsMargins(26, 22, 26, 18)
        root.setSpacing(10)
        root.addWidget(title)
        root.addWidget(subtitle)
        root.addWidget(self.stack, 1)

        self._analysis_done.connect(self._on_analysis_done)
        self._fill_list()

    # ------------------------------------------------------------------ liste sayfası
    def _build_list(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(10)
        self.hint_label = QLabel("")
        self.hint_label.setObjectName("hint")
        self.hint_label.setWordWrap(True)
        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet(_LIST_QSS)
        self.list_widget.setSpacing(2)
        self.analyze_page_btn = QPushButton("Sayfayı yt-dlp ile analiz et")
        self.analyze_page_btn.setObjectName("ghost")
        self.analyze_page_btn.setToolTip("Bazı siteleri (Vimeo, Dailymotion, X...) yt-dlp doğrudan tanır; kalite, dublaj ve altyazı listesi verir")
        self.next_btn = QPushButton("  Seçeneklere bak ve indir")
        self.next_btn.setObjectName("primary")
        self.close_btn = QPushButton("Kapat")
        self.list_btn = QPushButton("Listeye ekle")
        self.list_btn.setObjectName("ghost")
        self.list_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setObjectName("ghost")
        for b in (self.analyze_page_btn, self.next_btn, self.close_btn):
            b.setCursor(Qt.PointingHandCursor)
        buttons = QHBoxLayout()
        buttons.addWidget(self.analyze_page_btn)
        self.convert_combo = QComboBox()
        self.convert_combo.setStyleSheet(theme.combo_qss(190))
        self.convert_combo.setToolTip("Doğrudan dosya indirmelerinde uygulanır")
        for code, label in convert.CHOICES:
            self.convert_combo.addItem(label, code)
        buttons.addWidget(self.convert_combo)
        buttons.addStretch(1)
        buttons.addWidget(self.close_btn)
        buttons.addWidget(self.list_btn)
        buttons.addWidget(self.next_btn)
        lay.addWidget(self.hint_label)
        lay.addWidget(self.list_widget, 1)
        lay.addLayout(buttons)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._on_next())
        self.list_widget.currentItemChanged.connect(lambda *_: self._sync_next())
        self.next_btn.clicked.connect(self._on_next)
        self.close_btn.clicked.connect(self.close)
        self.list_btn.clicked.connect(lambda: self._on_next(True))
        self.analyze_page_btn.clicked.connect(self._analyze_page)
        return page

    def _fill_list(self) -> None:
        recommended, others, ads = self._page.groups()
        self._candidates = []
        self.list_widget.clear()

        def header(text: str) -> None:
            item = QListWidgetItem(text)
            item.setFlags(Qt.NoItemFlags)
            font = item.font()
            font.setBold(True)
            item.setFont(font)
            self.list_widget.addItem(item)

        def add(c: webvideo.WebCandidate, badge: str = "", dim: bool = False) -> QListWidgetItem:
            item = QListWidgetItem()
            item.setData(Qt.UserRole, len(self._candidates))
            item.setSizeHint(QSize(0, 70))
            self._candidates.append(c)
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, _Row(c, badge, dim))
            return item

        first = None
        if recommended is not None:
            header("Önerilen")
            first = add(recommended, "Önerilen")
        if others:
            header("Diğer videolar")
            for c in others:
                add(c)
        if ads:
            header("Reklam olabilir")
            for c in ads:
                add(c, "Reklam olabilir", dim=True)
        if first is None:
            for i in range(self.list_widget.count()):
                if self.list_widget.item(i).flags() & Qt.ItemIsSelectable:
                    first = self.list_widget.item(i)
                    break
        if first is not None:
            self.list_widget.setCurrentItem(first)

        if not self._candidates:
            self.hint_label.setText(
                "Bu sayfada henüz video algılanamadı. Videoyu oynatmayı başlatıp tekrar dene "
                "(oynatılınca ağ trafiğinde görünür) ya da aşağıdan sayfayı yt-dlp ile analiz et.")
        else:
            self.hint_label.setText(
                "Önerilen video, oynatılan/sayfanın bildirdiği ana videodur. Emin değilsen diğerlerine de bakabilirsin. "
                "Akışlarda (HLS/DASH) bir sonraki adımda kalite, ses dili ve altyazı seçilir.")
        self._sync_next()

    def auto_list(self) -> bool:
        """Kutudaki "Listeye ekle": önerilen (en yüksek puanlı) videoyu seçenek sormadan varsayılan ayarla listeye ekler."""
        if self._current_candidate() is None:
            return False
        self._auto = True
        self._on_next(True)
        return True

    def _current_candidate(self) -> Optional[webvideo.WebCandidate]:
        item = self.list_widget.currentItem()
        index = item.data(Qt.UserRole) if item is not None else None
        return self._candidates[index] if index is not None and 0 <= index < len(self._candidates) else None

    def _sync_next(self) -> None:
        c = self._current_candidate()
        self.next_btn.setEnabled(c is not None)
        self.list_btn.setEnabled(c is not None)
        self.next_btn.setText("  İndir" if c is not None and c.kind == "file" else "  Seçeneklere bak ve indir")

    def _on_next(self, listed: bool = False) -> None:
        listed = listed is True
        c = self._current_candidate()
        if c is None:
            return
        page = self._page
        if c.kind == "file":
            self.direct_requested.emit({
                "url": c.url, "filename": webvideo.file_name_for(c, page.title), "fileSize": c.size or -1,
                "mime": c.mime, "referrer": c.referrer or page.page_url, "cookies": c.cookies,
                "userAgent": page.user_agent, "listed": listed, "quick": listed,
                "thumbnail": c.poster or page.image,
                "convertTo": str(self.convert_combo.currentData() or ""),
            })
            self.close()
            return
        self._analyze(c.url, referrer=c.referrer or page.page_url, title=c.title or page.title,
                      thumbnail=c.poster or page.image)

    def _analyze_page(self) -> None:
        page = self._page
        self._analyze(page.page_url, referrer=page.page_url, title=page.title, thumbnail=page.image)

    # ------------------------------------------------------------------ yükleniyor / hata
    def _build_loading(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(12)
        label = QLabel("Akış analiz ediliyor…")
        label.setObjectName("emptyTitle")
        label.setAlignment(Qt.AlignCenter)
        bar = QProgressBar()
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        bar.setFixedWidth(260)
        cancel = QPushButton("Vazgeç")
        cancel.setObjectName("ghost")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.clicked.connect(self._back)
        lay.addWidget(label)
        lay.addWidget(bar, 0, Qt.AlignCenter)
        lay.addWidget(cancel, 0, Qt.AlignCenter)
        return page

    def _build_error(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setAlignment(Qt.AlignTop)
        lay.setSpacing(12)
        self.error_label = QLabel("")
        self.error_label.setObjectName("bannerWarn")
        self.error_label.setWordWrap(True)
        back = QPushButton("Geri dön")
        back.setObjectName("ghost")
        back.setCursor(Qt.PointingHandCursor)
        back.clicked.connect(self._back)
        lay.addWidget(self.error_label)
        lay.addWidget(back, 0, Qt.AlignLeft)
        return page

    def _back(self) -> None:
        self._token += 1                       # çalışan analizin sonucu yok sayılır
        if self._cancel is not None:
            self._cancel.set()
        self.stack.setCurrentIndex(_PAGE_LIST)

    # ------------------------------------------------------------------ akış analizi
    def _analyze(self, url: str, *, referrer: str, title: str, thumbnail: str) -> None:
        self._token += 1
        token = self._token
        if self._cancel is not None:
            self._cancel.set()
        cancel = self._cancel = threading.Event()
        self._ctx = {"url": url, "referrer": referrer, "title": title, "thumbnail": thumbnail}
        page = self._page
        self.stack.setCurrentIndex(_PAGE_LOADING)

        def work() -> None:
            info, error = None, ""
            try:
                info = webvideo.analyze_stream(url, referrer=referrer, user_agent=page.user_agent,
                                               jar=page.jar, cancel_event=cancel)
            except AnalysisCancelled:
                return
            except AnalysisError as e:
                error = str(e)
            except Exception as e:  # beklenmeyen: arayüz asla donmasın
                error = f"Analiz başarısız: {e}"
            try:
                self._analysis_done.emit(token, info, error)
            except RuntimeError:
                pass                           # pencere kapanmış

        threading.Thread(target=work, daemon=True, name="webvideo-analyze").start()

    def _on_analysis_done(self, token: int, info, error: str) -> None:
        if token != self._token:
            return
        if info is None:
            self._auto = False
            self.error_label.setText(error or "Analiz başarısız.")
            self.stack.setCurrentIndex(_PAGE_ERROR)
            return
        self._info = info
        self._fill_options(info)
        if self._auto:
            self._auto = False
            self._on_download(True)
            return
        self.stack.setCurrentIndex(_PAGE_OPTIONS)

    # ------------------------------------------------------------------ seçenekler sayfası
    def _build_options(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(8)

        def section(text: str) -> QLabel:
            label = QLabel(text)
            label.setObjectName("sectionLabel")
            return label

        self.name_edit = QLineEdit()
        self.video_combo = QComboBox()
        self.audio_combo = QComboBox()
        for combo in (self.video_combo, self.audio_combo):
            combo.setStyleSheet(_COMBO_QSS)
        self.audio_section = section("Ses (dublaj / dil)")
        self.audio_hint = QLabel("")
        self.audio_hint.setObjectName("hint")
        self.subs_section = section("Altyazı")
        self.subs_list = QListWidget()
        self.subs_list.setStyleSheet(_LIST_QSS)
        self.subs_list.setMaximumHeight(120)
        self.chip_mp4 = QPushButton("MP4")
        self.chip_mkv = QPushButton("MKV")
        group = QButtonGroup(self)
        group.setExclusive(True)
        container = QHBoxLayout()
        container.setSpacing(8)
        for chip in (self.chip_mp4, self.chip_mkv):
            chip.setObjectName("chip")
            chip.setCheckable(True)
            chip.setCursor(Qt.PointingHandCursor)
            group.addButton(chip)
            container.addWidget(chip)
        container.addStretch(1)
        self.chip_mp4.setChecked(True)
        self.summary_label = QLabel("")
        self.summary_label.setObjectName("summaryText")
        self.back_btn = QPushButton("Geri")
        self.back_btn.setObjectName("ghost")
        self.download_btn = QPushButton("  İndir")
        self.list_btn2 = QPushButton("Listeye ekle")
        self.list_btn2.setObjectName("ghost")
        self.list_btn2.setCursor(Qt.PointingHandCursor)
        self.download_btn.setObjectName("primary")
        for b in (self.back_btn, self.download_btn):
            b.setCursor(Qt.PointingHandCursor)
        buttons = QHBoxLayout()
        buttons.addWidget(self.summary_label, 1)
        buttons.addWidget(self.back_btn)
        buttons.addWidget(self.list_btn2)
        buttons.addWidget(self.download_btn)

        lay.addWidget(section("Dosya adı"))
        lay.addWidget(self.name_edit)
        lay.addWidget(section("Kalite"))
        lay.addWidget(self.video_combo, 0, Qt.AlignLeft)
        lay.addWidget(self.audio_section)
        lay.addWidget(self.audio_combo, 0, Qt.AlignLeft)
        lay.addWidget(self.audio_hint)
        lay.addWidget(self.subs_section)
        lay.addWidget(self.subs_list)
        self.chip_video = QPushButton("Video + ses")
        self.chip_audio = QPushButton("Sadece ses")
        mode_group = QButtonGroup(self)
        mode_group.setExclusive(True)
        mode_row = QHBoxLayout()
        mode_row.setSpacing(8)
        for chip in (self.chip_video, self.chip_audio):
            chip.setObjectName("chip")
            chip.setCheckable(True)
            chip.setCursor(Qt.PointingHandCursor)
            mode_group.addButton(chip)
            mode_row.addWidget(chip)
        self.chip_video.setChecked(True)
        self.audio_fmt_combo = QComboBox()
        self.audio_fmt_combo.setStyleSheet(theme.combo_qss(170))
        for code, label in (("mp3", "MP3"), ("wav", "WAV (kayıpsız)"), ("flac", "FLAC (kayıpsız)"),
                            ("m4a", "M4A (AAC)"), ("opus", "OPUS")):
            self.audio_fmt_combo.addItem(label, code)
        self.audio_fmt_combo.setVisible(False)
        mode_row.addWidget(self.audio_fmt_combo)
        mode_row.addStretch(1)
        lay.addWidget(section("Çıktı türü"))
        lay.addLayout(mode_row)
        lay.addWidget(section("Kapsayıcı"))
        lay.addLayout(container)
        lay.addStretch(1)
        lay.addLayout(buttons)

        self.back_btn.clicked.connect(self._back)
        self.download_btn.clicked.connect(self._on_download)
        self.list_btn2.clicked.connect(lambda: self._on_download(True))
        self.video_combo.currentIndexChanged.connect(lambda _i: self._refresh_summary())
        self.audio_combo.currentIndexChanged.connect(lambda _i: self._refresh_summary())
        self.subs_list.itemChanged.connect(lambda _i: self._refresh_summary())
        self.chip_mp4.clicked.connect(self._refresh_summary)
        self.chip_mkv.clicked.connect(self._refresh_summary)
        self.chip_video.clicked.connect(self._sync_mode)
        self.chip_audio.clicked.connect(self._sync_mode)
        self.audio_fmt_combo.currentIndexChanged.connect(lambda _i: self._refresh_summary())
        return page

    def _fill_options(self, info: webvideo.StreamInfo) -> None:
        self.name_edit.setText(self._ctx.get("title") or info.title)
        self.video_combo.blockSignals(True)
        self.video_combo.clear()
        self.video_combo.addItem("En iyi kalite (otomatik)", "")
        for f in info.video_formats:
            self.video_combo.addItem(webvideo.video_label(f), f.format_id)
        self.video_combo.blockSignals(False)

        audio = info.audio_formats
        self.audio_combo.blockSignals(True)
        self.audio_combo.clear()
        self.audio_combo.addItem("Otomatik (varsayılan ses)", "")
        for f in audio:
            self.audio_combo.addItem(webvideo.audio_label(f), f.format_id)
        self.audio_combo.blockSignals(False)
        self.audio_section.setVisible(bool(audio))
        self.audio_combo.setVisible(bool(audio))
        self.audio_hint.setText("" if audio else "Ses videonun içinde geliyor; ayrı dil seçeneği yok.")

        self.subs_list.blockSignals(True)
        self.subs_list.clear()
        for code, name in info.subtitles:
            item = QListWidgetItem(f"{name}  [{code}]")
            item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            item.setCheckState(Qt.Unchecked)
            item.setData(Qt.UserRole, code)
            self.subs_list.addItem(item)
        self.subs_list.blockSignals(False)
        self.subs_section.setVisible(bool(info.subtitles))
        self.subs_list.setVisible(bool(info.subtitles))
        self._refresh_summary()

    def _sync_mode(self) -> None:
        audio = self.chip_audio.isChecked()
        self.audio_fmt_combo.setVisible(audio)
        for widget in (self.video_combo, self.chip_mp4, self.chip_mkv, self.subs_list):
            widget.setEnabled(not audio)
        self._refresh_summary()

    def _choice(self) -> webvideo.StreamChoice:
        subs = [self.subs_list.item(i).data(Qt.UserRole) for i in range(self.subs_list.count())
                if self.subs_list.item(i).checkState() == Qt.Checked]
        return webvideo.StreamChoice(
            video_id=self.video_combo.currentData() or "", audio_id=self.audio_combo.currentData() or "",
            subtitles=subs, container=((self.audio_fmt_combo.currentData() or "mp3") if self.chip_audio.isChecked()
                       else ("mkv" if self.chip_mkv.isChecked() else "mp4")),
            title=self.name_edit.text().strip(),
        )

    def _refresh_summary(self) -> None:
        if self._info is not None:
            self.summary_label.setText(webvideo.choice_summary(self._info, self._choice()))

    def _on_download(self, listed: bool = False) -> None:
        listed = listed is True
        info = self._info
        if info is None:
            return
        choice = self._choice()
        page = self._page
        cookie_path = webvideo.cookie_file(page.jar)
        args = webvideo.build_args(info, choice, referrer=self._ctx.get("referrer", ""), user_agent=page.user_agent,
                                   cookie_path=cookie_path)
        self.stream_requested.emit({
            "url": self._ctx["url"], "args": args, "title": choice.title or info.title or page.title,
            "thumbnail": self._ctx.get("thumbnail") or info.thumbnail,
            "summary": webvideo.choice_summary(info, choice), "playlistItem": info.from_playlist, "listed": listed, "pageUrl": page.page_url,
        })
        self.close()

    def closeEvent(self, event) -> None:
        self._token += 1
        if self._cancel is not None:
            self._cancel.set()                 # çalışan yt-dlp analizi öldürülür
        super().closeEvent(event)
