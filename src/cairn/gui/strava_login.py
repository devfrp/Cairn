"""Fenêtre d'autorisation Strava.

Strava suit un OAuth2 ordinaire : l'utilisateur autorise l'application sur son
site, et Strava renvoie un `code` à usage unique dans l'adresse de rappel.
Jusqu'ici Cairn ouvrait le navigateur du système et demandait de recopier ce
code à la main depuis la barre d'adresse — une manipulation que rien
n'explique, sur une page d'erreur qui plus est, puisque l'adresse de rappel ne
mène nulle part.

La fenêtre intégrée le récupère elle-même. Le mot de passe Strava reste entre
l'utilisateur et Strava.
"""

from __future__ import annotations

from PySide6.QtWidgets import QWidget

from cairn.gui.oauth_window import OAuthDialog

NOTICE = (
    "Vous êtes sur le site de Strava. Autorisez Cairn à lire vos activités : "
    "la page d'arrivée affichera une erreur, c'est normal, la fenêtre se "
    "fermera d'elle-même."
)


class StravaLoginDialog(OAuthDialog):
    """Autorisation Strava dans une fenêtre de navigateur intégrée."""

    def __init__(self, authorize_url: str, parent: QWidget | None = None) -> None:
        super().__init__(
            authorize_url,
            param="code",
            title="Autoriser Cairn sur Strava",
            notice=NOTICE,
            parent=parent,
        )

    @property
    def code(self) -> str | None:
        return self.value


__all__ = ["StravaLoginDialog"]
