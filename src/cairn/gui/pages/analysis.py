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

from cairn.gui import prefs, queries, theme
from cairn.gui.pages.activities import SPORT_LABELS
from cairn.gui.widgets import (
    BarBreakdown,
    Card,
    ColumnChart,
    LoadCalendar,
    SectionHeader,
    format_distance,
    format_duration,
    format_elevation,
    row,
)
from cairn.text import pluriel

# Une couleur par zone, du calme à l'intense.
ZONE_COLOURS = theme.ZONES

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

        self.totals = self._card(
            "Depuis le 1er janvier",
            "Ce que l'année cumule, et l'écart avec la même période l'an "
            "dernier.",
        )
        self.totals_body = QLabel("—")
        self.totals_body.setObjectName("CardValue")
        self.totals_detail = QLabel("")
        self.totals_detail.setObjectName("CardHint")
        self.totals_detail.setWordWrap(True)
        self.totals.layout().addWidget(self.totals_body)
        self.totals.layout().addWidget(self.totals_detail)
        layout.addWidget(self.totals)

        self.zones = self._card(
            "Temps par zone de fréquence cardiaque",
            "Calculé depuis les fichiers d'activité, seconde par seconde.",
        )
        self.zones_chart = BarBreakdown()
        self.zones.layout().addWidget(self.zones_chart)
        # Les bornes qui servent au calcul, jamais montrées jusqu'ici : sans
        # elles, impossible de vérifier que le TRIMP repose sur les bonnes.
        self.zones_bounds = QLabel("")
        self.zones_bounds.setObjectName("CardHint")
        self.zones_bounds.setWordWrap(True)
        self.zones.layout().addWidget(self.zones_bounds)
        layout.addWidget(self.zones)

        self.sports = self._card("Volume par discipline", "")
        self.sports_chart = BarBreakdown()
        self.sports.layout().addWidget(self.sports_chart)
        layout.addWidget(self.sports)

        self.calendar = self._card(
            "Calendrier de charge",
            "Une case par jour sur l'année écoulée. Les colonnes sont les "
            "semaines : la lecture verticale donne la semaine, l'horizontale "
            "la régularité.",
        )
        self.calendar_chart = LoadCalendar()
        self.calendar.layout().addWidget(self.calendar_chart)
        layout.addWidget(self.calendar)

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

        self.segments = self._card(
            "Progression sur les segments",
            "Ceux parcourus plusieurs fois : c'est la répétition qui permet "
            "de comparer.",
        )
        layout.addWidget(self.segments)

        self.records = self._card(
            "Records personnels",
            "Par discipline, à partir de trois séances — en dessous, c'est "
            "simplement la seule sortie du sport.",
        )
        layout.addWidget(self.records)

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
            self.calendar_chart.set_data([], [])
            return

        days = self.period.currentData() or 30
        self._fill_totals(queries.period_totals())
        self._fill_zones(queries.zone_distribution(days))
        self._fill_zone_bounds(queries.hr_zones())
        self._fill_sports(queries.sport_breakdown(days))
        self._fill_weekly(queries.weekly_load(days))
        self._appliquer_objectif()
        # L'année entière, quelle que soit la période choisie : c'est
        # justement la vue d'ensemble que les autres cartes ne donnent pas.
        annee = queries.daily_metrics(365)
        self.calendar_chart.set_data(annee.days, annee.get("load"))
        # L'usure ne dépend pas de la période choisie : elle se compte depuis
        # la pose de la pièce, pas sur les trente derniers jours.
        self._fill_gear()
        self._fill_segments()
        self._fill_records()

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

    def _fill_segments(self) -> None:
        from cairn.gui.pages.segments import SegmentRow

        disposition = self.segments.layout()
        while disposition.count() > 2:
            element = disposition.takeAt(2)
            if element.widget():
                element.widget().deleteLater()

        lignes = queries.repeated_segments()
        if not lignes:
            vide = QLabel(
                "Aucun segment parcouru deux fois. Ils viennent de Strava, "
                "avec les sorties."
            )
            vide.setObjectName("CardHint")
            vide.setWordWrap(True)
            disposition.addWidget(vide)
            return

        # Au-delà d'une quinzaine, la carte devient un mur : les moins courus
        # n'apprennent de toute façon pas grand-chose.
        for ligne in lignes[:15]:
            disposition.addWidget(SegmentRow(dict(ligne)))

    def _fill_records(self) -> None:
        """Meilleures marques par discipline.

        La charge la plus élevée figure à côté de la distance : une séance
        courte et intense est une performance, ce que le kilométrage seul ne
        dit pas.
        """
        disposition = self.records.layout()
        while disposition.count() > 2:
            element = disposition.takeAt(2)
            if element.widget():
                element.widget().deleteLater()

        lignes = queries.personal_records()
        if not lignes:
            vide = QLabel("Pas encore assez de séances pour dégager des records.")
            vide.setObjectName("CardHint")
            disposition.addWidget(vide)
            return

        for ligne in lignes:
            d = dict(ligne)
            sport = SPORT_LABELS.get(d["sport"], d["sport"])
            marques = []
            if d["longest_m"]:
                marques.append(f"la plus longue {format_distance(d['longest_m'])}")
            if d["highest_m"]:
                marques.append(f"{format_elevation(d['highest_m'])} de D+")
            if d["longest_time_s"]:
                marques.append(f"{format_duration(d['longest_time_s'])} d'affilée")
            if d["hardest_load"]:
                marques.append(f"charge maximale {d['hardest_load']:.0f}")

            texte = QLabel(
                f"<b>{sport}</b> · {d['sessions']} séances — " + ", ".join(marques)
            )
            texte.setWordWrap(True)
            disposition.addWidget(texte)

    def _reload_gear(self) -> None:
        self._fill_gear()

    def _fill_totals(self, totals: dict) -> None:
        if not totals or not totals.get("sessions"):
            self.totals_body.setText("—")
            self.totals_detail.setText("Aucune sortie enregistrée cette année.")
            return

        self.totals_body.setText(
            f"{format_distance(totals['distance_m'])} · "
            f"{format_elevation(totals['elevation_m'])} · "
            f"{format_duration(totals['duration_s'])}"
        )

        morceaux = [
            pluriel(totals["sessions"], "sortie"),
            f"charge cumulée {totals['load']:.0f}",
        ]
        precedent = totals.get("distance_precedente_m") or 0
        if precedent:
            ecart = (totals["distance_m"] - precedent) / precedent * 100
            morceaux.append(f"{ecart:+.0f} % de distance vs l'an dernier")
        else:
            # Comparer à une année sans données donnerait un « +100 % » qui ne
            # veut rien dire : l'historique commence là, voilà tout.
            morceaux.append("pas d'historique sur la période équivalente")
        self.totals_detail.setText(" · ".join(morceaux))

    def _fill_zone_bounds(self, zones: list[dict]) -> None:
        if not zones:
            self.zones_bounds.setText("")
            return
        bornes = " · ".join(
            f"Z{z['zone_index']} {z['min_bpm']}–{z['max_bpm']}" for z in zones
        )
        origine = zones[0]
        self.zones_bounds.setText(
            f"Vos zones ({origine['source']}, FC max {origine['hr_max']}) : {bornes} bpm"
        )

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
            f"{pluriel(sessions, 'sortie')} · {format_duration(duration)} au total"
        )

    def _appliquer_objectif(self) -> None:
        """Trait de cible sur la charge hebdomadaire, si une cible existe.

        Seule la charge se trace ici : le graphe ne montre qu'elle, et poser
        un objectif de distance sur un axe de charge n'aurait aucun sens.
        """
        mesure, cible = prefs.load_objective()
        if mesure != "load" or not cible:
            self.weekly_chart.set_target(None)
            return
        self.weekly_chart.set_target(cible, f"objectif {cible:.0f}")

    def _fill_weekly(self, rows: list[dict]) -> None:
        # Au-delà d'une vingtaine de colonnes, les libellés deviennent
        # illisibles : on ne garde que les semaines les plus récentes.
        recent = rows[-20:]
        self.weekly_chart.set_values(
            [(f"S{str(r['week'])[-2:]}", float(r["load"] or 0)) for r in recent],
            theme.ACCENT,
        )
