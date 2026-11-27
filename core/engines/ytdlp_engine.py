"""
yt-dlp tabanlı indirme motoru (YouTube).

yt-dlp ALT SÜREÇ olarak çalışır (python -m yt_dlp). Bu sayede:
  * Duraklat/iptal süreci (ve ffmpeg alt sürecini) anında öldürerek yapılır; Windows'ta bile bloke olmaz.
  * Duraklatılan indirme, yt-dlp'nin .part dosyalarından kaldığı yerden sürer (geçici klasör korunur).
  * Birleştirme (video+ses), küçük resim gömme gibi son işlemler MERGING durumu olarak görünür.

Motor, görevin options sözlüğünden şunları okur:
  args           : core.youtube.build_args çıktısı (format seçimi, birleştirme, küçük resim vb.)
  expected_size  : analizden gelen tahmini toplam bayt (ilerleme yüzdesi için)
  playlist_item  : True ise oynatma listesinin yalnızca ilk videosu
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

from core import procutil, webvideo, ytauth
from core.engines.base import BaseEngine
from core.engines.http_engine import DownloadError, move_error_message
from core.events import EngineCallbacks, ProgressThrottle
from core.models import DownloadTask, TaskStatus
from core.ratelimit import RateLimiter
from core.youtube import (
    friendly_error, heal_ytdlp, is_youtube_url, js_runtime_args, should_heal, ytdlp_command,
)
from utils import paths
from utils.helpers import category_folder, hide_on_windows, reserve_unique_path
from utils.logger import get_logger

log = get_logger(__name__)

_PROGRESS_TEMPLATE = (
    "download:IDAP|%(progress.status)s|%(progress.downloaded_bytes)s|%(progress.total_bytes)s"
    "|%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s|%(info.format_id)s"
)
_POSTPROCESS_TEMPLATE = "postprocess:IDAPP|%(progress.status)s|%(progress.postprocessor)s"


class _Halted(Exception):
    pass


def _num(text: str) -> Optional[float]:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


class YtdlpEngine(BaseEngine):
    name = "ytdlp"
    _skip_info = False         # True: analiz dosyası kullanılmaz (sayfa yeniden çözümlenir)

    # Testlerde değiştirilir (eklenti dizini, ffmpeg konumu vb.)
    EXTRA_ARGS: tuple = ()
    FFMPEG_LOCATION: Optional[str] = None

    def __init__(
        self,
        task: DownloadTask,
        callbacks: Optional[EngineCallbacks] = None,
        connections: int = 8,
        limiter: Optional[RateLimiter] = None,
    ):
        super().__init__(task, callbacks)
        self.connections = max(1, min(int(connections), 8))
        self.limiter = limiter or RateLimiter(0)

        self._lock = threading.RLock()
        self._halt = threading.Event()
        self._halt_reason: Optional[str] = None   # "cancel" | "stop"
        self._proc: Optional[subprocess.Popen] = None
        self._running = False

        self._streams: dict[str, tuple[float, float]] = {}   # format_id -> (inen, toplam)
        self._finished_streams = 0
        self._speed = 0.0
        self._final_file: Optional[str] = None
        self._throttle = ProgressThrottle(0.25)

        self._temp_dir = paths.temp_root(task.save_dir) / f"yt-{task.id}"

    # ------------------------------------------------------------------ sözleşme
    @classmethod
    def can_handle(cls, url: str) -> bool:
        return False   # yalnızca görevin motor adına göre (ör. YouTube sekmesinden) seçilir

    def probe(self) -> None:
        pass           # bilgi analiz aşamasında zaten alındı

    def start(self) -> None:
        self._running = True
        try:
            self._set_status(TaskStatus.PROBING)
            self._check_halt()
            self._temp_dir.parent.mkdir(parents=True, exist_ok=True)
            hide_on_windows(self._temp_dir.parent)
            self._temp_dir.mkdir(parents=True, exist_ok=True)

            expected = self.task.options.get("expected_size")
            if expected and not self.task.file_size:
                self.task.file_size = int(expected)

            tail = self._run_process()
            if getattr(self, "_exit_code", 1) != 0 and self._info_file() and not self._halt.is_set():
                log.warning("Analiz dosyasıyla indirme başarısız; sayfa yeniden çözümlenerek denenecek")
                self._skip_info = True
                self._streams.clear()
                self._finished_streams = 0
                tail = self._run_process()
            self._check_halt()
            if getattr(self, "_exit_code", 1) != 0 and should_heal("\n".join(tail)) and heal_ytdlp():
                log.warning("yt-dlp güncellendi; indirme yeniden deneniyor")
                self._skip_info = True
                self._streams.clear()
                self._finished_streams = 0
                tail = self._run_process()
                self._check_halt()
            self._finalize(tail)
        except _Halted:
            self._finish_halted()
        except DownloadError as e:
            self._fail_status(str(e))
        except Exception as e:  # beklenmeyen hata uygulamayı çökertmesin
            log.exception("yt-dlp motorunda beklenmeyen hata")
            self._fail_status(f"Beklenmeyen hata: {e}")
        finally:
            self._running = False
            self._proc = None

    def pause(self) -> None:
        self.stop()    # yt-dlp için "geçici duraklatma" yok; yönetici stop() + yeniden başlatma kullanır

    def resume(self) -> None:
        pass

    def cancel(self) -> None:
        if self.task.status.is_finished:
            return
        self._request_halt("cancel")
        if not self._running:
            self._discard_temp()
            self.task.speed = 0.0
            self._set_status(TaskStatus.CANCELLED)

    def stop(self) -> None:
        if self.task.status.is_finished:
            return
        self._request_halt("stop")

    def discard_partial(self) -> None:
        self._discard_temp()

    # ------------------------------------------------------------------ süreç
    def _build_command(self) -> list[str]:
        options = self.task.options
        cmd = ytdlp_command(
            "--newline", "--no-colors", "--progress",
            "--progress-template", _PROGRESS_TEMPLATE,
            "--progress-template", _POSTPROCESS_TEMPLATE,
            "--print", "after_move:IDAFILE|%(filepath)s", "--no-simulate",
            "-P", str(self._temp_dir),
            "-o", "%(title).150B.%(ext)s",
            "--windows-filenames", "--no-mtime",
            "--socket-timeout", "15", "--retries", "10", "--fragment-retries", "10",
            "-N", str(self.connections),
        )
        cmd += ["--playlist-items", "1"] if options.get("playlist_item") else ["--no-playlist"]

        rate = self.limiter.rate
        if rate > 0:
            cmd += ["--limit-rate", str(rate)]

        ffmpeg = self.FFMPEG_LOCATION or (str(paths.get_ffmpeg_dir()) if paths.get_ffmpeg_dir() else "")
        if ffmpeg:
            cmd += ["--ffmpeg-location", ffmpeg]

        cmd += js_runtime_args()
        cmd += self._auth_args()
        cmd += list(self.EXTRA_ARGS)
        cmd += list(options.get("args") or [])
        info_file = self._info_file()
        if info_file:
            cmd += ["--load-info-json", info_file]       # sayfa ikinci kez çözümlenmez
        else:
            cmd.append(self.task.url)
        return cmd

    def _info_file(self) -> str:
        """Analizden kalan bilgi dosyası kullanılabilirse yolu (en çok 4 saatlik: adresler süresi dolar), yoksa boş."""
        if self._skip_info:
            return ""
        options = self.task.options
        path = str(options.get("info_file") or "")
        if not path or options.get("generic") or options.get("playlist_item"):
            return ""
        try:
            file = Path(path)
            if file.is_file() and time.time() - file.stat().st_mtime < 4 * 3600:
                return path
        except OSError:
            pass
        return ""

    def _auth_args(self) -> list[str]:
        """YouTube adresleri için tarayıcıdan aktarılan oturum çerezleri (üyelere özel / özel / yaş sınırlı videolar)."""
        if self.task.options.get("generic") or not is_youtube_url(self.task.url):
            return []
        return ytauth.cookie_args()

    def _run_process(self) -> list[str]:
        cmd = self._build_command()
        log.info("yt-dlp başlatılıyor: %s", self.task.url)
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", bufsize=1, **procutil.popen_kwargs(),
            )
        except OSError as e:
            raise DownloadError(f"yt-dlp çalıştırılamadı: {e}")

        with self._lock:
            self._proc = proc
            halted = self._halt.is_set()
        if halted:
            procutil.kill_tree(proc)

        tail: deque[str] = deque(maxlen=80)
        try:
            for raw in proc.stdout:           # süreç ölünce (durdurma) akış biter
                line = raw.rstrip("\r\n")
                if line:
                    tail.append(line)
                    self._handle_line(line)
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass
            code = proc.wait()
        self._exit_code = code
        return list(tail)

    # ------------------------------------------------------------------ çıktı ayrıştırma
    def _handle_line(self, line: str) -> None:
        if line.startswith("IDAP|"):
            self._on_download_progress(line.split("|"))
        elif line.startswith("IDAPP|"):
            parts = line.split("|")
            # yt-dlp indirmeden ÖNCE de bazı ön işlemleri "başladı" diye bildirir; birleştirme/son işlem
            # yalnızca en az bir akış tamamen indikten sonra başlamış sayılır.
            if (len(parts) >= 3 and parts[1] == "started" and self._finished_streams >= 1
                    and self.task.status != TaskStatus.MERGING):
                self._mark_streams_complete()
                self._set_status(TaskStatus.MERGING)
                self._report(force=True)
        elif line.startswith("IDAFILE|"):
            self._final_file = line.split("|", 1)[1].strip()
        elif line.startswith("WARNING"):
            log.warning("yt-dlp: %s", line)

    def _on_download_progress(self, parts: list[str]) -> None:
        if len(parts) < 8:
            return
        _, status, downloaded, total, estimate, speed, _eta, fmt = parts[:8]
        done = _num(downloaded) or 0.0
        size = _num(total) or _num(estimate) or 0.0
        with self._lock:
            self._streams[fmt] = (done, max(size, done))
            self._speed = _num(speed) or 0.0
        if self.task.status in (TaskStatus.PROBING, TaskStatus.PAUSED):
            self._set_status(TaskStatus.DOWNLOADING)
        if status == "finished":
            self._finished_streams += 1
            self._report(force=True)
        else:
            self._report()

    def _mark_streams_complete(self) -> None:
        with self._lock:
            self._streams = {k: (t, t) for k, (_, t) in self._streams.items()}

    def _report(self, force: bool = False) -> None:
        if not force and not self._throttle.ready():
            return
        with self._lock:
            done = sum(d for d, _ in self._streams.values())
            seen_total = sum(t for _, t in self._streams.values())
            expected = float(self.task.options.get("expected_size") or 0)
            total = max(expected, seen_total, done)
            self.task.downloaded = int(done)
            if total:
                self.task.file_size = int(total)
            self.task.speed = self._speed if self.task.status == TaskStatus.DOWNLOADING else 0.0
        self._notify_progress()

    # ------------------------------------------------------------------ bitiş
    def _finalize(self, tail: list[str]) -> None:
        code = getattr(self, "_exit_code", 1)
        if code != 0:
            errors = [ln for ln in tail if ln.startswith("ERROR")]
            text = "\n".join(errors or tail)
            if self.task.options.get("generic"):     # YouTube dışı (site videosu): YouTube'a özel mesajlar yanıltıcı olur
                raise DownloadError(webvideo.friendly_stream_error(text))
            raise DownloadError(friendly_error(text))

        source = Path(self._final_file) if self._final_file else None
        if source is None or not source.is_file():
            raise DownloadError("İndirme bitti ama çıktı dosyası bulunamadı.")

        task = self.task
        target_dir = task.save_dir
        if task.options.get("sort_by_category"):
            target_dir = category_folder(task.save_dir, source.name)
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise DownloadError(f"Klasör oluşturulamadı: {e}")
        final = reserve_unique_path(target_dir / source.name)
        try:
            os.replace(source, final)
        except OSError as e:
            try:
                final.unlink()
            except OSError:
                pass
            raise DownloadError(move_error_message(e))

        self._move_subtitles(source, final)
        size = final.stat().st_size
        task.file_name = final.name
        task.final_path = final
        task.file_size = size
        task.downloaded = size
        task.speed = 0.0
        self._discard_temp()
        self._notify_progress()
        self._set_status(TaskStatus.COMPLETED)

    # ------------------------------------------------------------------ durdurma
    @staticmethod
    def _move_subtitles(source: Path, final: Path) -> None:
        """Videoya gömülmeyen (ayrı dosya) altyazıları videonun yanına taşır: ad.tr.srt -> yeni ad.tr.srt."""
        try:
            prefix = source.stem
            for sub in source.parent.glob(glob.escape(prefix) + ".*"):
                if sub == source or sub.suffix.lower() not in (".srt", ".vtt", ".ass"):
                    continue
                os.replace(sub, final.with_name(final.stem + sub.name[len(prefix):]))
        except OSError:
            pass

    def _request_halt(self, reason: str) -> None:
        with self._lock:
            if self._halt_reason is None:
                self._halt_reason = reason
            self._halt.set()
            proc = self._proc
        if proc is not None:
            procutil.kill_tree(proc)     # engellemez: süreç hemen sonlanır, okuma döngüsü biter

    def _check_halt(self) -> None:
        if self._halt.is_set():
            raise _Halted()

    def _finish_halted(self) -> None:
        self.task.speed = 0.0
        if self._halt_reason == "stop":
            self._set_status(TaskStatus.PAUSED)      # geçici klasör korunur: .part dosyalarından devam
        else:
            self._discard_temp()
            self.task.downloaded = 0
            self._set_status(TaskStatus.CANCELLED)

    def _fail_status(self, message: str) -> None:
        self.task.speed = 0.0
        log.error("YouTube indirmesi başarısız (%s): %s", self.task.title or self.task.url, message)
        self._set_status(TaskStatus.ERROR, message)

    def _discard_temp(self) -> None:
        shutil.rmtree(self._temp_dir, ignore_errors=True)