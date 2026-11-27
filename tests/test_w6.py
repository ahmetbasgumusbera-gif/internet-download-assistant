"""W.6: kurallar, ayarlayıcı, istatistik, kopya denetimi, ağ duraklatma, ad uzantısı."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import QApplication

from core import rules, stats
from core.models import TaskStatus
from core.tuner import Tuner
from tests.test_http_engine import make_data, wait_for
from tests.test_manager import ManagerTestCase


class RulesTests(unittest.TestCase):
    def tearDown(self):
        rules.configure(False, "")

    def test_ext_of(self):
        self.assertEqual(rules.ext_of("a.jpg"), ".jpg")
        self.assertEqual(rules.ext_of("foto"), "")
        self.assertEqual(rules.ext_of("v1.2 notlar"), "")

    def test_pick_dir(self):
        rules.configure(False, "ext: iso, img => D:\\ISO\n# yorum\nsite: example.com => D:\\Oyun\nbozuk satir")
        self.assertEqual(rules.pick_dir("http://x/a.ISO", "", "C:\\d"), Path("D:\\ISO"))
        self.assertEqual(rules.pick_dir("http://dl.example.com/a.zip", "a.zip", "C:\\d"), Path("D:\\Oyun"))
        self.assertEqual(rules.pick_dir("http://y/a.zip", "a.zip", "C:\\d"), Path("C:\\d"))

    def test_clean_names(self):
        rules.configure(True, "strip: Oyunindir vip")
        self.assertEqual(rules.clean_name("[siteadi.com] Film Adi.mp4"), "Film Adi.mp4")
        self.assertEqual(rules.clean_name("www.site.net - Film.mkv"), "Film.mkv")
        self.assertEqual(rules.clean_name("Film (www.x.org).mp4"), "Film.mp4")
        self.assertEqual(rules.clean_name("Oyun-Oyunindir vip.zip"), "Oyun.zip")
        self.assertEqual(rules.clean_name("[1080p.BluRay] Film.mkv"), "[1080p.BluRay] Film.mkv")
        rules.configure(False, "")
        self.assertEqual(rules.clean_name("[siteadi.com] Film.mp4"), "[siteadi.com] Film.mp4")


class TunerStatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_tuner_explores_then_picks_faster(self):
        t = Tuner(self.tmp / "t.json")
        self.assertEqual(t.choose("s", 8), 8)
        t.record("s", 8, 1_000_000)
        self.assertEqual(t.choose("s", 8), 4)
        t.record("s", 4, 1_500_000)
        self.assertEqual(t.choose("s", 8), 4)
        t.record("s", 4, 100_000)               # kötü ölçüm: ortalama düşer
        t.record("s", 4, 100_000)
        self.assertEqual(Tuner(self.tmp / "t.json").choose("s", 8), 16, "8, 4'ten çok hızlıysa 16 denenir; kalıcı olmalı")
        t2 = Tuner(self.tmp / "t.json")
        t2.record("s", 16, 500_000)
        self.assertEqual(t2.choose("s", 8), 8)
        self.assertEqual(t.choose("s", 2), 2, "kullanıcının sınırı aşılmaz")

    def test_stats_summary(self):
        f = self.tmp / "s.jsonl"
        stats.record(f, "a.com", 1000, 1000, 2)
        stats.record(f, "a.com", 3000, 3000, 2)
        stats.record(f, "b.com", 500, 500, 1)
        s = stats.summary(f, datetime.now())
        self.assertEqual((s["total_bytes"], s["total_count"], s["month_count"]), (4500, 3, 3))
        self.assertEqual(s["top"][0][0], "a.com")
        self.assertAlmostEqual(s["avg_speed"], 4500 / 5)
        self.assertEqual(stats.summary(None)["total_count"], 0)


class ManagerW6Tests(ManagerTestCase):
    def test_find_completed(self):
        self.serve(make_data(30_000))
        m = self.manager()
        tid = m.add(self.url())
        self.wait_status(m, tid, TaskStatus.COMPLETED)
        found = m.find_completed(self.url())
        self.assertIsNotNone(found)
        task = m.get_task(tid)
        self.assertIsNotNone(m.find_completed("http://baska/x", task.file_name, task.file_size))
        task.final_path.unlink()
        self.assertIsNone(m.find_completed(self.url()))
        self.assertTrue(wait_for(lambda: m.stats_file().exists()), "istatistik yazılmalı")

    def test_net_pause_and_resume(self):
        data = make_data(2_000_000)
        cfg = self.serve(data)
        cfg.delay = 0.02
        m = self.manager()
        tid = m.add(self.url())
        self.assertTrue(wait_for(lambda: m.get_task(tid).downloaded > 200_000))
        self.assertEqual(m.net_pause(), 1)
        self.wait_status(m, tid, TaskStatus.PAUSED)
        late = m.add(self.url("b.bin"))
        import time
        time.sleep(0.4)
        self.assertEqual(self.status(m, late), TaskStatus.QUEUED, "ağ yokken yeni indirme başlamamalı")
        cfg.delay = 0
        m.net_resume()
        self.wait_status(m, tid, TaskStatus.COMPLETED, timeout=30)
        self.wait_status(m, late, TaskStatus.COMPLETED, timeout=30)


class CaptureNameTests(unittest.TestCase):
    def test_extension_is_kept(self):
        app = QApplication.instance() or QApplication([])
        from gui.components.capture_dialog import CaptureDialog
        d = CaptureDialog({"url": "http://x/a.jpg", "filename": "a.jpg"}, Path("."))
        d.name_edit.setText("benim foto")
        self.assertEqual(d.values()["file_name"], "benim foto.jpg")
        d.name_edit.setText("baska.png")
        self.assertEqual(d.values()["file_name"], "baska.png")
        d.deleteLater()


if __name__ == "__main__":
    unittest.main()
