"""
Çok parçalı indirmede her parça dosyanın FARKLI yerine yazar. Bu betik, böyle bir yazmanın
diskinde ne kadar sürdüğünü ve "seyrek dosya" işaretinin gerçekten tutup tutmadığını ölçer.
Her aşama ayrı ölçülür, böylece yavaşlığın nereden geldiği görülür.

Kullanım (proje kökünde):
    python tools/bench_prealloc.py                  # varsayılan: 2 GB, indirme klasöründe
    python tools/bench_prealloc.py 4 D:\\Indirilenler    # 4 GB, başka sürücüde
"""
import os
import stat
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils import paths
from utils.helpers import set_sparse

MB = 1024 * 1024


def filesystem_name(path: Path) -> str:
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


def run_case(directory: Path, size: int, sparse: bool) -> dict:
    path = directory / f"_bench_{'sparse' if sparse else 'plain'}.tmp"
    result = {}
    try:
        t = time.perf_counter()
        with open(path, "wb") as f:
            flagged = set_sparse(f) if sparse else None
            f.truncate(size)
        result["olustur"] = time.perf_counter() - t
        result["flag_istendi"] = flagged
        attrs = getattr(os.stat(path), "st_file_attributes", 0)
        result["gercekten_seyrek"] = bool(attrs & getattr(stat, "FILE_ATTRIBUTE_SPARSE_FILE", 0x200))

        t = time.perf_counter()
        f = open(path, "r+b", buffering=0)
        result["ac"] = time.perf_counter() - t

        t = time.perf_counter()
        f.seek(size - MB)
        f.write(b"x" * MB)
        result["uzak_yaz"] = time.perf_counter() - t

        t = time.perf_counter()
        os.fsync(f.fileno())
        result["fsync"] = time.perf_counter() - t

        t = time.perf_counter()
        f.seek(0)
        f.write(b"x" * MB)
        result["bas_yaz"] = time.perf_counter() - t
        f.close()
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    return result


def main() -> None:
    gigabytes = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0
    directory = Path(sys.argv[2]) if len(sys.argv) > 2 else paths.default_download_dir()
    directory.mkdir(parents=True, exist_ok=True)
    size = int(gigabytes * 1024 ** 3)

    print(f"Klasör        : {directory}")
    print(f"Dosya sistemi : {filesystem_name(directory)}")
    print(f"Dosya boyutu  : {gigabytes:g} GB\n")

    for sparse in (False, True):
        r = run_case(directory, size, sparse)
        title = "SEYREK İŞARETLİ (uygulamanın kullandığı)" if sparse else "Seyrek işaretsiz"
        print(f"--- {title}")
        if sparse:
            print(f"  işaret komutu başarılı mı : {r['flag_istendi']}")
        print(f"  dosya gerçekten seyrek mi : {r['gercekten_seyrek']}")
        print(f"  oluştur + boyutlandır     : {r['olustur']:6.2f} sn")
        print(f"  dosyayı yazmak için aç    : {r['ac']:6.2f} sn")
        print(f"  UZAK konuma 1 MB yaz      : {r['uzak_yaz']:6.2f} sn")
        print(f"  fsync                     : {r['fsync']:6.2f} sn")
        print(f"  BAŞA 1 MB yaz (karşılaştırma): {r['bas_yaz']:6.2f} sn\n")

    print("Bu çıktıyı olduğu gibi bana gönder.")


if __name__ == "__main__":
    main()