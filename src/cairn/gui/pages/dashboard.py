"""Page d'accueil : récupération d'un côté, charge de l'autre.

C'est le croisement que ni Garmin ni Strava ne montre, donc c'est ce que
l'application affiche en premier.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from cairn.gui import prefs, queries, theme
from cairn.gui.widgets import (
    Card,
    SectionHeader,
    StatCard,
    StatusPill,
    TrendChart,
    format_distance,
    format_duration,
    format_elevation,
    format_long_day,
    format_relative_day,
)
from cairn.gui.workers import Worker
from cairn.text import pluriel

# Seuils d'interprétation de la variation de charge d'une semaine à l'autre.
# Au-delà de +30 %, la progression est plus rapide que ce qu'on conseille
# habituellement ; en dessous de -30 %, c'est une semaine de coupure.
LOAD_SURGE_PCT = 30.0


# --- champs traçables -------------------------------------------------


def _entier(v: float) -> str:
    return f"{v:.0f}"


def _signe(v: float) -> str:
    return f"{v:+.0f}"


def _bpm(v: float) -> str:
    return f"{v:.0f} bpm"


def _ms(v: float) -> str:
    return f"{v:.0f} ms"


def _minutes(v: float) -> str:
    return f"{v:.0f} min"


def _kcal(v: float) -> str:
    return f"{v:.0f} kcal"


def _par_minute(v: float) -> str:
    return f"{v:.0f} /min"


def _pourcent(v: float) -> str:
    return f"{v:.0f} %"


def _kmh(v: float) -> str:
    return f"{v:.1f}".replace(".", ",") + " km/h"


def _watts(v: float) -> str:
    return f"{v:.0f} W"


def _ratio(v: float) -> str:
    return f"{v:.2f}".replace(".", ",")


def _stade(v: float) -> str:
    """Profondeur du sommeil rendue en clair.

    L'axe est numérique pour que la courbe s'étage comme un hypnogramme ; les
    graduations, elles, doivent nommer le stade et non afficher « 1,5 ».
    """
    noms = ("profond", "léger", "paradoxal", "éveillé")
    return noms[min(max(int(round(v)), 0), len(noms) - 1)]


def _pas(v: float) -> str:
    # Espace insécable entre les milliers : « 18 434 » se lit d'un coup,
    # « 18434 » se compte.
    return f"{v:,.0f}".replace(",", "\u00a0")


# Les formats généraux rendent « — » pour une valeur nulle, ce qui convient à
# une carte vide mais pas à une graduation : la borne basse d'une bande vaut
# souvent zéro, et c'est alors zéro qu'il faut lire.
def _duree(v: float) -> str:
    return format_duration(v) if v else "0 min"


def _distance(v: float) -> str:
    return format_distance(v) if v else "0 km"


@dataclass(frozen=True)
class Champ:
    """Une mesure traçable : d'où elle vient, comment elle s'écrit."""

    key: str
    label: str
    groupe: str
    colour: str
    unit: Callable[[float], str]


# Tout ce que la base sait tenir jour par jour. Le tableau de bord n'en montre
# que cinq par défaut — au-delà, le graphe devient un mur — mais la montre en
# relève bien plus, et il n'y a pas de raison de les garder inaccessibles.
CHAMPS_JOUR: tuple[Champ, ...] = (
    Champ("avg_hrv_ms", "Variabilité cardiaque", "Récupération", theme.SERIES[4], _ms),
    Champ("resting_hr", "FC de repos", "Récupération", theme.SERIES[2], _bpm),
    Champ("min_hr", "FC minimale", "Récupération", theme.SERIES[9], _bpm),
    Champ("max_hr", "FC maximale", "Récupération", theme.SERIES[6], _bpm),
    Champ("hr_amplitude", "Amplitude cardiaque", "Récupération", theme.SERIES[0], _bpm),
    Champ("body_battery_high", "Body Battery (haut)", "Récupération", theme.SERIES[3], _entier),
    Champ("body_battery_low", "Body Battery (bas)", "Récupération", theme.SERIES[8], _entier),
    Champ("body_battery_charged", "Body Battery rechargée", "Récupération",
          theme.SERIES[1], _entier),
    Champ("body_battery_drained", "Body Battery dépensée", "Récupération",
          theme.SERIES[6], _entier),
    Champ("avg_stress", "Stress moyen", "Récupération", theme.SERIES[7], _entier),
    Champ("max_stress", "Stress maximal", "Récupération", theme.DANGER, _entier),
    Champ("high_stress_s", "Temps de stress élevé", "Récupération", theme.DANGER, _duree),
    Champ("rest_stress_s", "Temps au repos", "Récupération", theme.SERIES[1], _duree),
    Champ("avg_respiration", "Respiration", "Récupération", theme.SERIES[5], _par_minute),
    Champ("sleep_s", "Sommeil", "Sommeil", theme.SERIES[1], _duree),
    Champ("deep_s", "Sommeil profond", "Sommeil", theme.SERIES[5], _duree),
    Champ("rem_s", "Sommeil paradoxal", "Sommeil", theme.SERIES[4], _duree),
    Champ("light_s", "Sommeil léger", "Sommeil", theme.SERIES[9], _duree),
    Champ("awake_s", "Éveils", "Sommeil", theme.SERIES[6], _duree),
    Champ("sleep_efficiency", "Efficacité du sommeil", "Sommeil", theme.OK, _pourcent),
    Champ("deep_share", "Part de sommeil profond", "Sommeil", theme.SERIES[5], _pourcent),
    Champ("sleep_hr", "FC nocturne moyenne", "Sommeil", theme.SERIES[2], _bpm),
    Champ("sleep_low_hr", "FC nocturne minimale", "Sommeil", theme.SERIES[8], _bpm),
    Champ("sleep_respiration", "Respiration nocturne", "Sommeil", theme.SERIES[7], _par_minute),
    Champ("steps", "Pas", "Journée", theme.SERIES[1], _pas),
    Champ("walk_distance_m", "Distance parcourue", "Journée", theme.SERIES[5], _distance),
    Champ("calories_active", "Calories actives", "Journée", theme.SERIES[3], _kcal),
    Champ("calories_bmr", "Calories au repos", "Journée", theme.SERIES[8], _kcal),
    Champ("calories_total", "Calories totales", "Journée", theme.SERIES[0], _kcal),
    Champ("intensity_min", "Minutes d'intensité", "Journée", theme.SERIES[6], _minutes),
    Champ("load", "Charge quotidienne", "Entraînement", theme.ACCENT, _entier),
    Champ("session_count", "Nombre de séances", "Entraînement", theme.SERIES[7], _entier),
    Champ("sport_s", "Temps d'entraînement", "Entraînement", theme.SERIES[1], _duree),
    Champ("moving_s", "Temps en mouvement", "Entraînement", theme.SERIES[5], _duree),
    Champ("sport_distance_m", "Distance à l'entraînement", "Entraînement",
          theme.SERIES[2], _distance),
    Champ("elevation_m", "Dénivelé positif", "Entraînement", theme.SERIES[8], format_elevation),
    Champ("descent_m", "Dénivelé négatif", "Entraînement", theme.SERIES[9], format_elevation),
    Champ("max_speed_kmh", "Vitesse maximale", "Entraînement", theme.SERIES[6], _kmh),
    Champ("avg_power_w", "Puissance moyenne", "Entraînement", theme.SERIES[4], _watts),
    Champ("sport_calories", "Calories des séances", "Entraînement", theme.SERIES[3], _kcal),
    Champ("ctl", "Condition (CTL)", "Charge", theme.SERIES[5], _entier),
    Champ("atl", "Fatigue (ATL)", "Charge", theme.SERIES[6], _entier),
    Champ("tsb", "Forme (TSB)", "Charge", theme.SERIES[3], _signe),
    Champ("acwr", "Rapport aigu / chronique", "Charge", theme.SERIES[2], _ratio),
    Champ("monotony", "Monotonie", "Charge", theme.SERIES[7], _ratio),
    Champ("strain", "Contrainte", "Charge", theme.SERIES[0], _entier),
)

# Sur une seule journée, seules les mesures relevées en continu ont du sens :
# une durée de sommeil ne se trace pas minute par minute. Trois sources s'y
# rejoignent — la montre au fil des heures, les stades de la nuit, et le
# fichier seconde par seconde des séances.
CHAMPS_JOURNEE: tuple[Champ, ...] = (
    Champ("hr", "Fréquence cardiaque", "En continu", theme.SERIES[2], _bpm),
    Champ("stress", "Stress", "En continu", theme.SERIES[7], _entier),
    Champ("body_battery", "Body Battery", "En continu", theme.SERIES[3], _entier),
    Champ("sleep_stage", "Stade de sommeil", "Nuit", theme.SERIES[4], _stade),
    Champ("speed_kmh", "Vitesse", "Séances", theme.SERIES[1], _kmh),
    Champ("altitude_m", "Altitude", "Séances", theme.SERIES[8], format_elevation),
    Champ("cadence", "Cadence", "Séances", theme.SERIES[6], _par_minute),
)

DEFAUT_JOUR = ("avg_hrv_ms", "resting_hr", "sleep_s", "body_battery_high", "load")
DEFAUT_JOURNEE = ("hr", "stress", "body_battery", "sleep_stage")

# Sélections qui furent un jour le réglage d'usine. Les retrouver telles
# quelles signifie que l'utilisateur n'a jamais touché au menu : on lui donne
# alors le nouveau défaut plutôt que de lui cacher les mesures ajoutées depuis.
DEFAUTS_ANCIENS = {
    "champs_journee": {("body_battery", "hr", "stress")},
}

# Au-delà de cette longueur, un menu plat déborde de l'écran : il se replie
# alors en sous-menus, un par famille de mesures.
SEUIL_SOUS_MENUS = 12

# Libellés courts des pièces, pour tenir sur une ligne d'alerte.
PIECES_COURTES = {
    "chain": "la chaîne",
    "cassette": "la cassette",
    "chainring": "le plateau",
    "tyre_front": "le pneu avant",
    "tyre_rear": "le pneu arrière",
    "brake_pads": "les plaquettes",
    "fork_service": "la fourche",
    "shock_service": "l'amortisseur",
    "shoes": "les semelles",
}


class DashboardPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._summary_worker: Worker | None = None
        self._metrics = queries.DailyMetrics()
        self._intra = queries.Intraday()
        # Jour affiché en vue « 1 j ». None : le dernier que la base connaisse.
        self._jour: str | None = None
        self._champs_jour = self._champs_retenus("champs_jour", CHAMPS_JOUR, DEFAUT_JOUR)
        self._champs_journee = self._champs_retenus(
            "champs_journee", CHAMPS_JOURNEE, DEFAUT_JOURNEE
        )
        self._sous_menus: dict[str, QMenu] = {}

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
                "Tableau de bord",
                "La récupération du matin en regard de l'entraînement des sept derniers jours.",
            )
        )

        # Bandeau de signaux, en tête : c'est ce qui doit sauter aux yeux.
        # Une ligne par signal, chacune avec son bouton : un bandeau qu'on ne
        # peut pas faire taire finit par ne plus être lu.
        self.signals_card = Card()
        self.signals_rows = QVBoxLayout()
        self.signals_rows.setContentsMargins(0, 0, 0, 0)
        self.signals_rows.setSpacing(4)
        self.signals_card.layout().addLayout(self.signals_rows)
        self.signals_card.hide()
        layout.addWidget(self.signals_card)

        # --- récupération ---
        self.hrv = StatCard("Variabilité cardiaque")
        self.sleep = StatCard("Sommeil")
        self.rhr = StatCard("FC de repos")
        self.battery = StatCard("Body battery")

        recovery_grid = QGridLayout()
        recovery_grid.setSpacing(14)
        for index, card in enumerate((self.hrv, self.sleep, self.rhr, self.battery)):
            recovery_grid.addWidget(card, 0, index)
        layout.addLayout(recovery_grid)

        # --- journée en cours ---
        self.steps = StatCard("Pas")
        self.stress = StatCard("Stress moyen")
        self.calories = StatCard("Calories actives")
        self.intensity = StatCard("Minutes d'intensité")

        day_grid = QGridLayout()
        day_grid.setSpacing(14)
        for index, card in enumerate(
            (self.steps, self.stress, self.calories, self.intensity)
        ):
            day_grid.addWidget(card, 0, index)
        layout.addLayout(day_grid)

        # --- tendances ---
        layout.addWidget(self._build_trends())

        # --- charge ---
        self.week_load = StatCard("Charge sur 7 jours")
        self.week_volume = StatCard("Volume sur 7 jours")
        self.week_elevation = StatCard("Dénivelé sur 7 jours")
        self.month_load = StatCard("Charge sur 28 jours")

        load_grid = QGridLayout()
        load_grid.setSpacing(14)
        for index, card in enumerate(
            (self.week_load, self.week_volume, self.week_elevation, self.month_load)
        ):
            load_grid.addWidget(card, 0, index)
        layout.addLayout(load_grid)

        # --- charge chronique et forme ---
        self.ctl = StatCard("Condition (CTL)")
        self.atl = StatCard("Fatigue (ATL)")
        self.tsb = StatCard("Forme (TSB)")
        self.acwr = StatCard("Rapport aigu / chronique")

        fitness_grid = QGridLayout()
        fitness_grid.setSpacing(14)
        for index, card in enumerate((self.ctl, self.atl, self.tsb, self.acwr)):
            fitness_grid.addWidget(card, 0, index)
        layout.addLayout(fitness_grid)

        layout.addWidget(self._build_summary())

        self.empty_notice = QLabel()
        self.empty_notice.setObjectName("PageHint")
        self.empty_notice.setWordWrap(True)
        self.empty_notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_notice.hide()
        layout.addWidget(self.empty_notice)

        layout.addStretch(1)

    # -- graphe de tendances -------------------------------------------

    def _build_trends(self) -> Card:
        trends = Card()

        entete = QHBoxLayout()
        titre = QLabel("Tendances")
        titre.setObjectName("SectionTitle")
        entete.addWidget(titre)
        entete.addStretch(1)

        # Le choix des mesures tracées appartient à l'utilisateur : elles sont
        # une trentaine et personne n'a le même usage du même appareil.
        self.btn_champs = QPushButton("Champs")
        self.btn_champs.setCursor(Qt.CursorShape.PointingHandCursor)
        self._menu_champs = QMenu(self)
        self.btn_champs.setMenu(self._menu_champs)
        entete.addWidget(self.btn_champs)

        # Sur trente jours les points se serrent au point de masquer une nuit
        # blanche isolée ; sur sept, chacun se lit ; sur une seule journée, la
        # montre livre ses relevés de cinq minutes en cinq minutes. Le choix
        # revient donc à l'utilisateur plutôt qu'à une constante.
        self._trend_days = 30
        self._period_buttons = {}
        for libelle, jours in (("1 j", 1), ("7 j", 7), ("30 j", 30),
                               ("90 j", 90), ("1 an", 365)):
            bouton = QPushButton(libelle)
            bouton.setCheckable(True)
            bouton.setChecked(jours == self._trend_days)
            bouton.setCursor(Qt.CursorShape.PointingHandCursor)
            bouton.clicked.connect(lambda _=False, d=jours: self._set_trend_days(d))
            self._period_buttons[jours] = bouton
            entete.addWidget(bouton)
        trends.layout().addLayout(entete)

        # Navigation d'un jour à l'autre, propre à la vue « 1 j ».
        self.day_bar = QWidget()
        barre = QHBoxLayout(self.day_bar)
        barre.setContentsMargins(0, 0, 0, 0)
        barre.setSpacing(8)

        self.btn_prev_day = QPushButton("◀")
        self.btn_prev_day.setFixedWidth(38)
        self.btn_prev_day.clicked.connect(lambda: self._shift_day(-1))
        self.btn_next_day = QPushButton("▶")
        self.btn_next_day.setFixedWidth(38)
        self.btn_next_day.clicked.connect(lambda: self._shift_day(1))

        self.btn_today = QPushButton("Aujourd'hui")
        self.btn_today.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_today.clicked.connect(self._open_today)

        self.day_label = QLabel("—")
        self.day_label.setObjectName("SectionTitle")

        barre.addWidget(self.btn_prev_day)
        barre.addWidget(self.btn_next_day)
        barre.addWidget(self.btn_today)
        barre.addWidget(self.day_label)
        barre.addStretch(1)
        self.day_bar.hide()
        trends.layout().addWidget(self.day_bar)

        self.trend_hint = QLabel("")
        self.trend_hint.setObjectName("CardHint")
        trends.layout().addWidget(self.trend_hint)

        self.trend_chart = TrendChart()
        self.trend_chart.day_clicked.connect(self._open_day)
        trends.layout().addWidget(self.trend_chart)

        # Les flèches font défiler les jours sans quitter le clavier. Portée
        # limitée à la page : ailleurs, elles doivent rester au défilement.
        for touche, pas in ((Qt.Key.Key_Left, -1), (Qt.Key.Key_Right, 1)):
            raccourci = QShortcut(QKeySequence(touche), self)
            raccourci.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            raccourci.activated.connect(lambda d=pas: self._shift_day(d))

        self._rebuild_field_menu()
        return trends

    def _catalogue(self) -> tuple[Champ, ...]:
        return CHAMPS_JOURNEE if self._trend_days == 1 else CHAMPS_JOUR

    def _selection(self) -> list[str]:
        return self._champs_journee if self._trend_days == 1 else self._champs_jour

    @staticmethod
    def _champs_retenus(
        cle: str, catalogue: tuple[Champ, ...], defaut: tuple[str, ...]
    ) -> list[str]:
        """Sélection persistée, filtrée sur ce que le catalogue connaît encore.

        Une clé disparue d'une version à l'autre ne doit pas faire échouer le
        chargement, seulement s'effacer. À l'inverse, une sélection identique
        à un ancien réglage d'usine n'en est pas une : elle date d'avant les
        mesures ajoutées depuis, et les cacher serait les rendre introuvables.
        """
        connus = {c.key for c in catalogue}
        stocke = [k for k in prefs.load_view(cle).split(",") if k in connus]
        if not stocke or tuple(sorted(stocke)) in DEFAUTS_ANCIENS.get(cle, set()):
            return list(defaut)
        return stocke

    def _rebuild_field_menu(self) -> None:
        """Reconstruit le menu : le catalogue change avec la période."""
        self._menu_champs.clear()
        self._sous_menus = {}
        catalogue = self._catalogue()
        selection = set(self._selection())

        if len(catalogue) <= SEUIL_SOUS_MENUS:
            groupe_courant = ""
            for champ in catalogue:
                if champ.groupe != groupe_courant:
                    groupe_courant = champ.groupe
                    self._menu_champs.addSection(champ.groupe)
                self._menu_champs.addAction(self._field_action(champ, selection))
        else:
            # Un par famille, avec le compte des mesures cochées : sans lui,
            # il faudrait ouvrir les cinq sous-menus pour savoir ce qui est
            # tracé.
            for groupe in dict.fromkeys(c.groupe for c in catalogue):
                champs = [c for c in catalogue if c.groupe == groupe]
                sous_menu = self._menu_champs.addMenu(groupe)
                self._sous_menus[groupe] = sous_menu
                for champ in champs:
                    sous_menu.addAction(self._field_action(champ, selection))

        self._refresh_menu_counts()

    def _refresh_menu_counts(self) -> None:
        """Réécrit les compteurs sans reconstruire le menu.

        Reconstruire supprimerait l'action en train d'émettre son signal.
        """
        selection = set(self._selection())
        for groupe, sous_menu in self._sous_menus.items():
            coches = sum(
                1 for c in self._catalogue() if c.groupe == groupe and c.key in selection
            )
            sous_menu.setTitle(f"{groupe} ({coches})" if coches else groupe)
        self.btn_champs.setText(f"Champs ({len(selection)})")

    def _field_action(self, champ: Champ, selection: set[str]) -> QAction:
        action = QAction(champ.label, self._menu_champs)
        action.setCheckable(True)
        # Coché avant le branchement : sinon l'état initial se propage comme un
        # clic et réécrit les préférences à chaque ouverture de la page.
        action.setChecked(champ.key in selection)
        action.toggled.connect(lambda coche, k=champ.key: self._toggle_field(k, coche))
        return action

    def _toggle_field(self, key: str, coche: bool) -> None:
        selection = self._selection()
        if coche and key not in selection:
            # L'ordre du catalogue fait foi : les bandes ne doivent pas se
            # réordonner selon l'ordre où l'on a coché les cases.
            ordre = [c.key for c in self._catalogue()]
            selection.append(key)
            selection.sort(key=ordre.index)
        elif not coche and key in selection:
            selection.remove(key)

        cle = "champs_journee" if self._trend_days == 1 else "champs_jour"
        prefs.save_view(cle, ",".join(selection))
        self._refresh_menu_counts()
        self._render_chart()

    def _open_today(self) -> None:
        """Ramène la vue sur la journée en cours."""
        self._open_day(dt.date.today().isoformat())

    def _open_day(self, jour: str) -> None:
        """Bascule sur la vue détaillée du jour cliqué."""
        bornes = queries.intraday_bounds()
        if not bornes["last_day"]:
            return
        premier = dt.date.fromisoformat(bornes["first_day"])
        dernier = dt.date.fromisoformat(bornes["last_day"])
        vise = dt.date.fromisoformat(jour[:10])
        # Cliquer une journée sans relevé continu ouvrirait un graphe vide :
        # on s'arrête à la plus proche que la montre ait couverte.
        self._jour = min(max(vise, premier), dernier).isoformat()
        self._set_trend_days(1)

    def _shift_day(self, delta: int) -> None:
        """Passe au jour voisin, sans sortir de la plage relevée."""
        if self._trend_days != 1:
            return
        bornes = queries.intraday_bounds()
        if not bornes["last_day"]:
            return
        premier = dt.date.fromisoformat(bornes["first_day"])
        dernier = dt.date.fromisoformat(bornes["last_day"])
        courant = dt.date.fromisoformat(self._jour or bornes["last_day"])
        self._jour = min(max(courant + dt.timedelta(days=delta), premier), dernier).isoformat()
        self._intra = queries.intraday(self._jour)
        self._render_chart()

    def _render_chart(self) -> None:
        """Redessine à partir des séries déjà chargées, sans requête."""
        catalogue = {c.key: c for c in self._catalogue()}
        selection = self._selection()
        source = self._intra if self._trend_days == 1 else self._metrics

        series = [
            {
                "label": catalogue[key].label,
                "values": source.get(key),
                "colour": catalogue[key].colour,
                "unit": catalogue[key].unit,
            }
            for key in selection
            if key in catalogue and source.get(key)
        ]

        if self._trend_days == 1:
            spans = [
                {**span, "label": self._span_label(span)} for span in self._intra.spans
            ]
            self.trend_chart.set_data(self._intra.labels, series, mode="time", spans=spans)
            self.day_label.setText(format_long_day(self._intra.day) if self._intra.day else "—")
            self.trend_hint.setText(self._journee_hint())
            self.btn_today.setEnabled(self._intra.day != dt.date.today().isoformat())
        else:
            self.trend_chart.set_data(self._metrics.days, series)
            self.trend_hint.setText(
                "Survolez le graphe pour lire une journée en détail."
                if series
                else "Aucun champ sélectionné — choisissez une mesure dans « Champs »."
            )

    @staticmethod
    def _span_label(span: dict) -> str:
        duree = format_duration(span.get("duration_s"))
        return f"{span['label']} · {duree}" if duree != "—" else str(span["label"])

    def _journee_hint(self) -> str:
        if not self._intra.day:
            return "Aucune journée détaillée dans la base pour l'instant."

        details = []
        aujourdhui = dt.date.today().isoformat()
        if self._intra.day == aujourdhui:
            # Une journée en cours s'arrête là où la montre en est : le dire
            # évite de lire la fin de courbe comme une montre retirée.
            details.append("journée en cours")
        elif self._intra.day < aujourdhui:
            retard = (dt.date.today() - dt.date.fromisoformat(self._intra.day)).days
            if retard > 1:
                details.append(f"il y a {pluriel(retard, 'jour')}")

        details.append(f"{_pas(self._intra.readings)} relevés")
        if any(s["kind"] == "sleep" for s in self._intra.spans):
            details.append("sommeil ombré en violet")
        sorties = sum(1 for s in self._intra.spans if s["kind"] == "activity")
        if sorties:
            details.append(f"{pluriel(sorties, 'séance')} en orange")
        return " · ".join(details) + " — survolez pour lire une minute précise."

    # -- résumé hebdomadaire -------------------------------------------

    def _build_summary(self) -> Card:
        card = Card()

        header = QHBoxLayout()
        title = QLabel("Résumé de la semaine")
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        header.addStretch(1)

        self.summary_origin = StatusPill("", "idle")
        self.summary_origin.hide()
        header.addWidget(self.summary_origin)

        self.btn_summary = QPushButton("Générer")
        self.btn_summary.setToolTip(
            "Les chiffres sont calculés par Cairn ; le modèle local ne fait "
            "que les rédiger. Tout nombre qu'il inventerait est rejeté."
        )
        self.btn_summary.clicked.connect(self._generate_summary)
        header.addWidget(self.btn_summary)
        card.layout().addLayout(header)

        self.summary_text = QLabel(
            "Aucun résumé pour l'instant. « Générer » établit le bilan de la "
            "dernière semaine complète."
        )
        self.summary_text.setObjectName("CardHint")
        self.summary_text.setWordWrap(True)
        self.summary_text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        card.layout().addWidget(self.summary_text)
        return card

    def _generate_summary(self) -> None:
        if self._summary_worker is not None:
            return

        from cairn.insights import weekly

        self.btn_summary.setEnabled(False)
        self.btn_summary.setText("Génération…")
        self.summary_origin.hide()

        worker = Worker(weekly.generate, self)
        self._summary_worker = worker
        worker.succeeded.connect(self._show_summary)
        worker.failed.connect(
            lambda message: self.summary_text.setText(f"Échec : {message}")
        )
        worker.done.connect(self._summary_done)
        worker.start()

    def _show_summary(self, result: object) -> None:
        if not isinstance(result, dict):
            return
        self.summary_text.setText(result["text"])

        if result["rejected_numbers"]:
            # L'utilisateur doit savoir que le modèle a déraillé et que le
            # texte affiché vient du repli déterministe.
            self.summary_origin.setText("chiffres rejetés")
            self.summary_origin.set_tone("warn")
            self.summary_origin.setToolTip(
                "Le modèle a cité des valeurs absentes du bilan ("
                + ", ".join(result["rejected_numbers"])
                + "). Le texte affiché est la rédaction de secours."
            )
        elif result["source"] == "model":
            self.summary_origin.setText(str(result["model"]))
            self.summary_origin.set_tone("ok")
            self.summary_origin.setToolTip(
                f"Rédigé par le modèle local en {result['latency_ms']} ms."
            )
        else:
            self.summary_origin.setText("sans modèle")
            self.summary_origin.set_tone("idle")
            self.summary_origin.setToolTip(
                result.get("error") or "Ollama n'a pas été sollicité."
            )
        self.summary_origin.show()

    def _summary_done(self) -> None:
        self._summary_worker = None
        self.btn_summary.setEnabled(True)
        self.btn_summary.setText("Générer")

    # -----------------------------------------------------------------

    def _set_trend_days(self, jours: int) -> None:
        """Change la fenêtre du graphe et redemande les séries."""
        self._trend_days = jours
        for valeur, bouton in self._period_buttons.items():
            bouton.setChecked(valeur == jours)
        self.day_bar.setVisible(jours == 1)
        # Le catalogue de mesures n'est pas le même d'une échelle à l'autre.
        self._rebuild_field_menu()
        self.refresh(queries.probe())

    def refresh(self, health: queries.Health) -> None:
        if not health.usable:
            self._show_unavailable(health)
            return

        self.empty_notice.hide()
        # Les cartes du haut donnent le dernier état connu : sur la vue d'une
        # seule journée, une fenêtre d'un jour les viderait alors que rien
        # n'a changé de leur côté.
        fenetre = max(self._trend_days, 7)
        recovery = queries.recovery(fenetre)
        load = queries.load(fenetre)

        self._fill_recovery(recovery)
        self._fill_load(load)
        self._fill_fitness(queries.fitness())
        self._fill_signals(queries.active_flags())

        self._metrics = queries.daily_metrics(fenetre)
        self._fill_day(self._metrics)
        if self._trend_days == 1:
            self._intra = queries.intraday(self._jour)
        self._render_chart()

        if not any(v for v in load.daily_series) and recovery.hrv is None:
            self.empty_notice.setText(
                "Aucune donnée pour l'instant. Lancez une synchronisation "
                "depuis l'onglet « Synchronisation »."
            )
            self.empty_notice.show()

    @staticmethod
    def _dernier(valeurs: list, jours: list[str]) -> tuple[float | None, str | None]:
        """Dernière valeur réellement mesurée, et le jour d'où elle vient.

        La journée en cours n'est remontée qu'en fin de synchronisation : sans
        ce repli, quatre cartes resteraient vides toute la matinée.
        """
        for index in range(len(valeurs) - 1, -1, -1):
            if valeurs[index] is not None:
                jour = jours[index] if index < len(jours) else None
                return valeurs[index], jour
        return None, None

    def _fill_day(self, metrics: queries.DailyMetrics) -> None:
        """Dépense et tension du dernier jour relevé."""
        cartes = (
            (self.steps, "steps", _pas, "pas"),
            (self.stress, "avg_stress", _entier, "sur 100"),
            (self.calories, "calories_active", _kcal, "hors métabolisme de base"),
            (self.intensity, "intensity_min", _minutes, "modérée et soutenue"),
        )
        for carte, cle, format_valeur, precision in cartes:
            valeur, jour = self._dernier(metrics.get(cle), metrics.days)
            if valeur is None:
                carte.set_value("—", "Pas encore de mesure")
                continue
            detail = precision
            if jour:
                detail += f" · {format_relative_day(dt.date.fromisoformat(jour))}"
            carte.set_value(format_valeur(valeur), detail)

    def _fill_recovery(self, recovery: queries.Recovery) -> None:
        if recovery.hrv is None:
            # « Pas encore » laisse espérer une mesure à venir. Si d'autres
            # relevés de la même période sont arrivés, c'est que la montre ne
            # mesure simplement pas la variabilité : autant le dire.
            releve = any(v is not None for v in recovery.rhr_series)
            self.hrv.set_value(
                "—",
                "non fournie par votre montre" if releve else "Pas encore de mesure",
            )
        else:
            status = (recovery.hrv_status or "").lower()
            colour = {
                "balanced": theme.OK,
                "unbalanced": theme.WARN,
                "low": theme.DANGER,
                "poor": theme.DANGER,
            }.get(status)
            self.hrv.set_value(
                f"{recovery.hrv:.0f} ms",
                f"{recovery.hrv_status or 'statut inconnu'} · "
                f"{format_relative_day(recovery.day)}",
                colour,
            )

        if recovery.sleep_score is None and recovery.sleep_duration_s:
            # Toutes les montres ne calculent pas de score. La durée mesurée
            # vaut mieux qu'un tiret : c'est elle qu'on regarde en premier.
            self.sleep.set_value(
                format_duration(recovery.sleep_duration_s),
                "dernière nuit · score non fourni par la montre",
            )
        elif recovery.sleep_score is None:
            self.sleep.set_value("—", "Pas encore de nuit enregistrée")
        else:
            colour = (
                theme.OK if recovery.sleep_score >= 80
                else theme.WARN if recovery.sleep_score >= 60
                else theme.DANGER
            )
            self.sleep.set_value(
                str(recovery.sleep_score),
                f"score · {format_duration(recovery.sleep_duration_s)} de sommeil",
                colour,
            )

        self.rhr.set_value(
            f"{recovery.resting_hr} bpm" if recovery.resting_hr else "—",
            "au repos" if recovery.resting_hr else "Pas encore de mesure",
        )

        if recovery.body_battery is None:
            self.battery.set_value("—", "Pas encore de mesure")
        else:
            hint = "maximum de la journée"
            if recovery.readiness is not None:
                hint += f" · readiness {recovery.readiness}"
            self.battery.set_value(str(recovery.body_battery), hint)

    def _fill_load(self, load: queries.Load) -> None:
        trend = load.trend_pct
        if trend is None:
            hint = pluriel(load.week_sessions, "sortie")
            colour = None
        else:
            direction = "+" if trend >= 0 else ""
            hint = f"{direction}{trend:.0f} % vs semaine précédente"
            colour = (
                theme.WARN if trend > LOAD_SURGE_PCT
                else theme.MUTED if trend < -LOAD_SURGE_PCT
                else theme.OK
            )
        self.week_load.set_value(f"{load.week_load:.0f}", hint, colour)

        self.week_volume.set_value(
            format_duration(load.week_duration_s),
            f"{format_distance(load.week_distance_m)} · "
            f"{pluriel(load.week_sessions, 'sortie')}",
        )
        self.week_elevation.set_value(format_elevation(load.week_elevation_m), "cumulé")
        self.month_load.set_value(f"{load.month_load:.0f}", "sur 4 semaines")

    def _fill_fitness(self, fitness: queries.Fitness) -> None:
        """Charge chronique, aiguë et forme.

        Les sigles physiologiques sont doublés d'un mot courant : « CTL » ne
        dit rien tant qu'on n'a pas lu la littérature.
        """
        if fitness.ctl is None:
            for card in (self.ctl, self.atl, self.tsb, self.acwr):
                card.set_value("—", "Pas encore calculé")
            return

        self.ctl.set_value(f"{fitness.ctl:.0f}", "charge encaissée sur six semaines")
        self.atl.set_value(f"{fitness.atl:.0f}", "charge des sept derniers jours")

        tsb = fitness.tsb or 0
        if tsb > 5:
            hint, colour = "reposé, prêt à encaisser", theme.OK
        elif tsb < -20:
            hint, colour = "fatigue accumulée", theme.DANGER
        elif tsb < -5:
            # « normal en bloc » venait du jargon d'entraînement — un bloc
            # étant une période de charge volontaire. Illisible pour qui ne
            # l'a pas déjà croisé.
            hint, colour = "en charge, rien d'anormal", theme.WARN
        else:
            hint, colour = "à l'équilibre", theme.OK
        self.tsb.set_value(f"{tsb:+.0f}", hint, colour)

        if fitness.acwr is None:
            self.acwr.set_value("—", "historique trop court")
        else:
            from cairn.metrics.signals import ACWR_HIGH, ACWR_LOW

            if fitness.acwr > ACWR_HIGH:
                hint, colour = "progression trop rapide", theme.DANGER
            elif fitness.acwr < ACWR_LOW:
                hint, colour = "en dessous de l'habitude", theme.MUTED
            else:
                hint, colour = "dans la zone raisonnable", theme.OK
            self.acwr.set_value(f"{fitness.acwr:.2f}", hint, colour)

    def _fill_signals(self, flags: list) -> None:
        while self.signals_rows.count():
            element = self.signals_rows.takeAt(0)
            if element.widget():
                element.widget().deleteLater()

        colours = {"alert": theme.DANGER, "warn": theme.WARN, "info": theme.MUTED}
        lignes = 0
        for signal in flags[:4]:
            self.signals_rows.addWidget(
                self._ligne_signal(
                    signal["message"],
                    colours.get(signal["severity"], theme.MUTED),
                    signal["kind"],
                )
            )
            lignes += 1

        # Une chaîne usée ne se découvre pas en consultant la page Analyse,
        # mais le jour où elle saute : l'alerte a sa place ici.
        for piece in queries.worn_components()[:3]:
            usure = piece["wear_pct"]
            nom = piece["label"] or PIECES_COURTES.get(piece["kind"], piece["kind"])
            self.signals_rows.addWidget(
                self._ligne_signal(
                    f"{nom} de {piece['gear_name']} : {usure:.0f} % de "
                    "l'intervalle d'entretien.",
                    theme.DANGER if usure >= 100 else theme.WARN,
                    None,
                )
            )
            lignes += 1

        self.signals_card.setVisible(bool(lignes))

    def _ligne_signal(self, message: str, couleur: str, kind: str | None) -> QWidget:
        """Un signal, et de quoi l'éteindre quand il est de nature à l'être.

        L'usure du matériel n'a pas de bouton : elle ne s'éteint pas d'un clic
        mais en changeant la pièce, et la faire taire ici masquerait le
        problème au lieu de le régler.
        """
        holder = QWidget()
        ligne = QHBoxLayout(holder)
        ligne.setContentsMargins(0, 0, 0, 0)
        ligne.setSpacing(8)

        pastille = QLabel("●")
        pastille.setStyleSheet(f"color: {couleur};")
        ligne.addWidget(pastille)

        texte = QLabel(message)
        texte.setObjectName("CardHint")
        texte.setWordWrap(True)
        ligne.addWidget(texte, 1)

        if kind:
            eteindre = QPushButton("Acquitter")
            eteindre.setCursor(Qt.CursorShape.PointingHandCursor)
            eteindre.setToolTip(
                "Écarte définitivement les signaux de cette nature. Le recalcul "
                "des métriques ne les régénérera pas."
            )
            eteindre.clicked.connect(lambda _=False, k=kind: self._acquitter(k))
            ligne.addWidget(eteindre)
        return holder

    def _acquitter(self, kind: str) -> None:
        queries.acknowledge_flag(kind)
        self.refresh(queries.probe())

    def _show_unavailable(self, health: queries.Health) -> None:
        self.signals_card.hide()
        for card in (self.hrv, self.sleep, self.rhr, self.battery,
                     self.steps, self.stress, self.calories, self.intensity,
                     self.week_load, self.week_volume, self.week_elevation,
                     self.month_load, self.ctl, self.atl, self.tsb, self.acwr):
            card.set_value("—", "")
        # Les courbes doivent disparaître avec les chiffres : laissées en
        # place, elles donneraient à croire que la base répond encore.
        self.trend_chart.set_data([], [])

        message = (
            "Base joignable, mais le schéma n'est pas installé. "
            "Ouvrez « Réglages » pour l'appliquer."
            if health.reachable
            else "Base de données injoignable. Vérifiez la connexion dans « Réglages »."
        )
        self.empty_notice.setText(message)
        self.empty_notice.show()
