"""
Projeyi sanal ortamıyla (.venv) başlatır. Yollar bu dosyanın konumuna göre hesaplanır,
bu yüzden proje klasörü nereye taşınırsa taşınsın (.venv yeniden kurulduysa) çalışır.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def venv_python() -> Path:
    if os.name == "nt":
        return ROOT / ".venv" / "Scripts" / "python.exe"
    return ROOT / ".venv" / "bin" / "python"


def main() -> int:
    python = venv_python()
    if not python.exists():
        print("HATA: Sanal ortam (.venv) bulunamadı!")
        print("Kurulum için proje klasöründe şunları çalıştırın:")
        print("    python -m venv .venv")
        print("    .venv\\Scripts\\Activate.ps1")
        print("    pip install -r requirements.txt")
        input("Çıkmak için Enter'a basın...")
        return 1
    return subprocess.call([str(python), str(ROOT / "main.py")], cwd=str(ROOT))


if __name__ == "__main__":
    sys.exit(main())