"""Tâches longues exécutées hors du fil d'affichage.

Une synchronisation dure de quelques secondes à plusieurs minutes. Lancée
dans le fil principal, elle figerait la fenêtre. Chaque opération part donc
dans un QThread et communique par signaux.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal


class _LogBridge(logging.Handler):
    """Renvoie les journaux produits pendant la tâche vers l'interface."""

    def __init__(self, emit_line: Callable[[str], None]) -> None:
        super().__init__(level=logging.INFO)
        self._emit_line = emit_line

    def emit(self, record: logging.LogRecord) -> None:
        # Un souci d'affichage ne doit jamais interrompre la tâche en cours.
        with contextlib.suppress(Exception):
            self._emit_line(self.format(record))


class Worker(QThread):
    """Exécute une fonction en arrière-plan.

    `succeeded` porte la valeur de retour, `failed` le message d'erreur.
    `terminated_ok` est émis dans les deux cas, pour réactiver l'interface
    sans avoir à brancher les deux signaux.
    """

    succeeded = Signal(object)
    failed = Signal(str)
    log_line = Signal(str)
    done = Signal()

    def __init__(self, task: Callable[[], Any], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._task = task

    def run(self) -> None:
        bridge = _LogBridge(self.log_line.emit)
        bridge.setFormatter(logging.Formatter("%(message)s"))
        root = logging.getLogger()
        root.addHandler(bridge)
        try:
            result = self._task()
        except Exception as exc:  # noqa: BLE001 — remonté à l'interface
            self.failed.emit(f"{type(exc).__name__} : {exc}")
        else:
            self.succeeded.emit(result)
        finally:
            root.removeHandler(bridge)
            self.done.emit()


class GarminLoginWorker(QThread):
    """Connexion Garmin, avec demande de code à double authentification.

    Le code ne peut être saisi que dans le fil d'affichage. Le worker émet
    `mfa_required`, se met en attente, et l'interface le réveille avec
    `provide_mfa()`.
    """

    succeeded = Signal(object)
    failed = Signal(str)
    mfa_required = Signal()
    done = Signal()

    def __init__(self, email: str, password: str, domain: str,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._email = email
        self._password = password
        self._domain = domain
        self._mfa_code: str | None = None
        self._mfa_ready = threading.Event()

    def provide_mfa(self, code: str | None) -> None:
        """Appelé depuis l'interface : None signifie annulation."""
        self._mfa_code = code
        self._mfa_ready.set()

    def _prompt_mfa(self) -> str:
        self._mfa_ready.clear()
        self.mfa_required.emit()
        # Sans limite de temps, un worker resterait bloqué si la fenêtre est
        # fermée pendant la saisie.
        if not self._mfa_ready.wait(timeout=300):
            raise TimeoutError("Aucun code de double authentification fourni.")
        if not self._mfa_code:
            raise RuntimeError("Connexion annulée.")
        return self._mfa_code

    def run(self) -> None:
        from cairn.sources.garmin import GarminClient

        try:
            client = GarminClient(domain=self._domain)
            client.login_interactive(self._email, self._password,
                                     prompt_mfa=self._prompt_mfa)
            name = client.display_name
        except Exception as exc:  # noqa: BLE001 — remonté à l'interface
            self.failed.emit(f"{type(exc).__name__} : {exc}")
        else:
            self.succeeded.emit(name)
        finally:
            self.done.emit()
