"""Réglages appliqués avant tout import, dans les binaires empaquetés.

pydantic charge ses plugins via les points d'entrée déclarés par les paquets
installés. L'un d'eux (logfire, tiré par garth) inspecte le code source de
pydantic au démarrage — ce qui échoue dans un binaire, où il n'y a pas de
fichier source à lire. Les plugins n'apportent rien à Cairn : on les coupe.
"""

import os

os.environ.setdefault("PYDANTIC_DISABLE_PLUGINS", "__all__")

# Ceinture et bretelles : la télémétrie de garth est déjà désactivée à la
# construction du client, mais on neutralise aussi les variables qui
# pourraient traîner dans l'environnement du poste.
os.environ.setdefault("GARTH_TELEMETRY_ENABLED", "false")
os.environ.setdefault("GARTH_TELEMETRY_SEND_TO_LOGFIRE", "false")
