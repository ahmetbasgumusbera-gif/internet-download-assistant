"""
Videoyu, kullanıcı İndir'e basmadan ÖNCE arka planda analiz eder (Qt yok).

Tarayıcı eklentisi bir YouTube videosu açılınca start() çağırır; sonuç kısa süre saklanır. Kullanıcı sonradan İndir'e
basınca YouTube sekmesi analyze_cached() ile sonucu anında alır; analiz hâlâ sürüyorsa yeniden başlamadan bitmesini bekler.
Videodan çıkılınca / başka videoya geçilince eklenti cancel() der ve çalışan analiz süreci öldürülür.
Kullanıcı uygulamada o analizi "sahiplendiyse" (İndir'e bastıysa) iptal isteği onu durdurmaz.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from core import youtube
from core.youtube import AnalysisCancelled, AnalysisError, VideoInfo
from utils.logger import get_logger

log = get_logger(__name__)

TTL = 20 * 60          # sonuç bu kadar saniye geçerli sayılır
MAX_JOBS = 16
WAIT_LIMIT = 200       # sahiplenilen analizi en fazla bu kadar saniye bekle


class _Job:
    def __init__(self, vid: str, url: str):
        self.vid = vid
        self.url = url
        self.cancel = threading.Event()
        self.done = threading.Event()
        self.claimed = False             # kullanıcı uygulamada İndir'e bastı: artık iptal edilmez
        self.cancelled = False
        self.info: Optional[VideoInfo] = None
        self.error = ""
        self.finished = 0.0


class Prefetcher:
    def __init__(self, analyze_fn: Optional[Callable] = None):
        self._analyze = analyze_fn
        self._lock = threading.RLock()
        self._jobs: dict[str, _Job] = {}
        self._current = ""

    # ------------------------------------------------------------------ yardımcılar
    @staticmethod
    def _fresh(job: _Job) -> bool:
        return job.info is not None and time.monotonic() - job.finished <= TTL

    def _cancel_running_locked(self, except_vid: str = "") -> None:
        for vid, job in self._jobs.items():
            if vid != except_vid and not job.done.is_set() and not job.claimed:
                job.cancel.set()

    def _prune_locked(self) -> None:
        now = time.monotonic()
        for vid in [v for v, j in self._jobs.items() if j.done.is_set() and now - j.finished > TTL]:
            del self._jobs[vid]
        finished = sorted((j for j in self._jobs.values() if j.done.is_set()), key=lambda j: j.finished)
        while len(self._jobs) >= MAX_JOBS and finished:
            self._jobs.pop(finished.pop(0).vid, None)

    # ------------------------------------------------------------------ eklentiden gelen komutlar
    def start(self, url: str) -> bool:
        """Videoyu arka planda analiz etmeye başlar (zaten çalışıyor/hazırsa dokunmaz). Başlatılabildiyse True."""
        vid = youtube.video_id(url)
        if not vid or not youtube.is_youtube_url(url):
            return False
        with self._lock:
            job = self._jobs.get(vid)
            if job is not None and not job.cancel.is_set() and (not job.done.is_set() or self._fresh(job)):
                self._current = vid
                return True
            self._cancel_running_locked(except_vid=vid)    # başka videonun analizi artık gereksiz
            self._prune_locked()
            job = _Job(vid, url)
            self._jobs[vid] = job
            self._current = vid
        threading.Thread(target=self._run, args=(job,), daemon=True, name=f"prefetch-{vid}").start()
        return True

    def cancel(self, vid: str = "") -> None:
        """vid verilen (boşsa şu anki) analizi iptal eder; kullanıcının sahiplendiği analize dokunmaz."""
        with self._lock:
            target = vid or self._current
            job = self._jobs.get(target)
            if target == self._current:
                self._current = ""
        if job is not None and not job.done.is_set() and not job.claimed:
            job.cancel.set()

    def cancel_all(self) -> None:
        """Uygulama kapanırken: sahiplenilenler dahil tüm çalışan analizleri durdurur."""
        with self._lock:
            jobs = list(self._jobs.values())
            self._current = ""
        for job in jobs:
            if not job.done.is_set():
                job.cancel.set()

    def state(self, vid: str) -> str:
        """none | running | done | error"""
        with self._lock:
            job = self._jobs.get(vid)
        if job is None:
            return "none"
        if not job.done.is_set():
            return "none" if job.cancel.is_set() else "running"
        if job.info is not None and self._fresh(job):
            return "done"
        return "error" if job.error else "none"

    # ------------------------------------------------------------------ uygulamanın (YouTube sekmesi) kullanımı
    def take(self, url: str) -> Optional[VideoInfo]:
        """
        Önceden analiz edilmiş/ediliyor olan videonun bilgisini verir. Sürüyorsa bitmesini bekler (iptal edilemez hâle gelir).
        Yok / iptal edilmiş ise None; analiz hata verdiyse AnalysisError fırlatır (hata bir kez tüketilir).
        """
        vid = youtube.video_id(url)
        if not vid:
            return None
        with self._lock:
            job = self._jobs.get(vid)
            if job is None or (job.cancel.is_set() and not job.done.is_set()):
                return None
            job.claimed = True
        job.done.wait(WAIT_LIMIT)
        if not job.done.is_set() or job.cancelled:
            return None
        if job.info is not None:
            return job.info if self._fresh(job) else None
        if job.error:
            with self._lock:
                if self._jobs.get(vid) is job:
                    del self._jobs[vid]
            raise AnalysisError(job.error)
        return None

    # ------------------------------------------------------------------ arka plan işi
    def _run(self, job: _Job) -> None:
        analyze = self._analyze or youtube.analyze
        try:
            job.info = analyze(job.url, cancel_event=job.cancel)
        except AnalysisCancelled:
            job.cancelled = True
        except AnalysisError as e:
            job.error = str(e)
        except Exception as e:  # beklenmeyen: uygulama asla etkilenmesin
            log.exception("Önceden analiz başarısız")
            job.error = f"Analiz başarısız: {e}"
        finally:
            job.finished = time.monotonic()
            job.done.set()
            if job.cancelled:
                with self._lock:
                    if self._jobs.get(job.vid) is job:
                        del self._jobs[job.vid]


PREFETCH = Prefetcher()


def analyze_cached(url: str, playlist_first: bool = False) -> VideoInfo:
    """YouTube sekmesinin analiz işlevi: önceden analiz varsa onu kullanır, yoksa normal analiz yapar."""
    if not playlist_first:
        info = PREFETCH.take(url)
        if info is not None:
            return info
    return youtube.analyze(url, playlist_first=playlist_first)
