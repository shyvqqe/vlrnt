"""Проверяем сохранение курсора и защиту от повторных матчей."""
import tempfile
import unittest
from pathlib import Path
from db import Database
from parser import Result


class DbTests(unittest.TestCase):
    def test_restarts_and_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            filename = str(Path(tmp) / "db.sqlite3")
            res = Result("loss", "Ascent", "Competitive", "Jett", 9, 13, 18, 15, 6, 231, 24)
            db = Database(filename)
            db.record("m1", 1234, res, "Проигрыш", 1)
            db.close()
            db2 = Database(filename)
            self.assertEqual(db2.get("last_match_id"), "m1")
            self.assertTrue(db2.exists("m1"))
            self.assertEqual(len(db2.pending()), 1)
            db2.mark_sent("m1")
            self.assertEqual(len(db2.pending()), 0)
            db2.close()


if __name__ == "__main__":
    unittest.main()
