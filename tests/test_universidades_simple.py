"""Pruebas sin red de scraping_simple/universidades.py. Los nombres y DNI de los HTML de prueba son ficticios."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import comun
import universidades

PERFIL = Path(__file__).resolve().parents[1] / "config.universidades.json"
UUID = "11111111-2222-3333-4444-555555555555"
BASE = "https://repo.example.edu.pe"


def hacer_config(tmp_path, **repo):
    valores = {
        "data_dir": str(tmp_path / "data"),
        "delay_seconds": 1,
        "repos": [{"key": "demo", "name": "Universidad Demo", "base_url": BASE, **repo}],
    }
    ruta = tmp_path / "config.json"
    ruta.write_text(json.dumps(valores), encoding="utf-8")
    config = universidades.cargar_config(ruta)
    universidades.cfg.clear()
    universidades.cfg.update(config)
    return config


def pagina_estado(uuid=UUID, dc_type="info:eu-repo/semantics/bachelorThesis"):
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
    estado = {"NGRX_STATE": {"cache": {"x": {"payload": item}}}}
    crudo = json.dumps(estado).replace("&", "&a;").replace('"', "&q;")
    return (
        "<html><head><title>Ficha</title>"
        '<meta name="citation_title" content="Calidad del agua en la cuenca del río Rímac">'
        '<meta name="citation_pdf_url" content="http://localhost:4000/bitstreams/abc/download">'
        f'</head><body><script id="dspace-angular-state" type="application/json">{crudo}</script></body></html>'
    )


@pytest.fixture
def sitio(monkeypatch):
    """Descarga falsa: un valor str es la página; un valor Exception se lanza."""
    paginas = {}

    def descargar(url):
        if url not in paginas:
            raise AssertionError(f"URL inesperada: {url}")
        valor = paginas[url]
        if isinstance(valor, Exception):
            raise valor
        return SimpleNamespace(url=url, content=valor.encode("utf-8"), status_code=200)

    monkeypatch.setattr(universidades, "descargar", descargar)
    return paginas


def test_perfil_se_carga():
    config = universidades.cargar_config(PERFIL)
    assert len(config["repos"]) == 12
    assert not {"une", "pucp"} & {r["key"] for r in config["repos"]}  # antibots en las fichas
    # UPC responde 429 a las fichas renderizadas: solo se ejecuta si se pide con --repos upc
    assert [r["key"] for r in config["repos"] if not r["enabled"]] == ["upc"]
    universidades.cfg.clear()
    universidades.cfg.update(config)
    assert len(universidades.elegir([])) == 11 and universidades.elegir(["upc"])[0]["key"] == "upc"
    assert sum(r["render"] for r in config["repos"]) == 7


def test_paginas_de_error_y_desafios():
    assert universidades.codigo_error("<html><head><title>429 Too Many Requests</title></head></html>") == 429
    assert universidades.codigo_error("<h1>Too Many Requests</h1>") == 429
    assert universidades.codigo_error("<title>503 Service Unavailable</title>") == 503
    assert universidades.codigo_error("<title>Repositorio UPC :: Home</title>") is None
    assert comun.es_bloqueo("<title>Making sure you're not a bot!</title>")
    assert not comun.es_bloqueo("<title>Repositorio UPC :: Home</title>")


def test_robots_comodines_grupos_y_retraso():
    reglas, retraso = comun.reglas_robots(
        "User-agent: *\nDisallow: /search\nDisallow: /items/*/full\n"
        "Allow: /server/api/core/bitstreams\nDisallow: /server/api/*\n"
        "User-agent: Googlebot\nAllow: /\n"
        "User-agent: *\nCrawl-delay: 10\n",
        "UniversidadesMVP",
    )
    assert retraso == 10
    assert comun.permitido(reglas, "https://x.pe/items/abc")
    assert not comun.permitido(reglas, "https://x.pe/items/abc/full")
    assert not comun.permitido(reglas, "https://x.pe/search?query=a")
    assert not comun.permitido(reglas, "https://x.pe/server/api/core/items/abc")
    assert comun.permitido(reglas, "https://x.pe/server/api/core/bitstreams/abc/content")
    cerrado, _ = comun.reglas_robots("User-agent: *\nDisallow: /\n", "UniversidadesMVP")
    assert not comun.permitido(cerrado, "https://x.pe/item/1")


def test_robots_ausente_o_con_error():
    assert comun.interpretar_robots(404, "", "UniversidadesMVP") == ([], 0)
    assert comun.interpretar_robots(200, "<!doctype html><html>", "UniversidadesMVP") == ([], 0)
    with pytest.raises(PermissionError):
        comun.interpretar_robots(429, "", "UniversidadesMVP")
    with pytest.raises(PermissionError):  # SciELO es estricto: un robots.txt HTML no se da por válido
        comun.interpretar_robots(200, "<html>", "SciELO", estricto=True)


def test_estado_conserva_campos_de_tesis_y_descarta_datos_personales(tmp_path):
    config = hacer_config(tmp_path)
    reg = universidades.leer_ficha(pagina_estado(), f"{BASE}/items/{UUID}", config["repos"][0])
    assert reg["metadata_sources"][0] == "estado"
    assert reg["is_thesis"] and reg["degree_name"] == "Ingeniero Ambiental"
    assert reg["abstract"].startswith("El objetivo") and reg["abstract_language"] == "es"
    assert reg["advisors"] == ["Gómez Ríos, Luis"]
    assert reg["pdf_url"] == f"{BASE}/bitstreams/abc/download"
    assert "12345678" not in json.dumps(reg) and "Jurado" not in json.dumps(reg)


def test_tabla_completa_y_meta_dspace6(tmp_path):
    repo = hacer_config(tmp_path)["repos"][0]
    tabla = (
        "<table><tr><td>dc.title</td><td>Estudio de suelos agrícolas</td><td>es</td></tr>"
        "<tr><td>dc.contributor.author</td><td>Rojas, Juan</td><td></td></tr>"
        "<tr><td>dc.description.abstract</td><td>Se evaluó la fertilidad de los suelos de la región y su manejo.</td><td></td></tr>"
        "<tr><td>renati.type</td><td>https://purl.org/pe-repo/renati/type#trabajoDeSuficienciaProfesional</td><td></td></tr>"
        "<tr><td>renati.advisor.dni</td><td>87654321</td><td></td></tr></table>"
    )
    reg = universidades.leer_ficha(tabla, f"{BASE}/items/{UUID}/full", repo)
    assert reg["is_thesis"] and reg["thesis_evidence"] == "renati.type"
    assert "87654321" not in json.dumps(reg)
    dc6 = (
        '<meta name="DC.title" content="Los enciclopedistas">'
        '<meta name="DC.creator" content="Lozano, Godofredo">'
        '<meta name="DCTERMS.abstract" content="Realiza un estudio sobre el pensamiento de los autores de la época.">'
        '<meta name="DCTERMS.issued" content="1880">'
        '<meta name="DC.type" content="info:eu-repo/semantics/doctoralThesis">'
    )
    reg = universidades.leer_ficha(dc6, f"{BASE}/handle/UNMSM/1061", repo)
    assert reg["paper_id"] == "demo:UNMSM/1061" and reg["year"] == 1880 and reg["is_thesis"]


def test_parser_mui_usa_rotulos_y_coleccion(tmp_path):
    repo = hacer_config(tmp_path, parser="mui")["repos"][0]
    html = (
        '<span class="MuiTypography-root MuiTypography-h5">Desarrollo de rúbricas para evaluar competencias</span>'
        "<div><h6>Autor(es)</h6><div><div>Cárdenas, Severo</div><div>García, Beder</div></div></div>"
        "<div><h6>Resumen</h6><div><p>Se realizó un estudio instrumental para determinar las características de las rúbricas.</p></div></div>"
        "<div><h6>Pertenece a la colección</h6><a>Trabajos de Investigación de Segunda Especialidad</a></div>"
        '<li><a href="/backend/api/core/bitstreams/abc/content">tesis.pdf</a></li>'
    )
    reg = universidades.leer_ficha(html, f"{BASE}/item/{UUID}", repo)
    assert reg["authors"] == ["Cárdenas, Severo", "García, Beder"]
    assert reg["is_thesis"] and reg["thesis_evidence"] == "colección"
    assert reg["language"] == "es"
    assert reg["pdf_url"].endswith("/bitstreams/abc/content")


def test_mui_coleccion_desde_la_ruta_de_navegacion(tmp_path):
    repo = hacer_config(tmp_path, parser="mui")["repos"][0]
    html = (
        '<div><a href="/">Inicio</a><a href="/community/abc">Facultad de Agronomía</a>'
        '<a href="/collection/def">Trabajos de Investigación de Pregrado</a></div>'
        '<span class="MuiTypography-root MuiTypography-h5">Abonos orgánicos y rendimiento de quinua</span>'
        "<div><h6>Resumen</h6><div>La investigación evaluó el efecto de los abonos en la fenología de la quinua.</div></div>"
    )
    reg = universidades.leer_ficha(html, f"{BASE}/item/{UUID}", repo)
    assert reg["collection"] == "Trabajos de Investigación de Pregrado"
    assert reg["is_thesis"] and reg["thesis_evidence"] == "colección"


def test_ficha_solo_con_citation_no_es_tesis(tmp_path):
    config = hacer_config(tmp_path)
    html = (
        '<meta name="citation_title" content="Sílabo de Estadística Aplicada">'
        '<meta name="description" content="El curso de estadística aplicada desarrolla las competencias de los estudiantes.">'
    )
    reg = universidades.leer_ficha(html, f"{BASE}/items/{UUID}", config["repos"][0])
    assert "not_thesis" in universidades.motivos(reg)
    assert "abstract_from_meta_description" in reg["warnings"]
    assert universidades.leer_ficha("<html><body>portada</body></html>", BASE, config["repos"][0]) is None


def test_sitemap_reescribe_urls_y_filtra_fichas(tmp_path, sitio):
    repo = hacer_config(
        tmp_path,
        sitemap_rewrites=[["^http://localhost:4000", BASE]],
        item_rewrites=[["/items/", "/item/"]],
        item_pattern="/item/[0-9a-f-]{36}/?$",
    )["repos"][0]
    sitio.update({
        f"{BASE}/sitemap_index.xml": "<sitemapindex><sitemap><loc>http://localhost:4000/sitemap0.xml</loc></sitemap></sitemapindex>",
        f"{BASE}/sitemap0.xml": (
            f"<urlset><url><loc>http://repo.example.edu.pe/items/{UUID}</loc></url>"
            "<url><loc>https://repo.example.edu.pe/communities/abc</loc></url></urlset>"
        ),
    })
    assert universidades.sitemap_items(repo) == [f"{BASE}/item/{UUID}"]


def test_browse_sigue_el_siguiente_offset(tmp_path, sitio):
    repo = hacer_config(tmp_path, discovery="browse", sitemap="/browse?type=dateissued&offset=0",
                        item_pattern=r"/handle/UNMSM/\d+$")["repos"][0]
    base = f"{BASE}/browse?type=dateissued"
    sitio.update({
        base + "&offset=0": '<a href="/handle/UNMSM/1">a</a><a href="browse?type=dateissued&offset=2">siguiente</a>',
        base + "&offset=2": (
            '<a href="/handle/UNMSM/2">b</a><a href="browse?type=dateissued&offset=0">anterior</a>'
            '<a href="/handle/UNMSM/1">a</a>'
        ),
    })
    assert universidades.browse_items(repo) == [f"{BASE}/handle/UNMSM/1", f"{BASE}/handle/UNMSM/2"]


def test_corrida_sin_red_exporta_solo_tesis_validas_y_se_reanuda(tmp_path, sitio):
    otro = "99999999-8888-7777-6666-555555555555"
    sitio.update({
        f"{BASE}/sitemap_index.xml": (
            f"<urlset><url><loc>{BASE}/items/{UUID}</loc></url><url><loc>{BASE}/items/{otro}</loc></url></urlset>"
        ),
        f"{BASE}/items/{UUID}": pagina_estado(),
        f"{BASE}/items/{otro}": pagina_estado(otro, "info:eu-repo/semantics/article"),
    })
    config = hacer_config(tmp_path)
    assert universidades.ejecutar(config, ["demo"], 1) == 0
    raiz = Path(config["data_dir"])
    informe = json.loads((raiz / "demo" / "report.json").read_text(encoding="utf-8"))
    assert informe["status"] == "passed" and informe["counts"]["valid_theses"] == 1
    filas = (raiz / "corpus.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(filas) == 1 and json.loads(filas[0])["paper_id"] == f"demo:{UUID}"
    for ruta in raiz.rglob("*.json*"):
        assert "12345678" not in ruta.read_text(encoding="utf-8")
    sitio.clear()  # reanudar no debe pedir nada: cualquier URL inesperada falla
    assert universidades.ejecutar(config, ["demo"], 1) == 0


def test_repositorio_bloqueado_no_se_reporta_como_exitoso(tmp_path, sitio):
    sitio[f"{BASE}/sitemap_index.xml"] = PermissionError("Desafío antibots ('making sure you're not a bot')")
    config = hacer_config(tmp_path)
    assert universidades.ejecutar(config, ["demo"], 1) == 2
    informe = json.loads((Path(config["data_dir"]) / "demo" / "report.json").read_text(encoding="utf-8"))
    assert informe["status"] == "blocked"


def test_cambiar_el_descubrimiento_exige_otra_carpeta(tmp_path, sitio):
    sitio[f"{BASE}/sitemap_index.xml"] = f"<urlset><url><loc>{BASE}/items/{UUID}</loc></url></urlset>"
    sitio[f"{BASE}/items/{UUID}"] = pagina_estado()
    config = hacer_config(tmp_path)
    universidades.ejecutar(config, ["demo"], 1)
    config = hacer_config(tmp_path, item_pattern=r"/handle/\d+$")
    assert universidades.ejecutar(config, ["demo"], 1) == 2
    informe = json.loads((Path(config["data_dir"]) / "demo" / "report.json").read_text(encoding="utf-8"))
    assert informe["status"] == "error" and "cambió" in informe["error"]
