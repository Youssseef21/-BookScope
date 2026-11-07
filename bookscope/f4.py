"""F4 — trace an indirect connection between two books.

Input
    Two book identifiers or titles. An ambiguous title is returned as a list.

Output
    A direct link when enough ordinary readers liked both books, then up to
    three indirect paths through another book. Paths through the 100 most
    rated books, or through readers with at least 150 ratings, are left out.
    A shared author or a specific tag is reported separately. If none of
    these holds, the service says that no convincing connection was found.
"""

from __future__ import annotations

import math
from typing import Any

from pymongo.database import Database

from bookscope.f1 import resolve_books
from bookscope.tags import is_extremely_common, is_status

MIN_RATING = 4
MIN_DIRECT = 5
MIN_LEG = 3
ACTIVITY_CAP = 150
TOP_POPULAR = 100
PATHS = 3
EVIDENCE_LIMIT = 5
MIN_TAG_COUNT = 50


def trace(db: Database, left: str | int, right: str | int) -> dict[str, Any]:
    """Find how two books are connected."""
    book_a, error_a = _one_book(db, left)
    if error_a:
        return error_a
    book_b, error_b = _one_book(db, right)
    if error_b:
        return error_b
    if book_a["book_id"] == book_b["book_id"]:
        return {"status": "same_book", "book_id": book_a["book_id"]}

    positive_a = _positive(db, book_a["book_id"])
    positive_b = _positive(db, book_b["book_id"])
    # L'activité sert à écarter les lecteurs qui ont au moins 150 notes :
    # ils relient trop de livres pour qu'un chemin soit informatif.
    activity = _activity(db, set(positive_a) | set(positive_b))
    direct = _direct(positive_a, positive_b, activity)
    counts = _rating_counts(db)
    # Les 100 livres les plus notés ne peuvent pas servir de pont.
    top100 = {
        book_id
        for book_id, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:TOP_POPULAR]
    }
    indirect = _indirect(db, book_a["book_id"], book_b["book_id"], positive_a, positive_b, activity, top100, counts)
    catalogue = _catalogue(db, book_a, book_b)
    convincing = direct["convincing"] or bool(indirect) or catalogue["convincing"]
    return {
        "status": "ok" if convincing else "not_convincing",
        "parameters": {
            "min_rating": MIN_RATING,
            "min_direct_readers": MIN_DIRECT,
            "min_leg_readers": MIN_LEG,
            "activity_cap": ACTIVITY_CAP,
            "top_popular_excluded": TOP_POPULAR,
        },
        "book_a": _brief(book_a),
        "book_b": _brief(book_b),
        "direct": direct,
        "indirect": indirect,
        "catalogue": catalogue,
    }


def _one_book(db: Database, query: str | int) -> tuple[dict | None, dict | None]:
    matches = resolve_books(db, query)
    if not matches:
        return None, {"status": "not_found", "query": query}
    if len(matches) > 1:
        return None, {
            "status": "ambiguous",
            "query": query,
            "matches": [_brief(book) for book in matches],
        }
    return matches[0], None


def _brief(book: dict) -> dict:
    return {
        "book_id": book["book_id"],
        "title": book.get("title"),
        "authors": book.get("authors"),
    }


def _positive(db: Database, book_id: int) -> dict[int, dict]:
    cursor = db.ratings.find(
        {"book_id": book_id, "rating": {"$gte": MIN_RATING}},
        {"_id": 0, "user_id": 1, "rating": 1, "source_row": 1},
    )
    return {row["user_id"]: row for row in cursor}


def _activity(db: Database, user_ids: set[int]) -> dict[int, int]:
    if not user_ids:
        return {}
    rows = db.ratings.aggregate(
        [
            {"$match": {"user_id": {"$in": list(user_ids)}}},
            {"$group": {"_id": "$user_id", "n": {"$sum": 1}}},
        ],
        allowDiskUse=True,
    )
    return {row["_id"]: row["n"] for row in rows}


def _rating_counts(db: Database) -> dict[int, int]:
    rows = db.ratings.aggregate(
        [{"$group": {"_id": "$book_id", "n": {"$sum": 1}}}],
        allowDiskUse=True,
    )
    return {row["_id"]: row["n"] for row in rows}


def _direct(positive_a: dict[int, dict], positive_b: dict[int, dict], activity: dict[int, int]) -> dict:
    shared = sorted(set(positive_a) & set(positive_b))
    # Le lien direct n'est convaincant que par les lecteurs ordinaires.
    # Les très actifs sont comptés à part et exclus de la preuve.
    ordinary = [user_id for user_id in shared if activity.get(user_id, 0) < ACTIVITY_CAP]
    highly_active = [user_id for user_id in shared if activity.get(user_id, 0) >= ACTIVITY_CAP]
    evidence = []
    for user_id in ordinary[:EVIDENCE_LIMIT]:
        left = positive_a[user_id]
        right = positive_b[user_id]
        evidence.append(
            {
                "user_id": user_id,
                "rating_a": left["rating"],
                "source_row_a": left["source_row"],
                "rating_b": right["rating"],
                "source_row_b": right["source_row"],
                "ratings_by_reader": activity.get(user_id, 0),
            }
        )
    return {
        "ordinary_readers": len(ordinary),
        "highly_active_readers": len(highly_active),
        "convincing": len(ordinary) >= MIN_DIRECT,
        "evidence": evidence,
    }


def _indirect(
    db: Database,
    book_a: int,
    book_b: int,
    positive_a: dict[int, dict],
    positive_b: dict[int, dict],
    activity: dict[int, int],
    top100: set[int],
    counts: dict[int, int],
) -> list[dict]:
    users_a = [user_id for user_id in positive_a if activity.get(user_id, 0) < ACTIVITY_CAP]
    users_b = [user_id for user_id in positive_b if activity.get(user_id, 0) < ACTIVITY_CAP]
    if len(users_a) < MIN_LEG or len(users_b) < MIN_LEG:
        return []
    excluded = list({book_a, book_b, *top100})
    from_a = list(
        db.ratings.aggregate(
            [
                {
                    "$match": {
                        "user_id": {"$in": users_a},
                        "rating": {"$gte": MIN_RATING},
                        "book_id": {"$nin": excluded},
                    }
                },
                {
                    "$group": {
                        "_id": "$book_id",
                        "n": {"$sum": 1},
                        "readers": {
                            "$push": {
                                "user_id": "$user_id",
                                "rating": "$rating",
                                "source_row": "$source_row",
                            }
                        },
                    }
                },
                {"$match": {"n": {"$gte": MIN_LEG}}},
                {"$sort": {"n": -1, "_id": 1}},
                {"$limit": 80},
            ],
            allowDiskUse=True,
        )
    )
    if not from_a:
        return []
    by_book = {row["_id"]: row for row in from_a}
    to_b = {
        row["_id"]: row
        for row in db.ratings.aggregate(
            [
                {
                    "$match": {
                        "book_id": {"$in": list(by_book)},
                        "user_id": {"$in": users_b},
                        "rating": {"$gte": MIN_RATING},
                    }
                },
                {
                    "$group": {
                        "_id": "$book_id",
                        "n": {"$sum": 1},
                        "readers": {
                            "$push": {
                                "user_id": "$user_id",
                                "rating": "$rating",
                                "source_row": "$source_row",
                            }
                        },
                    }
                },
                {"$match": {"n": {"$gte": MIN_LEG}}},
            ],
            allowDiskUse=True,
        )
    }
    ranked = []
    for book_id, leg_b in to_b.items():
        leg_a = by_book[book_id]
        popularity = counts.get(book_id, 1)
        score = min(leg_a["n"], leg_b["n"]) / math.sqrt(popularity)
        ranked.append((score, book_id, leg_a, leg_b, popularity))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    chosen = ranked[:PATHS]
    info = {
        book["book_id"]: book
        for book in db.books.find(
            {"book_id": {"$in": [item[1] for item in chosen]}},
            {"_id": 0, "book_id": 1, "title": 1, "authors": 1},
        )
    }
    paths = []
    for score, book_id, leg_a, leg_b, popularity in chosen:
        book = info.get(book_id, {})
        paths.append(
            {
                "book_id": book_id,
                "title": book.get("title"),
                "authors": book.get("authors"),
                "score": round(score, 4),
                "readers_from_a": leg_a["n"],
                "readers_to_b": leg_b["n"],
                "ratings_in_selection": popularity,
                "evidence_a": _leg_evidence(leg_a["readers"], positive_a),
                "evidence_b": _leg_evidence(leg_b["readers"], positive_b),
            }
        )
    return paths


def _leg_evidence(readers: list[dict], endpoint: dict[int, dict]) -> list[dict]:
    ordered = sorted(readers, key=lambda row: row["user_id"])[:EVIDENCE_LIMIT]
    evidence = []
    for row in ordered:
        end = endpoint[row["user_id"]]
        evidence.append(
            {
                "user_id": row["user_id"],
                "rating_on_endpoint": end["rating"],
                "source_row_on_endpoint": end["source_row"],
                "rating_on_bridge": row["rating"],
                "source_row_on_bridge": row["source_row"],
            }
        )
    return evidence


def _catalogue(db: Database, book_a: dict, book_b: dict) -> dict:
    left = _author_names(book_a.get("authors"))
    right = _author_names(book_b.get("authors"))
    authors = [left[name] for name in sorted(left.keys() & right.keys())]
    catalogue_size = db.books.count_documents({})
    tag_df = {
        row["_id"]: row["n"]
        for row in db.books.aggregate(
            [{"$unwind": "$tags"}, {"$group": {"_id": "$tags.name", "n": {"$sum": 1}}}]
        )
    }
    right = {tag["name"]: tag for tag in book_b.get("tags", [])}
    tags = []
    for tag in book_a.get("tags", []):
        other = right.get(tag["name"])
        if other is None or tag["count"] < MIN_TAG_COUNT or other["count"] < MIN_TAG_COUNT:
            continue
        if is_status(tag["name"]) or is_extremely_common(tag["name"], tag_df.get(tag["name"], 0), catalogue_size):
            continue
        tags.append(
            {
                "name": tag["name"],
                "count_a": tag["count"],
                "count_b": other["count"],
            }
        )
    tags.sort(key=lambda tag: (tag_df.get(tag["name"], 0), tag["name"]))
    return {
        "authors": authors,
        "tags": tags[:6],
        "convincing": bool(authors or tags),
    }


def _author_names(value: str | None) -> dict[str, str]:
    if not value:
        return {}
    names = {}
    for part in value.split(","):
        cleaned = part.strip()
        if len(cleaned) >= 5:
            names[cleaned.casefold()] = cleaned
    return names
