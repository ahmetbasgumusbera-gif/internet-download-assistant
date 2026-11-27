"""
Windows'ta uygulamanın oturum açılışında kendiliğinden (arka planda) başlaması: HKCU\\...\\Run.
Yönetici izni gerekmez. Diğer sistemlerde hiçbir şey yapmaz.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from utils import paths

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "InternetDownloadAssistant"


def launch_command() -> str:
    """Açılışta çalıştırılacak komut. Paketlenmiş (exe) sürümde exe'nin kendisi, geliştirmede venv'in pythonw'si."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --background'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")     # konsol penceresi açmaz
    if pythonw.exists():
        exe = pythonw
    return f'"{exe}" "{paths.ROOT_DIR / "main.py"}" --background'


def is_enabled() -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _VALUE_NAME)
        return True
    except OSError:
        return False


def set_enabled(enabled: bool) -> bool:
    """Başarılıysa True. Windows dışında ya da kayıt defteri yazılamazsa False (hata fırlatmaz)."""
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, launch_command())
            else:
                try:
                    winreg.DeleteValue(key, _VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False
    
def _registered_command() -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            return str(winreg.QueryValueEx(key, _VALUE_NAME)[0])
    except OSError:
        return None


def _first_path(command: str) -> str:
    command = command.strip()
    return command[1:].split('"')[0] if command.startswith('"') else command.split(" ")[0]


def sync(enabled: bool) -> bool:
    """
    Açılışta ayarı kayıt defteriyle eşitler. Ayar "Açık" ama kayıt yoksa (ayar zaten açık kaydedilmişti, Ayarlar penceresi
    değişiklik görmediği için hiç yazılmamış olabilir) ya da kayıt silinmiş bir dosyayı gösteriyorsa yazar. Kurulu exe her
    zaman kendi komutunu yazar; geliştirme kopyası, var olan bir dosyayı gösteren başka kayda dokunmaz. Kapalıysa bir şey yapmaz.
    """
    if os.name != "nt" or not enabled:
        return False
    current, wanted = _registered_command(), launch_command()
    if current == wanted:
        return False
    if current and not getattr(sys, "frozen", False) and Path(_first_path(current)).exists():
        return False
    return set_enabled(True)


_PROTO_KEY = r"Software\Classes\ida"


def _base_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    if pythonw.exists():
        exe = pythonw
    return f'"{exe}" "{paths.ROOT_DIR / "main.py"}"'


def register_protocol() -> bool:
    """ida:// bağlantısını bu uygulamaya bağlar (tarayıcı eklentisi kapalı uygulamayı böyle açar). Hata fırlatmaz."""
    if os.name != "nt":
        return False
    try:
        import winreg
        command = f'{_base_command()} --background --server "%1"'
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _PROTO_KEY) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:IDA Protocol")
            winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _PROTO_KEY + r"\shell\open\command") as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
        return True
    except OSError:
        return False