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

## Base principale et partie distribuée

La base principale est `bookscope_app`, sur le port 27017. Elle contient les 2 233 084 notes. La page http://127.0.0.1:8000 l'interroge (`scripts\serve.py`).

La partie distribuée est un cluster fragmenté décrit dans `compose.sharded.yml`. L'application ne parle qu'à une adresse, mongos sur le port 27200. mongos lit le serveur de configuration (port 27100), envoie la requête au fragment qui possède les notes, puis réunit les réponses. Deux fragments, chacun avec son disque :

- shard1, port 27101, environ la moitié des notes, plus le catalogue ;
- shard2, port 27102, le reste des notes.

Deux bases utilisent ces fragments. `dist_reader` range toutes les notes d'un lecteur sur un seul fragment, par un hash du numéro de lecteur. `dist_book` range toutes les notes d'un livre sur un seul fragment, par un hash du numéro du livre. Les 10 000 livres et les 357 387 entrées « à lire » restent sur shard1. Ce montage tourne sur un seul ordinateur : ce n'est pas un groupe de machines.

Sur les 2 233 084 notes, le hash donne :

- par lecteur : 1 096 986 sur shard1, 1 136 098 sur shard2. Les lecteurs 1 (112 notes) et 2 (60 notes) sont entiers sur shard1. Le livre 258 est coupé, 664 et 652 ;
- par livre : 1 172 371 sur shard1, 1 060 713 sur shard2. Le livre 258 (1 316 notes) est entier sur shard2, le livre 1467 (291 notes) est entier sur shard1. Le lecteur 1 est coupé, 59 et 53, le lecteur 2 aussi, 32 et 28.

```text
.venv\Scripts\python.exe scripts\start_sharded.py
.venv\Scripts\python.exe scripts\load_sharded.py
.venv\Scripts\python.exe scripts\serve_distributed.py
```

La page http://127.0.0.1:8001 a les mêmes formulaires. **Par lecteur** ouvre `dist_reader`, **Par livre** ouvre `dist_book`.

La comparaison avec la base principale est automatique :

```text
.venv\Scripts\python.exe scripts\compare_distributed.py by_reader
.venv\Scripts\python.exe scripts\compare_distributed.py by_book
```

Pour un fragment arrêté, avec le placement par livre (les notes d'un lecteur sont alors sur les deux fragments) :

```text
docker stop bookscope-shard2
.venv\Scripts\python.exe scripts\compare_distributed.py by_book --down
docker start bookscope-shard2
```

F2 puis F3 répondent « instance indisponible » et ne donnent pas de score. La page affiche « Réponse refusée : elle serait incomplète. » Le titre du livre 258 s'affiche encore, parce que le catalogue est sur shard1. En placement par livre, les 1 316 notes de ce livre sont sur shard2, donc l'exploration complète est refusée. Une copie de production pourrait relire les mêmes notes sur un second disque du fragment. Ici, les notes de shard2 n'existent pas ailleurs.

Les scores F1 à F5 sont les mêmes que sur la base principale. Le hash change l'endroit des notes, pas le résultat.

`scripts\compare_random_half.py` est un contrôle à part, sur le serveur principal. Il recopie environ la moitié des notes dans `bookscope_half` et laisse `bookscope_app` intacte.

## Expériences

| | Script | Résultat |
|---|---|---|
| E1 | `measure_e1.py` | trois tailles, tableau ci-dessous |
| E2 | `measure_e2.py --size 4 --repeats 3` (environ 12 min) | sur les 2 233 084 notes : serveur unique 2,1 s / 8,8 s / 23,3 s ; par lecteur 5,9 / 12,4 / 28,2 ; par livre 9,7 / 18,4 / 28,7. Réponses identiques. La prédiction `e2_prediction.txt` est confirmée pour les questions lecteur, pas pour les questions livre |
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

- `bookscope/` : `f1.py` à `f6.py` (services), `sharded.py` (mongos), `tags.py`, `db.py`, `web.py` (interface).
- `scripts/` : commandes des services, chargement (`load_raw.py`, `prepare_selection.py`, `selection_rule.py`), cluster (`start_sharded.py`, `load_sharded.py`, `compare_distributed.py`, `serve_distributed.py`), expériences (`measure_e1.py`, `measure_e2.py`, `evaluate_e3.py`), contrôles (`verify_independent.py`, `data_quality.py`, `compare_random_half.py`), `serve.py`.
- `compose.sharded.yml` : mongos, deux fragments et le serveur de configuration.
- `data/selection/` : lecteurs retenus, livres polars, lots E3 et F6. `data/measurements/` : mesures brutes.
- `docs/` : documentation des services et exemples réels.
