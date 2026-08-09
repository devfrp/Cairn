"""Réglages de l'application, avec un vrai mode portable.

Si un fichier `cairn.ini` se trouve à côté de l'exécutable, il fait foi :
l'application est alors entièrement contenue dans son dossier et peut vivre
sur une clé USB. Sinon on retombe sur l'emplacement standard du système.

Les valeurs sont recopiées dans l'environnement avant chaque usage, parce que
tout le reste de Cairn — CLI comprise — lit sa configuration depuis là.
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

from PySide6.QtCore import QSettings

ORG = "Cairn"
APP = "Cairn"

PORTABLE_FILENAME = "cairn.ini"

# Clés persistées, avec le nom de variable d'environnement correspondant.
FIELDS: dict[str, str] = {
    "db_dsn": "CAIRN_DB_DSN",
    "secret_key": "CAIRN_SECRET_KEY",
    "file_root": "CAIRN_FILE_ROOT",
    "garmin_email": "CAIRN_GARMIN_EMAIL",
    "garmin_domain": "CAIRN_GARMIN_DOMAIN",
    "strava_client_id": "CAIRN_STRAVA_CLIENT_ID",
    "strava_client_secret": "CAIRN_STRAVA_CLIENT_SECRET",
    "strava_redirect_uri": "CAIRN_STRAVA_REDIRECT_URI",
    "timezone": "CAIRN_TIMEZONE",
    "ollama_url": "CAIRN_OLLAMA_URL",
    "ollama_model": "CAIRN_OLLAMA_MODEL",
    "discord_webhook_url": "CAIRN_DISCORD_WEBHOOK_URL",
}

# Champs qui ne doivent jamais apparaître en clair dans un journal.
SENSITIVE = {"secret_key", "strava_client_secret", "db_dsn", "discord_webhook_url"}


def app_directory() -> Path:
    """Dossier de l'exécutable, ou du dépôt en développement."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def portable_file() -> Path:
    return app_directory() / PORTABLE_FILENAME


def is_portable() -> bool:
    return portable_file().exists()


def _store() -> QSettings:
    if is_portable():
        return QSettings(str(portable_file()), QSettings.Format.IniFormat)
    return QSettings(ORG, APP)


def enable_portable_mode() -> Path:
    """Bascule en mode portable en recopiant les réglages actuels."""
    current = load()
    target = portable_file()
    store = QSettings(str(target), QSettings.Format.IniFormat)
    for key, value in current.items():
        store.setValue(key, value)
    store.sync()
    _protect(target)
    return target


def _protect(path: Path) -> None:
    """Restreint les droits : le fichier contient la clé de chiffrement."""
    # Systèmes de fichiers sans permissions POSIX (FAT sur clé USB) : il n'y
    # a rien à faire, mais ce n'est pas une raison d'échouer.
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def load() -> dict[str, str]:
    store = _store()
    return {key: str(store.value(key, "") or "") for key in FIELDS}


def save(values: dict[str, str]) -> None:
    store = _store()
    for key in FIELDS:
        if key in values:
            store.setValue(key, values[key])
    store.sync()
    if is_portable():
        _protect(portable_file())
    apply_to_environment()


def apply_to_environment() -> None:
    """Publie les réglages dans l'environnement et vide les caches.

    Les réglages vides ne sont pas exportés : une valeur absente doit laisser
    le défaut de `Settings` s'appliquer, pas écraser avec une chaîne vide.
    """
    from cairn.config import reset_settings
    from cairn.db import close_pool

    for key, env_name in FIELDS.items():
        value = _store().value(key, "")
        if value:
            os.environ[env_name] = str(value)
        else:
            os.environ.pop(env_name, None)

    close_pool()
    reset_settings()


def describe_location() -> str:
    if is_portable():
        return f"Mode portable — {portable_file()}"
    return f"Réglages système — {_store().fileName()}"
