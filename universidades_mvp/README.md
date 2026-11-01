# universidades_mvp — tesis de 14 repositorios universitarios peruanos

Extrae fichas de tesis (título, resumen, autores, asesores, grado, fecha, tipo y enlace al PDF) de los 14 repositorios definidos en [`../config.universidades.json`](../config.universidades.json). Usa web scraping de HTML con **curl_cffi, BeautifulSoup y Selenium**, sin APIs. Reutiliza piezas de `scielo_mvp`: limpieza de texto, códigos de idioma, `Retry-After` y escritura atómica.

Un **solo comando** recorre los sitios. Las diferencias entre ellos se declaran en `config.universidades.json`, no en el código. Por defecto se ejecutan 13: UPC está deshabilitado por los límites de solicitudes que aplica (ver §5).

## 1. Instalar

Desde la raíz del repositorio, con Python 3.10 o superior y **Google Chrome** instalado (Selenium descarga su driver automáticamente):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-universidades.txt
.\.venv\Scripts\python.exe -m universidades_mvp.run --list
```

> **Windows:** si `curl_cffi` falla con *"El nombre del archivo o la extensión es demasiado largo"*, la ruta del entorno virtual es muy larga. Crea el `.venv` en una ruta más corta.

## 2. Ejecutar

```powershell
# 100 tesis válidas por repositorio (por defecto), en los 13 sitios habilitados
.\.venv\Scripts\python.exe -m universidades_mvp.run --target 100

# Solo algunos repositorios
.\.venv\Scripts\python.exe -m universidades_mvp.run --repos upch,pucp,unap --target 50

# Auditar y volver a exportar sin usar la red
.\.venv\Scripts\python.exe -m universidades_mvp.run --target 100 --verify-only
```

| Opción | Efecto |
|---|---|
| `--target N` | Tesis válidas por repositorio. Es acumulativo: repetir el comando continúa donde quedó |
| `--repos a,b` | Limita la ejecución a esas claves (ver `--list`) |
| `--verify-only` | Audita las huellas SHA-256 y exporta sin solicitar páginas |
| `--rediscover` | Vuelve a leer los sitemaps y añade a la cola las fichas nuevas |

- **Reanudar:** después de `Ctrl+C`, repite el comando. Se conservan la cola y las fichas guardadas.
- **Códigos de salida:** `0` si todos los repositorios alcanzaron el objetivo y `2` en cualquier otro caso. La causa queda en `report.json` y `summary.json`.
- **Muestra:** la cola se ordena de forma pseudoaleatoria con `sample_seed`, para que las primeras N tesis no dependan del orden del sitemap. Con la misma semilla, el orden es reproducible.

## 3. Cómo funciona

```text
descubrir (sitemap / índice)  →  cola SQLite  →  ficha (curl_cffi o Selenium)  →  parser común  →  auditoría  →  CSV/JSONL
```

1. **Descubrimiento**, según `discovery`:
   - `sitemap`: sitemaps XML de DSpace 7+ (13 repositorios).
   - `htmlmap`: mapa HTML de DSpace 6 (UNMSM).
   - `browse`: índice por fecha de DSpace 6, paginado con `offset`; alternativa para un DSpace 6 sin `htmlmap`.

   No se usan `/search` ni la API REST: robots.txt suele prohibirlos.
2. **Descarga de la ficha:**
   - Con `render: false`, con curl_cffi. El servidor entrega el HTML completo.
   - Con `render: true`, con Selenium (Chrome headless). La interfaz arma la ficha con JavaScript. Chrome solo se inicia si algún repositorio lo necesita.
3. **Parser común** (`parsers.py`), que combina estas fuentes en orden de prioridad:
   1. el estado JSON de DSpace Angular (`dspace-angular-state`), con todos los metadatos;
   2. la tabla de la vista completa (`/full` o `?show=full`);
   3. las etiquetas DC de DSpace 6;
   4. los rótulos de las interfaces Material UI (UPN y UNHEVAL);
   5. las etiquetas `citation_*` y la meta description.
4. **¿Es tesis?** Sí cuando se cumple, en este orden:
   - `dc.type` es una tesis (`bachelorThesis`, `masterThesis`, `doctoralThesis` o el código COAR equivalente);
   - `renati.type` es tesis, trabajo de suficiencia profesional, trabajo académico o trabajo de investigación;
   - **solo si no hay tipo declarado:** existe `thesis.degree.name` o la colección lo indica.
5. **Validación:** una tesis es válida si la descarga fue exitosa y tiene título, resumen, tipo tesis e idioma compatible con `languages`. La auditoría comprueba además el SHA-256 del HTML guardado.

### Perfil de cada repositorio

| Clave | Descarga | Descubrimiento | Particularidad |
|---|---|---|---|
| `upch`, `unfv`, `une`, `unap`, `unica` | curl_cffi | Sitemap | Estado de Angular en el HTML |
| `pucp` | curl_cffi | `/server/sitemaps/sitemap_index.xml` | El sitemap estándar da 404 |
| `unmsm` | curl_cffi | `/htmlmap` (DSpace 6) | Ficha con `?show=full`. Repositorio pequeño (unas 1.600 fichas) |
| `lamolina`, `unc` | Selenium | Sitemap | Vista `/full` |
| `uncp` | Selenium | `/server/sitemaps/` | El sitemap del frontend llega vacío; se reescribe la ruta. Vista `/full` |
| `esan` | Selenium | Sitemap | Se reemplaza `localhost:4000` por el dominio real. Vista `/full` |
| `upc` | Selenium | Sitemap | **Deshabilitado por defecto** (HTTP 429). robots.txt prohíbe `/full`; usa el estado de Angular de la ficha normal |
| `upn`, `unheval` | Selenium | Sitemap del backend | Se cambia `/items/` por `/item/`; parser `mui` |

### Agregar un repositorio

Si es un DSpace 7+ con HTML desde el servidor, basta con agregar a `repos`:

```json
{"key": "nueva", "name": "Universidad Nueva", "base_url": "https://repositorio.nueva.edu.pe"}
```

Si la ficha llega vacía sin navegador, agrega `"render": true, "item_suffix": "/full", "wait_for": "table td"`. Prueba primero con `--repos nueva --target 2`.

## 4. Archivos generados

En `data/universidades/` (configurable con `data_dir`):

| Archivo | Contenido |
|---|---|
| `corpus.csv`, `corpus.jsonl` | Tesis válidas de todos los repositorios; una fila por tesis. CSV en UTF-8 con BOM |
| `summary.json` | Resultado global y estado de cada repositorio |
| `universidades.log` | Historial de ejecución |
| `<clave>/corpus.csv`, `<clave>/corpus.jsonl` | Tesis válidas del repositorio |
| `<clave>/items.jsonl` | Todas las fichas procesadas, incluidas las excluidas y el motivo (`exclusion`) |
| `<clave>/universidades.sqlite3` | Cola y estado para reanudar |
| `<clave>/raw/` | HTML original comprimido (`.html.gz`) |
| `<clave>/report.json` | Conteos, motivos de exclusión, años y errores de integridad |

Estados de la cola: `pending`, `done` (tesis válida), `filtered` (ficha guardada pero excluida), `skipped` (no era una ficha), `error`, `disallowed` (robots.txt) y `blocked`.

## 5. Límites y uso responsable

- **robots.txt se respeta siempre**, con comodines (`*`, `$`) y `Crawl-delay`. UPCH, UPC y UNE piden 10 s entre solicitudes, así que 1.000 tesis de UPC toman al menos unas 3 horas, más las fichas excluidas.
- **Bloqueos:** un desafío antibots (Anubis, Cloudflare) o un HTTP 401/403/429 detiene ese repositorio con `status: blocked`. No hay mecanismos de evasión.
- **curl_cffi** se presenta como Chrome (`impersonate`). Ninguno de los 14 sitios lo exige; con `"impersonate": ""` se usa el cliente sin imitación.
- **UPC está deshabilitado por defecto** (`"enabled": false`). En la prueba del 8 de octubre de 2026 respondió **HTTP 429 (Too Many Requests)** después de unas pocas fichas, aunque se respetaban sus 10 s entre fichas: cada ficha renderizada genera muchas solicitudes internas a `/server/api`, una ruta que además su robots.txt prohíbe a rastreadores. Solo se ejecuta si se pide explícitamente con `--repos upc`, y conviene hacerlo con objetivos pequeños.
- **Errores 429/403/401 con Selenium:** como Selenium no expone el código HTTP, el programa reconoce esas páginas de error por su contenido y detiene el repositorio de inmediato (`blocked`), sin reintentar.
- **Datos personales:** se descartan DNI, ORCID y jurados (`renati.*`) aunque estén en la página. Se guardan nombres de autores y asesores porque forman parte de la referencia bibliográfica. El HTML original guardado en `raw/` sí los contiene: no lo compartas.
- **PDF:** `pdf_url` puede quedar vacío. Las tesis con embargo no tienen PDF público, y PUCP no incluye enlaces de descarga en el HTML que entrega el servidor. Este paquete guarda el enlace, pero no descarga el PDF.
- **Disponibilidad:** los sitios pueden cambiar de estructura. Si un repositorio deja de funcionar, revisa su `report.json` y prueba con `--target 2`.

## Comprobaciones (8 de octubre de 2026)

- **Pruebas sin red:** 12 pruebas en `tests/test_universidades.py` (robots.txt con comodines, los cinco tipos de fuente de metadatos, descarte de DNI, clasificación de tesis, descubrimiento, páginas de error, ejecución completa y auditoría). Se corren con `python -m pytest`.
- **Prueba en vivo con `--target 2`:** los 14 repositorios terminaron con `status: passed` (28 tesis válidas en 26 minutos) y el `--verify-only` posterior también.
- **Ajustes tras esa prueba:**
  - En UNHEVAL se esperaba poco a la ficha, así que no aparecía la colección y se descartaban fichas que sí eran tesis. Ahora se espera el enlace a la colección: 5 de 5 válidas, sin descartes.
  - En los sitios con Selenium se agregó una espera breve para los enlaces a archivos: La Molina pasó de 0/5 a 3/3 tesis con PDF.
  - UPC respondió HTTP 429 en una segunda prueba y quedó deshabilitado por defecto.
- **Segunda prueba en vivo con `--target 3`** de los repositorios con Selenium: La Molina, ESAN, UPN, UNCP, UNC y UNHEVAL pasaron. UPC quedó en 2 de 3 por el HTTP 429.

Estas pruebas verifican el código y su funcionamiento en esa fecha; no garantizan que los repositorios mantengan su estructura.
