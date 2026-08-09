# Cairn

*[English version](README.en.md)*

Suivi santé et sport auto-hébergé. Cairn agrège les données Garmin Connect et
Strava dans un fichier que vous contrôlez entièrement, et croise
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
- **Analyse intégrée** : temps par zone de fréquence cardiaque, volume par
  discipline, charge semaine après semaine.
- **Résumé hebdomadaire** rédigé par un modèle Ollama qui tourne chez vous,
  livrable sur Discord.

Site du projet : <https://devfrp.github.io/Cairn/>

## Installer l'application

Cairn est une **application de bureau autonome** : elle transporte sa base de
données, son moteur de langage et son modèle. Publiée en AppImage sous Linux,
archive sous Windows et `.dmg` sous macOS, sans rien d'autre à installer :

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

- Un compte Garmin Connect.
- Une application Strava — à créer sur <https://www.strava.com/settings/api>.

## Premier démarrage

Il n'y a rien à préparer. À l'ouverture, la page **Réglages** propose un
emplacement pour le fichier de données, génère la clé qui chiffrera les
jetons, installe le schéma et relie les comptes.

Le fichier `cairn.db` contient toutes vos données. Le sauvegarder, c'est
sauvegarder Cairn ; le copier sur une autre machine, c'est déménager.

## Synchronisation automatique

L'application synchronise à la demande. Pour une passe nocturne sans
intervention, sur une machine qui reste allumée :

```bash
sudo install -d -o cairn -g cairn /opt/cairn /var/lib/cairn
sudo -u cairn python3 -m venv /opt/cairn/venv
sudo -u cairn /opt/cairn/venv/bin/pip install 'cairn @ git+https://github.com/devfrp/Cairn'
sudo install -D -m 600 .env.example /etc/cairn/cairn.env
sudo cp packaging/systemd/cairn.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now cairn
```

Le service partage le fichier de données de l'application : indiquez le même
chemin dans `CAIRN_DB_PATH`.

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
```

La base est un fichier SQLite organisé en trois couches, marquées par le
préfixe des tables : `raw_` (payloads bruts, source de vérité), sans préfixe
(modèle normalisé) et `marts_` (agrégats). Le brut n'est jamais jeté : si un
mapper doit être corrigé, la transformation se rejoue sans retaper les API.
