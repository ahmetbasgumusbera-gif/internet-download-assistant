"""
Parçalı (çok bağlantılı) HTTP/HTTPS indirme motoru.

Akış: probe -> hazırlık (plan/devam kontrolü) -> parçalar tek dosyaya yazılır -> doğrula -> yerine koy

Birleştirme yoktur: tek bir (seyrek işaretli) veri dosyası oluşturulur ve her parça kendi
konumuna yazar; dosya, parçalar yazdıkça kendiliğinden büyür (baştan boyutlandırma YOK: Windows'ta
büyük dosyayı baştan oluşturmak GB başına saniyeler sürdüğü için ölçümle elendi).
İlerleme (parça başına inen bayt) state.json'a düzenli yazılır; böylece uygulama
kapansa/çökse bile kaldığı yerden devam edilir.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import queue
import re
import shutil
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import unquote, urlencode, urljoin, urlparse
import html as _html

import requests
from requests.adapters import HTTPAdapter

from core import netconf
from core import rules as _rules
from core.engines.base import BaseEngine
from core.events import EngineCallbacks, ProgressThrottle
from core.models import DownloadTask, TaskStatus
from core.ratelimit import RateLimiter
from utils import paths
from utils.helpers import (
    category_folder, has_enough_space, hide_on_windows, reserve_unique_path, sanitize_filename,
    set_sparse,
)
from utils.logger import get_logger

log = get_logger(__name__)

STATE_VERSION = 2

_DRIVE_QUOTA = re.compile(r"Quota exceeded|too many users have viewed or downloaded|indirme kotası", re.IGNORECASE)


def parse_drive_form(text: str, base_url: str) -> Optional[str]:
    """
    Google Drive'ın "virüs taraması yapılamadı" onay sayfasından indirme adresini çıkarır (ağ yok).
    Yeni biçim: gizli alanlı download-form. Eski biçim: /uc?export=download&confirm=... bağlantısı. Bulunamazsa None.
    """
    form = re.search(r'<form[^>]*id="download-form"[^>]*>', text, re.IGNORECASE)
    if form:
        action = re.search(r'action="([^"]+)"', form.group(0), re.IGNORECASE)
        if action:
            end = text.find("</form>", form.end())
            block = text[form.end(): end if end != -1 else len(text)]
            params = []
            for tag in re.findall(r"<input[^>]+>", block, re.IGNORECASE):
                name = re.search(r'name="([^"]*)"', tag)
                value = re.search(r'value="([^"]*)"', tag)
                if name:
                    params.append((_html.unescape(name.group(1)), _html.unescape(value.group(1)) if value else ""))
            if params:
                target = urljoin(base_url, _html.unescape(action.group(1)))
                return target + ("&" if "?" in target else "?") + urlencode(params)
    link = re.search(r'href="(/uc\?export=download[^"]+)"', text, re.IGNORECASE)
    if link:
        return urljoin(base_url, _html.unescape(link.group(1)))
    return None


class _ProbeCache:
    """Önceden (kullanıcı İndir'e basmadan) yapılan sondalamanın sonucunu kısa süre saklar; her kayıt bir kez kullanılır."""
    TTL = 120.0

    def __init__(self):
        self._lock = threading.Lock()
        self._items: dict = {}

    def put(self, key, value) -> None:
        now = time.monotonic()
        with self._lock:
            for old in [k for k, (t, _v) in self._items.items() if now - t > self.TTL]:
                del self._items[old]
            self._items[key] = (now, value)

    def take(self, key):
        now = time.monotonic()
        with self._lock:
            item = self._items.pop(key, None)
        return item[1] if item is not None and now - item[0] <= self.TTL else None


PROBE_CACHE = _ProbeCache()


class DownloadError(Exception):
    """Kullanıcıya gösterilecek, tekrar denemeyle düzelmeyecek hata."""

    def __init__(self, message: str, discard_temp: bool = False):
        super().__init__(message)
        self.discard_temp = discard_temp  # True: geçici dosyalar bozuk, silinmeli


def move_error_message(error: OSError) -> str:
    """Dosya taşıma hatasını kısa ve anlaşılır yazar (WinError 225: Windows Defender dosyayı virüs saydı)."""
    if getattr(error, "winerror", 0) in (225, 226):
        return "Windows Defender dosyayı virüs olarak işaretledi (WinError 225)."
    return f"Dosya hedef klasöre taşınamadı: {error}"


class _PartError(Exception):
    """Tek bir parçada oluşan, yeniden denenebilir hata. strain=True: sunucu fazla bağlantıdan zorlanıyor olabilir."""

    def __init__(self, message: str, strain: bool = False, status: int = 0):
        super().__init__(message)
        self.strain = strain
        self.status = status


class _Halted(Exception):
    """İptal / durdurma isteği geldi."""


class _SingleStreamFallback(Exception):
    """Sunucu Range'li parça isteklerini reddediyor: Range'siz, tek bağlantıyla baştan denenmeli."""


_HTML_TYPES = ("text/html", "application/xhtml+xml")

# Çözümleyicinin tanıdığı siteye özel, hata mesajına eklenen ipuçları (görevin options["source_hint"] değeri)
_SOURCE_HINTS = {
    "Google Drive": ("Dosya “Bağlantıya sahip olan herkes” olarak paylaşılmamış "
                     "ya da günlük indirme kotası dolmuş olabilir."),
}


def _http_error_message(code: int) -> str:
    """Sondalamada gelen HTTP hata kodunu anlaşılır Türkçe mesaja çevirir (kod mesajda kalır)."""
    if code == 401:
        return "Erişim izni yok ya da giriş gerekiyor (HTTP 401). Bağlantı herkese açık olmayabilir."
    if code == 403:
        return ("Sunucu erişimi reddetti (HTTP 403). Bağlantının süresi dolmuş, "
                "korumalı ya da izin gerektiriyor olabilir.")
    if code == 404:
        return "Dosya bulunamadı (HTTP 404). Bağlantı silinmiş ya da yanlış olabilir."
    if code == 410:
        return "Dosya artık mevcut değil (HTTP 410)."
    if code == 429:
        return "Sunucu çok fazla istek aldığını bildiriyor (HTTP 429). Biraz bekleyip tekrar deneyin."
    if 500 <= code < 600:
        return f"Sunucu hatası (HTTP {code}). Sunucu geçici olarak sorunlu olabilir, biraz sonra tekrar deneyin."
    return f"Sunucu hata döndürdü: HTTP {code}"


_STRAIN_STATUSES = (429, 503)   # "çok fazla istek / geçici olarak kapasite yok"

# Oturum boyunca öğrenilen sunucu başına bağlantı sınırı (host:port -> en fazla eşzamanlı bağlantı). Diske yazılmaz.
_HOST_LIMITS: dict[str, int] = {}
_HOST_LOCK = threading.Lock()


def _remembered_limit(host: str) -> Optional[int]:
    with _HOST_LOCK:
        return _HOST_LIMITS.get(host)


def _remember_limit(host: str, limit: int) -> None:
    if not host:
        return
    with _HOST_LOCK:
        _HOST_LIMITS[host] = min(limit, _HOST_LIMITS.get(host, limit))


def _is_server_strain(error: Exception) -> bool:
    """Hata, sunucunun çok sayıda bağlantıdan zorlandığına işaret ediyor mu? (zaman aşımı, kopma, 429/503)"""
    if isinstance(error, _PartError):
        return error.strain
    return isinstance(error, (requests.Timeout, requests.ConnectionError,
                              requests.exceptions.ChunkedEncodingError, TimeoutError, ConnectionError))


_EOF = object()


class _StreamReader:
    """
    Yanıt gövdesini AYRI bir thread'de okuyup kuyruğa koyar. Çağıran thread beklemeyi
    istediği an bırakabilir (abandon): okuma thread'i takılı kalsa bile çağıran asla bloke olmaz.

    Neden gerekli: Windows'ta (1) yerel soket kapatma, veri bekleyen okumayı uyandırmaz ve
    (2) response.close(), okuyan thread bitene kadar bekler. Bu yüzden kontrol eden thread
    hiçbir zaman bu nesnelere dokunmaz; yanıtı kapatma işi okuyucu thread'in kendisine aittir.
    """

    def __init__(self, response: requests.Response, chunk_size: int):
        self._response = response
        self._queue: queue.Queue = queue.Queue(maxsize=8)   # sınırlı: yavaş tüketicide bellek şişmez
        self._stop = threading.Event()
        threading.Thread(target=self._run, args=(chunk_size,), daemon=True, name="net-reader").start()

    def _put(self, item) -> bool:
        while not self._stop.is_set():
            try:
                self._queue.put(item, timeout=0.2)
                return True
            except queue.Full:
                continue
        return False

    def _run(self, chunk_size: int) -> None:
        try:
            for chunk in self._response.iter_content(chunk_size):
                if not self._put(chunk):
                    return
            self._put(_EOF)
        except BaseException as e:  # noqa: BLE001 - hata tüketiciye taşınır
            self._put(e)
        finally:
            try:
                self._response.close()   # okuyan thread kendisi kapatır: kilit çekişmesi olmaz
            except Exception:
                pass

    def get(self, timeout: float):
        """Sıradaki parça, _EOF ya da istisna nesnesi. Süre dolarsa queue.Empty fırlatır."""
        return self._queue.get(timeout=timeout)

    def abandon(self) -> None:
        self._stop.set()


class _ConnectionGate:
    """
    Aynı anda kaç parçanın sunucuya bağlı olabileceğini sınırlar. Sınır indirme sürerken düşürülebilir:
    fazla parçalar kapıda sırasını bekler, diğerleri bitince devam eder (parça planı ve ilerleme değişmez).
    """

    def __init__(self, limit: int):
        self._cond = threading.Condition()
        self._limit = max(1, int(limit))
        self._active = 0

    @property
    def limit(self) -> int:
        return self._limit

    def set_limit(self, limit: int) -> None:
        with self._cond:
            self._limit = max(1, int(limit))
            self._cond.notify_all()

    def acquire(self, halt: threading.Event) -> bool:
        """Yer açılınca True; beklerken durdurma gelirse False döner (0,1 sn aralıkla kontrol eder)."""
        with self._cond:
            while self._active >= self._limit:
                if halt.is_set():
                    return False
                self._cond.wait(0.1)
            if halt.is_set():
                return False
            self._active += 1
            return True

    def release(self) -> None:
        with self._cond:
            self._active -= 1
            self._cond.notify_all()


@dataclass
class _Part:
    index: int
    start: int
    end: Optional[int]       # None: boyut bilinmiyor (tek akış)
    resumable: bool          # Range ile kaldığı yerden sürdürülebilir mi
    done: int = 0

    @property
    def length(self) -> Optional[int]:
        return None if self.end is None else self.end - self.start + 1

    @property
    def complete(self) -> bool:
        return self.length is not None and self.done >= self.length


class HttpEngine(BaseEngine):
    name = "http"
    cache_probe = False        # True: sondalama sonucu kısa süre saklanır (önceden sondalama için)

    # Ayarlanabilir sabitler (testlerde alt sınıfta değiştirilir)
    MIN_PART_SIZE = 2 * 1024 * 1024
    CHUNK_SIZE = 512 * 1024
    PART_TARGET = 16 * 1024 * 1024    # büyük dosyalar bu boyutta parçalara bölünür: biten bağlantı hemen yenisini alır
    MAX_PARTS = 64
    MAX_RETRIES = 5
    RETRY_BASE_DELAY = 1.0
    CONNECT_TIMEOUT = 10
    READ_TIMEOUT = 15
    PROBE_READ_TIMEOUT = 10
    STATE_SAVE_INTERVAL = 2.0
    THROTTLE_WINDOW = 10.0   # bu süre içinde en az 2 farklı parçadan "zorlanma" sinyali gelirse bağlantı sınırı yarıya iner
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        task: DownloadTask,
        callbacks: Optional[EngineCallbacks] = None,
        connections: int = 8,
        limiter: Optional[RateLimiter] = None,
    ):
        super().__init__(task, callbacks)
        self.connections = max(1, int(connections))
        self.limiter = limiter or RateLimiter(0)

        self._session: Optional[requests.Session] = None
        self._lock = threading.RLock()
        self._resume_evt = threading.Event()
        self._resume_evt.set()
        self._halt = threading.Event()
        self._halt_reason: Optional[str] = None   # "cancel" | "stop" | "error"
        self._error: Optional[Exception] = None
        self._running = False
        self._probed = False
        self.timing: dict = {}                 # aşama süreleri (sn): probe, first_byte
        self._t0 = time.monotonic()
        self._single_tried = False      # Range reddi sonrası tek bağlantıya düşme bir kez denenir
        self._header_mode = "normal"    # sondalamada tutan başlık düzeni (normal | self_referer | no_cookie)

        self._resp_lock = threading.Lock()
        self._open_responses: set = set()          # durdurulunca anında kapatılacak bağlantılar
        self._state_lock = threading.Lock()
        self._last_state_save = time.monotonic()

        self._supports_ranges = False
        self._final_url = task.url
        self._etag: Optional[str] = None
        self._last_modified: Optional[str] = None
        self._parts: list[_Part] = []

        self._throttle = ProgressThrottle(0.25)
        self._speed_t = time.monotonic()
        self._speed_bytes = 0

        # Sunucu zorlanınca eşzamanlı bağlantı sayısını otomatik düşürme
        self._host = urlparse(task.url).netloc.lower()
        self._gate: Optional[_ConnectionGate] = None
        self._strain_lock = threading.Lock()
        self._strain_seen: dict[int, float] = {}     # parça no -> son "zorlanma" sinyali zamanı
        self._last_shrink = float("-inf")
        self._strain_epoch = 0                       # sınır her düştüğünde artar

        # Aynı URL her zaman aynı temp klasörüne düşer: uygulama kapanıp açılınca devam edebilmek için
        self._ident = task.options.get("resume_key") or task.url    # adres yenilense de aynı geçici klasör
        key = hashlib.sha1(self._ident.encode("utf-8")).hexdigest()[:16]
        self._temp_dir = paths.temp_root(task.save_dir) / key
        self._data_path = self._temp_dir / "data.tmp"

    # ------------------------------------------------------------------ genel arayüz
    @classmethod
    def can_handle(cls, url: str) -> bool:
        return urlparse(url).scheme.lower() in ("http", "https")

    def probe(self) -> None:
        if self._probed:
            return
        self._set_status(TaskStatus.PROBING)
        key = self._probe_key()
        cached = PROBE_CACHE.take(key) if key else None
        if cached is not None:
            self._restore_probe(cached)
            self._probed = True
            return
        session = self._get_session()

        last_error: Optional[Exception] = None
        for attempt in range(3):
            if self._halt.is_set():
                raise _Halted()
            try:
                self._probe_once(session)
                if key and self.cache_probe:
                    PROBE_CACHE.put(key, self._snapshot_probe())
                self._probed = True
                return
            except (requests.ConnectionError, requests.Timeout) as e:
                last_error = e
                log.warning("Sondalama başarısız (%d/3): %s", attempt + 1, e)
                if self._halt.wait(min(self.RETRY_BASE_DELAY * (2 ** attempt), 10)):
                    raise _Halted()
        raise DownloadError(f"Sunucuya bağlanılamadı: {last_error}")

    def start(self) -> None:
        self._running = True
        self._t0 = time.monotonic()
        self.timing = {}
        try:
            self.probe()
            self.timing["probe"] = time.monotonic() - self._t0
            self._check_halt()
            self._prepare()
            self._check_halt()
            self._set_status(TaskStatus.DOWNLOADING)
            self._speed_t = time.monotonic()
            self._speed_bytes = self._downloaded()
            self._run_parts()
            if isinstance(self._error, _SingleStreamFallback) and self._halt_reason == "error":
                log.warning("Sunucu Range'li istekleri reddediyor; tek bağlantıyla baştan deneniyor: %s", self._host)
                self._reset_for_single_stream()
                self._prepare()
                self._check_halt()
                self._set_status(TaskStatus.DOWNLOADING)
                self._speed_t = time.monotonic()
                self._speed_bytes = self._downloaded()
                self._run_parts()

            if self._error:
                raise self._error
            self._check_halt()

            self._finalize()
            log.info("İndirme süreleri (sn): %s", {k: round(v, 2) for k, v in self.timing.items()})
        except _Halted:
            self._finish_halted()
        except DownloadError as e:
            if e.discard_temp:
                self._discard_temp()
            self._fail_status(str(e))
        except requests.RequestException as e:
            self._fail_status(f"Ağ hatası: {e}")
        except Exception as e:  # beklenmeyen hata uygulamayı çökertmesin
            log.exception("Beklenmeyen indirme hatası")
            self._fail_status(f"Beklenmeyen hata: {e}")
        finally:
            self._running = False
            if self._session is not None:
                self._session.close()
                self._session = None

    def pause(self) -> None:
        if self._halt.is_set() or self.task.status != TaskStatus.DOWNLOADING:
            return
        self._resume_evt.clear()
        self.task.speed = 0.0
        self._set_status(TaskStatus.PAUSED)

    def resume(self) -> None:
        if self._halt.is_set() or self.task.status != TaskStatus.PAUSED:
            return
        self._speed_t = time.monotonic()
        self._speed_bytes = self._downloaded()
        self.task.speed = 0.0
        self._resume_evt.set()
        self._set_status(TaskStatus.DOWNLOADING)

    def cancel(self) -> None:
        if self.task.status.is_finished:
            return
        self._request_halt("cancel")
        if not self._running:  # hiç başlamamışsa temizliği biz yaparız
            self._discard_temp()
            self.task.speed = 0.0
            self._set_status(TaskStatus.CANCELLED)

    def stop(self) -> None:
        if self.task.status.is_finished:
            return
        self._request_halt("stop")

    def discard_partial(self) -> None:
        self._discard_temp()

    # ------------------------------------------------------------------ bağlantı yönetimi
    def _interruptible(self, fn: Callable, cleanup: Optional[Callable] = None):
        """
        Bloke eden bir ağ çağrısını (bağlanma / başlık bekleme) ayrı thread'de çalıştırır.
        Durdurma gelirse beklemeyi hemen bırakır (_Halted); çağrı sonradan biterse sonucu cleanup ile kapatır.
        """
        box: dict = {}
        done = threading.Event()
        gate = threading.Lock()
        state = {"abandoned": False}

        def runner():
            try:
                value = fn()
            except BaseException as e:  # noqa: BLE001 - hata çağırana taşınır
                box["error"] = e
            else:
                with gate:
                    abandoned = state["abandoned"]
                    if not abandoned:
                        box["value"] = value
                if abandoned and cleanup:
                    try:
                        cleanup(value)
                    except Exception:
                        pass
            finally:
                done.set()

        threading.Thread(target=runner, daemon=True, name=f"net-{self.task.id}").start()
        while not done.wait(0.1):
            if self._halt.is_set():
                with gate:
                    state["abandoned"] = True
                    late = box.pop("value", None)
                if late is not None and cleanup:
                    try:
                        cleanup(late)
                    except Exception:
                        pass
                raise _Halted()
        if "error" in box:
            raise box["error"]
        return box.get("value")

    @contextmanager
    def _tracked(self, response: requests.Response):
        """Yanıtı kayda alır; durdurma gelirse bu yanıtın soketi hemen kapatılır."""
        with self._resp_lock:
            self._open_responses.add(response)
        try:
            if self._halt.is_set():
                self._force_close(response)
            yield
        finally:
            with self._resp_lock:
                self._open_responses.discard(response)

    @staticmethod
    def _force_close(response: requests.Response) -> None:
        """
        Soketi "en iyi çaba" ile kapatır (Linux'ta veri bekleyen okumayı hemen uyandırır).
        ASLA response.close() çağırmaz: okuma sürerken başka thread'den çağrılırsa bloke olur.
        Yanıtın asıl kapatılması _StreamReader'ın görevidir.
        """
        sock = None
        try:
            sock = response.raw._fp.fp.raw._sock
        except AttributeError:
            try:
                sock = response.raw._connection.sock
            except AttributeError:
                sock = None
        try:
            if sock is not None:
                sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _abort_connections(self) -> None:
        with self._resp_lock:
            responses = list(self._open_responses)
        for response in responses:
            self._force_close(response)

    # ------------------------------------------------------------------ sondalama
    def _base_headers(self, mode: Optional[str] = None) -> dict:
        headers = {
            "User-Agent": self.USER_AGENT,
            "Accept": "*/*",
            "Accept-Encoding": "identity",  # sıkıştırma olmasın, byte sayıları tutsun
        }
        headers.update(self.task.headers or {})
        mode = mode or self._header_mode
        if mode == "self_referer":
            parsed = urlparse(self.task.url)
            root = f"{parsed.scheme}://{parsed.netloc}"
            headers["Referer"] = root + "/"
            headers["Origin"] = root
        elif mode == "no_cookie":
            headers.pop("Cookie", None)
        return headers

    def _get_session(self) -> requests.Session:
        if self._session is None:
            session = requests.Session()
            adapter = HTTPAdapter(
                pool_connections=self.connections,
                pool_maxsize=self.connections + 2,
                max_retries=0,
            )
            session.mount("http://", adapter)
            session.mount("https://", adapter)
            user = self.task.options.get("auth_user")
            if user:
                session.auth = (user, self.task.options.get("auth_pass") or "")
            proxies = netconf.requests_proxies()
            if proxies:
                session.proxies.update(proxies)
            self._session = session
        return self._session

    def _probe_key(self):
        if self.task.options.get("source_hint"):
            return None                        # Drive vb.: onay sayfası akışı, önbelleğe alınmaz
        return (self.task.url, self.task.file_name or "", tuple(sorted((self.task.headers or {}).items())))

    def _snapshot_probe(self) -> dict:
        return {"final_url": self._final_url, "etag": self._etag, "last_modified": self._last_modified,
                "ranges": self._supports_ranges, "size": self.task.file_size, "name": self.task.file_name,
                "mode": self._header_mode}

    def _restore_probe(self, cached: dict) -> None:
        self._final_url = cached["final_url"]
        self._etag = cached["etag"]
        self._last_modified = cached["last_modified"]
        self._supports_ranges = cached["ranges"]
        self._header_mode = cached["mode"]
        self.task.file_size = cached["size"]
        self.task.file_name = cached["name"]

    def _probe_once(self, session: requests.Session) -> None:
        timeout = (self.CONNECT_TIMEOUT, self.PROBE_READ_TIMEOUT)
        # Sunucu erişimi reddederse (401/403/405/406) sırayla farklı başlık düzenleri denenir
        attempts = [(True, "normal"), (False, "normal"), (True, "self_referer"), (False, "self_referer")]
        if (self.task.headers or {}).get("Cookie"):
            attempts.append((False, "no_cookie"))
        refused = 0
        for use_range, mode in attempts:
            headers = self._base_headers(mode)
            if use_range:
                headers["Range"] = "bytes=0-0"
            r = self._interruptible(
                lambda: session.get(self.task.url, headers=headers, stream=True,
                                    timeout=timeout, allow_redirects=True),
                cleanup=lambda resp: resp.close(),
            )
            with r:
                if r.status_code in (200, 206):
                    if mode != "normal":
                        log.info("Sunucu '%s' başlık düzeniyle yanıt verdi: %s", mode, self._host)
                        self._header_mode = mode
                    if (self.task.options.get("source_hint") == "Google Drive"
                            and self._looks_like_web_page(r)):
                        confirm = self._drive_confirm(r)
                        if confirm:
                            log.info("Google Drive onay sayfası geçiliyor: %s", self._host)
                            self._probe_confirm(session, confirm, timeout)
                            return
                    self._read_probe_response(r)
                    return
                if r.status_code == 416 and use_range:
                    continue  # boş dosya olabilir, Range'siz tekrar dene
                if r.status_code in (401, 403, 405, 406):
                    refused = r.status_code
                    continue  # bir sonraki başlık düzenini dene
                message = _http_error_message(r.status_code)
                if r.status_code in (401, 403, 404, 429):
                    message = self._with_hint(message)
                raise DownloadError(message)
        if refused:
            message = _http_error_message(refused)
            if refused in (401, 403):
                message = self._with_hint(message)
            raise DownloadError(message)
        raise DownloadError("Sunucudan geçerli bir yanıt alınamadı.")

    def _drive_confirm(self, r: requests.Response) -> Optional[str]:
        """Drive'ın HTML cevabını okur: kota doluysa DownloadError, onay formu varsa indirme adresi döndürür."""
        body = b""
        for chunk in r.iter_content(65536):
            body += chunk
            if len(body) > 400_000:
                break
        text = body.decode("utf-8", "replace")
        if _DRIVE_QUOTA.search(text):
            raise DownloadError(
                "Google Drive bu dosya için günlük indirme kotasını doldurmuş. Tarayıcıda da aynı olur: "
                "yaklaşık 24 saat sonra tekrar dene ya da dosyayı kendi Drive'ına kopyalayıp oradan indir.")
        return parse_drive_form(text, r.url)

    def _probe_confirm(self, session: requests.Session, url: str, timeout: tuple) -> None:
        """Onay adresini sondalar; başarılıysa gerçek dosya bilgisi (ad, boyut, adres) alınır."""
        headers = self._base_headers()
        headers["Range"] = "bytes=0-0"
        r = self._interruptible(
            lambda: session.get(url, headers=headers, stream=True, timeout=timeout, allow_redirects=True),
            cleanup=lambda resp: resp.close(),
        )
        with r:
            if r.status_code in (200, 206):
                self._read_probe_response(r)
                return
            raise DownloadError(self._with_hint(_http_error_message(r.status_code)))

    def _looks_like_web_page(self, r: requests.Response) -> bool:
        """
        Cevap bir WEB SAYFASI mı (dosya değil)? Üç koşul birlikte aranır, böylece gerçekten
        .html dosyası indirmek isteyen kişi engellenmez:
          1) Content-Type html/xhtml, 2) "attachment" (indirme) başlığı yok, 3) adres .html/.htm ile bitmiyor.
        """
        ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if ctype not in _HTML_TYPES:
            return False
        if re.match(r"\s*attachment", r.headers.get("Content-Disposition", ""), re.I):
            return False
        for url in (self.task.url, r.url):
            if urlparse(url).path.lower().endswith((".html", ".htm")):
                return False
        return True

    def _with_hint(self, message: str) -> str:
        """Görev bilinen bir paylaşım sitesinden geldiyse hata mesajına siteye özel ipucunu ekler."""
        hint = _SOURCE_HINTS.get(self.task.options.get("source_hint", ""))
        return f"{message} {hint}" if hint else message

    def _read_probe_response(self, r: requests.Response) -> None:
        if self._looks_like_web_page(r):
            raise DownloadError(self._with_hint(
                "Bu bağlantı indirilebilir bir dosyaya değil, bir web sayfasına çıkıyor. "
                "Dosyanın doğrudan indirme adresini kullanın."
            ))
        h = r.headers
        self._final_url = r.url
        self._etag = h.get("ETag")
        self._last_modified = h.get("Last-Modified")

        size: Optional[int] = None
        supports = False
        if r.status_code == 206:
            m = re.match(r"bytes\s+0-0/(\d+)", h.get("Content-Range", ""))
            if m:
                size = int(m.group(1))
                supports = True
        else:
            cl = h.get("Content-Length", "")
            size = int(cl) if cl.isdigit() else None

        self._supports_ranges = supports
        self.task.file_size = size
        server_name = self._name_from_response(r)
        given = self.task.file_name
        if given and not _rules.ext_of(given) and _rules.ext_of(server_name):
            given += _rules.ext_of(server_name)      # elle verilen ad uzantısızsa sunucunun uzantısı korunur
        self.task.file_name = sanitize_filename(_rules.clean_name(given or server_name))

    def _name_from_response(self, r: requests.Response) -> str:
        cd = r.headers.get("Content-Disposition", "")
        name = ""

        m = re.search(r"filename\*\s*=\s*([^']*)'[^']*'([^;]+)", cd, re.I)
        if m:
            charset = m.group(1) or "utf-8"
            try:
                name = unquote(m.group(2).strip().strip('"'), encoding=charset)
            except LookupError:
                name = unquote(m.group(2).strip().strip('"'))
        else:
            m = re.search(r'filename\s*=\s*"?([^";]+)"?', cd, re.I)
            if m:
                name = m.group(1).strip()
                try:  # requests başlıkları latin-1 çözer; asıl UTF-8 olabilir
                    name = name.encode("latin-1").decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass

        if not name:
            name = unquote(os.path.basename(urlparse(r.url).path))

        if name and "." not in name:
            ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if ctype and ctype != "application/octet-stream":
                ext = mimetypes.guess_extension(ctype)
                if ext:
                    name += ext
        return name

    # ------------------------------------------------------------------ hazırlık
    def _prepare(self) -> None:
        task = self.task
        task.save_dir.mkdir(parents=True, exist_ok=True)
        root = self._temp_dir.parent
        root.mkdir(parents=True, exist_ok=True)
        hide_on_windows(root)

        size = task.file_size
        resumable = bool(self._supports_ranges and size)

        if resumable:
            state = self._load_state()
            if state and self._state_matches(state) and self._data_covers(state["ranges"], state["progress"]):
                ranges = [tuple(x) for x in state["ranges"]]
                progress = list(state["progress"])
                log.info("Önceki indirme bulundu, devam ediliyor: %s", task.file_name)
            else:
                self._discard_temp()
                ranges = self._plan_ranges(size)
                progress = [0] * len(ranges)
            self._parts = [
                _Part(i, s, e, True, done=progress[i]) for i, (s, e) in enumerate(ranges)
            ]
        else:
            # Range yok: devam ettirme mümkün değil, her seferinde baştan
            self._discard_temp()
            end = size - 1 if size is not None else None
            self._parts = [_Part(0, 0, end, False)]

        self.task.downloaded = self._downloaded()

        if size:
            needed = size - self._downloaded()
            if not has_enough_space(task.save_dir, needed):
                raise DownloadError("Diskte yeterli boş alan yok.")

        self._temp_dir.mkdir(parents=True, exist_ok=True)
        if resumable:
            self._ensure_data_file()
            self._save_state(flush=False)      # henüz veri yok: diske senkron beklemeye gerek yok

    def _plan_ranges(self, size: int) -> list[tuple[int, int]]:
        n = max(1, min(self.connections, size // self.MIN_PART_SIZE))
        if size > self.connections * self.PART_TARGET:
            n = min(self.MAX_PARTS, -(-size // self.PART_TARGET))
        base = size // n
        ranges = []
        for i in range(n):
            start = i * base
            end = size - 1 if i == n - 1 else start + base - 1
            ranges.append((start, end))
        return ranges

    def _data_covers(self, ranges, progress) -> bool:
        """Kaydedilmiş ilerleme, diskteki veri dosyasıyla tutarlı mı? (dosya yeterince büyük olmalı)"""
        needed = max((start + done for (start, _end), done in zip(ranges, progress) if done > 0), default=0)
        if needed == 0:
            return True          # hiç ilerleme yok: veri dosyası önemsiz
        try:
            return self._data_path.stat().st_size >= needed
        except OSError:
            return False

    def _ensure_data_file(self) -> None:
        """
        Boş, seyrek işaretli veri dosyasını oluşturur (varsa dokunmaz). BOYUTLANDIRMA YAPILMAZ:
        parçalar uzak konumlara yazdıkça dosya kendiliğinden büyür; seyrek işaret sayesinde
        aradaki boşluk diskte yer tutmaz ve sıfırla doldurulmaz.
        """
        if self._data_path.exists():
            return
        try:
            with open(self._data_path, "wb") as f:
                sparse = set_sparse(f)
            if os.name == "nt" and not sparse:
                log.warning("Seyrek dosya işareti uygulanamadı; büyük indirmeler yavaş başlayabilir.")
        except OSError as e:
            raise DownloadError(f"Geçici dosya oluşturulamadı: {e}")

    def _state_file(self) -> Path:
        return self._temp_dir / "state.json"

    def _load_state(self) -> Optional[dict]:
        try:
            return json.loads(self._state_file().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def _save_state(self, flush: bool = True) -> None:
        """Parça ilerlemesini diske yazar (önce veri diske yazılır, sonra ilerleme kaydedilir)."""
        if not self._parts or not self._parts[0].resumable or not self.task.file_size:
            return
        with self._state_lock:
            try:
                if flush:
                    self._flush_data()
                state = {
                    "version": STATE_VERSION,
                    "url": self._ident,
                    "file_size": self.task.file_size,
                    "etag": self._etag,
                    "last_modified": self._last_modified,
                    "ranges": [[p.start, p.end] for p in self._parts],
                    "progress": [p.done for p in self._parts],
                }
                tmp = self._state_file().with_suffix(".tmp")
                tmp.write_text(json.dumps(state), encoding="utf-8")
                os.replace(tmp, self._state_file())
            except OSError as e:
                log.warning("İlerleme kaydedilemedi: %s", e)

    def _flush_data(self) -> None:
        """Veri dosyasındaki yazılanların gerçekten diske ulaşmasını sağlar (elektrik kesintisine karşı)."""
        try:
            with open(self._data_path, "r+b") as f:
                os.fsync(f.fileno())
        except OSError:
            pass

    def _state_matches(self, state: dict) -> bool:
        """Sunucudaki dosya aynı mı ve parça planı/ilerlemesi tutarlı mı?"""
        try:
            if (
                state.get("version") != STATE_VERSION
                or state.get("url") != self._ident
                or state.get("file_size") != self.task.file_size
                or state.get("etag") != self._etag
                or state.get("last_modified") != self._last_modified
            ):
                return False
            ranges, progress = state["ranges"], state["progress"]
            if len(ranges) != len(progress):
                return False
            expected_start = 0
            for (s, e), done in zip(ranges, progress):
                if s != expected_start or e < s:
                    return False
                if not isinstance(done, int) or done < 0 or done > e - s + 1:
                    return False
                expected_start = e + 1
            return expected_start == self.task.file_size
        except (KeyError, TypeError, ValueError):
            return False

    # ------------------------------------------------------------------ indirme
    def _run_parts(self) -> None:
        pending = [p for p in self._parts if not p.complete]
        limit = min(len(pending), self.connections)
        remembered = _remembered_limit(self._host)
        if remembered:                                   # bu sunucudan önce öğrenilen sınırla başla
            limit = min(limit, remembered)
        self._gate = _ConnectionGate(limit)
        try:
            if pending:
                with ThreadPoolExecutor(
                    max_workers=len(pending), thread_name_prefix=f"http-{self.task.id}"
                ) as pool:
                    futures = [pool.submit(self._part_worker, p) for p in pending]
                    for fut in as_completed(futures):
                        try:
                            fut.result()
                        except Exception as e:
                            self._fail(e)
        finally:
            self._save_state()   # durdurma/hata/bitiş: kaldığımız yer kesin kaydedilir
        self._report(force=True)

    def _part_worker(self, part: _Part) -> None:
        failures = 0
        epoch = self._strain_epoch
        while not self._halt.is_set():
            if not self._wait_if_paused():
                return
            if part.complete:
                return
            before = part.done
            try:
                outcome = self._stream_gated(part)
            except _Halted:
                return
            except (requests.RequestException, _PartError, ConnectionError, TimeoutError) as e:
                if self._halt.is_set():
                    return
                if getattr(e, "status", 0) in (401, 403) and not self._single_tried and self._downloaded() == 0:
                    with self._strain_lock:
                        first = not self._single_tried
                        self._single_tried = True
                    if first:                    # hiç veri inmeden Range reddedildi: Range'siz tek akışla baştan dene
                        raise _SingleStreamFallback()
                if part.done > before:
                    failures = 0  # ilerleme olduysa sayaç sıfırlanır
                if _is_server_strain(e):
                    self._note_strain(part.index)
                if self._strain_epoch != epoch:   # bağlantı sınırı düştü: koşul değişti, yeni deneme hakkı
                    epoch = self._strain_epoch
                    failures = 0
                failures += 1
                if failures > self.MAX_RETRIES:
                    raise DownloadError(f"Parça {part.index + 1} indirilemedi: {e}")
                delay = min(self.RETRY_BASE_DELAY * (2 ** (failures - 1)), 30)
                if getattr(e, "status", 0) == 429:        # sunucu "çok fazla istek" diyor: hemen tekrar zorlama
                    delay = max(delay, min(30.0, 5.0 * failures))
                log.warning("Parça %d hata verdi (%s). %.1fs sonra tekrar (%d/%d)",
                            part.index + 1, e, delay, failures, self.MAX_RETRIES)
                if self._halt.wait(delay):
                    return
                continue
            except Exception:
                if self._halt.is_set():
                    return   # durdurma sırasında kapatılan bağlantıların hataları önemsizdir
                raise
            if outcome == "done":
                return
            # "paused" veya "halt": döngü başı duruma göre bekler ya da çıkar

    def _stream_gated(self, part: _Part) -> str:
        """Parçayı, eşzamanlı bağlantı sınırına (kapı) uyarak indirir. Kapıda beklerken durdurma anında etki eder."""
        gate = self._gate
        if gate is None:
            return self._stream_part(part)
        if not gate.acquire(self._halt):
            return "halt"
        try:
            return self._stream_part(part)
        finally:
            gate.release()

    def _note_strain(self, part_index: int) -> None:
        """
        Bir parça "sunucu zorlanıyor" sinyali verdi (zaman aşımı, kopma, 429/503). THROTTLE_WINDOW içinde
        en az 2 FARKLI parçadan sinyal gelirse eşzamanlı bağlantı sınırı yarıya iner (en az 1).
        Pencere başına en fazla bir düşürme yapılır; tek parçanın kopması düşürmez.
        """
        gate = self._gate
        if gate is None:
            return
        with self._strain_lock:
            now = time.monotonic()
            self._strain_seen[part_index] = now
            if now - self._last_shrink < self.THROTTLE_WINDOW or gate.limit <= 1:
                return
            recent = [i for i, t in self._strain_seen.items() if now - t <= self.THROTTLE_WINDOW]
            if len(recent) < 2:
                return
            old = gate.limit
            new = max(1, old // 2)
            self._last_shrink = now
            self._strain_epoch += 1
            self._strain_seen.clear()
            gate.set_limit(new)
        _remember_limit(self._host, new)
        log.warning("Sunucu bağlantıları zorlanıyor (%s): eşzamanlı bağlantı %d → %d", self._host, old, new)

    def _stream_part(self, part: _Part) -> str:
        """Dönüş: 'done' | 'paused' | 'halt'. Hata durumunda istisna fırlatır."""
        headers = self._base_headers()
        if part.resumable:
            first = part.start + part.done
            headers["Range"] = f"bytes={first}-{part.end}"
        else:
            first = 0
            part.done = 0

        session = self._get_session()
        r = self._interruptible(
            lambda: session.get(self._final_url, headers=headers, stream=True,
                                timeout=(self.CONNECT_TIMEOUT, self.READ_TIMEOUT)),
            cleanup=lambda resp: resp.close(),
        )

        reader: Optional[_StreamReader] = None
        try:
            with self._tracked(r):
                if part.resumable:
                    if r.status_code != 206:
                        raise _PartError(f"Sunucu 206 yerine HTTP {r.status_code} döndürdü",
                                         strain=r.status_code in _STRAIN_STATUSES, status=r.status_code)
                    self._check_content_range(r, first)
                elif r.status_code != 200:
                    raise _PartError(f"HTTP {r.status_code}", strain=r.status_code in _STRAIN_STATUSES)

                try:
                    # buffering=0: yazılanlar uygulama tamponunda beklemez, ilerleme sayacı gerçeği yansıtır
                    f = open(self._data_path, "r+b" if part.resumable else "wb", buffering=0)
                except OSError as e:
                    raise DownloadError(f"Dosya açılamadı: {e}")

                with f:
                    if part.resumable:
                        f.seek(first)
                    reader = _StreamReader(r, self.CHUNK_SIZE)   # bundan sonra r'ye yalnızca okuyucu dokunur
                    while True:
                        if self._halt.is_set():
                            return "halt"
                        if not self._resume_evt.is_set():
                            if part.resumable:
                                return "paused"  # bağlantıyı bırak, devam edince kaldığı yerden açılır
                            if not self._wait_if_paused():
                                return "halt"
                        try:
                            item = reader.get(0.1)
                        except queue.Empty:
                            continue                 # veri yok: durdurma/duraklatma isteğini tekrar kontrol et
                        if item is _EOF:
                            break
                        if isinstance(item, BaseException):
                            raise item
                        chunk = item
                        if not chunk:
                            continue
                        if part.length is not None:
                            room = part.length - part.done
                            if len(chunk) > room:
                                chunk = chunk[:room]
                        try:
                            view = memoryview(chunk)
                            while view:
                                written = f.write(view)
                                view = view[written:]
                        except OSError as e:
                            raise DownloadError(f"Diske yazılamadı: {e}")
                        part.done += len(chunk)
                        if "first_byte" not in self.timing:
                            self.timing["first_byte"] = time.monotonic() - self._t0
                        self.limiter.acquire(len(chunk), self._halt)
                        self._report()
                        if part.length is not None and part.done >= part.length:
                            return "done"
        finally:
            if reader is not None:
                reader.abandon()        # okuyucu kendi başına kapanır; biz asla beklemeyiz
            else:
                try:
                    r.close()           # henüz kimse okumuyordu: güvenle kapatılabilir
                except Exception:
                    pass

        if self._halt.is_set():
            return "halt"
        if part.length is None:
            return "done"  # boyutu bilinmeyen akış: sunucu kapattıysa bitmiştir
        raise _PartError("Bağlantı beklenenden erken kapandı", strain=True)
    @staticmethod
    def _check_content_range(r: requests.Response, expected_start: int) -> None:
        m = re.match(r"bytes\s+(\d+)-(\d+)/", r.headers.get("Content-Range", ""))
        if not m or int(m.group(1)) != expected_start:
            raise _PartError(
                f"Content-Range beklenenle uyuşmuyor: {r.headers.get('Content-Range')!r}"
            )

    # ------------------------------------------------------------------ bitiş
    def _finalize(self) -> None:
        task = self.task
        parts = self._parts

        if parts[0].resumable:
            if not all(p.complete for p in parts):
                raise DownloadError("Parçalar eksik kaldı.", discard_temp=True)
            actual = task.file_size
        else:
            actual = parts[0].done
            if task.file_size is not None and actual != task.file_size:
                raise DownloadError(
                    f"Dosya boyutu uyuşmuyor (beklenen {task.file_size}, gelen {actual}).",
                    discard_temp=True,
                )

        if not self._data_path.exists():
            self._data_path.touch()          # boş (0 bayt) dosya
        if self._data_path.stat().st_size != actual:
            raise DownloadError("Geçici dosya boyutu beklenenle uyuşmuyor.", discard_temp=True)
        self._flush_data()
        task.file_size = actual

        target_dir = task.save_dir
        if task.options.get("auto_dir"):
            target_dir = _rules.pick_dir(task.url, task.file_name, task.save_dir)
        if task.options.get("sort_by_category"):
            target_dir = category_folder(target_dir, task.file_name)
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise DownloadError(f"Klasör oluşturulamadı: {e}")
        final = reserve_unique_path(target_dir / task.file_name)
        try:
            os.replace(self._data_path, final)
        except OSError as e:
            try:
                final.unlink()  # ayırdığımız boş yer tutucuyu temizle
            except OSError:
                pass
            raise DownloadError(move_error_message(e))

        task.file_name = final.name
        task.final_path = final
        task.downloaded = actual
        task.speed = 0.0
        self._discard_temp()
        self._notify_progress()
        self._set_status(TaskStatus.COMPLETED)

    # ------------------------------------------------------------------ yardımcılar
    def _downloaded(self) -> int:
        return sum(p.done for p in self._parts)

    def _report(self, force: bool = False) -> None:
        if not force and not self._throttle.ready():
            return
        save_due = False
        with self._lock:
            now = time.monotonic()
            total = self._downloaded()
            dt = now - self._speed_t
            if dt > 0 and self._resume_evt.is_set():
                instant = (total - self._speed_bytes) / dt
                prev = self.task.speed
                self.task.speed = instant if prev <= 0 else 0.6 * prev + 0.4 * instant
            self._speed_t = now
            self._speed_bytes = total
            self.task.downloaded = total
            if not force and now - self._last_state_save >= self.STATE_SAVE_INTERVAL:
                self._last_state_save = now
                save_due = True
        if save_due:
            self._save_state()
        self._notify_progress()

    def _wait_if_paused(self) -> bool:
        """Duraklatıldıysa bekler. İptal/durdurma gelirse False döner."""
        while not self._resume_evt.is_set():
            if self._halt.is_set():
                return False
            self._resume_evt.wait(0.2)
        return not self._halt.is_set()

    def _request_halt(self, reason: str) -> None:
        with self._lock:
            if self._halt_reason is None:
                self._halt_reason = reason
            self._halt.set()
            self._resume_evt.set()  # bekleyen thread'leri uyandır
        self._abort_connections()   # veri bekleyen bağlantılar anında kopar

    def _fail(self, error: Exception) -> None:
        """İşçi thread'lerden gelen ilk hatayı kaydeder ve diğerlerini durdurur."""
        with self._lock:
            if self._halt.is_set():
                return  # iptal/durdurma sırasında oluşan hatalar yok sayılır
            self._error = error
            self._request_halt("error")

    def _reset_for_single_stream(self) -> None:
        """Hata nedeniyle verilen durdurma kararını geri alır ve Range'siz tek akış planına geçer."""
        with self._lock:
            self._error = None
            self._halt_reason = None
            self._halt.clear()
            self._resume_evt.set()
        self._supports_ranges = False
        self._gate = None
        self._strain_seen.clear()
        self._parts = []

    def _check_halt(self) -> None:
        if self._halt.is_set():
            raise _Halted()

    def _finish_halted(self) -> None:
        self.task.speed = 0.0
        if self._halt_reason == "stop":
            self._set_status(TaskStatus.PAUSED)  # temp korunur
        else:
            self._discard_temp()
            self.task.downloaded = 0
            self._set_status(TaskStatus.CANCELLED)

    def _fail_status(self, message: str) -> None:
        self.task.speed = 0.0
        log.error("İndirme başarısız (%s): %s", self.task.file_name or self.task.url, message)
        self._set_status(TaskStatus.ERROR, message)

    def _discard_temp(self) -> None:
        shutil.rmtree(self._temp_dir, ignore_errors=True)