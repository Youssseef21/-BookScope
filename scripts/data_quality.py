"""Problèmes de qualité présents dans les données de la sélection.

Lit les CSV d'origine et la sélection. Chaque problème est compté avec des
identifiants qu'on peut retrouver dans les fichiers. Le résultat est écrit
dans data/measurements/data_quality.json.

    python scripts/data_quality.py
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import selection_rule as rule
from bookscope.tags import is_status

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
SEL = ROOT / "data" / "selection"
OUTPUT = ROOT / "data" / "measurements" / "data_quality.json"


def read_csv(path: Path):
    with path.open(encoding="utf-8", newline="") as handle:
        yield from csv.DictReader(handle)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    users = {int(row["user_id"]) for row in read_csv(SEL / "readers.csv")}
    summary = json.loads((SEL / "summary.json").read_text(encoding="utf-8"))

    books = {int(row["book_id"]): row for row in read_csv(RAW / "books.csv")}
    goodreads_to_book = {int(row["goodreads_book_id"]): int(row["book_id"]) for row in books.values()}
    tags = {int(row["tag_id"]): row["tag_name"] for row in read_csv(RAW / "tags.csv")}

    selection_books: set[int] = set()
    ratings_rows = 0
    pair_seen: Counter = Counter()
    first_row_of_pair: dict[tuple[int, int], int] = {}
    for source_row, row in enumerate(read_csv(RAW / "ratings.csv"), start=1):
        user_id = int(row["user_id"])
        if user_id not in users:
            continue
        book_id = int(row["book_id"])
        ratings_rows += 1
        selection_books.add(book_id)
        key = (user_id, book_id)
        pair_seen[key] += 1
        first_row_of_pair.setdefault(key, source_row)
    duplicates = [key for key, n in pair_seen.items() if n > 1]

    rated_by_user: dict[int, set[int]] = defaultdict(set)
    for user_id, book_id in pair_seen:
        rated_by_user[user_id].add(book_id)

    to_read_rows = 0
    to_read_and_rated: list[tuple[int, int]] = []
    to_read_pairs: Counter = Counter()
    for row in read_csv(RAW / "to_read.csv"):
        user_id = int(row["user_id"])
        if user_id not in users:
            continue
        book_id = int(row["book_id"])
        to_read_rows += 1
        to_read_pairs[(user_id, book_id)] += 1
        if book_id in rated_by_user[user_id]:
            to_read_and_rated.append((user_id, book_id))
    to_read_duplicates = [key for key, n in to_read_pairs.items() if n > 1]

    issues: list[dict] = []

    # 1. livres sans ISBN, sans année ou sans titre original
    missing_isbn = sorted(b for b in selection_books if not books[b]["isbn"].strip())
    issues.append(
        issue(
            "Livre sans ISBN",
            len(missing_isbn),
            len(selection_books),
            missing_isbn[:5],
            "books.csv, colonne isbn vide. Un livre ne peut pas être relié à une édition précise.",
        )
    )
    missing_year = sorted(b for b in selection_books if not books[b]["original_publication_year"].strip())
    issues.append(
        issue(
            "Livre sans année de publication",
            len(missing_year),
            len(selection_books),
            missing_year[:5],
            "books.csv, colonne original_publication_year vide.",
        )
    )
    missing_original = sorted(b for b in selection_books if not books[b]["original_title"].strip())
    issues.append(
        issue(
            "Livre sans titre original",
            len(missing_original),
            len(selection_books),
            missing_original[:5],
            "books.csv, colonne original_title vide : la recherche par titre ne trouve que le titre de l'édition.",
        )
    )

    # 2. titres portés par plusieurs livres
    by_title: dict[str, list[int]] = defaultdict(list)
    for book_id in selection_books:
        by_title[books[book_id]["title"].strip().casefold()].append(book_id)
    ambiguous = {title: ids for title, ids in by_title.items() if len(ids) > 1}
    example_titles = sorted(ambiguous.items(), key=lambda item: (-len(item[1]), item[0]))[:3]
    issues.append(
        {
            "issue": "Titre porté par plusieurs livres",
            "count": len(ambiguous),
            "out_of": len(by_title),
            "examples": [{"title": title, "book_ids": sorted(ids)} for title, ids in example_titles],
            "effect": "Un titre n'identifie pas un livre : F1 et F4 affichent l'ambiguïté au lieu de choisir.",
        }
    )

    # 3. tags qui décrivent un statut de lecture
    status_rows = 0
    other_rows = 0
    tag_rows = 0
    negative: list[tuple[int, int, int]] = []
    unresolved_goodreads: list[int] = []
    unknown_tag: list[int] = []
    status_examples: Counter = Counter()
    for row in read_csv(RAW / "book_tags.csv"):
        goodreads = int(row["goodreads_book_id"])
        count = int(row["count"])
        tag_id = int(row["tag_id"])
        if goodreads not in goodreads_to_book:
            unresolved_goodreads.append(goodreads)
            continue
        if tag_id not in tags:
            unknown_tag.append(tag_id)
            continue
        if goodreads_to_book[goodreads] not in selection_books:
            continue
        tag_rows += 1
        if count < 0:
            negative.append((goodreads, tag_id, count))
        if is_status(tags[tag_id]):
            status_rows += count
            status_examples[tags[tag_id]] += count
        else:
            other_rows += count
    total_tag_weight = status_rows + other_rows
    issues.append(
        {
            "issue": "Tags qui décrivent un statut de lecture et non le contenu",
            "count": status_rows,
            "out_of": total_tag_weight,
            "examples": [{"tag": name, "uses": uses} for name, uses in status_examples.most_common(5)],
            "effect": "Part des usages de tags qui sont des étagères personnelles (to-read, favorites). F1, F4 et F5 les écartent.",
        }
    )
    issues.append(
        issue(
            "Compteur de tag négatif",
            len(negative),
            None,
            [{"goodreads_book_id": g, "tag_id": t, "count": c} for g, t, c in negative[:5]],
            "book_tags.csv : un count négatif n'a pas de sens ; un seuil count >= 50 l'élimine.",
        )
    )

    # 4. livres à lire déjà notés par le même lecteur
    issues.append(
        issue(
            "Livre à la fois dans la liste à lire et noté par le même lecteur",
            len(to_read_and_rated),
            to_read_rows,
            [{"user_id": u, "book_id": b} for u, b in to_read_and_rated[:5]],
            "to_read.csv recoupe ratings.csv : la liste à lire ne dit pas ce que le lecteur n'a pas lu.",
        )
    )

    # 5. années douteuses
    odd_years = []
    for book_id in sorted(selection_books):
        text = books[book_id]["original_publication_year"].strip()
        if not text:
            continue
        try:
            year = int(float(text))
        except ValueError:
            odd_years.append((book_id, text))
            continue
        if year < 1000 or year > 2017:
            odd_years.append((book_id, year))
    issues.append(
        issue(
            "Année de publication hors de 1000-2017",
            len(odd_years),
            len(selection_books) - len(missing_year),
            [{"book_id": b, "year": y, "title": books[b]["title"]} for b, y in odd_years[:5]],
            "Années négatives pour les textes anciens : un calcul d'âge d'un livre serait faux.",
        )
    )

    # 6. code de langue
    missing_language = sorted(b for b in selection_books if not books[b]["language_code"].strip())
    issues.append(
        issue(
            "Livre sans code de langue",
            len(missing_language),
            len(selection_books),
            missing_language[:5],
            "books.csv, colonne language_code vide.",
        )
    )

    # 7. doublons dans la sélection
    issues.append(
        issue(
            "Même couple (lecteur, livre) noté deux fois dans la sélection",
            len(duplicates),
            ratings_rows,
            [{"user_id": u, "book_id": b} for u, b in duplicates[:5]],
            "Attendu : 0. La politique F6 est implémentée mais n'a pas de conflit réel à traiter.",
        )
    )
    issues.append(
        issue(
            "Même couple (lecteur, livre) deux fois dans la liste à lire",
            len(to_read_duplicates),
            to_read_rows,
            [{"user_id": u, "book_id": b} for u, b in to_read_duplicates[:5]],
            "Attendu : 0.",
        )
    )
    issues.append(
        issue(
            "book_tags.csv : livre introuvable ou tag inconnu",
            len(unresolved_goodreads) + len(unknown_tag),
            None,
            {"unresolved_goodreads_book_id": sorted(set(unresolved_goodreads))[:5], "unknown_tag_id": sorted(set(unknown_tag))[:5]},
            "Références qui ne peuvent pas être résolues. Attendu : 0.",
        )
    )

    activity = Counter(user_id for user_id, _book_id in pair_seen)
    per_book = Counter(book_id for _user_id, book_id in pair_seen)
    result = {
        "selection": {
            "readers": len(users),
            "books_rated": len(selection_books),
            "ratings": ratings_rows,
            "to_read_entries": to_read_rows,
            "tags_in_tags_csv": len(tags),
            "book_tag_rows_on_selected_books": tag_rows,
            "mystery_books": summary["mystery_books"],
            "ratings_per_reader": distribution(list(activity.values())),
            "ratings_per_book": distribution(list(per_book.values())),
        },
        "issues": issues,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def issue(label: str, count: int, out_of: int | None, examples, effect: str) -> dict:
    return {"issue": label, "count": count, "out_of": out_of, "examples": examples, "effect": effect}


def distribution(values: list[int]) -> dict:
    ordered = sorted(values)

    def pick(percent: float) -> int:
        return ordered[round(percent / 100 * (len(ordered) - 1))]

    return {
        "min": ordered[0],
        "p25": pick(25),
        "p50": pick(50),
        "p75": pick(75),
        "p90": pick(90),
        "p99": pick(99),
        "max": ordered[-1],
        "mean": round(sum(ordered) / len(ordered), 2),
    }


if __name__ == "__main__":
    main()
