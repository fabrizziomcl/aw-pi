"""Pruebas sin red de scraping/scielo.py."""

import json
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup as bs

import scielo

B = "https://www.scielo.org.pe/scielo.php"
R1, R2 = "1810-9993", "2077-1886"
ES = "Este estudio analiza el rendimiento de los modelos en la industria y en las empresas del país."
EN = "This study analyses the performance of the models in the industry and the firms of the country."


def articulo(titulo, resumen):
    return f"""<html><head><meta name="citation_title" content="{titulo}">
    <meta name="citation_journal_title" content="Revista"><meta name="citation_author" content="Pérez, Ana">
    <meta name="citation_author" content="Rojas, Luis"></head>
    <body><p><b>RESUMEN</b></p><p>{resumen}</p><p>Palabras clave: datos</p></body></html>"""


def indice(script, *pids):
    return "".join(f'<a href="{B}?script={script}&pid={p}&lng=es">x</a>' for p in pids)


def art(revista, n):
    return f"S{revista}2020000100{n:03d}"


SITIO = {
    f"{B}?script=sci_alphabetic&lng=es&nrm=iso": indice("sci_serial", R1, R2),
    f"{B}?script=sci_issues&pid={R1}&lng=es&nrm=iso": indice("sci_issuetoc", f"{R1}20200001"),
    f"{B}?script=sci_issues&pid={R2}&lng=es&nrm=iso": indice("sci_issuetoc", f"{R2}20200001"),
    f"{B}?script=sci_issuetoc&pid={R1}20200001&lng=es&nrm=iso": indice("sci_arttext", art(R1, 1), art(R1, 2)),
    f"{B}?script=sci_issuetoc&pid={R2}20200001&lng=es&nrm=iso": indice("sci_abstract", art(R2, 1)),
}
for pid, resumen in [(art(R1, 1), ES), (art(R1, 2), ES), (art(R2, 1), EN)]:
    SITIO[f"{B}?script=sci_arttext&pid={pid}&lng=es&nrm=iso&tlng=es"] = articulo("Título " + pid, resumen)


@pytest.fixture
def sitio(tmp_path, monkeypatch):
    pedidas = []

    def descargar(url):
        pedidas.append(url)
        return SimpleNamespace(content=SITIO[url].encode())

    monkeypatch.setattr(scielo, "CARPETA", str(tmp_path))
    monkeypatch.setattr(scielo, "descargar", descargar)
    return SimpleNamespace(pedidas=pedidas, carpeta=tmp_path)


def test_leer_articulo_y_resumen_en_espanol():
    reg = scielo.leer_articulo(bs(articulo("Modelos", ES), "html.parser"), art(R1, 1), "u")
    assert reg == {"paper_id": f"scielo:{art(R1, 1)}", "title": "Modelos", "abstract": ES,
                   "authors": "Pérez, Ana; Rojas, Luis", "url": "u", "year": 2020, "journal": "Revista"}
    # sección de resumen con rótulo y palabras clave: se quitan; la versión en inglés no se toma
    html = (f'<div class="abstract"><h4>Abstract</h4><p>{EN}</p></div>'
            f'<div class="trans-abstract"><p class="sec">RESUMEN</p><p>{ES}</p><p>Palabras clave: a; b</p></div>')
    assert scielo.resumen_es(bs(html, "html.parser")) == ES


def test_corrida_alterna_revistas_filtra_ingles_y_reanuda(sitio):
    scielo.ejecutar(5)
    regs = [json.loads(l) for l in (sitio.carpeta / "articulos.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [r["paper_id"][8:17] for r in regs] == [R1, R2, R1]  # un artículo de cada revista por turno
    assert [r["status"] for r in regs] == ["ok", "filtrado", "ok"]
    corpus = (sitio.carpeta / "corpus.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(corpus) == 2 and list(json.loads(corpus[0])) == scielo.CAMPOS
    sitio.pedidas.clear()
    scielo.ejecutar(5)
    # solo se relee el catálogo (por si hay revistas nuevas); urls.json y articulos.jsonl evitan el resto
    assert sitio.pedidas == [f"{B}?script=sci_alphabetic&lng=es&nrm=iso"]
