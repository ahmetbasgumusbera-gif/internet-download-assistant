"""
Klasör ve ad kuralları (Qt yok).

Kural metni (satır satır, # ile yorum):
    ext: iso, img => D:\ISO          uzantıya göre klasör
    site: example.com => D:\Oyunlar  siteye göre klasör
    strip: Oyunindir vip             dosya adından bu metni sil
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

_clean = False
_folders: list = []
_strips: list = []

_EXT = re.compile(r"\.[A-Za-z0-9]{1,8}")
_TLD = r"(?:com|net|org|io|co|tv|me|info|ws|nl|to|xyz|club|site|top|pw|biz|us|tr|ru|de|uk|cc|vip|fun|online|download)"
_DOMAIN = rf"(?:www\.)?(?:[\w-]+\.)+{_TLD}"
_BRACKET_PREFIX = re.compile(rf"^\s*[\[\(]\s*{_DOMAIN}\s*[\]\)]\s*[-_–.]*\s*", re.IGNORECASE)
_WWW_PREFIX = re.compile(rf"^\s*{_DOMAIN}\s*[-_–]+\s*", re.IGNORECASE)
_BRACKET_SUFFIX = re.compile(rf"\s*[\[\(]\s*{_DOMAIN}\s*[\]\)]\s*$", re.IGNORECASE)


def ext_of(name: str) -> str:
    """Geçerli bir dosya uzantısı varsa '.jpg' gibi döndürür, yoksa ''. ('v1.2 notlar' uzantı sayılmaz.)"""
    suffix = os.path.splitext(name or "")[1]
    return suffix if _EXT.fullmatch(suffix) else ""


def parse(text: str):
    folders, strips = [], []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("strip:"):
            value = line[6:].strip()
            if value:
                strips.append(value)
            continue
        if "=>" not in line:
            continue
        left, target = line.split("=>", 1)
        kind, _, values = left.partition(":")
        kind = kind.strip().lower()
        items = [v.strip().lower().lstrip(".") for v in values.split(",") if v.strip()]
        target = target.strip().strip('"')
        if kind in ("ext", "site") and items and target:
            folders.append((kind, items, target))
    return folders, strips


def configure(clean_names: bool, text: str) -> None:
    global _clean, _folders, _strips
    _clean = bool(clean_names)
    _folders, _strips = parse(text)


def pick_dir(url: str, file_name: str, default) -> Path:
    ext = ext_of(file_name).lstrip(".").lower() or ext_of(unquote(urlparse(url or "").path)).lstrip(".").lower()
    host = (urlparse(url or "").hostname or "").lower()
    for kind, values, target in _folders:
        if kind == "ext" and ext in values:
            return Path(target)
        if kind == "site" and any(host == v or host.endswith("." + v) for v in values):
            return Path(target)
    return Path(default)


def clean_name(name: str) -> str:
    if not name:
        return name
    stem, ext = os.path.splitext(name)
    if not _EXT.fullmatch(ext):
        stem, ext = name, ""
    out = stem
    for text in _strips:
        out = re.sub(re.escape(text), "", out, flags=re.IGNORECASE)
    if _clean:
        out = _BRACKET_PREFIX.sub("", out)
        out = _WWW_PREFIX.sub("", out)
        out = _BRACKET_SUFFIX.sub("", out)
    out = re.sub(r"\s{2,}", " ", out).strip(" -_.")
    return (out + ext) if out else name
