"""SQLite por repositorio: cola ordenada, registros y HTML comprimido con huella SHA-256."""

import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

from scielo_mvp.storage import dumps, now


class Store:
    def __init__(self, directory, *, readonly=False):
        self.root = Path(directory).resolve()
        database = self.root / "universidades.sqlite3"
        if readonly:
            if not database.is_file():
                raise ValueError(f"No existe una base para verificar: {database}")
            self.db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
            self.db.row_factory = sqlite3.Row
            return
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY, url TEXT NOT NULL, rank TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', record_json TEXT, error TEXT, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS items_queue ON items(status, rank);
        CREATE TABLE IF NOT EXISTS discovery (
            id INTEGER PRIMARY KEY CHECK (id = 1), settings TEXT NOT NULL,
            found INTEGER NOT NULL, finished_at TEXT NOT NULL
        );
        """)

    def close(self):
        self.db.close()

    def write(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_bytes(content)
        temp.replace(path)

    def raw(self, key, content):
        name = hashlib.sha256(key.encode()).hexdigest()[:24]
        path = self.root / "raw" / (name + ".html.gz")
        self.write(path, gzip.compress(content, mtime=0))
        return str(path.relative_to(self.root))

    def discovery(self):
        row = self.db.execute("SELECT settings,found,finished_at FROM discovery WHERE id=1").fetchone()
        return dict(row, settings=json.loads(row["settings"])) if row else None

    def enqueue(self, items, settings, seed):
        """items: [(id, url)]. El orden pseudoaleatorio con semilla evita sesgar la muestra al sitemap."""
        before = self.db.total_changes
        with self.db:
            self.db.executemany(
                "INSERT OR IGNORE INTO items(id,url,rank,updated_at) VALUES(?,?,?,?)",
                [
                    (key, url, hashlib.sha256(f"{seed}:{key}".encode()).hexdigest() if seed else f"{i:09}", now())
                    for i, (key, url) in enumerate(items)
                ],
            )
            added = self.db.total_changes - before
            self.db.execute(
                "INSERT OR REPLACE INTO discovery VALUES(1,?,?,?)",
                (dumps(settings), len(items), now()),
            )
        return added

    def pending(self, limit):
        return self.db.execute(
            "SELECT id,url FROM items WHERE status='pending' ORDER BY rank LIMIT ?", (limit,)
        ).fetchall()

    def save(self, key, record, status):
        self.db.execute(
            "UPDATE items SET status=?, record_json=?, error=NULL, updated_at=? WHERE id=?",
            (status, dumps(record), now(), key),
        )
        self.db.commit()

    def fail(self, key, error, status="error"):
        self.db.execute(
            "UPDATE items SET status=?, error=?, updated_at=? WHERE id=?", (status, str(error), now(), key)
        )
        self.db.commit()

    def count(self, status):
        return self.db.execute("SELECT count(*) FROM items WHERE status=?", (status,)).fetchone()[0]

    def records(self, *statuses):
        marks = ",".join("?" * len(statuses))
        for row in self.db.execute(
            f"SELECT id,status,record_json FROM items WHERE status IN ({marks}) ORDER BY rank", statuses
        ):
            yield row["id"], row["status"], json.loads(row["record_json"])
