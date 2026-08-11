"""Tâches longues exécutées hors du fil d'affichage.

Une synchronisation dure de quelques secondes à plusieurs minutes. Lancée
dans le fil principal, elle figerait la fenêtre. Chaque opération part donc
dans un QThread et communique par signaux.
"""

from __future__ import annotations

import contextlib
import logging
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
    """Échange le ticket de connexion Garmin contre des jetons durables.

    L'identification elle-même a lieu dans la fenêtre de navigateur : ni le
    mot de passe ni le code de double authentification n'arrivent jusqu'ici.
    Ne reste que le ticket, valable quelques secondes, dont l'échange est un
    appel réseau — donc à faire hors du fil d'affichage.
    """

    succeeded = Signal(object)
    failed = Signal(str)
    # Distinct de `failed` : rien n'est cassé, il faut seulement attendre.
    rate_limited = Signal(str)
    done = Signal()

    def __init__(self, ticket: str, domain: str, service: str | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ticket = ticket
        self._domain = domain
        self._service = service

    def run(self) -> None:
        from cairn.sources.garmin import (
            GarminClient,
            GarminError,
            GarminRateLimited,
        )

        try:
            client = GarminClient(domain=self._domain)
            client.login_with_ticket(self._ticket, self._service)
            name = client.display_name
        except GarminRateLimited as exc:
            self.rate_limited.emit(str(exc))
        except GarminError as exc:
            # Message déjà écrit pour l'utilisateur : le préfixer du nom de la
            # classe ne ferait que le rendre inquiétant.
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 — remonté à l'interface
            self.failed.emit(f"{type(exc).__name__} : {exc}")
        else:
            self.succeeded.emit(name)
        finally:
            self.done.emit()
