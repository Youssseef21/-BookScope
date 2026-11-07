"""E2 : serveur unique contre deux répartitions, sur deux charges.

Données : toutes les notes (préparées par prepare_e2_data.py).
Trois configurations : serveur unique, répartition par lecteur, par livre.
Deux charges : personnalisée (F2 et F3) et exploration (F1 et F4).

On fait varier la part de chaque charge (100/0, 50/50, 0/100) dans une liste
de SIZE requêtes, identique pour les trois configurations. Une passe
d'échauffement non comptée vérifie aussi que les réponses sont identiques ;
puis REPEATS passes sont mesurées (médiane, moyenne, écart-type) avec le
travail fait sur chaque instance.

    python scripts/measure_e2.py [--size 8] [--repeats 5]
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

from bookscope.cluster import Cluster
from bookscope.db import MONGO_URI
from bookscope.f1 import explore
from bookscope.f2 import compare
from bookscope.f3 import recommend
from bookscope.f4 import trace

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data" / "measurements" / "e2.json"
SEED = 20261106
MIXES = ((1.0, "100/0"), (0.5, "50/50"), (0.0, "0/100"))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", type=int, default=8)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--only", choices=("single", "by_reader", "by_book"))
    args = parser.parse_args()

    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    single = client["e1_all"]
    configs = {"single": single}
    for placement in ("by_reader", "by_book"):
        cluster = Cluster(placement, "_full")
        cluster.refresh()
        configs[placement] = cluster
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
        "prediction_file": "data/measurements/e2_prediction.txt",
        "environment": environment(client),
        "protocol": {
            "dataset": "toutes les notes de ratings.csv (5 976 479)",
            "request_list_size": args.size,
            "repeats": args.repeats,
            "mixes": [label for _share, label in MIXES],
            "warmup": "une passe complète non comptée avant les passes mesurées ; elle sert aussi à vérifier l'égalité des réponses",
            "cache": "cache WiredTiger par défaut de mongo:7 ; pas de vidage entre les passes ; les trois configurations sont mesurées l'une après l'autre sur la même machine",
            "requests": {
                "personalised": [describe(r) for r in personalised],
                "exploration": [describe(r) for r in exploration],
            },
        },
        "configurations": {},
    }

    for name, target in configs.items():
        print(f"== {name}", flush=True)
        entry = {}
        for label, requests in mixes.items():
            entry[label] = measure_mix(name, target, requests, args.repeats, answers_single)
        report["configurations"][name] = entry
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
        for row in db.ratings.aggregate([{"$group": {"_id": "$book_id", "n": {"$sum": 1}}}], allowDiskUse=True)
    }
    middle = sorted(book for book, n in popularity.items() if 800 <= n <= 3000)
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
    for book in books[:10]:
        others = [
            row["user_id"]
            for row in db.ratings.find({"book_id": book, "user_id": {"$ne": user_id}}, {"_id": 0, "user_id": 1}).limit(200)
        ]
        if others:
            return sorted(others)[rng.randrange(len(others))]
    raise RuntimeError("aucun lecteur en commun")


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
    label = ",".join(describe(r) for r in requests)
    is_cluster = isinstance(target, Cluster)
    # passe d'échauffement, qui sert aussi à comparer avec le serveur unique
    answers = []
    for request in requests:
        if is_cluster:
            target.clear()
        answers.append(canonical(execute(target, request)))
    if name == "single":
        answers_single[label] = answers
        identical = True
    else:
        identical = answers == answers_single.get(label)

    totals = []
    per_request: dict[str, list[float]] = {}
    work = None
    for _ in range(repeats):
        if is_cluster:
            target.clear()
        started = time.perf_counter()
        for request in requests:
            began = time.perf_counter()
            execute(target, request)
            per_request.setdefault(describe(request), []).append((time.perf_counter() - began) * 1000)
        totals.append((time.perf_counter() - started) * 1000)
        if is_cluster and work is None:
            work = target.summary()
    print(f"  {name} {len(requests)} requêtes : médiane {statistics.median(totals):.0f} ms", flush=True)
    return {
        "requests": [describe(r) for r in requests],
        "identical_to_single": identical,
        "total_ms": {
            "median": round(statistics.median(totals), 1),
            "mean": round(statistics.mean(totals), 1),
            "stdev": round(statistics.stdev(totals), 1) if len(totals) > 1 else 0.0,
            "min": round(min(totals), 1),
            "max": round(max(totals), 1),
            "samples": [round(value, 1) for value in totals],
        },
        "per_request_median_ms": {key: round(statistics.median(values), 1) for key, values in per_request.items()},
        "work_per_instance_one_pass": work,
    }


def canonical(value):
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def environment(client) -> dict:
    info = {
        "platform": platform.platform(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "mongo_server_version": client.server_info()["version"],
    }
    try:
        text = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}|{{.NCPU}}|{{.MemTotal}}"],
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        version, cpus, memory = text.split("|")
        info["docker"] = {"version": version, "cpus_available_to_containers": int(cpus), "memory_bytes": int(memory), "container_limits": "aucune limite explicite (docker run sans --cpus ni --memory)"}
    except Exception:
        info["docker"] = "indisponible"
    try:
        import pymongo

        info["pymongo"] = pymongo.version
    except Exception:
        pass
    return info


if __name__ == "__main__":
    main()
