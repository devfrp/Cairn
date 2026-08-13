"""Composants d'interface réutilisés par les différentes pages."""

from __future__ import annotations

import datetime as dt

from PySide6.QtCore import QRectF, Qt, Signal
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


class LoadCalendar(QWidget):
    """Une case par jour, teintée selon la charge — une année d'un coup d'œil.

    Les colonnes sont les semaines, les lignes les jours : la lecture verticale
    donne la semaine, l'horizontale la régularité. Ce que la charge par semaine
    ne montre pas, c'est où sont les coupures et si l'entraînement s'est
    concentré sur deux jours ou étalé sur cinq.

    Les teintes sont discrètes plutôt que continues : l'œil compare mal deux
    nuances proches, et cinq paliers se lisent sans échelle.
    """

    GAP = 3
    LEFT = 26
    TOP = 16
    PALIERS = 4

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._days: list[str] = []
        self._loads: list[float] = []
        self._hover: int | None = None
        self.setMouseTracking(True)
        # La hauteur découle de la largeur : les cases sont carrées et se
        # serrent quand la fenêtre rétrécit. C'est le mécanisme prévu par Qt
        # pour cela — s'en remettre à `resizeEvent` laissait la mise en page
        # calculer la hauteur avant d'avoir donné la largeur, et la dernière
        # ligne de jours se peignait hors du cadre.
        politique = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        politique.setHeightForWidth(True)
        self.setSizePolicy(politique)

    def set_data(self, days: list[str], loads: list[float | None]) -> None:
        self._days = list(days)
        self._loads = [float(v or 0) for v in loads]
        self._hover = None
        self.update()

    # -- géométrie -------------------------------------------------------

    def _first_column_offset(self) -> int:
        """Jour de la semaine du premier jour affiché, lundi valant zéro."""
        if not self._days:
            return 0
        try:
            return dt.date.fromisoformat(self._days[0][:10]).weekday()
        except ValueError:
            return 0

    def _columns(self) -> int:
        return max(1, -(-(len(self._days) + self._first_column_offset()) // 7))

    def _cell_for(self, width: int) -> float:
        # La case s'ajuste à la largeur disponible plutôt que d'imposer la
        # sienne : une année tient dans la fenêtre quelle que soit sa taille.
        utile = max(1, width - self.LEFT - 4)
        return max(4.0, min(15.0, utile / self._columns() - self.GAP))

    def _cell(self) -> float:
        return self._cell_for(self.width())

    def _height_for(self, cote: float) -> int:
        return int(self.TOP + 7 * (cote + self.GAP) + 4)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 — API Qt
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 — API Qt
        return self._height_for(self._cell_for(width))

    def _rect_of(self, index: int) -> QRectF:
        cote = self._cell()
        pas = cote + self.GAP
        position = index + self._first_column_offset()
        return QRectF(
            self.LEFT + (position // 7) * pas,
            self.TOP + (position % 7) * pas,
            cote,
            cote,
        )

    def _index_at(self, x: float, y: float) -> int | None:
        for index in range(len(self._days)):
            if self._rect_of(index).adjusted(0, 0, self.GAP, self.GAP).contains(x, y):
                return index
        return None

    def sizeHint(self):  # noqa: N802 — API Qt
        from PySide6.QtCore import QSize

        return QSize(self.width() or 720, self._height_for(self._cell()))

    def minimumSizeHint(self):  # noqa: N802 — API Qt
        from PySide6.QtCore import QSize

        return QSize(self.LEFT + self._columns() * 5, self._height_for(4.0))

    # -- interaction -----------------------------------------------------

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 — API Qt
        index = self._index_at(event.position().x(), event.position().y())
        if index != self._hover:
            self._hover = index
            if index is not None:
                self.setToolTip(
                    f"{format_long_day(self._days[index])} — "
                    f"charge {self._loads[index]:.0f}"
                )
            else:
                self.setToolTip("")
            self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802 — API Qt
        self._hover = None
        self.update()

    # -- rendu -----------------------------------------------------------

    def _tone(self, load: float, maximum: float) -> QColor:
        if load <= 0:
            return QColor(theme.SURFACE_ALT)
        palier = min(self.PALIERS, max(1, int(load / maximum * self.PALIERS + 0.999)))
        # Le palier le plus haut doit rester en deçà de l'opacité maximale :
        # 255 est la borne, et la dépasser d'un cran fait crier Qt.
        return theme.qcolor(theme.ACCENT, 60 + palier * 48)

    def paintEvent(self, event) -> None:  # noqa: N802 — API Qt
        if not self._days:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font = painter.font()
        font.setPointSizeF(max(7.0, font.pointSizeF() - 3))
        painter.setFont(font)
        metrics = QFontMetrics(font)

        maximum = max(self._loads, default=0) or 1.0
        cote = self._cell()

        # Initiales des jours, une ligne sur deux pour ne pas surcharger.
        painter.setPen(QColor(theme.MUTED))
        for ligne, initiale in enumerate(("L", "", "M", "", "V", "", "D")):
            if initiale:
                painter.drawText(
                    2,
                    int(self.TOP + ligne * (cote + self.GAP) + cote / 2 + metrics.ascent() / 2),
                    initiale,
                )

        mois_vu = ""
        for index, jour in enumerate(self._days):
            rect = self._rect_of(index)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self._tone(self._loads[index], maximum))
            painter.drawRoundedRect(rect, 2, 2)

            if index == self._hover:
                painter.setPen(QPen(QColor(theme.TEXT), 1.2))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect, 2, 2)

            # Étiquette du mois au-dessus de sa première semaine.
            mois = jour[:7]
            if mois != mois_vu and dt.date.fromisoformat(jour[:10]).day <= 7:
                mois_vu = mois
                painter.setPen(QColor(theme.MUTED))
                painter.drawText(int(rect.left()), int(self.TOP - 4), _MOIS_COURTS[
                    int(jour[5:7]) - 1
                ])
        painter.end()


# Trois lettres suffisent au-dessus d'une colonne de quinze pixels.
_MOIS_COURTS = ("jan", "fév", "mar", "avr", "mai", "juin",
                "juil", "août", "sep", "oct", "nov", "déc")


class TrackMap(QWidget):
    """Tracé d'une sortie, dessiné à partir des seules coordonnées.

    Sans fond de carte : ni tuiles, ni clé d'API, ni appel réseau. Cairn tient
    dans son dossier et fonctionne hors ligne — aller chercher des images
    ailleurs contredirait les deux. La forme du parcours suffit d'ailleurs à
    le reconnaître, et c'est le point survolé qui apporte l'information.

    La longitude est resserrée par le cosinus de la latitude : un degré de
    longitude vaut deux tiers de degré de latitude sous nos latitudes, et
    l'ignorer étire le parcours d'une bonne moitié en largeur.
    """

    MARGIN = 10
    point_hovered = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._points: list[tuple[float, float] | None] = []
        self._marker: int | None = None
        self.setMinimumHeight(200)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_track(self, points: list[tuple[float | None, float | None]]) -> None:
        self._points = [
            (float(lat), float(lon)) if lat is not None and lon is not None else None
            for lat, lon in points
        ]
        self._marker = None
        self.update()

    def has_track(self) -> bool:
        return sum(1 for p in self._points if p is not None) >= 2

    def set_marker(self, index: int | None) -> None:
        if index != self._marker:
            self._marker = index
            self.update()

    # -- projection ------------------------------------------------------

    def _transform(self) -> tuple[float, float, float, float, float] | None:
        """Échelle et décalage pour inscrire le tracé dans le cadre."""
        connus = [p for p in self._points if p is not None]
        if len(connus) < 2:
            return None

        import math

        lats = [p[0] for p in connus]
        lons = [p[1] for p in connus]
        cos_lat = max(0.05, math.cos(math.radians(sum(lats) / len(lats))))

        largeur_geo = (max(lons) - min(lons)) * cos_lat
        hauteur_geo = max(lats) - min(lats)
        # Un aller-retour sur une ligne droite a une emprise nulle dans un des
        # deux sens : sans plancher, l'échelle partirait à l'infini.
        largeur_geo = max(largeur_geo, 1e-6)
        hauteur_geo = max(hauteur_geo, 1e-6)

        utile_x = max(1.0, self.width() - 2 * self.MARGIN)
        utile_y = max(1.0, self.height() - 2 * self.MARGIN)
        # Une seule échelle pour les deux axes : deux échelles déformeraient
        # le parcours, et une boucle ne ressemblerait plus à rien.
        echelle = min(utile_x / largeur_geo, utile_y / hauteur_geo)

        decalage_x = self.MARGIN + (utile_x - largeur_geo * echelle) / 2
        decalage_y = self.MARGIN + (utile_y - hauteur_geo * echelle) / 2
        return echelle, cos_lat, min(lons), max(lats), (decalage_x, decalage_y)

    def _screen(self, point: tuple[float, float], transform) -> tuple[float, float]:
        echelle, cos_lat, lon_min, lat_max, (dx, dy) = transform
        x = dx + (point[1] - lon_min) * cos_lat * echelle
        # La latitude croît vers le nord, l'ordonnée de l'écran vers le bas.
        y = dy + (lat_max - point[0]) * echelle
        return x, y

    # -- interaction -----------------------------------------------------

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 — API Qt
        transform = self._transform()
        if transform is None:
            return
        cible = (event.position().x(), event.position().y())
        meilleur, distance_min = None, 400.0   # vingt pixels, au carré
        for index, point in enumerate(self._points):
            if point is None:
                continue
            x, y = self._screen(point, transform)
            distance = (x - cible[0]) ** 2 + (y - cible[1]) ** 2
            if distance < distance_min:
                meilleur, distance_min = index, distance
        if meilleur != self._marker:
            self.set_marker(meilleur)
            self.point_hovered.emit(-1 if meilleur is None else meilleur)

    def leaveEvent(self, event) -> None:  # noqa: N802 — API Qt
        self.set_marker(None)
        self.point_hovered.emit(-1)

    # -- rendu -----------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 — API Qt
        transform = self._transform()
        if transform is None:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        started = False
        premier = dernier = None
        for point in self._points:
            if point is None:
                # Un tunnel ou une perte de signal ne se relie pas par une
                # ligne droite : elle inventerait un passage jamais emprunté.
                started = False
                continue
            x, y = self._screen(point, transform)
            if started:
                path.lineTo(x, y)
            else:
                path.moveTo(x, y)
                started = True
            if premier is None:
                premier = (x, y)
            dernier = (x, y)

        pen = QPen(QColor(theme.ACCENT))
        pen.setWidthF(2.2)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        painter.setPen(Qt.PenStyle.NoPen)
        if premier:
            painter.setBrush(QColor(theme.OK))
            painter.drawEllipse(QRectF(premier[0] - 4, premier[1] - 4, 8, 8))
        if dernier:
            painter.setBrush(QColor(theme.DANGER))
            painter.drawEllipse(QRectF(dernier[0] - 4, dernier[1] - 4, 8, 8))

        if self._marker is not None and self._marker < len(self._points):
            point = self._points[self._marker]
            if point is not None:
                x, y = self._screen(point, transform)
                painter.setPen(QPen(QColor(theme.TEXT), 2.0))
                painter.setBrush(QColor(theme.BG))
                painter.drawEllipse(QRectF(x - 5.5, y - 5.5, 11, 11))
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
    # Largeur minimale de la colonne de gauche. La vraie est calculée à chaque
    # jeu de données : « Distance à l'entraînement » débordait de la colonne
    # et venait s'écrire par-dessus le graphe.
    LABEL_WIDTH = 132
    LABEL_MAX = 220
    RIGHT_MARGIN = 12
    AXIS_HEIGHT = 22

    # Teintes des plages de fond : une nuit et une séance ne se confondent pas.
    SPAN_COLOURS = {"sleep": theme.SERIES[4], "activity": theme.ACCENT}

    # Émis quand on clique un jour du graphe, pour l'ouvrir en détail.
    day_clicked = Signal(str)
    # Indice survolé, ou -1 : sert à faire suivre le tracé d'une sortie.
    point_hovered = Signal(int)

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

        metrics = QFontMetrics(self.font())
        self.LABEL_WIDTH = min(
            self.LABEL_MAX,
            max(
                type(self).LABEL_WIDTH,
                max(
                    (metrics.horizontalAdvance(s["label"]) + 14 for s in self._series),
                    default=0,
                ),
            ),
        )
        self.setCursor(
            Qt.CursorShape.PointingHandCursor
            if mode == "day"
            else Qt.CursorShape.ArrowCursor
        )
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
            self.point_hovered.emit(-1 if index is None else index)
            self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802 — API Qt
        """Un clic sur une journée demande à l'ouvrir en détail.

        Repérer un creux puis devoir retrouver sa date à la main dans un
        sélecteur était le chaînon manquant entre les deux vues.
        """
        index = self._index_at(event.position().x())
        if self._mode == "day" and index is not None:
            self.day_clicked.emit(self._days[index])

    def leaveEvent(self, event) -> None:  # noqa: N802 — API Qt
        self._hover = None
        self.point_hovered.emit(-1)
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
        # Une bande peut porter une seconde courbe, à la même échelle : c'est
        # ce qui permet de superposer deux sorties. Les bornes se calculent
        # alors sur les deux, sans quoi l'une sortirait du cadre.
        seconde = serie.get("values2")
        known = [v for v in values if v is not None]
        if seconde:
            known += [v for v in seconde if v is not None]
        if not known:
            return
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

        if seconde:
            chemin = QPainterPath()
            commence = False
            for index, value in enumerate(seconde):
                if value is None:
                    commence = False
                    continue
                x = self.LABEL_WIDTH + index * step
                if commence:
                    chemin.lineTo(x, y_of(value))
                else:
                    chemin.moveTo(x, y_of(value))
                    commence = True
            stylo = QPen(QColor(serie.get("colour2") or theme.MUTED))
            stylo.setWidthF(2.0)
            stylo.setStyle(Qt.PenStyle.DashLine)
            stylo.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(stylo)
            painter.drawPath(chemin)

        if self._hover is not None:
            x = self.LABEL_WIDTH + self._hover * step
            for rang_courbe, (serie_values, couleur) in enumerate((
                (values, serie["colour"]),
                (seconde, serie.get("colour2") or theme.MUTED),
            )):
                if not serie_values or self._hover >= len(serie_values):
                    continue
                value = serie_values[self._hover]
                if value is None:
                    continue
                painter.setBrush(QColor(couleur))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(QRectF(x - 3.5, y_of(value) - 3.5, 7, 7))
                # La seconde valeur se pose de l'autre côté du curseur :
                # deux mesures voisines s'y recouvriraient.
                self._paint_value_tag(
                    painter, metrics, x, y_of(value), unit(value), couleur,
                    a_gauche=bool(rang_courbe),
                )

    def _paint_value_tag(self, painter, metrics, x, y, texte, couleur,
                         a_gauche: bool = False) -> None:
        """Valeur posée contre son propre point, dans sa propre bande.

        L'encart unique qui les rassemblait toutes grandissait avec le nombre
        de mesures : à dix bandes, il masquait le tiers du graphe, et il
        fallait faire l'aller-retour entre une ligne de texte et une courbe
        pour savoir laquelle valait quoi.
        """
        largeur = metrics.horizontalAdvance(texte) + 12
        hauteur = metrics.height() + 4
        gauche = x - largeur - 10 if a_gauche else x + 10
        if gauche + largeur > self.width() - 2:
            gauche = x - largeur - 10
        if gauche < 2:
            gauche = x + 10

        painter.setPen(theme.qcolor(couleur, 150))
        painter.setBrush(theme.qcolor(theme.BG, 235))
        painter.drawRoundedRect(QRectF(gauche, y - hauteur / 2, largeur, hauteur), 4, 4)

        painter.setPen(QColor(theme.TEXT))
        painter.drawText(
            QRectF(gauche, y - hauteur / 2, largeur, hauteur),
            Qt.AlignmentFlag.AlignCenter,
            texte,
        )

    def _paint_cursor(self, painter, metrics, step) -> None:
        """Trait vertical et date. Les valeurs, elles, sont dans les bandes."""
        x = self.LABEL_WIDTH + self._hover * step
        pen = QPen(QColor(theme.MUTED))
        pen.setWidthF(1.0)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawLine(int(x), 4, int(x), self.height() - 4)

        # Sur l'axe des dates plutôt qu'en tête du graphe : c'est là qu'on
        # cherche une date, et la pastille y recouvre les graduations
        # voisines, qu'elle remplace le temps du survol.
        texte = self._cursor_label(self._days[self._hover])
        largeur = metrics.horizontalAdvance(texte) + 16
        hauteur = metrics.height() + 6
        gauche = min(max(x - largeur / 2, 2.0), self.width() - largeur - 2)
        haut = len(self._series) * (self.BAND_HEIGHT + self.BAND_SPACING) + 4

        painter.setPen(QColor(theme.BORDER))
        painter.setBrush(QColor(theme.BG))
        painter.drawRoundedRect(QRectF(gauche, haut, largeur, hauteur), 5, 5)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(
            QRectF(gauche, haut, largeur, hauteur), Qt.AlignmentFlag.AlignCenter, texte
        )

    def _axis_label(self, valeur: str) -> str:
        if self._mode in ("time", "plain"):
            return valeur
        try:
            return dt.date.fromisoformat(valeur[:10]).strftime("%d/%m")
        except ValueError:
            return valeur

    def _cursor_label(self, valeur: str) -> str:
        if self._mode == "plain":
            return valeur
        if self._mode == "time":
            # La date est déjà affichée au-dessus du graphe : seule l'heure
            # manque, et on l'écrit comme on la dit.
            return f"à {valeur.replace(':', ' h ')}"
        try:
            jour = dt.date.fromisoformat(valeur[:10])
        except ValueError:
            return valeur
        return format_relative_day(jour)
