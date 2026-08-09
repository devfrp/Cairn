"""Appariement des activités entre Garmin et Strava.

Une sortie enregistrée sur la montre remonte dans les deux services. Il n'y a
aucun identifiant commun exploitable : Strava expose bien parfois un
`external_id` dérivé du fichier Garmin, mais pas pour les activités importées
autrement, et jamais de façon garantie.

On apparie donc sur l'heure de départ. Deux activités distinctes commençant à
moins de deux minutes d'écart dans la même famille de sport n'existent pas en
pratique — sauf enregistrement en double sur deux appareils, ce qui est
justement le cas qu'on veut fusionner.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Tolérance sur l'heure de départ. Strava arrondit à la seconde, Garmin aussi,
# mais une activité importée via un fichier tiers peut dériver un peu.
MATCH_WINDOW = dt.timedelta(seconds=120)


def dedup_key(start_time: dt.datetime) -> str:
    """Clé de regroupement grossière, indexée, pour présélectionner sur 5 min.

    Ce n'est pas le critère d'appariement — juste un filtre bon marché avant
    la comparaison fine. Deux activités appariables peuvent tomber dans deux
    buckets voisins, d'où la recherche sur une fenêtre temporelle en base.
    """
    utc = start_time.astimezone(dt.UTC)
    bucket = utc.replace(second=0, microsecond=0, minute=utc.minute // 5 * 5)
    return bucket.strftime("%Y%m%dT%H%M")


def pick_best_match(
    candidates: list[dict[str, Any]],
    start_time: dt.datetime,
    sport_family: str,
) -> dict[str, Any] | None:
    """Meilleur candidat pour une activité, ou None.

    Filtre sur la famille de sport puis retient le départ le plus proche.
    La famille (cycling / running / other) est volontairement plus permissive
    que le sport : Garmin peut classer une sortie en `mountain_biking` là où
    Strava dit `Ride`.
    """
    viable = [
        c
        for c in candidates
        if c.get("sport_family") == sport_family
        and abs(c["start_time"] - start_time) <= MATCH_WINDOW
    ]
    if not viable:
        return None
    best = min(viable, key=lambda c: abs(c["start_time"] - start_time))
    if len(viable) > 1:
        log.warning(
            "dedup: plusieurs candidats, on garde le plus proche",
            start_time=start_time.isoformat(),
            candidates=[c["id"] for c in viable],
            chosen=best["id"],
        )
    return best


def find_existing_activity(
    conn,
    *,
    athlete_id: int,
    start_time: dt.datetime,
    sport_family: str,
) -> int | None:
    """Cherche en base une activité déjà connue correspondant à celle-ci."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, start_time, sport_family
              FROM activity
             WHERE athlete_id = %s
               AND start_time BETWEEN %s AND %s
            """,
            (athlete_id, start_time - MATCH_WINDOW, start_time + MATCH_WINDOW),
        )
        candidates = cur.fetchall()
    match = pick_best_match(candidates, start_time, sport_family)
    return match["id"] if match else None


def resolve_sport(conn, source: str, source_value: str | None) -> tuple[str, str]:
    """Traduit un libellé de sport via `sport_map`, avec repli sur 'other'."""
    if not source_value:
        return "other", "other"
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT canonical_sport, canonical_family
              FROM sport_map WHERE source = %s AND source_value = %s
            """,
            (source, source_value),
        )
        row = cur.fetchone()
    if row is None:
        log.warning(
            "sport inconnu, classé en 'other' — ajouter une ligne dans sport_map",
            source=source,
            value=source_value,
        )
        return "other", "other"
    return row["canonical_sport"], row["canonical_family"]
