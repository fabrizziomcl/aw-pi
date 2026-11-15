import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

import pandas as pd

IDIOMAS = {'spanish': 'es', 'español': 'es', 'spa': 'es', 'esp': 'es',
           'english': 'en', 'eng': 'en', 'portuguese': 'pt', 'por': 'pt'}
TITULOS_BLOQUEO = ('just a moment', 'access denied', 'attention required', 'captcha',
                   'verify you are human', 'not a bot')
MARCAS_BLOQUEO = ("making sure you're not a bot", 'cf-chl', 'challenge-platform')


def limpiar(texto):
    return re.sub(r'\s+', ' ', texto or '').strip()


def idioma(valor):
    valor = limpiar(valor).lower()
    return IDIOMAS.get(valor, valor.split('-')[0])


def ahora():
    return datetime.now(timezone.utc).isoformat()


def es_bloqueo(html):
    if isinstance(html, bytes):
        html = html.decode('utf-8', 'replace')
    cabeza = html[:30000].lower()
    titulo = re.search(r'<title[^>]*>(.*?)</title>', cabeza, re.S)
    titulo = titulo.group(1) if titulo else ''
    return any(t in titulo for t in TITULOS_BLOQUEO) or any(m in cabeza for m in MARCAS_BLOQUEO)


def origen_de(url):
    partes = urlsplit(url)
    if partes.scheme not in ('http', 'https') or not partes.netloc or partes.username:
        raise ConnectionError(f'URL HTTP(S) inválida: {url}')
    return f'{partes.scheme}://{partes.netloc}'


def espera_servidor(cabecera, defecto):
    texto = str(cabecera or '')
    espera = float(texto) if texto.replace('.', '', 1).isdigit() else defecto
    if espera > 300:
        raise ConnectionError(f'El servidor pide esperar {espera:.0f}s; reanude más tarde')
    return espera


# robots.txt: grupos por user-agent, comodines (*, $), Allow/Disallow por la regla más larga y Crawl-delay
def reglas_robots(texto, agente):
    grupos, actual, despues = [], None, False
    for linea in texto.splitlines():
        linea = linea.split('#')[0].strip()
        if ':' not in linea:
            continue
        clave, valor = [x.strip() for x in linea.split(':', 1)]
        clave = clave.lower()
        if clave == 'user-agent':
            if actual is None or not despues:
                actual = {'agentes': [], 'reglas': [], 'retraso': 0}
                grupos.append(actual)
            actual['agentes'].append(valor.lower())
            despues = True
            continue
        despues = False
        if actual is None:
            continue
        if clave in ('allow', 'disallow') and valor:
            actual['reglas'].append((clave == 'allow', valor))
        elif clave == 'crawl-delay':
            try:
                actual['retraso'] = float(valor)
            except ValueError:
                pass
    propios = [g for g in grupos if any(a != '*' and a in agente.lower() for a in g['agentes'])]
    elegidos = propios or [g for g in grupos if '*' in g['agentes']]
    reglas = [r for g in elegidos for r in g['reglas']]
    return reglas, max([g['retraso'] for g in elegidos], default=0)


def interpretar_robots(codigo, texto, agente, estricto=False):
    es_html = texto.lstrip().lower().startswith(('<html', '<!doctype'))
    if codigo == 200 and not es_html:
        return reglas_robots(texto, agente)
    sin_robots = codigo in (404, 410) or (not estricto and (codigo == 200 or 400 <= codigo < 500 and codigo != 429))
    if sin_robots:
        return [], 0
    raise PermissionError(f'No se pudo verificar robots.txt (HTTP {codigo})')


def permitido(reglas, url):
    partes = urlsplit(url)
    destino = (partes.path or '/') + (f'?{partes.query}' if partes.query else '')
    mejor = None
    for permite, patron in reglas:
        fin = patron.endswith('$')
        cuerpo = patron[:-1] if fin else patron
        regex = '.*'.join(re.escape(x) for x in cuerpo.split('*')) + ('$' if fin else '')
        if re.match(regex, destino):
            clave = (len(patron), permite)
            if mejor is None or clave > mejor:
                mejor = clave
    return True if mejor is None else mejor[1]


def cargar_json(ruta):
    if not os.path.exists(ruta):
        return None
    with open(ruta, encoding='utf-8') as f:
        return json.load(f)


def guardar_json(ruta, datos):
    os.makedirs(os.path.dirname(ruta) or '.', exist_ok=True)
    with open(ruta + '.tmp', 'w', encoding='utf-8') as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)
    os.replace(ruta + '.tmp', ruta)


def leer_jsonl(ruta):
    if not os.path.exists(ruta):
        return []
    with open(ruta, encoding='utf-8') as f:
        return [json.loads(linea) for linea in f if linea.strip()]


def agregar_jsonl(ruta, registro):
    os.makedirs(os.path.dirname(ruta) or '.', exist_ok=True)
    with open(ruta, 'a', encoding='utf-8') as f:
        f.write(json.dumps(registro, ensure_ascii=False) + '\n')


def escribir_corpus(ruta, filas, campos):
    # ruta sin extensión: escribe ruta.jsonl y ruta.csv con una fila por documento
    os.makedirs(os.path.dirname(ruta) or '.', exist_ok=True)
    with open(ruta + '.jsonl', 'w', encoding='utf-8') as f:
        for fila in filas:
            f.write(json.dumps({c: fila.get(c) for c in campos}, ensure_ascii=False) + '\n')
    tabla = [{c: json.dumps(fila.get(c), ensure_ascii=False) if isinstance(fila.get(c), (list, dict))
              else fila.get(c) for c in campos} for fila in filas]
    pd.DataFrame(tabla, columns=campos).to_csv(ruta + '.csv', index=False, encoding='utf-8-sig')
