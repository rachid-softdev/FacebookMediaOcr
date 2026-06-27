# Facebook Group Photo Scraper + OCR

Scrape les photos d'un groupe Facebook public et extrait les emails par OCR.

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

### Mode par défaut (sans login, groupe public)
```bash
python fb_selenium.py
```

Afficher le navigateur (debug) :
```bash
python fb_selenium.py --no-headless
```

Changer de groupe (ID depuis l'URL, y compris les slugs texte) :
```bash
python fb_selenium.py --group-id 175831749464922
python fb_selenium.py --group-id offres.d.emploi.indre
```

### Exécution parallèle (VPS, plusieurs groupes simultanément)
```bash
# Chaque instance doit avoir un --name unique pour isoler fichiers + dossiers
python fb_selenium.py --name saisonniers --group-id 362347087928780
python fb_selenium.py --name indre    --group-id offres.d.emploi.indre
python fb_selenium.py --name ardennes --group-id offres.d.emploi.ardennes
```
Produit : `state-{name}.json`, `emails-{name}.csv`, `download-{name}/` — pas de conflit.

Pipeline :
1. **GraphQL** (PowerShell) → récupère les fbid des photos (4000 max)
2. **Selenium** → navigue sur chaque page photo, extrait l'URL full-résolution (og:image)
3. **Download** → télécharge l'image
4. **OCR** → prétraitement OpenCV → Tesseract → emails

**Reprise après interruption** : voir section [Reprise après interruption](#9-reprise-après-interruption) plus bas.

### OCR seul sur un dossier existant
```bash
python fb_selenium.py --ocr-only ./download
```

### GraphQL seul (PowerShell, sans Selenium)
```bash
python fb_graphql.py <group_id> --pages 50
```

### Découverte automatique des groupes par département

```bash
# Tous les 101 départements (métropole + outre-mer)
python discover_groups.py

# Départements spécifiques
python discover_groups.py --dept 01 08 36

# Simulation (n'écrit pas les fichiers)
python discover_groups.py --dry-run

# Forcer le re-traitement des departements deja decouverts
python discover_groups.py --force
```

Stratégie :
1. Charge `departements.json` (101 départements)
2. Ignore les départements déjà dans `groups.json` (sauf `--force`)
3. Génère le slug attendu `offres.d.emploi.{departement}` (ex: `offres.d.emploi.ain`)
4. Vérifie son existence via PowerShell
5. Si trouvé → résout l'ID numérique + vérifie l'API GraphQL
6. Enregistre dans `groups.json` + `groups.txt`

Produit :
- `groups.txt` : pour `run_all.sh` (format `name:group_id`)
- `groups.json` : format détaillé avec URL, source (pattern/search), département

```json
[
  {"name": "emploi01", "group_id": "927601630680870",
   "slug": "offres.d.emploi.ain",
   "url": "https://www.facebook.com/groups/offres.d.emploi.ain",
   "dept_num": "01", "dept_name": "Ain", "source": "pattern"}
]
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
| `groups.txt` | Groupes trouvés (name:group_id, pour run_all.sh) |
| `groups.json` | Groupes trouvés (format détaillé avec URLs + source) |
| `urls.json` / `urls_graphql.json` | fbid + URLs |
| `download-{name}/*.jpg` | Images téléchargées (préfixé par `--name`) |
| `emails-{name}.csv` | Emails extraits (préfixé par `--name`) |
| `state-{name}.json` | Reprise après interruption (préfixé par `--name`) |

## Structure
```
fb_selenium.py              # Script principal (Selenium + GraphQL + OCR)
fb_graphql.py               # GraphQL standalone (PowerShell)
discover_groups.py          # Decouverte auto des groupes par departement
notify.py                   # Notifications Discord (webhook)
departements.json           # Liste des 101 departements (source discover)
all_email_provider_domains.txt.txt  # Liste des domaines email connus
groups.txt                  # Groupes trouves (format name:group_id)
groups.json                 # Groupes trouves (format detaille avec URL)
run_all.sh                  # Lancement parallele (lit groups.txt)
facebook-media-ocr.service   # Unite systemd pour le service
facebook-media-ocr.timer     # Timer systemd (tous les lundis 2h)
facebook-media-ocr.logrotate # Rotation des logs (logrotate)
download-{name}/            # Images telechargees (selon --name)
emails-{name}.csv           # Resultats OCR (selon --name)
state-{name}.json           # Reprise (selon --name)
logs-{name}.txt             # Logs individuels (mode service)
```

## Compatibilité

| Élément | Windows | Ubuntu |
|---------|---------|--------|
| PowerShell | `powershell.exe` (intégré) | `pwsh` (PowerShell Core) |
| ChromeDriver | `%USERPROFILE%\appdata\...` | `/usr/bin/chromedriver` |
| Tesseract | `C:\Program Files\...` | `/usr/bin/tesseract` |
| Headless | `--headless=new` (par défaut, `--no-headless` pour debug) | Idem |

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
python fb_selenium.py --name saisonniers --group-id 362347087928780
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

### 3. Fichier groups.txt

Tous les groupes sont listés dans `groups.txt` (un par ligne, format `nom:group_id`) :

```
# Exemple (les lignes # sont ignorées)
saisonniers:362347087928780
ain:offres.d.emploi.ain
aisne:offres.d.emploi.aisne
allier:offres.d.emploi.allier
...
```

Les slug textes (`offres.d.emploi.ain`) comme les ID numériques fonctionnent.

**Pour ajouter un groupe** : une ligne dans `groups.txt`, c'est tout.

### 4. Script de lancement tout-en-un

```bash
chmod +x run_all.sh
./run_all.sh              # Mode screen (interactif)
./run_all.sh --systemd    # Mode service (arrière-plan direct)
```

Lit automatiquement `groups.txt`, lance chaque groupe. Le script Python
gère lui-même la reprise (state.json) et le skip si déjà terminé. Aucune
logique complexe dans le shell.

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

# Rotation des logs (evite l'accumulation)
sudo cp facebook-media-ocr.logrotate /etc/logrotate.d/facebook-media-ocr

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

**Nettoyage des logs :**
Les logs individuels (`logs-{name}.txt`) sont automatiquement nettoyés :
- **Truncate** au début de chaque run (`run_all.sh`) — pas d'accumulation entre deux exécutions
- **Logrotate** (`/etc/logrotate.d/facebook-media-ocr`) — rotation hebdo, 4 semaines archivées, compression, `copytruncate` pour les runs en cours

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
| Erreur `Failed to create shared context` | Sans conséquence, lancer avec `--no-headless` pour debug |
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

| RAM VPS | Max instances | 4 groupes | 90+ groupes |
|---------|---------------|-----------|-------------|
| 2 Go    | 3             | 3 sur 4   | 3 à la fois |
| 4 Go    | 11            | Tous      | 11 à la fois |
| 8 Go    | 25            | Tous      | 25 à la fois |

Si la RAM est insuffisante pour tout lancer d'un coup, les groupes en attente
démarreront automatiquement dès qu'une place se libère (quand un groupe
termine).

**Avec 90 départements** : le script tourne en continu, 3 à 11 groupes en
parallèle selon la RAM. Ceux qui sont déjà terminés sont sautés
automatiquement (vérification du `state-{name}.json`). Le temps total dépend
du nombre de photos par groupe, mais le caractère hebdomadaire laisse
largement le temps de tout traiter.

Voir le calcul en direct dans les logs :

```
[date] RAM totale : 1982 Mo — Parallélisme max : 3
  [  1/90] ain -> offres.d.emploi.ain
  [  2/90] aisne -> offres.d.emploi.aisne
  [  3/90] allier -> offres.d.emploi.allier
... (attente de place libre) ...
  [  4/90] alpes -> offres.d.emploi.alpes
```

### 9. Reprise après interruption

Chaque groupe a son propre fichier `state-{name}.json` qui fait office
de **marque-page** : il stocke le nombre de photos déjà traitées.

#### Cycle de vie d'un groupe

```
Démarrage
  │
  ├─ state-{name}.json trouvé ? ── NON ──> Départ à zéro
  │
  └─ OUI ──> Reprend à la photo N (index "processed")
               │
               ├─ "processed" >= total photos ──> Déjà fini, on passe
               │                                    (state supprimé)
               │
               └─ "processed" < total ──> On continue
```

#### Scénarios concrets

| Scénario | Ce qui se passe |
|----------|----------------|
| **Ctrl+C** en cours de route | `state-{name}.json` sauvegarde l'index atteint. Relancer reprend exactement à cette photo. |
| **Crash / reboot VPS** | Idem : le fichier state est préservé (écrit avant le crash) ou perdu → la prochaine exécution du timer relance tout ; les groupes avancés reprennent. |
| **Le timer du lundi suivant** | `run_all.sh` tue les éventuels vieux process, puis relance chaque groupe. Ceux qui étaient finis voient `processed >= total` et s'arrêtent en ~2s. Ceux qui n'avaient pas fini reprennent à l'index sauvegardé. |
| **Supprimer un groupe de groups.txt** | Les fichiers `state-{name}.json`, `download-{name}/`, `emails-{name}.csv` ne sont pas touchés. Ils restent sur le disque, à supprimer manuellement si souhaité. |
| **Ajouter un nouveau groupe** | Une ligne dans `groups.txt` suffit. Au prochain run, le groupe n'aura pas de state → traité intégralement. |

#### Fichier state.json brut

```json
{"phase": "live", "processed": 42, "ocr_results": [...]}
```

- `processed` : index de la dernière photo traitée (commence à 0)
- `ocr_results` : emails déjà trouvés (pour ne pas les perdre)
- Le state est **sauvegardé** à chaque batch (toutes les 200 photos) et sur Ctrl+C/erreur
- Le state est **supprimé** quand le groupe est terminé (plus rien à traiter)

#### Illustration

```
Semaine 1 : run_all.sh lance tous les groupes
  ├─ Groupe A (200 photos) : state créé → avance → terminé → state supprimé
  ├─ Groupe B (8 photos)   : state créé → avance → terminé → state supprimé
  ├─ Groupe C (4000 photos): state créé → avance jusqu'à 1500 → CRASH !
  └─ Groupe D (16 photos)  : jamais démarré (pas de state)

Semaine 2 : le timer relance
  ├─ Groupe A : state absent → démarre → "déjà 0 photo" → s'arrête (~2s)
  ├─ Groupe B : state absent → idem (~2s)
  ├─ Groupe C : state présent (processed=1500) → reprend à 1500 → finit → state supprimé
  └─ Groupe D : state absent → traite les 16 photos → state supprimé
```
