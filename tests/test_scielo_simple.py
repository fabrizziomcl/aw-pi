"""Pruebas sin red de scraping_simple/scielo.py."""

import json
from types import SimpleNamespace

import pytest

import scielo

BASE = "https://www.scielo.org.pe/scielo.php"
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
    link("sci_alphabetic"): page_with_links(link("sci_serial", JOURNAL), link("sci_serial", OTHER_JOURNAL)),
    link("sci_serial", JOURNAL): page_with_links(link("sci_issuetoc", f"{JOURNAL}20200001")),
    link("sci_issuetoc", f"{JOURNAL}20200001"): page_with_links(
        link("sci_arttext", PID_ES), link("sci_abstract", PID_EN), link("sci_arttext", PID_OTHER)
    ),
    link("sci_arttext", PID_ES): article_html("Modelos en Perú", "es", ABSTRACT_ES),
    link("sci_arttext", PID_EN): article_html("Models in Peru", "en", "This study analyses machine learning models."),
}


@pytest.fixture
def sitio(tmp_path, monkeypatch):
    """Sitio falso: devuelve las páginas de SITE y recuerda las URL pedidas."""
    pedidas = []
    paginas = dict(SITE)

    def descargar(url):
        pedidas.append(url)
        if isinstance(paginas[url], Exception):
            raise paginas[url]
        return SimpleNamespace(url=url, content=paginas[url], status_code=200)

    monkeypatch.setattr(scielo, "CARPETA", str(tmp_path / "data"))
    monkeypatch.setattr(scielo, "REVISTAS", [JOURNAL])
    monkeypatch.setattr(scielo, "IDIOMAS", ["es"])
    monkeypatch.setattr(scielo, "descargar", descargar)
    return SimpleNamespace(pedidas=pedidas, paginas=paginas, carpeta=tmp_path / "data")


def estados(sitio):
    registros = [json.loads(linea) for linea in (sitio.carpeta / "articulos.jsonl").read_text(encoding="utf-8").splitlines()]
    return {r["url"]: r["status"] for r in registros}


def test_leer_articulo_extrae_metadatos_y_resumen_en_espanol():
    reg = scielo.leer_articulo(article_html("Modelos en Perú", "es", ABSTRACT_ES), link("sci_arttext", PID_ES))
    assert reg["title"] == "Modelos en Perú"
    assert reg["authors"] == ["Pérez, Ana"]
    assert reg["year"] == 2020
    assert reg["languages"] == ["es"]
    assert reg["abstract_language"] == "es"
    assert reg["abstract"] == ABSTRACT_ES
    assert reg["pid"] == PID_ES


def test_leer_articulo_rechaza_pagina_de_bloqueo_y_html_sin_articulo():
    with pytest.raises(ValueError):
        scielo.leer_articulo(b"<html><head><title>Just a moment...</title></head></html>", link("sci_alphabetic"))
    with pytest.raises(ValueError):
        scielo.leer_articulo(b"<html><head><title>Hola</title></head><body>nada</body></html>", link("sci_alphabetic"))


def test_url_catalogo_normaliza_y_filtra():
    assert scielo.url_catalogo(link("sci_abstract", PID_EN), BASE) == link("sci_arttext", PID_EN)
    assert scielo.url_catalogo("https://otro.org/scielo.php?script=sci_arttext&pid=X", BASE) is None
    assert scielo.url_catalogo(f"{BASE}?script=sci_arttext", BASE) is None  # sin pid
    assert scielo.url_catalogo(f"{BASE}?script=sci_pdf&pid=X", BASE) is None  # script no permitido


def test_ejecutar_guarda_espanol_filtra_ingles_y_no_visita_otras_revistas(sitio):
    assert scielo.ejecutar(5) == 2  # solo hay 1 artículo válido: el catálogo se agota
    assert estados(sitio) == {link("sci_arttext", PID_ES): "ok", link("sci_arttext", PID_EN): "filtrado"}
    assert link("sci_serial", OTHER_JOURNAL) not in sitio.pedidas
    report = json.loads((sitio.carpeta / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "exhausted" and report["counts"]["valid_records"] == 1
    corpus = (sitio.carpeta / "corpus.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(corpus) == 1 and json.loads(corpus[0])["abstract"] == ABSTRACT_ES


def test_ejecutar_alcanza_el_objetivo_y_se_reanuda_sin_repetir_peticiones(sitio):
    assert scielo.ejecutar(1) == 0
    assert json.loads((sitio.carpeta / "report.json").read_text(encoding="utf-8"))["status"] == "passed"
    sitio.pedidas.clear()
    assert scielo.ejecutar(1) == 0
    assert sitio.pedidas == []


def test_ejecutar_reanuda_el_catalogo_agotado_sin_repetir_peticiones(sitio):
    scielo.ejecutar(5)
    sitio.pedidas.clear()
    assert scielo.ejecutar(5) == 2
    assert sitio.pedidas == []


def test_bloqueo_no_se_reporta_como_exitoso(sitio):
    sitio.paginas[link("sci_alphabetic")] = PermissionError("Acceso restringido, HTTP 403")
    assert scielo.ejecutar(1) == 2
    report = json.loads((sitio.carpeta / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "blocked" and report["counts"]["valid_records"] == 0


def test_error_de_parseo_se_registra_y_continua(sitio):
    sitio.paginas[link("sci_arttext", PID_ES)] = b"<html><head><title>Hola</title></head><body>vacio</body></html>"
    assert scielo.ejecutar(5) == 2
    assert estados(sitio) == {link("sci_arttext", PID_ES): "error", link("sci_arttext", PID_EN): "filtrado"}


def test_cambiar_filtros_se_rechaza_pero_ampliar_revistas_se_permite(sitio, monkeypatch):
    scielo.ejecutar(5)
    scielo.cargar_estado()  # mismos filtros: sin error
    monkeypatch.setattr(scielo, "REVISTAS", [JOURNAL, OTHER_JOURNAL])
    scielo.cargar_estado()  # ampliación permitida
    monkeypatch.setattr(scielo, "REVISTAS", [JOURNAL])
    monkeypatch.setattr(scielo, "IDIOMAS", ["en"])
    with pytest.raises(ValueError):
        scielo.cargar_estado()
    monkeypatch.setattr(scielo, "IDIOMAS", ["es"])
    monkeypatch.setattr(scielo, "REVISTAS", [OTHER_JOURNAL])
    with pytest.raises(ValueError):
        scielo.cargar_estado()
