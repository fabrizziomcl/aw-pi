"""Exportaciones atómicas por repositorio y consolidadas; el corpus contiene una fila por tesis."""

import csv
import logging
from pathlib import Path

from scielo_mvp.exports import output_stream
from scielo_mvp.storage import dumps

LOG = logging.getLogger("universidades")
CORPUS_FIELDS = [
    "paper_id",
    "repo",
    "university",
    "title",
    "abstract",
    "abstract_language",
    "authors",
    "advisors",
    "year",
    "date_issued",
    "language",
    "document_type",
    "renati_type",
    "renati_level",
    "degree_name",
    "degree_level",
    "degree_discipline",
    "degree_grantor",
    "ocde",
    "keywords",
    "publisher",
    "collection",
    "handle",
    "url",
    "pdf_url",
    "fetched_at",
    "raw_html_path",
    "html_sha256",
]


def corpus_row(record, prefix=""):
    row = {key: record.get(key) for key in CORPUS_FIELDS}
    row["raw_html_path"] = prefix + str(record.get("raw_html_path") or "")
    return row


def write_rows(path, rows):
    """rows: iterable de diccionarios con CORPUS_FIELDS. Escribe .csv o .jsonl."""
    path = Path(path)
    csv_format = path.suffix.lower() == ".csv"
    count = 0
    with output_stream(path, csv_format=csv_format) as stream:
        writer = csv.DictWriter(stream, fieldnames=CORPUS_FIELDS) if csv_format else None
        if writer:
            writer.writeheader()
        for row in rows:
            values = {k: dumps(v) if isinstance(v, (list, dict)) else v for k, v in row.items()}
            if writer:
                writer.writerow(values)
            else:
                stream.write(dumps(row) + "\n")
            count += 1
    return count


def valid_rows(store, valid_ids, prefix=""):
    allowed = set(valid_ids)
    return [
        corpus_row(record, prefix)
        for key, _, record in store.records("done", "filtered")
        if key in allowed
    ]


def export_repo(store, valid_ids):
    outputs = {}
    with output_stream(store.root / "items.jsonl") as stream:
        count = 0
        for _, status, record in store.records("done", "filtered"):
            stream.write(dumps({"status": status, **record}) + "\n")
            count += 1
    outputs["items.jsonl"] = count
    rows = valid_rows(store, valid_ids)
    for name in ("corpus.jsonl", "corpus.csv"):
        outputs[name] = write_rows(store.root / name, rows)
    LOG.info("Corpus de %s: %s tesis en %s", store.root.name, len(rows), store.root)
    return outputs


def export_combined(root, stores_and_ids):
    """Une los corpus de cada repositorio; raw_html_path queda relativo a data_dir."""
    rows = []
    for store, valid_ids in stores_and_ids:
        rows.extend(valid_rows(store, valid_ids, store.root.name + "/"))
    outputs = {name: write_rows(Path(root) / name, rows) for name in ("corpus.jsonl", "corpus.csv")}
    LOG.info("Corpus consolidado: %s tesis en %s", len(rows), Path(root).resolve())
    return outputs
