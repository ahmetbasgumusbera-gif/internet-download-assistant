"""W.17: silme güvencesi, listede bekleyenin küçük resmi, yakalama penceresinde Listeye ekle."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import types
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtWidgets import QApplication

from core.models import TaskStatus
from tests.test_http_engine import make_data, wait_for
from tests.test_manager import ManagerTestCase


class RemoveTests(ManagerTestCase):
    def test_cancel_while_connecting_then_remove_really_removes(self):
        cfg = self.serve(make_data(50_000))
        cfg.header_delay = 30                      # sunucu yanıt vermiyor: indirme "bağlanıyor"da kalır
        m = self.manager()
        tid = m.add(self.url())
        self.assertTrue(wait_for(lambda: self.status(m, tid) == TaskStatus.PROBING))
        m.cancel(tid)
        self.assertEqual(self.status(m, tid), TaskStatus.CANCELLED)
        m.remove(tid)
        self.assertTrue(wait_for(lambda: m.get_task(tid) is None, 6), "iptal edilen indirme silinebilmeli")
        self.assertTrue(wait_for(lambda: self.db_get(tid) is None))

    def test_hung_engine_does_not_keep_the_record(self):
        self.serve(make_data(1000))
        m = self.manager()
        m.set_hold(True)
        tid = m.add(self.url())
        entry = m._entries[tid]
        entry.engine = types.SimpleNamespace(cancel=lambda: None, stop=lambda: None)   # kapanmayan motor
        entry.thread = None
        m.remove(tid)
        self.assertTrue(wait_for(lambda: m.get_task(tid) is None, 6), "kayıt en geç 3 sn'de silinmeli")
        self.assertIsNone(self.db_get(tid))

    def test_finished_engine_thread_is_removed_immediately(self):
        self.serve(make_data(1000))
        m = self.manager()
        m.set_hold(True)
        tid = m.add(self.url())
        done = threading.Thread(target=lambda: None)
        done.start()
        done.join()
        entry = m._entries[tid]
        entry.engine = types.SimpleNamespace(cancel=lambda: None, stop=lambda: None)
        entry.thread = done
        m.remove(tid)
        self.assertIsNone(m.get_task(tid))

    def test_removed_record_is_not_written_back_on_shutdown(self):
        self.serve(make_data(1000))
        m = self.manager()
        m.set_hold(True)
        tid = m.add(self.url())
        m._entries[tid].remove_after = True
        m.shutdown()
        self.assertIsNone(self.db_get(tid))


class StagedPreviewTests(ManagerTestCase):
    def test_listed_image_gets_a_thumbnail_before_download(self):
        class FakeResponse:
            status_code = 206

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def iter_content(self, size):
                yield b"x" * 2000

        self.serve(make_data(1000))
        m = self.manager()
        m.set_hold(True)
        with mock.patch("core.manager.requests.get", return_value=FakeResponse()), \
                mock.patch("core.manager.thumbgen.make_thumb", return_value=True):
            tid = m.add(self.url("resim.jpg"), listed=True)
            self.assertTrue(wait_for(lambda: (m.get_task(tid).thumbnail_url or "").startswith("file:")),
                            "bekleyen resim için küçük resim üretilmeli")

    def test_non_visual_file_gets_no_preview(self):
        self.serve(make_data(1000))
        m = self.manager()
        m.set_hold(True)
        with mock.patch("core.manager.requests.get") as get:
            m.add(self.url("belge.zip"), listed=True)
            import time
            time.sleep(0.3)
            get.assert_not_called()


class CaptureDialogTests(unittest.TestCase):
    def test_list_button_is_visible_for_blob_files_too(self):
        app = QApplication.instance() or QApplication([])
        from gui.components.capture_dialog import CaptureDialog
        dialog = CaptureDialog({"url": "https://pinterest.example/pin/1", "filename": "pin.jpg", "localPath": "x"}, Path("."))
        dialog.show()
        self.assertTrue(dialog.list_btn.isVisible())
        dialog.close()
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
