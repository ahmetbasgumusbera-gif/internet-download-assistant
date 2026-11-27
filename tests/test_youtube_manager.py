"""YouTube görevlerinin yönetici ve veritabanı ile entegrasyonu (gerçek yt-dlp, sahte site)."""
import shutil
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

from core.database import Database
from core.engines.ytdlp_engine import YtdlpEngine
from core.manager import DownloadManager
from core.models import TaskStatus
from core.youtube import analyze, build_args, default_selection, estimate_size
from tests.test_http_engine import FastHttpEngine
from tests.yt_fake_site import FakeYoutube, find_ffmpeg, yt_dlp_available
from utils import paths
from utils.config_manager import ConfigManager

READY = yt_dlp_available() and find_ffmpeg() is not None


def wait_for(cond, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@unittest.skipUnless(READY, "yt-dlp ve ffmpeg gerekli")
class YoutubeManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = FakeYoutube(seconds=8).__enter__()
        cls.engine_cls = type("TestYtdlpEngine", (YtdlpEngine,), {
            "EXTRA_ARGS": cls.site.extra_args, "FFMPEG_LOCATION": cls.site.ffmpeg})
        cls.info = analyze(cls.site.url(), extra_args=cls.site.extra_args)

    @classmethod
    def tearDownClass(cls):
        cls.site.__exit__(None, None, None)

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.root = Path(self._tmp)
        self.save_dir = self.root / "dl"
        self.db_path = self.root / "h.db"
        self.config = ConfigManager(self.root / "c.json")
        self.config.set("download_dir", str(self.save_dir))
        self.managers = []
        self.site.delay = 0.0

    def tearDown(self):
        for m in self.managers:
            m.shutdown(timeout=5)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def manager(self):
        m = DownloadManager(self.config, Database(self.db_path), engines=[self.engine_cls, FastHttpEngine])
        self.managers.append(m)
        return m

    def add(self, m, sel=None, url=None):
        sel = sel or default_selection(self.info)
        return m.add(
            url or self.site.url(), engine="ytdlp", title=self.info.title,
            thumbnail_url=self.info.thumbnail_url,
            options={"args": build_args(self.info, sel), "expected_size": estimate_size(self.info, sel),
                     "summary": "1080p · MP4"},
        )

    def status(self, m, tid):
        return m.get_task(tid).status

    def test_download_through_manager_and_persistence(self):
        m = self.manager()
        tid = self.add(m)
        self.assertTrue(wait_for(lambda: self.status(m, tid) == TaskStatus.COMPLETED), m.get_task(tid).error)
        task = m.get_task(tid)
        self.assertEqual(task.engine, "ytdlp")
        self.assertEqual(task.title, "Sahte Video ŞÖĞ")
        self.assertTrue(task.final_path.exists())
        m.shutdown()

        m2 = self.manager()                                  # yeniden açılış: bilgiler korunmuş olmalı
        restored = m2.get_task(tid)
        self.assertEqual(restored.title, "Sahte Video ŞÖĞ")
        self.assertEqual(restored.thumbnail_url, self.info.thumbnail_url)
        self.assertEqual(restored.options["summary"], "1080p · MP4")
        self.assertIn("-f", restored.options["args"])

    def test_same_url_with_different_options_are_separate_tasks(self):
        m = self.manager()
        self.site.delay = 0.05
        video = self.add(m)
        audio = self.add(m, sel=default_selection(self.info).__class__(mode="audio", audio="mp3"))
        same_again = self.add(m)
        self.assertNotEqual(video, audio)
        self.assertEqual(video, same_again, "aynı iş ikinci kez eklenmemeli")
        self.assertEqual(len(m.tasks()), 2)

    def test_both_variants_complete_side_by_side(self):
        self.config.set("max_concurrent_downloads", 2)
        m = self.manager()
        from core.youtube import Selection
        a = self.add(m)
        b = self.add(m, sel=Selection(mode="audio", audio="mp3", audio_kbps=128))
        self.assertTrue(wait_for(lambda: self.status(m, a) == TaskStatus.COMPLETED and
                                 self.status(m, b) == TaskStatus.COMPLETED, 60))
        names = sorted(p.suffix for p in self.save_dir.iterdir() if p.is_file())
        self.assertEqual(names, [".mp3", ".mp4"])

    def test_pause_is_instant_and_resume_completes(self):
        self.site.delay = 0.03
        m = self.manager()
        tid = self.add(m)
        self.assertTrue(wait_for(lambda: m.get_task(tid).downloaded > 100_000))
        start = time.monotonic()
        m.pause(tid)
        self.assertEqual(self.status(m, tid), TaskStatus.PAUSED)
        self.assertLess(time.monotonic() - start, 0.3)
        time.sleep(0.5)
        self.assertEqual(self.status(m, tid), TaskStatus.PAUSED)
        self.site.delay = 0
        m.resume(tid)
        self.assertTrue(wait_for(lambda: self.status(m, tid) == TaskStatus.COMPLETED), m.get_task(tid).error)

    def test_cancel_and_remove_clean_temp(self):
        self.site.delay = 0.05
        m = self.manager()
        tid = self.add(m)
        self.assertTrue(wait_for(lambda: m.get_task(tid).downloaded > 0))
        m.cancel(tid)
        self.assertEqual(self.status(m, tid), TaskStatus.CANCELLED)
        root = paths.temp_root(self.save_dir)
        self.assertTrue(wait_for(lambda: not root.exists() or not list(root.iterdir())))
        m.remove(tid)
        self.assertIsNone(m.get_task(tid))

    def test_error_is_reported(self):
        m = self.manager()
        tid = self.add(m, url=self.site.url("gone"))
        self.assertTrue(wait_for(lambda: self.status(m, tid) == TaskStatus.ERROR))
        self.assertIn("kullanılamıyor", m.get_task(tid).error)


class DatabaseMigrationTests(unittest.TestCase):
    def test_old_database_without_new_columns_is_upgraded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "old.db"
            conn = sqlite3.connect(path)
            conn.execute("""CREATE TABLE downloads (
                id TEXT PRIMARY KEY, url TEXT NOT NULL, save_dir TEXT NOT NULL,
                file_name TEXT NOT NULL DEFAULT '', file_size INTEGER, status TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '', downloaded INTEGER NOT NULL DEFAULT 0, final_path TEXT,
                engine TEXT NOT NULL DEFAULT '', headers TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL, finished_at TEXT)""")
            conn.execute("INSERT INTO downloads (id,url,save_dir,file_name,status,created_at) VALUES (?,?,?,?,?,?)",
                         ("eski1", "http://x/a.zip", str(Path(tmp) / "dl"), "a.zip", "completed",
                          datetime.now().isoformat()))
            conn.commit()
            conn.close()

            db = Database(path)
            tasks = db.load_all()
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0].file_name, "a.zip")
            self.assertEqual(tasks[0].options, {})
            self.assertEqual(tasks[0].title, "")

            tasks[0].title = "Yeni"
            tasks[0].options = {"a": 1}
            db.save(tasks[0])
            again = db.get("eski1")
            self.assertEqual((again.title, again.options), ("Yeni", {"a": 1}))
            db.close()


if __name__ == "__main__":
    unittest.main()
