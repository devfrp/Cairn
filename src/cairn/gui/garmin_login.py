"""Fenêtre de connexion Garmin.

Depuis mars 2026, Garmin écarte les clients HTTP sur leur seule signature TLS :
envoyer un mot de passe depuis Python se solde par un 429 avant même qu'il
soit examiné. La connexion passe donc par un vrai moteur de rendu.

Ce que Cairn en retire tient en une chaîne : le « service ticket » que Garmin
ajoute à l'adresse de redirection une fois l'identification faite. Le mot de
passe et le code de double authentification restent entre l'utilisateur et
Garmin — ils ne traversent jamais le code de Cairn, qui ne peut pas les lire.

La session est volontairement sans mémoire : rien n'est écrit sur le disque,
et la fenêtre repart vierge à chaque fois. Le jeton obtenu vaut environ un an,
il n'y a donc rien à gagner à conserver des cookies.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from cairn.gui.webengine import prepare_webengine
from cairn.sources.garmin import signin_url


def extract_ticket(url: str) -> str | None:
    """Récupère le « service ticket » d'une adresse de redirection.

    Garmin le remet en paramètre de requête après une identification réussie.
    L'adresse visée n'est pas une page : le navigateur échouera à la charger,
    ce qui est sans importance puisqu'on intercepte avant.
    """
    ticket = parse_qs(urlparse(url).query).get("ticket")
    if not ticket:
        return None
    value = ticket[0].strip()
    return value or None


def service_of(url: str) -> str:
    """Adresse à laquelle le ticket a été remis, sans ses paramètres.

    Garmin ne s'engage pas à renvoyer vers le service demandé — il peut
    rediriger ailleurs. L'échange du ticket exigeant l'adresse exacte, on
    transmet celle qu'on a vue passer plutôt que celle qu'on avait réclamée.
    """
    parts = urlparse(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


class GarminLoginDialog(QDialog):
    """Connexion Garmin dans une fenêtre de navigateur intégrée."""

    def __init__(self, domain: str = "garmin.com",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ticket: str | None = None
        self.service: str | None = None

        self.setWindowTitle("Connexion à Garmin Connect")
        self.resize(560, 720)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        notice = QLabel(
            "Vous êtes sur le site de Garmin. Votre mot de passe est envoyé "
            "à Garmin directement : Cairn ne le voit pas et ne le conserve pas."
        )
        notice.setWordWrap(True)
        notice.setContentsMargins(14, 12, 14, 12)
        layout.addWidget(notice)

        # Profil sans mémoire : ni cookies ni cache ne survivent à la fenêtre.
        self._profile = QWebEngineProfile(self)
        self.view = QWebEngineView(self)
        # La page est rattachée au profil, et non à la vue : Qt détruit un
        # objet après ses enfants, ce qui garantit que la page parte en
        # premier. L'ordre inverse laisse le moteur travailler sur un profil
        # déjà libéré — Qt le signale, puis plante à l'occasion.
        self._page = QWebEnginePage(self._profile, self._profile)
        self.view.setPage(self._page)
        self.view.urlChanged.connect(self._on_url)
        layout.addWidget(self.view, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        buttons.rejected.connect(self.reject)
        buttons.setContentsMargins(14, 8, 14, 12)
        layout.addWidget(buttons)

        self.view.setUrl(QUrl(signin_url(domain)))

    def _on_url(self, url: QUrl) -> None:
        raw = url.toString()
        ticket = extract_ticket(raw)
        if ticket is None:
            return
        # Intercepté au changement d'adresse, donc avant que le navigateur ne
        # tente de charger une page qui n'existe pas.
        self.ticket = ticket
        self.service = service_of(raw)
        self.view.stop()
        self.accept()


# Réexporté par commodité : la fenêtre et son réglage préalable se pensent
# ensemble, même s'ils vivent dans deux modules pour ne pas mêler leurs
# dépendances.
__all__ = ["GarminLoginDialog", "extract_ticket", "prepare_webengine", "service_of"]
