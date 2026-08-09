"""Serveur Ollama embarqué dans l'application.

L'application transporte son propre binaire Ollama et son propre modèle : au
premier résumé, elle démarre un serveur local sur un port libre et le pilote
comme n'importe quelle instance. Rien à installer, rien à télécharger, et le
texte ne quitte jamais la machine.

Une instance externe reste prioritaire : qui possède déjà un Ollama avec un
gros modèle a tout intérêt à s'en servir, la rédaction y gagne en style.
L'embarqué est le filet — il garantit que la fonction marche partout.
"""

from __future__ import annotations

import atexit
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import structlog

log = structlog.get_logger(__name__)

# Nom du dossier de ressources produit à la compilation.
VENDOR_DIR = "ollama_runtime"
MODELS_DIR = "ollama_models"

# Démarrage à froid d'Ollama : chargement du binaire puis ouverture du port.
STARTUP_TIMEOUT_S = 45.0
POLL_INTERVAL_S = 0.4

_server: EmbeddedOllama | None = None
_lock = threading.Lock()


def _resource_root() -> Path:
    """Racine des ressources, empaquetées ou issues du dépôt."""
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parents[3] / "packaging" / "vendor"


def binary_path() -> Path | None:
    """Chemin du binaire Ollama embarqué, s'il a été inclus."""
    root = _resource_root() / VENDOR_DIR
    for name in ("ollama", "ollama.exe"):
        candidate = root / "bin" / name
        if candidate.exists():
            return candidate
        candidate = root / name
        if candidate.exists():
            return candidate
    return None


def models_path() -> Path | None:
    candidate = _resource_root() / MODELS_DIR
    return candidate if candidate.exists() else None


def is_available() -> bool:
    return binary_path() is not None and models_path() is not None


def _free_port() -> int:
    """Port libre attribué par le système, pour ne pas heurter un Ollama existant."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class EmbeddedOllama:
    """Cycle de vie du serveur local."""

    def __init__(self) -> None:
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self._process: subprocess.Popen | None = None

    def start(self) -> str:
        binary = binary_path()
        models = models_path()
        if binary is None or models is None:
            raise RuntimeError("Aucun moteur Ollama embarqué dans cette version.")

        # PyInstaller ne conserve pas le bit d'exécution des ressources
        # embarquées : on le repose avant chaque démarrage.
        if not os.access(binary, os.X_OK):
            binary.chmod(0o755)

        env = os.environ.copy()
        env["OLLAMA_HOST"] = f"127.0.0.1:{self.port}"
        env["OLLAMA_MODELS"] = str(models)
        # Le serveur ne sert que cette application : ni découverte réseau,
        # ni remontée d'usage.
        env["OLLAMA_ORIGINS"] = "http://127.0.0.1"
        env["OLLAMA_NOHISTORY"] = "1"
        env["OLLAMA_KEEP_ALIVE"] = "5m"

        creation_flags = 0
        if sys.platform == "win32":
            # Sans cela, une fenêtre de console s'ouvre derrière l'application.
            creation_flags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

        log.info("ollama embarqué : démarrage", port=self.port)
        self._process = subprocess.Popen(
            [str(binary), "serve"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=creation_flags,
        )
        atexit.register(self.stop)

        self._wait_until_ready()
        return self.url

    def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + STARTUP_TIMEOUT_S
        while time.monotonic() < deadline:
            if self._process is not None and self._process.poll() is not None:
                detail = ""
                if self._process.stderr is not None:
                    detail = self._process.stderr.read().decode("utf-8", "replace")[-400:]
                raise RuntimeError(f"Le moteur embarqué s'est arrêté. {detail}")
            try:
                response = httpx.get(f"{self.url}/api/tags", timeout=2.0)
                if response.status_code == 200:
                    log.info("ollama embarqué : prêt", url=self.url)
                    return
            except httpx.HTTPError:
                pass
            time.sleep(POLL_INTERVAL_S)

        self.stop()
        raise RuntimeError(
            f"Le moteur embarqué n'a pas répondu en {STARTUP_TIMEOUT_S:.0f} s."
        )

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def stop(self) -> None:
        if self._process is None:
            return
        log.info("ollama embarqué : arrêt")
        self._process.terminate()
        try:
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._process.kill()
        self._process = None


def ensure_running() -> str:
    """Démarre le serveur embarqué si besoin et retourne son adresse.

    Réutilise l'instance déjà lancée : Ollama garde le modèle en mémoire,
    et le second résumé est bien plus rapide que le premier.
    """
    global _server
    with _lock:
        if _server is not None and _server.running:
            return _server.url
        _server = EmbeddedOllama()
        return _server.start()


def shutdown() -> None:
    global _server
    with _lock:
        if _server is not None:
            _server.stop()
            _server = None


def bundled_model() -> str | None:
    """Nom du modèle embarqué, lu dans les manifestes déposés au build.

    Le dossier de modèles d'Ollama place ses manifestes sous
    `manifests/<registre>/<espace>/<nom>/<étiquette>` : le chemin porte donc
    le nom, sans qu'il faille le coder en dur.
    """
    models = models_path()
    if models is None:
        return None

    manifests = models / "manifests"
    if not manifests.is_dir():
        return None

    for tag_file in sorted(manifests.rglob("*")):
        if not tag_file.is_file():
            continue
        parts = list(tag_file.relative_to(manifests).parts)
        if len(parts) < 2:
            continue

        tag = parts.pop()
        # Le registre par défaut et l'espace « library » sont implicites :
        # « qwen3:0.6b », pas « registry.ollama.ai/library/qwen3:0.6b ».
        # Tout le reste fait partie du nom.
        if parts and parts[0] == "registry.ollama.ai":
            parts.pop(0)
        if parts and parts[0] == "library":
            parts.pop(0)
        if not parts:
            continue
        return f"{'/'.join(parts)}:{tag}"
    return None
