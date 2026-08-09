"""Produit les icônes natives à partir du SVG source.

Windows veut un .ico, macOS un .icns, Linux se contente du SVG. Plutôt que
de versionner des binaires générés, on les fabrique au moment de la
compilation : le SVG reste la seule source à maintenir.

Le rendu passe par Qt, déjà présent pour construire l'application — aucune
dépendance supplémentaire.
"""

from __future__ import annotations

import os
import struct
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "cairn.svg"

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICNS_SIZES = (16, 32, 64, 128, 256, 512, 1024)


def render(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(str(SOURCE)).render(painter)
    painter.end()
    return image


def png_bytes(image: QImage) -> bytes:
    # Le QByteArray doit rester vivant aussi longtemps que le QBuffer :
    # le passer en temporaire laisse une référence pendante côté C++.
    storage = QByteArray()
    buffer = QBuffer(storage)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(storage)


def write_ico(target: Path) -> None:
    """Assemble un .ico contenant des PNG.

    Le format ICO accepte des images PNG telles quelles depuis Windows Vista,
    ce qui évite d'avoir à produire du bitmap DIB à la main.
    """
    images = [(size, png_bytes(render(size))) for size in ICO_SIZES]

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)

    entries, payload = b"", b""
    for size, data in images:
        # 0 signifie 256 dans un ICONDIRENTRY.
        dimension = 0 if size >= 256 else size
        entries += struct.pack(
            "<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(data), offset
        )
        payload += data
        offset += len(data)

    target.write_bytes(header + entries + payload)


def write_png_set(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for size in ICNS_SIZES:
        (directory / f"icon_{size}x{size}.png").write_bytes(png_bytes(render(size)))
        if size <= 512:
            doubled = render(size * 2)
            (directory / f"icon_{size}x{size}@2x.png").write_bytes(png_bytes(doubled))


def write_icns(target: Path) -> bool:
    """Passe par `iconutil`, seul outil fiable pour produire un .icns."""
    iconset = target.with_suffix(".iconset")
    write_png_set(iconset)
    try:
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(target)],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"icns non généré ({exc}) — l'icône par défaut sera utilisée.")
        return False
    return True


def main() -> int:
    if not SOURCE.exists():
        print(f"SVG introuvable : {SOURCE}", file=sys.stderr)
        return 1

    app = QGuiApplication([])  # noqa: F841 — doit survivre à tous les rendus

    (HERE / "cairn.png").write_bytes(png_bytes(render(512)))
    print("écrit : cairn.png")

    write_ico(HERE / "cairn.ico")
    print("écrit : cairn.ico")

    if sys.platform == "darwin" and write_icns(HERE / "cairn.icns"):
        print("écrit : cairn.icns")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
