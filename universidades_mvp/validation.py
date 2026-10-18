"""Criterios compartidos para aceptar, auditar y exportar tesis."""

import gzip
import hashlib
import zlib
from collections import Counter


def qualification_errors(record, cfg):
    errors = []
    transport = record.get("transport") or {}
    if not (
        transport.get("mode") == "http" and transport.get("http_status") == 200
        or transport.get("mode") == "selenium"
    ):
        errors.append("not_fetched")
    if not str(record.get("title") or "").strip():
        errors.append("title_missing")
    if not str(record.get("abstract") or "").strip():
        errors.append("abstract_missing")
    if cfg.thesis_only and not record.get("is_thesis"):
        errors.append("not_thesis")
    # Sin idioma declarado ni detectable no se excluye: queda el aviso language_unknown.
    if cfg.languages and record.get("language") and record["language"] not in cfg.languages:
        errors.append("language_mismatch")
    if not record.get("paper_id") or not record.get("url"):
        errors.append("identity_missing")
    return errors


def audit(store, cfg):
    counts, years = Counter(), Counter()
    problems, excluded, valid_ids = [], [], []
    for row in store.db.execute("SELECT status FROM items"):
        counts[row["status"]] += 1
    for key, status, record in store.records("done", "filtered"):
        try:
            if not isinstance(record, dict):
                raise ValueError("El registro no es un objeto JSON")
            reasons = qualification_errors(record, cfg)
            if record.get("paper_id") != key:
                reasons.append("identity_mismatch")
            raw_path = (store.root / record["raw_html_path"]).resolve()
            if not raw_path.is_relative_to(store.root):
                raise ValueError("HTML fuera de la carpeta del repositorio")
            if hashlib.sha256(gzip.decompress(raw_path.read_bytes())).hexdigest() != record["html_sha256"]:
                raise ValueError("SHA-256 diferente")
            counts["html_integrity_ok"] += 1
            if reasons:
                excluded.append({"id": key, "reasons": reasons})
                continue
            valid_ids.append(key)
            years[record.get("year") or "unknown"] += 1
        except (OSError, ValueError, TypeError, KeyError, EOFError, zlib.error) as exc:
            problems.append({"id": key, "error": str(exc)})
    counts["valid_theses"] = len(valid_ids)
    for key in ("pending", "done", "filtered", "skipped", "error", "blocked", "disallowed", "html_integrity_ok"):
        counts.setdefault(key, 0)
    reasons = Counter(reason for item in excluded for reason in item["reasons"])
    return {
        "counts": dict(counts),
        "exclusion_reasons": dict(reasons),
        "years": {str(k): v for k, v in sorted(years.items(), key=lambda kv: str(kv[0]))},
        "integrity_errors": problems,
        "valid_ids": valid_ids,
    }
