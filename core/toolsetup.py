"""Eksik araçları (yt-dlp, ffmpeg, Deno) indirir (Qt yok): çevrimiçi kurulum paketi araçları içermez."""
from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Callable, Optional

import requests

from core import tools, youtube
from utils import paths
from utils.logger import get_logger

log = get_logger(__name__)

YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
DENO_URL = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"
Progress = Callable[[str, int, int], None]


def missing() -> list[str]:
    """Kurulu (paketli) uygulamada eksik araçlar. Geliştirme kopyasında (python main.py) boş döner."""
    if not paths.FROZEN or os.name != "nt":
        return []
    out = []
    if tools.tool_path("yt-dlp", use_path=False) is None:
        out.append("yt-dlp")
    if tools.tool_path("ffmpeg") is None:
        out.append("ffmpeg")
    if tools.tool_path("deno") is None and youtube.js_runtime_status() is None:
        out.append("deno")
    return out


def _download(url: str, dest: Path, label: str, progress: Optional[Progress], cancel=None) -> None:
    part = dest.with_name(dest.name + ".part")
    try:
        with requests.get(url, stream=True, timeout=(15, 30)) as r:
            r.raise_for_status()
            total, done = int(r.headers.get("Content-Length") or 0), 0
            with open(part, "wb") as f:
                for chunk in r.iter_content(256 * 1024):
                    if cancel is not None and cancel.is_set():
                        raise InterruptedError("iptal")
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(label, done, total)
        os.replace(part, dest)
    except BaseException:
        try:
            part.unlink()
        except OSError:
            pass
        raise


def install(names: list[str], progress: Optional[Progress] = None, cancel=None) -> list[str]:
    """Verilen araçları tools\\ klasörüne indirir. Hata iletilerinin listesini döndürür (boşsa hepsi tamam)."""
    paths.ensure_dirs()
    errors: list[str] = []
    for name in names:
        if cancel is not None and cancel.is_set():
            break
        try:
            if name == "yt-dlp":
                _download(YTDLP_URL, paths.TOOLS_DIR / "yt-dlp.exe", "yt-dlp", progress, cancel)
            elif name == "deno":
                archive = paths.TOOLS_DIR / "deno.zip"
                _download(DENO_URL, archive, "Deno", progress, cancel)
                with zipfile.ZipFile(archive) as zf, open(paths.TOOLS_DIR / "deno.exe", "wb") as out:
                    out.write(zf.read("deno.exe"))
                archive.unlink()
            elif name == "ffmpeg":
                ok, message = tools.update_ffmpeg(progress=(lambda d, t: progress("ffmpeg", d, t)) if progress else None)
                if not ok:
                    errors.append(message)
        except InterruptedError:
            break
        except (requests.RequestException, OSError, zipfile.BadZipFile, KeyError) as e:
            log.warning("Araç indirilemedi (%s): %s", name, e)
            errors.append(f"{name} indirilemedi: {e}")
    return errors
