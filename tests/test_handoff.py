"""Bilet kaydı, YouTube çerez dosyası ve Range reddinde tek bağlantıya düşme testleri."""
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import handoff, ytauth
from core.models import DownloadTask, TaskStatus
from tests.test_http_engine import EngineTestCase, make_data
from utils import paths


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.h = handoff.Handoff()

    def task(self, status, error=""):
        return DownloadTask(url="http://x/a", save_dir=Path("."), id="t1", status=status, error=error)

    def test_lifecycle(self):
        t = self.h.new_ticket()
        self.assertEqual(self.h.state(t)["state"], "pending")
        self.h.attach(t, "t1")
        self.assertEqual(self.h.state(t)["state"], "running")
        self.h.on_task(self.task(TaskStatus.COMPLETED))
        self.assertEqual(self.h.state(t)["state"], "done")
        self.assertEqual(self.h.task_of(t), "t1")

    def test_browser_helpful_only_for_access_errors(self):
        t = self.h.new_ticket()
        self.h.attach(t, "t1")
        self.h.on_task(self.task(TaskStatus.ERROR, "Sunucu erişimi reddetti (HTTP 403)."))
        self.assertTrue(self.h.state(t)["browserHelpful"])
        self.h.on_task(self.task(TaskStatus.ERROR, "Diskte yeterli boş alan yok."))
        self.assertFalse(self.h.state(t)["browserHelpful"])

    def test_more_http_errors_are_helpful(self):
        cases = {
            "Sunucu hata döndürdü: HTTP 405": True,
            "Sunucu hatası (HTTP 503). Sunucu geçici olarak sorunlu olabilir.": True,
            "Sunucu erişimi reddetti (HTTP 403).": True,
            "Dosya bulunamadı (HTTP 404).": False,
            "Google Drive bu dosya için günlük indirme kotasını doldurmuş.": False,
            "Diskte yeterli boş alan yok.": False,
        }
        for error, expected in cases.items():
            with self.subTest(error=error):
                t = self.h.new_ticket()
                self.h.attach(t, "t1")
                self.h.on_task(self.task(TaskStatus.ERROR, error))
                self.assertEqual(self.h.state(t)["browserHelpful"], expected)

    def test_removed_cancelled_unknown(self):
        t = self.h.new_ticket()
        self.h.attach(t, "t1")
        self.h.on_removed("t1")
        self.assertEqual(self.h.state(t)["state"], "gone")
        t2 = self.h.new_ticket()
        self.h.finish(t2, "cancelled")
        self.assertEqual(self.h.state(t2)["state"], "cancelled")
        self.assertEqual(self.h.state("yok")["state"], "unknown")


class YtAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.patch = mock.patch.object(paths, "DATA_DIR", self.tmp)
        self.patch.start()
        ytauth.configure(True, "")

    def tearDown(self):
        self.patch.stop()
        ytauth.configure(True, "")
        shutil.rmtree(self.tmp, ignore_errors=True)

    JAR = [
        {"domain": ".youtube.com", "path": "/", "secure": True, "hostOnly": False, "httpOnly": True,
         "name": "SAPISID", "value": "x", "expires": 99},
        {"domain": "evil.com", "path": "/", "name": "a", "value": "b"},
        {"domain": "www.google.com", "hostOnly": True, "path": "/", "name": "n", "value": "v\tx"},
    ]

    def test_only_youtube_google_and_valid_cookies(self):
        lines = ytauth.to_netscape(self.JAR).splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].startswith("#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t99\tSAPISID\tx"))
        self.assertEqual(ytauth.to_netscape([{"domain": "evil.com", "name": "a", "value": "b"}]), "")

    def test_save_and_cookie_args_use_a_copy(self):
        self.assertEqual(ytauth.cookie_args(), [])
        self.assertTrue(ytauth.save_jar(self.JAR))
        self.assertTrue(ytauth.has_login())
        args = ytauth.cookie_args()
        self.assertEqual(args[0], "--cookies")
        self.assertNotEqual(Path(args[1]), ytauth.cookie_file())
        self.assertEqual(Path(args[1]).read_text(encoding="utf-8"), ytauth.cookie_file().read_text(encoding="utf-8"))
        self.assertFalse(ytauth.save_jar([]), "boş liste eskisini silmemeli")
        self.assertTrue(ytauth.cookie_file().exists())

    def test_disabled_and_browser_mode(self):
        ytauth.save_jar(self.JAR)
        ytauth.configure(False, "")
        self.assertEqual(ytauth.cookie_args(), [])
        ytauth.configure(True, "Firefox")
        self.assertEqual(ytauth.cookie_args(), ["--cookies-from-browser", "firefox"])


class DriveConfirmTests(unittest.TestCase):
    def test_new_style_form(self):
        from core.engines.http_engine import parse_drive_form
        page = ('<form id="download-form" action="https://drive.usercontent.google.com/download" method="get">'
                '<input type="hidden" name="id" value="ABC"><input type="hidden" name="export" value="download">'
                '<input type="hidden" name="confirm" value="t"><input type="hidden" name="uuid" value="u-1"></form>')
        self.assertEqual(parse_drive_form(page, "https://drive.usercontent.google.com/download?id=ABC"),
                         "https://drive.usercontent.google.com/download?id=ABC&export=download&confirm=t&uuid=u-1")

    def test_old_style_link_and_no_form(self):
        from core.engines.http_engine import parse_drive_form
        page = '<a href="/uc?export=download&amp;confirm=XY12&amp;id=ABC">indir</a>'
        self.assertEqual(parse_drive_form(page, "https://drive.google.com/uc?id=ABC"),
                         "https://drive.google.com/uc?export=download&confirm=XY12&id=ABC")
        self.assertIsNone(parse_drive_form("<html>yok</html>", "https://x/"))


class RangeRefusedTests(EngineTestCase):
    def test_range_refused_falls_back_to_single_stream(self):
        data = make_data(1_200_000)
        cfg = self.serve(data)
        cfg.part_status = 403            # sondalama (bytes=0-0) geçer, parça Range'leri 403 alır
        eng = self.engine(connections=4)
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)
        self.assertEqual(eng.task.final_path.read_bytes(), data)
        self.assertEqual(self.temp_dirs(), [])


if __name__ == "__main__":
    unittest.main()
