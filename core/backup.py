"""Ayar / geçmiş yedekleme (Qt yok). Geri yükleme bir sonraki açılışta uygulanır (veritabanı açıkken değiştirilmez)."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

from utils import paths
from utils.logger import get_logger
from utils.version import __version__

log = get_logger(__name__)
FILES = ("config.json", "history.db", "stats.jsonl", "speedtune.json")


class BackupError(Exception):
    """Kullanıcıya gösterilecek yedekleme hatası."""


def create(dest) -> list:
    """Veri klasöründeki ayar ve geçmişi zip'e yazar; eklenen dosya adlarını döndürür."""
    data = paths.DATA_DIR
    names = []
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in FILES:
            src = data / name
            if not src.is_file():
                continue
            if name == "history.db":
                tmp = data / "history.backup.tmp"
                try:
                    source, copy = sqlite3.connect(src), sqlite3.connect(tmp)
                    try:
                        source.backup(copy)
                    finally:
                        copy.close()
                        source.close()
                    zf.write(tmp, name)
                except sqlite3.Error:
                    zf.write(src, name)
                finally:
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
            else:
                zf.write(src, name)
            names.append(name)
        zf.writestr("ida-backup.json", json.dumps({"version": __version__,
                                                   "created": datetime.now().isoformat(timespec="seconds")}))
    return names


def stage_restore(archive) -> list:
    """Yedeği "<ad>.restore" olarak hazırlar; apply_pending() açılışta yerine koyar."""
    data = paths.DATA_DIR
    data.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as zf:
            names = [n for n in FILES if n in zf.namelist()]
            if not names:
                raise BackupError("Bu dosya bir IDA yedeği değil.")
            for name in names:
                with zf.open(name) as src, open(data / (name + ".restore"), "wb") as out:
                    shutil.copyfileobj(src, out)
    except (zipfile.BadZipFile, OSError) as e:
        raise BackupError(f"Yedek okunamadı: {e}")
    return names


def apply_pending() -> list:
    """Uygulama açılırken, hazırlanmış geri yüklemeleri yerine koyar."""
    done = []
    for name in FILES:
        pending = paths.DATA_DIR / (name + ".restore")
        if pending.is_file():
            try:
                os.replace(pending, paths.DATA_DIR / name)
                done.append(name)
            except OSError as e:
                log.warning("Yedek yerine konamadı (%s): %s", name, e)
    return done
