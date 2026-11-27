"""Windows Gezgini'nde dosyayı gösterir: klasör zaten açıksa genelde var olan pencere öne gelir ve dosya seçilir."""
from __future__ import annotations

import os
from pathlib import Path


def reveal_in_explorer(path: Path) -> bool:
    """Başarılıysa True. Windows dışında ya da hata olursa False (çağıran yedek yönteme geçer)."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        shell32 = ctypes.windll.shell32
        shell32.SHParseDisplayName.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
                                               wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
        shell32.SHOpenFolderAndSelectItems.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, wintypes.DWORD]
        pidl = ctypes.c_void_p()
        attrs = wintypes.ULONG(0)
        if shell32.SHParseDisplayName(str(Path(path)), None, ctypes.byref(pidl), 0, ctypes.byref(attrs)) != 0:
            return False
        try:
            return shell32.SHOpenFolderAndSelectItems(pidl, 0, None, 0) == 0
        finally:
            ctypes.windll.ole32.CoTaskMemFree(pidl)
    except Exception:
        return False
