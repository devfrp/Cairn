"""Synchronisation Strava.

Le quota est la contrainte dominante : une centaine de requêtes par quart
d'heure, un millier par jour. La liste d'activités en renvoie 200 par
requête, mais le détail d'une activité coûte une requête chacune — un
backfill complet en détail brûlerait plusieurs jours de quota.

Compromis retenu : le détail n'est demandé que lorsqu'il apporte vraiment
quelque chose, c'est-à-dire pour les activités que Garmin ne connaît pas
(importées d'ailleurs) et pour les activités récentes. Pour les autres, le
résumé de liste suffit — Garmin fournit déjà les capteurs, et Strava n'ajoute
que le matériel et le nom, tous deux présents dans le résumé.

Quand le quota journalier tombe, la passe s'arrête proprement : le curseur
retient la position et la nuit suivante reprend là.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import structlog

from cairn.config import get_settings
from cairn.db import connection, store_raw
from cairn.sources.strava import StravaClient, StravaDailyQuotaExhausted
from cairn.sync import repo
from cairn.sync.dedup import find_existing_activity, resolve_sport
from cairn.sync.mappers import (
    map_strava_activity,
    map_strava_gear,
    map_strava_laps,
    parse_iso,
    to_float,
    to_int,
)
from cairn.sync.state import RunStats, get_cursor, mark_error, mark_success

log = structlog.get_logger(__name__)

SOURCE = "strava"
PAGE_SIZE = 200

# Strava n'existait pas avant 2009 : borne de fin du backfill.
EPOCH = dt.datetime(2009, 1, 1, tzinfo=dt.UTC)

# Au-delà de cet âge, on ne redemande pas le détail d'une activité déjà
# couverte par Garmin.
DETAIL_RECENT_DAYS = 30


def sync_gear(client: StravaClient, athlete_id: int, stats: RunStats) -> dict[str, int]:
    """Vélos et chaussures. Retourne {strava_gear_id: id interne}."""
    profile = client.athlete()
    if not profile:
        return {}

    with connection() as conn:
        store_raw(conn, source=SOURCE, endpoint="athlete",
                  resource_key=str(profile.get("id")), payload=profile)

    mapping: dict[str, int] = {}
    with connection() as conn:
        for item in list(profile.get("bikes") or []) + list(profile.get("shoes") or []):
            if not isinstance(item, dict) or not item.get("id"):
                continue
            gear_pk = repo.upsert_gear(conn, athlete_id, map_strava_gear(item))
            mapping[str(item["id"])] = gear_pk
            stats.items_upserted += 1
    log.info("strava: matériel synchronisé", count=len(mapping))
    return mapping


def sync_zones(client: StravaClient, athlete_id: int, stats: RunStats) -> None:
    """Zones de FC déclarées sur Strava.

    Strava borne la dernière zone à -1 (« et au-delà ») et ne publie pas la
    FC maximale. On la reprend donc de la plus haute réellement atteinte en
    course sur l'année écoulée, et à défaut on l'estime depuis le seuil bas
    de la dernière zone, qui vaut environ 90 % de la FC max.
    """
    payload = client.zones()
    hr_zones = (payload or {}).get("heart_rate") or {}
    raw_zones = hr_zones.get("zones") or []
    if not raw_zones:
        log.info("strava: aucune zone de FC déclarée")
        return

    with connection() as conn:
        store_raw(conn, source=SOURCE, endpoint="zones",
                  resource_key=str(athlete_id), payload=payload)

        observed = repo.observed_hr_max(
            conn, athlete_id, dt.date.today() - dt.timedelta(days=365)
        )
        last_floor = to_int(raw_zones[-1].get("min")) or 0
        hr_max = observed or (round(last_floor / 0.9) if last_floor else None)
        if not hr_max:
            log.warning("strava: FC max indéterminable, zones ignorées")
            return

        zones = []
        for index, zone in enumerate(raw_zones, start=1):
            low = to_int(zone.get("min"))
            high = to_int(zone.get("max"))
            if low is None:
                continue
            if high is None or high < 0:
                high = hr_max
            zones.append((index, low, high))

        repo.upsert_hr_zones(
            conn,
            athlete_id,
            valid_from=dt.date.today(),
            hr_max=hr_max,
            zones=zones,
            source="strava",
        )
    stats.detail["hr_zones"] = len(zones)
    log.info("strava: zones de FC enregistrées", count=len(zones), hr_max=hr_max)


def _needs_detail(conn, activity_id: int | None, start_time: dt.datetime, mode: str) -> bool:
    if activity_id is None:
        return True  # activité inconnue : on prend tout ce qu'on peut
    age = dt.datetime.now(dt.UTC) - start_time
    if age <= dt.timedelta(days=DETAIL_RECENT_DAYS):
        return True
    if mode == "backfill":
        # Déjà couverte par Garmin ? Le détail n'apporterait rien de neuf.
        found = conn.execute(
            "SELECT 1 FROM activity_source WHERE activity_id = ? AND source = 'garmin'",
            (activity_id,),
        ).fetchone()
        return found is None
    return False


def _ingest_segment_efforts(conn, activity_id: int, detail: dict[str, Any]) -> int:
    efforts = detail.get("segment_efforts") or []
    if not efforts:
        return 0

    for effort in efforts:
        if not isinstance(effort, dict):
            continue
        segment = effort.get("segment") or {}
        segment_id = segment.get("id")
        if not segment_id:
            continue

        conn.execute(
            """
            INSERT INTO segment (strava_segment_id, name, activity_family, distance_m,
                                 avg_grade_pct, max_grade_pct, elevation_high_m,
                                 elevation_low_m, climb_category, starred, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT (strava_segment_id) DO UPDATE
               SET name = excluded.name, starred = excluded.starred,
                   updated_at = datetime('now')
            """,
            (
                segment_id,
                segment.get("name") or str(segment_id),
                segment.get("activity_type"),
                to_float(segment.get("distance")),
                to_float(segment.get("average_grade")),
                to_float(segment.get("maximum_grade")),
                to_float(segment.get("elevation_high")),
                to_float(segment.get("elevation_low")),
                to_int(segment.get("climb_category")),
                int(bool(segment.get("starred"))),
            ),
        )
        conn.execute(
            """
            INSERT INTO segment_effort (strava_effort_id, segment_id, activity_id,
                                        start_time, elapsed_s, moving_s, avg_hr, max_hr,
                                        avg_power_w, avg_cadence, pr_rank, kom_rank)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (strava_effort_id) DO UPDATE
               SET pr_rank = excluded.pr_rank, kom_rank = excluded.kom_rank
            """,
            (
                effort.get("id"),
                segment_id,
                activity_id,
                parse_iso(effort.get("start_date")),
                to_int(effort.get("elapsed_time")),
                to_int(effort.get("moving_time")),
                to_int(effort.get("average_heartrate")),
                to_int(effort.get("max_heartrate")),
                to_int(effort.get("average_watts")),
                to_float(effort.get("average_cadence")),
                to_int(effort.get("pr_rank")),
                to_int(effort.get("kom_rank")),
            ),
        )
    return len(efforts)


def _ingest_activity(
    client: StravaClient,
    athlete_id: int,
    summary: dict[str, Any],
    gear_map: dict[str, int],
    mode: str,
    stats: RunStats,
) -> None:
    external_id = str(summary.get("id"))
    start_time = parse_iso(summary.get("start_date"))
    if start_time is None:
        log.warning("strava: activité sans date de départ, ignorée", external_id=external_id)
        return

    with connection() as conn:
        payload_id, changed = store_raw(
            conn, source=SOURCE, endpoint="activity_summary",
            resource_key=external_id, payload=summary,
        )
        stats.items_fetched += 1

        existing_id = repo.activity_id_for_source(conn, SOURCE, external_id)
        sport_type = summary.get("sport_type") or summary.get("type")
        sport, family = resolve_sport(conn, SOURCE, sport_type)

        if existing_id is None:
            existing_id = find_existing_activity(
                conn, athlete_id=athlete_id, start_time=start_time, sport_family=family
            )

        gear_pk = gear_map.get(str(summary.get("gear_id") or "")) or repo.gear_id_for_strava(
            conn, summary.get("gear_id")
        )
        fields = map_strava_activity(summary, sport, family, gear_pk)

        activity_id, created = repo.upsert_activity(
            conn,
            athlete_id=athlete_id,
            source=SOURCE,
            external_id=external_id,
            external_url=f"https://www.strava.com/activities/{external_id}",
            raw_payload_id=payload_id,
            fields=fields,
            existing_id=existing_id,
        )
        stats.items_upserted += 1
        key = "activities_created" if created else "activities_merged"
        stats.detail[key] = stats.detail.get(key, 0) + 1

        want_detail = changed and _needs_detail(conn, existing_id, start_time, mode)

    if not want_detail:
        return

    detail = client.activity(external_id)
    if not detail:
        return

    with connection() as conn:
        store_raw(conn, source=SOURCE, endpoint="activity_detail",
                  resource_key=external_id, payload=detail)

        extra = map_strava_activity(detail, sport, family, gear_pk)
        repo.upsert_activity(
            conn,
            athlete_id=athlete_id,
            source=SOURCE,
            external_id=external_id,
            external_url=None,
            raw_payload_id=None,
            fields=extra,
            existing_id=activity_id,
        )

        laps = map_strava_laps(detail)
        if laps:
            repo.replace_laps(conn, activity_id, laps)
        count = _ingest_segment_efforts(conn, activity_id, detail)
        if count:
            stats.detail["segment_efforts"] = stats.detail.get("segment_efforts", 0) + count


def sync_activities(
    client: StravaClient, athlete_id: int, gear_map: dict[str, int], mode: str, stats: RunStats
) -> None:
    cursor = get_cursor(SOURCE, "activities")
    settings = get_settings()

    after: dt.datetime | None = None
    before: dt.datetime | None = None
    if mode == "incremental" and cursor.get("incremental_from"):
        after = cursor["incremental_from"] - dt.timedelta(
            days=settings.incremental_lookback_days
        )
    elif mode == "backfill":
        raw_before = cursor.get("backfill_cursor")
        if cursor.get("backfill_done"):
            log.info("strava: backfill activités déjà terminé")
            return
        if raw_before:
            before = dt.datetime.combine(raw_before, dt.time.min, tzinfo=dt.UTC)

    newest_seen: dt.datetime | None = None
    oldest_seen: dt.datetime | None = None
    page = 1
    exhausted = False

    try:
        while True:
            batch = client.activities(after=after, before=before, page=page,
                                      per_page=PAGE_SIZE)
            if not batch:
                break

            for summary in batch:
                _ingest_activity(client, athlete_id, summary, gear_map, mode, stats)

            starts = [d for s in batch if (d := parse_iso(s.get("start_date"))) is not None]
            if starts:
                newest_seen = max(starts) if newest_seen is None else max(newest_seen, *starts)
                oldest_seen = min(starts) if oldest_seen is None else min(oldest_seen, *starts)

            if len(batch) < PAGE_SIZE:
                break
            page += 1
    except StravaDailyQuotaExhausted:
        exhausted = True
        log.warning("strava: quota journalier atteint, reprise à la prochaine passe")

    if mode == "backfill":
        done = not exhausted and (oldest_seen is None or oldest_seen <= EPOCH)
        mark_success(
            SOURCE,
            "activities",
            backfill_cursor=oldest_seen.date() if oldest_seen else None,
            backfill_done=done,
            incremental_from=newest_seen,
        )
    else:
        mark_success(SOURCE, "activities", incremental_from=newest_seen)

    if exhausted:
        raise StravaDailyQuotaExhausted("passe interrompue par le quota journalier")


def run(mode: str = "incremental") -> RunStats:
    settings = get_settings()
    stats = RunStats()

    with StravaClient(settings.strava_client_id, settings.strava_client_secret) as client:
        profile = client.athlete()
        if not profile:
            raise RuntimeError("strava: profil inaccessible")

        with connection() as conn:
            athlete_id = repo.get_or_create_athlete(
                conn,
                display_name=(
                    f"{profile.get('firstname', '')} {profile.get('lastname', '')}".strip()
                    or "athlete"
                ),
                strava_athlete_id=profile.get("id"),
                timezone=settings.timezone,
            )

        gear_map = sync_gear(client, athlete_id, stats)
        try:
            sync_zones(client, athlete_id, stats)
        except Exception as exc:  # noqa: BLE001 — ressource optionnelle
            log.warning("strava: zones en échec", error=str(exc))

        try:
            sync_activities(client, athlete_id, gear_map, mode, stats)
        except StravaDailyQuotaExhausted:
            stats.detail["interrupted"] = "daily_quota"
        except Exception:
            mark_error(SOURCE, "activities")
            raise
        finally:
            stats.api_calls = client.api_calls
            stats.detail["rate_short"] = f"{client.rate.short_usage}/{client.rate.short_limit}"
            stats.detail["rate_daily"] = f"{client.rate.daily_usage}/{client.rate.daily_limit}"

    return stats
