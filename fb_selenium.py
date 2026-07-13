#!/usr/bin/env python3
"""
fb_selenium.py -- Facebook Group Media Scraper + OCR (Selenium)
=============================================================

Pipeline :
  1. Login Facebook (email + mot de passe)
  2. Scroll de la page /media du groupe  ->  collecte des fbid
  3. Récupération des URLs haute résolution (via requests + cookies)
  4. Téléchargement des images
  5. OCR (pytesseract)  ->  emails.csv

Usage :
  python fb_selenium.py
  python fb_selenium.py --email user@example.com --password monpass
"""

import csv
import json
import os
import re
import shutil
import sys
import time
import subprocess
from datetime import datetime
from pathlib import Path

import sys
sys.stdout.reconfigure(encoding='utf-8')

import requests
from notify import notify
from fb_doc_id import DOC_ID, QUERY_NAME

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import TimeoutException
except ImportError:
    print("[ERR] selenium requis.")
    print("      pip install selenium")
    sys.exit(1)

try:
    import cv2
    import numpy as np
    import pytesseract
    from PIL import Image
    # Chercher Tesseract
    for p in (["/usr/bin/tesseract", "/usr/local/bin/tesseract"] if sys.platform != "win32"
              else [r"C:\Program Files\Tesseract-OCR\tesseract.exe", r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"]):
        if os.path.exists(p):
            pytesseract.pytesseract.tesseract_cmd = p
            break
except ImportError:
    print("[ERR] pytesseract, opencv-python, Pillow requis pour l'OCR.")
    print("      pip install pytesseract opencv-python Pillow numpy")
    sys.exit(1)

# --- Constantes ------------------------------------------------
GROUP_ID = "362347087928780"
GROUP_MEDIA_URL = f"https://www.facebook.com/groups/{GROUP_ID}/media"
MAX_PAGES = 500
GROUP_NAME = None
GROUP_POS = None
PHOTO_URL_TPL = "https://www.facebook.com/photo/?fbid={}"
RESULTS_DIR = "results"
DOWNLOAD_DIR = f"{RESULTS_DIR}/download"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
STATE_FILE = f"{RESULTS_DIR}/state.json"
RESULTS_FILE = f"{RESULTS_DIR}/urls.json"
EMAILS_CSV = f"{RESULTS_DIR}/emails.csv"
BATCH_SIZE = 200
UPDATE_INTERVAL = 60
STAGNANT_MAX = 20
SCROLL_DELAY = 0.3
SCROLL_FRACTION = 1.0

EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.IGNORECASE
)

# Domaine email connu ? (filtre etape 4 + debruitage)
_EMAIL_DOMAINS_FILE = Path(__file__).parent / "all_email_provider_domains.txt.txt"
_KNOWN_DOMAINS = frozenset(
    line.strip().lower() for line in _EMAIL_DOMAINS_FILE.read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.startswith("#")
) if _EMAIL_DOMAINS_FILE.exists() else frozenset()

def _is_known_domain(email):
    """Verifie si le domaine de l'email est dans la liste des fournisseurs connus."""
    try:
        at = email.index("@")
        return email[at+1:].lower() in _KNOWN_DOMAINS
    except ValueError:
        return False

DEBUG_DIR = Path("debug")
DEBUG_DIR.mkdir(exist_ok=True)

def debug_save(page_type, driver):
    """Sauvegarde l'URL + le HTML pour debug."""
    ts = time.strftime("%H%M%S")
    url = driver.current_url
    with open(DEBUG_DIR / f"{ts}_{page_type}_url.txt", "w", encoding="utf-8") as f:
        f.write(url)
    html = driver.page_source
    with open(DEBUG_DIR / f"{ts}_{page_type}_html.txt", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  [DEBUG] Sauvegardé : {ts}_{page_type}_url.txt / .html (taille: {len(html)})")

# --- Helpers ---------------------------------------------------

def extract_image_url(html):
    # Normaliser les slashs échappés (format JSON de Facebook)
    html_normalized = html.replace("\\/", "/")

    # Capture l'URL jusqu'à ", ', <, espace, >, ), \n, \r
    url_pat = r'https?://[^"\'<\s>)\\]*?(?:scontent|fbcdn)[^"\'<\s>)\\]*'
    # Version : tout domaine fbcdn
    url_pat2 = r'https?://[^"\'<\s>)\\]*\.fbcdn\.[^"\'<\s>)\\]*'

    strategies = [
        # 1. og:image (URL complète dans l'attribut)
        ('og:image', r'<meta\s+property="og:image"\s+content="([^"]+)"'),
        # 2. previewImage (JSON, avec slashs normalisés)
        ('previewImage', r'"previewImage":\{"uri":"([^"]+)"'),
        # 3. scontent / fbcdn dans le HTML
        ('scontent', url_pat),
        ('fbcdn', url_pat2),
    ]

    seen = set()
    for name, pat in strategies:
        for m in re.finditer(pat, html_normalized, re.IGNORECASE):
            url = m.group(1) if m.lastindex else m.group(0)
            # Nettoyer les restes d'encodage HTML
            url = url.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            # Éviter le doublon
            if url in seen:
                continue
            seen.add(url)
            # Filtrer les URL trop courtes (icônes par défaut)
            domain_end = url.find(".fbcdn.net")
            if domain_end > 0 and len(url) < domain_end + 25:
                continue
            # S'assurer que ce n'est pas l'icône par défaut Facebook
            if "453178253_471506465671661" in url:
                continue
            return url
    return None


def sync_cookies_to_session(driver, session):
    for c in driver.get_cookies():
        session.cookies.set(c["name"], c["value"])
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
    })


def count_links(driver):
    """Compte les liens de photos (plus rapide que count_photos)."""
    return len(driver.find_elements(By.CSS_SELECTOR, 'a[href*="/photo/?fbid="]'))


def extract_fbids_js(driver):
    """Extrait les fbid via JS (bien plus rapide que Selenium find_elements)."""
    return driver.execute_script("""
        const links = document.querySelectorAll('a[href*="/photo/?fbid="]');
        const fbids = new Set();
        links.forEach(a => {
            const m = a.href.match(/fbid=(\\d+)/);
            if (m) fbids.add(m[1]);
        });
        return Array.from(fbids).sort();
    """)


def save_state(state):
    now = datetime.now().isoformat()
    state["last_activity"] = now
    if "started_at" not in state:
        state["started_at"] = now
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def clear_state():
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)


def progress_bar(current, total, bar_len=40):
    filled = int(bar_len * current / total) if total > 0 else 0
    bar = "#" * filled + "." * (bar_len - filled)
    pct = 100.0 * current / total if total > 0 else 0
    return f"[{bar}] {current}/{total} ({pct:.0f}%)"


# --- OCR -------------------------------------------------------

def preprocess_image(img_path):
    img = cv2.imread(str(img_path))
    if img is None:
        raise ValueError(f"Impossible de lire : {img_path}")
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    if max(h, w) < 1000:
        gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary


def ocr_image(img_path):
    processed = preprocess_image(img_path)
    pil_img = Image.fromarray(processed)
    text = pytesseract.image_to_string(pil_img, lang="fra+eng", config="--oem 3 --psm 3")
    return text


_VALID_EMAIL = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9\-]+\.[a-zA-Z]{2,4}$')


def _extract_dept(group_name):
    """Extrait le numéro de département depuis le nom du groupe (ex: emploi06 -> 06)."""
    if not group_name:
        return ""
    m = re.search(r'(\d+)$', group_name)
    return m.group(1) if m else ""

def extract_emails(text):
    """Retourne une liste de {"email": str, "stage": int}."""
    seen = set()
    result = []

    def add(email, stage):
        el = email.lower()
        if el not in seen and _is_known_domain(el):
            seen.add(el)
            result.append({"email": el, "stage": stage})

    # ---- etape 1 : emails normaux user@domain.tld ----
    for e in EMAIL_RE.findall(text):
        add(e, 1)

    # ---- etape 2 : obfusqué [at]→@, [dot]→., (a)→@, (dot)→. ----
    cleaned = re.sub(r'[[(]\s*at\s*[])]', '@', text, flags=re.IGNORECASE)
    cleaned = re.sub(r'\bat\b', '@', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'[[(]\s*dot\s*[])]', '.', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\bdot\b', '.', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*\(a\)\s*', '@', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*\(dot\)\s*', '.', cleaned, flags=re.IGNORECASE)
    for e in EMAIL_RE.findall(cleaned):
        add(e, 2)

    # ---- etape 3 : OCR "user a domain . tld" (point obligatoire) ----
    for m in re.finditer(
        r'(?<![@\w])([a-zA-Z0-9._%+\-]{3,}?)\s*[a@]\s*([a-zA-Z0-9\-]{2,}?)\s*\.\s*([a-zA-Z]{2,4})(?![@\w])',
        cleaned, re.IGNORECASE
    ):
        candidate = f"{m.group(1)}@{m.group(2)}.{m.group(3)}"
        if _VALID_EMAIL.match(candidate) and _is_known_domain(candidate):
            add(candidate, 3)

    # ---- etape 4 : OCR continu "useradomaincom" (pas de @, pas de .) ----
    for m in re.finditer(r'(?<![@\w])([a-zA-Z0-9]{6,})(?![@\w])', cleaned, re.IGNORECASE):
        seq = m.group()
        if '@' in seq or '.' in seq:
            continue
        for at_pos in range(3, len(seq) - 4):
            if seq[at_pos] not in 'a@':
                continue
            local = seq[:at_pos]
            rest = seq[at_pos+1:]
            if len(rest) < 4:
                continue
            for dot_pos in range(1, len(rest) - 1):
                domain = rest[:dot_pos]
                tld = rest[dot_pos:]
                if len(domain) < 3 or len(tld) < 2 or len(tld) > 4:
                    continue
                candidate = f"{local}@{domain}.{tld}"
                if _VALID_EMAIL.match(candidate) and _is_known_domain(candidate):
                    add(candidate, 4)

    return result


def git_push_results(gname):
    if not gname or gname == "?":
        return
    csv_file = f"results/emails-{gname}.csv"
    from pathlib import Path as _P
    if not _P(csv_file).exists():
        return
    try:
        subprocess.run(["git", "add", csv_file], check=True, timeout=30, capture_output=True)
        msg = f"resultats OCR {gname} - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        subprocess.run(["git", "commit", "-m", msg], check=False, timeout=30, capture_output=True)
        subprocess.run(["git", "push"], check=True, timeout=120, capture_output=True)
        print(f"    [git] push OK -> {csv_file}")
    except Exception as e:
        print(f"    [git] push: {e}")


def cleanup_downloads(gname):
    if not gname or gname == "?":
        return
    import shutil as _su
    dl_dir = f"results/download-{gname}"
    if _su.os.path.isdir(dl_dir):
        _su.rmtree(dl_dir)
        print(f"    [cleanup] {dl_dir}/ supprime")


def process_image_ocr(img_path, url=None, owner_id="", group_id="", dept_num="",
                      image_width=None, image_height=None, accessibility_caption=""):
    try:
        text = ocr_image(img_path)
        emails = extract_emails(text)
        raw = text.strip().replace("\n", " | ")[:300]
        print(f"    [DEBUG OCR] {raw}")

        return {
            "file": img_path.name,
            "fbid": img_path.stem,
            "image_url": url or "",
            "owner_id": owner_id,
            "group_id": group_id,
            "dept_num": dept_num,
            "image_width": image_width,
            "image_height": image_height,
            "accessibility_caption": accessibility_caption,
            "emails": emails,
            "raw_text": text.strip().replace("\n", " ")[:500],
            "collected_at": datetime.now().isoformat(),
        }
    except Exception as e:
        print(f"  [WARN] OCR {img_path.name} : {e}")
        return None


# --- Scraper principal ----------------------------------------

class FacebookScraper:
    def __init__(self):
        self.driver = None
        self.session = requests.Session()

    # -- Setup ----------------------------------------------

    def start_driver(self):
        print("[*] Lancement du navigateur…")
        if sys.platform == "win32":
            chromedriver_path = os.path.join(
                os.environ.get("USERPROFILE", "C:/Users/nawak"),
                "appdata/roaming/undetected_chromedriver/undetected_chromedriver.exe",
            )
            if not os.path.exists(chromedriver_path):
                chromedriver_path = "chromedriver.exe"
        else:
            chromedriver_path = shutil.which("chromedriver")
            if not chromedriver_path:
                for p in ["/usr/bin/chromedriver", "/usr/local/bin/chromedriver"]:
                    if os.path.exists(p):
                        chromedriver_path = p
                        break
            if not chromedriver_path:
                chromedriver_path = "chromedriver"

        service = Service(chromedriver_path)
        options = webdriver.ChromeOptions()
        chrome_binary = shutil.which("google-chrome") or shutil.which("google-chrome-stable") or "/usr/bin/google-chrome"
        options.binary_location = chrome_binary
        if not getattr(self, "no_headless", False):
            options.add_argument("--headless=new")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--window-size=1280,900")
        options.add_argument("--lang=fr")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--disable-dev-shm-usage")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        # Bloquer toutes les notifications navigateur
        prefs = {
            "profile.default_content_setting_values.notifications": 2,
            "profile.default_content_setting_values.popups": 2,
        }
        options.add_experimental_option("prefs", prefs)

        self.driver = webdriver.Chrome(service=service, options=options)

        # Stealth : masquer la détection Selenium
        self.driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                Object.defineProperty(navigator, "webdriver", {get: () => undefined});
                Object.defineProperty(navigator, "plugins", {get: () => [1,2,3,4,5]});
            """
        })

    def close(self):
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass

    # -- Login (manuel) -------------------------------------

    def _is_logged_in(self):
        """Vérifie si on est bien connecté (page d'accueil ou fil d'actualité)."""
        cur = self.driver.current_url
        if "login" in cur and "checkpoint" not in cur:
            return False
        if "facebook.com" not in cur:
            return False
        # Éléments typiques d'une page connectée
        try:
            self.driver.find_element(By.CSS_SELECTOR, '[aria-label="Facebook"]')
            return True
        except Exception:
            pass
        try:
            self.driver.find_element(By.CSS_SELECTOR, '[data-pagelet="FeedUnit"]')
            return True
        except Exception:
            pass
        if cur.rstrip("/") in ("https://www.facebook.com", "https://www.facebook.com/?"):
            return True
        if "facebook.com/?sk=welcome" in cur:
            return True
        if "facebook.com/?sk=h_chr" in cur:
            return True
        return False

    def _is_anti_bot_page(self):
        """Détecte si on est sur une page anti-bot / checkpoint."""
        cur = self.driver.current_url
        page = self.driver.page_source.lower()
        if "checkpoint" in cur:
            return True
        if "two_factor" in page or "approvals_code" in page:
            return True
        keywords = [
            "confirm your identity", "confirmer votre identité",
            "enter the code", "saisissez le code",
            "security check", "vérification de sécurité",
            "suspicious login", "connexion suspecte",
            "we noticed a new login", "nouvelle connexion",
            "was this you", "est-ce bien vous",
        ]
        return any(k in page for k in keywords)

    def _wait_input(self, msg=""):
        """input() avec gestion d'erreur."""
        try:
            if msg:
                return input(msg)
            return input()
        except (EOFError, OSError):
            print("  (entrée non disponible, attente 30s…)")
            time.sleep(30)
            return ""

    def login(self):
        """Connexion manuelle : l'utilisateur se connecte dans le navigateur."""
        self.driver.get("https://www.facebook.com")
        print("\n" + "=" * 55)
        print("  CONNEXION MANUELLE REQUISE")
        print("  Connecte-toi à Facebook dans le navigateur ouvert.")
        print("  Si tu es déjà connecté, tu peux continuer.")
        print("  Appuie sur Entrée une fois connecté.")
        print("=" * 55)
        self._wait_input()

        # Vérifier et attendre un peu
        time.sleep(3)
        if self._is_logged_in():
            print("[OK] Connecté")
        else:
            for _ in range(30):
                time.sleep(2)
                if self._is_logged_in():
                    print("[OK] Connecté")
                    break
                if self._is_anti_bot_page():
                    print("[!] Page anti-bot détectée. Résous-la dans le navigateur.")
                    print("    Appuie sur Entrée quand c'est fait.")
                    self._wait_input()
                    if self._is_logged_in():
                        print("[OK] Connecté")
                        break
            else:
                print("[!] Connexion non confirmée. Continuation quand même.")

        sync_cookies_to_session(self.driver, self.session)
        print("[*] Cookies synchronisés")

    # -- Phase 1 : Scroll -----------------------------------

    def _handle_relogin_popup(self):
        """Détecte et remplit le popup 'Voir plus sur Facebook' qui demande une re-connexion."""
        for _ in range(15):
            try:
                # Chercher un champ email/password visible (popup de re-login)
                email_popup = self.driver.find_element(By.CSS_SELECTOR, 'input[autocomplete="username"]')
                if email_popup and email_popup.is_displayed():
                    print("[*] Popup de re-connexion détecté, remplissage…")
                    # Mot de passe
                    pwd_inputs = self.driver.find_elements(By.CSS_SELECTOR, 'input[type="password"]')
                    if not pwd_inputs:
                        pwd_inputs = self.driver.find_elements(By.CSS_SELECTOR, 'input[autocomplete="current-password"]')
                    if not pwd_inputs:
                        time.sleep(1)
                        continue
                    pwd_popup = pwd_inputs[0]
                    email_popup.clear()
                    email_popup.send_keys(self.email)
                    time.sleep(0.5)
                    pwd_popup.clear()
                    pwd_popup.send_keys(self.password)
                    time.sleep(0.5)
                    # Bouton de connexion
                    clicked = False
                    for sel in [
                        'div[aria-label="Se connecter"]',
                        'div[aria-label="Log in"]',
                        'button[type="submit"]',
                        'div[role="button"]:not([aria-hidden])',
                    ]:
                        for b in self.driver.find_elements(By.CSS_SELECTOR, sel):
                            txt = b.text.strip().lower()
                            if b.is_displayed() and any(k in txt for k in ["se connecter", "log in", "continuer", "continue"]):
                                self.driver.execute_script("arguments[0].click()", b)
                                clicked = True
                                break
                        if clicked:
                            break
                    if not clicked:
                        # Fallback : Enter sur le champ mot de passe
                        pwd_popup.send_keys("\n")
                    time.sleep(3)
                    return True
            except Exception:
                pass
            time.sleep(1)
        return False

    def _wait_for_group_page(self):
        """Attend d'être sur la page du groupe.
        Si anti-bot déclenche et redirige vers facebook.com, prévient l'utilisateur.
        """
        # URLs attendues après navigation vers le groupe
        expected_prefixes = [
            f"https://www.facebook.com/groups/{GROUP_ID}",
            f"https://facebook.com/groups/{GROUP_ID}",
        ]
        previous_url = self.driver.current_url

        for _ in range(40):
            time.sleep(0.5)
            cur = self.driver.current_url

            # 1) On est bien sur la page groupe -> OK
            if GROUP_ID in cur and "media" in cur:
                return True

            # 2) Changement d'URL inattendu : anti-bot a redirigé
            #    (de la page groupe vers facebook.com ou checkpoint)
            is_expected = any(cur.startswith(p) for p in expected_prefixes)
            was_group_before = any(previous_url.startswith(p) for p in expected_prefixes)

            if was_group_before and not is_expected and "facebook.com" in cur:
                debug_save("antibot_redirect", self.driver)
                print("\n" + "!" * 58)
                print("  ANTI-BOT DÉTECTÉ — redirection automatique")
                print(f"  De: groupe/{GROUP_ID}/media")
                print(f"  Vers: {cur[:70]}")
                print("   -> Résous le défi manuellement dans le navigateur")
                print("   -> Le script attend patiemment")
                print("!" * 58)
                # Attendre que l'utilisateur résolve
                for _ in range(180):  # max 6 min
                    time.sleep(2)
                    cur2 = self.driver.current_url
                    if GROUP_ID in cur2 and "media" in cur2:
                        print("[OK] Page groupe atteinte après résolution")
                        return True
                    # Si on est revenu sur facebook.com sans anti-bot, re-essayer
                    if "facebook.com" in cur2 and GROUP_ID not in cur2:
                        pass  # toujours en attente
                # Timeout
                print("   -> Appuie sur Entrée quand la page du groupe est affichée.")
                self._wait_input()
                self.driver.get(GROUP_MEDIA_URL)
                time.sleep(5)
                continue

            # 3) Anti-bot classique (si jamais on le catch avant la redirection)
            if self._is_anti_bot_page():
                debug_save("antibot_caught", self.driver)
                print("\n" + "!" * 58)
                print("  ANTI-BOT DÉTECTÉ sur la page")
                print(f"  URL: {cur[:70]}")
                print("   -> Résous le défi manuellement dans le navigateur")
                print("   -> Puis appuie sur Entrée")
                print("!" * 58)
                self._wait_input()
                if GROUP_ID in self.driver.current_url and "media" in self.driver.current_url:
                    return True
                self.driver.get(GROUP_MEDIA_URL)
                time.sleep(5)
                continue

            previous_url = cur

        debug_save("wait_failed", self.driver)
        return False

    def scroll_media_page(self, state=None):
        print(f"\n{'='*50}")
        print("Phase 1 : Scroll de la page média")
        print(f"{'='*50}")
        self.driver.get(GROUP_MEDIA_URL)
        print("[*] Chargement de la page groupe…")
        time.sleep(6)
        debug_save("group_first_load", self.driver)

        # Attendre d'être sur la bonne page
        if not self._wait_for_group_page():
            print("[!] Page groupe non atteinte. Vérifie le navigateur.")
            print("    Appuie sur Entrée quand tu es sur la page du groupe.")
            self._wait_input()
            if GROUP_ID not in self.driver.current_url:
                self.driver.get(GROUP_MEDIA_URL)
                time.sleep(5)

        # Rejeter les popups éventuelles
        self._dismiss_popups()

        # Gérer le popup de re-connexion
        if self._handle_relogin_popup():
            print("[OK] Re-connexion effectuée")
            time.sleep(4)
            self._dismiss_popups()
            sync_cookies_to_session(self.driver, self.session)

        # Vérifier qu'on est bien sur la page
        if not self._is_logged_in():
            print("[!] Session non détectée sur la page groupe.")
            print("    Vérifie le navigateur et appuie sur Entrée.")
            self._wait_input()
            time.sleep(3)

        self._dismiss_popups()

        # Scroll : on défile jusqu'en bas à chaque tour pour forcer le lazy load
        stagnant = 0
        all_fbids = extract_fbids_js(self.driver)
        print(f"  Liens : {len(all_fbids)} fbid")

        scroll_container = """
            return (document.scrollingElement || document.documentElement).scrollHeight;
        """

        while stagnant < STAGNANT_MAX:
            old_count = len(all_fbids)

            # Rejeter les popups qui pourraient bloquer le focus
            self._dismiss_popups()

            # Scroll progressif par petits paliers jusqu'en bas
            for _ in range(5):
                self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(0.5)

            # Cliquer sur le body pour remettre le focus sur la page
            self.driver.execute_script("document.body.click()")
            time.sleep(0.3)

            # Attendre que Facebook ait fini de charger
            time.sleep(1.5)

            # Vérifier les nouveaux liens
            links = count_links(self.driver)
            fbids_now = extract_fbids_js(self.driver)

            if len(fbids_now) > len(all_fbids):
                print(f"  {links} liens / {len(fbids_now)} fbid")
                all_fbids = fbids_now
                stagnant = 0
                if state is not None:
                    state["phase"] = "scroll"
                    state["fbids"] = all_fbids
                    save_state(state)
            else:
                stagnant += 1
                print(f"  stagnant {stagnant}/{STAGNANT_MAX} ({len(fbids_now)} fbid)")

        all_fbids = extract_fbids_js(self.driver)
        print(f"\n[OK] Scroll terminé : {len(all_fbids)} photos trouvées")
        return all_fbids

    def _dismiss_popups(self):
        for _ in range(8):
            try:
                # Boutons "Decline / Allow cookies"
                btns = self.driver.find_elements(By.CSS_SELECTOR, 'div[role="button"]')
                for b in btns:
                    txt = b.text.strip().lower()
                    if any(k in txt for k in ["decline", "allow", "cookie", "accepter", "refuser", "ok", "fermer", "close", "not now", "plus tard"]):
                        self.driver.execute_script("arguments[0].click()", b)
                        time.sleep(0.5)
            except Exception:
                pass
            try:
                # "Not now" pour les notifications
                not_now = self.driver.find_element(By.XPATH, "//span[text()='Not Now']")
                not_now.click()
                time.sleep(0.5)
            except Exception:
                pass
            try:
                # Boutons "Allow" dans les notifications navigateur
                for b in self.driver.find_elements(By.XPATH, "//*[contains(text(), 'Allow')]"):
                    if b.is_displayed():
                        self.driver.execute_script("arguments[0].click()", b)
                        time.sleep(0.5)
            except Exception:
                pass
            # Overlay générique : click sur body pour perdre le focus
            try:
                self.driver.execute_script(
                    "document.querySelector('div[role=\"presentation\"]')?.remove()"
                )
            except Exception:
                pass

    # -- Phase 2 : Fetch URLs -------------------------------

    def fetch_photo_urls(self, all_fbids, processed, results, state=None):
        print(f"\n{'='*50}")
        print("Phase 2 : Récupération des URLs haute résolution")
        print(f"{'='*50}")

        if not self.driver:
            print("[!] Selenium non disponible, abandon.")
            return results

        remaining = [f for f in all_fbids if f not in processed]
        print(f"  {len(processed)}/{len(all_fbids)} déjà traités")
        print(f"  {len(remaining)} restants\n")

        for idx, fbid in enumerate(remaining):
            url = None
            error = None

            try:
                self.driver.get(PHOTO_URL_TPL.format(fbid))
                WebDriverWait(self.driver, 8).until(
                    lambda d: d.execute_script("return document.readyState") == "complete"
                )
                time.sleep(0.2)

                # Extraction via JS : og:image > img[src*=scontent] > img[src*=fbcdn]
                url = self.driver.execute_script("""
                    var og = document.querySelector('meta[property="og:image"]');
                    if (og) return og.getAttribute('content');

                    var imgs = document.querySelectorAll('img[src*="scontent"], img[src*="fbcdn"]');
                    for (var i = 0; i < imgs.length; i++) {
                        if (imgs[i].naturalWidth > 100) return imgs[i].src;
                    }

                    var all = document.getElementsByTagName('img');
                    for (var i = 0; i < all.length; i++) {
                        if (all[i].naturalWidth > 200) return all[i].src;
                    }
                    return null;
                """)

                if url:
                    # Nettoyer
                    url = url.replace("&amp;", "&")
                    # Filtrer les icônes par défaut
                    if "static.xx.fbcdn.net" in url or "rsrc.php" in url:
                        url = None
            except Exception as e:
                error = str(e)

            entry = {"fbid": fbid, "url": url}
            if error:
                entry["error"] = error

            results.append(entry)
            processed.add(fbid)

            num = len(processed)
            total = len(all_fbids)
            status = "OK" if url else ("ERR" if error else "NONE")
            print(f"  [{num:>4}/{total}] {fbid}  ->  {status}")

            # Sauvegarde par lots
            if num % BATCH_SIZE == 0 or num == total:
                with open(RESULTS_FILE, "w", encoding="utf-8") as f:
                    json.dump(results, f, ensure_ascii=False, indent=2)
                if state is not None:
                    state["phase"] = "fetch"
                    state["processed"] = sorted(processed)
                    state["results"] = results
                    save_state(state)
                print(f"\n  --- Lot sauvegardé : {progress_bar(num, total)} ---\n")

            # Pause pour éviter le rate limiting
            time.sleep(0.3)

        # Sauvegarde finale
        with open(RESULTS_FILE, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n[OK] URLs récupérées : {len([r for r in results if r.get('url')])}/{len(results)}")
        return results

    # -- Phase 2b : Fetch URLs via GraphQL (rapide) ----------

    def fetch_photo_urls_graphql(self, group_id):
        """Utilise l'API GraphQL via fetch() du navigateur (pas de blocage TLS).
        Fonctionne sans auth pour les groupes publics."""
        print(f"\n{'='*50}")
        print("Phase 2b : Récupération via API GraphQL")
        print(f"{'='*50}")

        # Extraire LSD depuis la page courante (le navigateur est déjà sur le groupe)
        lsd = self.driver.execute_script("""
            var m = document.cookie.match(/\\bsd=([^;]+)/) ||
                    document.cookie.match(/lsd=([^;]+)/);
            if (m) return m[1];
            if (window.__LSD) return window.__LSD;
            m = document.body.innerHTML.match(/\\"LSD\\",\\[\\],\\{\\"token\\":\\"([^\\"]+)\\"}/);
            if (m) return m[1];
            var scripts = document.querySelectorAll('script[data-bootloader-hash]');
            for (var s of scripts) {
                if (s.textContent.includes('LSD')) {
                    m = s.textContent.match(/\\"token\\":\\"([^\\"]+)\\"}/);
                    if (m) return m[1];
                }
            }
            try {
                var l = localStorage.getItem('__LSD') || localStorage.getItem('lsd');
                if (l) return l;
            } catch(e) {}
            return '';
        """)

        if not lsd:
            print("[!] LSD token non trouvé, fallback Selenium")
            return None

        print(f"  LSD: {lsd[:20]}...")

        results = []
        cursor = None
        page = 0
        has_next = True

        from urllib.parse import quote

        while has_next:
            page += 1
            variables = {
                "count": 8,
                "cursor": cursor,
                "scale": 1,
                "id": str(group_id),
            }

            data = {
                "lsd": lsd,
                "fb_api_caller_class": "RelayModern",
                "fb_api_req_friendly_name": "GroupsCometMediaPhotosTabGridQuery",
                "server_timestamps": "true",
                "variables": json.dumps(variables, separators=(",", ":")),
                 "doc_id": DOC_ID,
            }

            # Utiliser fetch() du navigateur pour éviter le blocage anti-bot
            params = "&".join(f"{k}={quote(str(v))}" for k, v in data.items())
            # Échapper pour l'injection dans la chaîne JS
            params_js = params.replace("\\", "\\\\").replace("'", "\\'")
            script = f"""
                return fetch('https://www.facebook.com/api/graphql/', {{
                    method: 'POST',
                    headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
                    body: '{params_js}'
                }}).then(r => r.text()).catch(e => 'FETCH_ERR: ' + e.message);
            """

            try:
                body = self.driver.execute_script(script)
                if body and body.startswith("FETCH_ERR:"):
                    print(f"  Erreur fetch page {page}: {body}")
                    break

                if body and body.startswith("for (;;);"):
                    body = body[len("for (;;);"):]

                result = json.loads(body) if body else {}
                media = (
                    result.get("data", {}).get("node", {})
                    .get("group_mediaset", {}).get("media", {})
                )
                if not media:
                    break

                edges = media.get("edges", [])
                page_info = media.get("page_info", {})

                for edge in edges:
                    node = edge.get("node", {})
                    entry = {
                        "fbid": node.get("id", ""),
                        "url": node.get("image", {}).get("uri", ""),
                        "owner": node.get("owner", {}).get("id", ""),
                    }
                    if entry["url"]:
                        results.append(entry)

                cursor = page_info.get("end_cursor", "")
                has_next = page_info.get("has_next_page", False)
                print(f"  Page {page:>4} | {len(edges)} photos | total: {len(results)}")

            except Exception as e:
                print(f"  Erreur page {page}: {e}")
                break

        if results:
            with open(RESULTS_FILE, "w", encoding="utf-8") as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            print(f"\n[OK] {len(results)} URLs -> {RESULTS_FILE}")

        return results

    # -- Phase 3 : OCR direct sur les photos ----------------

    def _ocr_image_from_page(self, fbid):
        """Va sur la page photo, récupère l'img via canvas, la sauvegarde et la passe à l'OCR.
        Retourne (dict_ocr | None, chemin_image | None)."""
        try:
            self.driver.get(PHOTO_URL_TPL.format(fbid))
            time.sleep(1.5)
            WebDriverWait(self.driver, 8).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )

            # Extraire l'image en base64 via canvas
            b64 = self.driver.execute_script("""
                var img = document.querySelector('img[src*="scontent"], img[src*="fbcdn"]');
                if (!img || img.naturalWidth < 50) return null;
                var c = document.createElement('canvas');
                c.width = img.naturalWidth;
                c.height = img.naturalHeight;
                var ctx = c.getContext('2d');
                ctx.drawImage(img, 0, 0);
                return c.toDataURL('image/jpeg').split(',')[1];
            """)
            if not b64:
                return None, None

            import base64
            from io import BytesIO
            data = base64.b64decode(b64)

            # Sauvegarder l'image
            out = Path(DOWNLOAD_DIR) / f"{fbid}.jpg"
            out.parent.mkdir(exist_ok=True)
            with open(out, "wb") as f:
                f.write(data)

            # OCR direct sur les bytes
            try:
                nparr = np.frombuffer(data, np.uint8)
                img_cv = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if img_cv is None:
                    return None, out

                gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
                h, w = gray.shape
                if max(h, w) < 1000:
                    gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
                gray = cv2.fastNlMeansDenoising(gray, h=10)
                binary = cv2.adaptiveThreshold(
                    gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, blockSize=31, C=15
                )
                pil_img = Image.fromarray(binary)
                text = pytesseract.image_to_string(pil_img, lang="fra+eng", config="--oem 3 --psm 6")
                emails = extract_emails(text)

                if emails:
                    return {
                        "file": out.name,
                        "fbid": fbid,
                        "owner_id": "",
                        "group_id": GROUP_ID,
                        "dept_num": _extract_dept(GROUP_NAME),
                        "image_width": w,
                        "image_height": h,
                        "accessibility_caption": "",
                        "emails": emails,
                        "raw_text": text.strip().replace("\n", " ")[:500],
                        "collected_at": datetime.now().isoformat(),
                    }, out
                # Pas d'email → supprimer l'image
                out.unlink(missing_ok=True)
                return None, None

            except Exception:
                out.unlink(missing_ok=True)
                return None, None

        except Exception:
            return None, None

    def _ocr_image_from_url(self, url, fbid):
        """Télécharge l'image via requests et exécute l'OCR directement.
        Retourne (dict_ocr | None, chemin_image | None)."""
        sync_cookies_to_session(self.driver, self.session)
        try:
            resp = self.session.get(
                url,
                headers={"Referer": PHOTO_URL_TPL.format(fbid)},
                stream=True, timeout=20,
            )
            if resp.status_code != 200:
                return None, None

            data = resp.content
            if len(data) < 5120:
                return None, None

            # Sauvegarder
            ct = resp.headers.get("Content-Type", "")
            ext = ".png" if "png" in ct else ".jpg"
            out = Path(DOWNLOAD_DIR) / f"{fbid}{ext}"
            out.parent.mkdir(exist_ok=True)
            with open(out, "wb") as f:
                f.write(data)

            # OCR direct
            try:
                nparr = np.frombuffer(data, np.uint8)
                img_cv = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if img_cv is None:
                    return None, out

                gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
                h, w = gray.shape
                if max(h, w) < 1000:
                    gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
                gray = cv2.fastNlMeansDenoising(gray, h=10)
                binary = cv2.adaptiveThreshold(
                    gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, blockSize=31, C=15
                )
                pil_img = Image.fromarray(binary)
                text = pytesseract.image_to_string(pil_img, lang="fra+eng", config="--oem 3 --psm 6")
                emails = extract_emails(text)

                if emails:
                    return {
                        "file": out.name,
                        "fbid": fbid,
                        "owner_id": "",
                        "group_id": GROUP_ID,
                        "dept_num": _extract_dept(GROUP_NAME),
                        "image_width": "",
                        "image_height": "",
                        "accessibility_caption": "",
                        "emails": emails,
                        "raw_text": text.strip().replace("\n", " ")[:500],
                        "collected_at": datetime.now().isoformat(),
                    }, out
                out.unlink(missing_ok=True)
                return None, None

            except Exception:
                out.unlink(missing_ok=True)
                return None, None

        except Exception:
            return None, None

    def download_images(self, results):
        """Phase 3 : Télécharge toutes les images depuis les URLs."""
        print(f"\n{'='*50}")
        print("Phase 3 : Téléchargement des images")
        print(f"{'='*50}")

        Path(DOWNLOAD_DIR).mkdir(exist_ok=True)
        total = len(results)
        ok = 0

        for i, item in enumerate(results, 1):
            fbid = item["fbid"]
            url = item.get("url")
            print(f"  [{i:>4}/{total}] {fbid} .. ", end="", flush=True)

            if not url:
                print("SKIP (pas d'URL)")
                continue

            sync_cookies_to_session(self.driver, self.session)
            try:
                resp = self.session.get(
                    url,
                    headers={"Referer": PHOTO_URL_TPL.format(fbid)},
                    stream=True, timeout=20,
                )
                if resp.status_code != 200:
                    print(f"HTTP {resp.status_code}")
                    continue

                data = resp.content
                if len(data) < 5120:
                    print("TROP PETIT")
                    continue

                ct = resp.headers.get("Content-Type", "")
                ext = ".png" if "png" in ct else ".jpg"
                out = Path(DOWNLOAD_DIR) / f"{fbid}{ext}"
                with open(out, "wb") as f:
                    f.write(data)
                ok += 1
                print("OK")
            except Exception as e:
                print(f"ERR {e}")

        print(f"\n[OK] {ok}/{total} images téléchargées dans {DOWNLOAD_DIR}/")

    # -- Phase 4 : OCR ----------------------------------------

    def run_ocr(self, download_dir=DOWNLOAD_DIR):
        """Phase 4 : OCR sur les images téléchargées, génère emails.csv.
        Utilisable aussi en solo (mode --ocr-only)."""
        gname = GROUP_NAME or f"grp{GROUP_ID}"
        notify("debut", group=gname, script="fb_selenium", group_pos=GROUP_POS, data={"mode": "ocr-only", "dossier": download_dir})
        print(f"\n{'='*50}")
        print("Phase 4 : OCR — Extraction des emails")
        print(f"{'='*50}")

        img_dir = Path(download_dir)
        if not img_dir.exists():
            print("[!] Dossier d'images introuvable.")
            notify("echec", group=gname, script="fb_selenium", group_pos=GROUP_POS, error="Dossier introuvable")
            return

        images = sorted(
            p for p in img_dir.iterdir()
            if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp"}
        )
        if not images:
            print("[!] Aucune image trouvée.")
            notify("echec", group=gname, script="fb_selenium", group_pos=GROUP_POS, error="Aucune image")
            return

        print(f"  {len(images)} image(s) à analyser\n")

        ocr_results = []
        for img in images:
            print(f"   ->  {img.name} .. ", end="", flush=True)
            res = process_image_ocr(img, dept_num=_extract_dept(GROUP_NAME))
            if res:
                ocr_results.append(res)
                print(f"OK  {[e['email'] for e in res['emails']]}")
            else:
                print("- (rien)")

        if not ocr_results:
            print("\n[!] Aucun email trouvé.")
            notify("info", group=gname, script="fb_selenium", group_pos=GROUP_POS, data={"mode": "ocr-only", "emails": 0, "images": len(images)})

        fieldnames = ["file", "fbid", "image_url", "fb_url", "email", "email_stage", "all_emails_in_image", "raw_text", "owner_id", "group_id", "dept_num", "image_width", "image_height", "accessibility_caption", "collected_at"]
        now_iso = datetime.now().isoformat()
        rows = []
        for r in ocr_results:
            raw = r.get("raw_text", "").replace('"', "'").strip()
            emails_list = r.get("emails", [])
            collected_at = r.get("collected_at", now_iso)
            owner_id = r.get("owner_id", "")
            group_id = r.get("group_id", GROUP_ID)
            dept_num = r.get("dept_num", "")
            iw = r.get("image_width", "")
            ih = r.get("image_height", "")
            acc_cap = r.get("accessibility_caption", "")
            fbid = r["fbid"]
            fb_url = f"https://www.facebook.com/photo/?fbid={fbid}"
            flat_emails = [e["email"] for e in emails_list]
            if emails_list:
                for entry in emails_list:
                    rows.append({
                        "file": r["file"],
                        "fbid": fbid,
                        "image_url": r.get("image_url", ""),
                        "fb_url": fb_url,
                        "email": entry["email"],
                        "email_stage": entry["stage"],
                        "all_emails_in_image": ", ".join(flat_emails),
                        "raw_text": raw,
                        "owner_id": owner_id,
                        "group_id": group_id,
                        "dept_num": dept_num,
                        "image_width": iw,
                        "image_height": ih,
                        "accessibility_caption": acc_cap,
                        "collected_at": collected_at,
                    })
            else:
                rows.append({
                    "file": r["file"],
                    "fbid": fbid,
                    "image_url": r.get("image_url", ""),
                    "fb_url": fb_url,
                    "email": "",
                    "email_stage": "",
                    "all_emails_in_image": "",
                    "raw_text": raw,
                    "owner_id": owner_id,
                    "group_id": group_id,
                    "dept_num": dept_num,
                    "image_width": iw,
                    "image_height": ih,
                    "accessibility_caption": acc_cap,
                    "collected_at": collected_at,
                })

        file_exists = os.path.exists(EMAILS_CSV) and os.path.getsize(EMAILS_CSV) > 0
        mode = "a" if file_exists else "w"
        with open(EMAILS_CSV, mode, newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if mode == "w":
                writer.writeheader()
            writer.writerows(rows)

        email_count = sum(1 for r in rows if r["email"])
        print(f"\n[OK] {email_count} email(s) sur {len(rows)} images -> {EMAILS_CSV}")
        git_push_results(gname)
        cleanup_downloads(gname)
        notify("ok", group=gname, script="fb_selenium", group_pos=GROUP_POS,
               data={"mode": "ocr-only", "emails": email_count, "images": len(images)})

    # -- Pipeline live : GraphQL (PowerShell) -> navigation Selenium (full-res) -> OCR page par page

    @staticmethod
    @staticmethod
    def _powershell_graphql_fbids(group_id, max_pages=500):
        """Récupère les URLs des photos via PowerShell + GraphQL (pas de login).
        Retourne une liste de dicts {"fbid": ..., "url": ...} ou None."""
        import time as _time
        from fb_graphql import fetch_lsd, graphql_page
        lsd, resolved_id, _, _ = fetch_lsd(group_id)
        if not lsd:
            return None
        photos = []
        cursor = None
        for page in range(1, max_pages + 1):
            print(f"  [GraphQL page {page}]", end="", flush=True)
            entries, cursor = graphql_page(lsd, resolved_id, cursor)
            if not entries:
                print(" empty")
                break
            for e in entries:
                if e.get("url"):
                    photos.append({
                        "fbid": e["fbid"],
                        "url": e["url"],
                        "owner": e.get("owner", ""),
                        "image_width": e.get("image_width"),
                        "image_height": e.get("image_height"),
                        "accessibility_caption": e.get("accessibility_caption", ""),
                    })
            print(f" +{len(entries)}")
            if not cursor:
                break
            _time.sleep(1.0)
        return photos

    def _process_photo(self, fbid, url, session, owner_id="", image_width=None, image_height=None, accessibility_caption=""):
        """Télécharge une photo depuis l'URL GraphQL et lance l'OCR directement."""
        label = f"  {fbid}"
        try:
            resp = session.get(
                url,
                headers={"Referer": PHOTO_URL_TPL.format(fbid), "User-Agent": UA},
                stream=True, timeout=20,
            )
            if resp.status_code != 200:
                print(f"{label}  HTTP {resp.status_code}")
                return None

            data = resp.content
            if len(data) < 1024:
                print(f"{label}  TROP PETIT ({len(data)} octets)")
                return None

            ct = resp.headers.get("Content-Type", "")
            ext = ".png" if "png" in ct else ".jpg"
            out_path = Path(DOWNLOAD_DIR) / f"{fbid}{ext}"
            with open(out_path, "wb") as f:
                f.write(data)

            return process_image_ocr(out_path, url=url, owner_id=owner_id, group_id=GROUP_ID,
                                     dept_num=_extract_dept(GROUP_NAME),
                                     image_width=image_width, image_height=image_height,
                                     accessibility_caption=accessibility_caption)
        except Exception as e:
            print(f"{label}  ERR {e}")
            return None

    def extract_full_url(self, fbid):
        """Visite la page photo et extrait l'URL full-res (Selenium, pas de login)."""
        try:
            photo_url = PHOTO_URL_TPL.format(fbid)
            print(f"    [DEBUG] Page: {photo_url}")
            self.driver.get(photo_url)
            WebDriverWait(self.driver, 12).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            time.sleep(1.0)
            url = self.driver.execute_script("""
                var og = document.querySelector('meta[property="og:image"]');
                if (og) return og.getAttribute('content');
                var imgs = document.querySelectorAll('img[src*="scontent"], img[src*="fbcdn"]');
                for (var i = 0; i < imgs.length; i++) {
                    if (imgs[i].naturalWidth > 100) return imgs[i].src;
                }
                return null;
            """)
            if url:
                url = url.replace("&amp;", "&")
                if "static.xx.fbcdn.net" in url or "rsrc.php" in url:
                    return None
            return url
        except Exception as e:
            print(f"    [DEBUG] extract_full_url error: {e}")
            return None

    def process_fbid_live(self, fbid, session):
        """Traite un fbid complet : extrait URL -> download -> OCR -> return result."""
        label = f"  {fbid}"
        url = self.extract_full_url(fbid)
        if not url:
            print(f"{label}  -> URL non trouvée")
            return None

        print(f"{label}  URL: {url[:120]}...")

        try:
            resp = session.get(
                url,
                headers={"Referer": PHOTO_URL_TPL.format(fbid), "User-Agent": UA},
                stream=True, timeout=20,
            )
            if resp.status_code != 200:
                print(f"{label}  -> HTTP {resp.status_code}")
                return None
            data = resp.content
            print(f"{label}  -> Taille: {len(data)} octets, Content-Type: {resp.headers.get('Content-Type', '?')}")

            if len(data) < 1024:
                print(f"{label}  -> TROP PETIT")
                return None

            ct = resp.headers.get("Content-Type", "")
            ext = ".png" if "png" in ct else ".jpg"
            out_path = Path(DOWNLOAD_DIR) / f"{fbid}{ext}"
            with open(out_path, "wb") as f:
                f.write(data)

            # Dimensions de l'image
            try:
                from PIL import Image as _PIL
                with _PIL.open(out_path) as img:
                    print(f"{label}  -> Dimensions: {img.size[0]}x{img.size[1]}")
            except Exception:
                pass

            res = process_image_ocr(out_path, url=url)
            if res:
                print(f"{label}  -> OCR: {[e['email'] for e in res['emails']]}")
            else:
                print(f"{label}  -> OCR: aucun email")
            return res
        except Exception as e:
            print(f"{label}  ERR {e}")
            return None

    @staticmethod
    def _normalize_emails(emails):
        """Convertit les emails ancien format (liste de strings) en format dict."""
        normalized = []
        for e in emails:
            if isinstance(e, str):
                normalized.append({"email": e, "stage": 1})
            elif isinstance(e, dict):
                normalized.append(e)
        return normalized

    def run_live(self):
        """Pipeline live : GraphQL (Tor) -> download -> OCR page par page."""
        import subprocess as _sp

        gname = GROUP_NAME or f"grp{GROUP_ID}"
        msg_id = notify("debut", group=gname, script="fb_selenium", group_pos=GROUP_POS, data={"mode": "live"})

        state = load_state()
        processed_fbids = set()
        ocr_results = []
        if state and state.get("phase") == "live":
            processed_fbids = set(state.get("processed_fbids", []))
            ocr_results = state.get("ocr_results", [])
            for r in ocr_results:
                r["emails"] = self._normalize_emails(r.get("emails", []))
            if processed_fbids:
                print(f"[*] Reprise : {len(processed_fbids)} photos déjà traitées")

        fbids = self._powershell_graphql_fbids(GROUP_ID, max_pages=MAX_PAGES)
        if not fbids:
            print("[!] Aucune photo trouvée via GraphQL")
            notify("echec", group=gname, script="fb_selenium", group_pos=GROUP_POS, error="Aucune photo via GraphQL", message_id=msg_id)
            return

        total_all = len(fbids)
        new_fbids = [p for p in fbids if p["fbid"] not in processed_fbids]
        total_new = len(new_fbids)

        if total_new == 0:
            print(f"[*] Toutes les {total_all} photos sont déjà traitées")
            email_count = sum(len(r["emails"]) for r in ocr_results)
            notify("ok", group=gname, script="fb_selenium", group_pos=GROUP_POS,
                   data={"deja_traite": True, "emails": email_count}, message_id=msg_id)
            return

        print(f"\n[*] {total_all} photos au total, {total_new} nouvelles\n")

        Path(DOWNLOAD_DIR).mkdir(exist_ok=True)
        session = requests.Session()
        session.headers.update({"User-Agent": UA})

        last_update = 0
        csv_saved_count = len(ocr_results)

        try:
            for idx, photo in enumerate(new_fbids):
                fbid = photo["fbid"]
                url = photo["url"]
                label = f"  [{idx+1:>4}/{total_new}] {fbid}"

                res = self._process_photo(fbid, url, session,
                    owner_id=photo.get("owner", ""),
                    image_width=photo.get("image_width"),
                    image_height=photo.get("image_height"),
                    accessibility_caption=photo.get("accessibility_caption", ""))
                if res:
                    ocr_results.append(res)
                    print(f"{label}  OK  {[e['email'] for e in res['emails']]}")
                else:
                    print(f"{label}  OK  -")
                processed_fbids.add(fbid)

                if (idx + 1) % BATCH_SIZE == 0 or idx == total_new - 1:
                    new_results = ocr_results[csv_saved_count:]
                    if new_results:
                        self._save_ocr_csv(new_results, append=csv_saved_count > 0)
                        csv_saved_count = len(ocr_results)
                    save_state({
                        "phase": "live",
                        "processed_fbids": sorted(processed_fbids),
                        "ocr_results": ocr_results,
                    })
                    email_count = sum(len(r["emails"]) for r in ocr_results)
                    print(f"\n  --- Lot : {idx+1}/{total_new}, {email_count} email(s) ---")

                if (idx + 1) - last_update >= UPDATE_INTERVAL or idx == total_new - 1:
                    email_count = sum(len(r["emails"]) for r in ocr_results)
                    all_emails = [e["email"] for r in ocr_results for e in r["emails"]]
                    emails_str = ", ".join(all_emails[:20])
                    notify("info", group=gname, script="fb_selenium", group_pos=GROUP_POS, message_id=msg_id,
                           data={"progression": f"{idx+1}/{total_new}", "emails": email_count,
                                 "liste": emails_str or "—"})
                    last_update = idx + 1

                time.sleep(0.5)

            new_results = ocr_results[csv_saved_count:]
            if new_results:
                self._save_ocr_csv(new_results, append=csv_saved_count > 0)
            self._print_ocr_summary(ocr_results)
            save_state({
                "phase": "live",
                "processed_fbids": sorted(processed_fbids),
                "ocr_results": ocr_results,
            })
            email_count = sum(len(r["emails"]) for r in ocr_results)
            all_emails = [e["email"] for r in ocr_results for e in r["emails"]]
            emails_str = ", ".join(all_emails[:30])
            notify("ok", group=gname, script="fb_selenium", group_pos=GROUP_POS, message_id=msg_id,
                   data={"mode": "live", "photos": total_all, "emails": email_count,
                         "liste": emails_str or "—"})
            git_push_results(gname)
            cleanup_downloads(gname)

        except KeyboardInterrupt:
            print("\n[!] Interrompu par l'utilisateur")
            save_state({"phase": "live", "processed_fbids": sorted(processed_fbids), "ocr_results": ocr_results})
            print(f"    État sauvegardé dans {STATE_FILE} (reprise possible)")
            notify("info", group=gname, script="fb_selenium", group_pos=GROUP_POS,
                   data={"interrompu": True, "processed": len(processed_fbids)}, message_id=msg_id)
        except Exception as e:
            print(f"\n[!] Erreur : {e}")
            save_state({"phase": "live", "processed_fbids": sorted(processed_fbids), "ocr_results": ocr_results})
            print(f"    État sauvegardé dans {STATE_FILE} (reprise possible)")
            notify("echec", group=gname, script="fb_selenium", group_pos=GROUP_POS, error=str(e), message_id=msg_id)
            raise

    def _save_ocr_csv(self, ocr_results, append=False):
        if not ocr_results:
            return
        fieldnames = ["file", "fbid", "image_url", "fb_url", "email", "email_stage", "all_emails_in_image", "raw_text", "owner_id", "group_id", "dept_num", "image_width", "image_height", "accessibility_caption", "collected_at"]
        now_iso = datetime.now().isoformat()
        rows = []
        for r in ocr_results:
            raw = r.get("raw_text", "").replace('"', "'").strip()
            emails_list = self._normalize_emails(r.get("emails", []))
            collected_at = r.get("collected_at", now_iso)
            owner_id = r.get("owner_id", "")
            group_id = r.get("group_id", "")
            dept_num = r.get("dept_num", "")
            iw = r.get("image_width", "")
            ih = r.get("image_height", "")
            acc_cap = r.get("accessibility_caption", "")
            fbid = r["fbid"]
            fb_url = f"https://www.facebook.com/photo/?fbid={fbid}"
            flat_emails = [e["email"] for e in emails_list]
            if emails_list:
                for entry in emails_list:
                    rows.append({
                        "file": r["file"],
                        "fbid": fbid,
                        "image_url": r.get("image_url", ""),
                        "fb_url": fb_url,
                        "email": entry["email"],
                        "email_stage": entry["stage"],
                        "all_emails_in_image": ", ".join(flat_emails),
                        "raw_text": raw,
                        "owner_id": owner_id,
                        "group_id": group_id,
                        "dept_num": dept_num,
                        "image_width": iw,
                        "image_height": ih,
                        "accessibility_caption": acc_cap,
                        "collected_at": collected_at,
                    })
            else:
                rows.append({
                    "file": r["file"],
                    "fbid": fbid,
                    "image_url": r.get("image_url", ""),
                    "fb_url": fb_url,
                    "email": "",
                    "email_stage": "",
                    "all_emails_in_image": "",
                    "raw_text": raw,
                    "owner_id": owner_id,
                    "group_id": group_id,
                    "dept_num": dept_num,
                    "image_width": iw,
                    "image_height": ih,
                    "accessibility_caption": acc_cap,
                    "collected_at": collected_at,
                })
        needs_header = not append
        mode = "a" if append else "w"
        with open(EMAILS_CSV, mode, newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if needs_header:
                writer.writeheader()
            writer.writerows(rows)
        print(f"    -> emails.csv mis à jour ({len(rows)} entrées)")

    def _print_ocr_summary(self, ocr_results):
        if not ocr_results:
            print(f"\n[!] Aucun email trouvé")
            return
        print(f"\n{'='*50}")
        print("Emails trouvés")
        print(f"{'='*50}")
        for r in ocr_results:
            emails = self._normalize_emails(r.get("emails", []))
            for e in emails:
                print(f"  {e['email']}")
        email_count = sum(len(r.get("emails", [])) for r in ocr_results)

# --- Entry point ----------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Facebook Group Media Scraper + OCR (Selenium)"
    )
    parser.add_argument("--ocr-only", metavar="DOSSIER",
                        help="Lancer uniquement l'OCR sur un dossier d'images")
    parser.add_argument("--no-headless", action="store_true",
                        help="Afficher le navigateur (desactiver headless)")
    parser.add_argument("--group-id", metavar="ID",
                        help="ID du groupe Facebook (defaut: 362347087928780)")
    parser.add_argument("--name", metavar="ETIQUETTE",
                        help="Prefixe pour isolement (state-{name}.json, download-{name}/, ...)")
    parser.add_argument("--max-pages", type=int, metavar="N",
                        help="Nombre max de pages GraphQL (defaut: 500)")
    parser.add_argument("--group-pos", metavar="X/Y",
                        help="Position du groupe dans la liste (ex: 15/83)")
    args = parser.parse_args()
    Path(RESULTS_DIR).mkdir(parents=True, exist_ok=True)

    if args.group_id:
        global GROUP_ID, GROUP_MEDIA_URL
        GROUP_ID = args.group_id
        GROUP_MEDIA_URL = f"https://www.facebook.com/groups/{GROUP_ID}/media"

    if args.name:
        global STATE_FILE, EMAILS_CSV, DOWNLOAD_DIR, GROUP_NAME
        STATE_FILE = f"{RESULTS_DIR}/state-{args.name}.json"
        EMAILS_CSV = f"{RESULTS_DIR}/emails-{args.name}.csv"
        DOWNLOAD_DIR = f"{RESULTS_DIR}/download-{args.name}"
        GROUP_NAME = args.name

    if args.group_pos:
        global GROUP_POS
        GROUP_POS = args.group_pos

    if args.max_pages:
        global MAX_PAGES
        MAX_PAGES = args.max_pages
    print(f"[*] Mode isolé : {STATE_FILE}, {EMAILS_CSV}, {DOWNLOAD_DIR}/")

    if args.ocr_only:
        print("[*] Mode OCR seul")
        s = FacebookScraper()
        s.run_ocr(args.ocr_only)
        return

    scraper = FacebookScraper()
    if args.no_headless:
        scraper.no_headless = True
    scraper.run_live()


if __name__ == "__main__":
    main()
