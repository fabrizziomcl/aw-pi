"""Criterios compartidos para contar, auditar y exportar el corpus de Parte 1."""

import gzip
import hashlib
import json
import zlib
from collections import Counter


def select_abstract(record, languages=()):
    """Una versión por documento, siguiendo el orden de idiomas configurado."""
    abstracts = [
        a for a in record.get("abstracts", []) if isinstance(a, dict) and str(a.get("text") or "").strip()
    ]
    if languages:
        for language in languages:
            for abstract in abstracts:
                if abstract.get("language") == language:
                    return abstract
        return None
    return next(
        (a for a in abstracts if a.get("text") == record.get("abstract")), abstracts[0] if abstracts else None
    )


def qualification_errors(record, cfg=None):
    errors = []
    transport = record.get("transport") or {}
    if transport.get("mode") != "http" or transport.get("http_status") != 200:
        errors.append("not_http_200")
    if not str(record.get("title") or "").strip():
        errors.append("title_missing")
    languages = cfg.languages if cfg else []
    if languages and not set(languages).intersection(record.get("languages") or []):
        errors.append("publication_language_mismatch")
    if not select_abstract(record, languages):
        errors.append("abstract_missing_or_language_mismatch")
    if not record.get("url") or not record.get("id"):
        errors.append("identity_missing")
    return errors


def count_ready(store, cfg=None):
    """Cuenta candidatos; la auditoría final comprueba además el HTML guardado."""
    ready = 0
    for row in store.db.execute("SELECT record_json FROM articles WHERE status='done'"):
        record = json.loads(row[0])
        ready += not qualification_errors(record, cfg)
    return ready


def audit(store, cfg=None):
    counts, languages, journals = Counter(), Counter(), Counter()
    problems, rejected, valid_ids = [], [], []
    qualified = Counter()
    for row in store.db.execute("SELECT id,status,record_json FROM articles ORDER BY id"):
        counts[row["status"]] += 1
        if row["status"] != "done":
            continue
        try:
            record = json.loads(row["record_json"])
            if not isinstance(record, dict):
                raise ValueError("El registro no es un objeto JSON")
            reasons = qualification_errors(record, cfg)
            if record.get("id") != row["id"]:
                reasons.append("identity_mismatch")
            counts["http_records"] += record.get("transport", {}).get("mode") == "http"
            for key, field in (
                ("with_abstract", "abstract"),
                ("with_pdf_link", "pdf_url"),
                ("with_xml_link", "xml_url"),
                ("with_doi", "doi"),
            ):
                counts[key] += bool(record.get(field))
            languages.update(record.get("languages") or ["unknown"])
            journals.update([record.get("journal") or "unknown"])
            raw_path = (store.root / record["raw_html_path"]).resolve()
            if not raw_path.is_relative_to(store.root):
                raise ValueError("HTML fuera de data_dir")
            content = gzip.decompress(raw_path.read_bytes())
            if hashlib.sha256(content).hexdigest() != record["html_sha256"]:
                raise ValueError("SHA-256 diferente")
            counts["html_integrity_ok"] += 1
            if reasons:
                rejected.append({"id": row["id"], "reasons": reasons})
                continue
            valid_ids.append(row["id"])
            for key, field in (
                ("with_pdf_link", "pdf_url"),
                ("with_xml_link", "xml_url"),
                ("with_doi", "doi"),
            ):
                qualified[key] += bool(record.get(field))
        except (OSError, ValueError, TypeError, KeyError, AttributeError, EOFError, zlib.error) as exc:
            problems.append({"id": row["id"], "error": str(exc)})
    counts["valid_records"] = len(valid_ids)
    for key in (
        "done",
        "pending",
        "error",
        "blocked",
        "filtered",
        "valid_records",
        "http_records",
        "html_integrity_ok",
        "with_abstract",
        "with_pdf_link",
        "with_xml_link",
        "with_doi",
    ):
        counts.setdefault(key, 0)
    return {
        "counts": dict(counts),
        "languages": dict(languages),
        "journals": dict(journals),
        "qualified_counts": {key: qualified[key] for key in ("with_pdf_link", "with_xml_link", "with_doi")},
        "integrity_errors": problems,
        "excluded_from_corpus": rejected,
        "valid_ids": valid_ids,
    }
