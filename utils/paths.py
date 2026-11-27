"""
Projedeki tüm dosya yollarının tek merkezi.

Geliştirirken (python main.py): her şey proje klasöründedir (bin/, data/, extension/).
Paketli sürümde (IDA.exe): program dosyaları kurulum klasöründe (salt-okunur kabul edilir), kullanıcı verileri
(ayarlar, geçmiş, günlükler, güncellenebilir araçlar) %LOCALAPPDATA%\\InternetDownloadAssistant altındadır.
Hiçbir yol sabit değildir; başka bir bilgisayarda eksik klasörler ilk açılışta kendiliğinden oluşturulur.
"""
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

FROZEN = bool(getattr(sys, "frozen", False))

# geliştirme: utils/paths.py -> utils -> proje kökü;  paketli: IDA.exe'nin bulunduğu klasör
ROOT_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent.parent


def _user_data_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "InternetDownloadAssistant"


BIN_DIR = ROOT_DIR / "bin"                         # kurulumla gelen araçlar (yt-dlp, ffmpeg, Deno): temel kopya
DATA_DIR = _user_data_dir() if FROZEN else ROOT_DIR / "data"
TOOLS_DIR = DATA_DIR / "tools"                     # güncellenebilir araç kopyaları (açılışta bin/'den tohumlanır)
EXTENSION_DIR = ROOT_DIR / "extension"             # tarayıcı eklentisi
LOG_DIR = DATA_DIR / "logs"

CONFIG_FILE = DATA_DIR / "config.json"
DB_FILE = DATA_DIR / "history.db"
LOG_FILE = LOG_DIR / "ida.log"

TEMP_FOLDER_NAME = ".temp_downloads"


def ensure_dirs() -> None:
    """Çalışma zamanı klasörlerini oluşturur (yoksa)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)


def _windows_downloads_dir() -> Optional[Path]:
    """Windows'un gerçek "İndirilenler" klasörü (taşınmış ya da yerelleştirilmiş olsa da). Başarısızsa None."""
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                        ("Data4", ctypes.c_ubyte * 8)]

        folder_id = GUID(0x374DE290, 0x123F, 0x4565,
                         (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
        shell32 = ctypes.windll.shell32
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE,
                                                 ctypes.POINTER(ctypes.c_wchar_p)]
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        pointer = ctypes.c_wchar_p()
        if shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(pointer)) == 0 and pointer.value:
            path = Path(pointer.value)
            ctypes.windll.ole32.CoTaskMemFree(pointer)
            return path
    except Exception:
        pass
    return None


def default_download_dir() -> Path:
    """Varsayılan indirme klasörü: <İndirilenler>/IDA"""
    base = _windows_downloads_dir() if os.name == "nt" else None
    return (base or Path.home() / "Downloads") / "IDA"


def temp_root(download_dir: Path) -> Path:
    """
    Parçaların tutulacağı geçici klasör. İndirme klasörüyle aynı sürücüde
    olması, birleştirme/taşıma işleminin hızlı olmasını sağlar.
    """
    return Path(download_dir) / TEMP_FOLDER_NAME


def get_ffmpeg_dir() -> Optional[Path]:
    """
    FFmpeg'in bulunduğu klasörü döndürür: önce güncellenebilir tools/, sonra bin/, en son sistem PATH'i.
    Hiçbiri yoksa None döner.
    """
    exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"

    for folder in (TOOLS_DIR, BIN_DIR):
        if (folder / exe_name).is_file():
            return folder

    found = shutil.which("ffmpeg")
    if found:
        return Path(found).parent

    return None
