"""Uygulama simgesinden (gui/icons.py) installer/IDA.ico üretir. build.ps1 tarafından çalıştırılır."""
import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QIODevice  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from gui.icons import app_icon  # noqa: E402

app = QApplication([])
icon = app_icon()
images = []
for size in (16, 24, 32, 48, 64, 128, 256):
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    icon.pixmap(size, size).save(buffer, "PNG")
    images.append((size, bytes(buffer.data())))

header = struct.pack("<HHH", 0, 1, len(images))
offset = 6 + 16 * len(images)
entries, data = b"", b""
for size, png in images:
    dim = 0 if size >= 256 else size
    entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset + len(data))
    data += png
target = ROOT / "installer" / "IDA.ico"
target.write_bytes(header + entries + data)
print(f"Simge yazildi: {target}")
