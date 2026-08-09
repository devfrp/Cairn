# -*- mode: python ; coding: utf-8 -*-
"""Recette PyInstaller de l'application de bureau Cairn.

Deux points structurants :

* **onedir, pas onefile.** L'application embarque un moteur Ollama et un
  modèle, soit plusieurs centaines de mégaoctets. En fichier unique,
  PyInstaller les redéploierait dans un dossier temporaire à chaque
  lancement — plusieurs secondes d'attente et autant d'espace disque gaspillé
  à chaque démarrage. En dossier, les fichiers sont lus sur place.
  L'AppImage et le bundle macOS restent malgré tout des livrables uniques.

* **le moteur est facultatif.** S'il n'a pas été récupéré par
  `packaging/fetch_ollama.py`, la compilation réussit quand même et
  l'application se rabat sur une instance Ollama configurée par
  l'utilisateur.

Qt est volumineux : on écarte explicitement les modules inutilisés, sans quoi
le paquet doublerait de taille pour rien.
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).resolve().parent
VENDOR = ROOT / "packaging" / "vendor"

datas = [
    (str(ROOT / "db" / "migrations"), "db/migrations"),
    (str(ROOT / "packaging" / "cairn.svg"), "."),
]

# Moteur et modèle embarqués, quand ils ont été préparés.
for source, target in (
    (VENDOR / "ollama_runtime", "ollama_runtime"),
    (VENDOR / "ollama_models", "ollama_models"),
):
    if source.is_dir():
        datas.append((str(source), target))
        print(f"[cairn] embarque {target}")
    else:
        print(f"[cairn] {target} absent — application sans moteur local")

binaries = []
hiddenimports = collect_submodules("cairn")

QT_UNUSED = [
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DRender",
    "PySide6.QtBluetooth", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtHelp", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtNfc", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
    "PySide6.QtPositioning", "PySide6.QtQml", "PySide6.QtQuick",
    "PySide6.QtQuick3D", "PySide6.QtQuickControls2", "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtSpatialAudio", "PySide6.QtSql",
    "PySide6.QtStateMachine", "PySide6.QtTest", "PySide6.QtTextToSpeech",
    "PySide6.QtWebChannel", "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets", "PySide6.QtWebSockets",
]

icon = None
if sys.platform == "win32" and (ROOT / "packaging" / "cairn.ico").exists():
    icon = str(ROOT / "packaging" / "cairn.ico")
elif sys.platform == "darwin" and (ROOT / "packaging" / "cairn.icns").exists():
    icon = str(ROOT / "packaging" / "cairn.icns")

a = Analysis(
    [str(ROOT / "packaging" / "entrypoint_gui.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[str(ROOT / "packaging" / "runtime_hook.py")],
    excludes=[
        # logfire, tiré par garth, casse le démarrage d'un binaire : son
        # intégration pydantic lit le code source de pydantic.
        "logfire",
        "opentelemetry",
        "tkinter",
        "matplotlib",
        "numpy",
        "PIL",
        "pytest",
        "IPython",
        *QT_UNUSED,
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Cairn",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Cairn",
)

# macOS attend un bundle .app, qui est déjà un dossier : le mode onedir s'y
# glisse naturellement.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Cairn.app",
        icon=icon,
        bundle_identifier="io.github.devfrp.cairn",
        info_plist={
            "CFBundleName": "Cairn",
            "CFBundleDisplayName": "Cairn",
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": True,
            "LSUIElement": False,
        },
    )
