"""Transformation des payloads bruts vers les colonnes du modèle.

Ces mappers sont écrits d'après la forme observée des réponses Garmin et
Strava. Garmin en particulier ne documente rien et fait évoluer ses payloads.
Le parsing est donc volontairement défensif : un champ absent ou renommé
donne None, pas une exception, et la donnée brute reste dans `raw.api_payload`
pour rejouer la transformation une fois le mapper corrigé.

`cairn raw show` permet d'inspecter un payload archivé sans retaper l'API.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from cairn.sync.dedup import dedup_key

# ---------------------------------------------------------------------
# Helpers de lecture tolérante
# ---------------------------------------------------------------------


def dig(data: Any, *keys: str, default: Any = None) -> Any:
    """Descente dans des dicts imbriqués sans lever si un niveau manque."""
    current = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def ms_to_dt(value: Any) -> dt.datetime | None:
    """Epoch en millisecondes -> datetime UTC."""
    if value is None:
        return None
    try:
        return dt.datetime.fromtimestamp(float(value) / 1000.0, tz=dt.UTC)
    except (TypeError, ValueError, OSError):
        return None


def parse_iso(value: Any, assume_utc: bool = True) -> dt.datetime | None:
    """Parse une date ISO, y compris les variantes Garmin sans fuseau.

    Garmin renvoie tantôt '2026-08-07T22:10:00.0', tantôt
    '2026-08-07 22:10:00', tantôt un epoch en millisecondes.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return ms_to_dt(value)
    if not isinstance(value, str):
        return None

    text = value.strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None and assume_utc:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed


def to_int(value: Any) -> int | None:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------
# Garmin — activités
# ---------------------------------------------------------------------


def map_garmin_activity(payload: dict[str, Any], sport: str, family: str) -> dict[str, Any]:
    """Résumé d'activité Garmin -> colonnes d'`activity`.

    La liste d'activités et le détail d'une activité partagent la même forme
    de champs à plat, à quelques clés près.
    """
    start_utc = parse_iso(payload.get("startTimeGMT"))
    start_local_raw = parse_iso(payload.get("startTimeLocal"), assume_utc=False)
    start_local = start_local_raw.replace(tzinfo=None) if start_local_raw else None

    fields: dict[str, Any] = {
        "start_time": start_utc,
        "start_time_local": start_local,
        "local_date": start_local.date() if start_local else None,
        "timezone": dig(payload, "timeZoneUnitDTO", "timeZone"),
        "sport": sport,
        "sport_family": family,
        "name": payload.get("activityName"),
        "elapsed_s": to_int(payload.get("elapsedDuration") or payload.get("duration")),
        "moving_s": to_int(payload.get("movingDuration")),
        "distance_m": to_float(payload.get("distance")),
        "elevation_gain_m": to_float(payload.get("elevationGain")),
        "elevation_loss_m": to_float(payload.get("elevationLoss")),
        "elevation_max_m": to_float(payload.get("maxElevation")),
        "avg_speed_mps": to_float(payload.get("averageSpeed")),
        "max_speed_mps": to_float(payload.get("maxSpeed")),
        "avg_hr": to_int(payload.get("averageHR")),
        "max_hr": to_int(payload.get("maxHR")),
        "avg_cadence": to_float(
            payload.get("averageBikingCadenceInRevPerMinute")
            or payload.get("averageRunningCadenceInStepsPerMinute")
        ),
        "avg_power_w": to_int(payload.get("avgPower")),
        "weighted_power_w": to_int(payload.get("normPower")),
        "max_power_w": to_int(payload.get("maxPower")),
        "calories": to_int(payload.get("calories")),
        "training_load": to_float(payload.get("activityTrainingLoad")),
        "aerobic_te": to_float(payload.get("aerobicTrainingEffect")),
        "anaerobic_te": to_float(payload.get("anaerobicTrainingEffect")),
        "is_indoor": bool(dig(payload, "activityType", "isIndoor") or False),
    }
    if start_utc:
        fields["dedup_key"] = dedup_key(start_utc)
    return {k: v for k, v in fields.items() if v is not None}


def map_garmin_laps(payload: Any) -> list[dict[str, Any]]:
    """Réponse `/splits` -> lignes d'`activity_lap`."""
    splits = payload.get("lapDTOs") if isinstance(payload, dict) else None
    if not splits:
        return []
    laps = []
    for index, split in enumerate(splits, start=1):
        start = parse_iso(split.get("startTimeGMT"))
        if start is None:
            continue
        laps.append(
            {
                "lap_index": index,
                "start_time": start,
                "elapsed_s": to_int(split.get("elapsedDuration") or split.get("duration")),
                "moving_s": to_int(split.get("movingDuration")),
                "distance_m": to_float(split.get("distance")),
                "elevation_gain_m": to_float(split.get("elevationGain")),
                "avg_hr": to_int(split.get("averageHR")),
                "max_hr": to_int(split.get("maxHR")),
                "avg_power_w": to_int(split.get("averagePower")),
                "avg_cadence": to_float(
                    split.get("averageBikingCadenceInRevPerMinute")
                    or split.get("averageRunningCadenceInStepsPerMinute")
                ),
                "avg_speed_mps": to_float(split.get("averageSpeed")),
            }
        )
    return laps


# ---------------------------------------------------------------------
# Garmin — wellness
# ---------------------------------------------------------------------

_STAGE_BY_LEVEL = {0: "deep", 1: "light", 2: "rem", 3: "awake"}


def map_garmin_sleep(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Payload sommeil -> (colonnes de `sleep_session`, stades)."""
    dto = payload.get("dailySleepDTO") or {}
    values = {
        "start_time": ms_to_dt(dto.get("sleepStartTimestampGMT")),
        "end_time": ms_to_dt(dto.get("sleepEndTimestampGMT")),
        "duration_s": to_int(dto.get("sleepTimeSeconds")),
        "deep_s": to_int(dto.get("deepSleepSeconds")),
        "light_s": to_int(dto.get("lightSleepSeconds")),
        "rem_s": to_int(dto.get("remSleepSeconds")),
        "awake_s": to_int(dto.get("awakeSleepSeconds")),
        "nap_s": to_int(dto.get("napTimeSeconds")),
        "score": to_int(dig(dto, "sleepScores", "overall", "value")),
        "score_quality": dig(dto, "sleepScores", "overall", "qualifierKey"),
        "score_feedback": dto.get("sleepScoreFeedback"),
        "avg_respiration": to_float(dto.get("averageRespirationValue")),
        "awake_count": to_int(dto.get("awakeCount")),
        "sleep_need_s": to_int(dig(dto, "sleepNeed", "actual")),
        "restless_moments": to_int(payload.get("restlessMomentsCount")),
        "avg_spo2": to_float(dig(payload, "wellnessSpO2SleepSummaryDTO", "averageSPO2")),
        "lowest_spo2": to_int(dig(payload, "wellnessSpO2SleepSummaryDTO", "lowestSPO2")),
    }

    hr_series = payload.get("sleepHeartRate") or []
    hr_values = [to_int(p.get("value")) for p in hr_series if isinstance(p, dict)]
    hr_values = [v for v in hr_values if v is not None]
    if hr_values:
        values["avg_hr"] = round(sum(hr_values) / len(hr_values))
        values["lowest_hr"] = min(hr_values)

    stages = []
    for level in payload.get("sleepLevels") or []:
        if not isinstance(level, dict):
            continue
        start = parse_iso(level.get("startGMT"))
        end = parse_iso(level.get("endGMT"))
        stage = _STAGE_BY_LEVEL.get(to_int(level.get("activityLevel")), "unmeasurable")
        if start and end and end > start:
            stages.append({"start_time": start, "end_time": end, "stage": stage})

    return {k: v for k, v in values.items() if v is not None}, stages


def map_garmin_hrv(payload: dict[str, Any]) -> dict[str, Any]:
    summary = payload.get("hrvSummary") or {}
    baseline = summary.get("baseline") or {}
    values = {
        "avg_hrv_ms": to_float(summary.get("lastNightAvg")),
        "last_night_5m_high": to_float(summary.get("lastNight5MinHigh")),
        "weekly_avg_ms": to_float(summary.get("weeklyAvg")),
        "baseline_low": to_float(baseline.get("lowUpper")),
        "baseline_balanced_low": to_float(baseline.get("balancedLow")),
        "baseline_balanced_high": to_float(baseline.get("balancedUpper")),
        "baseline_marker": to_float(baseline.get("markerValue")),
        "status": summary.get("status"),
        "feedback": dig(summary, "feedbackPhrase"),
    }
    return {k: v for k, v in values.items() if v is not None}


def map_garmin_hrv_readings(payload: dict[str, Any]) -> list[tuple]:
    readings = []
    for point in payload.get("hrvReadings") or []:
        if not isinstance(point, dict):
            continue
        ts = ms_to_dt(point.get("readingTimeGMT")) or parse_iso(point.get("readingTimeGMT"))
        value = to_float(point.get("hrvValue"))
        if ts and value is not None:
            readings.append((ts, "hrv_5m", value))
    return readings


def map_garmin_daily_summary(payload: dict[str, Any]) -> dict[str, Any]:
    values = {
        "resting_hr": to_int(payload.get("restingHeartRate")),
        "min_hr": to_int(payload.get("minHeartRate")),
        "max_hr": to_int(payload.get("maxHeartRate")),
        "avg_stress": to_int(payload.get("averageStressLevel")),
        "max_stress": to_int(payload.get("maxStressLevel")),
        "rest_stress_s": to_int(payload.get("restStressDuration")),
        "low_stress_s": to_int(payload.get("lowStressDuration")),
        "medium_stress_s": to_int(payload.get("mediumStressDuration")),
        "high_stress_s": to_int(payload.get("highStressDuration")),
        "body_battery_high": to_int(payload.get("bodyBatteryHighestValue")),
        "body_battery_low": to_int(payload.get("bodyBatteryLowestValue")),
        "body_battery_charged": to_int(payload.get("bodyBatteryChargedValue")),
        "body_battery_drained": to_int(payload.get("bodyBatteryDrainedValue")),
        "steps": to_int(payload.get("totalSteps")),
        "floors_climbed": to_int(payload.get("floorsAscended")),
        "distance_m": to_int(payload.get("totalDistanceMeters")),
        "calories_bmr": to_int(payload.get("bmrKilocalories")),
        "calories_active": to_int(payload.get("activeKilocalories")),
        "moderate_intensity_min": to_int(payload.get("moderateIntensityMinutes")),
        "vigorous_intensity_min": to_int(payload.get("vigorousIntensityMinutes")),
        "avg_spo2": to_float(payload.get("averageSpo2")),
        "avg_respiration": to_float(payload.get("avgWakingRespirationValue")),
    }
    return {k: v for k, v in values.items() if v is not None}


def map_garmin_readiness(payload: Any) -> dict[str, Any]:
    """L'endpoint readiness renvoie une liste d'un élément."""
    entry = payload[0] if isinstance(payload, list) and payload else payload
    if not isinstance(entry, dict):
        return {}
    values = {
        "training_readiness": to_int(entry.get("score")),
        "training_readiness_lbl": entry.get("level"),
    }
    return {k: v for k, v in values.items() if v is not None}


def map_garmin_training_status(payload: dict[str, Any]) -> dict[str, Any]:
    """Statut d'entraînement et charge aiguë/chronique.

    Les valeurs sont imbriquées sous une clé qui est l'id de l'appareil, ce
    qui oblige à parcourir le dictionnaire plutôt qu'à nommer une clé.
    """
    values: dict[str, Any] = {}
    devices = dig(payload, "mostRecentTrainingStatus", "latestTrainingStatusData") or {}

    for entry in devices.values():
        if not isinstance(entry, dict):
            continue
        status = entry.get("trainingStatusFeedbackPhrase") or entry.get("trainingStatus")
        if status is not None:
            values.setdefault("training_status", str(status))

        acute = to_float(dig(entry, "acuteTrainingLoadDTO", "acuteTrainingLoad"))
        if acute is not None:
            values.setdefault("acute_load", acute)

        # Garmin ne publie pas de charge chronique nommée : le milieu du
        # "tunnel" de charge optimale en est l'équivalent le plus proche.
        low = to_float(entry.get("loadTunnelMin"))
        high = to_float(entry.get("loadTunnelMax"))
        if low is not None and high is not None:
            values.setdefault("chronic_load", (low + high) / 2)

    return values


def map_garmin_max_metrics(payload: Any) -> dict[dt.date, dict[str, Any]]:
    """VO2max par jour, course et vélo séparés."""
    result: dict[dt.date, dict[str, Any]] = {}
    entries = payload if isinstance(payload, list) else [payload]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        day_raw = entry.get("calendarDate")
        day = parse_iso(day_raw)
        if day is None:
            continue
        bucket = result.setdefault(day.date(), {})
        if (v := to_float(dig(entry, "generic", "vo2MaxValue"))) is not None:
            bucket["vo2max_running"] = v
        if (v := to_float(dig(entry, "cycling", "vo2MaxValue"))) is not None:
            bucket["vo2max_cycling"] = v
    return result


def map_garmin_stress_readings(payload: dict[str, Any]) -> list[tuple]:
    readings = []
    for point in payload.get("stressValuesArray") or []:
        if not isinstance(point, list) or len(point) < 2:
            continue
        ts, value = ms_to_dt(point[0]), to_float(point[1])
        # Garmin encode l'absence de mesure par des valeurs négatives.
        if ts and value is not None and value >= 0:
            readings.append((ts, "stress", value))
    for point in payload.get("bodyBatteryValuesArray") or []:
        if not isinstance(point, list) or len(point) < 3:
            continue
        ts, value = ms_to_dt(point[0]), to_float(point[2])
        if ts and value is not None:
            readings.append((ts, "body_battery", value))
    return readings


def map_garmin_heart_rate_readings(payload: dict[str, Any]) -> list[tuple]:
    readings = []
    for point in payload.get("heartRateValues") or []:
        if not isinstance(point, list) or len(point) < 2:
            continue
        ts, value = ms_to_dt(point[0]), to_float(point[1])
        if ts and value is not None and value > 0:
            readings.append((ts, "hr", value))
    return readings


def map_garmin_weight(payload: Any) -> list[dict[str, Any]]:
    entries = []
    groups = payload.get("dateWeightList") if isinstance(payload, dict) else None
    if groups is None and isinstance(payload, dict):
        groups = [
            item
            for day in (payload.get("dailyWeightSummaries") or [])
            for item in (day.get("allWeightMetrics") or [])
        ]
    for item in groups or []:
        if not isinstance(item, dict):
            continue
        measured = ms_to_dt(item.get("date") or item.get("timestampGMT"))
        grams = to_float(item.get("weight"))
        if measured is None or grams is None:
            continue
        entries.append(
            {
                "measured_at": measured,
                "weight_kg": round(grams / 1000.0, 2),
                "body_fat_pct": to_float(item.get("bodyFat")),
                "muscle_mass_kg": (
                    round(v / 1000.0, 2) if (v := to_float(item.get("muscleMass"))) else None
                ),
                "bone_mass_kg": (
                    round(v / 1000.0, 2) if (v := to_float(item.get("boneMass"))) else None
                ),
                "body_water_pct": to_float(item.get("bodyWater")),
                "bmi": to_float(item.get("bmi")),
            }
        )
    return entries


# ---------------------------------------------------------------------
# Strava
# ---------------------------------------------------------------------


def map_strava_activity(
    payload: dict[str, Any], sport: str, family: str, gear_pk: int | None
) -> dict[str, Any]:
    start_utc = parse_iso(payload.get("start_date"))
    start_local_raw = parse_iso(payload.get("start_date_local"), assume_utc=False)
    start_local = start_local_raw.replace(tzinfo=None) if start_local_raw else None

    fields: dict[str, Any] = {
        "start_time": start_utc,
        "start_time_local": start_local,
        "local_date": start_local.date() if start_local else None,
        "timezone": payload.get("timezone"),
        "sport": sport,
        "sport_family": family,
        "name": payload.get("name"),
        "is_indoor": bool(payload.get("trainer") or False),
        "is_commute": bool(payload.get("commute") or False),
        "is_race": payload.get("workout_type") in (1, 11),
        "elapsed_s": to_int(payload.get("elapsed_time")),
        "moving_s": to_int(payload.get("moving_time")),
        "distance_m": to_float(payload.get("distance")),
        "elevation_gain_m": to_float(payload.get("total_elevation_gain")),
        "elevation_max_m": to_float(payload.get("elev_high")),
        "avg_speed_mps": to_float(payload.get("average_speed")),
        "max_speed_mps": to_float(payload.get("max_speed")),
        "avg_hr": to_int(payload.get("average_heartrate")),
        "max_hr": to_int(payload.get("max_heartrate")),
        "avg_cadence": to_float(payload.get("average_cadence")),
        "avg_power_w": to_int(payload.get("average_watts")),
        "weighted_power_w": to_int(payload.get("weighted_average_watts")),
        "max_power_w": to_int(payload.get("max_watts")),
        "calories": to_int(payload.get("calories")),
        "perceived_exertion": to_int(payload.get("perceived_exertion")),
        "gear_id": gear_pk,
    }
    if start_utc:
        fields["dedup_key"] = dedup_key(start_utc)
    return {k: v for k, v in fields.items() if v is not None}


def map_strava_gear(payload: dict[str, Any]) -> dict[str, Any]:
    gear_id = payload.get("id") or ""
    kind = "bike" if str(gear_id).startswith("b") else "shoes"
    return {
        "strava_gear_id": str(gear_id),
        "kind": kind,
        "name": payload.get("name") or str(gear_id),
        "brand": payload.get("brand_name"),
        "model": payload.get("model_name"),
        "frame_type": _FRAME_TYPES.get(payload.get("frame_type")),
        "is_primary": bool(payload.get("primary") or False),
        "retired": bool(payload.get("retired") or False),
        "distance_m": to_int(payload.get("distance")) or 0,
    }


_FRAME_TYPES = {1: "mtb", 2: "cross", 3: "road", 4: "tt"}


def map_strava_laps(payload: dict[str, Any]) -> list[dict[str, Any]]:
    laps = []
    for index, lap in enumerate(payload.get("laps") or [], start=1):
        if not isinstance(lap, dict):
            continue
        start = parse_iso(lap.get("start_date"))
        if start is None:
            continue
        laps.append(
            {
                "lap_index": index,
                "start_time": start,
                "elapsed_s": to_int(lap.get("elapsed_time")),
                "moving_s": to_int(lap.get("moving_time")),
                "distance_m": to_float(lap.get("distance")),
                "elevation_gain_m": to_float(lap.get("total_elevation_gain")),
                "avg_hr": to_int(lap.get("average_heartrate")),
                "max_hr": to_int(lap.get("max_heartrate")),
                "avg_power_w": to_int(lap.get("average_watts")),
                "avg_cadence": to_float(lap.get("average_cadence")),
                "avg_speed_mps": to_float(lap.get("average_speed")),
            }
        )
    return laps
