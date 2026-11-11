/*
 * Сборщик игровых событий Valorant.
 * Не обращается к Riot API, Telegram или файлам игры.
 * Можно тестировать обычным Node.js без Overwolf.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ValorantCollector = api;
})(typeof window === "object" ? window : this, function () {
  "use strict";

  const MAPS = {
    Infinity: "Abyss", Triad: "Haven", Duality: "Bind", Bonsai: "Split",
    Ascent: "Ascent", Port: "Icebox", Foxtrot: "Breeze", Canyon: "Fracture",
    Pitt: "Pearl", Jam: "Lotus", Juliett: "Sunset", Rook: "Corrode",
    HURM_Alley: "District", HURM_Yard: "Piazza", HURM_Bowl: "Kasbah",
    HURM_Helix: "Drift", HURM_HighTide: "Glitch"
  };
  // Некоторые внутренние имена агентов отличаются от отображаемых.
  const AGENTS = {
    Wushu: "Jett", Sarge: "Brimstone", Thorne: "Sage", Hunter: "Sova",
    Vampire: "Reyna", Gumshoe: "Cypher", Pandemic: "Viper",
    BountyHunter: "Fade", Guide: "Skye", Stealth: "Yoru",
    Deadeye: "Chamber", Sprinter: "Neon"
  };

  function parseMaybeJSON(value) {
    if (typeof value !== "string") return value;
    const raw = value.trim();
    // round_report иногда приходит как строка без внешних фигурных скобок.
    if (/^"[\w]+"\s*:/.test(raw)) {
      try { return JSON.parse("{" + raw + "}"); } catch (_error) {}
    }
    if (!raw || (raw[0] !== "{" && raw[0] !== "[" && raw[0] !== '"')) return value;
    try { return JSON.parse(raw); } catch (_error) { return value; }
  }

  function freshState() {
    return {
      id: null, pseudoId: null, outcome: null, map: null, mode: null,
      agent: null, won: null, lost: null, kills: null, deaths: null,
      assists: null, round: 0, reports: {}, ended: false, locked: false,
      sessionId: "local-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 11)
    };
  }

  function modeName(value) {
    const m = parseMaybeJSON(value);
    if (typeof m !== "object" || m === null) return null;
    if (m.custom === true || m.custom === "true") return "Custom";
    const mode = String(m.mode || "").toLowerCase();
    const ranked = String(m.ranked || "");
    if (mode === "bomb") return ranked === "1" ? "Competitive" : "Unrated";
    return ({ swift: "Swiftplay", "quick bomb": "Spike Rush",
      deathmatch: "Deathmatch", escalation: "Escalation",
      "team deathmatch": "Team Deathmatch", team_deathmatch: "Team Deathmatch",
      range: "Practice Range" })[mode] || (mode || null);
  }

  function num(value) {
    if (value === null || value === undefined || value === "") return null;
    const n = Number(value);
    return Number.isInteger(n) && n >= 0 ? n : null;
  }

  class Collector {
    constructor() { this.s = freshState(); this.pendingStart = []; }

    newMatch() {
      // Карта и режим иногда приходят ДО события match_start.
      const previous = this.s;
      const next = freshState();
      if (!previous.locked && !previous.ended) {
        next.map = previous.map;
        next.mode = previous.mode;
        next.agent = previous.agent;
        next.id = previous.id;
        next.pseudoId = previous.pseudoId;
      }
      this.s = next;
      // Документация: поле map может обновиться ДО события match_start.
      // Такие данные не теряем при переходе между матчами.
      const early = this.pendingStart;
      this.pendingStart = [];
      early.forEach(field => this.applyField(field.category, field.key, field.value));
    }

    applyInfo(packet) {
      if (!packet) return;
      const info = packet.info || packet.res || {};
      // Нативный Overwolf посылает объект по feature, другие SDK — key/value.
      const groups = (info && typeof info === "object") ? info : {};
      Object.entries(groups).forEach(([category, entries]) => {
        if (!entries || typeof entries !== "object") return;
        Object.entries(entries).forEach(([key, value]) => this.applyField(category, key, value));
      });
      if (packet.category && packet.key) {
        this.applyField(packet.category, packet.key, packet.value ?? packet.data);
      }
    }

    applyField(category, key, rawValue) {
      const s = this.s;
      if (rawValue === null || rawValue === undefined || rawValue === "") return;
      if (s.locked) {
        if ((category === "match_info" && ["map", "game_mode", "match_id", "pseudo_match_id"].includes(key)) ||
            (category === "me" && key === "agent")) {
          this.pendingStart = this.pendingStart.filter(item => item.category !== category || item.key !== key);
          this.pendingStart.push({ category, key, value: rawValue });
        }
        return;
      }
      const v = parseMaybeJSON(rawValue);
      if (category === "me" && key === "agent") {
        s.agent = AGENTS[String(v)] || String(v);
        return;
      }
      if (category !== "match_info") return;
      switch (key) {
        case "match_id":
          if (typeof v === "string" && v.length >= 6) s.id = v;
          break;
        case "pseudo_match_id":
          if (typeof v === "string" && v.length >= 6) s.pseudoId = v;
          break;
        case "match_outcome":
          if (["victory", "defeat", "draw"].includes(String(v))) s.outcome = String(v);
          break;
        case "map":
          if (typeof v === "string") s.map = MAPS[v] || v;
          break;
        case "game_mode":
          s.mode = modeName(v) || s.mode;
          break;
        case "score":
          if (v && typeof v === "object") {
            s.won = num(v.won);
            s.lost = num(v.lost);
          }
          break;
        case "round_number":
          s.round = num(v) || s.round;
          break;
        case "round_report":
          if (v && typeof v === "object" && s.round > 0) {
            // Один раунд может сообщаться повторно: не суммируем его два раза.
            s.reports[s.round] = v;
          }
          break;
        default:
          if (key.startsWith("scoreboard_") && v && typeof v === "object") {
            const local = v.is_local === true || v.is_local === "true";
            if (local) {
              s.kills = num(v.kills);
              s.deaths = num(v.deaths);
              s.assists = num(v.assists);
              if (v.character) s.agent = AGENTS[String(v.character)] || String(v.character);
            }
          }
      }
    }

    applyEvents(packet) {
      if (!packet || !Array.isArray(packet.events)) return { started: false, ended: false };
      let started = false;
      let ended = false;
      packet.events.forEach(event => {
        if (event.name === "match_start") {
          this.newMatch();
          started = true;
        }
        if (event.name === "match_end") {
          this.s.ended = true;
          ended = true;
        }
      });
      return { started, ended };
    }

    finish() {
      const s = this.s;
      if (s.locked || !s.outcome) return null;
      s.locked = true;
      const result = {
        match_id: s.id || s.pseudoId || s.sessionId,
        outcome: s.outcome,
        finished_at: Date.now(), map: s.map, mode: s.mode, agent: s.agent,
        won: s.won, lost: s.lost, kills: s.kills, deaths: s.deaths, assists: s.assists
      };
      // HS рассчитывается только при полном наборе отчётов за все раунды.
      const rounds = s.won !== null && s.lost !== null ? s.won + s.lost : 0;
      const reportKeys = Object.keys(s.reports);
      if (rounds > 0 && reportKeys.length >= rounds) {
        const hits = reportKeys.reduce((sum, i) => sum + (num(s.reports[i].hit) || 0), 0);
        const heads = reportKeys.reduce((sum, i) => sum +
          (num(s.reports[i].headshot) || 0) + (num(s.reports[i].final_headshot) || 0), 0);
        if (hits > 0) result.hs_percent = Math.min(100, Math.round(100 * heads / hits));
      }
      // ACS и RR в официальных GEP-событиях не подтверждены.
      return result;
    }
  }

  return { Collector, parseMaybeJSON, modeName };
});
