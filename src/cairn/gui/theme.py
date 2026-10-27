"""Palette et feuille de style de l'application.

Une seule source de vérité pour les couleurs, reprise du site du projet
(thème Almanac) : papier gris-bleu, feuilles claires bordées d'un filet
épais, encre presque noire, un seul accent magenta, angles droits. Deux
rendus du même système : clair, et sombre (re-dérivé, jamais inversé).

Le mode (« auto », « clair » ou « sombre ») est lu dans les réglages au
chargement de ce module : plusieurs pages copient des couleurs à l'import,
un changement de mode vaut donc au prochain démarrage. En « auto », Cairn
suit le réglage clair ou sombre du système.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtGui import QColor

FONT_DIR = Path(__file__).resolve().parent / "fonts"
DISPLAY, BODY, MONO = "Hanken Grotesk", "Newsreader", "IBM Plex Mono"

MODES = ("auto", "light", "dark")

_PALETTES: dict[str, dict[str, object]] = {
    "light": {
        "BG": "#c4cbd2",            # papier
        "SURFACE": "#e5eaef",       # feuille
        "SURFACE_ALT": "#d6dce2",
        "BORDER": "#a1a9af",        # filet
        "STRONG": "#2b343b",        # filet épais, en tête des feuilles
        "TEXT": "#060e15",
        "MUTED": "#3b434b",
        "ACCENT": "#970d63",
        "ACCENT_DIM": "#76004b",
        "ACCENT_INK": "#f2f6f9",
        "OK": "#21763c",
        "WARN": "#8a6200",
        "DANGER": "#be2323",
        "SERIES": (
            "#970d63", "#21763c", "#1f5fa8", "#8a6200", "#6a3fa0",
            "#0f7470", "#b03a6e", "#5b6e1c", "#8a5a2b", "#3d5f9a",
        ),
        "ZONES": ("#3d5f9a", "#21763c", "#8a6200", "#b4521c", "#be2323"),
    },
    "dark": {
        "BG": "#14191e",
        "SURFACE": "#1c2228",
        "SURFACE_ALT": "#252c33",
        "BORDER": "#333b42",
        "STRONG": "#b7bfc5",
        "TEXT": "#e4e8ed",
        "MUTED": "#97a0a7",
        "ACCENT": "#db6ea5",
        "ACCENT_DIM": "#c0558d",
        "ACCENT_INK": "#060e15",
        "OK": "#69ba7c",
        "WARN": "#e0ae57",
        "DANGER": "#ea6b60",
        "SERIES": (
            "#db6ea5", "#69ba7c", "#6ea8e8", "#e0ae57", "#b08ce0",
            "#4fbfb2", "#e88bb0", "#a7c46a", "#d6a372", "#8fb0ea",
        ),
        "ZONES": ("#8fb0ea", "#69ba7c", "#e0ae57", "#e8834a", "#ea6b60"),
    },
}


def system_prefers_dark() -> bool:
    """Le système demande-t-il un thème sombre ? Faux en cas de doute."""
    try:
        if sys.platform == "win32":
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
        if sys.platform == "darwin":
            out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                                 capture_output=True, text=True, timeout=2)
            return out.stdout.strip().lower() == "dark"
        if "dark" in os.environ.get("GTK_THEME", "").lower():
            return True
        out = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                             capture_output=True, text=True, timeout=2)
        return "dark" in out.stdout.lower()
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def stored_mode() -> str:
    from cairn.gui import prefs

    mode = prefs.load_view("theme", "auto")
    return mode if mode in MODES else "auto"


def resolve(mode: str) -> str:
    if mode == "auto":
        return "dark" if system_prefers_dark() else "light"
    return mode if mode in _PALETTES else "light"


def _initial_mode() -> str:
    forced = os.environ.get("CAIRN_THEME", "")
    if forced in MODES:
        return resolve(forced)
    try:
        return resolve(stored_mode())
    except Exception:  # réglages illisibles : on ne bloque pas le démarrage
        return "light"


MODE = _initial_mode()
_P = _PALETTES[MODE]

# --- palette ---------------------------------------------------------

BG: str = _P["BG"]                      # type: ignore[assignment]
SURFACE: str = _P["SURFACE"]            # type: ignore[assignment]
SURFACE_ALT: str = _P["SURFACE_ALT"]    # type: ignore[assignment]
BORDER: str = _P["BORDER"]              # type: ignore[assignment]
STRONG: str = _P["STRONG"]              # type: ignore[assignment]
TEXT: str = _P["TEXT"]                  # type: ignore[assignment]
MUTED: str = _P["MUTED"]                # type: ignore[assignment]
ACCENT: str = _P["ACCENT"]              # type: ignore[assignment]
ACCENT_DIM: str = _P["ACCENT_DIM"]      # type: ignore[assignment]
ACCENT_INK: str = _P["ACCENT_INK"]      # type: ignore[assignment]
OK: str = _P["OK"]                      # type: ignore[assignment]
WARN: str = _P["WARN"]                  # type: ignore[assignment]
DANGER: str = _P["DANGER"]              # type: ignore[assignment]

# Couleurs de série pour les graphiques, dans l'ordre d'utilisation. Les cinq
# premières sont désignées par leur indice ailleurs dans le code : les
# suivantes s'ajoutent en fin de liste, jamais au milieu.
SERIES: tuple[str, ...] = _P["SERIES"]  # type: ignore[assignment]
# Zones d'intensité, de la plus douce à la plus dure.
ZONES: tuple[str, ...] = _P["ZONES"]    # type: ignore[assignment]


def qcolor(hex_value: str, alpha: int = 255) -> QColor:
    color = QColor(hex_value)
    color.setAlpha(alpha)
    return color


def load_fonts() -> None:
    """Charge les polices du site (embarquées, licence OFL)."""
    from PySide6.QtGui import QFontDatabase

    if FONT_DIR.is_dir():
        for font in sorted(FONT_DIR.glob("*.ttf")):
            QFontDatabase.addApplicationFont(str(font))


STYLESHEET = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: "{DISPLAY}";
    font-size: 14px;
}}

QMainWindow, QDialog {{ background: {BG}; }}

/* Sans cela, chaque étiquette repeint le fond général par-dessus la carte
   qui la contient, ce qui dessine un rectangle plus sombre derrière le
   moindre texte. */
QLabel {{ background: transparent; }}

/* ---- barre latérale ---- */

#Sidebar {{
    background: {BG};
    border-right: 2px solid {STRONG};
}}

#SidebarTitle {{
    color: {TEXT};
    font-size: 19px;
    font-weight: 600;
    padding: 4px 0 2px;
}}

#SidebarSubtitle {{
    color: {MUTED};
    font-family: "{BODY}";
    font-size: 13px;
    padding-bottom: 8px;
}}

QPushButton#NavButton {{
    background: transparent;
    border: none;
    border-left: 2px solid transparent;
    border-radius: 0;
    color: {MUTED};
    text-align: left;
    padding: 10px 14px;
    font-size: 14px;
}}

QPushButton#NavButton:hover {{
    background: {SURFACE_ALT};
    color: {TEXT};
}}

QPushButton#NavButton:checked {{
    background: {SURFACE};
    border-left: 2px solid {ACCENT};
    color: {ACCENT};
    font-weight: 600;
}}

/* ---- cartes : des feuilles bordées d'un filet épais ---- */

#Card {{
    background: {SURFACE};
    border: none;
    border-top: 2px solid {STRONG};
    border-radius: 0;
}}

#CardTitle {{ color: {MUTED}; font-size: 13px; font-weight: 500; }}
#CardValue {{ color: {TEXT}; font-family: "{MONO}"; font-size: 26px; font-weight: 500; }}
#CardHint  {{ color: {MUTED}; font-family: "{BODY}"; font-style: italic; font-size: 13px; }}

#PageTitle {{ font-size: 26px; font-weight: 600; }}
#PageHint  {{ color: {MUTED}; font-family: "{BODY}"; font-size: 14px; }}
#SectionTitle {{ font-size: 16px; font-weight: 600; }}

/* ---- boutons ---- */

QPushButton {{
    background: transparent;
    border: 1px solid {STRONG};
    border-radius: 0;
    padding: 8px 16px;
    color: {TEXT};
    font-weight: 500;
}}

QPushButton:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton:pressed {{ background: {SURFACE_ALT}; }}
QPushButton:focus {{ border: 2px solid {ACCENT}; padding: 7px 15px; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {BORDER}; background: transparent; }}

QPushButton#Primary {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: {ACCENT_INK};
    font-weight: 600;
}}
QPushButton#Primary:hover {{
    background: {ACCENT_DIM};
    border-color: {ACCENT_DIM};
    color: {ACCENT_INK};
}}
QPushButton#Primary:disabled {{
    background: {SURFACE_ALT};
    border-color: {BORDER};
    color: {MUTED};
}}

/* ---- champs ---- */

QLineEdit, QSpinBox, QComboBox {{
    background: {SURFACE};
    border: 1px solid {MUTED};
    border-radius: 0;
    padding: 8px 10px;
    selection-background-color: {ACCENT};
    selection-color: {ACCENT_INK};
}}

QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border: 2px solid {ACCENT}; padding: 7px 9px; }}

QComboBox QAbstractItemView {{
    background: {SURFACE};
    border: 1px solid {STRONG};
    selection-background-color: {ACCENT};
    selection-color: {ACCENT_INK};
}}

QLabel#FieldLabel {{ color: {MUTED}; font-size: 13px; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {STRONG};
    background: {SURFACE};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

/* ---- tableaux ---- */

QTableView {{
    background: {SURFACE};
    alternate-background-color: {SURFACE};
    border: none;
    border-top: 2px solid {STRONG};
    border-radius: 0;
    gridline-color: {BORDER};
    selection-background-color: {ACCENT};
    selection-color: {ACCENT_INK};
}}

QHeaderView::section {{
    background: {SURFACE};
    border: none;
    border-bottom: 2px solid {STRONG};
    color: {MUTED};
    padding: 8px 10px;
    font-family: "{BODY}";
    font-size: 13px;
    font-weight: 400;
}}

QTableView::item {{ padding: 6px 10px; border-bottom: 1px solid {BORDER}; }}

/* ---- divers ---- */

QPlainTextEdit {{
    background: {SURFACE};
    border: none;
    border-top: 2px solid {STRONG};
    border-radius: 0;
    font-family: "{MONO}";
    font-size: 12px;
}}

QProgressBar {{
    background: {SURFACE_ALT};
    border: none;
    border-radius: 0;
    height: 4px;
    text-align: center;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 0; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 0; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 0; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}

QScrollArea {{ border: none; }}

QTabWidget::pane {{ border: none; border-top: 2px solid {STRONG}; }}
QTabBar::tab {{
    background: transparent;
    color: {MUTED};
    padding: 8px 14px;
    border: none;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}

QStatusBar {{ background: {BG}; border-top: 2px solid {STRONG}; color: {MUTED}; }}
QToolTip {{ background: {SURFACE}; color: {TEXT}; border: 1px solid {STRONG}; padding: 6px; }}
QMenu {{ background: {SURFACE}; border: 1px solid {STRONG}; }}
QMenu::item:selected {{ background: {ACCENT}; color: {ACCENT_INK}; }}
"""
