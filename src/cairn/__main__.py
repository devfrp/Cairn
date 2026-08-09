"""Point d'entrée de la CLI, pour `python -m cairn` et les binaires empaquetés.

Les erreurs d'infrastructure sont traduites ici en messages lisibles : un
utilisateur qui lance un exécutable téléchargé n'a pas à lire une trace
Python pour comprendre que sa base n'est pas démarrée.
"""

from __future__ import annotations

import sys


def main() -> None:
    from psycopg import OperationalError
    from psycopg_pool import PoolTimeout

    from cairn.cli import app

    try:
        app()
    except (PoolTimeout, OperationalError) as exc:
        from cairn.config import get_settings

        dsn = get_settings().db_dsn
        # Le DSN contient le mot de passe : on n'affiche que l'hôte.
        location = dsn.rsplit("@", 1)[-1] if "@" in dsn else dsn
        print(
            f"Base de données injoignable sur {location}.\n"
            f"  {type(exc).__name__}: {exc}\n\n"
            "Vérifier que PostgreSQL est démarré et que "
            "CAIRN_DB_DSN pointe au bon endroit.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
