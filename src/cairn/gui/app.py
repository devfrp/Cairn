"""Fenêtre principale et démarrage de l'application graphique."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from cairn.gui import prefs, queries, theme
from cairn.gui.pages.activities import ActivitiesPage
from cairn.gui.pages.dashboard import DashboardPage
from cairn.gui.pages.settings_page import SettingsPage
from cairn.gui.pages.sync import SyncPage
from cairn.gui.widgets import StatusPill
from cairn.gui.workers import Worker

# Vérification périodique de la base : assez rare pour ne rien peser, assez
# fréquente pour que la pastille d'état reste crédible.
HEALTH_INTERVAL_MS = 30_000

# L'interface préfère un diagnostic rapide à une tentative obstinée.
GUI_DB_TIMEOUT_S = "3"


def icon_path() -> Path | None:
    """Icône de l'application, empaquetée ou lue depuis le dépôt."""
    bundled = getattr(sys, "_MEIPASS", None)
    candidates = (
        Path(bundled) / "cairn.svg" if bundled else None,
        Path(__file__).resolve().parents[3] / "packaging" / "cairn.svg",
    )
    for candidate in candidates:
        if candidate and candidate.exists():
            return candidate
    return None


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Cairn")
        self.resize(1180, 780)
        self.setMinimumSize(940, 640)

        path = icon_path()
        if path:
            self.setWindowIcon(QIcon(str(path)))

        self._probe: Worker | None = None

        self.pages = QStackedWidget()
        self.dashboard = DashboardPage()
        self.activities = ActivitiesPage()
        self.sync = SyncPage()
        self.settings = SettingsPage()

        for page in (self.dashboard, self.activities, self.sync, self.settings):
            self.pages.addWidget(page)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        body = QWidget()
        from PySide6.QtWidgets import QHBoxLayout

        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self._build_sidebar())
        body_layout.addWidget(self.pages, 1)
        layout.addWidget(body)

        self.setCentralWidget(container)

        self.status_pill = StatusPill("vérification…", "idle")
        self.statusBar().addPermanentWidget(self.status_pill)
        self.statusBar().showMessage(prefs.describe_location())

        self.sync.finished_run.connect(self.refresh)
        self.settings.settings_changed.connect(self.refresh)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(HEALTH_INTERVAL_MS)

        # Premier rafraîchissement après affichage : sonder une base
        # injoignable prend quelques secondes, autant que la fenêtre soit
        # déjà à l'écran.
        QTimer.singleShot(80, self.refresh)

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(216)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 18, 14, 18)
        layout.setSpacing(4)

        title = QLabel("Cairn")
        title.setObjectName("SidebarTitle")
        layout.addWidget(title)

        subtitle = QLabel("Garmin + Strava, chez vous")
        subtitle.setObjectName("SidebarSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)

        entries = (
            ("Tableau de bord", 0),
            ("Activités", 1),
            ("Synchronisation", 2),
            ("Réglages", 3),
        )
        for label, index in entries:
            button = QPushButton(label)
            button.setObjectName("NavButton")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _=False, i=index: self._go(i))
            self._nav_group.addButton(button, index)
            layout.addWidget(button)

        self._nav_group.button(0).setChecked(True)
        layout.addStretch(1)

        hint = QLabel(
            "Les graphiques détaillés restent dans Grafana ; cette fenêtre "
            "sert à piloter et à voir l'essentiel."
        )
        hint.setObjectName("SidebarSubtitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        return sidebar

    def _go(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        self.refresh()

    def refresh(self) -> None:
        """Sonde la base sans bloquer l'affichage.

        Une base injoignable met plusieurs secondes à le dire. Fait dans le
        fil principal, chaque rafraîchissement figerait la fenêtre — y compris
        les rafraîchissements automatiques, ce qui rendrait l'application
        inutilisable justement quand quelque chose ne va pas.
        """
        if self._probe is not None:
            return

        probe = Worker(queries.probe, self)
        self._probe = probe
        probe.succeeded.connect(self._apply_health)
        probe.failed.connect(
            lambda message: self._apply_health(queries.Health(detail=message))
        )
        probe.done.connect(self._probe_done)
        probe.start()

    def _probe_done(self) -> None:
        self._probe = None

    def _apply_health(self, health: queries.Health) -> None:
        if health.usable:
            self.status_pill.setText("base connectée")
            self.status_pill.set_tone("ok")
        elif health.reachable:
            self.status_pill.setText("schéma absent")
            self.status_pill.set_tone("warn")
        else:
            self.status_pill.setText("base injoignable")
            self.status_pill.set_tone("danger")

        current = self.pages.currentWidget()
        if hasattr(current, "refresh"):
            current.refresh(health)


def selftest() -> int:
    """Vérifie ce que contient réellement le paquet, puis sort.

    Appelé par la chaîne de compilation : un paquet qui ne transporte pas son
    moteur ne doit pas être publié comme s'il en avait un.
    """
    from cairn.insights import embedded
    from cairn.migrate import _migrations_dir

    migrations = sorted(_migrations_dir().glob("*.sql"))
    print(f"migrations : {len(migrations)} — {', '.join(p.stem for p in migrations)}")
    if not migrations:
        return 1

    binary = embedded.binary_path()
    model = embedded.bundled_model()
    print(f"moteur embarqué : {binary or 'absent'}")
    print(f"modèle embarqué : {model or 'absent'}")

    if binary is None or model is None:
        print("→ paquet sans moteur local : le résumé exigera une adresse Ollama.")
        return 2

    print("→ paquet complet.")
    return 0


def main() -> int:
    """Point d'entrée de l'application graphique."""
    import os

    if "--selftest" in sys.argv:
        return selftest()

    os.environ.setdefault("CAIRN_DB_TIMEOUT", GUI_DB_TIMEOUT_S)

    app = QApplication(sys.argv)
    app.setApplicationName("Cairn")
    app.setOrganizationName("Cairn")
    app.setStyleSheet(theme.STYLESHEET)

    path = icon_path()
    if path:
        app.setWindowIcon(QIcon(str(path)))

    prefs.apply_to_environment()

    from cairn.logging_setup import configure

    configure("INFO")

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
