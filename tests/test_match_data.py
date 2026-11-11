import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from match_data import validate_match, loss_message


class MatchDataTest(unittest.TestCase):
    def sample(self, outcome="defeat"):
        return {"match_id": "e6c450aa-0249-4017-8201-af1378522711",
                "finished_at": int(time.time() * 1000), "outcome": outcome,
                "map": "Ascent", "mode": "Competitive", "agent": "Jett",
                "won": 9, "lost": 13, "kills": 18, "deaths": 15, "assists": 6}

    def test_loss_format(self):
        data = validate_match(self.sample())
        message = loss_message(data, 3)
        self.assertIn("Проигрыш", message)
        self.assertIn("Счёт: 9:13", message)
        self.assertIn("3 луза подряд", message)
        self.assertNotIn("RR:", message)
        self.assertNotIn("ACS:", message)

    def test_win_result(self):
        self.assertEqual(validate_match(self.sample("victory"))["outcome"], "win")

    def test_reject_bad_value(self):
        x = self.sample()
        x["match_id"] = "x"
        with self.assertRaises(ValueError):
            validate_match(x)

    def test_reject_fake_stats(self):
        x = self.sample()
        x["kills"] = -2
        with self.assertRaises(ValueError):
            validate_match(x)


if __name__ == '__main__':
    unittest.main()
