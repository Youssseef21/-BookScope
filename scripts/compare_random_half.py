"""Compare the services on every note and on a random half.

This check is not the sharded cluster. On mongos, F1 to F5 match the full
database. Here, a note is kept only when a fixed draw on (user_id, book_id)
falls on 0, so about half the notes remain and the scores change.

The half is stored in bookscope_half on the same MongoDB as the application
database. bookscope_app is left unchanged. The draw can be repeated.

    python scripts/compare_random_half.py
"""

from __future__ import annotations

import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pymongo import ASCENDING, MongoClient

from bookscope.db import MONGO_URI, connect
from bookscope.f1 import explore
from bookscope.f2 import compare
from bookscope.f3 import recommend

HALF_DB = "bookscope_half"
BATCH = 5_000


def kept(user_id: int, book_id: int) -> bool:
    token = f"{user_id}:{book_id}".encode()
    return zlib.crc32(token) % 2 == 0


def build_half(source, half) -> None:
    half.client.drop_database(HALF_DB)
    print("copie des livres et de la liste à lire", flush=True)
    books = list(source.books.find({}, {"_id": 0}))
    half.books.insert_many(books, ordered=False)
    batch: list[dict] = []
    copied = 0
    for row in source.to_read.find({}, {"_id": 0}):
        batch.append(row)
        if len(batch) >= BATCH:
            half.to_read.insert_many(batch, ordered=False)
            copied += len(batch)
            batch = []
    if batch:
        half.to_read.insert_many(batch, ordered=False)
        copied += len(batch)
    print(f"  livres {len(books)}, à lire {copied}", flush=True)

    print("tirage d'environ la moitié des notes", flush=True)
    batch = []
    seen = 0
    kept_count = 0
    for row in source.ratings.find(
        {},
        {"_id": 0, "user_id": 1, "book_id": 1, "rating": 1, "source_row": 1},
    ):
        seen += 1
        if kept(row["user_id"], row["book_id"]):
            batch.append(row)
            kept_count += 1
        if len(batch) >= BATCH:
            half.ratings.insert_many(batch, ordered=False)
            batch = []
        if seen % 200_000 == 0:
            print(f"  {seen} notes lues, {kept_count} gardées", flush=True)
    if batch:
        half.ratings.insert_many(batch, ordered=False)
    print(f"  {kept_count} notes gardées sur {seen}", flush=True)

    print("index", flush=True)
    half.books.create_index([("book_id", ASCENDING)], unique=True)
    half.books.create_index([("goodreads_book_id", ASCENDING)], unique=True)
    half.books.create_index([("title", ASCENDING)])
    half.books.create_index([("tags.name", ASCENDING)])
    half.ratings.create_index([("user_id", ASCENDING), ("book_id", ASCENDING)], unique=True)
    half.ratings.create_index([("book_id", ASCENDING)])
    half.to_read.create_index([("user_id", ASCENDING), ("book_id", ASCENDING)], unique=True)


def link_1467(result: dict) -> dict | None:
    for row in result["connections"]:
        if row.get("kind") == "shared_readership" and row["book_id"] == 1467:
            return row
    return None


def first_recommendation(result: dict) -> str:
    rows = result["recommendations"]
    if not rows:
        return "aucune"
    row = rows[0]
    return f"{row['book_id']} score {row['score']} ({result['mode']})"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    source = connect()
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    half = client[HALF_DB]
    build_half(source, half)

    print("lecteur 1 :", source.ratings.count_documents({"user_id": 1}), "notes, moitié", half.ratings.count_documents({"user_id": 1}))
    print("livre 258, notes >= 4 :", source.ratings.count_documents({"book_id": 258, "rating": {"$gte": 4}}), "et", half.ratings.count_documents({"book_id": 258, "rating": {"$gte": 4}}))

    for label, users in (("F2 lecteurs 1 et 2", (1, 2)), ("F2 lecteurs 1 et 6", (1, 6))):
        full = compare(source, *users)
        part = compare(half, *users)
        print(
            f"{label} : force {full['strength']} avec toutes les notes, "
            f"{part['strength']} avec la moitié "
            f"(communs {full['shared_books']} puis {part['shared_books']})"
        )

    print("F1 livre 258", flush=True)
    full_f1 = explore(source, 258)
    part_f1 = explore(half, 258)
    full_link = link_1467(full_f1)
    part_link = link_1467(part_f1)
    print(
        "  lien 258-1467 :",
        f"force {full_link['strength']}, {full_link['shared_readers']} communs"
        if full_link
        else "absent",
        "puis",
        f"force {part_link['strength']}, {part_link['shared_readers']} communs"
        if part_link
        else "absent de la moitié",
    )

    print("F3 lecteur 1", flush=True)
    full_f3 = recommend(source, 1)
    part_f3 = recommend(half, 1)
    print("  première proposition :", first_recommendation(full_f3), "puis", first_recommendation(part_f3))


if __name__ == "__main__":
    main()
