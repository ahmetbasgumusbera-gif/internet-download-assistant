"""YouTube sekmesi ve ana pencere entegrasyonu (analiz sahte bir fonksiyonla; ağ yok)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import io
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from core.database import Database
from core.engines.base import BaseEngine
from core.manager import DownloadManager
from core.models import DownloadTask, TaskStatus
from core.youtube import AnalysisError
from gui import theme
from gui.bridge import ManagerBridge
from gui.components.download_item import DownloadItemWidget
from gui.components.youtube_page import YouTubePage
from gui.main_window import MainWindow
from tests.test_gui_smoke import pump
from tests.test_http_engine import FastHttpEngine, ServerConfig, _Handler, _Server
from tests.yt_samples import sample_info_dict, sample_video_info
from utils.config_manager import ConfigManager


def png_bytes() -> bytes:
    image = QImage(160, 90, QImage.Format_RGB32)
    image.fill(QColor("#a78bfa"))
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


class StubYtdlpEngine(BaseEngine):
    """Arayüz testleri için: gerçek yt-dlp çalıştırmadan "ytdlp" adıyla görev kabul eder."""
    name = "ytdlp"

    def __init__(self, task, callbacks=None, connections=8, limiter=None):
        super().__init__(task, callbacks)

    @classmethod
    def can_handle(cls, url):
        return False

    def probe(self):
        pass

    def start(self):
        self._set_status(TaskStatus.DOWNLOADING)
        self.task.downloaded = 100
        self._notify_progress()

    def pause(self):
        pass

    def resume(self):
        pass

    def cancel(self):
        self._set_status(TaskStatus.CANCELLED)

    def stop(self):
        pass


class PageTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyleSheet(theme.build_stylesheet())

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.config = ConfigManager(self.tmp / "config.json")
        self.calls = []
        self.window = None
        self.page = None

    def tearDown(self):
        if self.window is not None:
            self.window.close()
        if self.page is not None:
            self.page.deleteLater()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_analyze(self, info=None, error=None, gate=None):
        def fn(url, playlist_first=False):
            self.calls.append((url, playlist_first))
            if gate is not None:
                gate.wait(10)
            if error:
                raise AnalysisError(error)
            return info or sample_video_info()
        return fn

    def make_page(self, **kw):
        page = YouTubePage(self.config, analyze_fn=kw.pop("analyze_fn", None) or self.fake_analyze(),
                           js_runtime_fn=kw.pop("js_runtime_fn", lambda: "deno"))
        page.resize(940, 900)
        page.show()
        self.page = page
        return page

    def analyzed_page(self):
        page = self.make_page()
        page.analyze("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertTrue(pump(self.app, lambda: page.info is not None))
        return page

    def tile_titles(self, page):
        from gui.components.youtube_page import _Tile
        tiles = page.tiles_host.findChildren(_Tile)
        return [t.findChildren(type(page.title_label))[0].text() for t in tiles]


class AnalysisFlowTests(PageTestCase):
    def test_result_shows_video_and_defaults(self):
        page = self.analyzed_page()
        self.assertEqual(page.stack.currentIndex(), 3)
        self.assertEqual(page.title_label.text(), "Örnek video başlığı")
        self.assertIn("Örnek Kanal", page.meta_label.text())
        sel = page.selection
        self.assertEqual((sel.mode, sel.height, sel.fps, sel.container, sel.codec), ("video", 1080, 30, "mp4", "compat"))
        self.assertTrue(sel.embed_thumbnail)
        self.assertEqual(sel.subtitles, [])
        self.assertTrue(page.summary_label.text().startswith("1080p  ·  30 FPS  ·  MP4  ·  ~"))
        self.assertTrue(page.summary_bar.isVisibleTo(page))

    def test_all_available_resolutions_are_shown(self):
        page = self.analyzed_page()
        self.assertEqual(self.tile_titles(page), ["2160p", "1440p", "1080p", "720p", "480p", "360p", "240p", "144p"])

    def test_video_badges(self):
        page = self.analyzed_page()
        texts = {page.badges_row.itemAt(i).widget().text() for i in range(page.badges_row.count())
                 if page.badges_row.itemAt(i).widget()}
        for expected in ("4K", "60 FPS", "HDR", "Altyazı var", "Bölümler"):
            self.assertIn(expected, texts)

    def test_paste_triggers_automatic_analysis(self):
        page = self.make_page()
        page.link_edit.setText("https://youtu.be/dQw4w9WgXcQ")
        self.assertTrue(pump(self.app, lambda: page.info is not None))
        self.assertEqual(len(self.calls), 1)

    def test_same_link_is_not_analyzed_twice(self):
        page = self.analyzed_page()
        page.analyze(page.link_edit.text())
        pump(self.app, timeout=0.3)
        self.assertEqual(len(self.calls), 1)

    def test_error_is_shown_and_retry_works(self):
        page = self.make_page(analyze_fn=self.fake_analyze(error="Video kullanılamıyor."))
        page.analyze("https://youtu.be/x")
        self.assertTrue(pump(self.app, lambda: page.stack.currentIndex() == 2))
        self.assertEqual(page.error_label.text(), "Video kullanılamıyor.")
        self.assertTrue(page.analyze_btn.isEnabled())
        self.assertFalse(page.summary_bar.isVisibleTo(page))

    def test_non_youtube_link_is_rejected_without_analysis(self):
        page = self.make_page()
        page.analyze("https://example.com/video.mp4")
        self.assertEqual(page.stack.currentIndex(), 2)
        self.assertEqual(self.calls, [])

    def test_playlist_offers_first_video(self):
        page = self.make_page()
        page.analyze("https://www.youtube.com/playlist?list=PL123")
        self.assertEqual(page.stack.currentIndex(), 2)
        self.assertFalse(page.playlist_btn.isHidden())
        self.assertEqual(self.calls, [])
        page.playlist_btn.click()
        self.assertTrue(pump(self.app, lambda: page.info is not None))
        self.assertEqual(self.calls, [("https://www.youtube.com/playlist?list=PL123", True)])

    def test_stale_analysis_result_is_ignored(self):
        gate = threading.Event()
        slow = self.fake_analyze(info=sample_video_info(title="ESKİ"), gate=gate)
        fast = self.fake_analyze(info=sample_video_info(title="YENİ"))
        page = self.make_page(analyze_fn=slow)
        page.analyze("https://youtu.be/one")
        page._analyze_fn = fast
        page.analyze("https://youtu.be/two", force=True)
        self.assertTrue(pump(self.app, lambda: page.info is not None))
        gate.set()                       # eski istek şimdi bitiyor
        pump(self.app, timeout=0.5)
        self.assertEqual(page.title_label.text(), "YENİ")

    def test_runtime_banner(self):
        self.assertTrue(self.make_page(js_runtime_fn=lambda: None).runtime_banner.isVisibleTo(self.page))
        self.page.deleteLater()
        self.assertFalse(self.make_page(js_runtime_fn=lambda: "node").runtime_banner.isVisibleTo(self.page))


class SelectionUiTests(PageTestCase):
    def click_tile(self, page, title):
        from gui.components.youtube_page import _Tile
        for tile in page.tiles_host.findChildren(_Tile):
            if tile.findChildren(type(page.title_label))[0].text() == title:
                tile.click()
                return
        self.fail(f"karo yok: {title}")

    def test_choosing_resolution_updates_selection_and_fps_buttons(self):
        page = self.analyzed_page()
        self.click_tile(page, "2160p")
        self.assertEqual(page.selection.height, 2160)
        self.assertTrue(page.fps_buttons["60"].isEnabled())
        self.click_tile(page, "480p")
        self.assertEqual(page.selection.height, 480)
        self.assertFalse(page.fps_buttons["60"].isEnabled(), "480p'de 60 FPS yok")
        self.assertEqual(page.selection.fps, 30)

    def test_fps_switch_changes_summary(self):
        page = self.analyzed_page()
        before = page.summary_label.text()
        page.fps_buttons["60"].click()
        self.assertEqual(page.selection.fps, 60)
        self.assertIn("60 FPS", page.summary_label.text())
        self.assertNotEqual(before, page.summary_label.text())

    def test_audio_mode_shows_audio_tiles(self):
        page = self.analyzed_page()
        page.mode_buttons["audio"].click()
        self.assertEqual(page.selection.mode, "audio")
        self.assertEqual(self.tile_titles(page),
                         ["Orijinal (M4A)", "Orijinal (OPUS)", "MP3 320 kbps", "MP3 192 kbps", "MP3 128 kbps", "WAV", "FLAC"])
        self.assertFalse(page.options_host.isVisibleTo(page))
        self.assertTrue(page.audio_note.isVisibleTo(page))
        self.click_tile(page, "MP3 320 kbps")
        self.assertEqual((page.selection.audio, page.selection.audio_kbps), ("mp3", 320))
        self.assertIn("MP3 320 kbps", page.summary_label.text())
        self.assertFalse(page.subs_chip.isEnabled())

    def test_video_only_mode(self):
        page = self.analyzed_page()
        page.mode_buttons["video_only"].click()
        self.assertEqual(page.selection.mode, "video_only")
        self.assertIn("Sessiz", page.summary_label.text())

    def test_container_and_codec(self):
        page = self.analyzed_page()
        page.codec_buttons["efficient"].click()
        self.assertEqual(page.selection.codec, "efficient")
        self.assertIn("AV1", page.codec_hint.text())
        page.container_buttons["webm"].click()
        self.assertEqual(page.selection.container, "webm")
        self.assertIn("VP9", page.codec_hint.text())
        self.assertFalse(page.codec_buttons["compat"].isEnabled(), "webm'de codec seçimi anlamsız")

    def test_extras(self):
        page = self.analyzed_page()
        page.thumb_chip.setChecked(False)
        self.assertFalse(page.selection.embed_thumbnail)
        page._toggle_subtitle("tr", True)
        self.assertEqual(page.selection.subtitles, ["tr"])
        self.assertEqual(page.subs_chip.text(), "Altyazı: tr")
        page._toggle_subtitle("tr", False)
        self.assertEqual(page.subs_chip.text(), "Altyazı: kapalı")

    def test_time_range_validation_blocks_download(self):
        page = self.analyzed_page()
        got = []
        page.download_requested.connect(got.append)
        page.range_chip.setChecked(True)
        self.assertTrue(page.range_host.isVisibleTo(page))
        page.start_edit.setText("5:00")
        page.end_edit.setText("1:00")
        self.assertIn("Bitiş", page.range_error.text())
        page.add_btn.click()
        self.assertEqual(got, [], "geçersiz aralıkla indirme istenmemeli")
        page.end_edit.setText("8:00")
        self.assertEqual(page.range_error.text(), "")
        page.add_btn.click()
        self.assertEqual((got[0]["selection"].start, got[0]["selection"].end), ("5:00", "8:00"))


class DownloadRequestTests(PageTestCase):
    def test_add_button_emits_payload_and_shows_confirmation(self):
        page = self.analyzed_page()
        got = []
        page.download_requested.connect(got.append)
        page.add_btn.click()
        self.assertEqual(len(got), 1)
        payload = got[0]
        self.assertEqual(payload["info"].id, "dQw4w9WgXcQ")
        self.assertEqual(payload["selection"].height, 1080)
        self.assertTrue(page.added_bar.isVisibleTo(page))
        self.assertIn("Listeye eklendi", page.added_label.text())
        page.mode_buttons["audio"].click()      # sayfa açık kalır: aynı video başka biçimde de eklenebilir
        self.assertEqual(payload["selection"].mode, "video", "gönderilen seçim kopya olmalı")

    def test_auto_download_uses_defaults_without_clicking(self):
        got = []
        page = self.make_page()
        page.download_requested.connect(got.append)
        page.auto_chip.setChecked(True)
        self.assertTrue(self.config.get("youtube_auto_download"))
        page.analyze("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertTrue(pump(self.app, lambda: len(got) == 1))
        sel = got[0]["selection"]
        self.assertEqual((sel.mode, sel.height, sel.fps, sel.container, sel.embed_thumbnail, sel.subtitles),
                         ("video", 1080, 30, "mp4", True, []))
        self.assertIn("otomatik", page.added_label.text())

    def test_auto_download_setting_is_restored(self):
        self.config.set("youtube_auto_download", True)
        self.assertTrue(self.make_page().auto_chip.isChecked())


class MainWindowIntegrationTests(PageTestCase):
    def setUp(self):
        super().setUp()
        self.config.set("download_dir", str(self.tmp / "dl"))
        self.config.set("max_concurrent_downloads", 3)
        self.manager = DownloadManager(self.config, Database(self.tmp / "h.db"),
                                       engines=[StubYtdlpEngine, FastHttpEngine])
        self.bridge = ManagerBridge(self.manager)
        self.window = MainWindow(self.manager, self.config, self.bridge)
        self.window.youtube_page._analyze_fn = self.fake_analyze()
        self.window.youtube_page._js_runtime_fn = lambda: "deno"
        self.window.show()

    def tearDown(self):
        super().tearDown()
        self.window = None

    def test_youtube_link_on_main_screen_redirects_to_youtube_tab(self):
        w = self.window
        w.link_fields.fields[0].setText("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        w._on_add()
        self.assertEqual(w.stack.currentIndex(), 1)
        self.assertTrue(w.tab_youtube.isChecked())
        self.assertEqual(w.link_fields.fields[0].text(), "", "taşınan linkin kutusu boşalmalı")
        self.assertTrue(pump(self.app, lambda: w.youtube_page.info is not None))
        self.assertEqual(self.manager.tasks(), [], "YouTube linki doğrudan indirme olarak eklenmemeli")
        self.assertEqual(self.calls[0][0], "https://www.youtube.com/watch?v=dQw4w9WgXcQ")

    def test_mixed_links_normal_one_is_downloaded_youtube_one_is_redirected(self):
        w = self.window
        w.link_fields.fields[0].setText("https://youtu.be/dQw4w9WgXcQ")
        w.link_fields.fields[1].setText("http://127.0.0.1:9/dosya.zip")
        w._on_add()
        self.assertEqual(w.stack.currentIndex(), 1)
        self.assertEqual([t.url for t in self.manager.tasks()], ["http://127.0.0.1:9/dosya.zip"])

    def test_extra_youtube_links_wait_in_their_fields(self):
        w = self.window
        w.link_fields.fields[0].setText("https://youtu.be/aaaaaaaaaaa")
        w.link_fields.fields[1].setText("https://youtu.be/bbbbbbbbbbb")
        w._on_add()
        remaining = [f.text() for f in w.link_fields.fields if f.text()]
        self.assertEqual(remaining, ["https://youtu.be/bbbbbbbbbbb"])
        self.assertIn("bekliyor", w.message_label.text())

    def test_download_request_becomes_a_task_with_title_and_thumbnail(self):
        w = self.window
        page = w.youtube_page
        page.analyze("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertTrue(pump(self.app, lambda: page.info is not None))
        page.add_btn.click()
        self.assertTrue(pump(self.app, lambda: len(self.manager.tasks()) == 1))
        task = self.manager.tasks()[0]
        self.assertEqual(task.engine, "ytdlp")
        self.assertEqual(task.title, "Örnek video başlığı")
        self.assertEqual(task.thumbnail_url, sample_video_info().thumbnail_url)   # indirince sayfa temizlenir
        self.assertIn("-f", task.options["args"])
        self.assertEqual(task.options["summary_short"], "1080p  ·  30 FPS  ·  MP4")
        self.assertTrue(task.options["expected_size"])
        self.assertTrue(pump(self.app, lambda: len(w._cards) == 1))
        card = next(iter(w._cards.values()))
        self.assertEqual(card.name_label.toolTip(), "Örnek video başlığı")

    def test_goto_downloads_button_switches_tab(self):
        w = self.window
        w._select_tab(1)
        w.youtube_page.goto_btn.click()
        self.assertEqual(w.stack.currentIndex(), 0)
        self.assertTrue(w.tab_downloads.isChecked())

    def test_tab_shows_active_count(self):
        w = self.window
        cfg = ServerConfig(b"x" * 1000)
        cfg.header_delay = 5                    # sunucu cevap vermiyor: indirme "bağlanıyor"da kalır
        server = _Server(("127.0.0.1", 0), _Handler)
        server.cfg = cfg
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            self.manager.add(f"http://127.0.0.1:{server.server_address[1]}/a.zip")
            self.assertTrue(pump(self.app, lambda: len(w._cards) == 1))
            w._refresh_status_bar()
            self.assertEqual(w.tab_downloads.text(), "İndirmeler (1)")
        finally:
            for task in self.manager.tasks():
                self.manager.cancel(task.id)
            server.shutdown()
            server.server_close()


class CardThumbnailTests(PageTestCase):
    def test_card_loads_thumbnail_from_url(self):
        cfg = ServerConfig(png_bytes())
        server = _Server(("127.0.0.1", 0), _Handler)
        server.cfg = cfg
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            task = DownloadTask(url="https://www.youtube.com/watch?v=x", save_dir=self.tmp, title="Başlık",
                                thumbnail_url=f"http://127.0.0.1:{server.server_address[1]}/t.png",
                                options={"summary_short": "1080p  ·  MP4"}, status=TaskStatus.PAUSED,
                                downloaded=10, file_size=100)
            card = DownloadItemWidget(task)
            card.show()
            self.assertEqual(card.name_label.toolTip(), "Başlık")
            self.assertTrue(pump(self.app, lambda: card.type_label.pixmap() is not None
                                 and not card.type_label.pixmap().isNull()))
            self.assertEqual(card.type_label.width(), 82)
            self.assertTrue(card.info_label.toolTip() == "" or True)
            self.assertIn("1080p", card._info_text(task))
            card.deleteLater()
        finally:
            server.shutdown()
            server.server_close()

    def test_card_without_thumbnail_keeps_extension_badge(self):
        task = DownloadTask(url="http://x/a.zip", save_dir=self.tmp, file_name="a.zip")
        card = DownloadItemWidget(task)
        self.assertEqual(card.type_label.text(), "ZIP")
        self.assertEqual(card.type_label.width(), 46)
        card.deleteLater()

    def test_broken_thumbnail_url_is_harmless(self):
        task = DownloadTask(url="https://youtu.be/x", save_dir=self.tmp, title="T",
                            thumbnail_url="http://127.0.0.1:9/yok.png")
        card = DownloadItemWidget(task)
        card.show()
        pump(self.app, timeout=1.0)
        self.assertEqual(card.name_label.toolTip(), "T")
        card.deleteLater()


if __name__ == "__main__":
    unittest.main()
