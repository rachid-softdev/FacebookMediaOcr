DOC_ID = "26680580074858996"
QUERY_NAME = "GroupsCometMediaPhotosTabGridQuery"


import json, os, re, time, sys
from datetime import datetime


def check_current(group_id="1704587393146296"):
    """Vérifie si le doc_id actuel fonctionne encore via une requête rapide.
    Retourne True si OK, False si à rafraîchir."""
    try:
        import requests
        import subprocess

        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        proxy = "socks5h://127.0.0.1:9050"

        ps = r'''
$ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
$tmp = [System.IO.Path]::GetTempFileName() + ".html"
try {
    $r = Invoke-WebRequest -Uri "https://www.facebook.com/groups/%s/media" -UserAgent $ua -TimeoutSec 15 -UseBasicParsing -OutFile $tmp -PassThru
    $html = [System.IO.File]::ReadAllText($tmp)
    if ($html -match '"LSD",\[\],\{"token":"([^"]+)"') { $Matches[1] }
} finally { if (Test-Path $tmp) { Remove-Item $tmp -Force } }
''' % group_id
        lsd = subprocess.check_output(["pwsh", "-Command", ps], text=True, timeout=20).strip().split("\n")[-1]
        if not lsd:
            return False

        data = {
            "lsd": lsd,
            "fb_api_caller_class": "RelayModern",
            "fb_api_req_friendly_name": QUERY_NAME,
            "server_timestamps": "true",
            "variables": json.dumps({"count": 1, "cursor": None, "scale": 1, "id": group_id}, separators=(",", ":")),
            "doc_id": DOC_ID,
        }
        r = requests.post("https://www.facebook.com/api/graphql/", data=data,
                          headers={"User-Agent": ua},
                          proxies={"https": proxy, "http": proxy}, timeout=15)
        body = r.text
        if body.startswith("for (;;);"):
            body = body[9:]
        j = json.loads(body)
        return "errors" not in j
    except Exception:
        return False


def refresh(group_id="1704587393146296", headless=True):
    """Découvre le doc_id GraphQL actuel via Selenium + Chrome DevTools.

    Retourne le nouveau doc_id ou None si non trouvé.
    Met à jour DOC_ID dans ce fichier si un nouveau doc_id est trouvé.
    """
    import re
    import time
    import json
    import os
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service

    chromedriver = os.environ.get("CHROMEDRIVER_PATH") or None

    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(f"--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")

    driver = webdriver.Chrome(service=Service(chromedriver), options=options)

    requests_log = []

    def log_request(request):
        if "/api/graphql/" in request.url and request.method == "POST":
            body = request.post_data.decode("utf-8") if request.post_data else ""
            requests_log.append(body)

    try:
        driver.request_interceptor = log_request
        driver.get(f"https://www.facebook.com/groups/{group_id}/media")
        time.sleep(10)

        for body in requests_log:
            m = re.search(r'doc_id[=:]\s*["\']?(\d+)', body)
            if m:
                new_id = m.group(1)
                if new_id != DOC_ID:
                    _update_file(new_id)
                return new_id
        return None
    finally:
        driver.quit()


def _update_file(new_id):
    """Remplace DOC_ID dans fb_doc_id.py."""
    path = os.path.abspath(__file__)
    with open(path) as f:
        content = f.read()
    content = content.replace(f'DOC_ID = "{DOC_ID}"', f'DOC_ID = "{new_id}"')
    with open(path, "w") as f:
        f.write(content)


def refresh_files(new_id):
    """Met à jour le doc_id dans tous les fichiers qui le référence."""
    import pathlib
    root = pathlib.Path(__file__).parent

    patterns = {
        root / "fb_doc_id.py":        (f'DOC_ID = "{DOC_ID}"', f'DOC_ID = "{new_id}"'),
        root / "fb_graphql.py":       (f'DOC_ID = "{DOC_ID}"', f'DOC_ID = "{new_id}"'),
        root / "fb_selenium.py":      (f'"doc_id": "{DOC_ID}"', f'"doc_id": "{new_id}"'),
        root / "discover_groups.py":  (f"doc_id = '{DOC_ID}'", f"doc_id = '{new_id}'"),
    }

    for filepath, (old, new) in patterns.items():
        if filepath.exists():
            content = filepath.read_text()
            if old in content:
                content = content.replace(old, new)
                filepath.write_text(content)
                print(f"  [OK] {filepath.name}")

    global DOC_ID
    DOC_ID = new_id


if __name__ == "__main__":
    quiet = "--quiet" in sys.argv
    group_id = "1704587393146296"
    for arg in sys.argv[1:]:
        if arg != "--quiet":
            group_id = arg

    # Vérification rapide : le doc_id actuel fonctionne-t-il encore ?
    if check_current(group_id):
        if not quiet:
            print(f"doc_id {DOC_ID} OK")
        sys.exit(0)

    # Échec : lancer la découverte Selenium
    if not quiet:
        print(f"doc_id {DOC_ID} invalide, lancement de la découverte…")
    new_id = refresh(group_id, headless=not quiet)
    if new_id:
        if not quiet:
            print(f"Nouveau doc_id trouvé : {new_id}")
        refresh_files(new_id)
    elif not quiet:
        print("Aucun doc_id trouvé (le doc_id actuel est probablement toujours valide)")
