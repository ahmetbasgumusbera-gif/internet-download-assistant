"""Chrome eklentisinden Firefox paketi üretir (ikinci bir manifest tutmaya gerek kalmaz)."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

GECKO_ID = "ida@internet-download-assistant"


def build(src, dest) -> Path:
    """src (extension/) klasörünü dest'e kopyalar ve manifesti Firefox'a uyarlar. dest'i döndürür."""
    src, dest = Path(src), Path(dest)
    manifest = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(src, dest)
    manifest.pop("minimum_chrome_version", None)
    manifest["background"] = {"scripts": ["sniffer.js", "background.js"]}      # Firefox: service_worker yerine olay sayfası
    manifest["browser_specific_settings"] = {"gecko": {"id": GECKO_ID, "strict_min_version": "128.0"}}
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest
