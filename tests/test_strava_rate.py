import datetime as dt

import httpx
import pytest

from cairn.sources.strava import (
    SHORT_WINDOW_RESERVE,
    RateLimit,
    seconds_until_next_quarter,
)


class TestRateLimitParsing:
    def test_lit_les_entetes(self):
        headers = httpx.Headers(
            {"x-ratelimit-limit": "100,1000", "x-ratelimit-usage": "42,300"}
        )
        rate = RateLimit.from_headers(headers)
        assert (rate.short_usage, rate.short_limit) == (42, 100)
        assert (rate.daily_usage, rate.daily_limit) == (300, 1000)

    def test_entetes_absents(self):
        assert RateLimit.from_headers(httpx.Headers({})) is None

    def test_entetes_illisibles(self):
        headers = httpx.Headers({"x-ratelimit-limit": "n/a", "x-ratelimit-usage": "n/a"})
        assert RateLimit.from_headers(headers) is None

    def test_marge_sur_la_fenetre_courte(self):
        rate = RateLimit(short_usage=100 - SHORT_WINDOW_RESERVE, short_limit=100)
        assert rate.short_remaining == 0

    def test_restant_jamais_negatif(self):
        rate = RateLimit(short_usage=500, short_limit=100, daily_usage=5000, daily_limit=1000)
        assert rate.short_remaining == 0
        assert rate.daily_remaining == 0


class TestQuarterWindow:
    @pytest.mark.parametrize(
        ("minute", "attendu"),
        [(0, 15 * 60), (7, 8 * 60), (14, 60), (15, 15 * 60), (44, 60), (59, 60)],
    )
    def test_attente_jusqu_au_prochain_quart(self, minute, attendu):
        now = dt.datetime(2026, 8, 8, 10, minute, 0, tzinfo=dt.UTC)
        assert seconds_until_next_quarter(now) == pytest.approx(attendu, abs=1)

    def test_passage_a_l_heure_suivante(self):
        now = dt.datetime(2026, 8, 8, 10, 52, 30, tzinfo=dt.UTC)
        wait = seconds_until_next_quarter(now)
        assert (now + dt.timedelta(seconds=wait)).hour == 11

    def test_toujours_strictement_positif(self):
        now = dt.datetime(2026, 8, 8, 10, 59, 59, 999999, tzinfo=dt.UTC)
        assert seconds_until_next_quarter(now) >= 1.0


class TestEnregistrementDuQuota:
    """Écriture du quota en base, à chaque réponse de Strava.

    Ce bloc est resté écrit en PostgreSQL après la migration : curseur pris
    pour un gestionnaire de contexte, marqueurs `%s`, `now()`. Rien ne l'a
    signalé pendant des mois — il ne s'exécute qu'au premier appel réel à
    l'API, et il n'y en avait jamais eu. Il faut donc que la suite l'exerce
    vraiment, pas qu'elle le relise.
    """

    @pytest.fixture
    def base(self, tmp_path, monkeypatch):
        from cairn import db, migrate
        from cairn.config import reset_settings

        monkeypatch.setenv("CAIRN_DB_PATH", str(tmp_path / "cairn.db"))
        monkeypatch.setenv("CAIRN_SECRET_KEY", "x" * 44)
        reset_settings()
        db.close_pool()
        migrate.upgrade()
        yield
        db.close_pool()
        reset_settings()

    def entetes(self, court: int, jour: int) -> httpx.Headers:
        return httpx.Headers(
            {
                "X-RateLimit-Limit": "200,2000",
                "X-RateLimit-Usage": f"{court},{jour}",
            }
        )

    def client(self):
        from cairn.sources.strava import StravaClient

        return StravaClient("id", "secret")

    def test_ecrit_les_deux_fenetres(self, base) -> None:
        from cairn.db import fetch_all

        with self.client() as c:
            c._record_rate(self.entetes(4, 40))

        lignes = {
            r["window_kind"]: (r["usage"], r["limit_value"])
            for r in fetch_all("SELECT * FROM api_quota WHERE source = 'strava'")
        }
        assert lignes == {"short": (4, 200), "daily": (40, 2000)}

    def test_reecrit_la_meme_fenetre(self, base) -> None:
        """Deux appels dans le même quart d'heure mettent à jour, sans doubler."""
        from cairn.db import fetch_all

        with self.client() as c:
            c._record_rate(self.entetes(4, 40))
            c._record_rate(self.entetes(9, 45))

        lignes = fetch_all("SELECT * FROM api_quota WHERE source = 'strava'")
        assert len(lignes) == 2
        assert {r["usage"] for r in lignes} == {9, 45}

    def test_entetes_absents_nen_ecrit_aucune(self, base) -> None:
        from cairn.db import fetch_all

        with self.client() as c:
            c._record_rate(httpx.Headers({}))

        assert fetch_all("SELECT * FROM api_quota") == []
