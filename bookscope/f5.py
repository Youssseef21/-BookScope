"""F5 — find what connects two reading interests.

Input
    Two thematic tag names. A status tag, or a tag present on at least half
    of the catalogue, is refused. An unknown fragment that matches several
    tags is returned as a list.

Output
    Books that carry both tags, ranked by how many readers scored them at
    least 4/5, and readers who liked a different book on each side. The same
    counts are repeated after dropping the 100 most rated books and readers
    with at least 150 ratings. The result says whether the connection
    persists, weakens, or disappears.
"""

from __future__ import annotations

import re
from typing import Any

from pymongo.database import Database

from bookscope.tags import is_extremely_common, is_status

MIN_TAG_COUNT = 50
MIN_RATING = 4
ACTIVITY_CAP = 150
TOP_POPULAR = 100
BRIDGE_LIMIT = 5
READER_EXAMPLES = 5


def link_interests(db: Database, left: str, right: str) -> dict[str, Any]:
    """Connect two thematic interests."""
    interest_a, error_a = _resolve(db, left)
    if error_a:
        return error_a
    interest_b, error_b = _resolve(db, right)
    if error_b:
        return error_b
    if interest_a["name"] == interest_b["name"]:
        return {"status": "same_interest", "interest": interest_a["name"]}

    books_a = _books_for(db, interest_a["name"])
    books_b = _books_for(db, interest_b["name"])
    bridge_ids = set(books_a) & set(books_b)
    exclusive_a = set(books_a) - bridge_ids
    exclusive_b = set(books_b) - bridge_ids
    counts = _rating_counts(db)
    top100 = {
        book_id
        for book_id, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:TOP_POPULAR]
    }
    positive = _positive_pairs(db, bridge_ids | exclusive_a | exclusive_b)
    activity = _activity(db, {user_id for user_id, _book_id, _row in positive})

    bridges = _rank_bridges(db, bridge_ids, positive, activity, counts, top100, discounted=False)
    bridges_discounted = _rank_bridges(db, bridge_ids - top100, positive, activity, counts, top100, discounted=True)
    readers_full = _connecting_readers(exclusive_a, exclusive_b, positive, activity, discounted=False)
    readers_discounted = _connecting_readers(
        exclusive_a - top100, exclusive_b - top100, positive, activity, discounted=True
    )
    examples = _examples(db, readers_discounted, exclusive_a - top100, exclusive_b - top100, positive)
    persistence = _persistence(len(readers_full), len(readers_discounted), len(bridges_discounted))
    return {
        "status": "ok",
        "parameters": {
            "min_tag_count": MIN_TAG_COUNT,
            "min_rating": MIN_RATING,
            "activity_cap": ACTIVITY_CAP,
            "top_popular_excluded": TOP_POPULAR,
        },
        "interest_a": interest_a,
        "interest_b": interest_b,
        "bridge_books_total": len(bridge_ids),
        "bridges": bridges,
        "bridges_discounted": bridges_discounted,
        "readers_full": len(readers_full),
        "readers_discounted": len(readers_discounted),
        "reader_examples": examples,
        "persistence": persistence,
    }


def _resolve(db: Database, text: str) -> tuple[dict | None, dict | None]:
    query = text.strip()
    if not query:
        return None, {"status": "not_found", "query": query}
    exact = _tag_names(db, f"^{re.escape(query)}$")
    chosen = exact or _tag_names(db, re.escape(query))
    if not chosen:
        return None, {"status": "not_found", "query": query}
    if len(chosen) > 1:
        return None, {"status": "ambiguous", "query": query, "matches": chosen}
    name, books = chosen[0]
    catalogue_size = db.books.count_documents({})
    if is_status(name) or is_extremely_common(name, books, catalogue_size):
        reason = "status" if is_status(name) else "extremely_common"
        return None, {"status": "not_a_theme", "query": name, "reason": reason, "books": books}
    return {"name": name, "books": books}, None


def _tag_names(db: Database, pattern: str) -> list[tuple[str, int]]:
    rows = db.books.aggregate(
        [
            {"$match": {"tags.name": {"$regex": pattern, "$options": "i"}}},
            {"$unwind": "$tags"},
            {"$match": {"tags.name": {"$regex": pattern, "$options": "i"}}},
            {"$group": {"_id": "$tags.name", "books": {"$sum": 1}}},
            {"$sort": {"books": -1, "_id": 1}},
            {"$limit": 12},
        ]
    )
    return [(row["_id"], row["books"]) for row in rows]


def _books_for(db: Database, tag_name: str) -> dict[int, dict]:
    found = {}
    cursor = db.books.find(
        {"tags": {"$elemMatch": {"name": tag_name, "count": {"$gte": MIN_TAG_COUNT}}}},
        {"_id": 0, "book_id": 1, "title": 1, "authors": 1},
    )
    for book in cursor:
        found[book["book_id"]] = book
    return found


def _rating_counts(db: Database) -> dict[int, int]:
    rows = db.ratings.aggregate(
        [{"$group": {"_id": "$book_id", "n": {"$sum": 1}}}],
        allowDiskUse=True,
    )
    return {row["_id"]: row["n"] for row in rows}


def _positive_pairs(db: Database, book_ids: set[int]) -> list[tuple[int, int]]:
    if not book_ids:
        return []
    cursor = db.ratings.find(
        {"book_id": {"$in": list(book_ids)}, "rating": {"$gte": MIN_RATING}},
        {"_id": 0, "user_id": 1, "book_id": 1, "rating": 1, "source_row": 1},
    )
    return [(row["user_id"], row["book_id"], row) for row in cursor]


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


def _rank_bridges(
    db: Database,
    book_ids: set[int],
    positive: list[tuple],
    activity: dict[int, int],
    counts: dict[int, int],
    top100: set[int],
    discounted: bool,
) -> list[dict]:
    scores: dict[int, int] = {book_id: 0 for book_id in book_ids}
    for user_id, book_id, _row in positive:
        if book_id not in scores:
            continue
        if discounted and activity.get(user_id, 0) >= ACTIVITY_CAP:
            continue
        scores[book_id] += 1
    ordered = sorted(scores, key=lambda book_id: (-scores[book_id], book_id))
    ordered = [book_id for book_id in ordered if scores[book_id] > 0][:BRIDGE_LIMIT]
    info = {
        book["book_id"]: book
        for book in db.books.find(
            {"book_id": {"$in": ordered}},
            {"_id": 0, "book_id": 1, "title": 1, "authors": 1},
        )
    }
    return [
        {
            "book_id": book_id,
            "title": info.get(book_id, {}).get("title"),
            "authors": info.get(book_id, {}).get("authors"),
            "positive_readers": scores[book_id],
            "ratings_in_selection": counts.get(book_id, 0),
            "in_top_100": book_id in top100,
        }
        for book_id in ordered
    ]


def _connecting_readers(
    books_a: set[int],
    books_b: set[int],
    positive: list[tuple],
    activity: dict[int, int],
    discounted: bool,
) -> set[int]:
    users_a = set()
    users_b = set()
    for user_id, book_id, _row in positive:
        if discounted and activity.get(user_id, 0) >= ACTIVITY_CAP:
            continue
        if book_id in books_a:
            users_a.add(user_id)
        if book_id in books_b:
            users_b.add(user_id)
    return users_a & users_b


def _examples(
    db: Database,
    readers: set[int],
    books_a: set[int],
    books_b: set[int],
    positive: list[tuple],
) -> list[dict]:
    chosen = sorted(readers)[:READER_EXAMPLES]
    picked: dict[int, dict] = {}
    for user_id, book_id, row in positive:
        if user_id not in chosen:
            continue
        slot = picked.setdefault(user_id, {})
        if book_id in books_a and "a" not in slot:
            slot["a"] = row
        if book_id in books_b and book_id != slot.get("a", {}).get("book_id") and "b" not in slot:
            slot["b"] = row
    book_ids = []
    for slot in picked.values():
        for side in ("a", "b"):
            if side in slot:
                book_ids.append(slot[side]["book_id"])
    info = {
        book["book_id"]: book
        for book in db.books.find(
            {"book_id": {"$in": book_ids}},
            {"_id": 0, "book_id": 1, "title": 1},
        )
    }
    examples = []
    for user_id in chosen:
        slot = picked.get(user_id, {})
        if "a" not in slot or "b" not in slot:
            continue
        examples.append(
            {
                "user_id": user_id,
                "book_a": _side(slot["a"], info),
                "book_b": _side(slot["b"], info),
            }
        )
    return examples


def _side(row: dict, info: dict[int, dict]) -> dict:
    book = info.get(row["book_id"], {})
    return {
        "book_id": row["book_id"],
        "title": book.get("title"),
        "rating": row["rating"],
        "source_row": row["source_row"],
    }


def _persistence(full_readers: int, discounted_readers: int, discounted_bridges: int) -> str:
    if discounted_readers >= 20 or discounted_bridges >= 3:
        return "persists"
    if full_readers >= 20 and discounted_readers < 5 and discounted_bridges == 0:
        return "disappears"
    if full_readers > 0 or discounted_readers > 0 or discounted_bridges > 0:
        return "weakens"
    return "none"
