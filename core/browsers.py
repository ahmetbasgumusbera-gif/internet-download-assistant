"""Kurulu tarayıcıları bulur ve eklenti sayfalarını açar (Qt yok). Yalnızca Windows."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Browser:
    name: str
    exe: str
    url: str            # eklentiler sayfası
    supported: bool     # eklenti bu tarayıcıda çalışır mı


def _candidates() -> list[tuple[str, list[str], str, bool]]:
    env = os.environ
    pf, pf86, local = env.get("ProgramFiles", ""), env.get("ProgramFiles(x86)", ""), env.get("LOCALAPPDATA", "")
    j = os.path.join
    return [
        ("Google Chrome", [j(pf, "Google", "Chrome", "Application", "chrome.exe"),
                           j(pf86, "Google", "Chrome", "Application", "chrome.exe"),
                           j(local, "Google", "Chrome", "Application", "chrome.exe")], "chrome://extensions", True),
        ("Microsoft Edge", [j(pf86, "Microsoft", "Edge", "Application", "msedge.exe"),
                            j(pf, "Microsoft", "Edge", "Application", "msedge.exe")], "edge://extensions", True),
        ("Brave", [j(pf, "BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
                   j(local, "BraveSoftware", "Brave-Browser", "Application", "brave.exe")], "brave://extensions", True),
        ("Opera", [j(local, "Programs", "Opera", "opera.exe")], "opera://extensions", True),
        ("Opera GX", [j(local, "Programs", "Opera GX", "opera.exe")], "opera://extensions", True),
        ("Vivaldi", [j(local, "Vivaldi", "Application", "vivaldi.exe")], "vivaldi://extensions", True),
        ("Firefox", [j(pf, "Mozilla Firefox", "firefox.exe"), j(pf86, "Mozilla Firefox", "firefox.exe")],
         "about:debugging#/runtime/this-firefox", True),
    ]


def detect() -> list[Browser]:
    if os.name != "nt":
        return []
    found = []
    for name, exes, url, supported in _candidates():
        exe = next((e for e in exes if e and os.path.isfile(e)), None)
        if exe:
            found.append(Browser(name, exe, url, supported))
    return found


def open_extensions_page(browser: Browser) -> bool:
    try:
        subprocess.Popen([browser.exe, browser.url], close_fds=True)
        return True
    except OSError:
        return False
