#!/usr/bin/env python3
"""
SMTP email verification — pure SMTP layer, zero external dependencies.

Connect to the recipient's SMTP server and simulate RCPT TO
to check if an address exists. Cuts before DATA — no email sent.

Usage:
    python smtp_verify.py user@example.com mx.example.com
    python smtp_verify.py --batch emails.csv --email-col email --mx-col mx_host
    cat pairs.txt | python smtp_verify.py --stdin
    python smtp_verify.py --batch emails.csv --output json --output-file results.json

Exit codes:
    0 — email exists (RCPT 250)
    1 — email invalid or error
"""

from __future__ import annotations

import csv
import json
import socket
import sys
import time
import argparse
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import smtplib


@dataclass
class SMTPResult:
    email: str
    mx_host: str
    exists: Optional[bool]
    smtp_code: Optional[int]
    detail: str
    duration_ms: int


def smtp_verify(
    email: str,
    mx_host: str,
    *,
    hello_name: str | None = None,
    timeout: int = 10,
    retry_greylist: bool = True,
    retry_delay: int = 30,
    source_addr: str = "verify@example.com",
) -> SMTPResult:
    start = time.monotonic()
    hello_name = hello_name or socket.getfqdn()

    for attempt in range(2 if retry_greylist else 1):
        try:
            with smtplib.SMTP(timeout=timeout) as smtp:
                smtp.connect(str(mx_host), 25)
                smtp.ehlo(hello_name)

                if smtp.has_extn("STARTTLS"):
                    smtp.starttls()
                    smtp.ehlo(hello_name)

                smtp.mail(source_addr)
                code, message = smtp.rcpt(email)
                smtp.quit()

                elapsed = int((time.monotonic() - start) * 1000)
                msg = message.decode(errors="replace")

                if code == 250:
                    return SMTPResult(email, mx_host, True, code, "Accepté par le serveur", elapsed)
                elif code == 550:
                    return SMTPResult(email, mx_host, False, code, "Refusé : adresse invalide", elapsed)
                elif 400 <= code < 500:
                    if attempt == 0 and retry_greylist:
                        smtp.quit()
                        time.sleep(retry_delay)
                        continue
                    return SMTPResult(email, mx_host, None, code, f"Temporaire (greylist ?) : {code} {msg}", elapsed)
                else:
                    return SMTPResult(email, mx_host, None, code, f"Réponse inattendue : {code} {msg}", elapsed)

        except smtplib.SMTPConnectError as e:
            elapsed = int((time.monotonic() - start) * 1000)
            return SMTPResult(email, mx_host, None, None, f"Connexion refusée : {e}", elapsed)

        except socket.timeout:
            elapsed = int((time.monotonic() - start) * 1000)
            if attempt == 0 and retry_greylist:
                time.sleep(retry_delay)
                continue
            return SMTPResult(email, mx_host, None, None, "Timeout", elapsed)

        except (socket.gaierror, socket.herror) as e:
            elapsed = int((time.monotonic() - start) * 1000)
            return SMTPResult(email, mx_host, None, None, f"Erreur DNS/réseau : {e}", elapsed)

        except OSError as e:
            elapsed = int((time.monotonic() - start) * 1000)
            return SMTPResult(email, mx_host, None, None, f"Erreur socket : {e}", elapsed)

        except Exception as e:
            elapsed = int((time.monotonic() - start) * 1000)
            return SMTPResult(email, mx_host, None, None, f"Exception : {e}", elapsed)

    elapsed = int((time.monotonic() - start) * 1000)
    return SMTPResult(email, mx_host, None, None, f"Échec après tentative(s)", elapsed)


KNOWN_CATCH_ALL_PROVIDERS = {
    "gmail-smtp-in.l.google.com": "Gmail accepte toutes les adresses (catch-all)",
    "outlook-com.olc.protection.outlook.com": "Outlook accepte toutes les adresses",
    "mx1.icloud.com": "iCloud peut accepter toutes les adresses",
    "mx2.icloud.com": "iCloud peut accepter toutes les adresses",
}


def verify_email(email: str, mx_host: str, **kwargs) -> SMTPResult:
    for known_mx, reason in KNOWN_CATCH_ALL_PROVIDERS.items():
        if known_mx in str(mx_host):
            return SMTPResult(
                email=email,
                mx_host=str(mx_host),
                exists=None,
                smtp_code=None,
                detail=f"Catch-all : {reason}",
                duration_ms=0,
            )
    return smtp_verify(email, mx_host, **kwargs)


def verify_batch(pairs: list[tuple[str, str]], **kwargs) -> list[SMTPResult]:
    results = []
    for i, (email, mx_host) in enumerate(pairs):
        print(f"[{i+1}/{len(pairs)}] {email} -> {mx_host}", file=sys.stderr)
        r = verify_email(email, mx_host, **kwargs)
        label = {True: "OK", False: "INVALIDE", None: "?"}.get(r.exists, "?")
        print(f"  [{label}] {r.detail} ({r.duration_ms}ms)", file=sys.stderr)
        results.append(r)
    return results


def load_pairs_from_csv(path: str, email_col: str = "email", mx_col: str = "mx_host") -> list[tuple[str, str]]:
    pairs = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            email = row.get(email_col, "").strip()
            mx = row.get(mx_col, "").strip()
            if email and mx:
                pairs.append((email, mx))
    return pairs


def load_pairs_from_stdin() -> list[tuple[str, str]]:
    pairs = []
    for line in sys.stdin:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(",")
        if len(parts) >= 2:
            pairs.append((parts[0].strip(), parts[1].strip()))
    return pairs


def generate_md_report(results: list[SMTPResult], path: str) -> None:
    n_valid = sum(1 for r in results if r.exists is True)
    n_invalid = sum(1 for r in results if r.exists is False)
    n_unknown = sum(1 for r in results if r.exists is None)
    total = len(results)
    valid_pct = round(n_valid / total * 100, 1) if total else 0
    invalid_pct = round(n_invalid / total * 100, 1) if total else 0
    unknown_pct = round(n_unknown / total * 100, 1) if total else 0

    lines = []
    lines.append(f"# Rapport de vérification SMTP")
    lines.append(f"")
    lines.append(f"**Date :** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**Total :** {total} adresse(s)")
    lines.append(f"")
    lines.append(f"## Résumé")
    lines.append(f"")
    lines.append(f"| Statut | Nombre | Pourcentage |")
    lines.append(f"|--------|-------:|------------:|")
    lines.append(f"| ✅ Valides | {n_valid} | {valid_pct}% |")
    lines.append(f"| ❌ Rejetés | {n_invalid} | {invalid_pct}% |")
    lines.append(f"| ❓ Incertains | {n_unknown} | {unknown_pct}% |")
    lines.append(f"| **Total** | **{total}** | **100%** |")
    lines.append(f"")

    if n_invalid > 0:
        lines.append(f"## ❌ Adresses rejetées ({n_invalid})")
        lines.append(f"")
        lines.append(f"| Email | Serveur MX | Code | Détail | Durée (ms) |")
        lines.append(f"|-------|------------|------|--------|-----------:|")
        for r in results:
            if r.exists is False:
                lines.append(f"| {r.email} | {r.mx_host} | {r.smtp_code or '-'} | {r.detail} | {r.duration_ms} |")
        lines.append(f"")

    if n_valid > 0:
        lines.append(f"## ✅ Adresses valides ({n_valid})")
        lines.append(f"")
        lines.append(f"| Email | Serveur MX | Détail | Durée (ms) |")
        lines.append(f"|-------|------------|--------|-----------:|")
        for r in results:
            if r.exists is True:
                lines.append(f"| {r.email} | {r.mx_host} | {r.detail} | {r.duration_ms} |")
        lines.append(f"")

    if n_unknown > 0:
        lines.append(f"## ❓ Adresses incertaines ({n_unknown})")
        lines.append(f"")
        lines.append(f"| Email | Serveur MX | Raison | Durée (ms) |")
        lines.append(f"|-------|------------|--------|-----------:|")
        for r in results:
            if r.exists is None:
                lines.append(f"| {r.email} | {r.mx_host} | {r.detail} | {r.duration_ms} |")
        lines.append(f"")

    lines.append(f"## Tableau complet")
    lines.append(f"")
    lines.append(f"| Email | Serveur MX | Statut | Code | Détail | Durée (ms) |")
    lines.append(f"|-------|------------|--------|------|--------|-----------:|")
    for r in results:
        status = {True: "✅ Valide", False: "❌ Rejeté", None: "❓ Incertain"}.get(r.exists, "?")
        lines.append(f"| {r.email} | {r.mx_host} | {status} | {r.smtp_code or '-'} | {r.detail} | {r.duration_ms} |")

    lines.append(f"")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Vérifie des adresses email via SMTP (zéro dépendance, aucun email envoyé).",
    )
    parser.add_argument("email", nargs="?", help="Email à vérifier")
    parser.add_argument("mx_host", nargs="?", help="Serveur SMTP cible")
    parser.add_argument("--batch", metavar="FICHIER", help="Fichier CSV avec colonnes email,mx_host")
    parser.add_argument("--email-col", default="email", help="Nom colonne email (défaut: email)")
    parser.add_argument("--mx-col", default="mx_host", help="Nom colonne mx_host (défaut: mx_host)")
    parser.add_argument("--stdin", action="store_true", help="Lire paires depuis stdin (email,mx_host par ligne)")
    parser.add_argument("--timeout", type=int, default=10, help="Timeout connexion secondes (défaut: 10)")
    parser.add_argument("--no-retry", action="store_true", help="Ne pas réessayer sur greylisting")
    parser.add_argument("--source-addr", default="verify@example.com", help="MAIL FROM (défaut: verify@example.com)")
    parser.add_argument("--hello", default=None, help="Hostname HELO/EHLO (défaut: hostname local)")
    parser.add_argument("--output", choices=["text", "csv", "json"], default="text", help="Format sortie (défaut: text)")
    parser.add_argument("--output-file", metavar="FICHIER", help="Fichier sortie (défaut: stdout)")


    args = parser.parse_args()

    kwargs = {
        "timeout": args.timeout,
        "retry_greylist": not args.no_retry,
        "source_addr": args.source_addr,
    }
    if args.hello:
        kwargs["hello_name"] = args.hello

    if args.batch or args.stdin:
        pairs = load_pairs_from_csv(args.batch, args.email_col, args.mx_col) if args.batch else load_pairs_from_stdin()
        if not pairs:
            print("[!] Aucune paire (email, mx_host) trouvée.", file=sys.stderr)
            sys.exit(1)

        print(f"[*] {len(pairs)} adresse(s) à vérifier", file=sys.stderr)
        results = verify_batch(pairs, **kwargs)

        outfile = open(args.output_file, "w", encoding="utf-8") if args.output_file else sys.stdout
        if args.output == "csv":
            writer = csv.writer(outfile)
            writer.writerow(["email", "mx_host", "exists", "smtp_code", "detail", "duration_ms"])
            for r in results:
                writer.writerow([r.email, r.mx_host, r.exists, r.smtp_code, r.detail, r.duration_ms])
        elif args.output == "json":
            json.dump([
                {"email": r.email, "mx_host": r.mx_host, "exists": r.exists,
                 "smtp_code": r.smtp_code, "detail": r.detail, "duration_ms": r.duration_ms}
                for r in results
            ], outfile, indent=2, ensure_ascii=False)
            outfile.write("\n")
        else:
            for r in results:
                label = {True: "OK", False: "INVALIDE", None: "INCERTAIN"}.get(r.exists, "?")
                print(f"{label:>10} | {r.email:40s} | {r.detail}")

        n_valid = sum(1 for r in results if r.exists is True)
        n_invalid = sum(1 for r in results if r.exists is False)
        n_unknown = sum(1 for r in results if r.exists is None)

        # Generate rapport path from input file name
        base = (args.batch or "stdin").rsplit(".", 1)[0]
        report_path = f"{base}-rapport.md"
        generate_md_report(results, report_path)
        print(f"[OK] Rapport généré : {report_path}", file=sys.stderr)

        print(f"\n[*] Bilan : {n_valid} valides, {n_invalid} invalides, {n_unknown} incertains", file=sys.stderr)
        return

    if not args.email or not args.mx_host:
        parser.print_help()
        sys.exit(1)

    result = verify_email(args.email, args.mx_host, **kwargs)
    label = {True: "EXISTE", False: "INVALIDE", None: "INCERTAIN"}.get(result.exists, "?")
    print(f"[{label}] {result.email} via {result.mx_host}")
    print(f"       {result.detail} ({result.duration_ms}ms)")
    sys.exit(0 if result.exists is True else 1)


if __name__ == "__main__":
    main()
