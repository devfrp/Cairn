"""Page de synchronisation : déclenchement, journal en direct, état des curseurs."""

from __future__ import annotations

import datetime as dt

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cairn.gui import queries, theme
from cairn.gui.widgets import Card, SectionHeader, StatusPill
from cairn.gui.workers import Worker
from cairn.text import pluriel


class SyncPage(QWidget):
    """Déclenche les passes et rend compte de leur déroulement.

    `finished_run` prévient la fenêtre principale qu'il faut rafraîchir les
    autres pages : les données viennent de changer.
    """

    finished_run = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker: Worker | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 28)
        layout.setSpacing(16)

        layout.addWidget(
            SectionHeader(
                "Synchronisation",
                "Chaque passe reprend là où la précédente s'est arrêtée. "
                "Une interruption ne fait jamais recommencer depuis le début.",
            )
        )

        # --- actions ---
        self.btn_incremental = QPushButton("Synchroniser maintenant")
        self.btn_incremental.setObjectName("Primary")
        self.btn_incremental.clicked.connect(lambda: self._start("incremental"))

        self.btn_backfill = QPushButton("Rattraper l'historique")
        self.btn_backfill.setToolTip(
            "Descend dans le passé par lots. À relancer plusieurs fois : "
            "Garmin limite le rythme et Strava impose un quota journalier."
        )
        self.btn_backfill.clicked.connect(lambda: self._start("backfill"))

        self.btn_streams = QPushButton("Traiter les fichiers .fit")
        self.btn_streams.setToolTip(
            "Décode les fichiers déjà téléchargés : séries seconde par seconde, "
            "zones de fréquence cardiaque, charge d'entraînement."
        )
        self.btn_streams.clicked.connect(self._start_streams)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addWidget(self.btn_incremental)
        actions.addWidget(self.btn_backfill)
        actions.addWidget(self.btn_streams)
        actions.addStretch(1)

        self.state_pill = StatusPill("au repos", "idle")
        actions.addWidget(self.state_pill)
        layout.addLayout(actions)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # indéterminé : la durée n'est pas prévisible
        self.progress.hide()
        layout.addWidget(self.progress)

        # --- curseurs ---
        self.cursors_card = Card()
        title = QLabel("Où en est chaque source")
        title.setObjectName("SectionTitle")
        self.cursors_card.layout().addWidget(title)
        self.cursors_body = QLabel("—")
        self.cursors_body.setObjectName("CardHint")
        self.cursors_body.setTextFormat(Qt.TextFormat.RichText)
        self.cursors_card.layout().addWidget(self.cursors_body)
        layout.addWidget(self.cursors_card)

        # --- journal ---
        journal_title = QLabel("Journal")
        journal_title.setObjectName("SectionTitle")
        layout.addWidget(journal_title)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Le déroulé de la synchronisation s'affichera ici.")
        layout.addWidget(self.log, 1)

        # --- passes précédentes ---
        self.runs_body = QLabel("—")
        self.runs_body.setObjectName("CardHint")
        self.runs_body.setTextFormat(Qt.TextFormat.RichText)
        runs_card = Card()
        runs_title = QLabel("Dernières passes")
        runs_title.setObjectName("SectionTitle")
        runs_card.layout().addWidget(runs_title)
        runs_card.layout().addWidget(self.runs_body)
        layout.addWidget(runs_card)

    # -----------------------------------------------------------------

    def refresh(self, health: queries.Health) -> None:
        self._set_enabled(health.usable and self._worker is None)
        if not health.usable:
            self.cursors_body.setText("Base indisponible.")
            self.runs_body.setText("—")
            return

        self.cursors_body.setText(self._render_cursors(queries.cursors()))
        self.runs_body.setText(self._render_runs(queries.recent_runs()))

    def _render_cursors(self, rows: list[dict]) -> str:
        if not rows:
            return "Aucune synchronisation effectuée pour l'instant."

        lines = []
        for entry in rows:
            if entry["backfill_done"]:
                backfill = f"<span style='color:{theme.OK}'>historique complet</span>"
            elif entry["backfill_cursor"]:
                backfill = f"remonté jusqu'au {entry['backfill_cursor']:%d/%m/%Y}"
            else:
                backfill = "rattrapage non démarré"

            last = entry["last_success_at"]
            last_text = last.strftime("%d/%m %H:%M") if last else "jamais"

            errors = ""
            if entry["consecutive_errors"]:
                errors = (
                    f" · <span style='color:{theme.DANGER}'>"
                    f"{pluriel(entry['consecutive_errors'], 'erreur')}</span>"
                )

            lines.append(
                f"<b>{entry['source']}</b> / {entry['resource']} — "
                f"dernier succès {last_text} · {backfill}{errors}"
            )
        return "<br>".join(lines)

    def _render_runs(self, rows: list[dict]) -> str:
        if not rows:
            return "Aucune passe enregistrée."

        colours = {"ok": theme.OK, "partial": theme.WARN,
                   "failed": theme.DANGER, "running": theme.ACCENT}
        lines = []
        for entry in rows:
            colour = colours.get(entry["status"], theme.MUTED)
            detail = ""
            if entry["error"]:
                detail = f" — {entry['error'][:90]}"
            lines.append(
                f"{entry['started_at']:%d/%m %H:%M} · <b>{entry['source']}</b> "
                f"{entry['mode']} · <span style='color:{colour}'>{entry['status']}</span> "
                f"· {entry['api_calls']} appels, {entry['items_upserted']} enregistrés{detail}"
            )
        return "<br>".join(lines)

    # -----------------------------------------------------------------

    def _set_enabled(self, enabled: bool) -> None:
        for button in (self.btn_incremental, self.btn_backfill, self.btn_streams):
            button.setEnabled(enabled)

    def _start(self, mode: str) -> None:
        from cairn.sync.runner import run_all

        self._run_task(
            lambda: run_all(mode=mode),
            f"Synchronisation ({'rattrapage' if mode == 'backfill' else 'incrémentale'})",
        )

    def _start_streams(self) -> None:
        from cairn.sync.streams import backfill

        self._run_task(backfill, "Traitement des fichiers .fit")

    def _run_task(self, task, label: str) -> None:
        if self._worker is not None:
            return

        self._append(f"── {label} — {dt.datetime.now():%H:%M:%S}")
        self._set_enabled(False)
        self.progress.show()
        self.state_pill.setText("en cours")
        self.state_pill.set_tone("accent")

        worker = Worker(task, self)
        self._worker = worker
        worker.log_line.connect(self._append)
        worker.succeeded.connect(self._on_success)
        worker.failed.connect(self._on_failure)
        worker.done.connect(self._on_done)
        worker.start()

    def _on_success(self, result: object) -> None:
        if isinstance(result, dict):
            summary = " · ".join(f"{k} : {v}" for k, v in result.items())
            self._append(f"Terminé — {summary}")
        else:
            self._append("Terminé.")
        self.state_pill.setText("terminé")
        self.state_pill.set_tone("ok")

    def _on_failure(self, message: str) -> None:
        self._append(f"Échec — {message}")
        self.state_pill.setText("échec")
        self.state_pill.set_tone("danger")

    def _on_done(self) -> None:
        self._worker = None
        self.progress.hide()
        self._set_enabled(True)
        self.finished_run.emit()

    def _append(self, line: str) -> None:
        self.log.appendPlainText(line)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())
