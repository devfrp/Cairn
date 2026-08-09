"""Application des migrations SQL.

Les fichiers de `db/migrations/` sont appliqués dans l'ordre lexicographique,
chacun dans sa propre transaction, et tracés dans `schema_migration`.
Le sha256 stocké permet de détecter qu'une migration déjà appliquée a été
modifiée après coup — cas qui doit hurler plutôt que passer inaperçu.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import structlog

from cairn.db import get_connection

log = structlog.get_logger(__name__)


def _migrations_dir() -> Path:
    """Localise `db/migrations` selon le mode d'exécution.

    Trois cas : binaire empaqueté (PyInstaller déplie ses données dans un
    dossier temporaire exposé par `sys._MEIPASS`), conteneur ou dépôt cloné
    (le dossier est à côté du code), et surcharge explicite pour les cas
    tordus.
    """
    override = os.environ.get("CAIRN_MIGRATIONS_DIR")
    if override:
        return Path(override)

    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled) / "db" / "migrations"

    return Path(__file__).resolve().parents[2] / "db" / "migrations"


MIGRATIONS_DIR = _migrations_dir()


class MigrationDrift(RuntimeError):
    """Une migration déjà appliquée ne correspond plus au fichier sur disque."""


def _discover(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("*.sql") if p.is_file())


def _applied(conn) -> dict[str, bytes]:
    found = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migration'"
    ).fetchone()
    if found is None:
        return {}
    return {
        row["version"]: bytes(row["sha256"])
        for row in conn.execute("SELECT version, sha256 FROM schema_migration")
    }


def upgrade(directory: Path | None = None) -> list[str]:
    """Applique les migrations manquantes. Retourne les versions appliquées."""
    directory = directory or _migrations_dir()
    files = _discover(directory)
    if not files:
        log.warning("aucune migration trouvée", directory=str(directory))
        return []

    applied_now: list[str] = []
    already = _applied(get_connection())

    for path in files:
        version = path.stem
        body = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(body.encode("utf-8")).digest()

        if version in already:
            if already[version] != digest:
                raise MigrationDrift(
                    f"{version} a été modifiée après application. "
                    "Créer une nouvelle migration plutôt que d'éditer celle-ci."
                )
            continue

        log.info("application de la migration", version=version)
        conn = get_connection()
        # executescript enchaîne plusieurs instructions et ouvre sa propre
        # transaction : on ne peut pas l'imbriquer dans un BEGIN explicite.
        conn.executescript(body)
        conn.execute(
            "INSERT INTO schema_migration (version, sha256) VALUES (?, ?)",
            (version, digest),
        )
        conn.commit()
        applied_now.append(version)

    if not applied_now:
        log.info("base déjà à jour", count=len(files))
    return applied_now
