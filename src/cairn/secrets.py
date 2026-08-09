"""Stockage chiffré des credentials (tokens OAuth Strava, session Garmin).

Les tokens vivent en base pour survivre à une recréation du conteneur, mais
chiffrés : la clé est dans l'environnement de la LXC, pas dans la base. Un
fichier de données copié ne donne donc pas accès aux comptes.
"""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from cairn.config import get_settings
from cairn.db import connection, fetch_one


class SecretsUnavailable(RuntimeError):
    """CAIRN_SECRET_KEY absente ou invalide."""


def _fernet() -> Fernet:
    key = get_settings().secret_key
    if not key:
        raise SecretsUnavailable(
            "CAIRN_SECRET_KEY non définie. Générer une clé avec :\n"
            '  python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError) as exc:
        raise SecretsUnavailable(f"CAIRN_SECRET_KEY invalide : {exc}") from exc


def save_credential(source: str, payload: dict[str, Any], expires_at=None) -> None:
    blob = _fernet().encrypt(json.dumps(payload, default=str).encode("utf-8"))
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO credential (source, payload_enc, expires_at, updated_at)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT (source) DO UPDATE
               SET payload_enc = excluded.payload_enc,
                   expires_at  = excluded.expires_at,
                   updated_at  = datetime('now')
            """,
            (source, blob, expires_at),
        )


def load_credential(source: str) -> dict[str, Any] | None:
    row = fetch_one("SELECT payload_enc FROM credential WHERE source = ?", (source,))
    if row is None:
        return None
    try:
        clear = _fernet().decrypt(bytes(row["payload_enc"]))
    except InvalidToken as exc:
        raise SecretsUnavailable(
            f"Le credential '{source}' ne se déchiffre pas avec la CAIRN_SECRET_KEY "
            "actuelle. Si la clé a changé, relancer `cairn auth " + source + "`."
        ) from exc
    return json.loads(clear)


def delete_credential(source: str) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM credential WHERE source = ?", (source,))
