"""Exportaciones atómicas JSONL y CSV; el corpus contiene una fila por paper."""

import csv
import json
import logging
from contextlib import contextmanager
from pathlib import Path

from .storage import dumps
from .validation import select_abstract

LOG = logging.getLogger("scielo")
CORPUS_FIELDS = [
    "paper_id",
    "pid",
    "title",
    "abstract",
    "language",
    "publication_languages",
    "authors",
    "doi",
    "journal",
    "year",
    "url",
    "pdf_url",
    "xml_url",
    "source",
    "collection",
    "discovered_from",
    "fetched_at",
    "raw_html_path",
    "html_sha256",
]


@contextmanager
def output_stream(path, *, csv_format=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    try:
        with temp.open("w", encoding="utf-8-sig" if csv_format else "utf-8", newline="") as stream:
            yield stream
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def records(store):
    for row in store.db.execute("SELECT record_json FROM articles WHERE status='done' ORDER BY id"):
        yield json.loads(row[0])


def export_records(store, output, abstracts_only=False, language_filter=None):
    """Formato histórico: todos los metadatos o una fila por versión del abstract."""
    count = 0
    with output_stream(output) as stream:
        for record in records(store):
            if not abstracts_only:
                stream.write(dumps(record) + "\n")
                count += 1
                continue
            for index, abstract in enumerate(record.get("abstracts", [])):
                if language_filter and abstract["language"] != language_filter:
                    continue
                result = {
                    key: record.get(key)
                    for key in ("id", "pid", "url", "title", "doi", "journal", "year", "pdf_url", "xml_url")
                }
                result.update(
                    {
                        "paper_id": record["id"],
                        "abstract_id": f"{record['id']}:{index}",
                        "abstract": abstract["text"],
                        "language": abstract["language"],
                        "source": abstract["source"],
                    }
                )
                stream.write(dumps(result) + "\n")
                count += 1
    LOG.info("Exportados %s registros a %s", count, Path(output).resolve())
    return count


def corpus_row(record, languages):
    abstract = select_abstract(record, languages)
    seed = record.get("discovery") or {}
    row = {key: record.get(key) for key in CORPUS_FIELDS}
    row.update(
        {
            "paper_id": record["id"],
            "abstract": abstract["text"],
            "language": abstract["language"],
            "publication_languages": record.get("languages", []),
            "source": "scielo",
            "collection": seed.get("collection"),
            "discovered_from": seed.get("source_url"),
        }
    )
    return row


def export_corpus(store, cfg, valid_ids, output):
    """Solo IDs validados por audit; CSV plano y JSONL comparten contenido."""
    path = Path(output)
    if path.suffix.lower() not in (".csv", ".jsonl"):
        raise ValueError("El corpus se exporta a .csv o .jsonl")
    allowed = set(valid_ids)
    count = 0
    csv_format = path.suffix.lower() == ".csv"
    with output_stream(path, csv_format=csv_format) as stream:
        writer = csv.DictWriter(stream, fieldnames=CORPUS_FIELDS) if csv_format else None
        if writer:
            writer.writeheader()
        for record in records(store):
            if record["id"] not in allowed:
                continue
            row = corpus_row(record, cfg.languages)
            if writer:
                writer.writerow(
                    {
                        key: dumps(value) if isinstance(value, (list, dict)) else value
                        for key, value in row.items()
                    }
                )
            else:
                stream.write(dumps(row) + "\n")
            count += 1
    LOG.info("Corpus: %s documentos únicos en %s", count, path.resolve())
    return count


def export_bundle(store, cfg, valid_ids):
    outputs = {}
    for filename, abstracts_only, language in [
        ("articles.jsonl", False, None),
        ("abstracts.jsonl", True, None),
    ]:
        outputs[filename] = export_records(store, store.root / filename, abstracts_only, language)
    for language in cfg.languages:
        filename = f"abstracts.{language}.jsonl"
        outputs[filename] = export_records(store, store.root / filename, True, language)
    for filename in ("corpus.jsonl", "corpus.csv"):
        outputs[filename] = export_corpus(store, cfg, valid_ids, store.root / filename)
    return outputs
