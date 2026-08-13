"""Tests des métriques dérivées : formules, signaux, calcul sur base réelle."""

import datetime as dt
import math

import pytest

from cairn import db, migrate
from cairn.config import reset_settings
from cairn.metrics import formulas, signals
from cairn.metrics.compute import recompute
from cairn.sync import repo


class TestEwma:
    def test_serie_vide(self):
        assert formulas.ewma([], 42) == []

    def test_converge_vers_une_charge_constante(self):
        """Cent jours à charge constante : la moyenne rejoint cette charge."""
        result = formulas.ewma([100.0] * 300, 42)
        assert result[-1] == pytest.approx(100.0, abs=0.5)

    def test_la_charge_aigue_reagit_plus_vite(self):
        loads = [100.0] * 20
        atl = formulas.ewma(loads, formulas.ATL_DAYS)
        ctl = formulas.ewma(loads, formulas.CTL_DAYS)
        assert atl[-1] > ctl[-1]

    def test_decroit_au_repos(self):
        montee = formulas.ewma([100.0] * 42, 42)
        repos = formulas.ewma([0.0] * 14, 42, seed=montee[-1])
        assert repos[-1] < montee[-1]
        assert repos[-1] > 0  # la forme ne disparaît pas en deux semaines

    def test_amorce_reprise(self):
        assert formulas.ewma([0.0], 42, seed=50.0)[0] == pytest.approx(
            50.0 * math.exp(-1 / 42), abs=0.01
        )

    def test_constante_de_temps_invalide(self):
        with pytest.raises(ValueError, match="positive"):
            formulas.ewma([1.0], 0)


class TestBalanceAndRatio:
    def test_forme_positive_apres_repos(self):
        assert formulas.training_stress_balance(80.0, 40.0) == 40.0

    def test_forme_negative_en_surcharge(self):
        assert formulas.training_stress_balance(40.0, 80.0) == -40.0

    def test_valeurs_absentes(self):
        assert formulas.training_stress_balance(None, 40.0) is None

    def test_rapport(self):
        assert formulas.acute_chronic_ratio(60.0, 50.0) == pytest.approx(1.2)

    def test_pas_de_rapport_sur_charge_chronique_negligeable(self):
        """Au début d'un historique, diviser par presque zéro n'a aucun sens."""
        assert formulas.acute_chronic_ratio(10.0, 0.4) is None
        assert formulas.acute_chronic_ratio(10.0, 0.0) is None


class TestMonotony:
    def test_semaine_variee_score_bas(self):
        varied = formulas.monotony([200, 0, 120, 0, 300, 0, 80])
        uniform = formulas.monotony([100] * 6 + [101])
        assert varied < uniform

    def test_charge_constante_bornee(self):
        assert formulas.monotony([100.0] * 7) == 10.0

    def test_semaine_sans_charge(self):
        assert formulas.monotony([0.0] * 7) is None

    def test_serie_trop_courte(self):
        assert formulas.monotony([100.0]) is None

    def test_contrainte(self):
        loads = [100.0, 0.0, 200.0, 0.0, 150.0, 0.0, 50.0]
        value = formulas.monotony(loads)
        assert formulas.strain(loads, value) == pytest.approx(sum(loads) * value)

    def test_contrainte_sans_monotonie(self):
        assert formulas.strain([1.0], None) is None


class TestBaseline:
    def test_refuse_un_echantillon_trop_maigre(self):
        """Un écart-type sur trois nuits ne veut rien dire."""
        assert formulas.mean_and_deviation([60.0, 62.0, 58.0]) == (None, None)

    def test_moyenne_et_ecart_type(self):
        mean, deviation = formulas.mean_and_deviation([60.0] * 10 + [70.0] * 10)
        assert mean == pytest.approx(65.0)
        assert deviation == pytest.approx(5.13, abs=0.05)

    def test_ignore_les_valeurs_manquantes(self):
        mean, _ = formulas.mean_and_deviation([60.0] * 14 + [None] * 5)
        assert mean == pytest.approx(60.0)

    def test_zscore(self):
        assert formulas.zscore(55.0, 65.0, 5.0) == pytest.approx(-2.0)

    def test_zscore_sans_reference(self):
        assert formulas.zscore(55.0, None, 5.0) is None
        assert formulas.zscore(55.0, 65.0, 0.0) is None


class TestSlope:
    def test_pente_descendante(self):
        assert formulas.slope([70.0, 68.0, 66.0, 64.0]) == pytest.approx(-2.0)

    def test_pente_plate(self):
        assert formulas.slope([65.0] * 5) == pytest.approx(0.0)

    def test_trous_toleres(self):
        assert formulas.slope([70.0, None, 66.0, None, 62.0]) == pytest.approx(-2.0)

    def test_trop_peu_de_points(self):
        assert formulas.slope([70.0, 68.0]) is None


class TestConsecutiveBelow:
    def test_compte_depuis_la_fin(self):
        assert formulas.consecutive_below([70, 70, 60, 60, 60], 65) == 3

    def test_s_arrete_a_la_premiere_valeur_normale(self):
        assert formulas.consecutive_below([60, 60, 70], 65) == 0

    def test_une_nuit_sans_mesure_interrompt(self):
        """Une mesure absente n'est pas une mesure sous la normale."""
        assert formulas.consecutive_below([60, 60, None], 65) == 0

    def test_sans_seuil(self):
        assert formulas.consecutive_below([60, 60], None) == 0


class TestSignals:
    def test_conjonction_prioritaire(self):
        """Charge en hausse et récupération en baisse : une seule alerte."""
        found = signals.detect(acwr=1.8, hrv_days_below=4)
        assert [s.kind for s in found] == ["overreaching"]
        assert found[0].severity == "alert"

    def test_charge_seule(self):
        found = signals.detect(acwr=1.8)
        assert [s.kind for s in found] == ["acwr_high"]
        assert found[0].severity == "warn"

    def test_vfc_seule(self):
        found = signals.detect(hrv_days_below=3, hrv_trend=-1.5)
        assert found[0].kind == "hrv_suppressed"
        assert "par jour" in found[0].message

    def test_monotonie(self):
        found = signals.detect(monotony=2.4)
        assert any(s.kind == "monotony_high" for s in found)

    def test_fc_repos(self):
        found = signals.detect(rhr_delta=6.0)
        assert any(s.kind == "rhr_elevated" for s in found)

    def test_dette_de_sommeil(self):
        found = signals.detect(sleep_debt_s=5 * 3600)
        assert found[0].kind == "sleep_debt"
        assert found[0].severity == "info"

    def test_charge_basse_signalee_seulement_si_rien_d_autre(self):
        assert any(s.kind == "acwr_low" for s in signals.detect(acwr=0.5))
        assert not any(
            s.kind == "acwr_low" for s in signals.detect(acwr=0.5, rhr_delta=6.0)
        )

    def test_semaine_normale_sans_signal(self):
        assert signals.detect(acwr=1.0, monotony=1.2, hrv_days_below=0,
                              rhr_delta=0.0, sleep_debt_s=0) == []

    def test_tri_par_gravite(self):
        found = signals.detect(acwr=1.8, monotony=2.5, sleep_debt_s=5 * 3600)
        severities = [s.severity for s in found]
        assert severities == sorted(severities, key=lambda s: {"alert": 0, "warn": 1}.get(s, 2))


# ---------------------------------------------------------------------
# Calcul complet, sur une vraie base
# ---------------------------------------------------------------------


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_DB_PATH", str(tmp_path / "cairn.db"))
    reset_settings()
    db.close_pool()
    migrate.upgrade()
    yield
    db.close_pool()
    reset_settings()


def seed_history(days: int, load: float, hrv=None, rhr=None, end=None):
    """Historique quotidien régulier, pour un scénario reproductible."""
    end = end or dt.date(2026, 8, 9)
    with db.connection() as conn:
        athlete = repo.get_or_create_athlete(conn, display_name="Test")
        for offset in range(days):
            day = end - dt.timedelta(days=days - 1 - offset)
            start = dt.datetime.combine(day, dt.time(9), tzinfo=dt.UTC)
            value = load(offset) if callable(load) else load
            if value:
                repo.upsert_activity(
                    conn, athlete_id=athlete, source="garmin",
                    external_id=f"g{offset}", external_url=None,
                    raw_payload_id=None, existing_id=None,
                    fields={
                        "start_time": start,
                        "start_time_local": start.replace(tzinfo=None),
                        "local_date": day, "sport": "mtb",
                        "sport_family": "cycling", "elapsed_s": 3600,
                        "distance_m": 20000.0, "elevation_gain_m": 500.0,
                        "training_load": value,
                    },
                )
            if hrv is not None:
                repo.upsert_hrv(conn, athlete, day,
                                {"avg_hrv_ms": hrv(offset) if callable(hrv) else hrv})
            if rhr is not None:
                repo.upsert_daily_wellness(
                    conn, athlete, day,
                    {"resting_hr": rhr(offset) if callable(rhr) else rhr},
                )
    return athlete, end


class TestRecompute:
    def test_base_vide(self, base):
        assert recompute() == {"days": 0, "flags": 0}

    def test_remplit_la_charge_quotidienne(self, base):
        _, end = seed_history(30, 100.0)
        result = recompute(today=end)
        assert result["days"] == 30

        row = db.fetch_one(
            "SELECT ctl, atl, tsb FROM marts_daily_load WHERE day = ?", (end,)
        )
        assert row["ctl"] > 0
        assert row["atl"] > row["ctl"]      # la charge aiguë monte plus vite
        assert row["tsb"] < 0               # donc la forme est négative

    def test_jour_de_repos_present_avec_charge_nulle(self, base):
        _, end = seed_history(20, lambda i: 0.0 if i % 2 else 150.0)
        recompute(today=end)
        rows = db.fetch_all("SELECT day, load FROM marts_daily_load ORDER BY day")
        assert len(rows) == 20
        assert any(r["load"] == 0 for r in rows)

    def test_lignes_de_base_de_vfc(self, base):
        _, end = seed_history(90, 80.0, hrv=lambda i: 65.0 + (i % 5) - 2)
        recompute(today=end)
        row = db.fetch_one(
            "SELECT hrv_mean_60d, hrv_sd_60d, hrv_z FROM marts_recovery_baseline "
            "WHERE day = ?", (end,)
        )
        assert row["hrv_mean_60d"] is not None
        assert row["hrv_sd_60d"] > 0

    def test_pas_de_ligne_de_base_sans_historique(self, base):
        """Les premiers jours n'ont rien à quoi se comparer."""
        _, end = seed_history(10, 80.0, hrv=65.0)
        recompute(today=end)
        row = db.fetch_one(
            "SELECT hrv_mean_60d FROM marts_recovery_baseline ORDER BY day LIMIT 1"
        )
        assert row["hrv_mean_60d"] is None

    def test_detecte_une_surcharge(self, base):
        """Charge qui explose et VFC qui décroche : l'alerte doit sortir."""
        def load(i):
            return 400.0 if i >= 85 else 40.0

        def hrv(i):
            return 45.0 if i >= 85 else 65.0

        _, end = seed_history(92, load, hrv=hrv)
        recompute(today=end)

        rows = db.fetch_all(
            "SELECT kind, severity FROM marts_flag WHERE day = ?", (end,)
        )
        kinds = {r["kind"] for r in rows}
        assert "overreaching" in kinds or "hrv_suppressed" in kinds
        assert any(r["severity"] in ("alert", "warn") for r in rows)

    def test_semaine_paisible_sans_alerte(self, base):
        _, end = seed_history(92, lambda i: 60.0 if i % 3 else 0.0, hrv=65.0, rhr=48)
        recompute(today=end)
        rows = db.fetch_all(
            "SELECT severity FROM marts_flag WHERE day = ? AND severity = 'alert'",
            (end,),
        )
        assert rows == []

    def test_recalcul_idempotent(self, base):
        _, end = seed_history(40, 100.0, hrv=65.0)
        recompute(today=end)
        first = db.fetch_all("SELECT day, ctl, atl FROM marts_daily_load ORDER BY day")
        recompute(today=end)
        second = db.fetch_all("SELECT day, ctl, atl FROM marts_daily_load ORDER BY day")
        assert [tuple(r) for r in first] == [tuple(r) for r in second]

    def test_conserve_les_signaux_acquittes(self, base):
        _, end = seed_history(30, 100.0)
        recompute(today=end)
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO marts_flag (athlete_id, day, kind, severity, message, acknowledged)
                SELECT id, ?, 'manuel', 'info', 'noté', 1 FROM athlete LIMIT 1
                """,
                (end,),
            )
        recompute(today=end)
        assert db.fetch_one(
            "SELECT count(*) AS n FROM marts_flag WHERE kind = 'manuel'"
        )["n"] == 1

    def test_vue_quotidienne_alimentee(self, base):
        _, end = seed_history(40, 100.0, hrv=65.0, rhr=50)
        recompute(today=end)
        row = db.fetch_one("SELECT * FROM v_daily WHERE day = ?", (end,))
        assert row["ctl"] is not None
        assert row["load"] == 100.0


class TestZonesAnterieuresAuJeuConnu:
    """Zones datées d'après l'activité qu'elles doivent servir.

    Les services datent les zones du jour où ils les fournissent. À la
    première synchronisation, elles sont donc postérieures à tout
    l'historique : chercher « en vigueur ce jour-là » ne trouvait rien, et ni
    les temps par zone ni le TRIMP n'étaient calculés — CTL, ATL et TSB
    restaient à zéro sur cent activités. Le défaut ne se voit qu'avec un
    historique antérieur au premier jeu de zones, ce qu'aucun test ne
    fabriquait.
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
        with db.connection() as conn:
            conn.execute("INSERT INTO athlete (id, display_name) VALUES (1, 'test')")
            conn.execute(
                """INSERT INTO hr_zone_set (athlete_id, valid_from, hr_max, hr_rest, source)
                   VALUES (1, '2026-08-11', 190, 45, 'strava')"""
            )
        yield
        db.close_pool()
        reset_settings()

    def test_jeu_posterieur_sert_quand_meme(self, base) -> None:
        import datetime as dt

        from cairn.db import connection
        from cairn.sync.streams import zone_set_for

        with connection() as conn:
            trouve = zone_set_for(conn, 1, dt.date(2025, 10, 5))

        assert trouve is not None, "une activité d'octobre doit trouver les zones d'août"
        assert trouve["hr_max"] == 190

    def test_zones_derivees_pour_une_date_ancienne(self, base) -> None:
        import datetime as dt

        from cairn.db import connection
        from cairn.sync.streams import get_zones

        with connection() as conn:
            zones = get_zones(conn, 1, dt.date(2025, 10, 5))

        assert zones, "sans zones, aucun temps par zone n'est calculé"
        assert len(zones) == 5

    def test_aucun_jeu_du_tout(self, base) -> None:
        import datetime as dt

        from cairn.db import connection, execute
        from cairn.sync.streams import zone_set_for

        execute("DELETE FROM hr_zone_set")
        with connection() as conn:
            assert zone_set_for(conn, 1, dt.date(2026, 1, 1)) is None


class TestVitessePlausible:
    """Un décrochage GPS d'une seconde écrase l'échelle d'un graphe entier.

    Une course à pied relevée à 93 km/h existe bel et bien dans les données :
    la montre l'a enregistrée, et rien en aval ne la mettait en doute.
    """

    def test_course_a_pied_impossible_ecartee(self):
        from cairn.metrics.formulas import vitesse_plausible

        assert not vitesse_plausible("running", 93.4)

    def test_sprint_de_haut_niveau_accepte(self):
        """Le seuil n'écarte que l'impossible, jamais l'inhabituel."""
        from cairn.metrics.formulas import vitesse_plausible

        assert vitesse_plausible("running", 43.0)

    def test_descente_a_velo_acceptee(self):
        from cairn.metrics.formulas import vitesse_plausible

        assert vitesse_plausible("cycling", 95.0)

    def test_famille_sans_plafond(self):
        """Sur l'eau, une remontée en bateau produit des pointes légitimes."""
        from cairn.metrics.formulas import vitesse_plausible

        assert vitesse_plausible("water", 60.0)

    def test_absence_de_mesure(self):
        from cairn.metrics.formulas import vitesse_plausible

        assert not vitesse_plausible("running", None)

    def test_le_sql_derive_de_la_table_python(self):
        """Deux tables de seuils finiraient par diverger."""
        from cairn.gui.queries import PLAFOND_VITESSE_SQL
        from cairn.metrics.formulas import VITESSE_IMPOSSIBLE_KMH

        for famille, plafond in VITESSE_IMPOSSIBLE_KMH.items():
            assert f"WHEN '{famille}' THEN {plafond:.1f}" in PLAFOND_VITESSE_SQL
