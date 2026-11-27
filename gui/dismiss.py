"""Dışarı tıklayınca kapanan pencereler: ayarlar ve ona bağlı alt pencereler (tanı, tarayıcı, kurallar)."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication


class _Dismiss(QObject):
    """
    Kayıtlı pencereler üst üste bir yığın oluşturur (ilk açılan altta). Fareyle tıklanınca:
      * yığındaki bir pencerenin üstüne tıklandıysa, onun üstündeki pencereler kapanır (yalnızca öndekiler);
      * başka bir pencereye (ör. ana pencere) tıklandıysa yığındaki hepsi kapanır; tıklama yine de o pencereye ulaşır.
    Açılır menü, ipucu ve sistem iletişim kutuları (dosya seç, onay) sayılmaz. Yığın boşken filtre uygulamadan çıkarılır.
    """

    def __init__(self):
        super().__init__()
        self._stack: list = []
        self._installed = False

    @staticmethod
    def _visible(widget) -> bool:
        try:
            return widget.isVisible()
        except RuntimeError:
            return False

    def _set_installed(self, on: bool) -> None:
        app = QApplication.instance()
        if app is None or on == self._installed:
            return
        (app.installEventFilter if on else app.removeEventFilter)(self)
        self._installed = on

    def register(self, widget) -> None:
        if widget in self._stack:
            return
        self._stack.append(widget)
        self._set_installed(True)
        widget.destroyed.connect(lambda *_a, w=widget: self._forget(w))
        finished = getattr(widget, "finished", None)
        if finished is not None:
            finished.connect(lambda *_a, w=widget: self._forget(w))

    def _forget(self, widget) -> None:
        if widget in self._stack:
            self._stack.remove(widget)
        if not self._stack:
            self._set_installed(False)

    def eventFilter(self, obj, event) -> bool:
        if event.type() != QEvent.MouseButtonPress or not self._stack:
            return False
        if QApplication.activePopupWidget() is not None:
            return False
        modal = QApplication.activeModalWidget()
        if modal is not None and modal not in self._stack:
            return False                                   # dosya seç / onay kutusu açık
        try:
            widget = QApplication.widgetAt(event.globalPosition().toPoint())
        except AttributeError:
            return False
        if widget is None:
            return False
        top = widget.window()
        if top.windowType() in (Qt.Popup, Qt.ToolTip, Qt.SplashScreen):
            return False
        stack = [w for w in self._stack if self._visible(w)]
        if not stack:
            return False
        keep = stack.index(top) + 1 if top in stack else 0
        for window in reversed(stack[keep:]):
            try:
                window.close()
            except RuntimeError:
                pass
        return False


DISMISS = _Dismiss()
