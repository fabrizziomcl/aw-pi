import argparse
import hashlib
import json
import re
import subprocess
import sys
from html import unescape
from urllib.parse import urljoin

from bs4 import BeautifulSoup as bs
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait

import comun
from comun import (agregar_jsonl, cargar_json, descargar, en_espanol, esperar, guardar_corpus, guardar_json,
                   leer_jsonl, limpiar, puede)

CARPETA = 'data/universidades_simple'
REPOS = __file__.replace('universidades.py', 'repos.json')
SEMILLA = 'aw-2026'  # orden pseudoaleatorio de las fichas: la muestra no sigue el orden del sitemap
CAMPOS = ['paper_id', 'title', 'abstract', 'authors', 'url', 'year', 'university']

# valores por defecto de cada repositorio; repos.json solo indica lo que cambia
REPO = {'discovery': 'sitemap', 'sitemap': '/sitemap_index.xml', 'rewrites': [],
        'item_pattern': r'/(items|entities/publication)/[0-9a-f-]{36}/?$', 'item_suffix': '/full', 'render': False,
        'wait_for': "//html[head/meta[@name='citation_title']]//table//td"}
# UPN y UNHEVAL muestran rótulos en vez de la tabla de metadatos
MUI = {'autor(es)': 'dc.contributor.author', 'fecha de publicación': 'dc.date.issued',
       'resumen': 'dc.description.abstract', 'tipo de documento': 'dc.type', 'pertenece a la colección': 'collection'}
TESIS = re.compile(r'thesis|tesis|suficiencia|trabajos? ?(de ?investigaci|acad)|segunda especialidad|/c_(7a1f|bdcc|db06|46ec)', re.I)
UUID = re.compile(r'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}')

navegador = {'driver': None, 'paginas': 0}


def cargar_repos():
    with open(REPOS, encoding='utf-8') as f:
        return [{**REPO, **r} for r in json.load(f)]


def normalizar(url, repo):
    for patron, reemplazo in repo['rewrites']:
        url = re.sub(patron, reemplazo, url)
    return re.sub('^http://', 'https://', url)  # algunos sitemaps declaran http://


# ---------- descubrimiento: sitemap XML o /htmlmap (DSpace 6) ----------
def descubrir(repo):
    pendientes, vistas, items = [repo['base_url'] + repo['sitemap']], set(), []
    while pendientes:
        url = normalizar(pendientes.pop(0), repo)
        if url in vistas:
            continue
        vistas.add(url)
        print(f"{repo['key']}: {url}")
        html = descargar(url).content.decode('utf-8', 'replace')
        if repo['discovery'] == 'htmlmap':
            enlaces = [urljoin(url, a['href']) for a in bs(html, 'html.parser').find_all('a', href=True)]
            pendientes += [e for e in enlaces if 'htmlmap?map=' in e]
        else:
            enlaces = [unescape(loc) for loc in re.findall(r'<loc>\s*(\S+?)\s*</loc>', html)]
            if '<sitemapindex' in html:
                pendientes += enlaces
        items += [normalizar(e, repo) for e in enlaces]
    ids = {}
    for url in items:
        if re.search(repo['item_pattern'], url):
            uuid = UUID.search(url)
            clave = uuid[0] if uuid else re.search(r'/handle/(.+?)/?$', url)[1]  # DSpace 6 usa handle
            ids.setdefault(repo['key'] + ':' + clave, url)
    return sorted(ids.items(), key=lambda p: hashlib.sha256(f'{SEMILLA}:{p[0]}'.encode()).hexdigest())


# ---------- descarga de la ficha: curl_cffi o Selenium ----------
def abrir(url, repo):
    if not repo['render']:
        r = descargar(url)
        return (r.content.decode('utf-8', 'replace'), r.url) if r else None
    if not puede(url):
        return None
    esperar(url)
    # Chrome acumula memoria página tras página (varios GB por hora): se reinicia cada 50 fichas
    navegador['paginas'] += 1
    if navegador['driver'] and navegador['paginas'] % 50 == 0:
        navegador['driver'].quit()
        navegador['driver'] = None
    if navegador['driver'] is None:
        opciones = webdriver.ChromeOptions()
        opciones.add_argument('--headless=new')
        opciones.page_load_strategy = 'eager'  # no espera imágenes ni hojas de estilo
        opciones.add_experimental_option('prefs', {'profile.managed_default_content_settings.images': 2})
        navegador['driver'] = webdriver.Chrome(options=opciones)
    driver = navegador['driver']
    driver.get(url)
    # el sitio arma la ficha con JavaScript: se espera a que aparezca la tabla o los rótulos
    WebDriverWait(driver, 30).until(lambda d: d.find_elements(by='xpath', value=repo['wait_for']))
    return driver.page_source, driver.current_url


# ---------- lectura de la ficha ----------
def leer_ficha(html):
    soup = bs(html, 'html.parser')
    md = {}
    # vista completa de DSpace: tabla con filas campo | valor | idioma
    for fila in soup.select('table tr'):
        celdas = [limpiar(c.get_text(' ')) for c in fila.find_all(['td', 'th'])]
        if len(celdas) >= 2 and celdas[1]:
            md.setdefault(celdas[0], []).append(celdas[1])
    # interfaz MUI (UPN, UNHEVAL): rótulo <h6> seguido de una caja con los valores
    for rotulo in soup.find_all('h6'):
        clave, caja = MUI.get(limpiar(rotulo.get_text(' ')).lower()), rotulo.find_next_sibling()
        if clave and caja:
            hijos = caja.find_all(recursive=False)
            partes = hijos if clave != 'dc.description.abstract' and len(hijos) > 1 else [caja]
            md[clave] = [limpiar(p.get_text(' ')) for p in partes]
    titulos = [limpiar(t.get_text(' ')) for t in soup.find_all(class_=re.compile('MuiTypography-h[45]'))]
    md.setdefault('dc.title', [t for t in titulos if len(t) > 10][:1])
    fechas = [m['content'] for m in soup.find_all('meta', {'name': 'citation_publication_date'}) if m.get('content')]
    md.setdefault('dc.date.issued', fechas)  # si la tabla no trae la fecha, se usa la de citation_*
    migas = soup.select("a[href*='/collection/']")  # ruta de navegación: la última es la colección
    md.setdefault('collection', [limpiar(migas[-1].get_text(' '))] if migas else [])
    return md


def es_tesis(md):
    tipos = md.get('dc.type', []) + md.get('renati.type', [])
    if tipos:  # un tipo declarado manda sobre el grado o la colección
        return any(TESIS.search(t) for t in tipos)
    return bool(md.get('thesis.degree.name')) or any(TESIS.search(c) for c in md['collection'])


def procesar(repo, pid, url):
    ficha = abrir(url + repo['item_suffix'], repo)
    if ficha is None:
        return {'paper_id': pid, 'url': url, 'status': 'no_permitido'}
    html, final = ficha
    if pid.split(':', 1)[1] not in final:
        return {'paper_id': pid, 'url': url, 'status': 'omitido', 'error': f'redirigió a {final}'}
    md = leer_ficha(html)
    anio = re.search(r'\b(1[89]|20)\d{2}\b', ' '.join(md.get('dc.date.issued', [])))
    reg = {'paper_id': pid, 'title': next(iter(md['dc.title']), None),
           'abstract': next((t for t in md.get('dc.description.abstract', []) if en_espanol(t)), None),
           'authors': '; '.join(md.get('dc.contributor.author', [])), 'url': url,
           'year': int(anio[0]) if anio else None, 'university': repo['name']}
    reg['status'] = 'ok' if reg['title'] and reg['abstract'] and es_tesis(md) else 'filtrado'
    return reg


def procesar_repo(repo, objetivo):
    carpeta = f"{CARPETA}/{repo['key']}"
    if not cargar_json(carpeta + '/urls.json'):
        guardar_json(carpeta + '/urls.json', {'urls': descubrir(repo)})
    hechos = leer_jsonl(carpeta + '/fichas.jsonl')
    vistos = {r['paper_id'] for r in hechos}
    validos = sum(r['status'] == 'ok' for r in hechos)
    for pid, url in cargar_json(carpeta + '/urls.json')['urls']:
        if validos >= objetivo:
            break
        if pid in vistos:
            continue
        try:
            reg = procesar(repo, pid, url)
        except PermissionError as e:  # HTTP 403/429: el sitio pide parar
            print(f"{repo['key']}: {e}; se detiene esta universidad")
            break
        except Exception as e:  # red caída, ficha que no cargó, etc.: se anota y se sigue
            reg = {'paper_id': pid, 'url': url, 'status': 'error', 'error': str(e).splitlines()[0]}
        agregar_jsonl(carpeta + '/fichas.jsonl', reg)
        validos += reg['status'] == 'ok'
        print(f"{repo['key']}: {validos}/{objetivo} tesis válidas ({reg['status']}) {url}")


def consolidar():
    # corpus de TODAS las universidades en disco, aunque cada una se haya corrido por separado
    registros = []
    for repo in cargar_repos():
        registros += leer_jsonl(f"{CARPETA}/{repo['key']}/fichas.jsonl")
    guardar_corpus(CARPETA, registros, CAMPOS)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='Scraping de tesis en repositorios universitarios peruanos')
    ap.add_argument('--repos', default='', help='claves separadas por comas (por defecto, todas)')
    ap.add_argument('--target', type=int, default=100, help='tesis válidas por universidad')
    ap.add_argument('--paralelo', action='store_true', help='un proceso por universidad, todas a la vez')
    ap.add_argument('--sin-corpus', action='store_true', help=argparse.SUPPRESS)  # lo usan los procesos hijos
    args = ap.parse_args()
    repos = [r for r in cargar_repos() if not args.repos or r['key'] in args.repos.split(',')]
    comun.cfg.update(ua='UniversidadesMVP', impersonate=True, pausa=1, robots='respect')
    try:
        if args.paralelo:
            # cada universidad en su propio proceso (y su propio Chrome); cada sitio sigue a 1 solicitud por segundo
            procesos = [subprocess.Popen([sys.executable, __file__, '--repos', r['key'], '--target', str(args.target),
                                          '--sin-corpus']) for r in repos]
            for p in procesos:
                p.wait()
        else:
            for repo in repos:
                procesar_repo(repo, args.target)
    finally:
        if navegador['driver']:
            navegador['driver'].quit()
        if not args.sin_corpus:
            consolidar()
