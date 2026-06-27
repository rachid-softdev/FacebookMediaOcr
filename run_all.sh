#!/bin/bash
# Usage: ./run_all.sh [--systemd]
#   --systemd : mode service (pas de screen, logs en arriere-plan directement)
set -e

cd "$(dirname "$0")"
source .venv/bin/activate

# --- Lecture des groupes depuis groups.txt ---
# Format : nom:group_id (les lignes commençant par # sont ignorées)
GROUP_ENTRIES_FILE="groups.txt"
if [ ! -f "$GROUP_ENTRIES_FILE" ]; then
  echo "[!] $GROUP_ENTRIES_FILE introuvable"
  exit 1
fi

GROUP_ENTRIES=()
while IFS=: read -r name gid; do
  [[ -z "$name" || "$name" == \#* ]] && continue
  GROUP_ENTRIES+=("$name:$gid")
done < "$GROUP_ENTRIES_FILE"
echo "[$(date)] ${#GROUP_ENTRIES[@]} groupes chargés depuis $GROUP_ENTRIES_FILE"

# --- Nettoyage des logs ---
# On tronque les logs des runs precedents pour eviter l'accumulation
for entry in "${GROUP_ENTRIES[@]}"; do
  > "logs-${entry%%:*}.txt" 2>/dev/null || true
done

# --- Détection RAM pour limiter le parallélisme ---
# Chaque Chrome headless ~300 Mo. On utilise MemAvailable (RAM dispo reelle)
# pour ne pas etouffer les autres services (kimaki, commune-scraper, etc.).
total_ram_mb=$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 1024)
avail_ram_mb=$(awk '/MemAvailable/ {printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 512)
max_parallel=$(( (avail_ram_mb - 1024) / 300 ))
[ "$max_parallel" -lt 1 ] && max_parallel=1
echo "[$(date)] RAM totale : ${total_ram_mb} Mo | disponible : ${avail_ram_mb} Mo | Parallélisme max : $max_parallel"

launch_job() {
  local name="$1" gid="$2" mode="$3"
  if [ "$mode" = "--systemd" ]; then
    pkill -f "fb_selenium.py.*--name $name" 2>/dev/null || true
    nohup python fb_selenium.py --name "$name" --group-id "$gid" >> "logs-$name.txt" 2>&1 &
    echo "  [PID $!] $name"
  else
    screen -dmS "$name" bash -c "cd $(pwd) && source .venv/bin/activate && python fb_selenium.py --name $name --group-id $gid >> logs-$name.txt 2>&1"
    echo "  [screen] $name"
  fi
}

total=${#GROUP_ENTRIES[@]}
idx=0
for entry in "${GROUP_ENTRIES[@]}"; do
  name="${entry%%:*}"
  gid="${entry##*:}"
  idx=$((idx + 1))

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
