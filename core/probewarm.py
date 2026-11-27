"""Önceden sondalama (Qt yok): yakalanan indirmenin sunucu bilgisini kullanıcı İndir'e basmadan arka planda alır."""
from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from core.engines.http_engine import HttpEngine
from core.models import DownloadTask
from utils.logger import get_logger

log = get_logger(__name__)


def warm(url: str, headers: dict | None = None, file_name: str = "") -> None:
    """Arka planda sondalar ve sonucu HttpEngine önbelleğine koyar. Hata fırlatmaz."""
    threading.Thread(target=_run, args=(url, dict(headers or {}), file_name), daemon=True, name="probe-warm").start()


def _run(url: str, headers: dict, file_name: str) -> None:
    engine = None
    try:
        task = DownloadTask(url=url, save_dir=Path(tempfile.gettempdir()), file_name=file_name, headers=headers)
        engine = HttpEngine(task, None, connections=1)
        engine.cache_probe = True
        engine.probe()
    except Exception as e:
        log.debug("Önceden sondalama başarısız: %s", e)
    finally:
        session = getattr(engine, "_session", None)
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
