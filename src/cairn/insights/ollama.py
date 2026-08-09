"""Client Ollama, et garde-fou sur ce que le modèle a le droit d'affirmer.

Cairn vise des modèles locaux de toute taille, y compris très petits. Un
modèle d'un demi-milliard de paramètres sait mettre en français des faits
qu'on lui fournit ; il ne sait pas compter. Deux conséquences sur la
conception :

* le prompt ne contient que des valeurs déjà calculées, jamais de données
  brutes à agréger ;
* la réponse est vérifiée — tout nombre absent des faits fournis fait
  rejeter le texte, qui est remplacé par une rédaction déterministe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx
import structlog

log = structlog.get_logger(__name__)

# Qwen3 raisonne à voix haute entre ces balises quand le mode réflexion est
# actif. On le désactive, et on nettoie tout de même au cas où.
THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")

# Une date et les minutes d'une durée ne sont pas des affirmations chiffrées :
# « semaine du 15/08 » ou « 6 h 20 » ne doivent pas être pris pour des
# inventions. On les retire avant l'analyse, en conservant les heures.
DATE = re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")
DURATION = re.compile(r"\b(\d+)\s*h\s*\d{2}\b")

# Tolérance d'arrondi : le modèle peut écrire « 6 h » là où les faits disent
# 6,2 — ce n'est pas une invention.
NUMBER_TOLERANCE = 0.5


class OllamaUnavailable(RuntimeError):
    """Le serveur Ollama ne répond pas, ou le modèle n'est pas disponible."""


@dataclass
class Generation:
    text: str
    model: str
    latency_ms: int
    verified: bool
    rejected_numbers: list[str]


@dataclass
class Endpoint:
    """Instance à interroger, et d'où elle vient."""

    url: str
    model: str
    embedded: bool

    @property
    def label(self) -> str:
        return "moteur embarqué" if self.embedded else self.url


def resolve(url: str = "", model: str = "") -> Endpoint:
    """Choisit l'instance à utiliser.

    Une adresse configurée l'emporte toujours : elle signale une instance
    choisie par l'utilisateur, souvent dotée d'un modèle plus gros que celui
    qu'on peut raisonnablement embarquer. Sinon on démarre le moteur fourni
    avec l'application.
    """
    from cairn.insights import embedded

    if url:
        return Endpoint(url=url.rstrip("/"), model=model, embedded=False)

    if not embedded.is_available():
        raise OllamaUnavailable(
            "Aucune adresse Ollama configurée et aucun moteur embarqué dans "
            "cette version. Renseignez CAIRN_OLLAMA_URL."
        )

    try:
        local_url = embedded.ensure_running()
    except RuntimeError as exc:
        raise OllamaUnavailable(f"Moteur embarqué indisponible : {exc}") from exc

    return Endpoint(
        url=local_url,
        # Le modèle embarqué prime sur la configuration : demander un modèle
        # absent du paquet ne mènerait qu'à une erreur.
        model=embedded.bundled_model() or model,
        embedded=True,
    )


class OllamaClient:
    def __init__(self, url: str, model: str, timeout: float = 120.0) -> None:
        if not url:
            raise OllamaUnavailable(
                "Aucune adresse Ollama configurée (CAIRN_OLLAMA_URL)."
            )
        self.url = url.rstrip("/")
        self.model = model
        self._timeout = timeout
        self.embedded = False

    @classmethod
    def from_settings(cls, url: str = "", model: str = "", timeout: float = 120.0):
        endpoint = resolve(url, model)
        client = cls(endpoint.url, endpoint.model, timeout)
        client.embedded = endpoint.embedded
        return client

    def available_models(self) -> list[str]:
        try:
            response = httpx.get(f"{self.url}/api/tags", timeout=10.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(f"Ollama injoignable sur {self.url} : {exc}") from exc
        return [m.get("name", "") for m in response.json().get("models", [])]

    def check(self) -> tuple[bool, str]:
        """État du service, sans lever : destiné à l'affichage."""
        try:
            models = self.available_models()
        except OllamaUnavailable as exc:
            return False, str(exc)

        # Ollama nomme les modèles « qwen3:0.6b » ; une configuration sans
        # étiquette doit quand même correspondre.
        wanted = self.model if ":" in self.model else f"{self.model}:latest"
        if wanted in models or self.model in models:
            return True, f"{self.model} disponible"
        return False, (
            f"{self.model} absent. Modèles présents : "
            f"{', '.join(models) if models else 'aucun'}. "
            f"Installer avec : ollama pull {self.model}"
        )

    def generate(self, prompt: str, system: str = "") -> tuple[str, int]:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            # Réponse factuelle : on veut de la reformulation, pas de
            # l'imagination.
            "options": {"temperature": 0.2, "top_p": 0.9, "num_predict": 400},
            # Ollama récent accepte de couper la réflexion des modèles qui
            # en font ; les autres ignorent simplement le champ.
            "think": False,
        }
        if system:
            payload["system"] = system

        try:
            response = httpx.post(
                f"{self.url}/api/generate", json=payload, timeout=self._timeout
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(f"Génération impossible : {exc}") from exc

        body = response.json()
        text = THINK_BLOCK.sub("", body.get("response", "")).strip()
        latency = int(body.get("total_duration", 0) / 1_000_000)
        return text, latency


# ---------------------------------------------------------------------
# Vérification
# ---------------------------------------------------------------------


def _numbers_in(text: str) -> list[float]:
    cleaned = DATE.sub(" ", text)
    cleaned = DURATION.sub(r" \1 ", cleaned)

    values = []
    for match in NUMBER.findall(cleaned):
        try:
            values.append(float(match.replace(",", ".")))
        except ValueError:
            continue
    return values


def verify_numbers(text: str, allowed: list[float]) -> list[str]:
    """Nombres du texte qui ne correspondent à aucun fait fourni.

    Les valeurs de zéro à douze sont tolérées sans correspondance : elles
    servent à compter des séances ou des jours et apparaissent naturellement
    dans une phrase.
    """
    rejected = []
    for value in _numbers_in(text):
        if 0 <= value <= 12:
            continue
        if any(abs(value - candidate) <= NUMBER_TOLERANCE for candidate in allowed):
            continue
        rejected.append(f"{value:g}")
    return rejected
