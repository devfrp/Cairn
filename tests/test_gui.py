"""Tests de la couche graphique : formatage, modèle de table, réglages."""

import datetime as dt

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from cairn.gui.pages.activities import ActivityModel  # noqa: E402
from cairn.gui.widgets import (  # noqa: E402
    format_distance,
    format_duration,
    format_elevation,
    format_relative_day,
)


@pytest.fixture(scope="session")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


class TestFormatting:
    @pytest.mark.parametrize(
        ("seconds", "attendu"),
        [(None, "—"), (0, "—"), (90, "1 min"), (3600, "1 h 00"),
         (27000, "7 h 30"), (5400, "1 h 30")],
    )
    def test_duree(self, seconds, attendu):
        assert format_duration(seconds) == attendu

    def test_distance_en_virgule_francaise(self):
        assert format_distance(62000) == "62,0 km"
        assert format_distance(1500) == "1,5 km"

    def test_distance_absente(self):
        assert format_distance(None) == "—"
        assert format_distance(0) == "—"

    def test_denivele(self):
        assert format_elevation(1250.4) == "1250 m"
        assert format_elevation(None) == "—"
        # Un dénivelé nul est une information, pas une absence de mesure.
        assert format_elevation(0) == "0 m"

    def test_jour_relatif(self):
        today = dt.date.today()
        assert format_relative_day(today) == "aujourd'hui"
        assert format_relative_day(today - dt.timedelta(days=1)) == "hier"
        assert format_relative_day(today - dt.timedelta(days=3)) == "il y a 3 jours"
        vieux = today - dt.timedelta(days=40)
        assert format_relative_day(vieux) == vieux.strftime("%d/%m/%Y")

    def test_jour_relatif_accepte_un_datetime(self):
        assert format_relative_day(dt.datetime.now()) == "aujourd'hui"


def _activity(**overrides):
    base = {
        "start_time_local": dt.datetime(2026, 8, 8, 9, 30),
        "sport": "mtb",
        "name": "Enduro Pra Loup",
        "distance_m": 24500.0,
        "elevation_gain_m": 1250.0,
        "elapsed_s": 7200,
        "avg_hr": 148,
        "max_hr": 181,
        "load": 220.0,
        "has_streams": True,
        "gear_name": "Santa Cruz Nomad",
        "sources": ["garmin", "strava"],
    }
    base.update(overrides)
    return base


class TestActivityModel:
    def test_dimensions(self, qt_app):
        model = ActivityModel()
        model.set_rows([_activity(), _activity()])
        assert model.rowCount() == 2
        assert model.columnCount() == len(ActivityModel.COLUMNS)

    def _cell(self, model, row, column, role=Qt.ItemDataRole.DisplayRole):
        return model.data(model.index(row, column), role)

    def test_colonnes_affichees(self, qt_app):
        model = ActivityModel()
        model.set_rows([_activity()])
        assert self._cell(model, 0, 0) == "08/08/26 09:30"
        assert self._cell(model, 0, 1) == "VTT"
        assert self._cell(model, 0, 2) == "Enduro Pra Loup"
        assert self._cell(model, 0, 3) == "24,5 km"
        assert self._cell(model, 0, 4) == "1250 m"
        assert self._cell(model, 0, 5) == "2 h 00"
        assert self._cell(model, 0, 6) == "148"
        assert self._cell(model, 0, 7) == "220"

    def test_sources_en_initiales(self, qt_app):
        model = ActivityModel()
        model.set_rows([_activity()])
        assert self._cell(model, 0, 9) == "G+S"

    def test_source_unique(self, qt_app):
        model = ActivityModel()
        model.set_rows([_activity(sources=["strava"])])
        assert self._cell(model, 0, 9) == "S"

    def test_infobulle_developpe_les_sources(self, qt_app):
        model = ActivityModel()
        model.set_rows([_activity()])
        tip = self._cell(model, 0, 9, Qt.ItemDataRole.ToolTipRole)
        assert tip == "garmin + strava"

    def test_valeurs_manquantes(self, qt_app):
        model = ActivityModel()
        model.set_rows([
            _activity(avg_hr=None, load=None, gear_name=None,
                      distance_m=None, sources=[])
        ])
        assert self._cell(model, 0, 3) == "—"
        assert self._cell(model, 0, 6) == "—"
        assert self._cell(model, 0, 7) == "—"
        assert self._cell(model, 0, 8) == "—"
        assert self._cell(model, 0, 9) == "—"

    def test_sport_inconnu_affiche_sa_cle(self, qt_app):
        model = ActivityModel()
        model.set_rows([_activity(sport="kitesurf")])
        assert self._cell(model, 0, 1) == "kitesurf"

    def test_index_invalide(self, qt_app):
        from PySide6.QtCore import QModelIndex

        model = ActivityModel()
        model.set_rows([_activity()])
        assert model.data(QModelIndex()) is None

    def test_entetes(self, qt_app):
        model = ActivityModel()
        assert model.headerData(0, Qt.Orientation.Horizontal) == "Date"
        assert model.headerData(0, Qt.Orientation.Vertical) is None


class TestPrefs:
    def test_champs_sensibles_declares(self):
        from cairn.gui import prefs

        # Une clé de chiffrement ou un secret client ne doit jamais finir
        # dans un journal : la liste sert de garde-fou explicite.
        assert "secret_key" in prefs.SENSITIVE
        assert "strava_client_secret" in prefs.SENSITIVE

    def test_chaque_champ_a_sa_variable(self):
        from cairn.gui import prefs

        for key, env_name in prefs.FIELDS.items():
            assert env_name.startswith("CAIRN_"), key

    def test_valeur_vide_ne_masque_pas_le_defaut(self, qt_app, monkeypatch, tmp_path):
        """Un réglage laissé vide doit disparaître de l'environnement."""
        from cairn.gui import prefs

        monkeypatch.setenv("CAIRN_GARMIN_EMAIL", "ancien@exemple.fr")
        monkeypatch.setattr(prefs, "app_directory", lambda: tmp_path)
        (tmp_path / prefs.PORTABLE_FILENAME).write_text("[General]\n")

        prefs.apply_to_environment()

        import os

        assert "CAIRN_GARMIN_EMAIL" not in os.environ

    def test_mode_portable_detecte(self, qt_app, monkeypatch, tmp_path):
        from cairn.gui import prefs

        monkeypatch.setattr(prefs, "app_directory", lambda: tmp_path)
        assert prefs.is_portable() is False

        (tmp_path / prefs.PORTABLE_FILENAME).write_text("[General]\n")
        assert prefs.is_portable() is True
        assert "Mode portable" in prefs.describe_location()
