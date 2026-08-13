"""Fenêtre principale et démarrage de l'application graphique."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
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
from cairn.gui.pages.analysis import AnalysisPage
from cairn.gui.pages.dashboard import DashboardPage
from cairn.gui.pages.settings_page import SettingsPage
from cairn.gui.pages.sync import SyncPage
from cairn.gui.widgets import StatusPill
from cairn.gui.workers import Worker

# Vérification périodique de la base : assez rare pour ne rien peser, assez
# fréquente pour que la pastille d'état reste crédible.
HEALTH_INTERVAL_MS = 30_000


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
        # La passe d'ouverture ne part qu'une fois : le sondage de la base se
        # répète toutes les trente secondes, et c'est lui qui la déclenche.
        self._auto_sync_done = False

        self.pages = QStackedWidget()
        self.dashboard = DashboardPage()
        self.activities = ActivitiesPage()
        self.analysis = AnalysisPage()
        self.sync = SyncPage()
        self.settings = SettingsPage()

        for page in (self.dashboard, self.activities, self.analysis,
                     self.sync, self.settings):
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
        self.sync.finished_run.connect(self._restore_status)
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
            ("Analyse", 2),
            ("Synchronisation", 3),
            ("Réglages", 4),
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
            "Vos données restent dans un fichier que vous contrôlez, sur "
            "cette machine."
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

        # `ensure_schema` et non `probe` : une base neuve s'installe d'elle-même
        # au premier passage, sans quoi tout le reste refuse de démarrer.
        probe = Worker(queries.ensure_schema, self)
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
            self.status_pill.setText("base illisible")
            self.status_pill.set_tone("danger")

        current = self.pages.currentWidget()
        if hasattr(current, "refresh"):
            current.refresh(health)

        if health.usable and not self._auto_sync_done:
            self._auto_sync_done = True
            self._maybe_auto_sync()

    def _maybe_auto_sync(self) -> None:
        """Passe d'ouverture, si l'utilisateur l'a demandée.

        Déclenchée après le premier sondage réussi, jamais avant : lancée au
        montage de la fenêtre, elle se serait heurtée à une base dont le schéma
        n'est pas encore installé. Elle passe par la page de synchronisation
        pour que le journal, la barre de progression et le rafraîchissement des
        autres pages fonctionnent comme pour un clic.
        """
        if not prefs.load_option("sync_au_demarrage"):
            return
        if self.sync.start_auto():
            # Sans délai d'expiration : c'est la fin de la passe qui remet le
            # message d'origine. Un message minuté aurait laissé la barre vide.
            self.statusBar().showMessage("Synchronisation en cours…")

    def _restore_status(self) -> None:
        self.statusBar().showMessage(prefs.describe_location())


def _emit(line: str) -> None:
    """Écrit une ligne, même sans console attachée.

    Compilée sans console — le cas sous Windows —, l'application a un
    `sys.stdout` à None : `print()` n'écrit alors nulle part. On passe par le
    descripteur de fichier, qui reste valide quand la sortie est redirigée.
    """
    try:
        os.write(1, (line + "\n").encode("utf-8", "replace"))
    except OSError:
        print(line)


_KEEP_ALIVE: list[object] = []


def check_crypto() -> str | None:
    """Chiffre puis déchiffre une valeur. Retourne l'erreur, ou None.

    `cryptography` s'appuie sur une bibliothèque native liée à OpenSSL. Quand
    l'OpenSSL embarqué est plus ancien que celui contre lequel elle a été
    construite, l'import échoue sur un symbole manquant — jamais à la
    compilation, toujours au premier lancement. Or c'est elle qui chiffre les
    jetons Garmin et Strava : sans elle, aucun compte ne peut être relié ni
    relu, et le paquet ne vaut rien.
    """
    try:
        from cryptography.fernet import Fernet

        key = Fernet(Fernet.generate_key())
        if key.decrypt(key.encrypt(b"cairn")) != b"cairn":
            return "chiffrement incohérent"
    except Exception as exc:  # noqa: BLE001 — paquet incomplet
        return f"{type(exc).__name__} : {exc}"
    return None


def check_webengine() -> str | None:
    """Affiche une page dans le moteur de rendu. Retourne l'erreur, ou None.

    La fenêtre de connexion Garmin est la seule façon de relier un compte
    depuis mars 2026. Or un moteur de rendu amputé — une bibliothèque Qt
    écartée de trop, un fichier de ressources oublié — se compile sans la
    moindre erreur et ne se manifeste qu'à l'usage, par une fenêtre blanche.

    La page est écrite en dur, sans accès au réseau : on vérifie le moteur,
    pas la disponibilité de Garmin.
    """
    # Toujours hors écran : cette vérification tourne pendant la compilation,
    # sur des machines sans affichage, et ne doit pas faire surgir de fenêtre
    # chez qui la lance à la main. Elle ne peut donc pas éprouver une vraie
    # surface d'affichage — c'est le rôle de `--garmin-login`, qui ouvre la
    # fenêtre pour de bon quand on le demande.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    # Les machines de compilation n'ont pas de carte graphique.
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

    try:
        # `cairn.gui.webengine` et non `garmin_login` : ce dernier tire toute
        # la chaîne Garmin, et la moindre dépendance cassée se serait affichée
        # ici comme une panne d'affichage.
        from cairn.gui.webengine import prepare_webengine

        prepare_webengine()

        from PySide6.QtWebEngineWidgets import QWebEngineView
    except Exception as exc:  # noqa: BLE001 — paquet incomplet
        return f"{type(exc).__name__} : {exc}"

    app = QApplication.instance() or QApplication([])

    # Une vue plutôt qu'une simple page : elle met en jeu la même mécanique
    # que la fenêtre de connexion, widget compris. Sans être affichée, faute
    # d'écran là où cette vérification tourne.
    view = QWebEngineView()
    view.resize(320, 240)

    outcome: dict[str, bool] = {}

    def finished(ok: bool) -> None:
        outcome["ok"] = ok
        QTimer.singleShot(0, app.quit)

    view.loadFinished.connect(finished)
    view.setHtml("<title>cairn</title><p>moteur de rendu opérationnel</p>")
    QTimer.singleShot(60_000, app.quit)
    app.exec()

    # Qt se plaint bruyamment si la page survit à son profil ; la boucle étant
    # arrêtée, les destructions différées doivent être réclamées à la main.
    _KEEP_ALIVE.append(view)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    if not outcome.get("ok"):
        return "aucune page affichée en soixante secondes"
    return None


def selftest() -> int:
    """Vérifie ce que contient réellement le paquet, puis sort.

    Appelé par la chaîne de compilation. Le code de sortie distingue deux
    situations : 1 pour un paquet inutilisable, 2 pour un paquet sans moteur
    local — amoindri, mais parfaitement publiable.
    """
    from cairn.insights import embedded
    from cairn.migrate import _migrations_dir

    migrations = sorted(_migrations_dir().glob("*.sql"))
    _emit(f"migrations : {len(migrations)} — {', '.join(p.stem for p in migrations)}")
    if not migrations:
        return 1

    binary = embedded.binary_path()
    model = embedded.bundled_model()
    _emit(f"moteur embarqué : {binary or 'absent'}")
    _emit(f"modèle embarqué : {model or 'absent'}")

    # Les deux vérifications ci-dessous portent sur des pannes qui ne se
    # voient pas à la compilation : le paquet se construit sans broncher et
    # cède au premier lancement. Chacune rend le paquet inutilisable.
    broken = False

    failure = check_crypto()
    _emit(f"chiffrement : {failure or 'opérationnel'}")
    if failure:
        _emit("→ aucun compte ne pourrait être relié ni relu.")
        broken = True

    failure = check_webengine()
    _emit(f"moteur de rendu : {failure or 'opérationnel'}")
    if failure:
        _emit("→ la fenêtre de connexion Garmin resterait blanche.")
        broken = True

    if broken:
        return 1

    if binary is None or model is None:
        _emit("→ paquet sans moteur local : le résumé exigera une adresse Ollama.")
        return 2

    _emit("→ paquet complet.")
    return 0


def garmin_login_only() -> int:
    """Ouvre la seule fenêtre de connexion Garmin, puis sort.

    Le reste de l'application n'est pas chargé. Sert à éprouver la fenêtre là
    où elle casse — c'est-à-dire dans le paquet compilé, sur la machine de
    l'utilisateur : l'auto-test, lui, se contente d'une page sans fenêtre et
    laisse passer les pannes liées à l'affichage réel.
    """
    from cairn.gui.webengine import prepare_webengine

    prepare_webengine()

    app = QApplication(sys.argv)
    app.setStyleSheet(theme.STYLESHEET)

    from cairn.gui.garmin_login import GarminLoginDialog

    _emit(f"plateforme : {app.platformName()}")
    dialog = GarminLoginDialog(get_domain())
    dialog.view.loadFinished.connect(
        lambda ok: _emit(f"page chargée : {ok} — « {dialog.view.title()} »")
    )
    accepted = dialog.exec()
    _emit(f"ticket : {dialog.ticket or 'aucun'}")
    _emit(f"service : {dialog.service or '—'}")
    return 0 if accepted else 1


def get_domain() -> str:
    prefs.apply_to_environment()
    from cairn.config import get_settings

    return get_settings().garmin_domain


def main() -> int:
    """Point d'entrée de l'application graphique."""

    if "--selftest" in sys.argv:
        return selftest()

    if "--garmin-login" in sys.argv:
        return garmin_login_only()

    # Avant QApplication, impérativement : le moteur de rendu de la fenêtre de
    # connexion Garmin exige des réglages posés à ce moment-là.
    from cairn.gui.garmin_login import prepare_webengine

    prepare_webengine()

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
