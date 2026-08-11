"""Progression sur les segments répétés.

Strava garde les temps de chaque passage mais ne montre la progression qu'aux
abonnés payants. Les données sont pourtant déjà là : deux cent dix passages sur
cent soixante-quatre segments, dont dix-neuf parcourus plusieurs fois.

Un segment vu une seule fois ne dit rien ; c'est la répétition qui permet de
comparer. La liste s'y limite donc, et le détail montre chaque temps dans
l'ordre, avec l'écart au record.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from cairn.gui import queries, theme
from cairn.gui.widgets import Card, TrendChart


def format_chrono(seconds: float | None) -> str:
    """Un temps de segment se lit en minutes et secondes, jamais en heures."""
    if not seconds:
        return "—"
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


class SegmentHistoryDialog(QDialog):
    """Tous les passages sur un segment, dans l'ordre."""

    def __init__(self, segment: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(segment["name"] or "Segment")
        self.setMinimumSize(820, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        titre = QLabel(segment["name"] or "Segment")
        titre.setObjectName("PageTitle")
        layout.addWidget(titre)

        pente = segment.get("avg_grade_pct")
        details = [f"{(segment['distance_m'] or 0) / 1000:.1f} km".replace(".", ",")]
        if pente is not None:
            details.append(f"{pente:.1f} %".replace(".", ","))
        details.append(f"{segment['passages']} passages")
        sous_titre = QLabel(" · ".join(details))
        sous_titre.setObjectName("PageHint")
        layout.addWidget(sous_titre)

        passages = queries.segment_history(segment["segment_id"])
        layout.addWidget(self._graphe(passages), 1)
        layout.addWidget(self._verdict(passages))

        boutons = QDialogButtonBox()
        boutons.addButton("Fermer", QDialogButtonBox.ButtonRole.RejectRole)
        boutons.rejected.connect(self.reject)
        layout.addWidget(boutons)

    def _graphe(self, passages: list[dict[str, Any]]) -> QWidget:
        carte = Card()
        titre = QLabel("Temps par passage")
        titre.setObjectName("SectionTitle")
        carte.layout().addWidget(titre)

        self.chart = TrendChart()
        carte.layout().addWidget(self.chart, 1)
        self.chart.set_data(
            [str(dict(p)["start_time"])[:10] for p in passages],
            [
                {
                    "label": "Temps",
                    "values": [dict(p)["elapsed_s"] for p in passages],
                    "colour": theme.ACCENT,
                    "unit": format_chrono,
                },
                {
                    "label": "FC moyenne",
                    "values": [dict(p)["avg_hr"] for p in passages],
                    "colour": theme.SERIES[2],
                    "unit": lambda v: f"{v:.0f} bpm",
                },
            ],
        )
        return carte

    def _verdict(self, passages: list[dict[str, Any]]) -> QWidget:
        """Ce que la courbe ne dit pas d'un coup d'œil : où en est le dernier."""
        temps = [dict(p)["elapsed_s"] for p in passages if dict(p)["elapsed_s"]]
        if len(temps) < 2:
            return QLabel("")

        record, dernier = min(temps), temps[-1]
        ecart = dernier - record
        if ecart <= 0:
            texte = f"Dernier passage en {format_chrono(dernier)} : c'est le record."
            couleur = theme.OK
        else:
            texte = (
                f"Dernier passage en {format_chrono(dernier)}, "
                f"soit {format_chrono(ecart)} de plus que le record "
                f"({format_chrono(record)})."
            )
            couleur = theme.MUTED
        etiquette = QLabel(texte)
        etiquette.setStyleSheet(f"color: {couleur};")
        etiquette.setWordWrap(True)
        return etiquette


class SegmentRow(QWidget):
    """Une ligne de la liste : nom, profil, record, dernier temps."""

    def __init__(self, segment: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._segment = segment

        ligne = QHBoxLayout(self)
        ligne.setContentsMargins(0, 2, 0, 2)
        ligne.setSpacing(10)

        nom = QLabel(segment["name"] or "Sans nom")
        nom.setMinimumWidth(240)
        ligne.addWidget(nom)

        pente = segment.get("avg_grade_pct")
        profil = f"{(segment['distance_m'] or 0) / 1000:.1f} km".replace(".", ",")
        if pente is not None:
            profil += f" · {pente:.1f} %".replace(".", ",")
        forme = QLabel(profil)
        forme.setObjectName("CardHint")
        forme.setMinimumWidth(130)
        ligne.addWidget(forme)

        passages = QLabel(f"{segment['passages']} passages")
        passages.setObjectName("CardHint")
        passages.setMinimumWidth(90)
        ligne.addWidget(passages)

        record = QLabel(f"record {format_chrono(segment['best_s'])}")
        record.setMinimumWidth(110)
        ligne.addWidget(record)

        # L'écart au record est l'information utile : un temps brut ne dit pas
        # si on progresse.
        ecart = (segment["last_s"] or 0) - (segment["best_s"] or 0)
        if ecart <= 0:
            texte, couleur = "record au dernier passage", theme.OK
        else:
            texte, couleur = f"+{format_chrono(ecart)} au dernier", theme.MUTED
        dernier = QLabel(texte)
        dernier.setStyleSheet(f"color: {couleur};")
        ligne.addWidget(dernier)
        ligne.addStretch(1)

        detail = QLabel("<a href='#'>détail</a>")
        detail.setTextFormat(Qt.TextFormat.RichText)
        detail.setCursor(Qt.CursorShape.PointingHandCursor)
        detail.linkActivated.connect(self._ouvrir)
        ligne.addWidget(detail)

    def _ouvrir(self) -> None:
        SegmentHistoryDialog(self._segment, self).exec()
