import datetime as dt

from cairn.sync.dedup import dedup_key, pick_best_match


def at(hour: int, minute: int, second: int = 0) -> dt.datetime:
    return dt.datetime(2026, 8, 8, hour, minute, second, tzinfo=dt.UTC)


def candidate(cid: int, start: dt.datetime, family: str = "cycling") -> dict:
    return {"id": cid, "start_time": start, "sport_family": family}


class TestDedupKey:
    def test_buckets_par_tranche_de_cinq_minutes(self):
        assert dedup_key(at(9, 2)) == dedup_key(at(9, 4, 59))
        assert dedup_key(at(9, 4)) != dedup_key(at(9, 5))

    def test_normalise_le_fuseau(self):
        paris = dt.timezone(dt.timedelta(hours=2))
        assert dedup_key(at(9, 0)) == dedup_key(
            dt.datetime(2026, 8, 8, 11, 0, tzinfo=paris)
        )


class TestPickBestMatch:
    def test_apparie_dans_la_fenetre(self):
        match = pick_best_match([candidate(1, at(9, 0, 30))], at(9, 0), "cycling")
        assert match["id"] == 1

    def test_rejette_au_dela_de_deux_minutes(self):
        assert pick_best_match([candidate(1, at(9, 3))], at(9, 0), "cycling") is None

    def test_rejette_une_autre_famille_de_sport(self):
        candidates = [candidate(1, at(9, 0), family="running")]
        assert pick_best_match(candidates, at(9, 0), "cycling") is None

    def test_garde_le_depart_le_plus_proche(self):
        candidates = [candidate(1, at(9, 1, 30)), candidate(2, at(9, 0, 10))]
        assert pick_best_match(candidates, at(9, 0), "cycling")["id"] == 2

    def test_liste_vide(self):
        assert pick_best_match([], at(9, 0), "cycling") is None

    def test_ecart_symetrique(self):
        """Une activité Strava en retard sur Garmin s'apparie aussi bien."""
        assert pick_best_match([candidate(1, at(8, 59))], at(9, 0), "cycling") is not None
