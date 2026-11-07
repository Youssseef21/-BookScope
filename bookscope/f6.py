"""F6 — import a rating batch without duplicates or data loss.

The identity of a rating is the pair (user_id, book_id).

- A new pair is inserted.
- The same pair with the same score is left untouched.
- The same pair with a different score keeps the score already stored.
  The incoming score is written to rating_conflicts, so it is not lost
  and it does not replace the rating read by the other services.
- Running the same file again inserts nothing.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from pymongo import ASCENDING
from pymongo.database import Database

LOOKUP_CHUNK = 2_000
INSERT_CHUNK = 20_000
EXAMPLE_LIMIT = 5


def import_file(db: Database, path: Path) -> dict[str, Any]:
    """Import a CSV with columns user_id, book_id, rating, source_row."""
    path = Path(path)
    if not path.is_file():
        return {"status": "not_found", "path": str(path)}
    parsed, rejected, error = _read_csv(path)
    if error:
        return {"status": "invalid_file", "path": str(path), "reason": error}
    outcome = _apply(db, parsed, rejected)
    outcome["status"] = "ok"
    outcome["path"] = str(path)
    return outcome


def _read_csv(path: Path) -> tuple[list[dict], list[dict], str | None]:
    parsed: list[dict] = []
    rejected: list[dict] = []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        required = {"user_id", "book_id", "rating", "source_row"}
        if not required <= fields:
            return [], [], "colonnes attendues : user_id, book_id, rating, source_row"
        for line_number, raw in enumerate(reader, start=2):
            row, reason = _parse_row(raw, line_number)
            if reason:
                rejected.append({"line": line_number, "reason": reason})
            else:
                parsed.append(row)
    return parsed, rejected, None


def _parse_row(raw: dict, line_number: int) -> tuple[dict | None, str | None]:
    try:
        user_id = int(raw["user_id"])
        book_id = int(raw["book_id"])
        rating = int(raw["rating"])
        source_row = int(raw["source_row"])
    except (TypeError, ValueError):
        return None, "valeur illisible"
    if rating < 1 or rating > 5:
        return None, "note hors de 1 à 5"
    if user_id < 1 or book_id < 1 or source_row < 1:
        return None, "identifiant invalide"
    return {
        "line": line_number,
        "user_id": user_id,
        "book_id": book_id,
        "rating": rating,
        "source_row": source_row,
    }, None


def _apply(db: Database, parsed: list[dict], rejected: list[dict]) -> dict[str, Any]:
    file_rows = len(parsed) + len(rejected)
    first: dict[tuple[int, int], dict] = {}
    extras: dict[tuple[int, int], list[dict]] = {}
    for row in parsed:
        key = (row["user_id"], row["book_id"])
        if key not in first:
            first[key] = row
        else:
            extras.setdefault(key, []).append(row)

    known_users = _known_users(db, {user_id for user_id, _book_id in first})
    known_books = _known_books(db, {book_id for _user_id, book_id in first})
    existing = _existing_ratings(db, list(first))

    to_insert: list[dict] = []
    conflicts: list[dict] = []
    already_present = 0
    for key, row in first.items():
        stored = existing.get(key)
        if stored is not None:
            kept = stored
            # Même note : on ne réécrit rien. Relancer le fichier est donc sans effet.
            if stored["rating"] == row["rating"]:
                already_present += 1
            else:
                # Note différente : on garde l'ancienne pour F1–F5,
                # et on enregistre la nouvelle à part pour ne pas la perdre.
                conflicts.append(_conflict(stored, row))
        elif row["user_id"] not in known_users:
            _reject_pair(rejected, row, extras.get(key, []), "lecteur hors sélection")
            continue
        elif row["book_id"] not in known_books:
            _reject_pair(rejected, row, extras.get(key, []), "livre inconnu")
            continue
        else:
            to_insert.append(row)
            kept = row
        for extra in extras.get(key, []):
            if extra["rating"] == kept["rating"]:
                already_present += 1
            else:
                conflicts.append(_conflict(kept, extra))

    ratings_before = db.ratings.count_documents({})
    inserted_examples = [
        {
            "user_id": row["user_id"],
            "book_id": row["book_id"],
            "rating": row["rating"],
            "source_row": row["source_row"],
        }
        for row in to_insert[:EXAMPLE_LIMIT]
    ]
    if to_insert:
        for start in range(0, len(to_insert), INSERT_CHUNK):
            chunk = to_insert[start : start + INSERT_CHUNK]
            db.ratings.insert_many(
                [
                    {
                        "user_id": row["user_id"],
                        "book_id": row["book_id"],
                        "rating": row["rating"],
                        "source_row": row["source_row"],
                    }
                    for row in chunk
                ],
                ordered=False,
            )
    new_conflicts = _store_conflicts(db, conflicts)
    return {
        "rows": file_rows,
        "inserted": len(to_insert),
        "already_present": already_present,
        "conflicts": new_conflicts,
        "conflicts_already_recorded": len(conflicts) - new_conflicts,
        "rejected": len(rejected),
        "ratings_before": ratings_before,
        "ratings_after": ratings_before + len(to_insert),
        "inserted_examples": inserted_examples,
        "conflict_examples": conflicts[:EXAMPLE_LIMIT],
        "rejected_examples": rejected[:EXAMPLE_LIMIT],
    }


def _reject_pair(rejected: list[dict], row: dict, extras: list[dict], reason: str) -> None:
    rejected.append({"line": row["line"], "reason": reason})
    for extra in extras:
        rejected.append({"line": extra["line"], "reason": reason})


def _conflict(kept: dict, incoming: dict) -> dict:
    return {
        "user_id": incoming["user_id"],
        "book_id": incoming["book_id"],
        "stored_rating": kept["rating"],
        "stored_source_row": kept["source_row"],
        "incoming_rating": incoming["rating"],
        "incoming_source_row": incoming["source_row"],
    }


def _known_users(db: Database, user_ids: set[int]) -> set[int]:
    if not user_ids:
        return set()
    found: set[int] = set()
    identifiers = list(user_ids)
    for start in range(0, len(identifiers), LOOKUP_CHUNK):
        chunk = identifiers[start : start + LOOKUP_CHUNK]
        rows = db.ratings.aggregate(
            [
                {"$match": {"user_id": {"$in": chunk}}},
                {"$group": {"_id": "$user_id"}},
            ],
            allowDiskUse=True,
        )
        found.update(row["_id"] for row in rows)
    return found


def _known_books(db: Database, book_ids: set[int]) -> set[int]:
    if not book_ids:
        return set()
    cursor = db.books.find({"book_id": {"$in": list(book_ids)}}, {"_id": 0, "book_id": 1})
    return {row["book_id"] for row in cursor}


def _existing_ratings(db: Database, pairs: list[tuple[int, int]]) -> dict[tuple[int, int], dict]:
    found: dict[tuple[int, int], dict] = {}
    for start in range(0, len(pairs), LOOKUP_CHUNK):
        chunk = pairs[start : start + LOOKUP_CHUNK]
        query = {"$or": [{"user_id": user_id, "book_id": book_id} for user_id, book_id in chunk]}
        cursor = db.ratings.find(
            query,
            {"_id": 0, "user_id": 1, "book_id": 1, "rating": 1, "source_row": 1},
        )
        for row in cursor:
            found[(row["user_id"], row["book_id"])] = row
    return found


def _store_conflicts(db: Database, conflicts: list[dict]) -> int:
    if not conflicts:
        return 0
    db.rating_conflicts.create_index(
        [
            ("user_id", ASCENDING),
            ("book_id", ASCENDING),
            ("incoming_rating", ASCENDING),
            ("incoming_source_row", ASCENDING),
        ],
        unique=True,
    )
    keys = [
        {
            "user_id": row["user_id"],
            "book_id": row["book_id"],
            "incoming_rating": row["incoming_rating"],
            "incoming_source_row": row["incoming_source_row"],
        }
        for row in conflicts
    ]
    already = {
        (row["user_id"], row["book_id"], row["incoming_rating"], row["incoming_source_row"])
        for row in db.rating_conflicts.find(
            {"$or": keys},
            {
                "_id": 0,
                "user_id": 1,
                "book_id": 1,
                "incoming_rating": 1,
                "incoming_source_row": 1,
            },
        )
    }
    fresh = [
        dict(row)
        for row in conflicts
        if (row["user_id"], row["book_id"], row["incoming_rating"], row["incoming_source_row"]) not in already
    ]
    if fresh:
        db.rating_conflicts.insert_many(fresh, ordered=False)
    return len(fresh)
