"""Apply the registered selection and set aside the F6 and E3 ratings.

Reads the Goodbooks CSV files directly. Writes the two held-out files, then
loads every earlier rating of the selected readers into the bookscope_app
database. The full bookscope database is left untouched.
"""

from __future__ import annotations

import csv
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

from pymongo import ASCENDING, MongoClient

import load_raw
import selection_rule as rule

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "selection"
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
APP_DB = os.environ.get("MONGO_APP_DB", "bookscope_app")


def percentile(sorted_values: list[int], percent: float) -> int:
    index = round((percent / 100) * (len(sorted_values) - 1))
    return sorted_values[index]


def summarize(values: list[int]) -> dict:
    ordered = sorted(values)
    return {
        "min": ordered[0],
        "p50": percentile(ordered, 50),
        "p90": percentile(ordered, 90),
        "max": ordered[-1],
    }


def mystery_book_ids() -> set[int]:
    tags = load_raw.read_tags()
    by_goodreads = load_raw.read_book_tags(tags)
    goodreads_to_book: dict[int, int] = {}
    with (RAW / "books.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            goodreads_to_book[int(row["goodreads_book_id"])] = int(row["book_id"])

    selected: set[int] = set()
    for goodreads_book_id, items in by_goodreads.items():
        if any(
            item["name"] in rule.MYSTERY_TAGS and item["count"] >= rule.MIN_TAG_COUNT
            for item in items
        ):
            book_id = goodreads_to_book.get(goodreads_book_id)
            if book_id is not None:
                selected.add(book_id)
    return selected


def count_readers(mystery_books: set[int]) -> tuple[dict[int, int], dict[int, int]]:
    mystery_ratings: dict[int, int] = defaultdict(int)
    all_ratings: dict[int, int] = defaultdict(int)
    with (RAW / "ratings.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            user_id = int(row["user_id"])
            all_ratings[user_id] += 1
            if int(row["book_id"]) in mystery_books:
                mystery_ratings[user_id] += 1
    return all_ratings, mystery_ratings


def selected_users(all_ratings: dict[int, int], mystery_ratings: dict[int, int]) -> set[int]:
    qualified = {
        user_id
        for user_id, total in all_ratings.items()
        if user_id % rule.USER_MOD in rule.USER_REMAINDERS
        and mystery_ratings[user_id] >= rule.MIN_MYSTERY_RATINGS
    }
    too_short = [
        user_id
        for user_id in qualified
        if all_ratings[user_id] < rule.E3_TAIL + rule.F6_BEFORE
    ]
    if too_short:
        raise RuntimeError(f"{len(too_short)} selected readers have fewer than 10 ratings")
    return qualified


def split_ratings(users: set[int]) -> tuple[list[tuple], list[tuple], list[tuple]]:
    """Return working, F6 and E3 rows as (user_id, book_id, rating, source_row)."""
    by_user: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    with (RAW / "ratings.csv").open(encoding="utf-8", newline="") as handle:
        for source_row, row in enumerate(csv.DictReader(handle), start=1):
            user_id = int(row["user_id"])
            if user_id in users:
                by_user[user_id].append(
                    (source_row, int(row["book_id"]), int(row["rating"]))
                )

    working: list[tuple] = []
    f6_rows: list[tuple] = []
    e3_rows: list[tuple] = []
    tail = rule.E3_TAIL + rule.F6_BEFORE
    for user_id, history in by_user.items():
        if len(history) < tail:
            raise RuntimeError(f"user {user_id} has only {len(history)} ratings")
        older = history[:-tail]
        update = history[-tail : -rule.E3_TAIL]
        evaluation = history[-rule.E3_TAIL :]
        working.extend((user_id, book_id, rating, source_row) for source_row, book_id, rating in older)
        f6_rows.extend((user_id, book_id, rating, source_row) for source_row, book_id, rating in update)
        e3_rows.extend(
            (user_id, book_id, rating, source_row) for source_row, book_id, rating in evaluation
        )
    return working, f6_rows, e3_rows


def write_ratings(path: Path, rows: list[tuple]) -> None:
    ordered = sorted(rows, key=lambda item: item[3])
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["user_id", "book_id", "rating", "source_row"])
        writer.writerows(ordered)


def kept_to_read(users: set[int], held_out: set[tuple[int, int]]) -> tuple[list[tuple], int]:
    kept: list[tuple] = []
    dropped = 0
    with (RAW / "to_read.csv").open(encoding="utf-8", newline="") as handle:
        for source_row, row in enumerate(csv.DictReader(handle), start=1):
            user_id = int(row["user_id"])
            book_id = int(row["book_id"])
            if user_id not in users:
                continue
            if (user_id, book_id) in held_out:
                dropped += 1
                continue
            kept.append((user_id, book_id, source_row))
    return kept, dropped


def assert_disjoint(working: list[tuple], f6_rows: list[tuple], e3_rows: list[tuple]) -> None:
    def pairs(rows: list[tuple]) -> set[tuple[int, int]]:
        return {(user_id, book_id) for user_id, book_id, _rating, _source_row in rows}

    working_pairs = pairs(working)
    f6_pairs = pairs(f6_rows)
    e3_pairs = pairs(e3_rows)
    if working_pairs & f6_pairs or working_pairs & e3_pairs or f6_pairs & e3_pairs:
        raise RuntimeError("held-out ratings overlap the loaded ratings or each other")
    if len(working_pairs) != len(working) or len(f6_pairs) != len(f6_rows) or len(e3_pairs) != len(e3_rows):
        raise RuntimeError("a reader has two ratings for the same book inside one split")


def load_app(book_ids: set[int], working: list[tuple], to_read_rows: list[tuple], summary: dict) -> None:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    client.drop_database(APP_DB)
    database = client[APP_DB]
    print(f"loading into {MONGO_URI} / {APP_DB}", flush=True)

    tags = load_raw.read_tags()
    book_tags = load_raw.read_book_tags(tags)
    with (RAW / "books.csv").open(encoding="utf-8", newline="") as handle:
        books = [
            load_raw.book_document(row, book_tags)
            for row in csv.DictReader(handle)
            if int(row["book_id"]) in book_ids
        ]
    database.books.insert_many(books, ordered=False)
    print(f"  books: {len(books)} done", flush=True)

    load_raw.insert_batches(
        database.ratings,
        (
            {
                "user_id": user_id,
                "book_id": book_id,
                "rating": rating,
                "source_row": source_row,
            }
            for user_id, book_id, rating, source_row in working
        ),
        "ratings",
    )
    load_raw.insert_batches(
        database.to_read,
        (
            {"user_id": user_id, "book_id": book_id, "source_row": source_row}
            for user_id, book_id, source_row in to_read_rows
        ),
        "to_read",
    )

    print("creating indexes", flush=True)
    database.books.create_index([("book_id", ASCENDING)], unique=True)
    database.books.create_index([("goodreads_book_id", ASCENDING)], unique=True)
    database.books.create_index([("title", ASCENDING)])
    database.books.create_index([("tags.name", ASCENDING)])
    database.ratings.create_index([("user_id", ASCENDING), ("book_id", ASCENDING)], unique=True)
    database.ratings.create_index([("book_id", ASCENDING)])
    database.ratings.create_index([("source_row", ASCENDING)])
    database.to_read.create_index([("user_id", ASCENDING), ("book_id", ASCENDING)], unique=True)
    database.to_read.create_index([("book_id", ASCENDING)])
    database.meta.insert_one({"_id": "selection", **summary})

    counts = {
        "books": database.books.count_documents({}),
        "ratings": database.ratings.count_documents({}),
        "to_read": database.to_read.count_documents({}),
    }
    if counts["ratings"] != summary["working_ratings"]:
        raise RuntimeError(f"loaded ratings {counts['ratings']} != {summary['working_ratings']}")
    print(counts)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("finding mystery-family books", flush=True)
    mystery_books = mystery_book_ids()
    print(f"  mystery books: {len(mystery_books)}", flush=True)

    print("counting readers", flush=True)
    all_ratings, mystery_ratings = count_readers(mystery_books)
    users = selected_users(all_ratings, mystery_ratings)
    print(f"  selected readers: {len(users)}", flush=True)

    print("splitting ratings by file order", flush=True)
    working, f6_rows, e3_rows = split_ratings(users)
    assert_disjoint(working, f6_rows, e3_rows)
    held_out = {(user_id, book_id) for user_id, book_id, _rating, _source_row in f6_rows + e3_rows}

    print("filtering to-read lists", flush=True)
    to_read_rows, dropped_to_read = kept_to_read(users, held_out)

    book_ids = {book_id for _user_id, book_id, _rating, _source_row in working + f6_rows + e3_rows}
    book_ids.update(book_id for _user_id, book_id, _source_row in to_read_rows)

    selection_user_counts = [all_ratings[user_id] for user_id in users]
    working_user_counts = Counter(user_id for user_id, _book_id, _rating, _source_row in working)
    selection_book_counts = Counter(
        book_id for _user_id, book_id, _rating, _source_row in working + f6_rows + e3_rows
    )
    mystery_on_selected = sum(mystery_ratings[user_id] for user_id in users)

    summary = {
        "rule": {
            "mystery_tags": sorted(rule.MYSTERY_TAGS),
            "min_tag_count": rule.MIN_TAG_COUNT,
            "user_id_mod": rule.USER_MOD,
            "user_id_remainders": sorted(rule.USER_REMAINDERS),
            "min_mystery_ratings": rule.MIN_MYSTERY_RATINGS,
            "keeps": "all ratings of each selected reader",
        },
        "time_order": "source_row is the position in ratings.csv. The file has no dates; this order is the relative time order.",
        "e3_policy": f"last {rule.E3_TAIL} ratings of each selected reader",
        "f6_policy": f"{rule.F6_BEFORE} ratings immediately before the E3 tail of each selected reader",
        "to_read_policy": "to-read rows of selected readers, except a pair withheld in E3 or F6",
        "mystery_books": len(mystery_books),
        "readers": len(users),
        "selection_ratings": len(working) + len(f6_rows) + len(e3_rows),
        "mystery_ratings_of_selected_readers": mystery_on_selected,
        "e3_ratings": len(e3_rows),
        "f6_ratings": len(f6_rows),
        "working_ratings": len(working),
        "books_referenced": len(book_ids),
        "to_read_kept": len(to_read_rows),
        "to_read_dropped_as_holdout_pairs": dropped_to_read,
        "ratings_per_reader_in_selection": summarize(selection_user_counts),
        "ratings_per_reader_loaded": summarize(list(working_user_counts.values())),
        "ratings_per_book_in_selection": summarize(list(selection_book_counts.values())),
    }
    if summary["working_ratings"] < 1_000_000:
        raise RuntimeError("the loaded subset has fewer than one million ratings")

    write_ratings(OUT / "e3_eval.csv", e3_rows)
    write_ratings(OUT / "f6_batch.csv", f6_rows)
    with (OUT / "readers.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["user_id", "selection_ratings", "mystery_ratings", "loaded_ratings"])
        for user_id in sorted(users):
            writer.writerow(
                [
                    user_id,
                    all_ratings[user_id],
                    mystery_ratings[user_id],
                    working_user_counts[user_id],
                ]
            )
    with (OUT / "mystery_books.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["book_id"])
        writer.writerows([book_id] for book_id in sorted(mystery_books))
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)

    load_app(book_ids, working, to_read_rows, summary)


if __name__ == "__main__":
    main()
