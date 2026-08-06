#!/bin/sh
# Démarre un serveur X sans écran avant de passer la main à la commande du conteneur.
#
# Raison d'être : Air Canada refuse le parcours de réservation à un Chromium lancé en mode
# « headless » — la recherche aboutit alors à une page d'erreur générique (BKRW-DBS-999), sans
# aucun code HTTP en échec, la détection se faisant côté client. Le même parcours, avec le même
# navigateur, aboutit normalement dès lors que celui-ci s'ouvre dans une vraie fenêtre. Xvfb
# fournit cette fenêtre sans matériel d'affichage. Les autres sources (Google Flights, Air Transat)
# n'en ont pas besoin et ne sont pas affectées.
set -e

if [ -n "$DISPLAY" ]; then
    ecran="${DISPLAY#:}"
    socket="/tmp/.X11-unix/X${ecran}"

    # Les résidus d'un cycle précédent sont supprimés avant tout. `docker compose restart` relance
    # le processus sans recréer le système de fichiers : le socket et le verrou survivent au
    # serveur qui les avait créés. Un entrypoint qui conclut « le socket existe, donc Xvfb tourne »
    # ne relançait alors jamais rien, et Chromium échouait sur « Missing X server » — c'est-à-dire
    # qu'Air Canada tombait en panne à chaque redémarrage, sans que rien ne le signale. Aucun
    # processus ne survit à ce point du démarrage : ces fichiers ne peuvent qu'être orphelins.
    rm -f "$socket" "/tmp/.X${ecran}-lock"

    Xvfb "$DISPLAY" -screen 0 1440x900x24 -nolisten tcp >/dev/null 2>&1 &

    # Laisser le serveur créer son socket : un navigateur lancé trop tôt échouerait à s'y
    # connecter, et l'échec ressemblerait à une panne de la source.
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        [ -e "$socket" ] && break
        sleep 0.3
    done

    if [ ! -e "$socket" ]; then
        echo "docker-entrypoint: Xvfb n'a pas démarré sur $DISPLAY ; Air Canada échouera" >&2
    fi
fi

exec "$@"
