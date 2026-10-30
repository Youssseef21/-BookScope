"""F1 — explore one book and the evidence behind its connections.

Input
    A book_id, or a title. A title that matches several books is returned as
    a list. The service does not choose one of them.

Output
    The catalogue record, then two kinds of related books:
    shared readership (readers who gave both books at least 4/5) and shared
    content tags. A third item shows a link that exists only through a status
    tag or a tag present on at least half of the catalogue.

    Every connection lists the ratings or tag counts that support it.
"""

from __future__ import annotations

import math
import re
from typing import Any

from pymongo.database import Database

from bookscope.tags import COMMON_TAG_FRACTION, is_extremely_common, is_status

MIN_RATING = 4
MIN_SHARED_READERS = 5
MIN_TAG_COUNT = 50
READER_LINKS = 5
THEME_LINKS = 5
EVIDENCE_LIMIT = 10


def explore(db: Database, query: str | int) -> dict[str, Any]:
    """Explore a book. `query` is a book_id or a title."""
    matches = resolve_books(db, query)
    if not matches:
        return {"status": "not_found", "query": query}
    if len(matches) > 1:
        return {
            "status": "ambiguous",
            "query": query,
            "matches": [_public_book(book) for book in matches],
        }

    book = matches[0]
    catalogue_size, tag_df = _tag_frequencies(db)
    described = _describe_book(book, tag_df, catalogue_size)
    seed_ratings = _positive_ratings(db, book["book_id"])
    reader_links = _reader_links(db, book, seed_ratings, tag_df, catalogue_size)
    already = {item["book_id"] for item in reader_links}
    theme_links = _theme_links(db, book, described["content_tags"], tag_df, catalogue_size, already)
    weak_link = _common_tag_only(
        db, book, described["ignored_tags"], described["content_tags"], seed_ratings
    )

    connections = reader_links + theme_links
    if weak_link is not None:
        connections.append(weak_link)

    return {
        "status": "ok",
        "query": query,
        "parameters": {
            "min_rating": MIN_RATING,
            "min_shared_readers": MIN_SHARED_READERS,
            "min_tag_count": MIN_TAG_COUNT,
            "common_tag_fraction": COMMON_TAG_FRACTION,
            "reader_links": READER_LINKS,
            "theme_links": THEME_LINKS,
        },
        "book": {
            **_public_book(book),
            "ratings_in_selection": db.ratings.count_documents({"book_id": book["book_id"]}),
            "positive_ratings": len(seed_ratings),
            "content_tags": described["content_tags"],
            "ignored_tags": described["ignored_tags"],
        },
        "connections": connections,
    }


def resolve_books(db: Database, query: str | int) -> list[dict]:
    if isinstance(query, int) or (isinstance(query, str) and query.strip().isdigit()):
        book = db.books.find_one({"book_id": int(query)})
        return [] if book is None else [book]

    title = str(query).strip()
    exact = _find_titles(db, title, exact=True)
    if exact:
        return exact
    if len(title) < 3:
        return []
    return _find_titles(db, title, exact=False)


def _find_titles(db: Database, title: str, exact: bool) -> list[dict]:
    pattern = re.escape(title)
    if exact:
        pattern = f"^{pattern}$"
    regex = {"$regex": pattern, "$options": "i"}
    books = list(
        db.books.find({"$or": [{"title": regex}, {"original_title": regex}]}).sort("book_id", 1)
    )
    return books


def _public_book(book: dict) -> dict:
    return {
        "book_id": book["book_id"],
        "title": book.get("title"),
        "original_title": book.get("original_title"),
        "authors": book.get("authors"),
        "original_publication_year": book.get("original_publication_year"),
        "language_code": book.get("language_code"),
    }


def _tag_frequencies(db: Database) -> tuple[int, dict[str, int]]:
    catalogue_size = db.books.count_documents({})
    rows = db.books.aggregate(
        [
            {"$unwind": "$tags"},
            {"$group": {"_id": "$tags.name", "n": {"$sum": 1}}},
        ]
    )
    return catalogue_size, {row["_id"]: row["n"] for row in rows}


def _describe_book(book: dict, tag_df: dict[str, int], catalogue_size: int) -> dict:
    content: list[dict] = []
    ignored: list[dict] = []
    for tag in book.get("tags", []):
        item = {
            "tag_id": tag["tag_id"],
            "name": tag["name"],
            "count": tag["count"],
            "books_with_tag": tag_df.get(tag["name"], 0),
        }
        common = is_extremely_common(tag["name"], item["books_with_tag"], catalogue_size)
        if common or is_status(tag["name"]):
            item["reason"] = "status" if is_status(tag["name"]) else "extremely_common"
            ignored.append(item)
        elif tag["count"] >= MIN_TAG_COUNT:
            content.append(item)
    content.sort(key=lambda item: (-item["count"], item["name"]))
    ignored.sort(key=lambda item: (-item["count"], item["name"]))
    return {"content_tags": content[:8], "ignored_tags": ignored[:6]}


def _positive_ratings(db: Database, book_id: int) -> dict[int, dict]:
    cursor = db.ratings.find(
        {"book_id": book_id, "rating": {"$gte": MIN_RATING}},
        {"_id": 0, "user_id": 1, "rating": 1, "source_row": 1},
    )
    return {doc["user_id"]: doc for doc in cursor}


def _reader_links(
    db: Database,
    book: dict,
    seed_ratings: dict[int, dict],
    tag_df: dict[str, int],
    catalogue_size: int,
) -> list[dict]:
    if len(seed_ratings) < MIN_SHARED_READERS:
        return []

    grouped = db.ratings.aggregate(
        [
            {
                "$match": {
                    "user_id": {"$in": list(seed_ratings)},
                    "book_id": {"$ne": book["book_id"]},
                    "rating": {"$gte": MIN_RATING},
                }
            },
            {
                "$group": {
                    "_id": "$book_id",
                    "shared": {"$sum": 1},
                    "readers": {
                        "$push": {
                            "user_id": "$user_id",
                            "rating": "$rating",
                            "source_row": "$source_row",
                        }
                    },
                }
            },
            {"$match": {"shared": {"$gte": MIN_SHARED_READERS}}},
        ],
        allowDiskUse=True,
    )
    candidates = list(grouped)
    if not candidates:
        return []

    popularity = _positive_counts(db, [book["book_id"], *[row["_id"] for row in candidates]])
    n_seed = popularity.get(book["book_id"], len(seed_ratings))
    ranked = []
    for row in candidates:
        n_other = popularity.get(row["_id"], row["shared"])
        strength = row["shared"] / math.sqrt(n_seed * n_other) if n_seed and n_other else 0.0
        ranked.append((strength, row["shared"], row["_id"], row["readers"]))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    chosen = ranked[:READER_LINKS]

    books = _books_by_id(db, [item[2] for item in chosen])
    links = []
    for strength, shared, book_id, readers in chosen:
        other = books[book_id]
        evidence = []
        for reader in sorted(readers, key=lambda item: item["user_id"])[:EVIDENCE_LIMIT]:
            seed = seed_ratings[reader["user_id"]]
            evidence.append(
                {
                    "user_id": reader["user_id"],
                    "rating_on_book": seed["rating"],
                    "source_row_on_book": seed["source_row"],
                    "rating_on_related": reader["rating"],
                    "source_row_on_related": reader["source_row"],
                }
            )
        links.append(
            {
                "kind": "shared_readership",
                "book": _public_book(other),
                "book_id": book_id,
                "strength": round(strength, 4),
                "shared_readers": shared,
                "ratings_evidence": evidence,
                "ratings_evidence_total": shared,
                "shared_tags": _shared_content_tags(book, other, tag_df, catalogue_size),
            }
        )
    return links


def _positive_counts(db: Database, book_ids: list[int]) -> dict[int, int]:
    rows = db.ratings.aggregate(
        [
            {"$match": {"book_id": {"$in": book_ids}, "rating": {"$gte": MIN_RATING}}},
            {"$group": {"_id": "$book_id", "n": {"$sum": 1}}},
        ]
    )
    return {row["_id"]: row["n"] for row in rows}


def _books_by_id(db: Database, book_ids: list[int]) -> dict[int, dict]:
    cursor = db.books.find({"book_id": {"$in": book_ids}})
    return {book["book_id"]: book for book in cursor}


def _shared_content_tags(
    left: dict,
    right: dict,
    tag_df: dict[str, int] | None = None,
    catalogue_size: int = 0,
) -> list[dict]:
    right_by_name = {tag["name"]: tag for tag in right.get("tags", [])}
    shared = []
    for tag in left.get("tags", []):
        if is_status(tag["name"]) or tag["count"] < MIN_TAG_COUNT:
            continue
        if tag_df is not None and is_extremely_common(tag["name"], tag_df.get(tag["name"], 0), catalogue_size):
            continue
        other = right_by_name.get(tag["name"])
        if other is None or other["count"] < MIN_TAG_COUNT:
            continue
        shared.append(
            {
                "tag_id": tag["tag_id"],
                "name": tag["name"],
                "count_on_book": tag["count"],
                "count_on_related": other["count"],
            }
        )
    shared.sort(key=lambda item: (-min(item["count_on_book"], item["count_on_related"]), item["name"]))
    return shared[:8]


def _theme_links(
    db: Database,
    book: dict,
    content_tags: list[dict],
    tag_df: dict[str, int],
    catalogue_size: int,
    exclude: set[int],
) -> list[dict]:
    names = [tag["name"] for tag in content_tags]
    if not names:
        return []
    cursor = db.books.find(
        {"book_id": {"$nin": [book["book_id"], *exclude]}, "tags.name": {"$in": names}},
        {"book_id": 1, "title": 1, "authors": 1, "original_title": 1, "original_publication_year": 1, "language_code": 1, "tags": 1},
    )
    ranked = []
    for other in cursor:
        shared = _shared_content_tags(book, other, tag_df, catalogue_size)
        if not shared:
            continue
        score = len(shared)
        support = sum(min(tag["count_on_book"], tag["count_on_related"]) for tag in shared)
        ranked.append((score, support, other["book_id"], other, shared))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))

    links = []
    for _score, _support, book_id, other, shared in ranked[:THEME_LINKS]:
        links.append(
            {
                "kind": "thematic",
                "book": _public_book(other),
                "book_id": book_id,
                "strength": len(shared),
                "shared_readers": None,
                "ratings_evidence": [],
                "ratings_evidence_total": 0,
                "shared_tags": shared,
            }
        )
    return links


def _common_tag_only(
    db: Database,
    book: dict,
    ignored_tags: list[dict],
    content_tags: list[dict],
    seed_ratings: dict[int, dict],
) -> dict | None:
    if not ignored_tags:
        return None
    common = ignored_tags[0]
    specific_names = [tag["name"] for tag in content_tags]
    cursor = (
        db.books.find(
            {"book_id": {"$ne": book["book_id"]}, "tags.name": common["name"]},
            {"book_id": 1, "title": 1, "authors": 1, "original_title": 1, "original_publication_year": 1, "language_code": 1, "tags": 1, "ratings_count": 1},
        )
        .sort([("ratings_count", 1), ("book_id", 1)])
        .limit(250)
    )
    for other in cursor:
        if not _mostly_latin(other.get("title")):
            continue
        if _shares_specific_tag(other, specific_names):
            continue
        shared_readers = 0
        if seed_ratings:
            shared_readers = db.ratings.count_documents(
                {
                    "book_id": other["book_id"],
                    "user_id": {"$in": list(seed_ratings)},
                    "rating": {"$gte": MIN_RATING},
                }
            )
        if shared_readers >= MIN_SHARED_READERS:
            continue
        other_tag = next(tag for tag in other["tags"] if tag["name"] == common["name"])
        return {
            "kind": "common_tag_only",
            "book": _public_book(other),
            "book_id": other["book_id"],
            "strength": 0,
            "shared_readers": shared_readers,
            "ratings_evidence": [],
            "ratings_evidence_total": shared_readers,
            "shared_tags": [
                {
                    "tag_id": common["tag_id"],
                    "name": common["name"],
                    "count_on_book": common["count"],
                    "count_on_related": other_tag["count"],
                    "books_with_tag": common["books_with_tag"],
                    "reason": common["reason"],
                }
            ],
        }
    return None


def _mostly_latin(title: str | None) -> bool:
    letters = [character for character in title or "" if character.isalpha()]
    if len(letters) < 3:
        return False
    latin = sum(1 for character in letters if ord(character) < 128)
    return latin / len(letters) >= 0.8


def _shares_specific_tag(book: dict, names: list[str]) -> bool:
    wanted = set(names)
    return any(
        tag["name"] in wanted and tag["count"] >= MIN_TAG_COUNT for tag in book.get("tags", [])
    )
