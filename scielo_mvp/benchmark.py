"""Ejecución reanudable y auditoría de Parte 1. No cuenta importaciones locales."""

import argparse
import json
import logging
import platform
import sqlite3
import time
from dataclasses import replace

from .config import Config, search_url
from .exports import export_bundle
from .http import Blocked, Client, FetchError
from .pipeline import crawl, discover
from .storage import Store, now
from .validation import audit as audit
from .validation import count_ready as count_ready

LOG = logging.getLogger("scielo")


def run(cfg, target=1000, *, verify_only=False):
    """Devuelve 0 si el objetivo está validado; 2 si no se puede completar."""
    if target < 1:
        raise ValueError("El objetivo debe ser positivo")
    store = Store(cfg.data_dir, readonly=verify_only)
    client = None
    handler = None
    try:
        if cfg.discovery == "catalog":
            from .catalog import check_settings

            check_settings(cfg, store)
        handler = logging.FileHandler(store.root / "test.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        LOG.addHandler(handler)
        start, tick = now(), time.monotonic()
        error, status = None, "incomplete"
        snapshot = cfg.as_dict()
        config_path = store.root / ("verification.config.json" if verify_only else "run.config.json")
        store.write(config_path, json.dumps(snapshot, ensure_ascii=False, indent=2).encode("utf-8"))
        if not verify_only and not (store.root / "config.snapshot.json").exists():
            store.write(store.root / "config.snapshot.json", config_path.read_bytes())
        LOG.info(
            "%s; objetivo=%s; base=%s",
            "Auditoría sin red" if verify_only else "Extracción",
            target,
            store.root,
        )
        try:
            initial = audit(store, cfg)
            if initial["integrity_errors"]:
                status = "invalid"
                error = "Hay registros o HTML dañados; revise integrity_errors antes de continuar"
            elif not verify_only:
                client = Client(cfg)
                ready = count_ready(store, cfg)
                while ready < target:
                    pending = store.db.execute(
                        "SELECT count(*) FROM articles WHERE status='pending'"
                    ).fetchone()[0]
                    if pending:
                        crawl(replace(cfg, max_articles=min(25, target - ready)), store, client)
                    else:
                        if cfg.discovery == "catalog":
                            from .catalog import exhausted

                            done = exhausted(cfg, store)
                        else:
                            done = store.search_state(cfg, search_url(cfg))["done"]
                        if done:
                            status = "exhausted"
                            break
                        discover(replace(cfg, max_pages=1), store, client)
                    ready = count_ready(store, cfg)
                    LOG.info("Progreso: %s artículos HTTP con abstract / %s", ready, target)
        except Blocked as exc:
            status, error = "blocked", str(exc)
        except (FetchError, ValueError, OSError, sqlite3.Error) as exc:
            status, error = "error", str(exc)
        except KeyboardInterrupt:
            status, error = "interrupted", "Interrupción solicitada; se conserva el estado para reanudar"
        if error:
            LOG.error("%s", error)
        results = audit(store, cfg)
        if results["integrity_errors"]:
            status = "invalid"
        elif status in ("incomplete", "exhausted") and results["counts"]["valid_records"] >= target:
            status = "passed"
        exports = {}
        if not results["integrity_errors"]:
            exports = export_bundle(store, cfg, results["valid_ids"])
        report = {
            "schema_version": 2,
            "started_at": start,
            "finished_at": now(),
            "elapsed_seconds": round(time.monotonic() - tick, 2),
            "status": status,
            "target": target,
            "error": error,
            "verify_only": verify_only,
            "python": platform.python_version(),
            "config": snapshot,
            "discovery_url": cfg.catalog_url if cfg.discovery == "catalog" else search_url(cfg),
            "validation_policy": "HTTP 200, título, idioma de publicación y abstract según filtros, identidad y HTML SHA-256",
            **{key: value for key, value in results.items() if key != "valid_ids"},
            "exports": exports,
        }
        report_path = store.root / ("verification.json" if verify_only else "report.json")
        store.write(report_path, json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
        LOG.info(
            "Resultado: %s; válidos=%s/%s; informe=%s",
            status,
            results["counts"]["valid_records"],
            target,
            report_path,
        )
        return 0 if status == "passed" else 2
    finally:
        if client:
            client.close()
        if handler:
            LOG.removeHandler(handler)
            handler.close()
        store.close()


def main(argv=None):
    app = argparse.ArgumentParser(description="Prueba de N artículos reales con HTML y abstract")
    app.add_argument("--config", default="config.peru-catalog.json")
    app.add_argument("--target", type=int, default=1000)
    app.add_argument(
        "--verify-only", action="store_true", help="Auditar y exportar sin usar la red ni modificar SQLite"
    )
    args = app.parse_args(argv)
    if args.target < 1:
        app.error("--target debe ser positivo")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        return run(Config.load(args.config), args.target, verify_only=args.verify_only)
    except (ValueError, OSError, sqlite3.Error) as exc:
        LOG.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
