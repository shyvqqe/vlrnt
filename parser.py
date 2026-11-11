"""Подсчёт статистики из официального ответа VAL-MATCH-V1."""

from dataclasses import dataclass


@dataclass
class Result:
    outcome: str  # loss, win, draw или unknown
    map_name: str
    mode: str
    agent: str
    own_score: int | None
    enemy_score: int | None
    kills: int
    deaths: int
    assists: int
    acs: int | None
    hs: int | None


MODES = {
    "competitive": "Competitive", "unrated": "Unrated", "swiftplay": "Swiftplay",
    "spikerush": "Spike Rush", "deathmatch": "Deathmatch",
    "ggteam": "Team Deathmatch", "onefa": "Escalation", "premier": "Premier",
}

# Для карт код Riot может содержать не их отображаемое имя.
MAP_ALIASES = {
    "triad": "Haven", "dual ity": "Bind", "duality": "Bind",
    "ascent": "Ascent", "port": "Icebox", "bonsai": "Split",
    "foxtrot": "Breeze", "canyon": "Fracture", "pitt": "Pearl",
    "jam": "Lotus", "juliett": "Sunset", "infinity": "Abyss",
    "hurdle": "Corrode",
}


def asset_name(items: list, code: str) -> str | None:
    for item in items:
        if str(item.get("id", "")).lower() == str(code).lower():
            return item.get("name")
    return None


def parse_match(payload: dict, puuid: str, content: dict | None = None) -> Result:
    content = content or {}
    info = payload.get("matchInfo") or {}
    player = next((p for p in payload.get("players", []) if p.get("puuid") == puuid), None)
    if player is None:
        raise ValueError("В данных матча нет нужного игрока")

    team_id = player.get("teamId")
    teams = payload.get("teams") or []
    my_team = next((t for t in teams if t.get("teamId") == team_id), None)
    enemy_team = next((t for t in teams if t.get("teamId") != team_id), None)
    own_score = my_team.get("roundsWon") if my_team else None
    enemy_score = enemy_team.get("roundsWon") if enemy_team else None
    outcome = "unknown"
    if my_team and enemy_team:
        if own_score is not None and enemy_score is not None and own_score == enemy_score:
            outcome = "draw"
        elif my_team.get("won") is True:
            outcome = "win"
        elif enemy_team.get("won") is True:
            outcome = "loss"
        elif own_score is not None and enemy_score is not None:
            outcome = "win" if own_score > enemy_score else "loss"

    mode_code = str(info.get("queueID") or "").lower()
    mode = MODES.get(mode_code, mode_code.capitalize() or "Custom")
    map_code = info.get("mapId") or ""
    map_name = asset_name(content.get("maps", []), map_code)
    if not map_name:
        short_name = str(map_code).rsplit("/", 1)[-1]
        map_name = MAP_ALIASES.get(short_name.lower(), short_name or "Неизвестна")
    agent_id = player.get("characterId", "")
    agent = asset_name(content.get("characters", []), agent_id) or "Неизвестен"

    stats = player.get("stats") or {}
    rounds = int(stats.get("roundsPlayed") or 0)
    score = stats.get("score")
    acs = round(int(score) / rounds) if score is not None and rounds > 0 else None

    # В официальном ответе попадания находятся ПО РАУНДАМ, не в stats игрока.
    head = body = leg = 0
    for rnd in payload.get("roundResults") or []:
        for row in rnd.get("playerStats") or []:
            if row.get("puuid") != puuid:
                continue
            for damage in row.get("damage") or []:
                head += int(damage.get("headshots") or 0)
                body += int(damage.get("bodyshots") or 0)
                leg += int(damage.get("legshots") or 0)
    hits = head + body + leg
    hs = round(100 * head / hits) if hits else None

    return Result(
        outcome=outcome, map_name=map_name, mode=mode, agent=agent,
        own_score=own_score, enemy_score=enemy_score,
        kills=int(stats.get("kills") or 0),
        deaths=int(stats.get("deaths") or 0),
        assists=int(stats.get("assists") or 0), acs=acs, hs=hs,
    )


def loss_message(result: Result, streak: int) -> str:
    # RR отсутствует в официальном API VAL-MATCH-V1: ничего не выдумываем.
    score = f"{result.own_score}:{result.enemy_score}" if result.own_score is not None and result.enemy_score is not None else "—"
    lines = [
        "Проигрыш",
        f"Карта: {result.map_name}, Режим: {result.mode}",
        f"Агент: {result.agent}",
        f"Счет: {score}",
        f"KDA: {result.kills}/{result.deaths}/{result.assists}",
        f"ACS: {result.acs if result.acs is not None else '—'}",
        f"HS: {str(result.hs) + '%' if result.hs is not None else '—'}",
    ]
    if streak >= 3:
        lines.extend(["", "3 луза подряд, сделай перерыв."])
    return "\n".join(lines)
