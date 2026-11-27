"""
Uygulamanın giriş noktası. Tüm parçaları (ayarlar, veritabanı, yönetici, arayüz) birbirine bağlar.
Çalıştırma:  python main.py   (ya da run.py)
"""
import os
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core import backup, i18n, netconf, tools, ytauth
from core.manager import DownloadManager
from utils.config_manager import ConfigManager as _EarlyConfig
from gui import theme as _early_theme
_early_cfg = _EarlyConfig()
_early_theme.apply(str(_early_cfg.get("theme") or "dark"), str(_early_cfg.get("accent") or "purple"))
from gui import i18n_qt, theme
from gui.bridge import ManagerBridge
from gui.icons import app_icon
from gui.main_window import MainWindow
from gui.single_instance import SingleInstance
from utils import autostart, paths
from utils.config_manager import ConfigManager
from utils.logger import get_logger, install_excepthook, setup_logging

APP_NAME = "İnternet Download Assistant"


_MUTEX_HANDLE = None


def _hold_app_mutex() -> None:
    """Kurulum paketi (Inno Setup) çalışan uygulamayı algılayıp kapatmayı önerebilsin diye adlandırılmış bir mutex tutar."""
    global _MUTEX_HANDLE
    if os.name != "nt":
        return
    try:
        import ctypes
        _MUTEX_HANDLE = ctypes.windll.kernel32.CreateMutexW(None, False, "IDA.InternetDownloadAssistant.Mutex")
    except Exception:
        pass


def _set_windows_app_id() -> None:
    """Windows görev çubuğunda Python yerine kendi simgemizin görünmesi için."""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("IDA.InternetDownloadAssistant")
    except Exception:
        pass


def main() -> int:
    paths.ensure_dirs()
    backup.apply_pending()
    setup_logging()
    install_excepthook()
    log = get_logger("main")
    log.info("Uygulama başlatılıyor")
    try:
        tools.seed_tools()    # kurulumla gelen araçları (yt-dlp, ffmpeg, Deno) güncellenebilir klasöre kopyala
    except Exception:
        log.exception("Araçlar hazırlanamadı")

    _hold_app_mutex()
    _set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(app_icon())
    app.setStyleSheet(theme.build_stylesheet())

    instance = SingleInstance()
    # başka bir kopya zaten açık: ona haber verip çık (ida:// ile geldiysek "sunucuyu aç", değilse "öne gel")
    if instance.notify_running(b"server" if "--server" in sys.argv else b"show"):
        log.info("Uygulama zaten çalışıyor; mevcut kopyaya haber verildi")
        return 0

    config = ConfigManager()
    i18n_qt.apply_language(i18n.resolve(str(config.get("language") or "auto")), "tr")   # arayüz dili
    autostart.register_protocol()   # eklenti kapalı uygulamayı ida:// ile açabilsin (her açılışta güncellenir)
    autostart.sync(bool(config.get("start_with_windows")))   # "Windows ile başlat" açıksa kayıt defterinde de olsun
    ytauth.configure(bool(config.get("youtube_use_cookies")), str(config.get("youtube_cookies_browser") or ""))
    netconf.configure(str(config.get("proxy_url") or ""))
    manager = DownloadManager(config)
    bridge = ManagerBridge(manager)

    window = MainWindow(manager, config, bridge, tray=True, auto_updates=True)
    instance.listen()
    instance.show_requested.connect(window.show_and_raise)
    instance.server_requested.connect(window.start_server)   # eklenti ida:// ile çağırdı, uygulama zaten açıktı
    if not ("--background" in sys.argv and window.has_tray):   # Windows ile başlayınca tepside sessizce açılır
        window.show()
    theme.enable_dark_titlebar(window)
    window.sync_mini_box()
    QTimer.singleShot(3500, window.offer_crash_report)
    manager.start_pending()   # kapanırken çalışan indirmeler (ayar açıksa) arayüz bağlandıktan sonra sürer
    if config.get("server_autostart") or "--server" in sys.argv:   # eklenti çağırdıysa sunucu da açılır
        window.start_server()

    code = app.exec()
    manager.shutdown()   # closeEvent zaten çağırdı; ikinci çağrı zararsızdır
    log.info("Uygulama kapandı")
    return code


if __name__ == "__main__":
    sys.exit(main())