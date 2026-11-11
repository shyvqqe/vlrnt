"""Тестирование расчётов без Riot-ключа и Telegram."""
import unittest
from parser import parse_match, loss_message


class ParserTests(unittest.TestCase):
    def test_loss_and_stats(self):
        match = {
            "matchInfo": {"queueID": "competitive", "mapId": "/Game/Maps/Ascent/Ascent"},
            "players": [{"puuid": "owner", "teamId": "Blue", "characterId": "jett-id",
                         "stats": {"kills": 18, "deaths": 15, "assists": 6,
                                   "score": 5313, "roundsPlayed": 23}}],
            "teams": [{"teamId": "Blue", "won": False, "roundsWon": 9},
                      {"teamId": "Red", "won": True, "roundsWon": 13}],
            "roundResults": [{"playerStats": [{"puuid": "owner", "damage": [
                {"headshots": 6, "bodyshots": 16, "legshots": 3}
            ]}]}],
        }
        result = parse_match(match, "owner", {"characters": [{"id": "jett-id", "name": "Jett"}]})
        self.assertEqual((result.outcome, result.agent, result.map_name), ("loss", "Jett", "Ascent"))
        self.assertEqual(result.acs, 231)
        self.assertEqual(result.hs, 24)
        message = loss_message(result, 3)
        self.assertIn("3 луза подряд", message)
        self.assertNotIn("RR:", message)


if __name__ == "__main__":
    unittest.main()
