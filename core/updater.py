"""Araçların zamanlanmış otomatik güncellemesi (Qt yok): yt-dlp günde bir, Deno ayda bir. ffmpeg otomatik güncellenmez."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Callable, Optional

from core import tools, youtube
from utils.logger import get_logger

log = get_logger(__name__)

YTDLP_EVERY = timedelta(hours=24)
DENO_EVERY = timedelta(days=30)
RETRY_AFTER = timedelta(hours=4)      # başarısız denemeden sonra tekrar deneme aralığı


def is_due(raw: str, every: timedelta, now: datetime) -> bool:
    try:
        last = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return True
    return now - last >= every


def _stamp(config, key: str, every: timedelta, ok: bool, now: datetime) -> None:
    when = now if ok else now - every + RETRY_AFTER
    config.set(key, when.isoformat(timespec="seconds"))


def run_scheduled(config, busy: Optional[Callable[[], bool]] = None, now: Optional[datetime] = None,
                  force: bool = False) -> list[str]:
    """
    Zamanı gelen araçları günceller ve yapılan işlerin kısa özetini döndürür. Ayar kapalıysa (force olmadıkça) ya da
    indirme sürerken (busy() True) hiçbir şey yapmaz. Güncelleme için paketli araç (exe) gerekir; pip ile kurulmuş
    yt-dlp'ye (geliştirme ortamı) dokunulmaz.
    """
    if not force and not config.get("auto_update_tools"):
        return []
    if busy is not None and busy():
        return []
    now = now or datetime.now()
    jobs = (
        ("last_ytdlp_check", YTDLP_EVERY, youtube.update_ytdlp, tools.tool_path("yt-dlp", use_path=False)),
        ("last_deno_check", DENO_EVERY, tools.update_deno, tools.tool_path("deno", use_path=False)),
    )
    results: list[str] = []
    for key, every, update, exe in jobs:
        if exe is None or not (force or is_due(config.get(key), every, now)):
            continue
        try:
            ok, message = update()
        except Exception as e:
            ok, message = False, f"güncelleme hatası: {e}"
        log.info("Otomatik güncelleme: %s", message)
        _stamp(config, key, every, ok, now)
        results.append(message)
    return results
