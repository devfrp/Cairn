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
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
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
    TrackMap,
    TrendChart,
    format_distance,
    format_duration,
    format_elevation,
    format_relative_day,
)
from cairn.text import pluriel

ZONES = {
    1: "Zone 1 · récup",
    2: "Zone 2 · endurance",
    3: "Zone 3 · tempo",
    4: "Zone 4 · seuil",
    5: "Zone 5 · maximal",
}

COULEURS_ZONES = theme.ZONES



class ActivityDetailDialog(QDialog):
    """Chiffres clés d'une sortie, puis ses courbes."""

    def __init__(self, activity_id: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(940, 640)

        self.chart: TrendChart | None = None
        self.map: TrackMap | None = None
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

        tours = queries.activity_laps(activity_id)
        if len(tours) > 1:
            # Un tour unique n'apprend rien : c'est la sortie elle-même,
            # déjà résumée par les cartes du haut.
            layout.addWidget(self._tours(tours, detail))

        flux = queries.activity_streams(activity_id)
        layout.addWidget(self._trace_et_courbes(flux), 1)

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
        if detail.get("is_race"):
            morceaux.append("course")
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
        # Le ressenti n'apparaît que s'il a été noté : une carte vide sur
        # toutes les sorties sauf quatorze n'apporte rien.
        if detail.get("perceived_exertion"):
            valeurs.append(("Ressenti", f"{detail['perceived_exertion']}/10"))

        holder = QWidget()
        grille = QGridLayout(holder)
        grille.setContentsMargins(0, 0, 0, 0)
        grille.setSpacing(10)
        for index, (libelle, valeur) in enumerate(valeurs):
            carte = Card()
            titre = QLabel(libelle)
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

    # -- tours -----------------------------------------------------------

    def _tours(self, tours: list[dict[str, Any]], detail: dict[str, Any]) -> QWidget:
        """Tableau des tours : ce qui distingue un fractionné d'une sortie continue."""
        carte = Card()
        titre = QLabel(f"Tours · {pluriel(len(tours), 'tour')}")
        titre.setObjectName("SectionTitle")
        carte.layout().addWidget(titre)

        table = QTableWidget(len(tours), 6)
        table.setHorizontalHeaderLabels(
            ["#", "Durée", "Distance", "Allure", "FC moy.", "FC max"]
        )
        table.verticalHeader().hide()
        table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        table.setAlternatingRowColors(True)
        # Sans cela, la dernière colonne s'étire sur toute la largeur restante
        # et « FC max » se retrouve à un empan de sa valeur.
        table.horizontalHeader().setStretchLastSection(False)

        # À pied, une allure en minutes par kilomètre ; à vélo, une vitesse.
        # Afficher 12 km/h à un coureur ne lui dit rien de son entraînement.
        a_pied = (detail.get("sport_family") or "") == "running"
        for ligne, tour in enumerate(tours):
            valeurs = (
                str(tour["lap_index"]),
                format_duration(tour["elapsed_s"]),
                format_distance(tour["distance_m"]),
                self._allure(tour["avg_speed_mps"], a_pied),
                self._bpm(tour["avg_hr"]),
                self._bpm(tour["max_hr"]),
            )
            for colonne, valeur in enumerate(valeurs):
                cellule = QTableWidgetItem(valeur)
                if colonne:
                    cellule.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                table.setItem(ligne, colonne, cellule)

        table.resizeColumnsToContents()
        # Une hauteur bornée : soixante-dix-huit tours pousseraient tout le
        # reste de la fenêtre hors de vue.
        hauteur = table.horizontalHeader().height() + min(len(tours), 8) * 28 + 4
        table.setMinimumHeight(hauteur)
        table.setMaximumHeight(hauteur)
        carte.layout().addWidget(table)
        return carte

    @staticmethod
    def _allure(mps: Any, a_pied: bool) -> str:
        if not mps:
            return "—"
        if not a_pied:
            return f"{mps * 3.6:.1f}".replace(".", ",") + " km/h"
        secondes = 1000 / mps
        return f"{int(secondes // 60)}:{int(secondes % 60):02d} /km"

    # -- courbes ---------------------------------------------------------

    def _trace_et_courbes(self, flux: dict[str, Any]) -> QWidget:
        """Le parcours à gauche, les courbes à droite, liés par le survol.

        C'est le couplage qui compte : une bosse sur la courbe de fréquence
        cardiaque ne dit rien tant qu'on ignore où elle s'est produite.
        """
        courbes = self._courbes(flux)
        trace = self._trace(flux)
        if trace is None:
            return courbes

        # Les courbes défilent : à quatre bandes elles dépassent la hauteur de
        # la fenêtre, et la dernière restait hors d'atteinte. Le parcours, lui,
        # est d'un seul tenant et occupe toute la hauteur disponible.
        defilement = QScrollArea()
        defilement.setWidgetResizable(True)
        defilement.setFrameShape(QScrollArea.Shape.NoFrame)
        defilement.setWidget(courbes)

        holder = QWidget()
        cote_a_cote = QHBoxLayout(holder)
        cote_a_cote.setContentsMargins(0, 0, 0, 0)
        cote_a_cote.setSpacing(14)
        cote_a_cote.addWidget(trace, 2)
        cote_a_cote.addWidget(defilement, 3)
        return holder

    def _trace(self, flux: dict[str, Any]) -> QWidget | None:
        """Parcours dessiné, ou rien si la sortie n'a pas été géolocalisée."""
        carte_widget = TrackMap()
        carte_widget.set_track(flux.get("track") or [])
        if not carte_widget.has_track():
            return None
        self.map = carte_widget

        carte = Card()
        titre = QLabel("Parcours")
        titre.setObjectName("SectionTitle")
        carte.layout().addWidget(titre)

        indication = QLabel("Départ en vert, arrivée en rouge.")
        indication.setObjectName("CardHint")
        carte.layout().addWidget(indication)
        carte.layout().addWidget(carte_widget, 1)

        # Les deux échantillonnages sont le même : un indice désigne le même
        # instant de part et d'autre, et il n'y a rien à convertir.
        if self.chart is not None:
            self.chart.point_hovered.connect(
                lambda index: carte_widget.set_marker(None if index < 0 else index)
            )
            carte_widget.point_hovered.connect(self._suivre_sur_les_courbes)
        return carte

    def _suivre_sur_les_courbes(self, index: int) -> None:
        if self.chart is None:
            return
        self.chart._hover = None if index < 0 else index
        self.chart.update()

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
