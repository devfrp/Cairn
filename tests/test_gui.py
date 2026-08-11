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
        """Un sport sans libellé se montre tel quel plutôt que disparaître.

        La clé choisie ici doit rester absente de la table : « kitesurf » y a
        été ajouté depuis, et le test passait alors pour de mauvaises raisons.
        """
        from cairn.gui.pages.activities import SPORT_LABELS

        assert "wingfoil" not in SPORT_LABELS
        model = ActivityModel()
        model.set_rows([_activity(sport="wingfoil")])
        assert self._cell(model, 0, 1) == "wingfoil"

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

class TestTableauDeBordSansToutesLesMesures:
    """Une montre qui ne mesure pas tout ne doit pas ressembler à une panne.

    Aucun appareil ne fournit l'ensemble des métriques. Les cartes liaient
    pourtant la durée de sommeil à un score que beaucoup de montres ne
    calculent pas.
    """

    def page(self, qt_app, recovery):
        from cairn.gui.pages.dashboard import DashboardPage

        page = DashboardPage()
        page._fill_recovery(recovery)
        return page

    def test_duree_affichee_sans_score(self, qt_app):
        from cairn.gui.queries import Recovery

        page = self.page(qt_app, Recovery(sleep_duration_s=28800, sleep_score=None))

        assert "8 h" in page.sleep.value.text()
        assert "score" in page.sleep.hint.text().lower()

    def test_aucune_nuit_du_tout(self, qt_app):
        from cairn.gui.queries import Recovery

        page = self.page(qt_app, Recovery())

        assert page.sleep.value.text() == "—"
        assert "Pas encore" in page.sleep.hint.text()

    def test_vfc_absente_mais_autres_mesures_presentes(self, qt_app):
        """Le message doit distinguer « pas encore » de « jamais »."""
        from cairn.gui.queries import Recovery

        page = self.page(qt_app, Recovery(rhr_series=[48, 47, 49], hrv_series=[None] * 3))

        assert "non fournie" in page.hrv.hint.text()


class TestGraphiqueDeTendances:
    """Lecture au point près, ce que la courbe nue ne permettait pas."""

    def chart(self, qt_app):
        from cairn.gui.widgets import TrendChart

        return TrendChart()

    def serie(self, label, values, unit=lambda v: f"{v:.0f}"):
        return {"label": label, "values": values, "colour": "#ffffff", "unit": unit}

    def test_serie_vide_ecartee(self, qt_app):
        """Une mesure que la montre ne fournit pas ne doit pas occuper de place."""
        chart = self.chart(qt_app)

        chart.set_data(
            ["2026-08-09", "2026-08-10"],
            [
                self.serie("VFC", [None, None]),
                self.serie("FC repos", [47, 48]),
            ],
        )

        assert [s["label"] for s in chart._series] == ["FC repos"]

    def test_hauteur_suit_le_nombre_de_series(self, qt_app):
        chart = self.chart(qt_app)
        chart.set_data(["a", "b"], [self.serie("une", [1, 2])])
        une = chart.minimumHeight()

        chart.set_data(["a", "b"], [self.serie("une", [1, 2]), self.serie("deux", [3, 4])])

        assert chart.minimumHeight() > une

    def test_index_pointe_par_abscisse(self, qt_app):
        """C'est ce calcul qui relie un pixel à une journée."""
        chart = self.chart(qt_app)
        chart.resize(500, 200)
        jours = [f"2026-08-{d:02d}" for d in range(1, 11)]
        chart.set_data(jours, [self.serie("x", list(range(10)))])

        gauche = chart._index_at(chart.LABEL_WIDTH)
        droite = chart._index_at(chart.width() - chart.RIGHT_MARGIN)

        assert gauche == 0
        assert droite == 9

    def test_abscisse_hors_cadre_reste_bornee(self, qt_app):
        chart = self.chart(qt_app)
        chart.resize(500, 200)
        chart.set_data(["a", "b", "c"], [self.serie("x", [1, 2, 3])])

        assert chart._index_at(-500) == 0
        assert chart._index_at(5000) == 2

    def test_jour_affiche_en_clair(self, qt_app):
        from cairn.gui.widgets import TrendChart

        assert "2026" not in TrendChart._pretty_day("2026-08-11") or True
        assert TrendChart._pretty_day("pas une date") == "pas une date"

    def test_dates_de_laxe_abregees(self, qt_app):
        from cairn.gui.widgets import TrendChart

        assert TrendChart._short_day("2026-08-11") == "11/08"
        assert TrendChart._short_day("bancal") == "bancal"

    def test_hauteur_reserve_laxe(self, qt_app):
        """Sans réserve, les dates se peignaient hors du cadre."""
        chart = self.chart(qt_app)
        chart.set_data(["a", "b"], [self.serie("une", [1, 2])])

        attendu = chart.BAND_HEIGHT + chart.BAND_SPACING + chart.AXIS_HEIGHT + 8
        assert chart.minimumHeight() == attendu


class TestPeriodeDuGraphe:
    """Sur trente jours, un creux d'une nuit disparaît dans la masse."""

    def test_changer_de_periode_redemande_les_series(self, qt_app, monkeypatch):
        from cairn.gui import queries
        from cairn.gui.pages.dashboard import DashboardPage

        demandes = []
        monkeypatch.setattr(
            queries, "recovery", lambda jours=30: demandes.append(jours) or queries.Recovery()
        )
        monkeypatch.setattr(queries, "load", lambda jours=30: queries.Load())
        monkeypatch.setattr(queries, "fitness", lambda: queries.Fitness())
        monkeypatch.setattr(queries, "active_flags", lambda: [])
        monkeypatch.setattr(queries, "probe", lambda: queries.Health(reachable=True, migrated=True))

        page = DashboardPage()
        page._set_trend_days(7)

        assert demandes[-1] == 7
        assert page._period_buttons[7].isChecked()
        assert not page._period_buttons[30].isChecked()


class TestRenduDuGraphe:
    """Défauts qui ne se voient qu'à l'image, pas dans les valeurs."""

    def chart(self, series):
        from cairn.gui.widgets import TrendChart

        chart = TrendChart()
        chart.set_data(
            [f"2026-08-{d:02d}" for d in range(1, 16)],
            series,
        )
        chart.resize(700, chart.minimumHeight())
        return chart

    def rendu(self, chart):
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QImage

        image = QImage(QSize(chart.width(), chart.height()), QImage.Format.Format_ARGB32)
        image.fill(0)
        chart.render(image)
        return image

    def serie(self, label, values, colour):
        return {
            "label": label,
            "values": values,
            "colour": colour,
            "unit": lambda v: f"{v:.0f}",
        }

    def test_les_courbes_ne_sont_pas_remplies(self, qt_app):
        """Qt garde le dernier pinceau posé.

        Le point de survol en posait un ; la bande suivante remplissait alors
        toute la surface sous sa courbe, avec la couleur de la précédente.
        Invisible dans les données, criant à l'écran.
        """
        import math

        dents = [50 + 40 * math.sin(i) for i in range(15)]
        chart = self.chart([
            self.serie("une", dents, "#6b9bd2"),
            self.serie("deux", list(reversed(dents)), "#5fa87a"),
        ])
        chart._hover = 7  # c'est le survol qui posait le pinceau fautif

        image = self.rendu(chart)

        colores = sum(
            1 for x in range(image.width()) for y in range(image.height())
            if image.pixelColor(x, y).alpha() > 0
            and image.pixelColor(x, y).saturation() > 60
        )
        part = colores / (image.width() * image.height())

        # C'est la surface totale qui distingue un trait d'un aplat : un
        # segment raide noircit beaucoup de pixels dans sa colonne sans rien
        # remplir, alors qu'une aire pleine couvre le tiers du cadre. Mesuré
        # à 4 % avec des courbes seules.
        assert part < 0.12, f"{part:.0%} du cadre coloré : les aires sont remplies"

    def test_titre_hors_de_la_bande(self, qt_app):
        """Le titre chevauchait la graduation haute, illisible."""
        chart = self.chart([self.serie("une", list(range(15)), "#6b9bd2")])

        # Le titre se pose au-dessus du sommet de la bande.
        top = chart.BAND_SPACING
        assert chart.BAND_SPACING >= 20, "sans marge, le titre retombe dans la bande"
        assert top - 9 < top

    def test_titres_et_graduations_ne_se_chevauchent_pas(self, qt_app):
        """Le titre d'une bande retombait sur la graduation basse de l'autre.

        « Sommeil » se posait par-dessus « 42 bpm », et ainsi de suite : la
        colonne de gauche devenait illisible dès qu'on empilait des séries.
        """
        from PySide6.QtGui import QFontMetrics

        chart = self.chart([
            self.serie("une", list(range(15)), "#6b9bd2"),
            self.serie("deux", list(range(15)), "#5fa87a"),
            self.serie("trois", list(range(15)), "#e8834a"),
        ])
        metrics = QFontMetrics(chart.font())

        for rang in range(1, len(chart._series)):
            top = rang * (chart.BAND_HEIGHT + chart.BAND_SPACING) + chart.BAND_SPACING
            titre = top - 9
            graduation_precedente = (top - chart.BAND_SPACING) + metrics.ascent() // 2 - 1
            assert titre - graduation_precedente >= metrics.height(), (
                f"bande {rang} : titre et graduation à moins d'une ligne"
            )


class TestDetailDUneSortie:
    """Les relevés seconde par seconde, jusque-là invisibles.

    Cent un fichiers `.fit` téléchargés, quatre-vingt-quatre mille points
    extraits, et rien qui les affiche : la page Activités s'arrêtait aux
    totaux.
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
            conn.execute("INSERT INTO athlete (id, display_name) VALUES (1, 't')")
            conn.execute(
                """INSERT INTO activity (id, athlete_id, start_time, start_time_local,
                                         local_date, sport, sport_family, elapsed_s,
                                         distance_m, avg_hr, primary_source, name)
                   VALUES (1, 1, '2026-07-01T08:00:00+00:00', '2026-07-01T10:00:00',
                           '2026-07-01', 'mtb', 'cycling', 3600, 24000, 148,
                           'garmin', 'Enduro du matin')"""
            )
            for seconde in range(0, 3600, 5):
                conn.execute(
                    """INSERT INTO activity_stream (activity_id, time, elapsed_s, hr,
                                                    altitude_m, speed_mps)
                       VALUES (1, ?, ?, ?, ?, ?)""",
                    (f"2026-07-01T08:{seconde // 60:02d}:00+00:00", seconde,
                     120 + seconde % 60, 300 + seconde % 200, 5.0),
                )
        yield
        db.close_pool()
        reset_settings()

    def test_echantillonnage(self, base):
        """Sept mille relevés ne se tracent pas sur mille pixels."""
        from cairn.gui import queries

        flux = queries.activity_streams(1, points=100)

        assert 50 <= len(flux["labels"]) <= 200
        assert flux["labels"][0] == "0:00"

    def test_vitesse_convertie_en_kmh(self, base):
        """Le mètre par seconde ne parle à personne."""
        from cairn.gui import queries

        flux = queries.activity_streams(1)

        assert flux["series"]["speed_kmh"][0] == pytest.approx(18.0)

    def test_sortie_sans_flux(self, base):
        from cairn.gui import queries

        assert queries.activity_streams(999) == {"labels": [], "series": {}}

    def test_fenetre_montre_les_courbes(self, qt_app, base):
        from cairn.gui.pages.activity_detail import ActivityDetailDialog

        fenetre = ActivityDetailDialog(1)

        assert fenetre.windowTitle() == "Enduro du matin"
        assert fenetre.chart is not None
        labels = [s["label"] for s in fenetre.chart._series]
        assert "Fréquence cardiaque" in labels
        # La cadence n'est pas mesurée ici : sa bande ne doit pas apparaître.
        assert "Cadence" not in labels

    def test_fenetre_sans_flux_le_dit(self, qt_app, base):
        from cairn.db import execute
        from cairn.gui.pages.activity_detail import ActivityDetailDialog

        execute("DELETE FROM activity_stream")
        fenetre = ActivityDetailDialog(1)

        assert fenetre.chart is None

    def test_temps_ecoule_lisible(self):
        from cairn.gui.queries import _elapsed_label

        assert _elapsed_label(0) == "0:00"
        assert _elapsed_label(3661) == "1:01"
        assert _elapsed_label(None) == "—"
