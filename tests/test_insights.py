"""Tests du résumé hebdomadaire : garde-fou sur les chiffres, prompt, repli."""

import datetime as dt

from cairn.insights.discord import MAX_CONTENT, format_message
from cairn.insights.facts import (
    HRV_SUPPRESSED_DAYS,
    LOAD_SURGE_PCT,
    WeeklyFacts,
    _detect_signals,
    last_complete_week,
)
from cairn.insights.ollama import THINK_BLOCK, verify_numbers
from cairn.insights.weekly import (
    allowed_numbers,
    fallback_summary,
    render_prompt,
)

START = dt.date(2026, 8, 3)
END = dt.date(2026, 8, 9)


def facts(**overrides) -> WeeklyFacts:
    base = WeeklyFacts(start=START, end=END)
    base.sessions = 5
    base.duration_s = 6 * 3600 + 1200
    base.distance_m = 142000.0
    base.elevation_m = 3450.0
    base.load = 612.0
    base.previous_load = 430.0
    base.hrv_mean = 62.0
    base.hrv_baseline = 68.0
    base.rhr_mean = 51.0
    base.rhr_baseline = 48.0
    base.sleep_mean_score = 74.0
    base.sleep_mean_duration_s = 25200.0
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


class TestVerifyNumbers:
    def test_accepte_les_chiffres_fournis(self):
        assert verify_numbers("Charge de 612 cette semaine.", [612.0]) == []

    def test_rejette_un_chiffre_invente(self):
        rejected = verify_numbers("Tu as roulé 999 km.", [142.0, 612.0])
        assert rejected == ["999"]

    def test_tolere_les_arrondis(self):
        """« 6 h » pour 6,2 heures est une reformulation, pas une invention."""
        assert verify_numbers("Environ 6 h de sport.", [6.33]) == []

    def test_petits_nombres_toleres(self):
        """Compter des séances ou des jours ne demande aucun fait à l'appui."""
        assert verify_numbers("3 séances sur 7 jours.", []) == []

    def test_seuil_des_petits_nombres(self):
        assert verify_numbers("13 séances.", []) == ["13"]

    def test_virgule_decimale_francaise(self):
        assert verify_numbers("Tu as fait 62,5 km.", [62.5]) == []

    def test_plusieurs_inventions(self):
        rejected = verify_numbers("450 W pendant 88 minutes.", [612.0])
        assert set(rejected) == {"450", "88"}

    def test_texte_sans_chiffre(self):
        assert verify_numbers("Semaine solide, continue ainsi.", []) == []

    def test_les_dates_ne_sont_pas_des_affirmations(self):
        """« semaine du 15/08 au 21/08 » ne contient aucune donnée à vérifier."""
        assert verify_numbers("Semaine du 15/08 au 21/08.", []) == []

    def test_les_minutes_d_une_duree_sont_ignorees(self):
        """Dans « 6 h 20 », seule l'heure est une valeur à justifier."""
        assert verify_numbers("Tu as cumulé 6 h 20 de sport.", [6.33]) == []

    def test_l_heure_d_une_duree_reste_verifiee(self):
        assert verify_numbers("Tu as cumulé 47 h 20 de sport.", [6.33]) == ["47"]


class TestThinkBlock:
    def test_supprime_la_reflexion_du_modele(self):
        raw = "<think>Je dois calculer...</think>\nSemaine chargée."
        assert THINK_BLOCK.sub("", raw).strip() == "Semaine chargée."

    def test_multiligne_et_insensible_a_la_casse(self):
        raw = "<THINK>\nligne 1\nligne 2\n</THINK>Texte final."
        assert THINK_BLOCK.sub("", raw).strip() == "Texte final."

    def test_texte_sans_balise_intact(self):
        assert THINK_BLOCK.sub("", "Rien à retirer.") == "Rien à retirer."


class TestAllowedNumbers:
    def test_contient_les_valeurs_du_bilan(self):
        allowed = allowed_numbers(facts())
        for expected in (5, 612.0, 430.0, 62.0, 68.0, 51.0, 48.0, 74.0, 3450.0):
            assert expected in allowed

    def test_contient_les_derivees(self):
        """Le pourcentage de variation n'est nulle part en base : il se calcule."""
        allowed = allowed_numbers(facts())
        change = facts().load_change_pct
        assert change is not None
        assert any(abs(v - change) < 0.01 for v in allowed)

    def test_resiste_a_un_bilan_vide(self):
        assert allowed_numbers(WeeklyFacts(start=START, end=END))

    def test_le_resume_de_repli_passe_son_propre_controle(self):
        """Le texte de secours ne doit jamais contenir de chiffre non fondé."""
        data = facts()
        assert verify_numbers(fallback_summary(data), allowed_numbers(data)) == []


class TestSignals:
    def test_hausse_de_charge(self):
        data = facts(load=1000.0, previous_load=400.0)
        _detect_signals(data)
        assert any("augmenté" in s for s in data.signals)

    def test_baisse_de_charge(self):
        data = facts(load=100.0, previous_load=600.0)
        _detect_signals(data)
        assert any("baissé" in s for s in data.signals)

    def test_charge_stable_sans_signal(self):
        data = facts(load=440.0, previous_load=430.0)
        _detect_signals(data)
        assert not any("charge" in s for s in data.signals)

    def test_vfc_basse_plusieurs_jours(self):
        data = facts(hrv_days_below=HRV_SUPPRESSED_DAYS)
        _detect_signals(data)
        assert any("variabilité" in s for s in data.signals)

    def test_surcharge_combinee(self):
        """Charge en hausse et récupération en baisse : le signal qui compte."""
        data = facts(load=1000.0, previous_load=400.0, hrv_days_below=4)
        _detect_signals(data)
        assert any("surcharge" in s for s in data.signals)

    def test_fc_repos_elevee(self):
        data = facts(rhr_mean=55.0, rhr_baseline=48.0)
        _detect_signals(data)
        assert any("repos" in s for s in data.signals)

    def test_pas_de_signal_sans_semaine_precedente(self):
        data = facts(previous_load=0.0, hrv_days_below=0,
                     rhr_mean=48.0, rhr_baseline=48.0, short_nights=0)
        _detect_signals(data)
        assert data.signals == []

    def test_seuil_de_hausse(self):
        juste_en_dessous = facts(previous_load=100.0, load=100.0 + LOAD_SURGE_PCT - 1)
        _detect_signals(juste_en_dessous)
        assert not any("augmenté" in s for s in juste_en_dessous.signals)


class TestPrompt:
    def test_contient_les_chiffres_cles(self):
        text = render_prompt(facts())
        assert "612" in text
        assert "142 km" in text
        assert "5 séance(s)" in text

    def test_mentionne_la_ligne_de_base(self):
        text = render_prompt(facts())
        assert "habitude" in text

    def test_signale_les_alertes(self):
        data = facts()
        data.signals = ["la charge a augmenté de 42 %"]
        assert "À SIGNALER" in render_prompt(data)

    def test_bilan_vide_reste_lisible(self):
        text = render_prompt(WeeklyFacts(start=START, end=END))
        assert "0 séance(s)" in text


class TestFallback:
    def test_sans_donnees(self):
        text = fallback_summary(WeeklyFacts(start=START, end=END))
        assert "Aucune donnée" in text

    def test_mentionne_les_signaux(self):
        data = facts()
        data.signals = ["la variabilité cardiaque est sous sa ligne de base depuis 4 jours"]
        text = fallback_summary(data)
        assert "À signaler" in text
        assert "récupération" in text

    def test_sans_signal_le_dit(self):
        assert "Aucun signal" in fallback_summary(facts(signals=[]))


class TestLastCompleteWeek:
    def test_renvoie_lundi_a_dimanche(self):
        start, end = last_complete_week(dt.date(2026, 8, 12))  # un mercredi
        assert start.weekday() == 0
        assert end.weekday() == 6
        assert (end - start).days == 6

    def test_semaine_precedente_pas_la_courante(self):
        today = dt.date(2026, 8, 12)
        start, end = last_complete_week(today)
        assert end < today

    def test_un_lundi(self):
        start, end = last_complete_week(dt.date(2026, 8, 10))
        assert start == dt.date(2026, 8, 3)
        assert end == dt.date(2026, 8, 9)


class TestDiscord:
    def test_entete_avec_les_dates(self):
        message = format_message("Texte.", START, END, "model")
        assert "03/08" in message and "09/08" in message

    def test_signale_une_redaction_sans_modele(self):
        message = format_message("Texte.", START, END, "fallback")
        assert "sans modèle" in message

    def test_pas_de_mention_quand_le_modele_a_ecrit(self):
        message = format_message("Texte.", START, END, "model")
        assert "sans modèle" not in message

    def test_tronque_les_messages_trop_longs(self):
        message = format_message("x" * 5000, START, END, "model")
        assert len(message) < MAX_CONTENT + 200
        assert message.endswith("…")
