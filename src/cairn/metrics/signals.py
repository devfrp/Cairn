"""Détection des situations qui méritent un avertissement.

Un seul indicateur ne dit jamais grand-chose : une charge qui monte est
normale, une VFC qui baisse une nuit aussi. C'est leur conjonction qui
signale une surcharge, et c'est elle qu'on remonte en priorité.

Les seuils viennent de la littérature sur la charge d'entraînement. Ils sont
rassemblés ici, nommés, pour être discutés et ajustés plutôt que dispersés
dans le code.
"""

from __future__ import annotations

from dataclasses import dataclass

# Rapport charge aiguë sur charge chronique. En dessous de 0,8 on se
# désentraîne, au-dessus de 1,5 le risque de blessure augmente nettement.
ACWR_HIGH = 1.5
ACWR_LOW = 0.8

# Monotonie de Foster : au-delà, les séances se ressemblent trop.
MONOTONY_HIGH = 2.0

# Variabilité cardiaque : un écart-type sous la normale est une mauvaise
# nuit, trois jours d'affilée sont un signal.
HRV_Z_LOW = -1.0
HRV_SUPPRESSED_DAYS = 3

# Fréquence cardiaque de repos, en battements au-dessus de l'habitude.
RHR_ELEVATED = 5.0

# Dette de sommeil hebdomadaire, en secondes (quatre heures).
SLEEP_DEBT_HIGH = 4 * 3600


@dataclass
class Signal:
    kind: str
    severity: str  # info, warn, alert
    message: str
    detail: dict


def detect(
    *,
    acwr: float | None = None,
    monotony: float | None = None,
    hrv_z: float | None = None,
    hrv_days_below: int = 0,
    hrv_trend: float | None = None,
    rhr_delta: float | None = None,
    sleep_debt_s: int | None = None,
) -> list[Signal]:
    """Signaux du jour, du plus grave au plus anodin."""
    signals: list[Signal] = []

    suppressed = hrv_days_below >= HRV_SUPPRESSED_DAYS
    overloaded = acwr is not None and acwr > ACWR_HIGH

    # La conjonction d'abord : c'est elle qui justifie de lever le pied.
    if suppressed and overloaded:
        signals.append(Signal(
            kind="overreaching",
            severity="alert",
            message=(
                f"Charge en forte hausse (rapport {acwr:.2f}) alors que la "
                f"variabilité cardiaque est sous sa normale depuis "
                f"{hrv_days_below} jours. Une semaine allégée s'impose."
            ),
            detail={"acwr": acwr, "hrv_days_below": hrv_days_below},
        ))
    else:
        if overloaded:
            signals.append(Signal(
                kind="acwr_high",
                severity="warn",
                message=(
                    f"La charge des sept derniers jours dépasse nettement "
                    f"l'habitude (rapport {acwr:.2f}). Au-delà de "
                    f"{ACWR_HIGH}, le risque de blessure augmente."
                ),
                detail={"acwr": acwr},
            ))
        if suppressed:
            trend = ""
            if hrv_trend is not None and hrv_trend < 0:
                trend = f", en baisse de {abs(hrv_trend):.1f} ms par jour"
            signals.append(Signal(
                kind="hrv_suppressed",
                severity="warn",
                message=(
                    f"Variabilité cardiaque sous sa normale depuis "
                    f"{hrv_days_below} jours{trend}."
                ),
                detail={"hrv_days_below": hrv_days_below, "hrv_z": hrv_z},
            ))

    if monotony is not None and monotony > MONOTONY_HIGH:
        signals.append(Signal(
            kind="monotony_high",
            severity="warn",
            message=(
                f"Semaine très uniforme (monotonie {monotony:.1f}) : alterner "
                "séances dures et vraies journées faciles protège mieux "
                "qu'une régularité constante."
            ),
            detail={"monotony": monotony},
        ))

    if rhr_delta is not None and rhr_delta >= RHR_ELEVATED:
        signals.append(Signal(
            kind="rhr_elevated",
            severity="warn",
            message=(
                f"Fréquence cardiaque de repos supérieure de "
                f"{rhr_delta:.0f} bpm à l'habitude."
            ),
            detail={"rhr_delta": rhr_delta},
        ))

    if sleep_debt_s is not None and sleep_debt_s >= SLEEP_DEBT_HIGH:
        signals.append(Signal(
            kind="sleep_debt",
            severity="info",
            message=(
                f"Dette de sommeil de {sleep_debt_s / 3600:.0f} h sur la "
                "semaine écoulée."
            ),
            detail={"sleep_debt_s": sleep_debt_s},
        ))

    if acwr is not None and acwr < ACWR_LOW and not signals:
        signals.append(Signal(
            kind="acwr_low",
            severity="info",
            message=(
                f"Charge nettement en dessous de l'habitude (rapport "
                f"{acwr:.2f}) : phase de repos ou de reprise."
            ),
            detail={"acwr": acwr},
        ))

    order = {"alert": 0, "warn": 1, "info": 2}
    return sorted(signals, key=lambda s: order.get(s.severity, 3))
