"""
SQLite ile kalıcı indirme geçmişi.
Tek bağlantı + kilit kullanılır; birden fazla thread güvenle çağırabilir.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional, Union

from core.models import DownloadTask, TaskStatus
from utils import paths
from utils.logger import get_logger

log = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS downloads (
    id          TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    save_dir    TEXT NOT NULL,
    file_name   TEXT NOT NULL DEFAULT '',
    file_size   INTEGER,
    status      TEXT NOT NULL,
    error       TEXT NOT NULL DEFAULT '',
    downloaded  INTEGER NOT NULL DEFAULT 0,
    final_path  TEXT,
    engine      TEXT NOT NULL DEFAULT '',
    headers     TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    finished_at TEXT,
    title         TEXT NOT NULL DEFAULT '',
    thumbnail_url TEXT NOT NULL DEFAULT '',
    options       TEXT NOT NULL DEFAULT '{}'
)
"""

# Eski sürümlerde bulunmayan sütunlar: açılışta eksikse eklenir (kayıtlar korunur)
_ADDED_COLUMNS = {
    "title": "TEXT NOT NULL DEFAULT ''",
    "thumbnail_url": "TEXT NOT NULL DEFAULT ''",
    "options": "TEXT NOT NULL DEFAULT '{}'",
}


class Database:
    def __init__(self, path: Union[str, Path] = paths.DB_FILE):
        self._path = str(path)
        self._lock = threading.RLock()
        self._closed = False
        self._conn = self._open()

    # ---------- bağlantı ----------
    def _open(self) -> sqlite3.Connection:
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        try:
            return self._connect()
        except sqlite3.DatabaseError as e:
            log.error("Veritabanı açılamadı (%s). Yedeklenip yenisi oluşturulacak.", e)
            self._backup_corrupt()
            return self._connect()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute(_SCHEMA)
            existing = {row[1] for row in conn.execute("PRAGMA table_info(downloads)")}
            for column, definition in _ADDED_COLUMNS.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE downloads ADD COLUMN {column} {definition}")
            conn.commit()
            conn.execute("SELECT COUNT(*) FROM downloads").fetchone()
        except sqlite3.DatabaseError:
            conn.close()
            raise
        return conn

    def _backup_corrupt(self) -> None:
        if self._path == ":memory:":
            return
        try:
            os.replace(self._path, self._path + ".bozuk")
        except OSError:
            try:
                os.remove(self._path)
            except OSError:
                pass

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._conn.close()

    # ---------- işlemler ----------
    def save(self, task: DownloadTask) -> None:
        """Görevi ekler ya da günceller."""
        with self._lock:
            if self._closed:
                return
            self._conn.execute(
                """INSERT OR REPLACE INTO downloads
                   (id, url, save_dir, file_name, file_size, status, error, downloaded,
                    final_path, engine, headers, created_at, finished_at,
                    title, thumbnail_url, options)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    task.id, task.url, str(task.save_dir), task.file_name, task.file_size,
                    task.status.value, task.error, int(task.downloaded),
                    str(task.final_path) if task.final_path else None,
                    task.engine, json.dumps(task.headers or {}),
                    task.created_at.isoformat(),
                    task.finished_at.isoformat() if task.finished_at else None,
                    task.title, task.thumbnail_url, json.dumps(task.options or {}),
                ),
            )
            self._conn.commit()

    def delete(self, task_id: str) -> None:
        with self._lock:
            if self._closed:
                return
            self._conn.execute("DELETE FROM downloads WHERE id = ?", (task_id,))
            self._conn.commit()

    def get(self, task_id: str) -> Optional[DownloadTask]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM downloads WHERE id = ?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def load_all(self) -> list[DownloadTask]:
        """Eskiden yeniye sıralı tüm görevler. Okunamayan satırlar atlanır."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM downloads ORDER BY created_at, rowid"
            ).fetchall()
        tasks = []
        for row in rows:
            try:
                tasks.append(self._row_to_task(row))
            except Exception:
                log.exception("Bozuk veritabanı satırı atlandı: %s", row["id"])
        return tasks

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> DownloadTask:
        try:
            status = TaskStatus(row["status"])
        except ValueError:
            status = TaskStatus.PAUSED
        return DownloadTask(
            id=row["id"],
            url=row["url"],
            save_dir=Path(row["save_dir"]),
            file_name=row["file_name"],
            headers=json.loads(row["headers"] or "{}"),
            file_size=row["file_size"],
            status=status,
            error=row["error"],
            downloaded=row["downloaded"],
            final_path=Path(row["final_path"]) if row["final_path"] else None,
            engine=row["engine"],
            created_at=datetime.fromisoformat(row["created_at"]),
            finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
            title=row["title"],
            thumbnail_url=row["thumbnail_url"],
            options=json.loads(row["options"] or "{}"),
        )