"""Compare two ways of storing the same ratings.

Current documents: one rating is one document.
Alternative: one reader is one document, and the ratings are embedded.

The two requests are the ones the services actually run:
a reader's whole history, and the positive ratings of one book.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pymongo import ASCENDING

from bookscope.db import connect

READER_ID = 1
BOOK_ID = 258
REPEATS = 11
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "measurements" / "model_tradeoff.json"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    db = connect()
    print("construction de la collection par lecteur", flush=True)
    started = time.perf_counter()
    build_embedded(db)
    build_seconds = time.perf_counter() - started
    check_same_ratings(db)

    separate_reader = time_query(lambda: read_reader_separate(db))
    embedded_reader = time_query(lambda: read_reader_embedded(db))
    separate_book = time_query(lambda: read_book_separate(db))
    embedded_book = time_query(lambda: read_book_embedded(db))
    if separate_reader["count"] != embedded_reader["count"]:
        raise RuntimeError("les deux modèles ne rendent pas le même historique")
    if separate_book["count"] != embedded_book["count"]:
        raise RuntimeError("les deux modèles ne rendent pas les mêmes notes du livre")

    result = {
        "reader_id": READER_ID,
        "book_id": BOOK_ID,
        "repeats": REPEATS,
        "embedded_build_seconds": round(build_seconds, 3),
        "reader_history": {
            "ratings": separate_reader["count"],
            "separate_median_ms": separate_reader["median_ms"],
            "embedded_median_ms": embedded_reader["median_ms"],
            "separate_samples_ms": separate_reader["samples_ms"],
            "embedded_samples_ms": embedded_reader["samples_ms"],
        },
        "book_positive_ratings": {
            "ratings": separate_book["count"],
            "separate_median_ms": separate_book["median_ms"],
            "embedded_median_ms": embedded_book["median_ms"],
            "separate_samples_ms": separate_book["samples_ms"],
            "embedded_samples_ms": embedded_book["samples_ms"],
        },
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def build_embedded(db) -> None:
    db.ratings.aggregate(
        [
            {
                "$group": {
                    "_id": "$user_id",
                    "ratings": {
                        "$push": {
                            "book_id": "$book_id",
                            "rating": "$rating",
                            "source_row": "$source_row",
                        }
                    },
                }
            },
            {"$project": {"_id": 0, "user_id": "$_id", "ratings": 1}},
            {"$out": "readers_embedded"},
        ],
        allowDiskUse=True,
    )
    db.readers_embedded.create_index([("user_id", ASCENDING)], unique=True)
    db.readers_embedded.create_index([("ratings.book_id", ASCENDING)])


def check_same_ratings(db) -> None:
    rows = db.readers_embedded.aggregate(
        [
            {"$project": {"n": {"$size": "$ratings"}}},
            {"$group": {"_id": None, "n": {"$sum": "$n"}}},
        ]
    )
    embedded = next(rows)["n"]
    separate = db.ratings.count_documents({})
    if embedded != separate:
        raise RuntimeError(f"{embedded} notes embarquées, {separate} notes séparées")


def read_reader_separate(db) -> int:
    return len(list(db.ratings.find({"user_id": READER_ID}, {"_id": 0})))


def read_reader_embedded(db) -> int:
    document = db.readers_embedded.find_one({"user_id": READER_ID}, {"_id": 0, "ratings": 1})
    return len(document["ratings"])


def read_book_separate(db) -> int:
    return len(
        list(
            db.ratings.find(
                {"book_id": BOOK_ID, "rating": {"$gte": 4}},
                {"_id": 0, "user_id": 1, "rating": 1, "source_row": 1},
            )
        )
    )


def read_book_embedded(db) -> int:
    cursor = db.readers_embedded.find(
        {"ratings": {"$elemMatch": {"book_id": BOOK_ID, "rating": {"$gte": 4}}}},
        {"_id": 0, "user_id": 1, "ratings.$": 1},
    )
    return len(list(cursor))


def time_query(fn) -> dict:
    fn()
    samples = []
    count = 0
    for _ in range(REPEATS):
        started = time.perf_counter()
        count = fn()
        samples.append((time.perf_counter() - started) * 1000)
    return {
        "count": count,
        "median_ms": round(statistics.median(samples), 3),
        "samples_ms": [round(sample, 3) for sample in samples],
    }


if __name__ == "__main__":
    main()
