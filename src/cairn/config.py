"""Configuration centralisée, lue depuis l'environnement (préfixe CAIRN_)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CAIRN_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- base ---
    db_dsn: str = "postgresql://cairn:cairn@localhost:5432/cairn"
    # Délai avant d'abandonner l'obtention d'une connexion. L'interface
    # graphique le raccourcit : mieux vaut afficher « injoignable » vite
    # qu'attendre en silence.
    db_timeout: float = Field(default=10.0, ge=1.0, le=60.0)
    file_root: Path = Path("/data/activities")
    secret_key: str = ""

    # --- Garmin ---
    garmin_email: str = ""
    garmin_password: str = ""
    garmin_domain: str = "garmin.com"

    # --- Strava ---
    strava_client_id: str = ""
    strava_client_secret: str = ""
    strava_redirect_uri: str = "http://localhost:8000/auth/strava/callback"

    # --- sync ---
    sync_hour: int = 4
    timezone: str = "Europe/Paris"
    backfill_days_per_run: int = Field(default=60, ge=1, le=365)
    incremental_lookback_days: int = Field(default=14, ge=1, le=90)

    # --- intégrations ---
    ollama_url: str = ""
    # Le modèle ne fait que rédiger des faits déjà calculés : un petit modèle
    # suffit et répond en quelques secondes. Passer à un modèle plus gros
    # améliore surtout le style.
    ollama_model: str = "qwen3:0.6b"
    discord_webhook_url: str = ""
    # Jour d'envoi du résumé hebdomadaire (0 = lundi).
    summary_weekday: int = Field(default=0, ge=0, le=6)

    @property
    def raw_file_root(self) -> Path:
        return self.file_root / "raw"


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings() -> None:
    """Oublie la configuration mise en cache.

    L'interface graphique laisse modifier le DSN ou la clé de chiffrement en
    cours d'exécution : sans cette invalidation, les changements ne
    prendraient effet qu'au redémarrage.
    """
    get_settings.cache_clear()
