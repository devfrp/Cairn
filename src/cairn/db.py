"""Accès à la base : SQLite, un seul fichier, aucun serveur.

L'application transporte ses données avec elle. SQLite convient à cet usage
mono-utilisateur : les volumes en jeu — quelques millions de points de série
sur plusieurs années — ne le mettent pas en difficulté, et il n'y a rien à
installer ni à démarrer.

Conventions de typage, puisque SQLite n'a ni date ni booléen :

* les horodatages sont stockés en texte ISO 8601 UTC, ce qui rend la
  comparaison lexicographique équivalente à la comparaison chronologique ;
* les dates en `AAAA-MM-JJ` ;
* les booléens en entiers 0/1.

Les adaptateurs enregistrés plus bas rendent ces conversions invisibles au
reste du code, qui manipule des objets Python.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import structlog

from cairn.config import get_settings

log = structlog.get_logger(__name__)

_local = threading.local()
_write_lock = threading.Lock()


# ---------------------------------------------------------------------
# Conversion des types
# ---------------------------------------------------------------------


def _adapt_datetime(value: dt.datetime) -> str:
    # Un horodatage sans fuseau vient toujours d'une heure locale d'activité,
    # que l'on conserve telle quelle ; sinon on normalise en UTC.
    if value.tzinfo is None:
        return value.isoformat(sep=" ", timespec="seconds")
    return value.astimezone(dt.UTC).isoformat(sep=" ", timespec="seconds")


def _convert_timestamp(raw: bytes) -> dt.datetime | None:
    text = raw.decode("utf-8")
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def _convert_naive(raw: bytes) -> dt.datetime | None:
    """Horodatage local d'activité : volontairement sans fuseau."""
    text = raw.decode("utf-8")
    try:
        return dt.datetime.fromisoformat(text)
    except ValueError:
        return None


def _convert_date(raw: bytes) -> dt.date | None:
    try:
        return dt.date.fromisoformat(raw.decode("utf-8")[:10])
    except ValueError:
        return None


sqlite3.register_adapter(dt.datetime, _adapt_datetime)
sqlite3.register_adapter(dt.date, lambda value: value.isoformat())
sqlite3.register_converter("TIMESTAMP", _convert_timestamp)
sqlite3.register_converter("LOCALTIME", _convert_naive)
sqlite3.register_converter("DATE", _convert_date)


class Row(sqlite3.Row):
    """Ligne accessible par nom, et convertible en dictionnaire."""

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except (IndexError, KeyError):
            return default

    def keys(self) -> list[str]:  # type: ignore[override]
        return list(super().keys())

    def __contains__(self, key: object) -> bool:
        return key in list(super().keys())


# ---------------------------------------------------------------------
# Connexions
# ---------------------------------------------------------------------


def database_path() -> Path:
    return Path(get_settings().db_path).expanduser()


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Journal WAL : la synchronisation écrit pendant que l'interface lit,
    # sans que l'une bloque l'autre.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA temp_store = MEMORY")
    # 64 Mo de cache : les agrégations sur plusieurs mois de séries y gagnent
    # beaucoup, pour une empreinte mémoire négligeable sur un poste moderne.
    conn.execute("PRAGMA cache_size = -64000")


def _connect() -> sqlite3.Connection:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path),
        detect_types=sqlite3.PARSE_DECLTYPES,
        timeout=10.0,
        isolation_level=None,  # transactions pilotées explicitement
    )
    _configure(conn)
    return conn


def get_connection() -> sqlite3.Connection:
    """Connexion propre au fil d'exécution courant.

    Une connexion SQLite n'est pas partageable entre fils : chacun a la
    sienne, ce qui évite tout verrouillage croisé.
    """
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _connect()
        _local.conn = conn
    return conn


def close_pool() -> None:
    """Ferme la connexion du fil courant. Nommée ainsi par continuité d'usage."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    """Transaction : validée si tout va bien, annulée sinon."""
    conn = get_connection()
    conn.execute("BEGIN")
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


@contextmanager
def read_only() -> Iterator[sqlite3.Connection]:
    """Lecture seule : pas de transaction, donc pas de verrou d'écriture."""
    yield get_connection()


def fetch_all(sql: str, params: Sequence[Any] | dict | None = None) -> list[Row]:
    cur = get_connection().execute(sql, params or ())
    try:
        return cur.fetchall()
    finally:
        cur.close()


def fetch_one(sql: str, params: Sequence[Any] | dict | None = None) -> Row | None:
    cur = get_connection().execute(sql, params or ())
    try:
        return cur.fetchone()
    finally:
        cur.close()


def execute(sql: str, params: Sequence[Any] | dict | None = None) -> int:
    with _write_lock, connection() as conn:
        cur = conn.execute(sql, params or ())
        try:
            return cur.rowcount
        finally:
            cur.close()


# ---------------------------------------------------------------------
# Couche raw
# ---------------------------------------------------------------------


def payload_hash(payload: Any) -> bytes:
    """Hash stable d'un payload JSON, insensible à l'ordre des clés."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).digest()


def store_raw(
    conn: sqlite3.Connection,
    *,
    source: str,
    endpoint: str,
    resource_key: str,
    payload: Any,
    http_status: int = 200,
) -> tuple[int, bool]:
    """Archive une réponse d'API. Retourne (id du payload, contenu nouveau ?).

    Le conflit porte sur (source, endpoint, resource_key, content_hash) :
    resynchroniser une ressource inchangée ne crée pas de doublon et signale
    `False`, ce qui permet aux appelants de sauter la transformation.
    """
    digest = payload_hash(payload)
    cur = conn.execute(
        """
        INSERT INTO raw_api_payload
               (source, endpoint, resource_key, http_status, content_hash, payload)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (source, endpoint, resource_key, content_hash) DO NOTHING
        RETURNING id
        """,
        (source, endpoint, resource_key, http_status, digest,
         json.dumps(payload, default=str)),
    )
    row = cur.fetchone()
    cur.close()
    if row is not None:
        return row["id"], True

    existing = conn.execute(
        """
        SELECT id FROM raw_api_payload
         WHERE source = ? AND endpoint = ? AND resource_key = ? AND content_hash = ?
        """,
        (source, endpoint, resource_key, digest),
    ).fetchone()
    assert existing is not None  # garanti par le ON CONFLICT ci-dessus
    return existing["id"], False


def as_date(value: Any) -> dt.date | None:
    """Convertit en date une valeur venue d'une expression SQL.

    La conversion automatique ne s'applique qu'aux colonnes déclarées : dès
    qu'une date passe par `min()`, `max()` ou une sous-requête, SQLite rend
    la chaîne brute. Cette fonction rattrape ces cas.
    """
    if value is None or isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.datetime):
        return value.date()
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def load_json(value: Any) -> Any:
    """Décode une colonne JSON stockée en texte."""
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None
