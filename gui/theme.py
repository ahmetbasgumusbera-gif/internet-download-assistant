"""
Uygulamanın görsel teması: renk paleti ve Qt stil sayfası (QSS).
Renkleri değiştirmek için yalnızca bu dosyadaki sabitlere dokunmak yeterlidir.
"""
from __future__ import annotations

import os

# ---------------------------------------------------------------- palet
BG = "#0c0d16"            # pencere zemini
SURFACE = "#141626"       # kartlar, giriş alanları
SURFACE_2 = "#1b1e33"     # üzerine gelince / ikincil yüzey
BORDER = "#252944"
BORDER_HOVER = "#363c63"

TEXT = "#e8eaf6"
TEXT_MUTED = "#8c92b8"
TEXT_DIM = "#5d6289"

PURPLE = "#a78bfa"        # ana vurgu
PURPLE_SOFT = "#c4b5fd"
CYAN = "#22d3ee"          # ikincil neon vurgu
GREEN = "#4ade80"
AMBER = "#fbbf24"
RED = "#ff5d7a"

FONT_FAMILY = "'Segoe UI Variable Text', 'Segoe UI', 'Inter', 'Roboto', sans-serif"

# Durum adı -> (rozet metni, renk)
STATUS_STYLE = {
    "queued":      ("Sırada",          TEXT_MUTED),
    "probing":     ("Bağlanıyor",      CYAN),
    "downloading": ("İniyor",          CYAN),
    "paused":      ("Duraklatıldı",    AMBER),
    "merging":     ("İşleniyor",       PURPLE),
    "completed":   ("Tamamlandı",      GREEN),
    "error":       ("Hata",            RED),
    "cancelled":   ("İptal edildi",    TEXT_DIM),
}

# Dosya kategorisi -> vurgu rengi (kart üzerindeki uzantı rozeti)
CATEGORY_COLORS = {
    "Videolar":   PURPLE,
    "Müzik":      "#f472b6",
    "Resimler":   "#34d399",
    "Belgeler":   "#60a5fa",
    "Arşivler":   AMBER,
    "Programlar": "#fb7185",
    "E-kitaplar": "#a3e635",
    "Tasarım":    "#c084fc",
    "Kod":        "#38bdf8",
    "Diğer":      "#94a3b8",
}


def _base_stylesheet() -> str:
    return f"""
    * {{
        font-family: {FONT_FAMILY};
        color: {TEXT};
        font-size: 13px;
    }}
    QMainWindow, QDialog {{ background-color: {BG}; }}
    QWidget#central {{ background-color: {BG}; }}

    /* ---------- başlık ---------- */
    QLabel#appTitle {{ font-size: 20px; font-weight: 700; color: {TEXT}; }}
    QLabel#appSubtitle {{ font-size: 12px; color: {TEXT_MUTED}; }}

    /* ---------- bağlantı girişi ---------- */
    QLineEdit {{
        background-color: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 12px;
        padding: 11px 16px;
        color: {TEXT};
        selection-background-color: {PURPLE};
        selection-color: {BG};
    }}
    QLineEdit:hover {{ border-color: {BORDER_HOVER}; }}
    QLineEdit:focus {{ border: 1px solid {CYAN}; }}
    QLineEdit:disabled {{ color: {TEXT_DIM}; }}
    QLineEdit#linkField {{ padding: 8px 14px; border-radius: 10px; }}

    /* ---------- düğmeler ---------- */
    QPushButton#primary {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {PURPLE}, stop:1 {CYAN});
        color: {BG};
        border: none;
        border-radius: 12px;
        padding: 0 22px;
        font-weight: 700;
        min-height: 42px;
    }}
    QPushButton#primary:hover {{
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {PURPLE_SOFT}, stop:1 #67e8f9);
    }}
    QPushButton#primary:pressed {{ background: {PURPLE}; }}
    QPushButton#primary:disabled {{ background: {SURFACE_2}; color: {TEXT_DIM}; }}

    QPushButton#ghost {{
        background: transparent;
        border: 1px solid {BORDER};
        border-radius: 10px;
        padding: 0 14px;
        min-height: 34px;
        color: {TEXT_MUTED};
    }}
    QPushButton#ghost:hover {{ background: {SURFACE_2}; border-color: {BORDER_HOVER}; color: {TEXT}; }}
    QPushButton#ghost:pressed {{ background: {SURFACE}; }}
    QPushButton#ghost:disabled {{ color: {TEXT_DIM}; border-color: {SURFACE_2}; }}

    QPushButton#chip {{
        background: transparent;
        border: 1px solid {BORDER};
        border-radius: 16px;
        padding: 0 16px;
        min-height: 30px;
        color: {TEXT_MUTED};
    }}
    QPushButton#chip:hover {{ border-color: {BORDER_HOVER}; color: {TEXT}; }}
    QPushButton#chip:checked {{
        background: rgba(167, 139, 250, 38);
        border: 1px solid rgba(167, 139, 250, 150);
        color: {PURPLE_SOFT};
    }}

    QToolButton#iconBtn {{
        background: transparent;
        border: none;
        border-radius: 9px;
        padding: 0;
    }}
    QToolButton#iconBtn:hover {{ background: {SURFACE_2}; }}
    QToolButton#iconBtn:pressed {{ background: {BORDER}; }}
    QToolButton#iconBtn[role="danger"]:hover {{ background: rgba(255, 93, 122, 45); }}
    QToolButton#iconBtn[role="accent"]:hover {{ background: rgba(34, 211, 238, 40); }}

        /* ---------- sekmeler ---------- */
    QPushButton#tab {{
        background: transparent; border: none; border-bottom: 2px solid transparent; border-radius: 0;
        padding: 10px 20px; color: {TEXT_MUTED}; font-size: 14px;
    }}
    QPushButton#tab:hover {{ color: {TEXT}; }}
    QPushButton#tab:checked {{ color: {TEXT}; border-bottom: 2px solid {PURPLE}; }}
    QFrame#tabBar {{ border-bottom: 1px solid {BORDER}; }}

    /* ---------- YouTube sayfası: segmentli düğme, karo ---------- */
    QPushButton#seg {{
        background: {SURFACE}; border: 1px solid {BORDER}; padding: 0 18px; min-height: 36px; color: {TEXT_MUTED};
    }}
    QPushButton#seg:hover {{ color: {TEXT}; }}
    QPushButton#seg:checked {{
        background: rgba(167, 139, 250, 45); border-color: rgba(167, 139, 250, 170); color: {PURPLE_SOFT};
    }}
    QPushButton#seg:disabled {{ color: {TEXT_DIM}; background: {BG}; }}
    QPushButton#tile {{
        background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; min-height: 52px; text-align: left;
    }}
    QPushButton#tile:hover {{ border-color: {BORDER_HOVER}; }}
    QPushButton#tile:checked {{ background: rgba(34, 211, 238, 22); border: 1px solid rgba(34, 211, 238, 170); }}
    QLabel#tileTitle {{ font-size: 15px; font-weight: 700; background: transparent; }}
    QLabel#tileSub {{ font-size: 11px; color: {TEXT_MUTED}; background: transparent; }}
    QLabel#tileBadge {{ font-size: 10px; font-weight: 800; border-radius: 6px; padding: 1px 6px; }}
    QLabel#sectionLabel {{ font-size: 12px; font-weight: 700; color: {TEXT_MUTED}; }}
    QLabel#videoTitle {{ font-size: 16px; font-weight: 700; }}
    QLabel#videoMeta {{ font-size: 12px; color: {TEXT_MUTED}; }}
    QLabel#banner {{
        background: rgba(167, 139, 250, 30); border: 1px solid rgba(167, 139, 250, 110);
        border-radius: 12px; padding: 10px 14px; color: {PURPLE_SOFT};
    }}
    QLabel#bannerWarn {{
        background: rgba(251, 191, 36, 26); border: 1px solid rgba(251, 191, 36, 110);
        border-radius: 12px; padding: 10px 14px; color: {AMBER};
    }}
    QLabel#bannerOk {{
        background: rgba(74, 222, 128, 26); border: 1px solid rgba(74, 222, 128, 110);
        border-radius: 12px; padding: 10px 14px; color: {GREEN};
    }}
    QFrame#summaryBar {{ background-color: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
    QLabel#summaryText {{ font-size: 14px; font-weight: 700; }}

    /* ---------- kartlar ---------- */
    QFrame#card {{
        background-color: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 16px;
    }}
    QFrame#card:hover {{ border-color: {BORDER_HOVER}; }}
    QFrame#card[state="downloading"] {{ border: 1px solid rgba(34, 211, 238, 95); }}
    QFrame#card[state="probing"] {{ border: 1px solid rgba(34, 211, 238, 60); }}
    QFrame#card[state="merging"] {{ border: 1px solid rgba(167, 139, 250, 95); }}
    QFrame#card[state="error"] {{ border: 1px solid rgba(255, 93, 122, 100); }}

    QLabel#fileName {{ font-size: 14px; font-weight: 600; color: {TEXT}; }}
    QLabel#info {{ font-size: 12px; color: {TEXT_MUTED}; }}
    QLabel#info[state="error"] {{ color: {RED}; }}
    QLabel#info[state="warn"] {{ color: {AMBER}; }}
    QLabel#percent {{ font-size: 12px; font-weight: 700; color: {TEXT}; }}

    QLabel#badge {{
        border-radius: 9px;
        padding: 3px 10px;
        font-size: 11px;
        font-weight: 600;
    }}

    /* ---------- ilerleme çubuğu ---------- */
    QProgressBar {{
        background-color: #0a0b13;
        border: none;
        border-radius: 4px;
        min-height: 8px;
        max-height: 8px;
        text-align: center;
    }}
    QProgressBar::chunk {{
        border-radius: 4px;
        background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {PURPLE}, stop:1 {CYAN});
    }}
    QProgressBar[state="completed"]::chunk {{ background: {GREEN}; }}
    QProgressBar[state="paused"]::chunk {{ background: {AMBER}; }}
    QProgressBar[state="error"]::chunk {{ background: {RED}; }}
    QProgressBar[state="cancelled"]::chunk {{ background: {TEXT_DIM}; }}
    QProgressBar[state="queued"]::chunk {{ background: {TEXT_DIM}; }}

    /* ---------- liste ve kaydırma çubuğu ---------- */
    QScrollArea {{ background: transparent; border: none; }}
    QWidget#listHost {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 40px; }}
    QScrollBar::handle:vertical:hover {{ background: {BORDER_HOVER}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

    QLabel#emptyTitle {{ font-size: 16px; font-weight: 600; color: {TEXT_MUTED}; }}
    QLabel#emptyHint {{ font-size: 12px; color: {TEXT_DIM}; }}

    /* ---------- alt durum çubuğu ---------- */
    QFrame#statusBar {{
        background-color: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 12px;
    }}
    QLabel#statusText {{ font-size: 12px; color: {TEXT_MUTED}; }}
    QLabel#statusSpeed {{ font-size: 13px; font-weight: 700; color: {CYAN}; }}
    QLabel#message {{ font-size: 12px; color: {AMBER}; }}

    /* ---------- ayarlar penceresi ---------- */
    QLabel#sectionTitle {{ font-size: 12px; font-weight: 700; color: {TEXT_MUTED}; }}
    QLabel#hint {{ font-size: 11px; color: {TEXT_DIM}; }}
    QSpinBox {{
        background-color: {SURFACE};
        border: 1px solid {BORDER};
        border-radius: 10px;
        padding: 8px 12px;
        min-width: 130px;
    }}
    QSpinBox:hover {{ border-color: {BORDER_HOVER}; }}
    QSpinBox:focus {{ border-color: {CYAN}; }}
    QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}

    /* ---------- iletişim kutuları ---------- */
    QMessageBox {{ background-color: {SURFACE}; }}
    QMessageBox QLabel {{ color: {TEXT}; font-size: 13px; }}
    QMessageBox QPushButton, QDialogButtonBox QPushButton {{
        background: transparent;
        border: 1px solid {BORDER};
        border-radius: 10px;
        padding: 8px 18px;
        min-width: 80px;
        color: {TEXT};
    }}
    QMessageBox QPushButton:hover, QDialogButtonBox QPushButton:hover {{
        background: {SURFACE_2};
        border-color: {PURPLE};
    }}
    QMenu {{ background-color: {SURFACE}; border: 1px solid {BORDER_HOVER}; padding: 6px; }}
    QMenu::item {{ padding: 8px 22px; border-radius: 6px; color: {TEXT}; }}
    QMenu::item:selected {{ background-color: {SURFACE_2}; }}
    QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}
    QCheckBox {{ spacing: 8px; color: {TEXT_MUTED}; }}
    QCheckBox::indicator {{ width: 18px; height: 18px; border: 1px solid {BORDER_HOVER}; border-radius: 5px; background: {SURFACE}; }}
    QCheckBox::indicator:hover {{ border-color: {CYAN}; }}
    QCheckBox::indicator:checked {{ background: {CYAN}; border-color: {CYAN}; }}
    QToolButton#moreBtn {{ background: transparent; border: none; border-radius: 9px; font-size: 20px; font-weight: 700; color: {TEXT_MUTED}; }}
    QToolButton#moreBtn:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
    QTabWidget::pane {{ border: none; }}
    QTabBar::tab {{ background: transparent; border: none; border-bottom: 2px solid transparent;
        padding: 8px 16px; color: {TEXT_MUTED}; }}
    QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {PURPLE}; }}
    QTabBar::tab:hover {{ color: {TEXT}; }}
    QPushButton#ghost::menu-indicator {{ image: none; width: 0; }}
    QToolTip {{
        background-color: {SURFACE_2};
        color: {TEXT};
        border: 1px solid {BORDER_HOVER};
        padding: 5px 8px;
    }}
    """


IS_DARK = True
PROGRESS_BG = "#0a0b13"

_PALETTES = {
    "dark": dict(BG="#0c0d16", SURFACE="#141626", SURFACE_2="#1b1e33", BORDER="#252944", BORDER_HOVER="#363c63",
                 TEXT="#e8eaf6", TEXT_MUTED="#8c92b8", TEXT_DIM="#5d6289", GREEN="#4ade80", AMBER="#fbbf24",
                 RED="#ff5d7a", PROGRESS_BG="#0a0b13"),
    "light": dict(BG="#f4f5fb", SURFACE="#ffffff", SURFACE_2="#eceefa", BORDER="#d9dcee", BORDER_HOVER="#b7bcd8",
                  TEXT="#1c1f3a", TEXT_MUTED="#5a6088", TEXT_DIM="#8d93b5", GREEN="#16a34a", AMBER="#b45309",
                  RED="#dc2626", PROGRESS_BG="#e3e6f4"),
}
# vurgu: (ana, yumuşak, ikincil)
_ACCENTS = {
    "purple": {"dark": ("#a78bfa", "#c4b5fd", "#22d3ee"), "light": ("#7c5cf0", "#5b3fd1", "#0e8fb0")},
    "blue": {"dark": ("#60a5fa", "#93c5fd", "#2dd4bf"), "light": ("#2563eb", "#1d4ed8", "#0d9488")},
    "pink": {"dark": ("#f472b6", "#f9a8d4", "#fb923c"), "light": ("#db2777", "#be185d", "#ea580c")},
    "green": {"dark": ("#4ade80", "#86efac", "#22d3ee"), "light": ("#16a34a", "#15803d", "#0e8fb0")},
    "orange": {"dark": ("#fb923c", "#fdba74", "#f472b6"), "light": ("#ea580c", "#c2410c", "#db2777")},
}


def _rgb(color: str) -> str:
    h = color.lstrip("#")
    return f"{int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)}"


def apply(mode: str = "dark", accent: str = "purple") -> None:
    """Paleti ve vurgu rengini değiştirir. Arayüz modülleri içe aktarılmadan ÖNCE çağrılmalı (main.py yapar)."""
    global BG, SURFACE, SURFACE_2, BORDER, BORDER_HOVER, TEXT, TEXT_MUTED, TEXT_DIM
    global GREEN, AMBER, RED, PURPLE, PURPLE_SOFT, CYAN, IS_DARK, PROGRESS_BG
    mode = mode if mode in _PALETTES else "dark"
    accent = accent if accent in _ACCENTS else "purple"
    p = _PALETTES[mode]
    BG, SURFACE, SURFACE_2, BORDER, BORDER_HOVER = p["BG"], p["SURFACE"], p["SURFACE_2"], p["BORDER"], p["BORDER_HOVER"]
    TEXT, TEXT_MUTED, TEXT_DIM = p["TEXT"], p["TEXT_MUTED"], p["TEXT_DIM"]
    GREEN, AMBER, RED, PROGRESS_BG = p["GREEN"], p["AMBER"], p["RED"], p["PROGRESS_BG"]
    PURPLE, PURPLE_SOFT, CYAN = _ACCENTS[accent][mode]
    IS_DARK = mode == "dark"
    colors = {"queued": TEXT_MUTED, "probing": CYAN, "downloading": CYAN, "paused": AMBER, "merging": PURPLE,
              "completed": GREEN, "error": RED, "cancelled": TEXT_DIM}
    for key, color in colors.items():
        STATUS_STYLE[key] = (STATUS_STYLE[key][0], color)
    for key, color in (("Videolar", PURPLE), ("Arşivler", AMBER)):
        CATEGORY_COLORS[key] = color


def combo_qss(min_width: int = 140) -> str:
    return (f"QComboBox {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; "
            f"padding: 8px 12px; min-width: {min_width}px; color: {TEXT}; }}"
            f"QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid {BORDER}; "
            f"selection-background-color: {SURFACE_2}; color: {TEXT}; outline: none; }}")


def build_stylesheet() -> str:
    """Stil sayfası: sabit yazılmış vurgu renkleri geçerli paletle değiştirilir (varsayılan koyu/mor aynen kalır)."""
    css = _base_stylesheet()
    for old, new in (("167, 139, 250", _rgb(PURPLE)), ("34, 211, 238", _rgb(CYAN)), ("255, 93, 122", _rgb(RED)),
                     ("251, 191, 36", _rgb(AMBER)), ("74, 222, 128", _rgb(GREEN)), ("#0a0b13", PROGRESS_BG)):
        css = css.replace(old, new)
    return css


def enable_dark_titlebar(window) -> None:
    if not IS_DARK:
        return
    _enable_dark_titlebar(window)


def _enable_dark_titlebar(window) -> None:
    """Windows 10/11'de başlık çubuğunu koyu yapar. Diğer sistemlerde veya hata durumunda sessizce geçer."""
    if os.name != "nt":
        return
    try:
        import ctypes
        hwnd = int(window.winId())
        value = ctypes.c_int(1)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (yeni / eski sürüm numarası)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
            ) == 0:
                break
    except Exception:
        pass