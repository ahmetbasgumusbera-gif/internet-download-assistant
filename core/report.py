"""Hata raporu (Qt yok): GitHub issue taslağı için sistem bilgisi, günlük özeti ve çökme kaydı."""
from __future__ import annotations

import platform
from pathlib import Path
from urllib.parse import quote

from core import appupdate
from utils import paths
from utils.version import __version__


def _clean(text: str) -> str:
    home = str(Path.home())
    for variant in {home, home.replace("\\", "/")}:
        if variant:
            text = text.replace(variant, "~")
    return text


def system_info() -> str:
    return (f"- Uygulama: v{__version__} ({'kurulu' if paths.FROZEN else 'geliştirme'})\n"
            f"- İşletim sistemi: {platform.platform()}\n- Python: {platform.python_version()}")


def log_tail(lines: int = 40) -> str:
    try:
        return _clean("\n".join(paths.LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]))
    except OSError:
        return "(günlük okunamadı)"


_NOTE = "Tam rapor panoya kopyalandı: aşağıya yapıştır (Ctrl+V)."


def manual_report() -> tuple:
    """(başlık, tam rapor, kısa taslak)."""
    title = f"Hata bildirimi (v{__version__})"
    full = ("### Sorun nedir?\n(buraya yaz)\n\n### Sistem\n" + system_info()
            + "\n\n### Günlüğün son satırları\n```\n" + log_tail() + "\n```\n")
    return title, full, f"### Sorun nedir?\n(buraya yaz)\n\n### Sistem\n{system_info()}\n\n{_NOTE}\n"


def crash_report(trace: str) -> tuple:
    trace = _clean(trace)
    title = f"Çökme bildirimi (v{__version__})"
    full = ("### Hata\n```\n" + trace + "\n```\n\n### Sistem\n" + system_info()
            + "\n\n### Günlüğün son satırları\n```\n" + log_tail(30) + "\n```\n")
    short = "### Hata\n```\n" + "\n".join(trace.strip().splitlines()[-6:]) + "\n```\n\n" + system_info() + "\n\n" + _NOTE
    return title, full, short


def crash_file() -> Path:
    return paths.LOG_DIR / "crash.txt"


def read_crash() -> str:
    try:
        return crash_file().read_text(encoding="utf-8")
    except OSError:
        return ""


def clear_crash() -> None:
    try:
        crash_file().unlink()
    except OSError:
        pass


def issue_url(title: str, body: str, limit: int = 1900) -> str:
    base = f"https://github.com/{appupdate.REPO}/issues/new?title={quote(title, safe='')}&body="
    text = body
    while text and len(base + quote(text, safe="")) > limit:
        text = text[: int(len(text) * 0.8)]
    return base + quote(text, safe="")
