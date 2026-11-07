"""Prépare les données de E2 : toutes les notes de ratings.csv.

Elles sont chargées trois fois :
    e1_all            sur le serveur unique
    dist_reader_full  sur les instances A et B, répartition par lecteur
    dist_book_full    sur les instances A et B, répartition par livre

    python scripts/prepare_e2_data.py
"""

from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pymongo import ASCENDING, MongoClient

import load_raw
import measure_e1
from bookscope.cluster import INSTANCE_A, INSTANCE_B
from bookscope.db import MONGO_URI

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
BATCH = 20_000
FULL = ("dist_reader_full", "dist_book_full")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    tags = load_raw.read_tags()
    book_tags = load_raw.read_book_tags(tags)
    with (RAW / "books.csv").open(encoding="utf-8", newline="") as handle:
        books = [load_raw.book_document(row, book_tags) for row in csv.DictReader(handle)]

    if "e1_all" in client.list_database_names() and client["e1_all"].ratings.estimated_document_count() > 5_900_000:
        print("e1_all déjà chargé", flush=True)
    else:
        print("chargement de e1_all (toutes les notes)", flush=True)
        measure_e1.load_subset(client, "e1_all", None, books)

    instances = {
        "A": MongoClient(INSTANCE_A, serverSelectionTimeoutMS=5000),
        "B": MongoClient(INSTANCE_B, serverSelectionTimeoutMS=5000),
    }
    for instance in instances.values():
        instance.admin.command("ping")
        for name in FULL:
            instance.drop_database(name)
            instance[name].books.insert_many((dict(book) for book in books), ordered=False)

    print("répartition de toutes les notes sur A et B", flush=True)
    started = time.perf_counter()
    buckets = {f"{p}-{s}": [] for p in ("reader", "book") for s in ("A", "B")}
    counts = {key: 0 for key in buckets}
    with (RAW / "ratings.csv").open(encoding="utf-8", newline="") as handle:
        for source_row, row in enumerate(csv.DictReader(handle), start=1):
            user_id = int(row["user_id"])
            book_id = int(row["book_id"])
            document = {
                "user_id": user_id,
                "book_id": book_id,
                "rating": int(row["rating"]),
                "source_row": source_row,
            }
            reader_key = "reader-" + ("A" if user_id % 2 == 0 else "B")
            book_key = "book-" + ("A" if book_id % 2 == 0 else "B")
            buckets[reader_key].append(document)
            buckets[book_key].append(dict(document))
            counts[reader_key] += 1
            counts[book_key] += 1
            if len(buckets[reader_key]) >= BATCH or len(buckets[book_key]) >= BATCH:
                flush(instances, buckets)
            if source_row % 1_000_000 == 0:
                print(f"  {source_row} notes lues", flush=True)
    flush(instances, buckets)

    for instance in instances.values():
        for name in FULL:
            database = instance[name]
            database.books.create_index([("book_id", ASCENDING)], unique=True)
            database.books.create_index([("goodreads_book_id", ASCENDING)], unique=True)
            database.books.create_index([("title", ASCENDING)])
            database.books.create_index([("tags.name", ASCENDING)])
            database.ratings.create_index([("user_id", ASCENDING), ("book_id", ASCENDING)], unique=True)
            database.ratings.create_index([("book_id", ASCENDING)])

    total = client["e1_all"].ratings.count_documents({})
    print(f"serveur unique : {total} notes", flush=True)
    print(f"par lecteur : A {counts['reader-A']}, B {counts['reader-B']}", flush=True)
    print(f"par livre : A {counts['book-A']}, B {counts['book-B']}", flush=True)
    if counts["reader-A"] + counts["reader-B"] != total or counts["book-A"] + counts["book-B"] != total:
        raise RuntimeError("une répartition a perdu ou copié des notes")
    print(f"terminé en {time.perf_counter() - started:.0f} s (répartition)", flush=True)


def flush(instances, buckets) -> None:
    for key, rows in buckets.items():
        if not rows:
            continue
        placement, side = key.split("-")
        name = "dist_reader_full" if placement == "reader" else "dist_book_full"
        instances[side][name].ratings.insert_many(rows, ordered=False)
        buckets[key] = []


if __name__ == "__main__":
    main()
