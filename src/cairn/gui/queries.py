"""Lectures destinées à l'affichage.

Séparées du reste pour une raison : l'interface doit rester utilisable face à
une base absente, vide ou pas encore migrée. Chaque fonction renvoie donc une
structure exploitable plutôt que de laisser filer une exception, et
`probe()` dit à l'application dans quel état elle se trouve.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from cairn.db import fetch_all, fetch_one


@dataclass
class Health:
    reachable: bool = False
    migrated: bool = False
    detail: str = ""
    engine: str | None = None

    @property
    def usable(self) -> bool:
        return self.reachable and self.migrated


def probe() -> Health:
    """État de la base, sans jamais lever."""
    try:
        row = fetch_one("SELECT 1 AS ok")
    except Exception as exc:  # noqa: BLE001 — diagnostic, pas de propagation
        return Health(detail=f"{type(exc).__name__} : {exc}")

    if row is None:
        return Health(detail="Réponse inattendue de la base.")

    health = Health(reachable=True)
    try:
        found = fetch_one(
            """
            SELECT count(*) AS n FROM sqlite_master
             WHERE type = 'table' AND name IN ('activity', 'sleep_session')
            """
        )
        health.migrated = bool(found and found["n"] == 2)
        if not health.migrated:
            health.detail = "Fichier de données créé, mais le schéma n'est pas installé."
    except Exception as exc:  # noqa: BLE001
        health.detail = f"{type(exc).__name__} : {exc}"
        return health

    health.engine = f"SQLite {sqlite3.sqlite_version}"
    return health


def _safe(query: str, params: tuple | None = None) -> list[dict[str, Any]]:
    try:
        return fetch_all(query, params)
    except (sqlite3.Error, OSError):
        return []


@dataclass
class Recovery:
    day: dt.date | None = None
    hrv: float | None = None
    hrv_status: str | None = None
    hrv_series: list[float | None] = field(default_factory=list)
    resting_hr: int | None = None
    rhr_series: list[float | None] = field(default_factory=list)
    sleep_score: int | None = None
    sleep_duration_s: int | None = None
    body_battery: int | None = None
    readiness: int | None = None


def recovery(days: int = 30) -> Recovery:
    """Dernier état de récupération connu, et sa tendance."""
    rows = _safe(
        """
        WITH RECURSIVE calendar(day) AS (
            SELECT date('now', 'localtime', ? || ' days')
            UNION ALL
            SELECT date(day, '+1 day') FROM calendar
             WHERE day < date('now', 'localtime')
        )
        SELECT calendar.day AS day, h.avg_hrv_ms, h.status, w.resting_hr,
               s.score AS sleep_score, s.duration_s, w.body_battery_high,
               w.training_readiness
          FROM calendar
          LEFT JOIN hrv_night      h ON h.day        = calendar.day
          LEFT JOIN daily_wellness w ON w.day        = calendar.day
          LEFT JOIN sleep_session  s ON s.sleep_date = calendar.day
         ORDER BY calendar.day
        """,
        (-days,),
    )
    if not rows:
        return Recovery()

    result = Recovery(
        hrv_series=[r["avg_hrv_ms"] for r in rows],
        rhr_series=[r["resting_hr"] for r in rows],
    )
    # La journée en cours est souvent incomplète : on affiche la dernière
    # mesure réellement disponible plutôt qu'une case vide.
    for entry in reversed(rows):
        if result.hrv is None and entry["avg_hrv_ms"] is not None:
            result.hrv = float(entry["avg_hrv_ms"])
            result.hrv_status = entry["status"]
            result.day = entry["day"]
        if result.resting_hr is None and entry["resting_hr"] is not None:
            result.resting_hr = entry["resting_hr"]
        if result.sleep_score is None and entry["sleep_score"] is not None:
            result.sleep_score = entry["sleep_score"]
            result.sleep_duration_s = entry["duration_s"]
        if result.body_battery is None and entry["body_battery_high"] is not None:
            result.body_battery = entry["body_battery_high"]
        if result.readiness is None and entry["training_readiness"] is not None:
            result.readiness = entry["training_readiness"]
    return result


@dataclass
class Load:
    week_sessions: int = 0
    week_duration_s: int = 0
    week_distance_m: float = 0.0
    week_elevation_m: float = 0.0
    week_load: float = 0.0
    previous_week_load: float = 0.0
    month_load: float = 0.0
    daily_series: list[float | None] = field(default_factory=list)

    @property
    def trend_pct(self) -> float | None:
        """Écart de charge entre les sept derniers jours et les sept d'avant."""
        if not self.previous_week_load:
            return None
        return (self.week_load - self.previous_week_load) / self.previous_week_load * 100


def load(days: int = 30) -> Load:
    series = _safe(
        """
        WITH RECURSIVE calendar(day) AS (
            SELECT date('now', 'localtime', ? || ' days')
            UNION ALL
            SELECT date(day, '+1 day') FROM calendar
             WHERE day < date('now', 'localtime')
        )
        SELECT calendar.day AS day,
               coalesce(sum(coalesce(a.training_load, a.trimp)), 0) AS load
          FROM calendar
          LEFT JOIN activity a ON a.local_date = calendar.day
         GROUP BY calendar.day ORDER BY calendar.day
        """,
        (-days,),
    )
    totals = _safe(
        """
        SELECT count(*) AS sessions,
               coalesce(sum(elapsed_s), 0) AS duration_s,
               coalesce(sum(distance_m), 0) AS distance_m,
               coalesce(sum(elevation_gain_m), 0) AS elevation_m,
               coalesce(sum(coalesce(training_load, trimp)), 0) AS load
          FROM activity
         WHERE local_date > date('now', 'localtime', '-7 days')
        """
    )
    previous = _safe(
        """
        SELECT coalesce(sum(coalesce(training_load, trimp)), 0) AS load
          FROM activity
         WHERE local_date > date('now', 'localtime', '-14 days')
           AND local_date <= date('now', 'localtime', '-7 days')
        """
    )
    month = _safe(
        """
        SELECT coalesce(sum(coalesce(training_load, trimp)), 0) AS load
          FROM activity WHERE local_date > date('now', 'localtime', '-28 days')
        """
    )

    result = Load(daily_series=[float(r["load"] or 0) for r in series])
    if totals:
        row = totals[0]
        result.week_sessions = row["sessions"]
        result.week_duration_s = int(row["duration_s"] or 0)
        result.week_distance_m = float(row["distance_m"] or 0)
        result.week_elevation_m = float(row["elevation_m"] or 0)
        result.week_load = float(row["load"] or 0)
    if previous:
        result.previous_week_load = float(previous[0]["load"] or 0)
    if month:
        result.month_load = float(month[0]["load"] or 0)
    return result


def recent_activities(limit: int = 60) -> list[dict[str, Any]]:
    """Dernières sorties, sources rétablies en liste.

    SQLite ne sait pas agréger en tableau : `group_concat` rend une chaîne,
    qu'on redécoupe ici pour que l'affichage garde un contrat stable.
    """
    rows = _safe(
        """
        SELECT a.start_time_local, a.sport, a.name, a.distance_m, a.elevation_gain_m,
               a.elapsed_s, a.avg_hr, a.max_hr,
               coalesce(a.training_load, a.trimp) AS load,
               a.has_streams, g.name AS gear_name,
               group_concat(DISTINCT s.source) AS sources
          FROM activity a
          LEFT JOIN gear g ON g.id = a.gear_id
          LEFT JOIN activity_source s ON s.activity_id = a.id
         GROUP BY a.id, g.name
         ORDER BY a.start_time DESC
         LIMIT ?
        """,
        (limit,),
    )

    result = []
    for row in rows:
        entry = dict(zip(row.keys(), tuple(row), strict=True))
        raw = entry.get("sources") or ""
        entry["sources"] = sorted(s for s in str(raw).split(",") if s)
        result.append(entry)
    return result


def counts() -> dict[str, int]:
    row = _safe(
        """
        SELECT (SELECT count(*) FROM activity) AS activities,
               (SELECT count(*) FROM activity WHERE has_streams) AS with_streams,
               (SELECT count(*) FROM sleep_session) AS nights,
               (SELECT count(*) FROM raw.api_payload) AS payloads,
               (SELECT count(*) FROM gear) AS gear
        """
    )
    return row[0] if row else {}


def cursors() -> list[dict[str, Any]]:
    return _safe(
        """
        SELECT source, resource, last_success_at, backfill_cursor,
               backfill_done, consecutive_errors
          FROM sync_cursor ORDER BY source, resource
        """
    )


def recent_runs(limit: int = 12) -> list[dict[str, Any]]:
    return _safe(
        """
        SELECT source, mode, status, started_at, finished_at,
               api_calls, items_upserted, error
          FROM sync_run ORDER BY started_at DESC LIMIT ?
        """,
        (limit,),
    )


def credentials_present() -> dict[str, bool]:
    rows = _safe("SELECT source FROM credential")
    found = {r["source"] for r in rows}
    return {"garmin": "garmin" in found, "strava": "strava" in found}


def zone_distribution(days: int = 30) -> list[dict[str, Any]]:
    """Temps cumulé par zone de fréquence cardiaque sur la période."""
    return _safe(
        """
        SELECT z.zone_index, sum(z.seconds) AS seconds
          FROM activity_zone_time z
          JOIN activity a ON a.id = z.activity_id
         WHERE z.metric = 'hr'
           AND a.local_date > date('now', 'localtime', ? || ' days')
         GROUP BY z.zone_index ORDER BY z.zone_index
        """,
        (-days,),
    )


def sport_breakdown(days: int = 30) -> list[dict[str, Any]]:
    """Volume par discipline, du plus pratiqué au moins pratiqué."""
    return _safe(
        """
        SELECT sport,
               count(*) AS sessions,
               coalesce(sum(elapsed_s), 0) AS duration_s,
               coalesce(sum(distance_m), 0) AS distance_m,
               coalesce(sum(elevation_gain_m), 0) AS elevation_m
          FROM activity
         WHERE local_date > date('now', 'localtime', ? || ' days')
         GROUP BY sport ORDER BY duration_s DESC
        """,
        (-days,),
    )


def weekly_load(days: int = 90) -> list[dict[str, Any]]:
    """Charge agrégée par semaine ISO.

    `strftime('%W')` numérote les semaines à partir du lundi, ce qui
    correspond au découpage utilisé partout ailleurs dans Cairn.
    """
    return _safe(
        """
        SELECT strftime('%Y-%W', local_date) AS week,
               coalesce(sum(coalesce(training_load, trimp)), 0) AS load,
               count(*) AS sessions
          FROM activity
         WHERE local_date > date('now', 'localtime', ? || ' days')
         GROUP BY week ORDER BY week
        """,
        (-days,),
    )
