"""
Sitelerdeki videolar (Qt yok): tarayıcı eklentisinin bulduğu adaylar ve yt-dlp ile akış (HLS / DASH / sayfa) analizi.

  * Doğrudan dosya adayları (mp4, webm...) normal HTTP motoruyla iner.
  * HLS (.m3u8) ve DASH (.mpd) adayları ile "sayfayı yt-dlp ile analiz et" sonuçları yt-dlp ile analiz edilir:
    kalite seçenekleri, ses dilleri (dublaj/orijinal) ve altyazılar listelenir; seçim yt-dlp argümanlarına çevrilir.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from core import procutil, youtube
from core.youtube import AnalysisCancelled, AnalysisError
from utils import paths
from utils.helpers import sanitize_filename
from utils.logger import get_logger

log = get_logger(__name__)

MIN_SCORE = 25                       # "önerilen" sayılmak için en düşük puan
COOKIE_MAX_AGE = 7 * 24 * 3600

_FILE_EXTS = {"mp4", "m4v", "webm", "mkv", "mov", "avi", "flv", "wmv", "ogv", "3gp", "mpg", "mpeg", "f4v"}
_EXT_BY_MIME = {"video/mp4": "mp4", "video/webm": "webm", "video/x-matroska": "mkv", "video/quicktime": "mov",
                "video/x-flv": "flv", "video/ogg": "ogv", "video/3gpp": "3gp", "video/mpeg": "mpg"}


# ====================================================================== eklentiden gelen adaylar
@dataclass
class WebCandidate:
    url: str
    kind: str = "file"               # file | hls | dash
    mime: str = ""
    size: int = 0
    referrer: str = ""
    score: int = 0
    ad: bool = False                 # reklama benziyor
    title: str = ""
    poster: str = ""
    duration: int = 0
    width: int = 0
    height: int = 0
    cookies: str = ""
    source: str = ""                 # network | page

    @property
    def extension(self) -> str:
        ext = Path(urlparse(self.url).path).suffix.lstrip(".").lower()
        return ext if ext in _FILE_EXTS else _EXT_BY_MIME.get(self.mime.split(";")[0].strip().lower(), "mp4")


@dataclass
class WebPage:
    page_url: str
    title: str = ""
    image: str = ""
    user_agent: str = ""
    items: list[WebCandidate] = field(default_factory=list)
    jar: list[dict] = field(default_factory=list)

    def groups(self) -> tuple[Optional[WebCandidate], list[WebCandidate], list[WebCandidate]]:
        """(önerilen ya da None, diğer videolar, reklama benzeyenler); hepsi puana göre sıralı."""
        usable = [c for c in self.items if not c.ad]
        ads = [c for c in self.items if c.ad]
        recommended = usable[0] if usable and usable[0].score >= MIN_SCORE else None
        return recommended, [c for c in usable if c is not recommended], ads


def parse_page(data: dict) -> WebPage:
    """server.clean_webvideo çıktısından (ya da aynı biçimdeki sözlükten) WebPage üretir."""
    items = [WebCandidate(
        url=str(i.get("url") or ""), kind=i.get("kind") if i.get("kind") in ("file", "hls", "dash") else "file",
        mime=str(i.get("mime") or ""), size=int(i.get("size") or 0), referrer=str(i.get("referrer") or ""),
        score=int(i.get("score") or 0), ad=bool(i.get("ad")), title=str(i.get("title") or ""),
        poster=str(i.get("poster") or ""), duration=int(i.get("duration") or 0), width=int(i.get("width") or 0),
        height=int(i.get("height") or 0), cookies=str(i.get("cookies") or ""), source=str(i.get("source") or ""),
    ) for i in data.get("items") or [] if i.get("url")]
    items.sort(key=lambda c: c.score, reverse=True)
    return WebPage(page_url=str(data.get("pageUrl") or ""), title=str(data.get("pageTitle") or ""),
                   image=str(data.get("image") or ""), user_agent=str(data.get("userAgent") or ""),
                   items=items, jar=list(data.get("jar") or []))


def file_name_for(candidate: WebCandidate, fallback_title: str = "") -> str:
    """Doğrudan dosya adayı için önerilen dosya adı (başlık + uzantı)."""
    ext = candidate.extension
    base = sanitize_filename(candidate.title or fallback_title or "video", fallback="video", max_len=140)
    if base.lower().endswith("." + ext):
        return base
    return f"{base}.{ext}"


# ====================================================================== çerez dosyası (yt-dlp --cookies)
def cookie_file(jar: list[dict], directory: Optional[Path] = None) -> Optional[Path]:
    """Tarayıcıdan gelen çerezleri yt-dlp'nin okuduğu Netscape biçiminde dosyaya yazar (aynı içerik = aynı dosya)."""
    lines = ["# Netscape HTTP Cookie File"]
    for c in jar or []:
        try:
            name, value, domain = str(c["name"]), str(c["value"]), str(c["domain"])
        except KeyError:
            continue
        if any(ch in s for s in (name, value, domain) for ch in "\t\r\n") or not domain:
            continue
        include_sub = bool(domain.startswith(".") or not c.get("hostOnly"))
        if include_sub and not domain.startswith("."):
            domain = "." + domain
        lines.append("\t".join([domain, "TRUE" if include_sub else "FALSE", str(c.get("path") or "/"),
                                "TRUE" if c.get("secure") else "FALSE", str(int(c.get("expires") or 0)), name, value]))
    if len(lines) == 1:
        return None
    text = "\n".join(lines) + "\n"
    folder = Path(directory) if directory else paths.DATA_DIR / "webcookies"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (hashlib.sha1(text.encode("utf-8")).hexdigest()[:16] + ".txt")
    if not path.exists():
        path.write_text(text, encoding="utf-8")
    return path


def cleanup_cookie_files(directory: Optional[Path] = None) -> None:
    """Bir haftadan eski çerez dosyalarını siler."""
    folder = Path(directory) if directory else paths.DATA_DIR / "webcookies"
    try:
        now = time.time()
        for f in folder.glob("*.txt"):
            if now - f.stat().st_mtime > COOKIE_MAX_AGE:
                f.unlink()
    except OSError:
        pass


# ====================================================================== hata mesajları (siteler için)
_STREAM_ERRORS = [
    (r"HTTP Error 403|403: Forbidden|Just a moment|Cloudflare|captcha|Access Denied|Attention Required",
     "Site yt-dlp'nin erişimini reddetti (HTTP 403 / bot koruması). Videoyu tarayıcıda oynatıp kutudan ya da eklenti "
     "penceresinden listeyi yeniden aç; akış adresi (HLS/MP4) bulunursa \"Diğer videolar\" içinde görünür."),
    (r"HTTP Error 401|HTTP Error 407|sign in|log in|login required|Private video",
     "Bu içerik giriş gerektiriyor. Sitede giriş yapıp videoyu oynattıktan sonra listeyi yeniden aç."),
    (r"HTTP Error 404|HTTP Error 410|Not Found|has been removed",
     "Adres bulunamadı (404/410). Akış adresinin süresi dolmuş olabilir: videoyu sitede yeniden oynatıp listeyi yenile."),
    (r"HTTP Error 429|Too Many Requests", "Site çok fazla istek aldığını bildirdi (429). Biraz bekleyip tekrar dene."),
    (r"Unsupported URL|Unable to extract|no video formats found|No video formats",
     "yt-dlp bu sayfadan video çıkaramadı (site tanınmıyor). Videoyu sitede oynatıp listeyi yeniden aç; algılanan "
     "akışı (HLS/MP4) listeden seçebilirsin."),
    (r"DRM|Widevine|encrypted|protected by", "Bu içerik DRM ile korunuyor; indirilemez."),
    (r"getaddrinfo failed|Name or service not known|Temporary failure in name resolution|Failed to resolve|"
     r"Network is unreachable|Connection (refused|reset|aborted)|timed out|SSL: ",
     "Siteye bağlanılamadı. İnternet bağlantını ve sitenin açık olup olmadığını kontrol et."),
]


def friendly_stream_error(text: str) -> str:
    """yt-dlp hata çıktısını site (YouTube olmayan) bağlamına uygun, anlaşılır mesaja çevirir; gerçek hata satırı sonuna eklenir."""
    import re
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    errors = [ln for ln in lines if ln.startswith("ERROR")]
    detail = (errors or lines or ["bilinmeyen hata"])[-1]
    detail = re.sub(r"^ERROR:\s*", "", detail)[:220]
    for pattern, message in _STREAM_ERRORS:
        if re.search(pattern, text or "", re.IGNORECASE):
            return f"{message}\n\nyt-dlp: {detail}"
    return f"yt-dlp hatası: {detail}"


# ====================================================================== akış analizi (yt-dlp)
@dataclass(frozen=True)
class StreamFormat:
    format_id: str
    height: int
    width: int
    fps: float
    tbr: float
    vcodec: str
    acodec: str
    language: str
    note: str
    ext: str

    @property
    def has_video(self) -> bool:
        return self.vcodec != "none"

    @property
    def has_audio(self) -> bool:
        return self.acodec != "none"          # bilinmiyorsa (unknown) ses içeriyor sayılır

    @property
    def audio_only(self) -> bool:
        return not self.has_video and self.has_audio


@dataclass
class StreamInfo:
    url: str
    title: str
    thumbnail: str
    duration: int
    formats: list[StreamFormat]
    subtitles: list[tuple[str, str]]         # (dil kodu, ad)
    from_playlist: bool = False

    @property
    def video_formats(self) -> list[StreamFormat]:
        found = [f for f in self.formats if f.has_video]
        return sorted(found, key=lambda f: (f.height, f.tbr), reverse=True)

    @property
    def audio_formats(self) -> list[StreamFormat]:
        found = [f for f in self.formats if f.audio_only]
        return sorted(found, key=lambda f: (f.language or f.note, -f.tbr))


def parse_stream(data: dict, url: str = "") -> StreamInfo:
    """yt-dlp -J çıktısını (sözlük) StreamInfo'ya çevirir. Liste ise ilk videoyu alır."""
    from_playlist = False
    if data.get("_type") == "playlist":
        entries = [e for e in (data.get("entries") or []) if e]
        if not entries:
            raise AnalysisError("Sayfada indirilebilir video bulunamadı.")
        data, from_playlist = entries[0], True
    formats = []
    for f in data.get("formats") or []:
        if f.get("ext") == "mhtml" or str(f.get("format_id", "")).startswith("sb"):
            continue
        formats.append(StreamFormat(
            format_id=str(f.get("format_id")), height=int(f.get("height") or 0), width=int(f.get("width") or 0),
            fps=float(f.get("fps") or 0), tbr=float(f.get("tbr") or f.get("abr") or 0),
            vcodec=str(f.get("vcodec") or "unknown"), acodec=str(f.get("acodec") or "unknown"),
            language=str(f.get("language") or ""), note=str(f.get("format_note") or ""), ext=str(f.get("ext") or ""),
        ))
    if not formats:
        raise AnalysisError("Bu bağlantıda indirilebilir video biçimi bulunamadı.")
    subtitles = []
    for code, items in (data.get("subtitles") or {}).items():
        name = str((items[0].get("name") if items and isinstance(items, list) else "") or "") or code
        subtitles.append((code, name))
    subtitles.sort(key=lambda x: (x[0] != "tr", x[0]))
    return StreamInfo(
        url=str(data.get("webpage_url") or url), title=str(data.get("title") or ""),
        thumbnail=str(data.get("thumbnail") or ""), duration=int(data.get("duration") or 0),
        formats=formats, subtitles=subtitles, from_playlist=from_playlist,
    )


def _origin(url: str) -> str:
    parsed = urlparse(url or "")
    return f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme in ("http", "https") and parsed.netloc else ""


def header_args(referrer: str = "", user_agent: str = "", cookie_path: Optional[Path] = None) -> list[str]:
    args: list[str] = []
    if referrer:
        args += ["--add-header", f"Referer:{referrer}"]
        origin = _origin(referrer)
        if origin:                                   # birçok CDN yalnızca Origin başlığını denetler
            args += ["--add-header", f"Origin:{origin}"]
    if user_agent:
        args += ["--user-agent", user_agent]
    if cookie_path is not None:
        args += ["--cookies", str(cookie_path)]
    return args


def analyze_stream(url: str, *, referrer: str = "", user_agent: str = "", jar: Optional[list] = None,
                   timeout: float = 120, cancel_event=None, extra_args: tuple = ()) -> StreamInfo:
    """HLS/DASH adresini ya da bir web sayfasını yt-dlp ile çözer. Hata olursa AnalysisError (anlaşılır mesajla)."""
    cmd = youtube.ytdlp_command(
        "-J", "--no-warnings", "--socket-timeout", "15", "--playlist-items", "1",
        *header_args(referrer, user_agent, cookie_file(jar or [])), *youtube.js_runtime_args(), *extra_args, url,
    )
    try:
        if cancel_event is not None:
            done = procutil.run_capture_cancellable(cmd, timeout, cancel_event)
        else:
            done = procutil.run_capture(cmd, timeout)
    except procutil.ProcessCancelled:
        raise AnalysisCancelled()
    except Exception as e:
        if e.__class__.__name__ == "TimeoutExpired":
            raise AnalysisError("Analiz zaman aşımına uğradı. Bağlantını kontrol edip tekrar dene.")
        raise AnalysisError(f"yt-dlp çalıştırılamadı: {e}")
    if done.returncode != 0:
        raise AnalysisError(friendly_stream_error(done.stderr or done.stdout))
    import json
    try:
        data = json.loads(done.stdout)
    except ValueError:
        raise AnalysisError("Akış bilgisi okunamadı.")
    return parse_stream(data, url)


# ====================================================================== seçim -> indirme argümanları
@dataclass
class StreamChoice:
    video_id: str = ""               # boş = en iyi
    audio_id: str = ""               # boş = otomatik
    subtitles: list[str] = field(default_factory=list)
    container: str = "mp4"           # mp4 | mkv
    title: str = ""


def _rate(tbr: float) -> str:
    if not tbr:
        return ""
    return f"{tbr / 1000:.1f} Mbps".replace(".", ",") if tbr >= 1000 else f"{int(tbr)} kbps"


def video_label(f: StreamFormat) -> str:
    parts = [f"{f.height}p" if f.height else "Video"]
    if f.fps and f.fps >= 50:
        parts.append(f"{int(f.fps)} FPS")
    if f.tbr:
        parts.append(_rate(f.tbr))
    if f.has_video and not f.has_audio:
        parts.append("sessiz (ses ayrı)")
    return "  ·  ".join(parts)


def audio_label(f: StreamFormat) -> str:
    name = f.language or f.note or f.format_id
    if f.language and f.note and f.note.lower() != f.language.lower():
        name = f"{f.note} ({f.language})"
    return "  ·  ".join(p for p in (name, _rate(f.tbr)) if p)


def build_selector(info: StreamInfo, choice: StreamChoice) -> str:
    formats = {f.format_id: f for f in info.formats}
    video, audio = formats.get(choice.video_id), formats.get(choice.audio_id)
    if video is None:
        return f"bv*+{audio.format_id}/b" if audio else "bv*+ba/b"
    if video.has_audio:                                   # ses videonun içinde (karışık akış)
        return f"{video.format_id}+{audio.format_id}/{video.format_id}" if audio else video.format_id
    return f"{video.format_id}+{audio.format_id if audio else 'ba'}/{video.format_id}/b"


def _output_template(title: str) -> str:
    name = sanitize_filename(title or "video", fallback="video", max_len=150)
    return name.replace("%", "%%") + ".%(ext)s"


def build_args(info: StreamInfo, choice: StreamChoice, *, referrer: str = "", user_agent: str = "",
               cookie_path: Optional[Path] = None) -> list[str]:
    if choice.container in ("mp3", "wav", "flac", "m4a", "opus"):            # yalnızca ses
        audio_sel = choice.audio_id or "ba"
        args = ["-f", f"{audio_sel}/ba/b", "-x", "--audio-format", choice.container]
        if choice.container == "mp3":
            args += ["--audio-quality", "192K"]
        args += ["--embed-metadata", "-o", _output_template(choice.title or info.title)]
        args += header_args(referrer, user_agent, cookie_path)
        return args
    container = choice.container if choice.container in ("mp4", "mkv") else "mp4"
    args = ["-f", build_selector(info, choice), "--merge-output-format", container, "--remux-video", container,
            "--embed-metadata", "-o", _output_template(choice.title or info.title)]
    args += header_args(referrer, user_agent, cookie_path)
    if choice.subtitles:
        args += ["--write-subs", "--sub-langs", ",".join(choice.subtitles), "--embed-subs"]
    return args


def choice_summary(info: StreamInfo, choice: StreamChoice) -> str:
    formats = {f.format_id: f for f in info.formats}
    video, audio = formats.get(choice.video_id), formats.get(choice.audio_id)
    if choice.container in ("mp3", "wav", "flac", "m4a", "opus"):
        return "Sadece ses  ·  " + choice.container.upper()
    parts = [f"{video.height}p" if video and video.height else "En iyi kalite"]
    if audio:
        parts.append(audio.language or audio.note or "ses")
    parts.append(choice.container.upper())
    if choice.subtitles:
        parts.append("altyazı: " + ", ".join(choice.subtitles))
    return "  ·  ".join(parts)
