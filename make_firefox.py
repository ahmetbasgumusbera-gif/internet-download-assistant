"""Firefox için eklenti paketi üretir: dist_firefox/ klasörü ve IDA-firefox.zip (about:debugging ya da AMO için)."""
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core import firefox_ext  # noqa: E402


def main() -> None:
    out = firefox_ext.build(ROOT / "extension", ROOT / "dist_firefox")
    archive = ROOT / "IDA-firefox.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(out.rglob("*")):
            if f.is_file():
                zf.write(f, f.relative_to(out).as_posix())
    print(f"Hazır: {out}  ve  {archive}")


if __name__ == "__main__":
    main()
