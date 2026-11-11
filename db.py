"""SQLite: состояние курсора, статистика и очередь уведомлений."""

import json
import sqlite3
from pathlib import Path


class Database:
    def __init__(self, path: str):
        Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS matches (
                match_id TEXT PRIMARY KEY,
                started_at INTEGER NOT NULL,
                outcome TEXT NOT NULL,
                message TEXT,
                sent INTEGER NOT NULL DEFAULT 0,
                kills INTEGER NOT NULL DEFAULT 0,
                deaths INTEGER NOT NULL DEFAULT 0,
                assists INTEGER NOT NULL DEFAULT 0
            );
        """)
        self.conn.commit()

    def get(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def put(self, key: str, value):
        self.conn.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )
        self.conn.commit()

    def exists(self, match_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM matches WHERE match_id=?", (match_id,)).fetchone() is not None

    def record(self, match_id: str, started: int, result, message: str | None, streak: int):
        # Курсор двигаем только после успешного получения и разбора деталей матча.
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO matches(match_id,started_at,outcome,message,kills,deaths,assists) "
                "VALUES(?,?,?,?,?,?,?)",
                (match_id, started, result.outcome, message, result.kills, result.deaths, result.assists),
            )
            for key, value in {
                "last_match_id": match_id,
                "last_match_time": started,
                "loss_streak": streak,
            }.items():
                self.conn.execute(
                    "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, json.dumps(value, ensure_ascii=False)),
                )

    def pending(self):
        return self.conn.execute(
            "SELECT match_id,message FROM matches WHERE outcome='loss' AND sent=0 ORDER BY started_at,match_id"
        ).fetchall()

    def mark_sent(self, match_id: str):
        with self.conn:
            self.conn.execute("UPDATE matches SET sent=1 WHERE match_id=?", (match_id,))

    def last_loss(self):
        return self.conn.execute(
            "SELECT message FROM matches WHERE outcome='loss' ORDER BY started_at DESC LIMIT 1"
        ).fetchone()

    def stats(self):
        return self.conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) AS wins, "
            "SUM(CASE WHEN outcome='loss' THEN 1 ELSE 0 END) AS losses, "
            "SUM(kills) AS kills, SUM(deaths) AS deaths, SUM(assists) AS assists "
            "FROM matches"
        ).fetchone()

    def close(self):
        self.conn.close()
