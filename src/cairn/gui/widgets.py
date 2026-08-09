"""Composants d'interface réutilisés par les différentes pages."""

from __future__ import annotations

import datetime as dt

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from cairn.gui import theme


class Card(QFrame):
    """Conteneur encadré, brique de base des pages."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(18, 16, 18, 16)
        self._layout.setSpacing(8)

    def layout(self) -> QVBoxLayout:  # type: ignore[override]
        return self._layout


class StatCard(Card):
    """Une métrique : intitulé, valeur, et une précision facultative."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.title = QLabel(title.upper())
        self.title.setObjectName("CardTitle")
        self.value = QLabel("—")
        self.value.setObjectName("CardValue")
        self.hint = QLabel("")
        self.hint.setObjectName("CardHint")
        self.hint.setWordWrap(True)

        self._layout.addWidget(self.title)
        self._layout.addWidget(self.value)
        self._layout.addWidget(self.hint)
        self._layout.addStretch(1)
        self.setMinimumHeight(118)

    def set_value(self, text: str, hint: str = "", colour: str | None = None) -> None:
        self.value.setText(text)
        self.hint.setText(hint)
        self.value.setStyleSheet(f"color: {colour};" if colour else "")


class Sparkline(QWidget):
    """Courbe compacte sans axes, pour lire une tendance d'un coup d'œil.

    Volontairement dépouillée : les répartitions détaillées sont l'affaire de
    la page Analyse.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: list[float | None] = []
        self._colour = theme.ACCENT
        self.setMinimumHeight(46)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_values(self, values: list[float | None], colour: str | None = None) -> None:
        self._values = list(values)
        if colour:
            self._colour = colour
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 — API Qt
        known = [v for v in self._values if v is not None]
        if len(known) < 2:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        low, high = min(known), max(known)
        span = high - low or 1.0
        width = self.width()
        height = self.height() - 8
        step = width / max(1, len(self._values) - 1)

        path = QPainterPath()
        started = False
        for index, value in enumerate(self._values):
            if value is None:
                # Une coupure dans la série ne doit pas être reliée par un
                # trait droit qui suggérerait une mesure inexistante.
                started = False
                continue
            x = index * step
            y = 4 + height - ((value - low) / span) * height
            if started:
                path.lineTo(x, y)
            else:
                path.moveTo(x, y)
                started = True

        pen = QPen(QColor(self._colour))
        pen.setWidthF(2.0)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawPath(path)
        painter.end()


class StatusPill(QLabel):
    """Pastille d'état colorée."""

    TONES = {
        "ok": theme.OK,
        "warn": theme.WARN,
        "danger": theme.DANGER,
        "idle": theme.MUTED,
        "accent": theme.ACCENT,
    }

    def __init__(self, text: str = "", tone: str = "idle",
                 parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        colour = self.TONES.get(tone, theme.MUTED)
        self.setStyleSheet(
            f"color: {colour};"
            f"border: 1px solid {colour};"
            "border-radius: 9px;"
            "padding: 2px 9px;"
            "font-size: 11px;"
            "font-weight: 600;"
        )


class SectionHeader(QWidget):
    def __init__(self, title: str, hint: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        label = QLabel(title)
        label.setObjectName("PageTitle")
        layout.addWidget(label)

        if hint:
            sub = QLabel(hint)
            sub.setObjectName("PageHint")
            sub.setWordWrap(True)
            layout.addWidget(sub)


def field_row(label: str, widget: QWidget) -> QWidget:
    """Étiquette au-dessus de son champ, disposition uniforme."""
    holder = QWidget()
    layout = QVBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)

    caption = QLabel(label)
    caption.setObjectName("FieldLabel")
    layout.addWidget(caption)
    layout.addWidget(widget)
    return holder


def row(*widgets: QWidget, spacing: int = 12) -> QWidget:
    holder = QWidget()
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for widget in widgets:
        layout.addWidget(widget)
    return holder


# --- formatage -------------------------------------------------------


def format_duration(seconds: float | None) -> str:
    if not seconds:
        return "—"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    return f"{hours} h {minutes:02d}" if hours else f"{minutes} min"


def format_distance(metres: float | None) -> str:
    if not metres:
        return "—"
    # Séparateur décimal français : l'application est en français, Qt ne
    # localise pas les f-strings.
    return f"{metres / 1000:.1f}".replace(".", ",") + " km"


def format_elevation(metres: float | None) -> str:
    if metres is None:
        return "—"
    return f"{round(metres)} m"


def format_relative_day(day: dt.date | dt.datetime | None) -> str:
    """« aujourd'hui », « hier », puis la date."""
    if day is None:
        return "—"
    if isinstance(day, dt.datetime):
        day = day.date()
    delta = (dt.date.today() - day).days
    if delta == 0:
        return "aujourd'hui"
    if delta == 1:
        return "hier"
    if 2 <= delta < 7:
        return f"il y a {delta} jours"
    return day.strftime("%d/%m/%Y")


class BarBreakdown(QWidget):
    """Barres horizontales empilables : répartition d'un total.

    Sert aux zones de fréquence cardiaque et au volume par discipline, où ce
    qui compte est la proportion, pas la valeur absolue.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[tuple[str, float, str, str]] = []
        self.setMinimumHeight(40)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_rows(self, rows: list[tuple[str, float, str, str]]) -> None:
        """rows : (libellé, valeur, texte affiché à droite, couleur)."""
        self._rows = list(rows)
        self.setMinimumHeight(max(40, len(self._rows) * 30 + 6))
        self.updateGeometry()
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 — API Qt
        if not self._rows:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        largest = max((value for _, value, _, _ in self._rows), default=0) or 1

        font = painter.font()
        font.setPointSizeF(max(8.0, font.pointSizeF() - 1))
        painter.setFont(font)

        # La colonne de droite s'ajuste au texte le plus long : une largeur
        # fixe tronquait « 12 h 18 · 294,7 km ».
        metrics = QFontMetrics(font)
        label_width = 116
        value_width = max(
            (metrics.horizontalAdvance(caption) for _, _, caption, _ in self._rows),
            default=0,
        ) + 14
        track_left = label_width + 8
        track_width = max(20, self.width() - track_left - value_width)

        for index, (label, value, caption, colour) in enumerate(self._rows):
            top = index * 30
            row = QRectF(0, top, self.width(), 26)

            painter.setPen(QColor(theme.MUTED))
            painter.drawText(
                QRectF(0, top, label_width, 26),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                label,
            )

            # Rail de fond : rend lisibles les valeurs très faibles.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(theme.SURFACE_ALT))
            painter.drawRoundedRect(QRectF(track_left, top + 8, track_width, 10), 5, 5)

            filled = track_width * (value / largest)
            if filled > 0:
                painter.setBrush(QColor(colour))
                painter.drawRoundedRect(
                    QRectF(track_left, top + 8, max(4.0, filled), 10), 5, 5
                )

            painter.setPen(QColor(theme.TEXT))
            painter.drawText(
                QRectF(row.width() - value_width, top, value_width - 4, 26),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                caption,
            )

        painter.end()


class ColumnChart(QWidget):
    """Colonnes verticales, pour une charge semaine par semaine."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: list[tuple[str, float]] = []
        self._colour = theme.ACCENT
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_values(self, values: list[tuple[str, float]], colour: str | None = None) -> None:
        self._values = list(values)
        if colour:
            self._colour = colour
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 — API Qt
        if not self._values:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        font = painter.font()
        font.setPointSizeF(max(7.5, font.pointSizeF() - 2))
        painter.setFont(font)

        label_height = 18
        top_margin = 6
        plot_height = self.height() - label_height - top_margin
        largest = max((v for _, v in self._values), default=0) or 1
        slot = self.width() / len(self._values)
        bar_width = min(38.0, slot * 0.62)

        for index, (label, value) in enumerate(self._values):
            centre = slot * (index + 0.5)
            height = plot_height * (value / largest)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self._colour))
            painter.drawRoundedRect(
                QRectF(centre - bar_width / 2,
                       top_margin + plot_height - height,
                       bar_width,
                       max(2.0, height)),
                4, 4,
            )

            painter.setPen(QColor(theme.MUTED))
            painter.drawText(
                QRectF(centre - slot / 2, self.height() - label_height, slot, label_height),
                Qt.AlignmentFlag.AlignCenter,
                label,
            )

        painter.end()
