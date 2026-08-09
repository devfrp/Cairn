Cairn
=====

Glissez Cairn.app dans le dossier Applications.

L'application n'est ni signée ni notarisée par Apple : macOS la met en
quarantaine et refuse de l'ouvrir au premier lancement. Pour l'autoriser,
une seule commande dans le Terminal :

    xattr -dr com.apple.quarantine /Applications/Cairn.app

Ouvrez ensuite Cairn normalement.

Au premier démarrage, allez dans « Réglages » : renseignez l'adresse de
votre base PostgreSQL, générez une clé de chiffrement, installez le schéma,
puis reliez vos comptes Garmin et Strava.

Le serveur lui-même (PostgreSQL avec TimescaleDB, et Grafana pour les
graphiques détaillés) s'installe séparément sur la machine qui héberge vos
données. Voir la documentation du projet.

Ligne de commande
-----------------

Le fichier « cairn (ligne de commande) » présent sur cette image est le même
outil sans interface, pour les tâches planifiées. À copier dans votre PATH :

    cp "/Volumes/Cairn/cairn (ligne de commande)" /usr/local/bin/cairn
    xattr -d com.apple.quarantine /usr/local/bin/cairn
    chmod +x /usr/local/bin/cairn
