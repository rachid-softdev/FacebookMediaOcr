#!/bin/bash
# Discovery mensuel des nouveaux groupes Facebook via Google
# Execute discover_groups.py avec --search-only pour trouver
# des groupes non encore decouverts.
#
# Utilisation :
#   ./discover_groups.sh                    # Mode manuel
#   ./discover_groups.sh --force            # Retraite tout
#
# Timer systemd associe : facebook-discover-groups.timer
set -e

cd "$(dirname "$0")"
source .venv/bin/activate
unset LD_PRELOAD

DATE=$(date +%Y%m%d-%H%M%S)
LOG="results/discover-groups-${DATE}.txt"
mkdir -p results

echo "[$(date)] Debut decouverte groupes..." | tee -a "$LOG"

# --search-only : Google uniquement, pas de pattern
# $@ permet de passer --force si besoin
python discover_groups.py --search-only "$@" 2>&1 | tee -a "$LOG"

echo "[$(date)] Termine" | tee -a "$LOG"
