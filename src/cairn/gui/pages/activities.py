"""Liste des sorties, toutes disciplines et toutes sources confondues."""

from __future__ import annotations

import datetime as dt
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
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

    # Clé de tri par colonne. Le texte affiché ne convient pas : « 7,5 km »
    # se classerait avant « 61,9 km », et « 2 h 00 » avant « 45 min ».
    SORT_KEYS = ("start_time_local", "sport", "name", "distance_m",
                 "elevation_gain_m", "elapsed_s", "avg_hr", "load",
                 "gear_name", "sources")

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

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder) -> None:
        """Trie sur la valeur, jamais sur le texte affiché.

        Les valeurs absentes se rangent toujours en dernier, quel que soit le
        sens : une sortie sans capteur cardiaque n'a pas la fréquence la plus
        basse, elle n'en a pas.
        """
        if not 0 <= column < len(self.SORT_KEYS):
            return
        cle = self.SORT_KEYS[column]
        descendant = order == Qt.SortOrder.DescendingOrder

        def rang(entry: dict[str, Any]) -> tuple[int, Any]:
            valeur = entry.get(cle)
            if valeur is None or valeur == "":
                return (1, "")
            if isinstance(valeur, (list, tuple)):
                valeur = len(valeur)
            if isinstance(valeur, str):
                return (0, _fold(valeur))
            if isinstance(valeur, dt.datetime):
                return (0, valeur.timestamp())
            return (0, float(valeur))

        self.beginResetModel()
        # Deux passes, et le couple entier en clé de la première : comparer
        # les seules valeurs mettrait face à face un texte et un nombre dès
        # qu'une mesure manque. Dans un couple, Python tranche sur le premier
        # membre et ne regarde le second que s'ils sont égaux — donc jamais
        # entre un présent et un absent.
        self._rows.sort(key=rang, reverse=descendant)
        # La seconde passe, stable, renvoie les absents en fin de liste sans
        # défaire l'ordre établi par la première.
        self._rows.sort(key=lambda e: rang(e)[0])
        self.endResetModel()

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        entry = self._rows[index.row()]
        column = index.column()

        if role == Qt.ItemDataRole.TextAlignmentRole and column in (3, 4, 5, 6, 7):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        sources = sorted({s for s in (entry.get("sources") or []) if s})

        if role == Qt.ItemDataRole.ForegroundRole:
            # Une sortie écartée reste lisible mais cesse de compter : elle
            # doit se distinguer au premier regard, sinon on cherchera en vain
            # pourquoi les totaux ne tombent pas juste.
            if entry.get("excluded"):
                return QColor(theme.MUTED)
            if column == 9:
                # Une sortie vue des deux côtés a été fusionnée : on le montre.
                return QColor(theme.OK if len(sources) > 1 else theme.MUTED)

        if role == Qt.ItemDataRole.ToolTipRole:
            if entry.get("excluded"):
                raison = entry.get("excluded_reason") or "sans raison indiquée"
                return f"Écartée des statistiques — {raison}"
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
            nom = entry.get("name") or "—"
            return f"{nom} (écartée)" if entry.get("excluded") else nom
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

        self.period_filter = QComboBox()
        for libelle, jours in (("Toute la période", 0), ("30 jours", 30),
                               ("90 jours", 90), ("12 mois", 365)):
            self.period_filter.addItem(libelle, jours)
        self.period_filter.currentIndexChanged.connect(self._apply_filter)

        self.gear_filter = QComboBox()
        self.gear_filter.addItem("Tout le matériel", "")
        self.gear_filter.currentIndexChanged.connect(self._apply_filter)

        # Sept sorties marquées « course » par Strava : le drapeau existait
        # sans que rien ne s'en serve.
        self.race_filter = QCheckBox("Courses seulement")
        self.race_filter.toggled.connect(self._apply_filter)

        self.summary = QLabel("")
        self.summary.setObjectName("PageHint")
        layout.addWidget(
            row(self.sport_filter, self.period_filter, self.gear_filter,
                self.race_filter)
        )
        layout.addWidget(row(self.search, self.summary))

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
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu_contextuel)
        # Trier sur cent dix-neuf sorties évite de faire défiler pour trouver
        # la plus longue ou la plus chargée.
        self.table.setSortingEnabled(True)
        header.setSortIndicator(0, Qt.SortOrder.DescendingOrder)
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

    def _menu_contextuel(self, position) -> None:
        """Écarter une sortie aberrante, ou l'y remettre."""
        index = self.table.indexAt(position)
        if not index.isValid():
            return
        ligne = self.model._rows[index.row()]

        menu = QMenu(self)
        if ligne.get("excluded"):
            action = menu.addAction("Réintégrer aux statistiques")
        else:
            action = menu.addAction("Écarter des statistiques…")
        detail = menu.addAction("Voir le déroulé")

        choix = menu.exec(self.table.viewport().mapToGlobal(position))
        if choix is detail:
            self._open_detail(index)
        elif choix is action:
            self._basculer_exclusion(ligne)

    def _basculer_exclusion(self, ligne: dict[str, Any]) -> None:
        if ligne.get("excluded"):
            queries.set_activity_excluded(ligne["id"], False)
        else:
            raison, valide = QInputDialog.getText(
                self, "Écarter des statistiques",
                "Cette sortie restera dans la liste, avec sa raison, mais "
                "sortira des cumuls, des records et de la charge.\n\n"
                "Raison :",
                text="enregistrement aberrant",
            )
            if not valide:
                return
            queries.set_activity_excluded(ligne["id"], True, raison)
        self.refresh(queries.probe())

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
        self._remplir(
            self.sport_filter, "Tous les sports",
            [(SPORT_LABELS.get(s, s), s)
             for s in sorted({e.get("sport") for e in self._all if e.get("sport")})],
        )
        self._remplir(
            self.gear_filter, "Tout le matériel",
            [(g, g)
             for g in sorted({e.get("gear_name") for e in self._all if e.get("gear_name")})],
        )

    @staticmethod
    def _remplir(champ: QComboBox, tous: str, entrees: list) -> None:
        """Recharge une liste déroulante sans perdre le choix en cours."""
        courant = champ.currentData()
        champ.blockSignals(True)
        champ.clear()
        champ.addItem(tous, "")
        for libelle, valeur in entrees:
            champ.addItem(libelle, valeur)
        champ.setCurrentIndex(max(0, champ.findData(courant)))
        champ.blockSignals(False)

    def _apply_filter(self) -> None:
        wanted = self.sport_filter.currentData() or ""
        gear = self.gear_filter.currentData() or ""
        jours = self.period_filter.currentData() or 0
        courses = self.race_filter.isChecked()
        needle = self.search.text().strip().lower()

        limite = dt.date.today() - dt.timedelta(days=jours) if jours else None
        rows = [
            e for e in self._all
            if (not wanted or e.get("sport") == wanted)
            and (not gear or e.get("gear_name") == gear)
            and (not courses or e.get("is_race"))
            and self._dans_la_periode(e, limite)
            and self._matches(e, needle)
        ]
        self.model.set_rows(rows)
        # Le tri en cours doit survivre au filtrage : `set_rows` rend la liste
        # dans l'ordre de la requête, pas dans celui que l'utilisateur a choisi.
        entete = self.table.horizontalHeader()
        self.model.sort(entete.sortIndicatorSection(), entete.sortIndicatorOrder())

        merged = sum(1 for e in rows if len({s for s in (e.get("sources") or []) if s}) > 1)
        if rows:
            detail = (
                f"{pluriel(len(rows), 'sortie')} · "
                f"{pluriel(merged, 'vue')} des deux sources"
            )
            if len(rows) != len(self._all):
                detail += f" · sur {len(self._all)}"
            self.summary.setText(detail)
        elif self._all:
            self.summary.setText("Aucune sortie ne correspond à cette recherche.")
        else:
            self.summary.setText("Aucune activité. Lancez une synchronisation.")

    @staticmethod
    def _dans_la_periode(entry: dict[str, Any], limite: dt.date | None) -> bool:
        if limite is None:
            return True
        moment = entry.get("start_time_local")
        if not isinstance(moment, dt.datetime):
            return True
        return moment.date() >= limite

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
