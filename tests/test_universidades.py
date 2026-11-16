"""Pruebas sin red de scraping/universidades.py. Los nombres son ficticios."""

import json
from types import SimpleNamespace

import pytest

import comun
import universidades as u

UUID = "11111111-2222-3333-4444-555555555555"
BASE = "https://repo.example.edu.pe"
RESUMEN = "El objetivo de la investigación fue evaluar la calidad del agua en la cuenca del río y su manejo."


def repo(**cambios):
    return {**u.REPO, "key": "demo", "name": "Universidad Demo", "base_url": BASE, **cambios}


def tabla(tipo="info:eu-repo/semantics/bachelorThesis"):
    filas = [("dc.title", "Calidad del agua"), ("dc.contributor.author", "Pérez, Ana"),
             ("dc.contributor.author", "Rojas, Luis"), ("dc.date.issued", "2021-05-04"),
             ("dc.description.abstract", "The objective was to evaluate the water quality of the river."),
             ("dc.description.abstract", RESUMEN), ("dc.type", tipo), ("renati.author.dni", "12345678")]
    return "<table>" + "".join(f"<tr><td>{c}</td><td>{v}</td><td>es</td></tr>" for c, v in filas) + "</table>"


@pytest.fixture
def sitio(tmp_path, monkeypatch):
    paginas = {}

    def descargar(url):
        return SimpleNamespace(content=paginas[url].encode(), url=url)

    monkeypatch.setattr(u, "descargar", descargar)
    monkeypatch.setattr(u, "CARPETA", str(tmp_path))
    monkeypatch.setattr(u, "cargar_repos", lambda: [repo()])
    return paginas


def test_robots_comodines_y_retraso():
    reglas, retraso = comun.reglas_robots(
        "User-agent: *\nDisallow: /search\nDisallow: /items/*/full$\nAllow: /server/api/core/bitstreams\n"
        "Disallow: /server/api/\n\nUser-agent: Googlebot\nAllow: /\n\nUser-agent: *\nCrawl-delay: 10\n", "UniversidadesMVP")
    assert retraso == 10
    assert comun.permitido(reglas, "https://x.pe/items/abc") and not comun.permitido(reglas, "https://x.pe/items/a/full")
    assert not comun.permitido(reglas, "https://x.pe/search?q=a") and not comun.permitido(reglas, "https://x.pe/server/api/x")
    assert comun.permitido(reglas, "https://x.pe/server/api/core/bitstreams/abc/content")


def test_ficha_tabla_y_mui():
    md = u.leer_ficha(tabla())
    assert u.es_tesis(md) and "renati.author.dni" in md  # se lee, pero no llega al registro
    assert not u.es_tesis(u.leer_ficha(tabla("info:eu-repo/semantics/article")))
    mui = ('<a href="/collection/x">Trabajos de Investigación de Pregrado</a>'
           '<span class="MuiTypography-h5">Desarrollo de rúbricas para evaluar</span>'
           "<div><h6>Autor(es)</h6><div><div>Cárdenas, Severo</div><div>García, Beder</div></div></div>"
           f"<div><h6>Resumen</h6><div>{RESUMEN}</div></div>")
    md = u.leer_ficha(mui)
    assert md["dc.title"] == ["Desarrollo de rúbricas para evaluar"] and len(md["dc.contributor.author"]) == 2
    assert u.es_tesis(md)  # por la colección


def test_descubrir_sitemap_con_reescrituras(sitio):
    sitio[f"{BASE}/sitemap_index.xml"] = "<sitemapindex><loc>http://localhost:4000/s0.xml</loc></sitemapindex>"
    sitio[f"{BASE}/s0.xml"] = f"<urlset><loc>http://repo.example.edu.pe/items/{UUID}</loc><loc>{BASE}/communities/a</loc></urlset>"
    assert u.descubrir(repo(rewrites=[["^http://localhost:4000", BASE]])) == [(f"demo:{UUID}", f"{BASE}/items/{UUID}")]


def test_corrida_guarda_7_campos_sin_dni_y_reanuda(tmp_path, sitio):
    otro = "99999999-8888-7777-6666-555555555555"
    sitio[f"{BASE}/sitemap_index.xml"] = f"<urlset><loc>{BASE}/items/{UUID}</loc><loc>{BASE}/items/{otro}</loc></urlset>"
    sitio[f"{BASE}/items/{UUID}/full"] = tabla()
    sitio[f"{BASE}/items/{otro}/full"] = tabla("info:eu-repo/semantics/article")
    u.procesar_repo(repo(), 5)
    u.consolidar()
    filas = [json.loads(l) for l in (tmp_path / "corpus.jsonl").read_text(encoding="utf-8").splitlines()]
    assert filas == [{"paper_id": f"demo:{UUID}", "title": "Calidad del agua", "abstract": RESUMEN,
                      "authors": "Pérez, Ana; Rojas, Luis", "url": f"{BASE}/items/{UUID}", "year": 2021,
                      "university": "Universidad Demo"}]
    assert "12345678" not in (tmp_path / "demo" / "fichas.jsonl").read_text(encoding="utf-8")
    sitio.clear()
    u.procesar_repo(repo(), 5)  # reanudar no pide nada: una URL inesperada fallaría
