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


# Noms écrits en dur : l'application est en français partout, et se reposer
# sur la locale du système ferait afficher « Monday » sur un poste anglais.
_JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
_MOIS = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet",
         "août", "septembre", "octobre", "novembre", "décembre")


def format_long_day(day: dt.date | str | None) -> str:
    """« mardi 11 août », avec l'année si ce n'est pas celle en cours."""
    if day is None:
        return "—"
    if isinstance(day, str):
        try:
            day = dt.date.fromisoformat(day[:10])
        except ValueError:
            return day
    if isinstance(day, dt.datetime):
        day = day.date()
    texte = f"{_JOURS[day.weekday()]} {day.day} {_MOIS[day.month - 1]}"
    return texte if day.year == dt.date.today().year else f"{texte} {day.year}"


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


class TrendChart(QWidget):
    """Séries quotidiennes empilées, lisibles au point près.

    La version précédente traçait une courbe nue, sans repère ni valeur : on
    voyait une forme sans jamais savoir de quel jour ni de quel chiffre il
    s'agissait. Trois manques comblés ici :

    * chaque bande porte son minimum et son maximum, et la dernière valeur ;
    * un repère vertical suit le curseur, aligné sur toutes les séries à la
      fois — c'est la comparaison entre elles qui a du sens, pas la lecture
      d'une seule ;
    * un encart donne la date pointée et la valeur de chaque mesure ce
      jour-là, formatée dans son unité.

    Les séries n'ont pas la même échelle — des bpm et des heures ne se
    superposent pas — d'où une bande par mesure plutôt qu'un axe commun.

    L'axe porte soit des dates, soit des heures : le même graphe sert à
    suivre un mois et à dérouler une seule journée relevé par relevé.
    """

    BAND_HEIGHT = 82
    BAND_SPACING = 40
    LABEL_WIDTH = 132
    RIGHT_MARGIN = 12
    AXIS_HEIGHT = 22

    # Teintes des plages de fond : une nuit et une séance ne se confondent pas.
    SPAN_COLOURS = {"sleep": theme.SERIES[4], "activity": theme.ACCENT}

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._days: list[str] = []
        self._series: list[dict] = []
        self._spans: list[dict] = []
        self._mode = "day"
        self._hover: int | None = None
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # -- données ---------------------------------------------------------

    def set_data(
        self,
        days: list[str],
        series: list[dict],
        mode: str = "day",
        spans: list[dict] | None = None,
    ) -> None:
        """`series` : dicts {label, values, colour, unit} — unit formate la valeur.

        `mode` vaut `day` (étiquettes ISO) ou `time` (« HH:MM »). `spans`
        décrit des plages à ombrer, en indices fractionnaires de l'axe.
        """
        self._days = list(days)
        self._mode = mode
        self._spans = list(spans or [])
        # Une série entièrement vide n'apprend rien et volerait de la hauteur.
        self._series = [s for s in series if any(v is not None for v in s["values"])]
        self._hover = None
        self.setMinimumHeight(
            max(1, len(self._series)) * (self.BAND_HEIGHT + self.BAND_SPACING)
            + self.AXIS_HEIGHT
            + 8
        )
        self.updateGeometry()
        self.update()

    # -- interaction -----------------------------------------------------

    def _index_at(self, x: float) -> int | None:
        if len(self._days) < 2:
            return None
        left, width = self.LABEL_WIDTH, self._plot_width()
        if width <= 0:
            return None
        ratio = (x - left) / width
        index = round(ratio * (len(self._days) - 1))
        return min(max(index, 0), len(self._days) - 1)

    def _plot_width(self) -> float:
        return self.width() - self.LABEL_WIDTH - self.RIGHT_MARGIN

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 — API Qt
        index = self._index_at(event.position().x())
        if index != self._hover:
            self._hover = index
            self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802 — API Qt
        self._hover = None
        self.update()

    # -- rendu -----------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 — API Qt
        if not self._series or len(self._days) < 2:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(self.font())
        metrics = QFontMetrics(self.font())
        width = self._plot_width()
        step = width / (len(self._days) - 1)

        # Les plages passent en premier : elles sont un fond, pas un calque.
        self._paint_spans(painter, metrics, step)
        for rank, serie in enumerate(self._series):
            top = rank * (self.BAND_HEIGHT + self.BAND_SPACING) + self.BAND_SPACING
            self._paint_band(painter, metrics, serie, top, step)

        self._paint_time_axis(painter, metrics, step)
        if self._hover is not None:
            self._paint_cursor(painter, metrics, step)
        painter.end()

    def _paint_spans(self, painter, metrics, step) -> None:
        """Ombre les nuits et les séances derrière toutes les bandes.

        Sur une journée, une fréquence cardiaque à 45 ne veut pas dire la même
        chose selon qu'on dormait ou qu'on était assis : sans ces repères, la
        courbe se lit à l'aveugle.
        """
        if not self._spans:
            return

        # Les intitulés occupent leur propre bandeau, tout en haut : posés à la
        # hauteur des titres de bande, ils se cognaient au premier d'entre eux,
        # qui déborde de sa colonne dès qu'il est un peu long.
        bandeau = 15.0
        bas = float(len(self._series) * (self.BAND_HEIGHT + self.BAND_SPACING))
        for span in self._spans:
            couleur = self.SPAN_COLOURS.get(span.get("kind", ""), theme.MUTED)
            gauche = self.LABEL_WIDTH + span["start"] * step
            largeur = max(2.0, (span["end"] - span["start"]) * step)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.qcolor(couleur, 26))
            painter.drawRect(QRectF(gauche, 0.0, largeur, bas))

            # L'intitulé n'apparaît que s'il tient : tronqué à deux lettres, il
            # serait plus déroutant qu'absent.
            texte = span.get("label") or ""
            if texte and metrics.horizontalAdvance(texte) + 10 <= largeur:
                painter.setPen(theme.qcolor(couleur, 210))
                painter.drawText(
                    QRectF(gauche + 5, 0.0, largeur - 8, bandeau),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    texte,
                )

    def _paint_time_axis(self, painter, metrics, step) -> None:
        """Dates sous la dernière bande.

        Sans elles, il fallait survoler chaque point pour savoir de quand on
        parle — impossible de situer un creux d'un coup d'œil.
        """
        base = len(self._series) * (self.BAND_HEIGHT + self.BAND_SPACING) + 6
        largeur_date = metrics.horizontalAdvance("00/00") + 18
        maximum = max(2, int(self._plot_width() // largeur_date))
        pas = max(1, -(-len(self._days) // maximum))
        if self._mode == "time":
            # Une graduation au milieu d'une heure ne se lit pas. On arrondit
            # le pas à un nombre entier d'heures pour retomber sur des repères
            # ronds — 00:00, 03:00, 06:00…
            par_heure = max(1, round(60 / self._minutes_per_slot()))
            pas = max(par_heure, -(-pas // par_heure) * par_heure)

        painter.setPen(QColor(theme.MUTED))
        for index in range(0, len(self._days), pas):
            x = self.LABEL_WIDTH + index * step
            texte = self._axis_label(self._days[index])
            largeur = metrics.horizontalAdvance(texte)
            # Centrée sur son point, la dernière date dépassait du cadre et
            # s'affichait amputée de son mois.
            gauche = min(x - largeur / 2, self.width() - largeur - 2)
            painter.drawText(int(gauche), int(base + metrics.ascent()), texte)

    def _minutes_per_slot(self) -> float:
        """Durée d'une case, déduite des deux premières étiquettes horaires."""
        if len(self._days) < 2:
            return 60.0
        try:
            debut = self._days[0]
            suivant = self._days[1]
            ecart = (int(suivant[:2]) * 60 + int(suivant[3:5])) - (
                int(debut[:2]) * 60 + int(debut[3:5])
            )
        except (ValueError, IndexError):
            return 60.0
        return float(ecart) if ecart > 0 else 60.0

    def _paint_band(self, painter, metrics, serie, top, step) -> None:
        values = serie["values"]
        known = [v for v in values if v is not None]
        low, high = min(known), max(known)
        span = (high - low) or 1.0
        height = self.BAND_HEIGHT

        def y_of(value: float) -> float:
            return top + height - ((value - low) / span) * height

        unit = serie["unit"]
        # Le titre se pose dans l'espace au-dessus de la bande : à l'intérieur,
        # il chevauchait la graduation haute.
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(0, int(top) - 9, serie["label"])

        # Trois graduations chiffrées plutôt que deux bornes : le milieu donne
        # l'échelle, et une courbe plate cesse de ressembler à une agitée.
        droite = int(self.width() - self.RIGHT_MARGIN)
        for fraction in (0.0, 0.5, 1.0):
            y = int(top + height * fraction)
            valeur = high - (high - low) * fraction
            grid = QPen(QColor(theme.BORDER))
            grid.setWidthF(1.0)
            if fraction == 0.5:
                grid.setStyle(Qt.PenStyle.DotLine)
            painter.setPen(grid)
            painter.drawLine(self.LABEL_WIDTH, y, droite, y)
            # Alignées à droite, contre le graphe : elles forment une colonne
            # nette, et ne se confondent plus avec le titre de la bande, qui
            # part de la gauche.
            painter.setPen(QColor(theme.MUTED))
            texte = unit(valeur)
            painter.drawText(
                self.LABEL_WIDTH - 10 - metrics.horizontalAdvance(texte),
                y + metrics.ascent() // 2 - 1,
                texte,
            )

        path = QPainterPath()
        started = False
        for index, value in enumerate(values):
            if value is None:
                # Une coupure ne doit pas être reliée : le trait suggérerait
                # une mesure qui n'existe pas.
                started = False
                continue
            x = self.LABEL_WIDTH + index * step
            y = y_of(value)
            if started:
                path.lineTo(x, y)
            else:
                path.moveTo(x, y)
                started = True

        pen = QPen(QColor(serie["colour"]))
        pen.setWidthF(2.0)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        # Sans pinceau explicite, la courbe est remplie avec celui laissé par
        # la bande précédente — le point de survol en pose un et Qt le garde.
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        if self._hover is not None and self._hover < len(values):
            value = values[self._hover]
            if value is not None:
                painter.setBrush(QColor(serie["colour"]))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(
                    QRectF(self.LABEL_WIDTH + self._hover * step - 3.5, y_of(value) - 3.5, 7, 7)
                )

    def _paint_cursor(self, painter, metrics, step) -> None:
        x = self.LABEL_WIDTH + self._hover * step
        pen = QPen(QColor(theme.MUTED))
        pen.setWidthF(1.0)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(int(x), 4, int(x), self.height() - 4)

        lignes = [self._cursor_label(self._days[self._hover])]
        for serie in self._series:
            value = serie["values"][self._hover] if self._hover < len(serie["values"]) else None
            texte = serie["unit"](value) if value is not None else "—"
            lignes.append(f"{serie['label']} : {texte}")

        largeur = max(metrics.horizontalAdvance(t) for t in lignes) + 18
        hauteur = len(lignes) * (metrics.height() + 2) + 12
        # L'encart bascule à gauche du curseur quand il déborderait du cadre.
        gauche = x + 12
        if gauche + largeur > self.width():
            gauche = x - largeur - 12
        haut = 6

        painter.setPen(QColor(theme.BORDER))
        painter.setBrush(QColor(theme.BG))
        painter.drawRoundedRect(QRectF(gauche, haut, largeur, hauteur), 6, 6)

        y = haut + metrics.ascent() + 6
        for rank, texte in enumerate(lignes):
            painter.setPen(QColor(theme.TEXT if rank == 0 else theme.MUTED))
            painter.drawText(int(gauche + 9), int(y), texte)
            y += metrics.height() + 2

    def _axis_label(self, valeur: str) -> str:
        if self._mode == "time":
            return valeur
        try:
            return dt.date.fromisoformat(valeur[:10]).strftime("%d/%m")
        except ValueError:
            return valeur

    def _cursor_label(self, valeur: str) -> str:
        if self._mode == "time":
            # La date est déjà affichée au-dessus du graphe : seule l'heure
            # manque, et on l'écrit comme on la dit.
            return f"à {valeur.replace(':', ' h ')}"
        try:
            jour = dt.date.fromisoformat(valeur[:10])
        except ValueError:
            return valeur
        return format_relative_day(jour)
