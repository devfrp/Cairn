"""Détail d'une sortie : ses relevés seconde par seconde.

Cairn télécharge le fichier `.fit` de chaque activité Garmin et en extrait des
dizaines de milliers de points — quatre-vingt-quatre mille sur cent sorties.
Rien ne les montrait : la page Activités s'arrêtait aux totaux, et tout ce
détail dormait en base.

La fenêtre réutilise le graphe des tendances. Il n'attend que des étiquettes en
abscisse, pas nécessairement des dates : on lui passe le temps écoulé depuis le
départ, et la lecture au survol fonctionne à l'identique.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from cairn.gui import queries, theme

# Une seule table de libellés pour toute l'application : la copie qui
# vivait ici a fait afficher « surf » en clé brute le jour où le sport a
# été reconnu ailleurs.
from cairn.gui.pages.activities import SPORT_LABELS
from cairn.gui.widgets import (
    Card,
    TrendChart,
    format_distance,
    format_duration,
    format_elevation,
    format_relative_day,
)

ZONES = {
    1: "Zone 1 · récup",
    2: "Zone 2 · endurance",
    3: "Zone 3 · tempo",
    4: "Zone 4 · seuil",
    5: "Zone 5 · maximal",
}

COULEURS_ZONES = ("#6b9bd2", "#5fa87a", "#d9a441", "#e8834a", "#d9614b")



class ActivityDetailDialog(QDialog):
    """Chiffres clés d'une sortie, puis ses courbes."""

    def __init__(self, activity_id: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(940, 640)

        self.chart: TrendChart | None = None
        detail = queries.activity_detail(activity_id) or {}
        self.setWindowTitle(detail.get("name") or "Sortie")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        layout.addWidget(self._entete(detail))
        layout.addWidget(self._chiffres(detail))

        zones = queries.activity_zones(activity_id)
        if zones:
            layout.addWidget(self._zones(zones))

        flux = queries.activity_streams(activity_id)
        layout.addWidget(self._courbes(flux), 1)

        boutons = QDialogButtonBox()
        fermer = boutons.addButton("Fermer", QDialogButtonBox.ButtonRole.RejectRole)
        fermer.setCursor(Qt.CursorShape.PointingHandCursor)
        boutons.rejected.connect(self.reject)
        layout.addWidget(boutons)

    # -- en-tête ---------------------------------------------------------

    def _entete(self, detail: dict[str, Any]) -> QWidget:
        holder = QWidget()
        colonne = QVBoxLayout(holder)
        colonne.setContentsMargins(0, 0, 0, 0)
        colonne.setSpacing(2)

        titre = QLabel(detail.get("name") or "Sortie sans nom")
        titre.setObjectName("PageTitle")
        colonne.addWidget(titre)

        sport = SPORT_LABELS.get(detail.get("sport"), detail.get("sport") or "—")
        morceaux = [sport, format_relative_day(detail.get("start_time_local"))]
        if detail.get("gear_name"):
            morceaux.append(detail["gear_name"])
        if detail.get("sources"):
            morceaux.append(f"vu par {detail['sources'].replace(',', ' + ')}")

        sous_titre = QLabel(" · ".join(morceaux))
        sous_titre.setObjectName("PageHint")
        colonne.addWidget(sous_titre)
        return holder

    # -- chiffres --------------------------------------------------------

    def _chiffres(self, detail: dict[str, Any]) -> QWidget:
        valeurs = [
            ("Distance", format_distance(detail.get("distance_m"))),
            ("Durée", format_duration(detail.get("elapsed_s"))),
            ("Dénivelé", format_elevation(detail.get("elevation_gain_m"))),
            ("FC moyenne", self._bpm(detail.get("avg_hr"))),
            ("FC max", self._bpm(detail.get("max_hr"))),
            ("Charge", self._entier(detail.get("trimp") or detail.get("training_load"))),
        ]

        holder = QWidget()
        grille = QGridLayout(holder)
        grille.setContentsMargins(0, 0, 0, 0)
        grille.setSpacing(10)
        for index, (libelle, valeur) in enumerate(valeurs):
            carte = Card()
            titre = QLabel(libelle.upper())
            titre.setObjectName("CardTitle")
            chiffre = QLabel(valeur)
            chiffre.setObjectName("CardValue")
            carte.layout().addWidget(titre)
            carte.layout().addWidget(chiffre)
            grille.addWidget(carte, 0, index)
        return holder

    @staticmethod
    def _bpm(value: Any) -> str:
        return f"{int(value)} bpm" if value else "—"

    @staticmethod
    def _entier(value: Any) -> str:
        return f"{value:.0f}" if value else "—"

    # -- zones -----------------------------------------------------------

    def _zones(self, zones: list[dict[str, Any]]) -> QWidget:
        """Répartition de l'effort, ce que la durée seule ne dit pas."""
        from cairn.gui.widgets import BarBreakdown

        carte = Card()
        titre = QLabel("Temps par zone de fréquence cardiaque")
        titre.setObjectName("SectionTitle")
        carte.layout().addWidget(titre)

        total = sum(z["seconds"] for z in zones) or 1
        barres = BarBreakdown()
        barres.set_rows([
            (
                ZONES.get(z["zone_index"], f"Zone {z['zone_index']}"),
                float(z["seconds"]),
                f"{format_duration(z['seconds'])} · {z['seconds'] / total * 100:.0f} %",
                COULEURS_ZONES[min(z["zone_index"], len(COULEURS_ZONES)) - 1],
            )
            for z in zones
        ])
        carte.layout().addWidget(barres)
        return carte

    # -- courbes ---------------------------------------------------------

    def _courbes(self, flux: dict[str, Any]) -> QWidget:
        carte = Card()
        titre = QLabel("Relevés de la sortie")
        titre.setObjectName("SectionTitle")
        carte.layout().addWidget(titre)

        if not flux["labels"]:
            vide = QLabel(
                "Aucun relevé détaillé pour cette sortie. Ils viennent des "
                "fichiers .fit de Garmin, à traiter depuis l'onglet "
                "« Synchronisation »."
            )
            vide.setObjectName("CardHint")
            vide.setWordWrap(True)
            carte.layout().addWidget(vide)
            return carte

        indication = QLabel(
            "Survolez pour lire un instant précis. Le temps affiché court "
            "depuis le départ."
        )
        indication.setObjectName("CardHint")
        carte.layout().addWidget(indication)

        graphe = self.chart = TrendChart()
        series = flux["series"]
        graphe.set_data(
            flux["labels"],
            [
                {
                    "label": "Fréquence cardiaque",
                    "values": series["hr"],
                    "colour": theme.SERIES[2],
                    "unit": lambda v: f"{v:.0f} bpm",
                },
                {
                    "label": "Altitude",
                    "values": series["altitude_m"],
                    "colour": theme.SERIES[1],
                    "unit": lambda v: f"{v:.0f} m",
                },
                {
                    "label": "Vitesse",
                    "values": series["speed_kmh"],
                    "colour": theme.ACCENT,
                    "unit": lambda v: f"{v:.1f} km/h".replace(".", ","),
                },
                {
                    "label": "Cadence",
                    "values": series["cadence"],
                    "colour": theme.SERIES[3],
                    "unit": lambda v: f"{v:.0f}",
                },
            ],
        )
        carte.layout().addWidget(graphe, 1)
        return carte
