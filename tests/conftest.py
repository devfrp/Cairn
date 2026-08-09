import os

# Qt doit tourner sans serveur graphique : la suite s'exécute en intégration
# continue comme en local, sans écran.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Les tests ne doivent jamais tenter d'atteindre une vraie base.
os.environ.setdefault("CAIRN_DB_TIMEOUT", "1")
