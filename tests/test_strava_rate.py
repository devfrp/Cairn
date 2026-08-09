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
