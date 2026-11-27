"""
YouTube desteği (Qt yok): link tanıma, yt-dlp ile analiz, format ayıklama ve
kullanıcının seçiminden yt-dlp argümanları üretme.

Analiz ve indirme yt-dlp'yi ALT SÜREÇ olarak çalıştırır (python -m yt_dlp). Böylece:
  * durdurma/iptal süreci anında öldürerek yapılır (Windows'ta bile),
  * yt-dlp güncellemesi uygulamayı yeniden başlatmadan etkili olur,
  * yt-dlp'nin çökmesi uygulamayı etkilemez.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from urllib.parse import parse_qs, urlparse

from core import netconf, procutil, tools, ytauth
from utils import paths as _paths
from utils.logger import get_logger

log = get_logger(__name__)

# ====================================================================== link tanıma
_YOUTUBE_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com",
    "youtu.be", "www.youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com",
}


def is_youtube_url(url: str) -> bool:
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and (parsed.hostname or "").lower() in _YOUTUBE_HOSTS


def is_playlist_only(url: str) -> bool:
    """Tek bir videoya değil, yalnızca bir oynatma listesine işaret eden link mi?"""
    parsed = urlparse((url or "").strip())
    query = parse_qs(parsed.query)
    if parsed.path.rstrip("/") == "/playlist":
        return True
    has_video = "v" in query or (parsed.hostname or "").lower().endswith("youtu.be")
    return "list" in query and not has_video


def video_id(url: str) -> str:
    """Tek bir videonun kimliği (watch?v=, youtu.be/, shorts/, live/, embed/). Video değilse boş."""
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if host in ("youtu.be", "www.youtu.be"):
        return parsed.path.strip("/").split("/")[0]
    values = parse_qs(parsed.query).get("v")
    if values and values[0]:
        return values[0]
    match = re.match(r"^/(?:shorts|live|embed)/([\w-]+)", parsed.path)
    return match.group(1) if match else ""


# ====================================================================== veri modelleri
@dataclass(frozen=True)
class FormatInfo:
    format_id: str
    ext: str
    height: int
    width: int
    fps: float
    vcodec: str
    acodec: str
    filesize: Optional[int]
    tbr: float
    abr: float
    dynamic_range: str
    language: str
    note: str

    @property
    def has_video(self) -> bool:
        return self.vcodec not in ("", "none")

    @property
    def has_audio(self) -> bool:
        return self.acodec not in ("", "none")

    @property
    def video_only(self) -> bool:
        return self.has_video and not self.has_audio

    @property
    def audio_only(self) -> bool:
        return self.has_audio and not self.has_video

    @property
    def family(self) -> str:
        v = self.vcodec.lower()
        if v.startswith("avc1") or v.startswith("h264"):
            return "h264"
        if v.startswith("vp9") or v.startswith("vp09"):
            return "vp9"
        if v.startswith("av01"):
            return "av1"
        return "other"

    @property
    def fps_bucket(self) -> int:
        return 60 if (self.fps or 0) >= 50 else 30

    @property
    def is_hdr(self) -> bool:
        return bool(self.dynamic_range) and self.dynamic_range.upper() != "SDR"


@dataclass
class VideoInfo:
    id: str
    url: str
    title: str
    channel: str
    duration: int
    view_count: Optional[int]
    upload_date: str                      # "14 Eki 2026" gibi okunur biçim ya da boş
    thumbnail_url: str
    formats: list[FormatInfo]
    subtitle_langs: list[tuple[str, str]]  # (kod, ad) – yalnızca insan yapımı altyazılar
    auto_caption_langs: list[tuple[str, str]]
    has_chapters: bool
    is_live: bool
    from_playlist: bool = False           # oynatma listesinden ilk video alındı
    info_file: str = ""                   # analiz çıktısının saklandığı dosya (indirmede --load-info-json için)

    @property
    def video_formats(self) -> list[FormatInfo]:
        return [f for f in self.formats if f.video_only]

    @property
    def audio_formats(self) -> list[FormatInfo]:
        return [f for f in self.formats if f.audio_only]

    @property
    def combined_formats(self) -> list[FormatInfo]:
        return [f for f in self.formats if f.has_video and f.has_audio]


@dataclass
class Selection:
    mode: str = "video"            # video | audio | video_only
    height: Optional[int] = None
    fps: Optional[int] = None      # 30 | 60 | None (otomatik)
    container: str = "mp4"         # mp4 | mkv | webm
    codec: str = "compat"          # compat (H.264) | efficient (AV1/VP9)
    audio: str = "m4a"             # m4a | opus | mp3
    audio_kbps: int = 192          # yalnızca mp3
    embed_thumbnail: bool = True
    subtitles: list[str] = field(default_factory=list)
    chapters: bool = True
    start: str = ""
    end: str = ""
    audio_lang: str = ""      # boş = otomatik (orijinal ses); "en", "tr"... = o dildeki ses
    sub_embed: bool = True    # False: altyazı videoya gömülmez, ayrı .srt dosyası olarak kaydedilir


_MONTHS = ["Oca", "Şub", "Mar", "Nis", "May", "Haz", "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara"]


def _pretty_date(raw: str) -> str:
    try:
        d = datetime.strptime(raw, "%Y%m%d")
        return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"
    except (ValueError, TypeError):
        return ""


# ====================================================================== analiz sonucunu ayrıştırma
def parse_info(data: dict, url: str = "") -> VideoInfo:
    """yt-dlp -J çıktısını (sözlük) VideoInfo'ya çevirir."""
    from_playlist = False
    if data.get("_type") == "playlist":
        entries = [e for e in (data.get("entries") or []) if e]
        if not entries:
            raise AnalysisError("Oynatma listesinde video bulunamadı.")
        data = entries[0]
        from_playlist = True

    duration = int(data.get("duration") or 0)
    formats: list[FormatInfo] = []
    for f in data.get("formats") or []:
        if f.get("ext") == "mhtml" or str(f.get("format_id", "")).startswith("sb"):
            continue   # storyboard (önizleme kareleri), indirilebilir medya değil
        size = f.get("filesize") or f.get("filesize_approx")
        tbr = float(f.get("tbr") or 0)
        if not size and tbr and duration:
            size = int(tbr * 125 * duration)
        formats.append(FormatInfo(
            format_id=str(f.get("format_id")),
            ext=str(f.get("ext") or ""),
            height=int(f.get("height") or 0),
            width=int(f.get("width") or 0),
            fps=float(f.get("fps") or 0),
            vcodec=str(f.get("vcodec") or "none"),
            acodec=str(f.get("acodec") or "none"),
            filesize=int(size) if size else None,
            tbr=tbr,
            abr=float(f.get("abr") or 0),
            dynamic_range=str(f.get("dynamic_range") or ""),
            language=str(f.get("language") or ""),
            note=str(f.get("format_note") or ""),
        ))

    def langs(block: dict) -> list[tuple[str, str]]:
        out = []
        for code, items in (block or {}).items():
            if code == "live_chat":
                continue
            name = ""
            if items and isinstance(items, list):
                name = str(items[0].get("name") or "")
            out.append((code, name or code))
        return sorted(out, key=lambda x: (x[0] != "tr", x[0]))

    return VideoInfo(
        id=str(data.get("id") or ""),
        url=str(data.get("webpage_url") or url or ""),
        title=str(data.get("title") or "Başlıksız video"),
        channel=str(data.get("channel") or data.get("uploader") or ""),
        duration=duration,
        view_count=data.get("view_count"),
        upload_date=_pretty_date(str(data.get("upload_date") or "")),
        thumbnail_url=str(data.get("thumbnail") or ""),
        formats=formats,
        subtitle_langs=langs(data.get("subtitles")),
        auto_caption_langs=langs(data.get("automatic_captions")),
        has_chapters=bool(data.get("chapters")),
        is_live=bool(data.get("is_live")) or data.get("live_status") in ("is_live", "is_upcoming"),
        from_playlist=from_playlist,
    )


# ====================================================================== format seçimi
def available_heights(info: VideoInfo) -> list[int]:
    """Videoda gerçekten bulunan çözünürlükler, yüksekten düşüğe."""
    heights = {f.height for f in info.formats if f.has_video and f.height >= 100}
    return sorted(heights, reverse=True)


_LANG_NAMES = {"tr": "Türkçe", "en": "İngilizce", "es": "İspanyolca", "de": "Almanca", "fr": "Fransızca",
               "ru": "Rusça", "ar": "Arapça", "pt": "Portekizce", "it": "İtalyanca", "ja": "Japonca",
               "ko": "Korece", "hi": "Hintçe", "id": "Endonezce", "zh": "Çince", "pl": "Lehçe", "nl": "Felemenkçe"}


def _lang_key(language: str) -> str:
    return (language or "").split("-")[0].split("_")[0].lower()


def audio_languages(info: VideoInfo) -> list[tuple[str, str]]:
    """Videodaki ses dilleri [(kod, görünen ad)]. Dublajlı videolarda birden fazla olur; orijinal olan işaretlenir."""
    names: dict[str, str] = {}
    original: set[str] = set()
    for f in info.audio_formats:
        key = _lang_key(f.language)
        if not key:
            continue
        names.setdefault(key, _LANG_NAMES.get(key, key.upper()))
        if "original" in (f.note or "").lower():
            original.add(key)
    return [(key, name + (" (orijinal)" if key in original else "")) for key, name in names.items()]


def fps_options(info: VideoInfo, height: int) -> set[int]:
    return {f.fps_bucket for f in info.formats if f.has_video and f.height == height}


def has_hdr(info: VideoInfo, height: int) -> bool:
    return any(f.is_hdr for f in info.formats if f.has_video and f.height == height)


def _prefer(candidates: list[FormatInfo], families: list[str]) -> list[FormatInfo]:
    """Aday listesini tercih sırasındaki ilk dolu codec grubuna daraltır (yoksa olduğu gibi bırakır)."""
    for fam in families:
        group = [f for f in candidates if f.family == fam]
        if group:
            return group
    return candidates


def pick_video(info: VideoInfo, sel: Selection) -> Optional[FormatInfo]:
    """Seçime uyan EN İYİ video-only formatı (yt-dlp sıralaması: listede sonraki = daha iyi)."""
    if sel.height is None:
        return None
    cands = [f for f in info.video_formats if f.height == sel.height]
    if not cands:
        return None

    if sel.fps in (30, 60):
        same = [f for f in cands if f.fps_bucket == sel.fps]
        cands = same or cands

    sdr = [f for f in cands if not f.is_hdr]
    cands = sdr or cands

    if sel.container == "webm":
        order = ["vp9", "av1"]
    elif sel.codec == "efficient":
        order = ["av1", "vp9", "h264"]
    else:
        order = ["h264", "vp9", "av1"]
    return _prefer(cands, order)[-1]


def pick_audio(info: VideoInfo, sel: Selection) -> Optional[FormatInfo]:
    audio = info.audio_formats
    if sel.audio_lang:
        audio = [f for f in audio if _lang_key(f.language) == sel.audio_lang] or audio
    else:    # dil seçilmediyse dublajlı bir iz yerine orijinal/varsayılan ses tercih edilir
        audio = [f for f in audio if "original" in (f.note or "").lower()
                 or "default" in (f.note or "").lower()] or audio
    if not audio:
        return None
    if sel.mode == "audio":
        want = {"m4a": "m4a", "opus": "opus"}.get(sel.audio)
        if want == "m4a":
            group = [f for f in audio if f.ext == "m4a"]
        elif want == "opus":
            group = [f for f in audio if f.acodec.lower().startswith("opus")]
        else:
            group = []
        return (group or audio)[-1]
    if sel.container == "mp4":
        group = [f for f in audio if f.ext == "m4a"]
    elif sel.container == "webm":
        group = [f for f in audio if f.acodec.lower().startswith("opus")]
    else:
        group = []
    return (group or audio)[-1]


def effective_container(info: VideoInfo, sel: Selection) -> str:
    """WEBM yalnızca VP9/AV1 + Opus taşır; uymuyorsa MKV'ye geçilir."""
    if sel.container != "webm":
        return sel.container
    v = pick_video(info, sel)
    a = pick_audio(info, sel)
    if (v and v.family not in ("vp9", "av1")) or (a and not a.acodec.lower().startswith(("opus", "vorbis"))):
        return "mkv"
    return "webm"


def estimate_size(info: VideoInfo, sel: Selection) -> Optional[int]:
    if sel.mode == "audio":
        if sel.audio == "wav":
            return int(info.duration * 176400) if info.duration else None
        if sel.audio == "mp3":
            return int(info.duration * sel.audio_kbps * 125) if info.duration else None
        a = pick_audio(info, sel)
        return a.filesize if a else None
    v = pick_video(info, sel)
    if v is None:
        combined = [f for f in info.combined_formats if f.height == sel.height]
        return combined[-1].filesize if combined else None
    total = v.filesize or 0
    if sel.mode == "video":
        a = pick_audio(info, sel)
        total += (a.filesize or 0) if a else 0
    return total or None


def default_selection(info: VideoInfo) -> Selection:
    """
    "Linki yapıştır, bas, bitsin" varsayılanı: video + ses, 1080p (yoksa en yakın alt çözünürlük),
    30 FPS (yoksa 60), MP4, H.264, küçük resim gömülü, altyazısız, en iyi ses.
    """
    heights = available_heights(info)
    sel = Selection()
    if heights:
        below = [h for h in heights if h <= 1080]
        sel.height = below[0] if below else heights[-1]
        fps = fps_options(info, sel.height)
        sel.fps = 30 if 30 in fps else (60 if fps else None)
    else:
        sel.mode = "audio"
    return sel


def selection_summary(info: VideoInfo, sel: Selection, with_size: bool = True) -> str:
    parts: list[str] = []
    if sel.mode == "audio":
        parts.append({"m4a": "M4A (orijinal)", "opus": "OPUS (orijinal)", "wav": "WAV", "flac": "FLAC"}.get(
            sel.audio, f"MP3 {sel.audio_kbps} kbps"))
    else:
        if sel.height:
            parts.append(f"{sel.height}p")
        if sel.fps and len(fps_options(info, sel.height or 0)) > 0:
            parts.append(f"{sel.fps} FPS")
        if sel.mode == "video_only":
            parts.append("Sessiz")
        parts.append(effective_container(info, sel).upper())
    if sel.start or sel.end:
        parts.append(f"{sel.start or '0:00'}–{sel.end or 'son'}")
    size = estimate_size(info, sel) if with_size else None
    if size:
        parts.append(f"~{_human_size(size)}")
    return "  ·  ".join(parts)


def _human_size(n: int) -> str:
    if n >= 1024 ** 3:
        return f"{n / 1024 ** 3:.1f} GB".replace(".", ",")
    return f"{n / 1024 ** 2:.0f} MB"


# ====================================================================== zaman aralığı
def parse_time(text: str) -> Optional[float]:
    """'90', '1:30', '01:02:03' -> saniye. Boş ise None, geçersizse ValueError."""
    text = (text or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3 or not all(p.strip().replace(".", "", 1).isdigit() for p in parts):
        raise ValueError(f"Geçersiz zaman: {text!r}")
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + float(p)
    return seconds


def validate_range(start: str, end: str, duration: int = 0) -> None:
    s, e = parse_time(start), parse_time(end)
    if s is not None and e is not None and e <= s:
        raise ValueError("Bitiş zamanı, başlangıçtan sonra olmalı.")
    if duration and ((s or 0) >= duration or (e or 0) > duration + 1):
        raise ValueError("Zaman aralığı video süresinin dışında.")


# ====================================================================== indirme argümanları
def format_selector(info: VideoInfo, sel: Selection) -> str:
    """Somut format kimlikleriyle seçim ifadesi; kimlik kaybolursa yedek zincir devreye girer."""
    h = sel.height
    if sel.mode == "audio":
        a = pick_audio(info, sel)
        return f"{a.format_id}/ba/b" if a else "ba/b"

    v = pick_video(info, sel)
    if v is None:  # yalnızca birleşik (ses+video) formatlar var
        return f"b[height<={h}]/b" if h else "b"
    if sel.mode == "video_only":
        return f"{v.format_id}/bv*[height<={h}]/b"
    a = pick_audio(info, sel)
    if a is None:
        return f"{v.format_id}/b[height<={h}]/b"
    return f"{v.format_id}+{a.format_id}/{v.format_id}+ba/bv*[height<={h}]+ba/b[height<={h}]/b"


_THUMB_OK = ("mp4", "mkv", "m4a", "mp3")


def build_args(info: VideoInfo, sel: Selection) -> list[str]:
    """Seçimden yt-dlp argümanları (çıktı yolu, ilerleme şablonu ve ffmpeg yolunu motor ekler)."""
    validate_range(sel.start, sel.end, info.duration)
    args = ["-f", format_selector(info, sel)]

    if sel.mode == "audio":
        fmt = {"m4a": "m4a", "opus": "opus", "wav": "wav", "flac": "flac"}.get(sel.audio, "mp3")
        args += ["-x", "--audio-format", fmt]
        if fmt == "mp3":
            args += ["--audio-quality", f"{sel.audio_kbps}K"]
        target = fmt
    else:
        container = effective_container(info, sel)
        if sel.mode == "video":
            args += ["--merge-output-format", container]
        else:
            args += ["--remux-video", container]
        target = container

    args += ["--embed-metadata"]
    if sel.embed_thumbnail and target in _THUMB_OK:
        args += ["--embed-thumbnail", "--convert-thumbnails", "jpg"]
    if sel.mode != "audio":
        if sel.chapters and info.has_chapters:
            args += ["--embed-chapters"]
        if sel.subtitles and sel.mode == "video":
            manual = {c for c, _ in info.subtitle_langs}
            args += ["--write-subs", "--sub-langs", ",".join(sel.subtitles)]
            args += ["--embed-subs"] if sel.sub_embed else ["--sub-format", "srt/best", "--convert-subs", "srt"]
            if any(c not in manual for c in sel.subtitles):
                args += ["--write-auto-subs"]

    s, e = parse_time(sel.start), parse_time(sel.end)
    if s is not None or e is not None:
        left = sel.start.strip() or "0"
        right = sel.end.strip() or "inf"
        args += ["--download-sections", f"*{left}-{right}"]
    return args


# ====================================================================== yt-dlp çalıştırma
class AnalysisError(Exception):
    """Kullanıcıya gösterilecek, anlaşılır analiz hatası."""


class AnalysisCancelled(Exception):
    """Arka plan analizi iptal edildi (hata değildir, kullanıcıya gösterilmez)."""


_ERROR_PATTERNS = [
    (r"Sign in to confirm|not a bot|confirm you.re not a bot",
     "YouTube bu isteği bot saydı ve giriş istiyor. Bir süre sonra tekrar dene; sürerse yt-dlp'yi güncelle."),
    (r"Private video", "Bu video özel; erişim izni gerekiyor. Videoya erişimi olan hesapla tarayıcıda YouTube'a giriş yapıp "
                       "kutudan tekrar gönder (oturum çerezleri otomatik aktarılır)."),
    (r"Video unavailable|This video is not available|has been removed|no longer available",
     "Video kullanılamıyor (silinmiş, gizlenmiş ya da bölgende engelli)."),
    (r"members-only|Join this channel", "Bu video yalnızca kanal üyelerine açık. Üyesi olduğun hesapla tarayıcıda "
                                        "giriş yapıp kutudan tekrar gönder (Ayarlar > YouTube oturum çerezleri açık olmalı)."),
    (r"confirm your age|age-restricted|inappropriate for some users",
     "Bu video yaş sınırlamalı. Tarayıcıda giriş yapıp kutudan tekrar gönder (oturum çerezleri aktarılır)."),
    (r"Premieres in|This live event will begin", "Bu içerik henüz yayınlanmadı."),
    (r"live stream|is_live", "Canlı yayınlar bu sürümde desteklenmiyor."),
    (r"Requested format is not available",
     "İstenen biçim bu videoda yok. Başka bir çözünürlük ya da biçim seç."),
    (r"ffmpeg is not installed|ffmpeg not found|ffprobe and ffmpeg not found|Postprocessing: ffmpeg",
     "ffmpeg bulunamadı. bin klasörüne ffmpeg.exe koy ya da sisteme kur."),
    (r"HTTP Error 403", "YouTube indirmeyi reddetti (403). yt-dlp'yi güncelleyip tekrar dene."),
    (r"HTTP Error 429|Too Many Requests", "Çok fazla istek yapıldı (429). Biraz bekleyip tekrar dene."),
    (r"Unable to download webpage|getaddrinfo failed|Temporary failure in name resolution|timed out|Connection (reset|refused|aborted)",
     "YouTube'a bağlanılamadı. İnternet bağlantını kontrol et."),
    (r"Unsupported URL", "Bu bağlantı desteklenmiyor."),
    (r"No supported JavaScript runtime|JavaScript runtime",
     "YouTube için Deno (ya da Node) gerekli. Kurulum: winget install DenoLand.Deno"),
]


def friendly_error(text: str) -> str:
    for pattern, message in _ERROR_PATTERNS:
        if re.search(pattern, text or "", re.IGNORECASE):
            return message
    last = [ln for ln in (text or "").splitlines() if ln.strip()]
    tail = last[-1].strip() if last else "bilinmeyen hata"
    return f"yt-dlp hatası: {tail[:300]}"


JS_RUNTIMES = ("deno", "node", "bun", "quickjs")


def js_runtime_status() -> Optional[str]:
    """Kurulu ilk JavaScript çalışma ortamının adı (deno/node/bun/quickjs) ya da None. Önce paketle gelen Deno aranır."""
    if tools.tool_path("deno") is not None:
        return "deno"
    for name in JS_RUNTIMES:
        if shutil.which(name):
            return name
    return None


def js_runtime_args() -> list[str]:
    """yt-dlp varsayılan olarak yalnızca PATH'teki Deno'yu açar; paketle gelen Deno'nun ve diğer ortamların yolu verilir."""
    runtime = js_runtime_status()
    if runtime == "deno":
        exe = tools.tool_path("deno", use_path=False)       # tools/ ya da bin/ içindeki Deno
        return ["--js-runtimes", f"deno:{exe}"] if exe is not None else []
    if runtime:
        return ["--js-runtimes", runtime]
    return []


def ytdlp_command(*args: str) -> list[str]:
    """
    yt-dlp komutu. Paketle gelen yt-dlp.exe (tools/ ya da bin/) varsa o kullanılır; yoksa geliştirme ortamında
    `python -m yt_dlp`. --encoding utf-8: çıktının (başlık/dosya adı) her ortamda doğru okunması için.
    """
    exe = tools.tool_path("yt-dlp", use_path=False)
    if exe is not None:
        base = [str(exe)]
    elif getattr(sys, "frozen", False):
        base = [str(tools.default_tool_path("yt-dlp"))]       # yok: çalıştırılınca anlaşılır hata verir
    else:
        base = procutil.python_command("-m", "yt_dlp")
    return [*base, "--encoding", "utf-8", *netconf.ytdlp_args(), *args]


def _save_info_file(raw: str, video_id: str) -> str:
    """-J çıktısını saklar: indirme bunu --load-info-json ile kullanır (sayfa ikinci kez çözümlenmez). Hata olursa boş."""
    try:
        folder = _paths.DATA_DIR / "ytinfo"
        folder.mkdir(parents=True, exist_ok=True)
        now = time.time()
        for old in folder.glob("*.json"):
            if now - old.stat().st_mtime > 6 * 3600:
                old.unlink()
        safe = re.sub(r"[^\w-]", "_", video_id or "video")
        path = folder / f"{safe}-{int(now)}.json"
        path.write_text(raw, encoding="utf-8")
        return str(path)
    except OSError:
        return ""


def analyze(url: str, *, playlist_first: bool = False, timeout: float = 90,
            extra_args: tuple = (), cancel_event=None) -> VideoInfo:
    """Linki yt-dlp ile çözer. Hata olursa AnalysisError (anlaşılır Türkçe mesajla) fırlatır."""
    cmd = ytdlp_command(
        "-J", "--no-warnings", "--socket-timeout", "15",
        *(["--playlist-items", "1"] if playlist_first else ["--no-playlist"]),
        *js_runtime_args(), *ytauth.cookie_args(), *extra_args, url,
    )
    try:
        if cancel_event is not None:     # arka plan (önceden) analizi: iptal edilebilir
            done = procutil.run_capture_cancellable(cmd, timeout, cancel_event)
        else:
            done = procutil.run_capture(cmd, timeout)
    except procutil.ProcessCancelled:
        raise AnalysisCancelled()
    except Exception as e:  # zaman aşımı dahil
        if e.__class__.__name__ == "TimeoutExpired":
            raise AnalysisError("Analiz zaman aşımına uğradı. Bağlantını kontrol edip tekrar dene.")
        raise AnalysisError(f"yt-dlp çalıştırılamadı: {e}")
    if done.returncode != 0:
        raw = done.stderr or done.stdout
        if should_heal(raw) and heal_ytdlp():
            return analyze(url, playlist_first=playlist_first, timeout=timeout, extra_args=extra_args,
                           cancel_event=cancel_event)
        raise AnalysisError(friendly_error(raw))
    try:
        data = json.loads(done.stdout)
    except ValueError:
        raise AnalysisError("YouTube'dan gelen bilgi okunamadı.")
    info = parse_info(data, url)
    if data.get("_type") != "playlist" and not info.is_live:
        info.info_file = _save_info_file(done.stdout, info.id)
    if info.is_live:
        raise AnalysisError("Canlı yayınlar bu sürümde desteklenmiyor.")
    if not info.formats:
        raise AnalysisError("Bu video için indirilebilir biçim bulunamadı.")
    return info


# ====================================================================== oynatma listeleri
@dataclass
class PlaylistEntry:
    id: str
    title: str
    url: str
    duration: Optional[int]
    thumbnail_url: str
    available: bool = True


@dataclass
class PlaylistInfo:
    id: str
    url: str
    title: str
    channel: str
    entries: list[PlaylistEntry]


_UNAVAILABLE_TITLES = {"[private video]", "[deleted video]", "[unavailable video]"}


def playlist_id(url: str) -> str:
    try:
        query = parse_qs(urlparse((url or "").strip()).query)
    except ValueError:
        return ""
    return (query.get("list") or [""])[0]


def playlist_url(url: str) -> str:
    """Video ya da liste adresinden, listenin kendi adresini üretir (liste kimliği yoksa boş)."""
    pid = playlist_id(url)
    return f"https://www.youtube.com/playlist?list={pid}" if pid else ""


def parse_playlist(data: dict, url: str = "") -> PlaylistInfo:
    """yt-dlp -J --flat-playlist çıktısını (sözlük) PlaylistInfo'ya çevirir."""
    entries: list[PlaylistEntry] = []
    for item in data.get("entries") or []:
        if not item:
            continue
        video_id = str(item.get("id") or "")
        if not video_id:
            continue
        title = str(item.get("title") or video_id)
        link = str(item.get("url") or "")
        if not link.startswith("http"):
            link = f"https://www.youtube.com/watch?v={video_id}"
        duration = int(item["duration"]) if item.get("duration") else None
        available = (title.strip().lower() not in _UNAVAILABLE_TITLES
                     and item.get("availability") in (None, "public", "unlisted"))
        entries.append(PlaylistEntry(
            id=video_id, title=title, url=link, duration=duration, available=available,
            thumbnail_url=f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
        ))
    if not entries:
        raise AnalysisError("Oynatma listesinde video bulunamadı.")
    return PlaylistInfo(
        id=str(data.get("id") or ""), url=str(data.get("webpage_url") or url or ""),
        title=str(data.get("title") or "Oynatma listesi"),
        channel=str(data.get("channel") or data.get("uploader") or ""), entries=entries,
    )


def analyze_playlist(url: str, *, timeout: float = 180, extra_args: tuple = ()) -> PlaylistInfo:
    """Oynatma listesini hızlıca (videoları tek tek analiz etmeden) listeler. Hata olursa AnalysisError fırlatır."""
    target = playlist_url(url)
    if not target:
        raise AnalysisError("Bu bir oynatma listesi bağlantısı değil.")
    cmd = ytdlp_command(
        "-J", "--flat-playlist", "--yes-playlist", "--no-warnings", "--socket-timeout", "15",
        *js_runtime_args(), *ytauth.cookie_args(), *extra_args, target,
    )
    try:
        done = procutil.run_capture(cmd, timeout)
    except Exception as e:  # zaman aşımı dahil
        if e.__class__.__name__ == "TimeoutExpired":
            raise AnalysisError("Liste analizi zaman aşımına uğradı. Bağlantını kontrol edip tekrar dene.")
        raise AnalysisError(f"yt-dlp çalıştırılamadı: {e}")
    if done.returncode != 0:
        raise AnalysisError(friendly_error(done.stderr or done.stdout))
    try:
        data = json.loads(done.stdout)
    except ValueError:
        raise AnalysisError("YouTube'dan gelen liste bilgisi okunamadı.")
    return parse_playlist(data, target)


def playlist_summary(sel: Selection) -> str:
    if sel.mode == "audio":
        return {"m4a": "M4A (orijinal)", "opus": "OPUS (orijinal)"}.get(sel.audio, f"MP3 {sel.audio_kbps} kbps")
    quality = f"{sel.height}p" if sel.height else "En iyi"
    container = sel.container if sel.container in ("mp4", "mkv") else "mp4"
    return f"{quality}  ·  {container.upper()}"


def playlist_args(sel: Selection) -> list[str]:
    """
    Listedeki videolar için genel yt-dlp argümanları. Her video ayrıca analiz edilmediğinden somut format
    kimlikleri yerine "en fazla şu çözünürlük" gibi genel seçiciler kullanılır (yt-dlp her video için seçer).
    """
    cap = f"[height<={sel.height}]" if sel.height else ""
    if sel.mode == "audio":
        fmt = {"m4a": "m4a", "opus": "opus"}.get(sel.audio, "mp3")
        args = ["-f", "ba/b", "-x", "--audio-format", fmt]
        if fmt == "mp3":
            args += ["--audio-quality", f"{sel.audio_kbps}K"]
        target = fmt
    else:
        container = sel.container if sel.container in ("mp4", "mkv") else "mp4"
        if container == "mp4":
            selector = f"bv*{cap}[vcodec^=avc1]+ba[ext=m4a]/bv*{cap}+ba/b{cap}" + ("/b" if cap else "")
        else:
            selector = f"bv*{cap}+ba/b{cap}" + ("/b" if cap else "")
        args = ["-f", selector, "--merge-output-format", container]
        target = container
    args += ["--embed-metadata"]
    if sel.embed_thumbnail and target in _THUMB_OK:
        args += ["--embed-thumbnail", "--convert-thumbnails", "jpg"]
    if sel.mode != "audio":
        args += ["--embed-chapters"]
    return args


def ytdlp_version() -> str:
    try:
        done = procutil.run_capture(ytdlp_command("--version"), 30)
        return done.stdout.strip() or "?"
    except Exception:
        return "kurulu değil"


def update_ytdlp(timeout: float = 300) -> tuple[bool, str]:
    """yt-dlp'yi günceller (paketli exe kendini -U ile, geliştirmede pip ile). (başarılı mı, kısa mesaj) döner."""
    exe = tools.tool_path("yt-dlp", use_path=False)
    if exe is not None:
        return tools.self_update(exe, ["-U"], "yt-dlp", timeout, ytdlp_version)
    try:
        done = procutil.run_capture(
            procutil.python_command("-m", "pip", "install", "-U", "yt-dlp[default]"), timeout)
    except Exception as e:
        return False, f"Güncelleme başarısız: {e}"
    if done.returncode != 0:
        tail = (done.stderr or done.stdout).strip().splitlines()[-1:] or ["bilinmeyen hata"]
        return False, f"Güncelleme başarısız: {tail[0][:200]}"
    return True, f"yt-dlp güncellendi (sürüm {ytdlp_version()})."


# ---- yt-dlp kırılırsa kendini toparlama: extractor / 403 / format hatasında bir kez zorla güncelle ve yeniden dene
_HEAL_RE = re.compile(
    r"Unable to extract|\bnsig\b|n challenge|Failed to extract|Sign in to confirm|HTTP Error 403|"
    r"Requested format is not available|unable to download video data|Precondition check failed", re.IGNORECASE)
_heal_lock = threading.Lock()
_last_heal = 0.0


def should_heal(text: str) -> bool:
    return bool(_HEAL_RE.search(text or ""))


def heal_ytdlp(min_interval: float = 1800.0) -> bool:
    """yt-dlp'yi zorla günceller (en fazla 30 dakikada bir). Güncelleme başarılıysa True."""
    global _last_heal
    with _heal_lock:
        now = time.monotonic()
        if _last_heal and now - _last_heal < min_interval:
            return False
        _last_heal = now
        log.warning("yt-dlp hata verdi; güncelleme zorlanıyor")
        ok, message = update_ytdlp()
    log.info("yt-dlp kendini toparlama: %s", message)
    return ok