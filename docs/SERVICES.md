# Les six services

Chaque service s'utilise en ligne de commande (`scripts/`), par la page de la base principale (`scripts/serve.py`, http://127.0.0.1:8000) ou par la page du cluster (`scripts/serve_distributed.py`, http://127.0.0.1:8001). Cette page parle à mongos (port 27200). Les mêmes fonctions Python (`bookscope/`) calculent le résultat à chaque appel. Par lecteur ou par livre, F1 à F5 donnent les mêmes scores que sur `bookscope_app`. Si un fragment est arrêté, la page affiche « Réponse refusée : elle serait incomplète. » Le titre d'un livre peut encore s'afficher, parce que le catalogue est sur shard1. Les exemples sont des sorties réelles, enregistrées dans `docs/examples/`, sur la base `bookscope_app` (2 233 084 notes, lot F6 importé).

Chaque résultat cite ses preuves : `user_id`, note et `source_row`. Pour retrouver une note dans le fichier d'origine, regarder la ligne `source_row + 1` de `data/raw/ratings.csv` (l'en-tête compte pour une ligne).

## F1, explorer un livre

- **Commande :** `python scripts/explore_book.py <book_id ou titre>`
- **Entrée :** un `book_id` ou un titre.
- **Sortie :** fiche du livre, tags de contenu, tags ignorés (statut ou trop courants), 5 livres liés par les lecteurs (au moins 5 lecteurs à 4/5 ou plus sur les deux livres), 5 livres liés par les tags.
- **Cas particuliers :** un titre qui correspond à plusieurs livres renvoie la liste, sans choix. Un livre inconnu renvoie `not_found`.
- **Exemples :** `f1_ambigu.txt` (« Selected Poems », 4 livres), `f1_livre258.txt`.

## F2, comparer deux lecteurs

- **Commande :** `python scripts/compare_readers.py <user_a> <user_b> [--first USER K]`
- **Entrée :** deux `user_id`, historique éventuellement tronqué aux K plus anciennes notes.
- **Sortie :** livres communs, accords (écart de 1 au plus) et désaccords (écart de 2 au moins), force du lien.
- **Cas particuliers :** moins de 5 livres communs : `too_few_shared_books`, pas de score. Un historique tronqué est signalé.
- **Exemple :** `f2_lecteurs_1_2.txt` (6 livres communs, force 0,083).

## F3, recommander

- **Commande :** `python scripts/recommend.py <user_id> [--first K]`
- **Entrée :** un `user_id`, historique éventuellement tronqué.
- **Sortie :** 10 livres non notés, avec les lecteurs proches qui les ont aimés, la part dans le top 100 et la popularité médiane.
- **Cas particuliers :** moins de 5 notes ou aucun voisin : repli sur les tags (`short_history`).
- **Exemples :** `f3_lecteur1.txt`, `f3_lecteur1_first5.txt`.

## F4, relier deux livres

- **Commande :** `python scripts/trace_books.py <livre_a> <livre_b>`
- **Entrée :** deux `book_id` ou titres.
- **Sortie :** lien direct (lecteurs de moins de 150 notes), jusqu'à 3 chemins par un livre pont hors du top 100, auteur ou tags communs.
- **Cas particuliers :** titre ambigu : liste. Rien de convaincant : `not_convincing`.
- **Exemple :** `f4_258_1467.txt` (126 lecteurs aiment les deux, dont 121 non très actifs).

## F5, relier deux intérêts

- **Commande :** `python scripts/link_interests.py <tag_a> <tag_b>`
- **Entrée :** deux tags thématiques.
- **Sortie :** livres portant les deux tags, lecteurs qui ont aimé un livre de chaque thème, mêmes comptes sans le top 100 ni les lecteurs de 150 notes ou plus, verdict (`persists`, `weakens`, `disappears`, `none`).
- **Cas particuliers :** un tag de statut (`to-read`) ou présent sur la moitié du catalogue (`fiction`) est refusé.
- **Exemple :** `f5_mystery_historical.txt` (le lien persiste).

## F6, importer des notes

- **Commande :** `python scripts/import_batch.py [fichier.csv]` (par défaut `data/selection/f6_batch.csv`)
- **Entrée :** CSV avec les colonnes `user_id, book_id, rating, source_row`.
- **Sortie :** lignes lues, ajoutées, déjà présentes, en conflit, refusées.
- **Règle :** une note est le couple (lecteur, livre). Nouveau couple : ajouté. Même note : ignoré. Note différente : l'ancienne reste, la nouvelle va dans `rating_conflicts`. Lecteur hors sélection ou livre inconnu : refusé.
- **Exemple :** `f6_second_import.txt` (le second import ajoute 0 note).
