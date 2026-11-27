"""W.19: proxy, kimlik, yedek adres, dönüştürme kararı, yedekleme, tema, Firefox paketi, hata raporu, yt-dlp toparlama."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import backup, firefox_ext, hashcheck, netconf, report, webvideo, youtube
from core.models import DownloadTask, TaskStatus
from gui import theme
from tests.test_http_engine import make_data
from tests.test_manager import ManagerTestCase
from tests.test_webvideo import HLS
from utils import paths


class NetconfTests(unittest.TestCase):
    def tearDown(self):
        netconf.configure("")

    def test_normalize(self):
        self.assertEqual(netconf.normalize("127.0.0.1:8080"), "http://127.0.0.1:8080")
        self.assertEqual(netconf.normalize("socks5://h:1080"), "socks5://h:1080")
        self.assertEqual(netconf.normalize("ftp://h"), "")
        self.assertEqual(netconf.normalize(""), "")

    def test_arguments(self):
        netconf.configure("h:3128")
        self.assertEqual(netconf.requests_proxies()["https"], "http://h:3128")
        self.assertEqual(netconf.ytdlp_args(), ["--proxy", "http://h:3128"])
        self.assertIn("--proxy", youtube.ytdlp_command("--version"))


class SmallPureTests(unittest.TestCase):
    def test_hash_sidecar_parsing(self):
        h = "a" * 64
        self.assertEqual(hashcheck.parse_for_file(h + "  x.zip\n", "x.zip"), ("sha256", h))
        text = ("b" * 64) + "  other.zip\n" + h + "  x.zip\n"
        self.assertEqual(hashcheck.parse_for_file(text, "x.zip"), ("sha256", h))
        self.assertEqual(hashcheck.parse_for_file(text, "yok.zip"), ("", ""))

    def test_should_heal(self):
        self.assertTrue(youtube.should_heal("ERROR: [youtube] x: Unable to extract player"))
        self.assertTrue(youtube.should_heal("HTTP Error 403: Forbidden"))
        self.assertFalse(youtube.should_heal("ERROR: Video unavailable"))

    def test_issue_url_is_short_and_points_to_repo(self):
        url = report.issue_url("Başlık", "x" * 5000)
        self.assertLessEqual(len(url), 2000)
        self.assertIn("github.com/", url)

    def test_audio_only_stream_args(self):
        info = webvideo.parse_stream(HLS)
        args = webvideo.build_args(info, webvideo.StreamChoice(container="mp3", title="Film"))
        self.assertIn("-x", args)
        self.assertEqual(args[args.index("--audio-format") + 1], "mp3")
        self.assertEqual(webvideo.choice_summary(info, webvideo.StreamChoice(container="wav")), "Sadece ses  ·  WAV")

    def test_firefox_package(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            src = tmp / "ext"
            src.mkdir()
            (src / "manifest.json").write_text(json.dumps({"manifest_version": 3, "version": "1.0",
                                                           "minimum_chrome_version": "111",
                                                           "background": {"service_worker": "background.js"}}),
                                               encoding="utf-8")
            (src / "background.js").write_text("//", encoding="utf-8")
            out = firefox_ext.build(src, tmp / "ff")
            data = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(data["background"], {"scripts": ["sniffer.js", "background.js"]})
            self.assertNotIn("minimum_chrome_version", data)
            self.assertIn("gecko", data["browser_specific_settings"])
            self.assertTrue((out / "background.js").is_file())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_hash_result_is_shown_on_card_text(self):
        from gui.components.download_item import DownloadItemWidget
        task = DownloadTask(url="http://x/a.zip", save_dir=Path("."), file_name="a.zip", status=TaskStatus.COMPLETED,
                            file_size=10, options={"hash_check": {"algo": "sha256", "ok": True}})
        self.assertIn("✓ SHA256 doğrulandı", DownloadItemWidget._info_text(task))
        task.options["hash_check"]["ok"] = False
        self.assertIn("UYUŞMUYOR", DownloadItemWidget._info_text(task))


class ThemeTests(unittest.TestCase):
    def tearDown(self):
        theme.apply("dark", "purple")

    def test_light_theme_and_accent_change_the_stylesheet(self):
        theme.apply("light", "blue")
        self.assertFalse(theme.IS_DARK)
        self.assertNotIn("167, 139, 250", theme.build_stylesheet())
        theme.apply("dark", "purple")
        self.assertTrue(theme.IS_DARK)
        self.assertIn("167, 139, 250", theme.build_stylesheet())


class BackupTests(unittest.TestCase):
    def test_backup_and_staged_restore(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            data = tmp / "data"
            data.mkdir()
            (data / "config.json").write_text('{"a": 1}', encoding="utf-8")
            con = sqlite3.connect(data / "history.db")
            con.execute("create table t(x)")
            con.commit()
            con.close()
            with mock.patch.object(paths, "DATA_DIR", data):
                names = backup.create(tmp / "b.zip")
                self.assertEqual(sorted(names), ["config.json", "history.db"])
                (data / "config.json").write_text('{"a": 2}', encoding="utf-8")
                backup.stage_restore(tmp / "b.zip")
                self.assertTrue((data / "config.json.restore").is_file())
                self.assertEqual(sorted(backup.apply_pending()), ["config.json", "history.db"])
                self.assertEqual(json.loads((data / "config.json").read_text(encoding="utf-8")), {"a": 1})
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class ManagerW19Tests(ManagerTestCase):
    def test_credentials_are_split_from_the_url(self):
        self.serve(make_data(1000))
        m = self.manager()
        m.set_hold(True)
        tid = m.add(self.base.replace("http://", "http://kul:p%20s@") + "/a.bin")
        task = m.get_task(tid)
        self.assertNotIn("kul", task.url)
        self.assertEqual((task.options["auth_user"], task.options["auth_pass"]), ("kul", "p s"))

    def test_mirror_switch_and_auth_api(self):
        self.serve(make_data(1000))
        m = self.manager()
        m.set_hold(True)
        first, mirror = self.url(), self.url("b.bin")
        tid = m.add(first)
        self.assertTrue(m.add_mirror(tid, mirror))
        self.assertFalse(m.add_mirror(tid, "ftp://x"))
        entry = m._entries[tid]
        entry.task.status = TaskStatus.ERROR
        entry.task.error = "Parça 1 indirilemedi: HTTP 503"
        self.assertTrue(m._try_mirror(tid))
        task = m.get_task(tid)
        self.assertEqual(task.url, mirror)
        self.assertEqual(task.options["resume_key"], first)
        self.assertEqual(task.status, TaskStatus.QUEUED)
        self.assertTrue(m.set_auth(tid, "u", "p"))
        self.assertEqual(m.get_task(tid).options["auth_user"], "u")

    def test_convert_decision(self):
        m = self.manager()

        def task(name, engine="http", **options):
            return DownloadTask(url="http://x/a", save_dir=self.save_dir, file_name=name, engine=engine, options=options)

        with mock.patch("core.convert.available", return_value=True):
            self.assertEqual(m._wants_convert(task("f.mkv", convert_to="mp4")), "mp4")
            self.assertEqual(m._wants_convert(task("f.mp4", convert_to="mp4")), "")
            self.assertEqual(m._wants_convert(task("a.zip", convert_to="mp3")), "")
            self.assertEqual(m._wants_convert(task("f.mkv", engine="ytdlp", convert_to="mp4")), "")
            self.assertEqual(m._wants_convert(task("f.mkv")), "")
            self.config.set("auto_convert", "mp3")
            self.assertEqual(m._wants_convert(task("f.mkv")), "mp3")
        with mock.patch("core.convert.available", return_value=False):
            self.assertEqual(m._wants_convert(task("f.mkv")), "")


if __name__ == "__main__":
    unittest.main()
