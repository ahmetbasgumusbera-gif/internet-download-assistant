"""
Dış araçlar (yt-dlp, ffmpeg, Deno): konum bulma, güncellenebilir kopya, sürüm sorgulama ve güncelleme. Qt yok.

İki katman vardır:
  * bin/   : kurulumla gelen temel kopya (Program Files'ta yazılamayabilir)
  * tools/ : kullanıcı verisi altında güncellenebilir kopyalar (açılışta bin/'den tohumlanır)
Araç aranırken önce tools/, sonra bin/, en son (istenirse) sistem PATH'i kullanılır.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from pathlib import Path
from typing import Callable, Optional

import requests

from core import procutil
from utils import paths
from utils.logger import get_logger

log = get_logger(__name__)

EXE = ".exe" if os.name == "nt" else ""
FFMPEG_URL = "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
_SEED_TOOLS = ("yt-dlp", "deno", "ffmpeg", "ffprobe")


def _exe(name: str) -> str:
    return name + EXE


def tool_path(name: str, use_path: bool = True) -> Optional[Path]:
    """Aracın yolu (tools/ > bin/ > PATH). Bulunamazsa None."""
    for folder in (paths.TOOLS_DIR, paths.BIN_DIR):
        candidate = folder / _exe(name)
        if candidate.is_file():
            return candidate
    if use_path:
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def default_tool_path(name: str) -> Path:
    """Araç bulunamıyorsa bile kullanılacak (var olmayan) yol: çalıştırılınca anlaşılır bir hata oluşur."""
    return tool_path(name, use_path=False) or (paths.BIN_DIR / _exe(name))


# ---------------------------------------------------------------- tohumlama (bin/ -> tools/)
def _signature(path: Path) -> list[int]:
    stat = path.stat()
    return [stat.st_size, stat.st_mtime_ns]


def seed_tools() -> list[str]:
    """
    bin/ içindeki araçları (ve ffmpeg DLL'lerini) güncellenebilir tools/ klasörüne kopyalar: dosya tools/ içinde
    yoksa ya da bin/ içindeki dosya (yeni kurulum paketiyle) değiştiyse. Kendi kendini güncellemiş bir araç,
    bin/ içindeki dosya değişmedikçe ezilmez. Kopyalanan dosya adlarını döndürür.
    """
    paths.ensure_dirs()
    seed_file = paths.TOOLS_DIR / "seed.json"
    try:
        seen = json.loads(seed_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seen = {}
    if not isinstance(seen, dict):
        seen = {}

    sources = [paths.BIN_DIR / _exe(name) for name in _SEED_TOOLS]
    if paths.BIN_DIR.is_dir():
        sources += sorted(paths.BIN_DIR.glob("*.dll"))

    copied: list[str] = []
    for src in sources:
        if not src.is_file():
            continue
        signature = _signature(src)
        dst = paths.TOOLS_DIR / src.name
        if dst.is_file() and seen.get(src.name) == signature:
            continue
        try:
            temp = dst.with_name(dst.name + ".new")
            shutil.copy2(src, temp)
            os.replace(temp, dst)
            seen[src.name] = signature
            copied.append(src.name)
        except OSError as e:
            log.warning("Araç kopyalanamadı (%s): %s", src.name, e)
    try:
        seed_file.write_text(json.dumps(seen), encoding="utf-8")
    except OSError:
        pass
    if copied:
        log.info("Araçlar hazırlandı: %s", ", ".join(copied))
    return copied


# ---------------------------------------------------------------- sürümler
def _first_line(cmd: list[str], timeout: float = 20) -> str:
    try:
        done = procutil.run_capture(cmd, timeout)
    except Exception:
        return ""
    lines = (done.stdout or done.stderr or "").strip().splitlines()
    return lines[0].strip() if lines else ""


def deno_version() -> str:
    exe = tool_path("deno")
    if exe is None:
        return ""
    match = re.search(r"deno\s+([\w.\-+]+)", _first_line([str(exe), "--version"]), re.IGNORECASE)
    return match.group(1) if match else ""


def ffmpeg_version() -> str:
    exe = tool_path("ffmpeg")
    if exe is None:
        return ""
    match = re.search(r"version\s+(\S+)", _first_line([str(exe), "-version"]), re.IGNORECASE)
    return match.group(1) if match else ""


# ---------------------------------------------------------------- güncelleme
def self_update(exe: Path, args: list[str], name: str, timeout: float,
                version_fn: Callable[[], str]) -> tuple[bool, str]:
    """Aracın kendi güncelleme komutunu (yt-dlp -U, deno upgrade) çalıştırır. (başarılı mı, kısa mesaj) döner."""
    try:
        done = procutil.run_capture([str(exe), *args], timeout)
    except Exception as e:
        return False, f"{name} güncellenemedi: {e}"
    output = (done.stdout or "") + "\n" + (done.stderr or "")
    if done.returncode != 0:
        tail = [ln for ln in output.splitlines() if ln.strip()][-1:] or ["bilinmeyen hata"]
        return False, f"{name} güncellenemedi: {tail[0].strip()[:200]}"
    version = version_fn() or "?"
    lowered = output.lower()
    if "up to date" in lowered or "up-to-date" in lowered or "already" in lowered:
        return True, f"{name} güncel (sürüm {version})."
    return True, f"{name} güncellendi (sürüm {version})."


def update_deno(timeout: float = 300) -> tuple[bool, str]:
    exe = tool_path("deno", use_path=False)
    if exe is None:
        return False, "Deno kurulu değil."
    return self_update(exe, ["upgrade"], "Deno", timeout, deno_version)


def update_ffmpeg(progress: Optional[Callable[[int, int], None]] = None,
                  url: str = FFMPEG_URL) -> tuple[bool, str]:
    """
    Yeni bir ffmpeg (paylaşımlı derleme) indirir ve tools/ içindeki dosyaların yerine koyar. Değiştirme sırasında
    bir dosya kilitliyse (çalışan bir indirme kullanıyorsa) hiçbir şey değişmez. (başarılı mı, kısa mesaj) döner.
    """
    paths.ensure_dirs()
    work = paths.TOOLS_DIR / "ffmpeg_update"
    shutil.rmtree(work, ignore_errors=True)
    staging = work / "yeni"
    staging.mkdir(parents=True)
    swapped: list[tuple[Path, Optional[Path]]] = []
    try:
        archive = work / "ffmpeg.zip"
        with requests.get(url, stream=True, timeout=(15, 60)) as response:
            response.raise_for_status()
            total, done = int(response.headers.get("Content-Length") or 0), 0
            with open(archive, "wb") as f:
                for chunk in response.iter_content(256 * 1024):
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)

        with zipfile.ZipFile(archive) as zf:
            members = [m for m in zf.namelist()
                       if "/bin/" in m and m.lower().endswith((".exe", ".dll")) and not m.lower().endswith("ffplay.exe")]
            if not any(m.lower().endswith("ffmpeg.exe") for m in members):
                return False, "İndirilen pakette ffmpeg.exe bulunamadı."
            for member in members:
                with zf.open(member) as src, open(staging / Path(member).name, "wb") as out:
                    shutil.copyfileobj(src, out)

        for new_file in sorted(staging.iterdir()):
            target = paths.TOOLS_DIR / new_file.name
            backup = None
            if target.exists():
                backup = target.with_name(target.name + ".eski")
                if backup.exists():
                    backup.unlink()
                os.replace(target, backup)
            swapped.append((target, backup))
            os.replace(new_file, target)
    except Exception as e:
        for target, backup in reversed(swapped):       # geri al: eski dosyalar yerine dönsün
            try:
                if backup is not None:
                    os.replace(backup, target)
                elif target.exists():
                    target.unlink()
            except OSError:
                pass
        log.warning("ffmpeg güncellenemedi: %s", e)
        return False, f"ffmpeg güncellenemedi: {e}"
    finally:
        shutil.rmtree(work, ignore_errors=True)
    for _target, backup in swapped:
        if backup is not None:
            try:
                backup.unlink()
            except OSError:
                pass                                    # hâlâ kullanımda olabilir; sonraki açılışta kalır
    return True, f"ffmpeg güncellendi (sürüm {ffmpeg_version() or '?'})."
