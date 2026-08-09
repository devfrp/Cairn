"""Client Strava : OAuth2 avec renouvellement automatique et gestion du quota.

Strava applique deux limites simultanées, remontées dans les en-têtes de
chaque réponse : une fenêtre courte de 15 minutes alignée sur l'heure, et une
fenêtre quotidienne qui se remet à zéro à minuit UTC.

On ne code aucune valeur en dur : les limites réelles dépendent de
l'application et Strava les publie dans `X-RateLimit-Limit`. Épuiser la
fenêtre courte fait attendre ; épuiser la fenêtre quotidienne interrompt
proprement la passe, qui reprendra la nuit suivante là où le curseur s'est
arrêté. C'est le comportement voulu pour un backfill complet.
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from cairn.db import connection
from cairn.secrets import load_credential, save_credential

log = structlog.get_logger(__name__)

API_ROOT = "https://www.strava.com/api/v3"
TOKEN_URL = "https://www.strava.com/oauth/token"
AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
CREDENTIAL_SOURCE = "strava"

# Marge de sécurité sur la fenêtre courte : on s'arrête avant la limite pour
# laisser passer une requête manuelle sans déclencher de 429.
SHORT_WINDOW_RESERVE = 5


class StravaAuthRequired(RuntimeError):
    """Pas de token, ou refresh refusé : relancer `cairn auth strava`."""


class StravaDailyQuotaExhausted(RuntimeError):
    """Quota journalier atteint : la passe s'arrête et reprendra demain."""


@dataclass
class RateLimit:
    short_usage: int = 0
    short_limit: int = 100
    daily_usage: int = 0
    daily_limit: int = 1000

    @classmethod
    def from_headers(cls, headers: httpx.Headers) -> RateLimit | None:
        limit = headers.get("x-ratelimit-limit")
        usage = headers.get("x-ratelimit-usage")
        if not limit or not usage:
            return None
        try:
            short_limit, daily_limit = (int(x) for x in limit.split(",")[:2])
            short_usage, daily_usage = (int(x) for x in usage.split(",")[:2])
        except ValueError:
            log.warning("strava: en-têtes de quota illisibles", limit=limit, usage=usage)
            return None
        return cls(short_usage, short_limit, daily_usage, daily_limit)

    @property
    def short_remaining(self) -> int:
        return max(0, self.short_limit - SHORT_WINDOW_RESERVE - self.short_usage)

    @property
    def daily_remaining(self) -> int:
        return max(0, self.daily_limit - self.daily_usage)


def seconds_until_next_quarter(now: dt.datetime | None = None) -> float:
    """Secondes restantes avant la prochaine fenêtre de 15 min (alignée sur l'heure)."""
    now = now or dt.datetime.now(dt.UTC)
    minute = (now.minute // 15 + 1) * 15
    if minute >= 60:
        nxt = now.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
    else:
        nxt = now.replace(minute=minute, second=0, microsecond=0)
    return max(1.0, (nxt - now).total_seconds())


class StravaClient:
    def __init__(self, client_id: str, client_secret: str, sleeper=time.sleep) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self._sleep = sleeper  # injecté pour les tests
        self._http = httpx.Client(timeout=30.0)
        self._access_token: str | None = None
        self._expires_at: float = 0.0
        self.rate = RateLimit()
        self.api_calls = 0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> StravaClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- OAuth -----------------------------------------------------------

    def authorize_url(self, redirect_uri: str) -> str:
        params = httpx.QueryParams(
            client_id=self.client_id,
            redirect_uri=redirect_uri,
            response_type="code",
            approval_prompt="auto",
            # activity:read_all inclut les sorties privées ; profile:read_all
            # donne accès aux zones de FC et au matériel.
            scope="read,activity:read_all,profile:read_all",
        )
        return f"{AUTHORIZE_URL}?{params}"

    def exchange_code(self, code: str) -> dict[str, Any]:
        resp = self._http.post(
            TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "code": code,
                "grant_type": "authorization_code",
            },
        )
        resp.raise_for_status()
        token = resp.json()
        self._store(token)
        return token

    def _store(self, token: dict[str, Any]) -> None:
        self._access_token = token["access_token"]
        self._expires_at = float(token["expires_at"])
        save_credential(
            CREDENTIAL_SOURCE,
            {
                "access_token": token["access_token"],
                "refresh_token": token["refresh_token"],
                "expires_at": token["expires_at"],
                "athlete_id": (token.get("athlete") or {}).get("id"),
            },
            dt.datetime.fromtimestamp(float(token["expires_at"]), tz=dt.UTC),
        )

    def ensure_token(self) -> None:
        """Charge le token et le renouvelle s'il expire dans moins de 5 min."""
        if self._access_token and self._expires_at - time.time() > 300:
            return

        cred = load_credential(CREDENTIAL_SOURCE)
        if not cred:
            raise StravaAuthRequired("Aucun token Strava en base. Lancer : cairn auth strava")

        if float(cred.get("expires_at", 0)) - time.time() > 300:
            self._access_token = cred["access_token"]
            self._expires_at = float(cred["expires_at"])
            return

        log.info("strava: renouvellement du token")
        resp = self._http.post(
            TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": cred["refresh_token"],
                "grant_type": "refresh_token",
            },
        )
        if resp.status_code >= 400:
            raise StravaAuthRequired(
                f"Renouvellement refusé ({resp.status_code}) : {resp.text[:200]}"
            )
        self._store(resp.json())

    # -- quota -----------------------------------------------------------

    def _record_rate(self, headers: httpx.Headers) -> None:
        parsed = RateLimit.from_headers(headers)
        if parsed is None:
            return
        self.rate = parsed
        now = dt.datetime.now(dt.UTC)
        short_start = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
        daily_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        with connection() as conn, conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO api_quota (source, window_kind, window_start, usage, limit_value)
                VALUES ('strava', %s, %s, %s, %s)
                ON CONFLICT (source, window_kind, window_start) DO UPDATE
                   SET usage = EXCLUDED.usage,
                       limit_value = EXCLUDED.limit_value,
                       updated_at = now()
                """,
                [
                    ("short", short_start, parsed.short_usage, parsed.short_limit),
                    ("daily", daily_start, parsed.daily_usage, parsed.daily_limit),
                ],
            )

    def _throttle(self) -> None:
        if self.rate.daily_remaining <= 0:
            raise StravaDailyQuotaExhausted(
                f"Quota journalier Strava atteint ({self.rate.daily_usage}/"
                f"{self.rate.daily_limit}). Reprise à la prochaine passe."
            )
        if self.rate.short_remaining <= 0:
            wait = seconds_until_next_quarter()
            log.info("strava: fenêtre de 15 min épuisée, attente", seconds=round(wait))
            self._sleep(wait)
            self.rate.short_usage = 0

    # -- requêtes --------------------------------------------------------

    def get(self, path: str, **params: Any) -> Any:
        self.ensure_token()
        self._throttle()

        url = f"{API_ROOT}{path}"
        for attempt in range(3):
            self.api_calls += 1
            resp = self._http.get(
                url,
                params={k: v for k, v in params.items() if v is not None},
                headers={"Authorization": f"Bearer {self._access_token}"},
            )
            self._record_rate(resp.headers)

            if resp.status_code == 429:
                if self.rate.daily_remaining <= 0:
                    raise StravaDailyQuotaExhausted(
                        "429 avec quota journalier épuisé. Reprise à la prochaine passe."
                    )
                wait = seconds_until_next_quarter()
                log.warning("strava: 429, attente de la fenêtre suivante",
                            seconds=round(wait), attempt=attempt + 1)
                self._sleep(wait)
                continue

            if resp.status_code == 404:
                return None
            if resp.status_code in (500, 502, 503, 504):
                backoff = 2 ** attempt
                log.warning("strava: erreur serveur, retry", status=resp.status_code,
                            backoff=backoff)
                self._sleep(backoff)
                continue
            if resp.status_code == 401:
                raise StravaAuthRequired("401 : token rejeté. Lancer : cairn auth strava")

            resp.raise_for_status()
            return resp.json()

        raise RuntimeError(f"strava: échec après 3 tentatives sur {path}")

    # -- ressources ------------------------------------------------------

    def athlete(self) -> Any:
        return self.get("/athlete")

    def zones(self) -> Any:
        return self.get("/athlete/zones")

    def activities(
        self,
        *,
        after: dt.datetime | None = None,
        before: dt.datetime | None = None,
        page: int = 1,
        per_page: int = 200,
    ) -> list[dict[str, Any]]:
        result = self.get(
            "/athlete/activities",
            after=int(after.timestamp()) if after else None,
            before=int(before.timestamp()) if before else None,
            page=page,
            per_page=per_page,
        )
        return result or []

    def activity(self, activity_id: int | str) -> Any:
        return self.get(f"/activities/{activity_id}", include_all_efforts=True)

    def gear(self, gear_id: str) -> Any:
        return self.get(f"/gear/{gear_id}")
