"""W.3: bekletme, sıra düzenleme, otomatik yeniden deneme, hash, bağlantı temizleme."""
import hashlib
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from core import hashcheck
from core.manager import is_transient
from core.models import TaskStatus
from core.server import clean_links
from tests.test_http_engine import make_data, wait_for
from tests.test_manager import ManagerTestCase


class TransientTests(unittest.TestCase):
    def test_classification(self):
        yes = ("Parça 2 indirilemedi: HTTP 503", "Sunucu hatası (HTTP 503). Sunucu geçici olarak sorunlu olabilir.",
               "Sunucuya bağlanılamadı: timed out", "Ağ hatası: Connection reset", "Sunucu çok fazla istek (HTTP 429)")
        no = ("Sunucu erişimi reddetti (HTTP 403).", "Sunucu hata döndürdü: HTTP 405", "Dosya bulunamadı (HTTP 404).",
              "Diskte yeterli boş alan yok.", "Google Drive günlük indirme kotasını doldurmuş.",
              "Parça 1 indirilemedi: Sunucu 206 yerine HTTP 403 döndürdü", "")
        for text in yes:
            self.assertTrue(is_transient(text), text)
        for text in no:
            self.assertFalse(is_transient(text), text)


class QueueTests(ManagerTestCase):
    def test_hold_keeps_queued_until_released(self):
        self.serve(make_data(50_000))
        m = self.manager()
        m.set_hold(True)
        tid = m.add(self.url())
        time.sleep(0.4)
        self.assertEqual(self.status(m, tid), TaskStatus.QUEUED)
        self.assertTrue(m.is_on_hold())
        m.set_hold(False)
        self.wait_status(m, tid, TaskStatus.COMPLETED)

    def test_queue_moves(self):
        self.serve(make_data(10_000))
        m = self.manager()
        m.set_hold(True)
        a, b, c = [m.add(self.url(f"{n}.bin")) for n in "abc"]
        self.assertEqual(m.queue_order(), [a, b, c])
        m.move_in_queue(c, "top")
        self.assertEqual(m.queue_order(), [c, a, b])
        m.move_in_queue(c, "down")
        self.assertEqual(m.queue_order(), [a, c, b])
        m.move_in_queue(a, "bottom")
        self.assertEqual(m.queue_order(), [c, b, a])
        m.place_in_queue(a, c, True)
        self.assertEqual(m.queue_order(), [a, c, b])
        self.assertFalse(m.move_in_queue("yok", "top"))


class AutoRetryTests(ManagerTestCase):
    def test_transient_error_is_retried(self):
        data = make_data(800_000)
        cfg = self.serve(data)
        cfg.lie_after_probe = True
        m = self.manager()
        m.retry_delays = (0.8,)
        tid = m.add(self.url())
        self.wait_status(m, tid, TaskStatus.ERROR)
        self.assertTrue(wait_for(lambda: "otomatik" in m.get_task(tid).error))
        cfg.lie_after_probe = False
        self.wait_status(m, tid, TaskStatus.COMPLETED, timeout=25)
        self.assertEqual(m.get_task(tid).final_path.read_bytes(), data)

    def test_permanent_error_is_not_retried(self):
        cfg = self.serve(make_data(1000))
        cfg.error_status = 404
        m = self.manager()
        m.retry_delays = (0.2,)
        tid = m.add(self.url())
        self.wait_status(m, tid, TaskStatus.ERROR)
        time.sleep(0.6)
        self.assertEqual(self.status(m, tid), TaskStatus.ERROR)
        self.assertNotIn("otomatik", m.get_task(tid).error)


class HashTests(unittest.TestCase):
    def test_parse_and_hash(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            f = tmp / "a.bin"
            f.write_bytes(b"merhaba" * 1000)
            want = hashlib.sha256(f.read_bytes()).hexdigest()
            self.assertEqual(hashcheck.file_hash(f, "sha256"), want)
            self.assertEqual(hashcheck.parse_expected(f"SHA256: {want.upper()}"), ("sha256", want))
            self.assertEqual(hashcheck.parse_expected(f"{want} *a.bin"), ("sha256", want))
            self.assertEqual(hashcheck.parse_expected("abc")[0], "")
            self.assertEqual(hashcheck.parse_expected("d41d8cd98f00b204e9800998ecf8427e")[0], "md5")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class LinksTests(unittest.TestCase):
    def test_clean_links(self):
        data = clean_links({"pageUrl": "https://s/p", "items": [
            {"url": "https://s/a.zip", "ext": "ZIP", "text": " A "}, {"url": "https://s/a.zip"},
            {"url": "javascript:x"}, "bozuk"]})
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual((data["items"][0]["ext"], data["items"][0]["text"]), ("zip", "A"))
        with self.assertRaises(ValueError):
            clean_links({"pageUrl": "chrome://x"})


if __name__ == "__main__":
    unittest.main()
