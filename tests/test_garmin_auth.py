"""Authentification Garmin : ce que signifie un 429, et ce qu'on en dit.

Garmin filtre les clients HTTP sur leur signature TLS depuis mars 2026 et
répond 429 dès la première tentative. Le piège est de lire ce code comme une
limite de débit : on attend, on réessaie, et on recommence indéfiniment. Pire,
le même 429 reçu en renouvelant un jeton se faisait passer pour un jeton
révoqué — ce qui envoyait se reconnecter, donc rebloquer.
"""

from __future__ import annotations

import pytest
import requests
from garth.exc import GarthHTTPError

from cairn.sources import garmin
from cairn.sources.garmin import (
    GarminAuthRequired,
    GarminClient,
    GarminError,
    GarminRateLimited,
)


def http_error(status: int) -> GarthHTTPError:
    response = requests.Response()
    response.status_code = status
    return GarthHTTPError(msg="Error in request", error=requests.HTTPError(response=response))


# ------------------------------------------------------------ hiérarchie


def test_les_erreurs_lisibles_partagent_une_base() -> None:
    """L'interface s'en sert pour afficher le message tel quel, sans le
    préfixer du nom de la classe."""
    assert issubclass(GarminRateLimited, GarminError)
    assert issubclass(GarminAuthRequired, GarminError)
    assert issubclass(GarminError, RuntimeError)


def test_status_code_extrait_le_code() -> None:
    assert garmin._status_code(http_error(429)) == 429
    assert garmin._status_code(http_error(404)) == 404


def test_status_code_sur_erreur_sans_reponse() -> None:
    exc = GarthHTTPError(msg="rien", error=requests.HTTPError())
    assert garmin._status_code(exc) is None


# -------------------------------------------------- adresse de connexion


def test_signin_url_demande_un_ticket_pour_le_bon_service() -> None:
    """C'est le paramètre `service` qui détermine l'adresse de redirection,
    donc l'endroit où le ticket apparaîtra."""
    from urllib.parse import parse_qs, urlparse

    url = garmin.signin_url("garmin.com")
    query = parse_qs(urlparse(url).query)

    assert urlparse(url).netloc == "sso.garmin.com"
    assert query["service"] == ["https://mobile.integration.garmin.com/gcm/android"]
    assert query["clientId"] == ["GCM_ANDROID_DARK"]


def test_signin_url_suit_le_domaine() -> None:
    """Les comptes chinois vivent sur garmin.cn : tout doit suivre."""
    url = garmin.signin_url("garmin.cn")
    assert "sso.garmin.cn" in url
    assert "mobile.integration.garmin.cn" in url


# ------------------------------------------------------------ connexion


def test_429_a_la_connexion_nest_pas_une_erreur_didentifiants(monkeypatch) -> None:
    client = GarminClient()

    def refuse(*_a, **_k):
        raise http_error(429)

    monkeypatch.setattr(client._client, "login", refuse)

    with pytest.raises(GarminRateLimited) as caught:
        client.login_interactive("qui@example.com", "peu importe")

    message = str(caught.value)
    assert "navigateur" in message
    assert "réessayer" in message.lower()


def test_autre_erreur_http_remonte_telle_quelle(monkeypatch) -> None:
    """Un 401 est bien un problème d'identifiants : ne pas le déguiser."""
    client = GarminClient()
    monkeypatch.setattr(
        client._client, "login", lambda *a, **k: (_ for _ in ()).throw(http_error(401))
    )

    with pytest.raises(GarthHTTPError):
        client.login_interactive("qui@example.com", "peu importe")


# --------------------------------------------------- renouvellement du jeton


def test_429_au_renouvellement_ne_dit_pas_jeton_revoque(monkeypatch) -> None:
    """La régression à ne pas réintroduire : annoncer un jeton mort envoie se
    reconnecter, donc réessayer, donc rester bloqué."""
    monkeypatch.setattr(
        garmin, "load_credential", lambda _source: {"token": "peu importe"}
    )
    client = GarminClient()
    monkeypatch.setattr(client._client, "loads", lambda _token: None)
    monkeypatch.setattr(
        client._client,
        "refresh_oauth2",
        lambda: (_ for _ in ()).throw(http_error(429)),
    )

    with pytest.raises(GarminRateLimited) as caught:
        client.load_saved()

    assert "révoqué" not in str(caught.value)


def test_jeton_reellement_expire_le_dit(monkeypatch) -> None:
    monkeypatch.setattr(
        garmin, "load_credential", lambda _source: {"token": "peu importe"}
    )
    client = GarminClient()
    monkeypatch.setattr(client._client, "loads", lambda _token: None)
    monkeypatch.setattr(
        client._client,
        "refresh_oauth2",
        lambda: (_ for _ in ()).throw(http_error(401)),
    )

    with pytest.raises(GarminAuthRequired, match="expiré ou révoqué"):
        client.load_saved()


def test_absence_de_jeton(monkeypatch) -> None:
    monkeypatch.setattr(garmin, "load_credential", lambda _source: None)
    with pytest.raises(GarminAuthRequired, match="Aucun token"):
        GarminClient().load_saved()


# ------------------------------------------------- connexion par ticket


class FausseReponse:
    def __init__(self, status: int = 200) -> None:
        self.status_code = status
        self.text = "oauth_token=jeton&oauth_token_secret=secret"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


def session_qui_repond(status: int, journal: dict):
    """Remplace la session OAuth1 et note l'adresse appelée."""

    class Session:
        def __init__(self, *_a, **_k) -> None:
            pass

        def get(self, url: str, **_kwargs) -> FausseReponse:
            journal["url"] = url
            return FausseReponse(status)

    return Session


def test_ticket_echange_puis_persiste(monkeypatch) -> None:
    """Le chemin nominal, jusqu'à l'adresse réellement appelée."""
    journal: dict[str, str] = {}
    enregistres: list[str] = []
    monkeypatch.setattr(
        garmin.sso, "GarminOAuth1Session", session_qui_repond(200, journal)
    )
    monkeypatch.setattr(garmin.sso, "exchange", lambda o, _c, login: f"oauth2:{o}")

    client = GarminClient()
    monkeypatch.setattr(client, "_persist_token", lambda: enregistres.append("fait"))
    monkeypatch.setattr(type(client), "display_name", property(lambda _s: "Hélène"))

    client.login_with_ticket("ST-0-abc", "https://connect.garmin.com/modern")

    assert "ticket=ST-0-abc" in journal["url"]
    assert "login-url=https://connect.garmin.com/modern" in journal["url"]
    assert client._client.oauth1_token.oauth_token == "jeton"
    assert enregistres == ["fait"]


def test_service_par_defaut_dans_ladresse(monkeypatch) -> None:
    """Sans service observé, on retombe sur celui qu'on avait demandé."""
    journal: dict[str, str] = {}
    monkeypatch.setattr(
        garmin.sso, "GarminOAuth1Session", session_qui_repond(200, journal)
    )
    monkeypatch.setattr(garmin.sso, "exchange", lambda o, _c, login: "oauth2")

    client = GarminClient()
    monkeypatch.setattr(client, "_persist_token", lambda: None)
    monkeypatch.setattr(type(client), "display_name", property(lambda _s: "H"))
    client.login_with_ticket("ST-1")

    assert "login-url=https://mobile.integration.garmin.com/gcm/android" in journal["url"]


def test_ticket_perime_le_dit_clairement(monkeypatch) -> None:
    """Un ticket ne vaut que quelques secondes : le message doit orienter vers
    une nouvelle connexion, pas laisser croire à une panne."""
    monkeypatch.setattr(
        garmin.sso, "GarminOAuth1Session", session_qui_repond(401, {})
    )

    with pytest.raises(GarminAuthRequired, match="ne sert qu'une fois"):
        GarminClient().login_with_ticket("ST-0-perime")


def test_429_pendant_lechange(monkeypatch) -> None:
    """Le 429 vient de requests sans enveloppe garth : il doit être reconnu
    quand même."""
    monkeypatch.setattr(
        garmin.sso, "GarminOAuth1Session", session_qui_repond(429, {})
    )

    with pytest.raises(GarminRateLimited):
        GarminClient().login_with_ticket("ST-0-abc")


# ------------------------------------------------- extraction du ticket


@pytest.mark.parametrize(
    ("url", "attendu"),
    [
        ("https://mobile.integration.garmin.com/gcm/android?ticket=ST-0-abc", "ST-0-abc"),
        ("https://mobile.integration.garmin.com/gcm/android?a=1&ticket=ST-1&b=2", "ST-1"),
        ("https://sso.garmin.com/sso/signin?service=x", None),
        ("https://sso.garmin.com/sso/signin", None),
        ("https://mobile.integration.garmin.com/gcm/android?ticket=", None),
    ],
)
def test_extraction_du_ticket(url: str, attendu: str | None) -> None:
    from cairn.gui.garmin_login import extract_ticket

    assert extract_ticket(url) == attendu


# ------------------------------- service de redirection observé


def test_service_transmis_a_lechange(monkeypatch) -> None:
    """Garmin peut renvoyer le ticket ailleurs que là où on l'a demandé.

    L'échange exige l'adresse exacte : c'est celle observée qui doit partir,
    pas celle qu'on espérait.
    """
    vus: dict[str, str] = {}

    def faux_echange(_self, ticket, service):
        vus["ticket"] = ticket
        vus["service"] = service or "(défaut)"
        return "oauth1"

    monkeypatch.setattr(GarminClient, "_oauth1_from_ticket", faux_echange)
    monkeypatch.setattr(garmin.sso, "exchange", lambda o, _c, login: "oauth2")

    client = GarminClient()
    monkeypatch.setattr(client, "_persist_token", lambda: None)
    monkeypatch.setattr(type(client), "display_name", property(lambda _s: "Hélène"))

    client.login_with_ticket("ST-1", "https://connect.garmin.com/modern")

    assert vus == {"ticket": "ST-1", "service": "https://connect.garmin.com/modern"}


def test_service_par_defaut_si_non_observe(monkeypatch) -> None:
    vus: dict[str, object] = {}
    monkeypatch.setattr(
        GarminClient,
        "_oauth1_from_ticket",
        lambda _s, t, service: vus.setdefault("service", service) or "oauth1",
    )
    monkeypatch.setattr(garmin.sso, "exchange", lambda o, _c, login: "oauth2")

    client = GarminClient()
    monkeypatch.setattr(client, "_persist_token", lambda: None)
    monkeypatch.setattr(type(client), "display_name", property(lambda _s: "H"))
    client.login_with_ticket("ST-2")

    assert vus["service"] is None, "le défaut se décide dans l'échange, pas avant"


@pytest.mark.parametrize(
    ("url", "attendu"),
    [
        ("https://mobile.integration.garmin.com/gcm/android?ticket=ST-0",
         "https://mobile.integration.garmin.com/gcm/android"),
        ("https://connect.garmin.com/modern?ticket=ST-1&x=2",
         "https://connect.garmin.com/modern"),
    ],
)
def test_service_of(url: str, attendu: str) -> None:
    from cairn.gui.garmin_login import service_of

    assert service_of(url) == attendu
