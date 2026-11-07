"""E3 : F3 retrouve-t-il les livres que le lecteur a notés ensuite ?

Pour chaque lecteur tiré au sort, ses 5 dernières notes sont cachées dans
data/selection/e3_eval.csv. F3 propose 10 livres à partir de l'historique
stocké ; c'est un succès si le lecteur a mis 4 ou 5 à l'un d'eux dans les
notes cachées. Deux cas sont séparés :
    complet   tout l'historique
    tronqué   seulement les k premières notes (k = 5 par défaut)

Référence : les 10 livres les plus notés que le lecteur n'a pas notés.
Mesures : taux de succès@10 et rappel@10. Les fuites d'information possibles
sont calculées à la fin.

    python scripts/evaluate_e3.py [--readers 1500] [--k 5]
"""

from __future__ import annotations

import argparse
import csv
import functools
import json
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bookscope.f3 as f3
from bookscope.db import connect

ROOT = Path(__file__).resolve().parents[1]
EVAL = ROOT / "data" / "selection" / "e3_eval.csv"
OUTPUT = ROOT / "data" / "measurements" / "e3.json"
TOP_N = 10
SEED = 20261106


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--readers", type=int, default=1500)
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()

    db = connect()
    held: dict[int, list[tuple[int, int]]] = defaultdict(list)
    with EVAL.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            held[int(row["user_id"])].append((int(row["book_id"]), int(row["rating"])))

    eligible = sorted(user for user, rows in held.items() if any(rating >= 4 for _book, rating in rows))
    random.Random(SEED).shuffle(eligible)
    sample = sorted(eligible[: args.readers])
    print(f"lecteurs avec au moins une note retenue >= 4 : {len(eligible)} ; échantillon : {len(sample)}", flush=True)

    counts = {row["_id"]: row["n"] for row in db.ratings.aggregate([{"$group": {"_id": "$book_id", "n": {"$sum": 1}}}], allowDiskUse=True)}
    f3._rating_counts = lambda _db: counts  # the data does not change during the evaluation
    popular = [book for book, _n in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]
    catalogue = {
        row["book_id"]: row.get("ratings_count") or 0
        for row in db.books.find({}, {"_id": 0, "book_id": 1, "ratings_count": 1})
    }
    catalogue_popular = [book for book, _n in sorted(catalogue.items(), key=lambda item: (-item[1], item[0]))]
    top100 = set(popular[:100])

    results = {"complete": new_bucket(), "truncated": new_bucket()}
    started = time.perf_counter()
    for index, user_id in enumerate(sample, start=1):
        relevant = {book for book, rating in held[user_id] if rating >= 4}
        history = {row["book_id"] for row in db.ratings.find({"user_id": user_id}, {"_id": 0, "book_id": 1})}
        for label, first in (("complete", None), ("truncated", args.k)):
            seen = history if first is None else {
                row["book_id"]
                for row in sorted(db.ratings.find({"user_id": user_id}, {"_id": 0, "book_id": 1, "source_row": 1}), key=lambda r: r["source_row"])[:first]
            }
            answer = f3.recommend(db, user_id, first=first)
            recommended = [item["book_id"] for item in answer.get("recommendations", [])][:TOP_N]
            baseline = take(popular, seen, TOP_N)
            leaky = take(catalogue_popular, seen, TOP_N)
            record(results[label], "f3", recommended, relevant, answer.get("mode"))
            record(results[label], "popularity", baseline, relevant)
            record(results[label], "catalogue_popularity", leaky, relevant)
            results[label]["share_top100"].append(
                sum(1 for book in recommended if book in top100) / len(recommended) if recommended else 0.0
            )
        if index % 250 == 0:
            print(f"  {index} lecteurs, {time.perf_counter() - started:.0f} s", flush=True)

    held_books = Counter(book for rows in held.values() for book, rating in rows if rating >= 4)
    leak = leakage(db, held, popular, catalogue_popular)
    summary = {
        "date": time.strftime("%Y-%m-%d"),
        "seed": SEED,
        "parameters": {"top_n": TOP_N, "relevant": "note >= 4 dans les 5 dernières notes", "k_truncated": args.k},
        "readers_eligible": len(eligible),
        "readers_evaluated": len(sample),
        "groups": {label: finalize(bucket) for label, bucket in results.items()},
        "held_out_positive_ratings": sum(held_books.values()),
        "leakage": leak,
    }
    OUTPUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def new_bucket() -> dict:
    return {"share_top100": [], "methods": defaultdict(lambda: {"hit": [], "recall": [], "precision": []}), "modes": Counter()}


def take(order: list[int], seen: set[int], size: int) -> list[int]:
    chosen = []
    for book in order:
        if book not in seen:
            chosen.append(book)
            if len(chosen) == size:
                break
    return chosen


def record(bucket: dict, method: str, recommended: list[int], relevant: set[int], mode: str | None = None) -> None:
    hits = len(set(recommended) & relevant)
    slot = bucket["methods"][method]
    slot["hit"].append(1.0 if hits else 0.0)
    slot["recall"].append(hits / len(relevant))
    slot["precision"].append(hits / TOP_N)
    if mode:
        bucket["modes"][mode] += 1


def finalize(bucket: dict) -> dict:
    out = {"methods": {}, "f3_modes": dict(bucket["modes"]), "f3_share_in_top100_mean": round(statistics.mean(bucket["share_top100"]), 4)}
    for method, slot in bucket["methods"].items():
        n = len(slot["hit"])
        out["methods"][method] = {
            "readers": n,
            "hit_rate_at_10": round(statistics.mean(slot["hit"]), 4),
            "recall_at_10": round(statistics.mean(slot["recall"]), 4),
            "precision_at_10": round(statistics.mean(slot["precision"]), 4),
            "hit_rate_standard_error": round(statistics.pstdev(slot["hit"]) / n ** 0.5, 4),
        }
    return out


def leakage(db, held, popular: list[int], catalogue_popular: list[int]) -> dict:
    """Mesure comment les notes cachées peuvent atteindre les données lues par F3."""
    total_held = sum(len(rows) for rows in held.values())
    top_stored = set(popular[:100])
    top_catalogue = set(catalogue_popular[:100])
    positive = [(user, book) for user, rows in held.items() for book, rating in rows if rating >= 4]
    in_stored = sum(1 for _user, book in positive if book in top_stored)
    in_catalogue = sum(1 for _user, book in positive if book in top_catalogue)
    on_list = 0
    sample_users = list(held)[:3000]
    wanted = {(user, book) for user, rows in held.items() if user in set(sample_users) for book, _rating in rows}
    for row in db.to_read.find({"user_id": {"$in": sample_users}}, {"_id": 0, "user_id": 1, "book_id": 1}):
        if (row["user_id"], row["book_id"]) in wanted:
            on_list += 1
    return {
        "note": "ratings_count et average_rating de books.csv, et les compteurs de tags, viennent du jeu Goodreads complet : ils incluent les notes retenues. F3 ne lit ni ratings_count ni average_rating ; il lit les compteurs de tags (seulement en repli) et les notes stockées.",
        "positive_held_out_in_top100_of_stored_ratings": round(in_stored / len(positive), 4),
        "positive_held_out_in_top100_of_catalogue_ratings_count": round(in_catalogue / len(positive), 4),
        "held_out_pairs_on_reading_list_sample_3000_readers": on_list,
        "held_out_ratings_total": total_held,
        "reading_list_rule": "les 279 paires retenues présentes dans to_read.csv ont été retirées avant le chargement",
        "time_order_limit": "l'ordre du fichier est un ordre relatif par lecteur ; les notes des voisins ne sont pas datées par rapport aux notes retenues du lecteur évalué",
    }


if __name__ == "__main__":
    main()
