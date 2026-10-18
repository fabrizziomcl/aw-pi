"""Descubrimiento y extracción HTML, independientes de la interfaz de comandos."""

import hashlib
import json
import logging

from .config import search_url
from .http import Blocked, FetchError
from .parsers import ParseError, parse_article, parse_search
from .storage import now

LOG = logging.getLogger("scielo")


def discover(cfg, store, client):
    if cfg.discovery == "catalog":
        from .catalog import discover_catalog

        return discover_catalog(cfg, store, client)
    state = store.search_state(cfg, search_url(cfg))
    if state["done"]:
        LOG.info("Esta búsqueda ya terminó. Cambie los filtros o use otro data_dir para un rastreo nuevo.")
        return
    page_number = state["next_page"]
    processed = 0
    while not cfg.max_pages or processed < cfg.max_pages:
        url = search_url(cfg, page_number)
        LOG.info("Buscador, página %s: %s", page_number, url)
        page = client.get(url)
        raw = store.raw("search", f"{state['id']}-{page_number:07}", page.content)
        parsed = parse_search(page.content, page.url)
        prior = store.db.execute(
            "SELECT page FROM search_pages WHERE search_id=? AND fingerprint=?",
            (state["id"], parsed["fingerprint"]),
        ).fetchone()
        if prior:
            raise ParseError(f"Página repetida (ya apareció en {prior['page']}); no avanzo el checkpoint")
        with store.db:
            new = sum(store.enqueue(seed) for seed in parsed["records"])
            store.db.execute(
                "INSERT INTO search_pages VALUES(?,?,?,?)",
                (state["id"], page_number, parsed["fingerprint"], raw),
            )
            store.db.execute(
                "UPDATE searches SET next_page=?,done=? WHERE id=?",
                (page_number + 1, int(not parsed["has_next"]), state["id"]),
            )
        LOG.info("%s artículos encontrados; %s nuevos. HTML: %s", len(parsed["records"]), new, raw)
        processed += 1
        page_number += 1
        if not parsed["has_next"]:
            return
    LOG.info("Límite de páginas de esta ejecución alcanzado; próxima página: %s", page_number)


def ingest_article(cfg, store, content, url, requested_id, transport):
    raw = store.raw("articles", requested_id, content)
    record = parse_article(content, url, cfg.languages[0] if cfg.languages else None)
    record.update(
        {
            "raw_html_path": raw,
            "html_sha256": hashlib.sha256(content).hexdigest(),
            "fetched_at": now(),
            "transport": transport,
        }
    )
    seed_row = store.db.execute("SELECT seed_json FROM articles WHERE id=?", (requested_id,)).fetchone()
    if seed_row:
        record["discovery"] = json.loads(seed_row[0])
    record["filter_validation"] = {"publication_language": "unrestricted"}
    if record.get("discovery", {}).get("source") == "catalog" and cfg.languages:
        match = bool(set(record["languages"]) & set(cfg.languages))
        record["filter_validation"]["publication_language"] = "matched" if match else "mismatch_or_unknown"
        if (
            match
            and record["abstracts"]
            and not any(a["language"] in cfg.languages for a in record["abstracts"])
        ):
            record["filter_validation"]["publication_language"] = "abstract_language_conflict"
    store.save_record(record, requested_id)
    if record["filter_validation"]["publication_language"] not in ("matched", "unrestricted"):
        store.fail(
            requested_id, "Idioma fuera del filtro, ausente o incompatible con los abstracts", "filtered"
        )
    return record


def crawl(cfg, store, client):
    if cfg.discovery == "catalog":
        from .catalog import check_settings

        check_settings(cfg, store)
    count, errors = 0, 0
    # Una sola ejecución por base de datos. El estado pending no cambia hasta persistir.
    cursor = store.db.execute(
        "SELECT id,url FROM articles WHERE status='pending' ORDER BY rowid LIMIT ?", (cfg.max_articles or -1,)
    )
    for row in cursor:
        LOG.info("Artículo %s: %s", row["id"], row["url"])
        try:
            page = client.get(row["url"])
            record = ingest_article(
                cfg,
                store,
                page.content,
                page.url,
                row["id"],
                {
                    "mode": "http",
                    "requested_url": row["url"],
                    "final_url": page.url,
                    "http_status": page.status,
                    "response_headers": page.headers,
                },
            )
            if record["filter_validation"]["publication_language"] not in ("matched", "unrestricted"):
                LOG.info("Excluido del corpus por idioma no coincidente o no verificado: %s", record["title"])
            else:
                LOG.info("Guardado: %s; avisos: %s", record["title"], record["warnings"])
                count += 1
        except Blocked as exc:
            store.fail(row["id"], exc, "blocked")
            raise
        except (FetchError, ParseError) as exc:
            store.fail(row["id"], exc)
            LOG.error("%s", exc)
            errors += 1
    LOG.info("Artículos guardados: %s; errores: %s", count, errors)
    return errors
