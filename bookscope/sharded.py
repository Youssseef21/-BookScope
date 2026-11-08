"""Sharded cluster behind one mongos address.

The application never opens shard1 or shard2 itself for a user request.
mongos reads the config server, sends the query to the shards that own the
data, and merges the answers. Ratings are hashed across the two shards.
Books stay on shard1.
"""

from __future__ import annotations

import time

from pymongo import MongoClient

MONGOS_URI = "mongodb://127.0.0.1:27200"
PLACEMENTS = ("by_reader", "by_book")


class Incomplete(RuntimeError):
    """Un fragment nécessaire n'a pas répondu.

    On lève cette erreur avant de renvoyer quoi que ce soit.
    Une réponse calculée sans ce fragment serait incomplète,
    et elle ne doit pas être présentée comme un résultat.
    """

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__("instance indisponible : " + ", ".join(missing))


SHARDS = (
    ("shard1", "mongodb://127.0.0.1:27101"),
    ("shard2", "mongodb://127.0.0.1:27102"),
)
DATABASES = {"by_reader": "dist_reader", "by_book": "dist_book"}


class _Guarded:
    """Rating access fails before any result when a shard is down."""

    def __init__(self, collection, require):
        self._collection = collection
        self._require = require

    def __getattr__(self, name):
        self._require()
        return getattr(self._collection, name)


class Placement:
    def __init__(self, placement: str):
        if placement not in PLACEMENTS:
            raise ValueError(placement)
        self.placement = placement
        self.client = MongoClient(MONGOS_URI, serverSelectionTimeoutMS=5000)
        self.client.admin.command("ping")
        self.db = self.client[DATABASES[placement]]
        self.books = self.db.books
        self.to_read = self.db.to_read
        self.ratings = _Guarded(self.db.ratings, self.require_shards)
        self.rating_conflicts = _Guarded(self.db.rating_conflicts, self.require_shards)
        self._checked = 0.0
        self._missing: list[str] = []

    def require_shards(self) -> None:
        now = time.monotonic()
        if now - self._checked > 2:
            self._missing = missing_shards()
            self._checked = now
        if self._missing:
            raise Incomplete(self._missing)


def open_placement(placement: str) -> Placement:
    return Placement(placement)


def missing_shards() -> list[str]:
    missing = []
    for name, uri in SHARDS:
        try:
            MongoClient(uri, directConnection=True, serverSelectionTimeoutMS=1500).admin.command("ping")
        except Exception:
            missing.append(f"{name} (localhost:{uri.rsplit(':', 1)[-1]})")
    return missing
