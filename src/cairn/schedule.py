"""Passe de nuit et résumé hebdomadaire, planifiés.

Extrait de la ligne de commande, qui en enfermait la construction dans une
fonction qui ne rend jamais la main : impossible de vérifier à quelle heure
les tâches partent réellement sans attendre qu'elles partent.

Une règle vaut pour les deux tâches : elles ne laissent rien remonter. Une
exception qui traverse un travail planifié ne réveille personne — elle finit
dans un journal que nul ne lit, et la tâche suivante part comme si de rien
n'était. On la rattrape donc ici, pour la consigner en clair.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from cairn.config import Settings

log = structlog.get_logger(__name__)

# Le résumé part après la passe de nuit, une fois la semaine consolidée.
SUMMARY_DELAY_H = 3

JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


def nightly() -> None:
    """Synchronisation incrémentale, rattrapage, puis analyse des fichiers.

    Tant que le backfill n'est pas terminé, un lot de rattrapage suit la passe
    incrémentale : l'historique se remplit nuit après nuit, sans intervention.
    """
    from cairn.db import fetch_all
    from cairn.sync.runner import run_all
    from cairn.sync.streams import backfill as parse_streams

    run_all(mode="incremental")

    pending = fetch_all("SELECT source, resource FROM sync_cursor WHERE NOT backfill_done")
    if pending:
        log.info("rattrapage en cours", ressources=[r["resource"] for r in pending])
        run_all(mode="backfill", sources=sorted({r["source"] for r in pending}))

    # Les .fit fraîchement téléchargés sont analysés dans la foulée.
    parse_streams()


def weekly_summary(settings: Settings | None = None) -> dict[str, Any]:
    """Rédige le bilan de la semaine et l'envoie, si un webhook est connu.

    Retourne ce qui s'est passé, pour que l'appelant — test ou journal —
    puisse le constater : le résultat est autrement invisible.
    """
    from cairn.config import get_settings
    from cairn.insights import discord, weekly

    settings = settings or get_settings()
    result = weekly.generate()

    if not settings.discord_webhook_url:
        log.info("résumé produit, aucun webhook configuré")
        return {"generated": True, "delivered": False, "reason": "aucun webhook"}

    try:
        discord.send(
            settings.discord_webhook_url,
            discord.format_message(
                result["text"], result["start"], result["end"], result["source"]
            ),
        )
    except discord.DiscordUnavailable as exc:
        # Un webhook révoqué ou une panne de Discord ne doit pas empêcher le
        # résumé suivant : on renonce à celui-ci et on le dit.
        log.warning("envoi Discord impossible", raison=str(exc))
        return {"generated": True, "delivered": False, "reason": str(exc)}

    discord.mark_delivered(result["start"])
    return {"generated": True, "delivered": True, "reason": ""}


def _guarded(travail: Any, nom: str) -> Any:
    """Enveloppe une tâche pour qu'un échec n'emporte pas le planificateur."""

    def executer() -> None:
        try:
            travail()
        except Exception:  # noqa: BLE001 — un travail de fond ne remonte à personne
            log.exception("tâche planifiée en échec", tache=nom)

    return executer


def build_scheduler(settings: Settings | None = None) -> Any:
    """Planificateur prêt à démarrer, tâches déclarées mais pas encore lancées."""
    from apscheduler.schedulers.background import BackgroundScheduler

    from cairn.config import get_settings

    settings = settings or get_settings()
    sched = BackgroundScheduler(timezone=settings.timezone)

    sched.add_job(
        _guarded(nightly, "passe de nuit"),
        "cron",
        hour=settings.sync_hour,
        minute=0,
        id="nightly-sync",
        # Poste éteint à l'heure dite : la passe part au réveil plutôt que
        # d'être perdue, et les exécutions manquées se fondent en une seule.
        misfire_grace_time=3600,
        coalesce=True,
    )
    sched.add_job(
        _guarded(weekly_summary, "résumé hebdomadaire"),
        "cron",
        day_of_week=settings.summary_weekday,
        hour=(settings.sync_hour + SUMMARY_DELAY_H) % 24,
        minute=0,
        id="weekly-summary",
        misfire_grace_time=7200,
        coalesce=True,
    )
    return sched


def describe(settings: Settings | None = None) -> str:
    from cairn.config import get_settings

    settings = settings or get_settings()
    return (
        f"Planificateur démarré : passe quotidienne à {settings.sync_hour}h00 "
        f"({settings.timezone}), résumé hebdomadaire le "
        f"{JOURS[settings.summary_weekday]} à "
        f"{(settings.sync_hour + SUMMARY_DELAY_H) % 24}h00."
    )
