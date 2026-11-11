"""Проверка входящих событий и оформление уведомлений о проигрыше."""

import re
import time
from typing import Any

ID_PATTERN = re.compile(r"^[a-zA-Z0-9:_-]{6,128}$")
OUTCOMES = {"defeat": "loss", "victory": "win", "draw": "draw",
            "loss": "loss", "win": "win"}


def clean_str(data: dict, key: str, maximum: int = 64) -> str | None:
    value = data.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f"Некорректное поле {key}")
    # Не допускаем многострочный пользовательский текст в сообщении бота.
    return " ".join(value.split())


def clean_int(data: dict, key: str, maximum: int = 1000) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"Некорректное поле {key}")
    return value


def validate_match(data: Any) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Требуется JSON-объект")
    match_id = data.get("match_id")
    if not isinstance(match_id, str) or not ID_PATTERN.fullmatch(match_id):
        raise ValueError("Нужен корректный match_id от Overwolf")
    outcome = OUTCOMES.get(data.get("outcome"))
    if not outcome:
        raise ValueError("Неизвестный результат матча")
    finished_at = data.get("finished_at")
    now_ms = int(time.time() * 1000)
    # Даем локальной очереди до 7 дней на восстановление связи.
    if type(finished_at) is not int or not now_ms - 7*86400000 <= finished_at <= now_ms + 600000:
        raise ValueError("Некорректное время матча")
    return {
        "match_id": match_id, "outcome": outcome, "finished_at": finished_at,
        "map": clean_str(data, "map"),
        "mode": clean_str(data, "mode"),
        "agent": clean_str(data, "agent"),
        "won": clean_int(data, "won", 100),
        "lost": clean_int(data, "lost", 100),
        "kills": clean_int(data, "kills", 999),
        "deaths": clean_int(data, "deaths", 999),
        "assists": clean_int(data, "assists", 999),
        "hs_percent": clean_int(data, "hs_percent", 100),
    }


def loss_message(data: dict, streak: int) -> str:
    lines = ["Проигрыш"]
    labels = []
    if data["map"]:
        labels.append(f"Карта: {data['map']}")
    if data["mode"]:
        labels.append(f"Режим: {data['mode']}")
    if labels:
        lines.append(", ".join(labels))
    if data["agent"]:
        lines.append(f"Агент: {data['agent']}")
    if data["won"] is not None and data["lost"] is not None:
        lines.append(f"Счёт: {data['won']}:{data['lost']}")
    if all(data[key] is not None for key in ("kills", "deaths", "assists")):
        lines.append(f"KDA: {data['kills']}/{data['deaths']}/{data['assists']}")
    # ACS и RR не выдаются событиями Overwolf: намеренно не выдумываем их.
    if data["hs_percent"] is not None:
        lines.append(f"HS (по собранным раундам): {data['hs_percent']}%")
    if streak == 3:
        lines.append("3 луза подряд, сделай перерыв.")
    return "\n".join(lines)
