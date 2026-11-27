"""Resim ve video dosyaları için küçük resim üretimi (Qt yok): ffmpeg ile tek kare, 320 px genişlik."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from core import procutil, tools

IMAGE_EXT = {"jpg", "jpeg", "jfif", "png", "gif", "bmp", "webp", "tif", "tiff", "avif", "heic", "heif", "ico"}
VIDEO_EXT = {"mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "mpg", "mpeg", "3gp", "ogv", "m2ts", "mts",
             "vob", "divx", "ts"}


def kind(name: str) -> str:
    """'image' | 'video' | '' (küçük resim üretilemeyen türler)."""
    ext = os.path.splitext(name or "")[1].lstrip(".").lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    return ""


def make_thumb(src: Path, dest: Path, video: bool, size: int = 320, timeout: float = 40) -> bool:
    """src'den dest'e (jpg) küçük resim yazar. ffmpeg yoksa ya da dosya okunamazsa False (hata fırlatmaz)."""
    ffmpeg = tools.tool_path("ffmpeg")
    if ffmpeg is None or not Path(src).is_file():
        return False
    dest = Path(dest)
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    tmp = dest.with_name(f"{dest.stem}.{uuid.uuid4().hex[:6]}.jpg")
    vf = f"scale=w='min({size},iw)':h=-2"
    for pre in ([["-ss", "1"], []] if video else [[]]):
        cmd = [str(ffmpeg), "-y", "-v", "error", *pre, "-i", str(src), "-frames:v", "1", "-vf", vf,
               "-update", "1", "-q:v", "5", str(tmp)]
        try:
            procutil.run_capture(cmd, timeout)
        except Exception:
            continue
        try:
            if tmp.is_file() and tmp.stat().st_size > 0:
                os.replace(tmp, dest)
                return True
        except OSError:
            pass
    try:
        tmp.unlink()
    except OSError:
        pass
    return False
