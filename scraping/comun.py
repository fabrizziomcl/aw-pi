import json
import os
import re
from time import sleep, time
from urllib.parse import urlsplit

import pandas as pd
import requests as rq
from curl_cffi import requests as rq2

# cada script ajusta cfg: ua (también se usa para robots.txt), impersonate (curl_cffi), pausa, robots
cfg = {'ua': 'Mozilla/5.0', 'impersonate': False, 'pausa': 1, 'robots': 'respect'}
robots, ultima, avisados = {}, {}, set()

# palabras frecuentes propias de cada idioma (sin las que comparten español y portugués: de, que, se, para, como...)
ES = {'el', 'la', 'los', 'las', 'del', 'y', 'en', 'con', 'una', 'su', 'al', 'es', 'fue', 'entre', 'sobre'}
EN = {'the', 'of', 'and', 'to', 'in', 'is', 'that', 'for', 'with', 'this', 'was', 'were', 'are', 'on', 'by'}
PT = {'o', 'os', 'e', 'da', 'do', 'dos', 'das', 'não', 'uma', 'em', 'ao', 'no', 'na', 'com', 'foi', 'pela', 'pelo'}


def limpiar(texto):
    return re.sub(r'\s+', ' ', texto or '').strip()


def en_espanol(texto):
    # se cuenta cuántas palabras frecuentes de cada idioma aparecen en el texto
    palabras = re.findall(r'[a-záéíóúñãõç]+', (texto or '').lower())[:300]
    es, en, pt = [sum(p in lista for p in palabras) for lista in (ES, EN, PT)]
    return es >= 3 and es >= en and es >= pt


def origen(url):
    return '{0.scheme}://{0.netloc}'.format(urlsplit(url))


# ---------- robots.txt: reglas del grupo de nuestro agente (o '*') y Crawl-delay ----------
def reglas_robots(texto, agente):
    grupos, actual, nuevo = [], None, True
    for linea in texto.splitlines():
        clave, dos_puntos, valor = linea.split('#')[0].partition(':')
        clave, valor = clave.strip().lower(), valor.strip()
        if not dos_puntos:
            continue
        if clave == 'user-agent':
            if nuevo:
                actual = {'agentes': [], 'reglas': [], 'retraso': 0}
                grupos.append(actual)
            actual['agentes'].append(valor.lower())
        elif actual and clave in ('allow', 'disallow') and valor:
            actual['reglas'].append((clave == 'allow', valor))
        elif actual and clave == 'crawl-delay':
            actual['retraso'] = float(valor)
        nuevo = clave != 'user-agent'
    propios = [g for g in grupos if any(a != '*' and a in agente.lower() for a in g['agentes'])]
    elegidos = propios or [g for g in grupos if '*' in g['agentes']]
    return [r for g in elegidos for r in g['reglas']], max([g['retraso'] for g in elegidos], default=0)


def permitido(reglas, url):
    # gana la regla más larga que coincide; '*' es comodín y '$' marca el final
    partes = urlsplit(url)
    destino = partes.path + ('?' + partes.query if partes.query else '')
    mejor = (0, True)
    for permite, patron in reglas:
        regex = '.*'.join(re.escape(x) for x in patron.rstrip('$').split('*')) + ('$' if patron.endswith('$') else '')
        if re.match(regex, destino) and (len(patron), permite) > mejor:
            mejor = (len(patron), permite)
    return mejor[1]


def puede(url):
    o = origen(url)
    if o not in robots:
        robots[o] = ([], 0)
        r = pedir(o + '/robots.txt')
        if r.status_code == 200:
            robots[o] = reglas_robots(r.text, cfg['ua'])
    if permitido(robots[o][0], url):
        return True
    if cfg['robots'] == 'report':
        if o not in avisados:  # se avisa una vez por sitio
            print(f'Aviso: robots.txt de {o} no permite este rastreador; se continúa (ROBOTS="report")')
            avisados.add(o)
        return True
    return False


# ---------- descarga ----------
def esperar(url):
    o = origen(url)
    pausa = max(cfg['pausa'], robots.get(o, ([], 0))[1])  # el Crawl-delay de robots.txt manda si es mayor
    sleep(max(0, pausa - (time() - ultima.get(o, 0))))
    ultima[o] = time()


def pedir(url):
    for intento in range(3):
        esperar(url)
        try:
            if cfg['impersonate']:
                r = rq2.get(url, impersonate='chrome', timeout=40)
            else:
                r = rq.get(url, headers={'user-agent': cfg['ua']}, timeout=40)
            if r.status_code < 500:
                return r
        except Exception as e:  # errores de red
            print(f'Reintento {intento + 1} en {url}: {e}')
        sleep(5)
    raise ConnectionError(f'Sin respuesta tras 3 intentos: {url}')


def descargar(url):
    # devuelve la respuesta, o None si robots.txt no permite la URL
    if not puede(url):
        return None
    r = pedir(url)
    if r.status_code in (403, 429):  # el sitio pide que paremos: no se insiste
        raise PermissionError(f'HTTP {r.status_code} en {url}')
    if r.status_code != 200:
        raise ConnectionError(f'HTTP {r.status_code} en {url}')
    return r


# ---------- archivos ----------
def cargar_json(ruta):
    if not os.path.exists(ruta):
        return None
    with open(ruta, encoding='utf-8') as f:
        return json.load(f)


def guardar_json(ruta, datos):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, 'w', encoding='utf-8') as f:
        json.dump(datos, f, ensure_ascii=False, indent=1)


def leer_jsonl(ruta):
    if not os.path.exists(ruta):
        return []
    with open(ruta, encoding='utf-8') as f:
        return [json.loads(linea) for linea in f if linea.strip()]


def agregar_jsonl(ruta, registro):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, 'a', encoding='utf-8') as f:
        f.write(json.dumps(registro, ensure_ascii=False) + '\n')


def guardar_corpus(carpeta, registros, campos):
    # una fila por documento válido, en carpeta/corpus.jsonl y carpeta/corpus.csv
    filas = [{c: r.get(c) for c in campos} for r in registros if r['status'] == 'ok']
    with open(carpeta + '/corpus.jsonl', 'w', encoding='utf-8') as f:
        for fila in filas:
            f.write(json.dumps(fila, ensure_ascii=False) + '\n')
    pd.DataFrame(filas, columns=campos).to_csv(carpeta + '/corpus.csv', index=False, encoding='utf-8-sig')
    print(f'{len(filas)} documentos válidos en {carpeta}/corpus.jsonl y corpus.csv')
