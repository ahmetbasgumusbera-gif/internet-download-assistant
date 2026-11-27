"""Ağ ayarları (Qt yok): kullanıcının proxy adresi."""
from __future__ import annotations

import re

_proxy = ""
_OK = re.compile(r"^(https?|socks4a?|socks5h?)://\S+$", re.IGNORECASE)


def normalize(text: str) -> str:
    """"host:port" -> "http://host:port"; geçersizse boş."""
    text = (text or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "http://" + text
    return text if _OK.match(text) else ""


def configure(text: str) -> None:
    global _proxy
    _proxy = normalize(text)


def proxy() -> str:
    return _proxy


def requests_proxies() -> dict:
    return {"http": _proxy, "https": _proxy} if _proxy else {}


def ytdlp_args() -> list:
    return ["--proxy", _proxy] if _proxy else []
