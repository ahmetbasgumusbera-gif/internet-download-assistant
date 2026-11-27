"""
JSON tabanlı ayar yöneticisi.
Yeni ayar eklemek için sadece DEFAULTS sözlüğüne satır eklemek yeterlidir.
"""
import json
import os
import threading
from pathlib import Path
from typing import Any

from utils import paths
from utils.logger import get_logger

log = get_logger(__name__)

DEFAULTS: dict[str, Any] = {
    "download_dir": "",               # boş = varsayılan klasör (Downloads/IDA)
    "max_concurrent_downloads": 3,    # aynı anda kaç indirme
    "connections_per_download": 8,    # her indirmede kaç parça bağlantısı
    "speed_limit_kbps": 0,            # 0 = sınırsız
    "use_browser_cookies": False,     # tarayıcı çerezlerini kullan (varsayılan kapalı)
    "warn_dangerous_files": True,     # .exe/.apk vb. için uyarı
    "sort_into_categories": False,    # uzantıya göre alt klasör
    "download_mode": "parallel",      # parallel = aynı anda, sequential = sırayla
    "youtube_auto_download": False,   # YouTube linki analiz edilince varsayılan ayarla hemen indir
    "auto_resume_on_start": False,    # kapanırken çalışan/sıradaki indirmeler açılışta kendiliğinden sürsün
    "clipboard_watch": False,         # panoya kopyalanan indirme bağlantısını öner
    "server_port": 17653,             # tarayıcı eklentisinin konuştuğu yerel sunucunun portu
    "server_autostart": False,        # uygulama açılınca yerel sunucuyu da aç
    "start_with_windows": False,      # bilgisayar açılınca uygulamayı arka planda (tepside) başlat
    "auto_update_tools": True,        # yt-dlp (günde bir) ve Deno (ayda bir) arka planda kendiliğinden güncellensin
    "last_ytdlp_check": "",           # son yt-dlp güncelleme denemesi (ISO tarih; dahili)
    "last_deno_check": "",            # son Deno güncelleme denemesi (ISO tarih; dahili)
    "youtube_use_cookies": True,      # tarayıcı eklentisinin gönderdiği YouTube oturum çerezlerini yt-dlp ile kullan
    "youtube_cookies_browser": "",    # doluysa (chrome/edge/firefox/brave) --cookies-from-browser kullanılır
    "notify_on_complete": True,       # indirme bitince üst şerit + tepsi bildirimi
    "notify_raise_window": False,     # tüm indirmeler bitince pencereyi öne getir
    "auto_retry": True,               # geçici hatalarda 5 / 15 / 60 dk sonra otomatik yeniden dene
    "schedule_enabled": False,        # zamanlayıcı: kuyruk belirlenen saate kadar bekler
    "schedule_time": "02:00",         # zamanlayıcı saati (SS:DD)
    "schedule_at": "",                # sıradaki başlama zamanı (ISO; dahili)
    "after_all_action": "none",       # tüm indirmeler bitince: none | sleep | shutdown
    "folder_rules": "",               # klasör ve ad kuralları (metin; Ayarlar > Klasör ve ad kuralları)
    "list_mode": "parallel",          # Listeler sekmesi: parallel = hepsi aynı anda, sequential = sırayla
    "list_schedule_at": "",           # zamanlı liste indirmesi (ISO; dahili)
    "list_schedule_mode": "parallel",
    "auto_extract": False,            # biten arşivleri (zip, rar, 7z, tar...) kendi adlı klasöre çıkar
    "show_speed_graph": True,         # durum çubuğunda canlı hız grafiği
    "language": "auto",               # arayüz dili: auto (sistem dili) | tr | en
    "proxy_url": "",                  # proxy (boş = sistem ayarı): http://host:port ya da socks5://host:port
    "auto_convert": "",               # indirince ffmpeg dönüştürme (YouTube dışı videolar): "" | mp4 | mkv | mp3 | wav | flac | m4a
    "convert_keep_original": True,    # dönüştürünce orijinal dosyayı da koru
    "auto_verify_hash": True,         # sunucuda .sha256 / .sha1 / .md5 yan dosyası varsa indirmeyi doğrula
    "floating_box": False,            # sürükle-bırak yüzen mini kutu
    "mini_box_pos": "",               # mini kutu konumu "x,y" (dahili)
    "theme": "dark",                  # dark | light (yeniden başlatınca uygulanır)
    "accent": "purple",               # vurgu rengi: purple | blue | pink | green | orange
    "diag_dir": "",                   # tanı raporu klasörü (boş = kurulum klasörü\tani, yazılamazsa kullanıcı verisi\tani)
    "auto_update_app": True,          # yeni sürüm yayınlanınca (GitHub) haber ver
    "update_skipped_version": "",     # "İptal" denen sürüm: bir daha sorulmaz (dahili)
    "clean_names": False,             # dosya adındaki [site.com] gibi etiketleri temizle
    "auto_tune_connections": True,     # siteye göre bağlantı sayısını ölçülen hıza göre ayarla
    "youtube_prefetch": True,         # YouTube videosu açılınca İndir'e basmadan önce arka planda analiz et
}

# Sınırlı seçenek alan metin ayarları
CHOICES: dict[str, tuple[str, ...]] = {
    "download_mode": ("parallel", "sequential"),
    "language": ("auto", "tr", "en"),
    "auto_convert": ("", "mp4", "mkv", "mp3", "wav", "flac", "m4a"),
    "theme": ("dark", "light"),
    "accent": ("purple", "blue", "pink", "green", "orange"),
    "list_mode": ("parallel", "sequential"),
    "list_schedule_mode": ("parallel", "sequential"),
    "after_all_action": ("none", "sleep", "shutdown", "restart"),
}

# Her ayar için izin verilen aralık (sayısal olanlar)
LIMITS: dict[str, tuple[int, int]] = {
    "max_concurrent_downloads": (1, 10),
    "connections_per_download": (1, 32),
    "speed_limit_kbps": (0, 10_000_000),
    "server_port": (1024, 65535),
}


class ConfigManager:
    def __init__(self, config_file: Path = paths.CONFIG_FILE):
        self._file = Path(config_file)
        self._lock = threading.RLock()
        self._data: dict[str, Any] = dict(DEFAULTS)
        self.load()

    # ---------- okuma / yazma ----------
    def load(self) -> None:
        with self._lock:
            self._data = dict(DEFAULTS)
            if not self._file.exists():
                return
            try:
                raw = json.loads(self._file.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("Ayar dosyası bir sözlük değil")
                for key, value in raw.items():
                    if key in DEFAULTS:
                        self._data[key] = self._validate(key, value)
            except (OSError, ValueError) as e:
                log.error("Ayar dosyası okunamadı (%s). Varsayılanlara dönülüyor.", e)
                self._backup_corrupt_file()
                self._data = dict(DEFAULTS)

    def save(self) -> None:
        with self._lock:
            paths.ensure_dirs()
            tmp = self._file.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self._data, indent=4, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(tmp, self._file)  # atomik yer değiştirme

    # ---------- erişim ----------
    def get(self, key: str) -> Any:
        with self._lock:
            return self._data[key]

    def set(self, key: str, value: Any, autosave: bool = True) -> None:
        if key not in DEFAULTS:
            raise KeyError(f"Bilinmeyen ayar: {key}")
        with self._lock:
            self._data[key] = self._validate(key, value)
            if autosave:
                self.save()

    def reset(self) -> None:
        with self._lock:
            self._data = dict(DEFAULTS)
            self.save()

    # ---------- kolaylık ----------
    def download_dir(self) -> Path:
        """Etkin indirme klasörü (ayar boşsa varsayılan)."""
        value = self.get("download_dir")
        return Path(value) if value else paths.default_download_dir()

    # ---------- iç yardımcılar ----------
    def _validate(self, key: str, value: Any) -> Any:
        """Değeri varsayılanın türüne çevirir, geçersizse varsayılana döner."""
        default = DEFAULTS[key]
        try:
            if isinstance(default, bool):
                if isinstance(value, str):
                    return value.strip().lower() in ("1", "true", "evet", "yes")
                return bool(value)
            if isinstance(default, int):
                number = int(value)
                low, high = LIMITS.get(key, (number, number))
                return max(low, min(high, number))
            if isinstance(default, str):
                text = str(value)
                if key in CHOICES and text not in CHOICES[key]:
                    return default
                return text
        except (TypeError, ValueError):
            log.warning("Geçersiz ayar değeri: %s=%r, varsayılan kullanılıyor", key, value)
        return default

    def _backup_corrupt_file(self) -> None:
        try:
            backup = self._file.with_suffix(".bozuk.json")
            os.replace(self._file, backup)
            log.info("Bozuk ayar dosyası yedeklendi: %s", backup)
        except OSError:
            pass