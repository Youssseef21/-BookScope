"""E1 — loading time, storage and service time on three real subsets.

The subsets are nested prefixes of ratings.csv, in the file's time order.
The largest prefix is the whole file. The catalogue of 10 000 books is
loaded with every subset. Representative services are F2, a reader request,
and F1, a book request. The same reader pair and the same book are used
on all three subsets. One untimed call warms each service, then five calls
are timed.

    python scripts/measure_e1.py
"""

from __future__ import annotations

import csv
import json
import platform
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from pymongo import ASCENDING, MongoClient

import load_raw
from bookscope.db import MONGO_URI
from bookscope.f1 import explore
from bookscope.f2 import compare

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw" / "ratings.csv"
OUTPUT = ROOT / "data" / "measurements" / "e1.json"
SUBSETS = (
    ("e1_200k", 200_000),
    ("e1_1m", 1_000_000),
    ("e1_all", None),
)
REPEATS = 5
BATCH = 20_000


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    build = client.admin.command("buildInfo")
    tags = load_raw.read_tags()
    book_tags = load_raw.read_book_tags(tags)
    with (ROOT / "data" / "raw" / "books.csv").open(encoding="utf-8", newline="") as handle:
        books = [load_raw.book_document(row, book_tags) for row in csv.DictReader(handle)]

    loaded = []
    for name, limit in SUBSETS:
        print(f"chargement {name}", flush=True)
        loaded.append(load_subset(client, name, limit, books))

    small = client["e1_200k"]
    user_a, user_b = top_users(small)
    book_id = top_book(small)
    print(f"requêtes communes : lecteurs {user_a} et {user_b}, livre {book_id}", flush=True)

    for item in loaded:
        database = client[item["database"]]
        print(f"services {item['database']}", flush=True)
        item["services"] = {
            "f2": time_call(lambda db=database: compare(db, user_a, user_b), "shared_books"),
            "f1": time_call(lambda db=database: explore(db, book_id), "book"),
        }
        item["requests"] = {"user_a": user_a, "user_b": user_b, "book_id": book_id}

    result = {
        "date": "2026-10-30",
        "prediction_file": "data/measurements/e2_prediction.txt",
        "hardware": {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "cpu_count": __import__("os").cpu_count(),
            "python": platform.python_version(),
            "mongo": build.get("version"),
        },
        "protocol": {
            "subsets": "préfixes imbriqués de ratings.csv, le plus grand est le fichier entier",
            "catalogue": "les 10 000 livres sont chargés dans chaque sous-ensemble",
            "load_time": "insertions puis index, un seul passage",
            "services": "F2 puis F1, un appel d'échauffement non compté, puis 5 mesures",
            "same_requests": True,
        },
        "subsets": loaded,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def load_subset(client, name: str, limit: int | None, books: list[dict]) -> dict:
    client.drop_database(name)
    database = client[name]
    started = time.perf_counter()
    database.books.insert_many((dict(book) for book in books), ordered=False)
    users: set[int] = set()
    book_ids: set[int] = set()
    batch: list[dict] = []
    total = 0
    with RAW.open(encoding="utf-8", newline="") as handle:
        for source_row, row in enumerate(csv.DictReader(handle), start=1):
            if limit is not None and source_row > limit:
                break
            user_id = int(row["user_id"])
            book_id = int(row["book_id"])
            users.add(user_id)
            book_ids.add(book_id)
            batch.append(
                {
                    "user_id": user_id,
                    "book_id": book_id,
                    "rating": int(row["rating"]),
                    "source_row": source_row,
                }
            )
            if len(batch) >= BATCH:
                database.ratings.insert_many(batch, ordered=False)
                total += len(batch)
                batch.clear()
                if total % 200_000 == 0:
                    print(f"  {total} notes", flush=True)
    if batch:
        database.ratings.insert_many(batch, ordered=False)
        total += len(batch)
    database.books.create_index([("book_id", ASCENDING)], unique=True)
    database.books.create_index([("goodreads_book_id", ASCENDING)], unique=True)
    database.books.create_index([("title", ASCENDING)])
    database.books.create_index([("tags.name", ASCENDING)])
    database.ratings.create_index([("user_id", ASCENDING), ("book_id", ASCENDING)], unique=True)
    database.ratings.create_index([("book_id", ASCENDING)])
    elapsed = time.perf_counter() - started
    stats = database.command("dbStats")
    print(f"  {name}: {total} notes en {elapsed:.1f} s", flush=True)
    return {
        "database": name,
        "rating_limit": limit,
        "readers": len(users),
        "books_in_catalogue": len(books),
        "books_with_ratings": len(book_ids),
        "ratings": total,
        "load_seconds": round(elapsed, 3),
        "storage_bytes": {
            "data": stats["dataSize"],
            "indexes": stats["indexSize"],
            "storage": stats["storageSize"],
        },
    }


def top_users(database) -> tuple[int, int]:
    rows = list(
        database.ratings.aggregate(
            [
                {"$group": {"_id": "$user_id", "n": {"$sum": 1}}},
                {"$sort": {"n": -1, "_id": 1}},
                {"$limit": 2},
            ]
        )
    )
    return rows[0]["_id"], rows[1]["_id"]


def top_book(database) -> int:
    rows = list(
        database.ratings.aggregate(
            [
                {"$group": {"_id": "$book_id", "n": {"$sum": 1}}},
                {"$sort": {"n": -1, "_id": 1}},
                {"$limit": 1},
            ]
        )
    )
    return rows[0]["_id"]


def time_call(fn, marker: str) -> dict:
    warmed = fn()
    samples = []
    result = warmed
    for _ in range(REPEATS):
        started = time.perf_counter()
        result = fn()
        samples.append((time.perf_counter() - started) * 1000)
    summary = {"status": result.get("status")}
    if marker == "shared_books":
        summary["shared_books"] = result.get("shared_books")
        summary["ratings_used"] = [
            result.get("reader_a", {}).get("ratings_used"),
            result.get("reader_b", {}).get("ratings_used"),
        ]
    else:
        book = result.get("book") or {}
        summary["ratings_in_selection"] = book.get("ratings_in_selection")
    return {
        "warmup": "one untimed call",
        "repeats": REPEATS,
        "median_ms": round(statistics.median(samples), 3),
        "min_ms": round(min(samples), 3),
        "max_ms": round(max(samples), 3),
        "samples_ms": [round(sample, 3) for sample in samples],
        "result": summary,
    }


if __name__ == "__main__":
    main()
