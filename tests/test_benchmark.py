"""Pruebas sin red del ejecutor y de la auditoría."""

import hashlib
import json
from pathlib import Path

from scielo_mvp import benchmark
from scielo_mvp.config import Config
from scielo_mvp.http import Blocked
from scielo_mvp.storage import Store
from scielo_mvp.validation import audit

PROFILE = Path(__file__).resolve().parents[1] / "config.peru-catalog.json"


def load_profile(data_dir):
    values = json.loads(PROFILE.read_text(encoding="utf-8"))
    values["data_dir"] = str(data_dir)
    path = Path(data_dir).parent / "config.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    return Config.load(path)


def add_record(store, record_id, html, *, mode="http", sha256=None):
    url = f"https://www.scielo.org.pe/scielo.php?script=sci_arttext&pid={record_id}"
    store.enqueue({"id": record_id, "url": url, "source": "catalog"})
    store.save_record(
        {
            "id": record_id,
            "url": url,
            "title": "Título de prueba",
            "languages": ["es"],
            "abstract": "Resumen en español.",
            "abstracts": [{"language": "es", "text": "Resumen en español.", "source": "test"}],
            "raw_html_path": store.raw("articles", record_id, html),
            "html_sha256": sha256 or hashlib.sha256(html).hexdigest(),
            "transport": {"mode": mode, "http_status": 200},
        },
        record_id,
    )


def test_profile_loads():
    cfg = Config.load(PROFILE)
    assert cfg.discovery == "catalog"
    assert len(cfg.catalog_journals) == 6


def test_audit_excludes_local_imports_and_checks_gzip_hash(tmp_path):
    cfg = load_profile(tmp_path / "data")
    store = Store(cfg.data_dir)
    try:
        html = b"<html><body>articulo</body></html>"
        add_record(store, "S0001", html)
        add_record(store, "S0002", html, mode="local")
        add_record(store, "S0003", html, sha256="0" * 64)
        results = audit(store, cfg)
    finally:
        store.close()
    assert results["valid_ids"] == ["S0001"]
    assert results["excluded_from_corpus"] == [{"id": "S0002", "reasons": ["not_http_200"]}]
    assert [problem["id"] for problem in results["integrity_errors"]] == ["S0003"]


def test_live_test_does_not_report_blocked_run_as_passed(tmp_path, monkeypatch):
    class BlockedClient:
        def __init__(self, cfg):
            pass

        def get(self, url):
            raise Blocked(f"Acceso restringido, HTTP 403: {url}")

        def close(self):
            pass

    monkeypatch.setattr(benchmark, "Client", BlockedClient)
    cfg = load_profile(tmp_path / "data")
    assert benchmark.run(cfg, target=1) == 2
    report = json.loads((Path(cfg.data_dir) / "report.json").read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert report["counts"]["valid_records"] == 0


def test_verify_only_requires_existing_database(tmp_path):
    load_profile(tmp_path / "data")
    path = tmp_path / "config.json"
    assert benchmark.main(["--config", str(path),"--target", "1", "--verify-only"]) == 2
    assert not (tmp_path / "data").exists()
