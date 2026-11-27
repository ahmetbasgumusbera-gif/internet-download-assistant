"""
Arayüz dili (Qt yok). Kaynak metinler Türkçedir; İngilizce modda core/i18n_en.py içindeki sözlük ve kalıplarla çevrilir.
Çevrilemeyen metin olduğu gibi (Türkçe) kalır ve missing() ile listelenir.
"""
from __future__ import annotations

import os
import re

_LANG = "tr"
_CACHE: dict = {}
_MISSING: dict = {}
_TABLES = None
_PAD = re.compile(r"^(\s*)(.*?)(\s*)$", re.S)
_TR_CHARS = re.compile("[çğıöşüÇĞİÖŞÜ]")
_SEPARATORS = ("   ·   ", "  ·  ")


def language() -> str:
    return _LANG


def set_language(code: str) -> None:
    global _LANG
    _LANG = "en" if code == "en" else "tr"


def detect_system_language() -> str:
    """Windows görüntüleme dili Türkçeyse "tr", değilse "en"."""
    try:
        if os.name == "nt":
            import ctypes
            return "tr" if (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF) == 0x1F else "en"
    except Exception:
        pass
    return "tr" if str(os.environ.get("LANG", "")).lower().startswith("tr") else "en"


def resolve(setting: str) -> str:
    """Ayardaki değeri ("auto" | "tr" | "en") gerçek dile çevirir."""
    return setting if setting in ("tr", "en") else detect_system_language()


def tr(text):
    return text if _LANG == "tr" else _to_english(text)


def tr_for(lang: str, text):
    return text if lang == "tr" else _to_english(text)


def missing() -> list:
    """İngilizce modda çevirisi bulunamayan (Türkçe karakter içeren) metinler."""
    return list(_MISSING)


def _tables():
    global _TABLES
    if _TABLES is None:
        from core import i18n_en
        _TABLES = (i18n_en.EN, [(re.compile(rx, re.S), repl) for rx, repl in i18n_en.PATTERNS])
    return _TABLES


def _to_english(text):
    if not isinstance(text, str) or not text.strip():
        return text
    cached = _CACHE.get(text)
    if cached is not None:
        return cached
    result = _compute(text)
    if len(_CACHE) < 6000:
        _CACHE[text] = result
    if result == text and _TR_CHARS.search(text) and len(_MISSING) < 3000:
        _MISSING.setdefault(text, None)
    return result


def _compute(text: str) -> str:
    table, patterns = _tables()
    hit = table.get(text)
    if hit is not None:
        return hit
    lead, core, trail = _PAD.match(text).groups()
    if core != text:                                   # baştaki / sondaki boşluklar korunur
        inner = _to_english(core)
        return lead + inner + trail if inner != core else text
    for separator in _SEPARATORS:                      # "3 aktif   ·   2 sırada" gibi birleşik metinler parça parça çevrilir
        if separator in core:
            parts = core.split(separator)
            out = [_to_english(p) for p in parts]
            return separator.join(out) if out != parts else core
    if "\n" in core:
        lines = core.split("\n")
        out = [_to_english(line) for line in lines]
        return "\n".join(out) if out != lines else core
    for rx, repl in patterns:
        match = rx.match(core)
        if match:
            return repl(match, _to_english) if callable(repl) else match.expand(repl)
    return core
