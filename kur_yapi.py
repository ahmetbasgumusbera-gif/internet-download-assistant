"""
Proje iskeletini oluşturur. Bir kez çalıştırılır, sonra silinebilir.
Betik hangi klasördeyse orayı proje kökü kabul eder (taşınabilir).
"""
from pathlib import Path

KOK = Path(__file__).resolve().parent

KLASORLER = [
    "bin",
    "core",
    "core/engines",
    "gui",
    "gui/components",
    "utils",
    "data",
    "tests",
]

# __init__.py gereken paketler
PAKETLER = [
    "core",
    "core/engines",
    "gui",
    "gui/components",
    "utils",
    "tests",
]

# Başlangıçta boş oluşturulacak dosyalar
BOS_DOSYALAR = [
    "main.py",
    "run.py",
    "README.md",
    "bin/.gitkeep",
    "data/.gitkeep",
]

REQUIREMENTS = """\
PySide6>=6.8
yt-dlp
requests
"""

GITIGNORE = """\
# Sanal ortam
.venv/

# Python
__pycache__/
*.pyc

# Çalışma zamanı verisi
data/*
!data/.gitkeep

# Geçici indirme parçaları
.temp_downloads/

# Büyük ikili dosyalar
bin/*.exe
bin/*.dll

# Editör
.vscode/
.idea/
"""


def yaz_yoksa(yol: Path, icerik: str = "") -> None:
    """Dosya zaten varsa dokunmaz, böylece betik tekrar çalıştırılsa da güvenlidir."""
    if yol.exists():
        print(f"  atlandı (var): {yol.relative_to(KOK)}")
        return
    yol.write_text(icerik, encoding="utf-8")
    print(f"  oluşturuldu : {yol.relative_to(KOK)}")


def main() -> None:
    print(f"Proje kökü: {KOK}\n")

    print("[Klasörler]")
    for k in KLASORLER:
        (KOK / k).mkdir(parents=True, exist_ok=True)
        print(f"  {k}/")

    print("\n[Paket dosyaları]")
    for p in PAKETLER:
        yaz_yoksa(KOK / p / "__init__.py")

    print("\n[Boş dosyalar]")
    for d in BOS_DOSYALAR:
        yaz_yoksa(KOK / d)

    print("\n[Yapılandırma dosyaları]")
    yaz_yoksa(KOK / "requirements.txt", REQUIREMENTS)
    yaz_yoksa(KOK / ".gitignore", GITIGNORE)

    print("\nTamam. Bu betiği artık silebilirsin.")


if __name__ == "__main__":
    main()