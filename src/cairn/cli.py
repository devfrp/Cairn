"""Interface en ligne de commande de Cairn."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from cairn.config import get_settings
from cairn.logging_setup import configure

app = typer.Typer(help="Cairn — agrégation Garmin + Strava", no_args_is_help=True)
db_app = typer.Typer(help="Gestion de la base")
auth_app = typer.Typer(help="Authentification aux sources")
raw_app = typer.Typer(help="Inspection des payloads bruts")
app.add_typer(db_app, name="db")
app.add_typer(auth_app, name="auth")
app.add_typer(raw_app, name="raw")


@app.callback()
def main(log_level: str = "INFO") -> None:
    configure(log_level)


# ---------------------------------------------------------------------
# db
# ---------------------------------------------------------------------


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Applique les migrations SQL manquantes."""
    from cairn.migrate import upgrade

    applied = upgrade()
    if applied:
        typer.echo(f"Migrations appliquées : {', '.join(applied)}")
    else:
        typer.echo("Base déjà à jour.")


@db_app.command("migrations")
def db_migrations() -> None:
    """Liste les migrations détectées, sans toucher à la base.

    Utile pour vérifier qu'un binaire empaqueté embarque bien ses fichiers
    SQL : sans eux, `db upgrade` ne ferait rien.
    """
    from cairn.migrate import _migrations_dir

    directory = _migrations_dir()
    files = sorted(directory.glob("*.sql"))
    typer.echo(f"Dossier : {directory}")
    if not files:
        typer.echo("Aucune migration trouvée.")
        raise typer.Exit(code=1)
    for path in files:
        typer.echo(f"  {path.stem}  ({path.stat().st_size} octets)")


@db_app.command("check")
def db_check() -> None:
    """Vérifie que le fichier de données est lisible et donne sa taille."""
    import sqlite3

    from cairn.db import database_path, fetch_one

    path = database_path()
    typer.echo(f"Fichier : {path}")
    typer.echo(f"Moteur  : SQLite {sqlite3.sqlite_version}")
    if path.exists():
        typer.echo(f"Taille  : {path.stat().st_size / 1e6:.1f} Mo")
    row = fetch_one("SELECT count(*) AS n FROM sqlite_master WHERE type = 'table'")
    typer.echo(f"Tables  : {row['n'] if row else 0}")


# ---------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------


@auth_app.command("garmin")
def auth_garmin() -> None:
    """Ouvre une session Garmin et enregistre le token chiffré en base.

    Garmin refuse depuis mars 2026 les connexions par mot de passe venant d'un
    programme : sur un compte ordinaire, cette commande échoue quoi qu'on
    fasse. Passer par la fenêtre de connexion de l'application, qui écrit le
    jeton dans le même fichier de données. La commande reste utile pour les
    domaines qui ne filtrent pas.
    """
    from cairn.sources.garmin import GarminClient, GarminRateLimited

    settings = get_settings()
    email = settings.garmin_email or typer.prompt("Email Garmin")
    password = settings.garmin_password or typer.prompt("Mot de passe Garmin", hide_input=True)

    client = GarminClient(domain=settings.garmin_domain)
    try:
        client.login_interactive(
            email,
            password,
            prompt_mfa=lambda: typer.prompt("Code de double authentification"),
        )
    except GarminRateLimited as exc:
        typer.echo(f"{exc}", err=True)
        typer.echo(
            "\nOuvrez l'application Cairn, page « Réglages », bouton « Se "
            "connecter à Garmin » : la connexion s'y fait dans une fenêtre de "
            "navigateur, et le jeton obtenu vaut aussi pour cette commande.",
            err=True,
        )
        raise typer.Exit(2) from exc

    typer.echo(f"Session Garmin enregistrée pour {client.display_name}.")


@auth_app.command("strava")
def auth_strava(
    code: Annotated[str | None, typer.Option(help="Code renvoyé par Strava")] = None,
) -> None:
    """Flux OAuth2 Strava.

    Sans `--code`, affiche l'URL d'autorisation à ouvrir dans un navigateur.
    Strava redirige ensuite vers l'URL de callback avec `?code=...` : relancer
    la commande avec ce code.
    """
    from cairn.sources.strava import StravaClient

    settings = get_settings()
    if not settings.strava_client_id or not settings.strava_client_secret:
        raise typer.BadParameter(
            "CAIRN_STRAVA_CLIENT_ID et CAIRN_STRAVA_CLIENT_SECRET doivent être définis."
        )

    with StravaClient(settings.strava_client_id, settings.strava_client_secret) as client:
        if code is None:
            typer.echo("Ouvrir cette URL, autoriser, puis relancer avec --code=<code> :\n")
            typer.echo(client.authorize_url(settings.strava_redirect_uri))
            return
        token = client.exchange_code(code)
        athlete = token.get("athlete") or {}
        typer.echo(
            f"Token Strava enregistré pour "
            f"{athlete.get('firstname', '')} {athlete.get('lastname', '')}".strip()
        )


# ---------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------


@app.command("sync")
def sync(
    source: Annotated[str, typer.Option(help="garmin, strava ou both")] = "both",
    mode: Annotated[str, typer.Option(help="incremental ou backfill")] = "incremental",
) -> None:
    """Lance une passe de synchronisation."""
    from cairn.sync.runner import run_all

    if mode not in ("incremental", "backfill"):
        raise typer.BadParameter("mode doit valoir 'incremental' ou 'backfill'")
    sources = None if source == "both" else [source]
    results = run_all(mode=mode, sources=sources)

    for name, status in results.items():
        typer.echo(f"{name:8s} {status}")
    if any(status == "failed" for status in results.values()):
        raise typer.Exit(code=1)


@app.command("streams")
def streams(
    limit: Annotated[int, typer.Option(help="Nombre de fichiers à traiter")] = 200,
) -> None:
    """Parse les .fit archivés pas encore traités : streams, zones, TRIMP."""
    from cairn.sync.streams import backfill

    totals = backfill(limit=limit)
    typer.echo(
        f"{totals['files']} fichiers traités, {totals['points']} points insérés"
        + (f", {totals['failed']} échecs" if totals["failed"] else "")
    )


@app.command("metrics")
def metrics() -> None:
    """Recalcule charge chronique, lignes de base et signaux de surcharge."""
    from cairn.metrics.compute import recompute

    totals = recompute()
    typer.echo(f"{totals['days']} jours recalculés, {totals['flags']} signal(aux)")

    from cairn.db import fetch_all

    rows = fetch_all(
        """
        SELECT day, severity, message FROM marts_flag
         WHERE day > date('now', 'localtime', '-14 days')
         ORDER BY day DESC, severity
        """
    )
    for row in rows:
        typer.echo(f"  {row['day']}  [{row['severity']}] {row['message']}")


@app.command("summary")
def summary(
    send: Annotated[bool, typer.Option(help="Envoyer sur Discord")] = False,
    no_model: Annotated[bool, typer.Option(help="Rédaction déterministe, sans Ollama")] = False,
    week: Annotated[
        str | None, typer.Option(help="Lundi de la semaine à résumer (AAAA-MM-JJ)")
    ] = None,
) -> None:
    """Génère le résumé de la dernière semaine complète."""
    import datetime as dt

    from cairn.insights import discord, weekly

    start = end = None
    if week:
        try:
            start = dt.date.fromisoformat(week)
        except ValueError as exc:
            raise typer.BadParameter("Format attendu : AAAA-MM-JJ") from exc
        end = start + dt.timedelta(days=6)

    result = weekly.generate(start, end, use_model=not no_model)

    typer.echo(result["text"])
    typer.echo("")
    origin = "modèle " + str(result["model"]) if result["source"] == "model" else "sans modèle"
    detail = f" · {result['latency_ms']} ms" if result["latency_ms"] else ""
    typer.echo(f"— rédigé {origin}{detail}")
    if result["rejected_numbers"]:
        typer.echo(
            "— chiffres non fondés rejetés : "
            + ", ".join(result["rejected_numbers"])
            + " (repli déterministe)"
        )
    if result.get("error"):
        typer.echo(f"— {result['error']}")

    if not send:
        return

    settings = get_settings()
    try:
        discord.send(
            settings.discord_webhook_url,
            discord.format_message(
                result["text"], result["start"], result["end"], result["source"]
            ),
        )
    except discord.DiscordUnavailable as exc:
        typer.echo(f"Envoi Discord impossible : {exc}")
        raise typer.Exit(code=1) from exc
    discord.mark_delivered(result["start"])
    typer.echo("— envoyé sur Discord")


@app.command("check-ollama")
def check_ollama() -> None:
    """Vérifie le moteur utilisé : instance configurée, ou moteur embarqué."""
    from cairn.insights import embedded
    from cairn.insights.ollama import OllamaClient, OllamaUnavailable

    settings = get_settings()
    if embedded.is_available():
        typer.echo(f"Moteur embarqué présent — modèle {embedded.bundled_model()}")

    try:
        client = OllamaClient.from_settings(settings.ollama_url, settings.ollama_model)
    except OllamaUnavailable as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc

    ok, detail = client.check()
    source = "moteur embarqué" if client.embedded else client.url
    typer.echo(f"{source} — {detail}")
    if not ok:
        raise typer.Exit(code=1)


@app.command("scheduler")
def scheduler() -> None:
    """Applique les migrations puis planifie la passe nocturne (processus long).

    Tant que le backfill n'est pas terminé, la passe de nuit enchaîne un lot
    de rattrapage après la passe incrémentale : l'historique se remplit tout
    seul, nuit après nuit, sans intervention.
    """
    import time

    from apscheduler.schedulers.background import BackgroundScheduler

    from cairn.db import fetch_all
    from cairn.migrate import upgrade
    from cairn.sync.runner import run_all
    from cairn.sync.streams import backfill as parse_streams

    settings = get_settings()
    upgrade()

    def nightly() -> None:
        run_all(mode="incremental")
        pending = fetch_all(
            "SELECT source, resource FROM sync_cursor WHERE NOT backfill_done"
        )
        if pending:
            typer.echo(f"Backfill restant sur : {', '.join(r['resource'] for r in pending)}")
            run_all(mode="backfill", sources=sorted({r["source"] for r in pending}))
        # Les .fit fraîchement téléchargés sont parsés dans la foulée.
        parse_streams()

    def weekly_summary() -> None:
        from cairn.insights import discord, weekly

        result = weekly.generate()
        if not settings.discord_webhook_url:
            typer.echo("Résumé produit, aucun webhook configuré pour l'envoyer.")
            return
        try:
            discord.send(
                settings.discord_webhook_url,
                discord.format_message(
                    result["text"], result["start"], result["end"], result["source"]
                ),
            )
        except discord.DiscordUnavailable as exc:
            typer.echo(f"Envoi Discord impossible : {exc}")
            return
        discord.mark_delivered(result["start"])

    sched = BackgroundScheduler(timezone=settings.timezone)
    sched.add_job(
        nightly,
        "cron",
        hour=settings.sync_hour,
        minute=0,
        id="nightly-sync",
        misfire_grace_time=3600,
        coalesce=True,
    )
    # Le résumé part après la passe de nuit, une fois la semaine consolidée.
    sched.add_job(
        weekly_summary,
        "cron",
        day_of_week=settings.summary_weekday,
        hour=(settings.sync_hour + 3) % 24,
        minute=0,
        id="weekly-summary",
        misfire_grace_time=7200,
        coalesce=True,
    )

    sched.start()
    jours = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
    typer.echo(
        f"Planificateur démarré : passe quotidienne à {settings.sync_hour}h00 "
        f"({settings.timezone}), résumé hebdomadaire le "
        f"{jours[settings.summary_weekday]}."
    )
    try:
        while True:
            time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        sched.shutdown()


@app.command("keygen")
def keygen() -> None:
    """Génère une clé de chiffrement pour les jetons stockés en base.

    À faire une seule fois, à la mise en place. Changer de clé ensuite rend
    illisibles les jetons Garmin et Strava déjà enregistrés.
    """
    from cryptography.fernet import Fernet

    typer.echo(Fernet.generate_key().decode())


@app.command("status")
def status() -> None:
    """Affiche l'état des curseurs et des dernières passes."""
    from cairn.db import fetch_all

    typer.echo("— Curseurs —")
    for row in fetch_all(
        """
        SELECT source, resource, last_success_at, backfill_cursor,
               backfill_done, consecutive_errors
          FROM sync_cursor ORDER BY source, resource
        """
    ):
        backfill = "terminé" if row["backfill_done"] else (
            f"jusqu'à {row['backfill_cursor']}" if row["backfill_cursor"] else "non démarré"
        )
        typer.echo(
            f"{row['source']:8s} {row['resource']:14s} "
            f"dernier succès : {row['last_success_at'] or '—'} | backfill : {backfill}"
            + (f" | erreurs : {row['consecutive_errors']}" if row["consecutive_errors"] else "")
        )

    typer.echo("\n— 10 dernières passes —")
    for row in fetch_all(
        """
        SELECT source, mode, status, started_at, finished_at, api_calls, items_upserted, error
          FROM sync_run ORDER BY started_at DESC LIMIT 10
        """
    ):
        typer.echo(
            f"{row['started_at']:%Y-%m-%d %H:%M} {row['source']:8s} {row['mode']:12s} "
            f"{row['status']:8s} {row['api_calls']:5d} appels, "
            f"{row['items_upserted']:5d} enregistrés"
            + (f" — {row['error'][:80]}" if row["error"] else "")
        )


# ---------------------------------------------------------------------
# raw
# ---------------------------------------------------------------------


@raw_app.command("show")
def raw_show(
    source: str,
    endpoint: str,
    key: Annotated[str, typer.Argument(help="Date ISO ou identifiant externe")],
) -> None:
    """Affiche le dernier payload archivé pour une ressource.

    Sert à ajuster un mapper sans refaire d'appel API.
    """
    from cairn.db import fetch_one

    row = fetch_one(
        """
        SELECT payload, fetched_at FROM raw.api_payload
         WHERE source = %s AND endpoint = %s AND resource_key = %s
         ORDER BY fetched_at DESC LIMIT 1
        """,
        (source, endpoint, key),
    )
    if row is None:
        typer.echo("Aucun payload archivé pour cette ressource.")
        raise typer.Exit(code=1)
    typer.echo(json.dumps(row["payload"], indent=2, ensure_ascii=False, default=str))


@raw_app.command("endpoints")
def raw_endpoints() -> None:
    """Liste les endpoints archivés et leur volume."""
    from cairn.db import fetch_all

    for row in fetch_all(
        """
        SELECT source, endpoint, count(*) AS n, max(fetched_at) AS last
          FROM raw.api_payload GROUP BY source, endpoint ORDER BY source, endpoint
        """
    ):
        typer.echo(
            f"{row['source']:8s} {row['endpoint']:20s} "
            f"{row['n']:6d}  {row['last']:%Y-%m-%d}"
        )


if __name__ == "__main__":
    app()
