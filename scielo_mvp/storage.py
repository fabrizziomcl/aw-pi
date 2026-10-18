import gzip
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def dumps(value):
    return json.dumps(value, ensure_ascii=False)


class Store:
    def __init__(self, directory, *, readonly=False):
        self.root = Path(directory).resolve()
        if readonly:
            database = self.root / "scielo.sqlite3"
            if not database.is_file():
                raise ValueError(f"No existe una base para verificar: {database}")
            self.db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
            self.db.row_factory = sqlite3.Row
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "scielo.sqlite3")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
            id TEXT PRIMARY KEY, url TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
            seed_json TEXT NOT NULL, record_json TEXT, error TEXT, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS articles_status ON articles(status);
        CREATE TABLE IF NOT EXISTS searches (
            id TEXT PRIMARY KEY, next_page INTEGER NOT NULL DEFAULT 1,
            done INTEGER NOT NULL DEFAULT 0, config_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS search_pages (
            search_id TEXT, page INTEGER, fingerprint TEXT, raw_path TEXT,
            PRIMARY KEY(search_id, page), UNIQUE(search_id, fingerprint)
        );
        CREATE TABLE IF NOT EXISTS discoveries (
            article_id TEXT, source_url TEXT, seed_json TEXT,
            PRIMARY KEY(article_id, source_url)
        );
        CREATE TABLE IF NOT EXISTS downloads (
            article_id TEXT, kind TEXT, source_url TEXT, final_url TEXT, path TEXT,
            sha256 TEXT, downloaded_at TEXT, PRIMARY KEY(article_id, kind)
        );
        """)

    def close(self):
        self.db.close()

    def raw(self, group, key, content):
        path = self.root / "raw" / group / (key + ".html.gz")
        self.write(path, gzip.compress(content, mtime=0))
        return str(path.relative_to(self.root))

    def write(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_bytes(content)
        temp.replace(path)

    def enqueue(self, seed):
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO articles(id,url,seed_json,updated_at) VALUES(?,?,?,?)",
            (seed["id"], seed["url"], dumps(seed), now()),
        )
        self.db.execute(
            "INSERT OR REPLACE INTO discoveries VALUES(?,?,?)",
            (seed["id"], seed.get("source_url", seed["url"]), dumps(seed)),
        )
        return cursor.rowcount

    def save_record(self, record, requested_id):
        # Conservar identidad de cola aunque el sitio redirija al OPAC moderno.
        record["canonical_id"] = record["id"]
        record["id"] = requested_id
        self.db.execute(
            "UPDATE articles SET status='done', record_json=?, error=NULL, updated_at=? WHERE id=?",
            (dumps(record), now(), requested_id),
        )
        self.db.commit()

    def fail(self, record_id, error, status="error"):
        self.db.execute(
            "UPDATE articles SET status=?,error=?,updated_at=? WHERE id=?",
            (status, str(error), now(), record_id),
        )
        self.db.commit()

    def search_state(self, cfg, url):
        key = hashlib.sha256(url.encode()).hexdigest()[:24]
        self.db.execute(
            "INSERT OR IGNORE INTO searches(id,config_json) VALUES(?,?)", (key, dumps(cfg.as_dict()))
        )
        self.db.commit()
        return self.db.execute("SELECT * FROM searches WHERE id=?", (key,)).fetchone()
