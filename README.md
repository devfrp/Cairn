# Cairn

*[English version](README.en.md)*

Suivi santé et sport auto-hébergé. Cairn agrège les données Garmin Connect et
Strava dans une base PostgreSQL que vous contrôlez entièrement, et croise
récupération et charge d'entraînement — ce qu'aucun des deux services ne fait,
puisqu'ils ne se parlent pas.

Aucune donnée ne sort de votre serveur au-delà des appels d'API nécessaires à
la synchronisation.

## Ce que ça fait

- **Synchronisation quotidienne** des deux sources vers votre base, avec
  archivage du JSON brut et des fichiers `.fit` originaux.
- **Déduplication** : une sortie enregistrée sur la montre remonte dans Garmin
  *et* dans Strava. Cairn les reconnaît comme une seule activité et fusionne
  les métriques — capteurs côté Garmin, matériel et segments côté Strava.
- **Croisement récupération × charge** : sommeil, HRV et FC de repos d'une nuit
  en regard des sorties du jour.
- **Dashboards Grafana** versionnés dans le repo.
- **Résumé hebdomadaire** rédigé par un modèle Ollama qui tourne chez vous,
  livrable sur Discord.

Site du projet : <https://devfrp.github.io/Cairn/>

## Installer l'application

Cairn se compose de deux morceaux : une **base PostgreSQL** qui héberge vos
données, et une **application de bureau** qui s'y connecte. L'application est publiée en paquet autonome —
AppImage sous Linux, archive sous Windows, `.dmg` sous macOS — sans
interpréteur Python ni moteur de langage à installer :

```bash
curl -fsSL https://devfrp.github.io/Cairn/get.sh | sh
```

Les fichiers sont attachés à chaque
[release](https://github.com/devfrp/Cairn/releases/latest) avec leur empreinte
SHA-256. Ils ne sont ni signés ni notarisés : Windows et macOS avertiront au
premier lancement.

L'application est **portable** : placez un fichier `cairn.ini` à côté de
l'exécutable (bouton « Passer en mode portable » dans les réglages) et elle
devient entièrement autonome, utilisable depuis une clé USB.

La même release fournit la ligne de commande (`cairn-cli-*`), utile pour les
tâches planifiées ; elle est aussi incluse dans l'AppImage et le dmg.

## Prérequis

- Une machine avec PostgreSQL 16 et l'extension TimescaleDB (un conteneur
  LXC Debian fait très bien l'affaire).
- Un compte Garmin Connect.
- Une application Strava — à créer sur <https://www.strava.com/settings/api>.

## Installer la base

Sur Debian ou Ubuntu, avec les dépôts officiels de TimescaleDB :

```bash
sudo apt install -y postgresql-16 postgresql-16-timescaledb
sudo timescaledb-tune --quiet --yes && sudo systemctl restart postgresql
```

Créer le rôle et la base :

```bash
sudo -u postgres createuser --pwprompt cairn
sudo -u postgres createdb --owner=cairn cairn
```

L'application de bureau suffit ensuite : sa page **Réglages** renseigne la
chaîne de connexion, génère la clé de chiffrement et installe le schéma d'un
bouton.

## Synchronisation automatique

L'application synchronise à la demande. Pour une passe nocturne sans
intervention, installez le service sur la machine qui héberge la base :

```bash
sudo install -d -o cairn -g cairn /opt/cairn /var/lib/cairn
sudo -u cairn python3 -m venv /opt/cairn/venv
sudo -u cairn /opt/cairn/venv/bin/pip install 'cairn @ git+https://github.com/devfrp/Cairn'
```

Créer `/etc/cairn/cairn.env` à partir de [.env.example](.env.example) — il
contient la clé de chiffrement, donc en `0600` :

```bash
sudo install -D -m 600 .env.example /etc/cairn/cairn.env
sudo /opt/cairn/venv/bin/cairn keygen
```

Reporter la clé dans le fichier, puis activer le service :

```bash
sudo cp packaging/systemd/cairn.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now cairn
```

## Graphiques détaillés

L'application montre l'essentiel. Pour des graphiques fouillés, Grafana se
branche directement sur la base : le dossier [grafana/](grafana/) contient la
source de données et les dashboards, à copier dans
`/etc/grafana/provisioning/`.

## Authentification

L'application de bureau relie les deux comptes depuis sa page **Réglages**.
En ligne de commande, pour le service :

Garmin n'a pas d'API publique pour un usage individuel : Cairn ouvre une
session SSO et conserve le token OAuth (valable environ un an), chiffré en
base. Le mot de passe n'est pas conservé.

```bash
cairn auth garmin
```

Si la double authentification est active sur le compte, le code est demandé à
ce moment-là.

Pour Strava, le flux OAuth2 se fait en deux temps :

```bash
cairn auth strava
```

La commande affiche une URL. L'ouvrir, autoriser l'application, puis récupérer
le paramètre `code` dans l'URL de redirection et relancer :

```bash
cairn auth strava --code COLLER_LE_CODE_ICI
```

## Utilisation

Le service tourne à l'heure définie par `CAIRN_SYNC_HOUR`. Tant que l'historique n'est pas entièrement rapatrié, elle
enchaîne un lot de rattrapage après la synchronisation du jour.

```bash
# état des curseurs et des dernières passes
cairn status

# passe manuelle
cairn sync --source garmin --mode incremental

# rattrapage d'historique à la demande
cairn sync --mode backfill
```

## Résumé hebdomadaire

**L'application embarque son propre moteur de langage.** Rien à installer :
le paquet transporte Ollama et le modèle `qwen3:0.6b`, démarrés à la demande
sur un port local. Le résumé fonctionne hors ligne, et le texte ne quitte
jamais la machine. C'est ce qui explique la taille des paquets, de l'ordre de
800 Mo.

Si une adresse Ollama est renseignée dans les réglages, elle est utilisée à
la place du moteur embarqué — utile pour profiter d'un modèle plus gros
tournant déjà sur votre serveur.

Cairn calcule lui-même charges, moyennes, écarts à la ligne de base et
signaux de fatigue. Le modèle ne fait que **rédiger** ces valeurs : il ne lui
est jamais demandé de compter. La réponse est ensuite vérifiée — tout nombre
absent du bilan fait rejeter le texte au profit d'une rédaction déterministe,
moins fluide mais jamais fausse.

C'est ce qui permet d'embarquer un si petit modèle : 0,6 milliard de
paramètres ne raisonne pas, mais met parfaitement en français des valeurs
déjà établies.

```bash
cairn check-ollama
cairn summary
cairn summary --send
```

`--no-model` force la rédaction déterministe, utile pour comparer. Le
planificateur produit et envoie le résumé chaque semaine, le jour défini par
`CAIRN_SUMMARY_WEEKDAY`.

## Rythme du rattrapage d'historique

Les deux sources n'ont pas les mêmes contraintes :

| Source | Contrainte | Conséquence |
|---|---|---|
| Garmin | Une requête par jour et par métrique | ~7 requêtes par journée d'historique. `CAIRN_BACKFILL_DAYS_PER_RUN` borne le lot par passe. |
| Strava | ~100 requêtes / 15 min, ~1000 / jour | Les activités arrivent par pages de 200. Quand le quota journalier tombe, la passe s'arrête et reprend la nuit suivante. |

Dans les deux cas la position est mémorisée dans `sync_cursor` : une coupure
ne fait jamais recommencer depuis le début.

## Architecture

```
db/migrations/     schéma SQL, appliqué par « cairn db upgrade »
src/cairn/
  gui/             application de bureau (Qt) : pages, réglages, tâches de fond
  sources/         clients Garmin (garth) et Strava (OAuth2 + quota)
  sync/            mappers, déduplication, upserts, orchestration
packaging/         recettes PyInstaller, icônes, unité systemd
grafana/           source de données et dashboards provisionnés
```

La base est organisée en trois couches : `raw` (payloads bruts, source de
vérité), `public` (modèle normalisé) et `marts` (agrégats pour Grafana). Le
brut n'est jamais jeté : si un mapper doit être corrigé, la transformation se
rejoue sans retaper les API.

Les séries temporelles (streams d'activité, mesures intra-journée) sont des
hypertables TimescaleDB compressées.
