"""Önceden (arka planda) analiz testleri: gerçek yt-dlp yok, sahte analiz işleviyle."""
import threading
import time
import unittest

from core import youtube
from core.prefetch import Prefetcher
from core.youtube import AnalysisCancelled, AnalysisError
from tests.yt_samples import sample_video_info

URL_A = "https://www.youtube.com/watch?v=aaaaaaaaaaa"
URL_B = "https://www.youtube.com/watch?v=bbbbbbbbbbb"


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


class VideoIdTests(unittest.TestCase):
    def test_ids(self):
        self.assertEqual(youtube.video_id("https://www.youtube.com/watch?v=abc123&list=PL1"), "abc123")
        self.assertEqual(youtube.video_id("https://youtu.be/abc123?t=5"), "abc123")
        self.assertEqual(youtube.video_id("https://www.youtube.com/shorts/abc123"), "abc123")
        self.assertEqual(youtube.video_id("https://www.youtube.com/playlist?list=PL1"), "")
        self.assertEqual(youtube.video_id("https://example.com/x"), "")
        self.assertEqual(youtube.video_id(""), "")


class PrefetchTests(unittest.TestCase):
    def make(self, error=""):
        self.gate = threading.Event()
        self.calls = []

        def fake(url, cancel_event=None, **kw):
            self.calls.append(url)
            while not self.gate.is_set():
                if cancel_event is not None and cancel_event.is_set():
                    raise AnalysisCancelled()
                time.sleep(0.01)
            if error:
                raise AnalysisError(error)
            return sample_video_info()

        return Prefetcher(fake)

    def tearDown(self):
        self.gate.set()

    def test_non_youtube_or_playlist_is_not_started(self):
        p = self.make()
        self.assertFalse(p.start("https://example.com/watch?v=abc"))
        self.assertFalse(p.start("https://www.youtube.com/playlist?list=PL1"))
        self.assertEqual(self.calls, [])

    def test_running_then_done(self):
        p = self.make()
        self.assertTrue(p.start(URL_A))
        self.assertTrue(wait_for(lambda: self.calls))
        self.assertEqual(p.state("aaaaaaaaaaa"), "running")
        self.gate.set()
        self.assertTrue(wait_for(lambda: p.state("aaaaaaaaaaa") == "done"))
        self.assertEqual(p.take(URL_A).title, "Örnek video başlığı")
        self.assertEqual(len(self.calls), 1, "hazır sonuç için ikinci analiz başlatılmamalı")

    def test_same_video_is_not_started_twice(self):
        p = self.make()
        p.start(URL_A)
        p.start(URL_A)
        time.sleep(0.1)
        self.assertEqual(len(self.calls), 1)

    def test_new_video_cancels_the_old_one(self):
        p = self.make()
        p.start(URL_A)
        self.assertTrue(wait_for(lambda: self.calls))
        p.start(URL_B)
        self.assertTrue(wait_for(lambda: p.state("aaaaaaaaaaa") == "none"))
        self.assertEqual(p.state("bbbbbbbbbbb"), "running")

    def test_cancel_by_id_and_current(self):
        p = self.make()
        p.start(URL_A)
        self.assertTrue(wait_for(lambda: self.calls))
        p.cancel("bbbbbbbbbbb")                    # başka videonun iptali dokunmamalı
        self.assertEqual(p.state("aaaaaaaaaaa"), "running")
        p.cancel("aaaaaaaaaaa")
        self.assertTrue(wait_for(lambda: p.state("aaaaaaaaaaa") == "none"))

    def test_claimed_analysis_survives_cancel(self):
        p = self.make()
        p.start(URL_A)
        self.assertTrue(wait_for(lambda: self.calls))
        result = {}
        t = threading.Thread(target=lambda: result.update(info=p.take(URL_A)), daemon=True)
        t.start()
        time.sleep(0.1)                            # uygulama analizi sahiplendi (İndir'e basıldı)
        p.cancel("aaaaaaaaaaa")
        p.start(URL_B)                             # başka videoya geçildi
        time.sleep(0.1)
        self.assertEqual(p.state("aaaaaaaaaaa"), "running")
        self.gate.set()
        t.join(5)
        self.assertIsNotNone(result.get("info"))

    def test_error_is_reported_once(self):
        p = self.make(error="Video kullanılamıyor.")
        p.start(URL_A)
        self.gate.set()
        self.assertTrue(wait_for(lambda: p.state("aaaaaaaaaaa") == "error"))
        with self.assertRaises(AnalysisError):
            p.take(URL_A)
        self.assertIsNone(p.take(URL_A))           # hata tüketildi: normal analize düşülür

    def test_cancel_all_stops_claimed_too(self):
        p = self.make()
        p.start(URL_A)
        self.assertTrue(wait_for(lambda: self.calls))
        t = threading.Thread(target=lambda: p.take(URL_A), daemon=True)
        t.start()
        time.sleep(0.1)
        p.cancel_all()
        t.join(5)
        self.assertFalse(t.is_alive())


if __name__ == "__main__":
    unittest.main()
