"""E2 : serveur unique contre les deux répartitions du cluster fragmenté.

Données : les 2 233 084 notes de bookscope_app, déjà copiées dans
dist_reader et dist_book. Trois configurations : serveur unique (port 27017),
répartition par lecteur, répartition par livre. Les deux répartitions passent
par mongos (port 27200).

Deux charges : personnalisée (F2 et F3) et exploration (F1 et F4).
On fait varier la part de chaque charge (100/0, 50/50, 0/100) dans une liste
de SIZE requêtes, identique pour les trois configurations. Une passe
d'échauffement non comptée vérifie que les réponses sont identiques ;
puis REPEATS passes sont mesurées (médiane, moyenne, écart-type).

    python scripts/measure_e2.py --size 4 --repeats 3
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pymongo import MongoClient

from bookscope.db import MONGO_URI, connect
from bookscope.f1 import explore
from bookscope.f2 import compare
from bookscope.f3 import recommend
from bookscope.f4 import trace
from bookscope.sharded import DATABASES, MONGOS_URI, SHARDS, open_placement

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "measurements" / "e2.json"
PREDICTION = "data/measurements/e2_prediction.txt"
SEED = 20261108
MIXES = ((1.0, "100/0"), (0.5, "50/50"), (0.0, "0/100"))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=4)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--only", choices=("single", "by_reader", "by_book"))
    args = parser.parse_args()

    single = connect()
    configs = {
        "single": single,
        "by_reader": open_placement("by_reader"),
        "by_book": open_placement("by_book"),
    }
    if args.only:
        configs = {args.only: configs[args.only]}

    personalised, exploration = build_requests(single, args.size)
    print("requêtes personnalisées :", [describe(r) for r in personalised], flush=True)
    print("requêtes d'exploration :", [describe(r) for r in exploration], flush=True)

    mixes = {}
    for share, label in MIXES:
        count_p = round(args.size * share)
        mixes[label] = interleave(personalised[:count_p], exploration[: args.size - count_p])

    answers_single: dict[str, object] = {}
    report = {
        "date": time.strftime("%Y-%m-%d"),
        "prediction_file": PREDICTION,
        "setup": (
            "Serveur unique : bookscope_app sur le port 27017. "
            "Répartitions : dist_reader et dist_book à travers mongos, port 27200, "
            "fragments 27101 et 27102. Même ordinateur."
        ),
        "environment": environment(),
        "protocol": {
            "dataset": "notes de bookscope_app (2 233 084), les mêmes dans dist_reader et dist_book",
            "request_list_size": args.size,
            "repeats": args.repeats,
            "mixes": [label for _share, label in MIXES],
            "warmup": "une passe complète non comptée avant les passes mesurées ; elle vérifie aussi l'égalité des réponses",
            "cache": "pas de vidage entre les passes ; les configurations sont mesurées l'une après l'autre",
            "requests": {
                "personalised": [describe(r) for r in personalised],
                "exploration": [describe(r) for r in exploration],
            },
            "where_notes_sit": where_notes_sit(personalised, exploration),
        },
        "configurations": {},
    }

    order = [name for name in ("single", "by_reader", "by_book") if name in configs]
    for name in order:
        print(f"== {name}", flush=True)
        entry = {}
        for label, requests in mixes.items():
            print(f"  mélange {label}", flush=True)
            entry[label] = measure_mix(name, configs[name], requests, args.repeats, answers_single)
        report["configurations"][name] = entry
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


def build_requests(db, size: int) -> tuple[list[tuple], list[tuple]]:
    rng = random.Random(SEED)
    readers = [
        row["_id"]
        for row in db.ratings.aggregate(
            [
                {"$group": {"_id": "$user_id", "n": {"$sum": 1}}},
                {"$match": {"n": {"$gte": 60, "$lte": 150}}},
                {"$sort": {"_id": 1}},
            ],
            allowDiskUse=True,
        )
    ]
    if len(readers) < size + 1:
        raise RuntimeError(f"pas assez de lecteurs entre 60 et 150 notes : {len(readers)}")
    rng.shuffle(readers)
    personalised: list[tuple] = []
    for index in range(size):
        if index % 2 == 0:
            personalised.append(("f3", readers[index]))
        else:
            first = readers[index]
            partner = overlapping_reader(db, first, rng)
            personalised.append(("f2", first, partner))

    popularity = {
        row["_id"]: row["n"]
        for row in db.ratings.aggregate(
            [{"$group": {"_id": "$book_id", "n": {"$sum": 1}}}],
            allowDiskUse=True,
        )
    }
    middle = sorted(book for book, count in popularity.items() if 400 <= count <= 3000)
    if len(middle) < size * 2:
        raise RuntimeError(f"pas assez de livres entre 400 et 3000 notes : {len(middle)}")
    rng.shuffle(middle)
    exploration: list[tuple] = []
    for index in range(size):
        if index % 2 == 0:
            exploration.append(("f1", middle[index]))
        else:
            exploration.append(("f4", middle[index], middle[index + size]))
    return personalised, exploration


def overlapping_reader(db, user_id: int, rng: random.Random) -> int:
    books = [row["book_id"] for row in db.ratings.find({"user_id": user_id}, {"_id": 0, "book_id": 1})]
    rng.shuffle(books)
    for book in books[:12]:
        others = [
            row["user_id"]
            for row in db.ratings.find(
                {"book_id": book, "user_id": {"$ne": user_id}},
                {"_id": 0, "user_id": 1},
            ).limit(200)
        ]
        if others:
            return sorted(others)[rng.randrange(len(others))]
    raise RuntimeError(f"aucun lecteur en commun avec {user_id}")


def where_notes_sit(personalised: list[tuple], exploration: list[tuple]) -> dict:
    users = sorted({item for request in personalised for item in request[1:]})
    books = sorted({item for request in exploration for item in request[1:]})
    placed = {}
    for placement, database in DATABASES.items():
        placed[placement] = {}
        for name, uri in SHARDS:
            client = MongoClient(uri, directConnection=True, serverSelectionTimeoutMS=8000)
            ratings = client[database].ratings
            placed[placement][name] = {
                "users": {str(user_id): ratings.count_documents({"user_id": user_id}) for user_id in users},
                "books": {str(book_id): ratings.count_documents({"book_id": book_id}) for book_id in books},
            }
    return placed


def interleave(first: list[tuple], second: list[tuple]) -> list[tuple]:
    merged = []
    for index in range(max(len(first), len(second))):
        if index < len(first):
            merged.append(first[index])
        if index < len(second):
            merged.append(second[index])
    return merged


def describe(request: tuple) -> str:
    return request[0] + "(" + ", ".join(str(item) for item in request[1:]) + ")"


def execute(target, request: tuple):
    kind = request[0]
    if kind == "f1":
        return explore(target, request[1])
    if kind == "f2":
        return compare(target, request[1], request[2])
    if kind == "f3":
        return recommend(target, request[1])
    return trace(target, request[1], request[2])


def measure_mix(name: str, target, requests: list[tuple], repeats: int, answers_single: dict) -> dict:
    label = ",".join(describe(request) for request in requests)
    answers = [canonical(execute(target, request)) for request in requests]
    if name == "single":
        answers_single[label] = answers
        identical = True
    else:
        identical = answers == answers_single.get(label)
        if not identical:
            print(f"    réponses différentes de celles du serveur unique ({label})", flush=True)

    totals = []
    per_request: dict[str, list[float]] = {}
    for _ in range(repeats):
        started = time.perf_counter()
        for request in requests:
            began = time.perf_counter()
            execute(target, request)
            per_request.setdefault(describe(request), []).append((time.perf_counter() - began) * 1000)
        totals.append((time.perf_counter() - started) * 1000)
    print(f"    médiane {statistics.median(totals) / 1000:.1f} s", flush=True)
    return {
        "requests": [describe(request) for request in requests],
        "identical_to_single": identical,
        "total_ms": {
            "median": round(statistics.median(totals), 1),
            "mean": round(statistics.mean(totals), 1),
            "stdev": round(statistics.stdev(totals), 1) if len(totals) > 1 else 0.0,
            "min": round(min(totals), 1),
            "max": round(max(totals), 1),
            "samples": [round(value, 1) for value in totals],
        },
        "per_request_median_ms": {
            key: round(statistics.median(values), 1) for key, values in per_request.items()
        },
    }


def canonical(value):
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def environment() -> dict:
    single = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    router = MongoClient(MONGOS_URI, serverSelectionTimeoutMS=5000)
    info = {
        "platform": platform.platform(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "single_server": {"uri": MONGO_URI, "version": single.server_info()["version"]},
        "mongos": {"uri": MONGOS_URI, "version": router.server_info()["version"]},
    }
    try:
        text = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}|{{.NCPU}}|{{.MemTotal}}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout.strip()
        version, cpus, memory = text.split("|")
        info["docker"] = {
            "version": version,
            "cpus_available_to_containers": int(cpus),
            "memory_bytes": int(memory),
        }
    except Exception as error:
        info["docker"] = f"indisponible : {error}"
    return info


if __name__ == "__main__":
    main()
