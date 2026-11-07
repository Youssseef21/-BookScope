"""F3 — recommend books a reader has not rated.

Input
    A user_id. The history can be limited to its k earliest ratings. A
    truncated history is labelled and is not counted as a new reader.

Output
    A ranked list. With at least five ratings, the list comes from readers
    who liked the same less-popular books. Each item names those readers and
    the shared books. With fewer than five ratings, the list comes from the
    content tags of the books already rated. Either way the result reports
    how many recommendations sit in the 100 most rated books, and the median
    popularity of the list compared with that top 100.
"""

from __future__ import annotations

import math
from typing import Any

from pymongo.database import Database

from bookscope.tags import is_extremely_common, is_status

MIN_RATINGS_FOR_NEIGHBORS = 5
MIN_RATING = 4
# On cherche des voisins à partir des livres les moins populaires aimés par le lecteur,
# pas à partir de Harry Potter : sinon les propositions recopient le classement général.
SEED_BOOKS = 12
NEIGHBOR_LIMIT = 40
RECOMMENDATIONS = 10
MIN_SUPPORTERS = 2
TOP_POPULAR = 100
MIN_TAG_COUNT = 50
MIN_BOOK_RATINGS = 20
EVIDENCE_LIMIT = 3


def recommend(db: Database, user_id: int, first: int | None = None) -> dict[str, Any]:
    """Recommend books for one reader. `first` keeps only the earliest ratings."""
    profile, history = _history(db, user_id, first)
    if profile["ratings_available"] == 0:
        return {"status": "not_found", "user_id": user_id}

    counts = _rating_counts(db)
    top100 = _top_book_ids(counts)
    liked = [row for row in history if row["rating"] >= MIN_RATING]
    # S'il n'a aucune note >= 4, on se rabat sur toutes ses notes pour les tags.
    taste = liked or history
    already = {row["book_id"] for row in history}

    mode = "neighbors"
    fallback = None
    # Moins de 5 notes : on ne cherche pas de voisins, seulement les tags.
    if profile["ratings_used"] < MIN_RATINGS_FOR_NEIGHBORS:
        mode = "short_history"
        recommendations = _from_tags(db, taste, already, counts)
    else:
        recommendations = _from_neighbors(db, profile, taste, already, counts)
        if not recommendations:
            mode = "short_history"
            fallback = "no_neighbor"
            recommendations = _from_tags(db, taste, already, counts)

    # La liste « à lire » est une étiquette, pas le signal qui classe les livres.
    reading_list = {
        row["book_id"]
        for row in db.to_read.find({"user_id": user_id}, {"_id": 0, "book_id": 1})
    }
    for item in recommendations:
        item["on_reading_list"] = item["book_id"] in reading_list

    return {
        "status": "ok" if recommendations else "no_recommendation",
        "mode": mode,
        "fallback": fallback,
        "reader": {
            **profile,
            "positive_ratings": len(liked),
        },
        "parameters": {
            "min_ratings_for_neighbors": MIN_RATINGS_FOR_NEIGHBORS,
            "min_rating": MIN_RATING,
            "recommendations": RECOMMENDATIONS,
            "top_popular": TOP_POPULAR,
        },
        "popularity": _popularity_report(recommendations, counts, top100),
        "recommendations": recommendations,
    }


def _history(db: Database, user_id: int, first: int | None) -> tuple[dict, list[dict]]:
    cursor = db.ratings.find(
        {"user_id": user_id},
        {"_id": 0, "book_id": 1, "rating": 1, "source_row": 1},
    )
    history = sorted(cursor, key=lambda row: row["source_row"])
    used = history if first is None else history[:first]
    profile = {
        "user_id": user_id,
        "ratings_available": len(history),
        "ratings_used": len(used),
        "truncated": first is not None,
        "kept_earliest": first,
    }
    return profile, used


def _rating_counts(db: Database) -> dict[int, int]:
    rows = db.ratings.aggregate(
        [{"$group": {"_id": "$book_id", "n": {"$sum": 1}}}],
        allowDiskUse=True,
    )
    return {row["_id"]: row["n"] for row in rows}


def _top_book_ids(counts: dict[int, int]) -> list[int]:
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [book_id for book_id, _count in ordered[:TOP_POPULAR]]


def _from_neighbors(
    db: Database,
    profile: dict,
    taste: list[dict],
    already: set[int],
    counts: dict[int, int],
) -> list[dict]:
    min_shared = 2 if profile["ratings_used"] < 15 else 3
    seeds = sorted(taste, key=lambda row: (counts.get(row["book_id"], 0), row["book_id"]))[:SEED_BOOKS]
    seed_ids = [row["book_id"] for row in seeds]
    by_book = {row["book_id"]: row for row in taste}
    if len(seed_ids) < min_shared:
        return []

    grouped = list(
        db.ratings.aggregate(
            [
                {
                    "$match": {
                        "book_id": {"$in": seed_ids},
                        "rating": {"$gte": MIN_RATING},
                        "user_id": {"$ne": profile["user_id"]},
                    }
                },
                {
                    "$group": {
                        "_id": "$user_id",
                        "shared": {"$sum": 1},
                        "books": {
                            "$push": {
                                "book_id": "$book_id",
                                "rating": "$rating",
                                "source_row": "$source_row",
                            }
                        },
                    }
                },
                {"$match": {"shared": {"$gte": min_shared}}},
                {"$sort": {"shared": -1, "_id": 1}},
                {"$limit": NEIGHBOR_LIMIT},
            ],
            allowDiskUse=True,
        )
    )
    if not grouped:
        return []

    neighbor_ids = [row["_id"] for row in grouped]
    activity = {
        row["_id"]: row["n"]
        for row in db.ratings.aggregate(
            [
                {"$match": {"user_id": {"$in": neighbor_ids}}},
                {"$group": {"_id": "$user_id", "n": {"$sum": 1}}},
            ]
        )
    }
    neighbors = {}
    for row in grouped:
        # Un voisin qui a noté beaucoup de livres pèse moins.
        weight = row["shared"] / math.sqrt(activity.get(row["_id"], row["shared"]))
        neighbors[row["_id"]] = {
            "weight": weight,
            "books": {item["book_id"]: item for item in row["books"]},
        }

    candidates = db.ratings.aggregate(
        [
            {
                "$match": {
                    "user_id": {"$in": neighbor_ids},
                    "rating": {"$gte": MIN_RATING},
                    "book_id": {"$nin": list(already)},
                }
            },
            {
                "$group": {
                    "_id": "$book_id",
                    "readers": {
                        "$push": {
                            "user_id": "$user_id",
                            "rating": "$rating",
                            "source_row": "$source_row",
                        }
                    },
                }
            },
        ],
        allowDiskUse=True,
    )
    ranked = []
    for row in candidates:
        supporters = [reader for reader in row["readers"] if reader["user_id"] in neighbors]
        if len(supporters) < MIN_SUPPORTERS:
            continue
        support = sum(neighbors[reader["user_id"]]["weight"] for reader in supporters)
        popularity = counts.get(row["_id"], 1)
        # Le score baisse quand le livre proposé est déjà très noté.
        score = support / math.sqrt(popularity)
        ranked.append((score, row["_id"], supporters, popularity))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    chosen = ranked[:RECOMMENDATIONS]
    books = _book_info(db, [item[1] for item in chosen] + seed_ids)
    top100 = set(_top_book_ids(counts))

    results = []
    for score, book_id, supporters, popularity in chosen:
        supporters.sort(key=lambda reader: (-neighbors[reader["user_id"]]["weight"], reader["user_id"]))
        evidence = []
        for reader in supporters[:EVIDENCE_LIMIT]:
            shared_books = neighbors[reader["user_id"]]["books"]
            shared_ids = sorted(shared_books, key=lambda item: (counts.get(item, 0), item))[:2]
            evidence.append(
                {
                    "user_id": reader["user_id"],
                    "rating": reader["rating"],
                    "source_row": reader["source_row"],
                    "shared_books": [
                        {
                            "book_id": shared_id,
                            "title": books.get(shared_id, {}).get("title"),
                            "rating": by_book[shared_id]["rating"],
                            "source_row": by_book[shared_id]["source_row"],
                        }
                        for shared_id in shared_ids
                    ],
                }
            )
        info = books.get(book_id, {})
        results.append(
            {
                "book_id": book_id,
                "title": info.get("title"),
                "authors": info.get("authors"),
                "score": round(score, 4),
                "ratings_in_selection": popularity,
                "in_top_100": book_id in top100,
                "supporters_total": len(supporters),
                "supporters": evidence,
                "shared_tags": [],
            }
        )
    return results


def _from_tags(
    db: Database,
    taste: list[dict],
    already: set[int],
    counts: dict[int, int],
) -> list[dict]:
    taste_books = _book_info(db, [row["book_id"] for row in taste])
    catalogue_size = db.books.count_documents({})
    tag_df = {
        row["_id"]: row["n"]
        for row in db.books.aggregate(
            [{"$unwind": "$tags"}, {"$group": {"_id": "$tags.name", "n": {"$sum": 1}}}]
        )
    }
    by_book = {row["book_id"]: row for row in taste}
    useful: dict[str, dict] = {}
    for book_id, book in taste_books.items():
        for tag in book.get("tags", []):
            if tag["count"] < MIN_TAG_COUNT:
                continue
            if is_status(tag["name"]) or is_extremely_common(tag["name"], tag_df.get(tag["name"], 0), catalogue_size):
                continue
            current = useful.get(tag["name"])
            mine = by_book[book_id]
            if current is None or mine["rating"] > current["rating"]:
                useful[tag["name"]] = {
                    "name": tag["name"],
                    "tag_id": tag["tag_id"],
                    "from_book_id": book_id,
                    "from_title": book.get("title"),
                    "rating": mine["rating"],
                    "source_row": mine["source_row"],
                    "count_on_source": tag["count"],
                }
    if not useful:
        return []

    cursor = db.books.find(
        {"book_id": {"$nin": list(already)}, "tags.name": {"$in": list(useful)}},
        {"_id": 0, "book_id": 1, "title": 1, "authors": 1, "tags": 1},
    )
    ranked = []
    for book in cursor:
        shared = []
        for tag in book.get("tags", []):
            source = useful.get(tag["name"])
            if source is None or tag["count"] < MIN_TAG_COUNT:
                continue
            shared.append({**source, "count_on_recommended": tag["count"]})
        if not shared:
            continue
        popularity = counts.get(book["book_id"], 0)
        if popularity < MIN_BOOK_RATINGS:
            continue
        specificity = sum(1 / math.log(2 + tag_df.get(tag["name"], 1)) for tag in shared)
        score = specificity / math.log(2 + popularity)
        ranked.append((score, book["book_id"], book, shared, popularity))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    top100 = set(_top_book_ids(counts))
    results = []
    for score, book_id, book, shared, popularity in ranked[:RECOMMENDATIONS]:
        shared.sort(key=lambda tag: (tag_df.get(tag["name"], 0), tag["name"]))
        results.append(
            {
                "book_id": book_id,
                "title": book.get("title"),
                "authors": book.get("authors"),
                "score": round(score, 4),
                "ratings_in_selection": popularity,
                "in_top_100": book_id in top100,
                "supporters_total": 0,
                "supporters": [],
                "shared_tags": shared[:6],
            }
        )
    return results


def _book_info(db: Database, book_ids: list[int]) -> dict[int, dict]:
    if not book_ids:
        return {}
    cursor = db.books.find(
        {"book_id": {"$in": book_ids}},
        {"_id": 0, "book_id": 1, "title": 1, "authors": 1, "tags": 1},
    )
    return {book["book_id"]: book for book in cursor}


def _popularity_report(recommendations: list[dict], counts: dict[int, int], top100: list[int]) -> dict:
    top_counts = [counts[book_id] for book_id in top100 if book_id in counts]
    rec_counts = [item["ratings_in_selection"] for item in recommendations]
    in_top = sum(1 for item in recommendations if item["book_id"] in set(top100))
    total = len(recommendations)
    return {
        "recommendations": total,
        "in_top_100": in_top,
        "share_in_top_100": round(in_top / total, 4) if total else None,
        "median_popularity": _median(rec_counts),
        "median_popularity_of_top_100": _median(top_counts),
    }


def _median(values: list[int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2
