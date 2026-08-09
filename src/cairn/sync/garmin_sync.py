"""Synchronisation Garmin Connect.

Deux boucles indépendantes :

* **activités** — parcours de la liste paginée, de la plus récente vers la
  plus ancienne. En incrémental on s'arrête dès qu'on atteint la borne du
  curseur ; en backfill on descend jusqu'au bout.
* **wellness** — un jour à la fois, plusieurs endpoints par jour. C'est le
  poste coûteux : environ 7 requêtes par journée d'historique. Le backfill
  traite un lot borné par passe et mémorise sa position, ce qui étale la
  reprise de l'historique sur plusieurs nuits sans marteler Garmin.

Chaque ressource est isolée : si l'endpoint HRV change de forme, le sommeil
et les activités du même jour rentrent quand même, et la passe se termine en
`partial` plutôt qu'en `failed`.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from typing import Any

import structlog

from cairn.config import get_settings
from cairn.db import connection, store_raw
from cairn.sources.garmin import GarminClient
from cairn.sync import repo
from cairn.sync.dedup import find_existing_activity, resolve_sport
from cairn.sync.mappers import (
    map_garmin_activity,
    map_garmin_daily_summary,
    map_garmin_heart_rate_readings,
    map_garmin_hrv,
    map_garmin_hrv_readings,
    map_garmin_laps,
    map_garmin_max_metrics,
    map_garmin_readiness,
    map_garmin_sleep,
    map_garmin_stress_readings,
    map_garmin_training_status,
    map_garmin_weight,
    parse_iso,
)
from cairn.sync.state import RunStats, get_cursor, mark_error, mark_success

log = structlog.get_logger(__name__)

SOURCE = "garmin"
PAGE_SIZE = 50

# Date la plus ancienne à explorer en backfill. Garmin Connect n'existait pas
# avant, inutile de descendre plus bas.
EPOCH = dt.date(2010, 1, 1)


# ---------------------------------------------------------------------
# Activités
# ---------------------------------------------------------------------


def _store_fit(client: GarminClient, conn, external_id: str) -> bool:
    """Télécharge et archive le .fit s'il n'est pas déjà sur disque."""
    if repo.has_activity_file(conn, SOURCE, external_id):
        return False

    blob = client.activity_fit(external_id)
    if not blob:
        return False

    settings = get_settings()
    relative = f"garmin/{external_id[:2]}/{external_id}.fit"
    target = settings.file_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(blob)
    repo.record_activity_file(
        conn,
        source=SOURCE,
        external_id=external_id,
        fmt="fit",
        path=relative,
        size_bytes=len(blob),
        sha256=hashlib.sha256(blob).digest(),
    )
    return True


def _ingest_activity(
    client: GarminClient, conn, athlete_id: int, summary: dict[str, Any], stats: RunStats
) -> None:
    external_id = str(summary.get("activityId"))
    payload_id, changed = store_raw(
        conn,
        source=SOURCE,
        endpoint="activity_summary",
        resource_key=external_id,
        payload=summary,
    )
    stats.items_fetched += 1

    existing_id = repo.activity_id_for_source(conn, SOURCE, external_id)
    if existing_id is not None and not changed:
        # Déjà en base et inchangé : on ne redescend ni les tours ni le FIT.
        return

    type_key = (summary.get("activityType") or {}).get("typeKey")
    sport, family = resolve_sport(conn, SOURCE, type_key)
    fields = map_garmin_activity(summary, sport, family)
    if fields.get("start_time") is None:
        log.warning("garmin: activité sans date de départ, ignorée", external_id=external_id)
        return

    if existing_id is None:
        existing_id = find_existing_activity(
            conn,
            athlete_id=athlete_id,
            start_time=fields["start_time"],
            sport_family=family,
        )

    activity_id, created = repo.upsert_activity(
        conn,
        athlete_id=athlete_id,
        source=SOURCE,
        external_id=external_id,
        external_url=f"https://connect.garmin.com/modern/activity/{external_id}",
        raw_payload_id=payload_id,
        fields=fields,
        existing_id=existing_id,
    )
    stats.items_upserted += 1
    if created:
        stats.detail["activities_created"] = stats.detail.get("activities_created", 0) + 1
    else:
        stats.detail["activities_merged"] = stats.detail.get("activities_merged", 0) + 1

    splits = client.activity_splits(external_id)
    if splits:
        store_raw(conn, source=SOURCE, endpoint="activity_splits",
                  resource_key=external_id, payload=splits)
        laps = map_garmin_laps(splits)
        if laps:
            repo.replace_laps(conn, activity_id, laps)

    if _store_fit(client, conn, external_id):
        stats.detail["fit_downloaded"] = stats.detail.get("fit_downloaded", 0) + 1


def sync_activities(client: GarminClient, athlete_id: int, mode: str, stats: RunStats) -> None:
    cursor = get_cursor(SOURCE, "activities")
    stop_at: dt.datetime | None = None
    if mode == "incremental" and cursor.get("incremental_from"):
        lookback = get_settings().incremental_lookback_days
        stop_at = cursor["incremental_from"] - dt.timedelta(days=lookback)

    newest_seen: dt.datetime | None = None
    start = 0
    while True:
        page = client.activities(start=start, limit=PAGE_SIZE)
        if not page:
            break

        for summary in page:
            with connection() as conn:
                _ingest_activity(client, conn, athlete_id, summary, stats)

        starts = [d for s in page if (d := parse_iso(s.get("startTimeGMT"))) is not None]
        if newest_seen is None and starts:
            newest_seen = max(starts)

        if stop_at is not None and starts and min(starts) < stop_at:
            break

        if len(page) < PAGE_SIZE:
            break
        start += PAGE_SIZE

    mark_success(
        SOURCE,
        "activities",
        incremental_from=newest_seen or dt.datetime.now(dt.UTC),
        backfill_done=True if mode == "backfill" else None,
    )


# ---------------------------------------------------------------------
# Wellness
# ---------------------------------------------------------------------


def _ingest_day(client: GarminClient, conn, athlete_id: int, day: dt.date, stats: RunStats) -> None:
    """Toutes les ressources wellness d'une journée.

    Une ressource en échec est journalisée et n'interrompt pas les autres.
    """
    key = day.isoformat()
    readings: list[tuple] = []
    failures: list[str] = []

    def attempt(name: str, fn) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — isolation volontaire
            failures.append(name)
            log.warning("garmin: ressource en échec", resource=name, day=key, error=str(exc))

    def do_sleep() -> None:
        payload = client.sleep(day)
        if not payload:
            return
        store_raw(conn, source=SOURCE, endpoint="sleep", resource_key=key, payload=payload)
        values, stages = map_garmin_sleep(payload)
        if values:
            repo.upsert_sleep(conn, athlete_id, day, values, stages)

    def do_hrv() -> None:
        payload = client.hrv(day)
        if not payload:
            return
        store_raw(conn, source=SOURCE, endpoint="hrv", resource_key=key, payload=payload)
        values = map_garmin_hrv(payload)
        if values:
            repo.upsert_hrv(conn, athlete_id, day, values)
        readings.extend(map_garmin_hrv_readings(payload))

    def do_summary() -> None:
        payload = client.daily_summary(day)
        if not payload:
            return
        store_raw(conn, source=SOURCE, endpoint="daily_summary", resource_key=key, payload=payload)
        repo.upsert_daily_wellness(conn, athlete_id, day, map_garmin_daily_summary(payload))

    def do_stress() -> None:
        payload = client.stress(day)
        if not payload:
            return
        store_raw(conn, source=SOURCE, endpoint="stress", resource_key=key, payload=payload)
        readings.extend(map_garmin_stress_readings(payload))

    def do_heart_rate() -> None:
        payload = client.heart_rate(day)
        if not payload:
            return
        store_raw(conn, source=SOURCE, endpoint="heart_rate", resource_key=key, payload=payload)
        readings.extend(map_garmin_heart_rate_readings(payload))

    def do_readiness() -> None:
        payload = client.training_readiness(day)
        if not payload:
            return
        store_raw(conn, source=SOURCE, endpoint="readiness", resource_key=key, payload=payload)
        repo.upsert_daily_wellness(conn, athlete_id, day, map_garmin_readiness(payload))

    def do_training_status() -> None:
        payload = client.training_status(day)
        if not payload:
            return
        store_raw(conn, source=SOURCE, endpoint="training_status",
                  resource_key=key, payload=payload)
        repo.upsert_daily_wellness(conn, athlete_id, day, map_garmin_training_status(payload))

    for name, fn in (
        ("sleep", do_sleep),
        ("hrv", do_hrv),
        ("daily_summary", do_summary),
        ("stress", do_stress),
        ("heart_rate", do_heart_rate),
        ("readiness", do_readiness),
        ("training_status", do_training_status),
    ):
        attempt(name, fn)

    if readings:
        repo.insert_wellness_readings(conn, athlete_id, readings)
        stats.detail["wellness_readings"] = (
            stats.detail.get("wellness_readings", 0) + len(readings)
        )

    stats.items_fetched += 1
    if failures:
        stats.detail.setdefault("failed_resources", {})
        for name in failures:
            stats.detail["failed_resources"][name] = (
                stats.detail["failed_resources"].get(name, 0) + 1
            )
    else:
        stats.items_upserted += 1


def _wellness_days(mode: str, cursor: dict[str, Any]) -> list[dt.date]:
    """Jours à traiter pour cette passe."""
    settings = get_settings()
    today = dt.date.today()

    if mode == "incremental":
        span = settings.incremental_lookback_days
        return [today - dt.timedelta(days=i) for i in range(1, span + 1)]

    # Backfill : on descend vers le passé depuis la position mémorisée.
    if cursor.get("backfill_done"):
        return []
    anchor = cursor.get("backfill_cursor") or today
    count = settings.backfill_days_per_run
    return [anchor - dt.timedelta(days=i) for i in range(1, count + 1)]


def sync_wellness(client: GarminClient, athlete_id: int, mode: str, stats: RunStats) -> None:
    cursor = get_cursor(SOURCE, "wellness")
    days = _wellness_days(mode, cursor)
    if not days:
        log.info("garmin: backfill wellness déjà terminé")
        return

    for day in days:
        with connection() as conn:
            _ingest_day(client, conn, athlete_id, day, stats)

    oldest = min(days)
    if mode == "backfill":
        done = oldest <= EPOCH
        mark_success(SOURCE, "wellness", backfill_cursor=oldest, backfill_done=done)
        log.info("garmin: backfill wellness avancé", jusqu_a=oldest.isoformat(), termine=done)
    else:
        mark_success(SOURCE, "wellness")


# ---------------------------------------------------------------------
# Ressources sur plage de dates
# ---------------------------------------------------------------------


def sync_ranged(client: GarminClient, athlete_id: int, days: int, stats: RunStats) -> None:
    """VO2max et poids : endpoints acceptant une plage, donc peu coûteux.

    Garmin limite ces plages ; on découpe par tranches d'un an.
    """
    end = dt.date.today()
    oldest = end - dt.timedelta(days=days)

    while end > oldest:
        start = max(oldest, end - dt.timedelta(days=365))
        key = f"{start.isoformat()}:{end.isoformat()}"

        try:
            payload = client.max_metrics(start, end)
            if payload:
                with connection() as conn:
                    store_raw(conn, source=SOURCE, endpoint="max_metrics",
                              resource_key=key, payload=payload)
                    for day, values in map_garmin_max_metrics(payload).items():
                        repo.upsert_daily_wellness(conn, athlete_id, day, values)
        except Exception as exc:  # noqa: BLE001 — ressource optionnelle
            log.warning("garmin: max_metrics en échec", range=key, error=str(exc))

        try:
            payload = client.weight(start, end)
            if payload:
                with connection() as conn:
                    store_raw(conn, source=SOURCE, endpoint="weight",
                              resource_key=key, payload=payload)
                    for entry in map_garmin_weight(payload):
                        repo.upsert_body_measurement(conn, athlete_id, entry)
                        stats.items_upserted += 1
        except Exception as exc:  # noqa: BLE001 — ressource optionnelle
            log.warning("garmin: poids en échec", range=key, error=str(exc))

        end = start - dt.timedelta(days=1)


# ---------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------


def run(mode: str = "incremental") -> RunStats:
    """Passe complète Garmin. `mode` vaut 'incremental' ou 'backfill'."""
    settings = get_settings()
    stats = RunStats()

    client = GarminClient(domain=settings.garmin_domain)
    client.load_saved()

    profile = client.display_name
    with connection() as conn:
        athlete_id = repo.get_or_create_athlete(
            conn,
            display_name=profile or "athlete",
            garmin_user_id=profile,
            timezone=settings.timezone,
        )

    try:
        sync_activities(client, athlete_id, mode, stats)
    except Exception:
        mark_error(SOURCE, "activities")
        raise
    finally:
        stats.api_calls = client.api_calls

    try:
        sync_wellness(client, athlete_id, mode, stats)
    except Exception:
        mark_error(SOURCE, "wellness")
        raise
    finally:
        stats.api_calls = client.api_calls

    span = 365 * 10 if mode == "backfill" else 90
    sync_ranged(client, athlete_id, span, stats)
    stats.api_calls = client.api_calls
    return stats
