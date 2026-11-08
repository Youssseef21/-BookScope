"""Start the sharded cluster and register the two shards.

    python scripts/start_sharded.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", str(ROOT / "compose.sharded.yml")]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    subprocess.run([*COMPOSE, "up", "-d", "cfg", "shard1", "shard2"], cwd=ROOT, check=True)
    wait_port(27100)
    wait_port(27101)
    wait_port(27102)
    initiate(27100, {"_id": "cfgRS", "configsvr": True, "members": [{"_id": 0, "host": "cfg:27017"}]})
    initiate(27101, {"_id": "shard1", "members": [{"_id": 0, "host": "shard1:27017"}]})
    initiate(27102, {"_id": "shard2", "members": [{"_id": 0, "host": "shard2:27017"}]})
    subprocess.run([*COMPOSE, "up", "-d", "mongos"], cwd=ROOT, check=True)
    wait_port(27200)
    router = MongoClient("mongodb://127.0.0.1:27200", serverSelectionTimeoutMS=8000)
    add_shard(router, "shard1/shard1:27017")
    add_shard(router, "shard2/shard2:27017")
    print("mongos : localhost:27200", flush=True)
    print("shard1 : localhost:27101", flush=True)
    print("shard2 : localhost:27102", flush=True)
    print("cfg    : localhost:27100", flush=True)


def wait_port(port: int) -> None:
    deadline = time.time() + 90
    while time.time() < deadline:
        try:
            MongoClient(
                f"mongodb://127.0.0.1:{port}",
                directConnection=True,
                serverSelectionTimeoutMS=2000,
            ).admin.command("ping")
            print(f"port {port} répond", flush=True)
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError(f"le port {port} ne répond pas")


def initiate(port: int, config: dict) -> None:
    client = MongoClient(
        f"mongodb://127.0.0.1:{port}",
        directConnection=True,
        serverSelectionTimeoutMS=5000,
    )
    try:
        client.admin.command("replSetInitiate", config)
        print(f"replica set {config['_id']} créé", flush=True)
    except Exception as error:
        if "already initialized" not in str(error).lower():
            raise
        print(f"replica set {config['_id']} déjà créé", flush=True)
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            if client.admin.command("hello").get("isWritablePrimary"):
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError(f"{config['_id']} n'a pas de primaire")


def add_shard(router: MongoClient, host: str) -> None:
    try:
        router.admin.command("addShard", host)
        print(f"fragment ajouté : {host}", flush=True)
    except Exception as error:
        if "already" not in str(error).lower():
            raise
        print(f"fragment déjà présent : {host}", flush=True)


if __name__ == "__main__":
    main()
