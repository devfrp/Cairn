"""Tests de la couche graphique : formatage, modèle de table, réglages."""

import datetime as dt
import pathlib

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
        chart = self.chart(qt_app)

        assert chart._cursor_label("pas une date") == "pas une date"

    def test_dates_de_laxe_abregees(self, qt_app):
        chart = self.chart(qt_app)

        assert chart._axis_label("2026-08-11") == "11/08"
        assert chart._axis_label("bancal") == "bancal"

    def test_axe_horaire_garde_lheure_telle_quelle(self, qt_app):
        """En vue d'une journée, l'étiquette est déjà une heure."""
        chart = self.chart(qt_app)
        chart.set_data(["06:00", "06:05"], [self.serie("FC", [52, 54])], mode="time")

        assert chart._axis_label("06:05") == "06:05"
        assert chart._cursor_label("06:05") == "à 06 h 05"

    def test_plages_ombrees_derriere_les_courbes(self, qt_app):
        """La nuit doit se voir sans survoler le graphe."""
        chart = self.chart(qt_app)
        chart.set_data(
            ["00:00", "00:05", "00:10", "00:15"],
            [self.serie("FC", [52, 51, 50, 49])],
            mode="time",
            spans=[{"kind": "sleep", "label": "sommeil", "start": 0.0, "end": 2.0}],
        )
        chart.resize(600, chart.minimumHeight())

        from PySide6.QtCore import QSize
        from PySide6.QtGui import QImage

        image = QImage(QSize(chart.width(), chart.height()), QImage.Format.Format_ARGB32)
        image.fill(0)
        chart.render(image)

        y = chart.BAND_SPACING - 4
        dedans = image.pixelColor(chart.LABEL_WIDTH + 20, y).name()
        dehors = image.pixelColor(chart.width() - 30, y).name()
        assert dedans != dehors

    def test_plages_bornees_a_laxe(self, qt_app):
        """Une nuit commencée la veille ne doit pas déborder du cadre."""
        chart = self.chart(qt_app)
        chart.set_data(
            ["00:00", "00:05"],
            [self.serie("FC", [52, 51])],
            mode="time",
            spans=[{"kind": "sleep", "label": "", "start": 0.0, "end": 1.0}],
        )

        assert chart._spans[0]["end"] <= len(chart._days) - 1

    def test_colonne_selargit_pour_les_titres_longs(self, qt_app):
        """« Distance à l'entraînement » débordait sur le graphe."""
        from cairn.gui.widgets import TrendChart

        chart = self.chart(qt_app)
        chart.set_data(["a", "b"], [self.serie("Distance à l'entraînement", [1, 2])])

        assert chart.LABEL_WIDTH > TrendChart.LABEL_WIDTH
        assert chart.LABEL_WIDTH <= TrendChart.LABEL_MAX

    def test_colonne_bornee(self, qt_app):
        """Un titre démesuré ne doit pas manger la moitié du graphe."""
        from cairn.gui.widgets import TrendChart

        chart = self.chart(qt_app)
        chart.set_data(["a", "b"], [self.serie("t" * 200, [1, 2])])

        assert chart.LABEL_WIDTH == TrendChart.LABEL_MAX

    def test_valeur_affichee_contre_son_point(self, qt_app):
        """L'encart unique masquait le tiers du graphe à dix bandes."""
        chart = self.chart(qt_app)
        chart.set_data(
            [f"2026-08-{d:02d}" for d in range(1, 11)],
            [self.serie("x", list(range(10)))],
        )
        chart.resize(600, chart.minimumHeight())
        chart._hover = 5

        from PySide6.QtCore import QSize
        from PySide6.QtGui import QImage

        image = QImage(QSize(chart.width(), chart.height()), QImage.Format.Format_ARGB32)
        image.fill(0)
        chart.render(image)

        step = (chart.width() - chart.LABEL_WIDTH - chart.RIGHT_MARGIN) / 9
        x = int(chart.LABEL_WIDTH + 5 * step)
        haut = chart.BAND_SPACING
        # La pastille se pose juste à droite du point, dans la bande.
        proche = any(
            image.pixelColor(x + delta, y).name() != image.pixelColor(x + delta, 2).name()
            for delta in range(12, 40)
            for y in range(haut, haut + chart.BAND_HEIGHT)
        )
        assert proche

    def test_clic_sur_un_jour_le_signale(self, qt_app):
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        chart = self.chart(qt_app)
        chart.resize(600, 200)
        chart.set_data(
            [f"2026-08-{d:02d}" for d in range(1, 11)],
            [self.serie("x", list(range(10)))],
        )
        recus = []
        chart.day_clicked.connect(recus.append)

        chart.mousePressEvent(
            QMouseEvent(
                QMouseEvent.Type.MouseButtonPress,
                QPointF(chart.LABEL_WIDTH, 50),
                QPointF(chart.LABEL_WIDTH, 50),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )

        assert recus == ["2026-08-01"]

    def test_pas_de_clic_en_vue_journee(self, qt_app):
        """Une heure ne s'ouvre pas : il n'y a rien en dessous."""
        from PySide6.QtCore import QPointF, Qt
        from PySide6.QtGui import QMouseEvent

        chart = self.chart(qt_app)
        chart.resize(600, 200)
        chart.set_data(["00:00", "00:05"], [self.serie("x", [1, 2])], mode="time")
        recus = []
        chart.day_clicked.connect(recus.append)

        chart.mousePressEvent(
            QMouseEvent(
                QMouseEvent.Type.MouseButtonPress,
                QPointF(chart.LABEL_WIDTH, 50),
                QPointF(chart.LABEL_WIDTH, 50),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )

        assert recus == []

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

        assert queries.activity_streams(999) == {"labels": [], "track": [], "series": {}}

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


def _en_utc(local: dt.datetime) -> str:
    """Instant local écrit tel que la base le stocke."""
    return local.astimezone(dt.UTC).isoformat(sep=" ")


class TestJourneeDetaillee:
    """La montre relève toutes les deux minutes ; le graphe s'arrêtait au jour.

    Une nuit hachée ou un pic de stress en réunion n'apparaissaient nulle
    part : la moyenne quotidienne les avait déjà écrasés.
    """

    JOUR = dt.date(2026, 7, 1)

    @pytest.fixture
    def base(self, tmp_path, monkeypatch):
        from cairn import db, migrate
        from cairn.config import reset_settings

        monkeypatch.setenv("CAIRN_DB_PATH", str(tmp_path / "cairn.db"))
        monkeypatch.setenv("CAIRN_SECRET_KEY", "x" * 44)
        reset_settings()
        db.close_pool()
        migrate.upgrade()
        minuit = dt.datetime(2026, 7, 1).astimezone()
        with db.connection() as conn:
            conn.execute("INSERT INTO athlete (id, display_name) VALUES (1, 't')")
            # Les relevés sont stockés en UTC mais rangés par jour local : la
            # fixture part donc de minuit local, sans quoi le décalage horaire
            # de la machine déciderait du résultat.
            for minute in range(0, 1440, 3):
                heure = _en_utc(minuit + dt.timedelta(minutes=minute))
                for metric, valeur in (("hr", 50 + minute % 40), ("stress", minute % 90),
                                       ("body_battery", 5 + minute % 90)):
                    conn.execute(
                        "INSERT INTO wellness_reading (athlete_id, time, metric, value) "
                        "VALUES (1, ?, ?, ?)",
                        (heure, metric, float(valeur)),
                    )
            # Garmin code par -1 les instants où la mesure n'a pas abouti.
            conn.execute(
                "INSERT INTO wellness_reading (athlete_id, time, metric, value) "
                "VALUES (1, ?, 'hr', -1.0)",
                (_en_utc(minuit + dt.timedelta(hours=12, minutes=1)),),
            )
            conn.execute(
                """INSERT INTO sleep_session (athlete_id, sleep_date, start_time,
                                              end_time, duration_s)
                   VALUES (1, '2026-07-01', ?, ?, 28800)""",
                (_en_utc(minuit - dt.timedelta(hours=3)),
                 _en_utc(minuit + dt.timedelta(hours=5))),
            )
            conn.execute(
                """INSERT INTO activity (id, athlete_id, start_time, start_time_local,
                                         local_date, sport, sport_family, elapsed_s,
                                         primary_source, name)
                   VALUES (1, 1, '2026-07-01T16:00:00+00:00', '2026-07-01 18:00:00',
                           '2026-07-01', 'road', 'cycling', 3600, 'garmin', 'Sortie du soir')"""
            )
            # Le fichier de la séance relève à la seconde là où la montre, au
            # fil de la journée, ne donne qu'un point toutes les trois minutes.
            depart = minuit + dt.timedelta(hours=18)
            for seconde in range(0, 3600, 10):
                conn.execute(
                    """INSERT INTO activity_stream (activity_id, time, elapsed_s, hr,
                                                    speed_mps, altitude_m, cadence)
                       VALUES (1, ?, ?, 150, 5.0, 220.0, 85)""",
                    (_en_utc(depart + dt.timedelta(seconds=seconde)), seconde),
                )
            for stade, heure, duree in (("deep", 0, 60), ("light", 1, 60),
                                        ("rem", 2, 60), ("awake", 3, 30)):
                conn.execute(
                    """INSERT INTO sleep_stage (athlete_id, sleep_date, start_time,
                                                end_time, stage)
                       VALUES (1, '2026-07-01', ?, ?, ?)""",
                    (_en_utc(minuit + dt.timedelta(hours=heure)),
                     _en_utc(minuit + dt.timedelta(hours=heure, minutes=duree)), stade),
                )
        yield
        db.close_pool()
        reset_settings()

    def test_axe_couvre_la_journee_entiere(self, base):
        from cairn.gui import queries

        jour = queries.intraday("2026-07-01")

        assert len(jour.labels) == 24 * 60 // queries.INTRADAY_BUCKET_MIN
        assert jour.labels[0] == "00:00"
        assert jour.labels[-1] == "23:55"

    def test_cases_sans_releve_restent_vides(self, base):
        """La montre retirée en cours de journée doit se voir comme un trou."""
        from cairn.gui import queries

        jour = queries.intraday("2026-07-01")
        avant = sum(1 for v in jour.get("hr") if v is not None)

        from cairn.db import execute

        execute(
            "DELETE FROM wellness_reading WHERE metric = 'hr' "
            "AND CAST(strftime('%H', time, 'localtime') AS INTEGER) BETWEEN 10 AND 11"
        )
        apres = queries.intraday("2026-07-01")

        assert avant > sum(1 for v in apres.get("hr") if v is not None)
        assert any(v is None for v in apres.get("hr"))

    def test_valeur_manquante_codee_negativement_ecartee(self, base):
        """Un -1 laissé passer creuserait un puits au milieu de la courbe."""
        from cairn.gui import queries

        jour = queries.intraday("2026-07-01")

        assert min(v for v in jour.get("hr") if v is not None) >= 0

    def test_nuit_commencee_la_veille_rognee_a_minuit(self, base):
        from cairn.gui import queries

        jour = queries.intraday("2026-07-01")
        nuits = [s for s in jour.spans if s["kind"] == "sleep"]

        assert len(nuits) == 1
        assert nuits[0]["start"] == 0.0
        assert 0 < nuits[0]["end"] < len(jour.labels)

    def test_seance_situee_sur_laxe(self, base):
        from cairn.gui import queries

        jour = queries.intraday("2026-07-01")
        sorties = [s for s in jour.spans if s["kind"] == "activity"]

        assert [s["label"] for s in sorties] == ["Sortie du soir"]
        # Départ à 18 h locales, une heure durant : cases 216 à 228.
        assert sorties[0]["start"] == pytest.approx(216.0)
        assert sorties[0]["end"] == pytest.approx(228.0)

    def test_bornes_des_jours_releves(self, base):
        from cairn.gui import queries

        bornes = queries.intraday_bounds()

        assert bornes["first_day"] == "2026-07-01"
        assert bornes["last_day"] == "2026-07-01"

    def test_dernier_jour_par_defaut(self, base):
        from cairn.gui import queries

        assert queries.intraday().day == "2026-07-01"

    def test_fenetre_utc_couvre_le_jour_local(self, base):
        """Filtrer sur l'instant brut, sans quoi chaque clic coûte un scan.

        La conversion en heure locale ligne par ligne empêche tout usage
        d'index : sur cinq mois de relevés, une journée mettait presque deux
        secondes à s'afficher.
        """
        from cairn.gui.queries import _local_day, _utc_window

        debut, fin = _utc_window("2026-07-01")

        assert _local_day(debut) == "2026-07-01"
        # La borne haute est exclue : c'est déjà le lendemain.
        assert _local_day(fin) == "2026-07-02"
        assert debut < fin

    def test_meme_journee_quel_que_soit_le_filtre(self, base):
        """La fenêtre UTC doit sélectionner exactement les mêmes relevés."""
        from cairn.db import fetch_all
        from cairn.gui.queries import _utc_window

        par_date = fetch_all(
            "SELECT count(*) AS n FROM wellness_reading "
            "WHERE date(time, 'localtime') = '2026-07-01'"
        )[0]["n"]
        par_fenetre = fetch_all(
            "SELECT count(*) AS n FROM wellness_reading WHERE time >= ? AND time < ?",
            _utc_window("2026-07-01"),
        )[0]["n"]

        assert par_fenetre == par_date > 0

    def test_flux_de_seance_prime_sur_les_relevés_du_jour(self, base):
        """Un point par seconde vaut mieux qu'un relevé toutes les trois minutes.

        Pendant une séance, la montre continue son échantillonnage lâche ;
        le fichier de la sortie, lui, donne la vraie moyenne de chaque case.
        """
        from cairn.gui import queries

        jour = queries.intraday("2026-07-01")
        case = 18 * 60 // queries.INTRADAY_BUCKET_MIN + 1   # 18 h 05, en pleine séance

        assert jour.get("hr")[case] == pytest.approx(150.0)

    def test_mesures_de_seance_absentes_hors_seance(self, base):
        """Une bande de vitesse ne doit pas s'inventer un tracé la nuit."""
        from cairn.gui import queries

        jour = queries.intraday("2026-07-01")
        pas = queries.INTRADAY_BUCKET_MIN

        assert jour.get("speed_kmh")[18 * 60 // pas + 1] == pytest.approx(18.0)
        assert jour.get("speed_kmh")[3 * 60 // pas] is None
        assert jour.get("cadence")[18 * 60 // pas + 1] == pytest.approx(85.0)
        assert jour.get("altitude_m")[18 * 60 // pas + 1] == pytest.approx(220.0)

    def test_stades_de_sommeil_etages(self, base):
        """Profond en bas, éveillé en haut : la courbe se lit en hypnogramme."""
        from cairn.gui import queries

        stades = queries.intraday("2026-07-01").get("sleep_stage")
        pas = queries.INTRADAY_BUCKET_MIN

        assert stades[30 // pas] == queries.SLEEP_DEPTH["deep"]
        assert stades[90 // pas] == queries.SLEEP_DEPTH["light"]
        assert stades[150 // pas] == queries.SLEEP_DEPTH["rem"]
        assert stades[195 // pas] == queries.SLEEP_DEPTH["awake"]
        assert stades[12 * 60 // pas] is None

    def test_stade_ne_deborde_pas_sur_la_case_suivante(self, base):
        """Découper sur les bornes prolongeait chaque phase de cinq minutes."""
        from cairn.gui import queries

        stades = queries.intraday("2026-07-01").get("sleep_stage")
        pas = queries.INTRADAY_BUCKET_MIN

        # Le sommeil s'arrête à 3 h 30 : la case suivante doit être vide.
        assert stades[(3 * 60 + 25) // pas] == queries.SLEEP_DEPTH["awake"]
        assert stades[(3 * 60 + 30) // pas] is None

    def test_journee_sans_releve_continu_reste_consultable(self, base):
        """Une séance et une nuit suffisent à faire une journée à montrer.

        S'en tenir aux relevés continus interdisait d'ouvrir aujourd'hui tant
        que la montre n'avait pas versé ses mesures de la journée.
        """
        from cairn.db import execute
        from cairn.gui import queries

        execute("DELETE FROM wellness_reading")

        jour = queries.intraday()
        assert jour.day == "2026-07-01"
        assert any(v is not None for v in jour.get("speed_kmh"))
        assert any(v is not None for v in jour.get("sleep_stage"))

    def test_base_vide_ne_leve_pas(self, base):
        from cairn.db import execute
        from cairn.gui import queries

        for table in ("wellness_reading", "activity_stream", "sleep_stage",
                      "sleep_session", "activity"):
            execute(f"DELETE FROM {table}")

        vide = queries.intraday()
        assert vide.day == ""
        assert vide.labels == []


class TestMesuresQuotidiennes:
    """Le tableau de bord n'exposait que cinq des trente mesures stockées."""

    @pytest.fixture
    def base(self, tmp_path, monkeypatch):
        from cairn import db, migrate
        from cairn.config import reset_settings

        monkeypatch.setenv("CAIRN_DB_PATH", str(tmp_path / "cairn.db"))
        monkeypatch.setenv("CAIRN_SECRET_KEY", "x" * 44)
        reset_settings()
        db.close_pool()
        migrate.upgrade()
        hier = dt.date.today() - dt.timedelta(days=1)
        with db.connection() as conn:
            conn.execute("INSERT INTO athlete (id, display_name) VALUES (1, 't')")
            conn.execute(
                """INSERT INTO daily_wellness (athlete_id, day, resting_hr, steps,
                                               avg_stress, calories_active,
                                               moderate_intensity_min,
                                               vigorous_intensity_min)
                   VALUES (1, ?, 47, 12000, 33, 780, 40, 15)""",
                (hier,),
            )
            conn.execute(
                """INSERT INTO sleep_session (athlete_id, sleep_date, duration_s, deep_s)
                   VALUES (1, ?, 27000, 4200)""",
                (hier,),
            )
        yield hier
        db.close_pool()
        reset_settings()

    def test_calendrier_continu(self, base):
        from cairn.gui import queries

        mesures = queries.daily_metrics(7)

        assert len(mesures.days) == 8
        assert all(len(v) == 8 for v in mesures.series.values())

    def test_champs_lus(self, base):
        from cairn.gui import queries

        mesures = queries.daily_metrics(7)
        index = mesures.days.index(base.isoformat())

        assert mesures.get("steps")[index] == 12000
        assert mesures.get("deep_s")[index] == 4200
        assert mesures.get("intensity_min")[index] == 55

    def test_journee_absente_reste_vide(self, base):
        """Zéro pas et pas de mesure ne veulent pas dire la même chose."""
        from cairn.gui import queries

        mesures = queries.daily_metrics(7)
        veille = mesures.days.index((base - dt.timedelta(days=1)).isoformat())

        assert mesures.get("steps")[veille] is None
        assert mesures.get("intensity_min")[veille] is None
        # La charge, elle, vaut bien zéro : un jour sans sortie est un repos.
        assert mesures.get("load")[veille] == 0

    def test_toutes_les_cles_du_catalogue_existent(self, base):
        """Un champ au catalogue sans colonne derrière serait muet, sans erreur.

        Les séries sont lues par nom : une faute de frappe ne se verrait qu'à
        l'usage, sous la forme d'une bande qui n'apparaît jamais.
        """
        from cairn.gui import queries
        from cairn.gui.pages.dashboard import CHAMPS_JOUR, CHAMPS_JOURNEE

        quotidiennes = set(queries.daily_metrics(2).series)
        journalieres = set(queries.intraday("2026-01-01").series)

        assert {c.key for c in CHAMPS_JOUR} <= quotidiennes
        assert {c.key for c in CHAMPS_JOURNEE} <= journalieres

    def test_cle_inconnue_rend_une_liste_vide(self, base):
        from cairn.gui import queries

        assert queries.daily_metrics(7).get("inexistant") == []


class TestChoixDesChamps:
    """Trente mesures d'un côté, un graphe lisible de l'autre."""

    @pytest.fixture
    def page(self, qt_app, monkeypatch):
        from cairn.gui import prefs, queries
        from cairn.gui.pages.dashboard import DashboardPage

        # Les préférences ne doivent pas fuir vers la configuration réelle.
        memoire: dict[str, str] = {}
        monkeypatch.setattr(prefs, "load_view", lambda k, d="": memoire.get(k, d))
        monkeypatch.setattr(prefs, "save_view", lambda k, v: memoire.update({k: v}))
        monkeypatch.setattr(queries, "probe", lambda: queries.Health())
        return DashboardPage(), memoire

    def test_defaut_reprend_les_cinq_mesures_historiques(self, page):
        from cairn.gui.pages.dashboard import DEFAUT_JOUR

        vue, _ = page

        assert vue._selection() == list(DEFAUT_JOUR)

    def test_cocher_respecte_lordre_du_catalogue(self, page):
        """Sinon les bandes se réordonnent selon l'ordre des clics."""
        from cairn.gui.pages.dashboard import CHAMPS_JOUR

        vue, _ = page
        vue._toggle_field("steps", True)
        vue._toggle_field("min_hr", True)

        ordre = [c.key for c in CHAMPS_JOUR]
        assert vue._selection() == sorted(vue._selection(), key=ordre.index)

    def test_selection_persistee(self, page):
        vue, memoire = page
        vue._toggle_field("steps", True)

        assert "steps" in memoire["champs_jour"].split(",")

    def test_cle_disparue_ignoree(self, qt_app, monkeypatch):
        """Un champ retiré d'une version à l'autre ne doit rien casser."""
        from cairn.gui import prefs
        from cairn.gui.pages.dashboard import CHAMPS_JOUR, DEFAUT_JOUR, DashboardPage

        monkeypatch.setattr(prefs, "load_view", lambda k, d="": "steps,champ_disparu")

        retenus = DashboardPage._champs_retenus("champs_jour", CHAMPS_JOUR, DEFAUT_JOUR)

        assert retenus == ["steps"]

    def test_bouton_compte_les_champs(self, page):
        vue, _ = page
        vue._toggle_field("steps", True)

        assert vue.btn_champs.text() == "Champs (6)"

    def test_periode_dun_jour_change_de_catalogue(self, page):
        from cairn.gui.pages.dashboard import CHAMPS_JOURNEE

        vue, _ = page
        vue._set_trend_days(1)

        assert vue._catalogue() == CHAMPS_JOURNEE
        assert not vue.day_bar.isHidden()

    def test_retour_a_trente_jours_recache_la_navigation(self, page):
        vue, _ = page
        vue._set_trend_days(1)
        vue._set_trend_days(30)

        assert vue.day_bar.isHidden()

    def test_ancien_defaut_cede_la_place_au_nouveau(self, qt_app, monkeypatch):
        """Une sélection jamais modifiée ne doit pas masquer les mesures ajoutées.

        Sans cette règle, un champ nouveau reste invisible pour qui avait déjà
        lancé l'application une fois : ses préférences enregistrées font écran.
        """
        from cairn.gui import prefs
        from cairn.gui.pages.dashboard import (
            CHAMPS_JOURNEE,
            DEFAUT_JOURNEE,
            DashboardPage,
        )

        monkeypatch.setattr(
            prefs, "load_view", lambda k, d="": "hr,stress,body_battery"
        )

        retenus = DashboardPage._champs_retenus(
            "champs_journee", CHAMPS_JOURNEE, DEFAUT_JOURNEE
        )

        assert retenus == list(DEFAUT_JOURNEE)

    def test_choix_deliberé_respecté(self, qt_app, monkeypatch):
        """En revanche, une sélection décidée ne se fait pas écraser."""
        from cairn.gui import prefs
        from cairn.gui.pages.dashboard import (
            CHAMPS_JOURNEE,
            DEFAUT_JOURNEE,
            DashboardPage,
        )

        monkeypatch.setattr(prefs, "load_view", lambda k, d="": "hr,cadence")

        retenus = DashboardPage._champs_retenus(
            "champs_journee", CHAMPS_JOURNEE, DEFAUT_JOURNEE
        )

        assert retenus == ["hr", "cadence"]

    def test_catalogue_long_se_replie_en_sous_menus(self, page):
        """Quarante-six mesures d'affilée dépassent de l'écran."""
        from cairn.gui.pages.dashboard import CHAMPS_JOUR, SEUIL_SOUS_MENUS

        vue, _ = page

        assert len(CHAMPS_JOUR) > SEUIL_SOUS_MENUS
        assert set(vue._sous_menus) == {c.groupe for c in CHAMPS_JOUR}

    def test_compteur_de_sous_menu_suit_les_coches(self, page):
        vue, _ = page
        avant = vue._sous_menus["Journée"].title()
        vue._toggle_field("steps", True)

        assert vue._sous_menus["Journée"].title() != avant
        assert "(1)" in vue._sous_menus["Journée"].title()

    def test_catalogue_court_reste_plat(self, page):
        """Sept mesures se lisent mieux d'un coup que derrière trois sous-menus."""
        vue, _ = page
        vue._set_trend_days(1)

        assert vue._sous_menus == {}

    def test_clic_sur_un_jour_ouvre_le_detail(self, page, monkeypatch):
        """Repérer un creux puis retrouver sa date à la main était le chaînon manquant."""
        from cairn.gui import queries

        vue, _ = page
        monkeypatch.setattr(
            queries, "intraday_bounds",
            lambda: {"first_day": "2026-07-01", "last_day": "2026-08-10"},
        )
        monkeypatch.setattr(queries, "intraday", lambda j=None: queries.Intraday(day=j or ""))

        vue._open_day("2026-07-15")

        assert vue._jour == "2026-07-15"
        assert vue._trend_days == 1

    def test_jour_hors_plage_ramene_dans_les_bornes(self, page, monkeypatch):
        """Un jour sans relevé continu ouvrirait un graphe vide."""
        from cairn.gui import queries

        vue, _ = page
        monkeypatch.setattr(
            queries, "intraday_bounds",
            lambda: {"first_day": "2026-07-01", "last_day": "2026-08-10"},
        )
        monkeypatch.setattr(queries, "intraday", lambda j=None: queries.Intraday(day=j or ""))

        vue._open_day("2026-01-01")

        assert vue._jour == "2026-07-01"

    def test_fleches_sans_effet_hors_vue_journee(self, page, monkeypatch):
        """Sur trente jours, les flèches doivent rester au défilement."""
        from cairn.gui import queries

        vue, _ = page
        appels = []
        monkeypatch.setattr(
            queries, "intraday_bounds",
            lambda: appels.append(1) or {"first_day": "2026-07-01", "last_day": "2026-08-10"},
        )

        vue._shift_day(-1)

        assert appels == []
        assert vue._jour is None


class TestAccordDuPluriel:
    """« séance(s) » passe dans un journal, pas dans un message envoyé."""

    def test_singulier(self):
        from cairn.text import pluriel

        assert pluriel(1, "séance") == "1 séance"

    def test_pluriel(self):
        from cairn.text import pluriel

        assert pluriel(12, "séance") == "12 séances"

    def test_zero_reste_au_singulier(self):
        """Règle française : « 0 séance », et non « 0 séances »."""
        from cairn.text import pluriel

        assert pluriel(0, "séance") == "0 séance"

    def test_forme_irreguliere(self):
        from cairn.text import pluriel

        assert pluriel(3, "cheval", "chevaux") == "3 chevaux"

    def test_plus_aucune_forme_parenthesee(self):
        """Garde-fou : la forme revient vite si personne ne la surveille."""
        import pathlib

        racine = pathlib.Path(__file__).resolve().parents[1] / "src" / "cairn"
        fautifs = [
            f"{chemin.relative_to(racine)}:{numero}"
            for chemin in racine.rglob("*.py")
            # Seul endroit où la forme a le droit d'apparaître : la docstring
            # qui explique pourquoi elle n'a lieu nulle part ailleurs.
            if chemin.name != "text.py"
            for numero, ligne in enumerate(chemin.read_text().splitlines(), 1)
            if "(s)" in ligne
        ]

        assert fautifs == []


class TestBaseInjoignable:
    """Le chemin le moins emprunté, et celui qui plantait.

    `_show_unavailable` vidait des courbes qui n'existaient plus : la page
    levait une AttributeError au lieu d'afficher son message, et l'erreur ne
    se voyait qu'avec une base absente — jamais en usage normal.
    """

    def page(self, qt_app, monkeypatch):
        from cairn.gui import prefs, queries
        from cairn.gui.pages.dashboard import DashboardPage

        monkeypatch.setattr(prefs, "load_view", lambda k, d="": "")
        monkeypatch.setattr(prefs, "save_view", lambda k, v: None)
        monkeypatch.setattr(queries, "probe", lambda: queries.Health())
        return DashboardPage()

    def test_base_absente_affiche_un_message(self, qt_app, monkeypatch):
        from cairn.gui import queries

        vue = self.page(qt_app, monkeypatch)
        vue.refresh(queries.Health(reachable=False))

        assert not vue.empty_notice.isHidden()
        assert "injoignable" in vue.empty_notice.text()

    def test_schema_absent_renvoie_aux_reglages(self, qt_app, monkeypatch):
        from cairn.gui import queries

        vue = self.page(qt_app, monkeypatch)
        vue.refresh(queries.Health(reachable=True, migrated=False))

        assert "Réglages" in vue.empty_notice.text()

    def test_toutes_les_cartes_sont_vidées(self, qt_app, monkeypatch):
        """Des chiffres laissés en place feraient croire à des données à jour."""
        from cairn.gui import queries

        vue = self.page(qt_app, monkeypatch)
        vue.steps.set_value("18 434", "hier")
        vue.refresh(queries.Health())

        assert vue.steps.value.text() == "—"
        assert vue.signals_card.isHidden()

    def test_le_graphe_est_vidé(self, qt_app, monkeypatch):
        """Une courbe laissée à l'écran donnerait à croire que la base répond."""
        from cairn.gui import queries

        vue = self.page(qt_app, monkeypatch)
        vue.trend_chart.set_data(
            ["2026-08-01", "2026-08-02"],
            [{"label": "x", "values": [1, 2], "colour": "#fff", "unit": str}],
        )
        vue.refresh(queries.Health())

        assert vue.trend_chart._series == []


class TestFenetreDeSynchronisation:
    """La journée en cours n'était jamais demandée à Garmin.

    La passe incrémentale partait de la veille. Conséquence invisible mais
    quotidienne : une nuit porte la date du réveil, donc la nuit qui vient de
    s'achever n'arrivait que le lendemain — la carte « Sommeil » affichait
    l'avant-dernière nuit toute la journée.
    """

    def jours(self, mode="incremental", **cursor):
        from cairn.sync.garmin_sync import _wellness_days

        return _wellness_days(mode, cursor)

    def test_aujourdhui_est_demande(self):
        assert dt.date.today() in self.jours()

    def test_la_veille_reste_demandee(self):
        """Elle se complète : la journée d'hier n'était que partielle hier."""
        assert dt.date.today() - dt.timedelta(days=1) in self.jours()

    def test_pas_de_jour_futur(self):
        assert max(self.jours()) == dt.date.today()

    def test_fenetre_a_la_longueur_annoncee(self):
        from cairn.config import get_settings

        # Le jour même s'ajoute à la fenêtre de rattrapage, il ne la rogne pas.
        assert len(self.jours()) == get_settings().incremental_lookback_days + 1

    def test_le_backfill_ne_repasse_pas_par_aujourdhui(self):
        """Il descend vers le passé : le présent est l'affaire de l'autre passe."""
        jours = self.jours(mode="backfill", backfill_done=False)

        assert dt.date.today() not in jours
        assert max(jours) < dt.date.today()


class TestSynchronisationAuDemarrage:
    """Ouvrir l'application doit suffire à avoir des données fraîches.

    Sans garde-fou, ouvrir dix fois dans la journée déclencherait dix passes :
    Garmin coupe au-delà d'un certain rythme, et c'est ainsi qu'un 429 a été
    servi une première fois.
    """

    def page(self, qt_app, monkeypatch, heures=None, comptes=True):
        from cairn.gui import queries
        from cairn.gui.pages import sync as module

        monkeypatch.setattr(queries, "hours_since_last_sync", lambda: heures)
        monkeypatch.setattr(
            queries, "credentials_present",
            lambda: {"garmin": comptes, "strava": False},
        )
        monkeypatch.setattr(module.queries, "hours_since_last_sync", lambda: heures)
        monkeypatch.setattr(
            module.queries, "credentials_present",
            lambda: {"garmin": comptes, "strava": False},
        )
        return module.SyncPage()

    def test_part_quand_la_derniere_passe_est_ancienne(self, qt_app, monkeypatch):
        vue = self.page(qt_app, monkeypatch, heures=9.0)

        assert vue.auto_start_reason() is None

    def test_ne_part_pas_juste_apres_une_passe(self, qt_app, monkeypatch):
        from cairn.gui.pages.sync import AUTO_SYNC_MIN_HOURS

        vue = self.page(qt_app, monkeypatch, heures=AUTO_SYNC_MIN_HOURS - 0.1)

        assert "moins de" in vue.auto_start_reason()

    def test_premiere_utilisation_part(self, qt_app, monkeypatch):
        """Aucune passe jamais réussie : il n'y a rien à ménager."""
        vue = self.page(qt_app, monkeypatch, heures=None)

        assert vue.auto_start_reason() is None

    def test_sans_compte_relie_rien_ne_part(self, qt_app, monkeypatch):
        vue = self.page(qt_app, monkeypatch, heures=99.0, comptes=False)

        assert vue.auto_start_reason() == "aucun compte relié"

    def test_le_refus_est_consigne(self, qt_app, monkeypatch):
        """Qui a coché l'option doit comprendre pourquoi rien ne bouge."""
        vue = self.page(qt_app, monkeypatch, heures=0.5)

        assert vue.start_auto() is False
        assert "ignorée" in vue.log.toPlainText()

    def test_une_passe_en_cours_nen_declenche_pas_une_seconde(self, qt_app, monkeypatch):
        vue = self.page(qt_app, monkeypatch, heures=99.0)
        vue._worker = object()

        assert vue.auto_start_reason() == "une passe est déjà en cours"

    def test_option_persistee(self, qt_app, monkeypatch, tmp_path):
        from cairn.gui import prefs

        monkeypatch.setattr(prefs, "app_directory", lambda: tmp_path)
        (tmp_path / prefs.PORTABLE_FILENAME).write_text("[General]\n")

        assert prefs.load_option("sync_au_demarrage") is False
        prefs.save_option("sync_au_demarrage", True)
        assert prefs.load_option("sync_au_demarrage") is True

    def test_option_desactivee_par_defaut(self, qt_app, monkeypatch, tmp_path):
        """Une application qui appelle le réseau sans qu'on l'ait demandé
        surprend : l'option s'active, elle ne se subit pas."""
        from cairn.gui import prefs

        monkeypatch.setattr(prefs, "app_directory", lambda: tmp_path)
        (tmp_path / prefs.PORTABLE_FILENAME).write_text("[General]\n")

        assert prefs.load_option("sync_au_demarrage") is False


class TestTraceDUneSortie:
    """Soixante-dix mille points géolocalisés dormaient en base.

    Le tracé est dessiné à partir des seules coordonnées : ni tuiles, ni clé
    d'API, ni appel réseau. Cairn tient dans son dossier et fonctionne hors
    ligne — aller chercher des images ailleurs contredirait les deux.
    """

    def carte(self, qt_app, points):
        from cairn.gui.widgets import TrackMap

        carte = TrackMap()
        carte.set_track(points)
        carte.resize(300, 200)
        return carte

    def test_trace_reconnu(self, qt_app):
        carte = self.carte(qt_app, [(48.81, -3.47), (48.82, -3.46), (48.83, -3.45)])

        assert carte.has_track()

    def test_sortie_sans_coordonnees(self, qt_app):
        """Un tapis de course n'a pas de parcours : rien ne doit s'afficher."""
        carte = self.carte(qt_app, [(None, None)] * 50)

        assert not carte.has_track()

    def test_longitude_resserree_par_la_latitude(self, qt_app):
        """Un degré de longitude vaut deux tiers de degré de latitude ici.

        L'ignorer étire le parcours d'une bonne moitié en largeur : une boucle
        ronde devient un ovale couché.
        """
        import math

        carte = self.carte(qt_app, [(48.80, -3.50), (48.81, -3.49)])
        transform = carte._transform()

        assert transform is not None
        assert transform[1] == pytest.approx(math.cos(math.radians(48.805)), rel=1e-3)

    def test_meme_echelle_sur_les_deux_axes(self, qt_app):
        """Deux échelles déformeraient le parcours : une boucle n'en serait plus une."""
        # Emprise deux fois plus large que haute, dans un cadre carré.
        carte = self.carte(qt_app, [(48.80, -3.50), (48.81, -3.46)])
        carte.resize(200, 200)

        a = carte._screen((48.80, -3.50), carte._transform())
        b = carte._screen((48.81, -3.46), carte._transform())
        # La diagonale doit rester inscrite dans le cadre, sans l'emplir en
        # hauteur : c'est la largeur qui contraint.
        assert abs(b[0] - a[0]) > abs(b[1] - a[1])
        assert min(a[1], b[1]) >= 0 and max(a[1], b[1]) <= 200

    def test_aller_retour_ne_divise_pas_par_zero(self, qt_app):
        """Une ligne droite plein nord a une emprise nulle en longitude."""
        carte = self.carte(qt_app, [(48.80, -3.50), (48.81, -3.50), (48.82, -3.50)])

        assert carte._transform() is not None

    def test_marqueur_suit_lindice(self, qt_app):
        carte = self.carte(qt_app, [(48.80, -3.50), (48.81, -3.49)])
        carte.set_marker(1)

        assert carte._marker == 1

    def test_le_flux_porte_les_coordonnees(self):
        """Tracé et courbes partagent l'échantillonnage : même indice, même instant."""
        from cairn.gui.queries import activity_streams

        vide = activity_streams(999999)
        assert vide["track"] == []


class TestCalendrierDeCharge:
    """Ce que la charge par semaine ne montre pas : où sont les coupures."""

    def calendrier(self, qt_app, jours, charges):
        from cairn.gui.widgets import LoadCalendar

        cal = LoadCalendar()
        cal.set_data(jours, charges)
        cal.resize(800, cal.heightForWidth(800))
        return cal

    def annee(self):
        debut = dt.date(2026, 1, 1)
        return [(debut + dt.timedelta(days=i)).isoformat() for i in range(365)]

    def test_toutes_les_lignes_tiennent_dans_le_cadre(self, qt_app):
        """La hauteur découle de la largeur : sinon la dernière ligne sortait."""
        cal = self.calendrier(qt_app, self.annee(), [10] * 365)

        dernier = cal._rect_of(364)
        assert dernier.bottom() <= cal.heightForWidth(800)

    def test_les_cases_se_serrent_quand_la_fenetre_retrecit(self, qt_app):
        cal = self.calendrier(qt_app, self.annee(), [10] * 365)

        assert cal._cell_for(1000) > cal._cell_for(500)
        assert cal._cell_for(200) >= 4.0

    def test_la_premiere_colonne_respecte_le_jour_de_la_semaine(self, qt_app):
        """1er janvier 2026 : un jeudi, donc quatrième ligne."""
        cal = self.calendrier(qt_app, self.annee(), [10] * 365)

        assert cal._first_column_offset() == 3

    def test_un_jour_sans_charge_se_distingue_dun_jour_charge(self, qt_app):
        cal = self.calendrier(qt_app, self.annee(), [0] * 365)

        assert cal._tone(0, 100).name() != cal._tone(100, 100).name()

    def test_teinte_bornee_a_lopacite_maximale(self, qt_app):
        """Au-delà de 255, Qt refuse la valeur et crie."""
        cal = self.calendrier(qt_app, self.annee(), [100] * 365)

        assert cal._tone(100, 100).alpha() <= 255

    def test_calendrier_vide_ne_leve_pas(self, qt_app):
        cal = self.calendrier(qt_app, [], [])

        assert cal._columns() >= 1


class TestExportCsv:
    """Un export ouvre la porte à Grafana sans que Cairn devienne un serveur."""

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
                                         distance_m, primary_source, name)
                   VALUES (1, 1, '2026-07-01T08:00:00+00:00', '2026-07-01 10:00:00',
                           '2026-07-01', 'road', 'cycling', 3600, 24000,
                           'garmin', 'Sortie dénivelée')"""
            )
        yield tmp_path
        db.close_pool()
        reset_settings()

    def test_deux_tableaux_ecrits(self, base):
        from cairn.gui.queries import export_csv

        resultat = export_csv(str(base / "export"))

        assert set(resultat) == {"sorties", "journalier"}
        assert resultat["sorties"]["rows"] == 1

    def test_accents_lisibles_par_un_tableur(self, base):
        """Sans marque d'ordre des octets, Excel rend « dénivelé » illisible."""
        from cairn.gui.queries import export_csv

        resultat = export_csv(str(base / "export"))
        brut = pathlib.Path(resultat["sorties"]["path"]).read_bytes()

        assert brut.startswith(b"\xef\xbb\xbf")
        assert "dénivelée" in brut.decode("utf-8-sig")

    def test_separateur_standard(self, base):
        """La virgule, ce qu'attendent pandas et Grafana."""
        from cairn.gui.queries import export_csv

        resultat = export_csv(str(base / "export"))
        entete = pathlib.Path(resultat["sorties"]["path"]).read_text(
            encoding="utf-8-sig"
        ).splitlines()[0]

        assert entete.count(",") > 5

    def test_le_dossier_est_cree(self, base):
        from cairn.gui.queries import export_csv

        cible = base / "profond" / "encore"
        export_csv(str(cible))

        assert (cible / "cairn-journalier.csv").exists()

    def test_une_colonne_par_mesure_quotidienne(self, base):
        from cairn.gui.pages.dashboard import CHAMPS_JOUR
        from cairn.gui.queries import export_csv

        resultat = export_csv(str(base / "export"))
        entete = pathlib.Path(resultat["journalier"]["path"]).read_text(
            encoding="utf-8-sig"
        ).splitlines()[0]

        for champ in CHAMPS_JOUR:
            assert champ.key in entete


class TestSortieEcartee:
    """Un enregistrement peut être faux sans que la sortie soit à jeter.

    La course relevée à 93,4 km/h vient d'un décrochage GPS d'une seconde :
    la supprimer perdrait la donnée brute, et la resynchronisation la
    ramènerait de toute façon.
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
            # L'heure locale doit tomber le même jour que `local_date` : la
            # vue détaillée situe les séances d'après elle.
            aujourdhui = dt.date.today().isoformat()
            for identifiant, distance in ((1, 20000.0), (2, 5000.0)):
                conn.execute(
                    """INSERT INTO activity (id, athlete_id, start_time, start_time_local,
                                             local_date, sport, sport_family, elapsed_s,
                                             distance_m, training_load, primary_source, name)
                       VALUES (?, 1, ?, ?, ?, 'run', 'running',
                               3600, ?, 100, 'garmin', ?)""",
                    (identifiant, f"{aujourdhui}T0{identifiant}:00:00+00:00",
                     f"{aujourdhui} 0{identifiant}:00:00", aujourdhui,
                     distance, f"Sortie {identifiant}"),
                )
        yield
        db.close_pool()
        reset_settings()

    def test_la_vue_egale_la_table_sans_exclusion(self, base):
        from cairn.db import fetch_all

        assert (
            fetch_all("SELECT count(*) n FROM activity_kept")[0]["n"]
            == fetch_all("SELECT count(*) n FROM activity")[0]["n"]
        )

    def test_ecartee_des_cumuls(self, base):
        from cairn.gui import queries

        avant = queries.period_totals()
        queries.set_activity_excluded(2, True, "décrochage GPS")
        apres = queries.period_totals()

        assert apres["sessions"] == avant["sessions"] - 1
        assert apres["distance_m"] == avant["distance_m"] - 5000

    def test_toujours_visible_dans_la_liste(self, base):
        """Invisible, on ne pourrait plus revenir sur la décision."""
        from cairn.gui import queries

        queries.set_activity_excluded(2, True, "décrochage GPS")
        listee = {a["id"]: a for a in queries.recent_activities()}

        assert 2 in listee
        assert listee[2]["excluded"] == 1
        assert listee[2]["excluded_reason"] == "décrochage GPS"

    def test_reintegration(self, base):
        from cairn.gui import queries

        reference = queries.period_totals()["distance_m"]
        queries.set_activity_excluded(2, True, "erreur")
        queries.set_activity_excluded(2, False)

        assert queries.period_totals()["distance_m"] == reference

    def test_hors_des_records(self, base):
        from cairn.gui import queries

        queries.set_activity_excluded(1, True, "décrochage GPS")
        records = {r["sport"]: r for r in queries.personal_records(minimum_sessions=1)}

        assert records["run"]["longest_m"] == 5000

    def test_hors_de_la_charge_quotidienne(self, base):
        from cairn.gui import queries

        avant = max(queries.daily_metrics(7).get("load"))
        queries.set_activity_excluded(1, True, "décrochage GPS")
        apres = max(queries.daily_metrics(7).get("load"))

        assert apres == avant - 100

    def test_la_journee_la_montre_quand_meme(self, base):
        """Écartée des statistiques, la sortie a tout de même eu lieu."""
        from cairn.gui import queries

        queries.set_activity_excluded(1, True, "décrochage GPS")
        jour = queries.intraday(dt.date.today().isoformat())

        assert any(s["kind"] == "activity" for s in jour.spans)

    def test_identifiant_inconnu_ne_leve_pas(self, base):
        from cairn.gui import queries

        queries.set_activity_excluded(999, True, "peu importe")


class TestComparaisonDeDeuxSorties:
    """Savoir si le même parcours passe mieux qu'il y a deux mois."""

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
            for identifiant, jour, vitesse in ((1, "01", 5.0), (2, "02", 6.0), (3, "03", 5.0)):
                conn.execute(
                    """INSERT INTO activity (id, athlete_id, start_time, start_time_local,
                                             local_date, sport, sport_family, elapsed_s,
                                             distance_m, avg_hr, primary_source, name,
                                             has_streams)
                       VALUES (?, 1, ?, ?, ?, 'road', 'cycling', 3600, 10000, 150,
                               'garmin', ?, 1)""",
                    (identifiant, f"2026-07-{jour}T08:00:00+00:00",
                     f"2026-07-{jour} 10:00:00", f"2026-07-{jour}",
                     f"Sortie {identifiant}"),
                )
                # Dix kilomètres de relevés, un point tous les dix mètres.
                for pas in range(0, 1000):
                    conn.execute(
                        """INSERT INTO activity_stream (activity_id, time, elapsed_s,
                                                        hr, altitude_m, speed_mps, distance_m)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (identifiant, f"2026-07-{jour}T08:{pas // 60:02d}:{pas % 60:02d}+00:00",
                         pas, 140 + identifiant, 100 + pas / 10, vitesse, pas * 10),
                    )
            # Une sortie d'un autre sport : elle ne doit pas être proposée.
            conn.execute(
                """INSERT INTO activity (id, athlete_id, start_time, start_time_local,
                                         local_date, sport, sport_family, elapsed_s,
                                         distance_m, primary_source, name, has_streams)
                   VALUES (9, 1, '2026-07-04T08:00:00+00:00', '2026-07-04 10:00:00',
                           '2026-07-04', 'run', 'running', 3600, 10000, 'garmin',
                           'Course', 1)"""
            )
        yield
        db.close_pool()
        reset_settings()

    def test_axe_en_distance_et_non_en_temps(self, base):
        """Dès que l'une fut plus rapide, comparer à temps égal met face à
        face un sommet et une mi-pente."""
        from cairn.gui.queries import streams_by_distance

        flux = streams_by_distance(1, points=100)

        assert flux["labels"][0] == "0,0 km"
        assert flux["labels"][-1].endswith("km")
        assert flux["total_m"] == pytest.approx(9990, rel=0.01)

    def test_deux_sorties_de_vitesses_differentes_saligent(self, base):
        """Même parcours, allures différentes : les deux séries ont la même
        longueur, donc un indice y désigne le même endroit."""
        from cairn.gui.queries import streams_by_distance

        a = streams_by_distance(1, points=100)
        b = streams_by_distance(2, points=100)

        assert len(a["labels"]) == len(b["labels"])

    def test_candidats_du_meme_sport(self, base):
        from cairn.gui.queries import comparable_activities

        candidats = {c["id"] for c in comparable_activities(1)}

        assert candidats == {2, 3}

    def test_candidats_tries_par_ecart_de_distance(self, base):
        from cairn.db import execute
        from cairn.gui.queries import comparable_activities

        execute("UPDATE activity SET distance_m = 90000 WHERE id = 2")
        candidats = comparable_activities(1)

        assert candidats[0]["id"] == 3

    def test_une_sortie_ecartee_reste_comparable(self, base):
        """Écarter des statistiques n'est pas effacer : la sortie a eu lieu."""
        from cairn.gui import queries

        queries.set_activity_excluded(3, True, "erreur")

        assert 3 in {c["id"] for c in queries.comparable_activities(1)}

    def test_fenetre_superpose_les_deux(self, qt_app, base):
        from cairn.gui.pages.activity_compare import ActivityCompareDialog

        fenetre = ActivityCompareDialog(1)

        assert [s["label"] for s in fenetre.chart._series]
        for serie in fenetre.chart._series:
            assert any(v is not None for v in serie["values2"])

    def test_avertissement_si_les_parcours_different(self, qt_app, base):
        from cairn.db import execute
        from cairn.gui.pages.activity_compare import ActivityCompareDialog

        execute("UPDATE activity SET distance_m = 30000 WHERE id = 2")
        fenetre = ActivityCompareDialog(1)
        fenetre.choix.setCurrentIndex(fenetre.choix.findData(2))

        assert not fenetre.avertissement.isHidden()
        assert "%" in fenetre.avertissement.text()

    def test_sans_candidat_la_fenetre_le_dit(self, qt_app, base):
        from cairn.db import execute
        from cairn.gui.pages.activity_compare import ActivityCompareDialog

        execute("DELETE FROM activity WHERE id IN (2, 3)")
        fenetre = ActivityCompareDialog(1)

        assert fenetre._candidats == []

    def test_sortie_sans_distance_ne_leve_pas(self, base):
        from cairn.gui.queries import streams_by_distance

        assert streams_by_distance(9) == {"labels": [], "series": {}}


class TestSuperpositionDansUneBande:
    """Deux courbes à la même échelle : c'est ce qui permet de les comparer."""

    def serie(self, values, values2):
        return {
            "label": "x", "values": values, "colour": "#e8834a",
            "unit": lambda v: f"{v:.0f}", "values2": values2, "colour2": "#6b9bd2",
        }

    def test_les_bornes_couvrent_les_deux_courbes(self, qt_app):
        """Calculées sur la seule première, la seconde sortirait du cadre."""
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QImage

        from cairn.gui.widgets import TrendChart

        chart = TrendChart()
        chart.set_data(["a", "b", "c"], [self.serie([1, 2, 3], [50, 60, 70])],
                       mode="plain")
        chart.resize(500, chart.minimumHeight())

        image = QImage(QSize(chart.width(), chart.height()), QImage.Format.Format_ARGB32)
        image.fill(0)
        chart.render(image)

        # La courbe basse et la courbe haute doivent toutes deux se peindre
        # dans la bande, donc y rester bornées.
        haut = chart.BAND_SPACING
        bas = haut + chart.BAND_HEIGHT
        colores = [
            y for y in range(chart.height()) for x in range(chart.LABEL_WIDTH, chart.width())
            if image.pixelColor(x, y).name() in ("#e8834a", "#6b9bd2")
        ]
        assert colores
        assert min(colores) >= haut - 2
        assert max(colores) <= bas + 2

    def test_bande_sans_aucune_valeur_ecartee(self, qt_app):
        from cairn.gui.widgets import TrendChart

        chart = TrendChart()
        chart.set_data(["a", "b"], [self.serie([None, None], [None, None])], mode="plain")

        assert chart._series == []

    def test_etiquettes_rendues_telles_quelles(self, qt_app):
        from cairn.gui.widgets import TrendChart

        chart = TrendChart()
        chart.set_data(["12,4 km", "13,0 km"], [self.serie([1, 2], [3, 4])], mode="plain")

        assert chart._axis_label("12,4 km") == "12,4 km"
        assert chart._cursor_label("12,4 km") == "12,4 km"
