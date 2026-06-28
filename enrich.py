#!/usr/bin/env python3
"""
Enrichit les resultats OCR avec extraction IA (nom, prenom, telephone, ville, metier).

Usage:
    python enrich.py                              # Lit tous les state-*.json
    python enrich.py --from-csv emails-*.csv       # Lit depuis des CSV
    python enrich.py --name saisonniers            # Un seul groupe

Sortie:
    enriched-{name}.csv (ou enriched-all.csv)
"""

import sys
import json
import csv
import re
import argparse
from pathlib import Path

# --- Schema attendu pour la reponse IA ---
SCHEMA = {
    "file": "nom du fichier image",
    "fbid": "identifiant Facebook de la photo",
    "raw_text": "texte brut OCR",
    "extracted": {
        "name": "nom de famille (ou vide si non trouve)",
        "firstname": "prenom (ou vide si non trouve)",
        "phone": "telephone (06xxxxxxxx, 07xxxxxxxx, ou vide)",
        "email": "email trouve par OCR (ou vide)",
        "city": "ville (ou vide si non trouve)",
        "job": "metier recherche (ou vide si non trouve)"
    }
}


def load_from_states(patterns):
    """Charge les donnees OCR depuis les state-*.json"""
    items = []
    for pattern in patterns:
        for f in sorted(Path().glob(pattern)):
            name = re.match(r"state-(.+)\.json", f.name)
            gname = name.group(1) if name else "?"
            try:
                state = json.loads(f.read_text(encoding="utf-8"))
                for r in state.get("ocr_results", []):
                    r["_group"] = gname
                    items.append(r)
            except (json.JSONDecodeError, KeyError) as e:
                print(f"[!] {f.name}: {e}", file=sys.stderr)
    return items


def load_from_csv(patterns):
    """Charge depuis des CSV existants (doivent avoir raw_text)"""
    items = []
    for pattern in patterns:
        for f in sorted(Path().glob(pattern)):
            name = re.match(r"emails-(.+)\.csv", f.name)
            gname = name.group(1) if name else "?"
            with open(f, newline="", encoding="utf-8") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    row["_group"] = gname
                    items.append(row)
    return items


def format_prompt(items, batch_size=50):
    """Formate les donnees pour une requete IA en lots"""
    batches = []
    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        lines = []
        for item in batch:
            text = item.get("raw_text", item.get("raw_text", "")).strip()
            if not text:
                continue
            lines.append(f"<item file=\"{item.get('file','')}\" fbid=\"{item.get('fbid','')}\">")
            lines.append(text)
            lines.append("</item>")
        prompt = f"""Extrais les informations structurees depuis chaque texte OCR.
Reponds UNIQUEMENT avec un tableau JSON valide, un objet par <item>.

Schema attendu:
{json.dumps(SCHEMA["extracted"], indent=2)}

Si une info est introuvable, mets une chaine vide.
Si le JSON est invalide l'ensemble du lot sera rejete.

{chr(10).join(lines)}
"""
        batches.append((batch, prompt))
    return batches


def parse_response(response_text):
    """Parse la reponse IA, gere les erreurs JSON"""
    response_text = response_text.strip()

    # Tenter d'extraire un tableau JSON de la reponse
    m = re.search(r"\[\s*\{.*\}\s*\]", response_text, re.DOTALL)
    if not m:
        m = re.search(r"\{[^{}]*\}", response_text, re.DOTALL)
    if not m:
        return None, "Aucun JSON trouve dans la reponse"

    try:
        data = json.loads(m.group())
    except json.JSONDecodeError as e:
        return None, f"JSON invalide: {e}"

    if not isinstance(data, list):
        data = [data]

    # Valider et nettoyer chaque entree
    validated = []
    errors = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            errors.append(f"[{i}] Pas un objet: {type(entry).__name__}")
            continue
        extracted = entry.get("extracted", entry)
        validated.append({
            "file": entry.get("file", ""),
            "fbid": entry.get("fbid", ""),
            "name": extracted.get("name", ""),
            "firstname": extracted.get("firstname", ""),
            "phone": extracted.get("phone", ""),
            "email": extracted.get("email", ""),
            "city": extracted.get("city", ""),
            "job": extracted.get("job", ""),
        })

    if errors:
        print("[WARN] Erreurs de validation:", "; ".join(errors), file=sys.stderr)

    return validated, None


def save_enriched(items, name="all"):
    """Sauvegarde le CSV enrichi"""
    fieldnames = ["file", "fbid", "image_url", "name", "firstname", "phone", "email", "city", "job"]
    filename = f"results/enriched-{name}.csv"
    Path("results").mkdir(exist_ok=True)
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(items)
    print(f"[OK] {len(items)} entrees -> {filename}")
    return filename


def main():
    parser = argparse.ArgumentParser(description="Enrichissement OCR par IA")
    parser.add_argument("--from-csv", metavar="GLOB", nargs="+",
                        help="Lire depuis des CSV (ex: emails-*.csv)")
    parser.add_argument("--name", metavar="GROUPE",
                        help="Traiter un seul groupe (state-{name}.json)")
    parser.add_argument("--batch", type=int, default=50,
                        help="Taille des lots pour l'IA (defaut: 50)")
    args = parser.parse_args()

    # --- Chargement ---
    if args.from_csv:
        items = load_from_csv(args.from_csv)
        source = "CSV"
    elif args.name:
        items = load_from_states([f"state-{args.name}.json"])
        source = f"state-{args.name}.json"
    else:
        items = load_from_states(["state-*.json"])
        source = "state-*.json"

    if not items:
        print(f"[!] Aucune donnee trouvee dans {source}")
        print("    Lance d'abord fb_selenium.py pour generer des resultats.")
        sys.exit(1)

    # Filtrer ceux sans raw_text
    with_text = [it for it in items if it.get("raw_text", "").strip()]
    if not with_text:
        print(f"[!] Aucun raw_text trouve. Les OCR sont termines ?")
        print("    Les donnees OCR (raw_text) sont stockees dans les state-*.json")
        print("    Lance d'abord un groupe avec fb_selenium.py")
        sys.exit(1)

    print(f"[*] {len(items)} items charges depuis {source}")
    print(f"    {len(with_text)} avec raw_text non vide")
    print()

    # --- Preparation des lots ---
    batches = format_prompt(with_text, batch_size=args.batch)
    print(f"[*] {len(batches)} lots de ~{args.batch} items")
    print()

    # --- Mode interactif : afficher le prompt pour chaque lot ---
    all_enriched = []
    for batch_idx, (batch_items, prompt) in enumerate(batches):
        print(f"{'='*60}")
        print(f"LOT {batch_idx + 1}/{len(batches)} ({len(batch_items)} items)")
        print(f"{'='*60}")
        print()
        print("--- DEBUT DU PROMPT ---")
        print(prompt)
        print("--- FIN DU PROMPT ---")
        print()

        # Verifier si des donnees sont deja sur stdin
        if not sys.stdin.isatty():
            response = sys.stdin.read()
        else:
            print("Colle la reponse IA ci-dessous (Ctrl+D pour valider):")
            response = sys.stdin.read()

        validated, error = parse_response(response)
        if error:
            print(f"[!] ERREUR lot {batch_idx + 1}: {error}", file=sys.stderr)
            print("    Continue avec le lot suivant...")
            continue

        # Merger avec les donnees originales
        for orig, enriched in zip(batch_items, validated):
            enriched["file"] = orig.get("file", enriched["file"])
            enriched["fbid"] = orig.get("fbid", enriched["fbid"])
            enriched["image_url"] = orig.get("image_url", "")
            all_enriched.append(enriched)

        print(f"    -> {len(validated)} items valides")
        print()

    # --- Filtre : on ne garde que les entrees avec email (sinon c'est une offre, pas un CV) ---
    filtered = [e for e in all_enriched if e.get("email", "").strip()]
    if filtered:
        group_name = args.name or "all"
        save_enriched(filtered, group_name)
        skipped = len(all_enriched) - len(filtered)
        if skipped:
            print(f"    {skipped} entrees ignorees (pas d'email -> offre employeur)")
    else:
        print("[!] Aucune donnee enrichie avec email")


if __name__ == "__main__":
    main()
