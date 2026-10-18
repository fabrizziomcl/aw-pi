# Adquisición del corpus — SciELO Perú y repositorios universitarios peruanos

Código de la Parte 1 (adquisición del corpus) del proyecto *Descubrimiento de Literatura Académica en América Latina* — Analítica Web, Universidad del Pacífico, Grupo 4.

Contiene dos extractores, ambos basados en web scraping de HTML **sin API**:

| Paquete | Fuente | Herramientas | Resultado |
|---|---|---|---|
| [`scielo_mvp/`](#a-scielo_mvp--artículos-de-scielo-perú) | SciELO Perú: 6 revistas de ingeniería y ciencias | `requests` + BeautifulSoup | 1.000 artículos en español con abstract |
| [`universidades_mvp/`](#b-universidades_mvp--tesis-de-repositorios-universitarios) | 14 repositorios universitarios peruanos | curl_cffi + BeautifulSoup + Selenium | Tesis con resumen, autores, asesores, grado y enlace al PDF |

Las etapas posteriores del informe (`docs/`), como embeddings, recomendación, tópicos y grafo, **no forman parte de este código**.

## Estructura

```text
codigo/
├── scielo_mvp/                    # Artículos de SciELO (python -m scielo_mvp.benchmark)
├── universidades_mvp/             # Tesis universitarias (python -m universidades_mvp.run); README propio
├── tests/                         # Pruebas sin red de ambos paquetes
├── docs/                          # Informe parcial (LaTeX IEEE y PDF)
├── config.peru-catalog.json       # Perfil de SciELO Perú: español, 6 revistas
├── config.universidades.json      # Perfil de los 14 repositorios (13 habilitados)
├── universidades.md               # Evaluación de qué repositorios se pueden scrapear
├── manifest.sha256.json           # Huellas SHA-256 de los módulos de scielo_mvp y su perfil
├── requirements.txt               # Dependencias de scielo_mvp (versiones fijadas)
├── requirements-universidades.txt # Lo anterior + curl_cffi y Selenium
├── requirements-dev.txt           # Todo lo anterior + pytest
└── pytest.ini
```

`data/`, `.venv/` y las cachés están excluidas por `.gitignore`.

## Inicio rápido

Python 3.10 o superior. Abre PowerShell **dentro de `codigo`**: los paquetes y las carpetas `data_dir` se resuelven respecto del directorio actual.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-universidades.txt   # sirve para ambos paquetes

# SciELO: 1.000 artículos
.\.venv\Scripts\python.exe -m scielo_mvp.benchmark --config config.peru-catalog.json --target 1000

# Tesis: 100 por repositorio habilitado (requiere Google Chrome)
.\.venv\Scripts\python.exe -m universidades_mvp.run --target 100

# Pruebas sin red
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

En Linux/macOS, usa `python3 -m venv .venv` y `.venv/bin/python` en lugar de `.\.venv\Scripts\python.exe`. Si solo necesitas SciELO, basta con `requirements.txt`.

Los dos extractores comparten el mismo comportamiento:
- se pueden **reanudar**: tras `Ctrl+C`, repite el comando;
- el **objetivo es acumulativo**;
- terminan con **código `0`** solo si alcanzan el objetivo y con `2` en cualquier otro caso (la causa queda en el informe JSON);
- tienen un modo **`--verify-only`** que audita las huellas SHA-256 y exporta sin usar la red.

---

## A. `scielo_mvp` — artículos de SciELO Perú

El flujo es **catálogo → revistas → números → artículos → HTML y metadatos → auditoría → CSV/JSONL**.

### A.1 Generar una muestra de 1.000 registros

```powershell
.\.venv\Scripts\python.exe -m scielo_mvp.benchmark --config config.peru-catalog.json --target 1000
```

La salida se escribe en **`data/test-1000/peru-catalog/`**. Cada artículo y cada página del catálogo es una solicitud, con al menos 1 segundo de pausa entre ellas: una ejecución completa suele tardar entre media hora y más de una hora, según la respuesta del portal.

- **Reanudar:** después de `Ctrl+C` o de un error, repite el mismo comando. Se conservan la cola y los documentos guardados.
- **Objetivo acumulativo:** si ya hay 1.000 válidos, solo audita y exporta, sin volver a descargar.
- **Prueba rápida:** `--target 5` recorre el mismo flujo con pocos artículos. Usa otra `data_dir` si no quieres mezclarla con la muestra principal.
- No ejecutes dos procesos sobre la misma carpeta de salida.

No se garantiza obtener los mismos documentos en una nueva ejecución: el portal puede cambiar, agotarse o bloquear solicitudes.

### A.2 Verificar y exportar sin usar la red

```powershell
.\.venv\Scripts\python.exe -m scielo_mvp.benchmark --config config.peru-catalog.json --target 1000 --verify-only
```

El resultado esperado es `status: passed`, `counts.valid_records >= 1000`, `integrity_errors: []` y código de salida `0`. La auditoría comprueba, para cada registro: HTTP 200 registrado, título, identidad, idioma de publicación, abstract compatible con el filtro y HTML conservado con SHA-256 correcto. Los artículos sin abstract y las importaciones locales no cuentan.

`--verify-only` no solicita páginas ni modifica `scielo.sqlite3`. Sí escribe `verification.json`, `verification.config.json`, las exportaciones y una entrada en `test.log`. Si la carpeta no contiene una base, termina con código `2` sin crear nada.

**Auditar una muestra existente en otra carpeta.** La muestra original de 1.000 documentos **no se incluye en esta entrega**. Si tienes una copia, colócala en `data/test-1000/peru-catalog/` o crea un perfil que apunte a su ubicación:

```powershell
$cfg = Get-Content 'config.peru-catalog.json' -Raw | ConvertFrom-Json
$cfg.data_dir = 'C:/ruta/a/peru-catalog'
$cfg | ConvertTo-Json -Depth 10 | Set-Content 'config.original.json' -Encoding UTF8
.\.venv\Scripts\python.exe -m scielo_mvp.benchmark --config config.original.json --target 1000 --verify-only
```

`config.original.json` está en `.gitignore`.

### A.3 Archivos generados

| Archivo | Contenido |
|---|---|
| `corpus.csv` | Una fila por documento validado: ID, título, abstract, autores y enlaces. UTF-8 con BOM (se abre directamente en Excel) |
| `corpus.jsonl` | El mismo corpus para procesamiento; `paper_id` enlaza con los metadatos |
| `articles.jsonl` | Registros completos aceptados, incluidos los que no tienen abstract |
| `abstracts.jsonl`, `abstracts.es.jsonl` | Versiones de los abstracts; puede haber varias por paper |
| `scielo.sqlite3` | Registros, cola y estado para reanudar |
| `raw/catalog/`, `raw/articles/` | HTML original comprimido como `.html.gz` |
| `report.json`, `verification.json` | Resultado de la extracción o de la auditoría |
| `config.snapshot.json`, `run.config.json`, `verification.config.json` | Configuración de la primera extracción, de la última extracción y de la última auditoría |
| `test.log` | Historial de ejecución |

Se guardan **enlaces** PDF/XML cuando aparecen en la página; este paquete no descarga esos archivos.

### A.4 Configuración

`config.peru-catalog.json` define la muestra: colección Perú (`per`), artículos en español y cualquier año, en seis revistas seleccionadas por ISSN:

| Revista | ISSN |
|---|---|
| Industrial Data | `1810-9993` |
| Ingeniería Industrial | `2523-6326` |
| Tecnia | `2309-0413` |
| Revista de la Sociedad Química del Perú | `1810-634X` |
| Biblios | `1562-4730` |
| Ecología Aplicada | `1726-2216` |

| Parámetro | Valor | Efecto |
|---|---|---|
| `discovery` | `catalog` | Recorre catálogo → revistas → números → artículos (`search` usa el buscador central) |
| `catalog_journals` | 6 ISSN | `[]` recorre todas las revistas del catálogo |
| `languages` | `["es"]` | `[]` elimina el filtro de idioma |
| `years` | `[]` | Admite todos los años; por ejemplo `["2024", "2025"]` |
| `delay_seconds`, `timeout_seconds`, `retries` | `1`, `40`, `2` | Pausa entre solicitudes, tiempo de espera y reintentos |
| `max_pages`, `max_articles` | `5`, `25` | Tamaño de los lotes internos; el total lo controla `--target` |
| `robots_policy` | `report` | Registra las restricciones de robots.txt y continúa; `respect` las aplica |
| `data_dir` | `data/test-1000/peru-catalog` | Carpeta de salida |

En modo catálogo, `query`, `wos_areas`, `scielo_areas` y `document_types` deben quedar vacíos: la selección por revistas **no equivale a las facetas de áreas WoS** del buscador central.

Si cambias los filtros, usa otra `data_dir`: el programa rechaza mezclar muestras con filtros distintos. La única excepción es añadir revistas a `catalog_journals`, que amplía la muestra conservando los registros previos.

`robots_policy: report` se conserva por reproducibilidad del perfil histórico y no implica autorización del portal. Los bloqueos HTTP 401/403/429 se informan como tales, sin mecanismos de evasión. Para un uso distinto del académico, se recomienda `respect`.

### A.5 Módulos

| Módulo | Responsabilidad |
|---|---|
| `benchmark.py` | Punto de entrada, bucle reanudable, informes y objetivo de N registros |
| `config.py` | Lectura y validación de parámetros |
| `catalog.py` | Navegación de revistas, números y enlaces a artículos |
| `http.py` | Peticiones, pausas, reintentos, robots.txt y detección de bloqueos |
| `pipeline.py` | Descubrimiento, extracción y persistencia de artículos |
| `parsers.py` | Interpretación del HTML: abstracts, metadatos e identidad |
| `storage.py` | SQLite, cola, HTML comprimido y trazabilidad |
| `validation.py` | Criterios de aceptación y auditoría de integridad |
| `exports.py` | Exportación de corpus y metadatos |

Varias funciones de `scielo_mvp` (limpieza de texto, códigos de idioma, `Retry-After`, escritura atómica) también las usa `universidades_mvp`.

### A.6 Manifiesto

`manifest.sha256.json` registra las huellas de los módulos de `scielo_mvp` y de su perfil. Para comprobarlas:

```powershell
.\.venv\Scripts\python.exe -c "import json,hashlib,pathlib; m=json.loads(pathlib.Path('manifest.sha256.json').read_text(encoding='utf-8')); [print(f['file'], 'OK' if hashlib.sha256(pathlib.Path(f['file']).read_bytes()).hexdigest()==f['sha256'] else 'DIFERENTE') for f in m['files']]"
```

Si modificas un módulo, el manifiesto dejará de coincidir: regenéralo o indica el cambio.

---

## B. `universidades_mvp` — tesis de repositorios universitarios

La documentación completa está en [`universidades_mvp/README.md`](universidades_mvp/README.md), y la evaluación de cada sitio en [`universidades.md`](universidades.md). En resumen:

```powershell
.\.venv\Scripts\python.exe -m universidades_mvp.run --list                         # repositorios configurados
.\.venv\Scripts\python.exe -m universidades_mvp.run --target 100                   # todos los habilitados
.\.venv\Scripts\python.exe -m universidades_mvp.run --repos upch,unap --target 50  # solo algunos
.\.venv\Scripts\python.exe -m universidades_mvp.run --target 100 --verify-only     # auditar sin red
```

- **Repositorios:** se descargan con curl_cffi en UPCH, UNFV, UNMSM, PUCP, UNE, UNAP y UNICA, y con Selenium en La Molina, ESAN, UPN, UNCP, UNC, UNHEVAL y UPC. Las diferencias entre sitios están en `config.universidades.json`, no en el código.
- **Solo tesis:** se clasifican por `dc.type`, `renati.type` o el grado. Se descartan artículos, sílabos y monografías, y el motivo queda en `items.jsonl`.
- **Salida:** `data/universidades/corpus.csv` y `corpus.jsonl` consolidados, más un corpus, un `report.json` y la auditoría SHA-256 de cada repositorio.
- **UPC está deshabilitado por defecto:** respondió HTTP 429 (Too Many Requests) a las fichas renderizadas. Solo se ejecuta con `--repos upc`.
- **Uso responsable:**
  - robots.txt se respeta siempre, con comodines y `Crawl-delay` (UPCH y UNE piden 10 s entre solicitudes);
  - un desafío antibots o un 401/403/429 detiene el repositorio, sin evasión;
  - se descartan DNI, ORCID y jurados.
  - El HTML guardado en `raw/` sí conserva esos datos: **no lo compartas**.

---

## Pruebas

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

Son 16 pruebas, ninguna usa la red:
- **`tests/test_benchmark.py` (4, SciELO):** carga del perfil, auditoría que excluye importaciones locales y detecta HTML alterado, ejecución bloqueada no reportada como aprobada y `--verify-only` sin crear carpetas.
- **`tests/test_universidades.py` (12, tesis):**
  - robots.txt con comodines;
  - las fuentes de metadatos y el descarte de DNI;
  - la clasificación de tesis;
  - el descubrimiento por sitemap e índice;
  - las páginas de error y bloqueos;
  - una ejecución completa con auditoría.

## Problemas frecuentes

| Síntoma | Causa y solución |
|---|---|
| `No module named scielo_mvp` / `universidades_mvp` | El comando no se ejecutó desde `codigo` |
| `No existe una base para verificar` | `--verify-only` apunta a una `data_dir` sin base SQLite; ejecuta antes una extracción o corrige `data_dir` |
| `Los filtros del catálogo cambiaron` / `La configuración de descubrimiento cambió` | La `data_dir` contiene una muestra con otros filtros; usa otra carpeta |
| `status: blocked` | El sitio respondió 401/403/429 o mostró un desafío antibots; espera y repite el comando para reanudar |
| `curl_cffi`: *"El nombre del archivo o la extensión es demasiado largo"* (Windows) | La ruta del `.venv` es muy larga; créalo en una ruta más corta |
| `No se pudo iniciar Chrome con Selenium` | Instala Google Chrome; Selenium descarga su driver automáticamente |
| Tildes mal mostradas al redirigir la salida | Ejecuta `$env:PYTHONUTF8 = '1'` antes del comando. Solo afecta a los mensajes; los archivos se escriben siempre en UTF-8 |

## Comprobaciones de esta entrega

- **22 de septiembre de 2026 (SciELO, paquete original):** los módulos y el perfil coincidían por SHA-256 con sus fuentes. La auditoría de solo lectura de la muestra original encontró **1.000 documentos válidos**, 1.068 HTML íntegros y **cero errores de integridad**. Esa muestra no se incluye en esta carpeta.
- **8 de octubre de 2026 (revisión del repositorio):**
  - Con Python 3.14, las huellas del manifiesto coinciden y pasan las 16 pruebas.
  - **SciELO:** una extracción real con `--target 2` terminó con `status: passed`, y su `--verify-only` también.
  - **Tesis:** con `--target 2`, los 14 repositorios terminaron con `status: passed` (28 tesis en 26 minutos), y el `--verify-only` posterior también. Después se corrigieron UNHEVAL (clasificación de tesis) y la captura de PDF en los sitios con Selenium; la segunda prueba de esos sitios pasó, salvo UPC (HTTP 429), que quedó deshabilitado.

Estas comprobaciones verifican el código en esas fechas; no garantizan la disponibilidad futura de los portales.
