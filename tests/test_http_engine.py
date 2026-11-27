"""
HttpEngine testleri. İnternet gerekmez: yerel bir HTTP sunucusu Range desteğini
ve çeşitli bozuk sunucu davranışlarını taklit eder.
"""
import random
import re
import shutil
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import urlparse

import requests

from core.engines import http_engine
from core.engines.http_engine import HttpEngine
from core.events import EngineCallbacks
from core.models import DownloadTask, TaskStatus
from core.ratelimit import RateLimiter
from utils import paths


# ----------------------------------------------------------------- test sunucusu
class ServerConfig:
    def __init__(self, data: bytes):
        self.data = data
        self.support_ranges = True
        self.lie_after_probe = False   # probe'a 206, sonraki Range isteklerine 200 döner
        self.drop_first_n = 0          # ilk N büyük cevabı yarıda keser
        self.delay = 0.0               # her 32 KB'tan sonra bekleme (yavaş sunucu)
        self.header_delay = 0.0        # cevap başlığını göndermeden önce bekleme (hiç cevap vermeyen sunucu)
        self.etag = '"v1"'
        self.content_type = "application/octet-stream"
        self.error_status = 0          # 0 değilse her isteğe bu HTTP durum koduyla (gövdesiz) cevap verir
        self.part_status = 0           # 0 değilse sondalama dışındaki (Range'li) isteklere bu durum koduyla cevap verir
        self.max_connections = 0       # 0 = sınırsız; aşılırsa fazla istek overload_mode'a göre reddedilir
        self.overload_mode = "503"     # "503": hemen 503 ver | "stall": yanıtsız bırak
        self.active = 0
        self.peak_active = 0
        self.overloaded = 0
        self.content_disposition = None
        self.bytes_sent = 0
        self.range_headers = []
        self.lock = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        try:
            self._serve()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _serve(self):
        cfg = self.server.cfg
        if not cfg.max_connections:
            self._respond()
            return
        with cfg.lock:
            over = cfg.active >= cfg.max_connections
            if over:
                cfg.overloaded += 1
            else:
                cfg.active += 1
                cfg.peak_active = max(cfg.peak_active, cfg.active)
        if over:                                  # reddedilen istek yer tutmaz (aşırı yük kendini beslemesin)
            if cfg.overload_mode == "stall":
                time.sleep(30)
            else:
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
            return
        try:
            self._respond()
        finally:
            with cfg.lock:
                cfg.active -= 1

    def _respond(self):
        cfg = self.server.cfg
        data = cfg.data
        total = len(data)
        rng = self.headers.get("Range")
        with cfg.lock:
            cfg.range_headers.append(rng)
        if cfg.header_delay:
            time.sleep(cfg.header_delay)

        if cfg.error_status:
            self.send_response(cfg.error_status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if cfg.part_status and rng and rng != "bytes=0-0":
            self.send_response(cfg.part_status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        start, end, status = 0, total - 1, 200
        if rng and cfg.support_ranges:
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            if m:
                s = int(m.group(1))
                e = int(m.group(2)) if m.group(2) else total - 1
                if not (cfg.lie_after_probe and s > 0):
                    start, end, status = s, min(e, total - 1), 206
        body = data[start:end + 1]

        self.send_response(status)
        self.send_header("Content-Type", cfg.content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", cfg.etag)
        if cfg.support_ranges:
            self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        if cfg.content_disposition:
            self.send_header("Content-Disposition", cfg.content_disposition)
        self.end_headers()

        drop = False
        if len(body) > 1000:
            with cfg.lock:
                if cfg.drop_first_n > 0:
                    cfg.drop_first_n -= 1
                    drop = True
        if drop:
            self.wfile.write(body[: len(body) // 2])
            self.wfile.flush()
            self.close_connection = True
            return

        for i in range(0, len(body), 32768):
            piece = body[i:i + 32768]
            self.wfile.write(piece)
            with cfg.lock:
                cfg.bytes_sent += len(piece)
            if cfg.delay:
                time.sleep(cfg.delay)


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # istemci bağlantıyı kapatınca oluşan hataları sustur


class FastHttpEngine(HttpEngine):
    MIN_PART_SIZE = 100_000
    RETRY_BASE_DELAY = 0.01
    MAX_RETRIES = 2
    STATE_SAVE_INTERVAL = 0.05
    THROTTLE_WINDOW = 0.05


def make_data(n: int, seed: int = 1) -> bytes:
    return random.Random(seed).randbytes(n)


def wait_for(condition, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if condition():
            return True
        time.sleep(0.01)
    return False


# ----------------------------------------------------------------- test tabanı
class EngineTestCase(unittest.TestCase):
    def setUp(self):
        http_engine._HOST_LIMITS.clear()    # oturum boyunca öğrenilen sunucu sınırları testler arasında sızmasın
        self._tmp = tempfile.mkdtemp()
        self.save_dir = Path(self._tmp) / "dl"
        self.server = None
        self.cfg = None
        self.threads = []

    def tearDown(self):
        for t in self.threads:
            t.join(timeout=5)
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def serve(self, data: bytes) -> ServerConfig:
        self.cfg = ServerConfig(data)
        self.server = _Server(("127.0.0.1", 0), _Handler)
        self.server.cfg = self.cfg
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/dosya.bin"
        return self.cfg

    def engine(self, name="", connections=4, limiter=None, callbacks=None):
        task = DownloadTask(url=self.url, save_dir=self.save_dir, file_name=name)
        return FastHttpEngine(task, callbacks, connections, limiter)

    def run_async(self, engine):
        t = threading.Thread(target=engine.start, daemon=True)
        t.start()
        self.threads.append(t)
        return t

    def temp_dirs(self):
        root = paths.temp_root(self.save_dir)
        return list(root.iterdir()) if root.exists() else []


# ----------------------------------------------------------------- testler
class BasicDownloadTests(EngineTestCase):
    def test_multipart_download_is_correct(self):
        data = make_data(1_200_000)
        cfg = self.serve(data)
        eng = self.engine(connections=8)
        eng.start()

        self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)
        self.assertEqual(eng.task.final_path.read_bytes(), data)
        ranged = [r for r in cfg.range_headers if r and r != "bytes=0-0"]
        self.assertGreater(len(ranged), 1, "birden fazla parça istenmeliydi")
        self.assertEqual(self.temp_dirs(), [], "temp temizlenmeli")

    def test_server_without_range_support(self):
        data = make_data(500_000)
        cfg = self.serve(data)
        cfg.support_ranges = False
        eng = self.engine(connections=8)
        eng.start()

        self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)
        self.assertEqual(eng.task.final_path.read_bytes(), data)
        self.assertEqual(self.temp_dirs(), [])

    def test_small_file_single_part(self):
        data = make_data(50_000)
        cfg = self.serve(data)
        eng = self.engine(connections=8)
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)
        self.assertEqual(eng.task.final_path.read_bytes(), data)
        ranged = [r for r in cfg.range_headers if r and r != "bytes=0-0"]
        self.assertEqual(len(ranged), 1)

    def test_unreachable_server_gives_error(self):
        self.serve(b"x")
        self.server.shutdown()
        self.server.server_close()
        self.server = None
        eng = self.engine()
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.ERROR)
        self.assertTrue(eng.task.error)


class FileNameTests(EngineTestCase):
    def test_path_traversal_name_is_sanitized(self):
        cfg = self.serve(make_data(10_000))
        cfg.content_disposition = 'attachment; filename="..\\..\\evil.txt"'
        eng = self.engine()
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)
        self.assertEqual(eng.task.final_path.parent, self.save_dir)
        self.assertEqual(eng.task.final_path.name, "evil.txt")

    def test_rfc5987_utf8_name(self):
        cfg = self.serve(make_data(10_000))
        cfg.content_disposition = "attachment; filename*=UTF-8''%C3%A7al%C4%B1%C5%9Fma.txt"
        eng = self.engine()
        eng.start()
        self.assertEqual(eng.task.final_path.name, "çalışma.txt")

    def test_name_from_url_when_no_header(self):
        self.serve(make_data(10_000))
        eng = self.engine()
        eng.start()
        self.assertEqual(eng.task.final_path.name, "dosya.bin")

    def test_existing_file_not_overwritten(self):
        data = make_data(10_000)
        self.serve(data)
        self.save_dir.mkdir(parents=True)
        (self.save_dir / "dosya.bin").write_bytes(b"eski")
        eng = self.engine()
        eng.start()
        self.assertEqual((self.save_dir / "dosya.bin").read_bytes(), b"eski")
        self.assertEqual(eng.task.final_path.name, "dosya (1).bin")
        self.assertEqual(eng.task.final_path.read_bytes(), data)


class ErrorHandlingTests(EngineTestCase):
    def test_retry_after_dropped_connection(self):
        data = make_data(800_000)
        cfg = self.serve(data)
        cfg.drop_first_n = 2
        eng = self.engine(connections=4)
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)
        self.assertEqual(eng.task.final_path.read_bytes(), data)

    def test_server_ignoring_range_is_detected(self):
        cfg = self.serve(make_data(800_000))
        cfg.lie_after_probe = True
        eng = self.engine(connections=4)
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.ERROR)
        self.assertIn("206", eng.task.error)
        self.assertFalse(eng.task.final_path)

    def test_error_keeps_temp_for_retry(self):
        cfg = self.serve(make_data(800_000))
        cfg.lie_after_probe = True
        eng = self.engine(connections=4)
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.ERROR)
        self.assertTrue(self.temp_dirs(), "hata durumunda temp korunmalı")


class ControlTests(EngineTestCase):
    def slow_server(self, size=3_000_000):
        data = make_data(size)
        cfg = self.serve(data)
        cfg.delay = 0.02
        return data, cfg

    def test_cancel_removes_temp_and_no_output(self):
        self.slow_server()
        eng = self.engine()
        t = self.run_async(eng)
        self.assertTrue(wait_for(lambda: eng.task.downloaded > 200_000))
        eng.cancel()
        t.join(10)
        self.assertEqual(eng.task.status, TaskStatus.CANCELLED)
        self.assertEqual(self.temp_dirs(), [])
        self.assertFalse(eng.task.final_path)
        self.assertFalse((self.save_dir / "dosya.bin").exists())

    def test_pause_and_resume(self):
        data, _ = self.slow_server()
        eng = self.engine()
        t = self.run_async(eng)
        self.assertTrue(wait_for(lambda: eng.task.downloaded > 200_000))
        eng.pause()
        self.assertEqual(eng.task.status, TaskStatus.PAUSED)
        time.sleep(0.3)
        first = sum(p.done for p in eng._parts)
        time.sleep(0.4)
        second = sum(p.done for p in eng._parts)
        self.assertEqual(first, second, "duraklatılmışken veri inmemeli")
        self.assertEqual(eng.task.speed, 0)

        eng.resume()
        t.join(20)
        self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)
        self.assertEqual(eng.task.final_path.read_bytes(), data)

    def test_cancel_while_paused(self):
        self.slow_server()
        eng = self.engine()
        t = self.run_async(eng)
        self.assertTrue(wait_for(lambda: eng.task.downloaded > 200_000))
        eng.pause()
        time.sleep(0.2)
        eng.cancel()
        t.join(10)
        self.assertFalse(t.is_alive())
        self.assertEqual(eng.task.status, TaskStatus.CANCELLED)

    def test_cancel_before_start(self):
        self.serve(make_data(1000))
        eng = self.engine()
        eng.cancel()
        self.assertEqual(eng.task.status, TaskStatus.CANCELLED)


class ResumeTests(EngineTestCase):
    def test_resume_after_stop_downloads_only_remainder(self):
        data = make_data(3_000_000)
        cfg = self.serve(data)
        cfg.delay = 0.02

        eng1 = self.engine(connections=4)
        t = self.run_async(eng1)
        self.assertTrue(wait_for(lambda: eng1.task.downloaded > 900_000))
        eng1.stop()
        t.join(10)
        self.assertEqual(eng1.task.status, TaskStatus.PAUSED)
        self.assertTrue(self.temp_dirs(), "stop() temp'i korumalı")

        sent_before = cfg.bytes_sent
        cfg.delay = 0
        eng2 = self.engine(connections=4)
        eng2.start()
        self.assertEqual(eng2.task.status, TaskStatus.COMPLETED, eng2.task.error)
        self.assertEqual(eng2.task.final_path.read_bytes(), data)
        second_run = cfg.bytes_sent - sent_before
        self.assertLess(second_run, len(data) * 0.85, "yalnızca kalan kısım inmeliydi")

    def test_changed_file_on_server_restarts_from_scratch(self):
        old = make_data(2_000_000, seed=1)
        new = make_data(2_000_000, seed=2)
        cfg = self.serve(old)
        cfg.delay = 0.02

        eng1 = self.engine(connections=4)
        t = self.run_async(eng1)
        self.assertTrue(wait_for(lambda: eng1.task.downloaded > 500_000))
        eng1.stop()
        t.join(10)

        cfg.data = new
        cfg.etag = '"v2"'
        cfg.delay = 0
        eng2 = self.engine(connections=4)
        eng2.start()
        self.assertEqual(eng2.task.status, TaskStatus.COMPLETED, eng2.task.error)
        self.assertEqual(eng2.task.final_path.read_bytes(), new)

class StallTests(EngineTestCase):
    """Sunucu takılınca (veri göndermeyince) duraklat/iptal ANINDA tepki vermeli."""

    def stalled_download(self):
        data = make_data(3_000_000)
        cfg = self.serve(data)
        cfg.delay = 0.01
        eng = self.engine(connections=3)
        t = self.run_async(eng)
        self.assertTrue(wait_for(lambda: eng.task.downloaded > 100_000))
        cfg.delay = 30          # sunucu takılır: her bağlantı 30 sn sessiz kalır
        time.sleep(0.5)
        return eng, t

    def test_stop_is_prompt_when_server_stalls(self):
        eng, t = self.stalled_download()
        start = time.monotonic()
        eng.stop()
        t.join(10)
        self.assertFalse(t.is_alive())
        self.assertLess(time.monotonic() - start, 2.0)
        self.assertEqual(eng.task.status, TaskStatus.PAUSED)
        self.assertTrue(self.temp_dirs(), "stop() temp'i korumalı")

    def test_cancel_is_prompt_when_server_stalls(self):
        eng, t = self.stalled_download()
        start = time.monotonic()
        eng.cancel()
        t.join(10)
        self.assertFalse(t.is_alive())
        self.assertLess(time.monotonic() - start, 2.0)
        self.assertEqual(eng.task.status, TaskStatus.CANCELLED)
        self.assertEqual(self.temp_dirs(), [])

    def test_stop_and_cancel_never_block_under_worst_case_os_behaviour(self):
        """
        Windows'un en kötü davranışı taklidi: soket kapatma okumayı UYANDIRMIYOR ve
        response.close() okuma sürerken 3 sn bloke oluyor. stop()/cancel() yine de anında dönmeli.
        """
        real_close = requests.Response.close

        def blocking_close(self):
            time.sleep(3)
            return real_close(self)

        for action in ("stop", "cancel"):
            with self.subTest(action=action):
                self.setUp()
                try:
                    with mock.patch.object(HttpEngine, "_force_close", staticmethod(lambda r: None)), \
                            mock.patch.object(requests.Response, "close", blocking_close):
                        eng, t = self.stalled_download()
                        start = time.monotonic()
                        getattr(eng, action)()
                        call_time = time.monotonic() - start
                        t.join(10)
                        total = time.monotonic() - start
                    self.assertFalse(t.is_alive())
                    self.assertLess(call_time, 0.5, f"{action}() çağrısı bloke oldu")
                    self.assertLess(total, 2.0, f"{action} sonrası motor geç kapandı")
                    expected = TaskStatus.PAUSED if action == "stop" else TaskStatus.CANCELLED
                    self.assertEqual(eng.task.status, expected)
                finally:
                    self.tearDown()

    def test_cancel_during_probe_is_prompt(self):
        cfg = self.serve(make_data(100_000))
        cfg.header_delay = 30   # sunucu hiç cevap vermiyor
        eng = self.engine()
        t = self.run_async(eng)
        time.sleep(0.5)
        start = time.monotonic()
        eng.cancel()
        t.join(10)
        self.assertFalse(t.is_alive())
        self.assertLess(time.monotonic() - start, 2.0)
        self.assertEqual(eng.task.status, TaskStatus.CANCELLED)

    def test_stop_while_connecting_is_prompt(self):
        data = make_data(2_000_000)
        cfg = self.serve(data)
        eng = self.engine(connections=3)
        eng.start()                       # önce normal bir indirme: sunucu hazır
        cfg.header_delay = 30
        eng2 = self.engine(name="ikinci.bin", connections=3)
        eng2._probed = True               # sondalamayı atla, doğrudan parça bağlantılarında takıl
        eng2._supports_ranges = True
        eng2.task.file_size = len(data)
        eng2._etag = '"v1"'
        t = self.run_async(eng2)
        time.sleep(0.6)
        start = time.monotonic()
        eng2.stop()
        t.join(10)
        self.assertFalse(t.is_alive())
        self.assertLess(time.monotonic() - start, 2.0)


class CrashResumeTests(EngineTestCase):
    def test_resume_from_snapshot_taken_while_running(self):
        """Uygulama aniden ölürse: periyodik kaydedilmiş ilerlemeden doğru devam edilir."""
        data = make_data(3_000_000)
        cfg = self.serve(data)
        cfg.delay = 0.06
        eng1 = self.engine(connections=4)
        t = self.run_async(eng1)
        self.assertTrue(wait_for(lambda: eng1.task.downloaded > 900_000))
        time.sleep(0.3)                           # en az bir periyodik kayıt olsun

        temp = eng1._temp_dir
        snapshot = Path(self._tmp) / "snapshot"
        shutil.copytree(temp, snapshot)           # "çökme anındaki" disk durumu
        eng1.stop()
        t.join(10)
        shutil.rmtree(temp)
        shutil.copytree(snapshot, temp)           # ölümden sonra diskte kalan buydu

        cfg.delay = 0
        sent_before = cfg.bytes_sent
        eng2 = self.engine(connections=4)
        eng2.start()
        self.assertEqual(eng2.task.status, TaskStatus.COMPLETED, eng2.task.error)
        self.assertEqual(eng2.task.final_path.read_bytes(), data)
        self.assertLess(cfg.bytes_sent - sent_before, len(data) * 0.95,
                        "kaydedilmiş ilerleme kullanılmalıydı")

    def _stopped_midway(self):
        data = make_data(2_000_000)
        cfg = self.serve(data)
        cfg.delay = 0.06
        eng1 = self.engine(connections=4)
        t = self.run_async(eng1)
        self.assertTrue(wait_for(lambda: eng1.task.downloaded > 300_000))
        eng1.stop()
        t.join(10)
        self.assertTrue((eng1._temp_dir / "data.tmp").exists())
        return data, cfg, eng1

    def test_missing_data_file_with_saved_progress_restarts_cleanly(self):
        data, cfg, eng1 = self._stopped_midway()
        (eng1._temp_dir / "data.tmp").unlink()         # ilerleme kayıtlı ama veri yok
        cfg.delay = 0
        sent_before = cfg.bytes_sent
        eng2 = self.engine(connections=4)
        eng2.start()
        self.assertEqual(eng2.task.status, TaskStatus.COMPLETED, eng2.task.error)
        self.assertEqual(eng2.task.final_path.read_bytes(), data)
        self.assertGreaterEqual(cfg.bytes_sent - sent_before, len(data) * 0.9, "baştan inmeliydi")

    def test_truncated_data_file_with_saved_progress_restarts_cleanly(self):
        data, cfg, eng1 = self._stopped_midway()
        with open(eng1._temp_dir / "data.tmp", "r+b") as f:
            f.truncate(10)                              # veri dosyası ilerlemeyi karşılamıyor
        cfg.delay = 0
        sent_before = cfg.bytes_sent
        eng2 = self.engine(connections=4)
        eng2.start()
        self.assertEqual(eng2.task.status, TaskStatus.COMPLETED, eng2.task.error)
        self.assertEqual(eng2.task.final_path.read_bytes(), data)
        self.assertGreaterEqual(cfg.bytes_sent - sent_before, len(data) * 0.9, "baştan inmeliydi")

    def test_corrupt_progress_state_restarts_cleanly(self):
        data = make_data(2_000_000)
        cfg = self.serve(data)
        cfg.delay = 0.06
        eng1 = self.engine(connections=4)
        t = self.run_async(eng1)
        self.assertTrue(wait_for(lambda: eng1.task.downloaded > 300_000))
        eng1.stop()
        t.join(10)
        state_file = eng1._temp_dir / "state.json"
        state_file.write_text('{"version": 2, "progress": "bozuk"}', encoding="utf-8")

        cfg.delay = 0
        eng2 = self.engine(connections=4)
        eng2.start()
        self.assertEqual(eng2.task.status, TaskStatus.COMPLETED, eng2.task.error)
        self.assertEqual(eng2.task.final_path.read_bytes(), data)

        
class WebPageDetectionTests(EngineTestCase):
    """HTML dönen adres (web sayfası) dosya sanılıp .html olarak inmemeli; anlaşılır hata verilmeli."""

    def nothing_left_behind(self):
        self.assertEqual(self.temp_dirs(), [])
        self.assertTrue(not self.save_dir.exists() or not any(self.save_dir.iterdir()))

    def test_html_page_is_rejected_with_clear_message(self):
        cfg = self.serve(b"<html><body>giris yap</body></html>")
        for ctype in ("text/html", "text/html; charset=utf-8", "TEXT/HTML", "application/xhtml+xml"):
            with self.subTest(content_type=ctype):
                cfg.content_type = ctype
                eng = self.engine()
                eng.start()
                self.assertEqual(eng.task.status, TaskStatus.ERROR)
                self.assertIn("web sayfası", eng.task.error)
                self.nothing_left_behind()

    def test_html_page_is_rejected_when_server_has_no_range_support(self):
        cfg = self.serve(b"<html></html>")
        cfg.content_type = "text/html"
        cfg.support_ranges = False
        eng = self.engine()
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.ERROR)
        self.assertIn("web sayfası", eng.task.error)
        self.nothing_left_behind()

    def test_real_html_file_by_url_extension_is_downloaded(self):
        data = b"<html><body>gercek bir html dosyasi</body></html>"
        cfg = self.serve(data)
        cfg.content_type = "text/html"
        self.url = self.url.replace("dosya.bin", "sayfa.html")
        eng = self.engine()
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)
        self.assertEqual(eng.task.final_path.name, "sayfa.html")
        self.assertEqual(eng.task.final_path.read_bytes(), data)

    def test_html_with_attachment_header_is_downloaded(self):
        data = b"<html>rapor</html>"
        cfg = self.serve(data)
        cfg.content_type = "text/html"
        cfg.content_disposition = 'attachment; filename="rapor.html"'
        eng = self.engine()
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)
        self.assertEqual(eng.task.final_path.name, "rapor.html")
        self.assertEqual(eng.task.final_path.read_bytes(), data)

    def test_other_content_types_are_unaffected(self):
        data = make_data(30_000)
        cfg = self.serve(data)
        for ctype in ("text/plain", "application/zip", "application/octet-stream"):
            with self.subTest(content_type=ctype):
                cfg.content_type = ctype
                eng = self.engine(name=f"{ctype.replace('/', '_')}.bin")
                eng.start()
                self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)
                self.assertEqual(eng.task.final_path.read_bytes(), data)


class HttpErrorMessageTests(EngineTestCase):
    """Sunucu hata kodu dönünce ham "HTTP 401" yerine anlaşılır mesaj gösterilmeli (kod yine de görünür)."""

    def test_error_codes_get_clear_messages(self):
        cfg = self.serve(make_data(1000))
        cases = (
            (401, "izni"), (403, "reddetti"), (404, "bulunamadı"), (410, "mevcut değil"),
            (429, "bekleyip"), (500, "Sunucu hatası"), (503, "Sunucu hatası"), (418, "Sunucu hata döndürdü"),
        )
        for code, keyword in cases:
            with self.subTest(code=code):
                cfg.error_status = code
                eng = self.engine()
                eng.start()
                self.assertEqual(eng.task.status, TaskStatus.ERROR)
                self.assertIn(f"HTTP {code}", eng.task.error)
                self.assertIn(keyword, eng.task.error)
                self.assertEqual(self.temp_dirs(), [])


class CategorySortTests(EngineTestCase):
    """sort_by_category seçeneği açıksa biten dosya, indirme klasörünün altındaki kategori klasörüne gider."""

    def finished(self, name, options):
        eng = self.engine(name=name)
        eng.task.options = options
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)
        return eng

    def test_file_goes_into_category_folder(self):
        data = make_data(30_000)
        self.serve(data)
        eng = self.finished("film.mkv", {"sort_by_category": True})
        self.assertEqual(eng.task.final_path, self.save_dir / "Videolar" / "film.mkv")
        self.assertEqual(eng.task.final_path.read_bytes(), data)
        self.assertEqual(self.temp_dirs(), [])

    def test_without_option_file_stays_in_root(self):
        self.serve(make_data(30_000))
        eng = self.finished("film.mkv", {})
        self.assertEqual(eng.task.final_path, self.save_dir / "film.mkv")

    def test_unknown_type_goes_to_other_folder(self):
        self.serve(make_data(30_000))
        eng = self.finished("x.bilinmeyen", {"sort_by_category": True})
        self.assertEqual(eng.task.final_path.parent, self.save_dir / "Diğer")

    def test_name_collision_inside_category_folder(self):
        data = make_data(30_000)
        self.serve(data)
        (self.save_dir / "Videolar").mkdir(parents=True)
        (self.save_dir / "Videolar" / "film.mkv").write_bytes(b"eski")
        eng = self.finished("film.mkv", {"sort_by_category": True})
        self.assertEqual((self.save_dir / "Videolar" / "film.mkv").read_bytes(), b"eski")
        self.assertEqual(eng.task.final_path.name, "film (1).mkv")
        self.assertEqual(eng.task.final_path.read_bytes(), data)


class SourceHintTests(EngineTestCase):
    """Bilinen paylaşım sitesinden gelen görevlerde erişim hatalarına siteye özel ipucu eklenir."""
    HINT = "Bağlantıya sahip olan herkes"

    def test_hint_is_added_to_access_errors(self):
        cfg = self.serve(make_data(1000))
        for code in (401, 403, 404, 429):
            with self.subTest(code=code):
                cfg.error_status = code
                eng = self.engine()
                eng.task.options = {"source_hint": "Google Drive"}
                eng.start()
                self.assertEqual(eng.task.status, TaskStatus.ERROR)
                self.assertIn(f"HTTP {code}", eng.task.error)
                self.assertIn(self.HINT, eng.task.error)

    def test_hint_is_added_to_web_page_error(self):
        cfg = self.serve(b"<html></html>")
        cfg.content_type = "text/html"
        eng = self.engine()
        eng.task.options = {"source_hint": "Google Drive"}
        eng.start()
        self.assertIn("web sayfası", eng.task.error)
        self.assertIn(self.HINT, eng.task.error)

    def test_no_hint_for_server_errors_unknown_sources_or_plain_tasks(self):
        cfg = self.serve(make_data(1000))
        cases = ((503, {"source_hint": "Google Drive"}), (401, {"source_hint": "Dropbox"}), (401, {}))
        for code, options in cases:
            with self.subTest(code=code, options=options):
                cfg.error_status = code
                eng = self.engine()
                eng.task.options = options
                eng.start()
                self.assertEqual(eng.task.status, TaskStatus.ERROR)
                self.assertNotIn(self.HINT, eng.task.error)


class StrainHttpEngine(FastHttpEngine):
    READ_TIMEOUT = 0.5      # yanıtsız bırakan sunucuyu çabuk fark et
    MAX_RETRIES = 3


class ConnectionLimitTests(EngineTestCase):
    """Sunucu çok bağlantıdan zorlanınca eşzamanlı bağlantı sınırı düşer ve indirme hata vermeden biter."""

    def strain_engine(self, connections=8):
        task = DownloadTask(url=self.url, save_dir=self.save_dir)
        return StrainHttpEngine(task, None, connections, None)

    def host_key(self):
        return urlparse(self.url).netloc.lower()

    def test_overloaded_server_503_lowers_connections_and_completes(self):
        data = make_data(3_000_000)
        cfg = self.serve(data)
        cfg.max_connections = 2
        cfg.delay = 0.02
        eng = self.strain_engine(8)
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)
        self.assertEqual(eng.task.final_path.read_bytes(), data)
        self.assertLess(eng._gate.limit, 8)
        self.assertLessEqual(cfg.peak_active, 2)
        self.assertGreater(cfg.overloaded, 0)
        self.assertLess(http_engine._HOST_LIMITS[self.host_key()], 8, "öğrenilen sınır hatırlanmalı")
        self.assertEqual(self.temp_dirs(), [])

    def test_server_that_stalls_extra_connections_is_handled(self):
        data = make_data(1_500_000)
        cfg = self.serve(data)
        cfg.max_connections = 2
        cfg.overload_mode = "stall"
        cfg.delay = 0.01
        eng = self.strain_engine(8)
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)
        self.assertEqual(eng.task.final_path.read_bytes(), data)
        self.assertLess(eng._gate.limit, 8)

    def test_single_part_dropout_does_not_lower_connections(self):
        data = make_data(2_000_000)
        cfg = self.serve(data)
        cfg.drop_first_n = 1
        eng = self.strain_engine(4)
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)
        self.assertEqual(eng.task.final_path.read_bytes(), data)
        self.assertEqual(eng._gate.limit, len(eng._parts))
        self.assertNotIn(self.host_key(), http_engine._HOST_LIMITS)

    def test_healthy_server_keeps_all_connections(self):
        data = make_data(2_000_000)
        self.serve(data)
        eng = self.strain_engine(4)
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)
        self.assertEqual(eng._gate.limit, len(eng._parts))
        self.assertNotIn(self.host_key(), http_engine._HOST_LIMITS)

    def test_remembered_limit_is_used_by_next_download_from_same_host(self):
        data = make_data(2_000_000)
        cfg = self.serve(data)
        cfg.max_connections = 2
        http_engine._HOST_LIMITS[self.host_key()] = 2
        eng = self.strain_engine(8)
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)
        self.assertEqual(eng.task.final_path.read_bytes(), data)
        self.assertEqual(eng._gate.limit, 2)
        self.assertEqual(cfg.overloaded, 0, "öğrenilen sınırla başlayınca hiç reddedilmemeli")
        self.assertLessEqual(cfg.peak_active, 2)

    def test_stop_and_cancel_are_prompt_while_parts_wait_at_the_gate(self):
        for action in ("stop", "cancel"):
            with self.subTest(action=action):
                self.setUp()
                try:
                    cfg = self.serve(make_data(3_000_000))
                    cfg.delay = 0.05
                    http_engine._HOST_LIMITS[self.host_key()] = 1     # 6 parçanın 5'i kapıda bekler
                    eng = self.strain_engine(6)
                    t = self.run_async(eng)
                    self.assertTrue(wait_for(lambda: eng.task.downloaded > 100_000))
                    start = time.monotonic()
                    getattr(eng, action)()
                    t.join(10)
                    self.assertFalse(t.is_alive())
                    self.assertLess(time.monotonic() - start, 2.0)
                    if action == "stop":
                        self.assertEqual(eng.task.status, TaskStatus.PAUSED)
                        self.assertTrue(self.temp_dirs(), "stop() temp'i korumalı")
                    else:
                        self.assertEqual(eng.task.status, TaskStatus.CANCELLED)
                        self.assertEqual(self.temp_dirs(), [])
                finally:
                    self.tearDown()

    def test_resume_after_connection_limit_was_lowered(self):
        data = make_data(3_000_000)
        cfg = self.serve(data)
        cfg.max_connections = 2
        cfg.delay = 0.02
        eng1 = self.strain_engine(8)
        t = self.run_async(eng1)
        self.assertTrue(wait_for(lambda: eng1._gate is not None and eng1._gate.limit < 8
                                 and eng1.task.downloaded > 300_000, 20))
        eng1.stop()
        t.join(10)
        self.assertEqual(eng1.task.status, TaskStatus.PAUSED)
        self.assertTrue(self.temp_dirs())

        cfg.delay = 0
        self.assertTrue(wait_for(lambda: cfg.active == 0), "eski bağlantılar sunucuda kapanmalı")
        eng2 = self.strain_engine(8)
        eng2.start()
        self.assertEqual(eng2.task.status, TaskStatus.COMPLETED, eng2.task.error)
        self.assertEqual(eng2.task.final_path.read_bytes(), data)

    def test_persistent_failures_still_end_in_error_at_one_connection(self):
        cfg = self.serve(make_data(1_500_000))
        cfg.part_status = 503
        eng = self.strain_engine(4)
        eng.start()
        self.assertEqual(eng.task.status, TaskStatus.ERROR)
        self.assertIn("503", eng.task.error)
        self.assertEqual(eng._gate.limit, 1)


class RateAndProgressTests(EngineTestCase):
    def test_speed_limit_is_respected(self):
        data = make_data(300_000)
        self.serve(data)
        eng = self.engine(connections=2, limiter=RateLimiter(150_000))
        start = time.monotonic()
        eng.start()
        elapsed = time.monotonic() - start
        self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)
        self.assertGreater(elapsed, 1.3, f"sınır uygulanmadı ({elapsed:.2f}s)")
        self.assertEqual(eng.task.final_path.read_bytes(), data)

    def test_progress_is_throttled(self):
        cfg = self.serve(make_data(3_000_000))
        cfg.delay = 0.02
        count = []
        cb = EngineCallbacks(on_progress=lambda t: count.append(time.monotonic()))
        eng = self.engine(connections=4, callbacks=cb)
        start = time.monotonic()
        eng.start()
        duration = time.monotonic() - start
        self.assertEqual(eng.task.status, TaskStatus.COMPLETED, eng.task.error)
        self.assertLessEqual(len(count), duration * 4 + 4)

    def test_status_sequence(self):
        self.serve(make_data(20_000))
        seen = []
        cb = EngineCallbacks(on_status=lambda t: seen.append(t.status))
        eng = self.engine(callbacks=cb)
        eng.start()
        self.assertEqual(
            seen,
            [TaskStatus.PROBING, TaskStatus.DOWNLOADING, TaskStatus.COMPLETED],
        )


class RateLimiterTests(unittest.TestCase):
    def test_unlimited_does_not_block(self):
        rl = RateLimiter(0)
        start = time.monotonic()
        for _ in range(1000):
            rl.acquire(1_000_000)
        self.assertLess(time.monotonic() - start, 0.5)

    def test_limited_blocks(self):
        rl = RateLimiter(100_000)
        start = time.monotonic()
        for _ in range(5):
            rl.acquire(40_000)  # toplam 200 KB @ 100 KB/s
        self.assertGreater(time.monotonic() - start, 1.0)


if __name__ == "__main__":
    unittest.main()