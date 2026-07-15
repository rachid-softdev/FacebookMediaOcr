#!/usr/bin/env python3
"""Supervisor : traite les groupes avec reprise auto (max 5 tentatives).

Usage:
    ./supervisor.py   # Séquentiel, reprend automatiquement après coupure

État sauvegardé dans results/supervisor-state.json.
"""

import json
import os
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "results" / "supervisor-state.json"
PYTHON = str(BASE_DIR / ".venv" / "bin" / "python")
if not os.path.isfile(PYTHON):
    PYTHON = "python3"


log = lambda msg: print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def load_groups():
    """75 groupes : jamais lancés + interrompus (221-227)."""
    needed = {
        "group1239984249362769", "group1781770712071034", "group1035032955231685",
        "offresdemploiauluxembourg", "group1652543448641687", "group1470594153201221",
        "group214288905360691", "contrat.alternance", "emploi.agriculture.normandie",
        "emploi.stage.cdd.cdi.alternance.bordeaux", "emploi.stage.cdd.cdi.alternance.paris",
        "emploi40", "emploi51", "emploi75", "emploi79", "emploi80", "emploi82",
        "emploi85", "emploi88", "emploi89", "emploi91", "emploi94",
        "emploisenoutaouais", "fitinilyon", "fitininantes",
        "group1036742081614389", "group1110773543929238", "group1121280326550949",
        "group1149565206452921", "group1154665284629154", "group117161029065747",
        "group121196445246262", "group1240117446041933", "group1467539626889766",
        "group148249565526597", "group158935271182204", "group164123750775144",
        "group1795496124020320", "group2065591457097887", "group2072633816361298",
        "group2217851028525768", "group262440833864261", "group2686291441436189",
        "group304134176415499", "group306307694516352", "group309430419160333",
        "group324593501390247", "group325578587529032", "group338477970072949",
        "group343809057878", "group353965164784344", "group356034047104",
        "group391143770994648", "group406082302914012", "group498486570904822",
        "group50908151085", "group514140750546755", "group521645314682007",
        "group526252660745473", "group584944826848923", "group668062847148933",
        "group675444297702089", "group753410779542445", "group806072944672431",
        "group835185469996722", "group840103729779255", "group877274142348757",
        "group988162368463601", "offres.d.emploi.eure", "offres.d.emploi.haute.garonne",
        "offres.emploi.saisonniers.oleron", "offresemploiain",
        "staffdebaretrestaurantlorient", "staffdebaretrestauranttoulon", "zoneanimation",
    }

    entries, pos = [], 0
    with open(BASE_DIR / "data" / "groups.txt") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, gid = line.split(":", 1)
            if name in needed:
                pos += 1
                entries.append((name, gid, f"{pos}/{len(needed)}"))
    return entries


def main():
    running = True
    def cleanup(signum=None, frame=None):
        nonlocal running
        running = False
        log("Arrêt demandé…")
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    groups = load_groups()
    total = len(groups)
    log(f"{total} groupes à traiter")

    # Charger état
    idx, retries = 0, {}
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            data = json.load(f)
            idx = data.get("idx", 0)
            retries = data.get("retries", {})
        if idx > 0:
            log(f"Reprise à l'index {idx}/{total}")

    save_state = lambda: STATE_FILE.write_text(json.dumps({"idx": idx, "retries": retries}))

    while running and idx < total:
        name, gid, group_pos = groups[idx]
        attempts = retries.get(name, 0)

        if attempts >= 5:
            log(f"[{group_pos}] {name} — ÉCHEC x5, passé")
            idx += 1
            save_state()
            continue

        log(f"[{group_pos}] {name} → {gid} (tentative {attempts + 1}/5)")

        log_file = str(BASE_DIR / "results" / f"logs-{name}.txt")
        with open(log_file, "a") as lf:
            lf.write(f"\n--- [supervisor] Début {name} ({group_pos}) tentative {attempts + 1}/5 ---\n")

        proc = subprocess.Popen(
            [PYTHON, "fb_selenium.py", "--name", name, "--group-id", gid, "--group-pos", group_pos],
            stdout=open(log_file, "a"), stderr=subprocess.STDOUT, cwd=str(BASE_DIR),
        )
        print(f"  [PID {proc.pid}] {name}", flush=True)

        while running and proc.poll() is None:
            time.sleep(2)

        if not running:
            save_state()
            log(f"Interrompu à {idx}/{total}")
            return

        exit_code = proc.poll()

        if exit_code == 0:
            log(f"[{group_pos}] {name} — ✅ OK")
            retries.pop(name, None)
            idx += 1
        else:
            log(f"[{group_pos}] {name} — ❌ Échec (code={exit_code}), tentative {attempts + 1}/5")
            retries[name] = attempts + 1
            if attempts + 1 >= 5:
                log(f"[{group_pos}] {name} — ÉCHEC x5, abandon")
                idx += 1

        save_state()

    if running:
        log(f"✅ Tous les {total} groupes terminés !")
        STATE_FILE.unlink(missing_ok=True)

        log("Git push final…")
        subprocess.run(["git", "add", "-A", "results/emails-*.csv", "results/state-*.json"],
                       capture_output=True, cwd=str(BASE_DIR))
        subprocess.run(["git", "commit", "-m", f"supervisor: màj {datetime.now():%Y-%m-%d}"],
                       capture_output=True, cwd=str(BASE_DIR))
        subprocess.run(["git", "push"], capture_output=True, cwd=str(BASE_DIR))
        log("✅ Supervisor terminé")


if __name__ == "__main__":
    main()
