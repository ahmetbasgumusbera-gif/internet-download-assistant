"""
YouTube oturum çerezleri (Qt yok): üyelere özel, özel (private) ve yaş sınırlı videolar için.

Tarayıcı eklentisi, tarayıcıdaki YouTube/Google çerezlerini uygulamaya gönderir; burada Netscape biçiminde saklanır
(data/youtube_cookies.txt) ve yt-dlp'ye --cookies ile verilir. yt-dlp çıkışta çerez dosyasını yeniden yazdığı için her çağrıda
dosyanın bir KOPYASI kullanılır. Ayarlardan "youtube_cookies_browser" doluysa bunun yerine --cookies-from-browser kullanılır.
"""
from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from pathlib import Path

from utils import paths
from utils.logger import get_logger

log = get_logger(__name__)

_ALLOWED = ("youtube.com", "google.com", "youtube-nocookie.com")
_LOGIN_NAMES = ("SAPISID", "__Secure-3PSID", "__Secure-1PSID", "LOGIN_INFO")
_enabled = True
_browser = ""
_lock = threading.Lock()


def configure(enabled: bool, browser: str = "") -> None:
    global _enabled, _browser
    _enabled = bool(enabled)
    _browser = (browser or "").strip().lower()


def cookie_file() -> Path:
    return paths.DATA_DIR / "youtube_cookies.txt"


def _domain_ok(domain: str) -> bool:
    d = (domain or "").lstrip(".").lower()
    return any(d == a or d.endswith("." + a) for a in _ALLOWED)


def to_netscape(jar) -> str:
    """Çerez listesini Netscape biçimine çevirir (yalnızca YouTube/Google alanları). Boşsa ''."""
    lines = ["# Netscape HTTP Cookie File"]
    for c in jar or []:
        try:
            name, value, domain = str(c["name"]), str(c["value"]), str(c["domain"])
        except (KeyError, TypeError):
            continue
        if not domain or not _domain_ok(domain):
            continue
        if any(ch in s for s in (name, value, domain) for ch in "\t\r\n"):
            continue
        include_sub = bool(domain.startswith(".") or not c.get("hostOnly"))
        if include_sub and not domain.startswith("."):
            domain = "." + domain
        prefix = "#HttpOnly_" if c.get("httpOnly") else ""
        lines.append("\t".join([prefix + domain, "TRUE" if include_sub else "FALSE", str(c.get("path") or "/"),
                                "TRUE" if c.get("secure") else "FALSE", str(int(c.get("expires") or 0)), name, value]))
    return "\n".join(lines) + "\n" if len(lines) > 1 else ""


def save_jar(jar) -> bool:
    """Eklentiden gelen çerezleri saklar (içerik değişmediyse yazmaz). Boş/geçersizse eskisi korunur."""
    text = to_netscape(jar)
    if not text:
        return False
    target = cookie_file()
    with _lock:
        try:
            if target.is_file() and target.read_text(encoding="utf-8") == text:
                return True
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, target)
            log.info("YouTube oturum çerezleri güncellendi")
            return True
        except OSError as e:
            log.warning("YouTube çerezleri kaydedilemedi: %s", e)
            return False


def has_login() -> bool:
    try:
        text = cookie_file().read_text(encoding="utf-8")
    except OSError:
        return False
    return any(name in text for name in _LOGIN_NAMES)


def _prune(folder: Path) -> None:
    try:
        now = time.time()
        for f in folder.glob("*.txt"):
            if now - f.stat().st_mtime > 12 * 3600:
                f.unlink()
    except OSError:
        pass


def cookie_args() -> list[str]:
    """yt-dlp'ye eklenecek çerez argümanları (kapalıysa ya da çerez yoksa boş liste)."""
    if not _enabled:
        return []
    if _browser:
        return ["--cookies-from-browser", _browser]
    source = cookie_file()
    if not source.is_file():
        return []
    folder = paths.DATA_DIR / "ytcookies"
    try:
        folder.mkdir(parents=True, exist_ok=True)
        _prune(folder)
        copy = folder / f"{os.getpid()}-{uuid.uuid4().hex[:8]}.txt"
        shutil.copyfile(source, copy)
    except OSError:
        return []
    return ["--cookies", str(copy)]
