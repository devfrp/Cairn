"""Envoi du résumé vers Discord, par webhook.

Un webhook suffit : il ne demande ni bot à héberger ni jeton à renouveler,
et il se révoque d'un clic si l'adresse fuite.
"""

from __future__ import annotations

import datetime as dt

import httpx
import structlog

log = structlog.get_logger(__name__)

# Discord refuse les messages plus longs ; on tronque proprement plutôt que
# de laisser l'API rejeter l'envoi.
MAX_CONTENT = 1900


class DiscordUnavailable(RuntimeError):
    """Aucun webhook configuré, ou Discord a refusé le message."""


def format_message(text: str, start: dt.date, end: dt.date, source: str) -> str:
    header = f"**Cairn — semaine du {start:%d/%m} au {end:%d/%m}**"
    if source != "model":
        # Dire d'où vient le texte évite de faire passer une rédaction
        # automatique de secours pour une analyse du modèle.
        header += "\n*(résumé généré sans modèle)*"

    body = text if len(text) <= MAX_CONTENT else text[: MAX_CONTENT - 1] + "…"
    return f"{header}\n\n{body}"


def send(webhook_url: str, content: str, timeout: float = 15.0) -> None:
    if not webhook_url:
        raise DiscordUnavailable(
            "Aucun webhook configuré (CAIRN_DISCORD_WEBHOOK_URL)."
        )
    try:
        response = httpx.post(
            webhook_url,
            json={"content": content, "allowed_mentions": {"parse": []}},
            timeout=timeout,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise DiscordUnavailable(f"Envoi refusé : {exc}") from exc
    log.info("résumé envoyé sur Discord")


def mark_delivered(start: dt.date) -> None:
    from cairn.db import connection

    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE llm_summary SET delivered_at = now(), delivery_channel = 'discord'
             WHERE period_kind = 'weekly' AND period_start = %s
            """,
            (start,),
        )
