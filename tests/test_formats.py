"""Dosya türü tanıma: kategori ve tehlikeli dosya listeleri."""
import unittest

from utils.helpers import get_category, is_dangerous


class FormatTests(unittest.TestCase):
    def test_categories(self):
        cases = {
            "a.pdf": "Belgeler", "a.rtf": "Belgeler", "a.odt": "Belgeler", "a.ods": "Belgeler", "a.odp": "Belgeler",
            "a.xlsx": "Belgeler", "a.csv": "Belgeler", "a.txt": "Belgeler",
            "a.epub": "E-kitaplar", "a.azw3": "E-kitaplar", "a.mobi": "E-kitaplar",
            "a.jpg": "Resimler", "a.jpeg": "Resimler", "a.png": "Resimler", "a.gif": "Resimler", "a.webp": "Resimler",
            "a.svg": "Resimler", "a.bmp": "Resimler", "a.tiff": "Resimler", "a.heic": "Resimler",
            "a.psd": "Tasarım",
            "a.mp3": "Müzik", "a.wav": "Müzik", "a.aac": "Müzik", "a.m4a": "Müzik", "a.ogg": "Müzik", "a.wma": "Müzik",
            "a.mp4": "Videolar", "a.mkv": "Videolar", "a.avi": "Videolar", "a.webm": "Videolar", "a.flv": "Videolar",
            "a.7z": "Arşivler", "a.tar": "Arşivler", "a.gz": "Arşivler", "a.iso": "Arşivler",
            "a.exe": "Programlar", "a.msi": "Programlar", "a.apk": "Programlar", "a.dmg": "Programlar",
            "a.pkg": "Programlar", "a.jar": "Programlar", "a.ps1": "Programlar",
            "a.py": "Kod",
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(get_category(name), expected)

    def test_web_pages_are_not_files(self):
        for name in ("sayfa.html", "sayfa.htm", "haber"):
            self.assertEqual(get_category(name), "Diğer")

    def test_dangerous(self):
        for name in ("a.exe", "a.msi", "a.apk", "a.ps1", "a.py", "a.jar", "a.dmg", "a.pkg", "a.reg", "a.msix"):
            self.assertTrue(is_dangerous(name), name)
        for name in ("a.pdf", "a.jpg", "a.mp4", "a.zip", "a.txt"):
            self.assertFalse(is_dangerous(name), name)


if __name__ == "__main__":
    unittest.main()
