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

# --- Détection RAM pour limiter le parallélisme ---
# Chaque Chrome headless ~300 Mo. On garde 512 Mo de marge pour le système.
total_ram_mb=$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 1024)
max_parallel=$(( (total_ram_mb - 512) / 300 ))
if [ "$max_parallel" -lt 1 ]; then
  max_parallel=1
fi
echo "[$(date)] RAM totale : ${total_ram_mb} Mo — Parallélisme max : $max_parallel"

echo "[$(date)] Lancement de ${#GROUPS[@]} groupes"

launch_job() {
  local name="$1" gid="$2" mode="$3"
  if [ "$mode" = "--systemd" ]; then
    pkill -f "fb_selenium.py.*--name $name" 2>/dev/null || true
    nohup python fb_selenium.py --live --name "$name" --group-id "$gid" >> "logs-$name.txt" 2>&1 &
    echo "  [PID $!] $name"
  else
    screen -dmS "$name" bash -c "cd $(pwd) && source .venv/bin/activate && python fb_selenium.py --live --name $name --group-id $gid >> logs-$name.txt 2>&1"
    echo "  [screen] $name"
  fi
}

for entry in "${GROUPS[@]}"; do
  name="${entry%%:*}"
  gid="${entry##*:}"

  if [ "$1" = "--systemd" ]; then
    # Attendre si on a atteint la limite de parallélisme
    while [ "$(jobs -r | wc -l)" -ge "$max_parallel" ]; do
      sleep 5
    done
    launch_job "$name" "$gid" "--systemd"
  else
    # Mode screen : pas de limite, tout en parallèle
    launch_job "$name" "$gid"
  fi
done

echo "[$(date)] Terminé"

if [ "$1" = "--systemd" ]; then
  echo "[$(date)] Attente de la fin des processus..."
  wait
  echo "[$(date)] Tous les groupes sont terminés"
fi
