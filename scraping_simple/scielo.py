import hashlib
import re
import sys
from time import sleep, time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

import requests as rq
from bs4 import BeautifulSoup as bs

from comun import (agregar_jsonl, ahora, cargar_json, es_bloqueo, escribir_corpus, espera_servidor,
                   guardar_json, idioma, interpretar_robots, leer_jsonl, limpiar, origen_de, permitido)

BASE = 'https://www.scielo.org.pe/scielo.php'
CATALOGO = BASE + '?script=sci_alphabetic&lng=es&nrm=iso'
REVISTAS = ['1810-9993', '2523-6326', '2309-0413', '1810-634X', '1562-4730', '1726-2216']
IDIOMAS = ['es']
ANIOS = []
CARPETA = 'data/scielo_simple'
UA = 'SciELO-MVP/0.1 (small research crawler)'
PAUSA = 1
REINTENTOS = 2
TIMEOUT = 40
ROBOTS = 'report'  # 'respect' detiene la descarga si robots.txt la prohíbe; 'report' solo lo avisa

PRIORIDAD = {'sci_serial': 1, 'sci_issues': 2, 'sci_issuetoc': 3}
SCRIPTS = set(PRIORIDAD) | {'sci_alphabetic', 'sci_arttext', 'sci_abstract'}
ROTULOS = {'resumen': 'es', 'abstract': 'en', 'summary': 'en', 'resumo': 'pt'}
SECCIONES = ("div.abstract, section.abstract, div.trans-abstract, [id^='Abstract'], "
             ".articleSection[data-anchor='Abstract'], .articleSection[data-anchor='Resumo'], "
             ".articleSection[data-anchor='Resumen']")
CLAVES = r'^(palabras? clave|palavras?[- ]chave|key\s*words)\s*:'
FIN = r'^(palabras? clave|palavras?[- ]chave|key\s*words|resumen|resumo|abstract|summary|introduc|introduction|referenc)'
META_RESUMEN = ('dc.description', 'dcterms.abstract', 'citation_abstract', 'description', 'og:description')
CAMPOS = ['paper_id', 'pid', 'title', 'abstract', 'language', 'publication_languages', 'authors', 'doi',
          'journal', 'year', 'url', 'pdf_url', 'xml_url', 'source', 'collection', 'discovered_from', 'fetched_at']

robots = {}
ultima = {}
avisados = set()


# ---------- descarga ----------
def pedir(url):
    origen = origen_de(url)
    for intento in range(REINTENTOS + 1):
        retraso = max(PAUSA, robots[origen][1]) if origen in robots else PAUSA
        falta = retraso - (time() - ultima.get(origen, 0))
        if falta > 0:
            sleep(falta)
        ultima[origen] = time()
        try:
            r = rq.get(url, headers={'user-agent': UA}, timeout=TIMEOUT)
        except rq.RequestException as e:
            if intento == REINTENTOS:
                raise ConnectionError(f'Error de red en {url}: {e}')
            sleep(2 ** (intento + 1))
            continue
        if r.status_code in (429, 500, 502, 503, 504) and intento < REINTENTOS:
            sleep(espera_servidor(r.headers.get('Retry-After'), 2 ** (intento + 1)))
            continue
        return r


def descargar(url):
    origen = origen_de(url)
    if origen not in robots:
        r = pedir(origen + '/robots.txt')
        robots[origen] = interpretar_robots(r.status_code, r.text, UA, estricto=True)
    if not permitido(robots[origen][0], url):
        if ROBOTS != 'report':
            raise PermissionError(f'robots.txt no permite este rastreador: {url}')
        if origen not in avisados:
            print(f'Aviso: robots.txt restringe este rastreador en {origen}; ROBOTS="report" continúa')
            avisados.add(origen)
    r = pedir(url)
    if r.status_code in (401, 403, 429):
        raise PermissionError(f'Acceso restringido, HTTP {r.status_code}: {url}')
    if r.status_code != 200:
        raise ConnectionError(f'HTTP {r.status_code}: {url}')
    return r


# ---------- artículo ----------
def sopa(html):
    if es_bloqueo(html):
        raise ValueError('Página de bloqueo o verificación; no es un artículo')
    return bs(html, 'html.parser')


def identidad(url):
    partes = urlsplit(url)
    pid = parse_qs(partes.query).get('pid', [None])[0]
    clave = partes.hostname.lower().removeprefix('www.') + ':' + (pid or partes.path.rstrip('/'))
    return hashlib.sha256(clave.encode()).hexdigest()[:24], pid


def rotulo(texto):
    return ROTULOS.get(limpiar(texto).lower().strip(':.'))


def resumenes(soup):
    lista = []

    def agregar(texto, lang, fuente):
        texto = limpiar(texto)
        if len(texto) >= 20 and not any(a['text'] == texto for a in lista):
            lista.append({'language': idioma(lang) if lang else None, 'text': texto, 'source': fuente})

    # 1) secciones HTML (IAHx guarda incluso abstracts ocultos, con id <registro>_es)
    for nodo in soup.select(SECCIONES):
        if nodo.find_parent(class_='abstract'):
            continue
        lang = nodo.get('xml:lang') or nodo.get('lang') or nodo.get('data-lang')
        final = re.search(r'[_-](es|en|pt)$', nodo.get('id', ''), re.I)
        titulo = nodo.select_one('p.sec, .abstract-title') or nodo.find(['h1', 'h2', 'h3', 'h4', 'strong', 'b'])
        if not lang and final:
            lang = final[1]
        if not lang and titulo:
            lang = rotulo(titulo.get_text(' ', strip=True))
        copia = bs(str(nodo), 'html.parser')
        for x in copia.select('.kwd-group, .keywords, h1, h2, h3, h4'):
            x.decompose()
        for p in copia.select('p'):
            texto = limpiar(p.get_text(' ', strip=True))
            if rotulo(texto) or re.match(CLAVES, texto, re.I):
                p.decompose()
        agregar(copia.get_text(' ', strip=True), lang, 'html_section')

    # 2) HTML clásico: <p><b>RESUMEN</b></p><p>...</p><p>Palabras clave...</p>
    for marca in soup.find_all(['h1', 'h2', 'h3', 'h4', 'b', 'strong']):
        etiqueta = limpiar(marca.get_text(' ', strip=True))
        lang = rotulo(etiqueta)
        if not lang or marca.find_parent(class_='abstract') or marca.find_parent(class_='articleSection'):
            continue
        padre = marca.find_parent(['p', 'div', 'section'])
        if padre and padre.name == 'p':
            inicio = padre
            texto = limpiar(padre.get_text(' ', strip=True))[len(etiqueta):].lstrip(': ')
        else:
            inicio, texto = marca, ''
        partes = [texto] if texto else []
        for hermano in inicio.next_siblings:
            if not getattr(hermano, 'name', None):
                continue
            contenido = limpiar(hermano.get_text(' ', strip=True))
            if re.match(FIN, contenido, re.I) or hermano.name in ('h1', 'h2', 'h3', 'hr', 'table'):
                break
            if contenido:
                partes.append(contenido)
        agregar(' '.join(partes), lang, 'html_heading')

    # 3) metadatos; description/og:description son texto social y solo se usan como último recurso
    for meta in soup.find_all('meta'):
        clave = (meta.get('name') or meta.get('property') or '').lower()
        if clave in META_RESUMEN and not (clave in ('description', 'og:description') and lista):
            agregar(meta.get('content'), meta.get('xml:lang') or meta.get('lang'), 'meta:' + clave)
    return lista


def enlace(soup, base, tipo):
    candidatos = []
    meta = soup.find('meta', attrs={'name': f'citation_{tipo}_url'})
    if meta and meta.get('content'):
        candidatos.append(urljoin(base, meta['content']))
    for a in soup.select('a[href]'):
        destino = urljoin(base, a['href'])
        partes = urlsplit(destino)
        ruta = partes.path.lower()
        consulta = parse_qs(partes.query)
        if not destino.startswith(('http://', 'https://')):
            continue
        if tipo == 'pdf' and (ruta.endswith('.pdf') or consulta.get('format') == ['pdf']
                              or consulta.get('script') == ['sci_pdf']):
            candidatos.append(destino)
        if tipo == 'xml' and (ruta.endswith('.xml') or 'articlexml.php' in ruta or consulta.get('format') == ['xml']):
            candidatos.append(destino)
    # sci_pdf es una página intermedia: se prefiere un enlace directo al archivo
    return next((c for c in candidatos if 'script=sci_pdf' not in c), candidatos[0] if candidatos else None)


def leer_articulo(html, url):
    soup = sopa(html)
    meta = {}
    for nodo in soup.find_all('meta'):
        clave = (nodo.get('name') or nodo.get('property') or '').lower()
        if clave and nodo.get('content'):
            meta.setdefault(clave, []).append(nodo['content'])

    def primero(*nombres):
        return next((meta[n][0] for n in nombres if meta.get(n)), None)

    titulo = primero('citation_title', 'dc.title', 'og:title')
    if not titulo:
        nodo = soup.select_one('.article-title, h1')
        titulo = limpiar(nodo.get_text(' ', strip=True)) if nodo else None
    if not titulo and primero('citation_journal_title'):
        # páginas clásicas: citation_title vacío y el título en una fuente grande del cuerpo
        for nodo in soup.select("p font[size='4'], p font[size='+2']"):
            candidato = limpiar(nodo.get_text(' ', strip=True))
            if len(candidato) >= 25 and candidato != primero('citation_journal_title'):
                titulo = candidato
                break
    lista = resumenes(soup)
    if not titulo or not (primero('citation_title', 'dc.title') or soup.select_one('.article-title') or lista):
        raise ValueError('HTML sin evidencia suficiente de artículo')

    preferido = IDIOMAS[0] if IDIOMAS else None
    mejor = (next((a for a in lista if a['language'] == preferido), None)
             or next((a for a in lista if not a['language']), None) or next(iter(lista), None))
    crudo = meta.get('citation_language', []) or meta.get('dc.language', [])
    langs = sorted({idioma(v) for valor in crudo for v in re.split(r'[,;]', valor) if v.strip()})
    fecha = primero('citation_publication_date', 'citation_date', 'dc.date', 'dc.date.issued')
    anio = re.search(r'\b(18|19|20)\d{2}\b', fecha or '')
    id_, pid = identidad(url)
    return {
        'id': id_,
        'pid': pid,
        'url': url,
        'title': limpiar(titulo),
        'doi': primero('citation_doi', 'dc.identifier.doi'),
        'authors': meta.get('citation_author', []) or meta.get('dc.creator', []),
        'journal': primero('citation_journal_title', 'dc.source'),
        'publication_date': fecha,
        'year': int(anio[0]) if anio else None,
        'languages': langs,
        'abstract': mejor['text'] if mejor else None,
        'abstract_language': mejor['language'] if mejor else None,
        'abstracts': lista,
        'pdf_url': enlace(soup, url, 'pdf'),
        'xml_url': enlace(soup, url, 'xml'),
    }


# ---------- validación ----------
def elegir_resumen(reg):
    validos = [a for a in reg['abstracts'] if a['text'].strip()]
    if IDIOMAS:
        return next((a for lang in IDIOMAS for a in validos if a['language'] == lang), None)
    return next((a for a in validos if a['text'] == reg['abstract']), validos[0] if validos else None)


def motivos(reg):
    lista = []
    if not reg['title'].strip():
        lista.append('title_missing')
    if IDIOMAS and not set(IDIOMAS) & set(reg['languages']):
        lista.append('publication_language_mismatch')
    if not elegir_resumen(reg):
        lista.append('abstract_missing_or_language_mismatch')
    return lista


def procesar(url, origen):
    r = descargar(url)
    reg = leer_articulo(r.content, url)
    reg['discovered_from'] = origen
    reg['fetched_at'] = ahora()
    reg['motivos'] = motivos(reg)
    reg['status'] = 'filtrado' if reg['motivos'] else 'ok'
    return reg


def fila_corpus(reg):
    resumen = elegir_resumen(reg)
    fila = {c: reg.get(c) for c in CAMPOS}
    fila.update({
        'paper_id': reg['id'],
        'abstract': resumen['text'],
        'language': resumen['language'],
        'publication_languages': reg['languages'],
        'source': 'scielo',
        'collection': 'per' if urlsplit(reg['url']).hostname == 'www.scielo.org.pe' else None,
    })
    return fila


# ---------- catálogo ----------
def url_catalogo(href, base):
    partes, origen = urlsplit(urljoin(base, href)), urlsplit(BASE)
    if partes.hostname != origen.hostname or partes.path != '/scielo.php':
        return None
    consulta = parse_qs(partes.query)
    script = consulta.get('script', [''])[0]
    pid = consulta.get('pid', [''])[0]
    if script not in SCRIPTS or (script != 'sci_alphabetic' and not pid):
        return None
    if script == 'sci_abstract':
        script = 'sci_arttext'
    params = {'script': script}
    if pid:
        params['pid'] = pid
    params.update({'lng': 'es', 'nrm': 'iso'})
    if script == 'sci_arttext' and consulta.get('tlng'):
        params['tlng'] = consulta['tlng'][0]
    return urlunsplit((origen.scheme, origen.netloc, partes.path, urlencode(params), ''))


def ajustes():
    return {'url': CATALOGO, 'revistas': sorted(REVISTAS), 'idiomas': sorted(IDIOMAS),
            'anios': sorted(str(a) for a in ANIOS)}


def cargar_estado():
    actual = ajustes()
    estado = cargar_json(CARPETA + '/estado.json')
    if estado is None:
        return {'ajustes': actual, 'vistas': [], 'pendientes': {CATALOGO: 0}, 'articulos': []}
    previo = estado['ajustes']
    mismos = all(previo[k] == actual[k] for k in ('url', 'idiomas', 'anios'))
    amplia = bool(previo['revistas']) and bool(actual['revistas']) and set(previo['revistas']) <= set(actual['revistas'])
    if not mismos or (previo['revistas'] != actual['revistas'] and not amplia):
        raise ValueError('Los filtros del catálogo cambiaron: use otra carpeta para no mezclar muestras')
    if previo['revistas'] != actual['revistas']:
        # ampliación de revistas: se vuelve a leer el catálogo para encontrar las nuevas
        if CATALOGO in estado['vistas']:
            estado['vistas'].remove(CATALOGO)
        estado['pendientes'][CATALOGO] = 0
        estado['ajustes'] = actual
    return estado


def expandir(estado):
    url = min(estado['pendientes'], key=estado['pendientes'].get)
    r = descargar(url)
    soup = sopa(r.content)
    enlaces = [url_catalogo(a['href'], r.url) for a in soup.select('a[href]')]
    enlaces = list(dict.fromkeys(e for e in enlaces if e))
    if not enlaces:
        raise ValueError('No se reconocieron enlaces de navegación SciELO en el catálogo')
    conocidos = {identidad(u)[0] for u, _ in estado['articulos']}
    for destino in enlaces:
        consulta = parse_qs(urlsplit(destino).query)
        script, pid = consulta['script'][0], consulta.get('pid', [''])[0]
        revista = pid[1:10] if script == 'sci_arttext' else pid[:9]
        if REVISTAS and revista not in REVISTAS:
            continue
        if script == 'sci_arttext':
            traduccion = consulta.get('tlng', [None])[0]
            if ANIOS and pid[10:14] not in [str(a) for a in ANIOS]:
                continue
            if IDIOMAS and traduccion and traduccion not in IDIOMAS:
                continue
            if identidad(destino)[0] not in conocidos:
                estado['articulos'].append([destino, url])
                conocidos.add(identidad(destino)[0])
        elif script in PRIORIDAD and destino not in estado['vistas'] and destino not in estado['pendientes']:
            estado['pendientes'][destino] = PRIORIDAD[script]
    del estado['pendientes'][url]
    estado['vistas'].append(url)
    guardar_json(CARPETA + '/estado.json', estado)


# ---------- ejecución ----------
def ejecutar(objetivo=1000):
    ruta = CARPETA + '/articulos.jsonl'
    final, error = 'incomplete', None
    try:
        estado = cargar_estado()
        hechos = leer_jsonl(ruta)
        ids = {a['id'] for a in hechos}
        validos = sum(a['status'] == 'ok' for a in hechos)
        while validos < objetivo:
            siguiente = next(((u, o) for u, o in estado['articulos'] if identidad(u)[0] not in ids), None)
            if siguiente:
                url, origen = siguiente
                try:
                    reg = procesar(url, origen)
                except (ConnectionError, ValueError) as e:
                    reg = {'id': identidad(url)[0], 'url': url, 'status': 'error', 'error': str(e)}
                    print(f'Error en {url}: {e}')
                agregar_jsonl(ruta, reg)
                ids.add(reg['id'])
                validos += reg['status'] == 'ok'
                print(f'Progreso: {validos} artículos válidos / {objetivo}')
            elif estado['pendientes']:
                expandir(estado)
            else:
                final = 'exhausted'
                break
    except PermissionError as e:
        final, error = 'blocked', str(e)
    except (OSError, ValueError) as e:
        final, error = 'error', str(e)
    except KeyboardInterrupt:
        final, error = 'interrupted', 'Interrupción solicitada; se conserva el estado para reanudar'
    if error:
        print(error)

    registros = leer_jsonl(ruta)
    filas = [fila_corpus(r) for r in registros if r['status'] == 'ok']
    escribir_corpus(CARPETA + '/corpus', filas, CAMPOS)
    if final in ('incomplete', 'exhausted') and len(filas) >= objetivo:
        final = 'passed'
    conteo = {}
    for r in registros:
        conteo[r['status']] = conteo.get(r['status'], 0) + 1
    guardar_json(CARPETA + '/report.json', {
        'status': final, 'target': objetivo, 'error': error, 'finished_at': ahora(),
        'counts': {'valid_records': len(filas), **conteo},
    })
    print(f'Resultado: {final}; válidos={len(filas)}/{objetivo}; informe={CARPETA}/report.json')
    return 0 if final == 'passed' else 2


if __name__ == '__main__':
    objetivo = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    if objetivo < 1:
        sys.exit('El objetivo debe ser positivo')
    sys.exit(ejecutar(objetivo))
