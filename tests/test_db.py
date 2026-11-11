import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db import Database


class DBTests(unittest.TestCase):
    def test_streak_dedup_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'test.sqlite3')
            db = Database(path)
            def make(idx, outcome):
                return {'match_id': f'match-id-{idx}', 'outcome': outcome,
                        'finished_at': int(time.time() * 1000),
                        'kills': 1, 'deaths': 2, 'assists': 3}
            self.assertEqual(db.record(make(1, 'loss'), 'loss 1'), (True, 1))
            self.assertEqual(db.record(make(2, 'loss'), 'loss 2'), (True, 2))
            self.assertEqual(db.record(make(2, 'loss'), 'loss 2'), (False, 2))
            db.close()
            db = Database(path)
            self.assertEqual(db.get('loss_streak'), 2)
            self.assertEqual(db.record(make(3, 'win'), None), (True, 0))
            self.assertEqual(db.stats()['total'], 3)
            self.assertEqual(len(db.pending()), 2)
            db.mark_sent('match-id-1')
            self.assertEqual(len(db.pending()), 1)
            db.close()


if __name__ == '__main__':
    unittest.main()

class MigrationTests(unittest.TestCase):
    def test_old_riot_schema_migrates(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / 'old.sqlite3')
            conn = sqlite3.connect(path)
            conn.execute('CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            conn.execute('CREATE TABLE matches (match_id TEXT PRIMARY KEY, started_at INTEGER NOT NULL, '
                         'outcome TEXT NOT NULL, message TEXT, sent INTEGER NOT NULL DEFAULT 0, '
                         'kills INTEGER NOT NULL DEFAULT 0, deaths INTEGER NOT NULL DEFAULT 0, '
                         'assists INTEGER NOT NULL DEFAULT 0)')
            conn.execute("INSERT INTO kv VALUES ('loss_streak','3')")
            conn.commit(); conn.close()
            db = Database(path)
            self.assertEqual(db.get('loss_streak', 0), 0)
            self.assertEqual(db.record({'match_id':'match-upgrade-1','finished_at':123,
                                        'outcome':'loss','kills':1,'deaths':2,'assists':3},
                                       'new loss'), (True, 1))
            self.assertTrue(db.conn.execute("SELECT 1 FROM sqlite_master "
                                            "WHERE name='matches_riot_backup'").fetchone())
            db.close()
