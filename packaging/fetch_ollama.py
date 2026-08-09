"""Récupère le moteur Ollama et le modèle, à embarquer dans l'application.

Exécuté au moment de la compilation. Il dépose dans `packaging/vendor/` :

    ollama_runtime/   le binaire Ollama et ses bibliothèques
    ollama_models/    le modèle, au format du dépôt local d'Ollama

Le modèle est obtenu en démarrant le binaire fraîchement téléchargé et en lui
demandant de le tirer : c'est la seule façon fiable d'obtenir la disposition
exacte de fichiers qu'Ollama attend au démarrage.

Deux pièges appris à l'usage :

* les noms d'archives changent d'une version à l'autre — Linux est passé de
  `.tgz` à `.tar.zst`, et `ollama-darwin.tgz` n'existe pas dans les versions
  anciennes. On interroge donc la liste réelle des fichiers publiés au lieu
  de la deviner ;
* les archives Linux et Windows pèsent près d'un gigaoctet et demi parce
  qu'elles embarquent les bibliothèques CUDA et ROCm. On ne les extrait pas :
  un modèle de cette taille tourne très bien sur processeur.
"""

from __future__ import annotations

import argparse
import hashlib
import json
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

DEFAULT_VERSION = "v0.32.6"
DEFAULT_MODEL = "qwen3:0.6b"

RELEASES = "https://api.github.com/repos/ollama/ollama/releases/tags/"

# Répertoires d'accélérateurs à ne pas embarquer : ils pèsent plus lourd que
# tout le reste réuni et ne servent pas au modèle visé.
SKIP_PARTS = ("cuda", "rocm", "hip", "oneapi", "mlx")

# Motifs d'archive acceptables, par plateforme et dans l'ordre de préférence.
# On retient le premier qui existe réellement dans la version demandée.
CANDIDATES = {
    ("Linux", "x86_64"): ("ollama-linux-amd64.tgz", "ollama-linux-amd64.tar.zst"),
    ("Linux", "aarch64"): ("ollama-linux-arm64.tgz", "ollama-linux-arm64.tar.zst"),
    ("Darwin", "x86_64"): ("ollama-darwin.tgz", "Ollama-darwin.zip"),
    ("Darwin", "arm64"): ("ollama-darwin.tgz", "Ollama-darwin.zip"),
    ("Windows", "AMD64"): ("ollama-windows-amd64.zip",),
    ("Windows", "ARM64"): ("ollama-windows-arm64.zip",),
}


class Unavailable(RuntimeError):
    """Le moteur n'a pas pu être récupéré : la compilation continue sans lui."""


def published_assets(version: str) -> dict[str, str]:
    """Nom vers adresse de téléchargement, tel que publié pour cette version."""
    try:
        with urllib.request.urlopen(RELEASES + version, timeout=30) as response:  # noqa: S310
            data = json.load(response)
    except Exception as exc:  # noqa: BLE001 — réseau, quota, version inconnue
        raise Unavailable(f"Liste des fichiers de {version} illisible : {exc}") from exc
    return {a["name"]: a["browser_download_url"] for a in data.get("assets", [])}


def choose_asset(assets: dict[str, str]) -> tuple[str, str]:
    key = (platform.system(), platform.machine())
    if key not in CANDIDATES:
        raise Unavailable(f"Plateforme non gérée pour l'embarquement : {key}")

    for name in CANDIDATES[key]:
        if name in assets:
            return name, assets[name]
    raise Unavailable(
        f"Aucune archive utilisable pour {key}. Publiées : {', '.join(sorted(assets))}"
    )


def download(url: str, target: Path) -> Path:
    print(f"Téléchargement de {url}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as out:  # noqa: S310
        shutil.copyfileobj(response, out, length=1024 * 1024)
    print(f"  {target.name} — {target.stat().st_size / 1e6:.0f} Mo")
    return target


def verify(archive: Path, assets: dict[str, str]) -> None:
    """Compare l'empreinte publiée à celle du fichier reçu.

    On s'apprête à exécuter ce binaire : mieux vaut savoir qu'il est bien
    celui qu'Ollama a publié.
    """
    if "sha256sum.txt" not in assets:
        print("  pas d'empreinte publiée, vérification impossible")
        return

    try:
        with urllib.request.urlopen(assets["sha256sum.txt"], timeout=30) as response:  # noqa: S310
            listing = response.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        print(f"  empreintes illisibles ({exc}), vérification ignorée")
        return

    expected = None
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) == 2 and Path(parts[1]).name == archive.name:
            expected = parts[0]
            break
    if expected is None:
        print(f"  {archive.name} absent de sha256sum.txt, vérification ignorée")
        return

    digest = hashlib.sha256()
    with archive.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise Unavailable(f"Empreinte incorrecte pour {archive.name}")
    print("  empreinte vérifiée")


def _keep(name: str) -> bool:
    parts = [p.lower() for p in Path(name).parts]
    return not any(skip in part for part in parts for skip in SKIP_PARTS)


def _open_tar(archive: Path):
    """Ouvre une archive tar, compressée en gzip ou en zstandard."""
    if archive.name.endswith(".tar.zst"):
        try:
            import zstandard
        except ImportError as exc:  # pragma: no cover - dépendance de compilation
            raise Unavailable(
                "Archive .tar.zst : le paquet « zstandard » est requis "
                "(pip install 'cairn[build]')."
            ) from exc
        stream = zstandard.ZstdDecompressor().stream_reader(archive.open("rb"))
        return tarfile.open(fileobj=stream, mode="r|")
    return tarfile.open(archive)


def extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)

    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(destination, members=[m for m in zf.namelist() if _keep(m)])
    else:
        with _open_tar(archive) as tf:
            # Un flux zstandard ne se parcourt qu'une fois : on filtre au vol
            # plutôt que de demander la liste complète des membres.
            for member in tf:
                if _keep(member.name):
                    tf.extract(member, destination, filter="data")

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
    raise Unavailable(f"Binaire ollama introuvable sous {root}")


def pull_model(binary: Path, model: str) -> None:
    """Démarre le moteur téléchargé et lui fait tirer le modèle."""
    MODELS.mkdir(parents=True, exist_ok=True)
    binary.chmod(0o755)

    env = os.environ.copy()
    env["OLLAMA_HOST"] = "127.0.0.1:11500"
    env["OLLAMA_MODELS"] = str(MODELS)
    # Le binaire cherche ses bibliothèques à côté de lui.
    lib = binary.parent.parent / "lib"
    if lib.is_dir():
        env["LD_LIBRARY_PATH"] = f"{lib / 'ollama'}:{env.get('LD_LIBRARY_PATH', '')}"

    print(f"Démarrage du moteur pour tirer {model}")
    server = subprocess.Popen(
        [str(binary), "serve"], env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if server.poll() is not None:
                detail = ""
                if server.stderr is not None:
                    detail = server.stderr.read().decode("utf-8", "replace")[-500:]
                raise Unavailable(f"Le moteur s'est arrêté au démarrage. {detail}")
            probe = subprocess.run(
                [str(binary), "list"], env=env, capture_output=True,
                timeout=20, check=False,
            )
            if probe.returncode == 0:
                break
            time.sleep(2)
        else:
            raise Unavailable("Le moteur n'a pas démarré dans le délai imparti.")

        result = subprocess.run(
            [str(binary), "pull", model], env=env, check=False, timeout=1800
        )
        if result.returncode != 0:
            raise Unavailable(f"Échec du téléchargement de {model}")
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
        "--optional",
        action="store_true",
        help="signaler l'échec sans faire échouer la compilation",
    )
    args = parser.parse_args()

    try:
        shutil.rmtree(VENDOR, ignore_errors=True)
        VENDOR.mkdir(parents=True, exist_ok=True)

        assets = published_assets(args.version)
        name, url = choose_asset(assets)

        archive = download(url, VENDOR / name)
        verify(archive, assets)
        extract(archive, RUNTIME)
        archive.unlink()

        binary = locate_binary(RUNTIME)
        print(f"  binaire : {binary.relative_to(VENDOR)}")
        pull_model(binary, args.model)

        total = sum(f.stat().st_size for f in VENDOR.rglob("*") if f.is_file())
        print(f"\nTotal embarqué : {total / 1e6:.0f} Mo")
        return 0

    except Unavailable as exc:
        # Sans moteur, l'application reste parfaitement utilisable : elle
        # demandera l'adresse d'une instance Ollama. Mieux vaut publier une
        # version amoindrie que rien du tout.
        print(f"\nMoteur non embarqué : {exc}", file=sys.stderr)
        shutil.rmtree(VENDOR, ignore_errors=True)
        return 0 if args.optional else 1


if __name__ == "__main__":
    sys.exit(main())
