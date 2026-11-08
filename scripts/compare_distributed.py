"""Compare the main database with the sharded cluster.

    python scripts/compare_distributed.py by_reader
    python scripts/compare_distributed.py by_book
    python scripts/compare_distributed.py by_book --down
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pymongo import MongoClient

from bookscope.sharded import DATABASES, SHARDS, Incomplete, open_placement
from bookscope.db import connect
from bookscope.f1 import explore
from bookscope.f2 import compare
from bookscope.f3 import recommend
from bookscope.f4 import trace
from bookscope.f5 import link_interests
from bookscope.f6 import import_file

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "measurements" / "distributed.json"
BATCH = ROOT / "data" / "selection" / "f6_batch.csv"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Comparer la base principale et le cluster fragmenté")
    parser.add_argument("placement", choices=("by_reader", "by_book"))
    parser.add_argument("--down", action="store_true")
    args = parser.parse_args()
    placement = open_placement(args.placement)
    if args.down:
        show_incomplete(placement)
        return
    single = connect()
    report = {
        "placement": args.placement,
        "note": "Cluster fragmenté : mongos sur 27200, shard1 sur 27101, shard2 sur 27102, configuration sur 27100. Plusieurs conteneurs sur un seul ordinateur, pas un groupe de machines. F1 à F5 ont les mêmes scores que la base principale.",
        "local_ratings": local_counts(args.placement),
        "services": {},
    }
    report["services"]["f2"] = check("F2", placement, lambda db: compare(db, 1, 2), single)
    report["services"]["f1"] = check("F1", placement, lambda db: explore(db, 258), single)
    report["services"]["f4"] = check("F4", placement, lambda db: trace(db, 258, 1467), single)
    report["services"]["f3"] = check("F3", placement, lambda db: recommend(db, 1), single)
    report["services"]["f5"] = check("F5", placement, lambda db: link_interests(db, "mystery", "historical-fiction"), single)
    if args.placement == "by_reader":
        imported = import_file(placement, BATCH)
        report["f6"] = {
            "inserted": imported["inserted"],
            "already_present": imported["already_present"],
            "rejected": imported["rejected"],
        }
        print(
            f"F6 via mongos : {imported['inserted']} ajoutées, "
            f"{imported['already_present']} déjà présentes",
            flush=True,
        )
        if imported["inserted"] != 0 or imported["rejected"] != 0:
            raise RuntimeError("le second import des notes déjà réparties a modifié la base")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    previous = {}
    if OUTPUT.exists():
        previous = json.loads(OUTPUT.read_text(encoding="utf-8"))
    previous[args.placement] = report
    OUTPUT.write_text(json.dumps(previous, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def check(label: str, placement, service, single) -> dict:
    print(label, flush=True)
    expected = service(single)
    actual = service(placement)
    if actual != expected:
        raise RuntimeError(f"{label} diffère du serveur unique : {_first_difference(expected, actual)}")
    print("  identique", flush=True)
    return {"equal_to_single_server": True}


def show_incomplete(placement) -> None:
    print("fragment arrêté : une réponse partielle ne doit pas être présentée comme complète", flush=True)
    refused = {"ratings_reached": reachable_reader_counts(placement.placement)}
    for label, service in (
        ("F2", lambda: compare(placement, 1, 2)),
        ("F3", lambda: recommend(placement, 1)),
    ):
        try:
            result = service()
        except Incomplete as error:
            print(f"{label} incomplet : {error}", flush=True)
            refused[label] = {"complete": False, "missing": error.missing}
            continue
        raise RuntimeError(f"{label} a répondu {result.get('status')} alors qu'un fragment manque")
    book = placement.books.find_one({"book_id": 258}, {"_id": 0, "book_id": 1, "title": 1})
    print(f"fiche du livre 258 (catalogue sur shard1) : {book}", flush=True)
    refused["catalogue_lookup_book_258"] = {
        "complete": True,
        "answer": book,
        "why": "les livres restent sur shard1",
    }
    previous = read_output()
    previous[f"{placement.placement}_one_shard_stopped"] = refused
    write_output(previous)


def reachable_reader_counts(placement: str) -> dict:
    counts = {}
    database = DATABASES[placement]
    for name, uri in SHARDS:
        try:
            client = MongoClient(uri, directConnection=True, serverSelectionTimeoutMS=1500)
            counts[name] = {
                "user_1": client[database].ratings.count_documents({"user_id": 1}),
                "user_2": client[database].ratings.count_documents({"user_id": 2}),
            }
        except Exception:
            counts[name] = {"unavailable": True}
    return counts


def local_counts(placement: str) -> dict:
    counts = {}
    database = DATABASES[placement]
    for name, uri in SHARDS:
        client = MongoClient(uri, directConnection=True, serverSelectionTimeoutMS=8000)
        ratings = client[database].ratings
        counts[name] = {
            "endpoint": uri.removeprefix("mongodb://"),
            "ratings": ratings.count_documents({}),
            "user_1": ratings.count_documents({"user_id": 1}),
            "user_2": ratings.count_documents({"user_id": 2}),
            "book_258": ratings.count_documents({"book_id": 258}),
            "book_1467": ratings.count_documents({"book_id": 1467}),
        }
    return counts


def read_output() -> dict:
    if OUTPUT.exists():
        return json.loads(OUTPUT.read_text(encoding="utf-8"))
    return {}


def write_output(payload: dict) -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _first_difference(left, right, path=""):
    if type(left) is not type(right):
        return f"{path}: {type(left).__name__} / {type(right).__name__}"
    if isinstance(left, dict):
        for key in left.keys() | right.keys():
            if key not in left or key not in right:
                return f"{path}.{key} manque"
            found = _first_difference(left[key], right[key], f"{path}.{key}")
            if found:
                return found
        return None
    if isinstance(left, list):
        if len(left) != len(right):
            return f"{path} longueur {len(left)} / {len(right)}"
        for index, (item_left, item_right) in enumerate(zip(left, right)):
            found = _first_difference(item_left, item_right, f"{path}[{index}]")
            if found:
                return found
        return None
    if left != right:
        return f"{path}: {left!r} / {right!r}"
    return None


if __name__ == "__main__":
    main()
