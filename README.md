# AW-PI: scraping de literatura académica peruana

Este proyecto solo hace **scraping**. Arma dos corpus de resúmenes en español:

- **SciELO Perú**: artículos de todas las revistas del catálogo (61).
- **Repositorios universitarios**: tesis de 11 repositorios peruanos.

Cada fuente produce un `corpus.jsonl` y un `corpus.csv`, con una fila por documento y los mismos campos.

> Parte del código y de esta documentación se generó con apoyo de IA (Claude). Conviene que una persona conocedora de estos sitios lo valide antes de una extracción grande.

## Estructura

```text
scraping/
  comun.py           descarga con pausa por sitio, robots.txt, detección de español y archivos
  scielo.py          SciELO Perú: catálogo → revista → números → artículos (requests + BeautifulSoup)
  universidades.py   repositorios: sitemap → fichas (curl_cffi o Selenium + BeautifulSoup)
  repos.json         lista de repositorios y sus particularidades
tests/               pruebas sin red (python -m pytest -q)
data/                salidas (scielo_simple/, universidades_simple/)
```

## Instalación

```bash
python -m pip install -r requirements.txt
```

Seis repositorios (La Molina, ESAN, UPN, UNCP, UNC y UNHEVAL) arman la ficha con JavaScript y necesitan **Google Chrome**.

## Campos de cada registro

Las dos fuentes tienen los mismos campos; solo cambia el último.

| Campo | SciELO | Universidades |
|---|---|---|
| `paper_id` | `scielo:<pid>`, p. ej. `scielo:S1726-46342020000100005` | `<repo>:<uuid>`, o `unmsm:<handle>` |
| `title` | `citation_title` (o el título en letra grande de las páginas antiguas) | `dc.title` de la tabla de metadatos, o el título de la ficha (UPN, UNHEVAL) |
| `abstract` | Resumen **en español** de la sección de resumen o bajo el rótulo «RESUMEN» | Resumen **en español** (`dc.description.abstract`) |
| `authors` | Autores separados por `; ` (`citation_author`) | Autores separados por `; ` (`dc.contributor.author`) |
| `url` | Página del artículo | Página de la ficha |
| `year` | Año, tomado del pid | Año de `dc.date.issued` |
| `journal` / `university` | Nombre de la revista | Nombre de la universidad |

**Cuándo un documento es válido.** Un registro es válido (`status: "ok"`) si tiene título y resumen en español. En universidades, además debe ser **tesis**: el `dc.type` o el `renati.type` lo indican; si la ficha no declara tipo, se acepta si tiene grado académico o si la colección es de tesis. El idioma se reconoce contando palabras frecuentes del español, el inglés y el portugués.

## SciELO (`scraping/scielo.py`)

```bash
python scraping/scielo.py 10000      # objetivo total de artículos válidos
```

1. **Descubre** los artículos: lee el catálogo, luego la lista de números de cada revista y el índice de cada número. Guarda los pid de cada revista en `urls.json`, revista por revista; es lo más lento la primera vez (unas 2 000 páginas).
2. **Descarga** los artículos tomando **uno de cada revista por turno**, así el corpus crece parejo. Una revista sin ningún válido tras 30 artículos se salta, como pasa con las revistas en inglés.
3. Va a 1 solicitud por segundo, sin paralelizar, porque todo es un mismo sitio.

Constantes al inicio del archivo: `REVISTAS` (`[]` = todas, o una lista de ISSN), `MIN_EXITO`, `CARPETA`, `ROBOTS`.

| Archivo en `data/scielo_simple/` | Contenido |
|---|---|
| `urls.json` | pid de los artículos de cada revista. |
| `articulos.jsonl` | Un registro por artículo procesado, con `status`: `ok`, `filtrado` o `error`. |
| `corpus.jsonl`, `corpus.csv` | Solo los válidos. |

## Universidades (`scraping/universidades.py`)

```bash
python scraping/universidades.py --target 2000              # todas, una tras otra
python scraping/universidades.py --target 2000 --paralelo   # todas a la vez, un proceso por universidad
python scraping/universidades.py --repos upch,unfv --target 50
```

1. **Descubre** las fichas con el sitemap XML (o `/htmlmap` en UNMSM) y las ordena con un hash con semilla, para que la muestra no siga el orden del sitemap. Las guarda en `<repo>/urls.json`.
2. **Descarga** la vista completa de cada ficha (`/full`), que trae la tabla de metadatos:
   - con `curl_cffi` en UPCH, UNFV, UNMSM, UNAP y UNICA;
   - con Selenium en La Molina, ESAN, UNCP y UNC;
   - en UPN y UNHEVAL, Selenium lee los rótulos de la ficha («Autor(es)», «Resumen»…).
3. **Pausa** de 1 s por sitio, o el `Crawl-delay` de su robots.txt si es mayor (UPCH pide 10 s).
4. **`--paralelo`** lanza un proceso por universidad, cada uno con su propio Chrome. Cada sitio sigue recibiendo 1 solicitud por segundo, pero el tiempo total pasa a ser el de la universidad más lenta. También puedes correr una universidad por terminal con `--repos`.

| Archivo en `data/universidades_simple/` | Contenido |
|---|---|
| `<repo>/urls.json` | Fichas descubiertas, en el orden de la muestra. |
| `<repo>/fichas.jsonl` | Un registro por ficha procesada, con `status`: `ok`, `filtrado`, `omitido` (redirige a otra página), `no_permitido` (robots.txt) o `error`. |
| `corpus.jsonl`, `corpus.csv` | Tesis válidas de **todas** las universidades en disco. Se rehace al final de cada corrida. |

Para agregar un repositorio, añade una línea a `scraping/repos.json` con `key`, `name` y `base_url`. El resto toma los valores de `REPO` en `universidades.py`.

## Reanudar

Repite el mismo comando: lo que ya está en `articulos.jsonl` o `fichas.jsonl` no se vuelve a pedir. Para volver a descubrir un sitio (fichas nuevas), borra su `urls.json`. Para empezar de cero, borra la carpeta de salida.

## Límites

- En las universidades se respeta robots.txt, incluido su `Crawl-delay`. Si un sitio responde 403 o 429, se detiene esa universidad. No bajes la pausa ni paralelices dentro de un mismo sitio.
- **SciELO:** su robots.txt actual (`User-agent: *` → `Disallow: /`) solo admite buscadores web y excluye a cualquier otro rastreador, sea o no de IA. El proyecto corre en `ROBOTS = 'report'` por decisión del equipo: avisa y continúa. Por eso va a 1 solicitud por segundo y sin paralelizar. Con `ROBOTS = 'respect'` no se descarga nada de ese sitio.
- No se eluden CAPTCHAs ni bloqueos. Los registros guardan nombres de autores: comparte solo lo necesario.
