"""Run F6 from the command line.

Examples
    python scripts/import_batch.py
    python scripts/import_batch.py data/selection/f6_batch.csv
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bookscope.db import connect
from bookscope.f6 import import_file

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "data" / "selection" / "f6_batch.csv"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="F6 — importer un lot de notes")
    parser.add_argument("path", nargs="?", default=str(DEFAULT))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = import_file(connect(), Path(args.path))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_result(result))


def format_result(result: dict) -> str:
    status = result["status"]
    if status == "not_found":
        return f"Fichier introuvable : {result['path']}"
    if status == "invalid_file":
        return f"Fichier illisible : {result['reason']}"
    lines = [
        "Import terminé. Relancer le même fichier ne change rien.",
        "Une note déjà présente avec un autre score est conservée. Le score reçu est enregistré à part.",
        "",
        f"Lignes lues : {result['rows']}",
        f"Notes ajoutées : {result['inserted']}",
        f"Déjà présentes, même note : {result['already_present']}",
        f"Notes différentes, conservées à part : {result['conflicts']}",
        f"Notes différentes, déjà signalées : {result['conflicts_already_recorded']}",
        f"Lignes refusées : {result['rejected']}",
        f"Notes en base : {result['ratings_before']} puis {result['ratings_after']}",
    ]
    if result["inserted_examples"]:
        lines.extend(["", "Exemples ajoutés"])
        for row in result["inserted_examples"]:
            lines.append(
                f"- lecteur {row['user_id']}, livre {row['book_id']}, "
                f"{row['rating']}/5, position {row['source_row']}"
            )
    if result["conflict_examples"]:
        lines.extend(["", "Exemples de notes différentes"])
        for row in result["conflict_examples"]:
            lines.append(
                f"- lecteur {row['user_id']}, livre {row['book_id']} : "
                f"{row['stored_rating']}/5 conservée, {row['incoming_rating']}/5 enregistrée à part"
            )
    if result["rejected_examples"]:
        lines.extend(["", "Exemples refusés"])
        for row in result["rejected_examples"]:
            lines.append(f"- ligne {row['line']} : {row['reason']}")
    return "\n".join(lines)


if __name__ == "__main__":
    main()
