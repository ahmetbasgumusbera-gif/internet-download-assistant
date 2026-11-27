"""
Oynatma listesi paneli (YouTube sekmesinde): listedeki videoları gösterir, seçtirir ve ortak ayarla (çözünürlük/ses biçimi)
hepsini listeye ekletir. Panel indirmeyi kendisi başlatmaz: "download_requested" sinyaliyle
{"info": PlaylistInfo, "entries": [seçilenler], "selection": Selection} verir; asıl işi ana pencere yapar.

Yerleşim: içerik (başlık, liste, ayarlar) kaydırılabilir bir alandadır; pencere kısa olsa bile ögeler üst üste binmez.
"Vazgeç / Ekle ve indir" çubuğu her zaman altta görünür kalır.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from core import youtube
from core.youtube import PlaylistInfo, Selection
from gui import theme
from gui.icons import get_icon

_HEIGHTS = (("best", "En iyi"), ("2160", "2160p"), ("1440", "1440p"), ("1080", "1080p"),
            ("720", "720p"), ("480", "480p"), ("360", "360p"))
_AUDIO = (("m4a", "M4A (orijinal)"), ("mp3_192", "MP3 192 kbps"), ("mp3_320", "MP3 320 kbps"))

_LIST_QSS = f"""
QListWidget {{ background: {theme.SURFACE}; border: 1px solid {theme.BORDER}; border-radius: 12px; padding: 4px; outline: none; }}
QListWidget::item {{ padding: 7px 8px; border-radius: 8px; color: {theme.TEXT}; }}
QListWidget::item:hover {{ background: {theme.SURFACE_2}; }}
QListWidget::item:disabled {{ color: {theme.TEXT_DIM}; }}
QListWidget::indicator {{ width: 16px; height: 16px; border: 1px solid {theme.BORDER_HOVER}; border-radius: 5px; background: {theme.BG}; }}
QListWidget::indicator:checked {{ background: {theme.CYAN}; border-color: {theme.CYAN}; }}
"""


def _duration(seconds) -> str:
    if not seconds:
        return ""
    h, rest = divmod(int(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class PlaylistPanel(QWidget):
    download_requested = Signal(object)
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._info: PlaylistInfo | None = None
        self._mode = "video"
        self._height = "1080"
        self._container = "mp4"
        self._audio = "m4a"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        # ---- kaydırılabilir gövde
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("listHost")
        root = QVBoxLayout(body)
        root.setContentsMargins(0, 0, 8, 0)
        root.setSpacing(10)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # ---- üst kart
        card = QFrame()
        card.setObjectName("card")
        card.setAttribute(Qt.WA_StyledBackground, True)
        card_lay = QVBoxLayout(card)
        card_lay.setContentsMargins(16, 12, 16, 12)
        card_lay.setSpacing(4)
        self.title_label = QLabel("")
        self.title_label.setObjectName("videoTitle")
        self.title_label.setWordWrap(True)
        self.meta_label = QLabel("")
        self.meta_label.setObjectName("videoMeta")
        card_lay.addWidget(self.title_label)
        card_lay.addWidget(self.meta_label)
        root.addWidget(card)

        # ---- seçim araçları
        self.select_all_btn = self._chip("Tümünü seç")
        self.select_none_btn = self._chip("Hiçbirini seçme")
        self.count_label = QLabel("")
        self.count_label.setObjectName("hint")
        tools = QHBoxLayout()
        tools.setSpacing(8)
        tools.addWidget(self.select_all_btn)
        tools.addWidget(self.select_none_btn)
        tools.addStretch(1)
        tools.addWidget(self.count_label)
        root.addLayout(tools)

        # ---- video listesi (alan darsa kaydırma çubuğu devreye girer, ayarların altına binmez)
        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet(_LIST_QSS)
        self.list_widget.setMinimumHeight(260)
        root.addWidget(self.list_widget, 1)

        # ---- ortak ayarlar (kart içinde, iki satır)
        opts = QFrame()
        opts.setObjectName("card")
        opts.setAttribute(Qt.WA_StyledBackground, True)
        opts_lay = QVBoxLayout(opts)
        opts_lay.setContentsMargins(16, 12, 16, 12)
        opts_lay.setSpacing(12)

        row_a = QHBoxLayout()
        row_a.setSpacing(28)
        mode_col, self.mode_buttons = self._seg_group(
            "Ne indirmek istiyorsun?", (("video", "Video + ses"), ("audio", "Sadece ses")), self._set_mode)
        row_a.addLayout(mode_col)
        self.container_box = QWidget()
        cbox = QHBoxLayout(self.container_box)
        cbox.setContentsMargins(0, 0, 0, 0)
        container_col, self.container_buttons = self._seg_group(
            "Kapsayıcı", (("mp4", "MP4"), ("mkv", "MKV")), self._set_container)
        cbox.addLayout(container_col)
        row_a.addWidget(self.container_box)
        row_a.addStretch(1)
        self.thumb_chip = self._chip("Küçük resmi göm")
        self.thumb_chip.setCheckable(True)
        self.thumb_chip.setChecked(True)
        self.thumb_chip.clicked.connect(self._refresh_summary)
        row_a.addWidget(self.thumb_chip, 0, Qt.AlignBottom)
        opts_lay.addLayout(row_a)

        self.height_box = QWidget()
        hbox = QHBoxLayout(self.height_box)
        hbox.setContentsMargins(0, 0, 0, 0)
        height_col, self.height_buttons = self._seg_group("Çözünürlük (en fazla)", _HEIGHTS, self._set_height)
        hbox.addLayout(height_col)
        hbox.addStretch(1)
        self.audio_box = QWidget()
        abox = QHBoxLayout(self.audio_box)
        abox.setContentsMargins(0, 0, 0, 0)
        audio_col, self.audio_buttons = self._seg_group("Ses biçimi", _AUDIO, self._set_audio)
        abox.addLayout(audio_col)
        abox.addStretch(1)
        opts_lay.addWidget(self.height_box)
        opts_lay.addWidget(self.audio_box)
        root.addWidget(opts)

        # ---- alt çubuk (kaydırılmaz, hep görünür)
        self.status_label = QLabel("")
        self.status_label.setObjectName("bannerOk")
        self.status_label.hide()
        outer.addWidget(self.status_label)

        bar = QFrame()
        bar.setObjectName("summaryBar")
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(18, 12, 12, 12)
        self.summary_label = QLabel("")
        self.summary_label.setObjectName("summaryText")
        self.cancel_btn = QPushButton("Vazgeç")
        self.cancel_btn.setObjectName("ghost")
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.clicked.connect(self.cancelled.emit)
        self.add_btn = QPushButton("  Seçilenleri listeye ekle ve indir")
        self.add_btn.setObjectName("primary")
        self.add_btn.setMinimumWidth(290)
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.setIcon(get_icon("download", color=theme.BG, hover=theme.BG, disabled=theme.TEXT_DIM, size=20))
        bar_lay.addWidget(self.summary_label, 1)
        bar_lay.addWidget(self.cancel_btn)
        bar_lay.addWidget(self.add_btn)
        outer.addWidget(bar)

        self.select_all_btn.clicked.connect(lambda: self._set_all(True))
        self.select_none_btn.clicked.connect(lambda: self._set_all(False))
        self.list_widget.itemChanged.connect(lambda _item: self._refresh_summary())
        self.add_btn.clicked.connect(self._emit)
        self._sync_buttons()

    # ------------------------------------------------------------------ küçük kurucular
    @staticmethod
    def _chip(text: str) -> QPushButton:
        chip = QPushButton(text)
        chip.setObjectName("chip")
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

    def _seg_group(self, title: str, items, handler):
        """Başlıklı segmentli düğme grubu: (yerleşim, {anahtar: düğme}) döndürür."""
        column = QVBoxLayout()
        column.setSpacing(6)
        label = QLabel(title)
        label.setObjectName("sectionLabel")
        column.addWidget(label)
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
        return column, buttons

    # ------------------------------------------------------------------ içerik
    def set_info(self, info: PlaylistInfo) -> None:
        self._info = info
        self.status_label.hide()
        self.title_label.setText(info.title)
        bits = [b for b in (info.channel, f"{len(info.entries)} video") if b]
        self.meta_label.setText("   ·   ".join(bits))
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for i, entry in enumerate(info.entries, 1):
            duration = _duration(entry.duration)
            text = f"{i:>3}.   {entry.title}" + (f"     ·     {duration}" if duration else "")
            if not entry.available:
                text += "     (kullanılamıyor)"
            item = QListWidgetItem(text)
            if entry.available:
                item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
                item.setCheckState(Qt.Checked)
            else:
                item.setFlags(Qt.NoItemFlags)
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)
        self._refresh_summary()

    def selected_entries(self) -> list:
        if self._info is None:
            return []
        return [entry for i, entry in enumerate(self._info.entries)
                if i < self.list_widget.count()
                and self.list_widget.item(i).checkState() == Qt.Checked and entry.available]

    def selection(self) -> Selection:
        sel = Selection(mode=self._mode, container=self._container, embed_thumbnail=self.thumb_chip.isChecked())
        sel.height = None if self._height == "best" else int(self._height)
        if self._audio == "m4a":
            sel.audio, sel.audio_kbps = "m4a", 192
        else:
            sel.audio, sel.audio_kbps = "mp3", int(self._audio.split("_")[1])
        return sel

    # ------------------------------------------------------------------ olaylar
    def _set_all(self, checked: bool) -> None:
        self.list_widget.blockSignals(True)
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.flags() & Qt.ItemIsUserCheckable:
                item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self.list_widget.blockSignals(False)
        self._refresh_summary()

    def _set_mode(self, key: str) -> None:
        self._mode = key
        self._sync_buttons()

    def _set_height(self, key: str) -> None:
        self._height = key
        self._sync_buttons()

    def _set_container(self, key: str) -> None:
        self._container = key
        self._sync_buttons()

    def _set_audio(self, key: str) -> None:
        self._audio = key
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        for key, btn in self.mode_buttons.items():
            btn.setChecked(key == self._mode)
        for key, btn in self.height_buttons.items():
            btn.setChecked(key == self._height)
        for key, btn in self.container_buttons.items():
            btn.setChecked(key == self._container)
        for key, btn in self.audio_buttons.items():
            btn.setChecked(key == self._audio)
        video = self._mode == "video"
        self.container_box.setVisible(video)
        self.height_box.setVisible(video)
        self.audio_box.setVisible(not video)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        chosen = len(self.selected_entries())
        total = sum(1 for e in (self._info.entries if self._info else []) if e.available)
        self.count_label.setText(f"{chosen} / {total} video seçili")
        summary = youtube.playlist_summary(self.selection())
        self.summary_label.setText(f"{chosen} video  ·  {summary}")
        self.add_btn.setEnabled(chosen > 0)

    def _emit(self) -> None:
        entries = self.selected_entries()
        if self._info is None or not entries:
            return
        self.download_requested.emit({"info": self._info, "entries": entries, "selection": self.selection()})
        self.status_label.setText(f"{len(entries)} video listeye eklendi. İndirmeler sırayla başlayacak.")
        self.status_label.show()
