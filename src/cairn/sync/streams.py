"""Ingestion des streams 1 Hz et métriques dérivées d'une activité.

Trois choses sont produites à partir du .fit :

* les points seconde par seconde dans `activity_stream` (hypertable) ;
* le temps passé par zone de FC, précalculé pour que Grafana n'ait pas à
  rescanner la série à chaque affichage ;
* un TRIMP, qui sert de charge d'entraînement quand Garmin n'en fournit pas
  (activité importée dans Strava depuis un autre appareil, par exemple).
"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path
from typing import Any

import structlog

from cairn.config import get_settings
from cairn.db import connection
from cairn.sync import repo
from cairn.sync.fit import parse_fit, to_stream_rows

log = structlog.get_logger(__name__)

STREAM_COLUMNS = (
    "activity_id", "time", "elapsed_s", "hr", "cadence", "power_w", "speed_mps",
    "altitude_m", "distance_m", "grade_pct", "temperature_c", "lat", "lon", "moving",
)

# Bornes de zones Garmin par défaut, en pourcentage de la FC maximale.
DEFAULT_ZONE_BOUNDS = (0.50, 0.60, 0.70, 0.80, 0.90)

# Coefficient du TRIMP de Banister. Il diffère selon le sexe car la relation
# entre fréquence cardiaque de réserve et lactatémie n'est pas la même.
TRIMP_K = {"M": 1.92, "F": 1.67}


# ---------------------------------------------------------------------
# Zones de fréquence cardiaque
# ---------------------------------------------------------------------


def zones_from_hr_max(hr_max: int, hr_rest: int | None = None) -> list[tuple[int, int, int]]:
    """Zones dérivées de la FC max, quand aucun jeu de zones n'est connu."""
    bounds = [int(round(hr_max * pct)) for pct in DEFAULT_ZONE_BOUNDS]
    zones = []
    for index, low in enumerate(bounds, start=1):
        high = bounds[index] - 1 if index < len(bounds) else hr_max
        zones.append((index, low, high))
    return zones


def get_zones(conn, athlete_id: int, day: dt.date) -> list[tuple[int, int, int]]:
    """Zones en vigueur à cette date, ou dérivées de la FC max si besoin."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, hr_max, hr_rest FROM hr_zone_set
             WHERE athlete_id = %s AND valid_from <= %s
             ORDER BY valid_from DESC LIMIT 1
            """,
            (athlete_id, day),
        )
        zone_set = cur.fetchone()
        if zone_set is None:
            return []

        cur.execute(
            """
            SELECT zone_index, min_bpm, max_bpm FROM hr_zone
             WHERE zone_set_id = %s ORDER BY zone_index
            """,
            (zone_set["id"],),
        )
        rows = cur.fetchall()

    if rows:
        return [(r["zone_index"], r["min_bpm"], r["max_bpm"]) for r in rows]
    return zones_from_hr_max(zone_set["hr_max"], zone_set["hr_rest"])


def compute_zone_time(
    records: list[dict[str, Any]], zones: list[tuple[int, int, int]]
) -> dict[int, int]:
    """Secondes passées dans chaque zone.

    L'intervalle entre deux points est mesuré réellement plutôt que supposé
    à une seconde : les montres passent en enregistrement intelligent sur les
    longues sorties, avec des écarts de plusieurs secondes. Un trou de plus
    d'une minute est ignoré — c'est une pause, pas du temps d'effort.
    """
    if not records or not zones:
        return {}

    seconds: dict[int, int] = {}
    for current, following in zip(records, records[1:], strict=False):
        hr = current.get("hr")
        if hr is None:
            continue
        delta = (following["time"] - current["time"]).total_seconds()
        if delta <= 0 or delta > 60:
            continue
        for index, low, high in zones:
            if low <= hr <= high:
                seconds[index] = seconds.get(index, 0) + int(delta)
                break
    return seconds


def compute_trimp(
    records: list[dict[str, Any]],
    hr_rest: int,
    hr_max: int,
    sex: str | None = None,
) -> float | None:
    """TRIMP de Banister, pondéré exponentiellement par l'intensité.

    Une heure de sortie facile ne compte pas comme une heure d'intervalles :
    c'est tout l'intérêt par rapport à une simple durée.
    """
    if not records or hr_max <= hr_rest:
        return None

    k = TRIMP_K.get((sex or "M").upper(), TRIMP_K["M"])
    total = 0.0
    counted = False

    for current, following in zip(records, records[1:], strict=False):
        hr = current.get("hr")
        if hr is None:
            continue
        delta = (following["time"] - current["time"]).total_seconds()
        if delta <= 0 or delta > 60:
            continue

        reserve = (hr - hr_rest) / (hr_max - hr_rest)
        if reserve <= 0:
            continue
        reserve = min(reserve, 1.2)  # borne les pics de capteur aberrants
        total += (delta / 60.0) * reserve * 0.64 * math.exp(k * reserve)
        counted = True

    return round(total, 2) if counted else None


# ---------------------------------------------------------------------
# Écriture
# ---------------------------------------------------------------------


def ingest_streams(conn, activity_id: int, records: list[dict[str, Any]]) -> int:
    """Remplace les streams d'une activité. Retourne le nombre de points.

    Le remplacement est intégral : re-parser un .fit après correction du
    parser doit donner exactement le même résultat qu'un premier passage.
    TimescaleDB décompresse à la volée les chunks concernés si l'activité est
    ancienne.
    """
    rows = to_stream_rows(activity_id, records)
    if not rows:
        return 0

    with conn.cursor() as cur:
        cur.execute("DELETE FROM activity_stream WHERE activity_id = %s", (activity_id,))
        columns = ", ".join(STREAM_COLUMNS)
        with cur.copy(f"COPY activity_stream ({columns}) FROM STDIN") as copy:  # noqa: S608
            for row in rows:
                copy.write_row(row)

        cur.execute(
            "UPDATE activity SET has_streams = true, updated_at = now() WHERE id = %s",
            (activity_id,),
        )
    return len(rows)


def store_zone_time(conn, activity_id: int, seconds: dict[int, int]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM activity_zone_time WHERE activity_id = %s AND metric = 'hr'",
            (activity_id,),
        )
        if not seconds:
            return
        cur.executemany(
            """
            INSERT INTO activity_zone_time (activity_id, metric, zone_index, seconds)
            VALUES (%s, 'hr', %s, %s)
            """,
            [(activity_id, index, value) for index, value in sorted(seconds.items())],
        )


def _athlete_reference(conn, athlete_id: int, day: dt.date) -> tuple[int | None, int | None, str]:
    """FC de repos et FC max de référence pour le calcul du TRIMP.

    La FC de repos est celle mesurée autour de la date de l'activité, pas une
    constante : elle bouge avec la forme et la saison.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT round(avg(resting_hr))::int AS rhr FROM daily_wellness
             WHERE athlete_id = %s AND day BETWEEN %s AND %s AND resting_hr IS NOT NULL
            """,
            (athlete_id, day - dt.timedelta(days=30), day + dt.timedelta(days=30)),
        )
        row = cur.fetchone()
        hr_rest = row["rhr"] if row else None

        cur.execute(
            """
            SELECT hr_max, hr_rest FROM hr_zone_set
             WHERE athlete_id = %s AND valid_from <= %s
             ORDER BY valid_from DESC LIMIT 1
            """,
            (athlete_id, day),
        )
        zone_set = cur.fetchone()

        cur.execute("SELECT sex FROM athlete WHERE id = %s", (athlete_id,))
        athlete = cur.fetchone()

    hr_max = zone_set["hr_max"] if zone_set else None
    if hr_rest is None and zone_set:
        hr_rest = zone_set["hr_rest"]
    return hr_rest, hr_max, (athlete or {}).get("sex") or "M"


def process_file(conn, file_row: dict[str, Any]) -> dict[str, Any]:
    """Parse un .fit archivé et alimente streams, zones et TRIMP."""
    settings = get_settings()
    path = Path(settings.file_root) / file_row["path"]
    result = {"points": 0, "zones": 0, "trimp": None}

    if not path.exists():
        log.warning("fit: fichier introuvable", path=str(path))
        return result

    activity_id = repo.activity_id_for_source(conn, file_row["source"], file_row["external_id"])
    if activity_id is None:
        log.warning(
            "fit: aucune activité rattachée",
            source=file_row["source"],
            external_id=file_row["external_id"],
        )
        return result

    parsed = parse_fit(path)
    if not parsed.records:
        log.warning("fit: aucun point exploitable", path=str(path))
        return result

    result["points"] = ingest_streams(conn, activity_id, parsed.records)

    with conn.cursor() as cur:
        cur.execute("SELECT athlete_id, local_date FROM activity WHERE id = %s", (activity_id,))
        activity = cur.fetchone()
    if activity is None:
        return result

    athlete_id, day = activity["athlete_id"], activity["local_date"]

    zone_seconds = compute_zone_time(parsed.records, get_zones(conn, athlete_id, day))
    store_zone_time(conn, activity_id, zone_seconds)
    result["zones"] = len(zone_seconds)

    hr_rest, hr_max, sex = _athlete_reference(conn, athlete_id, day)
    if hr_rest is not None and hr_max is not None:
        trimp = compute_trimp(parsed.records, hr_rest, hr_max, sex)
        if trimp is not None:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE activity SET trimp = %s, updated_at = now() WHERE id = %s",
                    (trimp, activity_id),
                )
            result["trimp"] = trimp

    if not parsed.laps:
        return result

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM activity_lap WHERE activity_id = %s", (activity_id,))
        existing = cur.fetchone()["n"]
    if existing == 0:
        repo.replace_laps(conn, activity_id, parsed.laps)

    return result


def backfill(limit: int = 200) -> dict[str, int]:
    """Traite les .fit archivés pas encore parsés."""
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, source, external_id, path FROM raw.activity_file
             WHERE parsed_at IS NULL AND format = 'fit'
             ORDER BY downloaded_at DESC LIMIT %s
            """,
            (limit,),
        )
        pending = cur.fetchall()

    totals = {"files": 0, "points": 0, "failed": 0}
    for file_row in pending:
        try:
            with connection() as conn:
                outcome = process_file(conn, file_row)
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE raw.activity_file SET parsed_at = now() WHERE id = %s",
                        (file_row["id"],),
                    )
            totals["files"] += 1
            totals["points"] += outcome["points"]
        except Exception as exc:  # noqa: BLE001 — un fichier abîmé n'arrête pas le lot
            totals["failed"] += 1
            log.error(
                "fit: traitement en échec",
                path=file_row["path"],
                error=str(exc),
                exc_info=True,
            )

    log.info("fit: lot traité", **totals)
    return totals
