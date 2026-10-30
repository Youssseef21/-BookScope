"""Run F3 from the command line.

Examples
    python scripts/recommend.py 1
    python scripts/recommend.py 1 --first 5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bookscope.db import connect
from bookscope.f3 import recommend


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="F3 — recommander des livres à un lecteur")
    parser.add_argument("user_id", type=int)
    parser.add_argument("--first", type=int, help="ne garder que les K premières notes")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.first is not None and args.first < 1:
        parser.error("--first doit être au moins 1")
    result = recommend(connect(), args.user_id, args.first)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_result(result))


def format_result(result: dict) -> str:
    if result["status"] == "not_found":
        return f"Lecteur absent de la sélection : {result['user_id']}"

    reader = result["reader"]
    lines = [
        f"Lecteur {reader['user_id']} : {reader['ratings_used']} notes utilisées, "
        f"dont {reader['positive_ratings']} à 4 ou 5"
    ]
    if reader["truncated"]:
        lines.append(
            f"Historique tronqué aux {reader['kept_earliest']} premières notes, "
            f"sur {reader['ratings_available']} disponibles. Ce n'est pas un nouveau lecteur."
        )
    if result["mode"] == "neighbors":
        lines.append("Mode : lecteurs qui ont aimé les mêmes livres, en privilégiant les moins populaires.")
    elif result.get("fallback") == "no_neighbor":
        lines.append(
            "Mode : tags de contenu. Aucun livre n'était soutenu par assez de lecteurs proches."
        )
    else:
        lines.append(
            "Mode : tags de contenu. Moins de 5 notes, donc les lecteurs proches ne sont pas utilisés."
        )

    stats = result["popularity"]
    if result["status"] == "no_recommendation":
        lines.append("Aucune proposition.")
        return "\n".join(lines)
    if stats["in_top_100"] == 0:
        top_sentence = f"Aucune des {stats['recommendations']} propositions ne fait partie des 100 livres les plus notés."
    elif stats["in_top_100"] == 1:
        top_sentence = (
            f"1 des {stats['recommendations']} propositions "
            f"fait partie des 100 livres les plus notés (part {stats['share_in_top_100']})."
        )
    else:
        top_sentence = (
            f"{stats['in_top_100']} des {stats['recommendations']} propositions "
            f"font partie des 100 livres les plus notés (part {stats['share_in_top_100']})."
        )
    lines.append(
        f"{top_sentence} "
        f"Popularité médiane des propositions : {stats['median_popularity']} notes. "
        f"Popularité médiane de ces 100 livres : {stats['median_popularity_of_top_100']} notes."
    )
    lines.append("")
    for rank, item in enumerate(result["recommendations"], start=1):
        lines.append(f"{rank}. {item['book_id']} — {item['title']} — {item['authors']}")
        place = "dans le top 100" if item["in_top_100"] else "hors du top 100"
        lines.append(f"   {item['ratings_in_selection']} notes dans la sélection, {place}.")
        if item["on_reading_list"]:
            lines.append("   Ce lecteur l'a déjà marqué à lire.")
        if item["supporters"]:
            lines.append(f"   {item['supporters_total']} lecteurs proches ont mis au moins 4/5.")
            for supporter in item["supporters"]:
                shared = "; ".join(
                    f"« {book['title']} » noté {book['rating']}/5 (position {book['source_row']})"
                    for book in supporter["shared_books"]
                )
                lines.append(
                    f"   lecteur {supporter['user_id']} : {supporter['rating']}/5 "
                    f"(position {supporter['source_row']}), en commun sur {shared}"
                )
        if item["shared_tags"]:
            rendered = ", ".join(
                f"{tag['name']} (depuis « {tag['from_title']} », {tag['rating']}/5)"
                for tag in item["shared_tags"][:4]
            )
            lines.append(f"   Tags : {rendered}")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
