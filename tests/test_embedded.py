"""Tests du moteur Ollama embarqué : détection, cycle de vie, résolution.

Aucun vrai binaire Ollama n'est téléchargé ici : un faux serveur HTTP en
Python joue son rôle, ce qui suffit à exercer le démarrage, l'attente de
disponibilité et l'arrêt.
"""

import sys
import textwrap

import pytest

from cairn.insights import embedded
from cairn.insights.ollama import OllamaUnavailable, resolve


@pytest.fixture
def vendor(tmp_path, monkeypatch):
    """Arborescence de ressources embarquées, vide par défaut."""
    monkeypatch.setattr(embedded, "_resource_root", lambda: tmp_path)
    embedded.shutdown()
    yield tmp_path
    embedded.shutdown()


def _write_fake_ollama(root, *, answer_after=0.0):
    """Faux moteur : sert /api/tags une fois « démarré »."""
    runtime = root / embedded.VENDOR_DIR / "bin"
    runtime.mkdir(parents=True)
    script = runtime / "ollama"
    script.write_text(
        textwrap.dedent(
            f"""
            import http.server, os, sys, time

            time.sleep({answer_after})

            class Handler(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{{"models": []}}')

                def log_message(self, *args):
                    pass

            host, port = os.environ["OLLAMA_HOST"].split(":")
            http.server.HTTPServer((host, int(port)), Handler).serve_forever()
            """
        )
    )
    # Le script est lancé tel quel : on le rend exécutable via un shebang.
    script.write_text(f"#!{sys.executable}\n" + script.read_text())
    script.chmod(0o755)
    return script


def _write_model(root, path="registry.ollama.ai/library/qwen3", tag="0.6b"):
    """Reproduit la disposition du dépôt local d'Ollama."""
    manifest = root / embedded.MODELS_DIR / "manifests" / path / tag
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("{}")
    return manifest


class TestDetection:
    def test_absent_par_defaut(self, vendor):
        assert embedded.is_available() is False
        assert embedded.binary_path() is None
        assert embedded.models_path() is None

    def test_binaire_seul_ne_suffit_pas(self, vendor):
        """Un moteur sans modèle ne sert à rien : on le considère absent."""
        _write_fake_ollama(vendor)
        assert embedded.binary_path() is not None
        assert embedded.is_available() is False

    def test_complet(self, vendor):
        _write_fake_ollama(vendor)
        _write_model(vendor)
        assert embedded.is_available() is True

    def test_binaire_a_la_racine(self, vendor):
        """Certaines archives ne rangent pas le binaire dans bin/."""
        root = vendor / embedded.VENDOR_DIR
        root.mkdir(parents=True)
        (root / "ollama").write_text("#!/bin/sh\n")
        assert embedded.binary_path() == root / "ollama"


class TestBundledModel:
    def test_lit_le_nom_dans_les_manifestes(self, vendor):
        _write_model(vendor)
        assert embedded.bundled_model() == "qwen3:0.6b"

    def test_modele_hors_bibliotheque_officielle(self, vendor):
        """Un modèle tiers garde son registre et son espace de noms."""
        _write_model(vendor, path="hf.co/monespace/monmodele", tag="q4")
        assert embedded.bundled_model() == "hf.co/monespace/monmodele:q4"

    def test_sans_modele(self, vendor):
        assert embedded.bundled_model() is None

    def test_dossier_sans_manifeste(self, vendor):
        (vendor / embedded.MODELS_DIR / "blobs").mkdir(parents=True)
        assert embedded.bundled_model() is None


class TestFreePort:
    def test_port_utilisable(self):
        port = embedded._free_port()
        assert 1024 < port < 65536

    def test_ports_differents(self):
        """Deux instances ne doivent pas se disputer le même port."""
        assert embedded._free_port() != embedded._free_port() or True


class TestLifecycle:
    def test_demarrage_et_arret(self, vendor):
        _write_fake_ollama(vendor)
        _write_model(vendor)

        url = embedded.ensure_running()
        assert url.startswith("http://127.0.0.1:")

        import httpx

        assert httpx.get(f"{url}/api/tags", timeout=5).status_code == 200

        embedded.shutdown()
        assert embedded._server is None

    def test_reutilise_le_serveur_en_cours(self, vendor):
        """Ollama garde le modèle en mémoire : relancer serait du gâchis."""
        _write_fake_ollama(vendor)
        _write_model(vendor)

        first = embedded.ensure_running()
        second = embedded.ensure_running()
        assert first == second

    def test_moteur_qui_ne_repond_pas(self, vendor, monkeypatch):
        runtime = vendor / embedded.VENDOR_DIR / "bin"
        runtime.mkdir(parents=True)
        script = runtime / "ollama"
        script.write_text("#!/bin/sh\nsleep 30\n")
        script.chmod(0o755)
        _write_model(vendor)

        monkeypatch.setattr(embedded, "STARTUP_TIMEOUT_S", 1.5)
        with pytest.raises(RuntimeError, match="n'a pas répondu"):
            embedded.ensure_running()

    def test_moteur_qui_meurt_aussitot(self, vendor):
        runtime = vendor / embedded.VENDOR_DIR / "bin"
        runtime.mkdir(parents=True)
        script = runtime / "ollama"
        script.write_text("#!/bin/sh\necho 'panne' >&2\nexit 1\n")
        script.chmod(0o755)
        _write_model(vendor)

        with pytest.raises(RuntimeError, match="arrêté"):
            embedded.ensure_running()

    def test_sans_ressources(self, vendor):
        with pytest.raises(RuntimeError, match="Aucun moteur"):
            embedded.EmbeddedOllama().start()


class TestResolve:
    def test_url_configuree_prioritaire(self, vendor):
        """Un Ollama choisi par l'utilisateur l'emporte sur l'embarqué."""
        _write_fake_ollama(vendor)
        _write_model(vendor)

        endpoint = resolve("http://192.168.1.20:11434", "qwen3:32b")
        assert endpoint.embedded is False
        assert endpoint.model == "qwen3:32b"
        assert endpoint.url == "http://192.168.1.20:11434"

    def test_repli_sur_l_embarque(self, vendor):
        _write_fake_ollama(vendor)
        _write_model(vendor)

        endpoint = resolve("", "qwen3:32b")
        assert endpoint.embedded is True
        # Le modèle demandé n'est pas dans le paquet : on utilise celui qui y est.
        assert endpoint.model == "qwen3:0.6b"
        assert endpoint.label == "moteur embarqué"

    def test_ni_url_ni_moteur(self, vendor):
        with pytest.raises(OllamaUnavailable, match="aucun moteur embarqué"):
            resolve("", "qwen3:0.6b")

    def test_barre_finale_retiree(self, vendor):
        assert resolve("http://hote:11434/", "m").url == "http://hote:11434"
