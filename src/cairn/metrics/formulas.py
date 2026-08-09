"""Formules de charge et de récupération.

Toutes pures : elles prennent des séries de nombres et n'en connaissent ni
l'origine ni la destination. C'est ce qui permet de les vérifier sur des cas
choisis plutôt que sur des données réelles impossibles à reproduire.

Les constantes de temps sont celles admises en physiologie de l'entraînement :
42 jours pour la charge chronique, 7 pour la charge aiguë.
"""

from __future__ import annotations

import math

# Constantes de temps des moyennes exponentielles, en jours.
CTL_DAYS = 42
ATL_DAYS = 7

# Fenêtre de référence pour juger une mesure de récupération. Deux mois
# couvrent un cycle d'entraînement sans remonter à une autre saison.
BASELINE_DAYS = 60

# Nombre minimal de mesures avant de prétendre calculer une ligne de base :
# un écart-type sur trois nuits ne veut rien dire.
MIN_BASELINE_SAMPLES = 14


def ewma(values: list[float], days: int, seed: float | None = None) -> list[float]:
    """Moyenne mobile exponentielle, une valeur par jour.

    La charge d'entraînement s'accumule et s'estompe : chaque journée pèse
    d'autant moins qu'elle est ancienne. C'est le modèle utilisé pour la
    charge chronique comme pour l'aiguë, seule la constante de temps change.

    `seed` amorce la série quand on reprend un calcul en cours de route.
    """
    if days <= 0:
        raise ValueError("La constante de temps doit être positive.")

    decay = math.exp(-1.0 / days)
    current = 0.0 if seed is None else float(seed)

    result = []
    for value in values:
        current = current * decay + float(value) * (1.0 - decay)
        result.append(current)
    return result


def training_stress_balance(ctl: float | None, atl: float | None) -> float | None:
    """Écart entre charge chronique et charge aiguë.

    Positif, l'organisme est reposé par rapport à son niveau habituel ;
    négatif, il est encore sous le coup des séances récentes.
    """
    if ctl is None or atl is None:
        return None
    return ctl - atl


def acute_chronic_ratio(atl: float | None, ctl: float | None) -> float | None:
    """Rapport de la charge aiguë sur la chronique.

    Retourne None tant que la charge chronique est négligeable : diviser par
    une valeur proche de zéro produirait des rapports spectaculaires et faux
    au début d'un historique.
    """
    if not ctl or ctl < 1.0 or atl is None:
        return None
    return atl / ctl


def monotony(loads: list[float]) -> float | None:
    """Monotonie de Foster : moyenne des charges divisée par leur écart-type.

    Une semaine où toutes les séances se ressemblent obtient un score élevé.
    C'est un facteur de risque indépendant du volume : alterner dur et facile
    protège mieux qu'une régularité parfaite.

    Les jours de repos comptent comme des charges nulles — c'est justement
    leur présence qui fait baisser la monotonie.
    """
    if len(loads) < 2:
        return None

    mean = sum(loads) / len(loads)
    if mean == 0:
        return None

    variance = sum((value - mean) ** 2 for value in loads) / (len(loads) - 1)
    deviation = math.sqrt(variance)
    if deviation < 1e-9:
        # Charge rigoureusement identique tous les jours : la monotonie
        # tendrait vers l'infini, on la borne à une valeur parlante.
        return 10.0
    return mean / deviation


def strain(loads: list[float], monotony_value: float | None) -> float | None:
    """Contrainte de Foster : charge hebdomadaire pondérée par la monotonie."""
    if monotony_value is None:
        return None
    return sum(loads) * monotony_value


def mean_and_deviation(values: list[float]) -> tuple[float | None, float | None]:
    """Moyenne et écart-type d'échantillon, ou (None, None) si trop peu."""
    usable = [v for v in values if v is not None]
    if len(usable) < MIN_BASELINE_SAMPLES:
        return None, None

    mean = sum(usable) / len(usable)
    variance = sum((v - mean) ** 2 for v in usable) / (len(usable) - 1)
    return mean, math.sqrt(variance)


def zscore(value: float | None, mean: float | None, deviation: float | None) -> float | None:
    """Écart à la normale, exprimé en écarts-types.

    C'est la seule façon honnête de juger une variabilité cardiaque : 55 ms
    est excellent pour l'un et inquiétant pour l'autre.
    """
    if value is None or mean is None or not deviation:
        return None
    return (value - mean) / deviation


def slope(values: list[float | None]) -> float | None:
    """Pente d'une régression linéaire, en unités par jour.

    Sert à dire si une série décroît, et à quelle vitesse — une baisse lente
    et régulière échappe à une simple comparaison de moyennes.
    """
    points = [(index, value) for index, value in enumerate(values) if value is not None]
    if len(points) < 3:
        return None

    count = len(points)
    mean_x = sum(x for x, _ in points) / count
    mean_y = sum(y for _, y in points) / count

    numerator = sum((x - mean_x) * (y - mean_y) for x, y in points)
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    if denominator == 0:
        return None
    return numerator / denominator


def consecutive_below(values: list[float | None], threshold: float | None) -> int:
    """Nombre de mesures consécutives sous un seuil, en partant de la fin.

    Les valeurs manquantes interrompent le décompte plutôt que de le
    poursuivre : une nuit sans mesure n'est pas une nuit sous la normale.
    """
    if threshold is None:
        return 0

    streak = 0
    for value in reversed(values):
        if value is None or value >= threshold:
            break
        streak += 1
    return streak
