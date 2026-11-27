"""Dosya karma (hash) doğrulama (Qt yok): algoritma tahmini ve bloklar halinde hesaplama. Hiçbir dosyaya dokunmaz."""
from __future__ import annotations

import hashlib
import re
import threading
from pathlib import Path
from typing import Callable, Optional

_ALGOS = {32: "md5", 40: "sha1", 64: "sha256", 128: "sha512"}


def parse_expected(text: str) -> tuple[str, str]:
    """Yapıştırılan metinden (algoritma, onaltılık değer) çıkarır. "sha256: abc..." ve sha256sum satırları da kabul edilir."""
    for token in re.split(r"[\s:=*]+", text or ""):
        value = token.strip().lower()
        if value and re.fullmatch(r"[0-9a-f]+", value) and len(value) in _ALGOS:
            return _ALGOS[len(value)], value
    return "", ""


def parse_for_file(text: str, filename: str) -> tuple[str, str]:
    """Yan dosya (.sha256 vb.) metninden bu dosyanın karmasını çıkarır; çok satırlıysa dosya adına göre seçer."""
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) > 1:
        for line in lines:
            if filename and filename.lower() in line.lower():
                hit = parse_expected(line)
                if hit[0]:
                    return hit
        return "", ""
    return parse_expected(text)


def file_hash(path: Path, algo: str, progress: Optional[Callable[[int, int], None]] = None,
              cancel: Optional[threading.Event] = None, chunk: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.new(algo)
    size = Path(path).stat().st_size
    done = 0
    with open(path, "rb") as f:
        while True:
            if cancel is not None and cancel.is_set():
                raise InterruptedError("iptal")
            block = f.read(chunk)
            if not block:
                break
            digest.update(block)
            done += len(block)
            if progress:
                progress(done, size)
    return digest.hexdigest()
