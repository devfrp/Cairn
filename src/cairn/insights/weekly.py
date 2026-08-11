"""Résumé hebdomadaire : mise en forme des faits, rédaction, repli.

Le chemin nominal passe par le modèle local. Mais un résumé doit sortir
quelles que soient les circonstances — serveur Ollama éteint, modèle absent,
texte contenant des chiffres inventés. D'où une rédaction déterministe qui
prend le relais et dit exactement la même chose, en moins fluide.
"""

from __future__ import annotations

import datetime as dt
import json
import os

import structlog

from cairn.config import get_settings
from cairn.db import connection
from cairn.insights import facts as facts_module
from cairn.insights.facts import WeeklyFacts
from cairn.insights.ollama import OllamaClient, OllamaUnavailable, verify_numbers

log = structlog.get_logger(__name__)

# Le modèle embarqué compte six cents millions de paramètres : il rédige
# correctement, mais raisonne mal. Livré à lui-même il relie deux mesures par
# une cause inventée — « une baisse de charge de 36 %, ce qui indique une
# augmentation du rythme cardiaque » — et propose l'inverse de ce qu'il
# faudrait. Le garde-fou numérique n'y voit rien : aucun chiffre n'est faux,
# c'est le lien qui l'est.
#
# D'où une consigne qui lui retire tout ce qui demande de raisonner. Les
# conclusions sont calculées par Cairn et figurent déjà dans le bilan, sous
# « À SIGNALER » : il n'a qu'à les rapporter.
SYSTEM_PROMPT = (
    "Tu es l'assistant d'un cycliste et traileur. On te donne le bilan chiffré "
    "de sa semaine. Ta seule tâche est de le reformuler en français, en trois "
    "ou quatre phrases courtes. "
    "N'invente aucun chiffre : n'utilise que ceux fournis. "
    "N'ajoute aucune donnée absente du bilan. "
    "N'explique aucune cause et n'établis aucun lien entre deux mesures : "
    "contente-toi de rapporter ce qui est écrit. "
    "Ne donne aucun conseil de ton cru : s'il y a une rubrique « À SIGNALER », "
    "reprends-la telle quelle ; sinon, dis simplement qu'aucun signal "
    "particulier n'est apparu. "
    "Ton direct et sobre, pas de formule d'introduction, pas de liste."
)


def _hours(seconds: float | None) -> str:
    if not seconds:
        return "0 h"
    total = int(seconds)
    hours, minutes = divmod(total // 60, 60)
    return f"{hours} h {minutes:02d}" if minutes else f"{hours} h"


def render_prompt(facts: WeeklyFacts) -> str:
    """Bilan chiffré donné au modèle, en clair."""
    lines = [
        f"Semaine du {facts.start:%d/%m} au {facts.end:%d/%m}.",
        "",
        "ENTRAÎNEMENT",
        f"- {facts.sessions} séance(s), {_hours(facts.duration_s)} au total",
        f"- {facts.distance_m / 1000:.0f} km, {facts.elevation_m:.0f} m de dénivelé positif",
        f"- charge d'entraînement : {facts.load:.0f}",
    ]

    change = facts.load_change_pct
    if change is not None:
        sens = "hausse" if change >= 0 else "baisse"
        lines.append(
            f"- semaine précédente : {facts.previous_load:.0f} "
            f"(donc {sens} de {abs(change):.0f} %)"
        )

    for entry in facts.by_sport:
        lines.append(
            f"- {entry['sport']} : {entry['sessions']} séance(s), "
            f"{_hours(entry['duration_s'])}, charge {entry['load']:.0f}"
        )

    if facts.hardest:
        hardest = facts.hardest
        lines.append(
            f"- sortie la plus exigeante : {hardest['name'] or hardest['sport']} "
            f"le {hardest['day']:%d/%m}, {_hours(hardest['duration_s'])}, "
            f"{hardest['elevation_m']:.0f} m de dénivelé, charge {hardest['load']:.0f}"
        )

    lines += ["", "RÉCUPÉRATION"]
    if facts.hrv_mean is not None:
        line = f"- variabilité cardiaque moyenne : {facts.hrv_mean:.0f} ms"
        if facts.hrv_baseline is not None:
            line += f" (habitude : {facts.hrv_baseline:.0f} ms)"
        lines.append(line)
    if facts.rhr_mean is not None:
        line = f"- fréquence cardiaque de repos : {facts.rhr_mean:.0f} bpm"
        if facts.rhr_baseline is not None:
            line += f" (habitude : {facts.rhr_baseline:.0f} bpm)"
        lines.append(line)
    if facts.sleep_mean_score is not None:
        lines.append(
            f"- sommeil : score moyen {facts.sleep_mean_score:.0f}, "
            f"durée moyenne {_hours(facts.sleep_mean_duration_s)}"
        )
    if facts.short_nights:
        lines.append(f"- {facts.short_nights} nuit(s) de moins de 7 heures")

    if facts.signals:
        lines += ["", "À SIGNALER"]
        lines += [f"- {signal}" for signal in facts.signals]

    return "\n".join(lines)


def allowed_numbers(facts: WeeklyFacts) -> list[float]:
    """Valeurs que le modèle a le droit de citer."""
    values: list[float] = [
        facts.sessions,
        facts.duration_s / 3600,
        facts.distance_m / 1000,
        facts.elevation_m,
        facts.load,
        facts.previous_load,
        facts.previous_duration_s / 3600,
        facts.short_nights,
        facts.hrv_days_below,
    ]
    for optional in (
        facts.hrv_mean, facts.hrv_baseline, facts.hrv_last,
        facts.rhr_mean, facts.rhr_baseline,
        facts.sleep_mean_score,
    ):
        if optional is not None:
            values.append(float(optional))

    if facts.sleep_mean_duration_s:
        values.append(facts.sleep_mean_duration_s / 3600)
    for computed in (facts.load_change_pct, facts.hrv_delta, facts.rhr_delta):
        if computed is not None:
            values += [computed, abs(computed)]

    for entry in facts.by_sport:
        values += [entry["sessions"], entry["duration_s"] / 3600, entry["load"]]
    if facts.hardest:
        values += [
            facts.hardest["load"] or 0,
            (facts.hardest["duration_s"] or 0) / 3600,
            facts.hardest["elevation_m"] or 0,
        ]

    # Les valeurs arrondies comptent aussi : le modèle écrira « 6 h » pour
    # 6,2 heures, ce qui est une reformulation correcte.
    rounded = [round(v) for v in values]
    return sorted({float(v) for v in values + rounded})


def fallback_summary(facts: WeeklyFacts) -> str:
    """Rédaction sans modèle : moins fluide, jamais fausse."""
    if not facts.has_data:
        return (
            f"Aucune donnée entre le {facts.start:%d/%m} et le {facts.end:%d/%m}. "
            "Vérifiez que la synchronisation s'est bien exécutée."
        )

    parts = [
        f"Semaine du {facts.start:%d/%m} au {facts.end:%d/%m} : "
        f"{facts.sessions} séance(s), {_hours(facts.duration_s)}, "
        f"{facts.distance_m / 1000:.0f} km et {facts.elevation_m:.0f} m de dénivelé, "
        f"pour une charge de {facts.load:.0f}."
    ]

    change = facts.load_change_pct
    if change is not None:
        sens = "en hausse" if change >= 0 else "en baisse"
        parts.append(
            f"La charge est {sens} de {abs(change):.0f} % par rapport à la "
            f"semaine précédente ({facts.previous_load:.0f})."
        )

    recovery = []
    if facts.hrv_mean is not None:
        recovery.append(f"variabilité cardiaque à {facts.hrv_mean:.0f} ms")
    if facts.rhr_mean is not None:
        recovery.append(f"fréquence de repos à {facts.rhr_mean:.0f} bpm")
    if facts.sleep_mean_score is not None:
        recovery.append(f"score de sommeil moyen de {facts.sleep_mean_score:.0f}")
    if recovery:
        parts.append("Côté récupération : " + ", ".join(recovery) + ".")

    if facts.signals:
        parts.append("À signaler : " + " ; ".join(facts.signals) + ".")
        parts.append(
            "Une semaine allégée, ou au moins une sortie de récupération, "
            "paraît indiquée."
        )
    else:
        parts.append("Aucun signal de fatigue particulier cette semaine.")

    return " ".join(parts)


def generate(
    start: dt.date | None = None,
    end: dt.date | None = None,
    use_model: bool | None = None,
) -> dict:
    """Produit le résumé de la semaine et l'enregistre.

    Retourne le texte, sa provenance et, le cas échéant, ce qui a été rejeté.

    Le modèle n'est plus sollicité par défaut. Éprouvé sur des données réelles,
    celui qu'embarque l'application invente des liens entre mesures — « une
    baisse de charge de 36 %, ce qui indique une augmentation du rythme
    cardiaque » — et des comparaisons qui n'existent pas. Le garde-fou
    numérique n'attrape rien de tout cela : les chiffres cités sont exacts,
    c'est ce qu'on leur fait dire qui est faux. Le texte déterministe, moins
    fluide, ne se trompe jamais.

    `CAIRN_SUMMARY_USE_MODEL=1` réactive la rédaction — utile avec un modèle
    plus gros configuré à part.
    """
    if use_model is None:
        use_model = os.environ.get("CAIRN_SUMMARY_USE_MODEL", "").strip() in {"1", "true", "oui"}
    if start is None or end is None:
        start, end = facts_module.last_complete_week()

    facts = facts_module.collect(start, end)
    prompt = render_prompt(facts)
    settings = get_settings()

    result = {
        "start": start,
        "end": end,
        "prompt": prompt,
        "source": "fallback",
        "model": None,
        "latency_ms": 0,
        "rejected_numbers": [],
        "embedded": False,
        "text": fallback_summary(facts),
        "facts": facts,
    }

    if not use_model:
        _store(result, facts)
        return result

    try:
        # Sans adresse configurée, on démarre le moteur livré avec
        # l'application plutôt que de renoncer au résumé.
        client = OllamaClient.from_settings(
            settings.ollama_url, settings.ollama_model
        )
        text, latency = client.generate(prompt, SYSTEM_PROMPT)
    except OllamaUnavailable as exc:
        log.warning("résumé : modèle indisponible, repli déterministe", error=str(exc))
        result["error"] = str(exc)
        _store(result, facts)
        return result

    if not text:
        log.warning("résumé : réponse vide, repli déterministe")
        _store(result, facts)
        return result

    rejected = verify_numbers(text, allowed_numbers(facts))
    if rejected:
        # Un chiffre absent du bilan est une invention : on n'expédie pas
        # un conseil d'entraînement bâti sur une donnée fausse.
        log.warning(
            "résumé : chiffres non fondés, repli déterministe",
            model=client.model,
            rejected=rejected,
        )
        result["rejected_numbers"] = rejected
        result["model"] = client.model
        result["embedded"] = client.embedded
        result["latency_ms"] = latency
        _store(result, facts)
        return result

    result.update(
        text=text,
        source="model",
        model=client.model,
        embedded=client.embedded,
        latency_ms=latency,
    )
    _store(result, facts)
    return result


def _store(result: dict, facts: WeeklyFacts) -> None:
    payload = {
        "prompt": result["prompt"],
        "source": result["source"],
        "rejected_numbers": result["rejected_numbers"],
        "signals": facts.signals,
        "sessions": facts.sessions,
        "load": facts.load,
    }
    try:
        with connection() as conn:
            conn.execute(
                """
                INSERT INTO llm_summary (athlete_id, period_kind, period_start,
                                         period_end, model, input_facts,
                                         summary_md, latency_ms)
                SELECT id, 'weekly', ?, ?, ?, ?, ?, ? FROM athlete ORDER BY id LIMIT 1
                ON CONFLICT (athlete_id, period_kind, period_start) DO UPDATE
                   SET summary_md   = excluded.summary_md,
                       model        = excluded.model,
                       input_facts  = excluded.input_facts,
                       latency_ms   = excluded.latency_ms,
                       generated_at = datetime('now')
                """,
                (
                    result["start"],
                    result["end"],
                    result["model"] or "aucun",
                    json.dumps(payload, default=str),
                    result["text"],
                    result["latency_ms"],
                ),
            )
    except Exception as exc:  # noqa: BLE001 — l'enregistrement ne doit pas masquer le résumé
        log.warning("résumé : enregistrement impossible", error=str(exc))
