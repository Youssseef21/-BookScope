"""Run F1 from the command line.

Examples
    python scripts/explore_book.py 258
    python scripts/explore_book.py "Selected Poems"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bookscope.db import connect
from bookscope.f1 import explore


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="F1 — explorer un livre et ses liens")
    parser.add_argument("query", help="book_id ou titre")
    parser.add_argument("--json", action="store_true", help="imprimer le résultat brut")
    args = parser.parse_args()
    query: str | int = int(args.query) if args.query.isdigit() else args.query
    result = explore(connect(), query)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_result(result))


def format_result(result: dict) -> str:
    status = result["status"]
    if status == "not_found":
        return f"Aucun livre pour : {result['query']}"
    if status == "ambiguous":
        lines = [f"Plusieurs livres correspondent à « {result['query']} ». Aucun n'est choisi."]
        for book in result["matches"]:
            lines.append(f"- {book['book_id']} — {book['title']} — {book['authors']}")
        lines.append("Relancez avec un book_id.")
        return "\n".join(lines)

    book = result["book"]
    lines = [
        f"Livre {book['book_id']} — {book['title']}",
        f"Auteur : {book['authors']}",
        f"Année : {book['original_publication_year']}",
        f"Notes dans la sélection : {book['ratings_in_selection']} dont {book['positive_ratings']} à 4 ou 5",
        "",
        "Tags de contenu utilisés :",
    ]
    lines.extend(
        f"- {tag['name']} ({tag['count']} fois sur ce livre, {tag['books_with_tag']} livres du catalogue)"
        for tag in book["content_tags"]
    )
    if not book["content_tags"]:
        lines.append("- aucun")
    lines.append("")
    lines.append("Tags ignorés (statut de lecture, ou présents sur au moins la moitié du catalogue) :")
    lines.extend(
        f"- {tag['name']} ({tag['count']} fois, {tag['reason']})"
        for tag in book["ignored_tags"]
    )

    readers = [link for link in result["connections"] if link["kind"] == "shared_readership"]
    themes = [link for link in result["connections"] if link["kind"] == "thematic"]
    weak = [link for link in result["connections"] if link["kind"] == "common_tag_only"]

    lines.extend(["", "Liens par les lecteurs"])
    lines.extend(_format_links(readers, "Aucun groupe d'au moins 5 lecteurs n'a mis 4 ou 5 aux deux livres."))
    lines.extend(["", "Liens par le thème"])
    lines.extend(_format_links(themes, "Aucun tag de contenu assez précis n'est partagé."))
    lines.extend(["", "Lien qui ne compte pas"])
    lines.extend(
        _format_links(
            weak,
            "Aucun exemple : les livres qui partagent un tag très commun partagent aussi autre chose.",
        )
    )
    return "\n".join(lines)


def _format_links(links: list[dict], empty: str) -> list[str]:
    if not links:
        return [empty]
    lines = []
    for link in links:
        other = link["book"]
        lines.append(f"- {other['book_id']} — {other['title']} — {other['authors']}")
        if link["kind"] == "shared_readership":
            lines.append(
                f"  {link['shared_readers']} lecteurs ont mis au moins 4/5 aux deux livres. "
                f"Force {link['strength']} (plus elle est haute, plus le lien dépasse la simple popularité)."
            )
            for row in link["ratings_evidence"]:
                lines.append(
                    f"  lecteur {row['user_id']} : {row['rating_on_book']}/5 sur ce livre "
                    f"(position {row['source_row_on_book']} dans ratings.csv), "
                    f"{row['rating_on_related']}/5 sur l'autre "
                    f"(position {row['source_row_on_related']})"
                )
            hidden = link["ratings_evidence_total"] - len(link["ratings_evidence"])
            if hidden > 0:
                lines.append(f"  … et {hidden} autres lecteurs.")
        if link["shared_tags"]:
            rendered = ", ".join(
                f"{tag['name']} ({tag['count_on_book']} / {tag['count_on_related']})"
                for tag in link["shared_tags"]
            )
            lines.append(f"  Tags : {rendered}")
        if link["kind"] == "common_tag_only":
            tag = link["shared_tags"][0]
            lines.append(
                f"  Seul point commun retenu : le tag {tag['name']} ({tag['reason']}), "
                f"présent sur {tag['books_with_tag']} livres. "
                f"Lecteurs en commun avec une note d'au moins 4 : {link['shared_readers']}."
            )
    return lines


if __name__ == "__main__":
    main()
