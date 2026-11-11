// Проверяем сбор событий на примерах, похожих на официальные данные GEP.
const assert = require('node:assert/strict');
const { Collector, modeName, parseMaybeJSON } = require('../collector.js');
assert.equal(modeName('{"mode":"bomb","custom":false,"ranked":"1"}'), 'Competitive');
assert.equal(modeName('{"mode":"swift","custom":false,"ranked":"2"}'), 'Swiftplay');
assert.equal(parseMaybeJSON('"hit":9,"headshot":3').hit, 9);
const c = new Collector();
c.applyInfo({info:{match_info:{map:'Ascent',game_mode:'{"mode":"bomb","ranked":"1"}'}}});
c.applyEvents({events:[{name:'match_start',data:''}]});
c.applyInfo({info:{match_info:{
  match_id:'0c0ea3df-97ea-4d3a-b1f6-f8e34042251f',
  score:'{"won":9,"lost":13}',
  scoreboard_0:'{"character":"Wushu","kills":18,"deaths":15,"assists":6,"is_local":true}',
  match_outcome:'defeat'
}}});
c.applyEvents({events:[{name:'match_end',data:''}]});
const result = c.finish();
assert.equal(result.outcome,'defeat');
assert.equal(result.mode,'Competitive');
assert.equal(result.map,'Ascent');
assert.equal(result.agent,'Jett');
assert.equal(result.won,9);
assert.equal(result.lost,13);
assert.equal(result.kills,18);
assert.equal(c.finish(),null);
// Вторая игра: карта пришла ещё до match_start после завершения первой.
c.applyInfo({info:{match_info:{map:'Pitt'}}});
c.applyEvents({events:[{name:'match_start',data:''}]});
assert.equal(c.s.map,'Pearl');
assert.equal(c.s.id,null);
const win = new Collector();
win.applyInfo({category:'match_info', key:'match_outcome',value:'victory'});
assert.equal(win.finish().outcome,'victory');
console.log('Тесты Overwolf collector: OK');
