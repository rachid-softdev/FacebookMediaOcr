import csv
import os
import glob

csv_files = (
    glob.glob("results/**/*.csv", recursive=True)
    + glob.glob("results/*.csv", recursive=True)
    + glob.glob("emails-*.csv")
)

csv_files = [f for f in csv_files if os.path.isfile(f)]
csv_files = sorted(set(csv_files))

total_before = 0
total_after = 0
unique_global = set()

for filepath in csv_files:
    with open(filepath, newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if not rows:
        continue

    header = rows[0]
    email_idx = None
    for i, col in enumerate(header):
        if col.strip().lower() == "email":
            email_idx = i
            break

    if email_idx is None:
        print(f"SKIP (no email column): {filepath}")
        continue

    seen = set()
    deduped = [header]
    for row in rows[1:]:
        if len(row) <= email_idx:
            deduped.append(row)
            continue
        email = row[email_idx].strip().lower()
        if email and email not in seen:
            seen.add(email)
            deduped.append(row)

    before = len(rows) - 1
    after = len(deduped) - 1
    total_before += before
    total_after += after
    unique_global.update(seen)

    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(deduped)

    if before != after:
        print(f"{filepath}: {before} -> {after} rows (removed {before - after})")

print(f"\nTotal avant: {total_before}")
print(f"Total après: {total_after}")
print(f"Lignes supprimées: {total_before - total_after}")
print(f"E-mails uniques (global): {len(unique_global)}")
