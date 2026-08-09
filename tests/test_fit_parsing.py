"""Parsing d'un vrai fichier .fit binaire, produit par l'encodeur de test."""

import datetime as dt

import pytest

from cairn.sync.fit import parse_fit
from cairn.sync.streams import compute_trimp, compute_zone_time
from tests.fit_builder import build_fit

START = dt.datetime(2026, 8, 8, 7, 30, tzinfo=dt.UTC)


@pytest.fixture
def sortie_vtt(tmp_path):
    """Vingt minutes de VTT : montée à FC élevée puis descente."""
    samples = []
    for i in range(1200):
        montee = i < 600
        samples.append(
            {
                "offset": i,
                "hr": 165 if montee else 130,
                "cadence": 70 if montee else 40,
                "altitude": 1200.0 + (i * 0.5 if montee else 300 - (i - 600) * 0.4),
                "distance": i * 4.0,
                "speed": 2.5 if montee else 7.0,
                "lat": 44.5 + i * 0.00001,
                "lon": 6.5 + i * 0.00001,
            }
        )
    path = tmp_path / "sortie.fit"
    path.write_bytes(build_fit(START, samples))
    return path


class TestParseFit:
    def test_lit_tous_les_points(self, sortie_vtt):
        activity = parse_fit(sortie_vtt)
        assert len(activity.records) == 1200

    def test_horodatage_en_utc(self, sortie_vtt):
        activity = parse_fit(sortie_vtt)
        premier = activity.records[0]["time"]
        assert premier.tzinfo is not None
        assert premier == START

    def test_coordonnees_converties_en_degres(self, sortie_vtt):
        record = parse_fit(sortie_vtt).records[0]
        assert record["lat"] == pytest.approx(44.5, abs=1e-4)
        assert record["lon"] == pytest.approx(6.5, abs=1e-4)

    def test_unites_si(self, sortie_vtt):
        """fitdecode applique les facteurs d'échelle : m, m/s, m."""
        record = parse_fit(sortie_vtt).records[10]
        assert record["altitude_m"] == pytest.approx(1205.0, abs=0.5)
        assert record["speed_mps"] == pytest.approx(2.5, abs=0.01)
        assert record["distance_m"] == pytest.approx(40.0, abs=0.1)

    def test_capteurs(self, sortie_vtt):
        record = parse_fit(sortie_vtt).records[0]
        assert record["hr"] == 165
        assert record["cadence"] == 70

    def test_session(self, sortie_vtt):
        activity = parse_fit(sortie_vtt)
        assert activity.session["start_time"] == START
        assert activity.session["total_distance"] == pytest.approx(4796.0, abs=1)

    def test_start_time_depuis_la_session(self, sortie_vtt):
        assert parse_fit(sortie_vtt).start_time == START

    def test_points_tries_chronologiquement(self, sortie_vtt):
        records = parse_fit(sortie_vtt).records
        assert all(a["time"] <= b["time"] for a, b in zip(records, records[1:], strict=False))


class TestRobustesse:
    def test_fichier_tronque_garde_ce_qui_est_lu(self, tmp_path):
        """Une montre qui s'éteint en cours de route produit un .fit coupé."""
        samples = [{"offset": i, "hr": 150} for i in range(500)]
        complet = build_fit(START, samples)
        path = tmp_path / "tronque.fit"
        path.write_bytes(complet[: len(complet) // 2])

        activity = parse_fit(path)
        assert 0 < len(activity.records) < 500

    def test_fichier_vide(self, tmp_path):
        path = tmp_path / "vide.fit"
        path.write_bytes(b"")
        assert parse_fit(path).records == []

    def test_contenu_qui_n_est_pas_du_fit(self, tmp_path):
        path = tmp_path / "faux.fit"
        path.write_bytes(b"ceci n'est pas un fichier FIT" * 20)
        assert parse_fit(path).records == []

    def test_sans_message_session(self, tmp_path):
        samples = [{"offset": i, "hr": 150} for i in range(60)]
        path = tmp_path / "sans_session.fit"
        path.write_bytes(build_fit(START, samples, with_session=False))

        activity = parse_fit(path)
        assert len(activity.records) == 60
        # Sans session, l'heure de départ vient du premier point.
        assert activity.start_time == START

    def test_fc_aberrante_ecartee(self, tmp_path):
        samples = [{"offset": 0, "hr": 250}, {"offset": 1, "hr": 150}]
        path = tmp_path / "capteur.fit"
        path.write_bytes(build_fit(START, samples))

        records = parse_fit(path).records
        assert records[0]["hr"] is None
        assert records[1]["hr"] == 150


class TestChaineComplete:
    """Du fichier binaire jusqu'aux métriques dérivées."""

    zones = [(1, 100, 119), (2, 120, 139), (3, 140, 159), (4, 160, 179), (5, 180, 200)]

    def test_zones_de_fc(self, sortie_vtt):
        records = parse_fit(sortie_vtt).records
        seconds = compute_zone_time(records, self.zones)
        # 600 s à 165 bpm en zone 4, 599 intervalles à 130 bpm en zone 2.
        assert seconds[4] == pytest.approx(600, abs=2)
        assert seconds[2] == pytest.approx(599, abs=2)
        assert sum(seconds.values()) == pytest.approx(1199, abs=2)

    def test_trimp(self, sortie_vtt):
        records = parse_fit(sortie_vtt).records
        trimp = compute_trimp(records, hr_rest=50, hr_max=190)
        assert trimp is not None
        # Vingt minutes d'effort mixte : ordre de grandeur de quelques dizaines.
        assert 20 < trimp < 120
