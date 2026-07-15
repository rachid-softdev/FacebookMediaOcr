# Facebook Group Photo Scraper + OCR

Scrape les photos d'un groupe Facebook public et extrait les emails par OCR.
Les requêtes GraphQL passent par **Tor SOCKS** (`socks5://127.0.0.1:9050`)
pour éviter le rate limiting Facebook par IP.

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
sudo apt install chromium-chromedriver tesseract-ocr tesseract-ocr-fra pwsh tor

# Python
pip install selenium opencv-python numpy pytesseract Pillow requests requests[socks]
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

Le mode live n'utilise pas Selenium (téléchargement direct via GraphQL + Tor).
`--no-headless` n'est utile qu'en mode `--ocr-only`.

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
1. **LSD token** : récupéré via PowerShell ou requests (selon `LD_PRELOAD`)
2. **GraphQL via Tor SOCKS** (Python requests → fbid + URL full-résolution, `scale=10`)
3. **Download** → télécharge l'image (requests, pas de Selenium)
4. **OCR** → prétraitement OpenCV → Tesseract → emails + raw_text

> Les requêtes GraphQL passent par **Tor SOCKS** (`socks5://127.0.0.1:9050`)
> pour éviter le rate limiting Facebook par IP.
> 
> `LD_PRELOAD` (torsocks) est désactivé dans `run_all.sh` car il bloque
> les connexions SOCKS locales et PowerShell.

**Reprise après interruption** : voir section [Reprise après interruption](#9-reprise-après-interruption) plus bas.

### OCR seul sur un dossier existant
```bash
python fb_selenium.py --ocr-only ./download
```

### Notifications Discord

Le script envoie des notifications à chaque étape via un webhook Discord :

| Étape | Emoji | Description |
|-------|-------|-------------|
| Début | ▶️ | Traitement démarré (mode, groupe) |
| Succès | ✅ | Traitement terminé (emails trouvés) |
| Aucun email | ℹ️ | Aucun email trouvé / déjà traité |
| Erreur | ❌ | Erreur avec le message |

Les notifications sont envoyées par `notify.py` (webhook intégré). Pas de configuration nécessaire.

**Édition du message** : au lieu de poster une nouvelle notification à chaque étape,
le script **édite le même message** pour afficher la progression (toutes les ~60 photos).
Le message initial (▶️) devient le message final (✅ / ❌) — pas de spam Discord.

### Enrichissement IA des donnees OCR

```bash
# Lit tous les state-*.json, genere des prompts par lots
python enrich.py

# Depuis des CSV existants
python enrich.py --from-csv emails-saisonniers.csv

# Un seul groupe
python enrich.py --name saisonniers
```

Le script formate les textes OCR en lots (raw_text), attend la reponse IA
(JSON valide), parse et sauvegarde dans `enriched-{name}.csv`.
Les champs extraits sont : `name`, `firstname`, `phone`, `email`, `city`, `job`.

Schema extrait par l'IA :

| Champ | Description |
|-------|-------------|
| `name` | Nom de famille |
| `firstname` | Prenom |
| `phone` | Telephone (06, 07, ...) |
| `email` | Email |
| `city` | Ville |
| `job` | Métier recherche |

> **Filtre** : seules les entrees avec un `email` sont conservees (pas d'email = offre employeur, pas un CV).

Le CSV de sortie (`emails-{name}.csv`) contient une colonne `raw_text` avec le texte OCR brut,
permettant un re-traitement sans re-télécharger les images.

### GraphQL seul via Tor SOCKS
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

# Google uniquement, sans essayer le pattern
python discover_groups.py --search-only
```

Stratégie :
1. Charge `departements.json` (101 départements)
2. Ignore les départements déjà dans `groups.json` (sauf `--force`) — les résultats existants sont **conservés et fusionnés** avec les nouveaux
3. Génère le slug attendu `offres.d.emploi.{departement}` (ex: `offres.d.emploi.ain`)
4. Vérifie son existence via PowerShell
5. Si trouvé → résout l'ID numérique + vérifie l'API GraphQL
6. **Si le pattern échoue** → fallback recherche **Google via Selenium** (Chrome headless) avec la requête `"offres d emploi" facebook groupe {num} {nom}`, première page uniquement
7. Les URLs Facebook trouvées sont vérifiées une par une (existence + ID + GraphQL)
8. Enregistre dans `groups.json` + `groups.txt` (fusionné avec l'existant, jamais d'écrasement)

Options :
- `--search-only` : saute l'étape pattern, va directement sur Google
- `--force` : ignore les déjà découverts, retraite tout depuis zéro
- `--dry-run` : simulation seule (n'écrit pas les fichiers)
- `--dept 01 02 03` : départements spécifiques uniquement

Comportement au re-lancement :
| Scenario | Résultat |
|----------|----------|
| `python discover_groups.py` | Charge l'existant, ignore les déjà trouvés, **ajoute les nouveaux** |
| `python discover_groups.py --force` | Retraite tout, **remplace** les fichiers |
| `python discover_groups.py --search-only --dept 13` | Cherche sur Google pour le 13 seulement, fusionné avec l'existant |

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

> La colonne `raw_text` dans le CSV stocke le texte OCR brut pour permettre
> un re-traitement (enrichissement IA, correction) sans re-télécharger les images.

## Fichiers de sortie

| Fichier | Description |
|---------|-------------|
| `groups.txt` | Groupes trouvés (name:group_id, pour run_all.sh) |
| `groups.json` | Groupes trouvés (format détaillé avec URLs + source) |
| `discover_groups.sh` | Script de découverte mensuelle (Google search) |
| `facebook-discover-groups.service` | Service systemd pour la découverte mensuelle |
| `facebook-discover-groups.timer` | Timer systemd : 1er lundi du mois à 00h |
| `urls.json` / `urls_graphql.json` | fbid + URLs |
| `download-{name}/*.jpg` | Images téléchargées (préfixé par `--name`) |
| `emails-{name}.csv` | Emails extraits + `raw_text` (préfixé par `--name`) |
| `enriched-{name}.csv` | Données enrichies par IA (préfixé par `--name`) |
| `state-{name}.json` | Reprise après interruption (préfixé par `--name`) |
| `logs-{name}.txt` | Logs individuels (préfixé par `--name`) |

## Structure
```
fb_selenium.py              # Script principal (Selenium + GraphQL + OCR)
fb_graphql.py               # GraphQL standalone (Tor SOCKS)
fb_doc_id.py                # Auto-découverte du doc_id GraphQL (Selenium + CDP)
discover_groups.py          # Decouverte auto des groupes par departement
notify.py                   # Notifications Discord (webhook)
departements.json           # Liste des 101 departements (source discover)
all_email_provider_domains.txt.txt  # Liste des domaines email connus
groups.txt                  # Groupes trouves (format name:group_id)
groups.json                 # Groupes trouves (format detaille avec URL)
run_all.sh                  # Lancement parallele (lit groups.txt)
enrich.py                   # Enrichissement OCR par IA (nom, prenom, tel, ville...)
fb_doc_id.py                # Auto-découverte du doc_id GraphQL (Selenium + CDP)
AGENTS.md                   # Documentation technique (context memory)
discover_groups.sh            # Script de decouverte mensuelle (Google search)
facebook-discover-groups.service  # Service systemd pour la decouverte mensuelle
facebook-discover-groups.timer    # Timer systemd : 1er lundi du mois a 00h
facebook-media-ocr.service   # Unite systemd pour le service hebdomadaire
facebook-media-ocr.timer     # Timer systemd (tous les lundis 2h)
facebook-media-ocr.logrotate # Rotation des logs (logrotate)
download-{name}/            # Images telechargees (selon --name)
emails-{name}.csv           # Resultats OCR + raw_text (selon --name)
enriched-{name}.csv         # Enrichissement IA (selon --name)
state-{name}.json           # Reprise (selon --name)
logs-{name}.txt             # Logs individuels (mode service)
facebook-media-ocr.logrotate # Rotation des logs (logrotate)
```

## SMTP Email Verification (`smtp_verify.py`)

Vérifie si une adresse email existe réellement en se connectant au serveur SMTP
et en simulant un envoi (RCPT TO) sans jamais envoyer d'email (QUIT avant DATA).

**Zéro dépendance externe** — utilise uniquement la librairie standard Python.

### Principe

```
Vous → HELO/EHLO → STARTTLS → MAIL FROM → RCPT TO → QUIT (coupe avant DATA)
```

Le serveur SMTP répond `250` si l'adresse existe, `550` si elle n'existe pas.

### Usage

```bash
# Simple
python3 smtp_verify.py user@example.com mx.example.com

# Batch CSV (besoin colonnes email + mx_host)
python3 smtp_verify.py --batch emails.csv

# Depuis stdin
echo "user@example.com,mx.example.com" | python3 smtp_verify.py --stdin

# Sortie JSON
python3 smtp_verify.py --batch emails.csv --output json
```

### Options

| Option | Défaut | Description |
|--------|--------|-------------|
| `--batch` | — | Fichier CSV avec colonnes email et mx_host |
| `--email-col` | `email` | Nom de la colonne email dans le CSV |
| `--mx-col` | `mx_host` | Nom de la colonne mx_host dans le CSV |
| `--stdin` | — | Lire les paires depuis stdin (une par ligne: `email,mx_host`) |
| `--timeout` | `10` | Timeout de connexion SMTP en secondes |
| `--no-retry` | — | Ne pas réessayer en cas de greylisting (code 4xx) |
| `--source-addr` | `verify@example.com` | Adresse expéditeur pour MAIL FROM |
| `--hello` | hostname local | Hostname envoyé dans HELO/EHLO |
| `--output` | `text` | Format de sortie : `text`, `csv`, ou `json` |
| `--output-file` | stdout | Fichier de sortie |

### Rapport automatique

En mode batch (CSV ou stdin), un **rapport Markdown** est automatiquement
généré avec le même nom que le fichier d'entrée suffixé de `-rapport.md` :

```bash
python3 smtp_verify.py --batch emails.csv
# Produit : emails-rapport.md
```

Le rapport contient :

| Section | Contenu |
|---------|---------|
| Résumé | Stats : valides, rejetés, incertains |
| ❌ Rejetés | Toutes les adresses refusées par le serveur |
| ✅ Valides | Toutes les adresses acceptées |
| ❓ Incertains | Catch-all, timeout, greylisting... |
| Tableau complet | Toutes les adresses avec leur statut |

### Retours possibles

| `exists` | Signification |
|----------|---------------|
| `True` | Serveur SMTP a accepté (RCPT 250) — l'adresse existe probablement |
| `False` | Serveur SMTP a refusé (RCPT 550) — l'adresse n'existe pas |
| `None` | Incertain : catch-all détecté, timeout, greylist, ou erreur réseau |

### Détection catch-all

Les providers connus pour accepter toutes les adresses (Gmail, Outlook, iCloud)
sont automatiquement détectés par leur serveur MX et retournent `exists=None`
sans consommer de connexion SMTP (évite faux positifs).

### Format du CSV d'entrée

```csv
email,mx_host
contact@example.com,mx1.example.com
john@company.com,mail.company.com
jane@gmail.com,gmail-smtp-in.l.google.com
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
sudo apt install -y chromium-chromedriver tesseract-ocr tesseract-ocr-fra pwsh git screen tor

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
tor --version                                              # Tor (SOCKS proxy)
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
./run_all.sh                 # Mode screen, 2 groupes max en parallèle
./run_all.sh --systemd       # Mode service (arrière-plan direct)
./run_all.sh --parallel=4    # 4 groupes au lieu de 2
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

### 5b. Découverte mensuelle des nouveaux groupes (systemd timer)

Un timer séparé lance `discover_groups.py --search-only` le **1er lundi de chaque mois à 00h00**,
juste avant le scrape hebdomadaire (02h00), pour découvrir automatiquement les nouveaux
groupes Facebook sans intervention manuelle.

Fichiers fournis dans le repo :

```
discover_groups.sh            # Script de decouverte (Google search)
facebook-discover-groups.service  # Service oneshot
facebook-discover-groups.timer    # Timer : 1er lundi du mois a 00h
```

**Installation :**

```bash
# Copier les fichiers systemd
sudo cp facebook-discover-groups.service facebook-discover-groups.timer /etc/systemd/system/

# Activer le timer
sudo systemctl daemon-reload
sudo systemctl enable facebook-discover-groups.timer
sudo systemctl start facebook-discover-groups.timer

# Vérifier
sudo systemctl status facebook-discover-groups.timer
systemctl list-timers --all | grep discover
```

**Chronologie un 1er lundi du mois :**

```
00:00 → discover_groups.sh (recherche Google des nouveaux groupes)
02:00 → run_all.sh (scraping photos des groupes)
```

Les résultats de `discover_groups.sh` sont dans `results/discover-groups-YYYYMMDD-HHMMSS.txt`.

**Désactiver :**

```bash
sudo systemctl stop facebook-discover-groups.timer
sudo systemctl disable facebook-discover-groups.timer
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

Les requêtes GraphQL passent par **Tor SOCKS** (`socks5://127.0.0.1:9050`)
pour éviter le rate limiting IP de Facebook. Un délai de **1s entre chaque page**
GraphQL est appliqué. Le téléchargement des images se fait avec **0.5s entre
chaque photo**.

#### Parallélisme

Par défaut, `run_all.sh` lance **2 groupes à la fois** (`--parallel=2`).
Surchargeable avec `--parallel=N`. Le parallélisme est fixe car le mode
live n'utilise pas Selenium (pas de Chrome headless lourd).

#### Problème LD_PRELOAD / torsocks

Si torsocks est actif globalement (`LD_PRELOAD`), il bloque les connexions
SOCKS locales (`127.0.0.1:9050`) et PowerShell. `run_all.sh` le désactive
automatiquement via `unset LD_PRELOAD`.

#### Doc_id Facebook

Le `doc_id` de l'API GraphQL Facebook change périodiquement. `fb_doc_id.py`
le détecte automatiquement via Selenium + CDP et met à jour tous les
fichiers. Lancé automatiquement au début de `run_all.sh`.

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

### 10. Supervisor (reprise manuelle après coupure)

Le `supervisor.py` est un outil de **secours manuel** pour les interruptions en milieu de semaine
(reboot VPS, coupure système, `Ctrl+C` accidentel). Il ne remplace pas le timer systemd hebdomadaire.

#### Différence avec le timer systemd

| | `run_all.sh --systemd` (systemd) | `supervisor.py` (manuel) |
|---|---|---|
| **Déclencheur** | Timer systemd (lundi 02:00) | Lancement manuel (`./supervisor.py`) |
| **Groupes traités** | **Tous** les 295 groupes de `groups.txt` | **Uniquement** les groupes non finis |
| **Notification** | `Groupe X/295` | `Groupe X/75` (ou moins selon les restants) |
| **Parallélisme** | 2 groupes en simultané | 1 groupe à la fois (séquentiel) |
| **Répétition** | Cycle hebdomadaire automatique | Reprise après coupure uniquement |

Les deux outils sont **indépendants** : le lundi, le timer systemd reprend tout depuis le début
avec `run_all.sh --systemd`. Les groupes déjà terminés s'arrêtent en ~2 secondes
(grâce aux `state-*.json`).

#### Utilisation

```bash
# Reprendre les groupes non finis (séquentiel)
./supervisor.py

# Avec parallélisme
./supervisor.py --parallel 2
```

#### Fonctionnement

1. Analyse `groups.txt` et identifie les groupes qui ont encore du travail
2. Lance `fb_selenium.py` pour chaque groupe (qui reprend via son `state-*.json`)
3. Sauvegarde l'avancement dans `results/supervisor-state.json`
4. Si le superviseur est coupé (reboot, `Ctrl+C`), il reprend exactement là où il s'est arrêté
5. Chaque groupe est réessayé jusqu'à **5 fois** en cas d'échec, puis abandonné
6. Une fois tous les groupes terminés → `git push` automatique

#### Exemple de cycle

```
═══════════════════════════════════════════════════════════════
  Lundi 02:00 → systemd lance run_all.sh --systemd
  ├─ Traite 295 groupes avec --group-pos "X/295"
  └─ Coupure au groupe 227/288

  Intervention manuelle → ./supervisor.py
  ├─ Détecte 75 groupes non finis
  ├─ [1/75] group214288905360691 → (tentative 1/5)
  ├─ [2/75] contrat.alternance    → (tentative 1/5)
  │  ... (notification "Groupe X/75")
  └─ ✅ Tous les groupes terminés ! Git push.

  Lundi suivant 02:00 → systemd relance run_all.sh
  ├─ 295 groupes → les finis passent en ~2s
  └─ Notification "Groupe X/295"
═══════════════════════════════════════════════════════════════
```

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
