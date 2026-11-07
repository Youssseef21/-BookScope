# BookScope — pourquoi cette sélection, et où on en est

Youssef Harrane, M2 SLCD. Projet Big Data and NoSQL.

Le jeu de départ est Goodbooks-10k : 10 000 livres et 5 976 479 notes. Le sujet demande de choisir une partie réelle de ces notes, de l'enregistrer avant de construire les services, et de mettre de côté deux lots qui ne servent pas tout de suite.

## Pourquoi cette sélection

On ne garde pas tout le fichier. On garde des lecteurs qui ont vraiment lu des polars, et on garde toutes leurs notes, y compris les livres qui ne sont pas des polars.

Un livre est un polar si au moins un de ces tags a été posé au moins 50 fois : `mystery`, `mysteries`, `thriller`, `thrillers`, `crime`, `crime-fiction`, `detective`, `detectives`, `suspense`, `murder-mystery`, `murder`, `mystery-thriller`, `mystery-suspense`, `crime-thriller`, `cozy-mystery`. Cela fait 2 393 livres.

Un lecteur est gardé si deux choses sont vraies en même temps :

- `user_id % 5` vaut 1 ou 2 ;
- il a noté au moins 8 de ces polars.

`user_id % 5` ne dit rien sur la qualité du lecteur. C'est un tirage fixe : le même calcul redonne toujours les mêmes personnes. Le seuil de 8 notes sert à écarter quelqu'un qui n'a touché un polar qu'une fois.

Résultat : 20 586 lecteurs et 2 336 014 notes. Parmi ces notes, 741 899 portent sur un polar. Les autres restent, parce qu'un lecteur de polars lit aussi autre chose. Le lecteur 1, par exemple, a 117 notes dans le fichier d'origine, dont 23 polars.

Conséquence : un lecteur qui ne passe pas la règle disparaît. Un lien qui n'existerait que par lui n'est plus visible. Les goûts non polars des lecteurs gardés, eux, restent.

Les notes se relient aux livres par `book_id` (de 1 à 10 000). Les tags se relient par `goodreads_book_id`. Le fichier `ratings.csv` n'a pas de dates. L'ordre des lignes est l'ordre dans le temps. La position d'une note dans ce fichier s'appelle `source_row`.

## Ce qui a été mis de côté avant les services

Pour chaque lecteur gardé, les notes sont coupées selon cet ordre :

- les 5 plus récentes vont dans `data/selection/e3_eval.csv` (102 930 notes) : elles servent plus tard à vérifier si une proposition était bonne, et elles ne sont pas chargées dans l'application ;
- les 5 juste avant vont dans `data/selection/f6_batch.csv` (102 930 notes) : c'est le lot d'import ;
- le reste a été chargé en premier : 2 130 154 notes.

Ces trois lots ne se recouvrent pas. Une note est identifiée par le couple lecteur + livre. Les listes « à lire » des lecteurs gardés sont aussi chargées, sauf 279 paires qui tombaient sur une note mise de côté.

## Ce qui est fait

1. **Les données.** Les CSV sont dans MongoDB. La base `bookscope` contient le fichier complet. La base `bookscope_app`, sur `localhost:27017`, est celle des services. Chaque note y est un document : `user_id`, `book_id`, `rating`, `source_row`.

2. **Les six services**, calculés à partir des notes stockées, pas d'une table préparée à l'avance. La page est sur http://127.0.0.1:8000 .
   - F1 explore un livre. Le livre 258, *The Shadow of the Wind*, est lié au livre 1467 par des lecteurs qui ont mis au moins 4/5 aux deux. Le titre « Selected Poems » correspond à plusieurs livres : aucun n'est choisi à sa place.
   - F2 compare deux lecteurs. Les lecteurs 1 et 2 ont assez de livres en commun pour une comparaison. S'il y en a moins de 5, la comparaison est dite non significative.
   - F3 propose des livres pas encore notés, avec une explication, et dit quelle part tombe dans les 100 livres les plus notés.
   - F4 relie deux livres, par des lecteurs ou par le catalogue, et le dit quand le lien n'est pas convaincant.
   - F5 relie deux thèmes, par exemple `mystery` et `historical-fiction`, puis refait le compte sans les livres très notés ni les lecteurs très actifs. `to-read` et `fiction` sont refusés : ce ne sont pas des thèmes.
   - F6 a importé les 102 930 notes du lot. La base des services est alors passée à 2 233 084 notes. Un second import du même fichier ajoute 0 note. Si le couple lecteur + livre existe déjà avec une autre note, l'ancienne reste celle lue par les services, et la nouvelle est conservée à part.

3. **Deux façons de ranger les mêmes notes.** Une note par document, ou toutes les notes d'un lecteur dans un seul document. Partir d'un livre est beaucoup plus lent dans le second rangement (56 ms contre 5,5 ms pour les notes d'au moins 4/5 du livre 258). Les services gardent donc une note par document.

4. **Deux serveurs sur cet ordinateur**, ports 27018 (A) et 27019 (B), chacun avec son disque. Ce n'est pas un groupe de machines. Les livres sont copiés des deux côtés. Chaque note n'est que d'un côté.
   - Par lecteur : le lecteur 1 a ses 112 notes sur B, le lecteur 2 a les siennes sur A.
   - Par livre : le livre 258 est entier sur A, le livre 1467 est entier sur B, et le lecteur 1 est coupé entre les deux.
   - F1, F2, F3 et F4 donnent le même résultat que sur le serveur unique. Si B est arrêté alors que les notes du lecteur 1 sont des deux côtés, F2 et F3 ne répondent pas : une réponse incomplète n'est pas présentée comme complète.

5. **E1, trois tailles de notes**, préfixes du fichier complet. Mêmes questions à chaque fois : lecteurs 2276 et 4606, livre 26 (*The Da Vinci Code*).

   | Notes | Lecteurs | Chargement | Disque | F2 | F1 |
   |---:|---:|---:|---:|---:|---:|
   | 200 000 | 3 374 | 9,1 s | 17 Mo | 10 ms | 3,3 s |
   | 1 000 000 | 12 586 | 31 s | 41 Mo | 17 ms | 6,5 s |
   | 5 976 479 | 53 424 | 174 s | 167 Mo | 59 ms | 27 s |

6. **E2 a été mesurée** le 6 novembre 2026, après la prédiction déposée le 30 octobre dans `data/measurements/e2_prediction.txt`. Le jeu est le fichier complet, 5 976 479 notes. Trois configurations : le serveur unique, la répartition par lecteur, la répartition par livre. Deux charges : personnalisée (F2 et F3) et exploration (F1 et F4). Quatre requêtes, un échauffement non compté, puis 3 répétitions.

   La prédiction disait que le serveur unique serait le plus rapide, la répartition par lecteur meilleure pour un lecteur, et la répartition par livre meilleure pour un livre. Les temps mesurés sont du même ordre dans les trois configurations : environ 12 s pour quatre requêtes personnalisées, environ 28 s pour un mélange, environ 43 s pour quatre requêtes d'exploration. L'écart entre configurations reste dans l'écart-type. La prédiction n'est pas confirmée. Les deux instances tournent sur la même machine et se partagent le processeur, la mémoire et le disque. Le détail est dans `data/measurements/e2.json`.

7. **E3** juge F3 sur les notes jamais chargées dans l'application, `e3_eval.csv`. Une proposition est bonne si le livre était noté au moins 4/5 dans les 5 dernières notes du lecteur. Sur 1 500 lecteurs, avec l'historique complet, F3 retrouve un tel livre dans ses 10 propositions pour 9,8 % des lecteurs. Proposer les livres les plus notés y arrive pour 4,9 %. Avec seulement les 5 premières notes, F3 tombe à 3,1 %, au niveau de cette baseline. Le fichier `data/measurements/e3.json` note aussi les fuites possibles : les compteurs de tags viennent du jeu Goodreads complet, et l'ordre du fichier date chaque lecteur, pas les voisins entre eux.

8. **Vérification et qualité des données.** `scripts/verify_independent.py` relit les CSV, sans MongoDB. Pour F2 (lecteurs 1 et 2), F4 (livres 258 et 1467) et F5 (`mystery` et `historical-fiction`), les valeurs recalculées sont identiques à celles des services. `scripts/data_quality.py` relève dix problèmes, avec un effectif et des identifiants : par exemple 700 livres sans ISBN, 590 sans titre original, des titres portés par plusieurs livres (« Selected Poems »), et des tags qui décrivent une étagère plutôt qu'une histoire. Les entrées, les sorties et un exemple réel de chaque service sont dans `docs/SERVICES.md`.

## Ce qui reste

Le code, les mesures et le README sont dans le dépôt. Le rapport demandé par le sujet, dix pages au plus, n'y est pas. La page http://127.0.0.1:8000 interroge le serveur unique. Les deux instances se lancent et se comparent avec les scripts, pas avec cette page. Les 102 930 notes de `e3_eval.csv` restent en dehors de l'application : c'est le lot qui sert à juger F3, il ne doit pas être chargé.
