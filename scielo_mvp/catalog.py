"""Descubrimiento clásico revista -> números -> artículos, sin buscador ni API."""

import hashlib
import json
import logging
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from .parsers import ParseError, identity, soup_of

LOG = logging.getLogger("scielo")
SCRIPTS = {"sci_alphabetic", "sci_serial", "sci_issues", "sci_issuetoc", "sci_arttext", "sci_abstract"}


def normalize(url, base):
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


def links(html, base):
    result = {}
    for anchor in soup_of(html).select("a[href]"):
        target = normalize(anchor["href"], base)
        if target:
            result.setdefault(target, anchor.get_text(" ", strip=True))
    if not result:
        raise ParseError("No se reconocieron enlaces de navegación SciELO en el catálogo")
    return result


def settings_for(cfg):
    return {
        "url": cfg.catalog_url,
        "journals": sorted(cfg.catalog_journals),
        "languages": sorted(cfg.languages),
        "years": sorted(str(y) for y in cfg.years),
    }


def check_settings(cfg, store):
    """Comprueba la selección sin modificar el checkpoint (también en auditorías)."""
    exists = store.db.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_settings'").fetchone()
    if not exists:
        return
    saved = store.db.execute("SELECT settings FROM catalog_settings WHERE id=1").fetchone()
    if not saved:
        return
    previous, current = json.loads(saved[0]), settings_for(cfg)
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


def initialize(cfg, store):
    check_settings(cfg, store)
    store.db.executescript("""
        CREATE TABLE IF NOT EXISTS catalog_pages (
          url TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending',
          priority INTEGER NOT NULL, raw_path TEXT, error TEXT
        );
        CREATE TABLE IF NOT EXISTS catalog_settings (id INTEGER PRIMARY KEY, settings TEXT NOT NULL);
    """)
    settings = json.dumps(settings_for(cfg), sort_keys=True)
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
        # Una ampliación preserva todos los registros previos como subconjunto válido.
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
    initialize(cfg, store)
    return not store.db.execute("SELECT 1 FROM catalog_pages WHERE status='pending' LIMIT 1").fetchone()


def discover_catalog(cfg, store, client):
    initialize(cfg, store)
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
        found = links(page.content, page.url)
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
                    # Preferir la versión publicada en español, comprobada luego con meta.
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
                    # Priorizar el archivo de todas las revistas antes de consumir sus números.
                    priority = {"sci_serial": 1, "sci_issues": 2, "sci_issuetoc": 3}[script]
                    store.db.execute(
                        "INSERT OR IGNORE INTO catalog_pages(url,priority) VALUES(?,?)", (target, priority)
                    )
            store.db.execute("UPDATE catalog_pages SET status='done',raw_path=? WHERE url=?", (raw_path, url))
        LOG.info("Catálogo procesado; artículos nuevos: %s", new)
        processed += 1
