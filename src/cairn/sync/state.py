"""Suivi des exécutions de synchronisation et curseurs de reprise."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Any

from cairn.db import connection, fetch_one


@dataclass
class RunStats:
    api_calls: int = 0
    items_fetched: int = 0
    items_upserted: int = 0
    detail: dict[str, Any] = field(default_factory=dict)

    def merge(self, other: RunStats) -> None:
        self.api_calls += other.api_calls
        self.items_fetched += other.items_fetched
        self.items_upserted += other.items_upserted
        for key, value in other.detail.items():
            if isinstance(value, int) and isinstance(self.detail.get(key), int):
                self.detail[key] += value
            else:
                self.detail[key] = value


def start_run(source: str, mode: str) -> int:
    row = fetch_one(
        "INSERT INTO sync_run (source, mode) VALUES (%s, %s) RETURNING id",
        (source, mode),
    )
    assert row is not None
    return row["id"]


def finish_run(run_id: int, status: str, stats: RunStats, error: str | None = None) -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE sync_run
               SET finished_at = now(), status = %s, api_calls = %s,
                   items_fetched = %s, items_upserted = %s, error = %s, detail = %s
             WHERE id = %s
            """,
            (
                status,
                stats.api_calls,
                stats.items_fetched,
                stats.items_upserted,
                error,
                json.dumps(stats.detail, default=str),
                run_id,
            ),
        )


# ---------------------------------------------------------------------
# Curseurs
# ---------------------------------------------------------------------


def get_cursor(source: str, resource: str) -> dict[str, Any]:
    """Curseur d'une ressource, créé à la volée s'il n'existe pas."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sync_cursor (source, resource) VALUES (%s, %s)
            ON CONFLICT (source, resource) DO NOTHING
            """,
            (source, resource),
        )
        cur.execute(
            "SELECT * FROM sync_cursor WHERE source = %s AND resource = %s",
            (source, resource),
        )
        row = cur.fetchone()
    assert row is not None
    return row


def mark_success(
    source: str,
    resource: str,
    *,
    incremental_from: dt.datetime | None = None,
    last_external_id: str | None = None,
    backfill_cursor: dt.date | None = None,
    backfill_done: bool | None = None,
) -> None:
    """Enregistre une passe réussie. Les champs à None ne sont pas touchés."""
    sets = ["last_success_at = now()", "consecutive_errors = 0"]
    params: list[Any] = []
    if incremental_from is not None:
        sets.append("incremental_from = %s")
        params.append(incremental_from)
    if last_external_id is not None:
        sets.append("last_external_id = %s")
        params.append(last_external_id)
    if backfill_cursor is not None:
        sets.append("backfill_cursor = %s")
        params.append(backfill_cursor)
    if backfill_done is not None:
        sets.append("backfill_done = %s")
        params.append(backfill_done)
    params += [source, resource]

    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            f"UPDATE sync_cursor SET {', '.join(sets)} WHERE source = %s AND resource = %s",  # noqa: S608
            params,
        )


def mark_error(source: str, resource: str) -> int:
    """Incrémente le compteur d'erreurs. Retourne sa nouvelle valeur."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE sync_cursor
               SET consecutive_errors = consecutive_errors + 1
             WHERE source = %s AND resource = %s
            RETURNING consecutive_errors
            """,
            (source, resource),
        )
        row = cur.fetchone()
    return row["consecutive_errors"] if row else 0
