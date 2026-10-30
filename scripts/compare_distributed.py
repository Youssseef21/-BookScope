"""Compare the single server with the two-instance placement.

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

from bookscope.cluster import Cluster, Incomplete
from bookscope.db import connect
from bookscope.f1 import explore
from bookscope.f2 import compare
from bookscope.f3 import recommend
from bookscope.f4 import trace
from bookscope.f6 import import_file

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "measurements" / "distributed.json"
BATCH = ROOT / "data" / "selection" / "f6_batch.csv"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Comparer le serveur unique et deux instances")
    parser.add_argument("placement", choices=("by_reader", "by_book"))
    parser.add_argument("--down", action="store_true")
    args = parser.parse_args()
    cluster = Cluster(args.placement)
    cluster.refresh()
    if args.down:
        show_incomplete(cluster)
        return
    single = connect()
    report = {
        "placement": args.placement,
        "note": "Deux processus mongod sur cette machine, pas un cluster de machines.",
        "local_ratings": local_counts(cluster),
        "services": {},
    }
    report["services"]["f2"] = check("F2", cluster, lambda db: compare(db, 1, 2), single)
    report["services"]["f1"] = check("F1", cluster, lambda db: explore(db, 258), single)
    report["services"]["f4"] = check("F4", cluster, lambda db: trace(db, 258, 1467), single)
    report["services"]["f3"] = check("F3", cluster, lambda db: recommend(db, 1), single)
    if args.placement == "by_reader":
        imported = import_file(cluster, BATCH)
        report["f6"] = {
            "inserted": imported["inserted"],
            "already_present": imported["already_present"],
            "rejected": imported["rejected"],
        }
        print(
            f"F6 sur les deux instances : {imported['inserted']} ajoutées, "
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


def check(label: str, cluster: Cluster, service, single) -> dict:
    print(label, flush=True)
    expected = service(single)
    cluster.clear()
    actual = service(cluster)
    if actual != expected:
        raise RuntimeError(f"{label} diffère du serveur unique : {_first_difference(expected, actual)}")
    summary = cluster.summary()
    print(f"  identique, échanges {summary}", flush=True)
    return {"equal_to_single_server": True, "exchanged": summary}


def show_incomplete(cluster: Cluster) -> None:
    print("instance arrêtée : une réponse partielle ne doit pas être présentée comme complète", flush=True)
    refused = {}
    for label, service in (
        ("F2", lambda: compare(cluster, 1, 2)),
        ("F3", lambda: recommend(cluster, 1)),
    ):
        try:
            result = service()
        except Incomplete as error:
            print(f"{label} incomplet : {error}", flush=True)
            refused[label] = {"complete": False, "missing": error.missing}
            continue
        raise RuntimeError(f"{label} a répondu {result.get('status')} alors qu'une instance manque")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    previous = {}
    if OUTPUT.exists():
        previous = json.loads(OUTPUT.read_text(encoding="utf-8"))
    previous["by_book_instance_b_stopped"] = refused
    OUTPUT.write_text(json.dumps(previous, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def local_counts(cluster: Cluster) -> dict:
    counts = {}
    for name, instance in cluster.instances.items():
        counts[name] = {
            "endpoint": instance.endpoint,
            "ratings": instance.db.ratings.count_documents({}),
            "user_1": instance.db.ratings.count_documents({"user_id": 1}),
            "user_2": instance.db.ratings.count_documents({"user_id": 2}),
            "book_258": instance.db.ratings.count_documents({"book_id": 258}),
            "book_1467": instance.db.ratings.count_documents({"book_id": 1467}),
        }
    return counts


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
