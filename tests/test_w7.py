"""W.7: dinamik parçalama, küçük resim türleri, anahtar düğmesi, fareyle toplu seçim."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication

from core import thumbgen
from core.database import Database
from core.engines.http_engine import HttpEngine
from core.manager import DownloadManager
from core.models import DownloadTask
from core.tuner import Tuner
from gui import theme
from gui.bridge import ManagerBridge
from gui.components.toggle_switch import ToggleSwitch
from gui.main_window import MainWindow
from tests.test_gui_smoke import pump
from tests.test_http_engine import FastHttpEngine
from utils.config_manager import ConfigManager


class PlanTests(unittest.TestCase):
    def engine(self, connections=4):
        return HttpEngine(DownloadTask(url="http://x/a.bin", save_dir=Path(".")), None, connections)

    def test_big_file_is_split_into_small_parts(self):
        size = 100 * 1024 * 1024
        ranges = self.engine(4)._plan_ranges(size)
        self.assertEqual(len(ranges), 7)                       # 16 MB'lık parçalar
        self.assertEqual(ranges[0][0], 0)
        self.assertEqual(ranges[-1][1], size - 1)
        for (_, end), (start, _) in zip(ranges, ranges[1:]):
            self.assertEqual(start, end + 1, "parçalar kesintisiz olmalı")

    def test_small_file_keeps_connection_count(self):
        ranges = self.engine(4)._plan_ranges(10 * 1024 * 1024)
        self.assertEqual(len(ranges), 4)

    def test_part_count_is_capped(self):
        ranges = self.engine(8)._plan_ranges(50 * 1024 ** 3)
        self.assertEqual(len(ranges), HttpEngine.MAX_PARTS)


class TunerUpTests(unittest.TestCase):
    def test_tries_double_then_settles(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            t = Tuner(tmp / "t.json")
            t.record("s", 8, 1_000_000)
            t.record("s", 4, 500_000)
            self.assertEqual(t.choose("s", 8), 16, "8, 4'ten belirgin hızlıysa 16 denenir")
            t.record("s", 16, 1_020_000)
            self.assertEqual(t.choose("s", 8), 8, "fark %10'dan azsa az bağlantı seçilir")
            t.record("s", 16, 3_000_000)
            t.record("s", 16, 3_000_000)
            self.assertEqual(t.choose("s", 8), 16)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ThumbKindTests(unittest.TestCase):
    def test_kinds(self):
        for name in ("a.jpg", "a.PNG", "a.gif", "a.avif", "a.webp", "a.bmp", "a.tiff"):
            self.assertEqual(thumbgen.kind(name), "image", name)
        for name in ("a.mp4", "a.MKV", "a.avi", "a.mov", "a.webm"):
            self.assertEqual(thumbgen.kind(name), "video", name)
        for name in ("a.zip", "a.exe", "a.pdf", "a", ""):
            self.assertEqual(thumbgen.kind(name), "", name)

    def test_missing_ffmpeg_or_file_is_harmless(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            self.assertFalse(thumbgen.make_thumb(tmp / "yok.jpg", tmp / "t.jpg", False))
            (tmp / "a.jpg").write_bytes(b"x")
            with mock.patch("core.thumbgen.tools.tool_path", return_value=None):
                self.assertFalse(thumbgen.make_thumb(tmp / "a.jpg", tmp / "t.jpg", False))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class GuiW7Tests(unittest.TestCase):
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
        self.manager.set_hold(True)

    def tearDown(self):
        self.window.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_toggle_switch_state(self):
        sw = ToggleSwitch()
        self.assertFalse(sw.isChecked())
        sw.setChecked(True)
        self.assertTrue(sw.isChecked())
        self.assertEqual(sw._t, 1.0)
        sw.click()
        self.assertFalse(sw.isChecked())
        sw.deleteLater()

    def test_band_selects_intersecting_rows_only(self):
        ids = []
        for name in ("a.bin", "b.bin"):
            tid = self.manager.add(f"http://127.0.0.1:9/{name}")
            pump(self.app, lambda t=tid: t in self.window._rows)
            ids.append(tid)
        w = self.window
        pump(self.app, timeout=0.3)
        w._band_select(QRect(0, 0, 10000, 10000))
        self.assertTrue(all(w._checks[i].isChecked() for i in ids))
        w._band_select(QRect(0, 50000, 10, 10))
        self.assertFalse(any(w._checks[i].isChecked() for i in ids))
        w._band_select(QRect(0, 50000, 10, 10), base={ids[0]})
        self.assertTrue(w._checks[ids[0]].isChecked() and not w._checks[ids[1]].isChecked())


if __name__ == "__main__":
    unittest.main()
