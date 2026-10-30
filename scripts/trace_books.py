"""Run F4 from the command line.

Examples
    python scripts/trace_books.py 258 1467
    python scripts/trace_books.py "Selected Poems" "Rebecca"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bookscope.db import connect
from bookscope.f4 import trace


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="F4 — relier deux livres")
    parser.add_argument("left", help="book_id ou titre")
    parser.add_argument("right", help="book_id ou titre")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    left = int(args.left) if args.left.isdigit() else args.left
    right = int(args.right) if args.right.isdigit() else args.right
    result = trace(connect(), left, right)
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
        return "\n".join(lines)
    if status == "same_book":
        return "Il faut deux livres différents."

    book_a = result["book_a"]
    book_b = result["book_b"]
    lines = [
        f"De {book_a['book_id']} — {book_a['title']}",
        f"À {book_b['book_id']} — {book_b['title']}",
        "",
    ]
    if status == "not_convincing":
        lines.append("Aucune connexion convaincante.")
        lines.append(
            "Un lien direct demanderait au moins 5 lecteurs ordinaires. "
            "Un chemin indirect ne passe ni par les 100 livres les plus notés, "
            "ni par un lecteur qui a au moins 150 notes."
        )
    else:
        lines.append("Connexion convaincante.")

    direct = result["direct"]
    lines.extend(["", "Lien direct"])
    lines.append(
        f"{direct['ordinary_readers']} lecteurs ordinaires ont mis au moins 4/5 aux deux livres. "
        f"{direct['highly_active_readers']} autres lecteurs, très actifs, ont été écartés."
    )
    if not direct["evidence"]:
        lines.append("Aucune preuve directe retenue.")
    for row in direct["evidence"]:
        lines.append(
            f"- lecteur {row['user_id']} : {row['rating_a']}/5 (position {row['source_row_a']}), "
            f"{row['rating_b']}/5 (position {row['source_row_b']}), "
            f"{row['ratings_by_reader']} notes au total"
        )

    lines.extend(["", "Chemins indirects"])
    if not result["indirect"]:
        lines.append("Aucun chemin par un livre intermédiaire assez précis.")
    for path in result["indirect"]:
        lines.append(
            f"- via {path['book_id']} — {path['title']} "
            f"({path['readers_from_a']} lecteurs depuis le premier livre, "
            f"{path['readers_to_b']} vers le second, {path['ratings_in_selection']} notes)"
        )
        for row in path["evidence_a"]:
            lines.append(
                f"  vers le pont, lecteur {row['user_id']} : "
                f"{row['rating_on_endpoint']}/5 puis {row['rating_on_bridge']}/5"
            )
        for row in path["evidence_b"]:
            lines.append(
                f"  depuis le pont, lecteur {row['user_id']} : "
                f"{row['rating_on_bridge']}/5 puis {row['rating_on_endpoint']}/5"
            )

    catalogue = result["catalogue"]
    lines.extend(["", "Catalogue"])
    if catalogue["authors"]:
        lines.append("Auteur commun : " + ", ".join(catalogue["authors"]))
    if catalogue["tags"]:
        rendered = ", ".join(tag["name"] for tag in catalogue["tags"])
        lines.append("Tags précis communs : " + rendered)
    if not catalogue["authors"] and not catalogue["tags"]:
        lines.append("Pas d'auteur commun, ni de tag de contenu précis.")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
