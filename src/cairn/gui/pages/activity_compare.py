"""Deux sorties côte à côte, alignées sur la distance parcourue.

C'est ce qu'un carnet d'entraînement sert à faire et que Cairn ne faisait
pas : savoir si le même parcours passe mieux qu'il y a deux mois.

L'axe est la distance, jamais le temps écoulé. Dès que l'une des deux fut
plus rapide, comparer à temps égal met face à face un sommet et une
mi-pente ; à distance égale, on compare deux fois le même endroit.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from cairn.gui import queries, theme
from cairn.gui.widgets import (
    Card,
    TrendChart,
    format_distance,
    format_duration,
    format_relative_day,
)

COULEUR_A = theme.ACCENT
COULEUR_B = theme.SERIES[2]


class ActivityCompareDialog(QDialog):
    """Sortie de référence à gauche, sortie comparée au choix."""

    def __init__(self, activity_id: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(1000, 720)
        self.setWindowTitle("Comparer deux sorties")

        self._reference = queries.activity_detail(activity_id) or {}
        self._candidats = queries.comparable_activities(activity_id)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        titre = QLabel("Comparer deux sorties")
        titre.setObjectName("PageTitle")
        layout.addWidget(titre)

        self.choix = QComboBox()
        for entree in self._candidats:
            self.choix.addItem(self._libelle(entree), entree["id"])
        self.choix.currentIndexChanged.connect(self._recharger)
        layout.addWidget(self._entete())

        if not self._candidats:
            vide = QLabel(
                "Aucune autre sortie du même sport avec des relevés détaillés. "
                "La comparaison a besoin des fichiers .fit des deux."
            )
            vide.setObjectName("CardHint")
            vide.setWordWrap(True)
            layout.addWidget(vide)
            layout.addStretch(1)
            layout.addWidget(self._fermeture())
            return

        layout.addWidget(self.choix)

        self.chiffres = QGridLayout()
        self.chiffres.setSpacing(10)
        layout.addLayout(self.chiffres)

        self.avertissement = QLabel("")
        self.avertissement.setObjectName("CardHint")
        self.avertissement.setWordWrap(True)
        self.avertissement.setStyleSheet(f"color: {theme.WARN};")
        self.avertissement.hide()
        layout.addWidget(self.avertissement)

        self.carte_courbes = Card()
        legende = QLabel(
            f"<span style='color:{COULEUR_A}'>━</span> référence · "
            f"<span style='color:{COULEUR_B}'>╌</span> comparée — "
            "l'axe est la distance parcourue, pas le temps."
        )
        legende.setObjectName("CardHint")
        legende.setTextFormat(Qt.TextFormat.RichText)
        self.carte_courbes.layout().addWidget(legende)

        self.chart = TrendChart()
        self.carte_courbes.layout().addWidget(self.chart, 1)
        layout.addWidget(self.carte_courbes, 1)

        layout.addWidget(self._fermeture())
        self._recharger()

    # -- construction ----------------------------------------------------

    def _fermeture(self) -> QDialogButtonBox:
        boutons = QDialogButtonBox()
        fermer = boutons.addButton("Fermer", QDialogButtonBox.ButtonRole.RejectRole)
        fermer.setCursor(Qt.CursorShape.PointingHandCursor)
        boutons.rejected.connect(self.reject)
        return boutons

    def _entete(self) -> QWidget:
        holder = QWidget()
        colonne = QVBoxLayout(holder)
        colonne.setContentsMargins(0, 0, 0, 0)
        colonne.setSpacing(2)

        nom = QLabel(self._reference.get("name") or "Sortie")
        nom.setObjectName("SectionTitle")
        nom.setStyleSheet(f"color: {COULEUR_A};")
        colonne.addWidget(nom)

        detail = QLabel(
            f"{format_distance(self._reference.get('distance_m'))} · "
            f"{format_duration(self._reference.get('elapsed_s'))} · "
            f"{format_relative_day(self._reference.get('start_time_local'))}"
        )
        detail.setObjectName("CardHint")
        colonne.addWidget(detail)
        return holder

    @staticmethod
    def _libelle(entree: dict[str, Any]) -> str:
        return (
            f"{format_relative_day(entree['start_time_local'])} — "
            f"{entree['name'] or 'sans nom'} · "
            f"{format_distance(entree['distance_m'])} · "
            f"{format_duration(entree['elapsed_s'])}"
        )

    # -- contenu ---------------------------------------------------------

    def _recharger(self) -> None:
        autre_id = self.choix.currentData()
        if not autre_id:
            return
        autre = queries.activity_detail(autre_id) or {}

        self._remplir_chiffres(autre)
        self._prevenir_si_parcours_differents(autre)
        self._remplir_courbes(self._reference["id"], autre_id)

    def _prevenir_si_parcours_differents(self, autre: dict[str, Any]) -> None:
        """Deux sorties de longueurs très différentes ne se comparent pas.

        Sans ce garde-fou, un écart de durée de trente pour cent se lirait
        comme une contre-performance alors qu'il ne dit que la différence de
        parcours.
        """
        a = self._reference.get("distance_m") or 0
        b = autre.get("distance_m") or 0
        if not a or not b:
            self.avertissement.hide()
            return
        ecart = abs(a - b) / max(a, b) * 100
        if ecart <= 10:
            self.avertissement.hide()
            return
        self.avertissement.setText(
            f"Les deux parcours diffèrent de {ecart:.0f} % en distance : les "
            "écarts de durée et de charge en découlent surtout, et non de la "
            "forme du jour."
        )
        self.avertissement.show()

    def _remplir_chiffres(self, autre: dict[str, Any]) -> None:
        while self.chiffres.count():
            element = self.chiffres.takeAt(0)
            if element.widget():
                element.widget().deleteLater()

        # Seules la vitesse et la fréquence cardiaque sont jugées. Dire qu'une
        # sortie plus longue est « mieux » n'aurait pas de sens : c'est une
        # sortie différente, pas une meilleure. Distance, durée et charge sont
        # rendues telles quelles.
        lignes = (
            ("Distance", "distance_m", format_distance, 0),
            ("Durée", "elapsed_s", format_duration, 0),
            ("Vitesse moyenne", None, None, 1),
            ("FC moyenne", "avg_hr", lambda v: f"{v:.0f} bpm", -1),
            ("Charge", "load", lambda v: f"{v:.0f}", 0),
        )
        for colonne, (libelle, cle, mise_en_forme, sens) in enumerate(lignes):
            if cle is None:
                a = self._vitesse(self._reference)
                b = self._vitesse(autre)
                mise_en_forme = lambda v: f"{v:.1f}".replace(".", ",") + " km/h"  # noqa: E731
            else:
                a = self._valeur(self._reference, cle)
                b = self._valeur(autre, cle)
            self.chiffres.addWidget(
                self._carte(libelle, a, b, mise_en_forme, sens), 0, colonne
            )

    @staticmethod
    def _valeur(detail: dict[str, Any], cle: str) -> float | None:
        if cle == "load":
            valeur = detail.get("trimp") or detail.get("training_load")
        else:
            valeur = detail.get(cle)
        return float(valeur) if valeur else None

    @staticmethod
    def _vitesse(detail: dict[str, Any]) -> float | None:
        distance = detail.get("distance_m")
        duree = detail.get("elapsed_s")
        return distance / duree * 3.6 if distance and duree else None

    def _carte(self, libelle, a, b, mise_en_forme, sens) -> Card:
        """Les deux valeurs et leur écart.

        `sens` dit quel côté est le meilleur : plus loin vaut mieux, plus
        longtemps ne veut rien dire, et une fréquence cardiaque plus basse à
        allure égale est un progrès. Zéro s'abstient de juger.
        """
        carte = Card()
        titre = QLabel(libelle)
        titre.setObjectName("CardTitle")
        carte.layout().addWidget(titre)

        valeur = QLabel(mise_en_forme(a) if a is not None else "—")
        valeur.setObjectName("CardValue")
        valeur.setStyleSheet(f"color: {COULEUR_A};")
        carte.layout().addWidget(valeur)

        comparee = QLabel(mise_en_forme(b) if b is not None else "—")
        comparee.setObjectName("CardHint")
        comparee.setStyleSheet(f"color: {COULEUR_B};")
        carte.layout().addWidget(comparee)

        ecart = QLabel("")
        ecart.setObjectName("CardHint")
        if a is not None and b:
            pourcent = (a - b) / b * 100
            couleur = theme.MUTED
            if sens:
                favorable = pourcent * sens > 0
                couleur = theme.OK if favorable else theme.WARN
            ecart.setText(f"{pourcent:+.1f} %".replace(".", ","))
            ecart.setStyleSheet(f"color: {couleur};")
        carte.layout().addWidget(ecart)
        return carte

    def _remplir_courbes(self, id_a: int, id_b: int) -> None:
        a = queries.streams_by_distance(id_a)
        b = queries.streams_by_distance(id_b)
        if not a["labels"]:
            self.chart.set_data([], [])
            return

        mesures = (
            ("Fréquence cardiaque", "hr", lambda v: f"{v:.0f} bpm"),
            ("Altitude", "altitude_m", lambda v: f"{v:.0f} m"),
            ("Vitesse", "speed_kmh", lambda v: f"{v:.1f} km/h".replace(".", ",")),
        )
        series = []
        for libelle, cle, unite in mesures:
            valeurs = a["series"].get(cle) or []
            autres = (b["series"].get(cle) or [])[: len(valeurs)]
            if not any(v is not None for v in valeurs):
                continue
            series.append({
                "label": libelle,
                "values": valeurs,
                "colour": COULEUR_A,
                "unit": unite,
                "values2": autres,
                "colour2": COULEUR_B,
            })

        # `plain` : les étiquettes sont des distances, ni des dates ni des
        # heures — le graphe doit les rendre telles quelles.
        self.chart.set_data(a["labels"], series, mode="plain")
