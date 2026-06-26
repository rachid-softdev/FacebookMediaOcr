#!/usr/bin/env python3
"""
Discover Facebook job groups for each French department.
Strategy:
  1. Generer le slug attendu : offres.d.emploi.{departement_normalise}
  2. Verifier si le groupe existe (fetch HTTP)
  3. Si oui -> resoudre l'ID numerique via fetch_lsd
  4. Si non -> recherche DuckDuckGo pour trouver d'autres groupes (max 3)
  5. Ecrire groups.txt (format name:group_id)

Usage:
    python discover_groups.py                  # Tous les départements
    python discover_groups.py --dept 01 02 03  # Départements spécifiques
    python discover_groups.py --dry-run        # Simulation seulement
"""

import sys
import time
import re
import argparse
import unicodedata
import requests
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent))
from fb_graphql import fetch_lsd

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

DEPARTEMENTS = [
    ("01", "Ain"), ("02", "Aisne"), ("03", "Allier"), ("04", "Alpes-de-Haute-Provence"),
    ("05", "Hautes-Alpes"), ("06", "Alpes-Maritimes"), ("07", "Ardèche"), ("08", "Ardennes"),
    ("09", "Ariège"), ("10", "Aube"), ("11", "Aude"), ("12", "Aveyron"),
    ("13", "Bouches-du-Rhône"), ("14", "Calvados"), ("15", "Cantal"), ("16", "Charente"),
    ("17", "Charente-Maritime"), ("18", "Cher"), ("19", "Corrèze"),
    ("2A", "Corse-du-Sud"), ("2B", "Haute-Corse"),
    ("21", "Côte-d'Or"), ("22", "Côtes-d'Armor"), ("23", "Creuse"), ("24", "Dordogne"),
    ("25", "Doubs"), ("26", "Drôme"), ("27", "Eure"), ("28", "Eure-et-Loir"),
    ("29", "Finistère"), ("30", "Gard"), ("31", "Haute-Garonne"), ("32", "Gers"),
    ("33", "Gironde"), ("34", "Hérault"), ("35", "Ille-et-Vilaine"), ("36", "Indre"),
    ("37", "Indre-et-Loire"), ("38", "Isère"), ("39", "Jura"), ("40", "Landes"),
    ("41", "Loir-et-Cher"), ("42", "Loire"), ("43", "Haute-Loire"),
    ("44", "Loire-Atlantique"), ("45", "Loiret"), ("46", "Lot"), ("47", "Lot-et-Garonne"),
    ("48", "Lozère"), ("49", "Maine-et-Loire"), ("50", "Manche"), ("51", "Marne"),
    ("52", "Haute-Marne"), ("53", "Mayenne"), ("54", "Meurthe-et-Moselle"),
    ("55", "Meuse"), ("56", "Morbihan"), ("57", "Moselle"), ("58", "Nièvre"),
    ("59", "Nord"), ("60", "Oise"), ("61", "Orne"), ("62", "Pas-de-Calais"),
    ("63", "Puy-de-Dôme"), ("64", "Pyrénées-Atlantiques"), ("65", "Hautes-Pyrénées"),
    ("66", "Pyrénées-Orientales"), ("67", "Bas-Rhin"), ("68", "Haut-Rhin"),
    ("69", "Rhône"), ("70", "Haute-Saône"), ("71", "Saône-et-Loire"), ("72", "Sarthe"),
    ("73", "Savoie"), ("74", "Haute-Savoie"), ("75", "Paris"), ("76", "Seine-Maritime"),
    ("77", "Seine-et-Marne"), ("78", "Yvelines"), ("79", "Deux-Sèvres"), ("80", "Somme"),
    ("81", "Tarn"), ("82", "Tarn-et-Garonne"), ("83", "Var"), ("84", "Vaucluse"),
    ("85", "Vendée"), ("86", "Vienne"), ("87", "Haute-Vienne"), ("88", "Vosges"),
    ("89", "Yonne"), ("90", "Territoire de Belfort"), ("91", "Essonne"),
    ("92", "Hauts-de-Seine"), ("93", "Seine-Saint-Denis"), ("94", "Val-de-Marne"),
    ("95", "Val-d'Oise"),
]


def slugify(name):
    """Normalise un nom de département : sans accents, sans espaces."""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_val = nfkd.encode("ascii", "ignore").decode("ascii")
    return ascii_val.lower().replace(" ", ".")


def group_exists(slug):
    """Vérifie si un groupe Facebook existe via PowerShell (requests bloqué par Facebook)."""
    from fb_graphql import powershell
    url = f"https://www.facebook.com/groups/{slug}/media"
    ps_script = f'''
$ua = "{UA}"
try {{
    $r = Invoke-WebRequest -Uri "{url}" -UserAgent $ua -MaximumRedirection 5 -TimeoutSec 15 -UseBasicParsing
    Write-Output ("STATUS:" + $r.StatusCode)
    Write-Output $r.Content
}} catch {{
    Write-Output "__POWERSHELL_ERROR__:$_"
}}
'''
    out, _, _ = powershell(ps_script, timeout=20)
    if "__POWERSHELL_ERROR__" in out[:100]:
        return False
    return "LSD" in out


def search_duckduckgo(query, max_results=10):
    """Cherche sur DuckDuckGo et retourne les URLs des résultats."""
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results, region="fr-fr"))
        return [r["href"] for r in results]
    except Exception as e:
        print(f"    ERR search: {e}")
        return []


def extract_group_slug(url):
    """Extrait le slug/ID d'une URL Facebook groups."""
    m = re.search(r'(?:www\.)?facebook\.com/groups/([^/\?#]+)', url)
    return m.group(1) if m else None


def resolve_group_id(slug):
    """Résout un slug texte en ID numérique via fetch_lsd."""
    _, resolved = fetch_lsd(slug)
    return resolved


def main():
    parser = argparse.ArgumentParser(
        description="Découvre les groupes Facebook Offres d'emploi par département"
    )
    parser.add_argument("--dept", nargs="+", help="Départements spécifiques (ex: 01 02 03)")
    parser.add_argument("--dry-run", action="store_true", help="Simulation seulement")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Délai entre chaque département (defaut: 1s)")
    args = parser.parse_args()

    if args.dept:
        depts = [(n, next((nom for d, nom in DEPARTEMENTS if d == n), n))
                 for n in args.dept]
    else:
        depts = DEPARTEMENTS

    output_lines = []
    slug_found = 0
    search_found = 0

    for dept_num, dept_name in depts:
        expected_slug = f"offres.d.emploi.{slugify(dept_name)}"
        name = f"emploi{dept_num}"
        print(f"\n[{dept_num}] {dept_name}", end="", flush=True)

        # Étape 1 : vérifier le slug attendu
        sys.stdout.write(f" -> {expected_slug}")
        sys.stdout.flush()
        time.sleep(0.3)

        if group_exists(expected_slug):
            gid = resolve_group_id(expected_slug)
            if gid:
                line = f"{name}:{gid}"
                output_lines.append(line)
                slug_found += 1
                print(f" OK -> {gid}")
                if not args.dry_run:
                    with open("groups.txt", "w", encoding="utf-8") as f:
                        f.write("\n".join(output_lines) + "\n")
                continue
        else:
            print(" absent", end="")

        # Étape 2 : fallback DuckDuckGo
        sys.stdout.write(", recherche...")
        sys.stdout.flush()
        query = f'facebook groupe offres d emploi {dept_num} {dept_name}'
        urls = search_duckduckgo(query)

        seen = set()
        found_for_dept = 0
        for url in urls:
            if found_for_dept >= 3:
                break
            slug = extract_group_slug(url)
            if not slug or slug in seen:
                continue
            seen.add(slug)

            sys.stdout.write(f" check {slug}...")
            sys.stdout.flush()

            gid = resolve_group_id(slug)
            if gid:
                line = f"{name}:{gid}"
                output_lines.append(line)
                search_found += 1
                found_for_dept += 1
                print(f" -> {gid}")
                if not args.dry_run:
                    with open("groups.txt", "w", encoding="utf-8") as f:
                        f.write("\n".join(output_lines) + "\n")
                time.sleep(0.5)
            else:
                print(f" echec", end="")

        if found_for_dept == 0:
            print(" rien")

        time.sleep(args.delay)

    print(f"\n{'='*50}")
    print(f"Pattern OK  : {slug_found}")
    print(f"Search OK   : {search_found}")
    print(f"Total       : {len(output_lines)} groupes")
    if args.dry_run:
        print("\n" + "\n".join(output_lines))
    else:
        print(f"Écrit dans groups.txt")


if __name__ == "__main__":
    main()
