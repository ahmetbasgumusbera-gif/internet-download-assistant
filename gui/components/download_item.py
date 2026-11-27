"""
Tek bir indirmeyi gösteren kart: dosya türü rozeti, ad, durum, ilerleme çubuğu ve kontrol düğmeleri.
Kart kendi başına bir şey yapmaz; kullanıcı eylemlerini sinyal olarak dışarı verir.
"""
from __future__ import annotations

import html
from typing import Optional
from urllib.parse import unquote, urlparse

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QProgressBar, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from core.i18n import tr
from core.models import DownloadTask, TaskStatus
from gui import theme
from gui.icons import get_icon
from gui.thumbnails import ThumbnailLoader, cover_crop
from utils.helpers import format_eta, format_size, format_speed, get_category, get_extension, is_dangerous


class ElidedLabel(QLabel):
    """Sığmayan metni "…" ile kısaltan etiket (pencere daralınca düzen bozulmaz)."""

    def __init__(self, mode=Qt.ElideRight, parent=None):
        super().__init__(parent)
        self._full = ""
        self._src = ""
        self._mode = mode
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setMinimumWidth(40)

    def set_full_text(self, text: str, tooltip: bool = True) -> None:
        translated = tr(text)
        self._src = text
        if translated == self._full:
            return
        self._full = translated
        if tooltip:
            self.setToolTip(text)
        self._refresh()

    def retranslate(self) -> None:
        """Dil değişince kaynak (Türkçe) metinden yeniden çevirir."""
        if self._src:
            self._full = tr(self._src)
            self._refresh()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        width = max(10, self.width() - 2)
        self.setText(self.fontMetrics().elidedText(self._full, self._mode, width))


def _display_name(task: DownloadTask) -> str:
    """Başlık (ör. YouTube video adı) varsa onu, yoksa dosya adını, o da yoksa URL'nin son parçasını gösterir."""
    if task.title:
        return task.title
    if task.file_name:
        return task.file_name
    base = unquote(urlparse(task.url).path).rsplit("/", 1)[-1]
    return base or task.url


def _set_state(widget: QWidget, state: str) -> None:
    """Dinamik 'state' özelliğini değiştirir ve QSS'in yeniden uygulanmasını sağlar."""
    if widget.property("state") == state:
        return
    widget.setProperty("state", state)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class DownloadItemWidget(QFrame):
    pause_requested = Signal(str)
    resume_requested = Signal(str)
    cancel_requested = Signal(str)
    retry_requested = Signal(str)
    remove_requested = Signal(str)
    open_folder_requested = Signal(str)
    error_requested = Signal(str)

    def __init__(self, task: DownloadTask, parent=None):
        super().__init__(parent)
        self.task_id = task.id
        self._thumb_url = ""
        self._warn_dangerous = False
        self._task: Optional[DownloadTask] = None
        self.status: Optional[TaskStatus] = None
        self.setObjectName("card")
        self.setAttribute(Qt.WA_StyledBackground, True)

        # ---- sol: dosya türü rozeti
        self.type_label = QLabel()
        self.type_label.setFixedSize(46, 46)
        self.type_label.setAlignment(Qt.AlignCenter)

        # ---- orta: ad / ilerleme / bilgi
        self.name_label = ElidedLabel(Qt.ElideMiddle)
        self.name_label.setObjectName("fileName")

        self.badge = QLabel()
        self.badge.setObjectName("badge")
        self.badge.setAlignment(Qt.AlignCenter)

        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title_row.addWidget(self.name_label, 1)
        title_row.addWidget(self.badge, 0, Qt.AlignRight)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)

        self.info_label = ElidedLabel(Qt.ElideRight)
        self.info_label.setObjectName("info")
        self.percent_label = QLabel()
        self.percent_label.setObjectName("percent")
        self.percent_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        info_row = QHBoxLayout()
        info_row.setSpacing(10)
        info_row.addWidget(self.info_label, 1)
        info_row.addWidget(self.percent_label, 0)

        middle = QVBoxLayout()
        middle.setSpacing(8)
        middle.addLayout(title_row)
        middle.addWidget(self.progress)
        middle.addLayout(info_row)

        # ---- sağ: düğmeler
        self.btn_pause = self._make_button("pause", "Duraklat", self.pause_requested)
        self.btn_resume = self._make_button("play", "Devam et", self.resume_requested, role="accent")
        self.btn_retry = self._make_button("retry", "Tekrar dene", self.retry_requested, role="accent")
        self.btn_folder = self._make_button("folder", "Klasörde göster", self.open_folder_requested)
        self.btn_cancel = self._make_button("close", "İptal et", self.cancel_requested, role="danger")
        self.btn_remove = self._make_button("trash", "Listeden kaldır", self.remove_requested, role="danger")
        self.btn_info = QToolButton()
        self.btn_info.setObjectName("iconBtn")
        self.btn_info.setText("!")
        self.btn_info.setFixedSize(34, 34)
        self.btn_info.setCursor(Qt.PointingHandCursor)
        self.btn_info.setStyleSheet(
            f"QToolButton#iconBtn {{ color: {theme.RED}; font-size: 19px; font-weight: 800; }}")
        self.btn_info.clicked.connect(lambda: self.error_requested.emit(self.task_id))

        buttons = QHBoxLayout()
        buttons.setSpacing(2)
        for b in (self.btn_pause, self.btn_resume, self.btn_retry, self.btn_folder,
                  self.btn_cancel, self.btn_remove):
            buttons.addWidget(b)

        root = QHBoxLayout(self)
        root.setContentsMargins(16, 14, 12, 14)
        root.setSpacing(14)
        root.addWidget(self.type_label, 0, Qt.AlignVCenter)
        root.addLayout(middle, 1)
        buttons.insertWidget(0, self.btn_info)
        root.addLayout(buttons, 0)

        self.update_task(task)

    # ------------------------------------------------------------------ yardımcılar
    def _make_button(self, icon_name: str, tooltip: str, signal: Signal, role: str = "") -> QToolButton:
        btn = QToolButton()
        btn.setObjectName("iconBtn")
        btn.setIcon(get_icon(icon_name, size=20))
        btn.setIconSize(QSize(20, 20))
        btn.setFixedSize(34, 34)
        btn.setToolTip(tooltip)
        btn.setCursor(Qt.PointingHandCursor)
        if role:
            btn.setProperty("role", role)
        btn.clicked.connect(lambda: signal.emit(self.task_id))
        return btn

    # ------------------------------------------------------------------ güncelleme
    def update_task(self, task: DownloadTask) -> None:
        status = task.status
        key = status.value

        # ad ve tür rozeti
        self.name_label.set_full_text(_display_name(task))
        if task.thumbnail_url:
            self._set_thumbnail(task.thumbnail_url)
        else:
            self._update_type_badge(task.file_name)

        # durum rozeti
        text, color = theme.STATUS_STYLE.get(key, (key, theme.TEXT_MUTED))
        if status == TaskStatus.QUEUED and task.options.get("staged"):
            text = "Listede"
        self.badge.setText(text)
        c = QColor(color)
        self.badge.setStyleSheet(
            f"QLabel#badge {{ color: {color}; background-color: rgba({c.red()},{c.green()},{c.blue()},34); }}"
        )

        # kart / çubuk / bilgi satırı renk durumları
        _set_state(self, key)
        _set_state(self.progress, key)
        warn = self._warn_dangerous and status == TaskStatus.COMPLETED and is_dangerous(task.file_name)
        check = task.options.get("hash_check")
        bad_hash = isinstance(check, dict) and check.get("ok") is False
        _set_state(self.info_label, "error" if (status == TaskStatus.ERROR or bad_hash) else ("warn" if warn else "normal"))

        # ilerleme
        known = bool(task.file_size)
        if status in (TaskStatus.PROBING, TaskStatus.MERGING) or (
            status == TaskStatus.DOWNLOADING and not known
        ):
            self.progress.setRange(0, 0)           # belirsiz (kayan) animasyon
            self.percent_label.setText("")
        else:
            self.progress.setRange(0, 1000)
            value = 1000 if status == TaskStatus.COMPLETED else int(task.percent * 10)
            self.progress.setValue(value)
            if status == TaskStatus.COMPLETED:
                self.percent_label.setText("")
            elif known:
                self.percent_label.setText(f"%{task.percent:.0f}")
            else:
                self.percent_label.setText("")

        info = self._info_text(task)
        if warn:
            info = "⚠ Çalıştırılabilir dosya, kaynağına güvenmiyorsan açma   ·   " + info
        self.info_label.set_full_text(info, tooltip=status == TaskStatus.ERROR or warn)
        if status == TaskStatus.ERROR:
            self.info_label.setToolTip(task.error)
            self.btn_info.setToolTip(f"<p>{html.escape(task.error or 'Bilinmeyen hata')}</p>")

        self._update_buttons(status, bool(task.options.get("staged")) and status == TaskStatus.QUEUED)
        self.status = status
        self._task = task

    def set_warn_dangerous(self, enabled: bool) -> None:
        """Tamamlanan çalıştırılabilir dosyalarda uyarı satırını açar/kapatır."""
        if enabled == self._warn_dangerous:
            return
        self._warn_dangerous = enabled
        if self._task is not None:
            self.update_task(self._task)

    def _set_thumbnail(self, url: str) -> None:
        """Videonun küçük resmini (indirilene kadar yer tutucu) kartın solunda gösterir."""
        if url == self._thumb_url:
            return
        self._thumb_url = url
        self.type_label.setFixedSize(82, 46)
        self.type_label.setText("")
        self.type_label.setStyleSheet(
            f"QLabel {{ background-color: {theme.SURFACE_2}; border-radius: 10px; }}")

        def on_ready(pixmap) -> None:
            if pixmap is not None and url == self._thumb_url:
                self.type_label.setStyleSheet("QLabel { background: transparent; }")
                self.type_label.setPixmap(cover_crop(pixmap, 82, 46, 10))

        ThumbnailLoader.instance().request(url, on_ready)

    def _update_type_badge(self, file_name: str) -> None:
        if not file_name:
            label, color = "···", theme.CATEGORY_COLORS["Diğer"]
        else:
            ext = get_extension(file_name)
            label = ext.upper()[:4] if ext else "···"
            color = theme.CATEGORY_COLORS.get(get_category(file_name), theme.CATEGORY_COLORS["Diğer"])
        c = QColor(color)
        size = 11 if len(label) >= 4 else 12
        self.type_label.setText(label)
        self.type_label.setStyleSheet(
            f"QLabel {{ color: {color}; background-color: rgba({c.red()},{c.green()},{c.blue()},30);"
            f" border: 1px solid rgba({c.red()},{c.green()},{c.blue()},70); border-radius: 12px;"
            f" font-size: {size}px; font-weight: 800; }}"
        )

    @staticmethod
    def _info_text(task: DownloadTask) -> str:
        status = task.status
        size = format_size(task.file_size) if task.file_size else ""
        done = format_size(task.downloaded)
        short = task.options.get("summary_short", "")        # ör. "1080p · 30 FPS · MP4" (YouTube)
        lead = f"{short}   ·   " if short else ""

        if status == TaskStatus.DOWNLOADING:
            parts = [f"{done} / {size}" if size else done]
            parts.append(format_speed(task.speed))
            eta = task.eta
            if eta is not None:
                parts.append(f"{format_eta(eta)} kaldı")
            return "   ·   ".join(parts)
        if status == TaskStatus.PAUSED:
            return lead + (f"{done} / {size}" if size else done)
        if status == TaskStatus.COMPLETED:
            when = f"   ·   {task.finished_at:%d.%m.%Y %H:%M}" if task.finished_at else ""
            check = task.options.get("hash_check")
            tail = ""
            if isinstance(check, dict) and check.get("algo"):
                algo = str(check["algo"]).upper()
                tail = f"   ·   ✓ {algo} doğrulandı" if check.get("ok") else f"   ·   ✗ {algo} UYUŞMUYOR"
            return f"{lead}{size}{when}{tail}"
        if status == TaskStatus.ERROR:
            return task.error or "Bilinmeyen hata"
        if status == TaskStatus.QUEUED:
            return lead + ("Listede: başlatılmayı bekliyor" if task.options.get("staged") else "Sırada bekliyor")
        if status == TaskStatus.PROBING:
            return "Sunucuyla bağlantı kuruluyor…"
        if status == TaskStatus.MERGING:
             return "Dosya işleniyor (birleştirme, küçük resim, dönüştürme)…"
        if status == TaskStatus.CANCELLED:
            return "İndirme iptal edildi"
        return ""

    def _update_buttons(self, status: TaskStatus, staged: bool = False) -> None:
        if staged:
            status = TaskStatus.PAUSED          # "Listede" kartı: ▶ başlat + iptal
        visible = {
            TaskStatus.QUEUED:      (self.btn_pause, self.btn_cancel),
            TaskStatus.PROBING:     (self.btn_pause, self.btn_cancel),
            TaskStatus.DOWNLOADING: (self.btn_pause, self.btn_cancel),
            TaskStatus.MERGING:     (self.btn_cancel,),
            TaskStatus.PAUSED:      (self.btn_resume, self.btn_cancel),
            TaskStatus.ERROR:       (self.btn_retry, self.btn_remove),
            TaskStatus.CANCELLED:   (self.btn_retry, self.btn_remove),
            TaskStatus.COMPLETED:   (self.btn_folder, self.btn_remove),
        }.get(status, ())
        for btn in (self.btn_pause, self.btn_resume, self.btn_retry, self.btn_folder,
                    self.btn_cancel, self.btn_remove):
            btn.setVisible(btn in visible)
        self.btn_info.setVisible(status == TaskStatus.ERROR)