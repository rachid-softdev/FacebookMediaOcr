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

Changer de groupe (ID depuis l'URL, y compris les slugs texte) :
```bash
python fb_selenium.py --live --group-id 175831749464922
python fb_selenium.py --live --group-id offres.d.emploi.indre
```

### Exécution parallèle (VPS, plusieurs groupes simultanément)
```bash
# Chaque instance doit avoir un --name unique pour isoler fichiers + dossiers
python fb_selenium.py --live --name saisonniers --group-id 362347087928780
python fb_selenium.py --live --name indre    --group-id offres.d.emploi.indre
python fb_selenium.py --live --name ardennes --group-id offres.d.emploi.ardennes
```
Produit : `state-{name}.json`, `emails-{name}.csv`, `download-{name}/` — pas de conflit.

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
| `download-{name}/*.jpg` | Images téléchargées (préfixé par `--name`) |
| `emails-{name}.csv` | Emails extraits (préfixé par `--name`) |
| `state-{name}.json` | Reprise après interruption (préfixé par `--name`) |

## Structure
```
fb_selenium.py              # Script principal (Selenium + GraphQL + OCR)
fb_graphql.py               # GraphQL standalone (PowerShell)
all_email_provider_domains.txt.txt  # Liste des domaines email connus
run_all.sh                  # Lancement parallèle (screen ou --systemd)
facebook-media-ocr.service  # Unité systemd pour le service
facebook-media-ocr.timer    # Timer systemd (tous les lundis 2h)
download-{name}/            # Images téléchargées (selon --name)
emails-{name}.csv           # Résultats OCR (selon --name)
state-{name}.json           # Reprise (selon --name)
logs-{name}.txt             # Logs individuels (mode service)
```

## Compatibilité

| Élément | Windows | Ubuntu |
|---------|---------|--------|
| PowerShell | `powershell.exe` (intégré) | `pwsh` (PowerShell Core) |
| ChromeDriver | `%USERPROFILE%\appdata\...` | `/usr/bin/chromedriver` |
| Tesseract | `C:\Program Files\...` | `/usr/bin/tesseract` |
| Headless | `--headless=new` (par défaut, utiliser `--no-headless` pour voir le navigateur) | Idem |

## Déploiement VPS (Ubuntu)

### 1. Installation complète

```bash
# Mise à jour
sudo apt update && sudo apt upgrade -y

# Chrome
wget -qO- https://dl.google.com/linux/linux_signing_key.pub | sudo gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" | sudo tee /etc/apt/sources.list.d/google-chrome.list
sudo apt update && sudo apt install -y google-chrome-stable

# Dépendances système
sudo apt install -y chromium-chromedriver tesseract-ocr tesseract-ocr-fra pwsh git screen

# Python + pip
sudo apt install -y python3 python3-pip python3-venv

# Cloner le repo
git clone https://github.com/rachid-softdev/FacebookMediaOcr.git
cd FacebookMediaOcr

# Environnement Python
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Vérifications
google-chrome --version                                    # Chrome 149+
chromedriver --version                                     # ChromeDriver 149+
tesseract --version                                        # Tesseract + fra
pwsh --version                                             # PowerShell Core 7+
python3 -c "import selenium, cv2, pytesseract, requests; print('OK')"
```

### 2. Lancement parallèle avec screen

Chaque groupe dans une session `screen` dédiée :

```bash
# Lister les sessions en cours
screen -ls

# Créer une session pour un groupe
screen -S saisonniers
# Dans la session :
cd ~/FacebookMediaOcr
source .venv/bin/activate
python fb_selenium.py --live --name saisonniers --group-id 362347087928780
# Ctrl+A puis D pour détacher (le script continue)

# Lancer les autres groupes (chacun dans un nouveau screen)
screen -S indre
# ...
screen -S ardennes
# ...
screen -S jobenisere
# ...
```

**Raccourcis screen** :

| Commande | Action |
|----------|--------|
| `screen -S <nom>` | Nouvelle session nommée |
| `Ctrl+A D` | Détacher (laisser tourner) |
| `screen -r <nom>` | Rattacher une session |
| `screen -r` | Rattacher la session unique |
| `Ctrl+C` | Arrêter le script |
| `exit` | Fermer la session |
| `screen -ls` | Lister les sessions |

### 3. Script de lancement tout-en-un

```bash
chmod +x run_all.sh
./run_all.sh              # Mode screen (interactif)
./run_all.sh --systemd    # Mode service (arrière-plan direct)
```

### 4. Surveillance

```bash
# Mode screen : voir les sessions actives
screen -ls
screen -r saisonniers     # Ctrl+A D pour détacher

# Mode service : vérifier les process
ps aux | grep fb_selenium

# Logs individuels
tail -f logs-saisonniers.txt

# Vérifier l'avancement
cat state-saisonniers.json

# Consolider tous les emails trouvés
head -1 emails-saisonniers.csv > all_emails.csv
for f in emails-*.csv; do
  tail -n +2 "$f" >> all_emails.csv
done
echo "Consolidé dans all_emails.csv"
```

### 5. Exécution automatique hebdomadaire (systemd timer)

Fichiers fournis dans le repo :

```
facebook-media-ocr.service   # Service oneshot
facebook-media-ocr.timer     # Timer : tous les lundis à 2h
run_all.sh                   # Script de lancement (mode --systemd)
```

**Installation :**

```bash
# Éditer le chemin utilisateur dans le service
sudo sed -i 's/USERNAME/'"$(whoami)"'/g' facebook-media-ocr.service

# Copier les fichiers systemd
sudo cp facebook-media-ocr.service facebook-media-ocr.timer /etc/systemd/system/

# Activer le timer
sudo systemctl daemon-reload
sudo systemctl enable facebook-media-ocr.timer
sudo systemctl start facebook-media-ocr.timer

# Vérifier
sudo systemctl status facebook-media-ocr.timer
systemctl list-timers --all | grep facebook
```

**Fonctionnement :**
- Le timer déclenche le service **tous les lundis à 2h** (avec ±30min aléatoire)
- Le service tue d'abord les éventuels process `fb_selenium` encore en cours (`pkill -f`) avant de relancer — pas de doublon ni de conflit sur les fichiers
- `run_all.sh` détecte la RAM disponible et adapte le parallélisme : si la RAM est insuffisante pour tout lancer d'un coup, les groupes en attente démarrent automatiquement dès qu'une place se libère
- Chaque groupe a son propre `state-{name}.json` : reprise automatique

**Logs :**
```bash
journalctl -u facebook-media-ocr.service          # Sortie du service
tail -f logs-saisonniers.txt                       # Log individuel d'un groupe
```

**Désactiver :**
```bash
sudo systemctl stop facebook-media-ocr.timer
sudo systemctl disable facebook-media-ocr.timer
```

### 6. Mise à jour du code

```bash
cd ~/FacebookMediaOcr
git pull

# Redémarrer les sessions screen si utilisées
screen -ls | grep -oP '\d+\.\S+' | while read s; do screen -S "$s" -X quit; done
# Ou tuer les process systemd
pkill -f fb_selenium.py

# Relancer
./run_all.sh
```

### 7. Pièges courants

| Problème | Solution |
|----------|----------|
| `pwsh: command not found` | `sudo apt install pwsh` (PowerShell Core) |
| `chromedriver: not found` | `sudo apt install chromium-chromedriver` |
| Chrome ouvre une popup "Chrome n'est pas à jour" | Ignorer, le headless fonctionne quand même |
| Erreur `Failed to create shared context` | Sans conséquence, lancer avec `--no-headless` pour voir |
| `state.json` ne reprend pas au bon endroit | Supprimer `state-{name}.json` et relancer depuis le début |
| Concurrence sur le ChromeDriver | Chaque instance Selenium lance son propre driver, sans conflit |
| Processus zombie screen | `screen -wipe` pour nettoyer |

### 8. Parallélisation : limites et risques

#### Rate limiting Facebook

Chaque instance Selenium visite les pages photos avec **0.5s entre chaque requête**.
Avec 4 groupes en parallèle, le trafic total est d'environ **2-3 requêtes/seconde**
vers Facebook — ce qui reste très faible et **ne déclenche pas de rate limiting**
dans la pratique.

Points rassurants :
- Le script ne fait **aucune action** (clic, like, commentaire) — que des `GET`
- Les pages visitées sont **publiques** (pas de login, pas de session)
- Le délai de 0.5s est déjà bien plus lent qu'un humain
- Une exécution hebdomadaire laisse 7 jours entre deux runs

Si vous voulez réduire encore le risque :

```bash
# Ajouter un délai plus long entre chaque photo (modifier fb_selenium.py)
# Ligne 1283 : time.sleep(0.5) -> time.sleep(1.5)
```

#### RAM : adaptation automatique

`run_all.sh` détecte la RAM totale du VPS via `/proc/meminfo` et limite le
nombre d'instances lancées en parallèle :

```
RAM dispo = total - 512 Mo (marge système)
max = RAM_dispo / 300 Mo (estimation par Chrome headless)
```

| RAM VPS | Max instances | Groupes simultanés |
|---------|---------------|-------------------|
| 2 Go    | 3             | 3 sur 4           |
| 4 Go    | 11            | Tous (4)          |
| 8 Go    | 25            | Tous (4)          |

Si la RAM est insuffisante pour tout lancer d'un coup, les groupes en attente
démarreront automatiquement dès qu'une place se libère (quand un groupe termine).

Voir le calcul en direct dans les logs :

```
[date] RAM totale : 1982 Mo — Parallélisme max : 3
```
