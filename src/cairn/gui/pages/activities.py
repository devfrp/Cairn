"""Liste des sorties, toutes disciplines et toutes sources confondues."""

from __future__ import annotations

import datetime as dt
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from cairn.gui import queries, theme
from cairn.gui.widgets import (
    SectionHeader,
    format_distance,
    format_duration,
    format_elevation,
    row,
)
from cairn.text import pluriel


def _fold(text: str) -> str:
    """Minuscules sans accents, pour une recherche indulgente."""
    import unicodedata

    decompose = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decompose if unicodedata.category(c) != "Mn")


SPORT_LABELS = {
    "mtb": "VTT",
    "enduro": "Enduro",
    "road": "Route",
    "gravel": "Gravel",
    "trail_run": "Trail",
    "run": "Course",
    "hike": "Randonnée",
    "walk": "Marche",
    "swim": "Natation",
    "strength": "Renforcement",
    "cardio": "Cardio",
    "mobility": "Mobilité",
    "ski": "Ski",
    # Sports découverts à la première synchronisation réelle : sans libellé,
    # la clé technique s'affichait telle quelle à l'écran.
    "surf": "Surf",
    "paddle": "Paddle",
    "kitesurf": "Kitesurf",
    "windsurf": "Windsurf",
    "badminton": "Badminton",
    "football": "Football",
    "tennis": "Tennis",
    "ping_pong": "Tennis de table",
    "breathwork": "Respiration",
    "incident_detected": "Chute détectée",
    "other": "Autre",
}


class ActivityModel(QAbstractTableModel):
    COLUMNS = ("Date", "Sport", "Sortie", "Distance", "D+", "Durée",
               "FC moy (bpm)", "Charge", "Matériel", "Src")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[dict[str, Any]] = []

    def set_rows(self, rows: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802, B008
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation,  # noqa: N802
                   role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if role != Qt.ItemDataRole.DisplayRole or orientation != Qt.Orientation.Horizontal:
            return None
        return self.COLUMNS[section]

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        entry = self._rows[index.row()]
        column = index.column()

        if role == Qt.ItemDataRole.TextAlignmentRole and column in (3, 4, 5, 6, 7):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        sources = sorted({s for s in (entry.get("sources") or []) if s})

        if role == Qt.ItemDataRole.ForegroundRole and column == 9:
            # Une sortie vue des deux côtés a été fusionnée : on le montre.
            return QColor(theme.OK if len(sources) > 1 else theme.MUTED)

        if role == Qt.ItemDataRole.ToolTipRole:
            if column == 9:
                return " + ".join(sources) if sources else "aucune source"
            if column == 2:
                return entry.get("name") or ""

        if role != Qt.ItemDataRole.DisplayRole:
            return None

        moment = entry.get("start_time_local")
        if column == 0:
            # Année sur deux chiffres : la colonne doit rester étroite pour
            # que le nom de la sortie garde de la place.
            return moment.strftime("%d/%m/%y %H:%M") if isinstance(moment, dt.datetime) else "—"
        if column == 1:
            return SPORT_LABELS.get(entry.get("sport"), entry.get("sport") or "—")
        if column == 2:
            return entry.get("name") or "—"
        if column == 3:
            return format_distance(entry.get("distance_m"))
        if column == 4:
            return format_elevation(entry.get("elevation_gain_m"))
        if column == 5:
            return format_duration(entry.get("elapsed_s"))
        if column == 6:
            # L'unité est dans l'en-tête : la répéter n'ajoute que de la
            # largeur à une colonne déjà nombreuse.
            return str(entry["avg_hr"]) if entry.get("avg_hr") else "—"
        if column == 7:
            load = entry.get("load")
            return f"{float(load):.0f}" if load else "—"
        if column == 8:
            return entry.get("gear_name") or "—"
        if column == 9:
            return "+".join(s[0].upper() for s in sources) if sources else "—"
        return None


class ActivitiesPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(16)

        layout.addWidget(
            SectionHeader(
                "Activités",
                "Garmin et Strava réunis. Une sortie présente des deux côtés "
                "n'apparaît qu'une fois.",
            )
        )

        self.sport_filter = QComboBox()
        self.sport_filter.addItem("Tous les sports", "")
        self.sport_filter.currentIndexChanged.connect(self._apply_filter)

        # Cent dix-huit sorties : retrouver « la sortie de Pra Loup » au
        # défilement est pénible, et le seul filtre par sport n'y aide pas.
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher un nom, un matériel…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)

        self.summary = QLabel("")
        self.summary.setObjectName("PageHint")
        layout.addWidget(row(self.sport_filter, self.search, self.summary))

        self.model = ActivityModel(self)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        # Chaque colonne prend la largeur de son contenu, quitte à faire
        # défiler horizontalement sur une fenêtre étroite. Des largeurs fixes
        # tronquaient les valeurs, et un mode Stretch écrasait le nom de la
        # sortie — qui est pourtant ce qu'on lit en premier.
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)
        # Le détail seconde par seconde est ce qu'on cherche en cliquant une
        # ligne : les totaux du tableau ne disent rien du déroulé.
        self.table.doubleClicked.connect(self._open_detail)
        layout.addWidget(self.table, 1)

        aide = QLabel("Double-cliquez une sortie pour voir son déroulé.")
        aide.setObjectName("PageHint")
        layout.addWidget(aide)

        self._all: list[dict[str, Any]] = []

    def _open_detail(self, index) -> None:
        ligne = self.model._rows[index.row()] if index.isValid() else None
        if not ligne or not ligne.get("id"):
            return
        from cairn.gui.pages.activity_detail import ActivityDetailDialog

        ActivityDetailDialog(ligne["id"], self).exec()

    def refresh(self, health: queries.Health) -> None:
        if not health.usable:
            self._all = []
            self.model.set_rows([])
            self.summary.setText("Base indisponible.")
            return

        self._all = queries.recent_activities(limit=200)
        self._rebuild_filter()
        self._apply_filter()

    def _rebuild_filter(self) -> None:
        current = self.sport_filter.currentData()
        sports = sorted({e.get("sport") for e in self._all if e.get("sport")})

        self.sport_filter.blockSignals(True)
        self.sport_filter.clear()
        self.sport_filter.addItem("Tous les sports", "")
        for sport in sports:
            self.sport_filter.addItem(SPORT_LABELS.get(sport, sport), sport)
        index = self.sport_filter.findData(current)
        self.sport_filter.setCurrentIndex(max(0, index))
        self.sport_filter.blockSignals(False)

    def _apply_filter(self) -> None:
        wanted = self.sport_filter.currentData() or ""
        needle = self.search.text().strip().lower()

        rows = [
            e for e in self._all
            if (not wanted or e.get("sport") == wanted) and self._matches(e, needle)
        ]
        self.model.set_rows(rows)

        merged = sum(1 for e in rows if len({s for s in (e.get("sources") or []) if s}) > 1)
        if rows:
            detail = (
                f"{pluriel(len(rows), 'sortie')} · "
                f"{pluriel(merged, 'vue')} des deux sources"
            )
            if needle or wanted:
                detail += f" · sur {len(self._all)}"
            self.summary.setText(detail)
        elif self._all:
            self.summary.setText("Aucune sortie ne correspond à cette recherche.")
        else:
            self.summary.setText("Aucune activité. Lancez une synchronisation.")

    @staticmethod
    def _matches(entry: dict[str, Any], needle: str) -> bool:
        """Nom, matériel et libellé de sport, sans accent ni casse.

        Chercher « velo » doit trouver « Sortie vélo » : personne ne tape les
        accents dans un champ de recherche.
        """
        if not needle:
            return True
        champs = " ".join(
            str(entry.get(cle) or "") for cle in ("name", "gear_name", "sport")
        )
        champs += " " + SPORT_LABELS.get(entry.get("sport"), "")
        return _fold(needle) in _fold(champs)
