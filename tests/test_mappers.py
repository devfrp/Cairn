import datetime as dt

from cairn.sync.mappers import (
    dig,
    map_garmin_activity,
    map_garmin_hrv,
    map_garmin_sleep,
    map_garmin_stress_readings,
    map_strava_activity,
    map_strava_gear,
    ms_to_dt,
    parse_iso,
    to_float,
    to_int,
)


class TestHelpers:
    def test_dig_descend(self):
        assert dig({"a": {"b": {"c": 3}}}, "a", "b", "c") == 3

    def test_dig_niveau_manquant(self):
        assert dig({"a": {}}, "a", "b", "c") is None
        assert dig({"a": 5}, "a", "b", default="x") == "x"

    def test_ms_to_dt(self):
        assert ms_to_dt(1_754_000_000_000) == dt.datetime.fromtimestamp(
            1_754_000_000, tz=dt.UTC
        )
        assert ms_to_dt(None) is None
        assert ms_to_dt("bruit") is None

    def test_parse_iso_variantes_garmin(self):
        attendu = dt.datetime(2026, 8, 7, 22, 10, tzinfo=dt.UTC)
        assert parse_iso("2026-08-07T22:10:00.0") == attendu
        assert parse_iso("2026-08-07 22:10:00") == attendu
        assert parse_iso("2026-08-07T22:10:00Z") == attendu

    def test_parse_iso_conserve_le_fuseau_fourni(self):
        parsed = parse_iso("2026-08-07T22:10:00+02:00")
        assert parsed.utcoffset() == dt.timedelta(hours=2)

    def test_parse_iso_invalide(self):
        assert parse_iso("pas une date") is None
        assert parse_iso(None) is None

    def test_conversions_tolerantes(self):
        assert to_int("42.7") == 43
        assert to_int(None) is None
        assert to_float("3.5") == 3.5
        assert to_float("") is None


class TestGarminActivity:
    payload = {
        "activityId": 12345,
        "activityName": "Enduro Pra Loup",
        "startTimeGMT": "2026-08-08 07:30:00",
        "startTimeLocal": "2026-08-08 09:30:00",
        "activityType": {"typeKey": "mountain_biking"},
        "duration": 7200.0,
        "movingDuration": 5400.0,
        "distance": 24500.5,
        "elevationGain": 1250.0,
        "averageHR": 148,
        "maxHR": 181,
        "activityTrainingLoad": 220.5,
        "aerobicTrainingEffect": 3.8,
    }

    def test_champs_principaux(self):
        fields = map_garmin_activity(self.payload, "mtb", "cycling")
        assert fields["sport"] == "mtb"
        assert fields["name"] == "Enduro Pra Loup"
        assert fields["elapsed_s"] == 7200
        assert fields["avg_hr"] == 148
        assert fields["training_load"] == 220.5

    def test_date_locale_naive_et_date_du_jour(self):
        fields = map_garmin_activity(self.payload, "mtb", "cycling")
        assert fields["start_time"] == dt.datetime(2026, 8, 8, 7, 30, tzinfo=dt.UTC)
        assert fields["start_time_local"].tzinfo is None
        assert fields["start_time_local"].hour == 9
        # La date de rattachement suit l'heure locale, pas l'UTC.
        assert fields["local_date"] == dt.date(2026, 8, 8)

    def test_local_date_suit_le_fuseau(self):
        """Une sortie à 1h du matin heure locale reste rattachée à ce jour-là."""
        payload = {
            **self.payload,
            "startTimeGMT": "2026-08-07 23:30:00",
            "startTimeLocal": "2026-08-08 01:30:00",
        }
        fields = map_garmin_activity(payload, "mtb", "cycling")
        assert fields["local_date"] == dt.date(2026, 8, 8)

    def test_champs_absents_omis(self):
        fields = map_garmin_activity(
            {"activityId": 1, "startTimeGMT": "2026-08-08 07:30:00"}, "mtb", "cycling"
        )
        assert "avg_power_w" not in fields
        assert "training_load" not in fields

    def test_payload_vide_ne_leve_pas(self):
        assert map_garmin_activity({}, "other", "other") == {"sport": "other",
                                                            "sport_family": "other",
                                                            "is_indoor": False}


class TestGarminSleep:
    payload = {
        "dailySleepDTO": {
            "sleepTimeSeconds": 27000,
            "deepSleepSeconds": 5400,
            "lightSleepSeconds": 16200,
            "remSleepSeconds": 4500,
            "awakeSleepSeconds": 900,
            "sleepStartTimestampGMT": 1_754_000_000_000,
            "sleepEndTimestampGMT": 1_754_027_000_000,
            "sleepScores": {"overall": {"value": 82, "qualifierKey": "GOOD"}},
            "averageRespirationValue": 14.2,
            "awakeCount": 2,
        },
        "sleepHeartRate": [{"value": 52}, {"value": 48}, {"value": 56}],
        "sleepLevels": [
            {"startGMT": "2026-08-07T22:00:00.0", "endGMT": "2026-08-07T23:00:00.0",
             "activityLevel": 1},
            {"startGMT": "2026-08-07T23:00:00.0", "endGMT": "2026-08-08T00:30:00.0",
             "activityLevel": 0},
        ],
        "restlessMomentsCount": 7,
    }

    def test_valeurs(self):
        values, _ = map_garmin_sleep(self.payload)
        assert values["duration_s"] == 27000
        assert values["score"] == 82
        assert values["score_quality"] == "GOOD"
        assert values["restless_moments"] == 7

    def test_fc_agregee_depuis_la_serie(self):
        values, _ = map_garmin_sleep(self.payload)
        assert values["avg_hr"] == 52
        assert values["lowest_hr"] == 48

    def test_stades(self):
        _, stages = map_garmin_sleep(self.payload)
        assert [s["stage"] for s in stages] == ["light", "deep"]
        assert stages[0]["end_time"] > stages[0]["start_time"]

    def test_stade_incoherent_ignore(self):
        payload = {
            **self.payload,
            "sleepLevels": [
                {"startGMT": "2026-08-08T02:00:00.0", "endGMT": "2026-08-08T01:00:00.0",
                 "activityLevel": 1}
            ],
        }
        _, stages = map_garmin_sleep(payload)
        assert stages == []

    def test_payload_vide(self):
        values, stages = map_garmin_sleep({})
        assert values == {}
        assert stages == []


class TestGarminHrv:
    def test_valeurs(self):
        payload = {
            "hrvSummary": {
                "lastNightAvg": 68.0,
                "weeklyAvg": 71.5,
                "status": "BALANCED",
                "baseline": {"balancedLow": 62.0, "balancedUpper": 80.0},
            }
        }
        values = map_garmin_hrv(payload)
        assert values["avg_hrv_ms"] == 68.0
        assert values["status"] == "BALANCED"
        assert values["baseline_balanced_high"] == 80.0

    def test_payload_vide(self):
        assert map_garmin_hrv({}) == {}


class TestGarminStress:
    def test_ignore_les_valeurs_negatives(self):
        """Garmin encode l'absence de mesure par des valeurs négatives."""
        payload = {
            "stressValuesArray": [
                [1_754_000_000_000, 25],
                [1_754_000_180_000, -1],
                [1_754_000_360_000, 40],
            ]
        }
        readings = map_garmin_stress_readings(payload)
        assert [r[2] for r in readings] == [25.0, 40.0]

    def test_body_battery_troisieme_colonne(self):
        payload = {"bodyBatteryValuesArray": [[1_754_000_000_000, "CHARGED", 85]]}
        readings = map_garmin_stress_readings(payload)
        assert readings[0][1] == "body_battery"
        assert readings[0][2] == 85.0

    def test_lignes_malformees_ignorees(self):
        payload = {"stressValuesArray": [[1_754_000_000_000], "bruit", None]}
        assert map_garmin_stress_readings(payload) == []


class TestStrava:
    payload = {
        "id": 987654,
        "name": "Sortie route matinale",
        "sport_type": "Ride",
        "start_date": "2026-08-08T07:30:00Z",
        "start_date_local": "2026-08-08T09:30:00Z",
        "elapsed_time": 7200,
        "moving_time": 6800,
        "distance": 62000.0,
        "total_elevation_gain": 850.0,
        "average_heartrate": 142.0,
        "max_heartrate": 175.0,
        "gear_id": "b1234567",
        "commute": False,
        "trainer": False,
        "workout_type": 1,
    }

    def test_champs_principaux(self):
        fields = map_strava_activity(self.payload, "road", "cycling", gear_pk=3)
        assert fields["name"] == "Sortie route matinale"
        assert fields["distance_m"] == 62000.0
        assert fields["gear_id"] == 3
        assert fields["is_race"] is True

    def test_heure_locale_rendue_naive(self):
        fields = map_strava_activity(self.payload, "road", "cycling", gear_pk=None)
        assert fields["start_time_local"].tzinfo is None
        assert fields["local_date"] == dt.date(2026, 8, 8)

    def test_sans_materiel(self):
        fields = map_strava_activity(self.payload, "road", "cycling", gear_pk=None)
        assert "gear_id" not in fields

    def test_gear_velo(self):
        gear = map_strava_gear(
            {"id": "b123", "name": "Enduro", "brand_name": "Santa Cruz",
             "frame_type": 1, "primary": True, "distance": 120000}
        )
        assert gear["kind"] == "bike"
        assert gear["frame_type"] == "mtb"
        assert gear["distance_m"] == 120000

    def test_gear_chaussures(self):
        gear = map_strava_gear({"id": "g456", "name": "Speedgoat"})
        assert gear["kind"] == "shoes"
        assert gear["distance_m"] == 0
