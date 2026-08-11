"""Fenêtre d'identification partagée par Garmin et Strava.

Les deux services rendent un jeton à usage unique glissé dans une adresse,
mais pas sous le même nom ni par le même chemin : `ticket` chez Garmin, remis
par un appel de fond ; `code` chez Strava, remis par une vraie redirection.
"""

from __future__ import annotations

import pytest

from cairn.gui.oauth_window import extract_param, target_of


@pytest.mark.parametrize(
    ("url", "nom", "attendu"),
    [
        # Garmin, tel qu'observé en conditions réelles.
        (
            "https://mobile.integration.garmin.com/gcm/android"
            "?ticket=ST-0103600-2Mxh2dHllSFIcn3Bc3ic-cas",
            "ticket",
            "ST-0103600-2Mxh2dHllSFIcn3Bc3ic-cas",
        ),
        # Strava, avec les paramètres qui l'entourent.
        (
            "http://localhost:8000/auth/strava/callback"
            "?state=&code=8f2c1e9a4b&scope=read,activity:read_all",
            "code",
            "8f2c1e9a4b",
        ),
        # Le nom compte : un ticket n'est pas un code.
        ("https://exemple.test/x?ticket=ST-1", "code", None),
        ("https://exemple.test/x?code=abc", "ticket", None),
        # Absent, vide, ou sans requête du tout.
        ("https://exemple.test/x?code=", "code", None),
        ("https://exemple.test/x", "code", None),
    ],
)
def test_extract_param(url: str, nom: str, attendu: str | None) -> None:
    assert extract_param(url, nom) == attendu


@pytest.mark.parametrize(
    ("url", "attendu"),
    [
        (
            "https://mobile.integration.garmin.com/gcm/android?ticket=ST-1",
            "https://mobile.integration.garmin.com/gcm/android",
        ),
        (
            "http://localhost:8000/auth/strava/callback?state=&code=x&scope=read",
            "http://localhost:8000/auth/strava/callback",
        ),
    ],
)
def test_target_of_retire_les_parametres(url: str, attendu: str) -> None:
    """L'échange exige parfois l'adresse exacte, jamais ses paramètres."""
    assert target_of(url) == attendu


def test_les_deux_fenetres_guettent_le_bon_parametre() -> None:
    """Une inversion ici laisserait la fenêtre ouverte indéfiniment, sans la
    moindre erreur — le service ayant pourtant fait son travail."""
    from cairn.gui.garmin_login import GarminLoginDialog
    from cairn.gui.strava_login import StravaLoginDialog

    assert GarminLoginDialog.__mro__[1].__name__ == "OAuthDialog"
    assert StravaLoginDialog.__mro__[1].__name__ == "OAuthDialog"


def test_extraction_garmin_reste_disponible() -> None:
    """`extract_ticket` reste le nom employé côté Garmin."""
    from cairn.gui.garmin_login import extract_ticket, service_of

    url = "https://mobile.integration.garmin.com/gcm/android?ticket=ST-9"
    assert extract_ticket(url) == "ST-9"
    assert service_of(url) == "https://mobile.integration.garmin.com/gcm/android"
