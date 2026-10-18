"""Ejecución reanudable: descubre, descarga, audita y exporta tesis de varios repositorios.

    python -m universidades_mvp.run --config config.universidades.json --target 100
"""

import argparse
import hashlib
import json
import logging
import platform
import sqlite3
import time
from pathlib import Path

from scielo_mvp.parsers import ParseError
from scielo_mvp.storage import now

from .browser import Browser
from .config import Config
from .discovery import discover
from .exports import export_combined, export_repo
from .http import Blocked, Client, Disallowed, FetchError
from .parsers import NotItem, identifier, parse_item
from .storage import Store
from .validation import audit, qualification_errors

LOG = logging.getLogger("universidades")


def fetch(repo, url, client, browser):
    target = url if not repo.item_suffix or url.endswith(repo.item_suffix) else url + repo.item_suffix
    if repo.render:
        page = browser.get(target, repo.wait_for, repo.wait_also)
        return page, {"mode": "selenium", "requested_url": target, "final_url": page.url}
    page = client.get(target)
    return page, {
        "mode": "http",
        "requested_url": target,
        "final_url": page.url,
        "http_status": page.status,
        "response_headers": page.headers,
    }


def process(cfg, repo, store, row, client, browser):
    """Devuelve True si la ficha es una tesis válida."""
    page, transport = fetch(repo, row["url"], client, browser)
    key = row["id"].split(":", 1)[1]
    if key not in page.url:
        raise NotItem(f"La ficha redirigió a {page.url}")
    record = parse_item(page.content.decode("utf-8", "replace"), page.url, repo, requested_url=row["url"])
    record.update(
        {
            "paper_id": row["id"],
            "raw_html_path": store.raw(row["id"], page.content),
            "html_sha256": hashlib.sha256(page.content).hexdigest(),
            "fetched_at": now(),
            "transport": transport,
        }
    )
    record["exclusion"] = qualification_errors(record, cfg)
    store.save(row["id"], record, "filtered" if record["exclusion"] else "done")
    return not record["exclusion"]


def crawl(cfg, repo, store, client, browser, target):
    ready, errors = store.count("done"), 0
    while ready < target:
        rows = store.pending(cfg.batch_size)
        if not rows:
            return "exhausted"
        for row in rows:
            if ready >= target:
                break
            try:
                if process(cfg, repo, store, row, client, browser):
                    ready += 1
                    LOG.info("%s: %s/%s tesis válidas — %s", repo.key, ready, target, row["url"])
                else:
                    LOG.info("%s: excluida %s", repo.key, row["url"])
                errors = 0
            except NotItem as exc:
                store.fail(row["id"], exc, "skipped")
            except Disallowed as exc:
                store.fail(row["id"], exc, "disallowed")
                LOG.warning("%s", exc)
            except Blocked as exc:
                store.fail(row["id"], exc, "blocked")
                raise
            except (FetchError, ParseError) as exc:
                store.fail(row["id"], exc)
                LOG.error("%s: %s", repo.key, exc)
                errors += 1
                if errors >= cfg.max_consecutive_errors:
                    raise FetchError(f"{errors} errores seguidos; el repositorio podría estar caído") from exc
    return "incomplete"


def run_repo(cfg, repo, target, client, browser, *, verify_only=False, rediscover=False):
    root = Path(cfg.data_dir) / repo.key
    report = {"repo": repo.key, "university": repo.name, "target": target, "started_at": now()}
    tick = time.monotonic()
    try:
        store = Store(root, readonly=verify_only)
    except (ValueError, OSError, sqlite3.Error) as exc:
        report.update({"status": "missing" if verify_only else "error", "error": str(exc)})
        LOG.error("%s: %s", repo.key, exc)
        return report, None
    status, error = "incomplete", None
    try:
        if not verify_only:
            settings = repo.discovery_settings() | {"sample_seed": cfg.sample_seed}
            saved = store.discovery()
            if saved and saved["settings"] != settings:
                raise ValueError("La configuración de descubrimiento cambió: use otro data_dir para no mezclar muestras")
            if not saved or rediscover:
                urls = discover(repo, client)
                added = store.enqueue([(identifier(url, repo), url) for url in urls], settings, cfg.sample_seed)
                LOG.info("%s: %s fichas descubiertas; %s nuevas en la cola", repo.key, len(urls), added)
            status = crawl(cfg, repo, store, client, browser, target)
    except Blocked as exc:
        status, error = "blocked", str(exc)
    except (FetchError, ParseError, ValueError, OSError, sqlite3.Error) as exc:
        status, error = "error", str(exc)
    except KeyboardInterrupt:
        status, error = "interrupted", "Interrupción solicitada; se conserva el estado para reanudar"
    if error:
        LOG.error("%s: %s", repo.key, error)
    results = audit(store, cfg)
    if results["integrity_errors"]:
        status = "invalid"
    elif status in ("incomplete", "exhausted") and results["counts"]["valid_theses"] >= target:
        status = "passed"
    valid_ids = [] if results["integrity_errors"] else results["valid_ids"]
    exports = export_repo(store, valid_ids) if not results["integrity_errors"] else {}
    report.update(
        {
            "finished_at": now(),
            "elapsed_seconds": round(time.monotonic() - tick, 2),
            "status": status,
            "error": error,
            "verify_only": verify_only,
            "discovery": store.discovery(),
            "config": repo.__dict__,
            **{key: value for key, value in results.items() if key != "valid_ids"},
            "exports": exports,
        }
    )
    name = "verification.json" if verify_only else "report.json"
    store.write(root / name, json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
    LOG.info("%s: %s; tesis válidas %s/%s", repo.key, status, results["counts"]["valid_theses"], target)
    return report, (store, valid_ids)


def run(cfg, keys=None, target=100, *, verify_only=False, rediscover=False):
    """Devuelve 0 si todos los repositorios alcanzan el objetivo; 2 en otro caso."""
    if target < 1:
        raise ValueError("El objetivo debe ser positivo")
    repos = cfg.selected(keys)
    root = Path(cfg.data_dir)
    if verify_only and not root.is_dir():
        raise ValueError(f"No existe la carpeta para verificar: {root.resolve()}")
    root.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(root / "universidades.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    LOG.addHandler(handler)
    client = None if verify_only else Client(cfg)
    browser = Browser(cfg, client) if client and any(repo.render for repo in repos) else None
    reports, opened = [], []
    started, tick = now(), time.monotonic()
    try:
        LOG.info("%s; objetivo=%s tesis por repositorio; %s repositorios", "Auditoría sin red" if verify_only else "Extracción", target, len(repos))
        for repo in repos:
            LOG.info("===== %s (%s)", repo.name, repo.key)
            report, store = run_repo(
                cfg, repo, target, client, browser, verify_only=verify_only, rediscover=rediscover
            )
            reports.append({k: report.get(k) for k in ("repo", "university", "status", "error", "counts")})
            if store:
                opened.append(store)
            if report["status"] == "interrupted":
                break
        combined = export_combined(root, opened)
        passed = len(reports) == len(repos) and all(r["status"] == "passed" for r in reports)
        summary = {
            "schema_version": 1,
            "started_at": started,
            "finished_at": now(),
            "elapsed_seconds": round(time.monotonic() - tick, 2),
            "status": "passed" if passed else "incomplete",
            "target_per_repo": target,
            "verify_only": verify_only,
            "python": platform.python_version(),
            "config": {k: v for k, v in cfg.as_dict().items() if k != "repos"},
            "valid_theses": sum((r["counts"] or {}).get("valid_theses", 0) for r in reports if r.get("counts")),
            "repos": reports,
            "exports": combined,
        }
        name = "verification.json" if verify_only else "summary.json"
        (root / name).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        LOG.info("Resultado: %s; %s tesis válidas en total; informe=%s", summary["status"], summary["valid_theses"], root / name)
        return 0 if passed else 2
    finally:
        if browser:
            browser.close()
        if client:
            client.close()
        for store, _ in opened:
            store.close()
        LOG.removeHandler(handler)
        handler.close()


def main(argv=None):
    app = argparse.ArgumentParser(description="Scraping de tesis en repositorios universitarios peruanos")
    app.add_argument("--config", default="config.universidades.json")
    app.add_argument("--repos", default="", help="Claves separadas por comas; por defecto, todos los habilitados")
    app.add_argument("--target", type=int, default=100, help="Tesis válidas por repositorio")
    app.add_argument("--verify-only", action="store_true", help="Auditar y exportar sin usar la red ni modificar SQLite")
    app.add_argument("--rediscover", action="store_true", help="Volver a leer los sitemaps y añadir fichas nuevas")
    app.add_argument("--list", action="store_true", help="Listar los repositorios configurados y salir")
    args = app.parse_args(argv)
    if args.target < 1:
        app.error("--target debe ser positivo")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        cfg = Config.load(args.config)
        if args.list:
            for repo in cfg.repos:
                mode = "Selenium" if repo.render else "curl_cffi"
                print(f"{repo.key:10} {'sí' if repo.enabled else 'no':3} {mode:9} {repo.base_url}")
            return 0
        keys = [key.strip() for key in args.repos.split(",") if key.strip()]
        return run(cfg, keys, args.target, verify_only=args.verify_only, rediscover=args.rediscover)
    except (ValueError, OSError, sqlite3.Error) as exc:
        LOG.error("%s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
