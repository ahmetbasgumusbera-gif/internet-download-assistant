"""Arşiv açma (Qt yok): zip/tar kendi başına, rar/7z için 7-Zip ya da Windows'un tar.exe'si. Güvensiz yollar reddedilir."""
from __future__ import annotations

import bz2
import gzip
import lzma
import os
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path

from core import procutil

_TAR = (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2", ".txz", ".tar")
_OTHER = (".zip", ".7z", ".rar", ".gz", ".bz2", ".xz")


class ExtractError(Exception):
    """Kullanıcıya gösterilecek, anlaşılır arşiv hatası."""


def is_archive(path) -> bool:
    return str(path).lower().endswith(_TAR + _OTHER)


def _stem(name: str) -> str:
    lower = name.lower()
    for suffix in sorted(_TAR + _OTHER, key=len, reverse=True):
        if lower.endswith(suffix):
            return name[: -len(suffix)]
    return Path(name).stem


def destination(path: Path) -> Path:
    """Arşivin yanında, arşivin adını taşıyan boş bir klasör yolu (varsa "ad (1)" ...)."""
    path = Path(path)
    base = _stem(path.name) or "arsiv"
    candidate, n = path.parent / base, 1
    while candidate.exists():
        candidate = path.parent / f"{base} ({n})"
        n += 1
    return candidate


def extract(path) -> Path:
    """Arşivi kendi adlı klasöre çıkarır ve klasörü döndürür. Başarısızsa ExtractError (yarım klasör silinir)."""
    path = Path(path)
    if not path.is_file():
        raise ExtractError("Arşiv dosyası bulunamadı.")
    dest = destination(path)
    dest.mkdir(parents=True)
    name = path.name.lower()
    try:
        if name.endswith(".zip"):
            try:
                _zip(path, dest)
            except NotImplementedError:
                _external(path, dest)
        elif name.endswith(_TAR):
            _tar(path, dest)
        elif name.endswith((".gz", ".bz2", ".xz")):
            _single(path, dest)
        else:
            _external(path, dest)
    except ExtractError:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    except RuntimeError as e:
        shutil.rmtree(dest, ignore_errors=True)
        raise ExtractError("Arşiv parola korumalı: parolalı arşivler otomatik açılamaz." if "encrypted" in str(e).lower()
                           else f"Arşiv açılamadı: {e}")
    except (zipfile.BadZipFile, tarfile.TarError, OSError, EOFError, subprocess.SubprocessError, lzma.LZMAError) as e:
        shutil.rmtree(dest, ignore_errors=True)
        raise ExtractError(f"Arşiv açılamadı: {e}")
    return dest


def _inside(dest: Path, name: str) -> bool:
    root = os.path.realpath(dest)
    target = os.path.realpath(os.path.join(root, name))
    return target == root or target.startswith(root + os.sep)


def _zip(path: Path, dest: Path) -> None:
    with zipfile.ZipFile(path) as zf:
        if any(not _inside(dest, info.filename) for info in zf.infolist()):
            raise ExtractError("Arşivde güvensiz bir dosya yolu var, açılmadı.")
        zf.extractall(dest)


def _tar(path: Path, dest: Path) -> None:
    with tarfile.open(path) as tf:
        try:
            tf.extractall(dest, filter="data")
        except TypeError:                                   # eski Python: yolları kendimiz denetleriz
            if any(not _inside(dest, m.name) for m in tf.getmembers()):
                raise ExtractError("Arşivde güvensiz bir dosya yolu var, açılmadı.")
            tf.extractall(dest)


def _single(path: Path, dest: Path) -> None:
    opener = {".gz": gzip.open, ".bz2": bz2.open, ".xz": lzma.open}[path.suffix.lower()]
    with opener(path, "rb") as src, open(dest / (_stem(path.name) or "dosya"), "wb") as out:
        shutil.copyfileobj(src, out)


def _find_7z() -> str:
    for name in ("7z", "7za"):
        found = shutil.which(name)
        if found:
            return found
    for var, sub in (("ProgramFiles", "7-Zip"), ("ProgramFiles(x86)", "7-Zip"),
                     ("LOCALAPPDATA", os.path.join("Programs", "7-Zip"))):
        base = os.environ.get(var)
        if base and os.path.isfile(os.path.join(base, sub, "7z.exe")):
            return os.path.join(base, sub, "7z.exe")
    return ""


def _external(path: Path, dest: Path) -> None:
    seven = _find_7z()
    if seven:
        done = procutil.run_capture([seven, "x", "-y", f"-o{dest}", "-pIDA", str(path)], 7200)
        if done.returncode in (0, 1):                      # 1 = uyarıyla bitti
            return
        tail = [ln for ln in (done.stdout or done.stderr or "").splitlines() if ln.strip()][-1:] or ["bilinmeyen hata"]
        raise ExtractError(f"7-Zip arşivi açamadı: {tail[0].strip()[:200]}")
    tar = shutil.which("tar")
    if tar:                                                # güncel Windows 11: libarchive rar ve 7z de okur
        done = procutil.run_capture([tar, "-xf", str(path), "-C", str(dest)], 7200)
        if done.returncode == 0:
            return
    raise ExtractError("Bu arşiv türü (rar/7z) için 7-Zip gerekli. Kurulum:  winget install 7zip.7zip")
