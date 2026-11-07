"""Connection to the BookScope application database."""

from __future__ import annotations

import os

from pymongo import MongoClient
from pymongo.database import Database

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
APP_DB = os.environ.get("MONGO_APP_DB", "bookscope_app")


def connect() -> Database:
    # Chaque requête de la page ouvre sa connexion, interroge, puis s'arrête.
    # Les services relisent les notes à ce moment-là : un import F6 est donc visible
    # sans reconstruire de table.
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    return client[APP_DB]
