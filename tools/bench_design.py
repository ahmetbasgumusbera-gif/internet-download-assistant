"""
Çok parçalı indirmede üç farklı DOSYA DÜZENİNİ gerçek diskinde karşılaştırır (internet gerekmez).
Gerçek bir indirmeyi taklit eder: parçalar paralel yazılır, sonra dosya tamamlanır.

  P : parça dosyaları + sonda birleştirme       (Aşama B-C'deki yöntem)
  S : tek dosya, baştan tam boyutlandırılır     (D.1'deki yöntem, seyrek işaretli)
  F : tek dosya, seyrek işaretli, baştan boyutlandırma YOK (dosya yazıldıkça kendiliğinden büyür)

Kullanıcının hissettiği iki gecikme:
  Hazırlık = indirme başlamadan önceki bekleme   |   Bitiş = son bayttan "Tamamlandı"ya kadar

Kullanım (proje kökünde):
    python tools/bench_design.py            # 1 GB, 4 parça, indirme klasöründe
    python tools/bench_design.py 2 8 D:\\x   # 2 GB, 8 parça, başka klasörde
"""
import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import paths
from utils.helpers import set_sparse

MB = 1024 * 1024
CHUNK = os.urandom(MB)


def fs_name(path: Path) -> str:
    if os.name != "nt":
        return "(Windows dışı)"
    try:
        import ctypes
        root = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
        buf = ctypes.create_unicode_buffer(32)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(root), None, 0, None, None, None, buf, 32)
        return buf.value if ok else "?"
    except Exception:
        return "?"


def write_region(path: Path, mode: str, offset: int, nbytes: int) -> float:
    """Bir parçayı yazar ve dosyayı KAPATIR. Dönen değer: toplam süre (yazma + kapanış)."""
    t = time.perf_counter()
    with open(path, mode, buffering=0) as f:
        f.seek(offset)
        left = nbytes
        while left > 0:
            n = min(left, MB)
            f.write(CHUNK[:n])
            left -= n
    return time.perf_counter() - t


def parallel_write(jobs) -> float:
    t = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        for fut in [pool.submit(*job) for job in jobs]:
            fut.result()
    return time.perf_counter() - t


def design_parts(work: Path, size: int, n: int) -> tuple:
    per = size // n
    prepare = 0.0
    write = parallel_write([(write_region, work / f"p{i}.bin", "wb", 0, per) for i in range(n)])
    t = time.perf_counter()
    out_path = work / "birlesmis.tmp"
    with open(out_path, "wb") as out:
        for i in range(n):
            with open(work / f"p{i}.bin", "rb") as src:
                shutil.copyfileobj(src, out, MB)
            (work / f"p{i}.bin").unlink()
    os.replace(out_path, work / "final.bin")
    return prepare, write, time.perf_counter() - t


def design_single(work: Path, size: int, n: int, presize: bool) -> tuple:
    data = work / "data.tmp"
    t = time.perf_counter()
    with open(data, "wb") as f:
        set_sparse(f)
        if presize:
            f.truncate(size)
    prepare = time.perf_counter() - t

    per = size // n
    write = parallel_write([(write_region, data, "r+b", i * per, per) for i in range(n)])

    t = time.perf_counter()
    with open(data, "r+b") as f:
        os.fsync(f.fileno())
    os.replace(data, work / "final.bin")
    return prepare, write, time.perf_counter() - t


def main() -> None:
    gigabytes = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
    parts = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    base = Path(sys.argv[3]) if len(sys.argv) > 3 else paths.default_download_dir()
    size = int(gigabytes * 1024 ** 3) // (parts * MB) * (parts * MB)
    base.mkdir(parents=True, exist_ok=True)

    print(f"Klasör: {base}   Dosya sistemi: {fs_name(base)}")
    print(f"Boyut: {size / 1024 ** 3:.2f} GB   Parça: {parts}\n")
    print(f"{'Düzen':<44}{'Hazırlık':>10}{'Yazma':>10}{'Bitiş':>10}{'TOPLAM':>10}")

    cases = (
        ("S  tek dosya, baştan boyutlu (şimdiki)", lambda w: design_single(w, size, parts, True)),
        ("F  tek dosya, boyutsuz, seyrek", lambda w: design_single(w, size, parts, False)),
        ("P  parça dosyaları + birleştirme (eski)", lambda w: design_parts(w, size, parts)),
    )
    for title, fn in cases:
        work = base / "_bench_design"
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        try:
            prepare, write, finish = fn(work)
            print(f"{title:<44}{prepare:>9.1f}s{write:>9.1f}s{finish:>9.1f}s{prepare + write + finish:>9.1f}s")
        finally:
            shutil.rmtree(work, ignore_errors=True)

    print("\nHazırlık: indirme başlamadan önce beklenen süre.  Bitiş: son bayttan 'Tamamlandı'ya kadar.")
    print("Bu çıktıyı olduğu gibi bana gönder.")


if __name__ == "__main__":
    main()