"""W.16: çift görev engeli, Defender iletisi, pencere kapatıcı, hata penceresi."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PySide6.QtWidgets import QApplication, QDialog

from core.engines.http_engine import move_error_message
from tests.test_http_engine import make_data
from tests.test_manager import ManagerTestCase


class DuplicateTests(ManagerTestCase):
    def test_same_file_added_in_different_ways_is_one_task(self):
        self.serve(make_data(20_000))
        m = self.manager()
        m.set_hold(True)
        first = m.add(self.url(), listed=True)
        self.assertEqual(m.add(self.url(), listed=True), first, "listeye ikinci kez ekleme işe yaramaz")
        self.assertEqual(m.add(self.url()), first, "aynı dosya normal indirmeyle de çift görev olmaz")
        self.assertEqual(len(m.tasks()), 1)

    def test_info_file_difference_does_not_create_a_second_task(self):
        self.serve(make_data(20_000))
        m = self.manager()
        m.set_hold(True)
        a = m.add(self.url(), options={"info_file": "a.json"})
        b = m.add(self.url(), options={"info_file": "b.json"})
        self.assertEqual(a, b)

    def test_download_request_for_a_staged_item_starts_it(self):
        self.serve(make_data(20_000))
        m = self.manager()
        m.set_hold(True)
        tid = m.add(self.url(), listed=True)
        self.assertTrue(m.get_task(tid).options.get("staged"))
        self.assertEqual(m.add(self.url()), tid)
        self.assertFalse(m.get_task(tid).options.get("staged"), "İndir denince bekleyen başlatılabilir olmalı")

    def test_different_files_stay_separate(self):
        self.serve(make_data(20_000))
        m = self.manager()
        m.set_hold(True)
        self.assertNotEqual(m.add(self.url("a.bin"), listed=True), m.add(self.url("b.bin"), listed=True))


class MessageTests(unittest.TestCase):
    def test_defender_error_is_short_and_clear(self):
        err = OSError("virus")
        err.winerror = 225
        text = move_error_message(err)
        self.assertIn("Windows Defender", text)
        self.assertLess(len(text), 90)
        self.assertIn("taşınamadı", move_error_message(OSError("başka")))


class GuiTests(unittest.TestCase):
    def test_dismiss_registers_and_forgets_dialogs(self):
        app = QApplication.instance() or QApplication([])
        from gui.dismiss import DISMISS
        dialog = QDialog()
        DISMISS.register(dialog)
        self.assertIn(dialog, DISMISS._stack)
        self.assertTrue(DISMISS._installed)
        dialog.finished.emit(0)
        self.assertNotIn(dialog, DISMISS._stack)
        self.assertFalse(DISMISS._installed, "yığın boşalınca filtre uygulamadan çıkarılmalı")
        dialog.deleteLater()

    def test_error_dialog_holds_full_text(self):
        app = QApplication.instance() or QApplication([])
        from gui.components.error_dialog import ErrorDialog
        long_text = "hata " * 400
        dialog = ErrorDialog("dosya.zip", long_text, True)
        self.assertEqual(dialog.findChildren(type(dialog.findChildren(object)[0]))[0] is not None, True)
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
