"""Accès base : pool de connexions psycopg3 et helpers d'ingestion brute.

Pas d'ORM. Le schéma est très orienté upsert (`ON CONFLICT`) et séries
temporelles (`COPY`), deux choses qu'un ORM rend plus opaques qu'utiles.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from cairn.config import get_settings

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        settings = get_settings()
        # Échouer vite : une base injoignable est presque toujours une
        # erreur de configuration, pas une lenteur passagère. Attendre
        # trente secondes ne fait que retarder le diagnostic.
        timeout = settings.db_timeout
        _pool = ConnectionPool(
            settings.db_dsn,
            min_size=1,
            max_size=8,
            timeout=timeout,
            kwargs={
                "row_factory": dict_row,
                "connect_timeout": max(2, int(timeout // 2)),
            },
            open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """Connexion transactionnelle : commit si tout va bien, rollback sinon."""
    with get_pool().connection() as conn:
        yield conn


def fetch_all(sql: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_one(sql: str, params: Sequence[Any] | None = None) -> dict[str, Any] | None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def execute(sql: str, params: Sequence[Any] | None = None) -> int:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


# ---------------------------------------------------------------------
# Couche raw
# ---------------------------------------------------------------------


def payload_hash(payload: Any) -> bytes:
    """Hash stable d'un payload JSON, insensible à l'ordre des clés."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).digest()


def store_raw(
    conn: psycopg.Connection,
    *,
    source: str,
    endpoint: str,
    resource_key: str,
    payload: Any,
    http_status: int = 200,
) -> tuple[int, bool]:
    """Archive une réponse d'API. Retourne (id du payload, contenu nouveau ?).

    Le `ON CONFLICT DO NOTHING` porte sur (source, endpoint, resource_key,
    content_hash) : resynchroniser une ressource inchangée ne crée pas de
    doublon et signale `False`, ce qui permet aux appelants de sauter la
    transformation.
    """
    digest = payload_hash(payload)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw.api_payload
                   (source, endpoint, resource_key, http_status, content_hash, payload)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (source, endpoint, resource_key, content_hash) DO NOTHING
            RETURNING id
            """,
            (source, endpoint, resource_key, http_status, digest, json.dumps(payload, default=str)),
        )
        row = cur.fetchone()
        if row is not None:
            return row["id"], True

        # Contenu déjà connu : on récupère l'id existant.
        cur.execute(
            """
            SELECT id FROM raw.api_payload
             WHERE source = %s AND endpoint = %s
               AND resource_key = %s AND content_hash = %s
            """,
            (source, endpoint, resource_key, digest),
        )
        existing = cur.fetchone()
        assert existing is not None  # garanti par le ON CONFLICT ci-dessus
        return existing["id"], False
