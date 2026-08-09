"""Client Garmin Connect, au-dessus de `garth`.

Garmin n'expose pas d'API publique pour un usage individuel. `garth` gère le
SSO (y compris le MFA) et renvoie un couple de tokens OAuth ; le token OAuth1
est valable environ un an, ce qui évite de conserver le mot de passe pour la
synchronisation quotidienne.

Les chemins d'API ci-dessous sont ceux du site Garmin Connect. Ils ne sont
couverts par aucun contrat : Garmin peut les déplacer sans préavis. C'est
pourquoi chaque ressource est appelée indépendamment et qu'un échec isolé
dégrade la passe en `partial` au lieu de la faire échouer entièrement.
"""

from __future__ import annotations

import datetime as dt
import io
import zipfile
from collections.abc import Callable
from typing import Any

import garth
import structlog
from garth.exc import GarthHTTPError

from cairn.secrets import load_credential, save_credential

log = structlog.get_logger(__name__)

CREDENTIAL_SOURCE = "garmin"


class GarminAuthRequired(RuntimeError):
    """Aucun token utilisable : il faut relancer `cairn auth garmin`."""


class GarminEndpointGone(RuntimeError):
    """Un endpoint a répondu 404/410 — il a probablement changé côté Garmin."""


class GarminClient:
    """Enveloppe fine autour de garth : auth persistée + appels tolérants."""

    def __init__(self, domain: str = "garmin.com") -> None:
        # garth embarque une télémétrie optionnelle (via logfire), désactivée
        # par défaut mais activable par les variables d'environnement
        # GARTH_TELEMETRY_*. On la coupe explicitement : rien de ce qui
        # transite ici ne doit pouvoir partir vers un tiers.
        self._client = garth.Client(
            domain=domain,
            telemetry_enabled=False,
            telemetry_send_to_logfire=False,
        )
        self._display_name: str | None = None
        self.api_calls = 0

    # -- authentification ------------------------------------------------

    def login_interactive(
        self,
        email: str,
        password: str,
        prompt_mfa: Callable[[], str] | None = None,
    ) -> None:
        """Login SSO complet, puis persistance du token chiffré en base.

        `prompt_mfa` n'est appelé que si le compte a la double authentification
        activée. En contexte non interactif, passer une callable qui lève.
        """
        kwargs: dict[str, Any] = {}
        if prompt_mfa is not None:
            kwargs["prompt_mfa"] = prompt_mfa
        self._client.login(email, password, **kwargs)
        self._persist_token()
        log.info("garmin: authentification réussie", user=self.display_name)

    def load_saved(self) -> None:
        """Restaure la session depuis la base. Lève si aucun token valable."""
        cred = load_credential(CREDENTIAL_SOURCE)
        if not cred or "token" not in cred:
            raise GarminAuthRequired(
                "Aucun token Garmin en base. Lancer : cairn auth garmin"
            )
        try:
            self._client.loads(cred["token"])
        except Exception as exc:  # garth remonte des erreurs variées ici
            raise GarminAuthRequired(f"Token Garmin illisible : {exc}") from exc

        # Le token OAuth2 dure quelques heures ; garth le renouvelle à partir
        # du token OAuth1 (~1 an). On force le renouvellement au démarrage
        # pour échouer tôt plutôt qu'au milieu d'un backfill.
        try:
            self._client.refresh_oauth2()
        except GarthHTTPError as exc:
            raise GarminAuthRequired(
                f"Token Garmin expiré ou révoqué ({exc}). Relancer : cairn auth garmin"
            ) from exc
        self._persist_token()

    def _persist_token(self) -> None:
        expires_at = None
        oauth1 = getattr(self._client, "oauth1_token", None)
        raw_expiry = getattr(oauth1, "expires_at", None) if oauth1 else None
        if raw_expiry:
            expires_at = dt.datetime.fromtimestamp(raw_expiry, tz=dt.UTC)
        save_credential(CREDENTIAL_SOURCE, {"token": self._client.dumps()}, expires_at)

    @property
    def display_name(self) -> str:
        """Identifiant de profil, requis par plusieurs endpoints wellness."""
        if self._display_name is None:
            profile = self._client.profile or {}
            self._display_name = profile.get("displayName") or self._client.username
        return self._display_name

    # -- appels ----------------------------------------------------------

    def get(self, path: str, **params: Any) -> Any:
        """Appel API. Retourne None sur 404 (donnée absente ce jour-là)."""
        self.api_calls += 1
        try:
            return self._client.connectapi(path, params=params or None)
        except GarthHTTPError as exc:
            status = getattr(getattr(exc, "error", None), "response", None)
            code = getattr(status, "status_code", None)
            if code in (404, 410):
                # Deux cas indiscernables ici : pas de donnée pour cette date,
                # ou endpoint déplacé. L'appelant tranche selon la ressource.
                log.debug("garmin: 404", path=path, params=params)
                return None
            raise

    def download(self, path: str) -> bytes:
        self.api_calls += 1
        return self._client.download(path)

    # -- ressources wellness (une valeur par jour) -----------------------

    def sleep(self, day: dt.date) -> Any:
        return self.get(
            f"/wellness-service/wellness/dailySleepData/{self.display_name}",
            date=day.isoformat(),
            nonSleepBufferMinutes=60,
        )

    def hrv(self, day: dt.date) -> Any:
        return self.get(f"/hrv-service/hrv/{day.isoformat()}")

    def daily_summary(self, day: dt.date) -> Any:
        return self.get(
            f"/usersummary-service/usersummary/daily/{self.display_name}",
            calendarDate=day.isoformat(),
        )

    def stress(self, day: dt.date) -> Any:
        return self.get(f"/wellness-service/wellness/dailyStress/{day.isoformat()}")

    def body_battery(self, start: dt.date, end: dt.date) -> Any:
        return self.get(
            "/wellness-service/wellness/bodyBattery/reports/daily",
            startDate=start.isoformat(),
            endDate=end.isoformat(),
        )

    def heart_rate(self, day: dt.date) -> Any:
        return self.get(
            f"/wellness-service/wellness/dailyHeartRate/{self.display_name}",
            date=day.isoformat(),
        )

    def training_readiness(self, day: dt.date) -> Any:
        return self.get(f"/metrics-service/metrics/trainingreadiness/{day.isoformat()}")

    def training_status(self, day: dt.date) -> Any:
        return self.get(
            f"/metrics-service/metrics/trainingstatus/aggregated/{day.isoformat()}"
        )

    def max_metrics(self, start: dt.date, end: dt.date) -> Any:
        """VO2max course et vélo sur une plage de dates."""
        return self.get(
            f"/metrics-service/metrics/maxmet/daily/{start.isoformat()}/{end.isoformat()}"
        )

    def weight(self, start: dt.date, end: dt.date) -> Any:
        return self.get(
            "/weight-service/weight/dateRange",
            startDate=start.isoformat(),
            endDate=end.isoformat(),
        )

    # -- activités -------------------------------------------------------

    def activities(self, start: int = 0, limit: int = 50) -> list[dict[str, Any]]:
        """Page de la liste d'activités, de la plus récente à la plus ancienne."""
        result = self.get(
            "/activitylist-service/activities/search/activities",
            start=start,
            limit=limit,
        )
        return result or []

    def activity_detail(self, activity_id: str | int) -> Any:
        return self.get(f"/activity-service/activity/{activity_id}")

    def activity_splits(self, activity_id: str | int) -> Any:
        return self.get(f"/activity-service/activity/{activity_id}/splits")

    def activity_fit(self, activity_id: str | int) -> bytes | None:
        """Fichier .fit original. Garmin le sert dans un zip.

        Retourne None si l'activité n'a pas de fichier associé (saisie
        manuelle, par exemple).
        """
        try:
            blob = self.download(f"/download-service/files/activity/{activity_id}")
        except GarthHTTPError as exc:
            log.warning("garmin: fit indisponible", activity_id=activity_id, error=str(exc))
            return None
        if not blob:
            return None

        if blob[:2] != b"PK":  # déjà un .fit brut
            return blob
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".fit")]
            if not names:
                log.warning("garmin: zip sans .fit", activity_id=activity_id,
                            contents=zf.namelist())
                return None
            return zf.read(names[0])
