#!/bin/bash
# Usage: ./run_all.sh [--systemd] [--parallel N]
#   --systemd  : mode service (pas de screen, logs en arriere-plan directement)
#   --parallel N : nombre max de groupes en parallele (defaut: 2)
set -e

cd "$(dirname "$0")"
source .venv/bin/activate

# --- Lecture des groupes depuis groups.txt ---
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
for entry in "${GROUP_ENTRIES[@]}"; do
  > "logs-${entry%%:*}.txt" 2>/dev/null || true
done

# --- Parallélisme ---
# Par defaut 2 en parallele pour eviter de saturer le CPU/RAM.
# Surchargeable avec --parallel N.
max_parallel=2
mode="screen"
for arg in "$@"; do
  case "$arg" in
    --systemd) mode="systemd" ;;
    --parallel=*) max_parallel="${arg#*=}" ;;
  esac
done
echo "[$(date)] Parallélisme max : $max_parallel (mode: $mode)"

launch_job() {
  local name="$1" gid="$2" mode="$3"
  if [ "$mode" = "systemd" ]; then
    pkill -f "fb_selenium.py.*--name $name" 2>/dev/null || true
    nohup python fb_selenium.py --name "$name" --group-id "$gid" >> "logs-$name.txt" 2>&1 &
    echo "  [PID $!] $name"
  else
    screen -dmS "$name" bash -c "cd $(pwd) && source .venv/bin/activate && python fb_selenium.py --name $name --group-id $gid >> logs-$name.txt 2>&1"
    echo "  [screen] $name"
  fi
}

count_running() {
  if [ "$mode" = "systemd" ]; then
    jobs -r 2>/dev/null | wc -l
  else
    screen -ls 2>/dev/null | grep -cP 'emploi\d+' || true
  fi
}

total=${#GROUP_ENTRIES[@]}
idx=0
for entry in "${GROUP_ENTRIES[@]}"; do
  name="${entry%%:*}"
  gid="${entry##*:}"
  idx=$((idx + 1))

  # Attendre qu'une place se libere si on a atteint le max
  while [ "$(count_running)" -ge "$max_parallel" ]; do
    sleep 5
  done

  printf "  [%3d/%d] %s -> %s\n" "$idx" "$total" "$name" "$gid"
  launch_job "$name" "$gid" "$mode"
done

echo "[$(date)] Terminé"

if [ "$mode" = "systemd" ]; then
  echo "[$(date)] Attente de la fin des processus..."
  wait
  echo "[$(date)] Tous les groupes sont terminés"
fi
