"""W.4: hız basamakları, kart başına hız sınırı, ayar penceresi kaydırma alanı."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
import unittest

from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout,
)

from core.models import TaskStatus
from core.ratelimit import RateLimiter
from core.speed import CompositeLimiter, limit_text, step_limit
from gui.components.dialog_fit import fit_dialog
from tests.test_http_engine import make_data, wait_for
from tests.test_manager import ManagerTestCase


class SpeedTests(unittest.TestCase):
    def test_steps(self):
        self.assertEqual(step_limit(1024, True), 2048)
        self.assertEqual(step_limit(1024, False), 512)
        self.assertEqual(step_limit(20480, True), 0)
        self.assertEqual(step_limit(0, True), 0)
        self.assertEqual(step_limit(0, False), 20480)
        self.assertEqual(step_limit(100, False), 128)
        self.assertEqual(limit_text(0), "Sınırsız")
        self.assertEqual(limit_text(512), "512 KB/s")
        self.assertEqual(limit_text(1536), "1.5 MB/s")

    def test_composite_uses_lowest_rate(self):
        c = CompositeLimiter(RateLimiter(0), RateLimiter(500_000), RateLimiter(200_000))
        self.assertEqual(c.rate, 200_000)
        self.assertEqual(CompositeLimiter(RateLimiter(0), RateLimiter(0)).rate, 0)


class TaskLimitTests(ManagerTestCase):
    def test_task_limit_slows_download_and_is_stored(self):
        data = make_data(300_000)
        self.serve(data)
        m = self.manager()
        m.set_hold(True)
        tid = m.add(self.url())
        m.set_task_limit(tid, 150)                       # 150 KB/sn
        self.assertEqual(m.get_task_limit(tid), 150)
        self.assertEqual(m.add(self.url()), tid, "hız sınırı yinelenen iş denetimini bozmamalı")
        start = time.monotonic()
        m.set_hold(False)
        self.wait_status(m, tid, TaskStatus.COMPLETED, timeout=30)
        self.assertGreater(time.monotonic() - start, 1.3)
        self.assertEqual(m.get_task(tid).final_path.read_bytes(), data)
        m.set_task_limit(tid, 0)
        self.assertEqual(m.get_task_limit(tid), 0)


class FitDialogTests(unittest.TestCase):
    def test_content_moves_into_scroll_area_and_footer_stays(self):
        app = QApplication.instance() or QApplication([])
        dialog = QDialog()
        root = QVBoxLayout(dialog)
        for i in range(40):
            root.addWidget(QLabel(f"satır {i}"))
        save = QPushButton("Kaydet")
        save.setObjectName("primary")
        footer = QHBoxLayout()
        footer.addWidget(save)
        root.addLayout(footer)
        fit_dialog(dialog)
        scroll = dialog.findChild(QScrollArea)
        self.assertIsNotNone(scroll)
        self.assertIs(save.parentWidget(), dialog, "Kaydet düğmesi kaydırma alanının dışında kalmalı")
        self.assertEqual(len(scroll.widget().findChildren(QLabel)), 40)
        screen = app.primaryScreen().availableGeometry()
        self.assertLessEqual(dialog.height(), int(screen.height() * 0.9) + 1)
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
