"""Lecture des fichiers .fit archivés.

Les streams viennent du fichier original plutôt que d'un endpoint d'API :
c'est la donnée telle que la montre l'a enregistrée, complète, et ça ne coûte
aucune requête supplémentaire puisque le .fit est déjà sur disque.

fitdecode applique déjà les facteurs d'échelle du profil FIT : les distances
sont en mètres, les vitesses en m/s, les altitudes en mètres. Restent les
coordonnées, stockées en semicircles, qu'on convertit ici.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitdecode
import structlog

log = structlog.get_logger(__name__)

# Un semicircle vaut 180 / 2^31 degrés.
SEMICIRCLE_TO_DEGREES = 180.0 / (2**31)

# Au-delà, on considère que le capteur déraille plutôt que l'athlète.
MAX_PLAUSIBLE_HR = 240


@dataclass
class FitActivity:
    """Contenu exploitable d'un fichier .fit."""

    records: list[dict[str, Any]] = field(default_factory=list)
    laps: list[dict[str, Any]] = field(default_factory=list)
    session: dict[str, Any] = field(default_factory=dict)

    @property
    def start_time(self) -> dt.datetime | None:
        if self.session.get("start_time"):
            return self.session["start_time"]
        return self.records[0]["time"] if self.records else None


def _as_utc(value: Any) -> dt.datetime | None:
    if not isinstance(value, dt.datetime):
        return None
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value.astimezone(dt.UTC)


def _semicircles(value: Any) -> float | None:
    if value is None:
        return None
    try:
        degrees = float(value) * SEMICIRCLE_TO_DEGREES
    except (TypeError, ValueError):
        return None
    return degrees if -180.0 <= degrees <= 180.0 else None


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None  # écarte les NaN


def _int(value: Any) -> int | None:
    number = _num(value)
    return int(number) if number is not None else None


def _parse_record(msg: fitdecode.FitDataMessage) -> dict[str, Any] | None:
    timestamp = _as_utc(msg.get_value("timestamp", fallback=None))
    if timestamp is None:
        return None

    # Les champs « enhanced_* » couvrent une plage plus large et priment
    # quand l'appareil les fournit.
    speed = _num(msg.get_value("enhanced_speed", fallback=None))
    if speed is None:
        speed = _num(msg.get_value("speed", fallback=None))
    altitude = _num(msg.get_value("enhanced_altitude", fallback=None))
    if altitude is None:
        altitude = _num(msg.get_value("altitude", fallback=None))

    hr = _num(msg.get_value("heart_rate", fallback=None))
    if hr is not None and not (0 < hr <= MAX_PLAUSIBLE_HR):
        hr = None

    return {
        "time": timestamp,
        "hr": int(hr) if hr is not None else None,
        "cadence": _int(msg.get_value("cadence", fallback=None)),
        "power_w": _int(msg.get_value("power", fallback=None)),
        "speed_mps": speed,
        "altitude_m": altitude,
        "distance_m": _num(msg.get_value("distance", fallback=None)),
        "grade_pct": _num(msg.get_value("grade", fallback=None)),
        "temperature_c": _num(msg.get_value("temperature", fallback=None)),
        "lat": _semicircles(msg.get_value("position_lat", fallback=None)),
        "lon": _semicircles(msg.get_value("position_long", fallback=None)),
    }


def _parse_lap(msg: fitdecode.FitDataMessage, index: int) -> dict[str, Any] | None:
    start = _as_utc(msg.get_value("start_time", fallback=None))
    if start is None:
        return None
    return {
        "lap_index": index,
        "start_time": start,
        "elapsed_s": _int(msg.get_value("total_elapsed_time", fallback=None)),
        "moving_s": _int(msg.get_value("total_timer_time", fallback=None)),
        "distance_m": _num(msg.get_value("total_distance", fallback=None)),
        "elevation_gain_m": _num(msg.get_value("total_ascent", fallback=None)),
        "avg_hr": _int(msg.get_value("avg_heart_rate", fallback=None)),
        "max_hr": _int(msg.get_value("max_heart_rate", fallback=None)),
        "avg_power_w": _int(msg.get_value("avg_power", fallback=None)),
        "avg_cadence": _num(msg.get_value("avg_cadence", fallback=None)),
        "avg_speed_mps": _num(msg.get_value("enhanced_avg_speed", fallback=None))
        or _num(msg.get_value("avg_speed", fallback=None)),
    }


def parse_fit(source: Path | bytes) -> FitActivity:
    """Lit un .fit et retourne ses records, tours et résumé de session.

    Un fichier tronqué ou corrompu ne fait pas tout échouer : fitdecode
    signale l'erreur et on garde ce qui a pu être lu.
    """
    activity = FitActivity()
    lap_index = 0

    fileish: Any = str(source) if isinstance(source, Path) else source
    try:
        with fitdecode.FitReader(
            fileish,
            check_crc=fitdecode.CrcCheck.WARN,
            error_handling=fitdecode.ErrorHandling.WARN,
        ) as reader:
            for frame in reader:
                if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                    continue

                if frame.name == "record":
                    record = _parse_record(frame)
                    if record is not None:
                        activity.records.append(record)

                elif frame.name == "lap":
                    lap_index += 1
                    lap = _parse_lap(frame, lap_index)
                    if lap is not None:
                        activity.laps.append(lap)

                elif frame.name == "session":
                    activity.session = {
                        "sport": frame.get_value("sport", fallback=None),
                        "sub_sport": frame.get_value("sub_sport", fallback=None),
                        "start_time": _as_utc(frame.get_value("start_time", fallback=None)),
                        "total_elapsed_time": _num(
                            frame.get_value("total_elapsed_time", fallback=None)
                        ),
                        "total_distance": _num(
                            frame.get_value("total_distance", fallback=None)
                        ),
                        "total_ascent": _num(frame.get_value("total_ascent", fallback=None)),
                    }
    except Exception as exc:  # noqa: BLE001 — un .fit abîmé ne doit pas tout bloquer
        log.warning(
            "fit: lecture interrompue, on garde ce qui a été lu",
            error=str(exc),
            records=len(activity.records),
        )

    activity.records.sort(key=lambda r: r["time"])
    return activity


def to_stream_rows(activity_id: int, records: list[dict[str, Any]]) -> list[tuple]:
    """Prépare les lignes d'`activity_stream`, avec l'offset depuis le départ.

    `elapsed_s` sert d'axe « temps de sortie » : c'est ce qu'on veut en
    abscisse pour comparer deux sorties entre elles, pas l'heure murale.
    """
    if not records:
        return []

    origin = records[0]["time"]
    rows = []
    for record in records:
        elapsed = int((record["time"] - origin).total_seconds())
        rows.append(
            (
                activity_id,
                record["time"],
                elapsed,
                record["hr"],
                record["cadence"],
                record["power_w"],
                record["speed_mps"],
                record["altitude_m"],
                record["distance_m"],
                record["grade_pct"],
                record["temperature_c"],
                record["lat"],
                record["lon"],
                None if record["speed_mps"] is None else record["speed_mps"] > 0.5,
            )
        )
    return rows
