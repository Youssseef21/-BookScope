"""Local web interface for F1, F2 and F3."""

from __future__ import annotations

import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from bookscope.db import connect
from bookscope.f1 import explore
from bookscope.f2 import compare
from bookscope.f3 import recommend
from bookscope.f4 import trace

HOST = "127.0.0.1"
PORT = 8000


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"BookScope : http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        try:
            if parsed.path == "/":
                body = ""
            elif parsed.path == "/explore":
                body = render_explore(params)
            elif parsed.path == "/compare":
                body = render_compare(params)
            elif parsed.path == "/recommend":
                body = render_recommend(params)
            elif parsed.path == "/trace":
                body = render_trace(params)
            else:
                self._send(404, page("Introuvable", "<p>Cette page n'existe pas.</p>"))
                return
        except Exception as error:
            body = f"<p class='warn'>La base n'a pas répondu. {esc(error)}</p>"
        self._send(200, page("BookScope", body))

    def _send(self, status: int, content: str) -> None:
        payload = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args) -> None:
        print(f"{self.address_string()} {format % args}", flush=True)


def database():
    return connect()


def one(params: dict[str, list[str]], name: str) -> str:
    values = params.get(name) or [""]
    return values[0].strip()


def render_explore(params: dict[str, list[str]]) -> str:
    query = one(params, "q")
    if not query:
        return "<p class='warn'>Indiquez un titre ou un numéro de livre.</p>"
    result = explore(database(), int(query) if query.isdigit() else query)
    status = result["status"]
    if status == "not_found":
        return f"<p class='warn'>Aucun livre pour « {esc(query)} ».</p>"
    if status == "ambiguous":
        items = []
        for book in result["matches"]:
            href = f"/explore?q={book['book_id']}"
            items.append(
                f"<li><a href='{href}'>{book['book_id']}</a> — {esc(book['title'])} — {esc(book['authors'])}</li>"
            )
        return (
            f"<h2>Plusieurs livres correspondent à « {esc(query)} »</h2>"
            "<p>Aucun n'est choisi. Ouvrez un numéro.</p><ul>" + "".join(items) + "</ul>"
        )
    book = result["book"]
    tags = "".join(
        f"<li>{esc(tag['name'])} — {tag['count']} fois sur ce livre</li>" for tag in book["content_tags"]
    )
    ignored = "".join(
        f"<li>{esc(tag['name'])} — {esc(tag['reason'])}</li>" for tag in book["ignored_tags"]
    )
    blocks = [
        f"<h2>{book['book_id']} — {esc(book['title'])}</h2>",
        f"<p>{esc(book['authors'])} · {esc(book['original_publication_year'])}</p>",
        f"<p>{book['ratings_in_selection']} notes dans la sélection, dont {book['positive_ratings']} à 4 ou 5.</p>",
        "<h3>Tags de contenu</h3><ul>" + (tags or "<li>Aucun.</li>") + "</ul>",
        "<h3>Tags ignorés</h3><ul>" + (ignored or "<li>Aucun.</li>") + "</ul>",
    ]
    for kind, title in (
        ("shared_readership", "Liens par les lecteurs"),
        ("thematic", "Liens par le thème"),
        ("common_tag_only", "Lien qui ne compte pas"),
    ):
        links = [link for link in result["connections"] if link["kind"] == kind]
        blocks.append(f"<h3>{title}</h3>")
        blocks.append(render_book_links(links) if links else "<p>Aucun.</p>")
    return "".join(blocks)


def render_book_links(links: list[dict]) -> str:
    chunks = []
    for link in links:
        other = link["book"]
        lines = [f"<h4>{other['book_id']} — {esc(other['title'])}</h4>", f"<p>{esc(other['authors'])}</p>"]
        if link["kind"] == "shared_readership":
            lines.append(
                f"<p>{link['shared_readers']} lecteurs ont mis au moins 4/5 aux deux livres. Force {link['strength']}.</p>"
            )
            evidence = []
            for row in link["ratings_evidence"]:
                evidence.append(
                    "<li>"
                    f"lecteur {row['user_id']} : {row['rating_on_book']}/5 "
                    f"(position {row['source_row_on_book']}), "
                    f"{row['rating_on_related']}/5 (position {row['source_row_on_related']})"
                    "</li>"
                )
            hidden = link["ratings_evidence_total"] - len(link["ratings_evidence"])
            if hidden > 0:
                evidence.append(f"<li>et {hidden} autres lecteurs</li>")
            lines.append("<ul>" + "".join(evidence) + "</ul>")
        if link["shared_tags"]:
            tags = ", ".join(esc(tag["name"]) for tag in link["shared_tags"])
            lines.append(f"<p>Tags : {tags}</p>")
        if link["kind"] == "common_tag_only" and link["shared_tags"]:
            tag = link["shared_tags"][0]
            lines.append(
                f"<p>Seul point commun retenu : {esc(tag['name'])}, "
                f"présent sur {tag['books_with_tag']} livres. "
                f"Lecteurs en commun avec une note d'au moins 4 : {link['shared_readers']}.</p>"
            )
        chunks.append("<article>" + "".join(lines) + "</article>")
    return "".join(chunks)


def render_compare(params: dict[str, list[str]]) -> str:
    try:
        user_a = int(one(params, "a"))
        user_b = int(one(params, "b"))
    except ValueError:
        return "<p class='warn'>Indiquez deux numéros de lecteur.</p>"
    limits: dict[int, int] = {}
    raw_user = one(params, "first_user")
    raw_count = one(params, "first_k")
    if raw_user or raw_count:
        try:
            limits[int(raw_user)] = int(raw_count)
            if limits[int(raw_user)] < 1:
                raise ValueError
        except ValueError:
            return "<p class='warn'>La troncature demande un lecteur et un nombre de notes supérieur à 0.</p>"
    result = compare(database(), user_a, user_b, limits)
    status = result["status"]
    if status == "same_reader":
        return "<p class='warn'>Il faut deux lecteurs différents.</p>"
    if status == "not_found":
        missing = ", ".join(str(user_id) for user_id in result["user_ids"])
        return f"<p class='warn'>Lecteur absent de la sélection : {esc(missing)}</p>"
    blocks = [
        f"<h2>Lecteurs {user_a} et {user_b}</h2>",
        reader_line(result["reader_a"]),
        reader_line(result["reader_b"]),
    ]
    if status == "too_few_shared_books":
        blocks.append(
            f"<p class='warn'>Comparaison non significative : {result['shared_books']} livre(s) en commun. "
            f"Il en faut au moins {result['parameters']['min_shared_books']}.</p>"
        )
    else:
        blocks.append(
            "<p>"
            f"Livres en commun : {result['shared_books']}. "
            f"Recouvrement avec l'historique le plus court : {result['overlap_with_shorter_history']}. "
            f"Force du lien : {result['strength']}."
            "</p>"
        )
    blocks.append(f"<h3>Accords ({result['similarities_total']})</h3>")
    blocks.append(render_rated_books(result["similarities"], user_a, user_b))
    blocks.append(f"<h3>Désaccords ({result['disagreements_total']})</h3>")
    blocks.append(render_rated_books(result["disagreements"], user_a, user_b))
    return "".join(blocks)


def reader_line(profile: dict) -> str:
    text = f"<p>Lecteur {profile['user_id']} : {profile['ratings_used']} notes utilisées"
    if profile["truncated"]:
        text += (
            f" (historique tronqué aux {profile['kept_earliest']} premières, "
            f"sur {profile['ratings_available']} disponibles)"
        )
    return text + "</p>"


def render_rated_books(rows: list[dict], user_a: int, user_b: int) -> str:
    if not rows:
        return "<p>Aucun.</p>"
    items = []
    for row in rows:
        items.append(
            "<li>"
            f"<strong>{row['book_id']} — {esc(row['title'])}</strong> — {esc(row['authors'])}<br>"
            f"lecteur {user_a} : {row['rating_a']}/5 (position {row['source_row_a']}), "
            f"lecteur {user_b} : {row['rating_b']}/5 (position {row['source_row_b']}), "
            f"écart {row['difference']}, {row['ratings_in_selection']} notes dans la sélection"
            "</li>"
        )
    return "<ul>" + "".join(items) + "</ul>"


def render_recommend(params: dict[str, list[str]]) -> str:
    try:
        user_id = int(one(params, "user"))
    except ValueError:
        return "<p class='warn'>Indiquez un numéro de lecteur.</p>"
    first = None
    raw_first = one(params, "first")
    if raw_first:
        try:
            first = int(raw_first)
            if first < 1:
                raise ValueError
        except ValueError:
            return "<p class='warn'>Le nombre de premières notes doit être supérieur à 0.</p>"
    result = recommend(database(), user_id, first)
    if result["status"] == "not_found":
        return f"<p class='warn'>Lecteur absent de la sélection : {user_id}</p>"
    reader = result["reader"]
    blocks = [
        f"<h2>Propositions pour le lecteur {user_id}</h2>",
        f"<p>{reader['ratings_used']} notes utilisées, dont {reader['positive_ratings']} à 4 ou 5.</p>",
    ]
    if reader["truncated"]:
        blocks.append(
            f"<p>Historique tronqué aux {reader['kept_earliest']} premières notes, "
            f"sur {reader['ratings_available']} disponibles. Ce n'est pas un nouveau lecteur.</p>"
        )
    if result["mode"] == "neighbors":
        blocks.append("<p>Mode : lecteurs qui ont aimé les mêmes livres peu populaires.</p>")
    elif result.get("fallback") == "no_neighbor":
        blocks.append("<p>Mode : tags de contenu. Pas assez de lecteurs proches.</p>")
    else:
        blocks.append("<p>Mode : tags de contenu, parce que l'historique a moins de 5 notes.</p>")
    if result["status"] == "no_recommendation":
        blocks.append("<p>Aucune proposition.</p>")
        return "".join(blocks)
    stats = result["popularity"]
    count = stats["in_top_100"]
    total = stats["recommendations"]
    if count == 0:
        rank_sentence = f"Aucune des {total} propositions ne fait partie des 100 livres les plus notés."
    elif count == 1:
        rank_sentence = f"1 des {total} propositions fait partie des 100 livres les plus notés."
    else:
        rank_sentence = f"{count} des {total} propositions font partie des 100 livres les plus notés."
    blocks.append(
        "<p>"
        f"{rank_sentence} "
        f"Popularité médiane des propositions : {stats['median_popularity']}. "
        f"Popularité médiane de ce top 100 : {stats['median_popularity_of_top_100']}."
        "</p>"
    )
    for rank, item in enumerate(result["recommendations"], start=1):
        place = "dans le top 100" if item["in_top_100"] else "hors du top 100"
        bits = [
            f"<h3>{rank}. {item['book_id']} — {esc(item['title'])}</h3>",
            f"<p>{esc(item['authors'])}</p>",
            f"<p>{item['ratings_in_selection']} notes dans la sélection, {place}.</p>",
        ]
        if item["on_reading_list"]:
            bits.append("<p>Ce lecteur l'a déjà marqué à lire.</p>")
        if item["supporters"]:
            bits.append(f"<p>{item['supporters_total']} lecteurs proches ont mis au moins 4/5.</p><ul>")
            for supporter in item["supporters"]:
                shared = "; ".join(
                    f"« {esc(book['title'])} » noté {book['rating']}/5 (position {book['source_row']})"
                    for book in supporter["shared_books"]
                )
                bits.append(
                    "<li>"
                    f"lecteur {supporter['user_id']} : {supporter['rating']}/5 "
                    f"(position {supporter['source_row']}), en commun sur {shared}"
                    "</li>"
                )
            bits.append("</ul>")
        if item["shared_tags"]:
            tags = ", ".join(
                f"{esc(tag['name'])} (depuis « {esc(tag['from_title'])} », {tag['rating']}/5)"
                for tag in item["shared_tags"][:4]
            )
            bits.append(f"<p>Tags : {tags}</p>")
        blocks.append("<article>" + "".join(bits) + "</article>")
    return "".join(blocks)


def render_trace(params: dict[str, list[str]]) -> str:
    left = one(params, "x")
    right = one(params, "y")
    if not left or not right:
        return "<p class='warn'>Indiquez deux livres.</p>"
    result = trace(
        database(),
        int(left) if left.isdigit() else left,
        int(right) if right.isdigit() else right,
    )
    status = result["status"]
    if status == "not_found":
        return f"<p class='warn'>Aucun livre pour « {esc(result['query'])} ».</p>"
    if status == "ambiguous":
        items = "".join(
            f"<li><a href='/explore?q={book['book_id']}'>{book['book_id']}</a> — {esc(book['title'])} — {esc(book['authors'])}</li>"
            for book in result["matches"]
        )
        return f"<h2>Plusieurs livres correspondent à « {esc(result['query'])} »</h2><ul>{items}</ul>"
    if status == "same_book":
        return "<p class='warn'>Il faut deux livres différents.</p>"

    book_a = result["book_a"]
    book_b = result["book_b"]
    headline = "Connexion convaincante." if status == "ok" else "Aucune connexion convaincante."
    blocks = [
        f"<h2>{headline}</h2>",
        f"<p>De {book_a['book_id']} — {esc(book_a['title'])}</p>",
        f"<p>À {book_b['book_id']} — {esc(book_b['title'])}</p>",
    ]
    direct = result["direct"]
    blocks.append("<h3>Lien direct</h3>")
    blocks.append(
        "<p>"
        f"{direct['ordinary_readers']} lecteurs ordinaires ont mis au moins 4/5 aux deux livres. "
        f"{direct['highly_active_readers']} lecteurs très actifs ont été écartés."
        "</p>"
    )
    if direct["evidence"]:
        items = []
        for row in direct["evidence"]:
            items.append(
                "<li>"
                f"lecteur {row['user_id']} : {row['rating_a']}/5 (position {row['source_row_a']}), "
                f"{row['rating_b']}/5 (position {row['source_row_b']})"
                "</li>"
            )
        blocks.append("<ul>" + "".join(items) + "</ul>")
    blocks.append("<h3>Chemins indirects</h3>")
    if not result["indirect"]:
        blocks.append("<p>Aucun chemin qui évite les livres très notés et les lecteurs très actifs.</p>")
    for path in result["indirect"]:
        blocks.append(
            "<article>"
            f"<h4>via {path['book_id']} — {esc(path['title'])}</h4>"
            f"<p>{path['readers_from_a']} lecteurs depuis le premier livre, "
            f"{path['readers_to_b']} vers le second. "
            f"{path['ratings_in_selection']} notes dans la sélection.</p>"
            "</article>"
        )
    catalogue = result["catalogue"]
    blocks.append("<h3>Catalogue</h3>")
    if catalogue["authors"]:
        blocks.append("<p>Auteur commun : " + esc(", ".join(catalogue["authors"])) + "</p>")
    if catalogue["tags"]:
        blocks.append("<p>Tags précis : " + esc(", ".join(tag["name"] for tag in catalogue["tags"])) + "</p>")
    if not catalogue["authors"] and not catalogue["tags"]:
        blocks.append("<p>Pas d'auteur commun, ni de tag de contenu précis.</p>")
    return "".join(blocks)


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def page(title: str, result: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<style>
body {{ margin: 0; background: #f4efe6; color: #1d1a16; font: 17px/1.45 Georgia, serif; }}
main {{ max-width: 920px; margin: 0 auto; padding: 32px 20px 64px; }}
h1 {{ font-size: 2.2rem; margin-bottom: 0.2rem; }}
h2 {{ margin-top: 2rem; }}
.lead {{ max-width: 40rem; }}
.forms {{ display: grid; gap: 16px; margin-top: 28px; }}
form {{ background: #fffdf8; border: 1px solid #d9d0c3; padding: 16px; }}
label {{ display: block; margin: 8px 0 4px; font-size: 0.95rem; }}
input {{ width: 100%; box-sizing: border-box; padding: 8px; font: 16px Georgia, serif; border: 1px solid #b9ae9f; background: white; }}
button {{ margin-top: 12px; background: #6e2b23; color: white; border: 0; padding: 8px 14px; font: 16px Georgia, serif; cursor: pointer; }}
article {{ border-top: 1px solid #d9d0c3; padding-top: 8px; margin-top: 12px; }}
.warn {{ background: #fff1d6; border: 1px solid #e0c48a; padding: 10px 12px; }}
a {{ color: #6e2b23; }}
</style>
</head>
<body>
<main>
<h1>BookScope</h1>
<p class="lead">Exploration du catalogue à partir des notes des lecteurs retenus. Les notes cachées pour l'évaluation et pour l'import ne sont pas utilisées.</p>
<section class="forms">
<form action="/explore">
<h2>Explorer un livre</h2>
<label for="q">Titre ou numéro</label>
<input id="q" name="q" placeholder="258 ou Selected Poems">
<button>Explorer</button>
</form>
<form action="/compare">
<h2>Comparer deux lecteurs</h2>
<label for="a">Lecteur A</label>
<input id="a" name="a" placeholder="1">
<label for="b">Lecteur B</label>
<input id="b" name="b" placeholder="2">
<label for="first_user">Tronquer ce lecteur (facultatif)</label>
<input id="first_user" name="first_user" placeholder="1">
<label for="first_k">Nombre de premières notes</label>
<input id="first_k" name="first_k" placeholder="5">
<button>Comparer</button>
</form>
<form action="/recommend">
<h2>Proposer des livres</h2>
<label for="user">Lecteur</label>
<input id="user" name="user" placeholder="1">
<label for="first">Premières notes seulement (facultatif)</label>
<input id="first" name="first" placeholder="4">
<button>Proposer</button>
</form>
<form action="/trace">
<h2>Relier deux livres</h2>
<label for="x">Premier livre</label>
<input id="x" name="x" placeholder="258">
<label for="y">Second livre</label>
<input id="y" name="y" placeholder="1467">
<button>Relier</button>
</form>
</section>
<section>{result}</section>
</main>
</body>
</html>"""


if __name__ == "__main__":
    main()
