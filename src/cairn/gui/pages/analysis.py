"""Page d'analyse : les répartitions qui demandent un graphique.

Trois lectures qui demandent un graphique plutôt qu'un chiffre : la
répartition du temps par zone d'intensité, le volume par discipline, et
l'évolution de la charge semaine après semaine.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from cairn.gui import queries, theme
from cairn.gui.pages.activities import SPORT_LABELS
from cairn.gui.widgets import (
    BarBreakdown,
    Card,
    ColumnChart,
    SectionHeader,
    format_distance,
    format_duration,
    row,
)

# Une couleur par zone, du calme à l'intense.
ZONE_COLOURS = ("#6b9bd2", "#5fa87a", "#d9a441", "#e8834a", "#d9614b")

ZONE_LABELS = {
    1: "Zone 1 · récup",
    2: "Zone 2 · endurance",
    3: "Zone 3 · tempo",
    4: "Zone 4 · seuil",
    5: "Zone 5 · maximal",
}

PERIODS = (("30 derniers jours", 30), ("3 mois", 90), ("6 mois", 180), ("12 mois", 365))


class AnalysisPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(18)

        layout.addWidget(
            SectionHeader(
                "Analyse",
                "Où passe le temps d'entraînement, et comment la charge évolue.",
            )
        )

        self.period = QComboBox()
        for label, days in PERIODS:
            self.period.addItem(label, days)
        self.period.currentIndexChanged.connect(lambda: self.refresh(self._health))
        self.summary = QLabel("")
        self.summary.setObjectName("PageHint")
        layout.addWidget(row(self.period, self.summary))

        self.zones = self._card(
            "Temps par zone de fréquence cardiaque",
            "Calculé depuis les fichiers d'activité, seconde par seconde.",
        )
        self.zones_chart = BarBreakdown()
        self.zones.layout().addWidget(self.zones_chart)
        layout.addWidget(self.zones)

        self.sports = self._card("Volume par discipline", "")
        self.sports_chart = BarBreakdown()
        self.sports.layout().addWidget(self.sports_chart)
        layout.addWidget(self.sports)

        self.weekly = self._card(
            "Charge par semaine",
            "Une progression régulière vaut mieux qu'une semaine à pic.",
        )
        self.weekly_chart = ColumnChart()
        self.weekly.layout().addWidget(self.weekly_chart)
        layout.addWidget(self.weekly)

        self.gear = self._card(
            "Usure du matériel",
            "Le kilométrage vient de Strava ; l'usure se compte depuis la "
            "pose de chaque pièce.",
        )
        layout.addWidget(self.gear)

        layout.addStretch(1)
        self._health = queries.Health()

    def _card(self, title: str, hint: str) -> Card:
        card = Card()
        label = QLabel(title)
        label.setObjectName("SectionTitle")
        card.layout().addWidget(label)
        if hint:
            sub = QLabel(hint)
            sub.setObjectName("CardHint")
            sub.setWordWrap(True)
            card.layout().addWidget(sub)
        return card

    # -----------------------------------------------------------------

    def refresh(self, health: queries.Health) -> None:
        self._health = health
        if not health.usable:
            self.summary.setText("Base indisponible.")
            for chart in (self.zones_chart, self.sports_chart):
                chart.set_rows([])
            self.weekly_chart.set_values([])
            return

        days = self.period.currentData() or 30
        self._fill_zones(queries.zone_distribution(days))
        self._fill_sports(queries.sport_breakdown(days))
        self._fill_weekly(queries.weekly_load(days))
        # L'usure ne dépend pas de la période choisie : elle se compte depuis
        # la pose de la pièce, pas sur les trente derniers jours.
        self._fill_gear()

    def _fill_gear(self) -> None:
        """Une carte par matériel, ses pièces et leur usure."""
        from cairn.gui.pages.gear import GearCard

        disposition = self.gear.layout()
        # La carte se reconstruit à chaque passage : les pièces changent, et
        # recalculer une usure sur des widgets existants coûterait plus de
        # code que d'en refaire.
        while disposition.count() > 2:
            element = disposition.takeAt(2)
            if element.widget():
                element.widget().deleteLater()

        lignes = queries.gear_wear()
        par_materiel: dict[int, list[dict]] = {}
        materiels: dict[int, dict] = {}
        for ligne in lignes:
            ligne = dict(ligne)
            materiels.setdefault(ligne["id"], ligne)
            if ligne.get("component_id"):
                par_materiel.setdefault(ligne["id"], []).append(ligne)

        if not materiels:
            vide = QLabel(
                "Aucun matériel. Strava en fournit la liste : reliez le compte "
                "et lancez une synchronisation."
            )
            vide.setObjectName("CardHint")
            vide.setWordWrap(True)
            disposition.addWidget(vide)
            return

        for identifiant, materiel in materiels.items():
            disposition.addWidget(
                GearCard(materiel, par_materiel.get(identifiant, []), self._reload_gear)
            )

    def _reload_gear(self) -> None:
        self._fill_gear()

    def _fill_zones(self, rows: list[dict]) -> None:
        if not rows:
            self.zones_chart.set_rows([])
            return
        total = sum(r["seconds"] for r in rows) or 1
        self.zones_chart.set_rows([
            (
                ZONE_LABELS.get(r["zone_index"], f"Zone {r['zone_index']}"),
                float(r["seconds"]),
                f"{format_duration(r['seconds'])}  ·  {r['seconds'] / total * 100:.0f} %",
                ZONE_COLOURS[(r["zone_index"] - 1) % len(ZONE_COLOURS)],
            )
            for r in rows
        ])

    def _fill_sports(self, rows: list[dict]) -> None:
        if not rows:
            self.sports_chart.set_rows([])
            self.summary.setText("Aucune activité sur la période.")
            return

        palette = theme.SERIES
        self.sports_chart.set_rows([
            (
                SPORT_LABELS.get(r["sport"], r["sport"] or "—"),
                float(r["duration_s"] or 0),
                f"{format_duration(r['duration_s'])}  ·  {format_distance(r['distance_m'])}",
                palette[index % len(palette)],
            )
            for index, r in enumerate(rows)
        ])

        sessions = sum(r["sessions"] for r in rows)
        duration = sum(r["duration_s"] or 0 for r in rows)
        self.summary.setText(
            f"{sessions} sortie(s) · {format_duration(duration)} au total"
        )

    def _fill_weekly(self, rows: list[dict]) -> None:
        # Au-delà d'une vingtaine de colonnes, les libellés deviennent
        # illisibles : on ne garde que les semaines les plus récentes.
        recent = rows[-20:]
        self.weekly_chart.set_values(
            [(f"S{str(r['week'])[-2:]}", float(r["load"] or 0)) for r in recent],
            theme.ACCENT,
        )
