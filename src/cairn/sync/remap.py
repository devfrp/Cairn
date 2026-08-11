"""Reclassement des activités déjà en base.

La table de correspondance des sports s'enrichit à l'usage : on ne découvre
qu'un libellé manque qu'en voyant des séances tomber en « autre ». Jusqu'ici,
ajouter une ligne ne servait qu'aux synchronisations suivantes — trente-quatre
sessions de surf restaient classées « autre » alors que la correspondance
existait.

C'est précisément à cela que servent les payloads bruts, conservés depuis le
début : la traduction se rejoue sans retoucher aux API. Aucune requête réseau
ici, seulement une relecture.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from cairn.db import connection
from cairn.sync.dedup import resolve_sport

log = structlog.get_logger(__name__)

# Colonne appartenant à Garmin (voir `repo.COLUMN_OWNER`) : quand une activité
# est vue par les deux services, c'est sa nomenclature qui tranche.
SPORT_OWNER = "garmin"


def _raw_sport(source: str, payload: Any) -> str | None:
    """Libellé de sport tel que la source le nomme, dans son payload."""
    if not isinstance(payload, dict):
        return None
    if source == "garmin":
        kind = payload.get("activityType")
        return kind.get("typeKey") if isinstance(kind, dict) else None
    if source == "strava":
        return payload.get("sport_type") or payload.get("type")
    return None


def remap_sports() -> dict[str, int]:
    """Rejoue la correspondance des sports sur tout l'historique.

    Retourne le nombre d'activités examinées et celles dont le sport change.
    L'opération est rejouable : une activité déjà bien classée n'est pas
    réécrite.
    """
    examinees = reclassees = 0

    with connection() as conn:
        lignes = conn.execute(
            """
            SELECT a.id, a.sport, a.sport_family, s.source, p.payload
              FROM activity a
              JOIN activity_source s ON s.activity_id = a.id
              LEFT JOIN raw_api_payload p ON p.id = s.raw_payload_id
             ORDER BY a.id, s.source
            """
        ).fetchall()

        propositions: dict[int, dict[str, tuple[str, str]]] = {}
        actuel: dict[int, tuple[str, str]] = {}
        for ligne in lignes:
            actuel[ligne["id"]] = (ligne["sport"], ligne["sport_family"])
            brut = ligne["payload"]
            if isinstance(brut, str | bytes):
                try:
                    brut = json.loads(brut)
                except ValueError:
                    continue
            valeur = _raw_sport(ligne["source"], brut)
            if valeur is None:
                continue
            propositions.setdefault(ligne["id"], {})[ligne["source"]] = resolve_sport(
                conn, ligne["source"], valeur
            )

        for activite, par_source in propositions.items():
            examinees += 1
            # Le propriétaire de la colonne l'emporte ; sinon la seule source
            # qui sait nommer ce sport.
            choisi = par_source.get(SPORT_OWNER)
            if choisi is None or choisi[0] == "other":
                autres = [v for k, v in par_source.items() if v[0] != "other"]
                choisi = autres[0] if autres else choisi
            if choisi is None or choisi == actuel[activite]:
                continue

            conn.execute(
                """
                UPDATE activity SET sport = ?, sport_family = ?,
                       updated_at = datetime('now')
                 WHERE id = ?
                """,
                (choisi[0], choisi[1], activite),
            )
            reclassees += 1

    if reclassees:
        log.info("sports reclassés", examinees=examinees, reclassees=reclassees)
    return {"examined": examinees, "remapped": reclassees}
