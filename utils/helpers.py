"""
Küçük, bağımsız yardımcı fonksiyonlar. Qt veya ağ bağımlılığı yoktur.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

# ---------- Dosya adı güvenliği ----------

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str, fallback: str = "indirilen_dosya", max_len: int = 150) -> str:
    """
    Sunucudan/URL'den gelen dosya adını güvenli hale getirir:
    klasör parçalarını atar, yasaklı karakterleri değiştirir,
    Windows rezerve adlarını düzeltir, uzunluğu sınırlar (uzantıyı korur).
    """
    # Yol ayırıcılarla gelen kısımlardan yalnızca son parçayı al (path traversal engeli)
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = _INVALID_CHARS.sub("_", name).strip().rstrip(". ")

    if not name or set(name) <= {"."}:
        return fallback

    stem, dot, ext = name.rpartition(".")
    if not dot:                      # uzantı yok
        stem, ext = name, ""
    elif not stem:                   # ".gitignore" gibi
        stem, ext = name, ""
    else:
        ext = "." + ext

    if len(ext) > 12:                # anormal uzun "uzantı" aslında ad parçasıdır
        stem, ext = name, ""

    if stem.split(".")[0].upper() in _RESERVED_NAMES:
        stem = "_" + stem

    stem = stem[: max(1, max_len - len(ext))].rstrip(". ")
    return (stem or fallback) + ext


def unique_path(path: Path) -> Path:
    """Dosya zaten varsa 'ad (1).uzantı', 'ad (2).uzantı' ... şeklinde boş bir yol döndürür."""
    path = Path(path)
    if not path.exists():
        return path
    counter = 1
    while True:
        candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


# ---------- Biçimlendirme ----------

def format_size(num_bytes: Optional[float]) -> str:
    if num_bytes is None or num_bytes < 0:
        return "Bilinmiyor"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


def format_speed(bytes_per_sec: Optional[float]) -> str:
    if not bytes_per_sec or bytes_per_sec <= 0:
        return "-"
    return f"{format_size(bytes_per_sec)}/s"


def format_eta(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0 or seconds == float("inf"):
        return "-"
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h}sa {m:02d}dk"
    if m:
        return f"{m}dk {s:02d}sn"
    return f"{s}sn"


# ---------- Kategori ve güvenlik ----------

# Not: "html/htm" bilerek hiçbir kategoride yok (web sayfası dosya sayılmaz; pano takibi ve sayfa denetimi buna dayanır).
CATEGORIES: dict[str, set[str]] = {
    "Videolar":  {"mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "ts", "mpg", "mpeg", "3gp", "ogv",
                  "m2ts", "mts", "vob", "divx", "asf", "rm", "rmvb"},
    "Müzik":     {"mp3", "wav", "flac", "aac", "ogg", "oga", "m4a", "wma", "opus", "mid", "midi", "amr", "aiff",
                  "aif", "ape", "m4b", "mka", "ac3", "dts", "weba"},
    "Resimler":  {"jpg", "jpeg", "jfif", "png", "gif", "bmp", "webp", "svg", "ico", "tiff", "tif", "avif", "heic",
                  "heif", "raw", "cr2", "cr3", "nef", "arw", "dng", "orf", "rw2"},
    "Belgeler":  {"pdf", "doc", "docx", "dot", "dotx", "docm", "xls", "xlsx", "xlsm", "ppt", "pptx", "pptm", "pps",
                  "ppsx", "txt", "csv", "tsv", "odt", "ods", "odp", "rtf", "pages", "numbers", "key", "xps", "tex",
                  "log"},
    "E-kitaplar": {"epub", "azw", "azw3", "mobi", "fb2", "djvu", "cbz", "cbr"},
    "Tasarım":   {"psd", "ai", "eps", "indd", "xcf", "sketch", "fig"},
    "Arşivler":  {"zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "tbz2", "xz", "txz", "zst", "lz", "lzma", "cab",
                  "z", "arj", "wim", "iso", "img", "vhd", "vhdx", "vmdk", "ova"},
    "Programlar": {"exe", "msi", "msix", "appx", "apk", "aab", "xapk", "apks", "dmg", "pkg", "deb", "rpm", "appimage",
                   "run", "bat", "cmd", "ps1", "sh", "whl", "jar", "msu", "msp"},
    "Kod":       {"py", "pyw", "js", "mjs", "json", "xml", "yaml", "yml", "sql", "md", "c", "cpp", "h", "hpp", "java",
                  "cs", "go", "rs", "rb", "lua", "kt", "swift"},
}

DANGEROUS_EXTENSIONS = {
    "exe", "msi", "msix", "appx", "msp", "msu", "bat", "cmd", "ps1", "vbs", "vbe", "wsf", "wsh", "jse", "hta",
    "reg", "inf", "cpl", "pif", "js", "jar", "scr", "com", "apk", "aab", "xapk", "dll", "lnk", "sh", "run", "py",
    "pyw", "dmg", "pkg", "deb", "rpm", "appimage", "docm", "xlsm", "pptm", "cab",
}


def get_extension(filename: str) -> str:
    return Path(filename).suffix.lstrip(".").lower()


def get_category(filename: str) -> str:
    ext = get_extension(filename)
    for category, extensions in CATEGORIES.items():
        if ext in extensions:
            return category
    return "Diğer"


def is_dangerous(filename: str) -> bool:
    return get_extension(filename) in DANGEROUS_EXTENSIONS


def category_folder(base_dir: Path, filename: str) -> Path:
    """İndirme klasörünün altındaki kategori klasörü (Videolar, Müzik, ... ya da Diğer)."""
    return Path(base_dir) / get_category(filename)


# ---------- Disk ----------

def has_enough_space(directory: Path, required_bytes: int, margin: float = 1.05) -> bool:
    """
    Hedef sürücüde yeterli boş alan var mı? Klasör henüz yoksa
    var olan en yakın üst klasörün sürücüsüne bakılır.
    """
    target = Path(directory)
    while not target.exists() and target != target.parent:
        target = target.parent
    try:
        return shutil.disk_usage(target).free >= required_bytes * margin
    except OSError:
        return True  # ölçemiyorsak engelleme


def hide_on_windows(path: Path) -> None:
    """Windows'ta klasörü/dosyayı gizli yapar. Diğer sistemlerde bir şey yapmaz."""
    if os.name == "nt":
        try:                                   # her indirmede "attrib" süreci açmak yerine doğrudan Windows API'si
            import ctypes
            kernel32 = ctypes.windll.kernel32
            attrs = kernel32.GetFileAttributesW(str(path))
            if attrs != 0xFFFFFFFF:
                if attrs & 0x2:
                    return                     # zaten gizli
                if kernel32.SetFileAttributesW(str(path), attrs | 0x2):
                    return
        except Exception:
            pass
    if os.name != "nt":
        return
    try:
        subprocess.run(
            ["attrib", "+h", str(path)],
            check=False,
            capture_output=True,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except OSError:
        pass

def reserve_unique_path(path: Path) -> Path:
    """
    unique_path gibi çalışır ama boş bir dosya oluşturarak yeri ATOMİK olarak ayırır.
    Aynı anda biten iki indirmenin aynı adı seçip birbirinin üzerine yazmasını önler.
    """
    path = Path(path)
    counter = 0
    candidate = path
    while True:
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            counter += 1
            candidate = path.with_name(f"{path.stem} ({counter}){path.suffix}")
            continue
        os.close(fd)
        return candidate

def set_sparse(file_obj) -> bool:
    """
    Windows'ta (NTFS) dosyayı "seyrek" işaretler: uzak bir konuma yazınca aradaki boşluk
    sıfırla doldurulmaz. Başarılıysa True döner. Başka sistemlerde (zaten seyrek) True,
    desteklemeyen dosya sisteminde veya hata durumunda False döner (hata fırlatmaz).
    """
    if os.name != "nt":
        return True
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.DeviceIoControl.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
            wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
        ]
        kernel32.DeviceIoControl.restype = wintypes.BOOL

        handle = msvcrt.get_osfhandle(file_obj.fileno())
        returned = wintypes.DWORD(0)
        ok = kernel32.DeviceIoControl(
            handle, 0x000900C4,  # FSCTL_SET_SPARSE
            None, 0, None, 0, ctypes.byref(returned), None,
        )
        return bool(ok)
    except Exception:
        return False