"""Point d'entrée de la CLI, pour `python -m cairn` et les binaires empaquetés.

Les erreurs d'infrastructure sont traduites ici en messages lisibles : un
utilisateur qui lance un exécutable téléchargé n'a pas à lire une trace
Python pour comprendre que sa base n'est pas démarrée.
"""

from __future__ import annotations

import sys


def main() -> None:
    import sqlite3

    from cairn.cli import app

    try:
        app()
    except sqlite3.Error as exc:
        from cairn.config import get_settings

        print(
            f"Fichier de données inutilisable : {get_settings().db_path}\n"
            f"  {type(exc).__name__}: {exc}\n\n"
            "Vérifier que le chemin existe et qu'il est accessible en écriture "
            "(CAIRN_DB_PATH).",
            file=sys.stderr,
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
