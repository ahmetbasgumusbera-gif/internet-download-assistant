"""W.5: gruplama ve arama (arayüz duman testleri)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication

from core.database import Database
from core.manager import DownloadManager
from gui import theme
from gui.bridge import ManagerBridge
from gui.main_window import MainWindow
from tests.test_gui_smoke import pump
from tests.test_http_engine import FastHttpEngine
from utils.config_manager import ConfigManager


class W5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(theme.build_stylesheet())

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.config = ConfigManager(self.tmp / "config.json")
        self.config.set("download_dir", str(self.tmp / "dl"))
        self.manager = DownloadManager(self.config, Database(self.tmp / "h.db"), engines=[FastHttpEngine])
        self.window = MainWindow(self.manager, self.config, ManagerBridge(self.manager))
        self.window.show()
        self.manager.set_hold(True)          # hiçbir şey inmesin

    def tearDown(self):
        self.window.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def add(self, name, **options):
        tid = self.manager.add(f"http://127.0.0.1:9/{name}", options=options)
        pump(self.app, lambda: tid in self.window._rows)
        return tid

    def test_playlist_items_share_one_header_and_collapse(self):
        a = self.add("a.bin", group="Liste A", group_id="A")
        b = self.add("b.bin", group="Liste A", group_id="A")
        c = self.add("c.bin")
        w = self.window
        self.assertEqual(list(w._groups), ["A"])
        self.assertEqual(w._group_members("A").sort() or sorted(w._group_members("A")), sorted([a, b]))
        w._refresh_all()
        self.assertFalse(w._groups["A"]["header"].isHidden())
        idx = [w.list_layout.indexOf(x) for x in (w._groups["A"]["header"], w._rows[a], w._rows[b])]
        self.assertEqual(sorted(idx), list(range(min(idx), min(idx) + 3)), "başlık ve satırlar ardışık olmalı")
        self.assertLess(idx[0], min(idx[1:]), "başlık satırların üstünde olmalı")
        w._groups["A"]["toggle"].click()
        self.assertTrue(w._rows[a].isHidden() and w._rows[b].isHidden())
        self.assertFalse(w._rows[c].isHidden())
        self.manager.remove(a)
        self.manager.remove(b)
        pump(self.app, lambda: not w._groups)
        self.assertEqual(w._groups, {})

    def test_search_filters_rows(self):
        a = self.add("alpha.bin")
        b = self.add("beta.bin")
        w = self.window
        w.search_edit.setText("alpha")
        self.assertFalse(w._rows[a].isHidden())
        self.assertTrue(w._rows[b].isHidden())
        w.search_edit.setText("")
        self.assertFalse(w._rows[b].isHidden())
        w.date_combo.setCurrentIndex(1)       # bugün: yeni eklenenler görünür
        self.assertFalse(w._rows[a].isHidden())


if __name__ == "__main__":
    unittest.main()
