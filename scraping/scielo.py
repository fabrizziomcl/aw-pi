import argparse
import re
import subprocess
import sys
from collections import Counter
from itertools import zip_longest

from bs4 import BeautifulSoup as bs

import comun
from comun import agregar_jsonl, cargar_json, descargar, en_espanol, guardar_corpus, guardar_json, leer_jsonl, limpiar

# colecciones SciELO de Latinoamérica en español (una por país, cada una es un sitio distinto)
PAISES = {'mx': 'https://www.scielo.org.mx', 'cu': 'http://scielo.sld.cu', 've': 'http://ve.scielo.org',
          'pe': 'https://www.scielo.org.pe', 'cr': 'https://www.scielo.sa.cr', 'bo': 'http://www.scielo.org.bo',
          'uy': 'https://www.scielo.edu.uy', 'py': 'https://scielo.iics.una.py', 'ar': 'https://www.scielo.org.ar',
          'co': 'http://www.scielo.org.co', 'ec': 'https://scielo.senescyt.gob.ec'}
# Chile no está: su robots.txt prohíbe el rastreo. Perú también lo prohíbe, pero el equipo decidió continuar ('report')
REPORT = {'pe'}
MIN_EXITO = 30  # una revista sin ningún válido tras revisar 30 artículos se salta (revistas en inglés o portugués)
MAX_RESUMEN = 6000  # en algunas páginas antiguas el «resumen» arrastra el artículo entero: más largo que esto no es resumen
CARPETA = 'data/scielo_simple'
UA = 'SciELO-MVP/0.1 (small research crawler)'
CAMPOS = ['paper_id', 'title', 'abstract', 'authors', 'url', 'year', 'journal']

SECCIONES = ("div.abstract, section.abstract, div.trans-abstract, [id^='Abstract'], "
             "[data-anchor='Abstract'], [data-anchor='Resumen'], [data-anchor='Resumo']")
ROTULO = r'^(resumen|abstract|resumo|summary)\W*$|^(palabras? clave|palavras?[- ]chave|key\s*words)'
FIN = r'^(palabras? clave|palavras?[- ]chave|key\s*words|resumen|abstract|summary|resumo|introduc|referenc)'


def sopa(url):
    r = descargar(url)
    if r is None:
        raise PermissionError(f'robots.txt no permite {url}')
    return bs(r.content, 'html.parser')


def pids(url, *scripts):
    # identificadores (pid) de los enlaces de la página que van a esos scripts de SciELO
    lista = []
    try:
        soup = sopa(url)
    except ConnectionError as e:  # una página que no carga (p. ej. un número con 404) se salta
        print(f'Se salta {url}: {e}')
        return lista
    for a in soup.find_all('a', href=True):
        pid = re.search(r'pid=([\w-]+)', a['href'])
        if pid and any('script=' + s in a['href'] for s in scripts):
            lista.append(pid[1])
    return list(dict.fromkeys(lista))


def descubrir(base, carpeta):
    # revista → números → artículos; se guarda revista por revista en urls.json para poder reanudar
    ruta = carpeta + '/urls.json'
    revistas = cargar_json(ruta) or {}
    issns = pids(base + '?script=sci_alphabetic&lng=es&nrm=iso', 'sci_serial')
    for issn in issns:
        if issn not in revistas:
            articulos = []
            for numero in pids(f'{base}?script=sci_issues&pid={issn}&lng=es&nrm=iso', 'sci_issuetoc'):
                articulos += pids(f'{base}?script=sci_issuetoc&pid={numero}&lng=es&nrm=iso', 'sci_arttext', 'sci_abstract')
            revistas[issn] = list(dict.fromkeys(articulos))
            guardar_json(ruta, revistas)
            print(f'{carpeta[-2:]}: revista {issn}, {len(revistas[issn])} artículos')
    return [revistas[issn] for issn in issns]


def resumen_es(soup):
    textos = []
    # 1) secciones de resumen (páginas nuevas); se quitan rótulos y palabras clave
    for nodo in soup.select(SECCIONES):
        for x in nodo.select('h1, h2, h3, h4, .kwd-group, .keywords'):
            x.decompose()
        for p in nodo.find_all('p'):
            if re.match(ROTULO, limpiar(p.get_text(' ')), re.I):
                p.decompose()
        textos.append(nodo.get_text(' ', strip=True))
    # 2) páginas clásicas: <p><b>RESUMEN</b></p><p>texto...</p><p>Palabras clave...</p>
    for marca in soup.find_all(['b', 'strong', 'h2', 'h3', 'h4']):
        rotulo = limpiar(marca.get_text(' '))
        if rotulo.lower().strip(':. ') != 'resumen' or marca.find_parent(class_=['abstract', 'articleSection']):
            continue
        inicio = marca.find_parent('p') or marca
        partes = [limpiar(inicio.get_text(' '))[len(rotulo):]]
        for hermano in inicio.find_next_siblings():
            texto = limpiar(hermano.get_text(' '))
            if re.match(FIN, texto, re.I) or hermano.name in ('h1', 'h2', 'h3', 'hr', 'table'):
                break
            partes.append(texto)
        textos.append(' '.join(partes))
    return next((limpiar(t).strip(': ') for t in textos if en_espanol(t) and len(t) < MAX_RESUMEN), None)


def leer_articulo(soup, pid, url):
    def meta(nombre):
        return [m['content'] for m in soup.find_all('meta', {'name': nombre}) if m.get('content')]

    # páginas clásicas sin versión en español: citation_title viene vacío y el título va en letra grande
    grandes = [f.get_text(' ') for f in soup.select("p font[size='4'], p font[size='+2']")]
    titulo = meta('citation_title') or grandes or [None]
    revista = meta('citation_journal_title') or [None]
    return {'paper_id': 'scielo:' + pid, 'title': limpiar(titulo[0]) or None, 'abstract': resumen_es(soup),
            'authors': '; '.join(meta('citation_author')), 'url': url, 'year': int(pid[10:14]), 'journal': revista[0]}


def ejecutar(pais, objetivo):
    comun.cfg.update(ua=UA, impersonate=False, pausa=1, robots='report' if pais in REPORT else 'respect')
    base, carpeta = PAISES[pais] + '/scielo.php', f'{CARPETA}/{pais}'
    ruta = carpeta + '/articulos.jsonl'
    hechos = leer_jsonl(ruta)
    vistos = {r['paper_id'] for r in hechos}
    intentos = Counter(r['paper_id'][8:17] for r in hechos)  # 'scielo:S' + ISSN de 9 caracteres
    validos = Counter(r['paper_id'][8:17] for r in hechos if r['status'] == 'ok')
    try:
        # un artículo de cada revista por turno, así el corpus crece parejo entre revistas
        cola = [pid for turno in zip_longest(*descubrir(base, carpeta)) for pid in turno if pid]
        for pid in cola:
            issn = pid[1:10]
            if sum(validos.values()) >= objetivo:
                break
            if 'scielo:' + pid in vistos or (intentos[issn] >= MIN_EXITO and not validos[issn]):
                continue
            url = f'{base}?script=sci_arttext&pid={pid}&lng=es&nrm=iso&tlng=es'  # tlng=es: versión en español
            try:
                reg = leer_articulo(sopa(url), pid, url)
                reg['status'] = 'ok' if reg['title'] and reg['abstract'] else 'filtrado'
            except ConnectionError as e:
                reg = {'paper_id': 'scielo:' + pid, 'url': url, 'status': 'error', 'error': str(e)}
            agregar_jsonl(ruta, reg)
            vistos.add(reg['paper_id'])
            intentos[issn] += 1
            validos[issn] += reg['status'] == 'ok'
            print(f"{pais}: {sum(validos.values())}/{objetivo} válidos ({reg['status']}) {url}")
    except (ConnectionError, PermissionError) as e:  # sitio caído o que pide parar
        print(f'{pais}: se detiene ({e})')


def consolidar():
    # corpus de TODOS los países en disco, sin repetir artículos
    registros = {}
    for pais in PAISES:
        for r in leer_jsonl(f'{CARPETA}/{pais}/articulos.jsonl'):
            if len(r.get('abstract') or '') < MAX_RESUMEN:  # descarta los guardados antes de existir el límite
                registros.setdefault(r['paper_id'], r)
    guardar_corpus(CARPETA, list(registros.values()), CAMPOS)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='Scraping de artículos de SciELO Latinoamérica')
    ap.add_argument('--paises', default=','.join(PAISES), help='códigos separados por comas: ' + ','.join(PAISES))
    ap.add_argument('--target', type=int, default=1000, help='artículos válidos por país')
    ap.add_argument('--paralelo', action='store_true', help='un proceso por país, todos a la vez')
    ap.add_argument('--sin-corpus', action='store_true', help=argparse.SUPPRESS)  # lo usan los procesos hijos
    args = ap.parse_args()
    try:
        if args.paralelo:
            # cada país es un sitio distinto: en paralelo, cada uno sigue a 1 solicitud por segundo
            procesos = [subprocess.Popen([sys.executable, __file__, '--paises', p, '--target', str(args.target),
                                          '--sin-corpus']) for p in args.paises.split(',')]
            for p in procesos:
                p.wait()
        else:
            for pais in args.paises.split(','):
                ejecutar(pais, args.target)
    finally:
        if not args.sin_corpus:
            consolidar()
