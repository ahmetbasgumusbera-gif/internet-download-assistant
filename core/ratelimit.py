"""
Basit token-bucket hız sınırlayıcı. Birden fazla thread (ve ileride birden fazla
indirme) aynı örneği paylaşabilir.
"""
from __future__ import annotations

import threading
import time
from typing import Optional


class RateLimiter:
    def __init__(self, bytes_per_sec: int = 0):
        self._rate = max(0, int(bytes_per_sec))
        self._allowance = 0.0
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def set_rate(self, bytes_per_sec: int) -> None:
        """0 = sınırsız. Çalışırken değiştirilebilir."""
        with self._lock:
            self._rate = max(0, int(bytes_per_sec))
            self._allowance = 0.0
            self._last = time.monotonic()

    @property
    def rate(self) -> int:
        return self._rate

    def acquire(self, nbytes: int, stop_event: Optional[threading.Event] = None) -> None:
        """nbytes kadar veri için gereken süre kadar bekler (sınırsızsa hemen döner)."""
        with self._lock:
            rate = self._rate
            if rate <= 0:
                return
            now = time.monotonic()
            self._allowance = min(float(rate), self._allowance + (now - self._last) * rate)
            self._last = now
            self._allowance -= nbytes
            wait = -self._allowance / rate if self._allowance < 0 else 0.0
        if wait > 0:
            if stop_event is not None:
                stop_event.wait(wait)
            else:
                time.sleep(wait)