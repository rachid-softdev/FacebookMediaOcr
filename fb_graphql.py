"""Récupère les photos d'un groupe Facebook via l'API GraphQL interne
(PowerShell Invoke-WebRequest contourne le blocage TLS de curl/requests).

Pipeline : GraphQL -> Download -> OCR, page par page (avec affichage email).
Usage :
    python fb_graphql.py <group_id> [--save urls.json] [--pages N]
"""

import json
import sys
import subprocess
import os
import re
import argparse
import csv
from pathlib import Path

import requests
from notify import notify

try:
    import cv2
    import numpy as np
    import pytesseract
    from PIL import Image
    try:
        pytesseract.get_tesseract_version()
    except pytesseract.TesseractNotFoundError:
        tesseract_paths = []
        if sys.platform == "win32":
            tesseract_paths = [
                r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
            ]
        else:
            tesseract_paths = ["/usr/bin/tesseract", "/usr/local/bin/tesseract"]
        for p in tesseract_paths:
            if os.path.exists(p):
                pytesseract.pytesseract.tesseract_cmd = p
                break
        else:
            print("[!] Tesseract introuvable. Installe-le ou mets à jour le chemin.")
            if sys.platform != "win32":
                print("    sudo apt install tesseract-ocr tesseract-ocr-fra")
            else:
                print("    https://github.com/UB-Mannheim/tesseract/wiki")
            sys.exit(1)
except ImportError:
    print("[!] Dépendances manquantes.")
    print("    pip install opencv-python numpy pytesseract Pillow requests")
    sys.exit(1)


GRAPHQL_URL = "https://www.facebook.com/api/graphql/"
DOC_ID = "26680580074858996"
QUERY_NAME = "GroupsCometMediaPhotosTabGridQuery"
PAGE_SIZE = 8
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
DOWNLOAD_DIR = "download"
EMAILS_CSV = "emails.csv"
EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.IGNORECASE
)


# --- PowerShell ------------------------------------------------------------


def powershell(script, timeout=30):
    pwsh = "pwsh" if sys.platform != "win32" else "powershell.exe"
    cmd = [
        pwsh, "-NoProfile", "-NonInteractive",
        "-Command", script
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=False, timeout=timeout)
        stdout = r.stdout.decode("utf-8", errors="replace") if r.stdout else ""
        stderr = r.stderr.decode("utf-8", errors="replace") if r.stderr else ""
        return stdout, stderr, r.returncode
    except subprocess.TimeoutExpired:
        return "", "TIMEOUT", -1
    except FileNotFoundError:
        return "", f"{pwsh} not found", -1


def fetch_lsd(group_id):
    """Récupère le token LSD + l'ID numérique du groupe via PowerShell Invoke-WebRequest.
    Retourne (lsd, numeric_group_id) ou (None, None) en cas d'échec."""
    ps_script = f'''
$ua = "{UA}"
$tmpHtml = [System.IO.Path]::GetTempFileName() + ".html"
try {{
    $r = Invoke-WebRequest -Uri "https://www.facebook.com/groups/{group_id}/media" -UserAgent $ua -MaximumRedirection 5 -TimeoutSec 30 -UseBasicParsing -OutFile $tmpHtml -PassThru
    $html = [System.IO.File]::ReadAllText($tmpHtml)
    Write-Output $html
}} catch {{
    Write-Output "__POWERSHELL_ERROR__:$_"
}} finally {{
    if (Test-Path $tmpHtml) {{ Remove-Item $tmpHtml -Force }}
}}
'''
    print("  [1/2] Fetch group page via PowerShell…")
    out, err, code = powershell(ps_script, timeout=35)
    if "__POWERSHELL_ERROR__" in out[:100]:
        print(f"  [!] PowerShell error: {out[out.find('__POWERSHELL_ERROR__')+20:].strip()[:200]}")
        return None, None
    if code != 0 or not out.strip():
        print(f"  [!] PowerShell failed (code {code})")
        if err:
            for line in err.strip().splitlines()[:5]:
                print(f"      {line}")
        return None, None
    lsd = None
    for pat in [
        r'"LSD",\[\],\{"token":"([^"]+)"',
        r'"__DTSSToken":"([^"]+)"',
    ]:
        m = re.search(pat, out)
        if m:
            lsd = m.group(1)
            print(f"  LSD: {lsd[:20]}…")
            break
    if not lsd:
        print("  [!] LSD token not found in page")
        return None, None

    # Résoudre l'ID numérique si le group_id est un slug texte
    numeric_id = None
    for pat in [
        r'"groupID":"(\d+)"',
        r'"group_id":"(\d+)"',
        r'"entity_id":"(\d+)"',
        r'fb://group/?id=(\d+)',
    ]:
        m = re.search(pat, out)
        if m:
            numeric_id = m.group(1)
            break
    if numeric_id and numeric_id != group_id:
        print(f"  Group ID résolu : {group_id} -> {numeric_id}")
        return lsd, numeric_id
    return lsd, group_id


# --- GraphQL ---------------------------------------------------------------


SOCKS_PROXY = "socks5h://127.0.0.1:9050"


def graphql_page(lsd, group_id, cursor):
    """Récupère une page de photos via GraphQL (Python requests + Tor SOCKS)."""
    import requests as _requests

    variables = {
        "count": PAGE_SIZE,
        "cursor": cursor,
        "scale": 1,
        "id": str(group_id),
    }
    data = {
        "lsd": lsd,
        "fb_api_caller_class": "RelayModern",
        "fb_api_req_friendly_name": QUERY_NAME,
        "server_timestamps": "true",
        "variables": json.dumps(variables, separators=(",", ":")),
        "doc_id": DOC_ID,
    }
    try:
        r = _requests.post(
            GRAPHQL_URL,
            data=data,
            headers={"User-Agent": UA},
            proxies={"https": SOCKS_PROXY, "http": SOCKS_PROXY},
            timeout=35,
        )
        body = r.text
    except Exception:
        return None, None

    if body.startswith("for (;;);"):
        body = body[len("for (;;);"):]
    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        return None, None

    if "errors" in result:
        return None, None

    media = (
        result.get("data", {}).get("node", {})
        .get("group_mediaset", {}).get("media", {})
    )
    if not media:
        return None, None

    page_info = media.get("page_info", {})
    entries = []
    for edge in media.get("edges", []):
        node = edge.get("node", {})
        entry = {
            "fbid": node.get("id", ""),
            "url": node.get("image", {}).get("uri", ""),
            "owner": node.get("owner", {}).get("id", ""),
        }
        if entry["url"]:
            entries.append(entry)

    next_cursor = page_info.get("end_cursor", "")
    has_next = page_info.get("has_next_page", False)
    return entries, (next_cursor if has_next else None)


# --- OCR -------------------------------------------------------------------


def preprocess_image(img_path):
    img = cv2.imread(str(img_path))
    if img is None:
        raise ValueError(f"Impossible de lire : {img_path}")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    if max(h, w) < 1000:
        gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    gray = cv2.fastNlMeansDenoising(gray, h=10)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, blockSize=31, C=15
    )
    return binary


def ocr_image(img_path):
    processed = preprocess_image(img_path)
    pil_img = Image.fromarray(processed)
    text = pytesseract.image_to_string(pil_img, lang="fra+eng", config="--oem 3 --psm 6")
    return text


def extract_emails(text):
    seen = set()
    result = []
    for e in EMAIL_RE.findall(text):
        el = e.lower()
        if el not in seen:
            seen.add(el)
            result.append(el)
    return result


def ocr_photo(img_path):
    try:
        text = ocr_image(img_path)
        emails = extract_emails(text)
        if not emails:
            return None
        return {
            "file": img_path.name,
            "fbid": img_path.stem,
            "emails": emails,
            "raw_text": text.strip().replace("\n", " ")[:500],
        }
    except Exception as e:
        print(f"    [WARN] OCR {img_path.name} : {e}")
        return None


# --- Download + OCR par page -----------------------------------------------


def process_page(entries, page_num, session):
    """Télécharge un lot d'images, lance l'OCR, affiche les emails."""
    Path(DOWNLOAD_DIR).mkdir(exist_ok=True)
    found = []

    for i, item in enumerate(entries):
        fbid = item["fbid"]
        url = item.get("url")
        label = f"  [{page_num}.{i+1}] {fbid}"

        if not url:
            print(f"{label}  SKIP (pas d'URL)")
            continue

        try:
            resp = session.get(
                url,
                headers={
                    "Referer": f"https://www.facebook.com/photo/?fbid={fbid}",
                    "User-Agent": UA,
                },
                stream=True, timeout=20,
            )
            if resp.status_code != 200:
                print(f"{label}  HTTP {resp.status_code}")
                continue

            data = resp.content
            if len(data) < 1024:
                print(f"{label}  TROP PETIT ({len(data)} octets)")
                continue

            ct = resp.headers.get("Content-Type", "")
            ext = ".png" if "png" in ct else ".jpg"
            out_path = Path(DOWNLOAD_DIR) / f"{fbid}{ext}"
            with open(out_path, "wb") as f:
                f.write(data)

            res = ocr_photo(out_path)
            if res:
                found.append(res)
                print(f"{label}  OK  {res['emails']}")
            else:
                print(f"{label}  OK  - (aucun email)")
        except Exception as e:
            print(f"{label}  ERR {e}")

    return found


# --- Main ------------------------------------------------------------------


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Photos Facebook via GraphQL + OCR (PowerShell)"
    )
    parser.add_argument("group_id", help="ID du groupe Facebook")
    parser.add_argument("--save", default="urls_graphql.json",
                        help="Fichier de sortie des URLs")
    parser.add_argument("--pages", type=int, default=1000,
                        help="Nombre max de pages")
    args = parser.parse_args()

    print("[*] Connexion…")
    lsd, resolved_id = fetch_lsd(args.group_id)
    if not lsd:
        notify("echec", group=args.group_id, script="fb_graphql", error="LSD introuvable")
        sys.exit(1)

    print("\n[*] Récupération des photos + OCR…")
    notify("debut", group=args.group_id, script="fb_graphql", data={"pages": args.pages})
    all_entries = []
    all_ocr = []
    cursor = None
    page = 0
    session = requests.Session()
    session.headers.update({"User-Agent": UA})

    while page < args.pages:
        page += 1
        print(f"\n--- Page {page} ---")
        entries, cursor = graphql_page(lsd, resolved_id, cursor)
        if not entries:
            if page == 1:
                print("  [!] Aucune photo trouvée")
            break

        all_entries.extend(entries)

        ocr_results = process_page(entries, page, session)
        all_ocr.extend(ocr_results)

        email_count = len([r for r in all_ocr for _ in r["emails"]])
        print(f"  => Page {page}: {len(entries)} photos, "
              f"{len(ocr_results)} avec email(s) "
              f"(total: {email_count} email(s))")

        if not cursor:
            print("  [fin] Plus de pages")
            break

    # URLs
    if all_entries:
        with open(args.save, "w", encoding="utf-8") as f:
            json.dump(all_entries, f, ensure_ascii=False, indent=2)
        print(f"\n[OK] {len(all_entries)} URLs -> {args.save}")

    # Emails
    if all_ocr:
        fieldnames = ["file", "fbid", "email", "all_emails_in_image"]
        rows = []
        for r in all_ocr:
            for email in r["emails"]:
                rows.append({
                    "file": r["file"],
                    "fbid": r["fbid"],
                    "email": email,
                    "all_emails_in_image": ", ".join(r["emails"]),
                })
        with open(EMAILS_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"\n{'='*50}")
        print("Emails trouvés")
        print(f"{'='*50}")
        for r in all_ocr:
            for e in r["emails"]:
                print(f"  {e}")
        email_data = {"emails": len(rows), "photos": len(all_entries), "pages": page}
        notify("ok", group=args.group_id, script="fb_graphql", data=email_data)
        print(f"\n[OK] {len(rows)} email(s) -> {EMAILS_CSV}")
    else:
        notify("info", group=args.group_id, script="fb_graphql", data={"photos": len(all_entries), "pages": page})
        print(f"\n[!] Aucun email trouvé")
