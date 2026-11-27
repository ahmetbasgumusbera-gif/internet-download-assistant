import json
import tempfile
import unittest
from pathlib import Path

from utils.config_manager import ConfigManager
from utils.helpers import (
    category_folder, format_eta, format_size, get_category, is_dangerous,
    sanitize_filename, unique_path,
)


class SanitizeTests(unittest.TestCase):
    def test_path_traversal(self):
        self.assertEqual(sanitize_filename("..\\..\\x.exe"), "x.exe")
        self.assertEqual(sanitize_filename("../../etc/passwd"), "passwd")

    def test_invalid_chars(self):
        self.assertEqual(sanitize_filename('a<b>c:d"e|f?g*h.txt'), "a_b_c_d_e_f_g_h.txt")

    def test_reserved(self):
        self.assertEqual(sanitize_filename("CON.txt"), "_CON.txt")
        self.assertEqual(sanitize_filename("nul"), "_nul")

    def test_empty_and_dots(self):
        self.assertEqual(sanitize_filename(""), "indirilen_dosya")
        self.assertEqual(sanitize_filename(".."), "indirilen_dosya")

    def test_long_name_keeps_extension(self):
        result = sanitize_filename("a" * 400 + ".zip")
        self.assertTrue(result.endswith(".zip"))
        self.assertLessEqual(len(result), 150)

    def test_turkish_chars_kept(self):
        self.assertEqual(sanitize_filename("İnternet çalışması.pdf"), "İnternet çalışması.pdf")


class MiscTests(unittest.TestCase):
    def test_format_size(self):
        self.assertEqual(format_size(0), "0 B")
        self.assertEqual(format_size(1536), "1.50 KB")
        self.assertEqual(format_size(None), "Bilinmiyor")

    def test_format_eta(self):
        self.assertEqual(format_eta(45), "45sn")
        self.assertEqual(format_eta(125), "2dk 05sn")
        self.assertEqual(format_eta(7500), "2sa 05dk")

    def test_category_and_danger(self):
        self.assertEqual(get_category("film.MKV"), "Videolar")
        self.assertEqual(get_category("x.bilinmeyen"), "Diğer")
        self.assertTrue(is_dangerous("setup.exe"))
        self.assertFalse(is_dangerous("belge.pdf"))

    def test_unique_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.zip"
            p.write_text("x")
            self.assertEqual(unique_path(p).name, "a (1).zip")


class CategoryFolderTests(unittest.TestCase):
    def test_category_folders(self):
        base = Path("/indirilenler")
        self.assertEqual(category_folder(base, "film.MKV"), base / "Videolar")
        self.assertEqual(category_folder(base, "sarki.mp3"), base / "Müzik")
        self.assertEqual(category_folder(base, "rapor.pdf"), base / "Belgeler")
        self.assertEqual(category_folder(base, "x.bilinmeyen"), base / "Diğer")
        self.assertEqual(category_folder(base, "uzantisiz"), base / "Diğer")


class ConfigTests(unittest.TestCase):
    def test_defaults_save_and_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "config.json"
            cfg = ConfigManager(f)
            self.assertEqual(cfg.get("max_concurrent_downloads"), 3)
            cfg.set("max_concurrent_downloads", 5)
            self.assertEqual(ConfigManager(f).get("max_concurrent_downloads"), 5)

    def test_limits_clamped(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = ConfigManager(Path(tmp) / "c.json")
            cfg.set("connections_per_download", 999)
            self.assertEqual(cfg.get("connections_per_download"), 32)

    def test_corrupt_file_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "c.json"
            f.write_text("{bozuk json", encoding="utf-8")
            cfg = ConfigManager(f)
            self.assertEqual(cfg.get("max_concurrent_downloads"), 3)
            self.assertTrue((Path(tmp) / "c.bozuk.json").exists())

    def test_behavior_settings_default_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = ConfigManager(Path(tmp) / "c.json")
            self.assertFalse(cfg.get("auto_resume_on_start"))
            self.assertFalse(cfg.get("clipboard_watch"))
            self.assertFalse(cfg.get("sort_into_categories"))
            self.assertTrue(cfg.get("warn_dangerous_files"))

    def test_unknown_key_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(KeyError):
                ConfigManager(Path(tmp) / "c.json").set("yok_boyle_ayar", 1)


if __name__ == "__main__":
    unittest.main()