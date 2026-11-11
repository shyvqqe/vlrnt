/*
 * Настройка Overwolf и доставка завершённых матчей на Render.
 * В Telegram напрямую не отправляем и RIOT_API_KEY не используем.
 */
"use strict";

const collector = new ValorantCollector.Collector();
const STORAGE = {
  endpoint: "ow_bridge_endpoint", token: "ow_bridge_token",
  queue: "ow_bridge_queue", sent: "ow_bridge_sent", log: "ow_bridge_log"
};
const $ = id => document.getElementById(id);
const endpointInput = $("endpoint");
const tokenInput = $("token");
const stateLabel = $("state");
const queueLabel = $("queueSize");
const logLabel = $("logs");
let flushBusy = false;
let pendingFinishTimer = null;
let featuresReady = false;
let currentGame = false;

function fromStorage(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; }
  catch (_err) { return fallback; }
}
function toStorage(key, value) { localStorage.setItem(key, JSON.stringify(value)); }
function logs(text) {
  const now = new Date().toLocaleTimeString("ru-RU");
  const items = fromStorage(STORAGE.log, []);
  items.unshift(`[${now}] ${text}`);
  toStorage(STORAGE.log, items.slice(0, 30));
  logLabel.textContent = items.slice(0, 12).join("\n");
}
function status(text) { stateLabel.textContent = text; }
function renderQueue() {
  queueLabel.textContent = String(fromStorage(STORAGE.queue, []).length);
}
function getConfig() {
  return { endpoint: fromStorage(STORAGE.endpoint, ""),
    token: fromStorage(STORAGE.token, "") };
}
function saveConfig() {
  const endpoint = endpointInput.value.trim().replace(/\/+$/, "");
  const token = tokenInput.value.trim();
  if (!/^https:\/\/[a-z0-9][a-z0-9.-]+(?::\d+)?$/i.test(endpoint)) {
    logs("Укажи адрес вида https://vlrnt.onrender.com без / в конце");
    return;
  }
  if (token.length < 32) {
    logs("Секрет BRIDGE_TOKEN должен содержать минимум 32 символа");
    return;
  }
  toStorage(STORAGE.endpoint, endpoint);
  toStorage(STORAGE.token, token);
  logs("Настройки сохранены локально. Секрет не загружается на GitHub.");
  flushQueue();
}
function saveResult(data) {
  if (!data || !["defeat", "victory", "draw"].includes(data.outcome)) return;
  const sent = fromStorage(STORAGE.sent, []);
  const queue = fromStorage(STORAGE.queue, []);
  if (sent.includes(data.match_id) || queue.some(item => item.match_id === data.match_id)) return;
  queue.push(data);
  toStorage(STORAGE.queue, queue);
  renderQueue();
  logs(`Матч завершён: ${data.outcome}. Добавлен в очередь.`);
  flushQueue();
}
function finalize() {
  pendingFinishTimer = null;
  const result = collector.finish();
  if (result) saveResult(result);
  else logs("Результат матча ещё не известен: уведомление не создано.");
}
function scheduleFinish(resetTimer = false) {
  if (pendingFinishTimer && !resetTimer) return;
  if (pendingFinishTimer) clearTimeout(pendingFinishTimer);
  // Некоторые обновления (включая исход и финальный счёт) приходят после match_end.
  pendingFinishTimer = setTimeout(finalize, 6000);
}

async function request(path, body) {
  const { endpoint, token } = getConfig();
  if (!endpoint || !token) throw new Error("Сначала сохрани настройки Render");
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  try {
    return await fetch(endpoint + path, {
      method: body ? "POST" : "GET",
      headers: { "X-Bridge-Token": token, ...(body ? { "Content-Type": "application/json" } : {}) },
      ...(body ? { body: JSON.stringify(body) } : {}),
      signal: controller.signal
    });
  } finally { clearTimeout(timeout); }
}

async function testConnection() {
  try {
    const res = await request("/api/ping", null);
    if (res.ok) logs("Соединение с Render и BRIDGE_TOKEN работают.");
    else logs(`Проверка Render: HTTP ${res.status}. Проверь BRIDGE_TOKEN.`);
  } catch (error) { logs("Нет соединения с Render: " + String(error.message || error)); }
}

async function flushQueue() {
  if (flushBusy) return;
  const cfg = getConfig();
  if (!cfg.endpoint || !cfg.token) return;
  flushBusy = true;
  try {
    let queue = fromStorage(STORAGE.queue, []);
    while (queue.length > 0) {
      const current = queue[0];
      let response;
      try { response = await request("/api/match", current); }
      catch (error) {
        logs("Нет сети / Render спит. Повтор через 30 секунд.");
        break;
      }
      if (!response.ok) {
        logs(`Ошибка сервера HTTP ${response.status}; очередь сохранена.`);
        // Не удаляем событие: после исправления настроек можно повторить.
        break;
      }
      const reply = await response.json().catch(() => ({}));
      logs(`Матч передан: ${reply.status || "OK"} (${current.outcome})`);
      const sent = fromStorage(STORAGE.sent, []);
      sent.push(current.match_id);
      toStorage(STORAGE.sent, sent.slice(-500));
      queue.shift();
      toStorage(STORAGE.queue, queue);
      renderQueue();
    }
  } finally { flushBusy = false; }
}

function onGameInfo(packet) {
  collector.applyInfo(packet);
  if (collector.s.outcome && !collector.s.locked) scheduleFinish();
}
function onGameEvents(packet) {
  // Если начинается новый матч, сначала завершаем предыдущий известный результат.
  const hasNewMatch = (packet.events || []).some(e => e.name === "match_start");
  if (hasNewMatch && pendingFinishTimer) {
    clearTimeout(pendingFinishTimer);
    finalize();
  }
  const changed = collector.applyEvents(packet);
  if (changed.started) logs("Матч Valorant начался.");
  if (changed.ended) {
    logs("Матч закончился. Считываем итоговые данные...");
    scheduleFinish(true);
  }
}
function registerGameEvents() {
  if (!window.overwolf?.games?.events) {
    status("Overwolf API недоступен. Запусти приложение через Overwolf.");
    return;
  }
  overwolf.games.events.setRequiredFeatures(["me", "game_info", "match_info"], reply => {
    if (reply?.success && reply.supportedFeatures?.includes("match_info")) {
      if (!featuresReady) logs("События Valorant подключены.");
      featuresReady = true;
      status("Отслеживание Valorant активно");
    } else {
      featuresReady = false;
      status("События не подключены. Проверь Overwolf и запущенную игру.");
      logs("Overwolf GEP: " + String(reply?.reason || reply?.error || "ожидание игры"));
    }
  });
}

function initOverwolf() {
  if (!window.overwolf?.games?.events) {
    status("Открой эту программу через Overwolf, не браузер.");
    return;
  }
  overwolf.games.events.onInfoUpdates2.addListener(onGameInfo);
  overwolf.games.events.onNewEvents.addListener(onGameEvents);
  overwolf.games.events.onError.addListener(e => logs("Overwolf: " + String(e?.error || "ошибка GEP")));
  overwolf.games.onGameInfoUpdated.addListener(e => {
    const game = e.gameInfo;
    const isValorant = Boolean(game?.isRunning &&
      (game.classId === 21640 || Math.floor((game.id || 0) / 10) === 21640));
    if (isValorant && !currentGame) {
      currentGame = true;
      registerGameEvents();
    } else if (!isValorant) {
      currentGame = false;
    }
  });
  overwolf.games.getRunningGameInfo2(data => {
    const game = data?.gameInfo || data;
    if (game?.isRunning && (game.classId === 21640 || Math.floor((game.id || 0) / 10) === 21640)) {
      currentGame = true;
      registerGameEvents();
    } else {
      status("Ожидание запуска Valorant");
    }
  });
}

window.addEventListener("DOMContentLoaded", () => {
  const config = getConfig();
  endpointInput.value = config.endpoint;
  tokenInput.value = config.token;
  $("save").addEventListener("click", saveConfig);
  $("test").addEventListener("click", testConnection);
  $("flush").addEventListener("click", flushQueue);
  $("showToken").addEventListener("change", e => {
    tokenInput.type = e.target.checked ? "text" : "password";
  });
  renderQueue();
  logs("Локальное приложение запущено.");
  initOverwolf();
  flushQueue();
  setInterval(flushQueue, 30000);
});
