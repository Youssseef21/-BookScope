"""Vérification indépendante, depuis les fichiers CSV seulement.

Le script reconstruit les notes stockées à partir de ratings.csv et de la
sélection, sans base de données, puis recalcule :
    F2  la comparaison des lecteurs 1 et 2
    F4  le lien direct entre les livres 258 et 1467
    F5  les livres et lecteurs qui relient mystery et historical-fiction

Avec --compare, il affiche les valeurs des services à côté des siennes
(seule option qui lit MongoDB).

    python scripts/verify_independent.py
    python scripts/verify_independent.py --compare
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
SEL = ROOT / "data" / "selection"
OUTPUT = ROOT / "data" / "measurements" / "verification.json"

READER_A, READER_B = 1, 2
BOOK_A, BOOK_B = 258, 1467
TAG_A, TAG_B = "mystery", "historical-fiction"
ACTIVITY_CAP = 150
MIN_RATING = 4
MIN_TAG_COUNT = 50


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--compare", action="store_true", help="compare with the services (reads MongoDB)")
    args = parser.parse_args()

    users = {int(row["user_id"]) for row in csv.DictReader((SEL / "readers.csv").open(encoding="utf-8"))}
    held_out = {
        (int(row["user_id"]), int(row["book_id"]))
        for row in csv.DictReader((SEL / "e3_eval.csv").open(encoding="utf-8"))
    }
    # notes stockées = notes des lecteurs retenus, sans les notes cachées de E3
    # (le lot F6 en fait partie une fois importé)
    by_user: dict[int, dict[int, tuple[int, int]]] = defaultdict(dict)
    by_book_positive: dict[int, set[int]] = defaultdict(set)
    stored = 0
    book_counts: dict[int, int] = defaultdict(int)
    with (RAW / "ratings.csv").open(encoding="utf-8", newline="") as handle:
        for source_row, row in enumerate(csv.DictReader(handle), start=1):
            user_id = int(row["user_id"])
            if user_id not in users:
                continue
            book_id = int(row["book_id"])
            if (user_id, book_id) in held_out:
                continue
            rating = int(row["rating"])
            by_user[user_id][book_id] = (rating, source_row)
            book_counts[book_id] += 1
            stored += 1
            if rating >= MIN_RATING:
                by_book_positive[book_id].add(user_id)
    print(f"notes reconstruites depuis les CSV : {stored}", flush=True)

    result = {
        "stored_ratings_rebuilt": stored,
        "f2": verify_f2(by_user, book_counts),
        "f4": verify_f4(by_user, by_book_positive),
        "f5": verify_f5(by_user, by_book_positive),
    }
    if args.compare:
        result["service_comparison"] = compare_with_services(result)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def verify_f2(by_user, book_counts) -> dict:
    left, right = by_user[READER_A], by_user[READER_B]
    shared = sorted(set(left) & set(right))
    rows = []
    for book_id in shared:
        weight = round(1 / math.sqrt(book_counts[book_id]), 4)
        rows.append((abs(left[book_id][0] - right[book_id][0]), weight))
    shorter = min(len(left), len(right))
    overlap = len(rows) / shorter
    total_weight = sum(weight for _diff, weight in rows)
    agreement = sum((1 - diff / 4) * weight for diff, weight in rows) / total_weight
    return {
        "readers": [READER_A, READER_B],
        "ratings_used": [len(left), len(right)],
        "shared_books": len(rows),
        "overlap_with_shorter_history": round(overlap, 4),
        "popularity_weighted_agreement": round(agreement, 4),
        "strength": round(overlap * agreement, 4),
        "similar": sum(1 for diff, _w in rows if diff <= 1),
        "disagree": sum(1 for diff, _w in rows if diff >= 2),
    }


def verify_f4(by_user, by_book_positive) -> dict:
    shared = sorted(by_book_positive[BOOK_A] & by_book_positive[BOOK_B])
    ordinary = [user for user in shared if len(by_user[user]) < ACTIVITY_CAP]
    return {
        "books": [BOOK_A, BOOK_B],
        "readers_who_liked_both": len(shared),
        "ordinary_readers": len(ordinary),
        "highly_active_readers": len(shared) - len(ordinary),
        "convincing_direct": len(ordinary) >= 5,
    }


def verify_f5(by_user, by_book_positive) -> dict:
    books = {int(row["book_id"]): row for row in csv.DictReader((RAW / "books.csv").open(encoding="utf-8"))}
    goodreads = {int(row["goodreads_book_id"]): int(row["book_id"]) for row in books.values()}
    tags = {row["tag_name"]: int(row["tag_id"]) for row in csv.DictReader((RAW / "tags.csv").open(encoding="utf-8"))}
    wanted = {tags[TAG_A]: set(), tags[TAG_B]: set()}
    with (RAW / "book_tags.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            tag_id = int(row["tag_id"])
            if tag_id in wanted and int(row["count"]) >= MIN_TAG_COUNT:
                wanted[tag_id].add(goodreads[int(row["goodreads_book_id"])])
    books_a, books_b = wanted[tags[TAG_A]], wanted[tags[TAG_B]]
    bridges = books_a & books_b
    only_a, only_b = books_a - bridges, books_b - bridges
    readers_a, readers_b = set(), set()
    for book in only_a:
        readers_a |= by_book_positive.get(book, set())
    for book in only_b:
        readers_b |= by_book_positive.get(book, set())
    return {
        "tags": [TAG_A, TAG_B],
        "books_with_tag_a": len(books_a),
        "books_with_tag_b": len(books_b),
        "bridge_books_total": len(bridges),
        "readers_full": len(readers_a & readers_b),
    }


def compare_with_services(expected: dict) -> dict:
    sys.path.insert(0, str(ROOT))
    from bookscope.db import connect
    from bookscope.f2 import compare
    from bookscope.f4 import trace
    from bookscope.f5 import link_interests

    db = connect()
    f2 = compare(db, READER_A, READER_B)
    f4 = trace(db, BOOK_A, BOOK_B)
    f5 = link_interests(db, TAG_A, TAG_B)
    pairs = {
        "f2.shared_books": (expected["f2"]["shared_books"], f2["shared_books"]),
        "f2.overlap": (expected["f2"]["overlap_with_shorter_history"], f2["overlap_with_shorter_history"]),
        "f2.agreement": (expected["f2"]["popularity_weighted_agreement"], f2["popularity_weighted_agreement"]),
        "f2.strength": (expected["f2"]["strength"], f2["strength"]),
        "f4.ordinary_readers": (expected["f4"]["ordinary_readers"], f4["direct"]["ordinary_readers"]),
        "f4.highly_active_readers": (expected["f4"]["highly_active_readers"], f4["direct"]["highly_active_readers"]),
        "f5.bridge_books_total": (expected["f5"]["bridge_books_total"], f5["bridge_books_total"]),
        "f5.readers_full": (expected["f5"]["readers_full"], f5["readers_full"]),
    }
    return {name: {"csv_script": a, "service": b, "equal": a == b} for name, (a, b) in pairs.items()}


if __name__ == "__main__":
    main()
