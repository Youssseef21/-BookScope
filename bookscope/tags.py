"""Separate catalogue tags from shelf labels and very common tags.

A status tag describes what a reader did with a book (to-read, owned, kindle).
A tag present on at least half of the catalogue is too broad to explain a link
by itself: fiction, for example, sits on almost every book.
"""

from __future__ import annotations

COMMON_TAG_FRACTION = 0.5

STATUS_TAGS = frozenset(
    {
        "to-read",
        "currently-reading",
        "favorites",
        "favourites",
        "favorite",
        "all-time-favorites",
        "books-i-own",
        "owned",
        "owned-books",
        "i-own",
        "my-books",
        "kindle",
        "default",
        "audiobook",
        "audiobooks",
        "audio",
        "to-buy",
        "library",
        "ebook",
        "ebooks",
        "book-club",
        "bookclub",
        "favorite-books",
        "faves",
        "favs",
        "shelfari-favorites",
        "wish-list",
        "wishlist",
        "abandoned",
        "dnf",
        "did-not-finish",
        "unfinished",
        "reread",
        "re-read",
        "read",
        "maybe",
        "recommended",
        "tbr",
        "want-to-read",
    }
)


def is_status(name: str) -> bool:
    # « to-read », « favorites », « owned » : l'étagère, pas l'histoire.
    # « 5-stars » ou « read-in-2015 » aussi : ce sont des marques de lecture.
    if name in STATUS_TAGS or name.endswith("-stars") or name.endswith("-star"):
        return True
    return name.startswith("read-in-") or name.startswith("read-")


def is_extremely_common(name: str, books_with_tag: int, catalogue_size: int) -> bool:
    # Un tag de statut est toujours trop vague pour justifier un lien.
    if is_status(name):
        return True
    if catalogue_size <= 0:
        return False
    # « fiction » est sur 9097 livres sur 10000, donc au-dessus de 50 %.
    return books_with_tag / catalogue_size >= COMMON_TAG_FRACTION
