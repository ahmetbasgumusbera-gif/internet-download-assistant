"""
Siteye göre bağlantı sayısı ayarlayıcı (Qt yok): her sitede denenen bağlantı sayısı ve ölçülen ortalama hız hatırlanır.
Sıra: ayarlı sayı -> yarısı -> (yarısına göre belirgin hızlanma varsa) 2 katı -> en hızlı olan (fark %10'dan azsa en az bağlantı).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Optional


class Tuner:
    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path else None
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, list]] = {}
        if self._path:
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data = raw
            except (OSError, ValueError):
                pass

    def choose(self, host: str, default: int, cap: Optional[int] = None) -> int:
        default = max(1, int(default))
        cap = min(32, int(cap)) if cap else min(32, default * 2)
        if default < 4 or not host:
            return default
        with self._lock:
            rec = {int(k): float(v[0]) for k, v in (self._data.get(host) or {}).items()}
        if default not in rec:
            return default
        half = default // 2
        if half >= 2 and half not in rec:
            return half                               # keşif: yarı sayıyla bir kez dene
        up = min(default * 2, cap)
        if up > default and up not in rec and rec[default] > rec.get(half, 0.0) * 1.1:
            return up                                 # daha çok bağlantı belirgin hızlandırdıysa 2 katını dene
        top = max(rec.values())
        near = [n for n, s in rec.items() if s >= top * 0.9]
        return min(near)

    def record(self, host: str, connections: int, speed: float) -> None:
        if not host or connections < 2 or speed <= 0:
            return
        with self._lock:
            rec = self._data.setdefault(host, {})
            old = rec.get(str(connections))
            value = speed if not old else old[0] * 0.6 + speed * 0.4
            rec[str(connections)] = [value, (old[1] + 1) if old else 1]
            if self._path:
                try:
                    self._path.parent.mkdir(parents=True, exist_ok=True)
                    self._path.write_text(json.dumps(self._data), encoding="utf-8")
                except OSError:
                    pass
