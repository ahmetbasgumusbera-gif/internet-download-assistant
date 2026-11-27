"""
İndirme yöneticisi: kuyruk, eşzamanlılık, motor seçimi, kalıcılık ve yaşam döngüsü.
Qt bağımlılığı yoktur. Arayüz, ManagerListener ile olayları dinler.

Durum akışı:
    QUEUED -> PROBING -> DOWNLOADING -> COMPLETED
    duraklat: ekran HEMEN "duraklatıldı" olur; motor arkada kapanır, yuva boşalır; devam: tekrar QUEUED

Önemli tasarım kararı: motor, görevin KOPYASI üzerinde çalışır. Ekranın gördüğü görevi
yalnızca yönetici günceller. Böylece kapanmakta olan eski bir motorun geç gelen olayları,
kullanıcının verdiği son komutu (duraklat/iptal/devam) asla geri alamaz.
"""
from __future__ import annotations

import copy
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import requests

from core import resolver
from core.database import Database
from core.engines import ENGINES, pick_engine
from core.engines.base import BaseEngine
from core.events import EngineCallbacks
from core.models import DownloadTask, TaskStatus
from core.ratelimit import RateLimiter
from core.speed import CompositeLimiter
from urllib.parse import unquote, urlparse
from core import rules, stats
from core.tuner import Tuner
from core import thumbgen
from core import convert as _convert, hashcheck, netconf
from utils.helpers import get_category, has_enough_space
from utils.logger import get_logger

log = get_logger(__name__)

PERSIST_INTERVAL = 5.0  # ilerleme en fazla bu sıklıkta veritabanına yazılır (sn)

# Açılışta bu durumdaki kayıtlar yarım kalmış sayılıp PAUSED yapılır
_INTERRUPTED = (TaskStatus.QUEUED, TaskStatus.PROBING, TaskStatus.DOWNLOADING, TaskStatus.MERGING)

INTENT_PAUSE = "pause"
INTENT_CANCEL = "cancel"

RETRY_DELAYS = (300, 900)   # otomatik yeniden deneme bekleme süreleri (sn): 5, 15, 60 dk

_NOT_RETRYABLE = re.compile(
    r"HTTP (401|403|404|405|406|410)|kotası|boş alan|web sayfası|giriş|DRM|desteklen|bulunamadı|özel|üyelerine|yaş sınırlamalı",
    re.IGNORECASE)
_RETRYABLE = re.compile(
    r"HTTP (429|5\d\d)|bağlanılamadı|Ağ hatası|zaman aşımı|indirilemedi|Bağlantı|timed out|Connection|getaddrinfo|Beklenmeyen",
    re.IGNORECASE)


def is_transient(error: str) -> bool:
    """Hata kendiliğinden geçebilecek türden mi (bağlantı kopması, zaman aşımı, 429/5xx)? Kalıcı hatalar denenmez."""
    text = error or ""
    return not _NOT_RETRYABLE.search(text) and bool(_RETRYABLE.search(text))


def _split_credentials(url: str):
    """"https://kul:sifre@site/x" -> (kimliksiz adres, (kul, sifre)); kimlik yoksa (url, None)."""
    from urllib.parse import urlsplit, urlunsplit
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in ("http", "https") or not parts.username:
            return url, None
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        netloc = host + (f":{parts.port}" if parts.port else "")
        clean = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
        return clean, (unquote(parts.username), unquote(parts.password or ""))
    except ValueError:
        return url, None


def _noop_task(task: DownloadTask) -> None:
    pass


def _noop_id(task_id: str) -> None:
    pass


def _without_original(options: dict) -> dict:
    """Yinelenen iş karşılaştırmasında orijinal paylaşım linki sayılmaz (aynı dosya farklı linkle gelebilir)."""
    return {k: v for k, v in options.items() if k not in ("original_url", "speed_limit_kbps", "staged", "page_url", "resume_key", "listed", "info_file", "mirrors", "auth_user", "auth_pass", "convert_to", "convert_done", "hash_check")}


@dataclass
class ManagerListener:
    """
    Arayüzün bağlanacağı olaylar. Geri çağırımlar HERHANGİ bir thread'den gelebilir
    ve kısa sürmelidir (arayüzde Qt sinyali yayınlamak yeterli).
    Gelen task bir kopyadır, güvenle saklanabilir.
    """
    on_task_added: Callable[[DownloadTask], None] = _noop_task
    on_task_updated: Callable[[DownloadTask], None] = _noop_task
    on_task_removed: Callable[[str], None] = _noop_id


@dataclass
class _Entry:
    task: DownloadTask                          # ekranın ve veritabanının gördüğü görev
    engine: Optional[BaseEngine] = None         # çalışan (ya da kapanmakta olan) motor
    engine_task: Optional[DownloadTask] = None  # motorun kendi kopyası
    thread: Optional[threading.Thread] = None
    intent: Optional[str] = None                # kullanıcı niyeti: motor kapanırken ekranı korur
    remove_after: bool = False                  # motor bitince kaydı sil
    last_saved: float = 0.0
    retries: int = 0                            # otomatik yeniden deneme sayısı
    retry_timer: Optional[threading.Timer] = None
    conn_used: int = 0
    started_at: float = 0.0
    start_bytes: int = 0
    limiter: Optional[RateLimiter] = None       # bu indirmeye özel hız sınırı (genel sınırla birlikte uygulanır)


class DownloadManager:
    def __init__(self, config, db: Optional[Database] = None, engines: Optional[list] = None):
        """
        config: get(anahtar) ve download_dir() sunan nesne (ConfigManager).
        engines: kullanılacak motor sınıfları (varsayılan: core.engines.ENGINES).
        Kayıtlı motorlar (task, callbacks, connections=, limiter=) imzasını desteklemelidir.
        """
        self._config = config
        self._db = db or Database()
        self._engines = list(engines) if engines is not None else list(ENGINES)

        self._lock = threading.RLock()
        self._entries: dict[str, _Entry] = {}
        self._queue: deque[str] = deque()
        self._active: set[str] = set()
        self._listeners: list[ManagerListener] = []
        self._closing = False
        self._hold = False                  # zamanlayıcı: True iken kuyruktaki indirmeler başlamaz
        self.retry_delays = RETRY_DELAYS
        self._net_down = False                  # internet yok: kuyruk başlamaz
        self._net_paused: set[str] = set()      # internet kopunca bizim duraklattıklarımız
        _dbp = str(getattr(self._db, "_path", ":memory:"))
        _folder = None if _dbp == ":memory:" else Path(_dbp).parent
        self._stats_file = (_folder / "stats.jsonl") if _folder else None
        self._tuner = Tuner((_folder / "speedtune.json") if _folder else None)
        self._thumb_dir = (_folder / "thumbs") if _folder else None
        self._thumb_state: dict[str, dict] = {}

        self._limiter = RateLimiter(self._speed_limit_bytes())
        self._restore()

    # ================================================================= genel API
    def add_listener(self, listener: ManagerListener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def add(self, url: str, save_dir: Optional[Path] = None, file_name: str = "",
            headers: Optional[dict] = None, engine: str = "", options: Optional[dict] = None,
            title: str = "", thumbnail_url: str = "", listed: bool = False) -> str:
        """
        İndirmeyi kuyruğa ekler ve görev id'sini döndürür.
        Aynı URL + aynı ayarlar zaten aktifse onun id'sini verir.
        engine: motoru adıyla seçer (ör. "ytdlp"); boşsa URL'ye göre otomatik seçilir.
        """
        url = (url or "").strip()
        options = dict(options or {})
        url, _creds = _split_credentials(url)
        if _creds:
            options["auth_user"], options["auth_pass"] = _creds
        if listed:                       # Listeler sekmesi: eklenir ama "Listeyi indir" denene kadar beklemede kalır
            options["listed"] = True
            options["staged"] = True
        original_url = ""
        if not engine:   # motor adıyla seçilenlere (ör. YouTube) dokunulmaz
            # Paylaşım linkini doğrudan indirme adresine çevirir; desteklenmeyen türde UnsupportedLink (ValueError) fırlatır
            resolved = resolver.resolve(url)
            if resolved.url != url:
                original_url = url
                url = resolved.url
            if resolved.source:
                options["source_hint"] = resolved.source
        task_options = dict(options, original_url=original_url) if original_url else options
        engine_cls = self._pick_engine(url, engine)
        if engine_cls is None:
            raise ValueError(f"Desteklenmeyen bağlantı: {url!r}")
        directory = Path(save_dir) if save_dir else rules.pick_dir(url, file_name, Path(self._config.download_dir()))

        with self._lock:
            if self._closing:
                raise RuntimeError("İndirme yöneticisi kapatıldı.")
            for entry in self._entries.values():
                t = entry.task
                if (t.url == url and t.save_dir == directory and _without_original(t.options) == _without_original(options)
                        and not t.status.is_finished):
                    if t.options.get("staged") and not listed:
                        self.start_staged([t.id])      # listede bekleyen dosya için "İndir" denirse hemen başlar
                    return t.id  # aynı iş ikinci kez eklenmez (aynı URL farklı ayarla eklenebilir)

            task = DownloadTask(url=url, save_dir=directory, file_name=file_name,
                                headers=dict(headers or {}), engine=engine_cls.name,
                                title=title, thumbnail_url=thumbnail_url, options=task_options)
            self._entries[task.id] = _Entry(task)
            self._queue.append(task.id)
            self._persist(task)
            snapshot = copy.copy(task)

        self._emit_added(snapshot)
        if listed:
            self._stage_preview(snapshot)
        self._schedule()
        return task.id

    def pause(self, task_id: str) -> None:
        """Duraklatır: ekran hemen güncellenir, motor arkada kapanır. Kaldığı yerden devam edilebilir."""
        with self._lock:
            entry = self._entries.get(task_id)
            if not entry:
                return
            task = entry.task
            if task.status.is_finished or task.status == TaskStatus.PAUSED:
                return
            if entry.engine is not None:
                if entry.intent == INTENT_CANCEL:
                    return
                if task.status == TaskStatus.QUEUED:     # eski motor kapanırken tekrar sıraya alınmıştı
                    self._remove_from_queue(task_id)
                    self._set_task_status(task, TaskStatus.PAUSED)
                    return
                entry.intent = INTENT_PAUSE
                self._set_task_status(task, TaskStatus.PAUSED)
                entry.engine.stop()
            elif task.status == TaskStatus.QUEUED:
                self._remove_from_queue(task_id)
                self._set_task_status(task, TaskStatus.PAUSED)

    def pause_all(self) -> None:
        with self._lock:
            ids = list(self._entries)
        for task_id in ids:
            self.pause(task_id)

    def resume(self, task_id: str) -> None:
        """Listede bekleyen kartta ▶: indirme başlar. Duraklatılmışsa kaldığı yerden sürer."""
        if self.start_staged([task_id]):
            return
        self._resume_paused(task_id)

    def _resume_paused(self, task_id: str) -> None:
        """Duraklatılmış indirmeyi tekrar kuyruğa alır (yuva boşsa hemen başlar)."""
        with self._lock:
            entry = self._entries.get(task_id)
            if self._closing or not entry or entry.task.status != TaskStatus.PAUSED:
                return
            self._set_task_status(entry.task, TaskStatus.QUEUED)
            self._queue.append(task_id)
        self._schedule()

    def retry(self, task_id: str) -> None:
        """ERROR ise kaldığı yerden, CANCELLED ise baştan yeniden dener."""
        with self._lock:
            entry = self._entries.get(task_id)
            if self._closing or not entry:
                return
            task = entry.task
            if task.status not in (TaskStatus.ERROR, TaskStatus.CANCELLED):
                return
            entry.retries = 0
            self._drop_retry_timer(entry)
            if task.status == TaskStatus.CANCELLED:
                task.downloaded = 0
            task.options.pop("staged", None)         # "Tekrar dene" başlatmak demektir: listede bekleyen olarak kalmaz
            self._set_task_status(task, TaskStatus.QUEUED)
            self._queue.append(task_id)
        self._schedule()

    def cancel(self, task_id: str) -> None:
        """İptal eder ve geçici dosyaları siler. Ekran hemen "iptal edildi" olur."""
        with self._lock:
            entry = self._entries.get(task_id)
            if not entry:
                return
            task = entry.task
            if task.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED):
                return
            self._remove_from_queue(task_id)
            task.downloaded = 0
            if entry.engine is not None:
                entry.intent = INTENT_CANCEL
                self._set_task_status(task, TaskStatus.CANCELLED)
                entry.engine.cancel()          # geçici dosyalar motor kapanınca silinir
                return
            self._discard_partial(task)
            self._set_task_status(task, TaskStatus.CANCELLED)

    def remove(self, task_id: str) -> None:
        """
        Kaydı siler. Tamamlanmış dosyaya DOKUNMAZ.
        Görev o an çalışıyorsa önce iptal edilir ve kayıt motor durunca (çok kısa sürede)
        silinir; bitince on_task_removed olayı gelir.
        """
        with self._lock:
            entry = self._entries.get(task_id)
            if not entry:
                return
            if entry.engine is not None and entry.thread is not None and not entry.thread.is_alive():
                entry.engine = None                  # motor iş parçacığı çoktan bitmiş: kayıt hemen silinebilir
                entry.engine_task = None
                self._active.discard(task_id)
            if entry.engine is not None:
                entry.remove_after = True
                self._arm_force_remove(task_id)
                entry.intent = INTENT_CANCEL
                self._remove_from_queue(task_id)
                if entry.task.status != TaskStatus.COMPLETED:
                    entry.task.downloaded = 0
                    self._set_task_status(entry.task, TaskStatus.CANCELLED)
                entry.engine.cancel()
                return
            self._remove_from_queue(task_id)
            if entry.task.status != TaskStatus.COMPLETED:
                self._discard_partial(entry.task)
            del self._entries[task_id]
            self._db.delete(task_id)
        self._emit_removed(task_id)

    def _arm_force_remove(self, task_id: str) -> None:
        timer = threading.Timer(3.0, self._force_remove, args=(task_id,))
        timer.daemon = True
        timer.start()

    def _force_remove(self, task_id: str) -> None:
        """Silinmesi istenen indirmenin motoru 3 sn içinde kapanmadıysa kaydı yine de siler (takılı motor kaydı tutmasın)."""
        with self._lock:
            entry = self._entries.get(task_id)
            if entry is None or not entry.remove_after:
                return
            log.warning("Motor kapanmadı, kayıt zorla silindi (durum: %s)", entry.task.status.value)
            self._active.discard(task_id)
            self._remove_from_queue(task_id)
            self._entries.pop(task_id, None)
            self._db.delete(task_id)
        self._emit_removed(task_id)

    def _stage_preview(self, task: DownloadTask) -> None:
        """Listede bekleyen resim / video / gif için küçük resim (indirme başlamadan): dosyanın ilk birkaç MB'ı alınır."""
        if self._thumb_dir is None or task.thumbnail_url or task.engine not in ("http", "local"):
            return
        if task.engine == "local":
            name = task.file_name or Path(str(task.options.get("source_path") or "")).name
        else:
            name = task.file_name or unquote(urlparse(task.url).path)
        kind = thumbgen.kind(name)
        if not kind:
            return
        threading.Thread(target=self._preview_job, name="stage-preview", daemon=True,
                         args=(task.id, task.engine, task.url, dict(task.headers or {}), dict(task.options or {}),
                               name, kind)).start()

    def _preview_job(self, task_id: str, engine: str, url: str, headers: dict, options: dict, name: str,
                     kind: str) -> None:
        if engine == "local":
            source = Path(str(options.get("source_path") or ""))
            if source.is_file():
                self._thumb_job(task_id, source, kind, False)
            return
        suffix = Path(name).suffix or (".jpg" if kind == "image" else ".mp4")
        src = self._thumb_dir / f"{task_id}.preview{suffix}"
        try:
            self._thumb_dir.mkdir(parents=True, exist_ok=True)
            sent = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/124.0.0.0 Safari/537.36",
                    "Accept": "*/*", "Accept-Encoding": "identity"}
            sent.update({k: v for k, v in headers.items() if k.lower() not in ("range", "accept-encoding")})
            sent["Range"] = "bytes=0-4194303"
            got = 0
            with requests.get(url, headers=sent, stream=True, timeout=(10, 15)) as r:
                if r.status_code not in (200, 206):
                    return
                with open(src, "wb") as f:
                    for chunk in r.iter_content(256 * 1024):
                        f.write(chunk)
                        got += len(chunk)
                        if got >= 4 * 1024 * 1024:
                            break
            if got:
                self._thumb_job(task_id, src, kind, False)
        except (requests.RequestException, OSError):
            pass
        finally:
            try:
                src.unlink()
            except OSError:
                pass

    def tasks(self) -> list[DownloadTask]:
        with self._lock:
            return [copy.copy(e.task) for e in self._entries.values()]

    def get_task(self, task_id: str) -> Optional[DownloadTask]:
        with self._lock:
            entry = self._entries.get(task_id)
            return copy.copy(entry.task) if entry else None

    def apply_settings(self) -> None:
        """Ayarlar değişince çağrılır: hız sınırı ve mod hemen, bağlantı sayısı sonraki başlatmada geçerli olur."""
        self._limiter.set_rate(self._speed_limit_bytes())
        self._schedule()

    def start_pending(self) -> None:
        """Açılışta sürdürülmek üzere sıraya alınan indirmeleri başlatır (arayüz bağlandıktan sonra çağrılır)."""
        self._purge_page_urls()
        self._schedule()

    def _start_pending_legacy(self) -> None:
        """Açılışta sürdürülmek üzere sıraya alınan indirmeleri başlatır (arayüz bağlandıktan sonra çağrılır)."""
        self._schedule()

    def _compose_limiter(self, entry: _Entry, task: DownloadTask) -> CompositeLimiter:
        entry.limiter = RateLimiter(int(task.options.get("speed_limit_kbps") or 0) * 1024)
        return CompositeLimiter(self._limiter, entry.limiter)

    def get_task_limit(self, task_id: str) -> int:
        """Bu indirmeye özel hız sınırı (KB/sn); 0 = kart için sınır yok."""
        with self._lock:
            entry = self._entries.get(task_id)
            return int(entry.task.options.get("speed_limit_kbps") or 0) if entry else 0

    def set_task_limit(self, task_id: str, kbps: int) -> None:
        self._set_task_limit_raw(task_id, kbps)
        self._rebalance(measure=True)

    def _rebalance(self, measure: bool = False) -> None:
        """
        Listedeki çalışan indirmelerden bazılarına özel hız verildiyse, kalanlar geriye kalan hızı eşit paylaşır.
        Genel sınır yoksa toplam, sınır değiştirilirken ölçülen hızdır (yaklaşık). Özel sınırı olan kalmadıysa her şey serbest.
        """
        with self._lock:
            running = [e for e in self._entries.values()
                       if e.engine is not None and e.limiter is not None and e.task.options.get("listed")
                       and e.task.status in (TaskStatus.PROBING, TaskStatus.DOWNLOADING)]

            def user(e) -> int:
                return int(e.task.options.get("speed_limit_kbps") or 0)

            fixed = [e for e in running if user(e) > 0]
            free = [e for e in running if user(e) == 0]
            if not fixed or not free:
                self._list_total = 0.0
                for e in free:
                    if e.limiter.rate:
                        e.limiter.set_rate(0)
                return
            total = int(self._config.get("speed_limit_kbps") or 0)
            if total <= 0:
                known = getattr(self, "_list_total", 0.0)
                if measure or not known:
                    known = max(known, sum(e.task.speed for e in running) / 1024)
                    self._list_total = known
                total = known
            if total <= 0:
                return
            share = max(64, int((total - sum(user(e) for e in fixed)) / len(free)))
            for e in free:
                if e.limiter.rate != share * 1024:
                    e.limiter.set_rate(share * 1024)

    def _set_task_limit_raw(self, task_id: str, kbps: int) -> None:
        """Bu indirmeye özel hız sınırı (0 = kaldır). Çalışan HTTP indirmelerinde anında işler."""
        kbps = max(0, int(kbps))
        with self._lock:
            entry = self._entries.get(task_id)
            if entry is None:
                return
            if kbps:
                entry.task.options["speed_limit_kbps"] = kbps
            else:
                entry.task.options.pop("speed_limit_kbps", None)
            if entry.limiter is not None:
                entry.limiter.set_rate(kbps * 1024)
            snapshot = copy.copy(entry.task)
        self._persist(snapshot)

    def _maybe_thumb(self, entry: _Entry, task: DownloadTask, engine_task: DownloadTask) -> None:
        """İndirme sürerken, yeterli veri inince geçici dosyadan bir küçük resim üretmeyi dener (en fazla birkaç kez)."""
        if self._thumb_dir is None or task.engine != "http":
            return
        kind = thumbgen.kind(task.file_name)
        if not kind:
            return
        need = 256 * 1024 if kind == "image" else 3 * 1024 * 1024
        st = self._thumb_state.setdefault(task.id, {"n": 0, "t": 0.0, "busy": False, "ver": 0})
        now = time.monotonic()
        if (int(engine_task.downloaded or 0) < need or st["n"] >= 4 or st["busy"] or now - st["t"] < 6):
            return
        src = getattr(entry.engine, "_data_path", None)
        if src is None:
            return
        st["n"] += 1
        st["t"] = now
        st["busy"] = True
        threading.Thread(target=self._thumb_job, args=(task.id, Path(src), kind, False), daemon=True).start()

    def _thumb_async(self, task: DownloadTask) -> None:
        if self._thumb_dir is None or not task.final_path or task.engine not in ("http", "local"):
            return
        kind = thumbgen.kind(Path(task.final_path).name)
        if kind:
            threading.Thread(target=self._thumb_job, args=(task.id, Path(task.final_path), kind, True),
                             daemon=True).start()

    def _thumb_job(self, task_id: str, src: Path, kind: str, final: bool) -> None:
        ok = False
        dest = self._thumb_dir / f"{task_id}.jpg"
        try:
            ok = thumbgen.make_thumb(src, dest, kind == "video")
        except Exception:
            log.exception("Küçük resim üretilemedi")
        with self._lock:
            st = self._thumb_state.setdefault(task_id, {"n": 0, "t": 0.0, "busy": False, "ver": 0})
            st["busy"] = False
            entry = self._entries.get(task_id)
            if not ok or entry is None:
                return
            if not final:
                st["n"] = 99                                  # kısmi küçük resim yeterli; kesini bitince gelir
            st["ver"] += 1
            entry.task.thumbnail_url = f"{dest.as_uri()}?v={st['ver']}"
            snapshot = copy.copy(entry.task)
        self._persist(snapshot)
        self._emit_updated(snapshot)

    def start_staged(self, ids) -> int:
        """Listede bekleyen (staged) indirmeleri başlatılabilir yapar; sıra, kuyruk sırasıdır. Başlayanların sayısı döner."""
        started = []
        with self._lock:
            if self._closing:
                return 0
            for task_id in ids:
                entry = self._entries.get(task_id)
                if entry is None or not entry.task.options.get("staged"):
                    continue
                task = entry.task
                if task.status == TaskStatus.PAUSED:
                    task.options.pop("staged", None)
                    self._set_task_status(task, TaskStatus.QUEUED)
                    self._queue.append(task_id)
                elif task.status == TaskStatus.QUEUED:
                    task.options.pop("staged", None)
                    if task_id not in self._queue:
                        self._queue.append(task_id)
                else:
                    continue
                started.append(copy.copy(task))
        for snap in started:
            self._persist(snap)
            self._emit_updated(snap)
        self._schedule()
        return len(started)

    def refresh_url(self, task_id: str, url: str, headers: Optional[dict] = None) -> bool:
        """
        Hatalı/duraklatılmış HTTP indirmesine tarayıcının yeni verdiği adresi bağlar ve kaldığı yerden sürdürür
        (eski adres geçici klasör anahtarı olarak korunur). Uygun değilse False.
        """
        url = (url or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            return False
        with self._lock:
            entry = self._entries.get(task_id)
            if self._closing or entry is None:
                return False
            task = entry.task
            if task.engine != "http" or task.status not in (TaskStatus.ERROR, TaskStatus.PAUSED, TaskStatus.CANCELLED):
                return False
            task.options.setdefault("resume_key", task.url)
            task.url = url
            if headers is not None:
                task.headers = dict(headers)
            entry.retries = 0
            self._drop_retry_timer(entry)
            task.options.pop("staged", None)
            self._set_task_status(task, TaskStatus.QUEUED)
            self._queue.append(task_id)
        self._schedule()
        return True

    def _schedule_page_purge(self, task_id: str) -> None:
        timer = threading.Timer(600, self._drop_page_url, args=(task_id,))
        timer.daemon = True
        timer.start()

    def _drop_page_url(self, task_id: str) -> None:
        with self._lock:
            entry = self._entries.get(task_id)
            if entry is None or "page_url" not in entry.task.options:
                return
            entry.task.options.pop("page_url", None)
            snapshot = copy.copy(entry.task)
        self._persist(snapshot)

    def _purge_page_urls(self) -> None:
        changed = []
        with self._lock:
            for entry in self._entries.values():
                if entry.task.status == TaskStatus.COMPLETED and "page_url" in entry.task.options:
                    entry.task.options.pop("page_url", None)
                    changed.append(copy.copy(entry.task))
        for snap in changed:
            self._persist(snap)

    def find_completed(self, url: str, file_name: str = "", size: int = 0) -> Optional[DownloadTask]:
        """Aynı bağlantıdan (ya da aynı ad ve boyutta) tamamlanmış ve dosyası hâlâ diskte duran indirme."""
        with self._lock:
            for entry in self._entries.values():
                t = entry.task
                if t.status != TaskStatus.COMPLETED or not t.final_path or not Path(t.final_path).is_file():
                    continue
                same_url = url and (t.url == url or t.options.get("original_url") == url)
                same_file = bool(file_name and size > 0 and t.file_name == file_name and t.file_size == size)
                if same_url or same_file:
                    return copy.copy(t)
        return None

    def stats_file(self) -> Optional[Path]:
        return self._stats_file

    def net_pause(self) -> int:
        """İnternet koptu: çalışanları duraklatır, kuyruğu bekletir. Duraklatılan sayısını döndürür."""
        with self._lock:
            if self._net_down:
                return 0
            self._net_down = True
            ids = [i for i, e in self._entries.items()
                   if e.task.status in (TaskStatus.PROBING, TaskStatus.DOWNLOADING)]
        for task_id in ids:
            self.pause(task_id)
        with self._lock:
            self._net_paused.update(i for i in ids if i in self._entries
                                    and self._entries[i].task.status == TaskStatus.PAUSED)
            return len(self._net_paused)

    def net_resume(self) -> int:
        """İnternet geldi: bizim duraklattıklarımızı sürdürür ve kuyruğu bırakır."""
        with self._lock:
            self._net_down = False
            ids = list(self._net_paused)
            self._net_paused.clear()
        count = 0
        for task_id in ids:
            task = self.get_task(task_id)
            if task is not None and task.status == TaskStatus.PAUSED:
                self.resume(task_id)
                count += 1
        self._schedule()
        return count

    def _tuned_connections(self, entry: _Entry, task: DownloadTask) -> int:
        default = int(self._config.get("connections_per_download"))
        n = default
        if task.engine == "http" and self._config.get("auto_tune_connections"):
            n = self._tuner.choose(urlparse(task.url).netloc.lower(), default, min(32, default * 2))
        entry.conn_used = n
        entry.started_at = time.monotonic()
        entry.start_bytes = int(task.downloaded or 0)
        return n

    def _finish_stats(self, entry: _Entry, task: DownloadTask) -> None:
        try:
            elapsed = max(0.001, time.monotonic() - entry.started_at) if entry.started_at else 0.0
            size = int(task.file_size or 0)
            xfer = max(0, size - entry.start_bytes)
            host = urlparse(task.url).netloc.lower() or "yerel"
            stats.record(self._stats_file, host, size, xfer, elapsed)
            limited = (self._limiter.rate > 0) or (entry.limiter is not None and entry.limiter.rate > 0)
            if (task.engine == "http" and entry.conn_used >= 2 and entry.start_bytes == 0 and not limited
                    and xfer >= 20 * 1024 * 1024 and elapsed >= 5):
                self._tuner.record(host, entry.conn_used, xfer / elapsed)
        except Exception:
            log.exception("İstatistik kaydedilemedi")

    def _arm_disk_wait(self, task_id: str) -> None:
        """Disk dolduğu için hata veren indirme: yer açılınca kendiliğinden devam eder (30 sn'de bir bakar)."""
        with self._lock:
            entry = self._entries.get(task_id)
            if self._closing or entry is None:
                return
            task = entry.task
            if task.status != TaskStatus.ERROR or "boş alan" not in (task.error or ""):
                return
            if "otomatik" not in task.error:
                task.error += "  ·  yer açılınca otomatik sürecek"
            self._drop_retry_timer(entry)
            timer = threading.Timer(30, self._disk_check, args=(task_id,))
            timer.daemon = True
            entry.retry_timer = timer
            snapshot = copy.copy(task)
            timer.start()
        self._persist(snapshot)
        self._emit_updated(snapshot)

    def _disk_check(self, task_id: str) -> None:
        with self._lock:
            entry = self._entries.get(task_id)
            if self._closing or entry is None:
                return
            entry.retry_timer = None
            task = entry.task
            if task.status != TaskStatus.ERROR:
                return
            need = max(0, int(task.file_size or 0) - int(task.downloaded or 0))
            folder = task.save_dir
        if has_enough_space(folder, need):
            with self._lock:
                self.retry(task_id)
        else:
            self._arm_disk_wait(task_id)

    def set_hold(self, hold: bool) -> None:
        """True: kuyruktaki indirmeler başlamaz (zamanlayıcı). False: bırakır ve boş yuvaları doldurur."""
        with self._lock:
            self._hold = bool(hold)
        if not hold:
            self._schedule()

    def is_on_hold(self) -> bool:
        return self._hold

    def queue_order(self) -> list[str]:
        """Sıradaki (başlamamış) indirmelerin çalışma sırası."""
        with self._lock:
            return list(self._queue)

    def move_in_queue(self, task_id: str, where: str) -> bool:
        """where: top | up | down | bottom. Görev kuyrukta değilse False."""
        with self._lock:
            order = list(self._queue)
            if task_id not in order:
                return False
            i = order.index(task_id)
            order.pop(i)
            if where == "top":
                order.insert(0, task_id)
            elif where == "bottom":
                order.append(task_id)
            elif where == "up":
                order.insert(max(0, i - 1), task_id)
            elif where == "down":
                order.insert(min(len(order), i + 1), task_id)
            else:
                return False
            self._queue = deque(order)
            return True

    def place_in_queue(self, task_id: str, ref_id: str, before: bool) -> bool:
        """task_id'yi ref_id'nin hemen önüne (ya da arkasına) koyar. İkisi de kuyrukta olmalı."""
        with self._lock:
            order = list(self._queue)
            if task_id not in order or ref_id not in order or task_id == ref_id:
                return False
            order.remove(task_id)
            j = order.index(ref_id)
            order.insert(j if before else j + 1, task_id)
            self._queue = deque(order)
            return True

    def pending_retries(self) -> int:
        """Otomatik yeniden denenmeyi bekleyen hatalı indirme sayısı."""
        with self._lock:
            return sum(1 for e in self._entries.values()
                       if e.retry_timer is not None and e.task.status == TaskStatus.ERROR)

    @staticmethod
    def _drop_retry_timer(entry: _Entry) -> None:
        if entry.retry_timer is not None:
            entry.retry_timer.cancel()
            entry.retry_timer = None

    def _arm_retry(self, task_id: str) -> None:
        """Geçici bir hatadan sonra yeniden deneme zamanlar (ayar açıksa ve hak kaldıysa)."""
        if not self._config.get("auto_retry"):
            return
        with self._lock:
            entry = self._entries.get(task_id)
            if self._closing or entry is None:
                return
            task = entry.task
            if task.status != TaskStatus.ERROR or not is_transient(task.error):
                return
            if entry.retries >= len(self.retry_delays):
                return
            delay = self.retry_delays[entry.retries]
            entry.retries += 1
            self._drop_retry_timer(entry)
            timer = threading.Timer(delay, self._retry_fire, args=(task_id,))
            timer.daemon = True
            entry.retry_timer = timer
            task.error = (f"{task.error}  ·  {max(1, round(delay / 60))} dk sonra otomatik denenecek "
                          f"({entry.retries}/{len(self.retry_delays)})")
            snapshot = copy.copy(task)
            timer.start()
        self._persist(snapshot)
        self._emit_updated(snapshot)

    def _retry_fire(self, task_id: str) -> None:
        with self._lock:
            entry = self._entries.get(task_id)
            if self._closing or entry is None:
                return
            entry.retry_timer = None
            if entry.task.status != TaskStatus.ERROR:
                return
            count = entry.retries
            self.retry(task_id)               # kullanıcı yeniden denemesi sayacı sıfırlar: burada geri yüklenir
            entry.retries = count

    def effective_limit(self) -> int:
        """Aynı anda çalışabilecek indirme sayısı (sırayla modunda 1)."""
        if self._config.get("download_mode") == "sequential":
            return 1
        return max(1, int(self._config.get("max_concurrent_downloads")))

    def shutdown(self, timeout: float = 10.0) -> None:
        """Aktif indirmeleri durdurur (temp korunur), durumları kaydeder, veritabanını kapatır."""
        with self._lock:
            if self._closing:
                return
            self._closing = True
            running = [e for e in self._entries.values() if e.engine is not None]
            # Kullanıcının kendi duraklattıkları (niyet atanmış) hariç: kapanışta çalışanlar açılışta sürdürülebilir
            resumable = [e for e in running if e.intent is None] if self._config.get("auto_resume_on_start") else []
            for entry in running:
                entry.engine.stop()
            threads = [e.thread for e in running if e.thread is not None]

        deadline = time.monotonic() + timeout
        for t in threads:
            t.join(max(0.0, deadline - time.monotonic()))

        with self._lock:
            for entry in resumable:
                if entry.task.status == TaskStatus.PAUSED:
                    entry.task.status = TaskStatus.QUEUED    # kapanışta çalışıyordu: açılışta sürdürülür
            for entry in self._entries.values():
                if entry.remove_after:
                    self._db.delete(entry.task.id)       # silinmesi istenmişti: geri yazılmasın
                else:
                    self._persist(entry.task)
        self._db.close()

    # ================================================================= kuyruk
    def _schedule(self) -> None:
        """
        Boş yuva varsa kuyruktan indirme başlatır. Listeler sekmesindeki indirmeler kendi kuralıyla çalışır:
        "aynı anda" modunda sınırsız, "sırayla" modunda birer birer. Listede bekleyenler (staged) başlatılmaz.
        """
        with self._lock:
            if self._closing:
                return
            for task_id in [i for i in self._queue
                            if i in self._entries and self._entries[i].task.engine == "local"]:
                self._remove_from_queue(task_id)
                entry = self._entries[task_id]
                if entry.task.status == TaskStatus.QUEUED and task_id not in self._active:
                    self._start_entry(entry)
            if self._hold or self._net_down:
                return
            limit = self.effective_limit()
            sequential_list = self._config.get("list_mode") == "sequential"
            listed_active = sum(1 for i in self._active
                                if i in self._entries and self._entries[i].task.options.get("listed"))
            other_active = len(self._active) - listed_active
            for task_id in list(self._queue):
                entry = self._entries.get(task_id)
                if entry is None or entry.task.status != TaskStatus.QUEUED:
                    self._remove_from_queue(task_id)
                    continue
                if entry.task.options.get("staged") or task_id in self._active:
                    continue
                if entry.task.options.get("listed"):
                    if sequential_list and listed_active >= 1:
                        continue
                    listed_active += 1
                else:
                    if other_active >= limit:
                        continue
                    other_active += 1
                self._remove_from_queue(task_id)
                self._start_entry(entry)
            self._rebalance()

    def _schedule_legacy(self) -> None:
        """Boş yuva varsa kuyruktan indirme başlatır."""
        with self._lock:
            if self._closing:
                return
            # Yerel dosya görevleri (tarayıcının blob: ile verdiği dosyayı klasöre taşımak) ağ kullanmaz ve anında biter:
            # uzun bir indirmenin ya da "sırayla" modunun arkasında beklemesinler.
            for task_id in [i for i in self._queue
                            if i in self._entries and self._entries[i].task.engine == "local"]:
                self._remove_from_queue(task_id)
                entry = self._entries[task_id]
                if entry.task.status == TaskStatus.QUEUED and task_id not in self._active:
                    self._start_entry(entry)
            if self._hold or self._net_down:
                return
            limit = self.effective_limit()
            while self._queue and len(self._active) < limit:
                task_id = self._queue.popleft()
                entry = self._entries.get(task_id)
                if entry is None or entry.task.status != TaskStatus.QUEUED:
                    continue
                if task_id in self._active:
                    # Önceki motor thread'i henüz yuvayı bırakmadı; bitince _schedule tekrar çağrılacak
                    self._queue.appendleft(task_id)
                    break
                self._start_entry(entry)

    def _start_entry(self, entry: _Entry) -> None:
        task = entry.task
        if task.options.get("staged"):
            return                                   # listede bekliyor: "Listeyi indir" / ▶ ile başlar
        engine_cls = self._pick_engine(task.url, task.engine)
        if engine_cls is None:
            self._set_task_status(task, TaskStatus.ERROR, "Bu bağlantı için uygun indirme motoru yok.")
            return

        engine_task = copy.copy(task)
        engine_task.engine = engine_cls.name
        if self._config.get("sort_into_categories"):
            # yalnızca motorun kopyasına yazılır: ekran, veritabanı ve yinelenen-iş denetimi etkilenmez
            engine_task.options = dict(task.options, sort_by_category=True)
        if Path(task.save_dir) == Path(self._config.download_dir()):
            engine_task.options = dict(engine_task.options, auto_dir=True)   # ad kesinleşince kural yeniden uygulanır
        task.engine = engine_cls.name
        callbacks = EngineCallbacks(
            on_status=lambda t: self._on_engine_event(entry, t, is_status=True),
            on_progress=lambda t: self._on_engine_event(entry, t, is_status=False),
        )
        engine = engine_cls(
            engine_task, callbacks,
            connections=self._tuned_connections(entry, task),
            limiter=self._compose_limiter(entry, task),
        )
        entry.intent = None
        entry.engine = engine
        entry.engine_task = engine_task
        self._active.add(task.id)
        entry.thread = threading.Thread(
            target=self._run_entry, args=(entry, engine_task), name=f"dl-{task.id}", daemon=True
        )
        entry.thread.start()

    def _run_entry(self, entry: _Entry, engine_task: DownloadTask) -> None:
        try:
            entry.engine.start()
        except Exception as e:  # motorlar kendi hatalarını yakalar; bu son güvenlik ağı
            log.exception("Motor beklenmeyen şekilde çöktü")
            engine_task.status = TaskStatus.ERROR
            engine_task.error = f"Beklenmeyen hata: {e}"
            self._on_engine_event(entry, engine_task, is_status=True)  # normal olay yolu: eski motor ekranı bozamaz
        finally:
            self._on_engine_finished(entry, engine_task)

    def _on_engine_finished(self, entry: _Entry, engine_task: DownloadTask) -> None:
        removed_id = None
        snapshot = None
        with self._lock:
            task = entry.task
            self._active.discard(task.id)
            if entry.engine_task is engine_task:
                if engine_task.status == TaskStatus.COMPLETED:
                    # Kullanıcı o sırada duraklatsa/iptal etse bile dosya tamamlandıysa tamamlandı sayılır
                    self._remove_from_queue(task.id)
                    self._copy_fields(task, engine_task)
                    convert_to = self._wants_convert(task)
                    task.status = TaskStatus.MERGING if convert_to else TaskStatus.COMPLETED
                    task.error = ""
                    task.speed = 0.0
                    task.finished_at = None if convert_to else (task.finished_at or datetime.now())
                    snapshot = copy.copy(task)
                    self._finish_stats(entry, task)
                    self._schedule_page_purge(task.id)
                    self._thumb_async(task)
                    if convert_to:
                        threading.Thread(target=self._convert_job, args=(task.id, convert_to), daemon=True,
                                         name="convert").start()
                    else:
                        self._verify_async(task)
                elif entry.intent == INTENT_CANCEL:
                    self._discard_partial(task)
                    task.downloaded = 0
                elif entry.intent == INTENT_PAUSE:
                    task.downloaded = engine_task.downloaded
                entry.engine = None
                entry.engine_task = None
                entry.thread = None
                entry.intent = None

            if entry.remove_after and entry.engine is None:
                if task.status != TaskStatus.COMPLETED:
                    self._discard_partial(task)
                self._entries.pop(task.id, None)
                self._db.delete(task.id)
                removed_id = task.id
            else:
                self._persist(task)

        if snapshot is not None and removed_id is None:
            self._emit_updated(snapshot)
        if removed_id:
            self._emit_removed(removed_id)
        self._schedule()

    # ================================================================= motor geri çağırımları
    @staticmethod
    def _copy_fields(task: DownloadTask, engine_task: DownloadTask) -> None:
        task.file_name = engine_task.file_name or task.file_name
        task.file_size = engine_task.file_size
        task.downloaded = engine_task.downloaded
        task.final_path = engine_task.final_path

    def _on_engine_event(self, entry: _Entry, engine_task: DownloadTask, is_status: bool) -> None:
        """Motorun olaylarını (kendi kopyasından) ekranın gördüğü göreve aktarır."""
        with self._lock:
            if entry.engine_task is not engine_task:
                return  # artık geçerli olmayan (eski) motor
            task = entry.task
            self._copy_fields(task, engine_task)

            self._maybe_thumb(entry, task, engine_task)
            status = engine_task.status
            if entry.intent is not None and status != TaskStatus.COMPLETED:
                task.speed = 0.0    # kullanıcı zaten duraklattı/iptal etti; ekran korunur
                return

            if status == TaskStatus.COMPLETED and self._wants_convert(task):
                status = TaskStatus.MERGING          # dönüştürme sürerken "işleniyor" görünür
            task.status = status
            task.error = engine_task.error
            task.speed = engine_task.speed
            task.finished_at = (task.finished_at or datetime.now()) if status.is_finished else None

            now = time.monotonic()
            persist = is_status or now - entry.last_saved >= PERSIST_INTERVAL
            if persist:
                entry.last_saved = now
            snapshot = copy.copy(task)

        if persist:
            self._persist(snapshot)
        self._emit_updated(snapshot)
        if snapshot.status == TaskStatus.ERROR:
            if self._try_mirror(snapshot.id):
                return
            self._arm_retry(snapshot.id)
            self._arm_disk_wait(snapshot.id)

    # ================================================================= yardımcılar
    # ================================================================= dönüştürme, doğrulama, yedek adres, kimlik
    def _wants_convert(self, task: DownloadTask) -> str:
        """İndirme bitince ffmpeg dönüştürmesi gerekiyorsa hedef biçim ('' = gerekmez)."""
        if task.engine not in ("http", "local"):
            return ""
        options = task.options or {}
        if options.get("convert_done"):
            return ""
        name = task.file_name or ""
        category = get_category(name)
        target = str(options.get("convert_to") or "")
        if not target and category == "Videolar":
            target = str(self._config.get("auto_convert") or "")
        if target not in _convert.TARGETS or category not in ("Videolar", "Müzik"):
            return ""
        if target in _convert.VIDEO_TARGETS and category != "Videolar":
            return ""
        if Path(name).suffix.lstrip(".").lower() == target:
            return ""
        return target if _convert.available() else ""

    def _convert_job(self, task_id: str, target: str) -> None:
        with self._lock:
            entry = self._entries.get(task_id)
            if entry is None:
                return
            src = Path(entry.task.final_path) if entry.task.final_path else None
            keep = bool(self._config.get("convert_keep_original"))
        out = None
        try:
            if src is None or not src.is_file():
                raise _convert.ConvertError("Dosya bulunamadı.")
            out = _convert.convert(src, target)
        except _convert.ConvertError as e:
            log.warning("Dönüştürme başarısız (%s): %s", task_id, e)
        except Exception:
            log.exception("Dönüştürme beklenmeyen şekilde başarısız")
        with self._lock:
            entry = self._entries.get(task_id)
            if entry is None or entry.task.status != TaskStatus.MERGING:
                if out is not None and (entry is None or entry.task.status != TaskStatus.COMPLETED):
                    try:
                        out.unlink()
                    except OSError:
                        pass
                return
            task = entry.task
            if out is not None:
                if not keep and src is not None:
                    try:
                        src.unlink()
                    except OSError:
                        pass
                task.final_path = out
                task.file_name = out.name
                try:
                    task.file_size = task.downloaded = out.stat().st_size
                except OSError:
                    pass
            task.options["convert_done"] = True
            task.status = TaskStatus.COMPLETED
            task.error = ""
            task.speed = 0.0
            task.finished_at = datetime.now()
            snapshot = copy.copy(task)
        self._persist(snapshot)
        self._emit_updated(snapshot)
        self._schedule()

    def _verify_async(self, task: DownloadTask) -> None:
        if (not self._config.get("auto_verify_hash") or task.engine != "http" or not task.final_path
                or task.options.get("source_hint") or not str(task.url).lower().startswith(("http://", "https://"))):
            return
        threading.Thread(target=self._verify_job, args=(task.id,), daemon=True, name="hash-verify").start()

    def _verify_job(self, task_id: str) -> None:
        """Dosyanın yanında .sha256 / .sha1 / .md5 yan dosyası varsa indirmeyi doğrular."""
        with self._lock:
            entry = self._entries.get(task_id)
            if entry is None:
                return
            entry.task.options.pop("hash_check", None)
            task = copy.copy(entry.task)
        path = Path(task.final_path) if task.final_path else None
        if path is None or not path.is_file():
            return
        parts = urlparse(task.url)
        base = parts._replace(query="", fragment="").geturl()
        wanted = unquote(parts.path.rsplit("/", 1)[-1]) or path.name
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept-Encoding": "identity"}
        algo = value = ""
        for suffix in (".sha256", ".sha256sum", ".sha1", ".md5"):
            try:
                with requests.get(base + suffix, headers=headers, stream=True, timeout=(6, 8),
                                  proxies=netconf.requests_proxies() or None) as r:
                    if r.status_code != 200 or "html" in r.headers.get("Content-Type", "").lower():
                        continue
                    chunk = next(r.iter_content(8192), b"")
            except requests.RequestException:
                continue
            algo, value = hashcheck.parse_for_file(chunk.decode("utf-8", "replace"), wanted)
            if value:
                break
        if not value:
            return
        try:
            actual = hashcheck.file_hash(path, algo)
        except OSError:
            return
        with self._lock:
            entry = self._entries.get(task_id)
            if entry is None:
                return
            entry.task.options["hash_check"] = {"algo": algo, "ok": actual == value}
            snapshot = copy.copy(entry.task)
        self._persist(snapshot)
        self._emit_updated(snapshot)

    def add_mirror(self, task_id: str, url: str) -> bool:
        """Aynı dosyanın yedek adresi: indirme hata verirse sırayla denenir."""
        url = (url or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            return False
        with self._lock:
            entry = self._entries.get(task_id)
            if entry is None or entry.task.engine != "http" or url == entry.task.url:
                return False
            mirrors = list(entry.task.options.get("mirrors") or [])
            if url not in mirrors:
                mirrors.append(url)
            entry.task.options["mirrors"] = mirrors
            snapshot = copy.copy(entry.task)
        self._persist(snapshot)
        return True

    def _try_mirror(self, task_id: str) -> bool:
        """Hata veren HTTP indirmesini sıradaki yedek adresle (kaldığı yerden, dosya aynıysa) yeniden başlatır."""
        with self._lock:
            entry = self._entries.get(task_id)
            if self._closing or entry is None:
                return False
            task = entry.task
            mirrors = list(task.options.get("mirrors") or [])
            if (task.status != TaskStatus.ERROR or task.engine != "http" or not mirrors
                    or "boş alan" in (task.error or "")):
                return False
            next_url = mirrors.pop(0)
            if mirrors:
                task.options["mirrors"] = mirrors
            else:
                task.options.pop("mirrors", None)
            task.options.setdefault("resume_key", task.url)
            log.warning("Yedek adrese geçiliyor: %s", next_url)
            task.url = next_url
            entry.retries = 0
            self._drop_retry_timer(entry)
            self._set_task_status(task, TaskStatus.QUEUED)
            self._queue.append(task_id)
        self._schedule()
        return True

    def set_auth(self, task_id: str, user: str, password: str) -> bool:
        """HTTP indirmesine kullanıcı adı / şifre (Basic) verir; hatalıysa yeniden dener. Boş kullanıcı = kaldır."""
        with self._lock:
            entry = self._entries.get(task_id)
            if entry is None or entry.task.engine != "http":
                return False
            if user:
                entry.task.options["auth_user"] = user
                entry.task.options["auth_pass"] = password
            else:
                entry.task.options.pop("auth_user", None)
                entry.task.options.pop("auth_pass", None)
            snapshot = copy.copy(entry.task)
        self._persist(snapshot)
        if snapshot.status in (TaskStatus.ERROR, TaskStatus.CANCELLED):
            self.retry(task_id)
        return True

    def _restore(self) -> None:
        """Açılışta kayıtlı görevleri yükler; yarım kalanları PAUSED yapar (otomatik başlatmaz)."""
        auto_resume = bool(self._config.get("auto_resume_on_start"))
        for task in self._db.load_all():
            resume = False
            if (task.status == TaskStatus.MERGING and task.engine in ("http", "local") and task.final_path
                    and Path(task.final_path).is_file()):
                task.status = TaskStatus.COMPLETED      # dönüştürme sırasında kapanmış: dosya hazır
                task.finished_at = task.finished_at or datetime.now()
                self._persist(task)
            elif task.status in _INTERRUPTED:
                resume = auto_resume or bool(task.options.get("staged"))
                task.status = TaskStatus.QUEUED if resume else TaskStatus.PAUSED
                self._persist(task)
            task.speed = 0.0
            self._entries[task.id] = _Entry(task)
            if resume:
                self._queue.append(task.id)   # arayüz bağlandıktan sonra start_pending() başlatır

    def _pick_engine(self, url: str, name: str = ""):
        return pick_engine(url, self._engines, name)

    def _speed_limit_bytes(self) -> int:
        return int(self._config.get("speed_limit_kbps")) * 1024

    def _remove_from_queue(self, task_id: str) -> None:
        try:
            self._queue.remove(task_id)
        except ValueError:
            pass

    def _discard_partial(self, task: DownloadTask) -> None:
        engine_cls = self._pick_engine(task.url, task.engine)
        if engine_cls is None:
            return
        try:
            engine_cls(task).discard_partial()
        except Exception:
            log.exception("Geçici dosyalar silinemedi")

    def _set_task_status(self, task: DownloadTask, status: TaskStatus, error: str = "") -> None:
        """Yöneticinin kendi başlattığı durum değişimleri (kullanıcı komutları)."""
        task.status = status
        task.error = error
        task.speed = 0.0
        task.finished_at = (task.finished_at or datetime.now()) if status.is_finished else None
        self._persist(task)
        self._emit_updated(task)

    def _persist(self, task: DownloadTask) -> None:
        try:
            self._db.save(task)
        except Exception:
            log.exception("Görev veritabanına yazılamadı: %s", task.id)

    def _emit(self, name: str, arg) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                getattr(listener, name)(arg)
            except Exception:
                log.exception("Dinleyici hata verdi: %s", name)

    def _emit_added(self, task: DownloadTask) -> None:
        self._emit("on_task_added", copy.copy(task))

    def _emit_updated(self, task: DownloadTask) -> None:
        self._emit("on_task_updated", copy.copy(task))

    def _emit_removed(self, task_id: str) -> None:
        self._thumb_state.pop(task_id, None)
        if self._thumb_dir is not None:
            try:
                (self._thumb_dir / f"{task_id}.jpg").unlink()
            except OSError:
                pass
        self._emit("on_task_removed", task_id)