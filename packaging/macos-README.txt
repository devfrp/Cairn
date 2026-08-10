Cairn
=====

Glissez Cairn.app dans le dossier Applications.

L'application n'est ni signée ni notarisée par Apple : macOS la met en
quarantaine et refuse de l'ouvrir au premier lancement. Pour l'autoriser,
une seule commande dans le Terminal :

    xattr -dr com.apple.quarantine /Applications/Cairn.app

Ouvrez ensuite Cairn normalement.

Il n'y a rien d'autre à installer : Cairn emporte sa base de données, son
moteur de langage et son modèle. C'est ce qui explique sa taille.

Au premier démarrage, allez dans « Réglages » : choisissez où ranger votre
fichier de données, puis reliez vos comptes Garmin et Strava. La clé qui
chiffre vos jetons et le schéma de la base sont mis en place pour vous.

Ce fichier contient toutes vos données. Le sauvegarder, c'est sauvegarder
Cairn ; le copier sur une autre machine, c'est déménager.

Ligne de commande
-----------------

Le fichier « cairn (ligne de commande) » présent sur cette image est le même
outil sans interface, pour les tâches planifiées. À copier dans votre PATH :

    cp "/Volumes/Cairn/cairn (ligne de commande)" /usr/local/bin/cairn
    xattr -d com.apple.quarantine /usr/local/bin/cairn
    chmod +x /usr/local/bin/cairn
