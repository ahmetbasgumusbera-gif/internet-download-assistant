"""
Arayüz duman testleri. Ekran açmadan (offscreen) pencereyi kurar, gerçek bir indirme yapar
ve temel akışların (ekleme, filtre, silme, kapanış) bozulmadığını doğrular.
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication

from core.database import Database
from core.manager import DownloadManager
from core.models import DownloadTask, TaskStatus
from gui import theme
from gui.bridge import ManagerBridge
from gui.components.download_item import DownloadItemWidget
from gui.main_window import MainWindow
from utils.config_manager import ConfigManager
from tests.test_http_engine import (
    FastHttpEngine, ServerConfig, _Handler, _Server, make_data,
)


def pump(app, condition=None, timeout=15.0):
    """Qt olay döngüsünü çalıştırır; koşul sağlanınca (ya da süre dolunca) döner."""
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if condition is not None and condition():
            return True
        time.sleep(0.01)
    return condition is None


class GuiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(theme.build_stylesheet())

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db_path = self.tmp / "h.db"
        self.config = ConfigManager(self.tmp / "config.json")
        self.config.set("download_dir", str(self.tmp / "dl"))
        self.config.set("connections_per_download", 2)

        self.data = make_data(400_000)
        self.cfg = ServerConfig(self.data)
        self.server = _Server(("127.0.0.1", 0), _Handler)
        self.server.cfg = self.cfg
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.window = None

    def tearDown(self):
        if self.window is not None:
            self.window.close()
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_window(self):
        self.manager = DownloadManager(self.config, Database(self.db_path), engines=[FastHttpEngine])
        self.bridge = ManagerBridge(self.manager)
        self.window = MainWindow(self.manager, self.config, self.bridge)
        self.window.show()
        return self.window

    def wait_all_completed(self, count):
        w = self.window
        return pump(self.app, lambda: len(w._statuses) == count and all(
            s == TaskStatus.COMPLETED for s in w._statuses.values()))

    def test_add_download_creates_card_and_completes(self):
        w = self.make_window()
        w.url_input.setText(f"{self.base}/belge.pdf")
        w._on_add()
        self.assertTrue(self.wait_all_completed(1))
        card = next(iter(w._cards.values()))
        self.assertEqual(card.progress.value(), 1000)
        self.assertEqual(w.url_input.text(), "")
        final = self.manager.tasks()[0].final_path
        self.assertEqual(final.read_bytes(), self.data)

    def test_invalid_input_shows_message(self):
        w = self.make_window()
        w.url_input.setText("bu bir link değil")
        w._on_add()
        self.assertTrue(w.message_label.isVisible())
        self.assertEqual(len(w._cards), 0)

    def test_multiple_urls_in_one_input(self):
        w = self.make_window()
        w.url_input.setText(f"{self.base}/a.zip   {self.base}/b.zip")
        w._on_add()
        self.assertTrue(self.wait_all_completed(2))
        self.assertEqual(len(w._cards), 2)

    def test_filters(self):
        w = self.make_window()
        w.url_input.setText(f"{self.base}/a.zip")
        w._on_add()
        self.assertTrue(self.wait_all_completed(1))
        card = next(iter(w._cards.values()))
        w.chip_active.click()
        self.assertTrue(card.isHidden())
        w.chip_done.click()
        self.assertFalse(card.isHidden())
        w.chip_all.click()
        self.assertFalse(card.isHidden())

    def test_remove_button_removes_card(self):
        w = self.make_window()
        w.url_input.setText(f"{self.base}/a.zip")
        w._on_add()
        self.assertTrue(self.wait_all_completed(1))
        card = next(iter(w._cards.values()))
        card.btn_remove.click()
        self.assertTrue(pump(self.app, lambda: len(w._cards) == 0))
        self.assertEqual(self.manager.tasks(), [])

    def test_restored_tasks_are_shown_as_paused(self):
        db = Database(self.db_path)
        db.save(DownloadTask(url=f"{self.base}/x.bin", save_dir=self.tmp / "dl",
                             file_name="x.bin", status=TaskStatus.DOWNLOADING, downloaded=100))
        db.close()
        w = self.make_window()
        self.assertEqual(len(w._cards), 1)
        card = next(iter(w._cards.values()))
        self.assertEqual(card.badge.text(), "Duraklatıldı")
        self.assertTrue(w.resume_all_btn.isEnabled())

    def test_field_count_follows_concurrency_setting(self):
        self.config.set("max_concurrent_downloads", 5)
        w = self.make_window()
        self.assertEqual(len(w.link_fields.fields), 5)
        self.config.set("max_concurrent_downloads", 2)
        w.link_fields.set_count(self.config.get("max_concurrent_downloads"))
        self.assertEqual(len(w.link_fields.fields), 2)

    def test_shrinking_keeps_typed_text(self):
        self.config.set("max_concurrent_downloads", 3)
        w = self.make_window()
        w.link_fields.fields[2].setText("http://ornek.com/a.zip")
        w.link_fields.set_count(2)
        texts = [f.text() for f in w.link_fields.fields]
        self.assertEqual(texts.count("http://ornek.com/a.zip"), 1, "yazı kaybolmamalı")

    def test_pasting_many_links_spreads_over_fields(self):
        self.config.set("max_concurrent_downloads", 3)
        w = self.make_window()
        w.link_fields.fields[0].setText("http://x.com/1.zip http://x.com/2.zip http://x.com/3.zip")
        self.assertEqual([f.text() for f in w.link_fields.fields],
                         ["http://x.com/1.zip", "http://x.com/2.zip", "http://x.com/3.zip"])

    def test_more_links_than_fields_are_still_all_added(self):
        self.config.set("max_concurrent_downloads", 2)
        self.config.set("download_mode", "sequential")
        w = self.make_window()
        urls = [f"{self.base}/f{i}.zip" for i in range(4)]
        w.link_fields.fields[0].setText(" ".join(urls))
        w._on_add()
        self.assertEqual(len(self.manager.tasks()), 4)

    def test_each_filled_field_becomes_a_download(self):
        self.config.set("max_concurrent_downloads", 3)
        w = self.make_window()
        for i, field in enumerate(w.link_fields.fields):
            field.setText(f"{self.base}/dosya{i}.zip")
        w._on_add()
        self.assertTrue(self.wait_all_completed(3))
        self.assertTrue(all(f.text() == "" for f in w.link_fields.fields))

    def test_invalid_field_stays_filled(self):
        w = self.make_window()
        w.link_fields.fields[0].setText(f"{self.base}/iyi.zip")
        w.link_fields.fields[1].setText("bozuk giris")
        w._on_add()
        self.assertEqual(w.link_fields.fields[0].text(), "")
        self.assertEqual(w.link_fields.fields[1].text(), "bozuk giris")

    def test_mode_chips_update_config(self):
        w = self.make_window()
        self.assertTrue(w.chip_parallel.isChecked())
        w.chip_sequential.click()
        self.assertEqual(self.config.get("download_mode"), "sequential")
        w.chip_parallel.click()
        self.assertEqual(self.config.get("download_mode"), "parallel")

    def test_settings_unlimited_chip(self):
        from gui.components.settings_dialog import SettingsDialog
        w = self.make_window()                   # üst pencere verilmeli: yoksa çıkışta Qt nesneleri yarışır
        self.config.set("speed_limit_kbps", 512)
        dlg = SettingsDialog(self.config, w)
        self.assertFalse(dlg.unlimited_chip.isChecked())
        self.assertTrue(dlg.speed_spin.isEnabled())
        self.assertEqual(dlg.speed_spin.value(), 512)

        dlg.unlimited_chip.setChecked(True)
        self.assertFalse(dlg.speed_spin.isEnabled())
        dlg._save()
        self.assertEqual(self.config.get("speed_limit_kbps"), 0)

        dlg2 = SettingsDialog(self.config, w)    # sınırsız olarak açılır
        self.assertTrue(dlg2.unlimited_chip.isChecked())
        dlg2.unlimited_chip.setChecked(False)    # tek tıkla sınırı geri aç
        self.assertTrue(dlg2.speed_spin.isEnabled())
        dlg2._save()
        self.assertGreater(self.config.get("speed_limit_kbps"), 0)
        dlg.deleteLater()
        dlg2.deleteLater()

    def test_unsupported_share_link_shows_clear_message_and_stays_in_field(self):
        w = self.make_window()
        link = "https://drive.google.com/drive/folders/ABC123"
        w.link_fields.fields[0].setText(link)
        w._on_add()
        self.assertTrue(w.message_label.isVisible())
        self.assertIn("klasör", w.message_label.text())
        self.assertEqual(w.link_fields.fields[0].text(), link)
        self.assertEqual(self.manager.tasks(), [])

    def test_unsupported_link_does_not_block_other_links(self):
        w = self.make_window()
        link = "https://drive.google.com/drive/folders/ABC123"
        w.link_fields.fields[0].setText(link)
        w.link_fields.fields[1].setText(f"{self.base}/tamam.zip")
        w._on_add()
        self.assertTrue(self.wait_all_completed(1))
        self.assertIn("klasör", w.message_label.text())
        self.assertEqual(w.link_fields.fields[0].text(), link)

    def test_clipboard_link_shows_suggestion_and_adds_to_field(self):
        w = self.make_window()
        w._consider_clipboard("https://example.com/dosya.zip")
        self.assertTrue(w.clip_bar.isVisible())
        self.assertIn("dosya.zip", w.clip_label._full)
        w.clip_add_btn.click()
        self.assertFalse(w.clip_bar.isVisible())
        self.assertEqual(w.link_fields.fields[0].text(), "https://example.com/dosya.zip")
        self.assertEqual(self.manager.tasks(), [], "öneri kendiliğinden indirmemeli")

    def test_clipboard_ignores_ordinary_pages_and_unsupported_links(self):
        w = self.make_window()
        for text in ("https://example.com/haber/yazi", "merhaba dünya", "",
                     "https://drive.google.com/drive/folders/ABC123", "https://example.com/sayfa.html"):
            with self.subTest(text=text):
                w._clip_last = "sıfırla"
                w._consider_clipboard(text)
                self.assertFalse(w.clip_bar.isVisible())

    def test_clipboard_recognises_youtube_and_share_links(self):
        w = self.make_window()
        for text in ("https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                     "https://drive.google.com/file/d/ABC123/view?usp=sharing",
                     "https://www.dropbox.com/s/abc123/dosya.zip?dl=0",
                     "https://github.com/user/repo/blob/main/a.py"):
            with self.subTest(text=text):
                w.clip_bar.hide()
                w._clip_last = ""
                w._consider_clipboard(text)
                self.assertTrue(w.clip_bar.isVisible())

    def test_dismissed_suggestion_is_not_reopened_by_repeated_notification(self):
        w = self.make_window()
        url = "https://example.com/dosya.zip"
        w._consider_clipboard(url)
        self.assertTrue(w.clip_bar.isVisible())
        w.clip_close_btn.click()
        w._consider_clipboard(url)                 # aynı kopyalamanın tekrar bildirimi
        self.assertFalse(w.clip_bar.isVisible())
        w._consider_clipboard("başka bir metin")
        w._consider_clipboard(url)                 # başka bir şey kopyalanıp tekrar kopyalandı
        self.assertTrue(w.clip_bar.isVisible())

    def test_clipboard_link_already_in_list_is_not_suggested(self):
        w = self.make_window()
        url = f"{self.base}/a.zip"
        self.manager.add(url)
        w._consider_clipboard(url)
        self.assertFalse(w.clip_bar.isVisible())

    def test_clipboard_setting_controls_watching(self):
        w = self.make_window()
        QApplication.clipboard().setText("https://example.com/dosya.zip")
        self.config.set("clipboard_watch", False)
        w._on_clipboard_changed()
        self.assertFalse(w.clip_bar.isVisible())
        self.config.set("clipboard_watch", True)
        w._on_clipboard_changed()
        self.assertTrue(w.clip_bar.isVisible())

    def test_dangerous_file_warning_only_on_completed_cards_when_enabled(self):
        def card_for(name, status, warn=True):
            task = DownloadTask(url=f"http://x/{name}", save_dir=self.tmp, file_name=name,
                                status=status, file_size=100, downloaded=100)
            card = DownloadItemWidget(task)
            self.addCleanup(card.deleteLater)            # üst penceresiz kartlar çıkışta Qt'yi çökertmesin
            card.set_warn_dangerous(warn)
            return card

        done = card_for("kurulum.exe", TaskStatus.COMPLETED)
        self.assertIn("⚠", done.info_label._full)
        self.assertEqual(done.info_label.property("state"), "warn")
        self.assertNotIn("⚠", card_for("kurulum.exe", TaskStatus.COMPLETED, warn=False).info_label._full)
        self.assertNotIn("⚠", card_for("belge.pdf", TaskStatus.COMPLETED).info_label._full)
        self.assertNotIn("⚠", card_for("kurulum.exe", TaskStatus.PAUSED).info_label._full)
        done.set_warn_dangerous(False)                 # ayar kapanınca uyarı kalkar
        self.assertNotIn("⚠", done.info_label._full)

    def test_settings_behavior_toggles_are_saved(self):
        from gui.components.settings_dialog import SettingsDialog
        w = self.make_window()
        dlg = SettingsDialog(self.config, w)
        for key in ("sort_into_categories", "warn_dangerous_files", "auto_resume_on_start", "clipboard_watch"):
            self.assertIn(key, dlg.toggles)
        self.assertEqual(dlg.toggles["warn_dangerous_files"].text(), "Açık")
        self.assertEqual(dlg.toggles["clipboard_watch"].text(), "Kapalı")
        dlg.toggles["clipboard_watch"].setChecked(True)
        dlg.toggles["sort_into_categories"].setChecked(True)
        dlg.toggles["warn_dangerous_files"].setChecked(False)
        self.assertEqual(dlg.toggles["clipboard_watch"].text(), "Açık")
        dlg._save()
        self.assertTrue(self.config.get("clipboard_watch"))
        self.assertTrue(self.config.get("sort_into_categories"))
        self.assertFalse(self.config.get("warn_dangerous_files"))
        dlg.deleteLater()

    def test_close_shuts_manager_down(self):
        w = self.make_window()
        w.close()
        with self.assertRaises(RuntimeError):
            self.manager.add(f"{self.base}/a.zip")
        self.window = None


if __name__ == "__main__":
    unittest.main()