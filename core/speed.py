"""Hız sınırı yardımcıları (Qt yok): basamaklı arttır/düşür, metin biçimi ve genel + kart sınırını birleştiren sınırlayıcı."""
from __future__ import annotations

from typing import Optional
import threading

STEPS = (128, 256, 512, 1024, 2048, 5120, 10240, 20480)    # KB/sn


def step_limit(current_kbps: int, up: bool) -> int:
    """Bir basamak yukarı/aşağı. 0 = sınırsız: yukarısı yine sınırsız, aşağısı en yüksek basamak."""
    cur = int(current_kbps or 0)
    if up:
        if cur == 0:
            return 0
        for step in STEPS:
            if step > cur:
                return step
        return 0                                   # en üst basamağı aştı: sınırsız
    if cur == 0:
        return STEPS[-1]
    lower = [s for s in STEPS if s < cur]
    return lower[-1] if lower else STEPS[0]


def limit_text(kbps: int) -> str:
    kbps = int(kbps or 0)
    if kbps <= 0:
        return "Sınırsız"
    return f"{kbps} KB/s" if kbps < 1024 else f"{kbps / 1024:g} MB/s"


class CompositeLimiter:
    """Birden çok hız sınırlayıcıyı (genel + indirmeye özel) art arda uygular; etkin hız düşük olandır."""

    def __init__(self, *limiters):
        self._limiters = limiters

    @property
    def rate(self) -> int:
        rates = [l.rate for l in self._limiters if l.rate > 0]
        return min(rates) if rates else 0

    def acquire(self, nbytes: int, stop_event: Optional[threading.Event] = None) -> None:
        for limiter in self._limiters:
            limiter.acquire(nbytes, stop_event)
