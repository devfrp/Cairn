"""Récupère le moteur Ollama et le modèle, à embarquer dans l'application.

Exécuté au moment de la compilation. Il dépose dans `packaging/vendor/` :

    ollama_runtime/   le binaire Ollama et ses bibliothèques
    ollama_models/    le modèle, au format du dépôt local d'Ollama

Le modèle est obtenu en démarrant le binaire fraîchement téléchargé et en lui
demandant de le tirer : c'est la seule façon fiable d'obtenir la disposition
exacte de fichiers qu'Ollama attend au démarrage.

Les paquets contenant les accélérateurs GPU sont écartés : ils pèsent plus
d'un gigaoctet chacun et ne servent à rien pour un modèle de cette taille,
qui tourne très bien sur processeur.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENDOR = HERE / "vendor"
RUNTIME = VENDOR / "ollama_runtime"
MODELS = VENDOR / "ollama_models"

DEFAULT_VERSION = "v0.5.7"
DEFAULT_MODEL = "qwen3:0.6b"

# Répertoires d'accélérateurs à ne pas embarquer : le modèle visé tourne sur
# processeur, et ces bibliothèques pèsent plus lourd que tout le reste réuni.
SKIP_PARTS = ("cuda", "rocm", "hip", "oneapi")

ASSETS = {
    ("Linux", "x86_64"): "ollama-linux-amd64.tgz",
    ("Linux", "aarch64"): "ollama-linux-arm64.tgz",
    ("Darwin", "x86_64"): "ollama-darwin.tgz",
    ("Darwin", "arm64"): "ollama-darwin.tgz",
    ("Windows", "AMD64"): "ollama-windows-amd64.zip",
}


def asset_name() -> str:
    key = (platform.system(), platform.machine())
    if key not in ASSETS:
        raise SystemExit(f"Plateforme non gérée pour l'embarquement : {key}")
    return ASSETS[key]


def download(url: str, target: Path) -> Path:
    print(f"Téléchargement de {url}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response, target.open("wb") as out:  # noqa: S310
        shutil.copyfileobj(response, out)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    print(f"  {target.name} — {target.stat().st_size / 1e6:.0f} Mo, sha256 {digest[:16]}…")
    return target


def _keep(name: str) -> bool:
    parts = [p.lower() for p in Path(name).parts]
    return not any(skip in part for part in parts for skip in SKIP_PARTS)


def extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)

    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            members = [m for m in zf.namelist() if _keep(m)]
            zf.extractall(destination, members=members)
    else:
        with tarfile.open(archive) as tf:
            members = [m for m in tf.getmembers() if _keep(m.name)]
            # `filter="data"` refuse les chemins absolus et les liens qui
            # sortiraient du dossier de destination.
            tf.extractall(destination, members=members, filter="data")

    total = sum(f.stat().st_size for f in destination.rglob("*") if f.is_file())
    print(f"  moteur extrait : {total / 1e6:.0f} Mo")


def locate_binary(root: Path) -> Path:
    for name in ("ollama", "ollama.exe"):
        for candidate in (root / "bin" / name, root / name):
            if candidate.exists():
                return candidate
        found = next(root.rglob(name), None)
        if found is not None:
            return found
    raise SystemExit(f"Binaire ollama introuvable sous {root}")


def pull_model(binary: Path, model: str) -> None:
    """Démarre le moteur téléchargé et lui fait tirer le modèle."""
    MODELS.mkdir(parents=True, exist_ok=True)
    binary.chmod(0o755)

    env = os.environ.copy()
    env["OLLAMA_HOST"] = "127.0.0.1:11500"
    env["OLLAMA_MODELS"] = str(MODELS)

    print(f"Démarrage du moteur pour tirer {model}")
    server = subprocess.Popen(
        [str(binary), "serve"], env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            probe = subprocess.run(
                [str(binary), "list"], env=env, capture_output=True, timeout=10, check=False
            )
            if probe.returncode == 0:
                break
            time.sleep(1)
        else:
            raise SystemExit("Le moteur n'a pas démarré dans le délai imparti.")

        result = subprocess.run(
            [str(binary), "pull", model], env=env, check=False, timeout=1800
        )
        if result.returncode != 0:
            raise SystemExit(f"Échec du téléchargement de {model}")
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()

    size = sum(f.stat().st_size for f in MODELS.rglob("*") if f.is_file())
    print(f"  modèle embarqué : {size / 1e6:.0f} Mo")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default=DEFAULT_VERSION, help="version d'Ollama")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="modèle à embarquer")
    parser.add_argument(
        "--skip-if-present",
        action="store_true",
        help="ne rien faire si les ressources sont déjà là",
    )
    args = parser.parse_args()

    if args.skip_if_present and RUNTIME.exists() and MODELS.exists():
        print("Ressources déjà présentes, rien à faire.")
        return 0

    shutil.rmtree(VENDOR, ignore_errors=True)
    VENDOR.mkdir(parents=True, exist_ok=True)

    name = asset_name()
    url = f"https://github.com/ollama/ollama/releases/download/{args.version}/{name}"
    archive = download(url, VENDOR / name)
    extract(archive, RUNTIME)
    archive.unlink()

    binary = locate_binary(RUNTIME)
    print(f"  binaire : {binary.relative_to(VENDOR)}")
    pull_model(binary, args.model)

    total = sum(f.stat().st_size for f in VENDOR.rglob("*") if f.is_file())
    print(f"\nTotal embarqué : {total / 1e6:.0f} Mo")
    return 0


if __name__ == "__main__":
    sys.exit(main())
