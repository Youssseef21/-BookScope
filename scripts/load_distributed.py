"""Copy the application database onto two MongoDB instances.

Ratings are not copied twice. Books and the to-read list are.

    python scripts/load_distributed.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pymongo import ASCENDING, MongoClient

from bookscope.cluster import INSTANCE_A, INSTANCE_B
from bookscope.db import connect

BATCH = 5_000
DATABASES = ("dist_reader", "dist_book")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ensure_instances()
    source = connect()
    clients = {
        "A": MongoClient(INSTANCE_A, serverSelectionTimeoutMS=5000),
        "B": MongoClient(INSTANCE_B, serverSelectionTimeoutMS=5000),
    }
    for client in clients.values():
        client.admin.command("ping")
        for name in DATABASES:
            client.drop_database(name)
    print("livres et listes à lire copiés sur les deux instances", flush=True)
    copy_replicas(source, clients)
    print("notes réparties", flush=True)
    reader_counts, book_counts = copy_ratings(source, clients)
    index(clients)
    expected = source.ratings.count_documents({})
    print(f"notes sur le serveur unique : {expected}", flush=True)
    print(f"par lecteur : A {reader_counts['A']}, B {reader_counts['B']}", flush=True)
    print(f"par livre : A {book_counts['A']}, B {book_counts['B']}", flush=True)
    if reader_counts["A"] + reader_counts["B"] != expected:
        raise RuntimeError("la répartition par lecteur a perdu ou copié des notes")
    if book_counts["A"] + book_counts["B"] != expected:
        raise RuntimeError("la répartition par livre a perdu ou copié des notes")
    print("répartition terminée, sans copie complète des notes", flush=True)


def ensure_instances() -> None:
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        raise RuntimeError("Docker ne répond pas")
    running = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}"], capture_output=True, text=True)
    names = set(running.stdout.split())
    for name, port, volume in (
        ("bookscope-a", "27018", "bookscope-a-data"),
        ("bookscope-b", "27019", "bookscope-b-data"),
    ):
        if name not in names:
            subprocess.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    name,
                    "-p",
                    f"{port}:27017",
                    "-v",
                    f"{volume}:/data/db",
                    "mongo:7",
                ],
                check=True,
                capture_output=True,
            )
        else:
            subprocess.run(["docker", "start", name], check=True, capture_output=True)
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            MongoClient(INSTANCE_A, serverSelectionTimeoutMS=2000).admin.command("ping")
            MongoClient(INSTANCE_B, serverSelectionTimeoutMS=2000).admin.command("ping")
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError("les deux instances ne répondent pas")


def copy_replicas(source, clients) -> None:
    books = list(source.books.find({}, {"_id": 0}))
    for client in clients.values():
        for name in DATABASES:
            client[name].books.insert_many(dict(book) for book in books)
    batch: list[dict] = []
    for row in source.to_read.find({}, {"_id": 0}):
        batch.append(row)
        if len(batch) >= BATCH:
            _insert_both(clients, "to_read", batch)
            batch = []
    if batch:
        _insert_both(clients, "to_read", batch)


def _insert_both(clients, collection: str, rows: list[dict]) -> None:
    for client in clients.values():
        for name in DATABASES:
            client[name][collection].insert_many(dict(row) for row in rows)


def copy_ratings(source, clients) -> tuple[dict[str, int], dict[str, int]]:
    reader = {"A": 0, "B": 0}
    book = {"A": 0, "B": 0}
    buckets = {key: [] for key in ("reader-A", "reader-B", "book-A", "book-B")}
    seen = 0
    for row in source.ratings.find({}, {"_id": 0, "user_id": 1, "book_id": 1, "rating": 1, "source_row": 1}):
        reader_side = "A" if row["user_id"] % 2 == 0 else "B"
        book_side = "A" if row["book_id"] % 2 == 0 else "B"
        buckets[f"reader-{reader_side}"].append(row)
        buckets[f"book-{book_side}"].append(dict(row))
        reader[reader_side] += 1
        book[book_side] += 1
        seen += 1
        if len(buckets["reader-A"]) >= BATCH:
            _flush(clients, buckets)
        if seen % 200_000 == 0:
            print(f"  {seen} notes lues", flush=True)
    _flush(clients, buckets)
    return reader, book


def _flush(clients, buckets: dict[str, list]) -> None:
    for key, rows in buckets.items():
        if not rows:
            continue
        placement, side = key.split("-")
        database = "dist_reader" if placement == "reader" else "dist_book"
        clients[side][database].ratings.insert_many(rows)
        buckets[key] = []


def index(clients) -> None:
    print("index", flush=True)
    for client in clients.values():
        for name in DATABASES:
            database = client[name]
            database.books.create_index([("book_id", ASCENDING)], unique=True)
            database.books.create_index([("goodreads_book_id", ASCENDING)], unique=True)
            database.books.create_index([("title", ASCENDING)])
            database.books.create_index([("tags.name", ASCENDING)])
            database.ratings.create_index([("user_id", ASCENDING), ("book_id", ASCENDING)], unique=True)
            database.ratings.create_index([("book_id", ASCENDING)])
            database.to_read.create_index([("user_id", ASCENDING), ("book_id", ASCENDING)], unique=True)


if __name__ == "__main__":
    main()
