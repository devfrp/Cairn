"""Calcul des faits d'une semaine, avant toute rédaction.

Rien de ce qui compte n'est laissé au modèle de langage : charges, moyennes,
écarts à la ligne de base et signaux de fatigue sont calculés ici, en SQL et
en Python. Le modèle ne reçoit que des valeurs déjà établies et n'a plus qu'à
les formuler.

C'est ce qui rend utilisable un petit modèle : on ne lui demande jamais de
compter, seulement d'écrire.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from cairn.db import fetch_all, fetch_one

# Au-delà de cette hausse de charge d'une semaine à l'autre, la progression
# sort de ce qu'on recommande habituellement.
LOAD_SURGE_PCT = 30.0

# Une VFC sous sa ligne de base plusieurs jours d'affilée est le signal de
# fatigue le plus fiable dont on dispose ici.
HRV_SUPPRESSED_DAYS = 3

# Écart en battements au-dessus de la moyenne qui rend une FC de repos
# digne d'être signalée.
RHR_ELEVATED_BPM = 3.0

SPORT_LABELS = {
    "mtb": "VTT", "enduro": "enduro", "road": "route", "gravel": "gravel",
    "trail_run": "trail", "run": "course à pied", "hike": "randonnée",
    "walk": "marche", "swim": "natation", "strength": "renforcement",
    "cardio": "cardio", "mobility": "mobilité", "ski": "ski", "other": "autre",
}


@dataclass
class WeeklyFacts:
    start: dt.date
    end: dt.date

    sessions: int = 0
    duration_s: int = 0
    distance_m: float = 0.0
    elevation_m: float = 0.0
    load: float = 0.0
    by_sport: list[dict[str, Any]] = field(default_factory=list)
    hardest: dict[str, Any] | None = None

    previous_load: float = 0.0
    previous_duration_s: int = 0

    hrv_mean: float | None = None
    hrv_baseline: float | None = None
    hrv_last: float | None = None
    hrv_days_below: int = 0

    rhr_mean: float | None = None
    rhr_baseline: float | None = None

    sleep_mean_score: float | None = None
    sleep_mean_duration_s: float | None = None
    short_nights: int = 0

    signals: list[str] = field(default_factory=list)

    @property
    def load_change_pct(self) -> float | None:
        if not self.previous_load:
            return None
        return (self.load - self.previous_load) / self.previous_load * 100

    @property
    def hrv_delta(self) -> float | None:
        if self.hrv_mean is None or self.hrv_baseline is None:
            return None
        return self.hrv_mean - self.hrv_baseline

    @property
    def rhr_delta(self) -> float | None:
        if self.rhr_mean is None or self.rhr_baseline is None:
            return None
        return self.rhr_mean - self.rhr_baseline

    @property
    def has_data(self) -> bool:
        return bool(self.sessions or self.hrv_mean or self.sleep_mean_score)


def last_complete_week(today: dt.date | None = None) -> tuple[dt.date, dt.date]:
    """Lundi au dimanche de la semaine écoulée.

    On ne résume pas une semaine en cours : les jours manquants fausseraient
    toutes les comparaisons.
    """
    today = today or dt.date.today()
    this_monday = today - dt.timedelta(days=today.weekday())
    start = this_monday - dt.timedelta(days=7)
    return start, start + dt.timedelta(days=6)


def _round(value: Any, digits: int = 1) -> float | None:
    return None if value is None else round(float(value), digits)


def collect(start: dt.date, end: dt.date) -> WeeklyFacts:
    """Rassemble tout ce qui sera dit de la semaine."""
    facts = WeeklyFacts(start=start, end=end)

    totals = fetch_one(
        """
        SELECT count(*) AS sessions,
               coalesce(sum(elapsed_s), 0) AS duration_s,
               coalesce(sum(distance_m), 0) AS distance_m,
               coalesce(sum(elevation_gain_m), 0) AS elevation_m,
               coalesce(sum(coalesce(training_load, trimp)), 0) AS load
          FROM activity_kept WHERE local_date BETWEEN ? AND ?
        """,
        (start, end),
    )
    if totals:
        facts.sessions = totals["sessions"]
        facts.duration_s = int(totals["duration_s"] or 0)
        facts.distance_m = float(totals["distance_m"] or 0)
        facts.elevation_m = float(totals["elevation_m"] or 0)
        facts.load = _round(totals["load"], 0) or 0.0

    facts.by_sport = [
        {
            "sport": SPORT_LABELS.get(row["sport"], row["sport"]),
            "sessions": row["sessions"],
            "duration_s": int(row["duration_s"] or 0),
            "load": _round(row["load"], 0) or 0.0,
        }
        for row in fetch_all(
            """
            SELECT sport, count(*) AS sessions,
                   coalesce(sum(elapsed_s), 0) AS duration_s,
                   coalesce(sum(coalesce(training_load, trimp)), 0) AS load
              FROM activity_kept WHERE local_date BETWEEN ? AND ?
             GROUP BY sport ORDER BY load DESC
            """,
            (start, end),
        )
    ]

    hardest = fetch_one(
        """
        SELECT name, sport, local_date, elapsed_s, distance_m, elevation_gain_m,
               coalesce(training_load, trimp) AS load
          FROM activity_kept
         WHERE local_date BETWEEN ? AND ?
           AND coalesce(training_load, trimp) IS NOT NULL
         ORDER BY coalesce(training_load, trimp) DESC LIMIT 1
        """,
        (start, end),
    )
    if hardest:
        facts.hardest = {
            "name": hardest["name"],
            "sport": SPORT_LABELS.get(hardest["sport"], hardest["sport"]),
            "day": hardest["local_date"],
            "duration_s": hardest["elapsed_s"],
            "elevation_m": _round(hardest["elevation_gain_m"], 0),
            "load": _round(hardest["load"], 0),
        }

    previous = fetch_one(
        """
        SELECT coalesce(sum(coalesce(training_load, trimp)), 0) AS load,
               coalesce(sum(elapsed_s), 0) AS duration_s
          FROM activity_kept WHERE local_date BETWEEN ? AND ?
        """,
        (start - dt.timedelta(days=7), start - dt.timedelta(days=1)),
    )
    if previous:
        facts.previous_load = _round(previous["load"], 0) or 0.0
        facts.previous_duration_s = int(previous["duration_s"] or 0)

    _collect_recovery(facts, start, end)
    _detect_signals(facts)
    return facts


def _collect_recovery(facts: WeeklyFacts, start: dt.date, end: dt.date) -> None:
    hrv = fetch_one(
        """
        SELECT avg(avg_hrv_ms) AS mean FROM hrv_night
         WHERE day BETWEEN ? AND ? AND avg_hrv_ms IS NOT NULL
        """,
        (start, end),
    )
    facts.hrv_mean = _round(hrv["mean"] if hrv else None)

    # Ligne de base : les soixante jours qui précèdent la semaine résumée,
    # pour comparer la semaine à l'habitude et non à elle-même.
    # SQLite ne connaît pas FULL JOIN : deux requêtes sont plus claires
    # qu'une jointure contournée, et les deux séries sont indépendantes.
    window = (start - dt.timedelta(days=60), start - dt.timedelta(days=1))
    hrv_base = fetch_one(
        "SELECT avg(avg_hrv_ms) AS v FROM hrv_night WHERE day BETWEEN ? AND ?", window
    )
    rhr_base = fetch_one(
        "SELECT avg(resting_hr) AS v FROM daily_wellness WHERE day BETWEEN ? AND ?", window
    )
    facts.hrv_baseline = _round(hrv_base["v"] if hrv_base else None)
    facts.rhr_baseline = _round(rhr_base["v"] if rhr_base else None)

    rhr = fetch_one(
        """
        SELECT avg(resting_hr) AS mean FROM daily_wellness
         WHERE day BETWEEN ? AND ? AND resting_hr IS NOT NULL
        """,
        (start, end),
    )
    facts.rhr_mean = _round(rhr["mean"] if rhr else None)

    sleep = fetch_one(
        """
        SELECT avg(score) AS score, avg(duration_s) AS duration,
               sum(CASE WHEN duration_s < 25200 THEN 1 ELSE 0 END) AS short_nights
          FROM sleep_session WHERE sleep_date BETWEEN ? AND ?
        """,
        (start, end),
    )
    if sleep:
        facts.sleep_mean_score = _round(sleep["score"], 0)
        facts.sleep_mean_duration_s = _round(sleep["duration"], 0)
        facts.short_nights = sleep["short_nights"] or 0

    facts.hrv_last, facts.hrv_days_below = _hrv_streak(end, facts.hrv_baseline)


def _hrv_streak(end: dt.date, baseline: float | None) -> tuple[float | None, int]:
    """Dernière VFC connue, et nombre de jours consécutifs sous la base."""
    rows = fetch_all(
        """
        SELECT day, avg_hrv_ms FROM hrv_night
         WHERE day <= ? AND avg_hrv_ms IS NOT NULL
         ORDER BY day DESC LIMIT 14
        """,
        (end,),
    )
    if not rows:
        return None, 0

    last = _round(rows[0]["avg_hrv_ms"])
    if baseline is None:
        return last, 0

    streak = 0
    for row in rows:
        if float(row["avg_hrv_ms"]) < baseline:
            streak += 1
        else:
            break
    return last, streak


def _detect_signals(facts: WeeklyFacts) -> None:
    """Signaux notables, formulés en clair et déjà chiffrés."""
    change = facts.load_change_pct
    if change is not None and change > LOAD_SURGE_PCT:
        # Le pourcentage est déjà donné par le récit : le répéter ici le
        # faisait apparaître deux fois dans deux phrases consécutives. Ce qui
        # manque, c'est la portée de cette hausse, pas sa valeur.
        facts.signals.append(
            "la progression de charge dépasse ce qui est habituellement conseillé"
        )
    # Une baisse n'est pas un signal : elle n'appelle aucune action, et le
    # récit la mentionne déjà. La faire figurer ici déclenchait en prime un
    # conseil d'alléger la semaine — alors qu'elle venait justement de l'être.

    if facts.hrv_days_below >= HRV_SUPPRESSED_DAYS:
        facts.signals.append(
            f"la variabilité cardiaque est sous sa ligne de base depuis "
            f"{facts.hrv_days_below} jours"
        )

    rhr_delta = facts.rhr_delta
    if rhr_delta is not None and rhr_delta >= RHR_ELEVATED_BPM:
        facts.signals.append(
            f"la fréquence cardiaque de repos est supérieure de "
            f"{rhr_delta:.0f} bpm à l'habitude"
        )

    if facts.short_nights >= 3:
        facts.signals.append(f"{facts.short_nights} nuits ont duré moins de 7 heures")

    # La combinaison charge en hausse et récupération en baisse est le
    # signal qui justifie vraiment de lever un drapeau.
    if (
        change is not None
        and change > LOAD_SURGE_PCT
        and facts.hrv_days_below >= HRV_SUPPRESSED_DAYS
    ):
        facts.signals.append(
            "charge en hausse alors que la récupération se dégrade : "
            "situation de surcharge"
        )
