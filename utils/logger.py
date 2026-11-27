"""
Merkezi log kurulumu. Uygulama başında bir kez setup_logging() çağrılır,
diğer modüller get_logger(__name__) ile kendi logger'larını alır.
"""
import logging
import sys
from logging.handlers import RotatingFileHandler

from utils import paths

_ROOT_LOGGER_NAME = "ida"
_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    """Log sistemini kurar. Birden fazla kez çağrılsa da yalnızca bir kez çalışır."""
    global _configured
    if _configured:
        return

    paths.ensure_dirs()

    logger = logging.getLogger(_ROOT_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        paths.LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    if sys.stderr is not None:        # konsolsuz (paketli) çalışmada stderr yoktur
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(fmt)
        logger.addHandler(console_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Modüle özel logger döndürür. Kullanım: log = get_logger(__name__)"""
    return logging.getLogger(f"{_ROOT_LOGGER_NAME}.{name}")


def install_excepthook() -> None:
    """Yakalanmamış tüm hataları log dosyasına yazar."""
    log = get_logger("uncaught")

    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        log.critical("Yakalanmamış hata", exc_info=(exc_type, exc_value, exc_tb))
        try:
            import traceback
            (paths.LOG_DIR / "crash.txt").write_text(
                "".join(traceback.format_exception(exc_type, exc_value, exc_tb)), encoding="utf-8")
        except Exception:
            pass

    sys.excepthook = _hook