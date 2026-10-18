"""Fichas de tesis: un esquema común a partir de varias fuentes de la misma página.

Orden de prioridad de los metadatos:
1. Estado JSON de DSpace Angular (`dspace-angular-state`): metadatos completos (DSpace 7+ con SSR).
2. Tabla de la vista completa (`/full` en DSpace 7+, `?show=full` en DSpace 6).
3. Etiquetas DC/DCTERMS de DSpace 6.
4. Rótulos de interfaces Material UI (UPN, UNHEVAL).
5. Etiquetas citation_* y la meta description.

Solo se conservan los campos de KEEP: se descartan DNI, ORCID y jurados.
"""

import json
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from scielo_mvp.parsers import ParseError, clean, language

STATE = re.compile(r'<script id="dspace-angular-state" type="application/json">(.*?)</script>', re.S)
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
FIELD = re.compile(r"[a-z]+(\.[a-zA-Z]+){1,3}")
KEEP = {
    "dc.title",
    "dc.contributor.author",
    "dc.contributor.advisor",
    "dc.date.issued",
    "dc.description.abstract",
    "dc.language.iso",
    "dc.type",
    "dc.subject",
    "dc.subject.ocde",
    "dc.publisher",
    "dc.identifier.uri",
    "dc.rights",
    "renati.type",
    "renati.level",
    "thesis.degree.name",
    "thesis.degree.level",
    "thesis.degree.discipline",
    "thesis.degree.grantor",
    "collection",
}
DC6 = {
    "dc.title": "dc.title",
    "dc.creator": "dc.contributor.author",
    "dcterms.abstract": "dc.description.abstract",
    "dcterms.issued": "dc.date.issued",
    "dc.type": "dc.type",
    "dc.language": "dc.language.iso",
    "dc.subject": "dc.subject",
    "dc.publisher": "dc.publisher",
    "dc.identifier": "dc.identifier.uri",
    "dc.rights": "dc.rights",
}
CITATION = {
    "citation_title": "dc.title",
    "citation_author": "dc.contributor.author",
    "citation_publication_date": "dc.date.issued",
    "citation_date": "dc.date.issued",
    "citation_language": "dc.language.iso",
    "citation_keywords": "dc.subject",
    "citation_publisher": "dc.publisher",
    "citation_abstract_html_url": "dc.identifier.uri",
    "citation_type": "dc.type",
}
MUI = {
    "fecha de publicación": "dc.date.issued",
    "autor(es)": "dc.contributor.author",
    "asesor(es)": "dc.contributor.advisor",
    "editorial": "dc.publisher",
    "resumen": "dc.description.abstract",
    "palabras clave": "dc.subject",
    "identificador único": "dc.identifier.uri",
    "tipo de documento": "dc.type",
    "tipo de acceso": "dc.rights",
    "pertenece a la colección": "collection",
}
THESIS_TYPE = re.compile(r"thesis|tesis|/c_7a1f|/c_bdcc|/c_db06|/c_46ec", re.I)
THESIS_RENATI = {"tesis", "trabajodesuficienciaprofesional", "trabajoacademico", "trabajodeinvestigacion"}
THESIS_COLLECTION = re.compile(r"tesis|suficiencia profesional|trabajos? (de investigaci|acad[eé]mico)|segunda especialidad", re.I)
STOPWORDS = {
    "es": {"el", "la", "los", "las", "de", "del", "que", "y", "en", "se", "por", "con", "una", "para", "su", "al", "como"},
    "en": {"the", "of", "and", "to", "in", "is", "that", "for", "with", "this", "was", "were", "are", "on", "by"},
    "pt": {"o", "os", "da", "do", "dos", "das", "não", "uma", "em", "ao", "são", "também", "foi", "pela", "pelo"},
}


class NotItem(ParseError):
    """La URL no es una ficha (comunidad, colección o redirección a la portada)."""


def soup_of(html):
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
    if any(x in title for x in ("just a moment", "not a bot", "access denied", "verify you are human")):
        raise ParseError("Página de bloqueo o verificación; no es una ficha")
    return soup


def add(md, key, value, lang=None):
    value = clean(value)
    if key in KEEP and value:
        md.setdefault(key, []).append({"value": value, "language": clean(lang) or None})


def decode_state(raw):
    # Angular < 16 escapa el JSON del TransferState con entidades propias.
    if raw.lstrip().startswith("{&q;"):
        for code, char in (("&q;", '"'), ("&s;", "'"), ("&l;", "<"), ("&g;", ">"), ("&a;", "&")):
            raw = raw.replace(code, char)
    return json.loads(raw)


def from_state(html, uuid):
    match = STATE.search(html)
    if not match or not uuid:
        return {}
    try:
        state = decode_state(match.group(1))
    except ValueError:
        return {}
    stack = [state]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("uuid") == uuid and isinstance(node.get("metadata"), dict):
                md = {}
                for key, values in node["metadata"].items():
                    for value in values if isinstance(values, list) else []:
                        if isinstance(value, dict):
                            add(md, key, str(value.get("value") or ""), value.get("language"))
                return md
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return {}


def from_table(soup):
    md = {}
    for row in soup.select("table tr"):
        cells = [clean(cell.get_text(" ", strip=True)) for cell in row.find_all(["td", "th"])]
        if len(cells) >= 2 and FIELD.fullmatch(cells[0]):
            add(md, cells[0], cells[1], cells[2] if len(cells) > 2 else None)
    return md


def from_meta(soup, mapping, *, split_keywords=False):
    md = {}
    for node in soup.find_all("meta"):
        key = DC6.get((node.get("name") or "").lower()) if mapping is DC6 else mapping.get(node.get("name"))
        content = node.get("content")
        if not key or not content:
            continue
        values = content.split(";") if split_keywords and key == "dc.subject" else [content]
        for value in values:
            add(md, key, value, node.get("xml:lang") or node.get("lang"))
    return md


def from_mui(soup):
    md = {}
    for label in soup.find_all("h6"):
        key = MUI.get(clean(label.get_text(" ", strip=True)).lower())
        box = label.find_next_sibling()
        if not key or box is None:
            continue
        children = box.find_all(recursive=False)
        if key != "dc.description.abstract" and len(children) > 1:
            for child in children:
                add(md, key, child.get_text(" ", strip=True))
        else:
            add(md, key, box.get_text(" ", strip=True))
    for node in soup.find_all(class_=re.compile(r"MuiTypography-h[45]")):
        text = clean(node.get_text(" ", strip=True))
        if len(text) > 10:
            add(md, "dc.title", text)
            break
    if "collection" not in md:
        # Muchas fichas no muestran "Pertenece a la colección"; la ruta de navegación sí.
        crumbs = [a for a in soup.select("a[href*='/collection/']") if clean(a.get_text(" ", strip=True))]
        if crumbs:
            add(md, "collection", crumbs[-1].get_text(" ", strip=True))
    return md


def pdf_link(soup, url, repo):
    meta = soup.find("meta", attrs={"name": "citation_pdf_url"})
    candidates = [meta["content"]] if meta and meta.get("content") else []
    for anchor in soup.select("a[href]"):
        href = anchor["href"]
        if "/bitstreams/" in href or "/bitstream/" in href:
            name = clean(anchor.get_text(" ", strip=True)).lower()
            if ".pdf" in name and not any(x in name for x in ("autoriz", "similitud", "parcial")):
                candidates.append(href)
    for href in candidates:
        target = urljoin(url, href)
        parts = urlsplit(target)
        # ESAN publica enlaces a localhost:4000; se reemplaza por el host público.
        if parts.hostname in ("localhost", "127.0.0.1"):
            target = repo.origin + parts.path + (f"?{parts.query}" if parts.query else "")
        return target
    return None


def guess_language(text):
    words = re.findall(r"[a-záéíóúñãõçê]+", text.lower())[:300]
    scores = {code: sum(word in stop for word in words) for code, stop in STOPWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] >= 3 else None


def lang_code(value):
    return language(clean(value).replace("_", "-")) or None


def values(md, key):
    return [item["value"] for item in md.get(key, [])]


def last_part(value):
    return re.split(r"[#/]", value)[-1] if value else None


def classify(types, renati_type, degree, collection):
    if any(THESIS_TYPE.search(t) for t in types):
        return True, "dc.type"
    if renati_type and renati_type.lower() in THESIS_RENATI:
        return True, "renati.type"
    if types:
        # Un tipo declarado que no es tesis prevalece sobre el grado o la colección.
        return False, None
    if degree:
        return True, "thesis.degree.name"
    if collection and THESIS_COLLECTION.search(collection):
        return True, "colección"
    return False, None


def identifier(url, repo):
    match = UUID.search(url)
    if match:
        return f"{repo.key}:{match[0]}"
    handle = re.search(r"/handle/(.+?)/?$", urlsplit(url).path)
    if handle:
        return f"{repo.key}:{handle[1]}"
    raise ParseError(f"No se reconoce un identificador de ficha en {url}")


def parse_item(html, url, repo, requested_url=None):
    soup = soup_of(html)
    uuid = UUID.search(requested_url or url)
    sources = [
        ("estado", from_state(html, uuid[0] if uuid else None)),
        ("tabla", from_table(soup)),
        ("dc", from_meta(soup, DC6)),
        ("mui", from_mui(soup) if repo.parser == "mui" else {}),
        ("citation", from_meta(soup, CITATION, split_keywords=True)),
    ]
    md, used = {}, []
    for name, source in sources:
        if source:
            used.append(name)
        for key, items in source.items():
            md.setdefault(key, items)
    warnings = []
    if "dc.description.abstract" not in md:
        description = soup.find("meta", attrs={"name": "description"})
        if description and description.get("content") and "dc.title" in md:
            add(md, "dc.description.abstract", description["content"])
            warnings.append("abstract_from_meta_description")
    title = next(iter(values(md, "dc.title")), None)
    if not title:
        raise NotItem(f"Sin título ni metadatos de ficha: {url}")

    # Muchos repositorios etiquetan todos los abstracts con el idioma del documento:
    # se prioriza el idioma detectado en el texto.
    abstracts = [
        {"text": item["value"], "language": guess_language(item["value"]) or lang_code(item["language"] or "")}
        for item in md.get("dc.description.abstract", [])
    ]
    record_language = lang_code(next(iter(values(md, "dc.language.iso")), ""))
    preferred = record_language or "es"
    abstract = next((a for a in abstracts if a["language"] == preferred), None) or next(iter(abstracts), None)
    types = values(md, "dc.type")
    renati_type = last_part(next(iter(values(md, "renati.type")), None))
    degree = next(iter(values(md, "thesis.degree.name")), None)
    collection = next(iter(values(md, "collection")), None)
    is_thesis, evidence = classify(types, renati_type, degree, collection)
    issued = next(iter(values(md, "dc.date.issued")), None)
    year = re.search(r"\b(1[89]|20)\d{2}\b", issued or "")
    final_language = record_language or (abstract["language"] if abstract else None)
    if not abstracts:
        warnings.append("abstract_missing")
    if not final_language:
        warnings.append("language_unknown")
    return {
        "paper_id": identifier(requested_url or url, repo),
        "repo": repo.key,
        "university": repo.name,
        "url": requested_url or url,
        "final_url": url,
        "title": title,
        "abstract": abstract["text"] if abstract else None,
        "abstract_language": abstract["language"] if abstract else None,
        "abstracts": abstracts,
        "authors": values(md, "dc.contributor.author"),
        "advisors": values(md, "dc.contributor.advisor"),
        "date_issued": issued,
        "year": int(year[0]) if year else None,
        "language": final_language,
        "document_type": types,
        "renati_type": renati_type,
        "renati_level": last_part(next(iter(values(md, "renati.level")), None)),
        "degree_name": degree,
        "degree_level": next(iter(values(md, "thesis.degree.level")), None),
        "degree_discipline": next(iter(values(md, "thesis.degree.discipline")), None),
        "degree_grantor": next(iter(values(md, "thesis.degree.grantor")), None),
        "ocde": values(md, "dc.subject.ocde"),
        "keywords": [k for k in values(md, "dc.subject") if not k.startswith(("http", "info:eu-repo"))],
        "publisher": next(iter(values(md, "dc.publisher")), None),
        "collection": collection,
        "handle": next((v for v in values(md, "dc.identifier.uri") if v.startswith("http")), None),
        "rights": values(md, "dc.rights"),
        "pdf_url": pdf_link(soup, url, repo),
        "is_thesis": is_thesis,
        "thesis_evidence": evidence,
        "metadata_sources": used,
        "warnings": warnings,
    }
