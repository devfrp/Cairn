"""Tests du résumé hebdomadaire : garde-fou sur les chiffres, prompt, repli."""

import datetime as dt

import pytest

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
        """Une progression trop rapide reste un signal — sans son chiffre.

        Le pourcentage figure déjà dans le récit ; le répéter ici l'affichait
        deux fois à une phrase d'intervalle.
        """
        data = facts(load=1000.0, previous_load=400.0)
        _detect_signals(data)
        assert any("progression" in s for s in data.signals)
        assert not any("150" in s for s in data.signals)

    def test_baisse_de_charge_nest_pas_un_signal(self):
        """Une baisse n'appelle aucune action.

        La faire figurer parmi les signaux déclenchait un conseil d'alléger la
        semaine — alors qu'elle venait justement de l'être.
        """
        data = facts(load=100.0, previous_load=600.0)
        _detect_signals(data)
        # D'autres signaux peuvent exister (la fixture a une FC élevée) :
        # c'est l'absence de signal *de charge* qui est vérifiée ici.
        assert not any("charge" in s or "baiss" in s for s in data.signals)

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
        assert "5 séances" in text

    def test_mentionne_la_ligne_de_base(self):
        text = render_prompt(facts())
        assert "habitude" in text

    def test_signale_les_alertes(self):
        data = facts()
        data.signals = ["la charge a augmenté de 42 %"]
        assert "À SIGNALER" in render_prompt(data)

    def test_bilan_vide_reste_lisible(self):
        text = render_prompt(WeeklyFacts(start=START, end=END))
        # Zéro prend le singulier en français.
        assert "0 séance," in text


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


class TestConseilCoherentAvecLaCharge:
    """Le conseil doit suivre le sens de la variation.

    Une baisse de charge figurait parmi les signaux, ce qui déclenchait un
    « une semaine allégée paraît indiquée » — alors que la semaine venait
    justement de l'être. Le pourcentage était en prime annoncé deux fois, dans
    deux phrases consécutives.
    """

    def facts(self, change_pct):
        import datetime as dt

        from cairn.insights.facts import WeeklyFacts

        f = WeeklyFacts(start=dt.date(2026, 8, 3), end=dt.date(2026, 8, 9))
        f.sessions = 12
        f.duration_s = 28260
        f.distance_m = 57000
        f.elevation_m = 610
        f.load = 463
        f.previous_load = 463 / (1 + change_pct / 100)
        return f

    def test_baisse_nest_pas_un_signal(self):
        from cairn.insights.facts import _detect_signals

        f = self.facts(-36)
        _detect_signals(f)

        assert f.signals == [], "une baisse n'appelle aucune action"

    def test_hausse_signalee_sans_repeter_le_chiffre(self):
        from cairn.insights.facts import _detect_signals

        f = self.facts(45)
        _detect_signals(f)

        assert f.signals, "une progression trop rapide reste un signal"
        # Le récit donne déjà « en hausse de 45 % » : le redire ici affichait
        # deux fois le même nombre à une phrase d'intervalle.
        assert "45" not in " ".join(f.signals)

    def test_texte_sans_conseil_apres_une_baisse(self):
        from cairn.insights.facts import _detect_signals
        from cairn.insights.weekly import fallback_summary

        f = self.facts(-36)
        _detect_signals(f)
        texte = fallback_summary(f)

        assert "en baisse de 36 %" in texte
        assert "allégée" not in texte, "conseiller d'alléger une semaine déjà allégée"
        assert texte.count("36 %") == 1, "le même chiffre deux fois"


class TestModeleNonSollicitéParDefaut:
    """Le modèle embarqué invente des liens que le garde-fou ne voit pas.

    Éprouvé sur des données réelles : « une baisse de charge de 36 %, ce qui
    indique une augmentation du rythme cardiaque ». Les chiffres sont exacts,
    c'est ce qu'on leur fait dire qui est faux. Le texte déterministe, moins
    fluide, ne se trompe jamais.
    """

    def test_defaut_deterministe(self, monkeypatch, tmp_path):
        from cairn import db, migrate
        from cairn.config import reset_settings

        monkeypatch.setenv("CAIRN_DB_PATH", str(tmp_path / "cairn.db"))
        monkeypatch.delenv("CAIRN_SUMMARY_USE_MODEL", raising=False)
        reset_settings()
        db.close_pool()
        migrate.upgrade()

        from cairn.insights import weekly

        resultat = weekly.generate()

        assert resultat["source"] == "fallback"
        db.close_pool()
        reset_settings()

    def test_consigne_interdit_le_raisonnement(self):
        from cairn.insights.weekly import SYSTEM_PROMPT

        assert "n'établis aucun lien" in SYSTEM_PROMPT
        assert "aucun conseil de ton cru" in SYSTEM_PROMPT


class TestEnvoiDiscord:
    """Le seul chemin de Cairn vers l'extérieur, jamais couvert jusqu'ici."""

    def test_sans_webhook_le_dit(self):
        from cairn.insights.discord import DiscordUnavailable, send

        with pytest.raises(DiscordUnavailable, match="Aucun webhook"):
            send("", "peu importe")

    def test_refus_de_discord_remonte(self, respx_mock):
        import httpx

        from cairn.insights.discord import DiscordUnavailable, send

        respx_mock.post("https://discord.test/hook").mock(
            return_value=httpx.Response(404)
        )

        with pytest.raises(DiscordUnavailable, match="refusé"):
            send("https://discord.test/hook", "salut")

    def test_message_sans_mention(self, respx_mock):
        """Un résumé ne doit réveiller personne : pas de @everyone possible."""
        import httpx

        from cairn.insights.discord import send

        route = respx_mock.post("https://discord.test/hook").mock(
            return_value=httpx.Response(204)
        )

        send("https://discord.test/hook", "@everyone coucou")

        import json

        envoye = json.loads(route.calls[0].request.content)
        assert envoye["allowed_mentions"] == {"parse": []}

    def test_texte_trop_long_tronque(self):
        """Discord refuse au-delà de sa limite ; mieux vaut couper nous-mêmes."""
        from cairn.insights.discord import MAX_CONTENT, format_message

        message = format_message("x" * 5000, START, END, "model")

        assert len(message) < MAX_CONTENT + 100
        assert message.endswith("…")

    def test_origine_signalee_quand_le_modele_na_pas_ecrit(self):
        from cairn.insights.discord import format_message

        assert "sans modèle" in format_message("t", START, END, "fallback")
        assert "sans modèle" not in format_message("t", START, END, "model")


class TestPlanificateur:
    """Les tâches de fond ne réveillent personne quand elles échouent.

    Leur construction vivait dans une commande qui ne rend jamais la main :
    vérifier l'heure de départ imposait d'attendre l'heure de départ.
    """

    def settings(self, **overrides):
        from cairn.config import Settings

        return Settings(**{"db_path": "x.db", "secret_key": "k" * 44, **overrides})

    def test_jour_du_resume_est_bien_celui_annonce(self):
        """APScheduler compte le lundi comme zéro, contrairement à cron."""
        import datetime as dt

        from cairn.schedule import build_scheduler

        sched = build_scheduler(self.settings(summary_weekday=2, sync_hour=4))
        job = sched.get_job("weekly-summary")
        prochaine = job.trigger.get_next_fire_time(
            None, dt.datetime.now(job.trigger.timezone)
        )

        assert prochaine.isoweekday() == 3   # mercredi
        assert prochaine.hour == 7           # 4 h + le délai de consolidation

    def test_resume_apres_la_passe_de_nuit(self):
        """Résumer avant la synchronisation donnerait la semaine d'avant."""
        from cairn.schedule import SUMMARY_DELAY_H, build_scheduler

        sched = build_scheduler(self.settings(sync_hour=23))
        nuit = sched.get_job("nightly-sync").trigger
        resume = sched.get_job("weekly-summary").trigger

        assert {f.name: f for f in nuit.fields}["hour"].expressions[0].first == 23
        # Le passage de minuit ne doit pas renvoyer le résumé à la veille.
        assert {f.name: f for f in resume.fields}["hour"].expressions[0].first == (
            23 + SUMMARY_DELAY_H
        ) % 24

    def test_une_tache_en_echec_nemporte_pas_le_planificateur(self, monkeypatch):
        """Une exception qui traverse un travail de fond ne réveille personne.

        Sans ce filet, une coupure réseau à 4 h arrêtait la tâche sans que
        rien n'en garde trace.
        """
        from cairn import schedule

        consigne = []
        monkeypatch.setattr(
            schedule.log, "exception",
            lambda *args, **kwargs: consigne.append(kwargs.get("tache")),
        )

        def casse():
            raise RuntimeError("réseau coupé")

        schedule._guarded(casse, "essai")()   # ne doit pas lever

        assert consigne == ["essai"]

    def test_rattrapage_dune_execution_manquee(self):
        """Poste éteint à 4 h : la passe doit partir au réveil, pas disparaître."""
        from cairn.schedule import build_scheduler

        sched = build_scheduler(self.settings())

        assert sched.get_job("nightly-sync").misfire_grace_time >= 3600
        assert sched.get_job("nightly-sync").coalesce is True

    def test_resume_sans_webhook_ne_tente_pas_lenvoi(self, monkeypatch):
        from cairn.insights import weekly
        from cairn.schedule import weekly_summary

        monkeypatch.setattr(
            weekly, "generate",
            lambda: {"text": "t", "start": START, "end": END, "source": "fallback"},
        )
        envois = []
        from cairn.insights import discord

        monkeypatch.setattr(discord, "send", lambda *a, **k: envois.append(a))

        resultat = weekly_summary(self.settings(discord_webhook_url=""))

        assert resultat == {"generated": True, "delivered": False, "reason": "aucun webhook"}
        assert envois == []

    def test_echec_denvoi_ne_marque_pas_la_livraison(self, monkeypatch):
        """Marquer un résumé livré alors qu'il ne l'est pas le perdrait."""
        from cairn.insights import discord, weekly
        from cairn.schedule import weekly_summary

        monkeypatch.setattr(
            weekly, "generate",
            lambda: {"text": "t", "start": START, "end": END, "source": "model"},
        )
        monkeypatch.setattr(
            discord, "send",
            lambda *a, **k: (_ for _ in ()).throw(discord.DiscordUnavailable("hors ligne")),
        )
        marques = []
        monkeypatch.setattr(discord, "mark_delivered", lambda d: marques.append(d))

        resultat = weekly_summary(self.settings(discord_webhook_url="https://x.test/h"))

        assert resultat["delivered"] is False
        assert marques == []
