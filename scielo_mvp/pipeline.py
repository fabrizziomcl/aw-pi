"""Descubrimiento y extracción HTML, independientes de la interfaz de comandos."""

import hashlib
import json
import logging
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from .config import search_url
from .http import Blocked, FetchError
from .parsers import ParseError, identity, parse_article, parse_search, soup_of
from .storage import now

LOG = logging.getLogger("scielo")
SCRIPTS = {"sci_alphabetic", "sci_serial", "sci_issues", "sci_issuetoc", "sci_arttext", "sci_abstract"}


def discover(cfg, store, client):
    if cfg.discovery == "catalog":
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


def _catalog_url(url, base):
    parts, origin = urlsplit(urljoin(base, url)), urlsplit(base)
    if parts.hostname != origin.hostname or parts.path != "/scielo.php":
        return None
    query = parse_qs(parts.query)
    script = query.get("script", [""])[0]
    pid = query.get("pid", [""])[0]
    if script not in SCRIPTS or (script != "sci_alphabetic" and not pid):
        return None
    if script == "sci_abstract":
        script = "sci_arttext"
    params = {"script": script}
    if pid:
        params["pid"] = pid
    params.update({"lng": "es", "nrm": "iso"})
    if script == "sci_arttext" and query.get("tlng"):
        params["tlng"] = query["tlng"][0]
    return urlunsplit((origin.scheme, origin.netloc, parts.path, urlencode(params), ""))


def _catalog_links(content, base):
    result = {}
    for anchor in soup_of(content).select("a[href]"):
        target = _catalog_url(anchor["href"], base)
        if target:
            result.setdefault(target, anchor.get_text(" ", strip=True))
    if not result:
        raise ParseError("No se reconocieron enlaces de navegación SciELO en el catálogo")
    return result


def _catalog_settings(cfg):
    return {
        "url": cfg.catalog_url,
        "journals": sorted(cfg.catalog_journals),
        "languages": sorted(cfg.languages),
        "years": sorted(str(y) for y in cfg.years),
    }


def check_settings(cfg, store):
    exists = store.db.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_settings'").fetchone()
    if not exists:
        return
    saved = store.db.execute("SELECT settings FROM catalog_settings WHERE id=1").fetchone()
    if not saved:
        return
    previous, current = json.loads(saved[0]), _catalog_settings(cfg)
    for key in ("journals", "languages", "years"):
        previous[key] = sorted(str(v) for v in previous[key])
    same_filters = all(previous[key] == current[key] for key in ("url", "languages", "years"))
    expands = (
        previous["journals"]
        and current["journals"]
        and set(previous["journals"]).issubset(current["journals"])
    )
    if not same_filters or (previous["journals"] != current["journals"] and not expands):
        raise ValueError("Los filtros del catálogo cambiaron: use otro data_dir para no mezclar muestras")


def _initialize_catalog(cfg, store):
    check_settings(cfg, store)
    store.db.executescript("""
        CREATE TABLE IF NOT EXISTS catalog_pages (
          url TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending',
          priority INTEGER NOT NULL, raw_path TEXT, error TEXT
        );
        CREATE TABLE IF NOT EXISTS catalog_settings (id INTEGER PRIMARY KEY, settings TEXT NOT NULL);
    """)
    settings = json.dumps(_catalog_settings(cfg), sort_keys=True)
    saved = store.db.execute("SELECT settings FROM catalog_settings WHERE id=1").fetchone()
    if saved and saved[0] != settings:
        previous = json.loads(saved[0])
        current = json.loads(settings)
        for key in ("journals", "languages", "years"):
            previous[key] = sorted(str(v) for v in previous[key])
        added = set(current["journals"]) - set(previous["journals"])
        expands_only = (
            bool(previous["journals"])
            and bool(current["journals"])
            and bool(added)
            and set(previous["journals"]).issubset(current["journals"])
            and all(previous[key] == current[key] for key in ("url", "languages", "years"))
        )
        if previous != current and not expands_only:
            raise ValueError("Los filtros del catálogo cambiaron: use otro data_dir para no mezclar muestras")
        with store.db:
            store.db.execute("UPDATE catalog_settings SET settings=? WHERE id=1", (settings,))
            if added:
                store.db.execute("UPDATE catalog_pages SET status='pending' WHERE url=?", (cfg.catalog_url,))
        if added:
            LOG.info("Ampliación explícita de revistas del catálogo: %s", sorted(added))
    with store.db:
        store.db.execute("INSERT OR IGNORE INTO catalog_settings VALUES(1,?)", (settings,))
        store.db.execute("INSERT OR IGNORE INTO catalog_pages(url,priority) VALUES(?,0)", (cfg.catalog_url,))


def exhausted(cfg, store):
    _initialize_catalog(cfg, store)
    return not store.db.execute("SELECT 1 FROM catalog_pages WHERE status='pending' LIMIT 1").fetchone()


def discover_catalog(cfg, store, client):
    _initialize_catalog(cfg, store)
    processed = 0
    while not cfg.max_pages or processed < cfg.max_pages:
        row = store.db.execute(
            "SELECT url FROM catalog_pages WHERE status='pending' ORDER BY priority,rowid LIMIT 1"
        ).fetchone()
        if not row:
            LOG.info("Recorrido del catálogo agotado")
            return
        url = row["url"]
        LOG.info("Catálogo: %s", url)
        page = client.get(url)
        raw_path = store.raw("catalog", hashlib.sha256(url.encode()).hexdigest()[:24], page.content)
        found = _catalog_links(page.content, page.url)
        new = 0
        with store.db:
            for target, text in found.items():
                query = parse_qs(urlsplit(target).query)
                script, pid = query["script"][0], query.get("pid", [""])[0]
                journal = pid[1:10] if script == "sci_arttext" else pid[:9]
                if cfg.catalog_journals and journal not in cfg.catalog_journals:
                    continue
                if script == "sci_arttext":
                    year = pid[10:14]
                    if cfg.years and year not in [str(y) for y in cfg.years]:
                        continue
                    record_id, article_pid = identity(target)
                    translation = query.get("tlng", [None])[0]
                    if cfg.languages and translation and translation not in cfg.languages:
                        continue
                    seed = {
                        "id": record_id,
                        "pid": article_pid,
                        "url": target,
                        "source_url": page.url,
                        "source": "catalog",
                        "journal_issn": journal,
                        "link_text": text,
                        "collection": "per" if urlsplit(target).hostname == "www.scielo.org.pe" else None,
                    }
                    new += store.enqueue(seed)
                elif script in ("sci_serial", "sci_issues", "sci_issuetoc"):
                    priority = {"sci_serial": 1, "sci_issues": 2, "sci_issuetoc": 3}[script]
                    store.db.execute(
                        "INSERT OR IGNORE INTO catalog_pages(url,priority) VALUES(?,?)", (target, priority)
                    )
            store.db.execute("UPDATE catalog_pages SET status='done',raw_path=? WHERE url=?", (raw_path, url))
        LOG.info("Catálogo procesado; artículos nuevos: %s", new)
        processed += 1
