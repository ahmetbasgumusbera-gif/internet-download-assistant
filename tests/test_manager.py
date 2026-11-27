"""
DownloadManager ve Database testleri. İnternet gerekmez (yerel test sunucusu kullanılır).
"""
import shutil
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from core import resolver
from core.database import Database
from core.manager import DownloadManager, ManagerListener
from core.models import DownloadTask, TaskStatus
from utils import paths
from utils.config_manager import ConfigManager
from utils.helpers import reserve_unique_path
from tests.test_http_engine import (
    FastHttpEngine, ServerConfig, _Handler, _Server, make_data, wait_for,
)


class CountingEngine(FastHttpEngine):
    """Aynı anda kaç motorun çalıştığını ölçer."""
    lock = threading.Lock()
    running = 0
    peak = 0

    def start(self):
        cls = CountingEngine
        with cls.lock:
            cls.running += 1
            cls.peak = max(cls.peak, cls.running)
        try:
            super().start()
        finally:
            with cls.lock:
                cls.running -= 1


class ManagerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.root = Path(self._tmp)
        self.save_dir = self.root / "dl"
        self.db_path = self.root / "history.db"
        self.config = ConfigManager(self.root / "config.json")
        self.config.set("download_dir", str(self.save_dir))
        self.config.set("connections_per_download", 2)
        self.server = None
        self.managers = []
        CountingEngine.running = 0
        CountingEngine.peak = 0

    def tearDown(self):
        for m in self.managers:
            m.shutdown(timeout=5)
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ---- yardımcılar
    def serve(self, data: bytes) -> ServerConfig:
        self.cfg = ServerConfig(data)
        self.server = _Server(("127.0.0.1", 0), _Handler)
        self.server.cfg = self.cfg
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        return self.cfg

    def url(self, name="dosya.bin"):
        return f"{self.base}/{name}"

    def manager(self, engine=FastHttpEngine) -> DownloadManager:
        m = DownloadManager(self.config, Database(self.db_path), engines=[engine])
        self.managers.append(m)
        return m

    def status(self, m, task_id):
        return m.get_task(task_id).status

    def db_get(self, task_id):
        """Veritabanından tek kayıt okur ve bağlantıyı hemen kapatır (kaynak sızıntısı olmasın)."""
        db = Database(self.db_path)
        try:
            return db.get(task_id)
        finally:
            db.close()

    def wait_status(self, m, task_id, status, timeout=20):
        ok = wait_for(lambda: self.status(m, task_id) == status, timeout)
        self.assertTrue(ok, f"{status} bekleniyordu, şu an: {self.status(m, task_id)} "
                            f"({m.get_task(task_id).error})")

    def temp_dirs(self):
        root = paths.temp_root(self.save_dir)
        return list(root.iterdir()) if root.exists() else []


class QueueTests(ManagerTestCase):
    def test_add_and_complete(self):
        data = make_data(300_000)
        self.serve(data)
        m = self.manager()
        tid = m.add(self.url())
        self.wait_status(m, tid, TaskStatus.COMPLETED)

        task = m.get_task(tid)
        self.assertEqual(task.final_path.read_bytes(), data)
        self.assertIsNotNone(task.finished_at)
        # Veritabanı yazımı durum atamasından birkaç ms sonra olur
        self.assertTrue(wait_for(
            lambda: self.db_get(tid).status == TaskStatus.COMPLETED))

    def test_concurrency_limit_and_same_file_names(self):
        data = make_data(600_000)
        cfg = self.serve(data)
        cfg.delay = 0.02
        self.config.set("max_concurrent_downloads", 2)
        m = self.manager(CountingEngine)
        # Aynı dosya adı (dosya.bin) ile 5 farklı indirme: ad çakışması olmamalı
        ids = [m.add(self.url(f"dosya.bin?i={i}")) for i in range(5)]
        for tid in ids:
            self.wait_status(m, tid, TaskStatus.COMPLETED, timeout=40)

        self.assertLessEqual(CountingEngine.peak, 2)
        self.assertGreaterEqual(CountingEngine.peak, 2, "iki indirme paralel çalışmalıydı")
        files = sorted(p.name for p in self.save_dir.iterdir() if p.is_file())
        self.assertEqual(len(files), 5, files)
        for p in self.save_dir.iterdir():
            if p.is_file():
                self.assertEqual(p.read_bytes(), data)

    def test_pause_frees_slot_then_resume(self):
        data = make_data(1_500_000)
        cfg = self.serve(data)
        cfg.delay = 0.02
        self.config.set("max_concurrent_downloads", 1)
        m = self.manager()
        a = m.add(self.url("a.bin"))
        b = m.add(self.url("b.bin"))
        self.assertTrue(wait_for(lambda: m.get_task(a).downloaded > 200_000))
        self.assertEqual(self.status(m, b), TaskStatus.QUEUED)

        m.pause(a)
        self.wait_status(m, a, TaskStatus.PAUSED)
        # Yuva boşaldı: B başlamalı
        self.assertTrue(wait_for(lambda: self.status(m, b) in (
            TaskStatus.PROBING, TaskStatus.DOWNLOADING, TaskStatus.MERGING, TaskStatus.COMPLETED)))

        m.resume(a)
        self.assertIn(self.status(m, a), (TaskStatus.QUEUED, TaskStatus.PROBING, TaskStatus.DOWNLOADING))
        self.wait_status(m, b, TaskStatus.COMPLETED, timeout=30)
        self.wait_status(m, a, TaskStatus.COMPLETED, timeout=30)
        self.assertEqual(m.get_task(a).final_path.read_bytes(), data)
        self.assertEqual(m.get_task(b).final_path.read_bytes(), data)

    def test_pause_queued_task(self):
        cfg = self.serve(make_data(1_000_000))
        cfg.delay = 0.02
        self.config.set("max_concurrent_downloads", 1)
        m = self.manager()
        a = m.add(self.url("a.bin"))
        b = m.add(self.url("b.bin"))
        m.pause(b)
        self.assertEqual(self.status(m, b), TaskStatus.PAUSED)
        m.cancel(a)
        self.wait_status(m, a, TaskStatus.CANCELLED)
        time.sleep(0.3)
        self.assertEqual(self.status(m, b), TaskStatus.PAUSED, "duraklatılan kendiliğinden başlamamalı")

    def test_duplicate_url_returns_same_task(self):
        cfg = self.serve(make_data(1_000_000))
        cfg.delay = 0.02
        m = self.manager()
        a = m.add(self.url())
        b = m.add(self.url())
        self.assertEqual(a, b)
        self.assertEqual(len(m.tasks()), 1)

    def test_unsupported_scheme(self):
        m = self.manager()
        with self.assertRaises(ValueError):
            m.add("ftp://example.com/a.zip")
        with self.assertRaises(ValueError):
            m.add("bu bir link değil")


class LifecycleTests(ManagerTestCase):
    def slow(self, size=2_000_000):
        data = make_data(size)
        cfg = self.serve(data)
        cfg.delay = 0.02
        return data, cfg

    def test_cancel_running_and_queued_clean_temp(self):
        self.slow()
        self.config.set("max_concurrent_downloads", 1)
        m = self.manager()
        a = m.add(self.url("a.bin"))
        b = m.add(self.url("b.bin"))
        self.assertTrue(wait_for(lambda: m.get_task(a).downloaded > 200_000))
        m.cancel(b)
        self.assertEqual(self.status(m, b), TaskStatus.CANCELLED)
        m.cancel(a)
        self.wait_status(m, a, TaskStatus.CANCELLED)
        # Ekran anında "iptal" olur; geçici dosyalar motor kapanınca (ms içinde) silinir
        self.assertTrue(wait_for(lambda: self.temp_dirs() == []))

    def test_retry_after_error_resumes(self):
        data, cfg = self.slow(800_000)
        cfg.lie_after_probe = True
        cfg.delay = 0
        m = self.manager()
        tid = m.add(self.url())
        self.wait_status(m, tid, TaskStatus.ERROR)
        self.assertTrue(m.get_task(tid).error)
        self.assertTrue(self.temp_dirs(), "hata sonrası temp korunmalı")

        cfg.lie_after_probe = False
        m.retry(tid)
        self.wait_status(m, tid, TaskStatus.COMPLETED)
        self.assertEqual(m.get_task(tid).final_path.read_bytes(), data)
        self.assertEqual(m.get_task(tid).error, "")

    def test_retry_cancelled_starts_over(self):
        data, cfg = self.slow()
        m = self.manager()
        tid = m.add(self.url())
        self.assertTrue(wait_for(lambda: m.get_task(tid).downloaded > 200_000))
        m.cancel(tid)
        self.wait_status(m, tid, TaskStatus.CANCELLED)
        cfg.delay = 0
        m.retry(tid)
        self.wait_status(m, tid, TaskStatus.COMPLETED)
        self.assertEqual(m.get_task(tid).final_path.read_bytes(), data)

    def test_remove_completed_keeps_file(self):
        data = make_data(100_000)
        self.serve(data)
        m = self.manager()
        tid = m.add(self.url())
        self.wait_status(m, tid, TaskStatus.COMPLETED)
        final = m.get_task(tid).final_path
        m.remove(tid)  # motor thread'i hâlâ kapanıyorsa silme birkaç ms sonra tamamlanır
        self.assertTrue(wait_for(lambda: m.get_task(tid) is None))
        self.assertTrue(final.exists(), "dosya diskte kalmalı")
        self.assertTrue(wait_for(lambda: self.db_get(tid) is None))

    def test_remove_running_cancels_and_deletes_record(self):
        self.slow()
        m = self.manager()
        tid = m.add(self.url())
        self.assertTrue(wait_for(lambda: m.get_task(tid).downloaded > 200_000))
        m.remove(tid)
        self.assertTrue(wait_for(lambda: m.get_task(tid) is None))
        self.assertTrue(wait_for(lambda: self.temp_dirs() == []))
        self.assertIsNone(self.db_get(tid))

    def test_listener_events(self):
        self.serve(make_data(100_000))
        added, updated, removed = [], [], []
        m = self.manager()
        m.add_listener(ManagerListener(
            on_task_added=lambda t: added.append(t.id),
            on_task_updated=lambda t: updated.append(t.status),
            on_task_removed=lambda i: removed.append(i),
        ))
        tid = m.add(self.url())
        self.wait_status(m, tid, TaskStatus.COMPLETED)
        # Durum atanır atanmaz dinleyiciye bildirim biraz sonra gelir; bekle
        self.assertTrue(wait_for(lambda: updated and updated[-1] == TaskStatus.COMPLETED))
        m.remove(tid)
        self.assertTrue(wait_for(lambda: removed == [tid]))
        self.assertEqual(added, [tid])
        self.assertIn(TaskStatus.DOWNLOADING, updated)

class ResponsivenessTests(ManagerTestCase):
    """Kullanıcı komutları ekrana ANINDA yansımalı; eski motor ekranı geri bozmamalı."""

    def test_pause_is_instant_even_if_server_stalls_and_next_starts_fast(self):
        data = make_data(3_000_000)
        cfg = self.serve(data)
        cfg.delay = 0.01
        self.config.set("max_concurrent_downloads", 1)
        m = self.manager()
        a = m.add(self.url("a.bin"))
        b = m.add(self.url("b.bin"))
        self.assertTrue(wait_for(lambda: m.get_task(a).downloaded > 100_000))
        cfg.delay = 30                      # sunucu takıldı
        time.sleep(0.4)

        start = time.monotonic()
        m.pause(a)
        self.assertEqual(self.status(m, a), TaskStatus.PAUSED, "ekran hemen güncellenmeli")
        self.assertLess(time.monotonic() - start, 0.2)
        # Yuva kısa sürede boşalıp sıradaki başlamalı (eskiden 15-30 sn beklerdi)
        self.assertTrue(wait_for(lambda: self.status(m, b) in (
            TaskStatus.PROBING, TaskStatus.DOWNLOADING), timeout=3))
        self.assertLess(time.monotonic() - start, 3.0)

    def test_status_never_reverts_after_pause(self):
        """Duraklat'a basıldıktan sonra ekrana hiçbir 'iniyor' olayı sızmamalı."""
        cfg = self.serve(make_data(4_000_000))
        cfg.delay = 0.02
        events = []
        m = self.manager()
        m.add_listener(ManagerListener(on_task_updated=lambda t: events.append(t.status)))
        tid = m.add(self.url())
        self.assertTrue(wait_for(lambda: m.get_task(tid).downloaded > 300_000))
        mark = len(events)
        m.pause(tid)
        time.sleep(1.0)
        after = events[mark:]
        self.assertIn(TaskStatus.PAUSED, after)
        first_paused = after.index(TaskStatus.PAUSED)
        self.assertTrue(all(s == TaskStatus.PAUSED for s in after[first_paused:]),
                        f"duraklatma sonrası sızan durumlar: {after}")
        self.assertEqual(self.status(m, tid), TaskStatus.PAUSED)

    def test_pause_resume_storm_keeps_limit_and_consistency(self):
        import random
        rng = random.Random(11)
        cfg = self.serve(make_data(5_000_000))
        cfg.delay = 0.02
        self.config.set("max_concurrent_downloads", 2)
        m = self.manager(CountingEngine)
        ids = [m.add(self.url(f"{n}.bin")) for n in "abcd"]
        seen = {}
        m.add_listener(ManagerListener(on_task_updated=lambda t: seen.__setitem__(t.id, t.status)))
        time.sleep(0.8)
        running = (TaskStatus.PROBING, TaskStatus.DOWNLOADING)
        for _ in range(60):
            tid = rng.choice(ids)
            st = self.status(m, tid)
            if st == TaskStatus.PAUSED:
                m.resume(tid)
            elif st in running or st == TaskStatus.QUEUED:
                m.pause(tid)
            time.sleep(rng.choice([0.0, 0.02, 0.08, 0.3]))

        self.assertLessEqual(CountingEngine.peak, 2, "sınır aşıldı")
        cfg.delay = 0
        for tid in ids:                       # fırtına sonrası hepsi sürdürülüp bitmeli
            if self.status(m, tid) == TaskStatus.PAUSED:
                m.resume(tid)
        for tid in ids:
            self.wait_status(m, tid, TaskStatus.COMPLETED, timeout=40)
        for tid in ids:
            self.assertEqual(m.get_task(tid).final_path.stat().st_size, 5_000_000)
        self.assertLessEqual(CountingEngine.peak, 2)
        # ekranın (olay akışının) son hâli gerçek durumla aynı olmalı
        self.assertTrue(wait_for(lambda: all(seen.get(t) == TaskStatus.COMPLETED for t in ids)))

    def test_cancel_then_immediate_retry(self):
        cfg = self.serve(make_data(3_000_000))
        cfg.delay = 0.02
        m = self.manager()
        tid = m.add(self.url())
        self.assertTrue(wait_for(lambda: m.get_task(tid).downloaded > 200_000))
        m.cancel(tid)
        m.retry(tid)                          # motor daha kapanmadan
        cfg.delay = 0
        self.wait_status(m, tid, TaskStatus.COMPLETED, timeout=30)

    def test_error_then_immediate_retry(self):
        cfg = self.serve(make_data(800_000))
        cfg.lie_after_probe = True
        m = self.manager()
        tid = m.add(self.url())
        self.wait_status(m, tid, TaskStatus.ERROR)
        cfg.lie_after_probe = False
        m.retry(tid)                          # eski motor thread'i kapanmadan
        self.wait_status(m, tid, TaskStatus.COMPLETED)


class ResolverIntegrationTests(ManagerTestCase):
    """Paylaşım linkleri eklenirken doğrudan indirme adresine çevrilir (ağ yok: çözümleyici sahte yanıtla değiştirilir)."""
    DRIVE_LINK = "https://drive.google.com/file/d/ABC123/view?usp=sharing"

    def fake_resolve(self, target):
        return mock.patch("core.manager.resolver.resolve",
                          return_value=resolver.Resolved(url=target, source="Google Drive"))

    def test_shared_link_is_resolved_and_downloaded(self):
        data = make_data(50_000)
        self.serve(data)
        m = self.manager()
        target = self.url("hedef.bin")
        with self.fake_resolve(target):
            tid = m.add(self.DRIVE_LINK)
        task = m.get_task(tid)
        self.assertEqual(task.url, target)
        self.assertEqual(task.options["original_url"], self.DRIVE_LINK)
        self.assertEqual(task.options["source_hint"], "Google Drive")
        self.wait_status(m, tid, TaskStatus.COMPLETED)
        self.assertEqual(m.get_task(tid).final_path.read_bytes(), data)

    def test_same_file_via_different_links_is_not_duplicated(self):
        cfg = self.serve(make_data(1_000_000))
        cfg.delay = 0.02
        m = self.manager()
        with self.fake_resolve(self.url("hedef.bin")):
            a = m.add("https://drive.google.com/file/d/ABC123/view")
            b = m.add("https://drive.google.com/open?id=ABC123")
        self.assertEqual(a, b, "aynı dosya iki kez eklenmemeli (aynı geçici klasörü paylaşırlardı)")
        self.assertEqual(len(m.tasks()), 1)

    def test_engine_named_tasks_are_not_resolved(self):
        self.serve(make_data(10_000))
        m = self.manager()
        with self.fake_resolve("http://baska.example/x.bin") as fake:
            tid = m.add(self.url(), engine="http")
        fake.assert_not_called()
        self.assertEqual(m.get_task(tid).url, self.url())

    def test_ordinary_link_gets_no_extra_options(self):
        self.serve(make_data(10_000))
        m = self.manager()
        tid = m.add(self.url())
        self.assertEqual(m.get_task(tid).options, {})
        self.assertEqual(m.get_task(tid).url, self.url())

    def test_unsupported_share_type_raises_and_adds_nothing(self):
        m = self.manager()
        with self.assertRaises(resolver.UnsupportedLink):
            m.add("https://drive.google.com/drive/folders/ABC123")
        self.assertEqual(m.tasks(), [])


class CategoryAndAutoResumeTests(ManagerTestCase):
    def slow(self, size=3_000_000):
        data = make_data(size)
        cfg = self.serve(data)
        cfg.delay = 0.02
        return data, cfg

    def test_category_flag_goes_only_to_engine_copy(self):
        data = make_data(60_000)
        self.serve(data)
        self.config.set("sort_into_categories", True)
        m = self.manager()
        tid = m.add(self.url("film.mkv"))
        self.assertEqual(m.get_task(tid).options, {})
        self.wait_status(m, tid, TaskStatus.COMPLETED)
        task = m.get_task(tid)
        self.assertEqual(task.final_path.parent, self.save_dir / "Videolar")
        self.assertEqual(task.final_path.read_bytes(), data)
        self.assertEqual(task.options, {}, "ekranın/veritabanının görevi değişmemeli")

    def test_without_category_setting_file_stays_in_root(self):
        self.serve(make_data(60_000))
        m = self.manager()
        tid = m.add(self.url("film.mkv"))
        self.wait_status(m, tid, TaskStatus.COMPLETED)
        self.assertEqual(m.get_task(tid).final_path.parent, self.save_dir)

    def test_running_downloads_resume_after_restart_when_enabled(self):
        data, cfg = self.slow()
        self.config.set("auto_resume_on_start", True)
        m1 = self.manager()
        tid = m1.add(self.url())
        self.assertTrue(wait_for(lambda: m1.get_task(tid).downloaded > 900_000))
        m1.shutdown()
        self.assertEqual(self.db_get(tid).status, TaskStatus.QUEUED)

        cfg.delay = 0
        m2 = self.manager()
        self.assertEqual(m2.get_task(tid).status, TaskStatus.QUEUED)
        time.sleep(0.3)
        self.assertEqual(self.status(m2, tid), TaskStatus.QUEUED, "start_pending çağrılmadan başlamamalı")
        m2.start_pending()
        self.wait_status(m2, tid, TaskStatus.COMPLETED)
        self.assertEqual(m2.get_task(tid).final_path.read_bytes(), data)

    def test_user_paused_downloads_stay_paused_after_restart(self):
        data, cfg = self.slow()
        self.config.set("auto_resume_on_start", True)
        self.config.set("max_concurrent_downloads", 2)
        m1 = self.manager()
        a = m1.add(self.url("a.bin"))
        b = m1.add(self.url("b.bin"))
        self.assertTrue(wait_for(lambda: m1.get_task(a).downloaded > 300_000
                                 and m1.get_task(b).downloaded > 300_000))
        m1.pause(b)
        self.wait_status(m1, b, TaskStatus.PAUSED)
        m1.shutdown()

        cfg.delay = 0
        m2 = self.manager()
        self.assertEqual(self.status(m2, a), TaskStatus.QUEUED)
        self.assertEqual(self.status(m2, b), TaskStatus.PAUSED)
        m2.start_pending()
        self.wait_status(m2, a, TaskStatus.COMPLETED)
        time.sleep(0.3)
        self.assertEqual(self.status(m2, b), TaskStatus.PAUSED, "kullanıcının duraklattığı kendiliğinden başlamamalı")

    def test_without_setting_interrupted_downloads_stay_paused(self):
        data, cfg = self.slow()
        m1 = self.manager()
        tid = m1.add(self.url())
        self.assertTrue(wait_for(lambda: m1.get_task(tid).downloaded > 900_000))
        m1.shutdown()
        self.assertEqual(self.db_get(tid).status, TaskStatus.PAUSED)
        m2 = self.manager()
        self.assertEqual(m2.get_task(tid).status, TaskStatus.PAUSED)
        m2.start_pending()
        time.sleep(0.3)
        self.assertEqual(self.status(m2, tid), TaskStatus.PAUSED)

    def test_crash_leftovers_are_queued_when_enabled(self):
        self.config.set("auto_resume_on_start", True)
        db = Database(self.db_path)
        for status in (TaskStatus.DOWNLOADING, TaskStatus.PROBING, TaskStatus.QUEUED, TaskStatus.MERGING):
            db.save(DownloadTask(url="http://x/a.bin", save_dir=self.save_dir, status=status))
        db.save(DownloadTask(url="http://x/b.bin", save_dir=self.save_dir, status=TaskStatus.COMPLETED))
        db.close()
        m = self.manager()
        statuses = sorted(t.status.value for t in m.tasks())
        self.assertEqual(statuses, ["completed", "queued", "queued", "queued", "queued"])


class SequentialModeTests(ManagerTestCase):
    def test_sequential_mode_runs_one_at_a_time(self):
        cfg = self.serve(make_data(600_000))
        cfg.delay = 0.02
        self.config.set("max_concurrent_downloads", 3)
        self.config.set("download_mode", "sequential")
        m = self.manager(CountingEngine)
        ids = [m.add(self.url(f"{n}.bin")) for n in "abc"]
        for tid in ids:
            self.wait_status(m, tid, TaskStatus.COMPLETED, timeout=40)
        self.assertEqual(CountingEngine.peak, 1)

    def test_switching_to_parallel_starts_waiting_ones(self):
        cfg = self.serve(make_data(2_000_000))
        cfg.delay = 0.02
        self.config.set("max_concurrent_downloads", 3)
        self.config.set("download_mode", "sequential")
        m = self.manager(CountingEngine)
        ids = [m.add(self.url(f"{n}.bin")) for n in "abc"]
        time.sleep(0.5)
        self.assertEqual(CountingEngine.running, 1)
        self.config.set("download_mode", "parallel")
        m.apply_settings()
        self.assertTrue(wait_for(lambda: CountingEngine.running == 3, timeout=5))

    def test_invalid_mode_falls_back_to_parallel(self):
        self.config.set("download_mode", "bozuk")
        self.assertEqual(self.config.get("download_mode"), "parallel")
        

class PersistenceTests(ManagerTestCase):
    def test_shutdown_then_restore_and_resume(self):
        data = make_data(3_000_000)
        cfg = self.serve(data)
        cfg.delay = 0.02

        m1 = self.manager()
        tid = m1.add(self.url())
        self.assertTrue(wait_for(lambda: m1.get_task(tid).downloaded > 900_000))
        m1.shutdown()
        self.assertEqual(self.db_get(tid).status, TaskStatus.PAUSED)
        sent_before = cfg.bytes_sent

        cfg.delay = 0
        m2 = self.manager()
        restored = m2.get_task(tid)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.status, TaskStatus.PAUSED)
        time.sleep(0.3)
        self.assertEqual(self.status(m2, tid), TaskStatus.PAUSED, "otomatik başlamamalı")

        m2.resume(tid)
        self.wait_status(m2, tid, TaskStatus.COMPLETED)
        self.assertEqual(m2.get_task(tid).final_path.read_bytes(), data)
        self.assertLess(cfg.bytes_sent - sent_before, len(data) * 0.9)

    def test_crash_leftovers_become_paused(self):
        db = Database(self.db_path)
        for status in (TaskStatus.DOWNLOADING, TaskStatus.PROBING, TaskStatus.QUEUED, TaskStatus.MERGING):
            db.save(DownloadTask(url="http://x/a.bin", save_dir=self.save_dir, status=status))
        db.save(DownloadTask(url="http://x/b.bin", save_dir=self.save_dir, status=TaskStatus.COMPLETED))
        db.close()

        m = self.manager()
        statuses = sorted(t.status.value for t in m.tasks())
        self.assertEqual(statuses, ["completed", "paused", "paused", "paused", "paused"])

    def test_shutdown_is_idempotent_and_blocks_add(self):
        self.serve(b"x" * 1000)
        m = self.manager()
        m.shutdown()
        m.shutdown()
        with self.assertRaises(RuntimeError):
            m.add(self.url())


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_roundtrip(self):
        db = Database(Path(self.tmp) / "t.db")
        task = DownloadTask(
            url="http://x/ü.bin", save_dir=Path(self.tmp), file_name="ü.bin",
            headers={"Referer": "http://x"}, file_size=123, status=TaskStatus.COMPLETED,
            downloaded=123, final_path=Path(self.tmp) / "ü.bin", engine="http",
            finished_at=datetime(2026, 1, 2, 3, 4, 5),
        )
        db.save(task)
        got = db.get(task.id)
        for field in ("url", "save_dir", "file_name", "headers", "file_size", "status",
                      "downloaded", "final_path", "engine", "finished_at", "created_at"):
            self.assertEqual(getattr(got, field), getattr(task, field), field)

        task.status = TaskStatus.ERROR
        task.error = "hata"
        db.save(task)
        self.assertEqual(len(db.load_all()), 1)
        self.assertEqual(db.get(task.id).error, "hata")
        db.delete(task.id)
        self.assertEqual(db.load_all(), [])
        db.close()

    def test_corrupt_file_is_replaced(self):
        path = Path(self.tmp) / "t.db"
        path.write_bytes(b"bu bir sqlite dosyasi degil" * 100)
        db = Database(path)
        self.assertEqual(db.load_all(), [])
        db.save(DownloadTask(url="http://x/a", save_dir=Path(self.tmp)))
        self.assertEqual(len(db.load_all()), 1)
        self.assertTrue(Path(str(path) + ".bozuk").exists())
        db.close()

    def test_use_after_close_is_safe(self):
        db = Database(":memory:")
        db.close()
        db.save(DownloadTask(url="http://x/a", save_dir=Path(self.tmp)))  # hata vermemeli


class ReserveUniquePathTests(unittest.TestCase):
    def test_reserves_distinct_names_under_concurrency(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = []
            lock = threading.Lock()

            def worker():
                p = reserve_unique_path(Path(tmp) / "a.zip")
                with lock:
                    results.append(p.name)

            threads = [threading.Thread(target=worker) for _ in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(len(set(results)), 20)
            self.assertIn("a.zip", results)
            self.assertIn("a (1).zip", results)


if __name__ == "__main__":
    unittest.main()