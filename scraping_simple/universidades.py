import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from html import unescape
from time import sleep, time
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup as bs
from curl_cffi import requests as rq2

from comun import (agregar_jsonl, ahora, cargar_json, es_bloqueo, escribir_corpus, espera_servidor,
                   guardar_json, idioma, interpretar_robots, leer_jsonl, limpiar, origen_de, permitido)

AJUSTES = {'data_dir': 'data/universidades_simple', 'languages': ['es'], 'thesis_only': True,
           'sample_seed': 'aw-2026', 'delay_seconds': 2, 'timeout_seconds': 40,
           'render_timeout_seconds': 30, 'settle_seconds': 5, 'retries': 2, 'impersonate': 'chrome',
           'robots_agent': 'UniversidadesMVP', 'max_consecutive_errors': 10, 'headless': True}
REPO = {'discovery': 'sitemap', 'sitemap': '/sitemap_index.xml', 'sitemap_rewrites': [], 'item_rewrites': [],
        'item_pattern': r'/(items|entities/publication)/[0-9a-f-]{36}/?$', 'item_suffix': '', 'render': False,
        'wait_for': "meta[name='citation_title']",
        'wait_also': "meta[name='citation_pdf_url'], a[href*='/bitstreams/']", 'parser': 'dspace', 'enabled': True}

# Solo estos campos se conservan: se descartan DNI, ORCID y jurados
KEEP = {'dc.title', 'dc.contributor.author', 'dc.contributor.advisor', 'dc.date.issued', 'dc.description.abstract',
        'dc.language.iso', 'dc.type', 'dc.subject', 'dc.subject.ocde', 'dc.publisher', 'dc.identifier.uri',
        'dc.rights', 'renati.type', 'renati.level', 'thesis.degree.name', 'thesis.degree.level',
        'thesis.degree.discipline', 'thesis.degree.grantor', 'collection'}
DC6 = {'dc.title': 'dc.title', 'dc.creator': 'dc.contributor.author', 'dcterms.abstract': 'dc.description.abstract',
       'dcterms.issued': 'dc.date.issued', 'dc.type': 'dc.type', 'dc.language': 'dc.language.iso',
       'dc.subject': 'dc.subject', 'dc.publisher': 'dc.publisher', 'dc.identifier': 'dc.identifier.uri',
       'dc.rights': 'dc.rights'}
CITATION = {'citation_title': 'dc.title', 'citation_author': 'dc.contributor.author',
            'citation_publication_date': 'dc.date.issued', 'citation_date': 'dc.date.issued',
            'citation_language': 'dc.language.iso', 'citation_keywords': 'dc.subject',
            'citation_publisher': 'dc.publisher', 'citation_abstract_html_url': 'dc.identifier.uri',
            'citation_type': 'dc.type'}
MUI = {'fecha de publicación': 'dc.date.issued', 'autor(es)': 'dc.contributor.author',
       'asesor(es)': 'dc.contributor.advisor', 'editorial': 'dc.publisher', 'resumen': 'dc.description.abstract',
       'palabras clave': 'dc.subject', 'identificador único': 'dc.identifier.uri', 'tipo de documento': 'dc.type',
       'tipo de acceso': 'dc.rights', 'pertenece a la colección': 'collection'}
STOPWORDS = {
    'es': {'el', 'la', 'los', 'las', 'de', 'del', 'que', 'y', 'en', 'se', 'por', 'con', 'una', 'para', 'su', 'al', 'como'},
    'en': {'the', 'of', 'and', 'to', 'in', 'is', 'that', 'for', 'with', 'this', 'was', 'were', 'are', 'on', 'by'},
    'pt': {'o', 'os', 'da', 'do', 'dos', 'das', 'não', 'uma', 'em', 'ao', 'são', 'também', 'foi', 'pela', 'pelo'}}
ESTADO = re.compile(r'<script id="dspace-angular-state" type="application/json">(.*?)</script>', re.S)
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
CAMPO = re.compile(r'[a-z]+(\.[a-zA-Z]+){1,3}')
LOC = re.compile(r'<loc>\s*([^<\s]+)\s*</loc>')
TIPO_TESIS = re.compile(r'thesis|tesis|/c_7a1f|/c_bdcc|/c_db06|/c_46ec', re.I)
RENATI_TESIS = {'tesis', 'trabajodesuficienciaprofesional', 'trabajoacademico', 'trabajodeinvestigacion'}
COLECCION_TESIS = re.compile(r'tesis|suficiencia profesional|trabajos? (de investigaci|acad[eé]mico)|segunda especialidad', re.I)
CAMPOS = ['paper_id', 'repo', 'university', 'title', 'abstract', 'abstract_language', 'authors', 'advisors', 'year',
          'date_issued', 'language', 'document_type', 'renati_type', 'renati_level', 'degree_name', 'degree_level',
          'degree_discipline', 'degree_grantor', 'ocde', 'keywords', 'publisher', 'collection', 'handle', 'url',
          'pdf_url', 'fetched_at']

cfg = {}
robots = {}
ultima = {}
navegador = {'driver': None}


# ---------- configuración ----------
def cargar_config(ruta, carpeta=None):
    with open(ruta, encoding='utf-8-sig') as f:
        datos = json.load(f)
    config = {**AJUSTES, **{k: v for k, v in datos.items() if k != 'repos'}}
    if carpeta:
        config['data_dir'] = carpeta
    if config['delay_seconds'] < 1:
        raise ValueError('delay_seconds debe ser al menos 1 para no saturar los repositorios')
    config['repos'] = [{**REPO, **r} for r in datos['repos']]
    for r in config['repos']:
        r['base_url'] = r['base_url'].rstrip('/')
    return config


def elegir(claves):
    if not claves:
        return [r for r in cfg['repos'] if r['enabled']]
    conocidos = {r['key']: r for r in cfg['repos']}
    faltan = [k for k in claves if k not in conocidos]
    if faltan:
        raise ValueError('Repositorios desconocidos: ' + ', '.join(faltan))
    return [conocidos[k] for k in claves]


# ---------- descarga (curl_cffi y Selenium) ----------
def esperar(origen):
    retraso = max(cfg['delay_seconds'], robots[origen][1]) if origen in robots else cfg['delay_seconds']
    falta = retraso - (time() - ultima.get(origen, 0))
    if falta > 0:
        sleep(falta)
    ultima[origen] = time()


def pedir(url):
    origen = origen_de(url)
    for intento in range(cfg['retries'] + 1):
        esperar(origen)
        try:
            r = rq2.get(url, impersonate=cfg['impersonate'] or None, timeout=cfg['timeout_seconds'])
        except Exception as e:  # curl_cffi agrupa aquí los errores de red
            if intento == cfg['retries']:
                raise ConnectionError(f'Error de red en {url}: {e}')
            sleep(2 ** (intento + 1))
            continue
        if r.status_code in (429, 500, 502, 503, 504) and intento < cfg['retries']:
            sleep(espera_servidor(r.headers.get('Retry-After'), 2 ** (intento + 1)))
            continue
        return r


def reglas_de(url):
    origen = origen_de(url)
    if origen not in robots:
        r = pedir(origen + '/robots.txt')
        robots[origen] = interpretar_robots(r.status_code, r.text, cfg['robots_agent'])
        if robots[origen][1]:
            print(f'robots.txt de {origen} pide Crawl-delay {robots[origen][1]}s')
    return robots[origen][0]


def descargar(url):
    # None = robots.txt no permite la URL; PermissionError = acceso restringido o desafío antibots
    if not permitido(reglas_de(url), url):
        return None
    r = pedir(url)
    if r.status_code in (401, 403, 429):
        raise PermissionError(f'Acceso restringido, HTTP {r.status_code}: {url}')
    if es_bloqueo(r.content):
        raise PermissionError(f'Desafío antibots en {url}; no se elude')
    if r.status_code != 200:
        raise ConnectionError(f'HTTP {r.status_code}: {url}')
    return r


def bajar(url):
    r = descargar(url)
    if r is None:
        raise ConnectionError(f'robots.txt no permite esta URL: {url}')
    return r


def codigo_error(html):
    cabeza = html[:3000]
    m = re.search(r'<title>\s*(4\d\d|5\d\d)\b[^<]*</title>', cabeza, re.I)
    if m:
        return int(m[1])
    return 429 if re.search(r'too many requests', cabeza, re.I) else None


def localizador(selector):
    from selenium.webdriver.common.by import By
    return (By.XPATH, selector[6:]) if selector.startswith('xpath:') else (By.CSS_SELECTOR, selector)


def abrir_ficha(url, espera, espera2):
    from selenium import webdriver
    from selenium.common.exceptions import TimeoutException, WebDriverException
    from selenium.webdriver.support.ui import WebDriverWait

    if not permitido(reglas_de(url), url):
        return None
    esperar(origen_de(url))
    if navegador['driver'] is None:
        opciones = webdriver.ChromeOptions()
        if cfg['headless']:
            opciones.add_argument('--headless=new')
        for argumento in ('--window-size=1280,900', '--lang=es-PE', '--no-sandbox',
                          '--disable-dev-shm-usage', '--disable-gpu'):
            opciones.add_argument(argumento)
        try:
            navegador['driver'] = webdriver.Chrome(options=opciones)
        except WebDriverException as e:
            raise RuntimeError(f'No se pudo iniciar Chrome con Selenium: {e}')
        navegador['driver'].set_page_load_timeout(cfg['timeout_seconds'])
    driver = navegador['driver']
    try:
        driver.get(url)
        codigo = codigo_error(driver.page_source)
        if codigo in (401, 403, 429):
            raise PermissionError(f'Acceso restringido, HTTP {codigo}: {url}')
        if codigo:
            raise ConnectionError(f'HTTP {codigo}: {url}')
        WebDriverWait(driver, cfg['render_timeout_seconds']).until(
            lambda d: d.find_elements(*localizador(espera)) or es_bloqueo(d.page_source))
        if espera2:
            try:
                WebDriverWait(driver, cfg['settle_seconds']).until(
                    lambda d: d.find_elements(*localizador(espera2)))
            except TimeoutException:
                pass
    except TimeoutException:
        raise ConnectionError(f"La ficha no terminó de cargar en {cfg['render_timeout_seconds']}s: {url}")
    except WebDriverException as e:
        raise ConnectionError(f'Error de Selenium en {url}: {str(e).splitlines()[0]}')
    html = driver.page_source
    if es_bloqueo(html):
        raise PermissionError(f'Desafío antibots en {url}; no se elude')
    return html, driver.current_url


def bajar_ficha(repo, url):
    destino = url if not repo['item_suffix'] or url.endswith(repo['item_suffix']) else url + repo['item_suffix']
    if repo['render']:
        return abrir_ficha(destino, repo['wait_for'], repo['wait_also'])
    r = descargar(destino)
    return (r.content.decode('utf-8', 'replace'), r.url) if r else None


# ---------- descubrimiento ----------
def normalizar(url, repo, reglas=()):
    for patron, reemplazo in reglas:
        url = re.sub(patron, reemplazo, url)
    partes, base = urlsplit(url), urlsplit(repo['base_url'])
    # algunos sitemaps declaran http:// para el mismo host
    if partes.hostname == base.hostname and partes.scheme != base.scheme:
        url = urlunsplit((base.scheme, partes.netloc, partes.path, partes.query, ''))
    return url


def sitemap_items(repo):
    patron = re.compile(repo['item_pattern'])
    pendientes, vistos, items = [repo['base_url'] + repo['sitemap']], set(), []
    while pendientes:
        url = normalizar(pendientes.pop(0), repo, repo['sitemap_rewrites'])
        if url in vistos:
            continue
        vistos.add(url)
        print(f"{repo['key']}: sitemap {url}")
        texto = bajar(url).content.decode('utf-8', 'replace')
        locs = [unescape(loc) for loc in LOC.findall(texto)]
        if '<sitemapindex' in texto:
            pendientes.extend(locs)
            continue
        if '<urlset' not in texto:
            raise ValueError(f'{url} no es un sitemap XML; revise sitemap o sitemap_rewrites')
        encontrados = [normalizar(loc, repo, repo['item_rewrites']) for loc in locs]
        items.extend(loc for loc in encontrados if patron.search(loc))
    return list(dict.fromkeys(items))


def htmlmap_items(repo):
    patron = re.compile(repo['item_pattern'])
    pagina = bajar(repo['base_url'] + repo['sitemap'])
    soup = bs(pagina.content, 'html.parser')
    mapas = [urljoin(pagina.url, a['href']) for a in soup.select('a[href]') if 'htmlmap?map=' in a['href']]
    items = []
    for url in list(dict.fromkeys(mapas)) or [pagina.url]:
        url = normalizar(url, repo, repo['sitemap_rewrites'])
        print(f"{repo['key']}: htmlmap {url}")
        lista = bajar(url) if url != pagina.url else pagina
        for a in bs(lista.content, 'html.parser').select('a[href]'):
            destino = normalizar(urljoin(lista.url, a['href']), repo, repo['item_rewrites'])
            if patron.search(destino):
                items.append(destino)
    return list(dict.fromkeys(items))


def browse_items(repo):
    # índice paginado de DSpace 6 (/browse?...&offset=N): sigue el enlace al offset siguiente
    patron = re.compile(repo['item_pattern'])
    url, vistas, items = repo['base_url'] + repo['sitemap'], set(), []
    while url and url not in vistas:
        vistas.add(url)
        pagina = bajar(url)
        offset = int(parse_qs(urlsplit(pagina.url).query).get('offset', ['0'])[0])
        siguiente = None
        for a in bs(pagina.content, 'html.parser').select('a[href]'):
            destino = normalizar(urljoin(pagina.url, a['href']), repo, repo['item_rewrites'])
            if patron.search(destino):
                items.append(destino)
                continue
            valor = parse_qs(urlsplit(destino).query).get('offset', [''])[0]
            if valor.isdigit() and int(valor) > offset and (siguiente is None or int(valor) < siguiente[0]):
                siguiente = (int(valor), destino)
        url = siguiente[1] if siguiente else None
    return list(dict.fromkeys(items))


def descubrir(repo):
    if repo['discovery'] == 'htmlmap':
        return htmlmap_items(repo)
    if repo['discovery'] == 'browse':
        return browse_items(repo)
    return sitemap_items(repo)


def identificador(url, repo):
    m = UUID.search(url)
    if m:
        return f"{repo['key']}:{m[0]}"
    handle = re.search(r'/handle/(.+?)/?$', urlsplit(url).path)
    return f"{repo['key']}:{handle[1]}" if handle else None


# ---------- ficha de tesis ----------
def agregar(md, clave, valor, lang=None):
    valor = limpiar(valor)
    if clave in KEEP and valor:
        md.setdefault(clave, []).append({'value': valor, 'language': limpiar(lang) or None})


def desde_estado(html, uuid):
    # estado JSON de DSpace Angular (DSpace 7+ con SSR): trae los metadatos completos
    m = ESTADO.search(html)
    if not m or not uuid:
        return {}
    crudo = m.group(1)
    if crudo.lstrip().startswith('{&q;'):
        for codigo, car in (('&q;', '"'), ('&s;', "'"), ('&l;', '<'), ('&g;', '>'), ('&a;', '&')):
            crudo = crudo.replace(codigo, car)
    try:
        estado = json.loads(crudo)
    except ValueError:
        return {}
    pila = [estado]
    while pila:
        nodo = pila.pop()
        if isinstance(nodo, dict):
            if nodo.get('uuid') == uuid and isinstance(nodo.get('metadata'), dict):
                md = {}
                for clave, valores in nodo['metadata'].items():
                    for v in valores if isinstance(valores, list) else []:
                        if isinstance(v, dict):
                            agregar(md, clave, str(v.get('value') or ''), v.get('language'))
                return md
            pila.extend(nodo.values())
        elif isinstance(nodo, list):
            pila.extend(nodo)
    return {}


def desde_tabla(soup):
    md = {}
    for fila in soup.select('table tr'):
        celdas = [limpiar(c.get_text(' ', strip=True)) for c in fila.find_all(['td', 'th'])]
        if len(celdas) >= 2 and CAMPO.fullmatch(celdas[0]):
            agregar(md, celdas[0], celdas[1], celdas[2] if len(celdas) > 2 else None)
    return md


def desde_meta(soup, mapa, dividir=False):
    md = {}
    for nodo in soup.find_all('meta'):
        clave = mapa.get((nodo.get('name') or '').lower())
        contenido = nodo.get('content')
        if not clave or not contenido:
            continue
        for valor in contenido.split(';') if dividir and clave == 'dc.subject' else [contenido]:
            agregar(md, clave, valor, nodo.get('xml:lang') or nodo.get('lang'))
    return md


def desde_mui(soup):
    md = {}
    for rotulo in soup.find_all('h6'):
        clave = MUI.get(limpiar(rotulo.get_text(' ', strip=True)).lower())
        caja = rotulo.find_next_sibling()
        if not clave or caja is None:
            continue
        hijos = caja.find_all(recursive=False)
        if clave != 'dc.description.abstract' and len(hijos) > 1:
            for hijo in hijos:
                agregar(md, clave, hijo.get_text(' ', strip=True))
        else:
            agregar(md, clave, caja.get_text(' ', strip=True))
    for nodo in soup.find_all(class_=re.compile(r'MuiTypography-h[45]')):
        texto = limpiar(nodo.get_text(' ', strip=True))
        if len(texto) > 10:
            agregar(md, 'dc.title', texto)
            break
    if 'collection' not in md:
        # muchas fichas no muestran "Pertenece a la colección"; la ruta de navegación sí
        migas = [a for a in soup.select("a[href*='/collection/']") if limpiar(a.get_text(' ', strip=True))]
        if migas:
            agregar(md, 'collection', migas[-1].get_text(' ', strip=True))
    return md


def enlace_pdf(soup, url, repo):
    meta = soup.find('meta', attrs={'name': 'citation_pdf_url'})
    candidatos = [meta['content']] if meta and meta.get('content') else []
    for a in soup.select('a[href]'):
        if '/bitstreams/' in a['href'] or '/bitstream/' in a['href']:
            nombre = limpiar(a.get_text(' ', strip=True)).lower()
            if '.pdf' in nombre and not any(x in nombre for x in ('autoriz', 'similitud', 'parcial')):
                candidatos.append(a['href'])
    for href in candidatos:
        destino = urljoin(url, href)
        partes = urlsplit(destino)
        # ESAN publica enlaces a localhost:4000; se reemplaza por el host público
        if partes.hostname in ('localhost', '127.0.0.1'):
            destino = origen_de(repo['base_url']) + partes.path + (f'?{partes.query}' if partes.query else '')
        return destino
    return None


def adivinar_idioma(texto):
    palabras = re.findall(r'[a-záéíóúñãõçê]+', texto.lower())[:300]
    puntos = {c: sum(p in stop for p in palabras) for c, stop in STOPWORDS.items()}
    mejor = max(puntos, key=puntos.get)
    return mejor if puntos[mejor] >= 3 else None


def codigo_idioma(valor):
    return idioma(limpiar(valor).replace('_', '-')) or None


def valores(md, clave):
    return [i['value'] for i in md.get(clave, [])]


def ultimo_tramo(valor):
    return re.split(r'[#/]', valor)[-1] if valor else None


def clasificar(tipos, renati, grado, coleccion):
    if any(TIPO_TESIS.search(t) for t in tipos):
        return True, 'dc.type'
    if renati and renati.lower() in RENATI_TESIS:
        return True, 'renati.type'
    if tipos:
        return False, None  # un tipo declarado que no es tesis prevalece sobre el grado o la colección
    if grado:
        return True, 'thesis.degree.name'
    if coleccion and COLECCION_TESIS.search(coleccion):
        return True, 'colección'
    return False, None


def leer_ficha(html, url, repo, pedida=None):
    soup = bs(html, 'html.parser')
    uuid = UUID.search(pedida or url)
    # prioridad: estado JSON > tabla /full > DC de DSpace 6 > rótulos MUI > citation_*
    fuentes = [('estado', desde_estado(html, uuid[0] if uuid else None)),
               ('tabla', desde_tabla(soup)),
               ('dc', desde_meta(soup, DC6)),
               ('mui', desde_mui(soup) if repo['parser'] == 'mui' else {}),
               ('citation', desde_meta(soup, CITATION, True))]
    md, usadas = {}, []
    for nombre, fuente in fuentes:
        if fuente:
            usadas.append(nombre)
        for clave, items in fuente.items():
            md.setdefault(clave, items)
    avisos = []
    if 'dc.description.abstract' not in md:
        descripcion = soup.find('meta', attrs={'name': 'description'})
        if descripcion and descripcion.get('content') and 'dc.title' in md:
            agregar(md, 'dc.description.abstract', descripcion['content'])
            avisos.append('abstract_from_meta_description')
    titulo = next(iter(valores(md, 'dc.title')), None)
    if not titulo:
        return None  # no es una ficha (comunidad, colección o portada)

    # muchos repositorios etiquetan todos los abstracts con el idioma del documento: se prioriza el detectado
    resumenes = [{'text': i['value'], 'language': adivinar_idioma(i['value']) or codigo_idioma(i['language'] or '')}
                 for i in md.get('dc.description.abstract', [])]
    idioma_doc = codigo_idioma(next(iter(valores(md, 'dc.language.iso')), ''))
    preferido = idioma_doc or 'es'
    resumen = next((a for a in resumenes if a['language'] == preferido), None) or next(iter(resumenes), None)
    tipos = valores(md, 'dc.type')
    renati = ultimo_tramo(next(iter(valores(md, 'renati.type')), None))
    grado = next(iter(valores(md, 'thesis.degree.name')), None)
    coleccion = next(iter(valores(md, 'collection')), None)
    es_tesis, evidencia = clasificar(tipos, renati, grado, coleccion)
    fecha = next(iter(valores(md, 'dc.date.issued')), None)
    anio = re.search(r'\b(1[89]|20)\d{2}\b', fecha or '')
    idioma_final = idioma_doc or (resumen['language'] if resumen else None)
    if not resumenes:
        avisos.append('abstract_missing')
    if not idioma_final:
        avisos.append('language_unknown')
    return {
        'paper_id': identificador(pedida or url, repo),
        'repo': repo['key'],
        'university': repo['name'],
        'url': pedida or url,
        'final_url': url,
        'title': titulo,
        'abstract': resumen['text'] if resumen else None,
        'abstract_language': resumen['language'] if resumen else None,
        'abstracts': resumenes,
        'authors': valores(md, 'dc.contributor.author'),
        'advisors': valores(md, 'dc.contributor.advisor'),
        'date_issued': fecha,
        'year': int(anio[0]) if anio else None,
        'language': idioma_final,
        'document_type': tipos,
        'renati_type': renati,
        'renati_level': ultimo_tramo(next(iter(valores(md, 'renati.level')), None)),
        'degree_name': grado,
        'degree_level': next(iter(valores(md, 'thesis.degree.level')), None),
        'degree_discipline': next(iter(valores(md, 'thesis.degree.discipline')), None),
        'degree_grantor': next(iter(valores(md, 'thesis.degree.grantor')), None),
        'ocde': valores(md, 'dc.subject.ocde'),
        'keywords': [k for k in valores(md, 'dc.subject') if not k.startswith(('http', 'info:eu-repo'))],
        'publisher': next(iter(valores(md, 'dc.publisher')), None),
        'collection': coleccion,
        'handle': next((v for v in valores(md, 'dc.identifier.uri') if v.startswith('http')), None),
        'rights': valores(md, 'dc.rights'),
        'pdf_url': enlace_pdf(soup, url, repo),
        'is_thesis': es_tesis,
        'thesis_evidence': evidencia,
        'metadata_sources': usadas,
        'warnings': avisos,
    }


def motivos(reg):
    lista = []
    if not (reg['abstract'] or '').strip():
        lista.append('abstract_missing')
    if cfg['thesis_only'] and not reg['is_thesis']:
        lista.append('not_thesis')
    if cfg['languages'] and reg['language'] and reg['language'] not in cfg['languages']:
        lista.append('language_mismatch')
    return lista


def procesar(repo, pid, url):
    ficha = bajar_ficha(repo, url)
    if ficha is None:
        return {'paper_id': pid, 'url': url, 'status': 'no_permitido'}
    html, final = ficha
    reg = leer_ficha(html, final, repo, url) if pid.split(':', 1)[1] in final else None
    if reg is None:
        return {'paper_id': pid, 'url': url, 'status': 'omitido', 'error': f'No es una ficha o redirigió a {final}'}
    reg['fetched_at'] = ahora()
    reg['motivos'] = motivos(reg)
    reg['status'] = 'filtrado' if reg['motivos'] else 'ok'
    return reg


# ---------- ejecución ----------
def procesar_repo(repo, objetivo, redescubrir=False):
    carpeta = f"{cfg['data_dir']}/{repo['key']}"
    ruta_urls, ruta_fichas = carpeta + '/urls.json', carpeta + '/fichas.jsonl'
    ajustes = {k: repo[k] for k in ('base_url', 'discovery', 'sitemap', 'sitemap_rewrites', 'item_rewrites',
                                    'item_pattern')}
    ajustes['sample_seed'] = cfg['sample_seed']
    final, error = 'incomplete', None
    try:
        guardado = cargar_json(ruta_urls)
        if guardado and guardado['ajustes'] != ajustes:
            raise ValueError('La configuración de descubrimiento cambió: use otro data_dir para no mezclar muestras')
        if not guardado or redescubrir:
            ids = {}
            for url in descubrir(repo):
                pid = identificador(url, repo)
                if pid:
                    ids.setdefault(pid, url)
            # orden pseudoaleatorio con semilla: la muestra no queda sesgada hacia el orden del sitemap
            orden = sorted(ids.items(), key=lambda p: hashlib.sha256(f"{cfg['sample_seed']}:{p[0]}".encode()).hexdigest())
            guardar_json(ruta_urls, {'ajustes': ajustes, 'urls': orden})
            print(f"{repo['key']}: {len(orden)} fichas descubiertas")
        urls = cargar_json(ruta_urls)['urls']
        hechos = leer_jsonl(ruta_fichas)
        ids_hechos = {r['paper_id'] for r in hechos}
        validos = sum(r['status'] == 'ok' for r in hechos)
        seguidos = 0
        for pid, url in urls:
            if validos >= objetivo:
                break
            if pid in ids_hechos:
                continue
            try:
                reg = procesar(repo, pid, url)
                seguidos = 0
            except (ConnectionError, ValueError) as e:
                reg = {'paper_id': pid, 'url': url, 'status': 'error', 'error': str(e)}
                seguidos += 1
                print(f"{repo['key']}: {e}")
            agregar_jsonl(ruta_fichas, reg)
            ids_hechos.add(pid)
            validos += reg['status'] == 'ok'
            print(f"{repo['key']}: {validos}/{objetivo} tesis válidas ({reg['status']}) {url}")
            if seguidos >= cfg['max_consecutive_errors']:
                raise ConnectionError(f'{seguidos} errores seguidos; el repositorio podría estar caído')
        if validos < objetivo:
            final = 'exhausted'
    except PermissionError as e:
        final, error = 'blocked', str(e)
    except (RuntimeError, OSError, ValueError) as e:
        final, error = 'error', str(e)
    except KeyboardInterrupt:
        final, error = 'interrupted', 'Interrupción solicitada; se conserva el estado para reanudar'
    if error:
        print(f"{repo['key']}: {error}")

    registros = leer_jsonl(ruta_fichas)
    filas = [fila_corpus(r) for r in registros if r['status'] == 'ok']
    escribir_corpus(carpeta + '/corpus', filas, CAMPOS)
    if final in ('incomplete', 'exhausted') and len(filas) >= objetivo:
        final = 'passed'
    resumen = {
        'repo': repo['key'], 'university': repo['name'], 'status': final, 'error': error, 'target': objetivo,
        'finished_at': ahora(),
        'counts': {'valid_theses': len(filas), **Counter(r['status'] for r in registros)},
        'exclusion_reasons': dict(Counter(m for r in registros for m in r.get('motivos', []))),
    }
    guardar_json(carpeta + '/report.json', resumen)
    print(f"{repo['key']}: {final}; tesis válidas {len(filas)}/{objetivo}")
    return resumen, filas


def fila_corpus(reg):
    return {c: reg.get(c) for c in CAMPOS}


def ejecutar(config, claves=(), objetivo=100, redescubrir=False):
    cfg.clear()
    cfg.update(config)
    repos = elegir(claves)
    resumenes, todas = [], []
    try:
        for repo in repos:
            print(f"===== {repo['name']} ({repo['key']})")
            resumen, filas = procesar_repo(repo, objetivo, redescubrir)
            resumenes.append(resumen)
            todas.extend(filas)
            if resumen['status'] == 'interrupted':
                break
    finally:
        if navegador['driver'] is not None:
            navegador['driver'].quit()
            navegador['driver'] = None
    escribir_corpus(cfg['data_dir'] + '/corpus', todas, CAMPOS)
    paso = len(resumenes) == len(repos) and all(r['status'] == 'passed' for r in resumenes)
    guardar_json(cfg['data_dir'] + '/summary.json', {
        'status': 'passed' if paso else 'incomplete', 'target_per_repo': objetivo, 'finished_at': ahora(),
        'valid_theses': len(todas), 'repos': [{k: r[k] for k in ('repo', 'status', 'error', 'counts')} for r in resumenes],
    })
    print(f"Resultado: {'passed' if paso else 'incomplete'}; {len(todas)} tesis válidas en total")
    return 0 if paso else 2


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='Scraping de tesis en repositorios universitarios peruanos')
    ap.add_argument('--config', default='config.universidades.json')
    ap.add_argument('--carpeta', default='data/universidades_simple')
    ap.add_argument('--repos', default='', help='Claves separadas por comas; por defecto, todos los habilitados')
    ap.add_argument('--target', type=int, default=100, help='Tesis válidas por repositorio')
    ap.add_argument('--rediscover', action='store_true', help='Volver a leer los sitemaps y añadir fichas nuevas')
    ap.add_argument('--list', action='store_true', help='Listar los repositorios configurados y salir')
    args = ap.parse_args()
    if args.target < 1:
        ap.error('--target debe ser positivo')
    try:
        config = cargar_config(args.config, args.carpeta)
        if args.list:
            for r in config['repos']:
                print(f"{r['key']:10} {'sí' if r['enabled'] else 'no':3} {'Selenium' if r['render'] else 'curl_cffi':9} {r['base_url']}")
            sys.exit(0)
        claves = [k.strip() for k in args.repos.split(',') if k.strip()]
        sys.exit(ejecutar(config, claves, args.target, args.rediscover))
    except (ValueError, OSError) as e:
        sys.exit(f'Error: {e}')
