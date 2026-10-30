"""Load the Goodbooks-10k CSV files into the local MongoDB server.

The upstream README says that ratings.book_id matches books.work_id.
Checked on these files, that join matches nothing useful:
ratings.book_id and to_read.book_id match books.book_id (1..10000).
book_tags.goodreads_book_id matches books.goodreads_book_id.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

from pymongo import MongoClient, ASCENDING

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = os.environ.get("MONGO_DB", "bookscope")
BATCH = 20_000


def blank_to_none(value: str) -> str | None:
    value = value.strip()
    return value or None


def to_int(value: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    return int(float(value))


def to_float(value: str) -> float | None:
    value = value.strip()
    if not value:
        return None
    return float(value)


def read_tags() -> dict[int, str]:
    tags: dict[int, str] = {}
    with (RAW / "tags.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            tags[int(row["tag_id"])] = row["tag_name"]
    return tags


def read_book_tags(tags: dict[int, str]) -> dict[int, list[dict]]:
    by_goodreads: dict[int, list[dict]] = {}
    with (RAW / "book_tags.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            goodreads_book_id = int(row["goodreads_book_id"])
            by_goodreads.setdefault(goodreads_book_id, []).append(
                {
                    "tag_id": int(row["tag_id"]),
                    "name": tags[int(row["tag_id"])],
                    "count": int(row["count"]),
                }
            )
    for items in by_goodreads.values():
        items.sort(key=lambda item: item["count"], reverse=True)
    return by_goodreads


def book_document(row: dict[str, str], book_tags: dict[int, list[dict]]) -> dict:
    goodreads_book_id = int(row["goodreads_book_id"])
    return {
        "book_id": int(row["book_id"]),
        "goodreads_book_id": goodreads_book_id,
        "best_book_id": to_int(row["best_book_id"]),
        "work_id": to_int(row["work_id"]),
        "books_count": to_int(row["books_count"]),
        "isbn": blank_to_none(row["isbn"]),
        "isbn13": blank_to_none(row["isbn13"]),
        "authors": blank_to_none(row["authors"]),
        "original_publication_year": to_int(row["original_publication_year"]),
        "original_title": blank_to_none(row["original_title"]),
        "title": row["title"],
        "language_code": blank_to_none(row["language_code"]),
        "average_rating": to_float(row["average_rating"]),
        "ratings_count": to_int(row["ratings_count"]),
        "work_ratings_count": to_int(row["work_ratings_count"]),
        "work_text_reviews_count": to_int(row["work_text_reviews_count"]),
        "rating_histogram": {
            "1": to_int(row["ratings_1"]),
            "2": to_int(row["ratings_2"]),
            "3": to_int(row["ratings_3"]),
            "4": to_int(row["ratings_4"]),
            "5": to_int(row["ratings_5"]),
        },
        "image_url": blank_to_none(row["image_url"]),
        "small_image_url": blank_to_none(row["small_image_url"]),
        "tags": book_tags.get(goodreads_book_id, []),
    }


def insert_batches(collection, documents, label: str) -> int:
    batch: list[dict] = []
    total = 0
    for document in documents:
        batch.append(document)
        if len(batch) >= BATCH:
            collection.insert_many(batch, ordered=False)
            total += len(batch)
            batch.clear()
            print(f"  {label}: {total}", flush=True)
    if batch:
        collection.insert_many(batch, ordered=False)
        total += len(batch)
    print(f"  {label}: {total} done", flush=True)
    return total


def iter_ratings():
    with (RAW / "ratings.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            yield {
                "user_id": int(row["user_id"]),
                "book_id": int(row["book_id"]),
                "rating": int(row["rating"]),
            }


def iter_to_read():
    with (RAW / "to_read.csv").open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            yield {
                "user_id": int(row["user_id"]),
                "book_id": int(row["book_id"]),
            }


def main() -> None:
    client = MongoClient(MONGO_URI)
    client.admin.command("ping")
    database = client[DB_NAME]
    client.drop_database(DB_NAME)
    print(f"loading into {MONGO_URI} / {DB_NAME}", flush=True)

    tags = read_tags()
    book_tags = read_book_tags(tags)
    with (RAW / "books.csv").open(encoding="utf-8", newline="") as handle:
        books = [book_document(row, book_tags) for row in csv.DictReader(handle)]
    database.books.insert_many(books, ordered=False)
    print(f"  books: {len(books)} done", flush=True)

    insert_batches(database.ratings, iter_ratings(), "ratings")
    insert_batches(database.to_read, iter_to_read(), "to_read")

    print("creating indexes", flush=True)
    database.books.create_index([("book_id", ASCENDING)], unique=True)
    database.books.create_index([("goodreads_book_id", ASCENDING)], unique=True)
    database.books.create_index([("title", ASCENDING)])
    database.books.create_index([("tags.name", ASCENDING)])
    database.ratings.create_index([("user_id", ASCENDING), ("book_id", ASCENDING)], unique=True)
    database.ratings.create_index([("book_id", ASCENDING)])
    database.to_read.create_index([("user_id", ASCENDING), ("book_id", ASCENDING)], unique=True)
    database.to_read.create_index([("book_id", ASCENDING)])

    print(
        {
            "books": database.books.count_documents({}),
            "ratings": database.ratings.count_documents({}),
            "to_read": database.to_read.count_documents({}),
        }
    )


if __name__ == "__main__":
    main()
