"""Réglages du moteur de rendu, à poser avant la création de QApplication.

Isolé de `garmin_login` à dessein : ce module ne doit rien importer de Cairn.
L'auto-test s'en sert pour vérifier le moteur de rendu, et un import de plus
ferait passer n'importe quelle panne de dépendance pour une panne d'affichage
— c'est exactement ce qui est arrivé avec `cryptography` sur macOS Intel.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QCoreApplication, Qt


def prepare_webengine() -> None:
    """Prépare Qt à faire cohabiter un moteur de rendu et des widgets.

    Empaqueté en AppImage, le bac à sable de Chromium ne peut pas démarrer :
    il lui faut un binaire auxiliaire installé avec le bit setuid, ce qu'une
    archive portable ne peut pas fournir. Sans cette variable, la fenêtre de
    connexion reste blanche. Le moteur ne sert qu'à afficher la page de
    connexion de Garmin, jamais à naviguer ailleurs.
    """
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    # Qt exige que les contextes OpenGL soient partagés lorsqu'un moteur de
    # rendu coexiste avec des widgets classiques.
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
