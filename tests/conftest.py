import os

# Pydantic doit être chargé avant PySide6. Qt installe un crochet d'import qui
# inspecte le code source de chaque module importé ensuite ; sur les imports
# différés de pydantic, l'inspection retombe dans un module à moitié
# initialisé et l'import échoue. Lancer un fichier de test isolé suffisait à
# déclencher le cas — la suite complète y échappait par hasard, un autre
# module ayant déjà chargé pydantic.
import cairn.config  # noqa: F401

# Qt doit tourner sans serveur graphique : la suite s'exécute en intégration
# continue comme en local, sans écran.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Les tests ne doivent jamais tenter d'atteindre une vraie base.
os.environ.setdefault("CAIRN_DB_TIMEOUT", "1")
