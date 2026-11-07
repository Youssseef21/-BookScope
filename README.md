# BookScope

Plateforme de découverte de livres pour le projet Big Data and NoSQL (M2 SLCD, Université Côte d'Azur). Elle utilise le jeu [Goodbooks-10k](https://github.com/zygmuntz/goodbooks-10k) et MongoDB.

Les notes de `ratings.csv` se relient aux livres par `book_id` (1 à 10 000). Les tags se relient par `goodreads_book_id`. Le fichier n'a pas de dates : l'ordre des lignes est l'ordre dans le temps, et la position d'une note s'appelle `source_row`.

## Sélection

- Un livre est un polar si l'un de ces tags a un compteur d'au moins 50 : `mystery`, `mysteries`, `thriller`, `thrillers`, `crime`, `crime-fiction`, `detective`, `detectives`, `suspense`, `murder-mystery`, `murder`, `mystery-thriller`, `mystery-suspense`, `crime-thriller`, `cozy-mystery`.
- Un lecteur est gardé si `user_id % 5` vaut 1 ou 2 et s'il a noté au moins 8 polars. On garde toutes ses notes.
- Résultat : 20 586 lecteurs et 2 336 014 notes.

Pour chaque lecteur, les 5 notes les plus récentes sont cachées dans `data/selection/e3_eval.csv` (évaluation), les 5 d'avant sont dans `data/selection/f6_batch.csv` (import F6), le reste (2 130 154 notes) va dans la base `bookscope_app`. Les chiffres sont dans `data/selection/summary.json`.

## Lancer le projet

1. MongoDB 7 sur `localhost:27017`, avec Docker :

```text
docker run -d --name bookscope-mongo -p 27017:27017 -v bookscope-mongo-data:/data/db mongo:7
```

Si ce port est déjà pris, utilisez un autre port et dites-le aux scripts (ils lisent `MONGO_URI`) :

```text
docker run -d --name bookscope-mongo-bdd -p 27117:27017 -v bookscope-mongo-bdd-data:/data/db mongo:7
$env:MONGO_URI = "mongodb://localhost:27117"
```

2. Python :

```text
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

3. Télécharger [goodbooks-10k.zip](https://github.com/zygmuntz/goodbooks-10k/releases/download/v1.0/goodbooks-10k.zip) et mettre `books.csv`, `ratings.csv`, `to_read.csv`, `book_tags.csv` et `tags.csv` dans `data/raw`.

4. Charger les données :

```text
.venv\Scripts\python.exe scripts\load_raw.py
.venv\Scripts\python.exe scripts\prepare_selection.py
```

`load_raw.py` remplit la base complète `bookscope`. `prepare_selection.py` écrit les fichiers de `data/selection` et remplit `bookscope_app`.

## Utiliser les services

Page web (http://127.0.0.1:8000) :

```text
.venv\Scripts\python.exe scripts\serve.py
```

Ou en ligne de commande :

| Service | Commande | Rôle |
|---|---|---|
| F1 | `scripts\explore_book.py 258` ou `"Selected Poems"` | livre et livres liés ; un titre ambigu est affiché sans choix |
| F2 | `scripts\compare_readers.py 1 2` | compare deux lecteurs ; moins de 5 livres communs : pas de score |
| F3 | `scripts\recommend.py 1` (option `--first 5`) | recommande des livres non notés, loin des plus populaires |
| F4 | `scripts\trace_books.py 258 1467` | lien direct ou chemin par un livre pont |
| F5 | `scripts\link_interests.py mystery historical-fiction` | relie deux thèmes, avec et sans les cas extrêmes |
| F6 | `scripts\import_batch.py` | importe `f6_batch.csv` ; un second import ajoute 0 note |

Chaque résultat cite ses preuves (lecteurs, notes, `source_row`). Entrées, sorties et exemples : `docs/SERVICES.md`.

F6 : une note est identifiée par le couple (lecteur, livre). Si le couple existe avec une autre note, l'ancienne reste et la nouvelle est rangée dans `rating_conflicts`.

## Deux façons de ranger les notes

`scripts\measure_model.py` compare une note par document (choix retenu) et un document par lecteur. Historique du lecteur 1 : 1,45 ms contre 0,59 ms. Notes d'au moins 4/5 du livre 258 : 5,5 ms contre 56 ms. Détail : `data/measurements/model_tradeoff.json`.

## Deux instances

Deux processus MongoDB sur cet ordinateur (pas un cluster de machines) : A sur le port 27018, B sur 27019. Les livres sont copiés des deux côtés, chaque note n'est que d'un côté.

```text
.venv\Scripts\python.exe scripts\load_distributed.py
.venv\Scripts\python.exe scripts\compare_distributed.py by_reader
.venv\Scripts\python.exe scripts\compare_distributed.py by_book
```

- `by_reader` : `user_id` pair sur A, impair sur B. `by_book` : même règle avec `book_id`.
- F1 à F5 donnent le même résultat que sur le serveur unique. Un second import F6 ajoute 0 note.
- Si B est arrêtée (`docker stop bookscope-b`, puis `compare_distributed.py by_book --down`), F2 et F3 répondent « instance indisponible » au lieu d'une réponse partielle. `docker start bookscope-b` la relance.

## Expériences

| | Script | Résultat |
|---|---|---|
| E1 | `measure_e1.py` | trois tailles, tableau ci-dessous |
| E2 | `prepare_e2_data.py`, puis `measure_e2.py --size 4 --repeats 3` (environ 40 min) | serveur unique et deux répartitions au même niveau : de 11 à 44 s pour 4 requêtes ; la prédiction `e2_prediction.txt` n'est pas confirmée |
| E3 | `evaluate_e3.py --readers 1500 --k 5` | succès@10 de F3 : 9,8 % (historique complet) contre 4,9 % (popularité) ; 3,1 % contre 3,1 % avec 5 notes |
| Vérification | `verify_independent.py --compare` | F2, F4, F5 recalculés depuis les CSV : 8 valeurs identiques |
| Qualité | `data_quality.py` | dix problèmes avec effectifs et identifiants |

| Notes | Lecteurs | Chargement | Disque | F2 | F1 |
|---|---:|---:|---:|---:|---:|
| 200 000 | 3 374 | 5,4 s | 13 Mo | 18 ms | 2,8 s |
| 1 000 000 | 12 586 | 16,2 s | 40 Mo | 41 ms | 5,9 s |
| 5 976 479 | 53 424 | 86,3 s | 162 Mo | 134 ms | 27,7 s |

Toutes les mesures sont dans `data/measurements/` (`e1.json`, `e2.json`, `e3.json`, `distributed.json`, `verification.json`, `data_quality.json`). `e1_machine_binome.json` garde un E1 mesuré sur une autre machine. Les mesures de ce dépôt ont été faites avec `MONGO_URI=mongodb://localhost:27117`.

## Fichiers

- `bookscope/` : `f1.py` à `f6.py` (services), `cluster.py` (accès aux deux instances), `tags.py`, `db.py`, `web.py` (interface).
- `scripts/` : commandes des services, chargement (`load_raw.py`, `prepare_selection.py`, `selection_rule.py`), partie distribuée (`load_distributed.py`, `compare_distributed.py`), expériences (`measure_*.py`, `evaluate_e3.py`, `prepare_e2_data.py`), contrôles (`verify_independent.py`, `data_quality.py`), `serve.py`.
- `data/selection/` : lecteurs retenus, livres polars, lots E3 et F6. `data/measurements/` : mesures brutes.
- `docs/` : documentation des services et exemples réels.
