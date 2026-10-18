"""Extracción tolerante a HTML clásico (scielo.php) y moderno (OPAC)."""

import hashlib
import json
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup


class ParseError(ValueError):
    pass


def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()


def language(value):
    value = clean(value).lower()
    return {
        "spanish": "es",
        "español": "es",
        "spa": "es",
        "esp": "es",
        "english": "en",
        "eng": "en",
        "portuguese": "pt",
        "por": "pt",
    }.get(value, value.split("-")[0])


def soup_of(html):
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
    if any(
        x in title
        for x in ["just a moment", "access denied", "attention required", "captcha", "verify you are human"]
    ):
        raise ParseError("Página de bloqueo o verificación; no es un artículo/resultado")
    return soup


def absolute(base, href):
    if not href or href.startswith(("#", "javascript:", "mailto:", "data:")):
        return None
    target = urljoin(base, href)
    return target if urlsplit(target).scheme in ("http", "https") else None


def article_url(url):
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    if query.get("script", [""])[0] in ("sci_arttext", "sci_abstract") and query.get("pid"):
        query["script"] = ["sci_arttext"]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query, doseq=True), ""))
    if re.search(r"/j/[^/]+/a/[^/]+", parts.path) and query.get("format", [""])[0] not in ("pdf", "xml"):
        return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
    return None


def identity(url):
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    pid = query.get("pid", [None])[0]
    # El PID tiene sentido dentro de una colección; lng/lang son de interfaz.
    key = parts.hostname.lower().removeprefix("www.") + ":" + (pid or parts.path.rstrip("/"))
    return hashlib.sha256(key.encode()).hexdigest()[:24], pid


def extract_abstracts(soup):
    abstracts = []

    def add(text, lang, source):
        text = clean(text)
        if len(text) >= 20 and not any(a["text"] == text for a in abstracts):
            abstracts.append({"language": language(lang) if lang else None, "text": text, "source": source})

    # IAHx guarda incluso abstracts ocultos en el HTML, con id <registro>_es.
    for node in soup.select(
        "div.abstract, section.abstract, div.trans-abstract, [id^='Abstract'], .articleSection[data-anchor='Abstract'], .articleSection[data-anchor='Resumo'], .articleSection[data-anchor='Resumen']"
    ):
        if node.find_parent(class_="abstract"):
            continue
        lang = node.get("xml:lang") or node.get("lang") or node.get("data-lang")
        ident = node.get("id", "")
        match = re.search(r"(?:_|-)(es|en|pt)$", ident, re.I)
        if not lang and match:
            lang = match[1]
        heading = node.select_one("p.sec, .abstract-title") or node.find(
            ["h1", "h2", "h3", "h4", "strong", "b"]
        )
        if not lang and heading:
            lang = heading_language(heading.get_text(" ", strip=True))
        clone = BeautifulSoup(str(node), "html.parser")
        for unwanted in clone.select(".kwd-group, .keywords, h1, h2, h3, h4"):
            unwanted.decompose()
        for paragraph in clone.select("p"):
            text = clean(paragraph.get_text(" ", strip=True))
            if heading_language(text) or re.match(
                r"^(palabras? clave|palavras?[- ]chave|key\s*words)\s*:", text, re.I
            ):
                paragraph.decompose()
        add(clone.get_text(" ", strip=True), lang, "html_section")

    # HTML clásico: <p><b>RESUMEN</b></p><p>...</p><p>Palabras clave...</p>.
    for marker in soup.find_all(["h1", "h2", "h3", "h4", "b", "strong"]):
        label = clean(marker.get_text(" ", strip=True)).strip(":.").lower()
        lang = heading_language(label)
        if not lang or label not in ("abstract", "resumen", "resumo", "summary"):
            continue
        if marker.find_parent(class_="abstract") or marker.find_parent(class_="articleSection"):
            continue
        parent = marker.find_parent(["p", "div", "section"])
        if parent and parent.name == "p":
            start = parent
            text = clean(parent.get_text(" ", strip=True))
            text = text[len(clean(marker.get_text(" ", strip=True))) :].lstrip(": ")
        else:
            start, text = marker, ""
        pieces = [text] if text else []
        for node in start.next_siblings:
            if not getattr(node, "name", None):
                continue
            content = clean(node.get_text(" ", strip=True))
            if re.match(
                r"^(palabras? clave|palavras?[- ]chave|key\s*words|resumen|resumo|abstract|summary|introduc|introduction|referenc)",
                content,
                re.I,
            ):
                break
            if node.name in ("h1", "h2", "h3", "hr", "table"):
                break
            if content:
                pieces.append(content)
        add(" ".join(pieces), lang, "html_heading")

    for meta in soup.find_all("meta"):
        key = (meta.get("name") or meta.get("property") or "").lower()
        if key in (
            "dc.description",
            "dcterms.abstract",
            "citation_abstract",
            "description",
            "og:description",
        ):
            # description/og suelen ser texto social; se usan sólo como último recurso.
            if key in ("description", "og:description") and abstracts:
                continue
            add(meta.get("content"), meta.get("xml:lang") or meta.get("lang"), "meta:" + key)
    return abstracts


def heading_language(label):
    label = clean(label).lower().strip(":.")
    return {"resumen": "es", "abstract": "en", "summary": "en", "resumo": "pt"}.get(label)


def links_of(soup, base):
    links = []
    seen = set()
    for node in soup.find_all(["a", "link"], href=True):
        url = absolute(base, node["href"])
        if url and url not in seen:
            seen.add(url)
            links.append(
                {
                    "url": url,
                    "text": clean(node.get_text(" ", strip=True)),
                    "type": node.get("type"),
                    "language": node.get("hreflang"),
                    "rel": node.get("rel", []),
                }
            )
    return links


def resource_links(soup, base, metadata):
    resources = {"pdf": [], "xml": []}

    def add(kind, url, source, lang=None):
        url = absolute(base, url)
        if url and not any(item["url"] == url for item in resources[kind]):
            parts = urlsplit(url)
            query = parse_qs(parts.query)
            wrapper = query.get("script", [""])[0] == "sci_pdf"
            resources[kind].append(
                {
                    "url": url,
                    "source": source,
                    "kind": "landing_page" if wrapper else "document_link",
                    "verified": False,
                    "language": language(lang) if lang else query.get("tlng", [None])[0],
                }
            )

    for kind in resources:
        for node in soup.find_all("meta"):
            if (node.get("name") or "").lower() == "citation_" + kind + "_url":
                add(kind, node.get("content"), "meta", node.get("language") or node.get("lang"))
        for value in metadata.get("citation_" + kind + "_url", []):
            add(kind, value, "meta")
    for link in links_of(soup, base):
        url = link["url"]
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        text = link["text"].lower()
        if (
            parts.path.lower().endswith(".pdf")
            or query.get("format") == ["pdf"]
            or query.get("script") == ["sci_pdf"]
            or link["type"] == "application/pdf"
        ):
            add("pdf", url, "html_link", link["language"])
        if (
            parts.path.lower().endswith(".xml")
            or "articlexml.php" in parts.path.lower()
            or query.get("format") == ["xml"]
            or "xml" in (link["type"] or "")
            or text in ("xml", "texto em xml", "article in xml format", "artículo en xml")
        ):
            add("xml", url, "html_link", link["language"])
    return resources


def parse_article(html, url, preferred_language="es"):
    soup = soup_of(html)
    metadata = {}
    raw_meta = []
    for node in soup.find_all("meta"):
        raw_meta.append(dict(node.attrs))
        key = (node.get("name") or node.get("property") or node.get("http-equiv") or "").lower()
        if key and node.get("content"):
            metadata.setdefault(key, []).append(node["content"])

    def first(*names):
        return next((metadata[n][0] for n in names if metadata.get(n)), None)

    title = first("citation_title", "dc.title", "og:title")
    if not title:
        node = soup.select_one(".article-title, h1")
        title = clean(node.get_text(" ", strip=True)) if node else None
    if not title and first("citation_journal_title"):
        # Algunas páginas clásicas publican citation_title vacío y el título
        # únicamente en una fuente grande dentro del cuerpo del artículo.
        for node in soup.select("p font[size='4'], p font[size='+2']"):
            candidate = clean(node.get_text(" ", strip=True))
            if len(candidate) >= 25 and candidate != first("citation_journal_title"):
                title = candidate
                break
    if not title or not (
        first("citation_title", "dc.title") or soup.select_one(".article-title") or extract_abstracts(soup)
    ):
        raise ParseError("HTML sin evidencia suficiente de artículo; revise el archivo guardado")
    record_id, pid = identity(url)
    abstracts = extract_abstracts(soup)
    best = next((a for a in abstracts if a["language"] == preferred_language), None)
    # No adjudicar el idioma de interfaz a un abstract no identificado.
    best = best or next((a for a in abstracts if not a["language"]), None) or next(iter(abstracts), None)
    resources = resource_links(soup, url, metadata)
    resources["pdf"].sort(
        key=lambda item: (item["kind"] == "landing_page", item["language"] != preferred_language)
    )
    raw_lang = metadata.get("citation_language", []) or metadata.get("dc.language", [])
    langs = sorted(set(language(v) for value in raw_lang for v in re.split(r"[,;]", value)))
    date = first("citation_publication_date", "citation_date", "dc.date", "dc.date.issued")
    year_match = re.search(r"\b(18|19|20)\d{2}\b", date or "")
    jsonld = []
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            jsonld.append(json.loads(node.string or node.get_text()))
        except (ValueError, TypeError):
            jsonld.append({"unparsed": node.get_text()})
    warnings = []
    if not abstracts:
        warnings.append("abstract_missing")
    if preferred_language and not any(a["language"] == preferred_language for a in abstracts):
        warnings.append("preferred_abstract_language_not_confirmed")
    if not langs:
        warnings.append("publication_language_unknown")
    if not resources["pdf"]:
        warnings.append("pdf_link_missing")
    if not resources["xml"]:
        warnings.append("xml_link_missing")
    return {
        "id": record_id,
        "pid": pid,
        "url": url,
        "title": clean(title),
        "doi": first("citation_doi", "dc.identifier.doi"),
        "authors": metadata.get("citation_author", []) or metadata.get("dc.creator", []),
        "journal": first("citation_journal_title", "dc.source"),
        "publication_date": date,
        "year": int(year_match[0]) if year_match else None,
        "languages": langs,
        "abstract": best["text"] if best else None,
        "abstract_language": best["language"] if best else None,
        "abstracts": abstracts,
        "pdf_url": next((r["url"] for r in resources["pdf"]), None),
        "xml_url": next((r["url"] for r in resources["xml"]), None),
        "resources": resources,
        "meta": metadata,
        "meta_tags": raw_meta,
        "json_ld": jsonld,
        "links": links_of(soup, url),
        "page_text": soup.get_text(" ", strip=True),
        "warnings": warnings,
    }


def parse_search(html, url):
    soup = soup_of(html)
    results = []
    seen = set()
    nodes = soup.select("div.item, div.record, article.result")
    for node in nodes:
        candidates = []
        for anchor in node.select("a[href]"):
            target = absolute(url, anchor["href"])
            target = article_url(target) if target else None
            if target:
                candidates.append(target)
        candidates.sort(key=lambda u: parse_qs(urlsplit(u).query).get("tlng") != ["es"])
        for target in candidates:
            record_id, pid = identity(target)
            if record_id in seen:
                continue
            seen.add(record_id)
            title = node.select_one(".title, h2, h3")
            results.append(
                {
                    "id": record_id,
                    "pid": pid,
                    "url": target,
                    "title": clean(title.get_text(" ", strip=True)) if title else None,
                    "abstracts": extract_abstracts(node),
                    "resources": resource_links(node, url, {}),
                    "source_url": url,
                }
            )
    # Un cambio de plantilla o bloqueo no equivale a cero resultados.
    zero = any(
        clean(n.get_text(" ", strip=True)) in ("0", "0 resultados", "0 results")
        for n in soup.select(".resultsCount, .totalResults, #totalResults")
    )
    zero = zero or bool(re.search(r"(?:Results|Resultados)\s*:\s*0\b", soup.get_text(" ", strip=True), re.I))
    if not results and not zero:
        raise ParseError("No se reconocieron resultados ni un total cero explícito; revise el HTML")
    next_node = soup.select_one("a.pageNext, a[rel='next'], a.next")
    return {
        "records": results,
        "has_next": next_node is not None,
        "fingerprint": hashlib.sha256("\n".join(sorted(seen)).encode()).hexdigest(),
    }


def parse_facets(html):
    soup = soup_of(html)
    facets = {}
    for node in soup.select("input[name^='filter['], select[name^='filter[']"):
        match = re.match(r"filter\[([^]]+)\]", node["name"])
        if not match:
            continue
        options = node.select("option") if node.name == "select" else [node]
        for option in options:
            value = option.get("value", "")
            if value and value != "*":
                group = facets.setdefault(match[1], [])
                if value not in group:
                    group.append(value)
    if not facets:
        raise ParseError("No se reconocieron controles de facetas en este HTML")
    return facets
