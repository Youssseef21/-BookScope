# BookScope

Plateforme de découverte de livres pour le projet Big Data and NoSQL (M2 SLCD, Université Côte d'Azur). Elle s'appuie sur le jeu [Goodbooks-10k](https://github.com/zygmuntz/goodbooks-10k) et sur MongoDB.

Les notes du fichier `ratings.csv` se relient aux livres par `book_id` (de 1 à 10 000). Les tags se relient par `goodreads_book_id`. L'ordre des lignes de `ratings.csv` est l'ordre relatif dans le temps : le fichier ne contient pas de dates.

## Règle de sélection

Un livre est un polar si au moins un de ces tags a un `count` d'au moins 50 : `mystery`, `mysteries`, `thriller`, `thrillers`, `crime`, `crime-fiction`, `detective`, `detectives`, `suspense`, `murder-mystery`, `murder`, `mystery-thriller`, `mystery-suspense`, `crime-thriller`, `cozy-mystery`.

Un lecteur est retenu si `user_id % 5` vaut 1 ou 2, et s'il a noté au moins 8 livres polar. Toutes ses notes sont conservées, pas seulement les polars.

Cette règle retient 20 586 lecteurs et 2 336 014 notes. Pour chaque lecteur, les 5 notes les plus récentes sont mises de côté dans `data/selection/e3_eval.csv` (évaluation). Les 5 notes juste avant vont dans `data/selection/f6_batch.csv` (import ultérieur). Le reste, 2 130 154 notes, est chargé dans la base `bookscope_app`. Les chiffres complets sont dans `data/selection/summary.json`.

## Lancer le projet

MongoDB 7 doit tourner sur `localhost:27017`. Avec Docker :

```text
docker run -d --name bookscope-mongo -p 27017:27017 -v bookscope-mongo-data:/data/db mongo:7
```

Puis, dans ce dossier :

```text
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Télécharger l'archive et la décompresser dans `data/raw` :

```text
https://github.com/zygmuntz/goodbooks-10k/releases/download/v1.0/goodbooks-10k.zip
```

Les fichiers attendus sont `books.csv`, `ratings.csv`, `to_read.csv`, `book_tags.csv` et `tags.csv`.

```text
.venv\Scripts\python.exe scripts\load_raw.py
.venv\Scripts\python.exe scripts\prepare_selection.py
```

`load_raw.py` remplit la base complète `bookscope`. `prepare_selection.py` relit les CSV, réécrit les fichiers de `data/selection` et remplit `bookscope_app`.

## Interface

Avec MongoDB déjà chargé :

```text
.venv\Scripts\python.exe scripts\serve.py
```

La page est sur http://127.0.0.1:8000 . Elle permet d'explorer un livre, de comparer deux lecteurs, de proposer des livres, de relier deux livres, de relier deux thèmes et d'importer un lot de notes. Un calcul peut prendre quelques secondes.

## F1 — explorer un livre

```text
.venv\Scripts\python.exe scripts\explore_book.py 258
.venv\Scripts\python.exe scripts\explore_book.py "Selected Poems"
```

Un titre qui correspond à plusieurs livres est affiché tel quel, sans en choisir un. Pour un livre unique, le service montre la fiche, les livres liés par des lecteurs qui ont mis au moins 4/5 aux deux, et les livres liés par des tags de contenu. Un tag de statut (`to-read`, `favorites`) ou un tag présent sur au moins la moitié du catalogue (`fiction`) ne suffit pas à justifier un lien. Chaque lien par les lecteurs cite des `user_id`, des notes et leur position dans `ratings.csv`.

## F6 — importer des notes

```text
.venv\Scripts\python.exe scripts\import_batch.py
.venv\Scripts\python.exe scripts\import_batch.py data\selection\f6_batch.csv
```

Une note est identifiée par le couple lecteur + livre. Un couple nouveau est ajouté. Le même couple avec la même note est ignoré : relancer le fichier ne change rien. Si le couple existe déjà avec une note différente, la note en base reste celle lue par les autres services, et la note reçue est conservée dans `rating_conflicts`. Un lecteur absent de la sélection, ou un livre absent du catalogue, est refusé.

## Deux façons de ranger les notes

Les services lisent une collection où chaque note est un document. Une autre collection, `readers_embedded`, range les mêmes notes dans un document par lecteur. `scripts/measure_model.py` reconstruit cette collection, vérifie que le nombre de notes est identique, puis mesure deux requêtes : un échauffement, 11 répétitions, et la médiane.

Sur cet essai, l'historique du lecteur 1 (112 notes) prend 0,59 ms avec un document par lecteur et 1,45 ms avec une note par document. Les notes d'au moins 4/5 du livre 258 (987 notes) prennent 5,5 ms avec une note par document et 56 ms avec un document par lecteur. Le détail est dans `data/measurements/model_tradeoff.json`.

Les services qui partent d'un livre gardent donc une note par document. L'historique d'un lecteur reste rapide dans les deux rangements.

## Deux instances

Ce n'est pas un cluster de machines. Deux processus MongoDB tournent sur cet ordinateur, avec chacun son port et son disque : `localhost:27018` (A) et `localhost:27019` (B). Les livres et les listes « à lire » sont copiés des deux côtés. Chaque note n'est stockée que d'un côté.

```text
.venv\Scripts\python.exe scripts\load_distributed.py
.venv\Scripts\python.exe scripts\compare_distributed.py by_reader
.venv\Scripts\python.exe scripts\compare_distributed.py by_book
```

Par lecteur, `user_id` pair va sur A et `user_id` impair sur B. Le lecteur 1 a ses 112 notes sur B et aucune sur A. Le lecteur 2 a ses 60 notes sur A. Le livre 258 est coupé : 648 notes sur A et 668 sur B.

Par livre, `book_id` pair va sur A et `book_id` impair sur B. Le livre 258 a ses 1 316 notes sur A. Le livre 1467 en a 291, toutes sur B. Le lecteur 1 est alors coupé : 58 notes sur A et 54 sur B.

Les mêmes fonctions F1, F2, F3 et F4 donnent le même résultat, dans le même ordre, que sur le serveur unique. Relancer l'import F6 sur la répartition par lecteur ajoute 0 note : les 102 930 notes du lot sont déjà là. Le détail des échanges est dans `data/measurements/distributed.json`.

Pour une question qui part d'un lecteur, la répartition par lecteur lit son historique sur une seule instance. Pour une question qui part d'un livre, la répartition par livre lit ce livre sur une seule instance. Un chemin entre deux livres interroge les deux dans les deux cas, parce qu'il passe par d'autres lecteurs et d'autres livres.

Si B est arrêté, F2 puis F3 sur le lecteur 1 en répartition par livre ne répondent pas. Une partie de ses notes est sur B, et cette partie n'est pas présentée comme un résultat complet.

## Prédiction E2

`data/measurements/e2_prediction.txt` a été écrit avant les mesures de temps. La charge personnalisée est F2 et F3. La charge d'exploration est F1 et F4. Le serveur unique est attendu le plus rapide dans les deux cas. Entre les deux répartitions, celle par lecteur est attendue plus rapide pour un lecteur, et celle par livre pour un livre.

## E1 — trois tailles

Les trois jeux sont des préfixes de `ratings.csv`, dans l'ordre du fichier. Le plus grand contient les 5 976 479 notes. Les 10 000 livres sont chargés à chaque fois. Le temps de chargement inclut les index. F2 et F1 sont mesurés sur les mêmes entrées : lecteurs 2276 et 4606, livre 26 (*The Da Vinci Code*). Un appel d'échauffement, puis 5 mesures, et la médiane. Le détail est dans `data/measurements/e1.json`.

| Notes | Lecteurs | Livres notés | Chargement | Disque | F2 | F1 |
|---|---:|---:|---:|---:|---:|---:|
| 200 000 | 3 374 | 5 620 | 9,1 s | 17 Mo | 10 ms | 3,3 s |
| 1 000 000 | 12 586 | 7 708 | 31 s | 41 Mo | 17 ms | 6,5 s |
| 5 976 479 | 53 424 | 10 000 | 174 s | 167 Mo | 59 ms | 27 s |

Les deux lecteurs ont déjà presque tout leur historique dans le premier préfixe : 185 et 183 notes, 57 livres en commun. F2 ralentit quand même, parce que la popularité de ces livres est recomptée sur un fichier plus grand. F1 suit le nombre de notes du livre 26 : 1 246, puis 4 593, puis 13 072.

## Fichiers

| Chemin | Rôle |
|---|---|
| `scripts/selection_rule.py` | constantes de la règle de sélection |
| `scripts/load_raw.py` | chargement du jeu complet dans `bookscope` |
| `scripts/prepare_selection.py` | coupe E3 / F6 et chargement de `bookscope_app` |
| `scripts/explore_book.py` | commande du service F1 |
| `scripts/compare_readers.py` | commande du service F2 |
| `scripts/recommend.py` | commande du service F3 |
| `scripts/trace_books.py` | commande du service F4 |
| `scripts/link_interests.py` | commande du service F5 |
| `scripts/import_batch.py` | commande du service F6 |
| `scripts/measure_model.py` | comparaison des deux rangements de notes |
| `scripts/load_distributed.py` | copie des livres et répartition des notes |
| `scripts/compare_distributed.py` | comparaison avec le serveur unique |
| `scripts/measure_e1.py` | temps de chargement et de services sur trois tailles |
| `scripts/serve.py` | page web locale |
| `bookscope/f1.py` | calcul des liens d'un livre |
| `bookscope/f2.py` | comparaison de deux lecteurs |
| `bookscope/f3.py` | recommandations |
| `bookscope/f4.py` | chemin entre deux livres |
| `bookscope/f5.py` | lien entre deux thèmes |
| `bookscope/f6.py` | import d'un lot de notes |
| `bookscope/cluster.py` | accès aux deux instances |
| `bookscope/web.py` | interface |
| `bookscope/tags.py` | tags de statut et tags trop généraux |
| `data/selection/` | lecteurs retenus, livres polar, lots E3 et F6 |
| `data/measurements/model_tradeoff.json` | temps mesurés pour les deux rangements |
| `data/measurements/distributed.json` | égalité des résultats et échanges entre instances |
| `data/measurements/e2_prediction.txt` | prédiction E2, déposée avant les mesures |
| `data/measurements/e1.json` | temps E1 sur trois tailles de notes |
