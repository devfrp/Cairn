"""Écritures en base : upserts idempotents pour chaque entité.

Règle de préséance entre sources : chaque colonne d'`activity` a un
propriétaire déclaré dans `COLUMN_OWNER`. Le propriétaire écrase la valeur
existante quand il en fournit une ; les autres sources ne comblent que les
trous. Ça évite qu'une resync Strava écrase la fréquence cardiaque plus fine
venue de Garmin, ou qu'une resync Garmin efface le vélo associé côté Strava.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import structlog
from psycopg import sql

log = structlog.get_logger(__name__)

# Colonnes d'`activity` alimentables par les sources.
ACTIVITY_COLUMNS: tuple[str, ...] = (
    "start_time", "start_time_local", "local_date", "timezone",
    "sport", "sport_family", "name", "is_indoor", "is_commute", "is_race",
    "elapsed_s", "moving_s", "distance_m",
    "elevation_gain_m", "elevation_loss_m", "elevation_max_m",
    "avg_speed_mps", "max_speed_mps", "avg_hr", "max_hr", "avg_cadence",
    "avg_power_w", "weighted_power_w", "max_power_w", "calories",
    "training_load", "aerobic_te", "anaerobic_te", "trimp", "intensity_factor",
    "perceived_exertion", "feel", "gear_id", "dedup_key",
)

# Propriétaire par colonne. Garmin porte les capteurs et les métriques
# physiologiques ; Strava porte ce qui relève de l'édition manuelle et du
# matériel, que Garmin ne connaît pas.
COLUMN_OWNER: dict[str, str] = {
    "avg_hr": "garmin",
    "max_hr": "garmin",
    "avg_cadence": "garmin",
    "avg_power_w": "garmin",
    "weighted_power_w": "garmin",
    "max_power_w": "garmin",
    "calories": "garmin",
    "training_load": "garmin",
    "aerobic_te": "garmin",
    "anaerobic_te": "garmin",
    "elevation_gain_m": "garmin",
    "elevation_loss_m": "garmin",
    "moving_s": "garmin",
    "sport": "garmin",
    "sport_family": "garmin",
    "name": "strava",
    "gear_id": "strava",
    "is_commute": "strava",
    "is_race": "strava",
    "perceived_exertion": "strava",
    "feel": "strava",
}


def get_or_create_athlete(
    conn,
    *,
    display_name: str,
    garmin_user_id: str | None = None,
    strava_athlete_id: int | None = None,
    timezone: str = "Europe/Paris",
) -> int:
    """Retourne l'id de l'athlète, en complétant les identifiants manquants.

    Cairn est mono-utilisateur : s'il existe déjà une ligne, on la réutilise
    et on y rattache l'identifiant de la source qui appelle.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT id, garmin_user_id, strava_athlete_id FROM athlete ORDER BY id LIMIT 1")
        row = cur.fetchone()
        if row is None:
            cur.execute(
                """
                INSERT INTO athlete (display_name, garmin_user_id, strava_athlete_id, timezone)
                VALUES (%s, %s, %s, %s) RETURNING id
                """,
                (display_name, garmin_user_id, strava_athlete_id, timezone),
            )
            return cur.fetchone()["id"]

        updates, params = [], []
        if garmin_user_id and not row["garmin_user_id"]:
            updates.append("garmin_user_id = %s")
            params.append(garmin_user_id)
        if strava_athlete_id and not row["strava_athlete_id"]:
            updates.append("strava_athlete_id = %s")
            params.append(strava_athlete_id)
        if updates:
            params.append(row["id"])
            cur.execute(f"UPDATE athlete SET {', '.join(updates)} WHERE id = %s", params)  # noqa: S608
        return row["id"]


def _merge_assignments(source: str, columns: list[str]) -> sql.Composed:
    """Construit le SET d'un UPDATE en appliquant la règle de préséance.

    Colonnes issues d'une whitelist constante — aucune donnée externe
    n'entre dans la structure de la requête.
    """
    parts = []
    for col in columns:
        ident = sql.Identifier(col)
        if COLUMN_OWNER.get(col) == source:
            # Le propriétaire impose sa valeur quand il en a une.
            expr = sql.SQL("{c} = COALESCE(%({p})s, activity.{c})").format(
                c=ident, p=sql.SQL(col)
            )
        else:
            # Les autres ne remplissent que les trous.
            expr = sql.SQL("{c} = COALESCE(activity.{c}, %({p})s)").format(
                c=ident, p=sql.SQL(col)
            )
        parts.append(expr)
    parts.append(sql.SQL("updated_at = now()"))
    return sql.SQL(", ").join(parts)


def upsert_activity(
    conn,
    *,
    athlete_id: int,
    source: str,
    external_id: str,
    external_url: str | None,
    raw_payload_id: int | None,
    fields: dict[str, Any],
    existing_id: int | None,
) -> tuple[int, bool]:
    """Crée ou met à jour une activité. Retourne (id, créée ?)."""
    payload = {c: fields.get(c) for c in ACTIVITY_COLUMNS if c in fields}
    created = False

    with conn.cursor() as cur:
        if existing_id is None:
            cols = list(payload) + ["athlete_id", "primary_source"]
            values = {**payload, "athlete_id": athlete_id, "primary_source": source}
            stmt = sql.SQL("INSERT INTO activity ({cols}) VALUES ({vals}) RETURNING id").format(
                cols=sql.SQL(", ").join(sql.Identifier(c) for c in cols),
                vals=sql.SQL(", ").join(sql.Placeholder(c) for c in cols),
            )
            cur.execute(stmt, values)
            activity_id = cur.fetchone()["id"]
            created = True
        else:
            activity_id = existing_id
            if payload:
                stmt = sql.SQL("UPDATE activity SET {sets} WHERE id = %(activity_id)s").format(
                    sets=_merge_assignments(source, list(payload))
                )
                cur.execute(stmt, {**payload, "activity_id": activity_id})

        cur.execute(
            """
            INSERT INTO activity_source
                   (activity_id, source, external_id, external_url, raw_payload_id)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (source, external_id) DO UPDATE
               SET last_seen_at = now(),
                   external_url = COALESCE(EXCLUDED.external_url, activity_source.external_url),
                   raw_payload_id = COALESCE(EXCLUDED.raw_payload_id,
                                             activity_source.raw_payload_id)
            """,
            (activity_id, source, str(external_id), external_url, raw_payload_id),
        )
    return activity_id, created


def activity_id_for_source(conn, source: str, external_id: str) -> int | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT activity_id FROM activity_source WHERE source = %s AND external_id = %s",
            (source, str(external_id)),
        )
        row = cur.fetchone()
    return row["activity_id"] if row else None


def replace_laps(conn, activity_id: int, laps: list[dict[str, Any]]) -> int:
    """Remplace les tours d'une activité (leur découpage peut être réédité)."""
    if not laps:
        return 0
    cols = ("lap_index", "start_time", "elapsed_s", "moving_s", "distance_m",
            "elevation_gain_m", "avg_hr", "max_hr", "avg_power_w", "avg_cadence",
            "avg_speed_mps")
    with conn.cursor() as cur:
        cur.execute("DELETE FROM activity_lap WHERE activity_id = %s", (activity_id,))
        cur.executemany(
            f"""
            INSERT INTO activity_lap (activity_id, {", ".join(cols)})
            VALUES (%s, {", ".join(["%s"] * len(cols))})
            """,  # noqa: S608 — colonnes issues d'une constante
            [tuple([activity_id] + [lap.get(c) for c in cols]) for lap in laps],
        )
    return len(laps)


def upsert_hr_zones(
    conn,
    athlete_id: int,
    *,
    valid_from: dt.date,
    hr_max: int,
    zones: list[tuple[int, int, int]],
    hr_rest: int | None = None,
    source: str = "strava",
) -> int:
    """Enregistre un jeu de zones daté.

    Les jeux sont historisés : recalculer une sortie de l'an dernier doit
    utiliser les zones de l'an dernier, pas celles d'aujourd'hui.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO hr_zone_set (athlete_id, valid_from, hr_max, hr_rest, source)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (athlete_id, valid_from) DO UPDATE
               SET hr_max = EXCLUDED.hr_max,
                   hr_rest = COALESCE(EXCLUDED.hr_rest, hr_zone_set.hr_rest),
                   source = EXCLUDED.source
            RETURNING id
            """,
            (athlete_id, valid_from, hr_max, hr_rest, source),
        )
        zone_set_id = cur.fetchone()["id"]

        cur.execute("DELETE FROM hr_zone WHERE zone_set_id = %s", (zone_set_id,))
        cur.executemany(
            """
            INSERT INTO hr_zone (zone_set_id, zone_index, min_bpm, max_bpm)
            VALUES (%s, %s, %s, %s)
            """,
            [(zone_set_id, index, low, high) for index, low, high in zones],
        )
    return zone_set_id


def observed_hr_max(conn, athlete_id: int, since: dt.date) -> int | None:
    """FC maximale réellement atteinte, plus fiable qu'une formule sur l'âge."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT max(max_hr) AS hr FROM activity
             WHERE athlete_id = %s AND local_date >= %s AND max_hr IS NOT NULL
            """,
            (athlete_id, since),
        )
        row = cur.fetchone()
    return row["hr"] if row and row["hr"] else None


def upsert_gear(conn, athlete_id: int, gear: dict[str, Any]) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO gear (athlete_id, strava_gear_id, kind, name, brand, model,
                              frame_type, is_primary, retired, distance_m, updated_at)
            VALUES (%(athlete_id)s, %(strava_gear_id)s, %(kind)s, %(name)s, %(brand)s,
                    %(model)s, %(frame_type)s, %(is_primary)s, %(retired)s,
                    %(distance_m)s, now())
            ON CONFLICT (strava_gear_id) DO UPDATE
               SET name = EXCLUDED.name,
                   brand = COALESCE(EXCLUDED.brand, gear.brand),
                   model = COALESCE(EXCLUDED.model, gear.model),
                   frame_type = COALESCE(EXCLUDED.frame_type, gear.frame_type),
                   is_primary = EXCLUDED.is_primary,
                   retired = EXCLUDED.retired,
                   distance_m = GREATEST(EXCLUDED.distance_m, gear.distance_m),
                   updated_at = now()
            RETURNING id
            """,
            {"athlete_id": athlete_id, **gear},
        )
        return cur.fetchone()["id"]


def gear_id_for_strava(conn, strava_gear_id: str | None) -> int | None:
    if not strava_gear_id:
        return None
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM gear WHERE strava_gear_id = %s", (strava_gear_id,))
        row = cur.fetchone()
    return row["id"] if row else None


# ---------------------------------------------------------------------
# Wellness
# ---------------------------------------------------------------------

WELLNESS_COLUMNS: tuple[str, ...] = (
    "resting_hr", "min_hr", "max_hr", "avg_hr",
    "avg_stress", "max_stress", "rest_stress_s", "low_stress_s",
    "medium_stress_s", "high_stress_s",
    "body_battery_high", "body_battery_low", "body_battery_charged",
    "body_battery_drained",
    "steps", "floors_climbed", "distance_m", "calories_bmr", "calories_active",
    "moderate_intensity_min", "vigorous_intensity_min",
    "avg_spo2", "avg_respiration",
    "vo2max_running", "vo2max_cycling",
    "training_readiness", "training_readiness_lbl", "training_status",
    "acute_load", "chronic_load",
)


def upsert_daily_wellness(conn, athlete_id: int, day: dt.date, values: dict[str, Any]) -> None:
    """Fusionne des métriques journalières.

    Plusieurs endpoints Garmin alimentent la même ligne (résumé, readiness,
    VO2max, statut d'entraînement). Chacun n'écrit que ses colonnes ; les
    autres restent intactes grâce au COALESCE sur EXCLUDED.
    """
    present = [c for c in WELLNESS_COLUMNS if c in values]
    if not present:
        return
    cols = ["athlete_id", "day", *present]
    params = {"athlete_id": athlete_id, "day": day, **{c: values[c] for c in present}}

    stmt = sql.SQL(
        "INSERT INTO daily_wellness ({cols}) VALUES ({vals}) "
        "ON CONFLICT (athlete_id, day) DO UPDATE SET {sets}, updated_at = now()"
    ).format(
        cols=sql.SQL(", ").join(sql.Identifier(c) for c in cols),
        vals=sql.SQL(", ").join(sql.Placeholder(c) for c in cols),
        sets=sql.SQL(", ").join(
            sql.SQL("{c} = COALESCE(EXCLUDED.{c}, daily_wellness.{c})").format(
                c=sql.Identifier(c)
            )
            for c in present
        ),
    )
    with conn.cursor() as cur:
        cur.execute(stmt, params)


SLEEP_COLUMNS: tuple[str, ...] = (
    "start_time", "end_time", "duration_s", "deep_s", "light_s", "rem_s",
    "awake_s", "nap_s", "score", "score_quality", "score_feedback", "avg_hr",
    "lowest_hr", "avg_respiration", "avg_spo2", "lowest_spo2",
    "restless_moments", "awake_count", "sleep_need_s", "body_battery_change",
)


def upsert_sleep(
    conn,
    athlete_id: int,
    sleep_date: dt.date,
    values: dict[str, Any],
    stages: list[dict[str, Any]] | None = None,
) -> None:
    present = [c for c in SLEEP_COLUMNS if c in values]
    if not present:
        return
    cols = ["athlete_id", "sleep_date", *present]
    params = {"athlete_id": athlete_id, "sleep_date": sleep_date,
              **{c: values[c] for c in present}}

    stmt = sql.SQL(
        "INSERT INTO sleep_session ({cols}) VALUES ({vals}) "
        "ON CONFLICT (athlete_id, sleep_date) DO UPDATE SET {sets}"
    ).format(
        cols=sql.SQL(", ").join(sql.Identifier(c) for c in cols),
        vals=sql.SQL(", ").join(sql.Placeholder(c) for c in cols),
        sets=sql.SQL(", ").join(
            sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c)) for c in present
        ),
    )
    with conn.cursor() as cur:
        cur.execute(stmt, params)
        if stages:
            cur.execute(
                "DELETE FROM sleep_stage WHERE athlete_id = %s AND sleep_date = %s",
                (athlete_id, sleep_date),
            )
            cur.executemany(
                """
                INSERT INTO sleep_stage (athlete_id, sleep_date, start_time, end_time, stage)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                [
                    (athlete_id, sleep_date, s["start_time"], s["end_time"], s["stage"])
                    for s in stages
                ],
            )


HRV_COLUMNS: tuple[str, ...] = (
    "avg_hrv_ms", "last_night_5m_high", "weekly_avg_ms", "baseline_low",
    "baseline_balanced_low", "baseline_balanced_high", "baseline_marker",
    "status", "feedback",
)


def upsert_hrv(conn, athlete_id: int, day: dt.date, values: dict[str, Any]) -> None:
    present = [c for c in HRV_COLUMNS if c in values]
    if not present:
        return
    cols = ["athlete_id", "day", *present]
    params = {"athlete_id": athlete_id, "day": day, **{c: values[c] for c in present}}
    stmt = sql.SQL(
        "INSERT INTO hrv_night ({cols}) VALUES ({vals}) "
        "ON CONFLICT (athlete_id, day) DO UPDATE SET {sets}"
    ).format(
        cols=sql.SQL(", ").join(sql.Identifier(c) for c in cols),
        vals=sql.SQL(", ").join(sql.Placeholder(c) for c in cols),
        sets=sql.SQL(", ").join(
            sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c)) for c in present
        ),
    )
    with conn.cursor() as cur:
        cur.execute(stmt, params)


def insert_wellness_readings(conn, athlete_id: int, readings: list[tuple]) -> int:
    """Insère des points intra-journée : (timestamp, metric, value)."""
    if not readings:
        return 0
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO wellness_reading (athlete_id, time, metric, value)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (athlete_id, metric, time) DO UPDATE SET value = EXCLUDED.value
            """,
            [(athlete_id, ts, metric, value) for ts, metric, value in readings],
        )
    return len(readings)


def upsert_body_measurement(conn, athlete_id: int, values: dict[str, Any]) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO body_measurement
                   (athlete_id, measured_at, weight_kg, body_fat_pct, muscle_mass_kg,
                    bone_mass_kg, body_water_pct, bmi, source)
            VALUES (%(athlete_id)s, %(measured_at)s, %(weight_kg)s, %(body_fat_pct)s,
                    %(muscle_mass_kg)s, %(bone_mass_kg)s, %(body_water_pct)s, %(bmi)s,
                    %(source)s)
            ON CONFLICT (athlete_id, measured_at, source) DO UPDATE
               SET weight_kg = EXCLUDED.weight_kg,
                   body_fat_pct = EXCLUDED.body_fat_pct
            """,
            {"athlete_id": athlete_id, "source": "garmin", **values},
        )


def record_activity_file(
    conn, *, source: str, external_id: str, fmt: str, path: str,
    size_bytes: int, sha256: bytes,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO raw.activity_file
                   (source, external_id, format, path, size_bytes, sha256)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (source, external_id, format) DO UPDATE
               SET path = EXCLUDED.path,
                   size_bytes = EXCLUDED.size_bytes,
                   sha256 = EXCLUDED.sha256,
                   downloaded_at = now()
            """,
            (source, str(external_id), fmt, path, size_bytes, sha256),
        )


def has_activity_file(conn, source: str, external_id: str, fmt: str = "fit") -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM raw.activity_file
             WHERE source = %s AND external_id = %s AND format = %s
            """,
            (source, str(external_id), fmt),
        )
        return cur.fetchone() is not None
