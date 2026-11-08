# AW-PI

Extractor de abstracts y tesis para el proyecto de literatura académica.
Ejecuta los comandos desde la raíz del repositorio.

## 1. Instalar

Se recomienda Python 3.12. Python 3.14 no es compatible actualmente con
`gensim`.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install -r requirements-vectorizers.txt
```

Para los repositorios que usan Selenium también se necesita Chrome o Chromium.

## 2. Ejecutar el scraping

### SciELO

```bash
.venv/bin/python -m scielo_mvp.benchmark \
  --config config.peru-catalog.json \
  --target 1000
```

Para una prueba rápida usa `--target 2`. La salida queda en
`data/test-1000/peru-catalog/`.

### Repositorios universitarios

```bash
.venv/bin/python -m universidades_mvp.run --target 100
```

Esto solicita 100 tesis válidas por cada repositorio habilitado. Para probar
dos por repositorio usa `--target 2`. Para limitar el conjunto:

```bash
.venv/bin/python -m universidades_mvp.run \
  --repos upch,unfv,unap \
  --target 2
```

El programa respeta `robots.txt`, guarda el estado en SQLite y se puede
reanudar repitiendo el mismo comando después de una interrupción. UPC está
deshabilitado por defecto por sus respuestas HTTP 429.

## 3. Vectorizar los abstracts

Después del scraping, los dos corpus consolidados contienen una fila por
documento:

```text
data/test-1000/peru-catalog/corpus.jsonl
data/universidades/corpus.jsonl
```

`abstract` es el texto y `paper_id` permite relacionar cada vector con su
documento original. Las funciones de [vectorizers.py](./vectorizers.py)
reciben `list[list[str]]`, es decir, documentos ya tokenizados.

Para vectorizar todos los papers juntos ejecuta:

```bash
.venv/bin/python vectorize_data.py
```

El comando combina SciELO y universidades, elimina automáticamente las filas
repetidas por `paper_id` o por abstract exactamente igual, y recién después
entrena los modelos. Genera
únicamente en `data/`:

```text
data/vectorizations.tfidf.jsonl
data/vectorizations.tfidf.metadata.json
data/vectorizations.doc2vec.jsonl
```

Ejemplo equivalente en Python:

```python
import json
import re
from pathlib import Path

from vectorizers import vectorize_corpora

vectorize_corpora(
    [
        "data/test-1000/peru-catalog/corpus.jsonl",
        "data/universidades/corpus.jsonl",
    ],
    output_dir="data",
)
```

No ejecutes la vectorización sobre los `corpus.jsonl` de cada subdirectorio:
son subconjuntos de los dos corpus consolidados y producirían archivos
duplicados. La deduplicación se hace por `paper_id` o por el texto exacto del
abstract; se conserva la primera aparición. En corpus pequeños se usa
`min_count=1`; para corpus grandes puede subirse ese valor.

## 4. Comprobar el proyecto

```bash
.venv/bin/python -m pytest -q
```

Los archivos de `data/` son salidas locales y están excluidos de Git.
