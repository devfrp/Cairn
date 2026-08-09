import datetime as dt

import pytest

from cairn.sync.fit import SEMICIRCLE_TO_DEGREES, _semicircles, to_stream_rows
from cairn.sync.streams import (
    STREAM_COLUMNS,
    compute_trimp,
    compute_zone_time,
    zones_from_hr_max,
)

T0 = dt.datetime(2026, 8, 8, 7, 30, tzinfo=dt.UTC)


def records(*hrs: int | None, step: int = 1, start: dt.datetime = T0) -> list[dict]:
    """Série de points espacés de `step` secondes."""
    return [
        {
            "time": start + dt.timedelta(seconds=i * step),
            "hr": hr,
            "cadence": None,
            "power_w": None,
            "speed_mps": 5.0,
            "altitude_m": 1200.0,
            "distance_m": float(i * 5),
            "grade_pct": None,
            "temperature_c": 18.0,
            "lat": 44.5,
            "lon": 6.5,
        }
        for i, hr in enumerate(hrs)
    ]


class TestSemicircles:
    def test_conversion(self):
        # 2^31 semicircles = 180°, donc 2^30 = 90° et 2^29 = 45°
        assert _semicircles(2**30) == pytest.approx(90.0)
        assert _semicircles(2**29) == pytest.approx(45.0)
        assert _semicircles(-(2**29)) == pytest.approx(-45.0)

    def test_zero(self):
        assert _semicircles(0) == 0.0

    def test_hors_bornes_rejete(self):
        """Un capteur GPS sans fix produit des valeurs aberrantes."""
        assert _semicircles(2**31 * 3) is None

    def test_valeur_invalide(self):
        assert _semicircles(None) is None
        assert _semicircles("bruit") is None

    def test_facteur(self):
        assert pytest.approx(180.0 / 2147483648) == SEMICIRCLE_TO_DEGREES


class TestStreamRows:
    def test_colonnes_alignees(self):
        rows = to_stream_rows(7, records(140, 145))
        assert len(rows[0]) == len(STREAM_COLUMNS)

    def test_elapsed_depuis_le_depart(self):
        rows = to_stream_rows(7, records(140, 145, 150, step=10))
        assert [r[2] for r in rows] == [0, 10, 20]

    def test_activity_id_propage(self):
        rows = to_stream_rows(42, records(140))
        assert rows[0][0] == 42

    def test_moving_depuis_la_vitesse(self):
        recs = records(140, 145)
        recs[1]["speed_mps"] = 0.0
        rows = to_stream_rows(7, recs)
        assert rows[0][-1] is True
        assert rows[1][-1] is False

    def test_serie_vide(self):
        assert to_stream_rows(7, []) == []


class TestZonesFromHrMax:
    def test_cinq_zones_contigues(self):
        zones = zones_from_hr_max(190)
        assert len(zones) == 5
        for (_, _, high), (_, low, _) in zip(zones, zones[1:], strict=False):
            assert low == high + 1

    def test_bornes_extremes(self):
        zones = zones_from_hr_max(190)
        assert zones[0][1] == 95  # 50 % de 190
        assert zones[-1][2] == 190


class TestComputeZoneTime:
    zones = [(1, 100, 119), (2, 120, 139), (3, 140, 159), (4, 160, 179), (5, 180, 200)]

    def test_repartition(self):
        seconds = compute_zone_time(records(110, 110, 150, 150, 150), self.zones)
        assert seconds[1] == 2
        assert seconds[3] == 2

    def test_intervalles_reels_pas_supposes(self):
        """L'enregistrement intelligent espace les points de plusieurs secondes."""
        seconds = compute_zone_time(records(150, 150, 150, step=5), self.zones)
        assert seconds[3] == 10

    def test_pause_longue_ignoree(self):
        recs = records(150, 150)
        recs[1]["time"] = T0 + dt.timedelta(minutes=30)
        assert compute_zone_time(recs, self.zones) == {}

    def test_points_sans_fc_ignores(self):
        seconds = compute_zone_time(records(None, None, 150, 150), self.zones)
        assert seconds == {3: 1}

    def test_hors_zones(self):
        assert compute_zone_time(records(50, 50), self.zones) == {}

    def test_sans_zones(self):
        assert compute_zone_time(records(150, 150), []) == {}

    def test_serie_vide(self):
        assert compute_zone_time([], self.zones) == {}


class TestComputeTrimp:
    def test_croit_avec_l_intensite(self):
        facile = compute_trimp(records(*([120] * 601)), hr_rest=50, hr_max=190)
        dur = compute_trimp(records(*([175] * 601)), hr_rest=50, hr_max=190)
        assert dur > facile > 0

    def test_croit_avec_la_duree(self):
        court = compute_trimp(records(*([150] * 301)), hr_rest=50, hr_max=190)
        long = compute_trimp(records(*([150] * 601)), hr_rest=50, hr_max=190)
        assert long == pytest.approx(court * 2, rel=0.02)

    def test_coefficient_selon_le_sexe(self):
        recs = records(*([160] * 601))
        homme = compute_trimp(recs, hr_rest=50, hr_max=190, sex="M")
        femme = compute_trimp(recs, hr_rest=50, hr_max=190, sex="F")
        assert homme > femme

    def test_fc_sous_le_repos_ignoree(self):
        assert compute_trimp(records(*([45] * 601)), hr_rest=50, hr_max=190) is None

    def test_parametres_incoherents(self):
        assert compute_trimp(records(150, 150), hr_rest=190, hr_max=50) is None

    def test_sans_fc(self):
        assert compute_trimp(records(None, None, None), hr_rest=50, hr_max=190) is None

    def test_serie_vide(self):
        assert compute_trimp([], hr_rest=50, hr_max=190) is None

    def test_pic_aberrant_borne(self):
        """Un capteur qui saute à 300 bpm ne doit pas exploser la charge."""
        normal = compute_trimp(records(*([185] * 601)), hr_rest=50, hr_max=190)
        aberrant = compute_trimp(records(*([300] * 601)), hr_rest=50, hr_max=190)
        assert aberrant < normal * 3
