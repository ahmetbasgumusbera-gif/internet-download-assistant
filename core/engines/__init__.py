"""
Motor kayıt listesi. Yeni bir motor eklemek = dosyayı yazıp ENGINES listesine bir satır eklemek.

Seçim kuralı: görevde bir motor adı varsa (ör. YouTube için "ytdlp") o motor kullanılır;
yoksa URL'yi ilk can_handle() eden motor. Bu yüzden adla seçilen motorlar can_handle()'da False döner.
"""
from typing import Optional

from core.engines.base import BaseEngine
from core.engines.http_engine import HttpEngine
from core.engines.local_engine import LocalEngine
from core.engines.ytdlp_engine import YtdlpEngine

ENGINES: list[type[BaseEngine]] = [
    YtdlpEngine,
    LocalEngine,
    HttpEngine,
]


def pick_engine(url: str, engines: Optional[list] = None,
                name: str = "") -> Optional[type[BaseEngine]]:
    candidates = engines if engines is not None else ENGINES
    if name:
        for engine_cls in candidates:
            if engine_cls.name == name:
                return engine_cls
        return None
    for engine_cls in candidates:
        if engine_cls.can_handle(url):
            return engine_cls
    return None