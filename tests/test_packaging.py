"""Récupération du moteur Ollama, exécutée au moment de la compilation.

Ce script n'est pas importable comme le reste du paquet — il vit dans
`packaging/` et ne s'exécute que sur les machines de compilation. Il a pourtant
fait échouer la publication deux fois de suite, chaque fois pour une raison
qu'un test de quelques lignes aurait attrapée : un répertoire pris pour un
exécutable, puis un serveur muet parce que son journal n'était pas lu. D'où ce
fichier, joué avant les compilations.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "fetch_ollama", ROOT / "packaging" / "fetch_ollama.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetch = _load()


# --------------------------------------------------------------- exécutable


def test_locate_binary_ignore_les_repertoires(tmp_path: Path) -> None:
    """Les archives contiennent un répertoire `lib/ollama` : sous Windows, le
    retenir donnait un « accès refusé » à l'exécution."""
    (tmp_path / "lib" / "ollama").mkdir(parents=True)
    real = tmp_path / "bin" / "ollama"
    real.parent.mkdir()
    real.write_bytes(b"\x7fELF")

    assert fetch.locate_binary(tmp_path) == real


def test_locate_binary_a_la_racine(tmp_path: Path) -> None:
    binary = tmp_path / "ollama"
    binary.write_bytes(b"\x7fELF")
    assert fetch.locate_binary(tmp_path) == binary


def test_locate_binary_absent_explique_le_contenu(tmp_path: Path) -> None:
    (tmp_path / "lib").mkdir()
    with pytest.raises(fetch.Unavailable, match="lib"):
        fetch.locate_binary(tmp_path)


# ------------------------------------------------------------ bibliothèques


def test_library_env_un_cran_au_dessus(tmp_path: Path) -> None:
    """Disposition Linux : `bin/ollama` et `lib/ollama/` côte à côte."""
    (tmp_path / "lib" / "ollama").mkdir(parents=True)
    (tmp_path / "bin").mkdir()

    env = fetch._library_env(tmp_path / "bin" / "ollama")

    assert env
    assert str(tmp_path / "lib" / "ollama") in next(iter(env.values()))


def test_library_env_a_cote(tmp_path: Path) -> None:
    (tmp_path / "lib" / "ollama").mkdir(parents=True)
    assert fetch._library_env(tmp_path / "ollama")


def test_library_env_binaire_autonome(tmp_path: Path) -> None:
    """Le binaire macOS se suffit à lui-même : rien à déclarer."""
    assert fetch._library_env(tmp_path / "ollama") == {}


# ------------------------------------------------------- filtre d'extraction


@pytest.mark.parametrize(
    "name",
    ["lib/ollama/cuda_v12/libggml.so", "lib/ollama/rocm/libhipblas.so"],
)
def test_keep_ecarte_les_accelerateurs(name: str) -> None:
    assert not fetch._keep(name)


@pytest.mark.parametrize("name", ["bin/ollama", "lib/ollama/libggml-base.so"])
def test_keep_garde_le_reste(name: str) -> None:
    assert fetch._keep(name)


# ----------------------------------------------------------- sonde du moteur


def test_responding_sur_serveur_absent() -> None:
    # Port fermé : la sonde doit répondre non, sans lever ni s'éterniser.
    assert fetch._responding("127.0.0.1:1") is False


def test_responding_sur_serveur_present() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — signature imposée
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"models":[]}')

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host = f"127.0.0.1:{server.server_port}"
        assert fetch._responding(host) is True
    finally:
        server.shutdown()
        server.server_close()


FAUX_MOTEUR = '''\
import os, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"models":[]}')
    def log_message(self, *a):
        pass

if sys.argv[1] == "serve":
    host, port = os.environ["OLLAMA_HOST"].split(":")
    # Bien plus qu'un tuyau ne peut contenir : un serveur dont personne ne lit
    # la sortie se bloquerait ici, vivant mais muet.
    for i in range(3000):
        sys.stderr.write("journal " + "x" * 100 + "\\n")
    sys.stderr.flush()
    HTTPServer((host, int(port)), Handler).serve_forever()
else:
    root = os.environ["OLLAMA_MODELS"]
    os.makedirs(os.path.join(root, "blobs"), exist_ok=True)
    open(os.path.join(root, "blobs", "sha256-abc"), "wb").write(b"0" * 4096)
'''


@pytest.mark.skipif(sys.platform == "win32", reason="script exécutable POSIX")
def test_pull_model_survit_a_un_moteur_bavard(tmp_path: Path, monkeypatch) -> None:
    """Le moteur écrit beaucoup avant de servir.

    Sa sortie partait dans un tuyau que personne ne vidait : passé la taille
    du tampon, le serveur restait bloqué sur son écriture — vivant, donc
    jamais détecté comme mort, mais incapable de répondre. La récupération
    échouait alors sur un délai dépassé, plateforme par plateforme.
    """
    monkeypatch.setattr(fetch, "VENDOR", tmp_path)
    monkeypatch.setattr(fetch, "MODELS", tmp_path / "ollama_models")

    binary = tmp_path / "ollama"
    binary.write_text(f"#!{sys.executable}\n" + textwrap.dedent(FAUX_MOTEUR))
    binary.chmod(0o755)

    fetch.pull_model(binary, "qwen3:0.6b")

    assert (tmp_path / "ollama_models" / "blobs" / "sha256-abc").is_file()
    assert not (tmp_path / "ollama-serve.log").exists(), "journal laissé derrière"


@pytest.mark.skipif(sys.platform == "win32", reason="script exécutable POSIX")
def test_pull_model_signale_un_moteur_qui_ne_demarre_pas(
    tmp_path: Path, monkeypatch
) -> None:
    """Un moteur qui s'arrête doit être signalé avec son journal, pas attendu
    jusqu'au bout du délai."""
    monkeypatch.setattr(fetch, "VENDOR", tmp_path)
    monkeypatch.setattr(fetch, "MODELS", tmp_path / "ollama_models")

    binary = tmp_path / "ollama"
    binary.write_text(
        f"#!{sys.executable}\nimport sys; sys.stderr.write('port occupé\\n'); "
        "sys.exit(1)\n"
    )
    binary.chmod(0o755)

    start = time.monotonic()
    with pytest.raises(fetch.Unavailable, match="port occupé"):
        fetch.pull_model(binary, "qwen3:0.6b")
    assert time.monotonic() - start < 30, "le délai complet a été attendu"


def test_pull_model_ne_lance_rien_sans_binaire(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(fetch, "VENDOR", tmp_path)
    monkeypatch.setattr(fetch, "MODELS", tmp_path / "ollama_models")
    with pytest.raises((OSError, subprocess.SubprocessError)):
        fetch.pull_model(tmp_path / "introuvable", "qwen3:0.6b")
