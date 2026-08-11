"""Fenêtre de connexion à un service, dans un navigateur intégré.

Garmin et Strava demandent tous deux à l'utilisateur de s'identifier chez eux,
puis remettent à l'application un jeton à usage unique glissé dans une adresse.
Ce qu'ils ne font pas de la même façon :

* **Strava** redirige franchement le navigateur vers l'adresse de rappel, avec
  le paramètre `code`. Un simple suivi de la barre d'adresse suffirait.
* **Garmin** ne redirige pas : sa page de succès appelle l'adresse de service
  en arrière-plan, en JSONP, avec le paramètre `ticket`. Rien n'apparaît dans
  la barre d'adresse, et l'appel échoue d'ailleurs — l'adresse visée n'est pas
  une vraie page. Seule une inspection des requêtes le voit passer.

D'où deux guetteurs pour un seul paramètre. Le second ne coûte rien et évite de
redécouvrir le problème au prochain service.

Dans les deux cas, le mot de passe et l'éventuel code de double
authentification restent entre l'utilisateur et le service : ils ne traversent
jamais le code de Cairn, qui ne peut pas les lire. La session est sans mémoire,
rien n'est écrit sur le disque, et la fenêtre repart vierge à chaque fois.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)


def extract_param(url: str, name: str) -> str | None:
    """Valeur d'un paramètre de requête, ou None s'il est absent ou vide."""
    values = parse_qs(urlparse(url).query).get(name)
    if not values:
        return None
    return values[0].strip() or None


def target_of(url: str) -> str:
    """Adresse à laquelle le jeton a été remis, sans ses paramètres.

    Un service ne s'engage pas à renvoyer vers l'adresse demandée. Comme
    l'échange exige parfois l'adresse exacte, on retient celle qu'on a vue
    passer plutôt que celle qu'on avait réclamée.
    """
    parts = urlparse(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


class ParamInterceptor(QWebEngineUrlRequestInterceptor):
    """Guette un paramètre dans toutes les requêtes, navigations comprises."""

    found = Signal(str, str)

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._name = name

    def interceptRequest(self, info) -> None:  # noqa: N802 — signature Qt
        # Appelé depuis un fil du moteur de rendu : on se contente d'émettre,
        # la connexion en file d'attente ramène le travail au fil principal.
        url = info.requestUrl().toString()
        value = extract_param(url, self._name)
        if value:
            self.found.emit(value, target_of(url))


class OAuthDialog(QDialog):
    """Identification chez un service, dont on ne retire qu'un paramètre.

    `param` est le nom de ce paramètre : `ticket` chez Garmin, `code` chez
    Strava. La fenêtre se referme d'elle-même dès qu'il apparaît.
    """

    def __init__(
        self,
        url: str,
        *,
        param: str,
        title: str,
        notice: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.value: str | None = None
        self.target: str | None = None
        self._param = param

        self.setWindowTitle(title)
        self.resize(560, 720)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        label = QLabel(notice)
        label.setWordWrap(True)
        label.setContentsMargins(14, 12, 14, 12)
        layout.addWidget(label)

        # Profil sans mémoire : ni cookies ni cache ne survivent à la fenêtre.
        self._profile = QWebEngineProfile(self)
        self.view = QWebEngineView(self)
        # La page est rattachée au profil, et non à la vue : Qt détruit un
        # objet après ses enfants, ce qui garantit que la page parte en
        # premier. L'ordre inverse laisse le moteur travailler sur un profil
        # déjà libéré — Qt le signale, puis plante à l'occasion.
        self._page = QWebEnginePage(self._profile, self._profile)
        self.view.setPage(self._page)
        self.view.urlChanged.connect(self._on_url)

        self._interceptor = ParamInterceptor(param, self)
        self._interceptor.found.connect(
            self._on_value, Qt.ConnectionType.QueuedConnection
        )
        self._profile.setUrlRequestInterceptor(self._interceptor)

        layout.addWidget(self.view, 1)

        buttons = QDialogButtonBox()
        # Libellé posé en clair : les traductions de Qt ne sont pas dans le
        # paquet, et le bouton s'affichait « Cancel » dans une interface
        # française.
        annuler = buttons.addButton("Annuler", QDialogButtonBox.ButtonRole.RejectRole)
        annuler.setCursor(Qt.CursorShape.PointingHandCursor)
        buttons.rejected.connect(self.reject)
        buttons.setContentsMargins(14, 8, 14, 12)
        layout.addWidget(buttons)

        self.view.setUrl(QUrl(url))

    def _on_url(self, url: QUrl) -> None:
        raw = url.toString()
        value = extract_param(raw, self._param)
        if value is not None:
            self._on_value(value, target_of(raw))

    def _on_value(self, value: str, target: str) -> None:
        # Les deux guetteurs peuvent tirer sur le même paramètre : le premier
        # arrivé gagne, le second ne doit pas refermer une fenêtre déjà close.
        if self.value is not None:
            return
        self.value = value
        self.target = target
        self.view.stop()
        self.accept()
