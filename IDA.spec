# -*- mode: python ; coding: utf-8 -*-
# PyInstaller tarifi: dist/IDA/ klasörü (onedir, konsolsuz). Çalıştırma:  pyinstaller --noconfirm --clean IDA.spec
# yt-dlp, ffmpeg ve Deno burada gömülmez: bin/ klasöründen kurulum paketine (installer/IDA.iss) girer.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(SPECPATH)
ICON = ROOT / "installer" / "IDA.ico"

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=collect_data_files("certifi"),
    hiddenimports=["PySide6.QtNetwork", "socks", "sockshandler"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["yt_dlp", "tkinter", "pytest"],   # yt-dlp ayrı yt-dlp.exe olarak kullanılır
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="IDA",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(ICON) if ICON.exists() else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="IDA",
)
