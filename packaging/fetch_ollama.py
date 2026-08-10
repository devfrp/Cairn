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
  anciennes. On essaie donc plusieurs noms et on retient celui qui répond,
  sans passer par l'API de GitHub dont le quota est vite épuisé sur des
  machines de compilation partagées ;
* les archives Linux et Windows pèsent près d'un gigaoctet et demi parce
  qu'elles embarquent les bibliothèques CUDA et ROCm. On ne les extrait pas :
  un modèle de cette taille tourne très bien sur processeur.
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
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENDOR = HERE / "vendor"
RUNTIME = VENDOR / "ollama_runtime"
MODELS = VENDOR / "ollama_models"

DEFAULT_VERSION = "v0.32.6"
DEFAULT_MODEL = "qwen3:0.6b"

DOWNLOAD = "https://github.com/ollama/ollama/releases/download"

# Adresse du moteur démarré le temps de tirer le modèle.
HOST = "127.0.0.1:11500"

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


def asset_url(version: str, name: str) -> str:
    return f"{DOWNLOAD}/{version}/{name}"


def _available(url: str) -> bool:
    """Le fichier existe-t-il, sans le télécharger ?

    On interroge l'adresse de téléchargement plutôt que l'API : celle-ci
    limite à soixante appels par heure et par adresse IP, un plafond que les
    machines de compilation partagées atteignent sans peine — d'où des échecs
    intermittents et incompréhensibles.
    """
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return response.status == 200
    except urllib.error.HTTPError as exc:
        # Un code autre que 404 peut venir d'une redirection mal suivie : on
        # laisse le téléchargement trancher plutôt que d'écarter à tort.
        return exc.code != 404
    except Exception:  # noqa: BLE001 — réseau capricieux
        return True


def choose_asset(version: str) -> tuple[str, str]:
    key = (platform.system(), platform.machine())
    if key not in CANDIDATES:
        raise Unavailable(f"Plateforme non gérée pour l'embarquement : {key}")

    tried = []
    for name in CANDIDATES[key]:
        url = asset_url(version, name)
        if _available(url):
            return name, url
        tried.append(name)

    raise Unavailable(
        f"Aucune archive pour {key} dans {version} (essayé : {', '.join(tried)})"
    )


def download(url: str, target: Path) -> Path:
    print(f"Téléchargement de {url}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as response, target.open("wb") as out:  # noqa: S310
        shutil.copyfileobj(response, out, length=1024 * 1024)
    print(f"  {target.name} — {target.stat().st_size / 1e6:.0f} Mo")
    return target


def verify(archive: Path, version: str) -> None:
    """Compare l'empreinte publiée à celle du fichier reçu.

    On s'apprête à exécuter ce binaire : mieux vaut savoir qu'il est bien
    celui qu'Ollama a publié.
    """
    try:
        with urllib.request.urlopen(  # noqa: S310
            asset_url(version, "sha256sum.txt"), timeout=30
        ) as response:
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
    """Trouve l'exécutable dans l'arborescence extraite.

    Deux pièges, tous deux rencontrés en conditions réelles :

    * les archives contiennent un répertoire `lib/ollama` à côté de
      l'exécutable. Un test d'existence le retient volontiers, et Windows
      refuse ensuite d'exécuter un répertoire — d'où un « accès refusé »
      déroutant. On n'accepte donc que des fichiers ;
    * l'exécutable s'appelle `ollama.exe` sous Windows, `ollama` ailleurs :
      on cherche le nom de la plateforme courante en premier.
    """
    names = ("ollama.exe", "ollama") if platform.system() == "Windows" else ("ollama",)

    for name in names:
        for candidate in (root / name, root / "bin" / name):
            if candidate.is_file():
                return candidate

    for name in names:
        for found in sorted(root.rglob(name)):
            if found.is_file():
                return found

    listing = ", ".join(sorted(p.name for p in root.iterdir())) if root.is_dir() else "—"
    raise Unavailable(f"Exécutable ollama introuvable sous {root} (contenu : {listing})")


def _responding(host: str) -> bool:
    """Le serveur répond-il ?

    Interrogé en HTTP, et non par `ollama list` : la ligne de commande attend
    sans broncher un serveur qui n'est pas encore là, si bien que son propre
    délai d'attente finissait par expirer et faire échouer toute la
    récupération alors que le moteur allait très bien.
    """
    try:
        with urllib.request.urlopen(f"http://{host}/api/tags", timeout=5) as response:  # noqa: S310
            return response.status == 200
    except Exception:  # noqa: BLE001 — serveur pas encore prêt
        return False


def _tail(path: Path, limit: int = 700) -> str:
    try:
        return path.read_text("utf-8", errors="replace")[-limit:].strip()
    except OSError:
        return ""


def _library_env(binary: Path) -> dict[str, str]:
    """Indique au binaire où trouver ses bibliothèques.

    Elles vivent dans `lib/ollama`, tantôt à côté de l'exécutable, tantôt un
    cran au-dessus selon la disposition de l'archive.
    """
    for base in (binary.parent, binary.parent.parent):
        lib = base / "lib" / "ollama"
        if lib.is_dir():
            key = (
                "DYLD_LIBRARY_PATH"
                if platform.system() == "Darwin"
                else "LD_LIBRARY_PATH"
            )
            return {key: f"{lib}{os.pathsep}{os.environ.get(key, '')}"}
    return {}


def pull_model(binary: Path, model: str) -> None:
    """Démarre le moteur téléchargé et lui fait tirer le modèle."""
    MODELS.mkdir(parents=True, exist_ok=True)
    binary.chmod(0o755)

    env = os.environ.copy()
    env["OLLAMA_HOST"] = HOST
    env["OLLAMA_MODELS"] = str(MODELS)
    env.update(_library_env(binary))

    print(f"Démarrage du moteur pour tirer {model}")
    # Journal dans un fichier plutôt que dans un tuyau : `ollama serve` est
    # bavard au démarrage, et un tuyau que personne ne vide finit par se
    # remplir — le serveur se bloque alors sur son écriture et cesse de
    # répondre, sans jamais s'arrêter pour autant.
    log = VENDOR / "ollama-serve.log"
    with log.open("wb") as sink:
        server = subprocess.Popen(
            [str(binary), "serve"], env=env, stdout=sink, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    raise Unavailable(
                        f"Le moteur s'est arrêté au démarrage. {_tail(log)}"
                    )
                if _responding(HOST):
                    break
                time.sleep(2)
            else:
                raise Unavailable(
                    f"Le moteur n'a pas répondu en deux minutes. {_tail(log)}"
                )

            result = subprocess.run(
                [str(binary), "pull", model], env=env, check=False, timeout=1800
            )
            if result.returncode != 0:
                raise Unavailable(
                    f"Échec du téléchargement de {model}. {_tail(log)}"
                )
        finally:
            server.terminate()
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()

    log.unlink(missing_ok=True)

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

        name, url = choose_asset(args.version)

        archive = download(url, VENDOR / name)
        verify(archive, args.version)
        extract(archive, RUNTIME)
        archive.unlink()

        binary = locate_binary(RUNTIME)
        print(f"  binaire : {binary.relative_to(VENDOR)}")
        pull_model(binary, args.model)

        total = sum(f.stat().st_size for f in VENDOR.rglob("*") if f.is_file())
        print(f"\nTotal embarqué : {total / 1e6:.0f} Mo")
        return 0

    except Exception as exc:  # noqa: BLE001
        # Toute panne compte, pas seulement celles qu'on a prévues : archive
        # corrompue, disque plein, réseau coupé. Sans moteur, l'application
        # reste utilisable — elle demandera l'adresse d'une instance Ollama.
        # Mieux vaut publier une version amoindrie que rien du tout.
        print(f"\nMoteur non embarqué : {type(exc).__name__} : {exc}", file=sys.stderr)
        shutil.rmtree(VENDOR, ignore_errors=True)
        if args.optional:
            return 0
        raise


if __name__ == "__main__":
    sys.exit(main())
