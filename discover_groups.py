#!/usr/bin/env python3
"""
Discover Facebook job groups for each French department.

Pour chaque departement : tente le pattern `offres.d.emploi.{slug}`,
verifie que la page existe, extrait l'ID numerique, et verifie que
l'API GraphQL repond (photos publiques accessibles).

Usage:
    python discover_groups.py                  # Tous les departements
    python discover_groups.py --dept 75 92 93  # Departements specifiques
    python discover_groups.py --dry-run        # Simulation seulement
"""

import sys
import time
import re
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fb_graphql import fetch_lsd, powershell, UA
from notify import notify

PATTERNS = [
    "offres.d.emploi.{slug}",
]


def load_departements():
    path = Path(__file__).parent / "departements.json"
    if not path.exists():
        print(f"[!] {path} introuvable")
        sys.exit(1)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def dept_slug(nom):
    s = nom.lower().strip()
    replacements = {
        " ": "-", "'": "-", "(": "", ")": "",
        "\u00e9": "e", "\u00e8": "e", "\u00ea": "e", "\u00eb": "e",
        "\u00e0": "a", "\u00e2": "a",
        "\u00f9": "u", "\u00fb": "u",
        "\u00f4": "o", "\u00f6": "o",
        "\u00ee": "i", "\u00ef": "i",
        "\u00e7": "c",
    }
    for old, new in replacements.items():
        s = s.replace(old, new)
    s = re.sub(r"-+", "-", s)
    s = s.strip("-")
    return s


def group_exists(slug):
    """Verifie que la page Facebook du groupe est accessible (LSD)."""
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
    if not out or "__POWERSHELL_ERROR__" in out[:100]:
        return False
    return "LSD" in out


def resolve_group_id(slug):
    """Resout l'ID numerique du groupe via fetch_lsd."""

    if slug.isdigit():
        return slug
    _, resolved = fetch_lsd(slug)
    if not resolved or resolved == slug:
        return None
    return resolved


def graphql_works(lsd, group_id):
    """Verifie que l'API GraphQL repond avec des photos pour ce groupe."""
    from fb_doc_id import DOC_ID as doc_id
    variables_json = json.dumps({
        "count": 1, "cursor": None, "scale": 1, "id": str(group_id),
    }, separators=(",", ":"))

    ps = f'''
$variables = '{variables_json}'
$body = @{{
    lsd = '{lsd}'
    fb_api_caller_class = 'RelayModern'
    fb_api_req_friendly_name = 'GroupsCometMediaPhotosTabGridQuery'
    server_timestamps = 'true'
    variables = $variables
    doc_id = '{doc_id}'
}}
$r = Invoke-WebRequest -Uri 'https://www.facebook.com/api/graphql/' -Method POST -Body $body -UserAgent '{UA}' -MaximumRedirection 0 -TimeoutSec 15 -UseBasicParsing
Write-Output $r.Content
'''
    out, _, code = powershell(ps, timeout=20)
    if code != 0 or not out.strip():
        return False
    if out.startswith("for (;;);"):
        out = out[len("for (;;);"):]
    try:
        result = json.loads(out)
        node = result.get("data", {}).get("node")
        if node and node.get("group_mediaset"):
            return True
    except (json.JSONDecodeError, AttributeError):
        pass
    return False


def try_slug(dept_num, dept_name, slug):
    """Tente le pattern, verifie existence + ID + GraphQL."""
    for pat in PATTERNS:
        group_slug = pat.format(slug=slug)
        print(f"  Pattern: {group_slug} ... ", end="", flush=True)

        if not group_exists(group_slug):
            print("inaccessible")
            continue

        lsd, gid = fetch_lsd(group_slug)
        if not lsd:
            print("LSD absent")
            continue
        if not gid or gid == group_slug:
            print("ID non resoluble")
            continue

        print(f"ID={gid} ... ", end="", flush=True)

        if not graphql_works(lsd, gid):
            print("GraphQL echoue (photos non publiques)")
            continue

        print("OK")

        return {
            "name": f"emploi{dept_num}",
            "group_id": gid,
            "slug": group_slug,
            "url": f"https://www.facebook.com/groups/{group_slug}",
            "dept_num": dept_num,
            "dept_name": dept_name,
            "source": "pattern",
        }
    return None


def load_existing():
    """Charge les departements deja decouverts depuis groups.json."""
    path = Path(__file__).parent / "groups.json"
    if not path.exists():
        return set()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {r["dept_num"] for r in data if "dept_num" in r}
    except (json.JSONDecodeError, KeyError):
        return set()


def main():
    parser = argparse.ArgumentParser(
        description="Decouvre les groupes Facebook Offres d'emploi par departement"
    )
    parser.add_argument("--dept", nargs="+", help="Departements specifiques (ex: 01 02 03)")
    parser.add_argument("--dry-run", action="store_true", help="Simulation seulement")
    parser.add_argument("--delay", type=float, default=0.5, help="Delai entre chaque (defaut: 0.5s)")
    parser.add_argument("--force", action="store_true", help="Re-traite tous les departements meme deja decouverts")
    args = parser.parse_args()

    depts = load_departements()
    if args.dept:
        depts = [d for d in depts if d["num"] in args.dept]

    existing = set() if args.force else load_existing()
    if existing:
        print(f"[*] {len(existing)} departements deja decouverts, ignores (--force pour tout re-traiter)")

    results = []
    found = 0
    empty = 0
    skipped = 0

    notify("debut", script="discover_groups", data={"departements": len(depts), "deja_trouves": len(existing) or None})

    for dept in depts:
        dept_num = dept["num"]
        if dept_num in existing:
            print(f"  [{dept_num}] {dept['nom']}  -> deja traite (ignore)")
            skipped += 1
            continue

        dept_name = dept["nom"]
        slug = dept_slug(dept_name)
        print(f"\n[{dept_num}] {dept_name}  ({slug})")

        result = try_slug(dept_num, dept_name, slug)
        if result:
            gid = result["group_id"]
            existing = [r for r in results if r["group_id"] == gid]
            if existing:
                print(f"  => ID {gid} deja utilise par {existing[0]['dept_name']} (ignore)")
                empty += 1
                continue
            results.append(result)
            found += 1
            print(f"  => {gid}")
            notify("ok", group=result["name"], script="discover_groups",
                   data={"gid": gid, "slug": result["slug"]})
        else:
            empty += 1
            print(f"  => [echec]")
            notify("echec", group=dept_num, script="discover_groups",
                   data={"dept": dept_name, "slug": slug})

        if not args.dry_run:
            Path("groups.txt").write_text(
                "\n".join(f"{r['name']}:{r['group_id']}" for r in results) + "\n",
                encoding="utf-8",
            )
            Path("groups.json").write_text(
                json.dumps(results, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        time.sleep(args.delay)

    notify("ok" if found else "info", script="discover_groups",
           data={"trouves": found, "echecs": empty, "ignores": skipped, "total": len(depts)})

    print(f"\n{'='*50}")
    print(f"OK     : {found}")
    print(f"Echec  : {empty}")
    print(f"Ignore : {skipped}")
    print(f"Total  : {len(results)} / {len(depts)}")

    if args.dry_run:
        print("\n--- groups.txt ---")
        for r in results:
            print(f"{r['name']}:{r['group_id']}")

    else:
        print(f"Fichiers: groups.txt ({len(results)} lignes), groups.json")


if __name__ == "__main__":
    main()
