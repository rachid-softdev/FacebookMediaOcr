#!/bin/bash
# Usage: ./run_all.sh [--systemd]
#   --systemd : mode service (pas de screen, logs en arriere-plan directement)
set -e

cd "$(dirname "$0")"
source .venv/bin/activate

GROUPS=(
  "saisonniers:362347087928780"
  "indre:offres.d.emploi.indre"
  "ardennes:offres.d.emploi.ardennes"
  "jobenisere:jobenisere"
)

echo "[$(date)] Lancement de ${#GROUPS[@]} groupes"

for entry in "${GROUPS[@]}"; do
  name="${entry%%:*}"
  gid="${entry##*:}"

  if [ "$1" = "--systemd" ]; then
    # Mode service : tuer l'ancien process s'il existe encore, puis lancer en arrière-plan
    pkill -f "fb_selenium.py.*--name $name" 2>/dev/null || true
    nohup python fb_selenium.py --live --name "$name" --group-id "$gid" >> "logs-$name.txt" 2>&1 &
    echo "  [PID $!] $name"
  else
    # Mode interactif : screen
    screen -dmS "$name" bash -c "cd $(pwd) && source .venv/bin/activate && python fb_selenium.py --live --name $name --group-id $gid >> logs-$name.txt 2>&1"
    echo "  [screen] $name"
  fi
done

echo "[$(date)] Terminé"

# En mode systemd, attendre que tous les processus se terminent
if [ "$1" = "--systemd" ]; then
  echo "[$(date)] Attente de la fin des processus..."
  wait
  echo "[$(date)] Tous les groupes sont terminés"
fi
