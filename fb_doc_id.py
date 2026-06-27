DOC_ID = "26680580074858996"
QUERY_NAME = "GroupsCometMediaPhotosTabGridQuery"


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
    import sys
    group_id = sys.argv[1] if len(sys.argv) > 1 else "1704587393146296"
    print(f"Recherche du doc_id sur le groupe {group_id}…")
    new_id = refresh(group_id)
    if new_id:
        print(f"Nouveau doc_id trouvé : {new_id}")
        refresh_files(new_id)
    else:
        print("Aucun doc_id trouvé")
