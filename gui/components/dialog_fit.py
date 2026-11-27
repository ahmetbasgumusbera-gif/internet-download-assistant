"""
Pencereyi ekrana sığdırır: mevcut yerleşimin içeriğini kaydırma alanına taşır (Kaydet/İptal şeridi altta sabit kalır),
pencere boyutunu ekranın en çok %90'ı kadar açar ve istenirse küçültülüp büyütülebilir yapar.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QDialogButtonBox, QFrame, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from utils.logger import get_logger

log = get_logger(__name__)


def _is_footer(item) -> bool:
    widget = item.widget()
    if widget is not None:
        return isinstance(widget, QDialogButtonBox)
    layout = item.layout()
    if layout is None:
        return False
    for i in range(layout.count()):
        child = layout.itemAt(i).widget()
        if isinstance(child, QPushButton) and child.objectName() == "primary":
            return True
    return False


def _add(layout, item) -> None:
    widget = item.widget()
    if widget is not None:
        layout.addWidget(widget)
    elif item.layout() is not None:
        inner = item.layout()
        inner.setParent(None)
        layout.addLayout(inner)
    else:
        layout.addItem(item)                         # boşluk / esnek alan


def _compact(layout) -> None:
    """Form/ızgara yerleşimlerinin satır aralığını daraltır (ayarlar penceresi daha kompakt görünür)."""
    from PySide6.QtWidgets import QFormLayout, QGridLayout
    if isinstance(layout, (QFormLayout, QGridLayout)):
        layout.setVerticalSpacing(8)
    elif isinstance(layout, QVBoxLayout):
        layout.setSpacing(min(layout.spacing() if layout.spacing() >= 0 else 10, 10))
    for i in range(layout.count()):
        child = layout.itemAt(i).layout()
        if child is not None:
            _compact(child)


def _wrap(dialog, root) -> None:
    margins = root.contentsMargins()
    spacing = max(root.spacing(), 10)
    items = []
    while root.count():
        items.append(root.takeAt(0))
    footer = items.pop() if items and _is_footer(items[-1]) else None

    content = QWidget()
    content.setObjectName("listHost")
    body = QVBoxLayout()
    body.setContentsMargins(0, 0, 10, 0)
    body.setSpacing(spacing)
    for item in items:
        _add(body, item)
    content.setLayout(body)
    _compact(body)

    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setWidget(content)
    root.addWidget(scroll, 1)
    if footer is not None:
        _add(root, footer)
    root.setContentsMargins(margins)
    dialog.setProperty("idaScroll", True)
    dialog._ida_content = content
    dialog._ida_keep = (items, footer)                # Python tarafında yaşasın


def fit_dialog(dialog, ratio: float = 0.9) -> None:
    root = dialog.layout()
    if root is not None and not dialog.property("idaScroll"):
        try:
            _wrap(dialog, root)
        except Exception:
            log.exception("Pencere kaydırılabilir yapılamadı")
    screen = dialog.screen() or QApplication.primaryScreen()
    avail = screen.availableGeometry()
    dialog.setMaximumSize(16777215, 16777215)
    dialog.setMinimumSize(min(440, avail.width() - 40), min(320, avail.height() - 80))
    content = getattr(dialog, "_ida_content", None)
    hint = content.sizeHint() if content is not None else dialog.sizeHint()
    extra_w, extra_h = (80, 150) if content is not None else (0, 0)
    width = min(max(hint.width() + extra_w, 560), int(avail.width() * ratio))
    height = min(hint.height() + extra_h, int(avail.height() * ratio))
    dialog.resize(width, height)
    dialog.move(avail.center().x() - width // 2, avail.center().y() - height // 2)
