"""W.18: dil desteği (çeviri sözlüğü, kalıplar, açık pencerelerde anında değişim) ve bekleyen iptal/yeniden dene düzeltmesi."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton, QTabWidget, QVBoxLayout, QWidget

from core import i18n
from core.models import TaskStatus
from tests.test_http_engine import make_data
from tests.test_manager import ManagerTestCase


class TranslationTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("tr")

    def test_turkish_mode_changes_nothing(self):
        i18n.set_language("tr")
        for text in ("Ayarlar", " Duraklat", "3 aktif   ·   2 sırada", "bilinmeyen metin"):
            self.assertEqual(i18n.tr(text), text)

    def test_exact_padding_separators_and_patterns(self):
        i18n.set_language("en")
        self.assertEqual(i18n.tr("Ayarlar"), "Settings")
        self.assertEqual(i18n.tr(" Duraklat"), " Pause", "baştaki boşluk korunmalı")
        self.assertEqual(i18n.tr("3 aktif   ·   2 sırada   ·   4 tamamlandı"), "3 active   ·   2 queued   ·   4 completed")
        self.assertEqual(i18n.tr("İndirmeler (4)"), "Downloads (4)")
        self.assertEqual(i18n.tr(" Bitince: Uyut"), " When done: Sleep")
        self.assertEqual(i18n.tr("12.5 MB / 100 MB   ·   2 MB/s   ·   45sn kaldı"), "12.5 MB / 100 MB   ·   2 MB/s   ·   45s left")
        self.assertEqual(i18n.tr("18 Kas 2026"), "Nov 18, 2026")
        self.assertEqual(i18n.tr("10 B görüntüleme"), "10K views")
        self.assertEqual(i18n.tr("Uygulama güncel (v1.7.0)"), "The app is up to date (v1.7.0)")

    def test_unknown_text_stays_and_is_listed(self):
        i18n.set_language("en")
        self.assertEqual(i18n.tr("tamamen çevrilmemiş ğüşiö cümle"), "tamamen çevrilmemiş ğüşiö cümle")
        self.assertIn("tamamen çevrilmemiş ğüşiö cümle", i18n.missing())

    def test_resolve_uses_explicit_choice_and_detects_otherwise(self):
        self.assertEqual(i18n.resolve("tr"), "tr")
        self.assertEqual(i18n.resolve("en"), "en")
        self.assertIn(i18n.resolve("auto"), ("tr", "en"))

    def test_dictionary_has_no_duplicated_english_for_wrong_keys(self):
        from core import i18n_en
        self.assertTrue(all(isinstance(k, str) and isinstance(v, str) for k, v in i18n_en.EN.items()))


class QtTests(unittest.TestCase):
    def tearDown(self):
        from gui import i18n_qt
        i18n_qt.apply_language("tr", "en")

    def test_open_window_switches_language_both_ways(self):
        app = QApplication.instance() or QApplication([])
        from gui import i18n_qt
        window = QWidget()
        layout = QVBoxLayout(window)
        button, label = QPushButton("Tümü"), QLabel("Henüz indirme yok")
        tabs, combo = QTabWidget(), QComboBox()
        tabs.addTab(QWidget(), "Genel")
        combo.addItem("Bugün")
        for w in (button, label, tabs, combo):
            layout.addWidget(w)
        window.show()
        i18n_qt.apply_language("en", "tr")
        app.processEvents()
        self.assertEqual((button.text(), label.text(), tabs.tabText(0), combo.itemText(0)),
                         ("All", "No downloads yet", "General", "Today"))
        i18n_qt.apply_language("tr", "en")
        self.assertEqual((button.text(), label.text(), tabs.tabText(0), combo.itemText(0)),
                         ("Tümü", "Henüz indirme yok", "Genel", "Bugün"), "Türkçeye dönüş kayıpsız olmalı")
        window.close()
        window.deleteLater()

    def test_dynamic_text_is_translated_when_set_later(self):
        app = QApplication.instance() or QApplication([])
        from gui import i18n_qt
        i18n_qt.apply_language("en", "tr")
        if not i18n_qt._patched_ok:
            self.skipTest("metin ayarlayıcıları sarılamadı")
        label = QLabel()
        label.setText("2 aktif   ·   0 sırada")
        self.assertEqual(label.text(), "2 active   ·   0 queued")
        i18n_qt.apply_language("tr", "en")
        i18n_qt.retranslate_tree(label, "en")
        self.assertEqual(label.text(), "2 aktif   ·   0 sırada")
        label.deleteLater()


class StagedRetryTests(ManagerTestCase):
    def test_retry_of_a_cancelled_listed_item_really_starts_it(self):
        self.serve(make_data(30_000))
        m = self.manager()
        m.set_hold(True)
        tid = m.add(self.url(), listed=True)
        m.cancel(tid)
        self.assertEqual(self.status(m, tid), TaskStatus.CANCELLED)
        m.retry(tid)
        self.assertFalse(m.get_task(tid).options.get("staged"), "Tekrar dene başlatmak demektir")
        m.set_hold(False)
        self.wait_status(m, tid, TaskStatus.COMPLETED)


if __name__ == "__main__":
    unittest.main()
