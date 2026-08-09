"""Configuration des logs : lisible en console, JSON quand ce n'est pas un TTY."""

from __future__ import annotations

import logging
import sys

import structlog


def configure(level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())

    # Le pool psycopg journalise chaque tentative de reconnexion. Quand la
    # base est absente, ça noie le message d'erreur utile sous huit répétitions
    # du même texte. On ne garde ce détail qu'en mode debug.
    if level.upper() != "DEBUG":
        logging.getLogger("psycopg.pool").setLevel(logging.ERROR)

    renderer = (
        structlog.dev.ConsoleRenderer()
        if sys.stdout.isatty()
        else structlog.processors.JSONRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )
