"""Dönüştürme (Qt yok): ffmpeg ile ses çıkarma ve kapsayıcı değiştirme."""
from __future__ import annotations

from pathlib import Path

from core import procutil, tools
from utils.helpers import reserve_unique_path

AUDIO_TARGETS = ("mp3", "wav", "flac", "m4a", "opus")
VIDEO_TARGETS = ("mp4", "mkv")
TARGETS = VIDEO_TARGETS + AUDIO_TARGETS

CHOICES = (
    ("", "Dönüştürme yok"),
    ("mp4", "Video → MP4"), ("mkv", "Video → MKV"),
    ("mp3", "Sadece ses → MP3"), ("wav", "Sadece ses → WAV"),
    ("flac", "Sadece ses → FLAC"), ("m4a", "Sadece ses → M4A"),
)

_AUDIO = {
    "mp3": ["-vn", "-c:a", "libmp3lame", "-b:a", "192k"],
    "wav": ["-vn", "-c:a", "pcm_s16le"],
    "flac": ["-vn", "-c:a", "flac"],
    "m4a": ["-vn", "-c:a", "aac", "-b:a", "192k"],
    "opus": ["-vn", "-c:a", "libopus", "-b:a", "160k"],
}


class ConvertError(Exception):
    """Kullanıcıya gösterilebilecek dönüştürme hatası."""


def available() -> bool:
    return tools.tool_path("ffmpeg") is not None


def _attempts(target: str) -> list:
    if target in _AUDIO:
        return [_AUDIO[target]]
    if target == "mp4":
        return [["-map", "0:v?", "-map", "0:a?", "-c", "copy", "-movflags", "+faststart"],
                ["-map", "0:v?", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                 "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]]
    return [["-map", "0", "-c", "copy"], ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-c:a", "aac"]]


def convert(src, target: str, timeout: float = 4 * 3600) -> Path:
    """src'yi hedef biçime çevirip yeni dosyanın yolunu verir (orijinale dokunmaz). Başarısızsa ConvertError."""
    target = (target or "").lower()
    ffmpeg = tools.tool_path("ffmpeg")
    if ffmpeg is None:
        raise ConvertError("ffmpeg bulunamadı.")
    if target not in TARGETS:
        raise ConvertError(f"Desteklenmeyen biçim: {target}")
    src = Path(src)
    dest = reserve_unique_path(src.with_suffix("." + target))
    last = ""
    for extra in _attempts(target):
        cmd = [str(ffmpeg), "-y", "-v", "error", "-i", str(src), *extra, str(dest)]
        try:
            done = procutil.run_capture(cmd, timeout)
        except Exception as e:
            last = str(e)
            continue
        if done.returncode == 0 and dest.is_file() and dest.stat().st_size > 0:
            return dest
        lines = [ln for ln in (done.stderr or done.stdout or "").splitlines() if ln.strip()]
        last = lines[-1].strip() if lines else f"ffmpeg kod {done.returncode}"
    try:
        dest.unlink()
    except OSError:
        pass
    raise ConvertError(f"Dönüştürülemedi: {last[:200]}")
