"""Palette et feuille de style de l'application.

Une seule source de vérité pour les couleurs, reprise de l'identité du
projet : fonds ardoise, pierres claires, accent terre cuite.
"""

from __future__ import annotations

from PySide6.QtGui import QColor

# --- palette ---------------------------------------------------------

BG = "#14171b"
SURFACE = "#1b1f24"
SURFACE_ALT = "#21262c"
BORDER = "#2c3238"
TEXT = "#e8e4dd"
MUTED = "#9aa3ad"
ACCENT = "#e8834a"
ACCENT_DIM = "#c25e2a"
OK = "#5fa87a"
WARN = "#d9a441"
DANGER = "#d9614b"

# Couleurs de série pour les graphiques, dans l'ordre d'utilisation.
SERIES = ("#e8834a", "#5fa87a", "#6b9bd2", "#d9a441", "#a97fd0")


def qcolor(hex_value: str, alpha: int = 255) -> QColor:
    color = QColor(hex_value)
    color.setAlpha(alpha)
    return color


STYLESHEET = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-size: 14px;
}}

QMainWindow, QDialog {{ background: {BG}; }}

/* Sans cela, chaque étiquette repeint le fond général par-dessus la carte
   qui la contient, ce qui dessine un rectangle plus sombre derrière le
   moindre texte. */
QLabel {{ background: transparent; }}

/* ---- barre latérale ---- */

#Sidebar {{
    background: {SURFACE};
    border-right: 1px solid {BORDER};
}}

#SidebarTitle {{
    color: {TEXT};
    font-size: 17px;
    font-weight: 600;
    padding: 4px 0 2px;
}}

#SidebarSubtitle {{
    color: {MUTED};
    font-size: 12px;
    padding-bottom: 8px;
}}

QPushButton#NavButton {{
    background: transparent;
    border: none;
    border-radius: 8px;
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
    background: {SURFACE_ALT};
    color: {ACCENT};
    font-weight: 600;
}}

/* ---- cartes ---- */

#Card {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 12px;
}}

#CardTitle {{ color: {MUTED}; font-size: 12px; font-weight: 600; }}
#CardValue {{ color: {TEXT}; font-size: 26px; font-weight: 600; }}
#CardHint  {{ color: {MUTED}; font-size: 12px; }}

#PageTitle {{ font-size: 21px; font-weight: 600; }}
#PageHint  {{ color: {MUTED}; font-size: 13px; }}
#SectionTitle {{ font-size: 15px; font-weight: 600; }}

/* ---- boutons ---- */

QPushButton {{
    background: {SURFACE_ALT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 16px;
    color: {TEXT};
}}

QPushButton:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton:disabled {{ color: {MUTED}; border-color: {BORDER}; background: {SURFACE}; }}

QPushButton#Primary {{
    background: {ACCENT_DIM};
    border: 1px solid {ACCENT_DIM};
    color: #ffffff;
    font-weight: 600;
}}
QPushButton#Primary:hover {{ background: {ACCENT}; border-color: {ACCENT}; color: #ffffff; }}
QPushButton#Primary:disabled {{
    background: {SURFACE_ALT};
    border-color: {BORDER};
    color: {MUTED};
}}

/* ---- champs ---- */

QLineEdit, QSpinBox, QComboBox {{
    background: {SURFACE_ALT};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 10px;
    selection-background-color: {ACCENT_DIM};
}}

QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}

QLabel#FieldLabel {{ color: {MUTED}; font-size: 12px; }}

/* ---- tableaux ---- */

QTableView {{
    background: {SURFACE};
    alternate-background-color: {SURFACE_ALT};
    border: 1px solid {BORDER};
    border-radius: 10px;
    gridline-color: {BORDER};
    selection-background-color: {ACCENT_DIM};
    selection-color: #ffffff;
}}

QHeaderView::section {{
    background: {SURFACE};
    border: none;
    border-bottom: 1px solid {BORDER};
    color: {MUTED};
    padding: 8px 10px;
    font-size: 12px;
    font-weight: 600;
}}

QTableView::item {{ padding: 6px 10px; }}

/* ---- divers ---- */

QPlainTextEdit {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 10px;
    font-family: "monospace";
    font-size: 12px;
}}

QProgressBar {{
    background: {SURFACE_ALT};
    border: none;
    border-radius: 4px;
    height: 6px;
    text-align: center;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {MUTED}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}

QScrollArea {{ border: none; }}

QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
QToolTip {{ background: {SURFACE_ALT}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px; }}
"""
