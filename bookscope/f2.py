"""F2 — compare two readers and the books that support the comparison.

Input
    Two user_id values. Each history can be limited to its k earliest ratings
    (file order). A truncated history is labelled and is not a new reader.

Output
    Similarities and disagreements on the books both readers rated, with both
    ratings and their position in ratings.csv. The strength divides the size
    of the overlap by the shorter history, then weights each book by the
    inverse of its popularity. If the readers share fewer than five books,
    the comparison is reported as not meaningful.
"""

from __future__ import annotations

import math
from typing import Any

from pymongo.database import Database

MIN_SHARED_BOOKS = 5
# Écart de 0 ou 1 étoile : les deux lecteurs sont d'accord.
# Écart de 2, 3 ou 4 : ils ne sont pas d'accord.
SIMILAR_MAX_DIFF = 1
DISAGREE_MIN_DIFF = 2
DISPLAY_LIMIT = 8


def compare(
    db: Database,
    user_a: int,
    user_b: int,
    first: dict[int, int] | None = None,
) -> dict[str, Any]:
    """Compare two readers. `first` maps a user_id to a truncation length."""
    if user_a == user_b:
        return {"status": "same_reader", "user_id": user_a}

    # `first` sert au jury : « les 5 premières notes du lecteur 1 ».
    # Ce n'est pas un nouveau lecteur, l'historique est seulement coupé.
    limits = first or {}
    profile_a, ratings_a = _profile(db, user_a, limits.get(user_a))
    profile_b, ratings_b = _profile(db, user_b, limits.get(user_b))
    missing = [profile["user_id"] for profile in (profile_a, profile_b) if profile["ratings_available"] == 0]
    if missing:
        return {"status": "not_found", "user_ids": missing}

    # Livres notés par les deux, dans l'ordre du book_id.
    shared_ids = sorted(set(ratings_a) & set(ratings_b))
    books = _books(db, shared_ids)
    # Popularité = nombre de notes du livre dans la sélection, toutes notes confondues.
    popularity = _popularity(db, shared_ids)
    rows = [_shared_row(book_id, ratings_a, ratings_b, books, popularity) for book_id in shared_ids]

    similar = [row for row in rows if row["difference"] <= SIMILAR_MAX_DIFF]
    disagree = [row for row in rows if row["difference"] >= DISAGREE_MIN_DIFF]
    # Les accords rares d'abord (poids fort), les désaccords les plus grands d'abord.
    similar.sort(key=lambda row: (-row["weight"], row["book_id"]))
    disagree.sort(key=lambda row: (-row["difference"], -row["weight"], row["book_id"]))

    # Le recouvrement divise par le plus court des deux historiques,
    # pour ne pas punir quelqu'un qui a noté peu de livres.
    shorter = min(profile_a["ratings_used"], profile_b["ratings_used"])
    overlap = len(rows) / shorter if shorter else 0.0
    weighted = _weighted_agreement(rows)
    # Moins de 5 livres en commun : on affiche quand même les livres,
    # mais on dit que la comparaison n'est pas significative.
    meaningful = len(rows) >= MIN_SHARED_BOOKS

    return {
        "status": "ok" if meaningful else "too_few_shared_books",
        "meaningful": meaningful,
        "parameters": {
            "min_shared_books": MIN_SHARED_BOOKS,
            "similar_max_difference": SIMILAR_MAX_DIFF,
            "disagreement_min_difference": DISAGREE_MIN_DIFF,
        },
        "reader_a": profile_a,
        "reader_b": profile_b,
        "shared_books": len(rows),
        "overlap_with_shorter_history": round(overlap, 4),
        "popularity_weighted_agreement": None if not rows else round(weighted, 4),
        "strength": None if not meaningful else round(overlap * weighted, 4),
        "similarities_total": len(similar),
        "disagreements_total": len(disagree),
        "similarities": similar[:DISPLAY_LIMIT],
        "disagreements": disagree[:DISPLAY_LIMIT],
    }


def _profile(db: Database, user_id: int, first: int | None) -> tuple[dict, dict[int, dict]]:
    cursor = db.ratings.find(
        {"user_id": user_id},
        {"_id": 0, "book_id": 1, "rating": 1, "source_row": 1},
    )
    # L'ordre du fichier est le temps. Les « premières notes » sont les plus anciennes.
    history = sorted(cursor, key=lambda row: row["source_row"])
    used = history if first is None else history[:first]
    profile = {
        "user_id": user_id,
        "ratings_available": len(history),
        "ratings_used": len(used),
        "truncated": first is not None,
        "kept_earliest": first,
    }
    return profile, {row["book_id"]: row for row in used}


def _books(db: Database, book_ids: list[int]) -> dict[int, dict]:
    if not book_ids:
        return {}
    cursor = db.books.find(
        {"book_id": {"$in": book_ids}},
        {"_id": 0, "book_id": 1, "title": 1, "authors": 1},
    )
    return {book["book_id"]: book for book in cursor}


def _popularity(db: Database, book_ids: list[int]) -> dict[int, int]:
    if not book_ids:
        return {}
    rows = db.ratings.aggregate(
        [
            {"$match": {"book_id": {"$in": book_ids}}},
            {"$group": {"_id": "$book_id", "n": {"$sum": 1}}},
        ]
    )
    return {row["_id"]: row["n"] for row in rows}


def _shared_row(
    book_id: int,
    ratings_a: dict[int, dict],
    ratings_b: dict[int, dict],
    books: dict[int, dict],
    popularity: dict[int, int],
) -> dict:
    left = ratings_a[book_id]
    right = ratings_b[book_id]
    book = books.get(book_id, {})
    readers = popularity.get(book_id, 1)
    return {
        "book_id": book_id,
        "title": book.get("title"),
        "authors": book.get("authors"),
        "rating_a": left["rating"],
        "source_row_a": left["source_row"],
        "rating_b": right["rating"],
        "source_row_b": right["source_row"],
        "difference": abs(left["rating"] - right["rating"]),
        "ratings_in_selection": readers,
        # Un livre noté par tout le monde pèse moins : poids = 1 / racine(popularité).
        "weight": round(1 / math.sqrt(readers), 4),
    }


def _weighted_agreement(rows: list[dict]) -> float:
    if not rows:
        return 0.0
    # 1 - écart/4 vaut 1 si les notes sont identiques, et 0 si l'écart est de 4.
    # Chaque livre est multiplié par son poids, donc un livre rare compte plus.
    total_weight = sum(row["weight"] for row in rows)
    agreed = sum((1 - row["difference"] / 4) * row["weight"] for row in rows)
    return agreed / total_weight
