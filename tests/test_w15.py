"""W.15: arşiv açma, araç kurulumu denetimi, tarayıcı bulma ve hız grafiği."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import io
import shutil
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from PySide6.QtWidgets import QApplication

from core import browsers, extract, toolsetup
from utils import paths


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_recognition_and_destination_names(self):
        for name in ("a.zip", "a.RAR", "a.7z", "a.tar.gz", "a.tgz", "a.tar", "a.xz"):
            self.assertTrue(extract.is_archive(name), name)
        for name in ("a.exe", "a.pdf", "a.zip.txt", "a"):
            self.assertFalse(extract.is_archive(name), name)
        self.assertEqual(extract.destination(self.tmp / "film.tar.gz").name, "film")
        (self.tmp / "oyun").mkdir()
        self.assertEqual(extract.destination(self.tmp / "oyun.zip").name, "oyun (1)")

    def test_zip_is_extracted_into_its_own_folder(self):
        path = self.tmp / "belge.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("a/b.txt", "merhaba")
        dest = extract.extract(path)
        self.assertEqual(dest, self.tmp / "belge")
        self.assertEqual((dest / "a" / "b.txt").read_text(), "merhaba")

    def test_zip_slip_is_rejected_and_nothing_is_left(self):
        path = self.tmp / "kotu.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("../kacak.txt", "x")
        with self.assertRaises(extract.ExtractError):
            extract.extract(path)
        self.assertFalse((self.tmp / "kacak.txt").exists())
        self.assertFalse((self.tmp / "kotu").exists(), "yarım klasör silinmeli")

    def test_tar_gz_and_broken_archive(self):
        path = self.tmp / "veri.tar.gz"
        with tarfile.open(path, "w:gz") as tf:
            data = b"icerik"
            info = tarfile.TarInfo("x.txt")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        self.assertEqual((extract.extract(path) / "x.txt").read_bytes(), b"icerik")
        broken = self.tmp / "bozuk.zip"
        broken.write_bytes(b"bu bir zip degil")
        with self.assertRaises(extract.ExtractError):
            extract.extract(broken)
        self.assertFalse((self.tmp / "bozuk").exists())


class ToolsAndBrowserTests(unittest.TestCase):
    def test_nothing_is_missing_in_development_copy(self):
        with mock.patch.object(paths, "FROZEN", False):
            self.assertEqual(toolsetup.missing(), [])

    def test_detect_returns_a_list(self):
        found = browsers.detect()
        self.assertIsInstance(found, list)
        self.assertTrue(all(b.exe and b.url for b in found))


class SpeedGraphTests(unittest.TestCase):
    def test_push_keeps_fixed_length_and_tooltip(self):
        app = QApplication.instance() or QApplication([])
        from gui.components.speed_graph import SpeedGraph
        graph = SpeedGraph()
        for value in (0, 500_000, 2_000_000, 1_000_000):
            graph.push(value)
        self.assertEqual(len(graph._samples), SpeedGraph.SAMPLES)
        self.assertIn("en yüksek", graph.toolTip())
        graph.grab()                                  # çizim hatasız çalışmalı
        graph.deleteLater()


if __name__ == "__main__":
    unittest.main()
