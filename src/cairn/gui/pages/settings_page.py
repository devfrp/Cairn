"""Réglages : connexion à la base, comptes Garmin et Strava, mode portable."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from cairn.gui import prefs, queries, theme
from cairn.gui.widgets import Card, SectionHeader, StatusPill, field_row
from cairn.gui.workers import GarminLoginWorker, Worker
from cairn.text import pluriel


def launch_browser(url: str) -> bool:
    """Ouvre le navigateur du système sur `url`.

    Depuis une AppImage, le processus fils hérite des bibliothèques embarquées
    et le navigateur meurt aussitôt, sans que rien ne le signale. PyInstaller
    conserve les valeurs d'origine dans des variables suffixées `_ORIG` : on
    les restaure avant de lancer quoi que ce soit.
    """
    if os.environ.get("APPDIR"):
        env = QProcessEnvironment.systemEnvironment()
        for key in ("LD_LIBRARY_PATH", "PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH"):
            original = os.environ.get(f"{key}_ORIG")
            if original:
                env.insert(key, original)
            else:
                env.remove(key)
        process = QProcess()
        process.setProcessEnvironment(env)
        process.setProgram("xdg-open")
        process.setArguments([url])
        started, _pid = process.startDetached()
        if started:
            return True

    return QDesktopServices.openUrl(QUrl(url))


class StravaCredentialsDialog(QDialog):
    """Récupère l'identifiant d'application Strava, en une fois.

    Strava n'ouvre sa page d'autorisation qu'à une application déclarée : son
    adresse contient un `client_id`. Impossible d'y couper, contrairement à
    Garmin, dont Cairn emprunte l'application mobile officielle.

    Autant que cette déclaration se fasse sans quitter le flux : la page de
    Strava s'ouvre d'ici, les deux valeurs se collent ici, et la connexion
    enchaîne aussitôt.
    """

    PAGE = "https://www.strava.com/settings/api"

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Déclarer Cairn auprès de Strava")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        intro = QLabel(
            "Strava n'ouvre sa page de connexion qu'aux applications "
            "déclarées. C'est à faire une seule fois, en deux minutes.\n\n"
            "Sur la page qui va s'ouvrir, créez une application et indiquez "
            "« localhost » comme domaine de rappel. Recopiez ensuite les deux "
            "valeurs ci-dessous ; la connexion démarrera aussitôt."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        ouvrir = QPushButton("Ouvrir la page Strava")
        ouvrir.clicked.connect(lambda: launch_browser(self.PAGE))
        layout.addWidget(ouvrir)

        self.client_id = QLineEdit()
        self.client_id.setPlaceholderText("Client ID")
        layout.addWidget(field_row("Identifiant client", self.client_id))

        self.client_secret = QLineEdit()
        self.client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.client_secret.setPlaceholderText("Client Secret")
        layout.addWidget(field_row("Secret client", self.client_secret))

        buttons = QDialogButtonBox()
        valider = buttons.addButton(
            "Se connecter", QDialogButtonBox.ButtonRole.AcceptRole
        )
        buttons.addButton("Annuler", QDialogButtonBox.ButtonRole.RejectRole)
        valider.clicked.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        identifiant = self.client_id.text().strip()
        secret = self.client_secret.text().strip()
        if not identifiant or not secret:
            QMessageBox.warning(
                self, "Champs manquants",
                "Les deux valeurs sont nécessaires : Strava refuse "
                "l'autorisation sans elles.",
            )
            return

        valeurs = prefs.load()
        valeurs["strava_client_id"] = identifiant
        valeurs["strava_client_secret"] = secret
        prefs.save(valeurs)
        prefs.apply_to_environment()
        self.accept()


class SettingsPage(QWidget):
    settings_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker: Worker | GarminLoginWorker | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)

        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(16)

        layout.addWidget(
            SectionHeader(
                "Réglages",
                "Cairn se connecte à votre propre base. Rien ne transite par "
                "un service tiers.",
            )
        )

        layout.addWidget(self._build_database())
        layout.addWidget(self._build_behaviour())
        layout.addWidget(self._build_garmin())
        layout.addWidget(self._build_strava())
        layout.addWidget(self._build_summary())
        layout.addWidget(self._build_storage())
        layout.addStretch(1)

        self.load_values()

    # -- comportement ----------------------------------------------------

    def _build_behaviour(self) -> Card:
        from cairn.gui.pages.sync import AUTO_SYNC_MIN_HOURS

        card = Card()
        title = QLabel("Au démarrage")
        title.setObjectName("SectionTitle")
        card.layout().addWidget(title)

        self.auto_sync = QCheckBox("Synchroniser à l'ouverture de l'application")
        self.auto_sync.setToolTip(
            "La passe part en arrière-plan, la fenêtre reste utilisable. "
            f"Elle est ignorée si une synchronisation a réussi il y a moins "
            f"de {AUTO_SYNC_MIN_HOURS:.0f} heures."
        )
        # Enregistré au clic : cette case ne fait pas partie des réglages
        # groupés sous « Enregistrer », et l'oublier serait la rendre inerte.
        self.auto_sync.toggled.connect(
            lambda coche: prefs.save_option("sync_au_demarrage", coche)
        )
        card.layout().addWidget(self.auto_sync)

        detail = QLabel(
            "Sans cette option, les données ne changent qu'en cliquant "
            "« Synchroniser maintenant » ou par le planificateur."
        )
        detail.setObjectName("CardHint")
        detail.setWordWrap(True)
        card.layout().addWidget(detail)
        return card

    # -- base ----------------------------------------------------------

    def _build_database(self) -> Card:
        card = Card()
        title = QLabel("Données")
        title.setObjectName("SectionTitle")
        card.layout().addWidget(title)

        self.dsn = QLineEdit()
        self.dsn.setPlaceholderText("chemin du fichier cairn.db")
        card.layout().addWidget(field_row("Fichier de données", self.dsn))

        self.secret = QLineEdit()
        self.secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.secret.setPlaceholderText("clé Fernet servant à chiffrer les jetons en base")
        card.layout().addWidget(field_row("Clé de chiffrement", self.secret))

        buttons = QHBoxLayout()
        test = QPushButton("Tester la connexion")
        test.clicked.connect(self._test_connection)
        buttons.addWidget(test)

        generate = QPushButton("Générer une clé")
        generate.setToolTip(
            "Une seule fois, à la mise en place. Changer de clé rend "
            "illisibles les jetons déjà enregistrés."
        )
        generate.clicked.connect(self._generate_key)
        buttons.addWidget(generate)

        self.btn_migrate = QPushButton("Installer le schéma")
        self.btn_migrate.clicked.connect(self._run_migrations)
        buttons.addWidget(self.btn_migrate)

        buttons.addStretch(1)
        self.db_state = StatusPill("non vérifié", "idle")
        buttons.addWidget(self.db_state)
        card.layout().addLayout(buttons)

        self.db_detail = QLabel("")
        self.db_detail.setObjectName("CardHint")
        self.db_detail.setWordWrap(True)
        card.layout().addWidget(self.db_detail)
        return card

    # -- Garmin --------------------------------------------------------

    def _build_garmin(self) -> Card:
        card = Card()
        header = QHBoxLayout()
        title = QLabel("Compte Garmin")
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.garmin_state = StatusPill("non relié", "idle")
        header.addWidget(self.garmin_state)
        card.layout().addLayout(header)

        hint = QLabel(
            "Garmin n'expose pas d'API publique et refuse depuis mars 2026 "
            "les connexions qui ne viennent pas d'un navigateur. Cairn ouvre "
            "donc la page de connexion de Garmin dans une fenêtre : votre mot "
            "de passe va directement à Garmin, Cairn ne le voit pas. Il n'en "
            "récupère qu'un jeton, chiffré dans votre fichier de données, "
            "à renouveler environ une fois par an."
        )
        hint.setObjectName("CardHint")
        hint.setWordWrap(True)
        card.layout().addWidget(hint)

        buttons = QHBoxLayout()
        self.btn_garmin = QPushButton("Se connecter à Garmin")
        self.btn_garmin.setObjectName("Primary")
        self.btn_garmin.clicked.connect(self._login_garmin)
        buttons.addWidget(self.btn_garmin)
        buttons.addStretch(1)
        card.layout().addLayout(buttons)
        return card

    # -- Strava --------------------------------------------------------

    def _build_strava(self) -> Card:
        card = Card()
        header = QHBoxLayout()
        title = QLabel("Compte Strava")
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.strava_state = StatusPill("non relié", "idle")
        header.addWidget(self.strava_state)
        card.layout().addLayout(header)

        hint = QLabel(
            "Créez une application sur strava.com/settings/api en indiquant "
            "« localhost » comme domaine de rappel, puis reportez ses "
            "identifiants ici. Cairn ouvre ensuite la page d'autorisation de "
            "Strava dans une fenêtre : votre mot de passe va directement à "
            "Strava, Cairn n'en récupère qu'un jeton."
        )
        hint.setObjectName("CardHint")
        hint.setWordWrap(True)
        card.layout().addWidget(hint)

        self.strava_id = QLineEdit()
        card.layout().addWidget(field_row("Identifiant client", self.strava_id))

        self.strava_secret = QLineEdit()
        self.strava_secret.setEchoMode(QLineEdit.EchoMode.Password)
        card.layout().addWidget(field_row("Secret client", self.strava_secret))

        buttons = QHBoxLayout()
        self.btn_strava = QPushButton("Se connecter à Strava")
        self.btn_strava.setObjectName("Primary")
        self.btn_strava.clicked.connect(self._open_strava)
        buttons.addWidget(self.btn_strava)
        buttons.addStretch(1)
        card.layout().addLayout(buttons)
        return card

    # -- résumé hebdomadaire -------------------------------------------

    def _build_summary(self) -> Card:
        card = Card()
        header = QHBoxLayout()
        title = QLabel("Résumé hebdomadaire")
        title.setObjectName("SectionTitle")
        header.addWidget(title)
        header.addStretch(1)
        self.ollama_state = StatusPill("non vérifié", "idle")
        header.addWidget(self.ollama_state)
        card.layout().addLayout(header)

        hint = QLabel(
            "Cairn calcule lui-même toutes les valeurs ; le modèle ne sert "
            "qu'à les rédiger, et tout chiffre absent du bilan est rejeté. "
            "Un petit modèle suffit donc."
        )
        hint.setObjectName("CardHint")
        hint.setWordWrap(True)
        card.layout().addWidget(hint)

        from cairn.insights import embedded

        if embedded.is_available():
            bundled = QLabel(
                f"Cette version embarque son propre moteur et le modèle "
                f"<b>{embedded.bundled_model()}</b> : le résumé fonctionne "
                "sans rien installer, hors ligne. Renseigner une adresse "
                "ci-dessous fait passer Cairn sur cette instance à la place — "
                "utile si vous disposez d'un modèle plus gros."
            )
        else:
            bundled = QLabel(
                "Cette version ne contient pas de moteur local : renseignez "
                "l'adresse d'une instance Ollama pour activer le résumé."
            )
        bundled.setObjectName("CardHint")
        bundled.setWordWrap(True)
        bundled.setStyleSheet(f"color: {theme.ACCENT};")
        card.layout().addWidget(bundled)

        self.ollama_url = QLineEdit()
        self.ollama_url.setPlaceholderText("vide = moteur embarqué")
        card.layout().addWidget(
            field_row("Adresse d'une instance externe (facultatif)", self.ollama_url)
        )

        self.ollama_model = QLineEdit()
        self.ollama_model.setPlaceholderText("qwen3:0.6b")
        card.layout().addWidget(field_row("Modèle", self.ollama_model))

        self.discord_url = QLineEdit()
        self.discord_url.setEchoMode(QLineEdit.EchoMode.Password)
        self.discord_url.setPlaceholderText("https://discord.com/api/webhooks/…")
        card.layout().addWidget(
            field_row("Webhook Discord (facultatif)", self.discord_url)
        )

        buttons = QHBoxLayout()
        check = QPushButton("Vérifier le modèle")
        check.clicked.connect(self._check_ollama)
        buttons.addWidget(check)

        preview = QPushButton("Aperçu du résumé")
        preview.setToolTip("Génère le bilan de la dernière semaine sans rien envoyer.")
        preview.clicked.connect(self._preview_summary)
        buttons.addWidget(preview)
        buttons.addStretch(1)
        card.layout().addLayout(buttons)

        self.ollama_detail = QLabel("")
        self.ollama_detail.setObjectName("CardHint")
        self.ollama_detail.setWordWrap(True)
        card.layout().addWidget(self.ollama_detail)
        return card

    def _check_ollama(self) -> None:
        from cairn.config import get_settings
        from cairn.insights.ollama import OllamaClient, OllamaUnavailable

        self.save_values()
        settings = get_settings()
        try:
            client = OllamaClient(settings.ollama_url, settings.ollama_model)
        except OllamaUnavailable as exc:
            self.ollama_state.setText("non configuré")
            self.ollama_state.set_tone("idle")
            self.ollama_detail.setText(str(exc))
            return

        def task() -> tuple[bool, str]:
            return client.check()

        worker = Worker(task, self)
        worker.succeeded.connect(self._show_ollama_state)
        worker.failed.connect(lambda message: self._show_ollama_state((False, message)))
        worker.start()
        self._keepalive = worker  # évite la destruction prématurée du thread

    def _show_ollama_state(self, outcome: object) -> None:
        ok, detail = outcome  # type: ignore[misc]
        self.ollama_state.setText("disponible" if ok else "indisponible")
        self.ollama_state.set_tone("ok" if ok else "warn")
        self.ollama_detail.setText(detail)

    def _preview_summary(self) -> None:
        from cairn.insights import weekly

        def task() -> str:
            result = weekly.generate()
            origin = (
                f"modèle {result['model']}" if result["source"] == "model"
                else "rédaction sans modèle"
            )
            note = ""
            if result["rejected_numbers"]:
                note = (
                    "\n\nChiffres rejetés car absents du bilan : "
                    + ", ".join(result["rejected_numbers"])
                )
            return f"{result['text']}\n\n— {origin}{note}"

        self._start(task, "Aperçu du résumé")

    # -- stockage ------------------------------------------------------

    def _build_storage(self) -> Card:
        card = Card()
        title = QLabel("Stockage des réglages")
        title.setObjectName("SectionTitle")
        card.layout().addWidget(title)

        self.storage_detail = QLabel(prefs.describe_location())
        self.storage_detail.setObjectName("CardHint")
        self.storage_detail.setWordWrap(True)
        card.layout().addWidget(self.storage_detail)

        buttons = QHBoxLayout()
        self.btn_portable = QPushButton("Passer en mode portable")
        self.btn_portable.setToolTip(
            "Écrit les réglages dans un fichier à côté de l'application, "
            "qui devient autonome — utilisable depuis une clé USB."
        )
        self.btn_portable.clicked.connect(self._enable_portable)
        buttons.addWidget(self.btn_portable)

        # Ce fichier est tout ce que l'utilisateur possède : une année de
        # synchronisation ne se rattrape pas en un clic si le disque lâche.
        self.btn_backup = QPushButton("Sauvegarder les données")
        self.btn_backup.setToolTip(
            "Copie cohérente du fichier de données, prise même pendant que "
            "l'application travaille."
        )
        self.btn_backup.clicked.connect(self._backup)
        buttons.addWidget(self.btn_backup)

        self.btn_export = QPushButton("Exporter en CSV")
        self.btn_export.setToolTip(
            "Deux tableaux : une ligne par sortie, une ligne par jour. "
            "Lisibles par Excel, pandas ou Grafana."
        )
        self.btn_export.clicked.connect(self._export)
        buttons.addWidget(self.btn_export)

        save = QPushButton("Enregistrer les réglages")
        save.setObjectName("Primary")
        save.clicked.connect(self.save_values)
        buttons.addWidget(save)
        buttons.addStretch(1)
        card.layout().addLayout(buttons)

        warning = QLabel(
            "La clé de chiffrement est stockée en clair dans ce fichier. "
            "En mode portable, le fichier accompagne l'application : "
            "veillez à ne pas le laisser sur un support partagé."
        )
        warning.setObjectName("CardHint")
        warning.setWordWrap(True)
        warning.setStyleSheet(f"color: {theme.WARN};")
        card.layout().addWidget(warning)
        return card

    # -- valeurs -------------------------------------------------------

    def load_values(self) -> None:
        values = prefs.load()
        # Le signal servirait la valeur relue comme un choix de l'utilisateur,
        # et réécrirait la préférence à chaque ouverture de la page.
        self.auto_sync.blockSignals(True)
        self.auto_sync.setChecked(prefs.load_option("sync_au_demarrage"))
        self.auto_sync.blockSignals(False)
        self.dsn.setText(values.get("db_path", ""))
        self.secret.setText(values.get("secret_key", ""))
        self.strava_id.setText(values.get("strava_client_id", ""))
        self.strava_secret.setText(values.get("strava_client_secret", ""))
        self.ollama_url.setText(values.get("ollama_url", ""))
        self.ollama_model.setText(values.get("ollama_model", "") or "qwen3:0.6b")
        self.discord_url.setText(values.get("discord_webhook_url", ""))
        self.storage_detail.setText(prefs.describe_location())
        self.btn_portable.setEnabled(not prefs.is_portable())

    def save_values(self) -> None:
        prefs.save(
            {
                "db_path": self.dsn.text().strip(),
                "secret_key": self.secret.text().strip(),
                "strava_client_id": self.strava_id.text().strip(),
                "strava_client_secret": self.strava_secret.text().strip(),
                "ollama_url": self.ollama_url.text().strip(),
                "ollama_model": self.ollama_model.text().strip(),
                "discord_webhook_url": self.discord_url.text().strip(),
            }
        )
        # La base visée peut avoir changé : une tentative d'installation
        # infructueuse sur l'ancienne ne doit pas condamner la nouvelle.
        queries.forget_schema_attempts()
        self.settings_changed.emit()
        self._test_connection()

    def refresh(self, health: queries.Health) -> None:
        if health.usable:
            self.db_state.setText("prêt")
            self.db_state.set_tone("ok")
            self.db_detail.setText(f"Schéma installé · {health.engine}.")
            linked = queries.credentials_present()
        elif health.reachable:
            self.db_state.setText("schéma absent")
            self.db_state.set_tone("warn")
            self.db_detail.setText(health.detail)
            linked = {"garmin": False, "strava": False}
        else:
            self.db_state.setText("illisible")
            self.db_state.set_tone("danger")
            self.db_detail.setText(health.detail)
            linked = {"garmin": False, "strava": False}

        for pill, key in ((self.garmin_state, "garmin"), (self.strava_state, "strava")):
            if linked.get(key):
                pill.setText("relié")
                pill.set_tone("ok")
            else:
                pill.setText("non relié")
                pill.set_tone("idle")

        self.btn_migrate.setEnabled(health.reachable and not health.migrated)

    # -- actions -------------------------------------------------------

    def _test_connection(self) -> None:
        prefs.apply_to_environment()
        self.refresh(queries.probe())

    def _generate_key(self) -> None:
        if self.secret.text().strip():
            confirm = QMessageBox.question(
                self,
                "Remplacer la clé ?",
                "Une clé est déjà renseignée. La remplacer rendra illisibles "
                "les jetons Garmin et Strava déjà enregistrés : il faudra "
                "relier les deux comptes.\n\nContinuer ?",
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

        from cryptography.fernet import Fernet

        self.secret.setText(Fernet.generate_key().decode())

    def _run_migrations(self) -> None:
        from cairn.migrate import upgrade

        prefs.apply_to_environment()
        self._start(upgrade, "Installation du schéma")

    def _backup(self) -> None:
        """Sauvegarde le fichier de données à l'endroit choisi."""
        import datetime as dt

        from PySide6.QtWidgets import QFileDialog

        defaut = f"cairn-{dt.date.today():%Y-%m-%d}.db"
        chemin, _ = QFileDialog.getSaveFileName(
            self, "Sauvegarder les données", defaut, "Base Cairn (*.db)"
        )
        if not chemin:
            return

        def task() -> str:
            resultat = queries.backup_database(chemin)
            return f"{resultat['size_bytes'] / 1e6:.0f} Mo écrits dans {resultat['path']}"

        self._start(task, "Sauvegarde")

    def _export(self) -> None:
        """Écrit les deux tableaux dans un dossier choisi."""
        from PySide6.QtWidgets import QFileDialog

        dossier = QFileDialog.getExistingDirectory(self, "Exporter les données")
        if not dossier:
            return

        def task() -> str:
            resultat = queries.export_csv(dossier)
            return " · ".join(
                f"{pluriel(detail['rows'], 'ligne')} dans "
                f"{Path(detail['path']).name}"
                for detail in resultat.values()
            )

        self._start(task, "Export")

    def _login_garmin(self) -> None:
        if self._worker is not None:
            return

        self.save_values()
        # Le jeton se range dans le fichier de données : on s'assure qu'il est
        # prêt, en l'installant au besoin plutôt qu'en renvoyant l'utilisateur
        # vers un bouton.
        health = queries.ensure_schema()
        if not health.usable:
            QMessageBox.warning(
                self, "Base indisponible",
                "Le jeton Garmin doit être enregistré dans le fichier de "
                f"données, qui n'est pas utilisable.\n\n{health.detail}",
            )
            return

        from cairn.config import get_settings
        from cairn.gui.garmin_login import GarminLoginDialog

        domain = get_settings().garmin_domain
        dialog = GarminLoginDialog(domain, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.ticket:
            return

        # Le ticket ne vaut que quelques secondes : l'échange part aussitôt,
        # dans un fil séparé pour ne pas figer la fenêtre.
        worker = GarminLoginWorker(dialog.ticket, domain, dialog.service, self)
        self._worker = worker
        self.btn_garmin.setEnabled(False)
        worker.succeeded.connect(self._garmin_ok)
        worker.failed.connect(self._garmin_failed)
        worker.rate_limited.connect(self._garmin_rate_limited)
        worker.done.connect(self._worker_done)
        worker.start()

    def _garmin_ok(self, name: object) -> None:
        QMessageBox.information(self, "Garmin relié",
                                f"Session ouverte pour {name}.")

    def _garmin_failed(self, message: str) -> None:
        QMessageBox.critical(self, "Connexion Garmin échouée", message)

    def _garmin_rate_limited(self, message: str) -> None:
        """Garmin temporise : rien n'est cassé, il n'y a qu'à attendre.

        Une croix rouge et le mot « échouée » enverraient vérifier des
        identifiants qui n'ont rien à se reprocher.
        """
        QMessageBox.warning(self, "Garmin temporise", message)

    def _open_strava(self) -> None:
        self.save_values()
        from cairn.config import get_settings
        from cairn.sources.strava import StravaClient

        settings = get_settings()
        if not settings.strava_client_id or not settings.strava_client_secret:
            # Contrairement à Garmin, Strava exige un identifiant
            # d'application dans l'adresse d'autorisation : sans lui, il n'y a
            # aucune page de connexion à ouvrir. Plutôt que de renvoyer
            # l'utilisateur vers les champs du dessus, on récupère les deux
            # valeurs ici et on enchaîne sur la connexion.
            if not StravaCredentialsDialog(self).exec():
                return
            self.load_values()
            get_settings.cache_clear()
            settings = get_settings()
            if not settings.strava_client_id or not settings.strava_client_secret:
                return

        # Le jeton se range dans le fichier de données, comme celui de Garmin.
        health = queries.ensure_schema()
        if not health.usable:
            QMessageBox.warning(
                self, "Base indisponible",
                "Le jeton Strava doit être enregistré dans le fichier de "
                f"données, qui n'est pas utilisable.\n\n{health.detail}",
            )
            return

        from cairn.gui.strava_login import StravaLoginDialog

        with StravaClient(settings.strava_client_id, settings.strava_client_secret) as client:
            url = client.authorize_url(settings.strava_redirect_uri)

        dialog = StravaLoginDialog(url, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.code:
            return
        self._exchange_strava(dialog.code)

    def _exchange_strava(self, code: str) -> None:
        """Échange le code contre un jeton. Il ne vaut que quelques minutes."""
        from cairn.config import get_settings

        settings = get_settings()

        def task() -> str:
            from cairn.sources.strava import StravaClient

            with StravaClient(settings.strava_client_id,
                              settings.strava_client_secret) as client:
                token = client.exchange_code(code)
            athlete = token.get("athlete") or {}
            return f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip()

        self._start(task, "Connexion à Strava")

    def _start(self, task, label: str) -> None:
        if self._worker is not None:
            return
        worker = Worker(task, self)
        self._worker = worker
        worker.succeeded.connect(lambda result: self._generic_ok(label, result))
        worker.failed.connect(lambda message: QMessageBox.critical(self, label, message))
        worker.done.connect(self._worker_done)
        worker.start()

    def _generic_ok(self, label: str, result: object) -> None:
        detail = f"\n\n{result}" if result else ""
        QMessageBox.information(self, label, f"Terminé.{detail}")

    def _worker_done(self) -> None:
        self._worker = None
        self.btn_garmin.setEnabled(True)
        self.settings_changed.emit()
        self.refresh(queries.probe())

    def _enable_portable(self) -> None:
        self.save_values()
        target = prefs.enable_portable_mode()
        self.load_values()
        QMessageBox.information(
            self,
            "Mode portable activé",
            f"Les réglages sont désormais lus depuis :\n{target}\n\n"
            "Déplacer ce fichier avec l'application la rend autonome.",
        )
