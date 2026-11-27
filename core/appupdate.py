"""
Uygulama güncellemesi (Qt yok): GitHub Releases'ten en son sürümü sorar, Setup'ı indirip doğrular ve sessizce kurdurur.
Yayın: GitHub'da depo > Releases > yeni sürüm (etiket v1.5.1) ve iki dosya: IDA-Setup-1.5.1.exe + IDA-Setup-1.5.1.exe.sha256.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

import requests

from utils import paths
from utils.logger import get_logger

log = get_logger(__name__)

REPO = "ahmetbasgumusbera-gif/internet-download-assistant"
API = "https://api.github.com/repos/{repo}/releases/latest"
UA = "InternetDownloadAssistant-Updater"


class UpdateError(Exception):
    """Kullanıcıya gösterilecek, anlaşılır güncelleme hatası."""


def parse_version(text: str) -> tuple:
    """"v1.5.0" -> (1, 5, 0, 0): sürüm karşılaştırması için."""
    nums = [int(n) for n in re.findall(r"\d+", str(text or ""))][:4]
    return tuple(nums + [0] * (4 - len(nums)))


def check(current: str, repo: str = REPO, timeout: float = 10.0) -> Optional[dict]:
    """
    Daha yeni bir sürüm varsa {version, notes, page, name, url, size, sha_url} döndürür; güncelse None.
    Ağ ya da yayın sorununda UpdateError fırlatır.
    """
    try:
        r = requests.get(API.format(repo=repo), timeout=timeout,
                         headers={"Accept": "application/vnd.github+json", "User-Agent": UA})
    except requests.RequestException as e:
        raise UpdateError(f"Güncelleme sunucusuna (GitHub) ulaşılamadı: {e}")
    if r.status_code == 404:
        raise UpdateError("Henüz yayınlanmış bir sürüm bulunamadı.")
    if r.status_code != 200:
        raise UpdateError(f"GitHub HTTP {r.status_code} döndürdü (biraz sonra tekrar dene).")
    try:
        data = r.json()
    except ValueError:
        raise UpdateError("GitHub yanıtı okunamadı.")
    tag = str(data.get("tag_name") or "")
    if parse_version(tag) <= parse_version(current):
        return None
    assets = [a for a in data.get("assets") or [] if isinstance(a, dict)]
    setup = next((a for a in assets if re.fullmatch(r"IDA-Setup-.*\.exe", str(a.get("name") or ""), re.I)), None)
    if setup is None:
        raise UpdateError(f"{tag} sürümünde kurulum dosyası (IDA-Setup-*.exe) yok.")
    sha = next((a for a in assets if str(a.get("name") or "").lower() == str(setup["name"]).lower() + ".sha256"), None)
    return {
        "version": tag.lstrip("vV"), "notes": str(data.get("body") or "").strip(),
        "page": str(data.get("html_url") or ""), "name": str(setup["name"]),
        "url": str(setup.get("browser_download_url") or ""), "size": int(setup.get("size") or 0),
        "sha_url": str(sha.get("browser_download_url") or "") if sha else "",
    }


def _expected_hash(info: dict) -> str:
    if not info.get("sha_url"):
        return ""
    try:
        r = requests.get(info["sha_url"], timeout=15, headers={"User-Agent": UA})
        r.raise_for_status()
    except requests.RequestException as e:
        raise UpdateError(f"Doğrulama dosyası (.sha256) alınamadı: {e}")
    match = re.search(r"\b[0-9a-fA-F]{64}\b", r.text)
    if not match:
        raise UpdateError("Doğrulama dosyası (.sha256) geçersiz.")
    return match.group(0).lower()


def download(info: dict, progress: Optional[Callable[[int, int], None]] = None, cancel=None) -> Path:
    """Setup'ı veri klasörüne indirir ve doğrular. Başarılıysa dosya yolunu verir. İptalde InterruptedError."""
    folder = paths.DATA_DIR / "updates"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*"):
        try:
            old.unlink()
        except OSError:
            pass
    final = folder / info["name"]
    part = final.with_name(final.name + ".part")
    digest = hashlib.sha256()
    done, total = 0, int(info.get("size") or 0)

    def cleanup() -> None:
        try:
            part.unlink()
        except OSError:
            pass

    try:
        with requests.get(info["url"], stream=True, timeout=(15, 30), headers={"User-Agent": UA}) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length") or total)
            with open(part, "wb") as f:
                for chunk in r.iter_content(256 * 1024):
                    if cancel is not None and cancel.is_set():
                        raise InterruptedError("iptal")
                    f.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
    except InterruptedError:
        cleanup()
        raise
    except (requests.RequestException, OSError) as e:
        cleanup()
        raise UpdateError(f"Güncelleme indirilemedi: {e}")
    if info.get("size") and done != int(info["size"]):
        cleanup()
        raise UpdateError("İndirilen dosya eksik kaldı. Bağlantını kontrol edip tekrar dene.")
    try:
        expected = _expected_hash(info)
    except UpdateError:
        cleanup()
        raise
    if expected and digest.hexdigest() != expected:
        cleanup()
        raise UpdateError("İndirilen dosya doğrulanamadı (SHA-256 uyuşmuyor). Güvenlik için kurulmadı.")
    os.replace(part, final)
    return final


def launch_installer(setup: Path) -> bool:
    """
    Setup'ı, bu uygulama kapandıktan SONRA sessizce çalıştırır (önceki kurulum klasörüne kurulur, bitince uygulama açılır).
    Önce bir PowerShell yardımcısı bu işlemin bitmesini bekler; olmazsa Setup doğrudan başlatılır. Windows dışında False.
    """
    if os.name != "nt" or not Path(setup).is_file():
        return False
    exe = str(setup).replace("'", "''")
    command = (f"Wait-Process -Id {os.getpid()} -Timeout 120 -ErrorAction SilentlyContinue; "
               f"Start-Process -FilePath '{exe}' -ArgumentList '/SILENT','/NORESTART','/CLOSEAPPLICATIONS'")
    flags = 0x08000200          # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", command],
                         creationflags=flags, close_fds=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError as e:
        log.warning("PowerShell yardımcısı başlatılamadı (%s); Setup doğrudan başlatılıyor", e)
    try:
        subprocess.Popen([str(setup), "/SILENT", "/NORESTART", "/CLOSEAPPLICATIONS"], creationflags=flags,
                         close_fds=True, stdin=subprocess.DEVNULL)
        return True
    except OSError as e:
        log.error("Setup başlatılamadı: %s", e)
        return False
