"""Tests d'intégration sur une vraie base.

Ce que SQLite change : le schéma, les upserts et la règle de préséance entre
sources s'exécutent réellement, dans un fichier temporaire. Avec un serveur
PostgreSQL, ces chemins n'étaient vérifiés que par relecture.
"""

import datetime as dt

import pytest

from cairn import db, migrate
from cairn.config import reset_settings
from cairn.sync import repo
from cairn.sync.dedup import find_existing_activity

START = dt.datetime(2026, 8, 8, 7, 30, tzinfo=dt.UTC)


@pytest.fixture
def base(tmp_path, monkeypatch):
    """Base migrée, vide, propre à chaque test."""
    monkeypatch.setenv("CAIRN_DB_PATH", str(tmp_path / "cairn.db"))
    reset_settings()
    db.close_pool()
    migrate.upgrade()
    yield
    db.close_pool()
    reset_settings()


@pytest.fixture
def athlete(base):
    with db.connection() as conn:
        return repo.get_or_create_athlete(conn, display_name="Test")


def activity_fields(**overrides):
    fields = {
        "start_time": START,
        "start_time_local": dt.datetime(2026, 8, 8, 9, 30),
        "local_date": dt.date(2026, 8, 8),
        "sport": "mtb",
        "sport_family": "cycling",
        "name": "Enduro Pra Loup",
        "elapsed_s": 7200,
        "distance_m": 24500.0,
        "avg_hr": 148,
        "training_load": 220.0,
        "is_indoor": False,
    }
    fields.update(overrides)
    return fields


class TestSchema:
    def test_migrations_appliquees(self, base):
        """Toutes celles du dossier, et rien d'autre.

        Comparer à une liste écrite en dur obligeait à modifier ce test à
        chaque migration ajoutée — un échec sans rapport avec un défaut.
        """
        from cairn.migrate import _migrations_dir

        attendues = sorted(p.stem for p in _migrations_dir().glob("*.sql"))
        rows = db.fetch_all("SELECT version FROM schema_migration ORDER BY version")

        assert [r["version"] for r in rows] == attendues
        assert attendues[0] == "0001_init", "la première reste le schéma initial"

    def test_relancer_est_sans_effet(self, base):
        assert migrate.upgrade() == []

    def test_correspondance_des_sports_chargee(self, base):
        row = db.fetch_one(
            "SELECT canonical_sport FROM sport_map WHERE source = ? AND source_value = ?",
            ("garmin", "mountain_biking"),
        )
        assert row["canonical_sport"] == "mtb"

    def test_vues_interrogeables(self, base):
        for view in ("v_daily", "v_activity_with_recovery", "v_gear_wear"):
            assert db.fetch_all(f"SELECT * FROM {view} LIMIT 1") == []  # noqa: S608

    def test_cles_etrangeres_actives(self, base):
        row = db.fetch_one("PRAGMA foreign_keys")
        assert row[0] == 1

    def test_journal_wal(self, base):
        row = db.fetch_one("PRAGMA journal_mode")
        assert row[0].lower() == "wal"


class TestAthlete:
    def test_creation_unique(self, base):
        with db.connection() as conn:
            first = repo.get_or_create_athlete(conn, display_name="A")
            second = repo.get_or_create_athlete(conn, display_name="B")
        assert first == second

    def test_complete_les_identifiants_manquants(self, base):
        with db.connection() as conn:
            repo.get_or_create_athlete(conn, display_name="A", garmin_user_id="g1")
            repo.get_or_create_athlete(conn, display_name="A", strava_athlete_id=42)
        row = db.fetch_one("SELECT garmin_user_id, strava_athlete_id FROM athlete")
        assert row["garmin_user_id"] == "g1"
        assert row["strava_athlete_id"] == 42


class TestActivityUpsert:
    def _insert(self, athlete, source="garmin", external_id="1", **fields):
        with db.connection() as conn:
            return repo.upsert_activity(
                conn,
                athlete_id=athlete,
                source=source,
                external_id=external_id,
                external_url=None,
                raw_payload_id=None,
                fields=activity_fields(**fields),
                existing_id=None,
            )

    def test_insertion(self, athlete):
        activity_id, created = self._insert(athlete)
        assert created is True
        row = db.fetch_one("SELECT * FROM activity WHERE id = ?", (activity_id,))
        assert row["name"] == "Enduro Pra Loup"
        assert row["avg_hr"] == 148

    def test_horodatages_relus_correctement(self, athlete):
        activity_id, _ = self._insert(athlete)
        row = db.fetch_one("SELECT * FROM activity WHERE id = ?", (activity_id,))
        assert row["start_time"] == START
        # L'heure locale reste sans fuseau : c'est l'heure lue sur la montre.
        assert row["start_time_local"].hour == 9
        assert row["start_time_local"].tzinfo is None
        assert row["local_date"] == dt.date(2026, 8, 8)

    def test_resync_identique_idempotente(self, athlete):
        activity_id, _ = self._insert(athlete)
        with db.connection() as conn:
            again, created = repo.upsert_activity(
                conn, athlete_id=athlete, source="garmin", external_id="1",
                external_url=None, raw_payload_id=None,
                fields=activity_fields(), existing_id=activity_id,
            )
        assert (again, created) == (activity_id, False)
        assert db.fetch_one("SELECT count(*) AS n FROM activity")["n"] == 1

    def test_booleen_stocke_en_entier(self, athlete):
        activity_id, _ = self._insert(athlete, is_indoor=True)
        row = db.fetch_one("SELECT is_indoor FROM activity WHERE id = ?", (activity_id,))
        assert row["is_indoor"] == 1


class TestColumnPrecedence:
    """La règle de préséance, enfin vérifiée sur une vraie base."""

    def test_strava_n_ecrase_pas_la_fc_de_garmin(self, athlete):
        with db.connection() as conn:
            activity_id, _ = repo.upsert_activity(
                conn, athlete_id=athlete, source="garmin", external_id="g1",
                external_url=None, raw_payload_id=None,
                fields=activity_fields(avg_hr=148), existing_id=None,
            )
            repo.upsert_activity(
                conn, athlete_id=athlete, source="strava", external_id="s1",
                external_url=None, raw_payload_id=None,
                fields=activity_fields(avg_hr=120, name="Sortie Strava"),
                existing_id=activity_id,
            )
        row = db.fetch_one("SELECT avg_hr, name FROM activity WHERE id = ?", (activity_id,))
        assert row["avg_hr"] == 148            # Garmin possède la FC
        assert row["name"] == "Sortie Strava"  # Strava possède le nom

    def test_garmin_n_ecrase_pas_le_materiel_de_strava(self, athlete):
        with db.connection() as conn:
            gear_id = repo.upsert_gear(conn, athlete, {
                "strava_gear_id": "b1", "kind": "bike", "name": "Nomad",
                "brand": None, "model": None, "frame_type": "mtb",
                "is_primary": True, "retired": False, "distance_m": 1000,
            })
            activity_id, _ = repo.upsert_activity(
                conn, athlete_id=athlete, source="strava", external_id="s1",
                external_url=None, raw_payload_id=None,
                fields=activity_fields(gear_id=gear_id), existing_id=None,
            )
            repo.upsert_activity(
                conn, athlete_id=athlete, source="garmin", external_id="g1",
                external_url=None, raw_payload_id=None,
                fields=activity_fields(gear_id=None), existing_id=activity_id,
            )
        row = db.fetch_one("SELECT gear_id FROM activity WHERE id = ?", (activity_id,))
        assert row["gear_id"] == gear_id

    def test_une_source_comble_les_trous_de_l_autre(self, athlete):
        with db.connection() as conn:
            activity_id, _ = repo.upsert_activity(
                conn, athlete_id=athlete, source="garmin", external_id="g1",
                external_url=None, raw_payload_id=None,
                fields=activity_fields(name=None), existing_id=None,
            )
            repo.upsert_activity(
                conn, athlete_id=athlete, source="garmin", external_id="g1",
                external_url=None, raw_payload_id=None,
                fields=activity_fields(name="Nom retrouvé"), existing_id=activity_id,
            )
        row = db.fetch_one("SELECT name FROM activity WHERE id = ?", (activity_id,))
        assert row["name"] == "Nom retrouvé"

    def test_les_deux_sources_sont_tracees(self, athlete):
        with db.connection() as conn:
            activity_id, _ = repo.upsert_activity(
                conn, athlete_id=athlete, source="garmin", external_id="g1",
                external_url=None, raw_payload_id=None,
                fields=activity_fields(), existing_id=None,
            )
            repo.upsert_activity(
                conn, athlete_id=athlete, source="strava", external_id="s1",
                external_url=None, raw_payload_id=None,
                fields=activity_fields(), existing_id=activity_id,
            )
        rows = db.fetch_all(
            "SELECT source FROM activity_source WHERE activity_id = ? ORDER BY source",
            (activity_id,),
        )
        assert [r["source"] for r in rows] == ["garmin", "strava"]


class TestDedupAgainstDatabase:
    def test_apparie_une_sortie_decalee_de_trente_secondes(self, athlete):
        with db.connection() as conn:
            activity_id, _ = repo.upsert_activity(
                conn, athlete_id=athlete, source="garmin", external_id="g1",
                external_url=None, raw_payload_id=None,
                fields=activity_fields(), existing_id=None,
            )
            found = find_existing_activity(
                conn, athlete_id=athlete,
                start_time=START + dt.timedelta(seconds=30),
                sport_family="cycling",
            )
        assert found == activity_id

    def test_ne_confond_pas_deux_familles_de_sport(self, athlete):
        with db.connection() as conn:
            repo.upsert_activity(
                conn, athlete_id=athlete, source="garmin", external_id="g1",
                external_url=None, raw_payload_id=None,
                fields=activity_fields(), existing_id=None,
            )
            found = find_existing_activity(
                conn, athlete_id=athlete, start_time=START, sport_family="running",
            )
        assert found is None

    def test_ne_rapproche_pas_deux_sorties_eloignees(self, athlete):
        with db.connection() as conn:
            repo.upsert_activity(
                conn, athlete_id=athlete, source="garmin", external_id="g1",
                external_url=None, raw_payload_id=None,
                fields=activity_fields(), existing_id=None,
            )
            found = find_existing_activity(
                conn, athlete_id=athlete,
                start_time=START + dt.timedelta(hours=3),
                sport_family="cycling",
            )
        assert found is None


class TestRawLayer:
    def test_payload_identique_non_duplique(self, base):
        payload = {"a": 1, "b": [2, 3]}
        with db.connection() as conn:
            first, created_first = db.store_raw(
                conn, source="garmin", endpoint="sleep",
                resource_key="2026-08-08", payload=payload,
            )
            second, created_second = db.store_raw(
                conn, source="garmin", endpoint="sleep",
                resource_key="2026-08-08", payload=payload,
            )
        assert first == second
        assert created_first is True
        assert created_second is False

    def test_ordre_des_cles_sans_incidence(self, base):
        with db.connection() as conn:
            first, _ = db.store_raw(
                conn, source="garmin", endpoint="sleep",
                resource_key="j", payload={"a": 1, "b": 2},
            )
            second, created = db.store_raw(
                conn, source="garmin", endpoint="sleep",
                resource_key="j", payload={"b": 2, "a": 1},
            )
        assert second == first
        assert created is False

    def test_contenu_modifie_cree_une_version(self, base):
        with db.connection() as conn:
            db.store_raw(conn, source="garmin", endpoint="sleep",
                         resource_key="j", payload={"score": 80})
            _, created = db.store_raw(conn, source="garmin", endpoint="sleep",
                                      resource_key="j", payload={"score": 85})
        assert created is True
        assert db.fetch_one("SELECT count(*) AS n FROM raw_api_payload")["n"] == 2


class TestWellness:
    def test_plusieurs_sources_alimentent_la_meme_journee(self, athlete):
        day = dt.date(2026, 8, 8)
        with db.connection() as conn:
            repo.upsert_daily_wellness(conn, athlete, day, {"resting_hr": 48})
            repo.upsert_daily_wellness(conn, athlete, day, {"training_readiness": 74})
        row = db.fetch_one(
            "SELECT resting_hr, training_readiness FROM daily_wellness WHERE day = ?",
            (day,),
        )
        assert row["resting_hr"] == 48
        assert row["training_readiness"] == 74

    def test_sommeil_et_stades(self, athlete):
        day = dt.date(2026, 8, 8)
        stages = [
            {"start_time": dt.datetime(2026, 8, 7, 22, tzinfo=dt.UTC),
             "end_time": dt.datetime(2026, 8, 7, 23, tzinfo=dt.UTC), "stage": "light"},
            {"start_time": dt.datetime(2026, 8, 7, 23, tzinfo=dt.UTC),
             "end_time": dt.datetime(2026, 8, 8, 0, 30, tzinfo=dt.UTC), "stage": "deep"},
        ]
        with db.connection() as conn:
            repo.upsert_sleep(conn, athlete, day, {"score": 82, "duration_s": 27000}, stages)
        assert db.fetch_one("SELECT score FROM sleep_session")["score"] == 82
        assert db.fetch_one("SELECT count(*) AS n FROM sleep_stage")["n"] == 2

    def test_suppression_en_cascade_des_stades(self, athlete):
        day = dt.date(2026, 8, 8)
        with db.connection() as conn:
            repo.upsert_sleep(conn, athlete, day, {"score": 82}, [
                {"start_time": dt.datetime(2026, 8, 7, 22, tzinfo=dt.UTC),
                 "end_time": dt.datetime(2026, 8, 7, 23, tzinfo=dt.UTC), "stage": "light"},
            ])
        with db.connection() as conn:
            conn.execute("DELETE FROM sleep_session WHERE sleep_date = ?", (day,))
        assert db.fetch_one("SELECT count(*) AS n FROM sleep_stage")["n"] == 0

    def test_mesures_intra_journee(self, athlete):
        readings = [
            (dt.datetime(2026, 8, 8, 10, tzinfo=dt.UTC), "stress", 25.0),
            (dt.datetime(2026, 8, 8, 10, 3, tzinfo=dt.UTC), "stress", 40.0),
        ]
        with db.connection() as conn:
            assert repo.insert_wellness_readings(conn, athlete, readings) == 2
            # Réinsérer la même minute met à jour au lieu de dupliquer.
            repo.insert_wellness_readings(conn, athlete, readings[:1])
        assert db.fetch_one("SELECT count(*) AS n FROM wellness_reading")["n"] == 2


class TestQueriesAgainstDatabase:
    def test_sonde_sur_base_migree(self, base):
        from cairn.gui import queries

        health = queries.probe()
        assert health.usable is True
        assert health.engine.startswith("SQLite")

    def test_activites_recentes_rendent_les_sources_en_liste(self, athlete):
        with db.connection() as conn:
            activity_id, _ = repo.upsert_activity(
                conn, athlete_id=athlete, source="garmin", external_id="g1",
                external_url=None, raw_payload_id=None,
                fields=activity_fields(local_date=dt.date.today()), existing_id=None,
            )
            repo.upsert_activity(
                conn, athlete_id=athlete, source="strava", external_id="s1",
                external_url=None, raw_payload_id=None,
                fields=activity_fields(local_date=dt.date.today()),
                existing_id=activity_id,
            )

        from cairn.gui import queries

        rows = queries.recent_activities()
        assert len(rows) == 1
        assert rows[0]["sources"] == ["garmin", "strava"]

    def test_charge_et_recuperation_sur_base_vide(self, base):
        from cairn.gui import queries

        assert queries.load().week_sessions == 0
        assert queries.recovery().hrv is None
        # Le calendrier doit tout de même produire une série complète.
        assert len(queries.load(days=30).daily_series) == 31


class TestLecturesDuTableauDeBord:
    """Requêtes d'affichage exercées contre une vraie base.

    Deux d'entre elles renvoyaient un résultat vide sans jamais lever :
    `counts()` interrogeait `raw.api_payload`, syntaxe PostgreSQL restée après
    la migration, et l'erreur était avalée par le garde-fou qui protège
    l'interface. Une requête qui ne rend rien doit donc être vérifiée sur son
    contenu, pas seulement sur son absence d'exception.
    """

    def test_counts_renvoie_bien_ses_compteurs(self, base):
        from cairn.gui import queries

        c = queries.counts()

        assert c, "un dictionnaire vide signale une requête en échec, pas une base vide"
        assert set(c) == {"activities", "with_streams", "nights", "payloads", "gear"}

    def test_duree_de_sommeil_sans_score(self, base):
        """Toutes les montres ne calculent pas de score de sommeil.

        La durée était lue en même temps que lui : sans score, des centaines de
        nuits mesurées disparaissaient de l'écran.
        """
        from cairn.gui import queries

        db.execute("INSERT INTO athlete (id, display_name) VALUES (1, 'test')")
        db.execute(
            """INSERT INTO sleep_session (athlete_id, sleep_date, start_time, end_time,
                                          duration_s, score)
               VALUES (1, date('now','localtime','-1 day'), datetime('now','-9 hours'),
                       datetime('now'), 28800, NULL)"""
        )

        recuperation = queries.recovery()

        assert recuperation.sleep_score is None
        assert recuperation.sleep_duration_s == 28800


class TestUsureDuMateriel:
    """Une chaîne se change tous les trois mille kilomètres, pas le vélo.

    Strava tient le kilométrage du matériel ; ce qui manque partout, c'est le
    compte par pièce. La vue `v_gear_wear` existait depuis le début mais ne
    renvoyait rien : aucune pièce n'était déclarable.
    """

    @pytest.fixture
    def velo(self, base):
        with db.connection() as conn:
            athlete = repo.get_or_create_athlete(conn, display_name="Test")
            return repo.upsert_gear(conn, athlete, {
                "strava_gear_id": "b1", "kind": "bike", "name": "Nomad",
                "brand": None, "model": None, "frame_type": "mtb",
                "is_primary": True, "retired": False, "distance_m": 5_000_000,
            })

    def test_piece_posee_au_kilometrage_du_jour(self, velo):
        from cairn.gui import queries

        queries.add_gear_component(velo, "chain", "XT", 3_000_000)

        pieces = [dict(r) for r in queries.gear_wear() if dict(r).get("component_id")]
        assert len(pieces) == 1
        # Posée aujourd'hui : rien de parcouru depuis.
        assert pieces[0]["since_install_m"] == 0
        assert pieces[0]["label"] == "XT"

    def test_usure_comptee_depuis_la_pose(self, velo):
        from cairn.gui import queries

        queries.add_gear_component(velo, "chain", None, 3_000_000)
        # Le vélo roule mille kilomètres de plus.
        db.execute("UPDATE gear SET distance_m = 6000000 WHERE id = ?", (velo,))

        piece = next(dict(r) for r in queries.gear_wear() if dict(r).get("component_id"))
        assert piece["since_install_m"] == 1_000_000
        usure = 100 * piece["since_install_m"] / piece["service_interval_m"]
        assert usure == pytest.approx(33.3, abs=0.1)

    def test_remplacement_archive_lancienne(self, velo):
        """L'historique doit dire combien de chaînes ont tenu combien."""
        from cairn.gui import queries

        queries.add_gear_component(velo, "chain", "XT", 3_000_000)
        ancienne = next(
            dict(r)["component_id"] for r in queries.gear_wear() if dict(r).get("component_id")
        )
        db.execute("UPDATE gear SET distance_m = 8000000 WHERE id = ?", (velo,))

        queries.replace_gear_component(ancienne)

        actives = [dict(r) for r in queries.gear_wear() if dict(r).get("component_id")]
        assert len(actives) == 1, "une seule pièce active à la fois"
        assert actives[0]["component_id"] != ancienne
        assert actives[0]["since_install_m"] == 0
        assert actives[0]["service_interval_m"] == 3_000_000, "réglages repris"
        # L'ancienne est archivée, pas supprimée.
        total = db.fetch_one("SELECT count(*) AS n FROM gear_component")["n"]
        assert total == 2

    def test_materiel_sans_piece_reste_liste(self, velo):
        """Un vélo sans pièce suivie doit tout de même apparaître."""
        from cairn.gui import queries

        lignes = [dict(r) for r in queries.gear_wear()]
        assert len(lignes) == 1
        assert lignes[0]["component_id"] is None
        assert lignes[0]["distance_m"] == 5_000_000

    def test_remplacer_une_piece_inconnue_ne_fait_rien(self, velo):
        from cairn.gui import queries

        queries.replace_gear_component(9999)

        assert db.fetch_one("SELECT count(*) AS n FROM gear_component")["n"] == 0
