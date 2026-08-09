"""Calcul des métriques dérivées et écriture dans les tables `marts_*`.

Ces valeurs ne viennent d'aucune API : elles se déduisent de l'historique
complet. Les moyennes exponentielles étant récursives, chaque journée dépend
de la précédente — on recalcule donc la série entière depuis la première
activité connue plutôt que de tenter une reprise partielle. Sur quelques
milliers de jours, le coût est négligeable et le résultat toujours cohérent.
"""

from __future__ import annotations

import datetime as dt
import json

import structlog

from cairn.db import as_date, connection, fetch_all, fetch_one
from cairn.metrics import formulas, signals

log = structlog.get_logger(__name__)

# Fenêtre glissante sur laquelle monotonie et contrainte sont évaluées.
WEEK = 7


def _daily_loads(athlete_id: int, start: dt.date, end: dt.date) -> dict[dt.date, dict]:
    """Volume et charge par jour, activités agrégées."""
    rows = fetch_all(
        """
        SELECT local_date AS day,
               count(*) AS session_count,
               coalesce(sum(elapsed_s), 0) AS duration_s,
               coalesce(sum(distance_m), 0) AS distance_m,
               coalesce(sum(elevation_gain_m), 0) AS elevation_gain_m,
               coalesce(sum(coalesce(training_load, trimp)), 0) AS load
          FROM activity
         WHERE athlete_id = ? AND local_date BETWEEN ? AND ?
         GROUP BY local_date
        """,
        (athlete_id, start, end),
    )
    return {
        as_date(row["day"]): dict(zip(row.keys(), tuple(row), strict=True))
        for row in rows
    }


def _recovery_inputs(athlete_id: int, start: dt.date, end: dt.date) -> dict[dt.date, dict]:
    rows = fetch_all(
        """
        SELECT h.day AS day, h.avg_hrv_ms, w.resting_hr, w.training_readiness,
               s.score AS sleep_score, s.duration_s AS sleep_duration_s,
               s.sleep_need_s
          FROM hrv_night h
          LEFT JOIN daily_wellness w ON w.athlete_id = h.athlete_id AND w.day = h.day
          LEFT JOIN sleep_session s ON s.athlete_id = h.athlete_id AND s.sleep_date = h.day
         WHERE h.athlete_id = ? AND h.day BETWEEN ? AND ?
        """,
        (athlete_id, start, end),
    )
    merged = {
        as_date(row["day"]): dict(zip(row.keys(), tuple(row), strict=True))
        for row in rows
    }

    # Une journée peut avoir une FC de repos et un sommeil sans mesure de VFC :
    # la jointure ci-dessus la manquerait.
    extra = fetch_all(
        """
        SELECT w.day AS day, NULL AS avg_hrv_ms, w.resting_hr, w.training_readiness,
               s.score AS sleep_score, s.duration_s AS sleep_duration_s,
               s.sleep_need_s
          FROM daily_wellness w
          LEFT JOIN sleep_session s ON s.athlete_id = w.athlete_id AND s.sleep_date = w.day
         WHERE w.athlete_id = ? AND w.day BETWEEN ? AND ?
        """,
        (athlete_id, start, end),
    )
    for row in extra:
        merged.setdefault(
            as_date(row["day"]), dict(zip(row.keys(), tuple(row), strict=True))
        )
    return merged


def _date_range(start: dt.date, end: dt.date) -> list[dt.date]:
    span = (end - start).days
    return [start + dt.timedelta(days=offset) for offset in range(span + 1)]


def _first_day(athlete_id: int) -> dt.date | None:
    row = fetch_one(
        """
        SELECT min(day) AS day FROM (
            SELECT min(local_date) AS day FROM activity WHERE athlete_id = ?
            UNION ALL
            SELECT min(day) FROM hrv_night WHERE athlete_id = ?
            UNION ALL
            SELECT min(day) FROM daily_wellness WHERE athlete_id = ?
        )
        """,
        (athlete_id, athlete_id, athlete_id),
    )
    return as_date(row["day"]) if row and row["day"] else None


def recompute(athlete_id: int | None = None, today: dt.date | None = None) -> dict[str, int]:
    """Recalcule charge, lignes de base et signaux sur tout l'historique."""
    today = today or dt.date.today()

    if athlete_id is None:
        row = fetch_one("SELECT id FROM athlete ORDER BY id LIMIT 1")
        if row is None:
            log.info("métriques : aucun athlète, rien à calculer")
            return {"days": 0, "flags": 0}
        athlete_id = row["id"]

    start = _first_day(athlete_id)
    if start is None:
        log.info("métriques : aucune donnée")
        return {"days": 0, "flags": 0}

    # La ligne de base regarde en arrière : on remonte assez loin pour que
    # le premier jour calculé dispose déjà d'un historique.
    days = _date_range(start, today)
    loads = _daily_loads(athlete_id, start, today)
    recovery = _recovery_inputs(athlete_id, start, today)

    load_series = [float(loads.get(day, {}).get("load") or 0.0) for day in days]
    ctl_series = formulas.ewma(load_series, formulas.CTL_DAYS)
    atl_series = formulas.ewma(load_series, formulas.ATL_DAYS)

    hrv_series = [recovery.get(day, {}).get("avg_hrv_ms") for day in days]
    rhr_series = [recovery.get(day, {}).get("resting_hr") for day in days]
    sleep_series = [recovery.get(day, {}).get("sleep_score") for day in days]

    load_rows, baseline_rows, flag_rows = [], [], []

    for index, day in enumerate(days):
        daily = loads.get(day, {})
        ctl, atl = ctl_series[index], atl_series[index]
        window = load_series[max(0, index - WEEK + 1): index + 1]

        monotony = formulas.monotony(window) if len(window) == WEEK else None
        acwr = formulas.acute_chronic_ratio(atl, ctl)

        load_rows.append((
            athlete_id, day,
            daily.get("session_count", 0),
            int(daily.get("duration_s") or 0),
            float(daily.get("distance_m") or 0),
            float(daily.get("elevation_gain_m") or 0),
            float(daily.get("load") or 0),
            round(ctl, 2), round(atl, 2),
            round(formulas.training_stress_balance(ctl, atl), 2),
            round(acwr, 3) if acwr is not None else None,
            round(monotony, 2) if monotony is not None else None,
            round(formulas.strain(window, monotony), 1) if monotony is not None else None,
        ))

        baseline, flags = _baseline_for(
            index, day, hrv_series, rhr_series, sleep_series, recovery, days
        )
        if baseline is not None:
            baseline_rows.append((athlete_id, *baseline))

        for signal in signals.detect(
            acwr=acwr,
            monotony=monotony,
            hrv_z=flags["hrv_z"],
            hrv_days_below=flags["hrv_days_below"],
            hrv_trend=flags["hrv_trend"],
            rhr_delta=flags["rhr_delta"],
            sleep_debt_s=flags["sleep_debt_s"],
        ):
            flag_rows.append((
                athlete_id, day, signal.kind, signal.severity, signal.message,
                json.dumps(signal.detail, default=str),
            ))

    _persist(athlete_id, load_rows, baseline_rows, flag_rows)
    log.info("métriques recalculées", jours=len(days), signaux=len(flag_rows))
    return {"days": len(days), "flags": len(flag_rows)}


def _baseline_for(index, day, hrv_series, rhr_series, sleep_series, recovery, days):
    """Ligne de base d'un jour, et les valeurs nécessaires aux signaux.

    La fenêtre de référence s'arrête la veille : comparer une mesure à une
    moyenne qui la contient atténuerait justement ce qu'on cherche à voir.
    """
    window_start = max(0, index - formulas.BASELINE_DAYS)
    hrv_window = hrv_series[window_start:index]
    rhr_window = rhr_series[window_start:index]

    hrv_mean, hrv_sd = formulas.mean_and_deviation(hrv_window)
    rhr_mean, _ = formulas.mean_and_deviation(rhr_window)

    hrv_value = hrv_series[index]
    rhr_value = rhr_series[index]

    hrv_z = formulas.zscore(hrv_value, hrv_mean, hrv_sd)
    hrv_trend = formulas.slope(hrv_series[max(0, index - 6): index + 1])
    rhr_delta = (rhr_value - rhr_mean) if (rhr_value is not None and rhr_mean) else None

    threshold = None
    if hrv_mean is not None and hrv_sd:
        threshold = hrv_mean + signals.HRV_Z_LOW * hrv_sd
    hrv_days_below = formulas.consecutive_below(hrv_series[: index + 1], threshold)

    recent_sleep = sleep_series[max(0, index - 6): index + 1]
    known_sleep = [s for s in recent_sleep if s is not None]
    sleep_7d = sum(known_sleep) / len(known_sleep) if known_sleep else None

    debt = 0
    counted = False
    for offset in range(max(0, index - 6), index + 1):
        entry = recovery.get(days[offset], {})
        need, slept = entry.get("sleep_need_s"), entry.get("sleep_duration_s")
        if need and slept:
            debt += max(0, need - slept)
            counted = True
    sleep_debt = debt if counted else None

    payload = {
        "hrv_z": hrv_z,
        "hrv_days_below": hrv_days_below,
        "hrv_trend": hrv_trend,
        "rhr_delta": rhr_delta,
        "sleep_debt_s": sleep_debt,
    }

    entry = recovery.get(day, {})
    if not entry:
        return None, payload

    baseline = (
        day,
        hrv_value,
        round(hrv_mean, 2) if hrv_mean is not None else None,
        round(hrv_sd, 2) if hrv_sd is not None else None,
        round(hrv_z, 2) if hrv_z is not None else None,
        round(hrv_trend, 3) if hrv_trend is not None else None,
        rhr_value,
        round(rhr_mean, 1) if rhr_mean is not None else None,
        round(rhr_delta, 1) if rhr_delta is not None else None,
        entry.get("sleep_score"),
        round(sleep_7d, 1) if sleep_7d is not None else None,
        sleep_debt,
        entry.get("training_readiness"),
    )
    return baseline, payload


def _persist(athlete_id, load_rows, baseline_rows, flag_rows) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM marts_daily_load WHERE athlete_id = ?", (athlete_id,))
        conn.executemany(
            """
            INSERT INTO marts_daily_load
                   (athlete_id, day, session_count, duration_s, distance_m,
                    elevation_gain_m, load, ctl, atl, tsb, acwr, monotony, strain)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            load_rows,
        )

        conn.execute(
            "DELETE FROM marts_recovery_baseline WHERE athlete_id = ?", (athlete_id,)
        )
        conn.executemany(
            """
            INSERT INTO marts_recovery_baseline
                   (athlete_id, day, hrv_ms, hrv_mean_60d, hrv_sd_60d, hrv_z,
                    hrv_trend_7d, rhr, rhr_mean_60d, rhr_delta, sleep_score,
                    sleep_score_7d, sleep_debt_s, readiness)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            baseline_rows,
        )

        # Les signaux déjà acquittés sont conservés : l'utilisateur les a vus
        # et n'a pas à les revoir à chaque recalcul.
        conn.execute(
            "DELETE FROM marts_flag WHERE athlete_id = ? AND acknowledged = 0",
            (athlete_id,),
        )
        conn.executemany(
            """
            INSERT INTO marts_flag (athlete_id, day, kind, severity, message, detail)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (athlete_id, day, kind) DO NOTHING
            """,
            flag_rows,
        )
