# Scraping simplificado: cómo ejecutar cada parte

Guía de uso de `scraping_simple/`, la versión corta del scraping de SciELO y de repositorios de tesis. Complementa a [README.md](README.md), que describe el flujo anterior (paquetes `scielo_mvp` y `universidades_mvp`, que ya no están en el proyecto).

> Parte del código y de esta documentación se generó con apoyo de IA (Claude). Conviene que una persona conocedora del scraping de estos sitios la valide antes de una extracción grande.

## 1. Qué hay

```text
scraping_simple/
  comun.py            funciones compartidas: robots.txt, detección de bloqueos, JSONL, exportación del corpus
  scielo.py           SciELO Perú: descubre por catálogo de revistas y descarga artículos
  universidades.py    repositorios de tesis: descubre por sitemap/htmlmap/browse y descarga fichas
  requirements.txt    dependencias del scraping
config.universidades.json   repositorios y parámetros de universidades.py
vectorize_data.py, vectorizers.py   vectorización de los abstracts (no cambió)
tests/test_scielo_simple.py, tests/test_universidades_simple.py   pruebas sin red
```

El flujo completo es: **scraping (dos fuentes) → `corpus.jsonl` de cada fuente → vectorización**.

`config.peru-catalog.json` ya no lo lee nadie: `scielo.py` tiene sus parámetros como constantes al inicio del archivo.

## 2. Instalación

Se recomienda Python 3.12. Ejecuta todo desde la raíz del repositorio.

```bash
python -m pip install -r scraping_simple/requirements.txt
```

- Para los repositorios que usan Selenium (La Molina, ESAN, UPN, UNCP, UNC, UNHEVAL) necesitas **Google Chrome** instalado.
- Para vectorizar, además:

```bash
python -m pip install -r requirements-vectorizers.txt
```

- Para correr los tests: `python -m pip install pytest`.

## 3. SciELO (`scielo.py`)

```bash
python scraping_simple/scielo.py 1000
```

El número es el **objetivo de artículos válidos** (por defecto 1000). Para una prueba rápida usa `2`.

### Qué hace

1. Parte de la página alfabética de revistas de SciELO Perú y baja por revista → números → artículos. Primero lee el catálogo, las revistas y sus listas de números; después trabaja siempre en la revista con menos artículos válidos, así el corpus crece parejo entre revistas.
2. Descarga cada artículo y extrae título, autores, DOI, revista, año, idiomas, resumen y enlaces PDF y XML.
3. Un artículo es **válido** si su idioma coincide con `IDIOMAS` y tiene resumen en esos idiomas. Los demás quedan como `filtrado`.
4. Se detiene al llegar al objetivo, al agotar el catálogo o ante un bloqueo.

### Revistas incluidas

`REVISTAS` tiene 25 revistas, unas 4 por tema:

| Tema | Revistas (ISSN) |
|---|---|
| Ingeniería y ciencias | Industrial Data (1810-9993), Ingeniería Industrial (2523-6326), Tecnia (2309-0413), Revista de la Sociedad Química del Perú (1810-634X), Ecología Aplicada (1726-2216) |
| Medicina | Revista Peruana de Medicina Experimental y Salud Pública (1726-4634), Acta Médica Peruana (1728-5917), Anales de la Facultad de Medicina (1025-5583), Revista Médica Herediana (1018-130X) |
| Psicología y educación | Educación (1019-9403), Revista de Psicología PUCP (0254-9247), Liberabit (1729-4827), Propósitos y Representaciones (2307-7999) |
| Derecho y política | Derecho PUCP (0251-3420), Revista Oficial del Poder Judicial (2663-9130), Revista Elecciones (1995-6290) |
| Humanidades y ciencias sociales | Allpanchis (2708-8960), Anthropologica (0254-9212), Lexis (0254-9239), Debates en Sociología (2304-4284), Biblios (1562-4730) |
| Economía y gestión | Quipukamayoc (1609-8196), Revista de Ciencias de la Gestión (2518-0495), Semestre Económico (2072-0572), Journal of Economics, Finance and Administrative Science (2077-1886) |

El catálogo de SciELO Perú tiene 61 revistas. Derecho solo tiene 3 en el catálogo. La selección se hizo por el título de cada revista, sin medir su volumen real de artículos en español. Si quieres otras, cambia sus ISSN en `REVISTAS`.

### Cómo se reparte entre revistas

Para que pocas revistas no dominen el corpus:

1. El script lee primero el catálogo, las revistas y sus listas de números (unas cuantas páginas por revista).
2. Después trabaja siempre en la **revista con menos artículos válidos**, así que el corpus crece parejo entre las 25.
3. `MAX_POR_REVISTA` (80) pone un tope por revista. Con 1000 artículos, el promedio es 40 por revista.
4. `MIN_EXITO` (30) abandona una revista que, tras revisar 30 artículos, no dio ninguno válido (por ejemplo, una revista en inglés).
5. Si una revista tiene pocos artículos en español, queda por debajo de su cuota y las demás compensan hasta el tope.
6. `report.json` trae `valid_by_journal`, con los válidos por revista, para comprobar el reparto.

Dentro de cada revista toma los números en el orden en que aparecen en la página, que probablemente es el más reciente primero. La muestra tenderá a los años recientes. Con `ANIOS` puedes acotar el rango.

Si ya tienes una carpeta `data/scielo_simple` de una corrida con otras revistas, sus artículos siguen contando. Para una corrida limpia, bórrala o usa otra carpeta.

### Cómo ajustarlo

Edita las constantes al inicio de [scielo.py](scraping_simple/scielo.py):

| Constante | Efecto |
|---|---|
| `REVISTAS` | Lista de ISSN (25 revistas, unas 4 por tema: ingeniería y ciencias, medicina, psicología y educación, derecho, humanidades y ciencias sociales, economía y gestión). `[]` recorre las 61 del catálogo. |
| `MAX_POR_REVISTA` | Tope de artículos válidos por revista (80). `None` quita el tope. |
| `MIN_EXITO` | Una revista sin ningún artículo válido tras revisar 30 se abandona (por ejemplo, si está en inglés). |
| `IDIOMAS` | `['es']` por defecto. `[]` quita el filtro de idioma. |
| `ANIOS` | Lista de años. `[]` no limita. |
| `CARPETA` | Dónde se guarda todo (`data/scielo_simple`). |
| `PAUSA` | Segundos entre solicitudes (1). |
| `ROBOTS` | `'report'` avisa si robots.txt lo prohíbe y continúa. `'respect'` se detiene. |

### Salidas (`data/scielo_simple/`)

| Archivo | Contenido |
|---|---|
| `corpus.jsonl` y `corpus.csv` | Un artículo válido por fila. Es lo que se vectoriza. |
| `articulos.jsonl` | Todos los registros procesados, con su `status` (`ok`, `filtrado`, `error`) y los motivos. |
| `estado.json` | Cola del catálogo, para reanudar. |
| `report.json` | Resultado de la corrida. |

## 4. Universidades (`universidades.py`)

```bash
python scraping_simple/universidades.py --target 100
```

`--target` es el número de **tesis válidas por repositorio**.

### Opciones

| Opción | Efecto |
|---|---|
| `--target N` | Tesis válidas por repositorio (por defecto 100). |
| `--repos upch,unfv` | Solo esos repositorios (claves separadas por coma). Por defecto, todos los habilitados. |
| `--carpeta RUTA` | Carpeta de salida (por defecto `data/universidades_simple`). |
| `--config RUTA` | Configuración (por defecto `config.universidades.json`). |
| `--rediscover` | Vuelve a leer los sitemaps y añade las fichas nuevas. |
| `--list` | Lista los repositorios configurados y sale. |

```bash
python scraping_simple/universidades.py --list
```

```bash
python scraping_simple/universidades.py --repos upch,unfv,unap --target 2
```

### Qué hace

1. **Descubre** las fichas del repositorio con su sitemap (`sitemap`), su mapa HTML (`htmlmap`) o su índice paginado (`browse`). No usa el buscador del sitio.
2. **Ordena** las fichas con un hash con semilla (`sample_seed`), así la muestra no queda sesgada hacia el orden del sitemap.
3. **Descarga** cada ficha con `curl_cffi` o, en los sitios que la arman con JavaScript (`"render": true`), con Selenium.
4. **Extrae** título, resumen, autores, asesores, grado, colección, año y PDF. No guarda DNI, ORCID ni jurados.
5. Una ficha es **válida** si es tesis, tiene resumen y su idioma coincide con `languages`.
6. Pasa al siguiente repositorio al llegar al objetivo o al agotar el sitio.

No hay búsqueda por tema: se toma una muestra de todas las tesis del repositorio.

### Cómo se reparte entre repositorios

El reparto es más simple que en SciELO:

- **Entre repositorios.** Los recorre uno por uno, en el orden del archivo de configuración, y cada uno recibe el mismo objetivo (`--target`). No se rebalancea durante la corrida. Si un repositorio se agota antes, el resto no compensa la diferencia.
- **Dentro de un repositorio.** No reparte por facultad, colección, año ni grado. Ordena todas sus fichas con un hash con semilla (`sample_seed`) y las toma en ese orden, así que es una muestra pseudoaleatoria de todo el sitio, proporcional a lo que tenga cada categoría.

### Repositorios

Habilitados (11): `upch`, `unfv`, `unmsm`, `unap`, `unica`, `lamolina`, `esan`, `upn`, `uncp`, `unc`, `unheval`.

| Repositorio | Estado |
|---|---|
| `upc` | Deshabilitado. Responde 429 a las fichas renderizadas. Se ejecuta solo con `--repos upc`. |
| UNE y PUCP | Eliminados. Sus sitios activaron un antibots y el script no debe eludirlo. |

Para agregar un repositorio, añade un objeto a `repos` en [config.universidades.json](config.universidades.json). Solo hace falta `key`, `name` y `base_url`; el resto tiene valores por defecto (`discovery`, `sitemap`, `item_pattern`, `render`, `parser`, etc.). Mira las entradas de `unmsm`, `esan` o `upn` como ejemplo de reescrituras de URL y de Selenium.

### Salidas (`data/universidades_simple/`)

| Archivo | Contenido |
|---|---|
| `corpus.jsonl` y `corpus.csv` | Tesis válidas de **los repositorios de esa corrida**. Es lo que se vectoriza. |
| `summary.json` | Resumen por repositorio. |
| `<repo>/corpus.*` | Lo mismo, de un solo repositorio. |
| `<repo>/fichas.jsonl` | Todas las fichas procesadas, con su `status` (`ok`, `filtrado`, `omitido`, `no_permitido`, `error`) y motivos. |
| `<repo>/urls.json` | Fichas descubiertas, en el orden de la muestra. |
| `<repo>/report.json` | Resultado del repositorio. |

El corpus consolidado se **reescribe en cada corrida con los repositorios que se ejecutaron**. Si corres solo uno con `--repos`, el consolidado queda solo con ese. Para reconstruirlo, repite el comando completo: reanuda sin hacer peticiones nuevas.

## 5. Reanudar, detener y empezar de cero

- **Reanudar:** repite exactamente el mismo comando. El script salta lo ya descargado y no repite peticiones.
- **Detener:** `Ctrl+C` en la terminal, o `Stop-Process -Id <PID>` si va en segundo plano. El estado se conserva.
- **Empezar de cero:** borra la carpeta de salida (`data/scielo_simple` o `data/universidades_simple`).
- **Cambiar filtros:** si cambias `IDIOMAS`, `ANIOS` o las revistas (salvo ampliarlas) en SciELO, o la configuración de descubrimiento de un repositorio, el script se niega a mezclar muestras y pide otra carpeta.

### Estados de una corrida

| `status` | Significado |
|---|---|
| `passed` | Llegó al objetivo. Código de salida 0. |
| `exhausted` | Se agotó el sitio antes del objetivo. Código 2. |
| `blocked` | El sitio bloqueó (403, 429 o antibots). No se eludió. Código 2. |
| `error` | Error de red, de configuración o 10 errores seguidos. Código 2. |
| `interrupted` | Detenido a mano. Se puede reanudar. |

El objetivo es un **máximo posible, no una garantía**: el tope real de cada repositorio es cuántas de sus fichas son tesis con resumen. Por ejemplo, en UNMSM solo pasó un 20 % de las fichas revisadas.

## 6. Vectorizar los abstracts

```bash
python vectorize_data.py
```

`vectorize_data.py` lee los dos corpus consolidados nuevos: `data/scielo_simple/corpus.jsonl` y `data/universidades_simple/corpus.jsonl`. Falla con `FileNotFoundError` si falta alguno. El de universidades **se escribe al terminar la corrida**, así que hasta entonces no existe.

Para vectorizar otras rutas, por ejemplo solo SciELO, usa la función directamente:

```python
from vectorizers import vectorize_corpora

vectorize_corpora(["data/scielo_simple/corpus.jsonl"], output_dir="data")
```

Usa solo los **dos corpus consolidados**, no los de cada repositorio, para no duplicar documentos. Une los corpus, elimina duplicados por `paper_id` o por resumen idéntico, y genera en `data/`:

- `vectorizations.tfidf.jsonl` y `vectorizations.tfidf.metadata.json`
- `vectorizations.doc2vec.jsonl`

Limitaciones conocidas, que **no he medido ni corregido**:

- `tfidf` usa por defecto `topN=14`: el vector solo tiene las 14 palabras más frecuentes del corpus, que suelen ser palabras vacías.
- La función cuenta palabras con `list.count()` repetido, así que crece mucho con corpus grandes (decenas de miles de resúmenes).

## 7. Cuánto tarda y cuánto pesa

Estimaciones aproximadas para `--target 4000` en los 11 repositorios, suponiendo que todas las fichas fueran válidas. Salen de una corrida de prueba de 4 tesis por repositorio, así que tienen un margen de error amplio:

| | Estimación |
|---|---|
| Tiempo mínimo | Unas 35 horas (día y medio). UPCH pide 10 s por solicitud: son unas 11 h solo en ese repositorio. |
| Peso en disco | Hasta ~670 MB. El corpus consolidado ocupa unos 220 MB (JSONL ~120 MB y CSV ~105 MB). |
| Total de tesis | Hasta ~41 600 (4000 por repositorio, salvo UNMSM). |
| Repositorios que podrían no llegar a 4000 | UNMSM seguro (1615 fichas, y pasa cerca del 20 %). ESAN (4111 fichas) y La Molina (5146) quedan justos si algunas fichas no son tesis. |

Si más adelante quieres un target mayor, la misma carpeta sirve: repite el comando con el nuevo `--target` y continúa desde lo ya descargado.

El script espera 2 s entre solicitudes, o más si el `robots.txt` del sitio lo pide. **No lo bajes.** Si dejas la corrida larga en una laptop, evita que el equipo se suspenda.

Para seguir una corrida en segundo plano (PowerShell):

```bash
Get-Content data\universidades_run.log -Tail 20 -Wait
```

## 8. Tests

Los tests nuevos no necesitan red:

```bash
python -m pytest tests/test_scielo_simple.py tests/test_universidades_simple.py -q
```

Los tres tests antiguos (`test_benchmark.py`, `test_scielo_scraping.py`, `test_universidades.py`) importan `scielo_mvp` y `universidades_mvp`, que ya no están en el proyecto, así que fallarían.

## 9. Límites del scraping

- Se respeta `robots.txt` (en SciELO solo se avisa, según `ROBOTS`) y se usan pausas entre solicitudes.
- No se eluden CAPTCHAs, desafíos antibots ni bloqueos: ante uno, el repositorio se detiene como `blocked`.
- Las fichas contienen nombres de autores y asesores. Los campos más sensibles (DNI, ORCID, jurados) se descartan. Guarda solo lo necesario.
- Los sitios cambian: si un repositorio empieza a fallar, revisa su `report.json` y comprueba a mano su sitemap y una ficha.
