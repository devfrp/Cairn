"""Page d'accueil : récupération d'un côté, charge de l'autre.

C'est le croisement que ni Garmin ni Strava ne montre, donc c'est ce que
l'application affiche en premier.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from cairn.gui import queries, theme
from cairn.gui.widgets import (
    Card,
    SectionHeader,
    Sparkline,
    StatCard,
    StatusPill,
    format_distance,
    format_duration,
    format_elevation,
    format_relative_day,
)
from cairn.gui.workers import Worker

# Seuils d'interprétation de la variation de charge d'une semaine à l'autre.
# Au-delà de +30 %, la progression est plus rapide que ce qu'on conseille
# habituellement ; en dessous de -30 %, c'est une semaine de coupure.
LOAD_SURGE_PCT = 30.0


class DashboardPage(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._summary_worker: Worker | None = None

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

        # --- tendances ---
        trends = Card()
        trend_title = QLabel("Tendances sur 30 jours")
        trend_title.setObjectName("SectionTitle")
        trends.layout().addWidget(trend_title)

        self.hrv_spark = Sparkline()
        self.rhr_spark = Sparkline()
        self.load_spark = Sparkline()

        for caption, spark, colour in (
            ("Variabilité cardiaque", self.hrv_spark, theme.SERIES[1]),
            ("FC de repos", self.rhr_spark, theme.SERIES[2]),
            ("Charge quotidienne", self.load_spark, theme.ACCENT),
        ):
            label = QLabel(caption)
            label.setObjectName("CardHint")
            trends.layout().addWidget(label)
            spark.set_values([], colour)
            trends.layout().addWidget(spark)

        layout.addWidget(trends)

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

        layout.addWidget(self._build_summary())

        self.empty_notice = QLabel()
        self.empty_notice.setObjectName("PageHint")
        self.empty_notice.setWordWrap(True)
        self.empty_notice.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_notice.hide()
        layout.addWidget(self.empty_notice)

        layout.addStretch(1)

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

    def refresh(self, health: queries.Health) -> None:
        if not health.usable:
            self._show_unavailable(health)
            return

        self.empty_notice.hide()
        recovery = queries.recovery()
        load = queries.load()

        self._fill_recovery(recovery)
        self._fill_load(load)

        self.hrv_spark.set_values(recovery.hrv_series, theme.SERIES[1])
        self.rhr_spark.set_values(recovery.rhr_series, theme.SERIES[2])
        self.load_spark.set_values(load.daily_series, theme.ACCENT)

        if not any(v for v in load.daily_series) and recovery.hrv is None:
            self.empty_notice.setText(
                "Aucune donnée pour l'instant. Lancez une synchronisation "
                "depuis l'onglet « Synchronisation »."
            )
            self.empty_notice.show()

    def _fill_recovery(self, recovery: queries.Recovery) -> None:
        if recovery.hrv is None:
            self.hrv.set_value("—", "Pas encore de mesure")
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

        if recovery.sleep_score is None:
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
            hint = f"{load.week_sessions} sortie(s)"
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
            f"{format_distance(load.week_distance_m)} · {load.week_sessions} sortie(s)",
        )
        self.week_elevation.set_value(format_elevation(load.week_elevation_m), "cumulé")
        self.month_load.set_value(f"{load.month_load:.0f}", "sur 4 semaines")

    def _show_unavailable(self, health: queries.Health) -> None:
        for card in (self.hrv, self.sleep, self.rhr, self.battery,
                     self.week_load, self.week_volume, self.week_elevation,
                     self.month_load):
            card.set_value("—", "")
        for spark in (self.hrv_spark, self.rhr_spark, self.load_spark):
            spark.set_values([])

        message = (
            "Base joignable, mais le schéma n'est pas installé. "
            "Ouvrez « Réglages » pour l'appliquer."
            if health.reachable
            else "Base de données injoignable. Vérifiez la connexion dans « Réglages »."
        )
        self.empty_notice.setText(message)
        self.empty_notice.show()
