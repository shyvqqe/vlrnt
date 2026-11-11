"""SQLite: принятые матчи, серия проигрышей и очередь личных уведомлений."""

import json
import sqlite3
from pathlib import Path


class Database:
    def __init__(self, path: str):
        Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        # Старый Riot-вариант использовал started_at вместо received_at.
        # Сохраняем его таблицу под резервным именем и создаём новую схему.
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(matches)")}
        if "started_at" in columns and "received_at" not in columns:
            backup = "matches_riot_backup"
            number = 2
            while self.conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (backup,)).fetchone():
                backup = f"matches_riot_backup_{number}"
                number += 1
            self.conn.execute(f'ALTER TABLE matches RENAME TO "{backup}"')
            self.conn.execute("DELETE FROM kv WHERE key='loss_streak'")
            self.conn.commit()
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS matches (
                match_id TEXT PRIMARY KEY,
                received_at INTEGER NOT NULL,
                outcome TEXT NOT NULL,
                message TEXT,
                sent INTEGER NOT NULL DEFAULT 0,
                kills INTEGER,
                deaths INTEGER,
                assists INTEGER
            );
        """)
        self.conn.commit()

    def get(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def put(self, key: str, value):
        with self.conn:
            self.conn.execute(
                "INSERT INTO kv(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def record(self, data: dict, message: str | None) -> tuple[bool, int]:
        """Атомарно сохраняем матч и счётчик. Повтор не меняет серию."""
        with self.conn:
            # Проверка и запись идут в одной транзакции: дубли не сбивают серию.
            existing = self.conn.execute(
                "SELECT 1 FROM matches WHERE match_id=?", (data["match_id"],)
            ).fetchone()
            streak = int(self.get("loss_streak", 0))
            if existing:
                return False, streak
            if data["outcome"] == "loss":
                streak += 1
            else:
                streak = 0
            self.conn.execute(
                "INSERT INTO matches(match_id,received_at,outcome,message,kills,deaths,assists) "
                "VALUES (?,?,?,?,?,?,?)",
                (data["match_id"], data["finished_at"], data["outcome"], message,
                 data.get("kills"), data.get("deaths"), data.get("assists")),
            )
            self.conn.execute(
                "INSERT INTO kv(key,value) VALUES('loss_streak',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(streak),),
            )
        return True, streak

    def exists(self, match_id: str):
        return bool(self.conn.execute(
            "SELECT 1 FROM matches WHERE match_id=?", (match_id,)
        ).fetchone())

    def pending(self):
        return self.conn.execute(
            "SELECT match_id,message FROM matches "
            "WHERE outcome='loss' AND sent=0 ORDER BY received_at ASC, rowid ASC"
        ).fetchall()

    def mark_sent(self, match_id: str):
        with self.conn:
            self.conn.execute("UPDATE matches SET sent=1 WHERE match_id=?", (match_id,))

    def last_loss(self):
        return self.conn.execute(
            "SELECT message FROM matches WHERE outcome='loss' "
            "ORDER BY received_at DESC, rowid DESC LIMIT 1"
        ).fetchone()

    def stats(self):
        return self.conn.execute(
            "SELECT COUNT(*) total, "
            "COALESCE(SUM(outcome='win'),0) wins, "
            "COALESCE(SUM(outcome='loss'),0) losses, "
            "COALESCE(SUM(kills),0) kills, "
            "COALESCE(SUM(deaths),0) deaths, "
            "COALESCE(SUM(assists),0) assists FROM matches"
        ).fetchone()

    def close(self):
        self.conn.close()
