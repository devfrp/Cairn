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

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineUrlRequestInterceptor,
)
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


class TicketInterceptor(QWebEngineUrlRequestInterceptor):
    """Guette le ticket dans toutes les requêtes, pas seulement les navigations.

    Garmin ne redirige pas le navigateur vers l'adresse de service : sa page
    de succès l'appelle en arrière-plan, en JSONP. Le ticket ne passe donc
    jamais par la barre d'adresse, et surveiller les changements d'adresse
    n'en voyait aucun — l'appel échoue d'ailleurs, l'adresse en question
    n'étant pas une vraie page.
    """

    found = Signal(str, str)

    def interceptRequest(self, info) -> None:  # noqa: N802 — signature Qt
        # Appelé depuis un fil du moteur de rendu : on se contente d'émettre,
        # la connexion en file d'attente ramène le travail au fil principal.
        url = info.requestUrl().toString()
        ticket = extract_ticket(url)
        if ticket:
            self.found.emit(ticket, service_of(url))


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

        # Deux guetteurs pour un même ticket : selon le chemin suivi par
        # Garmin, il arrive par une redirection ou par un appel de fond.
        self._interceptor = TicketInterceptor(self)
        self._interceptor.found.connect(self._on_ticket, Qt.ConnectionType.QueuedConnection)
        self._profile.setUrlRequestInterceptor(self._interceptor)

        layout.addWidget(self.view, 1)

        buttons = QDialogButtonBox()
        annuler = buttons.addButton(
            "Annuler", QDialogButtonBox.ButtonRole.RejectRole
        )
        annuler.setCursor(Qt.CursorShape.PointingHandCursor)
        buttons.rejected.connect(self.reject)
        buttons.setContentsMargins(14, 8, 14, 12)
        layout.addWidget(buttons)

        self.view.setUrl(QUrl(signin_url(domain)))

    def _on_url(self, url: QUrl) -> None:
        raw = url.toString()
        ticket = extract_ticket(raw)
        if ticket is not None:
            self._on_ticket(ticket, service_of(raw))

    def _on_ticket(self, ticket: str, service: str) -> None:
        # Les deux guetteurs peuvent tirer sur le même ticket : le premier
        # arrivé gagne, le second ne doit pas refermer une fenêtre déjà close.
        if self.ticket is not None:
            return
        self.ticket = ticket
        self.service = service
        self.view.stop()
        self.accept()


# Réexporté par commodité : la fenêtre et son réglage préalable se pensent
# ensemble, même s'ils vivent dans deux modules pour ne pas mêler leurs
# dépendances.
__all__ = ["GarminLoginDialog", "extract_ticket", "prepare_webengine", "service_of"]
