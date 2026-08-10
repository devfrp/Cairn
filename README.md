# Cairn

*[English version](README.en.md)*

Suivi santé et sport, chez vous. Cairn réunit vos données Garmin Connect et
Strava dans un fichier qui vous appartient, et rapproche récupération et
charge d'entraînement — ce qu'aucun des deux services ne fait, faute de se
parler.

Rien ne sort de votre machine, hormis les appels nécessaires pour récupérer
vos propres données.

## Ce que ça fait

- **Synchronisation quotidienne** des deux sources, avec conservation des
  réponses brutes et de vos fichiers `.fit` d'origine.
- **Plus de doublons** : une sortie enregistrée sur la montre remonte à la
  fois dans Garmin et dans Strava. Cairn n'en garde qu'une, avec les capteurs
  du premier et le matériel du second.
- **Récupération et charge** : sommeil, variabilité cardiaque et fréquence de
  repos d'une nuit, rapprochés des sorties du jour.
- **Charge chronique et aiguë**, comparaison à vos propres moyennes,
  détection de surcharge.
- **Analyse intégrée** : temps par zone de fréquence cardiaque, volume par
  discipline, charge semaine après semaine.
- **Résumé hebdomadaire** rédigé par un modèle qui tourne sur votre machine,
  et qu'on peut faire envoyer sur Discord.

Site du projet : <https://devfrp.github.io/Cairn/>

## Installer l'application

Cairn est une **application de bureau autonome** : elle emporte sa base de
données et son modèle. Publiée en AppImage sous Linux, en archive sous
Windows et en `.dmg` sous macOS, sans rien d'autre à installer :

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

Le plus simple est de relier les deux comptes depuis la page **Réglages** de
l'application. Ce qui suit ne concerne que le service en ligne de commande.

Garmin n'ouvrant pas son API aux particuliers, Cairn ouvre une session et
n'en garde que le jeton, chiffré et valable environ un an. Le mot de passe
n'est jamais conservé.

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

Le service tourne à l'heure définie par `CAIRN_SYNC_HOUR`. Tant que
l'historique n'est pas entièrement rapatrié, il enchaîne un lot de rattrapage
après la synchronisation du jour.

```bash
# état des curseurs et des dernières passes
cairn status

# passe manuelle
cairn sync --source garmin --mode incremental

# rattrapage d'historique à la demande
cairn sync --mode backfill
```

## Résumé hebdomadaire

**L'application emporte son propre modèle.** Rien à installer : Ollama et
`qwen3:0.6b` voyagent avec elle et démarrent à la demande sur un port local.
Le résumé fonctionne hors ligne et le texte ne quitte jamais la machine.
C'est ce qui explique les quelque 800 Mo de chaque version.

Si une adresse Ollama figure dans les réglages, c'est elle qui sert — utile
pour profiter d'un modèle plus gros qui tourne déjà ailleurs.

Cairn calcule lui-même les charges, les moyennes, les écarts à vos habitudes
et les signaux de fatigue. Le modèle ne fait que **mettre ces valeurs en
mots** : on ne lui demande jamais de compter. Sa réponse est ensuite
vérifiée, et tout chiffre absent du bilan la fait écarter au profit d'un
texte écrit d'avance — moins fluide, mais jamais faux.

C'est ce qui permet d'embarquer un modèle aussi petit : 0,6 milliard de
paramètres ne raisonne pas, mais rédige très correctement des valeurs déjà
établies.

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
  sync/            traduction des données, fusion des doublons, orchestration
  metrics/         charge chronique et aiguë, lignes de base, signaux
  insights/        résumé hebdomadaire, moteur embarqué, envoi Discord
packaging/         recettes PyInstaller, icônes, unité systemd
```

La base est un fichier SQLite organisé en trois couches, marquées par le
préfixe des tables : `raw_` (payloads bruts, source de vérité), sans préfixe
(modèle normalisé) et `marts_` (agrégats). Le brut n'est jamais jeté : si un
mapper doit être corrigé, la transformation se rejoue sans retaper les API.
