"""Écritures en base : upserts idempotents pour chaque entité.

Règle de préséance entre sources : chaque colonne d'`activity` a un
propriétaire déclaré dans `COLUMN_OWNER`. Le propriétaire écrase la valeur
existante quand il en fournit une ; les autres sources ne comblent que les
trous. Ça évite qu'une resynchronisation Strava écrase la fréquence
cardiaque plus fine venue de Garmin, ou qu'une resynchronisation Garmin
efface le vélo associé côté Strava.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import structlog

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

# Colonnes stockées en entier faute de booléen natif.
BOOLEAN_COLUMNS = {"is_indoor", "is_commute", "is_race"}


def _normalise(fields: dict[str, Any]) -> dict[str, Any]:
    return {
        key: (int(bool(value)) if key in BOOLEAN_COLUMNS and value is not None else value)
        for key, value in fields.items()
    }


def get_or_create_athlete(
    conn,
    *,
    display_name: str,
    garmin_user_id: str | None = None,
    strava_athlete_id: int | None = None,
    timezone: str = "Europe/Paris",
) -> int:
    """Retourne l'identifiant de l'athlète, en complétant ce qui manque.

    Cairn est mono-utilisateur : s'il existe déjà une ligne, on la réutilise
    et on y rattache l'identifiant de la source qui appelle.
    """
    row = conn.execute(
        "SELECT id, garmin_user_id, strava_athlete_id FROM athlete ORDER BY id LIMIT 1"
    ).fetchone()

    if row is None:
        cur = conn.execute(
            """
            INSERT INTO athlete (display_name, garmin_user_id, strava_athlete_id, timezone)
            VALUES (?, ?, ?, ?)
            """,
            (display_name, garmin_user_id, strava_athlete_id, timezone),
        )
        return cur.lastrowid

    updates, params = [], []
    if garmin_user_id and not row["garmin_user_id"]:
        updates.append("garmin_user_id = ?")
        params.append(garmin_user_id)
    if strava_athlete_id and not row["strava_athlete_id"]:
        updates.append("strava_athlete_id = ?")
        params.append(strava_athlete_id)
    if updates:
        params.append(row["id"])
        conn.execute(f"UPDATE athlete SET {', '.join(updates)} WHERE id = ?", params)  # noqa: S608
    return row["id"]


def merge_assignments(source: str, columns: list[str]) -> str:
    """Construit le SET d'un UPDATE en appliquant la règle de préséance.

    Les noms de colonnes viennent d'une liste constante : aucune donnée
    externe n'entre dans la structure de la requête.
    """
    parts = []
    for col in columns:
        if COLUMN_OWNER.get(col) == source:
            # Le propriétaire impose sa valeur dès qu'il en a une.
            parts.append(f"{col} = coalesce(:{col}, {col})")
        else:
            # Les autres ne remplissent que les trous.
            parts.append(f"{col} = coalesce({col}, :{col})")
    parts.append("updated_at = datetime('now')")
    return ", ".join(parts)


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
    """Crée ou met à jour une activité. Retourne (identifiant, créée ?)."""
    payload = _normalise({c: fields.get(c) for c in ACTIVITY_COLUMNS if c in fields})
    created = False

    if existing_id is None:
        columns = [*payload, "athlete_id", "primary_source"]
        values = {**payload, "athlete_id": athlete_id, "primary_source": source}
        placeholders = ", ".join(f":{c}" for c in columns)
        cur = conn.execute(
            f"INSERT INTO activity ({', '.join(columns)}) VALUES ({placeholders})",  # noqa: S608
            values,
        )
        activity_id = cur.lastrowid
        created = True
    else:
        activity_id = existing_id
        if payload:
            conn.execute(
                f"UPDATE activity SET {merge_assignments(source, list(payload))} "  # noqa: S608
                "WHERE id = :activity_id",
                {**payload, "activity_id": activity_id},
            )

    conn.execute(
        """
        INSERT INTO activity_source
               (activity_id, source, external_id, external_url, raw_payload_id)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (source, external_id) DO UPDATE
           SET last_seen_at   = datetime('now'),
               external_url   = coalesce(excluded.external_url, external_url),
               raw_payload_id = coalesce(excluded.raw_payload_id, raw_payload_id)
        """,
        (activity_id, source, str(external_id), external_url, raw_payload_id),
    )
    return activity_id, created


def activity_id_for_source(conn, source: str, external_id: str) -> int | None:
    row = conn.execute(
        "SELECT activity_id FROM activity_source WHERE source = ? AND external_id = ?",
        (source, str(external_id)),
    ).fetchone()
    return row["activity_id"] if row else None


def replace_laps(conn, activity_id: int, laps: list[dict[str, Any]]) -> int:
    """Remplace les tours d'une activité : leur découpage peut être réédité."""
    if not laps:
        return 0
    columns = ("lap_index", "start_time", "elapsed_s", "moving_s", "distance_m",
               "elevation_gain_m", "avg_hr", "max_hr", "avg_power_w", "avg_cadence",
               "avg_speed_mps")
    conn.execute("DELETE FROM activity_lap WHERE activity_id = ?", (activity_id,))
    conn.executemany(
        f"""
        INSERT INTO activity_lap (activity_id, {", ".join(columns)})
        VALUES ({", ".join(["?"] * (len(columns) + 1))})
        """,  # noqa: S608 — colonnes issues d'une constante
        [tuple([activity_id, *[lap.get(c) for c in columns]]) for lap in laps],
    )
    return len(laps)


# ---------------------------------------------------------------------
# Zones de fréquence cardiaque
# ---------------------------------------------------------------------


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
    conn.execute(
        """
        INSERT INTO hr_zone_set (athlete_id, valid_from, hr_max, hr_rest, source)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (athlete_id, valid_from) DO UPDATE
           SET hr_max  = excluded.hr_max,
               hr_rest = coalesce(excluded.hr_rest, hr_rest),
               source  = excluded.source
        """,
        (athlete_id, valid_from, hr_max, hr_rest, source),
    )
    row = conn.execute(
        "SELECT id FROM hr_zone_set WHERE athlete_id = ? AND valid_from = ?",
        (athlete_id, valid_from),
    ).fetchone()
    zone_set_id = row["id"]

    conn.execute("DELETE FROM hr_zone WHERE zone_set_id = ?", (zone_set_id,))
    conn.executemany(
        "INSERT INTO hr_zone (zone_set_id, zone_index, min_bpm, max_bpm) VALUES (?, ?, ?, ?)",
        [(zone_set_id, index, low, high) for index, low, high in zones],
    )
    return zone_set_id


def observed_hr_max(conn, athlete_id: int, since: dt.date) -> int | None:
    """FC maximale réellement atteinte, plus fiable qu'une formule sur l'âge."""
    row = conn.execute(
        """
        SELECT max(max_hr) AS hr FROM activity
         WHERE athlete_id = ? AND local_date >= ? AND max_hr IS NOT NULL
        """,
        (athlete_id, since),
    ).fetchone()
    return row["hr"] if row and row["hr"] else None


# ---------------------------------------------------------------------
# Matériel
# ---------------------------------------------------------------------


def upsert_gear(conn, athlete_id: int, gear: dict[str, Any]) -> int:
    values = {
        "athlete_id": athlete_id,
        **gear,
        "is_primary": int(bool(gear.get("is_primary"))),
        "retired": int(bool(gear.get("retired"))),
    }
    conn.execute(
        """
        INSERT INTO gear (athlete_id, strava_gear_id, kind, name, brand, model,
                          frame_type, is_primary, retired, distance_m, updated_at)
        VALUES (:athlete_id, :strava_gear_id, :kind, :name, :brand, :model,
                :frame_type, :is_primary, :retired, :distance_m, datetime('now'))
        ON CONFLICT (strava_gear_id) DO UPDATE
           SET name       = excluded.name,
               brand      = coalesce(excluded.brand, brand),
               model      = coalesce(excluded.model, model),
               frame_type = coalesce(excluded.frame_type, frame_type),
               is_primary = excluded.is_primary,
               retired    = excluded.retired,
               distance_m = max(excluded.distance_m, distance_m),
               updated_at = datetime('now')
        """,
        values,
    )
    row = conn.execute(
        "SELECT id FROM gear WHERE strava_gear_id = ?", (values["strava_gear_id"],)
    ).fetchone()
    return row["id"]


def gear_id_for_strava(conn, strava_gear_id: str | None) -> int | None:
    if not strava_gear_id:
        return None
    row = conn.execute(
        "SELECT id FROM gear WHERE strava_gear_id = ?", (strava_gear_id,)
    ).fetchone()
    return row["id"] if row else None


# ---------------------------------------------------------------------
# Récupération
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
    autres restent intactes.
    """
    present = [c for c in WELLNESS_COLUMNS if c in values]
    if not present:
        return

    columns = ["athlete_id", "day", *present]
    params = {"athlete_id": athlete_id, "day": day, **{c: values[c] for c in present}}
    sets = ", ".join(f"{c} = coalesce(excluded.{c}, {c})" for c in present)
    conn.execute(
        f"""
        INSERT INTO daily_wellness ({", ".join(columns)})
        VALUES ({", ".join(f":{c}" for c in columns)})
        ON CONFLICT (athlete_id, day) DO UPDATE
           SET {sets}, updated_at = datetime('now')
        """,  # noqa: S608 — colonnes issues d'une constante
        params,
    )


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

    columns = ["athlete_id", "sleep_date", *present]
    params = {"athlete_id": athlete_id, "sleep_date": sleep_date,
              **{c: values[c] for c in present}}
    sets = ", ".join(f"{c} = excluded.{c}" for c in present)
    conn.execute(
        f"""
        INSERT INTO sleep_session ({", ".join(columns)})
        VALUES ({", ".join(f":{c}" for c in columns)})
        ON CONFLICT (athlete_id, sleep_date) DO UPDATE SET {sets}
        """,  # noqa: S608 — colonnes issues d'une constante
        params,
    )

    if stages:
        conn.execute(
            "DELETE FROM sleep_stage WHERE athlete_id = ? AND sleep_date = ?",
            (athlete_id, sleep_date),
        )
        conn.executemany(
            """
            INSERT INTO sleep_stage (athlete_id, sleep_date, start_time, end_time, stage)
            VALUES (?, ?, ?, ?, ?)
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

    columns = ["athlete_id", "day", *present]
    params = {"athlete_id": athlete_id, "day": day, **{c: values[c] for c in present}}
    sets = ", ".join(f"{c} = excluded.{c}" for c in present)
    conn.execute(
        f"""
        INSERT INTO hrv_night ({", ".join(columns)})
        VALUES ({", ".join(f":{c}" for c in columns)})
        ON CONFLICT (athlete_id, day) DO UPDATE SET {sets}
        """,  # noqa: S608 — colonnes issues d'une constante
        params,
    )


def insert_wellness_readings(conn, athlete_id: int, readings: list[tuple]) -> int:
    """Insère des points intra-journée : (horodatage, métrique, valeur)."""
    if not readings:
        return 0
    conn.executemany(
        """
        INSERT INTO wellness_reading (athlete_id, time, metric, value)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (athlete_id, metric, time) DO UPDATE SET value = excluded.value
        """,
        [(athlete_id, ts, metric, value) for ts, metric, value in readings],
    )
    return len(readings)


def upsert_body_measurement(conn, athlete_id: int, values: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO body_measurement
               (athlete_id, measured_at, weight_kg, body_fat_pct, muscle_mass_kg,
                bone_mass_kg, body_water_pct, bmi, source)
        VALUES (:athlete_id, :measured_at, :weight_kg, :body_fat_pct, :muscle_mass_kg,
                :bone_mass_kg, :body_water_pct, :bmi, :source)
        ON CONFLICT (athlete_id, measured_at, source) DO UPDATE
           SET weight_kg    = excluded.weight_kg,
               body_fat_pct = excluded.body_fat_pct
        """,
        {"athlete_id": athlete_id, "source": "garmin",
         "muscle_mass_kg": None, "bone_mass_kg": None, "body_water_pct": None,
         "bmi": None, "body_fat_pct": None, **values},
    )


# ---------------------------------------------------------------------
# Fichiers d'activité
# ---------------------------------------------------------------------


def record_activity_file(
    conn, *, source: str, external_id: str, fmt: str, path: str,
    size_bytes: int, sha256: bytes,
) -> None:
    conn.execute(
        """
        INSERT INTO raw_activity_file
               (source, external_id, format, path, size_bytes, sha256)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (source, external_id, format) DO UPDATE
           SET path          = excluded.path,
               size_bytes    = excluded.size_bytes,
               sha256        = excluded.sha256,
               downloaded_at = datetime('now')
        """,
        (source, str(external_id), fmt, path, size_bytes, sha256),
    )


def has_activity_file(conn, source: str, external_id: str, fmt: str = "fit") -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM raw_activity_file
         WHERE source = ? AND external_id = ? AND format = ?
        """,
        (source, str(external_id), fmt),
    ).fetchone()
    return row is not None


def json_column(value: Any) -> str:
    """Sérialise une valeur destinée à une colonne JSON."""
    return json.dumps(value, default=str)
