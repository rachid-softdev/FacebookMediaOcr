#!/usr/bin/env python3
"""
Discover Facebook job groups for each French department.
Strategy:
  1. Generer le slug attendu : offres.d.emploi.{departement_normalise}
  2. Verifier si le groupe existe (fetch PowerShell)
  3. Si oui -> resoudre l'ID numerique via fetch_lsd
  4. Si non -> recherche DuckDuckGo pour trouver d'autres groupes (max 3)
  5. Si rien trouve -> on passe au suivant

Sources :
  - departements.json : liste des departements
  - groups.txt        : groupes trouves (format name:group_id)
  - groups.json       : groupes trouves (format detaillie, avec URL)

Usage:
    python discover_groups.py                  # Tous les departements
    python discover_groups.py --dept 01 02 03  # Departements specifiques
    python discover_groups.py --dry-run        # Simulation seulement
"""

import sys
import time
import re
import json
import argparse
import unicodedata
import requests
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fb_graphql import fetch_lsd

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def load_departements():
    """Charge la liste des departements depuis departements.json."""
    path = Path(__file__).parent / "departements.json"
    if not path.exists():
        print(f"[!] {path} introuvable")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def slugify(name):
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_val = nfkd.encode("ascii", "ignore").decode("ascii")
    return ascii_val.lower().replace(" ", ".")


def group_exists(slug):
    """Verifie si un groupe Facebook existe via PowerShell."""
    from fb_graphql import powershell
    url = f"https://www.facebook.com/groups/{slug}/media"
    ps = f'''
$ua = "{UA}"
try {{
    $r = Invoke-WebRequest -Uri "{url}" -UserAgent $ua -MaximumRedirection 5 -TimeoutSec 15 -UseBasicParsing
    Write-Output ("STATUS:" + $r.StatusCode)
    Write-Output $r.Content
}} catch {{
    Write-Output "__POWERSHELL_ERROR__:$_"
}}
'''
    out, _, _ = powershell(ps, timeout=20)
    if "__POWERSHELL_ERROR__" in out[:100]:
        return False
    return "LSD" in out


def search_duckduckgo(query, max_results=10):
    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results, region="fr-fr"))
        return [r["href"] for r in results]
    except Exception as e:
        print(f"    ERR search: {e}")
        return []


def extract_group_slug(url):
    m = re.search(r'(?:www\.)?facebook\.com/groups/([^/\?#]+)', url)
    return m.group(1) if m else None


def resolve_group_id(slug):
    _, resolved = fetch_lsd(slug)
    return resolved


def main():
    parser = argparse.ArgumentParser(description="Decouvre les groupes Facebook Offres d'emploi par departement")
    parser.add_argument("--dept", nargs="+", help="Departements specifiques (ex: 01 02 03)")
    parser.add_argument("--dry-run", action="store_true", help="Simulation seulement")
    parser.add_argument("--delay", type=float, default=1.0, help="Delai entre chaque departement (defaut: 1s)")
    args = parser.parse_args()

    depts = load_departements()
    if args.dept:
        depts = [d for d in depts if d["num"] in args.dept]

    results = []
    pattern_found = 0
    search_found = 0
    empty_depts = 0

    for dept in depts:
        dept_num = dept["num"]
        dept_name = dept["nom"]
        expected_slug = f"offres.d.emploi.{slugify(dept_name)}"
        name = f"emploi{dept_num}"

        print(f"\n[{dept_num}] {dept_name}", end="", flush=True)
        sys.stdout.write(f" -> {expected_slug}")
        sys.stdout.flush()
        time.sleep(0.3)

        found = False

        # Etape 1 : pattern
        if group_exists(expected_slug):
            gid = resolve_group_id(expected_slug)
            if gid:
                results.append({
                    "name": name,
                    "group_id": gid,
                    "slug": expected_slug,
                    "url": f"https://www.facebook.com/groups/{expected_slug}",
                    "dept_num": dept_num,
                    "dept_name": dept_name,
                    "source": "pattern",
                })
                pattern_found += 1
                found = True
                print(f" OK -> {gid}")
        else:
            print(" absent", end="")

        # Etape 2 : fallback DuckDuckGo
        if not found:
            sys.stdout.write(", recherche...")
            sys.stdout.flush()
            query = f'facebook groupe offres d emploi {dept_num} {dept_name}'
            urls = search_duckduckgo(query)

            seen = set()
            for url in urls:
                if len([r for r in results if r["dept_num"] == dept_num]) >= 3:
                    break
                slug = extract_group_slug(url)
                if not slug or slug in seen:
                    continue
                seen.add(slug)

                sys.stdout.write(f" check {slug}...")
                sys.stdout.flush()

                gid = resolve_group_id(slug)
                if gid:
                    results.append({
                        "name": name,
                        "group_id": gid,
                        "slug": slug,
                        "url": f"https://www.facebook.com/groups/{slug}",
                        "dept_num": dept_num,
                        "dept_name": dept_name,
                        "source": "search",
                    })
                    search_found += 1
                    found = True
                    print(f" -> {gid}")
                    time.sleep(0.5)
                else:
                    print(" echec", end="")

        if not found:
            empty_depts += 1
            print(" rien")

        time.sleep(args.delay)

    # Ecriture des fichiers de sortie
    print(f"\n{'='*50}")
    print(f"Pattern OK : {pattern_found}")
    print(f"Search OK  : {search_found}")
    print(f"Sans groupe: {empty_depts}")
    print(f"Total      : {len(results)} groupes")

    if args.dry_run:
        print(f"\n--- groups.txt ---")
        for r in results:
            print(f"{r['name']}:{r['group_id']}")
        print(f"\n--- groups.json ---")
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    # groups.txt : format name:group_id (backward compat run_all.sh)
    Path("groups.txt").write_text(
        "\n".join(f"{r['name']}:{r['group_id']}" for r in results) + "\n",
        encoding="utf-8",
    )
    print(f"Ecris dans groups.txt")

    # groups.json : format detaille avec URLs
    Path("groups.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Ecris dans groups.json")


if __name__ == "__main__":
    main()
