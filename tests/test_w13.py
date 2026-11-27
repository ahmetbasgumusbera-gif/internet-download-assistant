"""W.13: önceden sondalama önbelleği ve YouTube analiz dosyası."""
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from core.engines import http_engine
from core.engines.ytdlp_engine import YtdlpEngine
from core.models import DownloadTask, TaskStatus
from tests.test_http_engine import EngineTestCase, make_data


class ProbeCacheTests(EngineTestCase):
    def setUp(self):
        super().setUp()
        http_engine.PROBE_CACHE._items.clear()

    def probes(self):
        return len([r for r in self.cfg.range_headers if r == "bytes=0-0"])

    def test_prewarmed_probe_is_reused_exactly_once(self):
        data = make_data(300_000)
        self.serve(data)
        warm = self.engine(connections=2)
        warm.cache_probe = True
        warm.probe()
        warm._session.close()
        self.assertEqual(self.probes(), 1)

        eng = self.engine(connections=2)
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)
        self.assertEqual(eng.task.final_path.read_bytes(), data)
        self.assertEqual(self.probes(), 1, "önbellekteki sondalama kullanılmalı, sunucuya yeniden sorulmamalı")

        again = self.engine(connections=2)
        again.start()
        self.assertEqual(self.probes(), 2, "kayıt bir kez kullanılır; sonraki indirme gerçekten sondalar")

    def test_normal_engine_does_not_fill_the_cache(self):
        self.serve(make_data(50_000))
        self.engine().start()
        self.assertEqual(http_engine.PROBE_CACHE._items, {})

    def test_different_file_name_misses_the_cache(self):
        self.serve(make_data(50_000))
        warm = self.engine(name="a.bin")
        warm.cache_probe = True
        warm.probe()
        warm._session.close()
        eng = self.engine(name="baska.bin")
        eng.start()
        self.assertEqual(self.probes(), 2)

    def test_expired_entry_is_ignored(self):
        cache = http_engine._ProbeCache()
        cache.put("k", {"x": 1})
        cache._items["k"] = (time.monotonic() - cache.TTL - 1, {"x": 1})
        self.assertIsNone(cache.take("k"))


class InfoFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def engine(self, **options):
        task = DownloadTask(url="https://www.youtube.com/watch?v=abc", save_dir=self.tmp, engine="ytdlp",
                            options=dict({"args": []}, **options))
        return YtdlpEngine(task, None, connections=2)

    def test_fresh_info_file_replaces_the_url(self):
        info = self.tmp / "info.json"
        info.write_text("{}", encoding="utf-8")
        cmd = self.engine(info_file=str(info))._build_command()
        self.assertEqual(cmd[cmd.index("--load-info-json") + 1], str(info))
        self.assertNotIn("https://www.youtube.com/watch?v=abc", cmd)

    def test_missing_old_generic_or_playlist_use_the_url(self):
        info = self.tmp / "info.json"
        info.write_text("{}", encoding="utf-8")
        old = self.tmp / "old.json"
        old.write_text("{}", encoding="utf-8")
        os.utime(old, (time.time() - 5 * 3600,) * 2)
        for options in ({}, {"info_file": str(self.tmp / "yok.json")}, {"info_file": str(old)},
                        {"info_file": str(info), "generic": True}, {"info_file": str(info), "playlist_item": True}):
            with self.subTest(options=options):
                cmd = self.engine(**options)._build_command()
                self.assertNotIn("--load-info-json", cmd)
                self.assertEqual(cmd[-1], "https://www.youtube.com/watch?v=abc")

    def test_skip_flag_falls_back_to_url(self):
        info = self.tmp / "info.json"
        info.write_text("{}", encoding="utf-8")
        eng = self.engine(info_file=str(info))
        eng._skip_info = True
        self.assertNotIn("--load-info-json", eng._build_command())


if __name__ == "__main__":
    unittest.main()
