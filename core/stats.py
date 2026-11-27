"""İndirme istatistikleri (Qt yok): her tamamlanan indirme tek satırlık JSON olarak eklenir."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


def record(path: Optional[Path], host: str, size: int, xfer: int, secs: float) -> None:
    if path is None or size <= 0:
        return
    line = json.dumps({"t": datetime.now().isoformat(timespec="seconds"), "host": host,
                       "size": int(size), "xfer": int(xfer), "secs": round(float(secs), 2)})
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def summary(path: Optional[Path], now: Optional[datetime] = None) -> dict:
    now = now or datetime.now()
    out = {"total_bytes": 0, "total_count": 0, "month_bytes": 0, "month_count": 0, "week_bytes": 0,
           "avg_speed": 0.0, "since": "", "top": []}
    hosts: dict[str, list] = {}
    xfer = 0
    secs = 0.0
    week_start = now - timedelta(days=7)
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines() if path else []
    except OSError:
        lines = []
    for line in lines:
        try:
            r = json.loads(line)
            when = datetime.fromisoformat(r["t"])
            size = int(r["size"])
        except (ValueError, KeyError, TypeError):
            continue
        out["total_bytes"] += size
        out["total_count"] += 1
        out["since"] = out["since"] or when.strftime("%d.%m.%Y")
        if (when.year, when.month) == (now.year, now.month):
            out["month_bytes"] += size
            out["month_count"] += 1
        if when >= week_start:
            out["week_bytes"] += size
        entry = hosts.setdefault(str(r.get("host") or "?"), [0, 0])
        entry[0] += size
        entry[1] += 1
        if float(r.get("secs") or 0) > 0 and int(r.get("xfer") or 0) > 0:
            xfer += int(r["xfer"])
            secs += float(r["secs"])
    out["avg_speed"] = xfer / secs if secs > 0 else 0.0
    out["top"] = sorted(((h, v[0], v[1]) for h, v in hosts.items()), key=lambda x: x[1], reverse=True)[:5]
    return out
