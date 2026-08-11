import datetime as dt

import pytest

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


class TestReclassementDesSports:
    """Une correspondance ajoutée doit valoir aussi pour le passé.

    On ne découvre qu'un libellé manque qu'en voyant des séances tomber en
    « autre ». Ajouter la ligne ne servait jusqu'ici qu'aux synchronisations
    suivantes : trente-quatre sessions de surf sont restées mal classées alors
    que la correspondance existait. Les payloads bruts sont conservés pour
    permettre ce rejeu sans retoucher aux API.
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

    def _activite(self, conn, sport, sources, famille=None):
        """Crée une activité mal classée, avec ses payloads d'origine."""
        from cairn.db import store_raw

        conn.execute("INSERT OR IGNORE INTO athlete (id, display_name) VALUES (1, 't')")
        cur = conn.execute(
            """INSERT INTO activity (athlete_id, start_time, start_time_local,
                                     local_date, sport, sport_family, elapsed_s,
                                     primary_source)
               VALUES (1, '2026-07-01T08:00:00+00:00', '2026-07-01T10:00:00',
                       '2026-07-01', ?, ?, 3600, 'garmin')""",
            (sport, famille or sport),
        )
        activite = cur.lastrowid
        for source, payload in sources.items():
            # Écrit par le vrai chemin d'archivage : le schéma exact des
            # payloads bruts n'a pas à être recopié dans un test.
            payload_id, _ = store_raw(
                conn,
                source=source,
                endpoint="activity_summary",
                resource_key=f"{source}-{activite}",
                payload=payload,
            )
            conn.execute(
                """INSERT INTO activity_source (activity_id, source, external_id,
                                                raw_payload_id)
                   VALUES (?, ?, ?, ?)""",
                (activite, source, f"{source}-{activite}", payload_id),
            )
        return activite

    def test_surf_strava_reclasse(self, base):
        from cairn.db import connection, fetch_one
        from cairn.sync.remap import remap_sports

        with connection() as conn:
            activite = self._activite(conn, "other", {"strava": {"sport_type": "Surfing"}})

        assert remap_sports()["remapped"] == 1
        row = fetch_one("SELECT sport, sport_family FROM activity WHERE id = ?", (activite,))
        assert (row["sport"], row["sport_family"]) == ("surf", "water")

    def test_garmin_tranche_quand_les_deux_savent(self, base):
        """`sport` appartient à Garmin : sa nomenclature l'emporte."""
        from cairn.db import connection, fetch_one
        from cairn.sync.remap import remap_sports

        with connection() as conn:
            activite = self._activite(
                conn, "other",
                {
                    "garmin": {"activityType": {"typeKey": "mountain_biking"}},
                    "strava": {"sport_type": "Ride"},
                },
            )

        remap_sports()
        assert fetch_one("SELECT sport FROM activity WHERE id = ?", (activite,))["sport"] == "mtb"

    def test_strava_supplee_quand_garmin_ignore(self, base):
        """Garmin renvoie « other » pour ce qu'il ne sait pas nommer.

        Sa préséance ne doit pas alors écraser une source qui, elle, sait.
        """
        from cairn.db import connection, fetch_one
        from cairn.sync.remap import remap_sports

        with connection() as conn:
            activite = self._activite(
                conn, "other",
                {
                    "garmin": {"activityType": {"typeKey": "other"}},
                    "strava": {"sport_type": "Surfing"},
                },
            )

        remap_sports()
        assert fetch_one("SELECT sport FROM activity WHERE id = ?", (activite,))["sport"] == "surf"

    def test_rejeu_sans_effet_sur_du_deja_bon(self, base):
        from cairn.db import connection
        from cairn.sync.remap import remap_sports

        with connection() as conn:
            self._activite(
                conn, "mtb",
                {"garmin": {"activityType": {"typeKey": "mountain_biking"}}},
                famille="cycling",
            )

        assert remap_sports()["remapped"] == 0
