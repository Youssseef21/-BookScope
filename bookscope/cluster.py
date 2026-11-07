"""Two MongoDB instances on this machine, with separate storage.

This is not a cluster of machines. Each instance is one mongod process,
with its own port and its own data volume. Ratings are split. A rating is
stored on one instance only. Books and the to-read list are copied on both,
because they are catalogue data, not ratings.

Placements
    by_reader: every rating of a reader stays on one instance (user_id even
    on A, odd on B). A book is therefore split across the two instances.
    by_book: every rating of a book stays on one instance (book_id even on
    A, odd on B). A reader is therefore split across the two instances.
"""

from __future__ import annotations

from pymongo import MongoClient
from pymongo.collection import Collection

INSTANCE_A = "mongodb://localhost:27018"
INSTANCE_B = "mongodb://localhost:27019"
PLACEMENTS = ("by_reader", "by_book")


class Incomplete(RuntimeError):
    """Une instance nécessaire n'a pas répondu.

    On lève cette erreur avant de renvoyer quoi que ce soit.
    Une réponse calculée seulement avec l'autre instance serait incomplète,
    et elle ne doit pas être présentée comme un résultat.
    """

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__("instance indisponible : " + ", ".join(missing))


class Instance:
    def __init__(self, name: str, endpoint: str, uri: str, database: str):
        self.name = name
        self.endpoint = endpoint
        self.uri = uri
        self.client = MongoClient(uri, serverSelectionTimeoutMS=2000, connectTimeoutMS=2000)
        self.db = self.client[database]
        self.available = False

    def refresh(self) -> bool:
        try:
            self.client.admin.command("ping")
            self.available = True
        except Exception:
            self.available = False
        return self.available


class Cluster:
    def __init__(self, placement: str, suffix: str = ""):
        # suffix selects another copy of the data on the same two instances,
        # for example "_full" for the E2 experiment on every rating.
        if placement not in PLACEMENTS:
            raise ValueError(placement)
        self.placement = placement
        database = ("dist_reader" if placement == "by_reader" else "dist_book") + suffix
        self.instances = {
            "A": Instance("A", "localhost:27018", INSTANCE_A, database),
            "B": Instance("B", "localhost:27019", INSTANCE_B, database),
        }
        self.calls: list[dict] = []
        self.ratings = SplitCollection(self, "ratings", self._rating_key())
        self.rating_conflicts = SplitCollection(self, "rating_conflicts", self._rating_key())
        self.to_read = ReplicaCollection(self, "to_read")
        self.books = ReplicaCollection(self, "books")

    def refresh(self) -> None:
        for instance in self.instances.values():
            instance.refresh()

    def clear(self) -> None:
        self.calls.clear()

    def summary(self) -> dict[str, dict]:
        totals: dict[str, dict] = {}
        for call in self.calls:
            for part in call["parts"]:
                slot = totals.setdefault(
                    part["instance"],
                    {"endpoint": part["endpoint"], "calls": 0, "documents": 0},
                )
                slot["calls"] += 1
                slot["documents"] += part["documents"]
        return totals

    def home(self, user_id: int, book_id: int) -> str:
        # Par lecteur : toutes les notes d'une personne sont sur une seule instance.
        # Par livre : toutes les notes d'un livre sont sur une seule instance.
        # Pair -> A (port 27018), impair -> B (port 27019).
        if self.placement == "by_reader":
            return "A" if user_id % 2 == 0 else "B"
        return "A" if book_id % 2 == 0 else "B"

    def _rating_key(self) -> str:
        return "user_id" if self.placement == "by_reader" else "book_id"


class SplitCollection:
    """A collection whose documents live on exactly one instance."""

    def __init__(self, cluster: Cluster, name: str, key: str):
        self.cluster = cluster
        self.name = name
        self.key = key

    def find(self, filter=None, projection=None, **kwargs):
        filter = filter or {}
        rows = []
        parts = []
        for instance in self._targets(filter):
            local = list(instance.db[self.name].find(filter, projection, **kwargs))
            rows.extend(local)
            parts.append(self._part(instance, len(local)))
        self._record("find", parts)
        return rows

    def count_documents(self, filter=None, **kwargs) -> int:
        filter = filter or {}
        total = 0
        parts = []
        for instance in self._targets(filter):
            count = instance.db[self.name].count_documents(filter, **kwargs)
            total += count
            parts.append(self._part(instance, count))
        self._record("count", parts)
        return total

    def aggregate(self, pipeline, **kwargs):
        prefix, group, suffix = _split_pipeline(pipeline)
        merged_rows = []
        parts = []
        for instance in self._targets(prefix[0]["$match"] if prefix else {}):
            local = list(instance.db[self.name].aggregate([*prefix, {"$group": group}], allowDiskUse=True))
            merged_rows.extend(local)
            parts.append(self._part(instance, len(local)))
        self._record("aggregate", parts)
        return iter(_apply_suffix(_merge_groups(merged_rows, group), suffix))

    def insert_many(self, documents, **kwargs):
        buckets: dict[str, list] = {"A": [], "B": []}
        for document in documents:
            buckets[self.cluster.home(document["user_id"], document["book_id"])].append(document)
        for name, rows in buckets.items():
            if not rows:
                continue
            instance = self._require([name])[0]
            instance.db[self.name].insert_many(rows, **kwargs)

    def create_index(self, keys, **kwargs):
        for instance in self._require(["A", "B"]):
            instance.db[self.name].create_index(keys, **kwargs)

    def _targets(self, filter: dict) -> list[Instance]:
        names = _routed_names(filter, self.key)
        if names is None:
            names = ["A", "B"]
        return self._require(names)

    def _require(self, names: list[str]) -> list[Instance]:
        missing = []
        chosen = []
        for name in names:
            instance = self.cluster.instances[name]
            if not instance.available:
                missing.append(f"{instance.name} ({instance.endpoint})")
            else:
                chosen.append(instance)
        if missing:
            raise Incomplete(missing)
        return chosen

    def _part(self, instance: Instance, documents: int) -> dict:
        return {"instance": instance.name, "endpoint": instance.endpoint, "documents": documents}

    def _record(self, op: str, parts: list[dict]) -> None:
        self.cluster.calls.append({"op": op, "collection": self.name, "parts": parts})


class ReplicaCollection:
    """A full copy on each instance. One live copy answers."""

    def __init__(self, cluster: Cluster, name: str):
        self.cluster = cluster
        self.name = name

    def find(self, filter=None, projection=None, **kwargs):
        return self._collection().find(filter, projection, **kwargs)

    def find_one(self, filter=None, projection=None, **kwargs):
        return self._collection().find_one(filter, projection, **kwargs)

    def count_documents(self, filter=None, **kwargs) -> int:
        return self._collection().count_documents(filter or {}, **kwargs)

    def aggregate(self, pipeline, **kwargs):
        return self._collection().aggregate(pipeline, **kwargs)

    def insert_many(self, documents, **kwargs):
        copies = [dict(document) for document in documents]
        for instance in self._both():
            instance.db[self.name].insert_many([dict(document) for document in copies], **kwargs)

    def create_index(self, keys, **kwargs):
        for instance in self._both():
            instance.db[self.name].create_index(keys, **kwargs)

    def _collection(self) -> Collection:
        for name in ("A", "B"):
            instance = self.cluster.instances[name]
            if instance.available:
                return instance.db[self.name]
        raise Incomplete(["A (localhost:27018)", "B (localhost:27019)"])

    def _both(self) -> list[Instance]:
        missing = [
            f"{instance.name} ({instance.endpoint})"
            for instance in self.cluster.instances.values()
            if not instance.available
        ]
        if missing:
            raise Incomplete(missing)
        return list(self.cluster.instances.values())


def _routed_names(filter: dict, key: str) -> list[str] | None:
    """Return the instances that can hold these documents, or None when both are required."""
    if not filter or "$or" in filter or "$nor" in filter:
        return None
    identifiers = _exact_ids(filter.get(key))
    if identifiers is None:
        return None
    names = []
    for identifier in identifiers:
        side = "A" if identifier % 2 == 0 else "B"
        if side not in names:
            names.append(side)
    return names


def _exact_ids(value) -> list[int] | None:
    if isinstance(value, int):
        return [value]
    if isinstance(value, dict) and set(value) == {"$in"} and all(isinstance(item, int) for item in value["$in"]):
        return list(value["$in"])
    return None


def _split_pipeline(pipeline: list[dict]) -> tuple[list[dict], dict, list[dict]]:
    group_at = next((index for index, stage in enumerate(pipeline) if "$group" in stage), None)
    if group_at is None:
        raise NotImplementedError(pipeline)
    prefix = pipeline[:group_at]
    if any(set(stage) != {"$match"} for stage in prefix):
        raise NotImplementedError(prefix)
    suffix = pipeline[group_at + 1 :]
    return prefix, pipeline[group_at]["$group"], suffix


def _merge_groups(rows: list[dict], group: dict) -> list[dict]:
    sums = [key for key, expr in group.items() if key != "_id" and isinstance(expr, dict) and "$sum" in expr]
    pushes = [key for key, expr in group.items() if key != "_id" and isinstance(expr, dict) and "$push" in expr]
    unknown = [key for key in group if key != "_id" and key not in sums and key not in pushes]
    if unknown:
        raise NotImplementedError(unknown)
    merged: dict = {}
    for row in rows:
        bucket = merged.setdefault(row["_id"], {"_id": row["_id"]})
        for key in sums:
            bucket[key] = bucket.get(key, 0) + row.get(key, 0)
        for key in pushes:
            bucket.setdefault(key, []).extend(row.get(key, []))
    return list(merged.values())


def _apply_suffix(rows: list[dict], stages: list[dict]) -> list[dict]:
    for stage in stages:
        if "$match" in stage:
            rows = [row for row in rows if _keeps(row, stage["$match"])]
        elif "$sort" in stage:
            spec = list(stage["$sort"].items())
            rows = sorted(rows, key=lambda row: tuple(-row[field] if direction < 0 else row[field] for field, direction in spec))
        elif "$limit" in stage:
            rows = rows[: stage["$limit"]]
        else:
            raise NotImplementedError(stage)
    return rows


def _keeps(row: dict, spec: dict) -> bool:
    for field, condition in spec.items():
        if not isinstance(condition, dict) or set(condition) != {"$gte"}:
            raise NotImplementedError(spec)
        if row.get(field, 0) < condition["$gte"]:
            return False
    return True
