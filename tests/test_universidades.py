"""Pruebas sin red de universidades_mvp. Los nombres y DNI de los HTML de prueba son ficticios."""

import json
from pathlib import Path

from universidades_mvp import run as runner
from universidades_mvp.config import Config
from universidades_mvp.discovery import browse_items, sitemap_items
from universidades_mvp.http import Blocked, Page, Robots, challenge, error_page
from universidades_mvp.parsers import parse_item
from universidades_mvp.validation import qualification_errors

PROFILE = Path(__file__).resolve().parents[1] / "config.universidades.json"
UUID = "11111111-2222-3333-4444-555555555555"


def make_config(tmp_path, **repo):
    values = {
        "data_dir": str(tmp_path / "data"),
        "delay_seconds": 1,
        "repos": [{"key": "demo", "name": "Universidad Demo", "base_url": "https://repo.example.edu.pe", **repo}],
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    return Config.load(path)


def state_page(uuid=UUID, dc_type="info:eu-repo/semantics/bachelorThesis"):
    item = {
        "uuid": uuid,
        "metadata": {
            "dc.title": [{"value": "Calidad del agua en la cuenca del río Rímac", "language": "spa"}],
            "dc.contributor.author": [{"value": "Pérez Quispe, Ana", "language": None}],
            "dc.contributor.advisor": [{"value": "Gómez Ríos, Luis", "language": None}],
            "dc.description.abstract": [
                {"value": "The objective of this study was to evaluate the water quality of the river.", "language": "spa"},
                {"value": "El objetivo de la presente investigación fue evaluar la calidad del agua en la cuenca.", "language": "spa"},
            ],
            "dc.date.issued": [{"value": "2021-05-04", "language": None}],
            "dc.language.iso": [{"value": "spa", "language": None}],
            "dc.type": [{"value": dc_type, "language": None}],
            "renati.author.dni": [{"value": "12345678", "language": None}],
            "renati.juror": [{"value": "Jurado Ficticio, Uno", "language": None}],
            "thesis.degree.name": [{"value": "Ingeniero Ambiental", "language": None}],
        },
    }
    state = {"NGRX_STATE": {"cache": {"x": {"payload": item}}}}
    raw = json.dumps(state).replace("&", "&a;").replace('"', "&q;")
    return (
        "<html><head><title>Ficha</title>"
        '<meta name="citation_title" content="Calidad del agua en la cuenca del río Rímac">'
        '<meta name="citation_pdf_url" content="http://localhost:4000/bitstreams/abc/download">'
        f'</head><body><script id="dspace-angular-state" type="application/json">{raw}</script></body></html>'
    )


def test_profile_loads():
    cfg = Config.load(PROFILE)
    assert len(cfg.repos) == 14
    # UPC responde 429 a las fichas renderizadas: solo se ejecuta si se pide con --repos upc.
    assert [repo.key for repo in cfg.repos if not repo.enabled] == ["upc"]
    assert len(cfg.selected()) == 13 and cfg.selected(["upc"])[0].key == "upc"
    assert sum(repo.render for repo in cfg.repos) == 7


def test_error_pages_are_detected():
    assert error_page("<html><head><title>429 Too Many Requests</title></head></html>") == 429
    assert error_page("<h1>Too Many Requests</h1>") == 429
    assert error_page("<title>503 Service Unavailable</title>") == 503
    assert error_page("<title>Repositorio UPC :: Home</title>") is None


def test_robots_wildcards_groups_and_delay():
    robots = Robots(
        "User-agent: *\nDisallow: /search\nDisallow: /items/*/full\n"
        "Allow: /server/api/core/bitstreams\nDisallow: /server/api/*\n"
        "User-agent: Googlebot\nAllow: /\n"
        "User-agent: *\nCrawl-delay: 10\n",
        "UniversidadesMVP",
    )
    assert robots.crawl_delay == 10
    assert robots.allowed("https://x.pe/items/abc")
    assert not robots.allowed("https://x.pe/items/abc/full")
    assert not robots.allowed("https://x.pe/search?query=a")
    assert not robots.allowed("https://x.pe/server/api/core/items/abc")
    assert robots.allowed("https://x.pe/server/api/core/bitstreams/abc/content")
    assert not Robots("User-agent: *\nDisallow: /\n", "UniversidadesMVP").allowed("https://x.pe/item/1")


def test_state_parser_keeps_thesis_fields_and_drops_personal_ids(tmp_path):
    cfg = make_config(tmp_path)
    url = f"https://repo.example.edu.pe/items/{UUID}"
    record = parse_item(state_page(), url, cfg.repos[0])
    assert record["metadata_sources"][0] == "estado"
    assert record["is_thesis"] and record["degree_name"] == "Ingeniero Ambiental"
    assert record["abstract"].startswith("El objetivo") and record["abstract_language"] == "es"
    assert record["advisors"] == ["Gómez Ríos, Luis"]
    assert record["pdf_url"] == "https://repo.example.edu.pe/bitstreams/abc/download"
    assert "12345678" not in json.dumps(record) and "Jurado" not in json.dumps(record)


def test_full_table_and_dspace6_meta(tmp_path):
    cfg = make_config(tmp_path)
    table = (
        "<table><tr><td>dc.title</td><td>Estudio de suelos agrícolas</td><td>es</td></tr>"
        "<tr><td>dc.contributor.author</td><td>Rojas, Juan</td><td></td></tr>"
        "<tr><td>dc.description.abstract</td><td>Se evaluó la fertilidad de los suelos de la región y su manejo.</td><td></td></tr>"
        "<tr><td>renati.type</td><td>https://purl.org/pe-repo/renati/type#trabajoDeSuficienciaProfesional</td><td></td></tr>"
        "<tr><td>renati.advisor.dni</td><td>87654321</td><td></td></tr></table>"
    )
    record = parse_item(table, f"https://repo.example.edu.pe/items/{UUID}/full", cfg.repos[0])
    assert record["is_thesis"] and record["thesis_evidence"] == "renati.type"
    assert "87654321" not in json.dumps(record)
    dc6 = (
        '<meta name="DC.title" content="Los enciclopedistas">'
        '<meta name="DC.creator" content="Lozano, Godofredo">'
        '<meta name="DCTERMS.abstract" content="Realiza un estudio sobre el pensamiento de los autores de la época.">'
        '<meta name="DCTERMS.issued" content="1880">'
        '<meta name="DC.type" content="info:eu-repo/semantics/doctoralThesis">'
    )
    record = parse_item(dc6, "https://repo.example.edu.pe/handle/UNMSM/1061", cfg.repos[0])
    assert record["paper_id"] == "demo:UNMSM/1061" and record["year"] == 1880 and record["is_thesis"]


def test_mui_parser_uses_labels_and_collection(tmp_path):
    cfg = make_config(tmp_path, parser="mui")
    html = (
        '<span class="MuiTypography-root MuiTypography-h5">Desarrollo de rúbricas para evaluar competencias</span>'
        "<div><h6>Autor(es)</h6><div><div>Cárdenas, Severo</div><div>García, Beder</div></div></div>"
        "<div><h6>Resumen</h6><div><p>Se realizó un estudio instrumental para determinar las características de las rúbricas.</p></div></div>"
        "<div><h6>Pertenece a la colección</h6><a>Trabajos de Investigación de Segunda Especialidad</a></div>"
        '<li><a href="/backend/api/core/bitstreams/abc/content">tesis.pdf</a></li>'
    )
    record = parse_item(html, f"https://repo.example.edu.pe/item/{UUID}", cfg.repos[0])
    assert record["authors"] == ["Cárdenas, Severo", "García, Beder"]
    assert record["is_thesis"] and record["thesis_evidence"] == "colección"
    assert record["language"] == "es"
    assert record["pdf_url"].endswith("/bitstreams/abc/content")


def test_mui_collection_from_breadcrumb(tmp_path):
    cfg = make_config(tmp_path, parser="mui")
    html = (
        '<div><a href="/">Inicio</a><a href="/community/abc">Facultad de Agronomía</a>'
        '<a href="/collection/def">Trabajos de Investigación de Pregrado</a></div>'
        '<span class="MuiTypography-root MuiTypography-h5">Abonos orgánicos y rendimiento de quinua</span>'
        "<div><h6>Resumen</h6><div>La investigación evaluó el efecto de los abonos en la fenología de la quinua.</div></div>"
    )
    record = parse_item(html, f"https://repo.example.edu.pe/item/{UUID}", cfg.repos[0])
    assert record["collection"] == "Trabajos de Investigación de Pregrado"
    assert record["is_thesis"] and record["thesis_evidence"] == "colección"


def test_citation_only_record_is_not_a_thesis(tmp_path):
    cfg = make_config(tmp_path)
    html = (
        '<meta name="citation_title" content="Sílabo de Estadística Aplicada">'
        '<meta name="description" content="El curso de estadística aplicada desarrolla las competencias de los estudiantes.">'
    )
    record = parse_item(html, f"https://repo.example.edu.pe/items/{UUID}", cfg.repos[0])
    record["transport"] = {"mode": "selenium"}
    assert "not_thesis" in qualification_errors(record, cfg)
    assert "abstract_from_meta_description" in record["warnings"]


class FakeClient:
    pages = {}

    def __init__(self, cfg):
        self.cfg = cfg

    def get(self, url):
        if url not in self.pages:
            raise AssertionError(f"URL inesperada: {url}")
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        return Page(url, value.encode("utf-8"), 200, {})

    def check(self, url):
        pass

    def wait(self, origin):
        pass

    def close(self):
        pass


def test_sitemap_rewrites_and_item_filter(tmp_path):
    cfg = make_config(
        tmp_path,
        sitemap_rewrites=[["^http://localhost:4000", "https://repo.example.edu.pe"]],
        item_rewrites=[["/items/", "/item/"]],
        item_pattern="/item/[0-9a-f-]{36}/?$",
    )
    FakeClient.pages = {
        "https://repo.example.edu.pe/sitemap_index.xml": "<sitemapindex><sitemap><loc>http://localhost:4000/sitemap0.xml</loc></sitemap></sitemapindex>",
        "https://repo.example.edu.pe/sitemap0.xml": (
            f"<urlset><url><loc>http://repo.example.edu.pe/items/{UUID}</loc></url>"
            "<url><loc>https://repo.example.edu.pe/communities/abc</loc></url></urlset>"
        ),
    }
    assert sitemap_items(cfg.repos[0], FakeClient(cfg)) == [f"https://repo.example.edu.pe/item/{UUID}"]


def test_browse_follows_next_offset(tmp_path):
    cfg = make_config(tmp_path, discovery="browse", sitemap="/browse?type=dateissued&offset=0", item_pattern=r"/handle/UNMSM/\d+$")
    base = "https://repo.example.edu.pe/browse?type=dateissued"
    FakeClient.pages = {
        base + "&offset=0": '<a href="/handle/UNMSM/1">a</a><a href="browse?type=dateissued&offset=2">siguiente</a>',
        base + "&offset=2": (
            '<a href="/handle/UNMSM/2">b</a><a href="browse?type=dateissued&offset=0">anterior</a>'
            '<a href="/handle/UNMSM/1">a</a>'
        ),
    }
    assert browse_items(cfg.repos[0], FakeClient(cfg)) == [
        "https://repo.example.edu.pe/handle/UNMSM/1",
        "https://repo.example.edu.pe/handle/UNMSM/2",
    ]


def test_offline_run_exports_only_valid_theses(tmp_path, monkeypatch):
    other = "99999999-8888-7777-6666-555555555555"
    FakeClient.pages = {
        "https://repo.example.edu.pe/sitemap_index.xml": (
            f"<urlset><url><loc>https://repo.example.edu.pe/items/{UUID}</loc></url>"
            f"<url><loc>https://repo.example.edu.pe/items/{other}</loc></url></urlset>"
        ),
        f"https://repo.example.edu.pe/items/{UUID}": state_page(),
        f"https://repo.example.edu.pe/items/{other}": state_page(other, "info:eu-repo/semantics/article"),
    }
    monkeypatch.setattr(runner, "Client", FakeClient)
    cfg = make_config(tmp_path)
    assert runner.run(cfg, target=1) == 0
    root = Path(cfg.data_dir)
    report = json.loads((root / "demo" / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "passed" and report["counts"]["valid_theses"] == 1
    rows = (root / "corpus.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1 and json.loads(rows[0])["paper_id"] == f"demo:{UUID}"
    for path in root.rglob("*.json*"):
        assert "12345678" not in path.read_text(encoding="utf-8")
    assert runner.run(cfg, target=1, verify_only=True) == 0


def test_blocked_repository_is_not_reported_as_passed(tmp_path, monkeypatch):
    FakeClient.pages = {
        "https://repo.example.edu.pe/sitemap_index.xml": Blocked("Desafío antibots ('making sure you're not a bot')")
    }
    monkeypatch.setattr(runner, "Client", FakeClient)
    cfg = make_config(tmp_path)
    assert runner.run(cfg, target=1) == 2
    report = json.loads((Path(cfg.data_dir) / "demo" / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert challenge("<title>Making sure you're not a bot!</title>")
