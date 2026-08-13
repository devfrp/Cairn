"""Mise en forme du texte français, partagée par l'interface et les résumés.

Sans dépendance : le module est appelé aussi bien depuis Qt que depuis la
rédaction des bilans, qui ne doit rien connaître de l'interface.
"""

from __future__ import annotations


def pluriel(nombre: float, singulier: str, pluriel_: str | None = None) -> str:
    """« 1 séance », « 12 séances ».

    Remplace le « séance(s) » qui parsemait l'application. La forme
    parenthésée passe encore dans un journal technique ; dans un message
    envoyé sur Discord ou sur une carte du tableau de bord, elle se lit comme
    un texte que personne n'a relu.

    En français, zéro prend le singulier — « 0 séance », et non « 0 séances ».
    """
    forme = singulier if abs(nombre) < 2 else (pluriel_ or singulier + "s")
    return f"{nombre:.0f} {forme}"
