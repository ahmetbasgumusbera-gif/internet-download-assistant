"""
Tanı aracı (Qt yok): sistemi, araçları, ağı ve gerçek indirme motorlarını (HTTP, yt-dlp) otomatik dener; ölçümleri toplar,
bulguları çıkarır ve bir rapor metni üretir. Hiçbir kullanıcı dosyasına dokunmaz: testler geçici klasörde yapılır ve silinir.
"""
from __future__ import annotations

import dataclasses
import os
import platform
import re
import shutil
import socket
import ssl
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from core import appupdate, resolver, tools, urlcheck, youtube
from core.engines.http_engine import HttpEngine
from core.engines.ytdlp_engine import YtdlpEngine
from core.events import EngineCallbacks
from core.models import DownloadTask, TaskStatus
from utils import paths
from utils.helpers import format_size, format_speed
from utils.logger import get_logger
from utils.version import __version__

log = get_logger(__name__)

YT_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"       # YouTube'un ilk videosu: kısa ve kalıcı
NET_HOSTS = ("github.com", "api.github.com", "www.youtube.com", "drive.usercontent.google.com", "speed.cloudflare.com")
SPEED_CANDIDATES = (
    ("OVH", "https://proof.ovh.net/files/100Mb.dat"),
    ("Hetzner", "https://fsn1-speed.hetzner.com/100MB.bin"),
    ("Cloudflare", "https://speed.cloudflare.com/__down?bytes=100000000"),
)
MB = 1024 * 1024


# ====================================================================== rapor klasörü
def report_dir(config) -> Path:
    """Ayardaki klasör; boşsa kurulum klasörü\\tani; yazılamıyorsa kullanıcı verisi\\tani."""
    candidates = (str(config.get("diag_dir") or "").strip(), str(paths.ROOT_DIR / "tani"), str(paths.DATA_DIR / "tani"))
    for candidate in candidates:
        if not candidate:
            continue
        folder = Path(candidate)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / ".yazma-denemesi"
            probe.write_text("x", encoding="utf-8")
            probe.unlink()
            return folder
        except OSError:
            continue
    return paths.DATA_DIR


def save_report(text: str, config) -> Path:
    path = report_dir(config) / f"IDA-tani-{datetime.now():%Y%m%d-%H%M%S}.txt"
    path.write_text(text, encoding="utf-8-sig")
    return path


# ====================================================================== ölçüm yardımcıları
def _work_root() -> Path:
    root = paths.DATA_DIR / "diag"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _timed(fn):
    start = time.monotonic()
    try:
        value = fn()
    except Exception as e:
        value = f"hata: {e}"
    return value, time.monotonic() - start


def _net_test(host: str, port: int = 443, timeout: float = 6.0) -> dict:
    out: dict = {"host": host}
    sock = None
    try:
        t = time.monotonic()
        info = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)[0]
        out["dns_ms"] = int((time.monotonic() - t) * 1000)
        sock = socket.socket(info[0], socket.SOCK_STREAM)
        sock.settimeout(timeout)
        t = time.monotonic()
        sock.connect(info[4])
        out["tcp_ms"] = int((time.monotonic() - t) * 1000)
        t = time.monotonic()
        secure = ssl.create_default_context().wrap_socket(sock, server_hostname=host)
        out["tls_ms"] = int((time.monotonic() - t) * 1000)
        secure.close()
        sock = None
    except (OSError, ssl.SSLError) as e:
        out["error"] = str(e)
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
    return out


def _run_engine(engine, cancel: threading.Event, limit: float) -> bool:
    """Motoru çalıştırır; iptal ya da süre dolunca durdurur. Zaman aşımı olduysa True."""
    worker = threading.Thread(target=engine.start, daemon=True)
    worker.start()
    deadline = time.monotonic() + limit
    while worker.is_alive():
        worker.join(0.3)
        if cancel.is_set() or time.monotonic() > deadline:
            timed_out = not cancel.is_set()
            engine.cancel()
            worker.join(15)
            return timed_out
    return False


def _http_case(label: str, url: str, connections: int, cancel: threading.Event, group: str, cap: int = 0,
               source_hint: str = "", limit: float = 90.0) -> dict:
    """Gerçek HTTP motoruyla (en fazla cap bayt) bir indirme dener ve aşama sürelerini ölçer."""
    folder = Path(tempfile.mkdtemp(prefix="idadiag_", dir=_work_root()))
    task = DownloadTask(url=url, save_dir=folder)
    if source_hint:
        task.options["source_hint"] = source_hint
    marks: dict = {"start": time.monotonic()}
    box: dict = {}

    def on_status(t) -> None:
        now = time.monotonic()
        if t.status == TaskStatus.DOWNLOADING:
            marks.setdefault("ready", now)

    def on_progress(t) -> None:
        now = time.monotonic()
        if t.downloaded > 0:
            marks.setdefault("first", (now, t.downloaded))
        marks["last"] = (now, t.downloaded)
        if cap and t.downloaded >= cap and not box.get("capped"):
            box["capped"] = True
            threading.Thread(target=box["engine"].cancel, daemon=True).start()

    engine = HttpEngine(task, EngineCallbacks(on_status=on_status, on_progress=on_progress),
                        connections=connections, limiter=None)
    box["engine"] = engine
    timed_out = _run_engine(engine, cancel, limit)
    status = task.status
    ok = status == TaskStatus.COMPLETED or (bool(box.get("capped")) and status == TaskStatus.CANCELLED)
    start = marks["start"]
    result = {
        "group": group, "label": label, "url": url, "connections": connections, "ok": ok,
        "error": "" if ok else ("zaman aşımı" if timed_out else ("iptal edildi" if cancel.is_set()
                                                                  else (task.error or status.value))),
        "ready_s": (marks["ready"] - start) if "ready" in marks else None,
        "first_byte_s": (marks["first"][0] - start) if "first" in marks else None,
        "bytes": marks["last"][1] if "last" in marks else 0, "size": task.file_size or 0,
        "speed": None, "capped": bool(box.get("capped")),
        "engine_timing": {k: round(v, 2) for k, v in getattr(engine, "timing", {}).items()},
    }
    if "first" in marks and "last" in marks and marks["last"][0] - marks["first"][0] > 0.3:
        result["speed"] = (marks["last"][1] - marks["first"][1]) / (marks["last"][0] - marks["first"][0])
    shutil.rmtree(folder, ignore_errors=True)
    return result


def _ytdlp_case(label: str, info, sel, cancel: threading.Event, limit: float = 240.0,
                use_info_file: bool = False) -> dict:
    folder = Path(tempfile.mkdtemp(prefix="idadiag_", dir=_work_root()))
    try:
        args = youtube.build_args(info, sel)
    except ValueError as e:
        shutil.rmtree(folder, ignore_errors=True)
        return {"label": label, "ok": False, "error": str(e)}
    task = DownloadTask(url=info.url or YT_URL, save_dir=folder, engine="ytdlp", title=info.title,
                        options={"args": args, "expected_size": youtube.estimate_size(info, sel),
                                "info_file": getattr(info, "info_file", "") if use_info_file else ""})
    start = time.monotonic()
    marks: dict = {}

    def on_status(t) -> None:
        now = time.monotonic()
        if t.status == TaskStatus.DOWNLOADING:
            marks.setdefault("downloading", now)
        elif t.status == TaskStatus.MERGING:
            marks.setdefault("merging", now)
        elif t.status == TaskStatus.COMPLETED:
            marks.setdefault("done", now)

    engine = YtdlpEngine(task, EngineCallbacks(on_status=on_status), connections=4)
    timed_out = _run_engine(engine, cancel, limit)
    ok = task.status == TaskStatus.COMPLETED and bool(task.final_path) and Path(task.final_path).is_file()
    end = marks.get("done", time.monotonic())
    result = {
        "label": label, "ok": ok, "size": task.file_size or 0, "total_s": end - start,
        "startup_s": (marks["downloading"] - start) if "downloading" in marks else None,
        "download_s": ((marks.get("merging") or end) - marks["downloading"]) if "downloading" in marks else None,
        "merge_s": (end - marks["merging"]) if "merging" in marks else None,
        "error": "" if ok else ("zaman aşımı" if timed_out else ("iptal edildi" if cancel.is_set()
                                                                  else (task.error or task.status.value))),
    }
    shutil.rmtree(folder, ignore_errors=True)
    return result


def _http_case_retry(*args, **kwargs) -> dict:
    """Test sunucusu art arda isteklere 429 verirse 20 sn bekleyip bir kez daha dener."""
    cancel = args[3]
    cancel.wait(4)                                     # ardışık testler arasında kısa ara
    result = _http_case(*args, **kwargs)
    if not result.get("ok") and "429" in str(result.get("error")) and not cancel.is_set():
        cancel.wait(20)
        retry = _http_case(*args, **kwargs)
        retry["retried"] = True
        return retry
    return result


def _first_reachable(candidates) -> tuple[str, str, int]:
    for name, url in candidates:
        info = urlcheck.probe(url, None, timeout=8)
        if info.get("ok") and not info.get("page"):
            return name, url, int(info.get("size") or 0)
    return "", "", 0


# ====================================================================== ana akış
def run_all(config, say: Callable[[str], None], cancel: threading.Event, extra_urls=()) -> str:
    """Tüm testleri çalıştırır ve rapor metnini döndürür. say(metin) ile canlı ilerleme bildirilir."""
    started = time.monotonic()
    default_conn = max(1, int(config.get("connections_per_download")))
    data: dict = {"started": datetime.now().isoformat(timespec="seconds"), "http": [], "net": [], "youtube": {}}

    # ---- sistem
    say("Sistem bilgisi toplanıyor…")
    download_dir = Path(config.download_dir())
    probe_dir = download_dir
    while not probe_dir.exists() and probe_dir != probe_dir.parent:
        probe_dir = probe_dir.parent
    try:
        free = shutil.disk_usage(probe_dir).free
    except OSError:
        free = None
    data["system"] = {
        "app": __version__, "frozen": paths.FROZEN, "python": platform.python_version(), "os": platform.platform(),
        "cpu": os.cpu_count(), "root": str(paths.ROOT_DIR), "data": str(paths.DATA_DIR),
        "download_dir": str(download_dir), "disk_free": free, "js_runtime": youtube.js_runtime_status(),
        "proxy": [k for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy") if os.environ.get(k)],
        "config": {k: config.get(k) for k in ("connections_per_download", "max_concurrent_downloads",
                                              "speed_limit_kbps", "auto_tune_connections", "auto_retry",
                                              "youtube_use_cookies", "youtube_prefetch", "download_mode")},
    }

    # ---- araçlar (ilk çalıştırma süresi, antivirüs gecikmesini gösterir)
    say("Araçlar denetleniyor (yt-dlp, ffmpeg, Deno)…")
    data["tools"] = {}
    for name, fn in (("yt-dlp", youtube.ytdlp_version), ("ffmpeg", tools.ffmpeg_version), ("deno", tools.deno_version)):
        path = tools.tool_path(name, use_path=(name != "yt-dlp"))
        version, seconds = _timed(fn)
        entry = {"path": str(path) if path else None, "version": version, "seconds": seconds}
        if name == "yt-dlp":
            entry["warm_seconds"] = _timed(fn)[1]
        data["tools"][name] = entry

    # ---- ağ
    for host in NET_HOSTS:
        if cancel.is_set():
            break
        say(f"Ağ: {host}…")
        data["net"].append(_net_test(host))

    # ---- HTTP indirmeleri
    release = None
    if not cancel.is_set():
        say("Sürüm bilgisi alınıyor (GitHub)…")
        try:
            release = appupdate.check("0.0.0")
        except appupdate.UpdateError as e:
            data["http"].append({"group": "small", "label": "GitHub sürüm bilgisi", "ok": False, "error": str(e)})
    if release and not cancel.is_set():
        zip_url = f"https://github.com/{appupdate.REPO}/archive/refs/tags/v{release['version']}.zip"
        say("Küçük dosya (zip) deneniyor…")
        data["http"].append(_http_case("Küçük dosya (zip, GitHub)", zip_url, default_conn, cancel, "small"))
        if not cancel.is_set():
            say("Küçük dosya (önceden sondalamayla) deneniyor…")
            warm_engine = HttpEngine(DownloadTask(url=zip_url, save_dir=Path(tempfile.gettempdir())), None, connections=1)
            warm_engine.cache_probe = True
            try:
                warm_engine.probe()
            except Exception:
                pass
            finally:
                if warm_engine._session is not None:
                    warm_engine._session.close()
            data["http"].append(_http_case("Küçük dosya (önceden sondalamayla)", zip_url, default_conn, cancel, "warm"))
        if not cancel.is_set():
            say("Büyük dosya (exe, Setup'ın ilk 30 MB'ı) deneniyor…")
            data["http"].append(_http_case("Büyük dosya (exe, GitHub release)", release["url"], default_conn,
                                           cancel, "exe", cap=30 * MB))
    if not cancel.is_set():
        say("Hız testi sunucusu aranıyor…")
        name, url, size = ("GitHub", release["url"], 0) if release else _first_reachable(SPEED_CANDIDATES)
        if url:
            for n in sorted({1, 4, default_conn}):
                if cancel.is_set():
                    break
                say(f"Hız testi ({name}): {n} bağlantı…")
                data["http"].append(_http_case_retry(f"{name} hız testi", url, n, cancel, "speed", cap=40 * MB))
        else:
            data["http"].append({"group": "speed", "label": "Hız testi sunucusu", "ok": False,
                                 "error": "OVH, Hetzner ve Cloudflare test sunucularına ulaşılamadı"})
    for i, link in enumerate([u.strip() for u in extra_urls if u.strip()], 1):
        if cancel.is_set():
            break
        say(f"Kendi bağlantın {i}: deneniyor…")
        try:
            resolved = resolver.resolve(link)
        except resolver.UnsupportedLink as e:
            data["http"].append({"group": "custom", "label": f"Kendi bağlantın {i}", "url": link, "ok": False,
                                 "error": str(e)})
            continue
        data["http"].append(_http_case(f"Kendi bağlantın {i}" + (f" ({resolved.source})" if resolved.source else ""),
                                       resolved.url, default_conn, cancel, "custom", cap=30 * MB,
                                       source_hint=resolved.source))

    # ---- YouTube
    yt: dict = data["youtube"]
    if not cancel.is_set():
        say("YouTube: analiz ediliyor…")
        info = None
        start = time.monotonic()
        try:
            info = youtube.analyze(YT_URL, timeout=120)
        except youtube.AnalysisError as e:
            yt["analyze_error"] = str(e)
        except Exception as e:
            yt["analyze_error"] = f"{e}"
        yt["analyze_s"] = time.monotonic() - start
        if info is not None:
            heights = youtube.available_heights(info)
            yt["formats"], yt["heights"] = len(info.formats), heights
            sel = youtube.default_selection(info)
            wanted = [h for h in heights if h >= 360]
            sel.height = min(wanted) if wanted else (min(heights) if heights else None)
            if sel.height:
                options = youtube.fps_options(info, sel.height)
                sel.fps = 30 if 30 in options else (60 if options else None)
            if not cancel.is_set():
                say("YouTube: kısa video indiriliyor…")
                yt["download"] = _ytdlp_case("Video + ses", info, sel, cancel)
                if getattr(info, "info_file", "") and not cancel.is_set():
                    say("YouTube: analiz dosyasıyla indirme…")
                    yt["fast"] = _ytdlp_case("Video + ses (analiz dosyasıyla)", info, sel, cancel,
                                             use_info_file=True)
            langs = [c for c, _ in info.subtitle_langs] or [c for c, _ in info.auto_caption_langs if c in ("en", "tr")]
            if langs and not cancel.is_set():
                say(f"YouTube: altyazılı ({langs[0]}) indirme…")
                yt["subs"] = _ytdlp_case("Altyazılı", info, dataclasses.replace(sel, subtitles=[langs[0]]), cancel)
                yt["subs"]["lang"] = langs[0]
            elif not langs:
                yt["subs_note"] = "Test videosunda altyazı yok, altyazı testi atlandı."

    data["cancelled"] = cancel.is_set()
    data["elapsed"] = time.monotonic() - started
    shutil.rmtree(_work_root(), ignore_errors=True)
    return render(data, findings(data), _log_tail())


# ====================================================================== bulgular
def findings(data: dict) -> list[str]:
    notes: list[str] = []
    system = data.get("system") or {}
    free = system.get("disk_free")
    if isinstance(free, int) and free < 3 * 1024 ** 3:
        notes.append(f"[!] İndirme sürücüsünde yalnızca {format_size(free)} boş alan var.")
    if system.get("proxy"):
        notes.append(f"[i] Proxy ortam değişkeni tanımlı ({', '.join(system['proxy'])}): bağlantılar proxy üzerinden gidebilir.")
    if system.get("js_runtime") is None:
        notes.append("[!] JavaScript ortamı (Deno/Node) bulunamadı: YouTube indirmeleri çalışmayabilir.")

    for name, tool in (data.get("tools") or {}).items():
        if not tool.get("path"):
            notes.append(f"[!] {name} bulunamadı. Kurulumda bin\\ klasörü eksik olabilir (build.ps1 -RefreshTools ile yeniden derle).")
        elif (tool.get("seconds") or 0) > 3:
            notes.append(f"[!] {name} ilk çalıştırmada {tool['seconds']:.1f} sn sürdü"
                         + (f" (ikinci çalıştırma {tool['warm_seconds']:.1f} sn)" if tool.get("warm_seconds") else "")
                         + ". Antivirüs gerçek zamanlı taraması neden olabilir: IDA kurulum ve veri klasörünü "
                           "tarama dışı bırakmayı dene.")

    for entry in data.get("net") or []:
        if entry.get("error"):
            notes.append(f"[!] {entry['host']} bağlantısı kurulamadı: {entry['error']}")
            continue
        if entry.get("dns_ms", 0) > 800:
            notes.append(f"[!] {entry['host']} DNS çözümü yavaş ({entry['dns_ms']} ms): DNS sunucusunu değiştirmek (1.1.1.1 / 8.8.8.8) hızlandırabilir.")
        if entry.get("tls_ms", 0) > 2000:
            notes.append(f"[!] {entry['host']} TLS el sıkışması yavaş ({entry['tls_ms']} ms).")

    http = data.get("http") or []
    for item in http:
        if not item.get("ok"):
            notes.append(f"[!] {item.get('label')} [{item.get('connections', '-')} bağlantı]: başarısız ({item.get('error')})"
                         + (". Test sunucusu art arda isteği sınırlıyor, IDA hatası değil." if "429" in str(item.get("error")) else "."))
            continue
        ready, first = item.get("ready_s"), item.get("first_byte_s")
        if item.get("group") == "small" and ready is not None and ready > 3:
            notes.append(f"[!] Küçük dosyada bağlantı + sondalama {ready:.1f} sn sürdü: başlangıç gecikmesinin asıl kaynağı bu olabilir.")
        if ready is not None and first is not None and first - ready > 2:
            notes.append(f"[i] {item['label']}: indirme hazırlandıktan sonra ilk bayt {first - ready:.1f} sn geç geldi (sunucu yanıt veriyor ama veri başlatmakta yavaş).")
    small = next((i for i in http if i.get("group") == "small" and i.get("ok")), None)
    warm = next((i for i in http if i.get("group") == "warm" and i.get("ok")), None)
    if small and warm and small.get("ready_s") is not None and warm.get("ready_s") is not None:
        notes.append(f"[i] İndirme başlangıcı: önceden sondalamayla {warm['ready_s']:.2f} sn, sondalamasız {small['ready_s']:.2f} sn.")
    speeds = {i["connections"]: i["speed"] for i in http if i.get("group") == "speed" and i.get("ok") and i.get("speed")}
    if len(speeds) >= 2:
        best = max(speeds.values())
        good = min(n for n, s in speeds.items() if s >= best * 0.9)
        summary = ", ".join(f"{n} bağlantı: {format_speed(s)}" for n, s in sorted(speeds.items()))
        notes.append(f"[i] Bağlantı sayısı karşılaştırması: {summary}.")
        if good == 1:
            notes.append("[i] Tek bağlantı da aynı hızı veriyor: bu sunucu/hat için çok bağlantı kazanç sağlamıyor.")
        else:
            notes.append(f"[i] Bu testte {good} bağlantı yeterli göründü (ayarın: {(data.get('system') or {}).get('config', {}).get('connections_per_download')}).")

    yt = data.get("youtube") or {}
    if yt.get("analyze_error"):
        notes.append(f"[!] YouTube analizi başarısız: {yt['analyze_error']}")
    elif yt.get("analyze_s", 0) > 20:
        notes.append(f"[!] YouTube analizi {yt['analyze_s']:.0f} sn sürdü (normalde birkaç sn): yt-dlp başlangıcı ya da JavaScript ortamı yavaş.")
    download, subs = yt.get("download"), yt.get("subs")
    if download is not None:
        if not download.get("ok"):
            notes.append(f"[!] YouTube indirme testi başarısız: {download.get('error')}")
        elif (download.get("startup_s") or 0) > 8:
            notes.append(f"[!] YouTube indirmesi başlamadan önce {download['startup_s']:.0f} sn bekledi (yt-dlp başlangıcı + sayfa çözümleme).")
    fast = yt.get("fast")
    if fast is not None and download and download.get("ok"):
        if not fast.get("ok"):
            notes.append(f"[!] Analiz dosyasıyla indirme başarısız ({fast.get('error')}): uygulama eski yönteme döner.")
        elif fast.get("startup_s") is not None and download.get("startup_s") is not None:
            notes.append(f"[i] YouTube başlama süresi: analiz dosyasıyla {fast['startup_s']:.1f} sn, "
                         f"dosyasız {download['startup_s']:.1f} sn.")
    if subs is not None:
        if not subs.get("ok"):
            notes.append(f"[!] Altyazılı indirme başarısız ({subs.get('lang')}): {subs.get('error')}")
        elif download and download.get("ok") and subs["total_s"] > download["total_s"] * 1.5 + 8:
            notes.append(f"[!] Altyazılı indirme belirgin yavaş: {subs['total_s']:.0f} sn (altyazısız {download['total_s']:.0f} sn). Altyazı sunucusu yavaş ya da hız sınırlıyor olabilir; altyazıyı videoya gömmek yerine ayrı dosya seçeneğini dene.")
    if data.get("cancelled"):
        notes.append("[i] Test yarıda durduruldu, sonuçlar eksik olabilir.")
    if not notes:
        notes.append("Belirgin bir sorun bulunamadı.")
    return notes


# ====================================================================== rapor
def _log_tail(lines: int = 60) -> str:
    try:
        return "\n".join(paths.LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return "(günlük okunamadı)"


def _sec(value) -> str:
    return "-" if value is None else f"{value:.2f} sn"


def render(data: dict, notes: list[str], log_tail: str = "") -> str:
    s = data.get("system") or {}
    out = ["İnternet Download Assistant - Tanı raporu", "=" * 44,
           f"Tarih: {data.get('started')}   Toplam süre: {data.get('elapsed', 0):.0f} sn", "",
           "BULGULAR", "-" * 8, *notes, "", "SİSTEM", "-" * 6,
           f"Uygulama: v{s.get('app')} ({'kurulu' if s.get('frozen') else 'geliştirme'})",
           f"İşletim sistemi: {s.get('os')}   Python: {s.get('python')}   CPU: {s.get('cpu')}",
           f"Kurulum klasörü: {s.get('root')}", f"Veri klasörü: {s.get('data')}",
           f"İndirme klasörü: {s.get('download_dir')}   Boş alan: "
           f"{format_size(s['disk_free']) if isinstance(s.get('disk_free'), int) else '?'}",
           f"JavaScript ortamı: {s.get('js_runtime') or 'YOK'}   Proxy: {', '.join(s.get('proxy') or []) or 'yok'}",
           f"Ayarlar: {s.get('config')}", "", "ARAÇLAR", "-" * 7]
    for name, t in (data.get("tools") or {}).items():
        warm = f", ikinci çalıştırma {_sec(t.get('warm_seconds'))}" if t.get("warm_seconds") is not None else ""
        out.append(f"{name}: sürüm {t.get('version') or '?'}  ilk çalıştırma {_sec(t.get('seconds'))}{warm}  yol: {t.get('path')}")
    out += ["", "AĞ (ms)", "-" * 7]
    for n in data.get("net") or []:
        out.append(f"{n['host']}: " + (f"HATA {n['error']}" if n.get("error")
                                      else f"DNS {n.get('dns_ms')}  TCP {n.get('tcp_ms')}  TLS {n.get('tls_ms')}"))
    out += ["", "HTTP İNDİRME TESTLERİ", "-" * 21]
    for h in data.get("http") or []:
        if not h.get("ok"):
            out.append(f"{h.get('label')} [{h.get('connections', '-')} bağlantı]: BAŞARISIZ - {h.get('error')}")
            continue
        out.append(f"{h['label']} [{h['connections']} bağlantı]: hazır {_sec(h.get('ready_s'))}, ilk bayt {_sec(h.get('first_byte_s'))}, "
                   f"hız {format_speed(h.get('speed'))}, indirilen {format_size(h.get('bytes'))}"
                   + (" (üst sınıra kadar)" if h.get("capped") else "") + f"  motor süreleri: {h.get('engine_timing')}")
    yt = data.get("youtube") or {}
    out += ["", "YOUTUBE", "-" * 7]
    if yt.get("analyze_error"):
        out.append(f"Analiz: HATA - {yt['analyze_error']}  ({_sec(yt.get('analyze_s'))})")
    elif "analyze_s" in yt:
        out.append(f"Analiz: {_sec(yt['analyze_s'])}  biçim: {yt.get('formats')}  çözünürlükler: {yt.get('heights')}")
    for key in ("download", "fast", "subs"):
        d = yt.get(key)
        if d:
            out.append(f"{d['label']}" + (f" ({d['lang']})" if d.get("lang") else "") + ": "
                       + (f"başlama {_sec(d.get('startup_s'))}, indirme {_sec(d.get('download_s'))}, işleme {_sec(d.get('merge_s'))}, "
                          f"toplam {_sec(d.get('total_s'))}, boyut {format_size(d.get('size'))}" if d.get("ok")
                          else f"BAŞARISIZ - {d.get('error')}"))
    if yt.get("subs_note"):
        out.append(yt["subs_note"])
    out += ["", "GÜNLÜĞÜN SON SATIRLARI (paylaşmadan önce göz at: yol ve adres içerebilir)", "-" * 70, log_tail]
    return "\n".join(out) + "\n"
