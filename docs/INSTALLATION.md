# Livraison SMA — guide d'installation

Plateforme de livraison urbaine à allocation par appel d'offres (Contract Net), en Python.
Ce guide installe l'environnement de développement de A à Z sur **Windows 11**, **Ubuntu** ou **Debian**.

À la fin, vous aurez : Python et ses bibliothèques (SPADE, FastAPI, Flet…), PostgreSQL/PostGIS et Redis dans Docker, un serveur XMPP pour les agents, et en option le moteur d'itinéraires avec la carte de la Côte d'Ivoire.

## Avancement : version 0.3

| Partie | Contenu | Où |
|---|---|---|
| Système multi-agents | 8 agents SPADE : Annuaire (pages jaunes), Dispatcher (Contract Net en `FSMBehaviour`), Livreur (un par livreur), Commande (un par commande), Paiement, Notification, Superviseur, Passerelle | `src/livraison/agents/` |
| Protocole | Présélection par rayon, CFP, PROPOSE/REFUSE, ACCEPT/REJECT, confirmation humaine (30 s) avec offre suivante, file d'attente avec rayon élargi, priorité aux urgentes, réattribution en cas de panne ou de perte du GPS, reprise après redémarrage (F-30 à F-36) | idem |
| Serveur | API FastAPI : connexion par code SMS (simulé), jetons JWT, 4 rôles, commandes, livreurs, supervision, WebSocket | `src/livraison/api/` |
| Données | PostgreSQL + PostGIS (requêtes spatiales), journal de tous les messages entre agents | `src/livraison/donnees/` |
| Interfaces | **Console du répartiteur** (carte, commandes, journal, **Sniffer : diagramme de séquence du Contract Net en direct**), **application du livreur** (proposition avec compte à rebours, course sur la carte, remise par code, incidents, gains), **espace client** (commande en touchant la carte, prix garanti, suivi en direct, facture, évaluation) | `src/livraison/interfaces/` |
| Robustesse | Démonstration en direct de 5 scénarios : refus, silence, panne, file d'attente, téléphone éteint | `src/livraison/simulation/robustesse.py` |
| Facturation | Facture PDF numérotée (F-53), commission et relevé des gains, reversement des espèces (F-54), évaluation du livreur (F-15) | `src/livraison/services/facturation.py` |
| Simulation | Livreurs et clients simulés à Abidjan, par l'API (démonstration, test de charge) | `src/livraison/simulation/` |
| Tests | 45 tests, dont la non-régression du TP et 16 scénarios de la recette | `tests/` |
| Conception | Dossier de conception avec diagrammes (séquence, états, données, cas d'utilisation) | `docs/CONCEPTION.md` |

### Démarrage rapide (une fois l'installation ci-dessous terminée)

```bash
docker compose up -d                                   # PostgreSQL + Redis
python -m livraison.lancer --demo 15                   # XMPP intégré + API + 15 livreurs simulés
python -m livraison.interfaces.console --web           # 2e terminal : console du répartiteur
```

La console s'ouvre sur `http://127.0.0.1:8550` : connectez-vous avec le numéro du répartiteur (`0700000001`) ; en mode développement, le code « SMS » est rempli automatiquement. L'API est documentée sur `http://127.0.0.1:8000/docs`.

Sans Docker, pour une démonstration rapide : `python -m livraison.lancer --memoire --demo 15`.

Applications par profil (chacune dans son terminal, le serveur doit tourner) :

```bash
python -m livraison.interfaces.console --web   # répartiteur : http://127.0.0.1:8550
python -m livraison.interfaces.client --web    # client      : http://127.0.0.1:8551
python -m livraison.interfaces.livreur --web   # livreur     : http://127.0.0.1:8552
```

### Démonstration de robustesse (le Dispatcher face aux aléas)

```bash
python -m livraison.lancer --memoire --rapide              # délais courts : 15 s, 8 s, 20 s
python -m livraison.interfaces.console --web                # onglet « Sniffer »
python -m livraison.simulation.robustesse --rapide          # 5 scénarios commentés (--scenario 3 pour un seul)
```

1. un livreur décline : le Dispatcher retient l'offre suivante ;
2. un livreur ne répond pas : délai dépassé, offre suivante ;
3. panne après l'enlèvement : FAILURE, nouvel appel d'offres sans ce livreur ;
4. aucun livreur : file d'attente, puis attribution dès qu'un livreur se connecte ;
5. téléphone éteint en course : alerte GPS, puis réattribution.

## Sommaire

1. Prérequis
2. Installer les outils de base (Git, Python, Docker)
3. Récupérer le projet
4. Environnement Python
5. Configuration
6. Démarrer les services Docker
7. Moteur d'itinéraires (optionnel au début)
8. Serveur XMPP et test des agents
9. Vérification complète
10. Lancer la plateforme
11. Application Android (plus tard)
12. Utilisation quotidienne
13. Dépannage

## 1. Prérequis

| Élément | Minimum |
|---|---|
| Mémoire | 8 Go (16 Go conseillés avec le moteur d'itinéraires) |
| Disque libre | 10 Go |
| Système | Windows 11, Ubuntu 22.04 ou plus, Debian 12 ou plus |
| Droits | Compte administrateur (Windows) ou `sudo` (Linux) |
| Réseau | Connexion Internet pour les téléchargements |

Versions utilisées pour valider ce squelette : Python 3.13, SPADE 4.1, Flet 1.0, FastAPI 0.142. Python 3.11 ou plus convient.

## 2. Installer les outils de base

### Windows 11

Ouvrir **PowerShell en tant qu'administrateur**.

```powershell
# WSL 2, nécessaire à Docker Desktop (redémarrer l'ordinateur ensuite)
wsl --install

# Git, Python et Docker Desktop
winget install --id Git.Git -e
winget install --id Python.Python.3.13 -e
winget install --id Docker.DockerDesktop -e
```

Après le redémarrage : lancer **Docker Desktop** une première fois et attendre l'indication « Engine running ». Puis, dans un **nouveau** PowerShell (non administrateur), autoriser l'activation des environnements Python :

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### Ubuntu

```bash
sudo apt update
sudo apt install -y git curl ca-certificates python3 python3-venv python3-pip

# Docker (dépôt officiel)
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Utiliser Docker sans sudo (se déconnecter puis se reconnecter ensuite)
sudo usermod -aG docker $USER
```

Sur Ubuntu 22.04, Python est en version 3.10, trop ancienne. Installer une version plus récente, puis utiliser `python3.12` à la place de `python3` à l'étape 4 :

```bash
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt install -y python3.12 python3.12-venv
```

### Debian

Identique à Ubuntu, avec `debian` à la place de `ubuntu` dans les deux adresses du dépôt Docker :

```bash
sudo apt update
sudo apt install -y git curl ca-certificates python3 python3-venv python3-pip

sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

sudo usermod -aG docker $USER
```

Si `sudo` n'est pas installé sur Debian : `su -`, puis `apt install sudo && usermod -aG sudo votre_nom`, et se reconnecter.

### Contrôle (les trois systèmes)

```bash
git --version
python --version        # python3 --version sous Linux
docker --version
docker compose version
docker run --rm hello-world
```

## 3. Récupérer le projet

Décompresser l'archive `livraison-sma.zip` à l'emplacement de votre choix, sans espace ni accent dans le chemin (par exemple `C:\projets\livraison-sma` ou `~/projets/livraison-sma`), puis initialiser le dépôt Git :

```bash
cd livraison-sma
git init
git add .
git commit -m "Squelette initial"
```

Toutes les commandes qui suivent se lancent **depuis ce dossier**.

## 4. Environnement Python

### Windows 11

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
pip install -e .
```

### Ubuntu et Debian

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
pip install -e .
```

L'invite de commande commence alors par `(.venv)`. Il faut **réactiver l'environnement à chaque nouveau terminal** (ligne `Activate.ps1` ou `source`).

Premier contrôle, qui ne demande aucun service :

```bash
pytest
```

Résultat attendu : `39 passed` en une minute environ. Les tests multi-agents démarrent eux-mêmes un serveur XMPP de test (port 5242). Les deux tests PostgreSQL sont ignorés (`skipped`) tant que la base n'est pas lancée (étape 6) ; ils utilisent une base `livraison_test` à créer une fois :

```bash
docker compose exec postgres createdb -U livraison livraison_test
```

Les tests rejouent notamment le scénario des six colis du TP JADE, cette fois avec les vrais agents SPADE.

## 5. Configuration

```powershell
# Windows
Copy-Item .env.example .env
```

```bash
# Linux
cp .env.example .env
```

Ouvrir `.env` et remplacer les trois valeurs `changez-moi` (mot de passe PostgreSQL, mot de passe des agents, secret JWT). Le fichier `.env` n'est jamais versionné ; chaque membre de l'équipe a le sien.

## 6. Démarrer les services Docker

```bash
docker compose up -d
docker compose ps
```

Les services `postgres` et `redis` doivent afficher l'état `healthy` après quelques secondes.

| Service | Adresse | Rôle |
|---|---|---|
| PostgreSQL + PostGIS | `localhost:5432` | Comptes, commandes, paiements, données géographiques |
| Redis | `localhost:6379` | Dernières positions GPS, sessions |

Les ports ne sont ouverts que sur la machine locale. Le mot de passe PostgreSQL est fixé à la création de la base : ne modifiez plus `POSTGRES_PASSWORD` dans `.env` ensuite (voir Dépannage). Les données sont conservées dans un volume Docker : `docker compose stop` et `docker compose up -d` ne les effacent jamais.

## 7. Moteur d'itinéraires (optionnel au début)

Cette étape télécharge la carte OpenStreetMap de la Côte d'Ivoire (fournie par Geofabrik) et prépare les données d'OSRM. Elle prend plusieurs minutes et n'est nécessaire qu'à partir du lot 2 ; vous pouvez y revenir plus tard.

```powershell
# Windows
.\scripts\preparer_osrm.ps1
```

```bash
# Linux
bash scripts/preparer_osrm.sh
```

Puis démarrer le service :

```bash
docker compose --profile itineraires up -d
```

Test : l'adresse suivante doit renvoyer un itinéraire Plateau → Cocody au format JSON.

```
http://localhost:5000/route/v1/driving/-4.0197,5.3236;-3.9870,5.3480?overview=false
```

Notes :

- OSRM attend les coordonnées dans l'ordre **longitude, latitude**.
- Le profil préparé est celui de la voiture. Les profils moto et vélo seront ajoutés au lot 2.
- Pour le géocodage (adresse → coordonnées), le service public Nominatim suffit en développement, à raison d'une requête par seconde au maximum. Un serveur dédié sera à prévoir pour la production.

## 8. Serveur XMPP et test des agents

Le projet fournit le serveur XMPP de développement de SPADE (pyjabber), réglé pour Windows. Le lancer dans un **terminal dédié** (environnement activé) et le laisser ouvert :

```bash
python -m livraison.serveur_xmpp
```

> **Windows** : ne pas utiliser `spade run`. Il choisit la boucle `winloop`, avec laquelle les agents restent bloqués à la connexion puis sont déconnectés (`DisconnectedException`). `python -m livraison.serveur_xmpp` et tous les programmes du projet imposent la boucle asyncio standard sous Windows (voir `src/livraison/boucle.py`). Sous Linux et macOS, `spade run` fonctionne aussi.

Dans un **second terminal** (environnement activé) :

```bash
python -m livraison.agents.ping_pong
```

Résultat attendu :

```
ping -> ping #1
pong a reçu « ping #1 » de ping
ping <- « pong #1 »
...
OK : les agents communiquent.
```

C'est l'équivalent SPADE des agents Ping et Pong du TP : `PeriodicBehaviour` remplace `TickerBehaviour`, la métadonnée `performative` remplace la performative ACL, et `Template` remplace `MessageTemplate`.

Les comptes des agents sont créés automatiquement à chaque lancement, dans une base temporaire vidée au démarrage. Pour conserver la base : `python -m livraison.serveur_xmpp --db serveur.db`.

## 9. Vérification complète

Avec Docker démarré et `python -m livraison.serveur_xmpp` ouvert :

```bash
python scripts/verifier_env.py
```

Résultat attendu :

```
[OK] Python >= 3.11 (3.13.x)
[OK] Bibliothèque spade (4.1.x)
...
[OK] PostgreSQL + PostGIS (PostGIS 3.4)
[OK] Redis
[OK] Serveur XMPP (localhost:5222)
[--] Moteur d'itinéraires (optionnel) ...

Environnement prêt.
```

Une ligne `[KO]` indique ce qui reste à corriger. La ligne `[--]` du moteur d'itinéraires est normale tant que l'étape 7 n'a pas été faite.

## 10. Lancer la plateforme

**En une commande** (serveur XMPP intégré, API et agents dans le même processus) :

```bash
python -m livraison.lancer                    # avec PostgreSQL (docker compose up -d)
python -m livraison.lancer --demo 15          # + 15 livreurs et des clients simulés
python -m livraison.lancer --memoire          # sans base de données
```

**Ou en trois terminaux**, comme en production :

```bash
python -m livraison.serveur_xmpp              # terminal 1 : serveur XMPP
python -m livraison.lancer --sans-xmpp        # terminal 2 : API + système multi-agents
python -m livraison.simulation.simulateur --livreurs 15 --commandes 40   # terminal 3 (facultatif)
```

Ouvrir `http://127.0.0.1:8000/sante`, puis `http://127.0.0.1:8000/docs` (documentation interactive de toutes les routes).

**Console du répartiteur** :

```bash
python -m livraison.interfaces.console --web  # navigateur : http://127.0.0.1:8550
python -m livraison.interfaces.console        # fenêtre de bureau
```

**Test de charge de la recette** (100 livreurs, 200 commandes simultanées) :

```bash
python -m livraison.simulation.simulateur --livreurs 100 --commandes 200 --rafale --acceleration 60
```

Résultat obtenu lors de la mise au point : 200 commandes sur 200 livrées, décision du système multi-agents en 0,11 s en moyenne (maximum 1,9 s ; exigence NF-01 : moins de 5 s).

Comptes créés au démarrage : administrateur `0700000000`, répartiteur `0700000001` (modifiables dans `.env`). Un livreur s'inscrit avec son numéro (`POST /livreurs/inscription`), puis l'administrateur le valide (`POST /admin/livreurs/{id}/valider`).

## 11. Application Android (plus tard)

La construction de l'application du livreur (`flet build apk`) demande en plus le SDK Flutter, un JDK et le SDK Android. C'est une installation lourde, à faire au lot 3 sur un seul poste. D'ici là, l'application se teste sur un vrai téléphone avec l'application **Flet** du Play Store : lancer `flet run --android` et scanner le code QR affiché (téléphone et ordinateur sur le même réseau Wi-Fi).

## 12. Utilisation quotidienne

| Action | Commande |
|---|---|
| Activer l'environnement (Windows) | `.\.venv\Scripts\Activate.ps1` |
| Activer l'environnement (Linux) | `source .venv/bin/activate` |
| Démarrer les services | `docker compose up -d` |
| Arrêter les services | `docker compose stop` |
| Voir les journaux d'un service | `docker compose logs -f postgres` |
| Serveur XMPP | `python -m livraison.serveur_xmpp` |
| API | `uvicorn livraison.api.main:app --reload` |
| Tout en une commande | `python -m livraison.lancer` |
| Console du répartiteur | `python -m livraison.interfaces.console --web` |
| Simulateur | `python -m livraison.simulation.simulateur --livreurs 15` |
| Tests | `pytest` |
| Qualité du code | `ruff check .` puis `ruff format .` |
| Console SQL | `docker compose exec postgres psql -U livraison` |

### Arborescence

```
livraison-sma/
├── docker-compose.yml        services PostgreSQL/PostGIS, Redis, OSRM
├── .env.example              modèle de configuration
├── requirements*.txt, pyproject.toml
├── docs/
│   ├── CONCEPTION.md         dossier de conception
│   ├── diagrammes/           sources Mermaid (.mmd) et images
│   └── captures/             captures de la console
├── scripts/                  verifier_env.py, preparer_osrm.*
├── src/livraison/
│   ├── config.py             lecture du fichier .env
│   ├── lancer.py             lancement en une commande
│   ├── agents/               protocole.py, base.py, annuaire.py, dispatcher.py, livreur.py,
│   │                         commande.py, auxiliaires.py, passerelle.py, systeme.py, ping_pong.py
│   ├── modele/               cout.py, etats.py, geo.py, entites.py (règles métier)
│   ├── services/             itineraires.py, tarification.py, evenements.py
│   ├── donnees/              depot.py (mémoire), sql.py (PostgreSQL + PostGIS)
│   ├── api/                  main.py (FastAPI), securite.py (codes, jetons, rôles)
│   ├── interfaces/           console.py (répartiteur), demo.py
│   └── simulation/           virtuel.py (tests), simulateur.py (par l'API)
└── tests/                    45 tests (non-régression TP, recette, API, PostGIS, modèle)
```

## 13. Dépannage

| Symptôme | Cause probable et solution |
|---|---|
| `Activate.ps1 cannot be loaded` (Windows) | Lancer `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. |
| `python` ouvre le Microsoft Store (Windows) | Utiliser `py`, ou désactiver les « alias d'exécution d'application » pour Python dans les paramètres Windows. |
| `permission denied` sur `docker` (Linux) | Se déconnecter puis se reconnecter après `usermod -aG docker $USER`. |
| `port is already allocated` sur 5432, ou port refusé sous Windows | Un PostgreSQL local occupe le port, ou Windows le réserve : mettre `POSTGRES_PORT=15432` dans `.env`, puis `docker compose up -d`. |
| `password authentication failed` | `POSTGRES_PASSWORD` a été modifié dans `.env` après la création de la base : remettre l'ancien mot de passe dans `.env`, sans toucher aux volumes Docker. |
| `ModuleNotFoundError: livraison` | Environnement non activé, ou `pip install -e .` non exécuté. |
| Ping/Pong : « aucune réponse » | `python -m livraison.serveur_xmpp` n'est pas lancé, ou le port 5222 est bloqué par un pare-feu. |
| `Error during the connection with the server` (`DisconnectedException`) sous Windows | Le serveur a été lancé avec `spade run` (boucle winloop) : l'arrêter et lancer `python -m livraison.serveur_xmpp`. |
| Docker Desktop ne démarre pas (Windows) | Vérifier que la virtualisation est activée dans le BIOS et relancer `wsl --update`. |
| `Could not authenticate the agent` au démarrage de l'API | Le mot de passe des agents a changé alors que la base du serveur XMPP est conservée (`--db`) : relancer avec `--purge`. |
| `Address already in use` (ou erreur 10048 sous Windows) au démarrage du serveur XMPP intégré | Lancer `python -m livraison.serveur_xmpp` dans un terminal à part, puis `python -m livraison.lancer --sans-xmpp`. |
| `Le port 5222 est occupé` avec `python -m livraison.lancer` | Un serveur XMPP tourne déjà : ajouter `--sans-xmpp`, ou l'arrêter. |
| Carte grise dans la console | Le fond de carte OpenStreetMap demande une connexion Internet ; les livreurs et commandes s'affichent quand même. |
| `PostgreSQL injoignable` au démarrage de l'API | `docker compose up -d`, ou `DEPOT=memoire` dans `.env` pour une démonstration sans base. |
| OSRM s'arrête pendant la préparation | Mémoire insuffisante : augmenter la mémoire allouée à Docker (Docker Desktop → Settings → Resources). |
