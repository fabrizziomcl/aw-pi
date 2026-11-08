# universidades_mvp

Scraper de tesis de los repositorios universitarios definidos en
[`../config.universidades.json`](../config.universidades.json).

## Flujo

Desde la raíz del proyecto:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install -r requirements-vectorizers.txt
.venv/bin/python -m universidades_mvp.run --list
.venv/bin/python -m universidades_mvp.run --target 2
```

`--target 2` obtiene dos tesis válidas por repositorio habilitado. Para la
muestra completa usa el número deseado, por ejemplo `--target 100`.

```bash
.venv/bin/python -m universidades_mvp.run \
  --repos upch,pucp,unap \
  --target 2
```

El scraping se puede reanudar repitiendo el comando. `--verify-only` audita
los archivos guardados sin conectarse a Internet. UPC está deshabilitado por
defecto debido a HTTP 429.

Chrome o Chromium es necesario para los repositorios configurados con
`render: true` (Selenium). El programa respeta `robots.txt` y sus pausas.

## Salidas

```text
data/universidades/corpus.jsonl
data/universidades/corpus.csv
data/universidades/<repo>/corpus.jsonl
data/universidades/<repo>/items.jsonl
data/universidades/<repo>/report.json
```

El campo `abstract` contiene el resumen y `paper_id` identifica cada tesis.
Después de generar los corpus de SciELO y universidades, ejecuta desde la
raíz `python vectorize_data.py`. La vectorización se hace una sola vez sobre
los corpus consolidados, elimina duplicados por `paper_id` o por abstract
exactamente igual y las salidas globales quedan en `data/`; no se deben
vectorizar los subdirectorios individualmente.

## Pruebas

```bash
.venv/bin/python -m pytest -q
```
