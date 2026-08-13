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


# Une installation de schéma qui échoue échouera de la même façon à la
# suivante : on ne réessaie pas trente secondes plus tard, indéfiniment.
_schema_attempted: set[str] = set()


def ensure_schema() -> Health:
    """Installe le schéma s'il manque, puis retourne l'état de la base.

    Le fichier appartient à l'application et les migrations sont rejouables :
    rien ne justifie de réclamer un clic avant de pouvoir se servir de quoi
    que ce soit. Jusqu'ici, une base neuve renvoyait l'utilisateur vers un
    bouton qu'il devait deviner, et tout le reste — connexion Garmin comprise
    — refusait de démarrer sans dire pourquoi.
    """
    health = probe()
    if health.usable or not health.reachable:
        return health

    from cairn.config import get_settings

    marker = str(get_settings().db_path)
    if marker in _schema_attempted:
        return health
    _schema_attempted.add(marker)

    try:
        from cairn.migrate import upgrade

        upgrade()
    except Exception as exc:  # noqa: BLE001 — diagnostic, pas de propagation
        health.detail = f"Installation du schéma impossible — {type(exc).__name__} : {exc}"
        return health

    return probe()


def forget_schema_attempts() -> None:
    """À appeler quand les réglages changent : la base visée n'est plus la même."""
    _schema_attempted.clear()


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
    # Dates de la série, indispensables pour dire de quel jour on parle quand
    # l'utilisateur pointe un endroit du graphe.
    days: list[str] = field(default_factory=list)
    sleep_series: list[float | None] = field(default_factory=list)
    battery_series: list[float | None] = field(default_factory=list)


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
        days=[str(r["day"]) for r in rows],
        hrv_series=[r["avg_hrv_ms"] for r in rows],
        rhr_series=[r["resting_hr"] for r in rows],
        sleep_series=[r["duration_s"] for r in rows],
        battery_series=[r["body_battery_high"] for r in rows],
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
        # Durée cherchée séparément du score : toutes les montres ne calculent
        # pas de score de sommeil, et la lier à lui faisait disparaître des
        # centaines de nuits pourtant mesurées.
        if result.sleep_duration_s is None and entry["duration_s"] is not None:
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
    days: list[str] = field(default_factory=list)

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
          LEFT JOIN activity_kept a ON a.local_date = calendar.day
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
          FROM activity_kept
         WHERE local_date > date('now', 'localtime', '-7 days')
        """
    )
    previous = _safe(
        """
        SELECT coalesce(sum(coalesce(training_load, trimp)), 0) AS load
          FROM activity_kept
         WHERE local_date > date('now', 'localtime', '-14 days')
           AND local_date <= date('now', 'localtime', '-7 days')
        """
    )
    month = _safe(
        """
        SELECT coalesce(sum(coalesce(training_load, trimp)), 0) AS load
          FROM activity_kept WHERE local_date > date('now', 'localtime', '-28 days')
        """
    )

    result = Load(
        daily_series=[float(r["load"] or 0) for r in series],
        days=[str(r["day"]) for r in series],
    )
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


def _plafond_vitesse_sql() -> str:
    """Traduit la table des vitesses impossibles en expression SQL.

    Générée depuis la constante Python plutôt que recopiée : deux tables de
    seuils finiraient par diverger, et c'est celle qu'on ne relit pas qui
    ferait foi. Les familles absentes n'ont pas de plafond, d'où la borne
    inatteignable en repli.
    """
    from cairn.metrics.formulas import VITESSE_IMPOSSIBLE_KMH

    cas = " ".join(
        f"WHEN '{famille}' THEN {plafond:.1f}"
        for famille, plafond in sorted(VITESSE_IMPOSSIBLE_KMH.items())
    )
    return f"CASE a.sport_family {cas} ELSE 1e9 END"


PLAFOND_VITESSE_SQL = _plafond_vitesse_sql()


@dataclass
class DailyMetrics:
    """Toutes les mesures quotidiennes disponibles, sur un calendrier commun.

    Un seul calendrier pour toutes les séries : c'est ce qui permet au graphe
    d'aligner un pic de charge sur la nuit qui a suivi. Les séries sont
    rendues par clé plutôt que par attribut — le tableau de bord en propose
    une vingtaine au choix, et autant de champs figés serait ingérable.
    """

    days: list[str] = field(default_factory=list)
    series: dict[str, list[float | None]] = field(default_factory=dict)

    def get(self, key: str) -> list[float | None]:
        return self.series.get(key, [])


def daily_metrics(days: int = 30) -> DailyMetrics:
    """Récupération, sommeil, dépense et charge, jour par jour."""
    rows = _safe(
        """
        WITH RECURSIVE calendar(day) AS (
            SELECT date('now', 'localtime', ? || ' days')
            UNION ALL
            SELECT date(day, '+1 day') FROM calendar
             WHERE day < date('now', 'localtime')
        )
        SELECT calendar.day AS day,
               h.avg_hrv_ms, h.weekly_avg_ms,
               w.resting_hr, w.min_hr, w.max_hr,
               w.avg_stress, w.max_stress,
               w.body_battery_high, w.body_battery_low,
               w.body_battery_charged, w.body_battery_drained,
               w.steps, w.distance_m AS walk_distance_m,
               w.calories_active, w.avg_respiration,
               -- Le calcul doit rester nul quand la journée est absente :
               -- 0 + 0 afficherait un zéro là où rien n'a été mesuré.
               CASE WHEN w.day IS NOT NULL
                    THEN coalesce(w.moderate_intensity_min, 0)
                       + coalesce(w.vigorous_intensity_min, 0) END AS intensity_min,
               w.calories_bmr, w.rest_stress_s, w.high_stress_s,
               w.body_battery_charged, w.body_battery_drained,
               CASE WHEN w.day IS NOT NULL
                    THEN coalesce(w.calories_bmr, 0)
                       + coalesce(w.calories_active, 0) END AS calories_total,
               CASE WHEN w.max_hr IS NOT NULL AND w.min_hr IS NOT NULL
                    THEN w.max_hr - w.min_hr END AS hr_amplitude,
               s.duration_s AS sleep_s, s.deep_s, s.rem_s, s.light_s, s.awake_s,
               s.avg_hr AS sleep_hr, s.lowest_hr AS sleep_low_hr,
               s.avg_respiration AS sleep_respiration,
               -- Part de la nuit réellement dormie : deux nuits de huit heures
               -- ne se valent pas si l'une compte une heure d'éveil.
               CASE WHEN s.duration_s > 0
                    THEN 100.0 * s.duration_s
                       / (s.duration_s + coalesce(s.awake_s, 0)) END AS sleep_efficiency,
               CASE WHEN s.duration_s > 0
                    THEN 100.0 * coalesce(s.deep_s, 0) / s.duration_s END AS deep_share,
               (SELECT coalesce(sum(coalesce(a.training_load, a.trimp)), 0)
                  FROM activity_kept a WHERE a.local_date = calendar.day) AS load,
               (SELECT coalesce(sum(a.distance_m), 0)
                  FROM activity_kept a WHERE a.local_date = calendar.day)
                 AS sport_distance_m,
               (SELECT coalesce(sum(a.elevation_gain_m), 0)
                  FROM activity_kept a WHERE a.local_date = calendar.day)
                 AS elevation_m,
               (SELECT coalesce(sum(a.elevation_loss_m), 0)
                  FROM activity_kept a WHERE a.local_date = calendar.day)
                 AS descent_m,
               (SELECT coalesce(sum(a.elapsed_s), 0)
                  FROM activity_kept a WHERE a.local_date = calendar.day) AS sport_s,
               (SELECT coalesce(sum(a.moving_s), 0)
                  FROM activity_kept a WHERE a.local_date = calendar.day) AS moving_s,
               (SELECT sum(a.calories)
                  FROM activity_kept a WHERE a.local_date = calendar.day) AS sport_calories,
               (SELECT max(a.max_speed_mps) * 3.6
                  FROM activity_kept a
                 WHERE a.local_date = calendar.day
                   AND a.max_speed_mps * 3.6 <= """ + PLAFOND_VITESSE_SQL + """
               ) AS max_speed_kmh,
               -- Puissance pondérée par la durée : la moyenne simple donnerait
               -- autant de poids à dix minutes de home-trainer qu'à trois heures.
               (SELECT sum(a.avg_power_w * a.elapsed_s) / sum(a.elapsed_s)
                  FROM activity_kept a
                 WHERE a.local_date = calendar.day AND a.avg_power_w IS NOT NULL)
                 AS avg_power_w,
               m.ctl, m.atl, m.tsb, m.acwr, m.monotony, m.strain, m.session_count
          FROM calendar
          LEFT JOIN hrv_night       h ON h.day        = calendar.day
          LEFT JOIN daily_wellness  w ON w.day        = calendar.day
          LEFT JOIN sleep_session   s ON s.sleep_date = calendar.day
          LEFT JOIN marts_daily_load m ON m.day       = calendar.day
         ORDER BY calendar.day
        """,
        (-days,),
    )
    if not rows:
        return DailyMetrics()

    # `.keys()` est obligatoire : une ligne SQLite s'itère par valeur.
    colonnes = [k for k in rows[0].keys() if k != "day"]  # noqa: SIM118
    return DailyMetrics(
        days=[str(r["day"]) for r in rows],
        series={k: [r[k] for r in rows] for k in colonnes},
    )


@dataclass
class Intraday:
    """Une journée relevée en continu par la montre."""

    day: str = ""
    labels: list[str] = field(default_factory=list)
    series: dict[str, list[float | None]] = field(default_factory=dict)
    # Nuits et séances, pour situer les creux et les pics sur l'axe.
    spans: list[dict[str, Any]] = field(default_factory=list)
    readings: int = 0

    def get(self, key: str) -> list[float | None]:
        return self.series.get(key, [])


# Cinq minutes : la montre relève toutes les deux à trois minutes, donc chaque
# case contient au moins une mesure. Plus fin, la courbe se briserait en
# pointillés là où l'échantillonnage a simplement sauté un tour.
INTRADAY_BUCKET_MIN = 5


def intraday_bounds() -> dict[str, Any]:
    """Premier et dernier jour que la vue détaillée a quelque chose à montrer.

    Trois sources, pas une : une journée peut n'avoir aucun relevé continu et
    porter tout de même une séance et une nuit. S'en tenir à
    `wellness_reading` interdisait de naviguer jusqu'à aujourd'hui tant que la
    montre n'avait pas encore versé ses mesures de la journée.

    Les bornes des relevés sont prises sur l'instant brut, puis ramenées à
    l'heure locale en Python : demander à SQLite `min(date(time, 'localtime'))`
    l'oblige à convertir les cent soixante-dix mille lignes une à une, soit un
    demi-quart de seconde pour deux dates.
    """
    releves = _safe("SELECT min(time) AS premier, max(time) AS dernier FROM wellness_reading")
    autres = _safe(
        """
        SELECT min(jour) AS premier, max(jour) AS dernier FROM (
            SELECT min(local_date) AS jour FROM activity
            UNION ALL SELECT max(local_date) FROM activity
            UNION ALL SELECT min(sleep_date) FROM sleep_session
            UNION ALL SELECT max(sleep_date) FROM sleep_session
        )
        """
    )

    premiers = [_local_day(releves[0]["premier"]) if releves else None]
    derniers = [_local_day(releves[0]["dernier"]) if releves else None]
    if autres:
        premiers.append(_iso_day(autres[0]["premier"]))
        derniers.append(_iso_day(autres[0]["dernier"]))

    connus_debut = [d for d in premiers if d]
    connus_fin = [d for d in derniers if d]
    return {
        "first_day": min(connus_debut) if connus_debut else None,
        "last_day": max(connus_fin) if connus_fin else None,
    }


def _iso_day(valeur: Any) -> str | None:
    """Date rendue par une agrégation : SQLite en perd le type déclaré."""
    if valeur is None:
        return None
    texte = str(valeur)[:10]
    try:
        dt.date.fromisoformat(texte)
    except ValueError:
        return None
    return texte


def _local_day(instant: Any) -> str | None:
    """Date locale d'un horodatage UTC."""
    if instant is None:
        return None
    if not isinstance(instant, dt.datetime):
        try:
            instant = dt.datetime.fromisoformat(str(instant))
        except ValueError:
            return None
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=dt.UTC)
    return instant.astimezone().date().isoformat()


def _utc_window(day: str) -> tuple[str, str]:
    """Bornes UTC encadrant une journée locale, au format stocké.

    Les horodatages étant tous normalisés en UTC à l'écriture, leur
    comparaison lexicographique équivaut à la comparaison chronologique : une
    plage de texte suffit, et elle évite d'appeler une fonction de date sur
    chaque ligne de la table.
    """
    minuit = dt.datetime.fromisoformat(day).astimezone()
    lendemain = minuit + dt.timedelta(days=1)
    return (
        minuit.astimezone(dt.UTC).isoformat(sep=" "),
        lendemain.astimezone(dt.UTC).isoformat(sep=" "),
    )


# Profondeur du sommeil, du plus profond au plus léger. Traduite en nombre
# pour se tracer comme une courbe en escalier — la lecture classique d'un
# hypnogramme, où l'on voit d'un coup d'œil si les cycles se sont enchaînés.
SLEEP_DEPTH = {"deep": 0.0, "light": 1.0, "rem": 2.0, "awake": 3.0}


def intraday(day: str | None = None, bucket_min: int = INTRADAY_BUCKET_MIN) -> Intraday:
    """Tout ce qui est mesuré en continu sur une journée.

    Trois sources se rejoignent sur le même axe : les relevés de la montre au
    fil de la journée, les stades de sommeil de la nuit, et les flux seconde
    par seconde des séances. Les valeurs sont moyennées dans des cases de
    largeur fixe pour que l'axe reste proportionnel au temps ; une case sans
    relevé reste vide plutôt que d'être comblée, la montre retirée en milieu
    d'après-midi devant se voir.
    """
    if day is None:
        day = intraday_bounds()["last_day"]
    if not day:
        return Intraday()

    cases = 24 * 60 // bucket_min
    debut, fin = _utc_window(day)
    minuit = dt.datetime.fromisoformat(debut)

    def case_de(instant: Any) -> int | None:
        """Case de l'axe où tombe un horodatage UTC."""
        moment = _as_utc(instant)
        if moment is None:
            return None
        ecoule = (moment - minuit).total_seconds() / 60
        return min(max(int(ecoule // bucket_min), 0), cases - 1)

    cumuls: dict[str, list[list[float]]] = {
        cle: [[] for _ in range(cases)]
        for cle in ("hr", "stress", "body_battery", "sport_hr",
                    "speed_kmh", "altitude_m", "cadence")
    }

    # Les trois colonnes de la clé primaire sont nommées dans l'ordre : sans
    # l'athlète ni la mesure, la plage horaire seule oblige SQLite à parcourir
    # les cent soixante-dix mille relevés, soit une seconde et demie au premier
    # affichage, le temps que le fichier monte en cache.
    rows = _safe(
        """
        SELECT metric,
               CAST(strftime('%H', time, 'localtime') AS INTEGER) * 60
             + CAST(strftime('%M', time, 'localtime') AS INTEGER) AS minute,
               value
          FROM wellness_reading
         WHERE athlete_id IN (SELECT id FROM athlete)
           AND metric IN ('hr', 'stress', 'body_battery')
           AND time >= ? AND time < ?
        """,
        (debut, fin),
    )
    for entry in rows:
        seau = cumuls.get(entry["metric"])
        if seau is None:
            continue
        index = min(int(entry["minute"]) // bucket_min, cases - 1)
        # Garmin code par -1 les instants où la mesure n'a pas abouti ; les
        # laisser passer creuserait un puits au milieu de la courbe.
        if entry["value"] is not None and entry["value"] >= 0:
            seau[index].append(float(entry["value"]))

    # Pendant une séance, la montre n'enregistre au fil de la journée qu'un
    # point toutes les deux ou trois minutes, alors que le fichier de la sortie
    # en compte un par seconde. On préfère donc le second là où il existe.
    streams = _safe(
        """
        SELECT s.time, s.hr, s.speed_mps, s.altitude_m, s.cadence
          FROM activity_stream s
         WHERE s.activity_id IN (SELECT id FROM activity_kept
                                  WHERE local_date IN (?, date(?, '-1 day')))
           AND s.time >= ? AND s.time < ?
        """,
        (day, day, debut, fin),
    )
    for point in streams:
        index = case_de(point["time"])
        if index is None:
            continue
        for colonne, cle, facteur in (
            ("hr", "sport_hr", 1.0), ("speed_mps", "speed_kmh", 3.6),
            ("altitude_m", "altitude_m", 1.0), ("cadence", "cadence", 1.0),
        ):
            valeur = point[colonne]
            if valeur is not None:
                cumuls[cle][index].append(float(valeur) * facteur)

    series = {
        cle: [sum(v) / len(v) if v else None for v in seaux]
        for cle, seaux in cumuls.items()
    }
    # La fréquence de la séance remplace celle de la journée sur ses cases :
    # une moyenne de trois cents points vaut mieux qu'un relevé isolé.
    series["hr"] = [
        sport if sport is not None else jour
        for sport, jour in zip(series.pop("sport_hr"), series["hr"], strict=True)
    ]
    series["sleep_stage"] = _sleep_stage_series(debut, fin, cases, bucket_min, minuit)

    result = Intraday(
        day=day,
        labels=[f"{i * bucket_min // 60:02d}:{i * bucket_min % 60:02d}" for i in range(cases)],
        series=series,
        readings=len(rows) + len(streams),
    )
    result.spans = _intraday_spans(day, bucket_min, cases)
    return result


def _as_utc(value: Any) -> dt.datetime | None:
    """Horodatage exploitable, qu'il vienne converti ou en texte brut."""
    if value is None:
        return None
    if not isinstance(value, dt.datetime):
        try:
            value = dt.datetime.fromisoformat(str(value))
        except ValueError:
            return None
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def _sleep_stage_series(
    debut: str, fin: str, cases: int, bucket_min: int, minuit: dt.datetime
) -> list[float | None]:
    """Profondeur du sommeil case par case.

    Les stades sont stockés en intervalles, pas en points. Une case prend le
    stade qui couvre son milieu : découper sur les bornes ferait déborder
    chaque phase d'une case sur la suivante, et la dernière de la nuit
    prolongerait le sommeil de cinq minutes après le réveil.
    """
    profondeurs: list[float | None] = [None] * cases
    for stade in _safe(
        """
        SELECT start_time, end_time, stage FROM sleep_stage
         WHERE end_time > ? AND start_time < ? ORDER BY start_time
        """,
        (debut, fin),
    ):
        # « unmeasurable » n'est pas un stade mais une absence de mesure.
        valeur = SLEEP_DEPTH.get(stade["stage"])
        depart, arrivee = _as_utc(stade["start_time"]), _as_utc(stade["end_time"])
        if valeur is None or depart is None or arrivee is None:
            continue
        premiere = max(int((depart - minuit).total_seconds() // 60 // bucket_min), 0)
        derniere = min(int((arrivee - minuit).total_seconds() // 60 // bucket_min), cases - 1)
        for index in range(premiere, derniere + 1):
            milieu = minuit + dt.timedelta(minutes=(index + 0.5) * bucket_min)
            if depart <= milieu < arrivee:
                profondeurs[index] = valeur
    return profondeurs


def _intraday_spans(day: str, bucket_min: int, cases: int) -> list[dict[str, Any]]:
    """Nuits et séances de la journée, exprimées en cases de l'axe.

    Sur la table et non sur la vue : une sortie écartée des statistiques a
    tout de même eu lieu, et la journée doit la montrer.

    Les libellés restent bruts : leur mise en forme appartient à la page, qui
    seule sait combien de place elle a.
    """
    spans: list[dict[str, Any]] = []

    def borne(minutes: float) -> float:
        return min(max(minutes / bucket_min, 0.0), float(cases - 1))

    # Une nuit chevauche deux dates : celle où l'on s'endort et celle où l'on
    # se réveille. Les deux sessions concernées sont donc à considérer.
    for nuit in _safe(
        """
        SELECT datetime(start_time, 'localtime') AS debut,
               datetime(end_time, 'localtime')   AS fin,
               duration_s
          FROM sleep_session
         WHERE date(start_time, 'localtime') = ? OR date(end_time, 'localtime') = ?
        """,
        (day, day),
    ):
        debut = _minutes_in_day(nuit["debut"], day, 0.0)
        fin = _minutes_in_day(nuit["fin"], day, 24 * 60.0)
        if debut is None or fin is None or fin <= debut:
            continue
        spans.append({
            "kind": "sleep",
            "label": "sommeil",
            "duration_s": nuit["duration_s"],
            "start": borne(debut),
            "end": borne(fin),
        })

    for sortie in _safe(
        """
        SELECT name, sport, start_time_local, elapsed_s
          FROM activity WHERE local_date = ? ORDER BY start_time_local
        """,
        (day,),
    ):
        debut = _minutes_in_day(sortie["start_time_local"], day, None)
        if debut is None:
            continue
        fin = debut + (sortie["elapsed_s"] or 0) / 60
        spans.append({
            "kind": "activity",
            "label": sortie["name"] or sortie["sport"],
            "duration_s": sortie["elapsed_s"],
            "start": borne(debut),
            "end": borne(fin),
        })
    return spans


def _minutes_in_day(value: Any, day: str, outside: float | None) -> float | None:
    """Minutes écoulées depuis minuit, ou `outside` si l'instant est un autre jour.

    Sert à rogner une nuit sur la journée affichée : endormi la veille à 23 h,
    la barre commence à minuit et non hors du cadre.
    """
    if value is None:
        return None
    # Selon que la colonne est déclarée ou calculée, SQLite rend un datetime
    # converti ou la chaîne brute ; les deux s'écrivent pareil.
    texte = str(value)
    if texte[:10] != day:
        return outside
    try:
        return int(texte[11:13]) * 60 + int(texte[14:16])
    except (ValueError, IndexError):
        return None


def recent_activities(limit: int = 60) -> list[dict[str, Any]]:
    """Dernières sorties, sources rétablies en liste.

    SQLite ne sait pas agréger en tableau : `group_concat` rend une chaîne,
    qu'on redécoupe ici pour que l'affichage garde un contrat stable.
    """
    rows = _safe(
        """
        SELECT a.id, a.start_time_local, a.sport, a.name, a.distance_m, a.elevation_gain_m,
               a.elapsed_s, a.avg_hr, a.max_hr,
               coalesce(a.training_load, a.trimp) AS load,
               a.has_streams, a.is_race, a.excluded, a.excluded_reason,
               g.name AS gear_name,
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
               (SELECT count(*) FROM raw_api_payload) AS payloads,
               (SELECT count(*) FROM gear) AS gear
        """
    )
    # Converti en dictionnaire : la ligne brute s'itère par position, ce qui
    # surprend quiconque fait confiance à la signature.
    return dict(row[0]) if row else {}


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


def hours_since_last_sync() -> float | None:
    """Heures écoulées depuis la dernière passe réussie, toutes sources.

    None si aucune n'a jamais abouti — auquel cas il n'y a rien à ménager et
    la synchronisation peut partir.
    """
    rows = _safe(
        """
        SELECT (julianday('now') - julianday(max(last_success_at))) * 24 AS heures
          FROM sync_cursor WHERE last_success_at IS NOT NULL
        """
    )
    if not rows or rows[0]["heures"] is None:
        return None
    return float(rows[0]["heures"])


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
          JOIN activity_kept a ON a.id = z.activity_id
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
          FROM activity_kept
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
          FROM activity_kept
         WHERE local_date > date('now', 'localtime', ? || ' days')
         GROUP BY week ORDER BY week
        """,
        (-days,),
    )


@dataclass
class Fitness:
    """État de forme au sens de la charge d'entraînement."""

    ctl: float | None = None
    atl: float | None = None
    tsb: float | None = None
    acwr: float | None = None
    monotony: float | None = None
    ctl_series: list[float | None] = field(default_factory=list)
    tsb_series: list[float | None] = field(default_factory=list)


def fitness(days: int = 90) -> Fitness:
    """Charge chronique, aiguë et forme, telles que calculées en dernier."""
    rows = _safe(
        """
        SELECT day, ctl, atl, tsb, acwr, monotony
          FROM marts_daily_load
         WHERE day > date('now', 'localtime', ? || ' days')
         ORDER BY day
        """,
        (-days,),
    )
    if not rows:
        return Fitness()

    latest = rows[-1]
    return Fitness(
        ctl=latest["ctl"],
        atl=latest["atl"],
        tsb=latest["tsb"],
        acwr=latest["acwr"],
        monotony=latest["monotony"],
        ctl_series=[r["ctl"] for r in rows],
        tsb_series=[r["tsb"] for r in rows],
    )


def active_flags(days: int = 10) -> list[dict[str, Any]]:
    """Signaux récents non acquittés, du plus grave au plus récent.

    Un seul par nature : une charge trop élevée pendant plusieurs jours
    produit un signal par jour, et les empiler affichait quatre fois la même
    phrase à un chiffre près. C'est la plus récente qui vaut.
    """
    return _safe(
        """
        SELECT day, kind, severity, message
          FROM marts_flag f
         WHERE acknowledged = 0
           AND day > date('now', 'localtime', ? || ' days')
           AND day = (
                 SELECT max(g.day) FROM marts_flag g
                  WHERE g.kind = f.kind AND g.acknowledged = 0
                    AND g.day > date('now', 'localtime', ? || ' days')
               )
         ORDER BY CASE severity WHEN 'alert' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END,
                  day DESC
        """,
        (-days, -days),
    )


def activity_detail(activity_id: int) -> dict[str, Any] | None:
    """Une activité, avec son matériel et les sources qui l'ont vue."""
    rows = _safe(
        """
        SELECT a.*, g.name AS gear_name,
               (SELECT group_concat(source) FROM activity_source
                 WHERE activity_id = a.id) AS sources
          FROM activity a
          LEFT JOIN gear g ON g.id = a.gear_id
         WHERE a.id = ?
        """,
        (activity_id,),
    )
    return dict(rows[0]) if rows else None


def activity_streams(activity_id: int, points: int = 420) -> dict[str, Any]:
    """Relevés seconde par seconde d'une sortie, ramenés à `points` valeurs.

    Une sortie de deux heures compte plus de sept mille relevés, bien au-delà
    de ce qu'un graphe large de mille pixels peut montrer. On échantillonne
    donc à intervalle régulier plutôt que de tout tracer : le tracé est
    identique à l'œil et le rendu reste immédiat.
    """
    # Par `_safe` comme le reste du module : `fetch_one` laissait filer
    # l'erreur d'une base absente, alors que ce fichier promet le contraire.
    total = _safe(
        "SELECT count(*) AS n FROM activity_stream WHERE activity_id = ?",
        (activity_id,),
    )
    if not total or not total[0]["n"]:
        return {"labels": [], "track": [], "series": {}}
    total = total[0]

    pas = max(1, total["n"] // points)
    rows = _safe(
        """
        SELECT elapsed_s, hr, altitude_m, speed_mps, cadence, lat, lon
          FROM (
            SELECT elapsed_s, hr, altitude_m, speed_mps, cadence, lat, lon,
                   row_number() OVER (ORDER BY elapsed_s) AS rang
              FROM activity_stream WHERE activity_id = ?
          )
         WHERE rang % ? = 0 ORDER BY elapsed_s
        """,
        (activity_id, pas),
    )
    return {
        "labels": [_elapsed_label(r["elapsed_s"]) for r in rows],
        # Le tracé partage l'échantillonnage des courbes : un même indice y
        # désigne le même instant, ce qui suffit à les relier au survol.
        "track": [(r["lat"], r["lon"]) for r in rows],
        "series": {
            "hr": [r["hr"] for r in rows],
            "altitude_m": [r["altitude_m"] for r in rows],
            # Le mètre par seconde ne parle à personne.
            "speed_kmh": [
                r["speed_mps"] * 3.6 if r["speed_mps"] is not None else None for r in rows
            ],
            "cadence": [r["cadence"] for r in rows],
        },
    }


def _elapsed_label(seconds: float | None) -> str:
    """Temps écoulé depuis le départ, en h:mm."""
    if seconds is None:
        return "—"
    total = int(seconds)
    return f"{total // 3600}:{total % 3600 // 60:02d}"


def activity_laps(activity_id: int) -> list[dict[str, Any]]:
    """Tours d'une sortie, dans l'ordre.

    Quatre cent quatre-vingt-sept tours dorment en base sur cent dix-neuf
    sorties : c'est ce qui distingue un fractionné d'une sortie continue, et
    la fenêtre de détail n'en montrait rien.
    """
    return _safe(
        """
        SELECT lap_index, elapsed_s, moving_s, distance_m, elevation_gain_m,
               avg_hr, max_hr, avg_power_w, avg_cadence, avg_speed_mps
          FROM activity_lap WHERE activity_id = ? ORDER BY lap_index
        """,
        (activity_id,),
    )


def set_activity_excluded(activity_id: int, excluded: bool, reason: str = "") -> None:
    """Écarte une sortie des statistiques, ou l'y remet.

    Ni suppression ni correction : la sortie a eu lieu, seul son
    enregistrement est faux. La supprimer perdrait la donnée brute et la
    resynchronisation la ramènerait ; corriger une valeur inventerait un
    chiffre. Le drapeau laisse la ligne visible, avec sa raison, et la sort
    des agrégats — qui lisent tous la vue `activity_kept`.
    """
    from cairn.db import execute

    try:
        execute(
            "UPDATE activity SET excluded = ?, excluded_reason = ? WHERE id = ?",
            (1 if excluded else 0, reason.strip() or None, activity_id),
        )
    except (sqlite3.Error, OSError):
        return


def acknowledge_flag(kind: str) -> int:
    """Éteint tous les signaux d'une même nature.

    Un signal qu'on ne peut pas faire taire finit par ne plus être lu. Le
    recalcul des métriques efface les signaux non acquittés et les régénère :
    acquitter est donc le seul moyen d'en être débarrassé durablement, et
    c'est bien ainsi que la table a été conçue — l'interface manquait.

    Par nature et non par jour : la charge trop élevée produit un signal par
    jour, et il en resterait soixante-dix à éteindre un par un.
    """
    from cairn.db import execute

    try:
        return execute(
            "UPDATE marts_flag SET acknowledged = 1 WHERE kind = ? AND acknowledged = 0",
            (kind,),
        )
    except (sqlite3.Error, OSError):
        return 0


def streams_by_distance(activity_id: int, points: int = 300) -> dict[str, Any]:
    """Relevés d'une sortie rééchantillonnés le long du parcours.

    Comparer deux sorties sur le temps écoulé n'a pas de sens dès que l'une
    fut plus rapide : au bout d'une heure, l'une est au sommet et l'autre à
    mi-pente. C'est la distance parcourue qui aligne deux passages sur le
    même terrain.
    """
    bornes = _safe(
        """
        SELECT max(distance_m) AS totale, count(*) AS n
          FROM activity_stream WHERE activity_id = ? AND distance_m IS NOT NULL
        """,
        (activity_id,),
    )
    if not bornes or not bornes[0]["totale"]:
        return {"labels": [], "series": {}}

    totale = float(bornes[0]["totale"])
    pas = totale / points
    rows = _safe(
        """
        SELECT CAST(distance_m / ? AS INTEGER) AS case_,
               avg(hr) AS hr, avg(altitude_m) AS altitude_m, avg(speed_mps) AS speed_mps
          FROM activity_stream
         WHERE activity_id = ? AND distance_m IS NOT NULL
         GROUP BY case_ ORDER BY case_
        """,
        (pas, activity_id),
    )

    par_case = {int(r["case_"]): r for r in rows}
    cases = range(points + 1)
    return {
        "total_m": totale,
        "labels": [f"{i * pas / 1000:.1f}".replace(".", ",") + " km" for i in cases],
        "series": {
            "hr": [par_case[i]["hr"] if i in par_case else None for i in cases],
            "altitude_m": [
                par_case[i]["altitude_m"] if i in par_case else None for i in cases
            ],
            "speed_kmh": [
                par_case[i]["speed_mps"] * 3.6
                if i in par_case and par_case[i]["speed_mps"] is not None
                else None
                for i in cases
            ],
        },
    }


def weekly_history(weeks: int = 12) -> list[dict[str, Any]]:
    """Semaines complètes récentes, avec leurs trois cumuls.

    La semaine en cours est exclue : entamée, elle tirerait toute médiane
    vers le bas et proposerait un objectif inférieur à l'habitude.
    """
    return _safe(
        """
        SELECT strftime('%Y-%W', local_date) AS week,
               coalesce(sum(coalesce(training_load, trimp)), 0) AS load,
               coalesce(sum(elapsed_s), 0) AS duration_s,
               coalesce(sum(distance_m), 0) AS distance_m
          FROM activity_kept
         WHERE local_date < date('now', 'localtime', 'weekday 1', '-7 days')
           AND local_date >= date('now', 'localtime', 'weekday 1',
                                  '-' || (7 * (? + 1)) || ' days')
         GROUP BY week ORDER BY week
        """,
        (weeks,),
    )


def suggested_weekly_target(kind: str = "load", weeks: int = 12) -> float | None:
    """Médiane des semaines récentes, comme point de départ d'un objectif.

    Une médiane et non une moyenne : une semaine de coupure ou une sortie
    exceptionnelle ne doivent pas déplacer la cible. Et c'est une proposition,
    pas une prescription — Cairn n'a aucune idée de ce que vous préparez.
    """
    colonne = {"load": "load", "duration": "duration_s", "distance": "distance_m"}.get(kind)
    if colonne is None:
        return None

    valeurs = sorted(float(r[colonne]) for r in weekly_history(weeks) if r[colonne])
    if not valeurs:
        return None
    milieu = len(valeurs) // 2
    if len(valeurs) % 2:
        return valeurs[milieu]
    return (valeurs[milieu - 1] + valeurs[milieu]) / 2


def current_week_progress(kind: str = "load") -> dict[str, Any]:
    """Où en est la semaine en cours, du lundi à maintenant."""
    colonne = {
        "load": "coalesce(sum(coalesce(training_load, trimp)), 0)",
        "duration": "coalesce(sum(elapsed_s), 0)",
        "distance": "coalesce(sum(distance_m), 0)",
    }.get(kind)
    if colonne is None:
        return {"value": 0.0, "days_left": 0}

    rows = _safe(
        f"""
        SELECT {colonne} AS valeur
          FROM activity_kept
         WHERE local_date >= date('now', 'localtime', 'weekday 1', '-7 days')
        """  # noqa: S608 — colonne choisie dans une table fermée, jamais saisie
    )
    valeur = float(rows[0]["valeur"]) if rows else 0.0
    # Lundi vaut zéro : il reste six jours le lundi, zéro le dimanche.
    reste = _safe(
        "SELECT 6 - CAST(strftime('%w', 'now', 'localtime', '-1 day') AS INTEGER) AS n"
    )
    return {"value": valeur, "days_left": max(0, reste[0]["n"] if reste else 0)}


def comparable_activities(activity_id: int, limit: int = 40) -> list[dict[str, Any]]:
    """Sorties du même sport, les plus proches en distance d'abord.

    Comparer deux sorties n'a de sens qu'à parcours voisin : une heure de
    home-trainer et une sortie de soixante kilomètres n'ont rien à se dire.
    Le tri par écart de distance met les vraies comparaisons en tête, sans
    interdire les autres.
    """
    return _safe(
        """
        WITH reference AS (SELECT sport, distance_m FROM activity WHERE id = ?)
        SELECT a.id, a.name, a.start_time_local, a.distance_m, a.elapsed_s,
               a.avg_hr, coalesce(a.training_load, a.trimp) AS load,
               abs(coalesce(a.distance_m, 0) - coalesce(
                   (SELECT distance_m FROM reference), 0)) AS ecart
          FROM activity a
         WHERE a.id <> ?
           AND a.sport = (SELECT sport FROM reference)
           AND a.has_streams = 1
         ORDER BY ecart, a.start_time DESC
         LIMIT ?
        """,
        (activity_id, activity_id, limit),
    )


def period_totals(days: int | None = None) -> dict[str, Any]:
    """Cumuls sur une période, et l'écart avec la période précédente.

    `days` à None couvre l'année civile en cours. L'écart compte autant que le
    total : « 828 km » ne dit pas si l'on est en avance ou en retard.
    """
    if days:
        debut = f"date('now', 'localtime', '-{int(days)} days')"
        debut_precedent = f"date('now', 'localtime', '-{2 * int(days)} days')"
    else:
        debut = "date('now', 'localtime', 'start of year')"
        # Même nombre de jours écoulés, un an plus tôt : comparer une année
        # entamée à une année pleine n'apprendrait rien.
        debut_precedent = "date('now', 'localtime', 'start of year', '-1 year')"

    fin_precedente = (
        f"date('now', 'localtime', '-{int(days)} days')" if days
        else "date('now', 'localtime', '-1 year')"
    )

    rows = _safe(
        f"""
        SELECT
          (SELECT count(*) FROM activity_kept WHERE local_date >= {debut}) AS sessions,
          (SELECT coalesce(sum(distance_m), 0) FROM activity_kept
            WHERE local_date >= {debut}) AS distance_m,
          (SELECT coalesce(sum(elevation_gain_m), 0) FROM activity_kept
            WHERE local_date >= {debut}) AS elevation_m,
          (SELECT coalesce(sum(elapsed_s), 0) FROM activity_kept
            WHERE local_date >= {debut}) AS duration_s,
          (SELECT coalesce(sum(coalesce(training_load, trimp)), 0) FROM activity_kept
            WHERE local_date >= {debut}) AS load,
          (SELECT coalesce(sum(distance_m), 0) FROM activity_kept
            WHERE local_date >= {debut_precedent}
              AND local_date < {fin_precedente}) AS distance_precedente_m,
          (SELECT coalesce(sum(elapsed_s), 0) FROM activity_kept
            WHERE local_date >= {debut_precedent}
              AND local_date < {fin_precedente}) AS duration_precedente_s
        """  # noqa: S608 — bornes construites depuis un entier, jamais une saisie
    )
    return dict(rows[0]) if rows else {}


def hr_zones() -> list[dict[str, Any]]:
    """Zones de fréquence cardiaque en vigueur, avec leurs bornes.

    Cairn calcule le TRIMP de chaque sortie avec ces bornes sans jamais les
    montrer : impossible de vérifier qu'elles correspondent à la réalité.
    """
    return _safe(
        """
        SELECT z.zone_index, z.min_bpm, z.max_bpm, z.label,
               s.hr_max, s.hr_rest, s.lthr, s.valid_from, s.source
          FROM hr_zone z
          JOIN hr_zone_set s ON s.id = z.zone_set_id
         WHERE s.valid_from = (SELECT max(valid_from) FROM hr_zone_set)
         ORDER BY z.zone_index
        """
    )


def gear_wear() -> list[dict[str, Any]]:
    """Matériel suivi, avec l'usure de chaque pièce déclarée.

    Le kilométrage vient de Strava, qui le tient à jour ; l'usure se compte
    depuis la pose de la pièce, pas depuis l'achat du vélo.
    """
    return _safe(
        """
        SELECT g.id, g.name, g.kind, g.distance_m, g.retired,
               c.id AS component_id, c.kind AS component_kind, c.label,
               c.installed_at_gear_m, c.service_interval_m,
               g.distance_m - c.installed_at_gear_m AS since_install_m
          FROM gear g
          LEFT JOIN gear_component c
                 ON c.gear_id = g.id AND c.removed_on IS NULL
         ORDER BY g.retired, g.name, c.kind
        """
    )


def add_gear_component(
    gear_id: int, kind: str, label: str | None, interval_m: float | None
) -> None:
    """Déclare une pièce, posée au kilométrage actuel du matériel."""
    from cairn.db import execute

    execute(
        """
        INSERT INTO gear_component (gear_id, kind, label, installed_on,
                                    installed_at_gear_m, service_interval_m)
        SELECT id, ?, ?, date('now', 'localtime'), coalesce(distance_m, 0), ?
          FROM gear WHERE id = ?
        """,
        (kind, label, interval_m, gear_id),
    )


def replace_gear_component(component_id: int) -> None:
    """Retire une pièce et en pose une neuve, mêmes réglages.

    Changer une chaîne ne doit pas obliger à ressaisir son intervalle : on
    archive l'ancienne — l'historique d'usure garde ainsi une trace — et on
    repart du kilométrage du jour.
    """
    from cairn.db import connection

    with connection() as conn:
        ligne = conn.execute(
            "SELECT gear_id, kind, label, service_interval_m FROM gear_component "
            "WHERE id = ?",
            (component_id,),
        ).fetchone()
        if ligne is None:
            return
        conn.execute(
            "UPDATE gear_component SET removed_on = date('now', 'localtime') WHERE id = ?",
            (component_id,),
        )
        conn.execute(
            """
            INSERT INTO gear_component (gear_id, kind, label, installed_on,
                                        installed_at_gear_m, service_interval_m)
            SELECT ?, ?, ?, date('now', 'localtime'), coalesce(distance_m, 0), ?
              FROM gear WHERE id = ?
            """,
            (
                ligne["gear_id"], ligne["kind"], ligne["label"],
                ligne["service_interval_m"], ligne["gear_id"],
            ),
        )


def repeated_segments(minimum: int = 2) -> list[dict[str, Any]]:
    """Segments parcourus plusieurs fois, du plus fréquenté au moins.

    Un segment vu une seule fois ne dit rien d'une progression : c'est la
    répétition qui permet la comparaison.
    """
    return _safe(
        """
        SELECT s.strava_segment_id AS segment_id, s.name, s.distance_m,
               s.avg_grade_pct, s.climb_category,
               count(*) AS passages,
               min(e.elapsed_s) AS best_s,
               max(e.start_time) AS last_at,
               (SELECT elapsed_s FROM segment_effort
                 WHERE segment_id = s.strava_segment_id
                 ORDER BY start_time DESC LIMIT 1) AS last_s
          FROM segment_effort e
          JOIN segment s ON s.strava_segment_id = e.segment_id
         GROUP BY s.strava_segment_id
        HAVING passages >= ?
         ORDER BY passages DESC, s.name
        """,
        (minimum,),
    )


def segment_history(segment_id: int) -> list[dict[str, Any]]:
    """Tous les passages sur un segment, du plus ancien au plus récent."""
    return _safe(
        """
        SELECT e.start_time, e.elapsed_s, e.avg_hr, e.pr_rank,
               a.id AS activity_id, a.name AS activity_name
          FROM segment_effort e
          LEFT JOIN activity_kept a ON a.id = e.activity_id
         WHERE e.segment_id = ?
         ORDER BY e.start_time
        """,
        (segment_id,),
    )


def personal_records(minimum_sessions: int = 3) -> list[dict[str, Any]]:
    """Meilleures marques par discipline, avec la sortie qui les détient.

    Un record n'a de sens qu'après quelques séances : en dessous, c'est
    simplement la seule sortie du sport. Le seuil écarte ce bruit.

    La charge la plus élevée compte autant que la distance : une séance courte
    et intense est une performance, ce que le kilométrage seul ne dit pas.
    """
    return _safe(
        """
        WITH pratiques AS (
            SELECT sport, count(*) AS sessions FROM activity_kept
             GROUP BY sport HAVING sessions >= ?
        )
        SELECT p.sport, p.sessions,
               (SELECT id FROM activity_kept WHERE sport = p.sport
                 ORDER BY distance_m DESC LIMIT 1) AS longest_id,
               (SELECT max(distance_m) FROM activity_kept WHERE sport = p.sport) AS longest_m,
               (SELECT id FROM activity_kept WHERE sport = p.sport
                 ORDER BY elevation_gain_m DESC LIMIT 1) AS highest_id,
               (SELECT max(elevation_gain_m) FROM activity_kept WHERE sport = p.sport)
                 AS highest_m,
               (SELECT id FROM activity_kept WHERE sport = p.sport
                 ORDER BY elapsed_s DESC LIMIT 1) AS longest_time_id,
               (SELECT max(elapsed_s) FROM activity_kept WHERE sport = p.sport)
                 AS longest_time_s,
               (SELECT id FROM activity_kept WHERE sport = p.sport
                 ORDER BY coalesce(trimp, training_load) DESC LIMIT 1) AS hardest_id,
               (SELECT max(coalesce(trimp, training_load)) FROM activity_kept
                 WHERE sport = p.sport) AS hardest_load
          FROM pratiques p
         ORDER BY p.sessions DESC
        """,
        (minimum_sessions,),
    )


def activity_zones(activity_id: int) -> list[dict[str, Any]]:
    """Temps passé dans chaque zone de fréquence cardiaque, pour une sortie.

    La page Analyse cumule ces temps sur une période ; au niveau d'une séance,
    c'est ce qui distingue une sortie facile d'un travail au seuil, à durée
    identique.
    """
    return _safe(
        """
        SELECT zone_index, seconds FROM activity_zone_time
         WHERE activity_id = ? AND metric = 'hr'
         ORDER BY zone_index
        """,
        (activity_id,),
    )


def worn_components(threshold_pct: float = 80.0) -> list[dict[str, Any]]:
    """Pièces proches ou au-delà de leur intervalle d'entretien.

    Remonté au tableau de bord : une chaîne usée ne se découvre pas en
    consultant la page Analyse, mais le jour où elle saute.
    """
    return _safe(
        """
        SELECT g.name AS gear_name, c.kind, c.label,
               g.distance_m - c.installed_at_gear_m AS since_install_m,
               c.service_interval_m,
               100.0 * (g.distance_m - c.installed_at_gear_m)
                     / c.service_interval_m AS wear_pct
          FROM gear_component c
          JOIN gear g ON g.id = c.gear_id
         WHERE c.removed_on IS NULL
           AND c.service_interval_m > 0
           AND g.retired = 0
           AND 100.0 * (g.distance_m - c.installed_at_gear_m)
                     / c.service_interval_m >= ?
         ORDER BY wear_pct DESC
        """,
        (threshold_pct,),
    )


def export_csv(directory: str, days: int = 365) -> dict[str, Any]:
    """Écrit deux tableaux : une ligne par sortie, une ligne par jour.

    Deux fichiers plutôt qu'un : ce sont deux grains différents, et les mêler
    obligerait à répéter la journée sur chaque sortie ou l'inverse.

    Encodage `utf-8-sig` : sans la marque d'ordre des octets, Excel lit un
    fichier UTF-8 comme du latin-1 et rend « dénivelé » illisible. Le séparateur
    reste la virgule, ce qu'attendent pandas, Grafana et le reste.
    """
    import csv
    from pathlib import Path

    cible = Path(directory)
    cible.mkdir(parents=True, exist_ok=True)

    sorties = _safe(
        """
        SELECT a.local_date AS date, a.start_time_local AS depart, a.sport,
               a.sport_family AS famille, a.name AS nom,
               a.distance_m, a.elevation_gain_m, a.elapsed_s, a.moving_s,
               a.avg_hr, a.max_hr, a.avg_cadence, a.avg_power_w, a.calories,
               coalesce(a.training_load, a.trimp) AS charge,
               a.perceived_exertion AS ressenti,
               g.name AS materiel, a.primary_source AS source,
               (SELECT group_concat(source) FROM activity_source
                 WHERE activity_id = a.id) AS sources
          FROM activity_kept a
          LEFT JOIN gear g ON g.id = a.gear_id
         ORDER BY a.start_time
        """
    )

    quotidien = daily_metrics(days)
    colonnes = sorted(quotidien.series)

    fichiers = {}
    chemin = cible / "cairn-sorties.csv"
    with chemin.open("w", newline="", encoding="utf-8-sig") as flux:
        if sorties:
            plume = csv.DictWriter(flux, fieldnames=list(sorties[0].keys()))
            plume.writeheader()
            plume.writerows(dict(ligne) for ligne in sorties)
    fichiers["sorties"] = {"path": str(chemin), "rows": len(sorties)}

    chemin = cible / "cairn-journalier.csv"
    with chemin.open("w", newline="", encoding="utf-8-sig") as flux:
        plume = csv.writer(flux)
        plume.writerow(["date", *colonnes])
        for rang, jour in enumerate(quotidien.days):
            plume.writerow([jour, *(quotidien.get(c)[rang] for c in colonnes)])
    fichiers["journalier"] = {"path": str(chemin), "rows": len(quotidien.days)}

    return fichiers


def backup_database(destination: str) -> dict[str, Any]:
    """Copie cohérente du fichier de données vers `destination`.

    Passe par l'API de sauvegarde de SQLite, et non par une copie de fichier :
    la base tourne en mode WAL, où les écritures récentes vivent dans un
    journal séparé. Copier le seul `.db` donnerait une sauvegarde amputée des
    dernières synchronisations, sans que rien ne le signale.

    Une sauvegarde peut être prise pendant que l'application travaille.
    """
    import sqlite3 as sq
    from pathlib import Path

    from cairn.db import get_connection

    cible = Path(destination)
    cible.parent.mkdir(parents=True, exist_ok=True)

    copie = sq.connect(cible)
    try:
        get_connection().backup(copie)
    finally:
        copie.close()

    return {"path": str(cible), "size_bytes": cible.stat().st_size}
