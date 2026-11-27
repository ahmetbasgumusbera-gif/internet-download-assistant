"""
YtdlpEngine uçtan uca testleri: GERÇEK yt-dlp ve ffmpeg süreçleri, sahte yerel "YouTube" sitesine karşı.
yt-dlp ya da ffmpeg yoksa testler atlanır.
"""
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from core.engines.ytdlp_engine import YtdlpEngine
from core.events import EngineCallbacks
from core.models import DownloadTask, TaskStatus
from core.youtube import (
    Selection, analyze, build_args, default_selection, estimate_size, friendly_error,
)
from tests.yt_fake_site import (
    FakeYoutube, duration_seconds, find_ffmpeg, probe_streams, yt_dlp_available,
)
from utils import paths

READY = yt_dlp_available() and find_ffmpeg() is not None


def wait_for(cond, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@unittest.skipUnless(READY, "yt-dlp ve ffmpeg gerekli")
class YtdlpEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.site = FakeYoutube(seconds=8).__enter__()
        cls.ffmpeg = cls.site.ffmpeg
        cls.engine_cls = type("TestYtdlpEngine", (YtdlpEngine,), {
            "EXTRA_ARGS": cls.site.extra_args, "FFMPEG_LOCATION": cls.ffmpeg})
        cls.info = analyze(cls.site.url(), extra_args=cls.site.extra_args)

    @classmethod
    def tearDownClass(cls):
        cls.site.__exit__(None, None, None)

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.save_dir = Path(self._tmp) / "dl"
        self.site.delay = 0.0
        self.site.bytes_sent = 0
        self.threads = []

    def tearDown(self):
        for t in self.threads:
            t.join(timeout=10)
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ---- yardımcılar
    def selection(self, **kw):
        sel = default_selection(self.info)
        for key, value in kw.items():
            setattr(sel, key, value)
        return sel

    def make(self, sel=None, callbacks=None, url=None, task_id=None):
        sel = sel or self.selection()
        task = DownloadTask(
            url=url or self.site.url(), save_dir=self.save_dir, engine="ytdlp",
            title=self.info.title,
            options={"args": build_args(self.info, sel), "expected_size": estimate_size(self.info, sel)},
        )
        if task_id:
            task.id = task_id
        return self.engine_cls(task, callbacks, connections=4)

    def run_async(self, engine):
        t = threading.Thread(target=engine.start, daemon=True)
        t.start()
        self.threads.append(t)
        return t

    def temp_root_entries(self):
        root = paths.temp_root(self.save_dir)
        return list(root.iterdir()) if root.exists() else []

    # ---- testler
    def test_default_download_merges_and_embeds_thumbnail(self):
        seen = []
        eng = self.make(callbacks=EngineCallbacks(on_status=lambda t: seen.append(t.status)))
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)

        out = eng.task.final_path
        self.assertEqual(out.parent, self.save_dir)
        self.assertEqual(out.name, "Sahte Video ŞÖĞ.mp4")
        streams = probe_streams(self.ffmpeg, out)
        self.assertEqual(streams["audio"], 1, "ses akışı birleştirilmiş olmalı")
        self.assertGreaterEqual(streams["video"], 1)
        self.assertTrue(streams["attached_pic"], "küçük resim gömülmüş olmalı")
        self.assertEqual(eng.task.file_size, out.stat().st_size)
        self.assertEqual(eng.task.downloaded, eng.task.file_size)
        self.assertIn(TaskStatus.MERGING, seen, "birleştirme aşaması görünmeli")
        self.assertIn(TaskStatus.DOWNLOADING, seen)
        self.assertLess(seen.index(TaskStatus.DOWNLOADING), seen.index(TaskStatus.MERGING),
                        f"indirme, birleştirmeden önce görünmeli: {seen}")
        self.assertEqual(seen[0], TaskStatus.PROBING)
        self.assertEqual(seen[-1], TaskStatus.COMPLETED)
        self.assertEqual(self.temp_root_entries(), [], "geçici klasör temizlenmeli")

    def test_thumbnail_embedding_can_be_disabled(self):
        eng = self.make(self.selection(embed_thumbnail=False))
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)
        self.assertFalse(probe_streams(self.ffmpeg, eng.task.final_path)["attached_pic"])

    def test_video_only_has_no_audio(self):
        eng = self.make(self.selection(mode="video_only"))
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)
        self.assertEqual(probe_streams(self.ffmpeg, eng.task.final_path)["audio"], 0)

    def test_audio_mp3(self):
        eng = self.make(self.selection(mode="audio", audio="mp3", audio_kbps=192))
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)
        out = eng.task.final_path
        self.assertEqual(out.suffix, ".mp3")
        streams = probe_streams(self.ffmpeg, out)
        self.assertIn("mp3", streams["text"].lower())
        self.assertEqual(streams["video"] >= 0, True)

    def test_audio_original_m4a(self):
        eng = self.make(self.selection(mode="audio", audio="m4a"))
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)
        self.assertEqual(eng.task.final_path.suffix, ".m4a")
        self.assertEqual(probe_streams(self.ffmpeg, eng.task.final_path)["audio"], 1)

    def test_time_range_cuts_video(self):
        eng = self.make(self.selection(start="0:01", end="0:03"))
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)
        length = duration_seconds(self.ffmpeg, eng.task.final_path)
        self.assertIsNotNone(length)
        self.assertLess(length, 6.0, f"kırpılmış olmalı (süre {length})")

    def test_same_name_is_not_overwritten(self):
        first = self.make()
        first.start()
        second = self.make()
        second.start()
        self.assertEqual(second.task.status, TaskStatus.COMPLETED, second.task.error)
        self.assertEqual(first.task.final_path.name, "Sahte Video ŞÖĞ.mp4")
        self.assertEqual(second.task.final_path.name, "Sahte Video ŞÖĞ (1).mp4")

    def test_category_option_puts_video_and_audio_into_category_folders(self):
        video = self.make(self.selection(embed_thumbnail=False))
        video.task.options = dict(video.task.options, sort_by_category=True)
        video.start()
        self.assertEqual(video.task.status, TaskStatus.COMPLETED, video.task.error)
        self.assertEqual(video.task.final_path.parent, self.save_dir / "Videolar")

        audio = self.make(self.selection(mode="audio", audio="m4a", embed_thumbnail=False))
        audio.task.options = dict(audio.task.options, sort_by_category=True)
        audio.start()
        self.assertEqual(audio.task.status, TaskStatus.COMPLETED, audio.task.error)
        self.assertEqual(audio.task.final_path.parent, self.save_dir / "Müzik")

    def test_unavailable_video_gives_friendly_error(self):
        eng = self.make(url=self.site.url("gone"))
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.ERROR)
        self.assertIn("kullanılamıyor", eng.task.error)

    def test_missing_ffmpeg_gives_friendly_error(self):
        broken = type("NoFfmpegEngine", (self.engine_cls,), {"FFMPEG_LOCATION": str(Path(self._tmp) / "yok")})
        task = DownloadTask(
            url=self.site.url(), save_dir=self.save_dir, engine="ytdlp",
            options={"args": build_args(self.info, self.selection())},
        )
        eng = broken(task, None, connections=2)
        eng.start()
        self.assertEqual(task.status, TaskStatus.ERROR)
        self.assertTrue(task.error)

    def test_cancel_is_prompt_and_cleans_up(self):
        self.site.delay = 0.05
        eng = self.make()
        t = self.run_async(eng)
        self.assertTrue(wait_for(lambda: eng.task.status == TaskStatus.DOWNLOADING and eng.task.downloaded > 0))
        start = time.monotonic()
        eng.cancel()
        call_time = time.monotonic() - start
        t.join(10)
        self.assertFalse(t.is_alive())
        self.assertLess(call_time, 0.5, "cancel() bloke olmamalı")
        self.assertLess(time.monotonic() - start, 3.0)
        self.assertEqual(eng.task.status, TaskStatus.CANCELLED)
        self.assertEqual(self.temp_root_entries(), [])
        self.assertFalse(list(self.save_dir.glob("*.mp4")) if self.save_dir.exists() else [])

    def test_cancel_before_start(self):
        eng = self.make()
        eng.cancel()
        self.assertEqual(eng.task.status, TaskStatus.CANCELLED)

    def test_stop_keeps_temp_and_resume_continues(self):
        self.site.delay = 0.03
        total = estimate_size(self.info, self.selection())
        first = self.make(task_id="resume-test-1")
        t = self.run_async(first)
        self.assertTrue(wait_for(lambda: first.task.downloaded > total * 0.25, timeout=30))
        start = time.monotonic()
        first.stop()
        t.join(10)
        self.assertLess(time.monotonic() - start, 3.0)
        self.assertEqual(first.task.status, TaskStatus.PAUSED)
        self.assertTrue(self.temp_root_entries(), "stop() geçici klasörü korumalı")

        sent_before = self.site.bytes_sent
        self.site.delay = 0
        second = self.make(task_id="resume-test-1")      # aynı görev kimliği = aynı geçici klasör
        second.start()
        self.assertEqual(second.task.status, TaskStatus.COMPLETED, second.task.error)
        resumed_bytes = self.site.bytes_sent - sent_before
        self.assertLess(resumed_bytes, total * 0.95, "kaldığı yerden devam etmeli, baştan inmemeli")
        self.assertEqual(probe_streams(self.ffmpeg, second.task.final_path)["audio"], 1)

    def test_progress_is_monotonic_and_reaches_total(self):
        self.site.delay = 0.01
        samples = []
        eng = self.make(callbacks=EngineCallbacks(
            on_progress=lambda t: samples.append((t.downloaded, t.file_size))))
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)
        done = [d for d, _ in samples]
        self.assertEqual(done, sorted(done), "ilerleme geriye gitmemeli")
        self.assertTrue(all(d <= (s or d) for d, s in samples), "inen, toplamı aşmamalı")

    def test_speed_limit_flag_is_passed(self):
        from core.ratelimit import RateLimiter
        eng = self.make()
        eng.limiter = RateLimiter(500_000)
        cmd = eng._build_command()
        self.assertEqual(cmd[cmd.index("--limit-rate") + 1], "500000")


if __name__ == "__main__":
    unittest.main()
