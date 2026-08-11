"""Fenêtre de connexion Garmin.

Depuis mars 2026, Garmin écarte les clients HTTP sur leur seule signature TLS :
envoyer un mot de passe depuis Python se solde par un 429 avant même qu'il
soit examiné. La connexion passe donc par un vrai moteur de rendu.

Ce que Cairn en retire tient en une chaîne : le « service ticket » que Garmin
remet une fois l'identification faite. Le mot de passe et le code de double
authentification restent entre l'utilisateur et Garmin.

La mécanique de la fenêtre elle-même vit dans `oauth_window`, partagée avec
Strava.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from cairn.gui.oauth_window import OAuthDialog, extract_param, target_of
from cairn.gui.webengine import prepare_webengine
from cairn.sources.garmin import signin_url

NOTICE = (
    "Vous êtes sur le site de Garmin. Votre mot de passe est envoyé à Garmin "
    "directement : Cairn ne le voit pas et ne le conserve pas."
)


def extract_ticket(url: str) -> str | None:
    """Récupère le « service ticket » d'une adresse."""
    return extract_param(url, "ticket")


def service_of(url: str) -> str:
    """Adresse à laquelle le ticket a été remis, sans ses paramètres."""
    return target_of(url)


class GarminLoginDialog(OAuthDialog):
    """Connexion Garmin dans une fenêtre de navigateur intégrée."""

    def __init__(self, domain: str = "garmin.com",
                 parent: QWidget | None = None) -> None:
        super().__init__(
            signin_url(domain),
            param="ticket",
            title="Connexion à Garmin Connect",
            notice=NOTICE,
            parent=parent,
        )

    @property
    def ticket(self) -> str | None:
        return self.value

    @property
    def service(self) -> str | None:
        return self.target


__all__ = [
    "GarminLoginDialog",
    "extract_ticket",
    "prepare_webengine",
    "service_of",
]
