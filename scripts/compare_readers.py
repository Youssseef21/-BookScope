"""Run F2 from the command line.

Examples
    python scripts/compare_readers.py 1 2
    python scripts/compare_readers.py 1 2 --first 1 5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bookscope.db import connect
from bookscope.f2 import compare


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="F2 — comparer deux lecteurs")
    parser.add_argument("user_a", type=int)
    parser.add_argument("user_b", type=int)
    parser.add_argument(
        "--first",
        nargs=2,
        action="append",
        metavar=("USER", "K"),
        help="ne garder que les K premières notes de USER",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    limits = {}
    for user, count in args.first or []:
        limits[int(user)] = int(count)
    result = compare(connect(), args.user_a, args.user_b, limits)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_result(result))


def format_result(result: dict) -> str:
    status = result["status"]
    if status == "same_reader":
        return "Il faut deux lecteurs différents."
    if status == "not_found":
        missing = ", ".join(str(user_id) for user_id in result["user_ids"])
        return f"Lecteur absent de la sélection : {missing}"

    lines = [_reader_line("Lecteur A", result["reader_a"]), _reader_line("Lecteur B", result["reader_b"]), ""]
    shared = result["shared_books"]
    if shared == 0:
        shared_text = "aucun livre en commun"
    elif shared == 1:
        shared_text = "seulement 1 livre en commun"
    else:
        shared_text = f"seulement {shared} livres en commun"
    if status == "too_few_shared_books":
        lines.append(
            f"Comparaison non significative : {shared_text}. "
            f"Il en faut au moins {result['parameters']['min_shared_books']}."
        )
    else:
        lines.append(
            f"Livres en commun : {shared}. "
            f"Recouvrement avec l'historique le plus court : {result['overlap_with_shorter_history']}. "
            f"Accord pondéré (les livres très notés pèsent moins) : {result['popularity_weighted_agreement']}. "
            f"Force du lien : {result['strength']}."
        )

    lines.extend(["", f"Accords ({result['similarities_total']})"])
    lines.extend(_books(result["similarities"], result["reader_a"]["user_id"], result["reader_b"]["user_id"]))
    lines.extend(["", f"Désaccords ({result['disagreements_total']})"])
    lines.extend(_books(result["disagreements"], result["reader_a"]["user_id"], result["reader_b"]["user_id"]))
    return "\n".join(lines)


def _reader_line(label: str, profile: dict) -> str:
    text = f"{label} {profile['user_id']} : {profile['ratings_used']} notes utilisées"
    if profile["truncated"]:
        text += (
            f" (historique tronqué aux {profile['kept_earliest']} premières, "
            f"sur {profile['ratings_available']} disponibles)"
        )
    return text


def _books(rows: list[dict], user_a: int, user_b: int) -> list[str]:
    if not rows:
        return ["Aucun."]
    lines = []
    for row in rows:
        lines.append(f"- {row['book_id']} — {row['title']} — {row['authors']}")
        lines.append(
            f"  lecteur {user_a} : {row['rating_a']}/5 (position {row['source_row_a']}), "
            f"lecteur {user_b} : {row['rating_b']}/5 (position {row['source_row_b']}), "
            f"écart {row['difference']}, {row['ratings_in_selection']} notes dans la sélection"
        )
    return lines


if __name__ == "__main__":
    main()
