# Facebook Group Photo Scraper + OCR

Scrape les photos d'un groupe Facebook public et extrait les emails par OCR.
Deux modes : **`--live`** (recommandé, sans login) ou **pipelines complet** (avec login).

## Prérequis

### Windows
| Logiciel | Installation |
|----------|-------------|
| Chrome | google.com/chrome |
| Python 3.11+ | python.org |
| Tesseract OCR | `winget install Tesseract-OCR` + [fra.traineddata](https://github.com/tesseract-ocr/tessdata/blob/main/fra.traineddata) |
| PowerShell | Intégré à Windows |

### Ubuntu
```bash
# Chrome
wget -qO- https://dl.google.com/linux/linux_signing_key.pub | sudo gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" | sudo tee /etc/apt/sources.list.d/google-chrome.list
sudo apt update && sudo apt install google-chrome-stable

# Dépendances
sudo apt install chromium-chromedriver tesseract-ocr tesseract-ocr-fra pwsh

# Python
pip install selenium opencv-python numpy pytesseract Pillow requests
```

### Les deux plateformes
```bash
pip install -r requirements.txt
```

## Utilisation

### Mode `--live` (recommandé, sans login, groupe public)
```bash
python fb_selenium.py --live
```

Afficher le navigateur (debug) :
```bash
python fb_selenium.py --live --no-headless
```

Changer de groupe (ID depuis l'URL) :
```bash
python fb_selenium.py --live --group-id 175831749464922
```

Pipeline :
1. **GraphQL** (PowerShell) → récupère les fbid des photos (4000 max)
2. **Selenium** → navigue sur chaque page photo, extrait l'URL full-résolution (og:image)
3. **Download** → télécharge l'image
4. **OCR** → prétraitement OpenCV → Tesseract → emails

**Reprise après interruption** : si le script est interrompu (Ctrl+C ou crash),
relancez la même commande. Le fichier `state.json` contient l'avancement et les
photos déjà traitées sont sautées automatiquement.

### Pipeline complet (avec login Facebook)
```bash
python fb_selenium.py --email user@example.com --password "monpass"
```
Phases : Login → Scroll page média → Fetch URLs → Download → OCR.

### OCR seul sur un dossier existant
```bash
python fb_selenium.py --ocr-only ./download
```

### GraphQL seul (standalone, sans Selenium)
```bash
python fb_graphql.py <group_id> --pages 50
```

## Algorithmes d'extraction d'emails

| Étape | Description |
|-------|-------------|
| 1. Standard | `user@domain.tld` — regex classique |
| 2. Obfusqué | `[at]`/`(a)` → `@`, `[dot]`/`(dot)` → `.` |
| 3. OCR avec point | `user a gmail . com` → `user@gmail.com` |
| 4. OCR sans point | `useradomaincom` → `user@domain.com` (si domaine connu) |

Filtre : seul `domain.tld` présent dans `all_email_provider_domains.txt.txt` (6104 fournisseurs) est conservé.

## Fichiers de sortie

| Fichier | Description |
|---------|-------------|
| `urls.json` / `urls_graphql.json` | fbid + URLs |
| `download/*.jpg` | Images téléchargées |
| `emails.csv` | Emails extraits |
| `state.json` | Reprise après interruption |

## Structure
```
fb_selenium.py       # Script principal (Selenium + GraphQL + OCR)
fb_graphql.py        # GraphQL standalone (PowerShell)
all_email_provider_domains.txt.txt  # Liste des domaines email connus
download/            # Images téléchargées
emails.csv           # Résultats OCR
state.json           # Reprise
```

## Compatibilité

| Élément | Windows | Ubuntu |
|---------|---------|--------|
| PowerShell | `powershell.exe` (intégré) | `pwsh` (PowerShell Core) |
| ChromeDriver | `%USERPROFILE%\appdata\...` | `/usr/bin/chromedriver` |
| Tesseract | `C:\Program Files\...` | `/usr/bin/tesseract` |
| Headless | `--headless=new` (par défaut, utiliser `--no-headless` pour voir le navigateur) | Idem |
