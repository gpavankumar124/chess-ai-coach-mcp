"""Tiny SQLite cache for Chess.com API responses.

One database file, one table, key/value style:

    cache(namespace, key, value, fetched_at)   PRIMARY KEY (namespace, key)

`value` holds the API response as a JSON string. `fetched_at` is a unix
timestamp used to decide whether an entry is still fresh.

Design goal: a cache problem must NEVER break the app. Every function here
swallows database errors and behaves as if the cache were simply empty.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from log import get_logger

_log = get_logger("cache")

DB_PATH = Path(__file__).resolve().parent / "data" / "cache.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    namespace  TEXT    NOT NULL,
    key        TEXT    NOT NULL,
    value      TEXT    NOT NULL,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY (namespace, key)
)
"""


def _connect() -> sqlite3.Connection:
    """Open the database (creating the file, folder and table if needed)."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute(_SCHEMA)
    return conn


def load(namespace: str, key: str, ttl_seconds: int | None):
    """Return the cached value, or None if missing / stale / unreadable.

    ttl_seconds=None means "never expires".
    """
    try:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT value, fetched_at FROM cache WHERE namespace = ? AND key = ?",
                (namespace, key),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        _log.warning("cache read failed (%s/%s): %s", namespace, key, exc)
        return None

    if row is None:
        _log.debug("cache miss %s/%s", namespace, key)
        return None
    value, fetched_at = row
    if ttl_seconds is not None and (time.time() - fetched_at) > ttl_seconds:
        _log.debug("cache stale %s/%s", namespace, key)
        return None
    try:
        _log.debug("cache hit %s/%s", namespace, key)
        return json.loads(value)
    except ValueError:
        return None


def save(namespace: str, key: str, value) -> None:
    """Insert or replace a cache entry. Silently does nothing on failure."""
    try:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO cache (namespace, key, value, fetched_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(namespace, key)
                DO UPDATE SET value = excluded.value, fetched_at = excluded.fetched_at
                """,
                (namespace, key, json.dumps(value), int(time.time())),
            )
            conn.commit()
        finally:
            conn.close()
        _log.debug("cache save %s/%s", namespace, key)
    except (sqlite3.Error, TypeError) as exc:
        _log.warning("cache write failed (%s/%s): %s", namespace, key, exc)


def stats() -> dict:
    """Summarize what the cache currently holds."""
    info = {
        "db_path": str(DB_PATH),
        "db_size_bytes": DB_PATH.stat().st_size if DB_PATH.exists() else 0,
        "total_entries": 0,
        "namespaces": {},
    }
    try:
        conn = _connect()
        try:
            rows = conn.execute(
                """
                SELECT namespace, COUNT(*), MIN(fetched_at), MAX(fetched_at)
                FROM cache GROUP BY namespace
                """
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        info["error"] = str(exc)
        return info

    now = time.time()
    for namespace, count, oldest, newest in rows:
        info["total_entries"] += count
        info["namespaces"][namespace] = {
            "entries": count,
            "oldest_age_seconds": int(now - oldest),
            "newest_age_seconds": int(now - newest),
        }
    return info


def clear(namespace: str | None = None) -> int:
    """Delete cache entries (all, or just one namespace). Returns rows removed."""
    try:
        conn = _connect()
        try:
            if namespace:
                cur = conn.execute("DELETE FROM cache WHERE namespace = ?", (namespace,))
            else:
                cur = conn.execute("DELETE FROM cache")
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()
    except sqlite3.Error:
        return 0
