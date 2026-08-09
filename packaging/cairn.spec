# -*- mode: python ; coding: utf-8 -*-
"""Recette PyInstaller de la CLI Cairn.

Un seul fichier de spec pour les trois plateformes : les workflows GitHub
l'appellent tel quel, ce qui évite de maintenir trois jeux d'options qui
divergent silencieusement.

Ce qui compte ici :
* `db/migrations/*.sql` doit être embarqué, sinon `cairn db upgrade` ne
  trouve rien une fois le binaire distribué ;
* psycopg fournit sa libpq dans `psycopg_binary`, que l'analyse statique ne
  voit pas puisqu'il est chargé dynamiquement.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve().parent

datas = [(str(ROOT / "db" / "migrations"), "db/migrations")]
binaries = []
hiddenimports = collect_submodules("cairn")

# psycopg charge son implémentation à l'exécution : sans collecte explicite,
# le binaire démarre puis échoue à la première connexion.
for package in ("psycopg", "psycopg_binary", "psycopg_pool"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    except Exception:  # noqa: BLE001 — psycopg_binary est absent sur certaines plateformes
        continue
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

a = Analysis(
    [str(ROOT / "packaging" / "entrypoint.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[str(ROOT / "packaging" / "runtime_hook.py")],
    # logfire est tiré par garth pour une télémétrie que Cairn n'utilise pas.
    # Embarqué, il casse le démarrage : son intégration pydantic lit le code
    # source de pydantic, absent d'un binaire. garth l'importe dans un
    # try/except et fonctionne très bien sans.
    excludes=[
        "logfire",
        "opentelemetry",
        "tkinter",
        "matplotlib",
        "numpy",
        "PIL",
        "pytest",
        "IPython",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="cairn",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
