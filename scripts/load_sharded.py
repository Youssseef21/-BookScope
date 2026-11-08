"""Copy the application database into the sharded cluster.

dist_reader keeps every note of a reader on one shard (hashed user_id).
dist_book keeps every note of a book on one shard (hashed book_id).
Books and the to-read list stay on shard1. bookscope_app is not modified.

    python scripts/load_sharded.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pymongo import ASCENDING, MongoClient

from bookscope.db import connect
from bookscope.sharded import MONGOS_URI, SHARDS

BATCH = 5_000
PLACEMENTS = (
    ("dist_reader", {"user_id": "hashed"}),
    ("dist_book", {"book_id": "hashed"}),
)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    source = connect()
    router = MongoClient(MONGOS_URI, serverSelectionTimeoutMS=8000)
    router.admin.command("ping")
    expected = source.ratings.count_documents({})
    for name, key in PLACEMENTS:
        database = router[name]
        if database.ratings.estimated_document_count() == expected and database.books.estimated_document_count() == 10000:
            print(f"{name} déjà chargée ({expected} notes)", flush=True)
            continue
        print(f"chargement de {name}", flush=True)
        router.drop_database(name)
        enable(router, name, key)
        copy_catalogue(source, database)
        copy_ratings(source, database)
        index(database)
        stored = database.ratings.count_documents({})
        print(f"  {name} : {stored} notes", flush=True)
        if stored != expected:
            raise RuntimeError(f"{name} a {stored} notes au lieu de {expected}")
    print("répartition par fragment", flush=True)
    for label, uri in SHARDS:
        client = MongoClient(uri, directConnection=True, serverSelectionTimeoutMS=8000)
        for name, _key in PLACEMENTS:
            count = client[name].ratings.count_documents({})
            print(f"  {label} {name} {count}", flush=True)


def copy_catalogue(source, database) -> None:
    books = list(source.books.find({}, {"_id": 0}))
    database.books.insert_many(books, ordered=False)
    batch: list[dict] = []
    copied = 0
    for row in source.to_read.find({}, {"_id": 0}):
        batch.append(row)
        if len(batch) >= BATCH:
            database.to_read.insert_many(batch, ordered=False)
            copied += len(batch)
            batch = []
    if batch:
        database.to_read.insert_many(batch, ordered=False)
        copied += len(batch)
    print(f"  livres {len(books)}, à lire {copied}", flush=True)


def enable(router: MongoClient, name: str, key: dict) -> None:
    router.admin.command({"enableSharding": name, "primaryShard": "shard1"})
    router.admin.command("shardCollection", f"{name}.ratings", key=key)


def copy_ratings(source, database) -> None:
    batch: list[dict] = []
    seen = 0
    for row in source.ratings.find(
        {},
        {"_id": 0, "user_id": 1, "book_id": 1, "rating": 1, "source_row": 1},
    ):
        batch.append(row)
        seen += 1
        if len(batch) >= BATCH:
            database.ratings.insert_many(batch, ordered=False)
            batch = []
        if seen % 200_000 == 0:
            print(f"  {seen} notes copiées", flush=True)
    if batch:
        database.ratings.insert_many(batch, ordered=False)


def index(database) -> None:
    database.books.create_index([("book_id", ASCENDING)], unique=True)
    database.books.create_index([("goodreads_book_id", ASCENDING)], unique=True)
    database.books.create_index([("title", ASCENDING)])
    database.books.create_index([("tags.name", ASCENDING)])
    database.ratings.create_index([("user_id", ASCENDING), ("book_id", ASCENDING)])
    database.ratings.create_index([("book_id", ASCENDING)])
    database.to_read.create_index([("user_id", ASCENDING), ("book_id", ASCENDING)], unique=True)


if __name__ == "__main__":
    main()
