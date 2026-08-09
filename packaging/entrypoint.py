"""Script d'entrée du binaire empaqueté.

PyInstaller exécute ce fichier comme un script, pas comme un module : il ne
peut donc pas s'appuyer sur `python -m cairn`. Il se contente d'appeler le
même `main()` que l'entrée console installée par pip.
"""

from cairn.__main__ import main

main()
