"""Suivi de l'usure du matériel.

Strava tient le kilométrage de chaque vélo et de chaque paire de chaussures.
Ce qui manque partout, c'est le compte par pièce : une chaîne se change tous
les trois mille kilomètres, pas le vélo entier.

Une pièce est donc posée à un kilométrage donné, avec son intervalle
d'entretien. L'usure se lit alors comme un pourcentage, et le remplacement
archive l'ancienne pièce plutôt que de l'effacer — l'historique dira combien de
chaînes ont tenu combien de temps.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cairn.gui import queries, theme
from cairn.gui.widgets import Card, field_row

# Pièces courantes et leur durée de vie habituelle, en kilomètres. Ce sont des
# ordres de grandeur : chacun ajuste selon sa pratique et son terrain.
PIECES = (
    ("chain", "Chaîne", 3000),
    ("cassette", "Cassette", 9000),
    ("chainring", "Plateau", 12000),
    ("tyre_front", "Pneu avant", 4000),
    ("tyre_rear", "Pneu arrière", 3000),
    ("brake_pads", "Plaquettes", 2000),
    ("fork_service", "Révision fourche", 2000),
    ("shock_service", "Révision amortisseur", 2000),
    ("shoes", "Semelles", 800),
    ("other", "Autre", None),
)

SEUIL_ALERTE = 100.0
SEUIL_ATTENTION = 80.0


class ComponentDialog(QDialog):
    """Déclaration d'une pièce, posée au kilométrage du jour."""

    def __init__(self, gear_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Ajouter une pièce — {gear_name}")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        intro = QLabel(
            "La pièce est considérée comme posée aujourd'hui, au kilométrage "
            "actuel du matériel. L'usure se comptera à partir de là."
        )
        intro.setWordWrap(True)
        intro.setObjectName("CardHint")
        layout.addWidget(intro)

        self.kind = QComboBox()
        for cle, libelle, defaut in PIECES:
            self.kind.addItem(libelle, (cle, defaut))
        self.kind.currentIndexChanged.connect(self._suggerer_intervalle)
        layout.addWidget(field_row("Pièce", self.kind))

        self.label = QLineEdit()
        self.label.setPlaceholderText("marque, modèle… (facultatif)")
        layout.addWidget(field_row("Précision", self.label))

        self.interval = QDoubleSpinBox()
        self.interval.setRange(0, 100000)
        self.interval.setSingleStep(500)
        self.interval.setSuffix(" km")
        self.interval.setSpecialValueText("pas de seuil")
        layout.addWidget(field_row("Changer tous les", self.interval))
        self._suggerer_intervalle()

        boutons = QDialogButtonBox()
        boutons.addButton("Ajouter", QDialogButtonBox.ButtonRole.AcceptRole)
        boutons.addButton("Annuler", QDialogButtonBox.ButtonRole.RejectRole)
        boutons.accepted.connect(self.accept)
        boutons.rejected.connect(self.reject)
        layout.addWidget(boutons)

    def _suggerer_intervalle(self) -> None:
        """Propose la durée de vie habituelle de la pièce choisie."""
        _, defaut = self.kind.currentData()
        self.interval.setValue(defaut or 0)

    def valeurs(self) -> tuple[str, str | None, float | None]:
        cle, _ = self.kind.currentData()
        intervalle = self.interval.value() or None
        return cle, self.label.text().strip() or None, intervalle


class GearCard(Card):
    """Un matériel, son kilométrage, et l'usure de ses pièces."""

    def __init__(self, gear: dict[str, Any], pieces: list[dict[str, Any]],
                 on_change, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._gear = gear
        self._on_change = on_change

        entete = QHBoxLayout()
        titre = QLabel(gear["name"] or "Sans nom")
        titre.setObjectName("SectionTitle")
        entete.addWidget(titre)

        total = QLabel(f"{(gear['distance_m'] or 0) / 1000:.0f} km")
        total.setObjectName("CardHint")
        entete.addWidget(total)
        entete.addStretch(1)

        ajouter = QPushButton("Ajouter une pièce")
        ajouter.setCursor(Qt.CursorShape.PointingHandCursor)
        ajouter.clicked.connect(self._ajouter)
        entete.addWidget(ajouter)
        self.layout().addLayout(entete)

        if not pieces:
            vide = QLabel(
                "Aucune pièce suivie. Déclarez-en une pour savoir quand la "
                "changer, plutôt que de compter sur la mémoire."
            )
            vide.setObjectName("CardHint")
            vide.setWordWrap(True)
            self.layout().addWidget(vide)
            return

        for piece in pieces:
            self.layout().addWidget(self._ligne(piece))

    def _ligne(self, piece: dict[str, Any]) -> QWidget:
        holder = QWidget()
        ligne = QHBoxLayout(holder)
        ligne.setContentsMargins(0, 0, 0, 0)
        ligne.setSpacing(10)

        nom = dict(PIECES_PAR_CLE).get(piece["component_kind"], piece["component_kind"])
        if piece["label"]:
            nom = f"{nom} — {piece['label']}"
        etiquette = QLabel(nom)
        etiquette.setMinimumWidth(220)
        ligne.addWidget(etiquette)

        parcourus = (piece["since_install_m"] or 0) / 1000
        intervalle = piece["service_interval_m"]
        if intervalle:
            usure = 100 * (piece["since_install_m"] or 0) / intervalle
            couleur = (
                theme.DANGER if usure >= SEUIL_ALERTE
                else theme.WARN if usure >= SEUIL_ATTENTION
                else theme.MUTED
            )
            texte = f"{parcourus:.0f} / {intervalle / 1000:.0f} km · {usure:.0f} %"
        else:
            couleur, texte = theme.MUTED, f"{parcourus:.0f} km · pas de seuil"

        valeur = QLabel(texte)
        valeur.setStyleSheet(f"color: {couleur};")
        ligne.addWidget(valeur)
        ligne.addStretch(1)

        remplacer = QPushButton("Remplacée")
        remplacer.setCursor(Qt.CursorShape.PointingHandCursor)
        remplacer.setToolTip(
            "Archive la pièce actuelle et en pose une neuve, mêmes réglages."
        )
        remplacer.clicked.connect(lambda: self._remplacer(piece["component_id"]))
        ligne.addWidget(remplacer)
        return holder

    def _ajouter(self) -> None:
        fenetre = ComponentDialog(self._gear["name"] or "matériel", self)
        if fenetre.exec() != QDialog.DialogCode.Accepted:
            return
        kind, label, intervalle = fenetre.valeurs()
        queries.add_gear_component(
            self._gear["id"], kind, label, intervalle * 1000 if intervalle else None
        )
        self._on_change()

    def _remplacer(self, component_id: int) -> None:
        queries.replace_gear_component(component_id)
        self._on_change()


PIECES_PAR_CLE = tuple((cle, libelle) for cle, libelle, _ in PIECES)
