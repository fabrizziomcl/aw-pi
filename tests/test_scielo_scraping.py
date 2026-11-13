"""Pruebas sin red del scraping de SciELO: parser, descubrimiento por catálogo y rastreo."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from scielo_mvp.config import Config
from scielo_mvp.http import Blocked, Page
from scielo_mvp.parsers import ParseError, parse_article
from scielo_mvp.pipeline import _catalog_url, check_settings, crawl, discover
from scielo_mvp.storage import Store

BASE = "https://www.scielo.org.pe/scielo.php"
CATALOG = f"{BASE}?script=sci_alphabetic&lng=es&nrm=iso"
JOURNAL = "1810-9993"
OTHER_JOURNAL = "9999-0000"  # fuera del filtro de revistas
PID_ES = f"S{JOURNAL}2020000100001"
PID_EN = f"S{JOURNAL}2020000100002"
PID_OTHER = f"S{OTHER_JOURNAL}2020000100001"
ABSTRACT_ES = "Este estudio analiza el rendimiento de modelos de aprendizaje automático en datos peruanos."


def link(script, pid=None):
    suffix = f"&pid={pid}" if pid else ""
    return f"{BASE}?script={script}{suffix}&lng=es&nrm=iso"


def article_html(title, lang, abstract):
    return f"""<html><head><title>{title}</title>
    <meta name="citation_title" content="{title}">
    <meta name="citation_journal_title" content="Revista de prueba">
    <meta name="citation_author" content="Pérez, Ana">
    <meta name="citation_publication_date" content="2020/03/01">
    <meta name="citation_language" content="{lang}">
    </head><body>
    <p><b>RESUMEN</b></p><p>{abstract}</p>
    <p>Palabras clave: datos, modelos</p>
    </body></html>""".encode()


def page_with_links(*hrefs):
    anchors = "".join(f'<a href="{h}">enlace</a>' for h in hrefs)
    return f"<html><head><title>Índice</title></head><body>{anchors}</body></html>".encode()


SITE = {
    CATALOG: page_with_links(link("sci_serial", JOURNAL), link("sci_serial", OTHER_JOURNAL)),
    link("sci_serial", JOURNAL): page_with_links(link("sci_issuetoc", f"{JOURNAL}20200001")),
    link("sci_issuetoc", f"{JOURNAL}20200001"): page_with_links(
        link("sci_arttext", PID_ES), link("sci_abstract", PID_EN), link("sci_arttext", PID_OTHER)
    ),
    link("sci_arttext", PID_ES): article_html("Modelos en Perú", "es", ABSTRACT_ES),
    link("sci_arttext", PID_EN): article_html("Models in Peru", "en", "This study analyses machine learning models."),
}


class FakeClient:
    def __init__(self, site=None, blocked=()):
        self.site, self.blocked, self.requests = site or SITE, set(blocked), []

    def get(self, url):
        self.requests.append(url)
        if url in self.blocked:
            raise Blocked(f"Acceso restringido, HTTP 403: {url}")
        return Page(url, self.site[url], 200, {})


@pytest.fixture
def cfg(tmp_path):
    return Config(
        discovery="catalog",
        catalog_url=CATALOG,
        catalog_journals=[JOURNAL],
        languages=["es"],
        max_pages=10,
        max_articles=10,
        data_dir=str(tmp_path / "data"),
    )


@pytest.fixture
def store(cfg):
    store = Store(cfg.data_dir)
    yield store
    store.close()


def test_parse_article_extracts_metadata_and_spanish_abstract():
    record = parse_article(article_html("Modelos en Perú", "es", ABSTRACT_ES), link("sci_arttext", PID_ES))
    assert record["title"] == "Modelos en Perú"
    assert record["authors"] == ["Pérez, Ana"]
    assert record["year"] == 2020
    assert record["languages"] == ["es"]
    assert record["abstract_language"] == "es"
    assert record["abstract"] == ABSTRACT_ES
    assert record["pid"] == PID_ES


def test_parse_article_rejects_block_page():
    with pytest.raises(ParseError):
        parse_article(b"<html><head><title>Just a moment...</title></head></html>", CATALOG)


def test_parse_article_rejects_html_without_article_evidence():
    with pytest.raises(ParseError):
        parse_article(b"<html><head><title>Hola</title></head><body>nada</body></html>", CATALOG)


def test_catalog_url_normalizes_and_filters():
    assert _catalog_url(link("sci_abstract", PID_EN), BASE) == link("sci_arttext", PID_EN)
    assert _catalog_url("https://otro.org/scielo.php?script=sci_arttext&pid=X", BASE) is None
    assert _catalog_url(f"{BASE}?script=sci_arttext", BASE) is None  # sin pid
    assert _catalog_url(f"{BASE}?script=sci_pdf&pid=X", BASE) is None  # script no permitido


def test_discover_catalog_queues_only_configured_journal(cfg, store):
    client = FakeClient()
    discover(cfg, store, client)
    pending = [r["url"] for r in store.db.execute("SELECT url FROM articles WHERE status='pending'")]
    assert sorted(pending) == sorted([link("sci_arttext", PID_ES), link("sci_arttext", PID_EN)])
    assert link("sci_serial", OTHER_JOURNAL) not in client.requests
    assert all("raw_path" for _ in store.db.execute("SELECT raw_path FROM catalog_pages"))


def test_discover_is_resumable_and_idempotent(cfg, store):
    discover(cfg, store, FakeClient())
    second = FakeClient()
    discover(cfg, store, second)
    assert second.requests == []  # catálogo agotado, no repite peticiones
    assert store.db.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 2


def test_crawl_saves_spanish_and_filters_english(cfg, store):
    client = FakeClient()
    discover(cfg, store, client)
    assert crawl(cfg, store, client) == 0
    rows = {r["url"]: r for r in store.db.execute("SELECT url,status,record_json FROM articles")}
    es, en = rows[link("sci_arttext", PID_ES)], rows[link("sci_arttext", PID_EN)]
    assert es["status"] == "done"
    assert json.loads(es["record_json"])["filter_validation"]["publication_language"] == "matched"
    assert en["status"] == "filtered"
    raw = Path(cfg.data_dir) / json.loads(es["record_json"])["raw_html_path"]
    assert raw.is_file()


def test_crawl_marks_blocked_and_reraises(cfg, store):
    client = FakeClient()
    discover(cfg, store, client)
    client.blocked = {link("sci_arttext", PID_ES)}
    with pytest.raises(Blocked):
        crawl(cfg, store, client)
    statuses = [r["status"] for r in store.db.execute("SELECT status FROM articles WHERE url=?", (link("sci_arttext", PID_ES),))]
    assert statuses == ["blocked"]


def test_crawl_records_parse_error_and_continues(cfg, store):
    site = dict(SITE)
    site[link("sci_arttext", PID_ES)] = b"<html><head><title>Hola</title></head><body>vacio</body></html>"
    client = FakeClient(site)
    discover(cfg, store, client)
    assert crawl(cfg, store, client) == 1
    statuses = {r["url"]: r["status"] for r in store.db.execute("SELECT url,status FROM articles")}
    assert statuses[link("sci_arttext", PID_ES)] == "error"
    assert statuses[link("sci_arttext", PID_EN)] == "filtered"


def test_check_settings_rejects_changed_filters_but_allows_journal_expansion(cfg, store):
    discover(cfg, store, FakeClient())
    check_settings(cfg, store)  # mismos filtros: sin error
    check_settings(replace(cfg, catalog_journals=[JOURNAL, OTHER_JOURNAL]), store)  # ampliación permitida
    with pytest.raises(ValueError):
        check_settings(replace(cfg, languages=["en"]), store)
    with pytest.raises(ValueError):
        check_settings(replace(cfg, catalog_journals=[OTHER_JOURNAL]), store)
