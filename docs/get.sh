#!/bin/sh
# Installateur de Cairn.
#
#   curl -fsSL https://devfrp.github.io/Cairn/get.sh | sh
#
# Télécharge l'application de bureau de la dernière version publiée, vérifie
# son empreinte SHA-256 et l'installe. La base de données s'installe
# séparément, sur la machine qui héberge vos données.

set -eu

REPO="devfrp/Cairn"

info()  { printf '%s\n' "$*"; }
warn()  { printf '%s\n' "$*" >&2; }
abort() { printf 'Erreur : %s\n' "$*" >&2; exit 1; }

command -v curl > /dev/null 2>&1 || abort "curl est requis mais introuvable."

# ---------------------------------------------------------------------
# Plateforme
# ---------------------------------------------------------------------

os="$(uname -s)"
arch="$(uname -m)"

case "$os" in
    Linux)
        case "$arch" in
            x86_64|amd64) asset="Cairn-x86_64.AppImage" ;;
            *) abort "Architecture Linux non publiée : $arch.
Installer depuis les sources : pip install 'cairn[gui] @ git+https://github.com/$REPO'" ;;
        esac
        ;;
    Darwin)
        case "$arch" in
            arm64)  asset="Cairn-macos-arm64.dmg" ;;
            x86_64) asset="Cairn-macos-x86_64.dmg" ;;
            *) abort "Architecture macOS inconnue : $arch" ;;
        esac
        ;;
    *)
        abort "Système non pris en charge par ce script : $os.
Sous Windows, télécharger Cairn-windows-x86_64.zip depuis
https://github.com/$REPO/releases/latest"
        ;;
esac

# ---------------------------------------------------------------------
# Téléchargement et vérification
# ---------------------------------------------------------------------

base="https://github.com/$REPO/releases/latest/download"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT INT TERM

info "Téléchargement de $asset (plusieurs centaines de Mo : moteur et modèle inclus)…"
curl -fsSL -o "$tmp/$asset" "$base/$asset" \
    || abort "Téléchargement impossible. Aucune version publiée pour cette plateforme ?"

if curl -fsSL -o "$tmp/$asset.sha256" "$base/$asset.sha256" 2>/dev/null; then
    expected="$(cut -d' ' -f1 < "$tmp/$asset.sha256")"
    if command -v sha256sum > /dev/null 2>&1; then
        actual="$(sha256sum "$tmp/$asset" | cut -d' ' -f1)"
    elif command -v shasum > /dev/null 2>&1; then
        actual="$(shasum -a 256 "$tmp/$asset" | cut -d' ' -f1)"
    else
        actual=""
        warn "Aucun outil sha256 disponible : vérification ignorée."
    fi
    if [ -n "$actual" ] && [ "$actual" != "$expected" ]; then
        abort "Empreinte SHA-256 incorrecte. Fichier corrompu ou altéré, installation annulée."
    fi
    [ -n "$actual" ] && info "Empreinte vérifiée."
else
    warn "Pas d'empreinte publiée pour cet artefact : vérification impossible."
fi

# ---------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------

case "$os" in
    Linux)
        # L'AppImage est un fichier unique et exécutable : on le pose dans le
        # PATH, et on déclare l'entrée de menu si l'environnement en tient un.
        if [ -n "${CAIRN_INSTALL_DIR:-}" ]; then
            dest="$CAIRN_INSTALL_DIR"
        elif [ -w "/usr/local/bin" ]; then
            dest="/usr/local/bin"
        else
            dest="$HOME/.local/bin"
        fi
        mkdir -p "$dest"
        mv "$tmp/$asset" "$dest/Cairn"
        chmod +x "$dest/Cairn"
        installed="$dest/Cairn"

        apps="$HOME/.local/share/applications"
        if [ -d "$HOME/.local/share" ]; then
            mkdir -p "$apps"
            cat > "$apps/cairn.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Cairn
GenericName=Suivi santé et sport
Comment=Réunit vos données Garmin et Strava dans une base que vous contrôlez
Exec=$installed
Terminal=false
Categories=Utility;
EOF
            command -v update-desktop-database > /dev/null 2>&1 \
                && update-desktop-database "$apps" 2>/dev/null || true
            info "Entrée de menu créée."
        fi

        case ":$PATH:" in
            *":$dest:"*) ;;
            *)
                warn "$dest n'est pas dans votre PATH. Ajouter à votre profil :"
                warn "    export PATH=\"$dest:\$PATH\""
                ;;
        esac
        ;;

    Darwin)
        info "Montage de l'image disque…"
        mountpoint="$tmp/mnt"
        mkdir -p "$mountpoint"
        hdiutil attach -nobrowse -quiet -mountpoint "$mountpoint" "$tmp/$asset" \
            || abort "Impossible de monter $asset"

        # Le dmg contient un bundle Cairn.app, pas un exécutable nu.
        if [ ! -d "$mountpoint/Cairn.app" ]; then
            hdiutil detach -quiet "$mountpoint" || true
            abort "Cairn.app introuvable dans l'image disque"
        fi

        dest="${CAIRN_INSTALL_DIR:-/Applications}"
        [ -w "$dest" ] || dest="$HOME/Applications"
        mkdir -p "$dest"
        rm -rf "$dest/Cairn.app"
        cp -R "$mountpoint/Cairn.app" "$dest/Cairn.app"
        hdiutil detach -quiet "$mountpoint" || true

        # Sans cela, macOS refuse d'ouvrir une application non notarisée.
        xattr -dr com.apple.quarantine "$dest/Cairn.app" 2>/dev/null || true
        installed="$dest/Cairn.app"
        ;;
esac

info ""
info "Cairn installé : $installed"
info ""
info "Au premier démarrage, ouvrez « Réglages » pour indiquer l'adresse de"
info "votre base, installer le schéma, puis relier Garmin et Strava."
info ""
info "Documentation et code source :"
info "    https://github.com/$REPO"
