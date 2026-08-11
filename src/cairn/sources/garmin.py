"""Client Garmin Connect, au-dessus de `garth`.

Garmin n'expose pas d'API publique pour un usage individuel. `garth` gère le
SSO (y compris le MFA) et renvoie un couple de tokens OAuth ; le token OAuth1
est valable environ un an, ce qui évite de conserver le mot de passe pour la
synchronisation quotidienne.

Les chemins d'API ci-dessous sont ceux du site Garmin Connect. Ils ne sont
couverts par aucun contrat : Garmin peut les déplacer sans préavis. C'est
pourquoi chaque ressource est appelée indépendamment et qu'un échec isolé
dégrade la passe en `partial` au lieu de la faire échouer entièrement.

Deux choses à savoir avant de toucher à ce fichier :

* `garth` n'est plus maintenu depuis le 27 mars 2026. Son auteur l'a
  abandonné parce que Garmin a changé son flux d'authentification ; la
  bibliothèque reste en place tant qu'elle rend service, mais personne ne la
  corrigera en amont ;
* ce changement consiste à filtrer les clients HTTP sur leur signature TLS.
  Envoyer identifiant et mot de passe depuis Python se solde donc par un 429
  avant même que Garmin les regarde. C'est pourquoi la connexion passe par une
  fenêtre de navigateur — voir `signin_url()` et `login_with_ticket()` — et
  non plus par `login_interactive()`. Seuls l'échange du ticket et les appels
  de données restent en HTTP ordinaire : ces points d'entrée-là ne sont pas
  filtrés (mesuré : 400 pour un client nu comme pour une empreinte Chrome).
"""

from __future__ import annotations

import datetime as dt
import io
import zipfile
from collections.abc import Callable
from typing import Any

import garth
import requests
import structlog
from garth import sso
from garth.exc import GarthHTTPError

from cairn.secrets import load_credential, save_credential

log = structlog.get_logger(__name__)

CREDENTIAL_SOURCE = "garmin"


class GarminError(RuntimeError):
    """Erreur dont le message est écrit pour l'utilisateur, pas pour un journal."""


class GarminAuthRequired(GarminError):
    """Aucun token utilisable : il faut relancer `cairn auth garmin`."""


class GarminRateLimited(GarminError):
    """Garmin a répondu 429 : il faut attendre, surtout pas réessayer."""


class GarminEndpointGone(RuntimeError):
    """Un endpoint a répondu 404/410 — il a probablement changé côté Garmin."""


# Adresse de connexion chargée dans la fenêtre intégrée, et service auquel le
# ticket sera rattaché. Ce dernier n'est pas une page : Garmin y redirige avec
# le ticket en paramètre, ce qui suffit à l'intercepter.
SERVICE_URL = "https://mobile.integration.{domain}/gcm/android"
SIGNIN_URL = "https://sso.{domain}/sso/signin"

# Le 429 de Garmin ne veut pas dire « trop de requêtes » : il tombe dès la
# première tentative. Le message doit écarter la fausse piste de l'attente.
RATE_LIMITED = (
    "Garmin a refusé la requête (erreur 429). Ses serveurs écartent les "
    "clients qui ne ressemblent pas à un navigateur, sans regarder ce qui est "
    "demandé. Rien n'a été enregistré, et réessayer tout de suite n'y "
    "changera rien."
)


def _status_code(exc: Exception) -> int | None:
    """Code HTTP d'une erreur, qu'elle vienne de garth ou de requests.

    garth enveloppe l'erreur de requests ; l'échange du ticket, lui, appelle
    requests directement et lève sans enveloppe.
    """
    response = getattr(getattr(exc, "error", None), "response", None)
    if response is None:
        response = getattr(exc, "response", None)
    return getattr(response, "status_code", None)


def signin_url(domain: str = "garmin.com") -> str:
    """Adresse à charger dans la fenêtre de connexion.

    Une fois l'identification faite — mot de passe, double authentification,
    éventuel captcha —, Garmin redirige vers `service` en y ajoutant un
    paramètre `ticket`. C'est ce ticket, et lui seul, que Cairn récupère.
    """
    from urllib.parse import urlencode

    params = urlencode(
        {
            "service": SERVICE_URL.format(domain=domain),
            "clientId": "GCM_ANDROID_DARK",
            "gauthHost": SIGNIN_URL.format(domain=domain),
        }
    )
    return f"{SIGNIN_URL.format(domain=domain)}?{params}"


class GarminClient:
    """Enveloppe fine autour de garth : auth persistée + appels tolérants."""

    def __init__(self, domain: str = "garmin.com") -> None:
        # garth embarque une télémétrie optionnelle (via logfire), désactivée
        # par défaut mais activable par les variables d'environnement
        # GARTH_TELEMETRY_*. On la coupe explicitement : rien de ce qui
        # transite ici ne doit pouvoir partir vers un tiers.
        self.domain = domain
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
        """Login SSO par mot de passe. Conservé, mais Garmin le refuse.

        Depuis mars 2026 ce chemin se solde par un 429 : le mot de passe n'est
        même pas examiné. Il reste en place pour les comptes hébergés sur un
        domaine qui ne filtre pas, et parce que le jour où Garmin desserre,
        rien n'est à réécrire. Préférer `login_with_ticket()`.

        `prompt_mfa` n'est appelé que si le compte a la double authentification
        activée. En contexte non interactif, passer une callable qui lève.
        """
        kwargs: dict[str, Any] = {}
        if prompt_mfa is not None:
            kwargs["prompt_mfa"] = prompt_mfa
        try:
            self._client.login(email, password, **kwargs)
        except GarthHTTPError as exc:
            if _status_code(exc) == 429:
                raise GarminRateLimited(RATE_LIMITED) from exc
            raise
        self._persist_token()
        log.info("garmin: authentification réussie", user=self.display_name)

    def login_with_ticket(self, ticket: str, service: str | None = None) -> None:
        """Termine la connexion à partir du ticket délivré au navigateur.

        Garmin remet un « service ticket » à usage unique une fois
        l'identification faite. Il suffit à obtenir les jetons OAuth : ni le
        mot de passe ni le code de double authentification ne transitent par
        Cairn, qui ne les voit jamais.

        `service` est l'adresse à laquelle Garmin a effectivement renvoyé le
        ticket. Elle doit accompagner l'échange, sans quoi il est refusé — et
        Garmin ne s'engage pas à réutiliser celle qu'on lui a demandée. On
        transmet donc ce qu'on a observé plutôt que ce qu'on espérait.
        """
        try:
            oauth1 = self._oauth1_from_ticket(ticket, service)
            self._client.oauth1_token = oauth1
            self._client.oauth2_token = sso.exchange(oauth1, self._client, login=True)
        except (GarthHTTPError, requests.HTTPError) as exc:
            if _status_code(exc) == 429:
                raise GarminRateLimited(RATE_LIMITED) from exc
            raise GarminAuthRequired(
                "Garmin a refusé le ticket de connexion. Il n'est valable que "
                "quelques secondes et ne sert qu'une fois : relancez la "
                f"connexion. ({exc})"
            ) from exc

        self._persist_token()
        log.info("garmin: authentification réussie", user=self.display_name)

    def _oauth1_from_ticket(self, ticket: str, service: str | None):
        """Échange le ticket contre un jeton OAuth1.

        Repris de `garth.sso.get_oauth1_token`, à une différence près :
        l'adresse de service y est figée, alors qu'elle doit correspondre à
        celle pour laquelle le ticket a été émis.
        """
        from urllib.parse import parse_qs

        target = service or SERVICE_URL.format(domain=self.domain)
        session = sso.GarminOAuth1Session(parent=self._client.sess)
        url = (
            f"https://connectapi.{self.domain}/oauth-service/oauth/preauthorized"
            f"?ticket={ticket}&login-url={target}&accepts-mfa-tokens=true"
        )
        response = session.get(
            url, headers=sso.OAUTH_USER_AGENT, timeout=self._client.timeout
        )
        response.raise_for_status()
        fields = {k: v[0] for k, v in parse_qs(response.text).items()}
        return sso.OAuth1Token(domain=self.domain, **fields)

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
            # Un 429 n'est pas un jeton mort : l'annoncer comme tel enverrait
            # se reconnecter, donc réessayer, donc prolonger le blocage.
            if _status_code(exc) == 429:
                raise GarminRateLimited(RATE_LIMITED) from exc
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
            if _status_code(exc) in (404, 410):
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
