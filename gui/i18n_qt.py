"""
Arayüz dilini çalışan pencerelere uygular (İngilizce mod): açılan her pencere / menü gösterilirken metinleri çevrilir,
dinamik metin (QLabel/düğme/ipucu/başlık) ayarlanırken çevrilir ve dil değişince açık tüm pencereler yeniden çevrilir.
Kaynak (Türkçe) metin her parçacıkta bir özellik olarak saklanır; böylece Türkçeye dönüş kayıpsızdır.
Türkçe modda hiçbir şey yapılmaz (yalnızca Qt'nin kendi düğmeleri Türkçeleşir).
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QLibraryInfo, QObject, QTranslator
from PySide6.QtWidgets import (
    QAbstractButton, QApplication, QCheckBox, QComboBox, QLabel, QLineEdit, QMenu, QPlainTextEdit, QPushButton,
    QTabWidget, QToolButton, QWidget,
)

from core import i18n
from utils.logger import get_logger

log = get_logger(__name__)

_SRC, _TIP, _TITLE, _PH, _TABS, _ITEMS = ("_ida_src", "_ida_tip", "_ida_title", "_ida_ph", "_ida_tabs", "_ida_items")

# Yama uygulanmadan önceki özgün işlevler (gezgin bunları doğrudan kullanır: çifte çeviri olmaz)
_ORIG_LABEL = QLabel.setText
_ORIG_BUTTON = QAbstractButton.setText
_ORIG_TIP = QWidget.setToolTip
_ORIG_TITLE = QWidget.setWindowTitle

_patched = False
_patched_ok = False
_filter = None
_translator = None


# ---------------------------------------------------------------- dinamik metin: ayarlanırken çevir
def _remember(widget, key, text) -> None:
    try:
        widget.setProperty(key, text)
    except RuntimeError:
        pass


def _text_wrapper(original):
    def setter(self, text, *args, **kwargs):
        if isinstance(text, str):
            _remember(self, _SRC, text)
            text = i18n.tr(text)
        return original(self, text, *args, **kwargs)
    return setter


def _tip_wrapper(original):
    def setter(self, text, *args, **kwargs):
        if isinstance(text, str):
            _remember(self, _TIP, text)
            text = i18n.tr(text)
        return original(self, text, *args, **kwargs)
    return setter


def _title_wrapper(original):
    def setter(self, text, *args, **kwargs):
        if isinstance(text, str):
            _remember(self, _TITLE, text)
            text = i18n.tr(text)
        return original(self, text, *args, **kwargs)
    return setter


def _install_patches() -> None:
    """Metin ayarlayıcılarını sarar (bir kez). Başarısız olursa yalnızca durağan metinler (gezgin) çevrilir."""
    global _patched, _patched_ok
    if _patched:
        return
    _patched = True
    try:
        QLabel.setText = _text_wrapper(_ORIG_LABEL)
        QAbstractButton.setText = _text_wrapper(_ORIG_BUTTON)
        QWidget.setToolTip = _tip_wrapper(_ORIG_TIP)
        QWidget.setWindowTitle = _title_wrapper(_ORIG_TITLE)
        for cls in (QPushButton, QToolButton, QCheckBox):      # kendi setText'i olan alt sınıflar da sarılır
            probe = cls()
            probe.setText("Ayarlar")
            if probe.text() != "Settings":
                setattr(cls, "setText", _text_wrapper(getattr(cls, "setText")))
            probe.deleteLater()
        _patched_ok = True
    except Exception:
        log.warning("Metin ayarlayıcıları sarılamadı: dinamik metinler Türkçe kalabilir", exc_info=True)


# ---------------------------------------------------------------- gezgin: açık pencerelerdeki metinleri çevir
def _source(widget, key, current: str, prev: str) -> str:
    """Pencere parçacığının Türkçe kaynak metni: saklanan özellik hâlâ güncelse o, değilse şu anki metin."""
    stored = widget.property(key)
    if isinstance(stored, str) and current in (stored, i18n.tr_for(prev, stored), i18n.tr(stored)):
        return stored
    return current


def _apply(widget, original, key, current: str, prev: str) -> None:
    source = _source(widget, key, current, prev)
    translated = i18n.tr(source)
    if translated != current:
        original(widget, translated)
    widget.setProperty(key, source)


def _menu(menu: QMenu, prev: str) -> None:
    title = menu.title()
    if title:
        source = _source(menu, _TITLE, title, prev)
        translated = i18n.tr(source)
        if translated != title:
            menu.setTitle(translated)
        menu.setProperty(_TITLE, source)
    for action in menu.actions():
        text = action.text()
        if text:
            source = _source(action, _SRC, text, prev)
            translated = i18n.tr(source)
            if translated != text:
                action.setText(translated)
            action.setProperty(_SRC, source)
        if action.menu() is not None:
            _menu(action.menu(), prev)


def _widget(w: QWidget, prev: str) -> None:
    custom = getattr(w, "retranslate", None)
    if callable(custom):
        custom()
    if isinstance(w, QMenu):
        _menu(w, prev)
    if isinstance(w, QLabel) and not callable(custom):
        text = w.text()
        if text:
            _apply(w, _ORIG_LABEL, _SRC, text, prev)
    elif isinstance(w, QAbstractButton):
        text = w.text()
        if text:
            _apply(w, _ORIG_BUTTON, _SRC, text, prev)
    if isinstance(w, QLineEdit) and w.placeholderText():
        source = _source(w, _PH, w.placeholderText(), prev)
        w.setPlaceholderText(i18n.tr(source))
        w.setProperty(_PH, source)
    if isinstance(w, QPlainTextEdit) and w.placeholderText():
        source = _source(w, _PH, w.placeholderText(), prev)
        w.setPlaceholderText(i18n.tr(source))
        w.setProperty(_PH, source)
    if isinstance(w, QTabWidget):
        stored = w.property(_TABS)
        sources = []
        for i in range(w.count()):
            current = w.tabText(i)
            old = stored[i] if isinstance(stored, list) and i < len(stored) else None
            source = old if old is not None and current in (old, i18n.tr_for(prev, old), i18n.tr(old)) else current
            sources.append(source)
            if i18n.tr(source) != current:
                w.setTabText(i, i18n.tr(source))
        w.setProperty(_TABS, sources)
    if isinstance(w, QComboBox):
        stored = w.property(_ITEMS)
        sources = []
        for i in range(w.count()):
            current = w.itemText(i)
            old = stored[i] if isinstance(stored, list) and i < len(stored) else None
            source = old if old is not None and current in (old, i18n.tr_for(prev, old), i18n.tr(old)) else current
            sources.append(source)
            if i18n.tr(source) != current:
                w.setItemText(i, i18n.tr(source))
        w.setProperty(_ITEMS, sources)
    tip = w.toolTip()
    if tip:
        _apply(w, _ORIG_TIP, _TIP, tip, prev)
    if w.isWindow():
        title = w.windowTitle()
        if title:
            _apply(w, _ORIG_TITLE, _TITLE, title, prev)
    if callable(custom):
        w.update()


def retranslate_tree(root: QWidget, prev: str | None = None) -> None:
    prev = prev or i18n.language()
    try:
        widgets = [root, *root.findChildren(QWidget)]
    except RuntimeError:
        return
    for widget in widgets:
        try:
            _widget(widget, prev)
        except RuntimeError:
            continue                                     # pencere parçacığı bu arada silinmiş
        except Exception:
            log.debug("Metin çevrilemedi", exc_info=True)


class _ShowFilter(QObject):
    """Bir pencere ya da menü gösterilirken içindeki metinleri çevirir (yalnızca İngilizce modda uygulamaya takılır)."""

    def eventFilter(self, obj, event) -> bool:
        if event.type() == QEvent.Show and isinstance(obj, QWidget) and (obj.isWindow() or isinstance(obj, QMenu)):
            retranslate_tree(obj)
        return False


# ---------------------------------------------------------------- Qt'nin kendi metinleri (Evet / Hayır / İptal ...)
def _qt_translator(code: str) -> None:
    global _translator
    app = QApplication.instance()
    if app is None:
        return
    try:
        if _translator is not None:
            app.removeTranslator(_translator)
            _translator = None
        if code == "tr":
            translator = QTranslator(app)
            folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
            if translator.load("qtbase_tr", folder):
                app.installTranslator(translator)
                _translator = translator
    except Exception:
        log.debug("Qt çevirisi yüklenemedi", exc_info=True)


# ---------------------------------------------------------------- dışarı açılan işlev
def apply_language(code: str, prev: str | None = None) -> None:
    """Dili değiştirir ve açık tüm pencereleri anında yeniden çevirir. code: "tr" | "en"."""
    global _filter
    prev = prev or i18n.language()
    i18n.set_language(code)
    app = QApplication.instance()
    if app is None:
        return
    _qt_translator(i18n.language())
    if i18n.language() == "en":
        _install_patches()
        if _filter is None:
            _filter = _ShowFilter()
            app.installEventFilter(_filter)
    elif _filter is not None:
        app.removeEventFilter(_filter)
        _filter = None
    if prev == i18n.language() and i18n.language() == "tr":
        return
    for top in app.topLevelWidgets():
        retranslate_tree(top, prev)
