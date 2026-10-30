"""Run F5 from the command line.

Examples
    python scripts/link_interests.py mystery historical-fiction
    python scripts/link_interests.py to-read fiction
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bookscope.db import connect
from bookscope.f5 import link_interests

PERSISTENCE = {
    "persists": "Le lien reste visible sans les 100 livres les plus notés ni les lecteurs qui ont au moins 150 notes.",
    "weakens": "Le lien faiblit quand on écarte les livres les plus notés et les lecteurs très actifs.",
    "disappears": "Le lien apparent disparaît sans les livres les plus notés et les lecteurs très actifs.",
    "none": "Pas de lien convaincant entre ces deux intérêts.",
}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="F5 — relier deux thèmes")
    parser.add_argument("left")
    parser.add_argument("right")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = link_interests(connect(), args.left, args.right)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_result(result))


def format_result(result: dict) -> str:
    status = result["status"]
    if status == "not_found":
        return f"Aucun tag pour : {result['query']}"
    if status == "ambiguous":
        lines = [f"Plusieurs tags correspondent à « {result['query']} ». Aucun n'est choisi."]
        for name, books in result["matches"]:
            lines.append(f"- {name} ({books} livres)")
        return "\n".join(lines)
    if status == "not_a_theme":
        why = "un statut de lecture" if result["reason"] == "status" else "un tag présent sur trop de livres"
        return f"« {result['query']} » n'est pas un thème : c'est {why} ({result['books']} livres)."
    if status == "same_interest":
        return "Il faut deux thèmes différents."

    lines = [
        f"{result['interest_a']['name']} et {result['interest_b']['name']}",
        PERSISTENCE[result["persistence"]],
        "",
        f"Lecteurs qui ont aimé un livre de chaque côté : {result['readers_full']}.",
        f"Après avoir écarté les livres très notés et les lecteurs très actifs : {result['readers_discounted']}.",
        "",
        "Livres qui portent les deux tags, classés par le nombre de notes d'au moins 4/5",
    ]
    lines.extend(_bridges(result["bridges"]))
    lines.extend(["", "Les mêmes livres, sans le top 100 ni les lecteurs très actifs"])
    lines.extend(_bridges(result["bridges_discounted"]))
    lines.extend(["", "Exemples de lecteurs"])
    if not result["reader_examples"]:
        lines.append("Aucun.")
    for example in result["reader_examples"]:
        left = example["book_a"]
        right = example["book_b"]
        lines.append(
            f"- lecteur {example['user_id']} : "
            f"{left['rating']}/5 à « {left['title']} » (position {left['source_row']}), "
            f"{right['rating']}/5 à « {right['title']} » (position {right['source_row']})"
        )
    return "\n".join(lines)


def _bridges(rows: list[dict]) -> list[str]:
    if not rows:
        return ["Aucun."]
    lines = []
    for row in rows:
        place = "dans le top 100" if row["in_top_100"] else "hors du top 100"
        lines.append(
            f"- {row['book_id']} — {row['title']} — {row['positive_readers']} lecteurs à 4 ou 5, "
            f"{row['ratings_in_selection']} notes, {place}"
        )
    return lines


if __name__ == "__main__":
    main()
