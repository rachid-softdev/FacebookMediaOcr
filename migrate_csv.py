#!/usr/bin/env python3
"""
Migration : ajoute les colonnes image_url et fb_url aux anciens CSVs.
- fb_url est construit depuis le fbid : https://www.facebook.com/photo/?fbid={fbid}
- image_url reste vide pour les anciens donnees (perdu)

Formats :
  ancien : file,fbid,email,all_emails_in_image,raw_text
  intermediaire : file,fbid,image_url,email,all_emails_in_image,raw_text
  final   : file,fbid,image_url,fb_url,email,all_emails_in_image,raw_text
"""

import csv
import sys
from pathlib import Path

FIELDS_FINAL = ["file", "fbid", "image_url", "fb_url", "email", "all_emails_in_image", "raw_text"]
FB_URL_TPL = "https://www.facebook.com/photo/?fbid={}"


def needs_migration(path: Path) -> bool:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return "fb_url" not in reader.fieldnames


def migrate(path: Path) -> int:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS_FINAL)
        writer.writeheader()
        for row in rows:
            new_row = {k: row.get(k, "") for k in FIELDS_FINAL}
            fbid = new_row.get("fbid", "")
            if fbid and not new_row["fb_url"]:
                new_row["fb_url"] = FB_URL_TPL.format(fbid)
            writer.writerow(new_row)

    return len(rows)


def main():
    patterns = ["emails-*.csv", "results/emails-*.csv", "results/emails.csv"]
    files = sorted(set(
        p for pat in patterns for p in Path().glob(pat)
    ))

    if not files:
        print("[!] Aucun fichier CSV trouvé.")
        sys.exit(0)

    migrated = 0
    skipped = 0
    for path in files:
        if not needs_migration(path):
            print(f"  [SKIP] {path} (déjà à jour)")
            skipped += 1
            continue
        count = migrate(path)
        print(f"  [OK]   {path} ({count} lignes)")
        migrated += 1

    print(f"\nRésumé : {migrated} fichier(s) migré(s), {skipped} déjà à jour")


if __name__ == "__main__":
    main()
