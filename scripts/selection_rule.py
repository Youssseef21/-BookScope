"""Selection registered for BookScope.

A book is in the mystery family when at least one listed tag has a count
of MIN_TAG_COUNT or more. A reader is kept when user_id % USER_MOD is in
USER_REMAINDERS and the reader has rated at least MIN_MYSTERY_RATINGS
mystery-family books. Every rating of a kept reader stays in the selection.
"""

MYSTERY_TAGS = frozenset(
    {
        "mystery",
        "mysteries",
        "thriller",
        "thrillers",
        "crime",
        "crime-fiction",
        "detective",
        "detectives",
        "suspense",
        "murder-mystery",
        "murder",
        "mystery-thriller",
        "mystery-suspense",
        "crime-thriller",
        "cozy-mystery",
    }
)

MIN_TAG_COUNT = 50
MIN_MYSTERY_RATINGS = 8
USER_MOD = 5
USER_REMAINDERS = frozenset({1, 2})

# File order in ratings.csv is the documented time order.
# For each selected reader, the last E3_TAIL ratings are the evaluation set.
# The F6_BEFORE ratings just before that tail are the update batch.
E3_TAIL = 5
F6_BEFORE = 5
