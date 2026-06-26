#!/bin/bash
# Usage: ./run_all.sh [--systemd]
#   --systemd : mode service (pas de screen, logs en arriere-plan directement)
set -e

cd "$(dirname "$0")"
source .venv/bin/activate

# --- Lecture des groupes depuis groups.txt ---
# Format : nom:group_id (les lignes commençant par # sont ignorées)
GROUPS_FILE="groups.txt"
if [ ! -f "$GROUPS_FILE" ]; then
  echo "[!] $GROUPS_FILE introuvable"
  exit 1
fi

mapfile -t GROUPS < <(grep -v '^\s*#' "$GROUPS_FILE" | grep -v '^\s*$')
echo "[$(date)] ${#GROUPS[@]} groupes chargés depuis $GROUPS_FILE"

# --- Détection RAM pour limiter le parallélisme ---
# Chaque Chrome headless ~300 Mo. On garde 512 Mo de marge pour le système.
total_ram_mb=$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 1024)
max_parallel=$(( (total_ram_mb - 512) / 300 ))
if [ "$max_parallel" -lt 1 ]; then
  max_parallel=1
fi
echo "[$(date)] RAM totale : ${total_ram_mb} Mo — Parallélisme max : $max_parallel"

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

# Vérifier si un groupe est déjà terminé (state.json avec processed >= total)
is_group_done() {
  local name="$1"
  local state_file="state-${name}.json"
  if [ ! -f "$state_file" ]; then
    return 1  # pas de state = pas commencé
  fi
  # Vérifie si "processed" existe et est >= au nombre total de photos
  # Si le fichier state contient une clé "phase" mais pas "processed", on ignore
  local processed
  processed=$(python3 -c "import json; d=json.load(open('$state_file')); print(d.get('processed', -1))" 2>/dev/null || echo -1)
  [ "$processed" -ge 0 ] 2>/dev/null
  return $?
}

total=${#GROUPS[@]}
idx=0
for entry in "${GROUPS[@]}"; do
  name="${entry%%:*}"
  gid="${entry##*:}"
  idx=$((idx + 1))

  # Sauter les groupes déjà terminés (pour ne pas perdre de temps)
  if is_group_done "$name"; then
    echo "  [SKIP] $name (déjà terminé)"
    continue
  fi

  printf "  [%3d/%d] %s -> %s\n" "$idx" "$total" "$name" "$gid"

  if [ "$1" = "--systemd" ]; then
    while [ "$(jobs -r | wc -l)" -ge "$max_parallel" ]; do
      sleep 5
    done
    launch_job "$name" "$gid" "--systemd"
  else
    launch_job "$name" "$gid"
  fi
done

echo "[$(date)] Terminé"

if [ "$1" = "--systemd" ]; then
  echo "[$(date)] Attente de la fin des processus..."
  wait
  echo "[$(date)] Tous les groupes sont terminés"
fi
