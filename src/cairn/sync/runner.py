"""Orchestration d'une passe de synchronisation.

Les deux sources sont indépendantes : si Garmin échoue, Strava passe quand
même, et inversement. Chaque source a sa propre ligne dans `sync_run`, ce qui
rend l'historique lisible dans la page Synchronisation.
"""

from __future__ import annotations

import structlog

from cairn.sync import garmin_sync, strava_sync
from cairn.sync.state import RunStats, finish_run, start_run

log = structlog.get_logger(__name__)

SOURCES = {
    "garmin": garmin_sync.run,
    "strava": strava_sync.run,
}


def run_source(source: str, mode: str) -> tuple[str, RunStats]:
    """Exécute une source et trace le résultat. Ne lève pas."""
    run_id = start_run(source, mode)
    stats = RunStats()
    try:
        stats = SOURCES[source](mode)
    except Exception as exc:  # noqa: BLE001 — on trace et on continue
        log.error("sync en échec", source=source, mode=mode, error=str(exc), exc_info=True)
        finish_run(run_id, "failed", stats, error=f"{type(exc).__name__}: {exc}")
        return "failed", stats

    status = "partial" if stats.detail.get("failed_resources") or stats.detail.get(
        "interrupted"
    ) else "ok"
    finish_run(run_id, status, stats)
    log.info(
        "sync terminée",
        source=source,
        mode=mode,
        status=status,
        api_calls=stats.api_calls,
        fetched=stats.items_fetched,
        upserted=stats.items_upserted,
        **{k: v for k, v in stats.detail.items() if isinstance(v, (int, str))},
    )
    return status, stats


def run_all(mode: str = "incremental", sources: list[str] | None = None) -> dict[str, str]:
    targets = sources or list(SOURCES)
    results = {source: run_source(source, mode)[0] for source in targets}

    if any(status != "failed" for status in results.values()):
        # La correspondance des sports s'enrichit à l'usage. Sans rejeu, une
        # ligne ajoutée ne servait qu'aux activités à venir et l'historique
        # restait mal classé. Relecture des payloads déjà stockés, sans réseau.
        try:
            from cairn.sync.remap import remap_sports

            remap_sports()
        except Exception as exc:  # noqa: BLE001 — la synchronisation reste acquise
            log.error("reclassement des sports en échec", error=str(exc), exc_info=True)

        # Charge chronique, lignes de base et signaux se déduisent de tout
        # l'historique : ils sont recalculés dès que celui-ci a bougé.
        try:
            from cairn.metrics.compute import recompute

            recompute()
        except Exception as exc:  # noqa: BLE001 — la synchronisation reste acquise
            log.error("recalcul des métriques en échec", error=str(exc), exc_info=True)

    return results
