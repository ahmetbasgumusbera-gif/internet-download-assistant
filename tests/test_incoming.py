"""Adresi olmayan (blob/data) indirmelerin parça parça alınması ve yerel motorun dosyayı yerine koyması."""
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from core.engines.local_engine import LocalEngine
from core.incoming import Incoming, STALE_SECONDS
from core.models import DownloadTask, TaskStatus


class IncomingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.inc = Incoming(self.tmp / "in")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_chunks_are_joined_in_order(self):
        self.inc.append("a" * 8, 0, b"abc")
        self.inc.append("a" * 8, 1, b"def")
        path = self.inc.finish("a" * 8)
        self.assertEqual(path.read_bytes(), b"abcdef")
        self.assertEqual(path.suffix, ".bin")

    def test_out_of_order_is_rejected(self):
        self.inc.append("b" * 8, 0, b"x")
        with self.assertRaises(ValueError):
            self.inc.append("b" * 8, 2, b"y")
        with self.assertRaises(ValueError):
            self.inc.finish("c" * 8)               # hiç başlamamış yükleme

    def test_empty_file_and_abort(self):
        self.inc.append("d" * 8, 0, b"")
        self.assertEqual(self.inc.finish("d" * 8).read_bytes(), b"")
        self.inc.append("e" * 8, 0, b"x")
        self.inc.abort("e" * 8)
        self.assertEqual(list((self.tmp / "in").glob("e*")), [])

    def test_cleanup_removes_only_old_files(self):
        self.inc.append("f" * 8, 0, b"x")
        old = self.inc.finish("f" * 8)
        self.inc.append("1" * 8, 0, b"y")
        import os
        os.utime(old, (time.time() - STALE_SECONDS - 10,) * 2)
        self.inc.cleanup()
        self.assertFalse(old.exists())
        self.assertTrue((self.tmp / "in" / ("1" * 8 + ".part")).exists())


class LocalEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def task(self, name="pin.jpg", **options):
        src = self.tmp / "gelen.bin"
        src.write_bytes(b"veri")
        return DownloadTask(url="local://x", save_dir=self.tmp / "dl", file_name=name, engine="local",
                            options=dict(source_path=str(src), **options)), src

    def test_moves_file_and_completes(self):
        task, src = self.task()
        LocalEngine(task).start()
        self.assertEqual(task.status, TaskStatus.COMPLETED, task.error)
        self.assertEqual(task.final_path, self.tmp / "dl" / "pin.jpg")
        self.assertEqual(task.final_path.read_bytes(), b"veri")
        self.assertFalse(src.exists())

    def test_category_folder_and_name_collision(self):
        (self.tmp / "dl" / "Resimler").mkdir(parents=True)
        (self.tmp / "dl" / "Resimler" / "pin.jpg").write_bytes(b"eski")
        task, _ = self.task(sort_by_category=True)
        LocalEngine(task).start()
        self.assertEqual(task.final_path.name, "pin (1).jpg")
        self.assertEqual((self.tmp / "dl" / "Resimler" / "pin.jpg").read_bytes(), b"eski")

    def test_missing_source_is_an_error_and_cancel_deletes_temp(self):
        task = DownloadTask(url="local://x", save_dir=self.tmp, file_name="a.jpg", engine="local",
                            options={"source_path": str(self.tmp / "yok.bin")})
        LocalEngine(task).start()
        self.assertEqual(task.status, TaskStatus.ERROR)
        task2, src = self.task()
        LocalEngine(task2).cancel()
        self.assertEqual(task2.status, TaskStatus.CANCELLED)
        self.assertFalse(src.exists())


if __name__ == "__main__":
    unittest.main()
