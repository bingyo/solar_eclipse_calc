/* 日食・太陽面通過 精密計算機 - front-end */
'use strict';

const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') e.className = v;
    else if (k === 'html') e.innerHTML = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    e.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return e;
}
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const state = {
  info: null, obsTab: 'ground', satMode: 'celestrak', kInput: 'plan', fetchedTle: null, sscSats: null,
  sweep: null, sweepGroup: 0,
  result: null, filters: new Set(), selectedId: null,
  detail: null, local: null, stack: [], rootId: null, saved: null, view: 'welcome',
  t: 0, playing: false, mapData: {}, map: null, mapLayers: null, pickMap: null,
  tleInfo: null, downloadable: [], layerControls: [],
  tleReq: 0, satAutoName: '',     // the latest TLE fetch; the satellite name the page filled in (not typed)
  listSeq: 0, detailSeq: 0,       // bumped when another list / detail is shown, so that a late answer is dropped
  exact: null,                     // {t, row}: the viewer shows the exact values of a contact or of /at (not interpolated)
  cmp: null,                       // the rows of "観測画像と比べる"
  fullContacts: null,              // the contact tables with the values for images (null: by the event)
  sunRPrev: null,                  // the solar radius model chosen before "数値で指定"
};

/* ------------------------------------------------------------------ */
/* time & number formatting                                            */
/* ------------------------------------------------------------------ */
const TZ_OPTIONS = [
  ['Asia/Tokyo', '日本時間 (JST)'], ['UTC', '世界時 (UTC)'], ['local', 'このPCの時刻'],
  ['Asia/Seoul', '韓国'], ['Asia/Shanghai', '中国'], ['Asia/Singapore', 'シンガポール'],
  ['Australia/Sydney', 'シドニー'], ['Pacific/Honolulu', 'ハワイ'],
  ['America/Los_Angeles', '米国太平洋'], ['America/Chicago', '米国中部'], ['America/New_York', '米国東部'],
  ['Europe/London', '英国'], ['Europe/Paris', '中央ヨーロッパ'], ['Europe/Moscow', 'モスクワ'],
  ['Africa/Cairo', 'エジプト'], ['Asia/Kolkata', 'インド'], ['America/Mexico_City', 'メキシコ'],
];
const _fmtCache = new Map();
function tzId() { const v = $('#tz').value; return v === 'local' ? undefined : v; }
/* Numbers of dates and times come from a fixed locale; with `local` the words (weekday, month,
   time zone) are in the language of the page. */
function dtf(opts, local = false) {
  const loc = local ? langLocale() : 'ja-JP';
  const key = loc + (tzId() || 'local') + JSON.stringify(opts);
  if (!_fmtCache.has(key)) _fmtCache.set(key, new Intl.DateTimeFormat(loc, { timeZone: tzId(), hourCycle: 'h23', ...opts }));
  return _fmtCache.get(key);
}
function parts(date, opts, local = false) {
  const o = {};
  for (const p of dtf(opts, local).formatToParts(date)) o[p.type] = p.value;
  return o;
}
/* The server writes a time in a leap second as 23:59:60.xxx, which Date cannot hold: it is
   taken as the end of 23:59:59 (the exact text is shown where the UTC is shown, fmtUtc). */
function toDate(iso) {
  if (iso instanceof Date) return iso;
  const d = new Date(iso);
  if (!isNaN(d) || typeof iso !== 'string') return d;
  const m = /^(.*T\d\d:\d\d):60(\.\d+)?Z?$/.exec(iso);
  return m ? new Date(m[1] + ':59.999Z') : d;
}
function msOf(iso) { return toDate(iso).getTime(); }
/* The time rounded to `digits` decimals of a second (to the minute with digits = -1), so that
   12:34:59.96 is shown as 12:35:00.0 (with the date of the rounded time). */
function roundTime(iso, digits = 0) {
  const unit = digits < 0 ? 60000 : Math.pow(10, 3 - Math.min(3, digits));
  return new Date(Math.round(msOf(iso) / unit) * unit);
}
function fmtDate(iso) {
  if (!iso) return '—';
  if (LANG !== 'ja') return dtf({ year: 'numeric', month: 'short', day: 'numeric', weekday: 'short' }, true).format(toDate(iso));
  const p = parts(toDate(iso), { year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short' });
  return `${p.year}年${+p.month}月${+p.day}日(${p.weekday})`;
}
function fmtDateShort(iso, digits = null) {
  if (!iso) return '—';
  const p = parts(digits == null ? toDate(iso) : roundTime(iso, digits), { year: 'numeric', month: '2-digit', day: '2-digit' });
  return `${p.year}/${p.month}/${p.day}`;
}
function fmtTime(iso, digits = 0) {
  if (!iso) return '—';
  const d = roundTime(iso, digits);
  const p = parts(d, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  let s = `${p.hour}:${p.minute}:${p.second}`;
  if (digits > 0) {
    const ms = ((d.getTime() % 1000) + 1000) % 1000;
    s += '.' + String(Math.round(ms / Math.pow(10, 3 - digits))).padStart(digits, '0');
  }
  return s;
}
function fmtHM(iso) {
  const p = parts(roundTime(iso, -1), { hour: '2-digit', minute: '2-digit' });
  return `${p.hour}:${p.minute}`;
}
function fmtDT(iso, digits = 0) { return iso ? `${fmtDateShort(iso, digits)} ${fmtTime(iso, digits)}` : '—'; }
/* The UTC date and time to the millisecond as the server gives it ("2017-08-21 16:53:59.204"). */
function fmtUtc(iso) { return iso ? String(iso).replace('T', ' ').replace(/Z$/, '') : '—'; }
/* "（UT 8月21日）" when the date in the shown time zone is not the date in UT, else ''. */
function utDateNote(iso) {
  if (!iso || tzId() === 'UTC') return '';
  const d = toDate(iso);
  const p = parts(d, { year: 'numeric', month: '2-digit', day: '2-digit' });
  if (`${p.year}-${p.month}-${p.day}` === d.toISOString().slice(0, 10)) return '';
  const md = new Intl.DateTimeFormat(langLocale(), { timeZone: 'UTC', month: wideScript() ? 'long' : 'short', day: 'numeric' }).format(d);
  return paren(t('UT {date}', { date: md }));
}
function tzLabel(iso) {
  if (tzId() === 'UTC') return 'UTC';
  const p = parts(toDate(iso || Date.now()), { timeZoneName: 'short', hour: '2-digit' }, true);
  return p.timeZoneName || '';
}
function dayDiff(isoA, isoB) {
  const a = parts(toDate(isoA), { year: 'numeric', month: '2-digit', day: '2-digit' });
  const b = parts(toDate(isoB), { year: 'numeric', month: '2-digit', day: '2-digit' });
  return `${a.year}${a.month}${a.day}` !== `${b.year}${b.month}${b.day}`;
}
function fmtDur(s, precise = false) {
  if (s == null || !isFinite(s)) return '—';
  const r = precise ? Math.round(s * 10) / 10 : Math.round(s);   // round first: 59.6 s is "1 min 00 s", not "60 s"
  if (r < 60) return t('{s}秒', { s: r.toFixed(precise ? 1 : 0) });
  if (r < 3600) {
    const m = Math.floor(r / 60), x = r - 60 * m;
    return t('{m}分{s}秒', { m, s: precise ? x.toFixed(1).padStart(4, '0') : String(Math.round(x)).padStart(2, '0') });
  }
  const m = Math.round(s / 60);
  return t('{h}時間{m}分', { h: Math.floor(m / 60), m: String(m % 60).padStart(2, '0') });
}
const f1 = (v) => (v == null ? '—' : Number(v).toFixed(1));
const f3 = (v) => (v == null ? '—' : Number(v).toFixed(3));
const f4 = (v) => (v == null ? '—' : Number(v).toFixed(4));
const pct = (v, d = 1) => (v == null ? '—' : (100 * v).toFixed(d) + '%');
/* A value with its sign: +1.2 / −1.2 (the minus sign, as in tables) */
function signed(v, d = 1) {
  if (v == null || !isFinite(v)) return '—';
  const s = Math.abs(v).toFixed(d);
  return (+s === 0 ? '±' : v > 0 ? '+' : '−') + s;
}
const AU_KM = 149597870.7, RAD2ARCSEC = 180 * 3600 / Math.PI;
/* A radius in km and its angle at 1 AU (″) */
const kmToArcsec = (km) => km / AU_KM * RAD2ARCSEC;
const arcsecToKm = (as) => as / RAD2ARCSEC * AU_KM;
function fmtInt(v) { return Math.round(v).toLocaleString(langLocale()); }
// the sign is judged after rounding (no "南緯 0.00°" for -0.001), as cli.py
function fmtLat(v) { if (v == null) return '—'; const a = Math.abs(v).toFixed(2); return v >= 0 || +a === 0 ? t('北緯 {v}°', { v: a }) : t('南緯 {v}°', { v: a }); }
function fmtLon(v) { if (v == null) return '—'; const a = Math.abs(v).toFixed(2); return v >= 0 || +a === 0 ? t('東経 {v}°', { v: a }) : t('西経 {v}°', { v: a }); }
function km(v) { return t('{v} km', { v }); }
function fmtLatLon(la, lo) { return `${fmtLat(la)}${sep()}${fmtLon(lo)}`; }
function azName(a) {
  return compassPoints()[Math.round(((a % 360) + 360) % 360 / 22.5) % 16];
}

/* ------------------------------------------------------------------ */
/* API                                                                 */
/* ------------------------------------------------------------------ */
async function api(path, body) {
  const headers = { 'X-Lang': LANG };   // errors and warnings in the language of the page
  const opt = body ? { method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : { headers };
  const res = await fetch(path, opt);
  let data = null;
  try { data = await res.json(); } catch (_) { /* ignore */ }
  if (!res.ok) {
    const d = data && data.detail;
    const err = new Error(typeof d === 'string' ? d : d ? d.map((x) => x.msg).join('\n') : `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}
function setStatus(elm, msg, kind = '') {
  elm.className = 'status' + (kind ? ' ' + kind : '');
  elm.innerHTML = kind === 'busy' ? `<span class="spinner"></span>${esc(msg)}` : esc(msg);
}

/* ------------------------------------------------------------------ */
/* labels                                                              */
/* ------------------------------------------------------------------ */
const CAT = {
  total: { get label() { return t('皆既日食'); }, cls: 'total', color: '#262b38' },
  annular: { get label() { return t('金環日食'); }, cls: 'annular', color: '#e8850c' },
  hybrid: { get label() { return t('金環皆既日食'); }, cls: 'hybrid', color: '#8a3fd1' },
  partial: { get label() { return t('部分日食'); }, cls: 'partial', color: '#8f9bb3' },
  mercury: { get label() { return t('水星の太陽面通過'); }, cls: 'mercury', color: '#2b8fbf' },
  venus: { get label() { return t('金星の太陽面通過'); }, cls: 'venus', color: '#c9a227' },
};
function catOf(e) { return e.body === 'moon' ? e.type : e.body; }
/* The kind of an event, e.g. "皆既日食" or "水星の太陽面通過（外接のみ）". */
function typeLabel(e) {
  return CAT[catOf(e)].label + (e.body !== 'moon' && e.type === 'transit_grazing' ? t('（外接のみ）') : '');
}
function badge(e) {
  const c = CAT[catOf(e)];
  let label = typeLabel(e);
  if (e.kind === 'global' && e.noncentral) label += t('（非中心）');
  return el('span', { class: 'badge ' + c.cls }, label);
}
function contactName(label, e) {
  const moon = e.body === 'moon';
  const map = moon ? {
    C1: [t('第1接触'), t('欠け始め')],
    C2: [t('第2接触'), e.type === 'total' ? t('皆既の始まり') : e.type === 'annular' ? t('金環の始まり') : t('中心食の始まり')],
    MAX: [t('食の最大'), ''],
    C3: [t('第3接触'), e.type === 'total' ? t('皆既の終わり') : e.type === 'annular' ? t('金環の終わり') : t('中心食の終わり')],
    C4: [t('第4接触'), t('欠け終わり')],
  } : {
    C1: [t('第1接触'), t('外接・入り始め')], C2: [t('第2接触'), t('内接・入り終わり')], MAX: [t('最大'), t('太陽中心に最も近づく')],
    C3: [t('第3接触'), t('内接・出始め')], C4: [t('第4接触'), t('外接・出終わり')],
  };
  return map[label] || [label, ''];
}
function bodyJa(b) { return { moon: t('月'), mercury: t('水星'), venus: t('金星') }[b]; }

/* ------------------------------------------------------------------ */
/* initialisation                                                      */
/* ------------------------------------------------------------------ */
function isoDate(d) { return d.toISOString().slice(0, 10); }
function addYears(d, y) { const r = new Date(d); r.setUTCFullYear(r.getUTCFullYear() + y); return r; }

async function init() {
  const tz = $('#tz');
  initLanguage();
  let savedTz = null;
  try { savedTz = localStorage.getItem('tz'); } catch (_) { /* ignore */ }
  fillTzOptions(savedTz || (LANG === 'ja' ? 'Asia/Tokyo' : 'local'));
  tz.addEventListener('change', () => {
    _fmtCache.clear();
    try { localStorage.setItem('tz', tz.value); } catch (_) { /* ignore */ }
    if (state.sweep) renderSweep();
    else if (state.result) renderResults();
    if (state.detail) renderDetailAll(false);
  });

  const today = new Date();
  $('#start').value = isoDate(today);
  $('#end').value = isoDate(addYears(today, 10));
  const ep = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()));
  $('#kEpoch').value = ep.toISOString().slice(0, 19);

  try {
    state.info = await api('/api/info');
  } catch (err) {
    setStatus($('#status'), t('サーバーに接続できません: {msg}', { msg: err.message }), 'err');
    return;
  }
  const info = state.info;
  if (info.app_mode) {
    $('#quitBtn').hidden = false;
    $('#quitBtn').addEventListener('click', quitApp);
  }
  if (info.quit_when_closed) watchPage();
  const cp = $('#cityPreset');
  fillPresets();
  cp.addEventListener('change', () => {
    const c = info.cities[cp.value];
    if (!c) return;
    $('#lat').value = c.lat; $('#lon').value = c.lon; $('#elev').value = c.elevation_m; $('#placeName').value = t(c.name);
    updatePickMarker();
  });
  if ($('#placeName').value === '東京') $('#placeName').value = t('東京');
  const sp = $('#satPreset');
  sp.addEventListener('change', () => { const s = info.satellites[sp.value]; if (s) applySatSpec(presetSpec(s.spec)); });
  const eph = $('#ephem');
  fillEphemerides(info.ephemerides);
  eph.value = info.default_ephemeris;
  eph.addEventListener('change', updateCoverage);
  updateCoverage();
  state.downloadable = info.downloadable_ephemerides || [];
  renderEphemerisDownloads();

  $$('#obsTabs button').forEach((b) => b.addEventListener('click', () => setObsTab(b.dataset.obs)));
  $$('#satModes button').forEach((b) => b.addEventListener('click', () => {
    if (b.dataset.mode !== state.satMode) {     // a name the page filled in for another source does not carry over
      $('#satPreset').value = '';
      if ($('#satName').value === state.satAutoName) $('#satName').value = '';
    }
    setSatMode(b.dataset.mode);
  }));
  $('#fetchTle').addEventListener('click', fetchTle);
  $('#sscId').addEventListener('change', () => showSscInfo(true));
  $$('#kInput button').forEach((b) => b.addEventListener('click', () => setKeplerInput(b.dataset.kin)));
  ['#kSso', '#kPlane', '#kSweep', '#kPeri', '#kApo', '#kA', '#kE'].forEach((q) => {
    $(q).addEventListener('change', updateKeplerForm);
    $(q).addEventListener('input', updateKeplerForm);
  });
  updateKeplerForm();
  $('#pickToggle').addEventListener('click', togglePickMap);
  $$('#quickRange button').forEach((b) => b.addEventListener('click', () => quickRange(b)));
  $('#dtMode').addEventListener('change', () => { $('#dtValue').disabled = $('#dtMode').value !== 'manual'; });
  $('#sunR').addEventListener('change', () => {
    const v = $('#sunR').value, wrap = $('#sunRCustomWrap');
    if (v === 'custom' && wrap.hidden) {     // start from the model chosen before
      const prev = state.sunRPrev && state.info.radius_presets.sun[state.sunRPrev];
      if (prev) setSunRCustom(prev);
    }
    wrap.hidden = v !== 'custom';
    if (v !== 'custom') state.sunRPrev = v;
  });
  state.sunRPrev = $('#sunR').value;
  // the custom solar radius in km or as the angle at 1 AU (the value is converted)
  $('#sunRUnit').addEventListener('change', () => {
    const v = parseFloat($('#sunRCustom').value);      // in the unit chosen before
    if (isFinite(v)) setSunRCustom($('#sunRUnit').value === 'as' ? v : arcsecToKm(v));
  });
  $('#moonR').addEventListener('change', () => { $('#moonRCustomWrap').hidden = $('#moonR').value !== 'custom'; });
  $('#form').addEventListener('submit', (ev) => { ev.preventDefault(); runSearch(); });
  $('#helpBtn').addEventListener('click', () => $('#helpDlg').showModal());
  $('#csvBtn').addEventListener('click', downloadListCsv);
  $('#saveBtn').addEventListener('click', saveResult);
  // (delegated: the welcome text with one of these buttons is replaced when the language changes)
  document.addEventListener('click', (ev) => { if (ev.target.closest('[data-open]')) $('#openFile').click(); });
  $('#openFile').addEventListener('change', (ev) => {
    const f = ev.target.files[0];
    ev.target.value = '';
    if (f) openSavedFile(f);
  });
  $('#closeDetail').addEventListener('click', closeDetail);
  $('#toListBtn').addEventListener('click', () => scrollToEl($('#resultArea')));
  $$('#viewNav button').forEach((b) => b.addEventListener('click', () => goView(b.dataset.view)));
  $('#backBtn').addEventListener('click', goBack);
  $$('#detailTabs button').forEach((b) => b.addEventListener('click', () => showTab(b.dataset.tab)));
  $$('[data-example]').forEach((b) => b.addEventListener('click', () => runExample(b.dataset.example)));
  initViewer();
  $('#dlJson').addEventListener('click', downloadJson);
  $('#dlCsv').addEventListener('click', downloadSeriesCsv);
  $('#copyText').addEventListener('click', copyText);
  $('#sheetOrient').addEventListener('change', renderSheet);
  $('#dlSheetPng').addEventListener('click', downloadSheetPng);
  $('#dlSheetSvg').addEventListener('click', downloadSheetSvg);
}

/* ------------------------------------------------------------------ */
/* language                                                            */
/* ------------------------------------------------------------------ */
function initLanguage() {
  const sel = $('#lang');
  sel.replaceChildren(...LANGS.map(([v, name]) => el('option', { value: v }, name)));
  sel.value = LANG;
  translatePage();
  sel.addEventListener('change', () => changeLanguage(sel.value));
}
/* Switch the language of the page, keeping what is entered and shown. */
function changeLanguage(lang) {
  const prev = LANG;
  setLanguage(lang);
  $('#lang').value = LANG;
  _fmtCache.clear();
  fillTzOptions();
  fillSpeedOptions();
  $('#copyText').textContent = t('結果をテキストでコピー');
  const info = state.info;
  if (!info) return;
  retranslateInput('#placeName', [...info.cities.map((c) => c.name), '地図で選んだ地点'], prev);
  const autoName = $('#satName').value === state.satAutoName;
  retranslateInput('#satName', [...info.satellites.map((s) => s.spec.name), PRELAUNCH_NAME], prev);
  if (autoName) state.satAutoName = $('#satName').value;
  fillPresets();
  fillEphemerides([...$('#ephem').options].map((o) => o.value));
  renderEphemerisDownloads();
  updateKeplerForm();
  updateCoverage();
  if (state.sscSats) showSscInfo(false);
  if (state.tleInfo && !$('#satInfo').hidden) renderTleInfo();
  state.layerControls.forEach(relabelLayerControl);
  if (state.sweep) renderSweep();
  else if (state.result) renderResults();
  if (state.detail) renderDetailAll(false);
}
/* A name entered from a preset follows the language; one typed by hand is kept. */
function retranslateInput(sel, keys, prev) {
  const v = $(sel).value;
  const key = keys.find((k) => k && tIn(prev, k) === v);
  if (key) $(sel).value = t(key);
}
function fillTzOptions(value = $('#tz').value) {
  const tz = $('#tz');
  tz.replaceChildren(...TZ_OPTIONS.map(([v, l]) => el('option', { value: v }, t(l))));
  if (TZ_OPTIONS.some(([v]) => v === value)) tz.value = value;
}
function fillPresets() {
  const fill = (sel, items, label) => {
    const s = $(sel), cur = s.value;
    s.replaceChildren(s.options[0], ...items.map((x, i) => el('option', { value: i }, label(x))));
    s.value = cur;
  };
  fill('#cityPreset', state.info.cities, (c) => t(c.name));
  fill('#satPreset', state.info.satellites, (s) => t(s.label));
}
function presetSpec(spec) { return { ...spec, name: t(spec.name) }; }

// The calculator quits by itself when its last page is closed: a page keeps this event stream
// open, and the browser drops it when the tab or the browser is closed (see server.py).  One stream
// per browser is enough, and a browser opens only 6 connections to a server, so its pages take
// turns (Web Locks): the others wait for the lock and hear from the holder when the server stops.
function watchPage() {
  if (window.BroadcastChannel) {
    state.pageChannel = new BroadcastChannel('eclipsecalc-page');
    state.pageChannel.onmessage = (ev) => { if (ev.data === 'stopped' && !state.stopped) showStopped(t('日食計算機は終了しています')); };
  }
  const open = () => new Promise(() => {      // the lock is held while the page is open
    state.pageStream = new EventSource('/api/page/stream');
    state.pageStream.onerror = () => {
      if (state.stopped) return;
      fetch('/api/info').catch(() => showStopped(t('日食計算機は終了しています')));
    };
  });
  if (navigator.locks) navigator.locks.request('eclipsecalc-page-stream', open);
  else open();
}
function showStopped(title) {
  state.stopped = true;
  if (state.pageStream) state.pageStream.close();
  if (state.pageChannel) state.pageChannel.postMessage('stopped');
  const again = state.info && state.info.app_mode ? t('「日食計算機」アプリを開いてください') : t('start.bat（Mac・Linux は start.command）を起動してください');
  document.body.innerHTML = '';
  document.body.append(el('div', { class: 'quit-msg' },
    el('h2', {}, title),
    el('p', {}, t('このタブは閉じてかまいません。もう一度使うときは{again}。', { again }))));
}
async function quitApp() {
  if (!confirm(t('日食計算機を終了しますか？'))) return;
  state.stopped = true;
  try { await api('/api/shutdown', {}); } catch (_) { /* already stopped */ }
  showStopped(t('日食計算機を終了しました'));
}

function fillEphemerides(names) {
  const eph = $('#ephem');
  const cur = eph.value;
  eph.innerHTML = '';
  names.forEach((n) => eph.append(el('option', { value: n }, n + (n === 'de440s.bsp' ? t('（1849〜2150年）') : n === 'de440.bsp' ? t('（1550〜2650年）') : ''))));
  if (names.includes(cur)) eph.value = cur;
}
function renderEphemerisDownloads(list = state.downloadable) {
  const box = $('#ephemDlButtons');
  box.innerHTML = '';
  $('#ephemDl').hidden = !list.length;
  list.forEach((d) => box.append(el('button', {
    type: 'button', class: 'secondary small', onclick: (ev) => downloadEphemeris(d.name, ev.currentTarget),
  }, t('{name} を追加（{desc}）', { name: d.name, desc: t(d.description) }))));
}
async function downloadEphemeris(name, btn) {
  const st = $('#ephemDlStatus');
  btn.disabled = true;
  setStatus(st, t('JPL から {name} をダウンロード中…（数分かかることがあります）', { name }), 'busy');
  try {
    const d = await api('/api/ephemeris/download?name=' + encodeURIComponent(name), {});
    fillEphemerides(d.ephemerides);
    $('#ephem').value = name;
    updateCoverage();
    state.downloadable = d.downloadable_ephemerides;
    renderEphemerisDownloads();
    $('#ephemDl').hidden = false;
    setStatus(st, t('{name} を追加し、暦として選択しました。', { name }));
  } catch (err) {
    btn.disabled = false;
    setStatus(st, err.message, 'err');
  }
}

async function updateCoverage() {
  try {
    const c = await api('/api/ephemeris_coverage?name=' + encodeURIComponent($('#ephem').value));
    $('#coverageHint').textContent = t('選択中の暦で計算できる期間: {start} 〜 {end}', { start: c.start, end: c.end });
    state.coverage = c;
  } catch (err) { $('#coverageHint').textContent = err.message; }
}

function setObsTab(tab) {
  state.obsTab = tab;
  $$('#obsTabs button').forEach((b) => b.classList.toggle('active', b.dataset.obs === tab));
  $$('.obs-pane').forEach((p) => { p.hidden = p.dataset.pane !== tab; });
  if (tab === 'ground' && state.pickMap) setTimeout(() => state.pickMap.invalidateSize(), 50);
}
function setSatMode(mode) {
  state.satMode = mode;
  $$('#satModes button').forEach((b) => b.classList.toggle('active', b.dataset.mode === mode));
  $$('.sat-pane').forEach((p) => { p.hidden = p.dataset.sat !== mode; });
  if (mode === 'sscweb') loadSscList();
}
async function loadSscList() {
  if (state.sscSats) { showSscInfo(false); return; }
  const box = $('#sscInfo');
  box.hidden = false;
  box.innerHTML = '<span class="spinner"></span>' + esc(t('NASA SSCWeb の衛星一覧を取得中…'));
  try {
    const d = await api('/api/sscweb/satellites');
    state.sscSats = d.satellites;
    const dl = $('#sscList');
    dl.replaceChildren(...d.satellites.map((s) => el('option', { value: s.id, label: s.name + paren(`${s.start.slice(0, 10)} – ${s.end.slice(0, 10)}`) })));
    showSscInfo(false);
  } catch (err) {
    box.innerHTML = `<span style="color:var(--err)">${esc(err.message)}</span>`;
  }
}
function showSscInfo(setName) {
  const box = $('#sscInfo');
  if (!state.sscSats) return;
  const id = $('#sscId').value.trim().toLowerCase();
  const s = state.sscSats.find((x) => x.id === id);
  box.hidden = !id;
  if (!s) { box.innerHTML = `<span style="color:var(--err)">${esc(t('「{id}」は SSCWeb の衛星一覧にありません', { id }))}</span>`; return; }
  if (setName) $('#satName').value = state.satAutoName = s.name;
  box.innerHTML = `<b>${esc(s.name)}</b><br>` +
    esc(t('軌道データ: {start} 〜 {end}（{res} 秒間隔）', { start: s.start.slice(0, 10), end: s.end.slice(0, 10), res: s.resolution_s }));
}
/* A UTC time as cli.py accepts it (2027-1-1, 2027-01-01 5:30, ...Z) -> 'YYYY-MM-DDTHH:MM:SS' of the form. */
function formUtc(s) {
  const m = /^(\d{1,4})-(\d{1,2})-(\d{1,2})(?:[T ](\d{1,2}):(\d{2})(?::(\d{2}))?)?/.exec(String(s).trim());
  if (!m) return '';
  const p = (x, n = 2) => String(+(x || 0)).padStart(n, '0');
  return `${p(m[1], 4)}-${p(m[2])}-${p(m[3])}T${p(m[4])}:${p(m[5])}:${p(m[6])}`;
}
/* Line 2 of a TLE with the mean anomaly replaced (as eclipsecalc.observers.tle_with_mean_anomaly). */
function tleWithMeanAnomaly(line2, m) {
  const body = line2.slice(0, 43) + (((+m % 360) + 360) % 360).toFixed(4).padStart(8) + line2.slice(51, 68);
  return body + [...body].reduce((sum, c) => sum + (c === '-' ? 1 : /\d/.test(c) ? +c : 0), 0) % 10;
}
function applySatSpec(spec) {
  $('#satName').value = state.satAutoName = spec.name || '';
  state.fetchedTle = null; state.tleInfo = null; state.tleReq++;
  $('#satInfo').hidden = true;
  if (spec.type === 'celestrak') { setSatMode('celestrak'); $('#norad').value = spec.norad; }
  else if (spec.type === 'geo') { setSatMode('geo'); $('#geoLon').value = spec.lon; }
  else if (spec.type === 'horizons') { setSatMode('horizons'); $('#hzId').value = spec.command; $('#hzStep').value = spec.step_min; }
  else if (spec.type === 'sscweb') { $('#sscId').value = spec.id; setSatMode('sscweb'); }
  else if (spec.type === 'tle') {
    setSatMode('tle');
    const line2 = spec.m_deg != null ? tleWithMeanAnomaly(spec.line2, spec.m_deg) : spec.line2;   // one phase of a sweep
    $('#tleText').value = [spec.name, spec.line1, line2].filter(Boolean).join('\n');
  } else if (spec.type === 'kepler') {
    setSatMode('kepler');
    if (spec.epoch) $('#kEpoch').value = formUtc(spec.epoch);
    $('#kJ2').checked = spec.j2 !== false;
    let peri = spec.perigee_alt_km, apo = spec.apogee_alt_km;
    if (spec.a_km) {
      const e = spec.e ?? 0;
      peri = spec.a_km * (1 - e) - R_EARTH; apo = spec.a_km * (1 + e) - R_EARTH;
      $('#kA').value = spec.a_km; $('#kE').value = e;
    }
    if (peri != null) { $('#kPeri').value = +peri.toFixed(3); $('#kApo').value = +apo.toFixed(3); }
    $('#kSso').checked = !!spec.sso;
    if (spec.i_deg != null) $('#kInc').value = spec.i_deg;
    if (spec.ltan_h != null) { $('#kPlane').value = 'ltan'; $('#kLtan').value = fmtLtan(spec.ltan_h); }
    else { $('#kPlane').value = 'raan'; $('#kRaan').value = spec.raan_deg ?? 0; }
    $('#kArgp').value = spec.argp_deg ?? 0; $('#kM').value = spec.m_deg ?? 0;
    // the six elements cannot hold the sun-synchronous / LTAN shortcuts; the plan form holds every combination
    setKeplerInput(spec.a_km && !spec.sso && spec.ltan_h == null ? 'elements' : 'plan', false);
  }
}
/* "plan": perigee/apogee altitudes with sun-synchronous / LTAN shortcuts;
   "elements": the six classical elements as given (a, e, i, RAAN, argp, M).
   Switching converts the size and shape so that the same orbit is kept. */
function setKeplerInput(mode, convert = true) {
  if (convert && mode !== state.kInput) {
    if (mode === 'elements') {
      const p = num('#kPeri', NaN), q = num('#kApo', NaN);
      if (isFinite(p) && isFinite(q)) {
        const a = R_EARTH + 0.5 * (p + q);
        $('#kA').value = +a.toFixed(3); $('#kE').value = +((q - p) / (2 * a)).toFixed(7);
      }
    } else {
      const a = num('#kA', NaN), e = num('#kE', NaN);
      if (a > 0 && e >= 0 && e < 1) {
        $('#kPeri').value = +(a * (1 - e) - R_EARTH).toFixed(3); $('#kApo').value = +(a * (1 + e) - R_EARTH).toFixed(3);
      }
      // keep the entered inclination and node instead of the SSO / LTAN shortcuts
      $('#kSso').checked = false; $('#kPlane').value = 'raan';
    }
  }
  state.kInput = mode;
  $$('#kInput button').forEach((b) => b.classList.toggle('active', b.dataset.kin === mode));
  $$('[data-kin-pane]').forEach((p) => { p.hidden = p.dataset.kinPane !== mode; });
  updateKeplerForm();
}
/* Sun-synchronous inclination for the secular J2 model used by the server. */
const R_EARTH = 6378.137;
function ssoInclination(periKm, apoKm) {
  const R = R_EARTH, MU = 398600.4418, J2 = 1.08262668e-3;
  const a = R + 0.5 * (periKm + apoKm), e = (apoKm - periKm) / (2 * a);
  if (!(a > R) || !(e >= 0 && e < 1)) return null;
  const target = 2 * Math.PI / (365.24219 * 86400);
  const n0 = Math.sqrt(MU / a ** 3), f = 1.5 * J2 * (R / (a * (1 - e * e))) ** 2;
  let i = 98 * Math.PI / 180;
  for (let k = 0; k < 30; k++) {
    const n = n0 * (1 + f * Math.sqrt(1 - e * e) * (1 - 1.5 * Math.sin(i) ** 2));
    const c = -target / (f * n);
    if (c < -1) return null;
    i = Math.acos(c);
  }
  return i * 180 / Math.PI;
}
function ltanHours() {
  const [h, m] = ($('#kLtan').value || '').split(':').map(Number);
  if (!isFinite(h)) throw new Error(t('昇交点の地方時を入力してください（例: 18:00）'));
  return h + (isFinite(m) ? m : 0) / 60;
}
function fmtLtan(h) {
  const t = Math.round((((h % 24) + 24) % 24) * 60) % 1440;
  return `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}`;
}
function updateKeplerForm() {
  const elements = state.kInput === 'elements';
  const sso = !elements && $('#kSso').checked;
  $('#kInc').disabled = sso;
  const note = $('#kIncNote');
  if (sso) {
    const i = ssoInclination(num('#kPeri'), num('#kApo'));
    if (i == null) { note.textContent = t('この高度では太陽同期軌道になりません'); }
    else { $('#kInc').value = i.toFixed(3); note.textContent = t('太陽同期になるよう自動で決めた値'); }
  } else note.textContent = '';
  const ltan = !elements && $('#kPlane').value === 'ltan';
  $('#kLtanWrap').hidden = !ltan;
  $('#kRaanWrap').hidden = ltan;
  const sweep = $('#kSweep').checked;
  $('#kM').disabled = sweep;
  $('#kSweepWrap').hidden = !sweep;
  const a = num('#kA', NaN), e = num('#kE', NaN);
  $('#kANote').textContent = a > 0 && e >= 0 && e < 1
    ? t('高度 {lo}〜{hi} km', { lo: (a * (1 - e) - R_EARTH).toFixed(0), hi: (a * (1 + e) - R_EARTH).toFixed(0) }) + sep() +
      t('周期 {p} 分', { p: (2 * Math.PI * Math.sqrt(a ** 3 / 398600.4418) / 60).toFixed(1) })
    : '';
}
async function fetchTle() {
  const box = $('#satInfo'), norad = $('#norad').value, req = ++state.tleReq;
  // an answer that arrives after another satellite was chosen (number edited, preset, reopened result) is dropped
  const stale = () => req !== state.tleReq || $('#norad').value !== norad;
  box.hidden = false;
  box.innerHTML = '<span class="spinner"></span>' + esc(t('CelesTrak から取得中…'));
  try {
    const d = await api('/api/tle?norad=' + encodeURIComponent(norad));
    if (stale()) { if (req === state.tleReq) box.hidden = true; return; }
    state.fetchedTle = { norad: +norad, line1: d.line1, line2: d.line2, name: d.name };
    if (!$('#satName').value || $('#satPreset').value === '') $('#satName').value = state.satAutoName = d.name;
    state.tleInfo = d;
    renderTleInfo();
    $('#tleText').value = `${d.name}\n${d.line1}\n${d.line2}`;
  } catch (err) {
    if (stale()) { if (req === state.tleReq) box.hidden = true; return; }
    box.innerHTML = `<span style="color:var(--err)">${esc(err.message)}</span>`;
  }
}
function renderTleInfo() {
  const d = state.tleInfo;
  $('#satInfo').innerHTML = `<b>${esc(d.name)}</b><br>` + esc(t('元期: {epoch}', { epoch: `${fmtDT(d.epoch)} ${tzLabel(d.epoch)}` })) + '<br>' +
    esc([t('周期 {p} 分', { p: d.period_min.toFixed(1) }), t('近地点 {v} km', { v: d.perigee_km.toFixed(0) }),
      t('遠地点 {v} km', { v: d.apogee_km.toFixed(0) })].join(sep())) +
    `<br><small>${esc(t('TLE の予報精度は元期の前後数日が目安です。'))}</small>`;
}
function quickRange(b) {
  const today = new Date();
  if (b.dataset.y) {
    $('#start').value = isoDate(today);
    $('#end').value = isoDate(addYears(today, +b.dataset.y));
  } else {
    const [a, z] = b.dataset.range.split('-');
    $('#start').value = `${a}-01-01`;
    $('#end').value = `${+z + 1}-01-01`;
  }
}
function togglePickMap() {
  const box = $('#pickMap');
  box.hidden = !box.hidden;
  if (box.hidden) return;
  if (!state.pickMap) {
    const m = L.map(box, { worldCopyJump: true }).setView([+$('#lat').value || 35, +$('#lon').value || 135], 4);
    baseLayers(m);
    state.pickMarker = L.marker([+$('#lat').value, +$('#lon').value]).addTo(m);
    m.on('click', (ev) => {
      const ll = ev.latlng.wrap();
      $('#lat').value = ll.lat.toFixed(4); $('#lon').value = ll.lng.toFixed(4);
      $('#placeName').value = t('地図で選んだ地点'); $('#cityPreset').value = '';
      updatePickMarker();
    });
    state.pickMap = m;
  }
  setTimeout(() => state.pickMap.invalidateSize(), 50);
}
function updatePickMarker() {
  if (state.pickMarker) {
    const ll = [+$('#lat').value, +$('#lon').value];
    state.pickMarker.setLatLng(ll);
    state.pickMap.panTo(ll);
  }
}

/* ------------------------------------------------------------------ */
/* building the request                                                */
/* ------------------------------------------------------------------ */
function num(id, def = 0) { const v = parseFloat($(id).value); return isFinite(v) ? v : def; }
/* The radius models (/api/info radius_presets): the preset of a solar radius (km), and of the
   radii of Mercury and Venus (`p`: settings or params), or null for other values. */
const nearKm = (a, b) => a != null && b != null && Math.abs(a - b) < 1e-6;
function sunModel(km) {
  const rp = state.info.radius_presets.sun;
  return Object.keys(rp).find((k) => nearKm(rp[k], km)) || null;
}
function planetModel(p) {
  const rp = state.info.radius_presets;
  return Object.keys(rp.venus).find((k) => nearKm(rp.mercury[k], p.mercury_radius_km) && nearKm(rp.venus[k], p.venus_radius_km)) || null;
}
const SUN_MODEL_NAMES = { observed: '観測される縁', nasa: 'NASA 互換', iau2015: 'IAU 2015 公称値' };
const PLANET_MODEL_NAMES = { observed: '観測される円盤', iau: 'IAU 固体表面', nasa: 'NASA 互換', xray: 'X 線の円盤' };
/* The custom solar radius box set to `km`, in the unit chosen there. */
function setSunRCustom(km) {
  $('#sunRCustom').value = $('#sunRUnit').value === 'as' ? +kmToArcsec(km).toFixed(4) : +km.toFixed(1);
}
/* "太陽半径 696,231 km（観測される縁・1 AU で 959.96″）" */
function sunModelText(p) {
  const m = state.info ? sunModel(p.sun_radius_km) : null;
  const v = { km: fmtInt(p.sun_radius_km), as: kmToArcsec(p.sun_radius_km).toFixed(2) };
  return m ? t('太陽半径 {km} km（{model}・1 AU で {as}″）', { ...v, model: t(SUN_MODEL_NAMES[m]) }) : t('太陽半径 {km} km（1 AU で {as}″）', v);
}
/* "金星半径 6131.0 km（観測される円盤）"; the Moon's two radii */
function bodyModelText(body, p) {
  if (body === 'moon') return t('月半径 {ext} / {int} km（外接/内接）', { ext: p.moon_radius_ext_km.toFixed(2), int: p.moon_radius_int_km.toFixed(2) });
  const s = t('{body}半径 {v} km', { body: bodyJa(body), v: (body === 'venus' ? p.venus_radius_km : p.mercury_radius_km).toFixed(1) });
  const m = state.info ? planetModel(p) : null;
  return m ? s + paren(t(PLANET_MODEL_NAMES[m])) : s;
}
function buildRequest() {
  const phenomena = $$('input[name=ph]:checked').map((i) => i.value);
  let observer;
  if (state.obsTab === 'ground') {
    observer = { type: 'ground', lat: num('#lat'), lon: num('#lon'), elevation_m: num('#elev'), name: $('#placeName').value.trim() };
  } else if (state.obsTab === 'global') {
    observer = { type: 'global' };
  } else {
    const name = $('#satName').value.trim();
    const m = state.satMode;
    if (m === 'celestrak') {
      const ft = state.fetchedTle;
      observer = ft && ft.norad === num('#norad') ? { type: 'tle', line1: ft.line1, line2: ft.line2, name: name || ft.name }
        : { type: 'celestrak', norad: num('#norad'), name };
    } else if (m === 'tle') {
      const lines = $('#tleText').value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
      const i1 = lines.findIndex((s) => s.startsWith('1 '));
      if (i1 < 0 || !lines[i1 + 1] || !lines[i1 + 1].startsWith('2 ')) throw new Error(t('TLE の 1 行目（"1 "で始まる）と 2 行目（"2 "で始まる）を貼り付けてください'));
      observer = { type: 'tle', line1: lines[i1], line2: lines[i1 + 1], name: name || (i1 > 0 ? lines[i1 - 1] : '') };
    } else if (m === 'kepler') {
      const ep = $('#kEpoch').value;
      if (!ep) throw new Error(t('軌道要素の元期を入力してください'));
      observer = {
        type: 'kepler', epoch: ep.length === 16 ? ep + ':00' : ep,
        argp_deg: num('#kArgp'), m_deg: num('#kM'), j2: $('#kJ2').checked, name,
      };
      if (state.kInput === 'elements') {
        const a = num('#kA', NaN), e = num('#kE', NaN);
        if (!(a > 0)) throw new Error(t('軌道長半径を入力してください'));
        if (!(e >= 0 && e < 1)) throw new Error(t('軌道離心率は 0 以上 1 未満で入力してください'));
        Object.assign(observer, { a_km: a, e, i_deg: num('#kInc'), raan_deg: num('#kRaan') });
      } else {
        Object.assign(observer, { perigee_alt_km: num('#kPeri'), apogee_alt_km: num('#kApo') });
        if ($('#kSso').checked) observer.sso = true; else observer.i_deg = num('#kInc');
        if ($('#kPlane').value === 'ltan') observer.ltan_h = ltanHours(); else observer.raan_deg = num('#kRaan');
      }
    } else if (m === 'geo') {
      observer = { type: 'geo', lon: num('#geoLon'), name };
    } else if (m === 'sscweb') {
      const id = $('#sscId').value.trim().toLowerCase();
      if (!id) throw new Error(t('NASA SSCWeb の衛星 ID を入力してください（例: hinode）'));
      observer = { type: 'sscweb', id, name };
    } else {
      observer = { type: 'horizons', command: $('#hzId').value.trim(), step_min: num('#hzStep', 5), name };
    }
  }
  const rp = state.info.radius_presets;
  const sunSel = $('#sunR').value;
  const moonSel = $('#moonR').value;
  const plSel = $('#planetR').value;
  const radius = (id) => {
    const v = num(id, NaN);
    if (!(v > 0)) throw new Error(t('太陽・月の半径は正の数で入力してください'));
    return v;
  };
  const settings = {
    ephemeris: $('#ephem').value,
    delta_t: $('#dtMode').value === 'manual' && $('#dtValue').value !== '' ? num('#dtValue') : null,
    sun_radius_km: sunSel !== 'custom' ? rp.sun[sunSel] : $('#sunRUnit').value === 'as' ? arcsecToKm(radius('#sunRCustom')) : radius('#sunRCustom'),
    moon_radius_ext_km: moonSel === 'custom' ? radius('#moonRExt') : rp.moon[moonSel][0],
    moon_radius_int_km: moonSel === 'custom' ? radius('#moonRInt') : rp.moon[moonSel][1],
    mercury_radius_km: rp.mercury[plSel], venus_radius_km: rp.venus[plSel],
    min_sun_alt_deg: num('#minAlt'), refraction: $('#refraction').checked,
    earth_atm_km: num('#atm'), include_invisible: $('#includeInvisible').checked,
  };
  const sweepStep = observer.type === 'kepler' && $('#kSweep').checked ? num('#kSweepStep', 10) : null;
  return { phenomena, observer, start: $('#start').value, end: $('#end').value, settings, sweepStep };
}

async function runSearch() {
  const st = $('#status');
  let req;
  try { req = buildRequest(); } catch (err) { setStatus(st, err.message, 'err'); return; }
  if (!req.phenomena.length) { setStatus(st, t('計算する現象を 1 つ以上選んでください'), 'err'); return; }
  const btn = $('#runBtn');
  btn.disabled = true;
  const seq = ++state.listSeq;
  const msg = req.observer.type === 'horizons' ? t('JPL Horizons から軌道を取得して計算中…') :
    req.observer.type === 'sscweb' ? t('NASA SSCWeb から軌道を取得して計算中…') :
    req.observer.type === 'celestrak' ? t('CelesTrak から TLE を取得して計算中…') : t('計算中…');
  setStatus(st, req.sweepStep ? t('平均近点角を {step}° ずつ変えて計算中…', { step: req.sweepStep }) : msg, 'busy');
  try {
    if (req.sweepStep) {
      const { sweepStep, ...body } = req;
      const r = await api('/api/phase_sweep', { ...body, step_deg: sweepStep });
      if (seq !== state.listSeq) return;      // a saved result was opened meanwhile
      r.request = savedRequest(req, r);
      state.sweep = r;
      state.sweepGroup = 0;
      state.result = null;
      state.saved = null;
      setStatus(st, t('完了（{s} 秒）', { s: r.elapsed_s.toFixed(1) }));
      closeDetail();
      showView('results');
      renderSweep();
      return;
    }
    const r = await api('/api/search', req);
    if (seq !== state.listSeq) return;
    r.request = savedRequest(req, r);
    state.result = r;
    state.sweep = null;
    state.saved = null;
    state.filters = new Set();
    setStatus(st, t('完了（{s} 秒）', { s: r.elapsed_s.toFixed(1) }));
    closeDetail();
    showView('results');
    renderResults();
    if (r.events.length === 1) openDetail(r.events[0].id, { root: true });
  } catch (err) {
    if (seq === state.listSeq) setStatus(st, err.message, 'err');
  } finally {
    btn.disabled = false;
  }
}

const PRELAUNCH_NAME = '太陽同期 680km（計画）';
function runExample(name) {
  const now = new Date();
  $$('input[name=ph]').forEach((i) => { i.checked = true; });
  if (name === 'tokyo') {
    setObsTab('ground');
    const c = state.info.cities.find((x) => x.name === '東京');
    $('#lat').value = c.lat; $('#lon').value = c.lon; $('#elev').value = c.elevation_m; $('#placeName').value = t(c.name);
    $$('input[name=ph]').forEach((i) => { i.checked = i.value === 'moon'; });
    $('#start').value = isoDate(now); $('#end').value = isoDate(addYears(now, 30));
  } else if (name === 'global') {
    setObsTab('global');
    $('#start').value = '2001-01-01'; $('#end').value = '2101-01-01';
  } else if (name === 'iss') {
    setObsTab('space'); applySatSpec(presetSpec(state.info.satellites[0].spec));
    $$('input[name=ph]').forEach((i) => { i.checked = i.value === 'moon'; });
    $('#start').value = isoDate(now); $('#end').value = isoDate(addYears(now, 1));
  } else if (name === 'geo') {
    setObsTab('space'); applySatSpec(presetSpec(state.info.satellites[3].spec));
    $('#start').value = isoDate(now); $('#end').value = isoDate(addYears(now, 10));
  } else if (name === 'prelaunch') {
    setObsTab('space');
    applySatSpec({ type: 'kepler', name: t(PRELAUNCH_NAME), perigee_alt_km: 680, apogee_alt_km: 680, sso: true, ltan_h: 18, argp_deg: 0, m_deg: 0 });
    $('#kEpoch').value = '2027-01-01T00:00:00';
    $('#kSweep').checked = true; $('#kSweepStep').value = '10'; updateKeplerForm();
    $$('input[name=ph]').forEach((i) => { i.checked = i.value === 'moon'; });
    $('#start').value = '2027-01-01'; $('#end').value = '2028-01-01';
  } else if (name === 'hinode') {
    setObsTab('space'); applySatSpec(presetSpec(state.info.satellites.find((s) => s.spec.type === 'sscweb').spec));
    $$('input[name=ph]').forEach((i) => { i.checked = i.value === 'moon'; });
    $('#start').value = '2011-01-04'; $('#end').value = '2011-01-05';
  }
  runSearch();
}

/* ------------------------------------------------------------------ */
/* result list                                                         */
/* ------------------------------------------------------------------ */
function observerText(o) {
  if (!o) return '';
  if (o.kind === 'global') return t('地球全体');
  if (o.kind === 'ground') return o.name + paren(fmtLatLon(o.lat, o.lon) + sep() + t('標高 {v} m', { v: Math.round(o.elevation_m) }));
  if (o.kind === 'geocenter') return t('地球中心');
  const model = { tle: 'TLE/SGP4', kepler: t('軌道要素'), fixed: t('地球固定位置'), horizons: 'JPL Horizons', sscweb: 'NASA SSCWeb' }[o.model] || '';
  const items = [model];
  if (o.perigee_km != null) items.push(t('高度 {lo}〜{hi} km', { lo: o.perigee_km.toFixed(0), hi: o.apogee_km.toFixed(0) }));
  if (o.model === 'fixed') items.push(fmtLon(o.lon), t('高度 {v} km', { v: o.height_km.toFixed(0) }));
  if (o.model === 'kepler') {
    items.push(t('傾斜角 {v}°', { v: o.i_deg.toFixed(2) }));
    if (o.raan_deg != null && !o.sso) items.push(t('昇交点赤経 {v}°', { v: o.raan_deg.toFixed(2) }));
    if (o.ltan_h != null) items.push(t('昇交点の地方時 {v}', { v: fmtLtan(o.ltan_h) }));
    if (o.sso) items.push(t('太陽同期'));
  }
  if (o.epoch) items.push(t('元期 {v}', { v: fmtDT(o.epoch) }));
  return o.name + paren(items.join(sep()));
}

function renderResults() {
  const r = state.result;
  $('#listCard').hidden = false;
  $('#sweepArea').hidden = true;
  const evs = r.events;
  const global = r.observer.kind === 'global';
  const counts = {};
  evs.forEach((e) => { counts[catOf(e)] = (counts[catOf(e)] || 0) + 1; });
  const visCount = evs.filter((e) => e.kind === 'global' || (e.vis_fraction || 0) > 0).length;
  $('#summaryText').innerHTML =
    '<div>' + t('<span class="big">{n} 件</span> の現象が見つかりました', { n: evs.length }) +
    (!global && r.params.include_invisible ? esc(t('（うち見えるもの {n} 件）', { n: visCount })) : '') + '</div>' +
    savedNote() +
    `<div class="muted">${esc(t('観測者: {obs}', { obs: observerText(r.observer) }))}<br>` +
    esc([t('期間: {start} 〜 {end}（UTC）', { start: r.start, end: r.end }), t('暦 {name}', { name: r.ephemeris }),
      r.delta_t_override != null ? t('ΔT {v} 秒（手動）', { v: r.delta_t_override }) : t('ΔT 約 {v} 秒（期間中央）', { v: r.delta_t_mid_s.toFixed(1) }),
      t('計算 {s} 秒', { s: r.elapsed_s.toFixed(1) })].join(sep())) + '</div>';
  $('#warnings').innerHTML = (r.warnings || []).map((w) => `<div>⚠ ${esc(w)}</div>`).join('');

  const fbox = $('#filters');
  fbox.innerHTML = '';
  for (const [k, c] of Object.entries(CAT)) {
    if (!counts[k]) continue;
    const b = el('button', { type: 'button', class: state.filters.has(k) ? 'off' : '' },
      el('span', { class: 'dot', style: `background:${c.color}` }), `${c.label} ${counts[k]}`);
    b.addEventListener('click', () => { state.filters.has(k) ? state.filters.delete(k) : state.filters.add(k); renderResults(); });
    fbox.append(b);
  }

  const tbl = $('#eventTable');
  tbl.innerHTML = '';
  const head = global
    ? [t('日付'), t('種類'), t('最大食の時刻'), t('食分'), 'γ', t('中心食の継続'), t('中心食帯の幅'), t('最大食の地点'), t('サロス')]
    : [t('日付'), t('種類'), t('最大の時刻'), t('規模'), t('太陽が隠れる割合'), t('継続時間'),
      r.observer.kind === 'ground' ? t('最大時の太陽') : r.observer.kind === 'space' ? t('最大時の衛星位置') : '—', t('見えるか'), t('サロス')];
  tbl.append(el('thead', {}, el('tr', {}, head.map((h) => el('th', {}, h)))));
  const tb = el('tbody');
  for (const e of evs) {
    if (state.filters.has(catOf(e))) continue;
    const tr = el('tr', { 'data-id': e.id, class: e.id === state.selectedId ? 'selected' : '' });
    tr.addEventListener('click', () => openDetail(e.id, { root: true }));
    const isT = e.body !== 'moon';
    const cells = global ? globalRow(e, isT) : localRow(e, isT, r.observer.kind);
    tr.append(...cells);
    if (!global && (e.vis_fraction || 0) <= 0) tr.classList.add('dim');
    tb.append(tr);
  }
  tbl.append(tb);
  if (!tb.children.length) tb.append(el('tr', {}, el('td', { colspan: head.length, style: 'text-align:center;color:var(--muted);padding:20px' }, t('該当する現象はありません。期間や観測者を変えてお試しください。'))));
}

/* ------------------------------------------------------------------ */
/* phase sweep (satellite position along the orbit unknown)            */
/* ------------------------------------------------------------------ */
function phenLabel(b) { return { moon: t('日食'), mercury: t('水星の太陽面通過'), venus: t('金星の太陽面通過') }[b]; }
function renderSweep() {
  const r = state.sweep;
  $('#listCard').hidden = true;
  $('#sweepArea').hidden = false;
  const n = r.phases.length;
  const counts = {};
  r.groups.forEach((g) => { counts[g.body] = (counts[g.body] || 0) + 1; });
  const what = Object.entries(counts).map(([b, c]) => t('{what} {n} 件', { what: phenLabel(b), n: c })).join(sep()) || t('現象なし');
  $('#summaryText').innerHTML =
    `<div><span class="big">${esc(what)}</span>${esc(t('（平均近点角を {step}° ずつ変えた {n} 通りで計算）', { step: r.step_deg, n }))}</div>` +
    savedNote() +
    `<div class="muted">${esc(t('観測者: {obs}', { obs: observerText(r.observer) }))}<br>` +
    esc([t('期間: {start} 〜 {end}（UTC）', { start: r.start, end: r.end }), t('暦 {name}', { name: r.ephemeris }),
      t('計算 {s} 秒', { s: r.elapsed_s.toFixed(1) })].join(sep())) + '<br>' +
    esc(t('衛星が軌道上のどこにいるかで結果が変わります。行をクリックすると、平均近点角ごとの結果が表示されます。')) + '</div>';
  $('#warnings').innerHTML = (r.warnings || []).map((w) => `<div>⚠ ${esc(w)}</div>`).join('');
  $('#filters').innerHTML = '';
  const tbl = $('#sweepTable');
  tbl.innerHTML = '';
  const head = [t('日付'), t('現象'), t('見える位相'), t('見える回数'), t('最も深い食の食分'), t('皆既・金環になる位相'), t('最大の時刻の範囲')];
  tbl.append(el('thead', {}, el('tr', {}, head.map((h) => el('th', {}, h)))));
  const tb = el('tbody');
  r.groups.forEach((g, k) => {
    const tr = el('tr', { class: k === state.sweepGroup ? 'selected' : '' });
    tr.addEventListener('click', () => { state.sweepGroup = k; renderSweep(); });
    const isT = g.body !== 'moon';
    const central = g.central_phases.length;
    tr.append(
      el('td', {}, fmtDate(g.date)),
      el('td', {}, phenLabel(g.body)),
      el('td', { class: 'num' }, `${g.n_visible} / ${g.n_phases}`),
      el('td', { class: 'num' }, g.count_min === g.count_max ? t('{n} 回', { n: g.count_max }) : t('{a}〜{b} 回', { a: g.count_min, b: g.count_max })),
      el('td', { class: 'num' }, isT || g.mag_min == null ? '—' : span(f3(g.mag_min), f3(g.mag_max))),
      el('td', { class: 'num' }, isT ? '—' : central ? `${central} / ${g.n_phases}` + paren(`${Math.round(100 * central / g.n_phases)}%`) : t('なし')),
      el('td', { class: 'num' }, g.time_first ? sweepRange(g.time_first, g.time_last) : '—'),
    );
    if (!g.n_visible) tr.classList.add('dim');
    tb.append(tr);
  });
  if (!r.groups.length) tb.append(el('tr', {}, el('td', { colspan: head.length, style: 'text-align:center;color:var(--muted);padding:20px' }, t('この期間には、どの位相でも見られる現象がありません。'))));
  tbl.append(tb);
  renderSweepGroup();
}
function renderSweepGroup() {
  const r = state.sweep;
  const g = r.groups[state.sweepGroup];
  $('#sweepDetail').hidden = !g;
  if (!g) return;
  const isT = g.body !== 'moon';
  $('#sweepTitle').textContent = t('{date} の{what}：平均近点角ごとの結果', { date: fmtDate(g.date), what: phenLabel(g.body) });
  $('#sweepSub').textContent = isT
    ? t('各位相で最も長く見える通過を表示しています。行をクリックすると詳細が開きます。')
    : t('各位相で最も深い食を表示しています（1 周回ごとに複数回起きることがあります）。行をクリックすると詳細が開きます。');
  $('#sweepChart').replaceChildren(sweepChart(g));
  const tbl = $('#sweepPhaseTable');
  tbl.innerHTML = '';
  const multiDay = g.time_first && dayDiff(g.time_first, g.time_last);
  const head = [t('平均近点角'), t('見える回数'), t('種類'), t('最大の時刻'), isT ? t('中心間距離') : t('食分'), t('太陽が隠れる割合'),
    isT ? t('継続時間') : t('中心食の継続'), t('見えるか')];
  tbl.append(el('thead', {}, el('tr', {}, head.map((h) => el('th', {}, h)))));
  const tb = el('tbody');
  for (const row of g.rows) {
    const e = row.best;
    const tr = el('tr', {});
    if (!e) {
      tr.append(el('td', { class: 'num' }, `${row.m_deg}°`), el('td', { class: 'num' }, t('{n} 回', { n: 0 })),
        el('td', { colspan: head.length - 2, class: 'muted' }, t('この位相では見られません')));
      tr.classList.add('dim');
    } else {
      tr.addEventListener('click', () => openDetail(e.id, { root: true }));
      tr.append(
        el('td', { class: 'num' }, `${row.m_deg}°`),
        el('td', { class: 'num' }, t('{n} 回', { n: row.count })),
        el('td', {}, badge(e)),
        el('td', { class: 'num' }, `${multiDay ? fmtDateShort(e.max).slice(5) + ' ' : ''}${fmtTime(e.max)} ${tzLabel(e.max)}`),
        el('td', { class: 'num' }, isT ? `${f1(e.min_sep_arcsec)}″` : f3(e.magnitude)),
        el('td', {}, el('span', {}, el('span', { class: 'bar' }, el('i', { style: `width:${Math.min(100, e.obscuration * 100)}%` })), pct(e.obscuration, isT ? 3 : 1))),
        el('td', { class: 'num' }, isT ? fmtDur(e.duration_s) : e.type === 'partial' ? '—' : fmtDur(e.central_duration_s, true)),
        visCell(e, 'space'),
      );
      if ((e.vis_fraction || 0) <= 0) tr.classList.add('dim');
    }
    tb.append(tr);
  }
  tbl.append(tb);
}
function sweepRange(a, b) {
  if (!dayDiff(a, b)) return `${span(fmtHM(a), fmtHM(b))} ${tzLabel(a)}`;
  return `${span(`${fmtDateShort(a).slice(5)} ${fmtHM(a)}`, `${fmtDateShort(b).slice(5)} ${fmtHM(b)}`)} ${tzLabel(a)}`;
}
/* Bar chart of the outcome against the mean anomaly. */
function sweepChart(g) {
  const NS = 'http://www.w3.org/2000/svg';
  const isT = g.body !== 'moon';
  const W = 720, H = 190, L = 44, R = 10, T = 14, B = 34;
  const vals = g.rows.map((r) => (r.best && (r.best.vis_fraction || 0) > 0 ? (isT ? r.best.vis_fraction : r.best.magnitude) : 0));
  const top = isT ? 1 : Math.max(1.05, ...vals) * 1.02;
  const y = (v) => T + (1 - v / top) * (H - T - B);
  const bw = (W - L - R) / g.rows.length;
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', isT ? t('平均近点角ごとの見える割合') : t('平均近点角ごとの最大食分'));
  const add = (tag, attrs, text) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (text != null) n.textContent = text;
    svg.append(n);
    return n;
  };
  const ticks = isT ? [0, 0.5, 1] : [0, 0.25, 0.5, 0.75, 1];
  for (const t of ticks) {
    add('line', { x1: L, x2: W - R, y1: y(t), y2: y(t), class: t === 1 && !isT ? 'ref' : 'grid' });
    add('text', { x: L - 6, y: y(t) + 4, 'text-anchor': 'end', class: 'tick' }, isT ? `${t * 100}%` : t.toFixed(2));
  }
  g.rows.forEach((r, k) => {
    const v = vals[k];
    const x = L + k * bw;
    if (v > 0) {
      const c = CAT[catOf(r.best)];
      const rect = add('rect', { x: x + bw * 0.15, width: Math.max(1, bw * 0.7), y: y(v), height: y(0) - y(v), fill: c.color, rx: 1.5 });
      const title = document.createElementNS(NS, 'title');
      title.textContent = t('平均近点角 {m}°：{what}', { m: r.m_deg, what: c.label + sep() + (isT ? t('見える割合 {v}', { v: pct(v) }) : t('食分 {v}', { v: f3(v) })) });
      rect.append(title);
    }
    if (k % Math.max(1, Math.round(g.rows.length / 12)) === 0) add('text', { x: x + bw / 2, y: H - B + 16, 'text-anchor': 'middle', class: 'tick' }, `${r.m_deg}°`);
  });
  add('text', { x: L + (W - L - R) / 2, y: H - 4, 'text-anchor': 'middle', class: 'axis' }, t('元期での平均近点角（衛星が軌道上のどこにいるか）'));
  if (!isT) add('text', { x: W - R, y: y(1) - 5, 'text-anchor': 'end', class: 'tick' }, t('食分 {v}', { v: '1.0' }));
  return svg;
}
function downloadSweepCsv() {
  const r = state.sweep;
  const rows = [['date_utc', 'phenomenon', 'mean_anomaly_deg', 'visible_count', 'type', 'max_utc', 'magnitude', 'obscuration', 'min_sep_arcsec', 'central_duration_s', 'duration_s', 'visible_fraction']];
  for (const g of r.groups) {
    for (const row of g.rows) {
      const e = row.best || {};
      rows.push([g.date.slice(0, 10), phenLabel(g.body), row.m_deg, row.count, e.type ? CAT[catOf(e)].label : '', e.max ?? '', e.magnitude ?? '',
        e.obscuration ?? '', e.min_sep_arcsec ?? '', e.central_duration_s ?? '', e.duration_s ?? '', e.vis_fraction ?? '']);
    }
  }
  saveFile('phase_sweep.csv', toCsv(rows), 'text/csv');
}

/* The date in the shown time zone, with the UT date under it when that is another day. */
function dateCell(iso) {
  const ut = utDateNote(iso);
  return el('td', {}, fmtDate(iso), ut && el('span', { class: 'sub' }, ut));
}
function globalRow(e, isT) {
  if (isT) {
    return [dateCell(e.max), el('td', {}, badge(e)), el('td', { class: 'num' }, `${fmtTime(e.max)} ${tzLabel(e.max)}`),
      el('td', { class: 'num' }, t('中心間 {v}″', { v: f1(e.min_sep_arcsec) })), el('td', {}, '—'), el('td', { class: 'num' }, fmtDur(e.duration_s)),
      el('td', {}, '—'), el('td', { class: 'muted' }, t('地球中心から見た値')), el('td', {}, '—')];
  }
  return [
    dateCell(e.max), el('td', {}, badge(e)), el('td', { class: 'num' }, `${fmtTime(e.max)} ${tzLabel(e.max)}`),
    el('td', { class: 'num' }, f4(e.magnitude)), el('td', { class: 'num' }, (e.gamma >= 0 ? '+' : '') + f4(e.gamma)),
    el('td', { class: 'num' }, e.type === 'partial' ? '—' : fmtDur(e.central_duration_s, true)),
    el('td', { class: 'num' }, e.path_width_km ? km(e.path_width_km.toFixed(0)) : '—'),
    el('td', {}, fmtLatLon(e.ge_lat, e.ge_lon)), el('td', { class: 'num' }, e.saros ?? '—'),
  ];
}
function visCell(e, kind) {
  if (kind === 'geocenter') return el('td', { class: 'vis yes' }, t('（地心）'));
  const f = e.vis_fraction || 0;
  let txt, cls;
  if (f >= 0.999) { txt = t('◎ 全経過'); cls = 'yes'; }
  else if (f > 0) {
    cls = 'part';
    const part = t('○ 一部 {p}%', { p: Math.round(f * 100) });
    if (kind === 'ground') {
      const startVis = e.visible_intervals[0][0] === e.c1;
      txt = part + (startVis ? t('（日の入り帯食）') : t('（日の出帯食）'));
      if (e.visible_intervals.length > 1) txt = part;
    } else txt = part + t('（地球に隠される）');
  } else { txt = kind === 'ground' ? t('× 地平線の下') : t('× 地球に隠される'); cls = 'no'; }
  return el('td', { class: 'vis ' + cls }, txt);
}
function localRow(e, isT, kind) {
  const scale = isT ? t('中心間 {v}″', { v: f1(e.min_sep_arcsec) }) : t('食分 {v}', { v: f3(e.magnitude) });
  const obs = isT ? `${pct(e.obscuration, 3)}` : el('span', {}, el('span', { class: 'bar' }, el('i', { style: `width:${Math.min(100, e.obscuration * 100)}%` })), pct(e.obscuration));
  let dur = fmtDur(e.duration_s);
  if (!isT && e.central_duration_s > 0) {
    const v = { c: fmtDur(e.central_duration_s, true), d: fmtDur(e.duration_s) };
    dur = e.type === 'total' ? t('皆既 {c}／全体 {d}', v) : t('金環 {c}／全体 {d}', v);
  }
  let pos = '—';
  if (kind === 'ground' && e.sun_alt_max != null) pos = t('高度 {v}°', { v: e.sun_alt_max.toFixed(0) }) + sep() + azName(e.sun_az_max);
  if (kind === 'space' && e.sat_lat_max != null) pos = fmtLatLon(e.sat_lat_max, e.sat_lon_max) + sep() + km(e.sat_alt_km_max.toFixed(0));
  return [
    dateCell(e.max), el('td', {}, badge(e)), el('td', { class: 'num' }, `${fmtTime(e.max)} ${tzLabel(e.max)}`),
    el('td', { class: 'num' }, scale), el('td', { class: 'num' }, obs), el('td', { class: 'num' }, dur),
    el('td', { class: 'num' }, pos), visCell(e, kind), el('td', { class: 'num' }, e.saros ?? '—'),
  ];
}

function downloadListCsv() {
  if (state.sweep) { downloadSweepCsv(); return; }
  const r = state.result;
  if (!r) return;
  const rows = [['date_utc', 'type', 'max_utc', 'c1_utc', 'c4_utc', 'magnitude', 'obscuration', 'ratio', 'min_sep_arcsec', 'gamma',
    'central_duration_s', 'duration_s', 'path_width_km', 'ge_lat', 'ge_lon', 'sun_alt_max', 'visible_fraction', 'saros']];
  for (const e of r.events) {
    rows.push([e.max.slice(0, 10), CAT[catOf(e)].label, e.max, e.c1 || e.p1 || '', e.c4 || e.p4 || '', e.magnitude, e.obscuration ?? '', e.ratio ?? '',
      e.min_sep_arcsec ?? '', e.gamma ?? '', e.central_duration_s ?? '', e.duration_s ?? '', e.path_width_km ?? '', e.ge_lat ?? '', e.ge_lon ?? '',
      e.sun_alt_max ?? e.sun_alt ?? '', e.vis_fraction ?? '', e.saros ?? '']);
  }
  saveFile('eclipse_list.csv', toCsv(rows), 'text/csv');
}
function toCsv(rows) {
  return '﻿' + rows.map((r) => r.map((v) => {
    const s = v == null ? '' : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }).join(',')).join('\r\n');
}
function saveFile(name, text, type) {
  const a = el('a', { href: URL.createObjectURL(new Blob([text], { type })), download: name });
  document.body.append(a); a.click(); a.remove();
}

/* ------------------------------------------------------------------ */
/* saving and opening results                                          */
/* ------------------------------------------------------------------ */
const SAVE_TOOL = 'solar_eclipse_calc 画面';
/* The request of a result in the shape of the web API and of `cli.py --request`.
   A TLE fetched from CelesTrak is kept as fetched, so that the result can be computed again. */
function savedRequest(req, r) {
  const { sweepStep, ...out } = req;
  if (sweepStep) out.step_deg = sweepStep;
  const o = r.observer;
  if (req.observer.type === 'celestrak' && o && o.line1) {
    out.observer = { type: 'tle', line1: o.line1, line2: o.line2, name: req.observer.name || o.name, norad: req.observer.norad };
  }
  return out;
}
/* An event without the server's ids (which mean nothing once the server is restarted). */
function noId({ id, serverId, ...e }) { return e; }
function stripIds(r) {
  const out = { ...r };
  if (r.events) out.events = r.events.map(noId);
  if (r.groups) {
    out.groups = r.groups.map((g) => ({
      ...g, rows: g.rows.map((row) => ({ ...row, best: row.best && noId(row.best), events: (row.events || []).map(noId) })),
    }));
  }
  return out;
}
function stamp() {
  const p = parts(new Date(), { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
  return `${p.year}${p.month}${p.day}_${p.hour}${p.minute}`;
}
/* The whole result (list, or phase sweep) and the request that computed it.  The file has the
   shape of `cli.py --format json`, so either can be opened again with "保存した結果を開く". */
function saveResult() {
  const r = state.sweep || state.result;
  if (!r) return;
  const { request, ...rest } = r;
  const out = { tool: SAVE_TOOL, version: state.info.version, saved_at: new Date().toISOString(), request, ...stripIds(rest) };
  saveFile(`eclipse_result_${stamp()}.json`, JSON.stringify(out, null, 1), 'application/json');
}
async function openSavedFile(file) {
  const st = $('#status');
  let data;
  try {
    data = JSON.parse((await file.text()).replace(/^\uFEFF/, ''));
  } catch (_) {
    setStatus(st, t('「{name}」は JSON ファイルとして読めません', { name: file.name }), 'err');
    return;
  }
  try {
    if (data && data.event && data.event.max) openSavedEvent(data, file.name);
    else if (data && (Array.isArray(data.events) || Array.isArray(data.groups))) openSavedResult(data, file.name);
    else if (data && data.dry_run) throw new Error(t('このファイルは解釈の確認（--dry-run）の出力で、計算結果を含みません'));
    else throw new Error(t('「{name}」は日食計算機で保存した結果ではありません（「結果を保存」で保存した JSON、詳細の「JSON をダウンロード」、cli.py の --format json の出力を開けます）', { name: file.name }));
  } catch (err) {
    setStatus(st, err.message, 'err');
  }
}
/* A saved request with the settings its results were computed with: cli.py writes in "settings"
   only the options given, and the defaults (the radii) change between versions, so the values
   used (`params` of the result or the detail) fill the others; then the form and /api/restore
   give the saved numbers. */
const PARAM_KEYS = ['sun_radius_km', 'moon_radius_ext_km', 'moon_radius_int_km', 'mercury_radius_km', 'venus_radius_km',
  'min_sun_alt_deg', 'refraction', 'earth_atm_km'];
function withParams(req, params) {
  if (!req || typeof req !== 'object' || !params || typeof params !== 'object') return req;
  if (req.settings != null && typeof req.settings !== 'object') return req;
  const s = { ...(req.settings || {}) };
  for (const k of PARAM_KEYS) if ((s[k] == null || s[k] === '') && params[k] != null) s[k] = params[k];
  return { ...req, settings: s };
}
function openSavedResult(data, name) {
  state.listSeq++;
  state.mapData = {};     // the ids saved1, saved2, ... of another file named other events
  const events = new Map();
  const tag = (e) => { delete e.serverId; e.id = `saved${events.size + 1}`; events.set(e.id, e); };
  if (data.groups) {
    for (const g of data.groups) {
      for (const row of g.rows) {
        (row.events || []).forEach(tag);
        if (!row.best) continue;
        const same = (row.events || []).find((x) => x.jd_max === row.best.jd_max);
        if (same) row.best = same; else tag(row.best);
      }
    }
  } else data.events.forEach(tag);
  const { tool, version, saved_at: savedAt, ok, request: req, ...r } = data;
  const request = withParams(req, data.params);
  r.request = request;
  if (r.elapsed_s == null) r.elapsed_s = 0;
  state.saved = { request, events, name, savedAt, tool };
  if (request) {
    try { applyRequestToForm(request); } catch (err) { console.warn(err); }
  }
  closeDetail();
  if (data.groups) {
    state.sweep = r; state.sweepGroup = 0; state.result = null;
    renderSweep();
  } else {
    state.result = r; state.sweep = null; state.filters = new Set();
    renderResults();
  }
  showView('results');
  setStatus($('#status'), t('「{name}」を開きました', { name }));
}
/* One event saved from the detail view ("JSON をダウンロード"): shown as saved; the map and
   other places are computed from the request saved with it. */
function openSavedEvent(data, name) {
  state.listSeq++; state.detailSeq++;
  state.mapData = {};
  const d = data.event;
  delete d.serverId;
  d.id = 'saved1';
  const request = withParams(d.request || data.request, d.params || (data.local && data.local.params));     // a global event has the radii in its local view
  state.saved = { request, events: new Map([[d.id, d]]), name, savedAt: data.saved_at, tool: data.tool };
  if (state.saved.request) {
    try { applyRequestToForm(state.saved.request); } catch (err) { console.warn(err); }
  }
  state.result = null; state.sweep = null;
  state.stack = []; state.rootId = d.id; state.selectedId = null;
  state.detail = d;
  state.local = d.kind === 'global' ? data.local || null : d;
  showView('results');
  $('#backBtn').hidden = true;
  renderDetailAll(true);
  if (d.kind === 'global' && !state.local) loadGeLocal(d);
  setStatus($('#status'), t('「{name}」を開きました（保存した現象）', { name }));
}
function savedNote() {
  const s = state.saved;
  if (!s) return '';
  const when = s.savedAt ? sep() + t('{when} に保存', { when: `${fmtDT(s.savedAt)} ${tzLabel(s.savedAt)}` }) : '';
  const how = s.request ? t('詳細は保存した計算条件で再計算します。') : t('このファイルには計算条件がないため、詳細は表示できません。');
  return `<div class="saved-note">${esc(sentences([t('保存した結果を表示しています（{name}{when}）。', { name: s.name, when }), how]))}</div>`;
}
/* What /api/restore needs to find an event again. */
function restoreKey(e) {
  return Object.fromEntries(['body', 'kind', 'jd_max', 'jd_c1', 'jd_c4', 'm_deg'].filter((k) => e[k] != null).map((k) => [k, e[k]]));
}
/* The event of the list or phase sweep on the page with the server's id `id`. */
function listEvent(id) {
  if (state.result) return state.result.events.find((e) => e.id === id) || null;
  for (const g of state.sweep ? state.sweep.groups : []) {
    for (const row of g.rows) {
      const e = (row.events || []).find((x) => x.id === id) || (row.best && row.best.id === id ? row.best : null);
      if (e) return e;
    }
  }
  return null;
}
/* The server's id of an event: an event of a saved result is computed again (once) from the
   saved request; the server forgets events when restarted (and the oldest ones after many
   searches), then they are computed again from the request of their list. */
async function liveDetail(id) {
  const s = state.saved && state.saved.events.get(id);
  if (!s) {
    const e = listEvent(id), req = (state.result || state.sweep || {}).request;
    try { return await api('/api/event/' + ((e && e.serverId) || id)); } catch (err) {
      if (!e || !req || err.status !== 404) throw err;
    }
    const d = await api('/api/restore', { request: req, event: restoreKey(e) });
    e.serverId = d.id;
    return d;
  }
  if (s.serverId) {
    try { return await api('/api/event/' + s.serverId); } catch (_) { s.serverId = null; }
  }
  if (!state.saved.request) throw new Error(t('このファイルには計算条件（request）がないため、詳細を計算できません'));
  const d = await api('/api/restore', { request: state.saved.request, event: restoreKey(s) });
  s.serverId = d.id;
  return d;
}
async function liveId(id) {
  const s = state.saved && state.saved.events.get(id);
  if (!s) return id;
  if (!s.serverId) await liveDetail(id);
  return s.serverId;
}
/* The server's id of the event `d` of the detail (an event of a list has another id once
   computed again by liveDetail). */
async function detailServerId(d) {
  const s = state.saved && state.saved.events.get(d.id);
  const e = s ? null : listEvent(d.id);
  return s ? liveId(d.id) : (e && e.serverId) || d.id;
}
/* Set the form to a saved request, so that it can be changed and computed again. */
function applyRequestToForm(req) {
  const ph = req.phenomena || ['moon', 'mercury', 'venus'];
  $$('input[name=ph]').forEach((i) => { i.checked = ph.includes(i.value); });
  const o = req.observer || {};
  if (o.type === 'ground') {
    setObsTab('ground');
    $('#cityPreset').value = '';
    $('#lat').value = o.lat; $('#lon').value = o.lon; $('#elev').value = o.elevation_m ?? 0; $('#placeName').value = o.name || '';
    updatePickMarker();
  } else if (o.type === 'global') {
    setObsTab('global');
  } else if (o.type) {
    setObsTab('space');
    $('#satPreset').value = '';
    applySatSpec(o);
    const step = req.step_deg != null ? String(+req.step_deg) : null;
    $('#kSweep').checked = o.type === 'kepler' && step != null;
    if (step != null) {     // a step of cli.py --sweep that the list does not have
      if (!$$('#kSweepStep option').some((x) => x.value === step)) $('#kSweepStep').append(el('option', { value: step }, `${step}°`));
      $('#kSweepStep').value = step;
    }
    updateKeplerForm();
  }
  if (req.start) $('#start').value = formUtc(req.start).slice(0, 10);
  if (req.end) {      // the form has dates only: an end later than 0h is rounded up to the next day
    const e = formUtc(req.end);
    $('#end').value = e.slice(11) > '00:00:00' ? isoDate(new Date(Date.parse(e.slice(0, 10) + 'T00:00:00Z') + 864e5)) : e.slice(0, 10);
  }
  const s = { ...state.info.defaults, ...(req.settings || {}) };
  const eph = s.ephemeris && !s.ephemeris.endsWith('.bsp') ? s.ephemeris + '.bsp' : s.ephemeris;
  if (eph && $$('#ephem option').some((x) => x.value === eph)) { $('#ephem').value = eph; updateCoverage(); }
  const manual = s.delta_t != null && s.delta_t !== '' && s.delta_t !== 'auto';
  $('#dtMode').value = manual ? 'manual' : 'auto';
  $('#dtValue').value = manual ? s.delta_t : '';
  $('#dtValue').disabled = !manual;
  const rp = state.info.radius_presets;
  const near = (a, b) => Math.abs(a - b) < 1e-6;
  const sun = sunModel(s.sun_radius_km);
  $('#sunR').value = sun || 'custom';
  if (!sun) { $('#sunRUnit').value = 'km'; $('#sunRCustom').value = s.sun_radius_km; }
  else state.sunRPrev = sun;
  $('#sunRCustomWrap').hidden = !!sun;
  const moon = Object.keys(rp.moon).find((k) => near(rp.moon[k][0], s.moon_radius_ext_km) && near(rp.moon[k][1], s.moon_radius_int_km));
  $('#moonR').value = moon || 'custom';
  if (!moon) { $('#moonRExt').value = s.moon_radius_ext_km; $('#moonRInt').value = s.moon_radius_int_km; }
  $('#moonRCustomWrap').hidden = !!moon;
  // the planets' radii have no custom value: the nearest preset (both radii, else Venus, else Mercury)
  $('#planetR').value = planetModel(s) || Object.keys(rp.venus).find((k) => near(rp.venus[k], s.venus_radius_km))
    || Object.keys(rp.mercury).find((k) => near(rp.mercury[k], s.mercury_radius_km)) || 'observed';
  $('#minAlt').value = s.min_sun_alt_deg;
  $('#atm').value = s.earth_atm_km;
  $('#refraction').checked = !!s.refraction;
  $('#includeInvisible').checked = !!s.include_invisible;
}

/* ------------------------------------------------------------------ */
/* the welcome page and the results                                    */
/* ------------------------------------------------------------------ */
/* Show the welcome page or the results (the list and the open event), keeping both. */
function showView(view) {
  state.view = view;
  const res = view === 'results';
  if (!res) pause();
  $('#welcome').hidden = res;
  $('#resultArea').hidden = !res || !(state.result || state.sweep);
  $('#detail').hidden = !res || !state.detail;
  updateViewNav();
}
function updateViewNav() {
  $('#viewNav').hidden = !(state.result || state.sweep || state.detail);
  $$('#viewNav button').forEach((b) => b.classList.toggle('active', b.dataset.view === state.view));
  $('#toListBtn').hidden = !(state.result || state.sweep);
}
function goView(view) {
  const back = view === 'results' && state.view !== 'results';
  showView(view);
  if (back && state.detail) showTab(currentTab());     // the canvas and the map get their size again
  scrollToEl($('.results'));
}
/* Scroll to an element, below the bar at the top. */
function scrollToEl(e) {
  const y = e.getBoundingClientRect().top + window.scrollY - $('.topbar').offsetHeight - 12;
  window.scrollTo({ top: Math.max(0, y), behavior: 'smooth' });
}

/* ------------------------------------------------------------------ */
/* detail                                                              */
/* ------------------------------------------------------------------ */
async function openDetail(id, { root = false, push = false } = {}) {
  const st = $('#status');
  const saved = state.saved && state.saved.events.get(id);
  const busy = saved && !saved.serverId ? t('保存した計算条件で詳細を計算中…') : t('詳細を計算中…');
  setStatus(st, busy, 'busy');
  const seq = ++state.detailSeq;
  try {
    const d = await liveDetail(id);
    if (seq !== state.detailSeq) {      // closed, or another list or event was shown meanwhile
      if (st.textContent === busy) setStatus(st, '');
      return;
    }
    if (push && state.detail) state.stack.push(state.detail.id);
    if (root) { state.stack = []; state.rootId = d.id; }
    state.detail = d;
    state.selectedId = root ? id : state.selectedId;
    $$('#eventTable tbody tr').forEach((tr) => tr.classList.toggle('selected', tr.dataset.id === state.selectedId));
    state.local = d.kind === 'global' ? null : d;
    showView('results');
    $('#backBtn').hidden = state.stack.length === 0;
    renderDetailAll(true);
    setStatus(st, '');
    if (d.kind === 'global') loadGeLocal(d);
    $('#detail').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (err) {
    if (seq === state.detailSeq) setStatus(st, err.message, 'err');
  }
}
/* The view at the place of the greatest eclipse of the global event `d` (/api/local). */
async function geLocal(d) {
  return api('/api/local', { event_id: await detailServerId(d), lat: d.ge_lat, lon: d.ge_lon, elevation_m: 0, name: t('最大食の地点') });
}
async function loadGeLocal(d) {
  try {
    const r = await geLocal(d);
    if (state.detail !== d || !r.found) return;
    state.local = r;
    renderOverview();
    setupViewer();
    renderCharts();
    if (currentTab() === 'sheet') renderSheet();
  } catch (err) { console.warn(err); }
}
function closeDetail() {
  state.detailSeq++;
  $('#detail').hidden = true;
  state.detail = null; state.local = null; state.playing = false;
  state.selectedId = null;
  $$('#eventTable tbody tr').forEach((tr) => tr.classList.remove('selected'));
  if (!state.result && !state.sweep && state.view === 'results') showView('welcome');     // nothing left to show
  else updateViewNav();
}
function goBack() {
  const id = state.stack.pop();
  if (id) openDetail(id);
}
function currentTab() { return ($('#detailTabs button.active') || {}).dataset?.tab || 'overview'; }
function showTab(tab) {
  $$('#detailTabs button').forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
  $$('.tabpane').forEach((p) => { p.hidden = p.dataset.tabpane !== tab; });
  if (tab === 'map') loadMap();
  if (tab === 'view') { resizeCanvas(); drawDisk(); }
  if (tab === 'chart') renderCharts();
  if (tab === 'sheet') renderSheet();
  if (tab === 'data') renderData();
}
function renderDetailAll(reset) {
  const d = state.detail;
  if (!d) return;
  const obs = d.kind === 'global' ? null : d.observer;
  $('#detailTitle').textContent = t('{date} の{type}', { date: fmtDate(d.max) + utDateNote(d.max), type: typeLabel(d) });
  $('#detailSub').textContent = d.kind === 'global' ? t('地球全体での状況') : t('観測者: {obs}', { obs: observerText(obs) });
  renderOverview();
  setupViewer(reset);
  if (reset) {
    state.mapData[d.id] = state.mapData[d.id] || null;
    showTab(currentTab());
  } else {
    renderCharts();
    if (currentTab() === 'sheet') renderSheet();
    if (currentTab() === 'map') loadMap(true);
    if (currentTab() === 'data') renderData();
  }
}

function leadText(d) {
  const moon = d.body === 'moon';
  const out = [];
  if (d.kind === 'global') {
    if (d.body !== 'moon') return '';
    out.push(t('<b>{date}</b> の{type}です。', { date: fmtDate(d.max), type: esc(typeLabel(d)) }));
    out.push(t('食が最も大きくなる「最大食」は <b>{time}</b> に <b>{place}</b> で起こり、太陽高度は {alt}° です。',
      { time: `${fmtTime(d.max)} ${tzLabel(d.max)}`, place: fmtLatLon(d.ge_lat, d.ge_lon), alt: d.sun_alt.toFixed(0) }));
    if (d.type !== 'partial') {
      const v = { dur: fmtDur(d.central_duration_s, true), width: d.path_width_km ? d.path_width_km.toFixed(0) : '' };
      if (!d.path_width_km) out.push(t('中心食の継続時間は最大 <b>{dur}</b> です。', v));
      else if (d.type === 'annular') out.push(t('中心食の継続時間は最大 <b>{dur}</b>、金環帯の幅は約 <b>{width} km</b> です。', v));
      else out.push(t('中心食の継続時間は最大 <b>{dur}</b>、皆既帯の幅は約 <b>{width} km</b> です。', v));
    } else out.push(t('最大食分は {mag} の部分日食で、皆既・金環になる場所はありません。', { mag: f3(d.magnitude) }));
    if (d.p1 && d.p4) out.push(t('地球上のどこかで部分食が見られるのは {start} 〜 {end}（{tz}）です。', { start: fmtDT(d.p1), end: fmtDT(d.p4), tz: tzLabel(d.p1) }));
    return sentences(out);
  }
  const kind = d.observer_kind;
  const name = esc(d.observer.name);
  const who = kind === 'ground' ? t('{name}では', { name }) : kind === 'space' ? t('{name}から見ると', { name }) : t('地球中心から見ると');
  const c = Object.fromEntries(d.contacts.map((x) => [x.label, x]));
  const c2 = d.contacts.find((x) => x.label === 'C2');
  const c3 = [...d.contacts].reverse().find((x) => x.label === 'C3');
  const v = { who, date: fmtDate(d.max), c1: fmtTime(d.c1), max: fmtTime(d.max), c4: fmtTime(d.c4), tz: tzLabel(d.max) };
  if (moon) {
    out.push(t('{who}、<b>{date}</b> の <b>{c1}</b> に太陽が欠け始め、<b>{max}</b> に最大（食分 <b>{mag}</b>、太陽の面積の <b>{obs}</b> が隠れる）となり、<b>{c4}</b> に終わります（{tz}）。',
      { ...v, mag: f3(d.magnitude), obs: pct(d.obscuration) }));
    if (d.type === 'total' || d.type === 'annular') {
      const w = { start: fmtTime(c2.time), end: fmtTime(c3.time), dur: fmtDur(d.central_duration_s, true) };
      out.push(d.type === 'total' ? t('{start} から {end} までの <b>{dur}</b> 間は<b>皆既日食</b>（太陽が完全に隠れる）です。', w)
        : t('{start} から {end} までの <b>{dur}</b> 間は<b>金環日食</b>（太陽がリング状に見える）です。', w));
      if (d.n_internal > 1) out.push(t('（衛星の運動により {n} 回に分かれます）', { n: d.n_internal }));
    }
  } else {
    out.push(t('{who}、<b>{date}</b> の <b>{c1}</b> に{body}が太陽の縁にかかり始め、<b>{max}</b> に太陽の中心に最も近づき（中心間 {sep}″）、<b>{c4}</b> に太陽面から離れます（{tz}）。',
      { ...v, body: bodyJa(d.body), sep: f1(d.min_sep_arcsec) }));
    out.push(t('経過時間は {dur} です。', { dur: fmtDur(d.duration_s) }));
    if (d.type === 'transit_grazing') out.push(t('{body}が太陽の縁をかすめるだけで、全体が太陽面に入ることはありません。', { body: bodyJa(d.body) }));
  }
  if (kind === 'ground' || kind === 'space') {
    const f = d.vis_fraction;
    if (f >= 0.999) {
      out.push(kind === 'ground' ? t('全経過で太陽は地平線の上にあり、最大時の太陽高度は <b>{alt}°</b>（{az}の空）です。', { alt: c.MAX.sun_alt.toFixed(0), az: azName(c.MAX.sun_az) })
        : t('全経過で太陽は地球に隠されません。'));
    } else if (f > 0) {
      const iv = d.visible_intervals.map(([a, b]) => `${fmtTime(a)}–${fmtTime(b)}`).join(listSep());
      out.push(kind === 'ground' ? t('ただし見られるのは <b>{iv}</b> の間だけです（太陽が地平線の下にある時間を除く）。', { iv })
        : t('ただし見られるのは <b>{iv}</b> の間だけです（衛星から見て太陽が地球に隠される時間を除く）。', { iv }));
      if (d.visible_max && moon) out.push(t('見える範囲での最大食分は {mag} です。', { mag: f3(d.visible_max.magnitude) }));
    } else {
      out.push(kind === 'ground' ? t('<b>この現象は太陽が地平線の下にあるため見られません。</b>') : t('<b>この間、太陽は地球に隠されていて見えません。</b>'));
    }
    if (kind === 'space' && c.MAX.sat_lat != null) {
      out.push(t('最大時の衛星は {pos} の上空 {alt} km にいます。', { pos: fmtLatLon(c.MAX.sat_lat, c.MAX.sat_lon), alt: c.MAX.sat_alt_km.toFixed(0) }));
    }
  }
  if (kind === 'geocenter') out.push(t('これは地球中心から見た標準値で、地上の各地点では視差により数分ずれます。地図タブで地点をクリックすると、その場所での時刻を計算できます。'));
  return sentences(out);
}

function metric(k, v, s = '') { return el('div', { class: 'metric' }, el('div', { class: 'k' }, k), el('div', { class: 'v' }, v), el('div', { class: 's' }, s)); }

function renderOverview() {
  const d = state.detail;
  const box = $('#overview');
  box.innerHTML = '';
  if (!d) return;
  if (d.kind === 'global') {
    box.append(el('div', { class: 'lead', html: leadText(d) }));
    box.append(el('div', { class: 'metrics' },
      d.type === 'partial' ? metric(t('最大食分'), f4(d.magnitude), t('太陽の直径が隠れる割合'))
        : metric(t('食分（視直径比）'), f4(d.magnitude), t('月と太陽の見かけの大きさの比')),
      metric(t('γ（ガンマ）'), (d.gamma >= 0 ? '+' : '') + f4(d.gamma), t('影の軸と地球中心の最接近距離')),
      metric(t('中心食の継続時間'), d.type === 'partial' ? '—' : fmtDur(d.central_duration_s, true), t('最大食の地点で')),
      metric(t('中心食帯の幅'), d.path_width_km ? km(d.path_width_km.toFixed(1)) : '—', t('最大食の地点で')),
      metric(t('サロス番号'), d.saros ?? '—', t('約18年周期の系列')),
    ));
    box.append(el('div', { class: 'section-title' }, t('地球全体での経過')));
    const central = d.type !== 'partial';
    const rows = [[t('部分食の始まり（P1）'), d.p1], [central && t('中心食の始まり'), d.c_begin], [t('最大食'), d.max, true],
      [central && t('中心食の終わり'), d.c_end], [t('部分食の終わり（P4）'), d.p4]].filter((r) => r[0] && r[1]);
    box.append(el('div', { class: 'table-scroll' }, el('table', { class: 'contacts' },
      el('thead', {}, el('tr', {}, el('th', {}, t('段階')), el('th', {}, t('日時（{tz}）', { tz: tzLabel(d.max) })), el('th', {}, 'UTC'))),
      el('tbody', {}, rows.map(([k, tm, max]) => el('tr', { class: max ? 'max' : '' }, el('td', { class: 'lbl' }, k), el('td', {}, fmtDT(tm, 1)), el('td', { class: 'muted' }, tm.replace('T', ' ').replace('Z', '')))))
    )));
    const kv = el('dl', { class: 'kv' },
      el('dt', {}, t('最大食の地点')), el('dd', {}, fmtLatLon(d.ge_lat, d.ge_lon) + paren(t('太陽高度 {v}°', { v: d.sun_alt.toFixed(1) }))),
      el('dt', {}, 'ΔT'), el('dd', {}, t('{v} 秒', { v: d.delta_t_s.toFixed(2) })));
    box.append(el('div', { class: 'section-title' }, t('その他')), kv);
    box.append(el('div', { class: 'section-title' }, t('最大食の地点での見え方')));
    if (state.local) box.append(...[contactTable(state.local), radiusEffects(state.local)].filter(Boolean));
    else box.append(el('p', { class: 'muted' }, el('span', { class: 'spinner' }), t('計算中…')));
    box.append(el('p', { class: 'note' }, t('「地図」タブで地図上の好きな地点をクリックすると、その地点での見え方（時刻・食分）を計算できます。')));
    return;
  }
  box.append(el('div', { class: 'lead', html: leadText(d) }));
  if (d.warnings && d.warnings.length) box.append(el('div', { class: 'warnings' }, d.warnings.map((w) => el('div', {}, '⚠ ' + w))));
  const moon = d.body === 'moon';
  const cmax = d.contacts.find((c) => c.label === 'MAX');
  const ms = [];
  if (moon) {
    ms.push(metric(t('最大食分'), f4(d.magnitude), t('太陽の直径が隠れる割合')));
    ms.push(metric(t('食面積率'), pct(d.obscuration, 2), t('太陽の面積が隠れる割合')));
    if (d.type === 'total' || d.type === 'annular') {
      ms.push(metric(d.type === 'total' ? t('皆既の継続時間') : t('金環の継続時間'), fmtDur(d.central_duration_s, true),
        d.n_internal > 1 ? t('最長区間（{n}回）', { n: d.n_internal }) : t('第2〜第3接触')));
    }
    ms.push(metric(t('食の継続時間'), fmtDur(d.duration_s), t('第1〜第4接触')));
    ms.push(metric(t('視直径比（月/太陽）'), f4(d.ratio), d.ratio > 1 ? t('月の方が大きい') : t('月の方が小さい')));
  } else {
    ms.push(metric(t('太陽中心との最小距離'), f1(d.min_sep_arcsec) + '″', t('太陽の半径は {v}″', { v: f1(d.sun_diameter_arcsec / 2) })));
    ms.push(metric(t('{body}の視直径', { body: bodyJa(d.body) }), f1(d.body_diameter_arcsec) + '″', t('太陽の約 1/{n}', { n: Math.round(d.sun_diameter_arcsec / d.body_diameter_arcsec) })));
    ms.push(metric(t('経過時間'), fmtDur(d.duration_s), t('第1〜第4接触')));
    if (d.central_duration_s > 0) ms.push(metric(t('内接している時間'), fmtDur(d.central_duration_s), t('第2〜第3接触')));
  }
  if (d.observer_kind === 'ground') ms.push(metric(t('最大時の太陽高度'), `${cmax.sun_alt.toFixed(1)}°`, t('方位 {v}°', { v: cmax.sun_az.toFixed(0) }) + paren(azName(cmax.sun_az))));
  if (d.observer_kind === 'space') ms.push(metric(t('見える時間の割合'), pct(d.vis_fraction, 0), t('太陽が地球に隠されない時間')));
  box.append(el('div', { class: 'metrics' }, ms));
  box.append(el('div', { class: 'section-title' }, t('接触時刻と状況')));
  box.append(...[contactTable(d), limbNote(d), radiusEffects(d), d.observer_kind === 'space' && visibleIntervals(d)].filter(Boolean));
  const p = d.params;
  const cP = cmax && cmax.p_angle != null ? cmax.p_angle : null;
  const kv = el('dl', { class: 'kv' },
    el('dt', {}, t('暦')), el('dd', {}, d.ephemeris),
    el('dt', {}, t('ΔT（TT−UT）')), el('dd', {}, t('{v} 秒', { v: d.delta_t_s.toFixed(2) })),
    el('dt', {}, t('太陽の視直径（最大時）')), el('dd', {}, `${f1(d.sun_diameter_arcsec)}″`),
    el('dt', {}, t('{body}の視直径（最大時）', { body: bodyJa(d.body) })), el('dd', {}, `${f1(d.body_diameter_arcsec)}″`),
    cP != null && el('dt', {}, t('太陽の P 角（自転軸、最大時）')), cP != null && el('dd', {}, `${signed(cP, 2)}°`),
    moon && el('dt', {}, t('サロス番号')), moon && el('dd', {}, d.saros ?? '—'),
    el('dt', {}, t('計算モデル')), el('dd', {}, sunModelText(p) + sep() + bodyModelText(d.body, p)),
  );
  box.append(el('div', { class: 'section-title' }, t('計算条件')), kv);
  if (d.c1_cut || d.c4_cut) box.append(el('p', { class: 'note' }, t('※ 計算期間の端にかかっているため、一部の接触時刻は期間の端の値です。')));
}

/* Solar-north-up X / Y (″, toward solar west / north), the P angle (°) and the position angle from
   solar north of a contact (or of a row of /api/event/{id}/at); from ξ, η and the P angle of
   solarP() for results saved before the server gave them. */
function hpcOf(c) {
  const P = c.p_angle != null ? c.p_angle : solarP(msOf(c.time));
  const paSolar = c.pa_solar != null ? c.pa_solar : c.pa != null ? (((c.pa - P) % 360) + 360) % 360 : null;
  if (c.hpc_x != null) return { x: c.hpc_x, y: c.hpc_y, P, paSolar };
  const r = P * Math.PI / 180, cp = Math.cos(r), sp = Math.sin(r);
  return { x: -(c.xi * cp - c.eta * sp), y: c.xi * sp + c.eta * cp, P, paSolar };
}
/* Distance of the body's centre from the Sun's limb (″, positive outside the disk) and in units
   of the body's radius (+1 at the first contact, −1 at the second). */
function limbDist(c) {
  const d = c.limb_dist != null ? c.limb_dist : c.sep_arcsec - c.rho_s;
  return [d, c.rho_b > 0 ? d / c.rho_b : null];
}
function limbCell(c, body) {
  const [d, n] = limbDist(c);
  return el('td', {}, `${signed(d, 2)}″`, n != null && el('span', { class: 'sub' }, t('{body}の半径の {n} 倍', { body: bodyJa(body), n: signed(n, 2) })));
}
/* Whether the contact tables show the values to compare with images (UTC to the millisecond, the
   solar-north-up X, Y, ...): chosen with the box above the table; at first for transits and for
   spacecraft and the geocentre. */
function fullContacts(d) {
  return state.fullContacts ?? (d.body !== 'moon' || d.observer_kind !== 'ground');
}
function contactTable(d) {
  const kind = d.observer_kind, moon = d.body === 'moon', full = fullContacts(d);
  const head = [t('接触'), t('時刻（{tz}）', { tz: tzLabel(d.max) })];
  if (full) head.push(t('UTC（ミリ秒まで）'));
  if (kind === 'ground') head.push(t('太陽高度'), t('方位'));
  if (kind === 'space') head.push(t('衛星直下点'), t('高度'), t('地球の縁からの太陽の離角'));
  head.push(t('位置角（天の北から）'));
  if (full) head.push(t('位置角（太陽の北から）'));
  if (kind === 'ground') head.push(t('天頂角 V'));
  if (full) head.push(t('X, Y（太陽の北が上, ″）'));
  head.push(moon ? t('食分') : t('中心間距離'));
  if (full && !moon) head.push(t('太陽の縁からの距離（外側が +）'));
  head.push(t('観測'));
  const rows = d.contacts.map((c) => {
    const [n, sub] = contactName(c.label, d);
    const h = full ? hpcOf(c) : null;
    const cells = [el('td', { class: 'lbl' }, n, sub && el('span', { class: 'sub' }, sub)),
      el('td', {}, fmtTime(c.time, 1), dayDiff(c.time, d.max) ? el('span', { class: 'sub' }, fmtDateShort(c.time, 1)) : null)];
    if (full) cells.push(el('td', {}, fmtUtc(c.time)));
    if (kind === 'ground') cells.push(el('td', {}, `${c.sun_alt.toFixed(1)}°`), el('td', {}, `${c.sun_az.toFixed(1)}°` + paren(azName(c.sun_az))));
    if (kind === 'space') cells.push(el('td', {}, fmtLatLon(c.sat_lat, c.sat_lon)), el('td', {}, km(c.sat_alt_km.toFixed(0))), el('td', {}, `${c.vis.toFixed(1)}°`));
    cells.push(el('td', {}, `${c.pa.toFixed(1)}°`));
    if (full) cells.push(el('td', {}, h.paSolar == null ? '—' : `${h.paSolar.toFixed(1)}°`));
    if (kind === 'ground') cells.push(el('td', {}, `${c.v_angle.toFixed(1)}°`));
    if (full) cells.push(el('td', {}, `${h.x.toFixed(2)}, ${h.y.toFixed(2)}`));
    cells.push(el('td', {}, moon ? f4(c.magnitude) : `${(+c.sep_arcsec).toFixed(full ? 2 : 1)}″`));
    if (full && !moon) cells.push(limbCell(c, d.body));
    const visTxt = kind === 'geocenter' ? '—' : c.visible ? el('span', { class: 'pill ok' }, t('見える'))
      : el('span', { class: 'pill no' }, kind === 'ground' ? t('地平線下') : t('地球に隠れる'));
    cells.push(el('td', {}, visTxt));
    return el('tr', { class: c.label === 'MAX' ? 'max' : '' }, cells);
  });
  const toggle = el('label', { class: 'check table-opt' },
    el('input', { type: 'checkbox', checked: full, onchange: (ev) => { state.fullContacts = ev.target.checked; renderOverview(); } }),
    t('画像と比べるための値（UTC のミリ秒・太陽の北が上の X, Y・太陽の縁からの距離）を表示'));
  return el('div', {}, toggle, el('div', { class: 'table-scroll' },
    el('table', { class: 'contacts' }, el('thead', {}, el('tr', {}, head.map((h) => el('th', {}, h)))), el('tbody', {}, rows))));
}
/* The contacts at which the Sun is partly below the horizon or behind the Earth's limb (whether
   the Sun is seen is decided by its centre), or null. */
function limbNote(d) {
  const kind = d.observer_kind;
  if (kind !== 'ground' && kind !== 'space') return null;
  const near = d.contacts.filter((c) => c.label !== 'MAX' && c.vis != null && c.rho_s && Math.abs(c.vis) * 3600 < c.rho_s);
  if (!near.length) return null;
  const list = [...new Set(near.map((c) => contactName(c.label, d)[0]))].join(listSep());
  return el('p', { class: 'note warn-note' }, kind === 'ground'
    ? t('※ {list}のとき、太陽の一部が地平線（太陽高度の下限）の下にあります。見えるかどうかは太陽の中心で判定しています。', { list })
    : t('※ {list}のとき、太陽の一部が地球の縁（大気の遮蔽高度 {atm} km を含む）に隠れています。見えるかどうかは太陽の中心で判定しています。',
      { list, atm: d.params ? d.params.earth_atm_km : 0 }));
}
const MOON_MODEL_NAMES = { nasa: 'NASA方式', mean: '平均半径 1737.4 km' };
/* How the contact times change with the radii: per +1″ of the angular radius of the Sun and of
   the body (dt_drs, dt_drb of the server), and with the other radius models of the settings
   (to first order, from the same values). */
function radiusEffects(d) {
  const cs = d.contacts.filter((c) => c.label !== 'MAX');
  if (!state.info || !d.params || !cs.some((c) => c.dt_drs != null)) return null;
  const p = d.params, rp = state.info.radius_presets, moon = d.body === 'moon', body = bodyJa(d.body);
  const inner = (c) => c.label === 'C2' || c.label === 'C3';
  const rows = [[t('太陽の半径が 1″ 大きいと'), cs.map((c) => c.dt_drs)]];
  for (const [k, km] of Object.entries(rp.sun)) {
    if (nearKm(km, p.sun_radius_km)) continue;
    rows.push([t('太陽半径を {model}（{as}″）にすると', { model: t(SUN_MODEL_NAMES[k] || k), as: kmToArcsec(km).toFixed(2) }),
      cs.map((c) => (c.dt_drs == null ? null : c.dt_drs * c.rho_s * (km / p.sun_radius_km - 1)))]);
  }
  rows.push([t('{body}の半径が 1″ 大きいと', { body }), cs.map((c) => c.dt_drb)]);
  if (moon) {
    for (const [k, [ext, int]] of Object.entries(rp.moon)) {
      if (nearKm(ext, p.moon_radius_ext_km) && nearKm(int, p.moon_radius_int_km)) continue;
      rows.push([t('月半径を {model} にすると', { model: t(MOON_MODEL_NAMES[k] || k) }), cs.map((c) => (c.dt_drb == null ? null : inner(c)
        ? c.dt_drb * c.rho_b_int * (int / p.moon_radius_int_km - 1) : c.dt_drb * c.rho_b * (ext / p.moon_radius_ext_km - 1)))]);
    }
  } else {
    const cur = d.body === 'venus' ? p.venus_radius_km : p.mercury_radius_km;
    const seen = new Set([cur.toFixed(3)]);      // Mercury has the same radius in three models
    for (const [k, km] of Object.entries(rp[d.body] || {})) {
      if (seen.has(km.toFixed(3))) continue;
      seen.add(km.toFixed(3));
      rows.push([t('{body}半径を {model}（{km} km）にすると', { body, model: t(PLANET_MODEL_NAMES[k] || k), km: km.toFixed(1) }),
        cs.map((c) => (c.dt_drb == null ? null : c.dt_drb * c.rho_b * (km / cur - 1)))]);
    }
  }
  const fmt = (v) => (v == null || !isFinite(v) ? '—' : t('{s}秒', { s: signed(v, 1) }));
  const table = el('table', { class: 'contacts' },
    el('thead', {}, el('tr', {}, el('th', {}, ''), cs.map((c) => el('th', {}, contactName(c.label, d)[0])))),
    el('tbody', {}, rows.map(([k, vs]) => el('tr', {}, el('td', { class: 'lbl' }, k), vs.map((v) => el('td', {}, fmt(v)))))));
  return el('details', { class: 'radius-fx', open: !moon },
    el('summary', {}, t('半径による接触時刻の変化')),
    el('div', { class: 'table-scroll' }, table),
    el('p', { class: 'note' }, t('今の計算条件（{model}）からの差です。ほかの半径の値は、1″ あたりの変化から求めた近似値です。',
      { model: sunModelText(p) + sep() + bodyModelText(d.body, p) })));
}
/* The intervals in which the Sun is seen from a spacecraft (not hidden by the Earth), with the time
   its disk takes to cross the Earth's limb at each end (± half of it about the time of its centre). */
function visibleIntervals(d) {
  const iv = d.visible_intervals || [];
  if (!iv.length || !(d.vis_fraction < 0.999)) return null;
  const s = d.timeseries, t0 = s ? msOf(s.t0) : 0;
  const ends = [d.c1, d.c4].filter(Boolean).map(msOf);
  const cross = (iso) => {
    const ms = msOf(iso);
    if (!s || !s.vis || ends.some((e) => Math.abs(e - ms) < 2)) return null;    // an end of the event, not the limb
    const tt = (ms - t0) / 1000, i = s.dt_s.findIndex((x) => x >= tt);
    if (i < 1 || i > s.dt_s.length - 2) return null;
    const rate = (s.vis[i + 1] - s.vis[i - 1]) / (s.dt_s[i + 1] - s.dt_s[i - 1]);      // °/s
    return rate ? s.rho_s[i] / 3600 / Math.abs(rate) : null;
  };
  const cell = (iso) => {
    const w = cross(iso);
    return el('td', {}, fmtTime(iso, 1), w != null && el('span', { class: 'sub' }, t('太陽の円盤が縁を横切る ±{s}', { s: fmtDur(w, true) })));
  };
  const tz = tzLabel(d.max);
  const head = ['#', t('見え始め（{tz}）', { tz }), t('見え終わり（{tz}）', { tz }), t('長さ'), t('見え始め（UTC）'), t('見え終わり（UTC）')];
  const table = el('table', { class: 'contacts' },
    el('thead', {}, el('tr', {}, head.map((h) => el('th', {}, h)))),
    el('tbody', {}, iv.map(([a, b], i) => el('tr', {}, el('td', {}, String(i + 1)), cell(a), cell(b),
      el('td', {}, fmtDur((msOf(b) - msOf(a)) / 1000, true)), el('td', {}, fmtUtc(a)), el('td', {}, fmtUtc(b))))));
  return el('div', {},
    el('div', { class: 'section-title' }, t('太陽が見える時間帯（地球に隠されない時間）')),
    el('div', { class: 'table-scroll' }, table),
    el('p', { class: 'note' }, t('見えるかどうかは、太陽の中心が地球の縁（大気の遮蔽高度 {atm} km を含む）より上にあるかで判定しています。',
      { atm: d.params ? d.params.earth_atm_km : 0 })));
}

/* ------------------------------------------------------------------ */
/* disk viewer                                                         */
/* ------------------------------------------------------------------ */
const SPEEDS = [[1, '実時間'], [10, '10倍速'], [60, '60倍速（1秒=1分）'], [300, '300倍速（1秒=5分）'], [900, '900倍速（1秒=15分）'], [3600, '3600倍速（1秒=1時間）']];
function fillSpeedOptions() {
  const sp = $('#speed'), cur = sp.value;
  sp.replaceChildren(...SPEEDS.map(([v, l]) => el('option', { value: v }, t(l))));
  if (cur) sp.value = cur;
}
function initViewer() {
  fillSpeedOptions();
  $('#playBtn').addEventListener('click', () => { state.playing ? pause() : play(); });
  $('#timeSlider').addEventListener('input', () => {
    const ts = state.series; if (!ts) return;
    state.exact = null;
    state.t = ts.dur * (+$('#timeSlider').value / 1000);
    drawDisk(); updateCursor();
  });
  for (const q of ['#orient', '#zoom', '#showGrid', '#showAxes']) $(q).addEventListener('change', drawDisk);
  $('#atBtn').addEventListener('click', showTimeAt);
  $('#atTime').addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); showTimeAt(); } });
  $('#cmpBtn').addEventListener('click', runCompare);
  $('#cmpCsv').addEventListener('click', downloadCompareCsv);
  window.addEventListener('resize', () => { if (currentTab() === 'view') { resizeCanvas(); drawDisk(); } });
}
function unwrapDeg(a) {
  const out = a.slice();
  for (let i = 1; i < out.length; i++) {
    let d = out[i] - out[i - 1];
    while (d > 180) { out[i] -= 360; d -= 360; }
    while (d < -180) { out[i] += 360; d += 360; }
  }
  return out;
}
function setupViewer(reset = true) {
  const L = state.local;
  pause();
  state.exact = null;
  if (reset) clearCompare();
  if (!L || !L.timeseries) { state.series = null; drawDisk(); return; }
  const ts = L.timeseries;
  const t0 = msOf(ts.t0);
  const s = { ...ts, t0ms: t0, dur: ts.dt_s[ts.dt_s.length - 1] };
  if (ts.parallactic) s.parallactic = unwrapDeg(ts.parallactic);
  if (ts.sun_az) s.sun_az = unwrapDeg(ts.sun_az);      // no jump through 180° between 359° and 1°
  if (ts.earth_pa) s.earth_pa = unwrapDeg(ts.earth_pa);
  state.series = s;
  const o = $('#orient');
  const kind = L.observer_kind;
  const prev = o.value;
  o.innerHTML = '';
  const opt = (v, label) => o.append(el('option', { value: v }, label));
  // the first one is chosen: as seen in the sky from the ground, as in solar images from space
  const solar = () => opt('solar', t('太陽の北が上（ひので・SDO などの画像の向き）'));
  if (kind === 'ground') opt('zenith', t('天頂が上（見たままの向き）'));
  else solar();
  opt('north', t('天の北が上（天文図の向き）'));
  if (kind === 'ground') solar();
  if (kind === 'space') opt('earth', t('地球の方向が下'));
  if ([...o.options].some((x) => x.value === prev) && !reset) o.value = prev;
  fillZoomOptions(L, reset);
  // contact jump buttons: the exact values of the contact (not interpolated)
  const jb = $('#jumpBtns');
  jb.innerHTML = '';
  for (const c of L.contacts) {
    const [n] = contactName(c.label, L);
    const tt = (msOf(c.time) - t0) / 1000;
    jb.append(el('button', { type: 'button', onclick: () => setTime(tt, c) }, n));
  }
  // default speed: whole event in ~20 s
  const target = s.dur / 20;
  let best = SPEEDS[0][0];
  for (const [v] of SPEEDS) if (Math.abs(Math.log(v / target)) < Math.abs(Math.log(best / target))) best = v;
  $('#speed').value = best;
  const cmax = L.contacts.find((c) => c.label === 'MAX');
  state.t = (msOf(cmax.time) - t0) / 1000;
  state.exact = { t: state.t, row: cmax };
  $('#viewNote').textContent = kind === 'ground' ? t('空を見上げたときの見え方です（大きさは太陽が基準）。地平線は緑で表示します。')
    : kind === 'space' ? t('衛星から太陽方向を見た様子です。紺色の大きな円弧は地球の縁です。') : t('地球中心から見た様子です。');
  syncSlider();
  if (!reset && state.cmp) renderCompare();
  if (currentTab() === 'view') { resizeCanvas(); drawDisk(); }
}
/* The field of view: the whole Sun, or a square of some arcseconds around the body (a transit) or
   around the point of the Sun's limb of the nearest contact (an eclipse). */
function fillZoomOptions(L, reset) {
  const z = $('#zoom'), prev = z.value;
  const what = L.body === 'moon' ? t('接触点を中心に') : t('{body}を中心に', { body: bodyJa(L.body) });
  z.replaceChildren(el('option', { value: 'sun' }, t('太陽全体')),
    ...[300, 120, 60, 30].map((v) => el('option', { value: String(v) }, t('{v}″ 四方（{what}）', { v, what }))));
  z.value = !reset && [...z.options].some((x) => x.value === prev) ? prev : 'sun';
}
/* Show the time `tt` (s from the start of the animation); `row`: a contact or a row of
   /api/event/{id}/at whose exact values are shown (its time may be outside the animation). */
function setTime(tt, row = null) {
  const s = state.series; if (!s) return;
  state.t = Math.max(0, Math.min(s.dur, tt));
  state.exact = row ? { t: tt, row } : null;
  syncSlider(); drawDisk(); updateCursor();
}
function syncSlider() { const s = state.series; if (s) $('#timeSlider').value = Math.round(1000 * state.t / s.dur); }
let _raf = null, _last = 0;
function play() {
  const s = state.series; if (!s) return;
  state.exact = null;
  if (state.t >= s.dur - 1e-6) state.t = 0;
  state.playing = true; $('#playBtn').textContent = '❚❚';
  _last = performance.now();
  const step = (now) => {
    if (!state.playing) return;
    const dt = (now - _last) / 1000; _last = now;
    state.t += dt * (+$('#speed').value);
    if (state.t >= s.dur) { state.t = s.dur; pause(); }
    syncSlider(); drawDisk(); updateCursor();
    if (state.playing) _raf = requestAnimationFrame(step);
  };
  _raf = requestAnimationFrame(step);
}
function pause() { state.playing = false; $('#playBtn').textContent = '▶'; if (_raf) cancelAnimationFrame(_raf); }
function sample(arr, t) {
  const s = state.series;
  if (!arr) return null;
  const n = s.dt_s.length;
  const x = t / s.dur * (n - 1);
  const i = Math.max(0, Math.min(n - 2, Math.floor(x)));
  const f = x - i;
  return arr[i] * (1 - f) + arr[i + 1] * f;
}
/* The values at time t of the animation, interpolated in the time series. */
function frame(t) {
  const s = state.series;
  const tc = Math.max(0, Math.min(s.dur, t));
  const keys = ['xi', 'eta', 'rho_s', 'rho_b', 'rho_b_int', 'magnitude', 'obscuration', 'sep_arcsec', 'vis', 'sun_alt', 'sun_az', 'parallactic',
    'earth_pa', 'earth_sep_deg', 'earth_radius_deg', 'earth_atm_deg', 'sat_lat', 'sat_alt_km', 'p_angle', 'hpc_x', 'hpc_y', 'limb_dist', 'limb_depth'];
  const f = {};
  for (const k of keys) f[k] = sample(s[k], tc);
  if (s.sat_lon) { const i = Math.round(tc / s.dur * (s.dt_s.length - 1)); f.sat_lon = s.sat_lon[i]; }
  f.ms = s.t0ms + tc * 1000;
  return derive(f);
}
/* The values that results saved by older versions do not have: the P angle, X / Y, the limb distance. */
function derive(f) {
  if (f.p_angle == null) f.p_angle = solarP(f.ms);
  if (f.hpc_x == null) { const h = hpcOf(f); f.hpc_x = h.x; f.hpc_y = h.y; }
  if (f.limb_dist == null) f.limb_dist = f.sep_arcsec - f.rho_s;
  if (f.limb_depth == null) f.limb_depth = (f.rho_s - f.sep_arcsec) / f.rho_b;
  return f;
}
/* The frame of a contact or of a row of /api/event/{id}/at: the exact values of the server; those
   that a row does not have (e.g. where the horizon is) from the animation. */
function exactFrame(ex) {
  const r = ex.row, s = state.series;
  const f = frame(ex.t);
  for (const [k, v] of Object.entries(r)) if (typeof v === 'number' && isFinite(v)) f[k] = v;
  // derived from the exact values (below) when the row does not have them
  for (const k of ['p_angle', 'hpc_x', 'hpc_y', 'limb_dist', 'limb_depth']) if (r[k] == null) f[k] = null;
  if (r.xi == null && r.hpc_x != null && r.p_angle != null) {      // a row of /at has X / Y and the P angle
    const P = r.p_angle * Math.PI / 180, a = -r.hpc_x, b = r.hpc_y;
    f.xi = a * Math.cos(P) + b * Math.sin(P); f.eta = -a * Math.sin(P) + b * Math.cos(P);
  }
  if (r.vis == null && typeof r.visible === 'boolean') {
    f.vis = r.visible ? 1 : -1;
    f.visApprox = true;
  }
  f.outside = ex.t < -0.5 || ex.t > s.dur + 0.5;
  // what the row lacks far from the animation is not drawn
  f.noHorizon = f.outside && r.parallactic == null;
  f.noEarth = f.outside && (r.earth_pa == null || r.earth_radius_deg == null);
  f.ms = msOf(r.time); f.time = r.time; f.exact = true;
  return derive(f);
}
function resizeCanvas() {
  const c = $('#diskCanvas');
  const r = c.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(200, Math.round(r.width * dpr));
  if (c.width !== w) { c.width = w; c.height = w; }
}
/* Position angle (°) of the direction up on the screen. */
function refAngle(f, mode) {
  if (mode === 'zenith' && f.parallactic != null) return f.parallactic;
  if (mode === 'earth' && f.earth_pa != null) return f.earth_pa - 180;
  if (mode === 'solar' && f.p_angle != null) return f.p_angle;
  return 0;
}
/* The middle of a zoomed view (ξ, η, ″): the body's centre for a transit; for an eclipse, the point
   of the Sun's limb touched at the contact nearest in time. */
function zoomCentre(f, L) {
  if (L.body !== 'moon') return [f.xi, f.eta];
  let best = null;
  for (const c of L.contacts) {
    if (c.label === 'MAX') continue;
    const dt = Math.abs(msOf(c.time) - f.ms);
    if (!best || dt < best.dt) best = { dt, c };
  }
  if (!best) return [f.xi, f.eta];
  const c = best.c, d = Math.hypot(c.xi, c.eta) || 1;
  const r = (c.label === 'C2' || c.label === 'C3') && c.rho_b_int > c.rho_s ? -c.rho_s : c.rho_s;
  return [r * c.xi / d, r * c.eta / d];
}
function mix(a, b, t) {
  const pa = a.match(/\w\w/g).map((h) => parseInt(h, 16));
  const pb = b.match(/\w\w/g).map((h) => parseInt(h, 16));
  return '#' + pa.map((v, i) => Math.round(v + (pb[i] - v) * t).toString(16).padStart(2, '0')).join('');
}
function drawDisk() {
  const c = $('#diskCanvas');
  const g = c.getContext('2d');
  const W = c.width, H = c.height;
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.clearRect(0, 0, W, H);
  const s = state.series;
  const L = state.local;
  if (!s || !L) {
    g.fillStyle = '#0b0f19'; g.fillRect(0, 0, W, H);
    g.fillStyle = '#9aa4b8'; g.font = `${Math.round(W / 30)}px sans-serif`; g.textAlign = 'center';
    g.fillText(state.detail && state.detail.kind === 'global' ? t('最大食の地点での見え方を計算中…') : '', W / 2, H / 2);
    $('#readout').innerHTML = '';
    return;
  }
  const mode = $('#orient').value || 'north';
  const f = state.exact ? exactFrame(state.exact) : frame(state.t);
  const kind = L.observer_kind;
  const field = $('#zoom').value && $('#zoom').value !== 'sun' ? +$('#zoom').value : 0;     // width of the view (″), 0: the whole Sun
  const k = field ? Math.min(W, H) / field : (Math.min(W, H) * 0.30) / f.rho_s;           // px per ″
  const ref = refAngle(f, mode) * Math.PI / 180;
  // (ξ, η) from the Sun's centre -> offset on the screen (px, y down), the direction at position angle `refA` up
  const off = (xi, eta, refA = ref) => [-k * (xi * Math.cos(refA) - eta * Math.sin(refA)), -k * (eta * Math.cos(refA) + xi * Math.sin(refA))];
  // the Sun's centre on the screen: in the middle, or away from it so that the middle of a zoomed view is there
  const [zx, zy] = field ? off(...zoomCentre(f, L)) : [0, 0];
  const cx = W / 2 - zx, cy = H / 2 - zy;
  const toScreen = (xi, eta, refA = ref) => { const [x, y] = off(xi, eta, refA); return [cx + x, cy + y]; };
  const totalPhase = L.body === 'moon' && f.rho_b_int > f.rho_s && f.sep_arcsec < f.rho_b_int - f.rho_s;
  const far = Math.hypot(cx - W / 2, cy - H / 2) + 2 * W;      // reaches past the screen from the Sun's centre

  // sky
  let sky = '#000000';
  if (kind === 'ground') {
    const day = Math.max(0, Math.min(1, (f.sun_alt + 6) / 10));
    const light = day * Math.pow(Math.max(0, 1 - f.obscuration), 0.35);
    sky = mix('060912', '3f7fcc', light);
  }
  g.fillStyle = sky; g.fillRect(0, 0, W, H);

  // body trajectory (dotted)
  g.save();
  g.strokeStyle = kind === 'ground' ? 'rgba(255,255,255,.35)' : 'rgba(180,200,255,.35)';
  g.setLineDash([2, 5]); g.lineWidth = Math.max(1, W / 500);
  g.beginPath();
  const n = s.dt_s.length;
  const refAt = (i) => (mode === 'zenith' && s.parallactic ? s.parallactic[i] : mode === 'earth' && s.earth_pa ? s.earth_pa[i] - 180
    : mode === 'solar' ? (s.p_angle ? s.p_angle[i] : f.p_angle) : 0);
  for (let i = 0; i < n; i += 2) {
    const [x, y] = toScreen(s.xi[i], s.eta[i], refAt(i) * Math.PI / 180);
    i === 0 ? g.moveTo(x, y) : g.lineTo(x, y);
  }
  g.stroke(); g.restore();

  // corona during totality
  const rs = f.rho_s * k;
  if (totalPhase) {
    const grd = g.createRadialGradient(cx, cy, rs * 0.95, cx, cy, rs * 3.2);
    grd.addColorStop(0, 'rgba(255,255,255,.85)'); grd.addColorStop(0.25, 'rgba(220,230,255,.35)'); grd.addColorStop(1, 'rgba(200,210,255,0)');
    g.fillStyle = grd; g.beginPath(); g.arc(cx, cy, rs * 3.2, 0, 2 * Math.PI); g.fill();
  } else {
    const glow = g.createRadialGradient(cx, cy, rs, cx, cy, rs * 1.6);
    glow.addColorStop(0, `rgba(255,220,150,${kind === 'ground' ? 0.35 : 0.25})`); glow.addColorStop(1, 'rgba(255,220,150,0)');
    g.fillStyle = glow; g.beginPath(); g.arc(cx, cy, rs * 1.6, 0, 2 * Math.PI); g.fill();
  }
  // sun with limb darkening
  if (!totalPhase) {
    const sg = g.createRadialGradient(cx, cy, 0, cx, cy, rs);
    sg.addColorStop(0, '#fffbe8'); sg.addColorStop(0.6, '#ffe7a3'); sg.addColorStop(0.9, '#ffbe55'); sg.addColorStop(1, '#f39b1d');
    g.fillStyle = sg; g.beginPath(); g.arc(cx, cy, rs, 0, 2 * Math.PI); g.fill();
  }
  // the Sun's rotation axis across the disk, solar north marked (whole Sun)
  if ($('#showGrid').checked && !field && f.p_angle != null && !totalPhase) {
    const sig = f.p_angle * Math.PI / 180 - ref, ux = -Math.sin(sig), uy = -Math.cos(sig);
    g.save();
    g.strokeStyle = 'rgba(150,90,0,.45)'; g.lineWidth = Math.max(1, W / 600); g.setLineDash([6, 6]);
    g.beginPath(); g.moveTo(cx - ux * rs, cy - uy * rs); g.lineTo(cx + ux * rs, cy + uy * rs); g.stroke();
    g.setLineDash([]);
    const fs = Math.round(W / 40);
    g.font = `bold ${fs}px sans-serif`; g.fillStyle = 'rgba(122,75,0,.9)'; g.textAlign = 'center'; g.textBaseline = 'middle';
    g.fillText(t('太陽の北'), cx + ux * rs * 0.82, cy + uy * rs * 0.82);
    g.restore();
  }
  // occulting body
  const [bx, by] = toScreen(f.xi, f.eta);
  const rb = f.rho_b * k;
  g.fillStyle = L.body === 'moon' ? (totalPhase ? '#05070c' : '#161a22') : '#050505';
  g.beginPath(); g.arc(bx, by, Math.max(rb, 1.5), 0, 2 * Math.PI); g.fill();
  if (L.body === 'moon') { g.strokeStyle = 'rgba(255,255,255,.08)'; g.lineWidth = 1; g.stroke(); }

  // Earth limb (spacecraft)
  if (kind === 'space' && !f.noEarth && f.earth_sep_deg != null && (f.earth_sep_deg - f.earth_atm_deg) * 3600 < f.rho_s * 4.5) {
    const sig = (f.earth_pa * Math.PI / 180) - ref;
    const D = f.earth_sep_deg * 3600 * k;
    const ex = cx - D * Math.sin(sig), ey = cy - D * Math.cos(sig);
    if (f.earth_atm_deg > f.earth_radius_deg + 1e-6) {
      g.fillStyle = 'rgba(90,150,255,.35)';
      g.beginPath(); g.arc(ex, ey, f.earth_atm_deg * 3600 * k, 0, 2 * Math.PI); g.fill();
    }
    const eg = g.createRadialGradient(ex, ey, Math.max(0, f.earth_radius_deg * 3600 * k - rs * 0.6), ex, ey, f.earth_radius_deg * 3600 * k);
    eg.addColorStop(0, '#0a1528'); eg.addColorStop(1, '#1f3f74');
    g.fillStyle = eg;
    g.beginPath(); g.arc(ex, ey, f.earth_radius_deg * 3600 * k, 0, 2 * Math.PI); g.fill();
    g.strokeStyle = 'rgba(140,190,255,.9)'; g.lineWidth = Math.max(1.5, W / 300); g.stroke();
  }
  // horizon (ground)
  if (kind === 'ground' && !f.noHorizon && f.sun_alt != null && f.sun_alt * 3600 < f.rho_s * 4.5) {
    const sigz = (f.parallactic * Math.PI / 180) - ref;
    g.save();
    g.translate(cx, cy); g.rotate(-sigz);
    const hy = f.sun_alt * 3600 * k;
    const gg = g.createLinearGradient(0, hy, 0, hy + W);
    gg.addColorStop(0, '#2f5d3a'); gg.addColorStop(1, '#14261a');
    g.fillStyle = gg; g.fillRect(-far, hy, 2 * far, 2 * far);
    g.strokeStyle = '#9fd3a8'; g.lineWidth = Math.max(1, W / 400);
    g.beginPath(); g.moveTo(-far, hy); g.lineTo(far, hy); g.stroke();
    g.restore();
  }
  const axes = $('#showAxes').checked;
  if (axes) drawAxes(g, W, H, cx, cy, k, mode, ((refAngle(f, mode) - f.p_angle) % 360 + 540) % 360 - 180);
  // orientation marks: around the Sun, or in a corner of a zoomed view (the solar north longer there)
  if ($('#showGrid').checked) {
    const fs = Math.round(W / (field ? 42 : 32));
    g.font = `bold ${fs}px sans-serif`; g.textAlign = 'center'; g.textBaseline = 'middle';
    // closer to the Sun when the angle ticks take the edges of the view
    const [ox, oy, r1, r2] = field ? [W - fs * 6.6, fs * 5.8, fs * 0.3, fs * 2]
      : axes ? [cx, cy, rs * 1.1, rs * 1.27] : [cx, cy, rs * 1.22, rs * 1.45];
    if (field) {
      g.fillStyle = 'rgba(0,0,0,.5)';
      g.beginPath(); g.arc(ox, oy, fs * 5.1, 0, 2 * Math.PI); g.fill();
    }
    const arrow = (paDeg, label, color, len = 1) => {
      const sig = paDeg * Math.PI / 180 - ref;
      const x1 = ox - r1 * Math.sin(sig), y1 = oy - r1 * Math.cos(sig);
      const x2 = ox - r2 * len * Math.sin(sig), y2 = oy - r2 * len * Math.cos(sig);
      g.strokeStyle = color; g.fillStyle = color; g.lineWidth = Math.max(1.5, W / 350);
      g.beginPath(); g.moveTo(x1, y1); g.lineTo(x2, y2); g.stroke();
      g.fillText(label, ox - (r2 * len + fs) * Math.sin(sig), oy - (r2 * len + fs) * Math.cos(sig));
    };
    if (field && f.p_angle != null) arrow(f.p_angle, t('太陽の北'), 'rgba(255,209,102,.95)', 1.75);
    arrow(0, t('北'), 'rgba(255,255,255,.8)');
    arrow(90, t('東'), 'rgba(255,255,255,.6)');
    if (kind === 'ground' && f.parallactic != null && !f.noHorizon) arrow(f.parallactic, t('天頂'), 'rgba(160,255,190,.9)');
    if (kind === 'space' && f.earth_pa != null && !f.noEarth) arrow(f.earth_pa, t('地球'), 'rgba(140,190,255,.95)');
  }
  // scale text
  g.font = `${Math.round(W / 40)}px sans-serif`; g.textAlign = 'left'; g.textBaseline = 'alphabetic';
  g.fillStyle = 'rgba(255,255,255,.7)';
  const scale = t('太陽の視直径 {v}′', { v: (2 * f.rho_s / 60).toFixed(2) }) + (field ? sep() + t('表示範囲 {v}″ 四方', { v: field }) : '');
  g.fillText(scale, W * 0.03, H * (axes ? 0.92 : 0.965));
  updateReadout(f);
}
/* Angle ticks (″) along the edges, from the Sun's centre with X to the right and Y up: with the
   solar north up these are the helioprojective X / Y of solar images (Hinode, SDO), otherwise the
   same turned with the view by `turn` degrees (from solar north toward east). */
function drawAxes(g, W, H, cx, cy, k, mode, turn) {
  const step = niceStep(W / k, 8);
  const dec = step >= 1 ? 0 : Math.ceil(-Math.log10(step) - 1e-9);
  const fs = Math.round(W / 46);
  g.save();
  g.font = `${fs}px sans-serif`; g.lineWidth = 1;
  g.strokeStyle = 'rgba(255,255,255,.18)'; g.fillStyle = 'rgba(255,255,255,.85)';
  g.shadowColor = 'rgba(0,0,0,.8)'; g.shadowBlur = 3;
  const u0 = -cx / k, u1 = (W - cx) / k, v0 = (cy - H) / k, v1 = cy / k;      // ″ to the right, up
  g.textAlign = 'center'; g.textBaseline = 'bottom';
  for (let i = Math.ceil(u0 / step); i * step <= u1; i++) {
    const x = cx + i * step * k;
    g.beginPath(); g.moveTo(x, 0); g.lineTo(x, H - fs * 1.4); g.stroke();
    if (x > fs * 1.5 && x < W - fs * 1.5) g.fillText((i * step).toFixed(dec), x, H - 3);
  }
  g.textAlign = 'left'; g.textBaseline = 'middle';
  for (let i = Math.ceil(v0 / step); i * step <= v1; i++) {
    const y = cy - i * step * k;
    g.beginPath(); g.moveTo(fs * 3.2, y); g.lineTo(W, y); g.stroke();
    if (y > fs && y < H - fs * 2) g.fillText((i * step).toFixed(dec), 3, y);
  }
  // what the ticks are, on a dark band
  const lines = mode === 'solar' ? [t('X・Y（″）: 太陽の中心から'), t('太陽の北が上（ひので・SDO の画像の座標）')]
    : [t('X′・Y′（″）: 太陽の中心から画面の向きに'), t('画面の上は太陽の北から東回りに {a}°', { a: signed(turn, 1) })];
  g.shadowBlur = 0;
  g.fillStyle = 'rgba(0,0,0,.5)';
  g.fillRect(fs * 3.2, 2, Math.max(...lines.map((x) => g.measureText(x).width)) + fs * 0.8, fs * 2.7);
  g.fillStyle = 'rgba(255,255,255,.9)'; g.textBaseline = 'top';
  lines.forEach((x, i) => g.fillText(x, fs * 3.6, 4 + i * fs * 1.3));
  g.restore();
}
function phaseLabel(f, L) {
  if (L.body === 'moon') {
    if (f.magnitude <= 0) return t('食なし');
    if (f.rho_b_int > f.rho_s && f.sep_arcsec < f.rho_b_int - f.rho_s) return t('皆既中');
    if (f.rho_b_int < f.rho_s && f.sep_arcsec < f.rho_s - f.rho_b_int) return t('金環中');
    return t('部分食中');
  }
  if (f.sep_arcsec > f.rho_s + f.rho_b) return t('通過前後');
  if (f.sep_arcsec < f.rho_s - f.rho_b) return t('太陽面を通過中');
  return t('太陽の縁にかかっている');
}
function updateReadout(f) {
  const L = state.local;
  const kind = L.observer_kind, moon = L.body === 'moon', exact = !!f.exact;
  const visible = kind === 'geocenter' || f.vis > 0;
  const rows = [];
  if (moon) {
    rows.push([t('食分'), f.magnitude > 0 ? f.magnitude.toFixed(4) : '0'], [t('食面積率'), pct(Math.max(0, f.obscuration), 2)]);
  } else {
    const n = f.rho_b > 0 ? f.limb_dist / f.rho_b : null;
    rows.push([t('太陽中心からの距離'), `${f.sep_arcsec.toFixed(exact ? 2 : 1)}″`],
      [t('太陽の縁からの距離（外側が +）'), `${signed(f.limb_dist, 2)}″` + (n != null ? paren(t('{body}の半径の {n} 倍', { body: bodyJa(L.body), n: signed(n, 2) })) : '')]);
  }
  const d = exact ? 2 : 1;
  rows.push([t('X, Y（太陽の北が上, ″）'), `${f.hpc_x.toFixed(d)}, ${f.hpc_y.toFixed(d)}`]);
  if (f.p_angle != null) rows.push([t('太陽の P 角（自転軸）'), `${signed(f.p_angle, 2)}°`]);
  if (exact && f.pa != null) {
    const paSolar = f.pa_solar != null ? f.pa_solar : (((f.pa - f.p_angle) % 360) + 360) % 360;
    rows.push([t('位置角（天の北から）'), `${f.pa.toFixed(2)}°`], [t('位置角（太陽の北から）'), `${paSolar.toFixed(2)}°`]);
  }
  if (kind === 'ground' && f.sun_alt != null) rows.push([t('太陽の高度・方位'), `${f.sun_alt.toFixed(1)}°${sep()}${(((f.sun_az % 360) + 360) % 360).toFixed(0)}°` + paren(azName(f.sun_az))]);
  if (kind === 'space') {
    if (f.sat_lat != null && f.sat_lon != null) rows.push([t('衛星直下点'), `${fmtLat(f.sat_lat)} ${fmtLon(f.sat_lon)}`]);
    if (f.sat_alt_km != null) rows.push([t('衛星の高度'), km(f.sat_alt_km.toFixed(0))]);
    if (!f.visApprox) rows.push([t('地球の縁からの太陽'), `${f.vis.toFixed(2)}°`]);
  }
  rows.push([t('見えるか'), kind === 'geocenter' ? '—' : visible ? t('見える') : (kind === 'ground' ? t('地平線の下') : t('地球に隠れている'))]);
  const time = f.time || new Date(f.ms).toISOString();
  const utc = exact ? fmtUtc(time) : fmtUtc(roundTime(time, 1).toISOString()).slice(0, -2);
  $('#readout').innerHTML = `<div class="t">${fmtTime(time, exact ? 3 : 1)} <small class="muted">${esc(tzLabel(time))}</small></div>` +
    `<div class="muted" style="font-size:12px">${esc(fmtDate(time))}${tzId() === 'UTC' ? '' : esc(sep() + 'UTC ' + utc)}</div>` +
    `<span class="phase">${esc(phaseLabel(f, L))}</span> <span class="src${exact ? ' exact' : ''}">${esc(exact ? t('サーバーで計算した値') : t('動画用の補間値'))}</span>` +
    (f.outside ? `<div class="hint">${esc(t('動画の範囲の外の時刻です（その時刻の値で描いています）。'))}</div>` : '') +
    `<table>${rows.map(([a, b]) => `<tr><td>${esc(a)}</td><td>${esc(b)}</td></tr>`).join('')}</table>`;
}

/* ------------------------------------------------------------------ */
/* values at given times (POST /api/event/{id}/at)                     */
/* ------------------------------------------------------------------ */
/* The values of the event shown in the viewer at given times; the event of a list or of a saved
   file is computed again when the server has forgotten it, and so is the view at the place of
   the greatest eclipse of a global event (saved without the server's id). */
async function eventAt(body) {
  const L = state.local, d = state.detail;
  if (!L) throw new Error(t('現象が選ばれていません'));
  const at = (id) => api(`/api/event/${encodeURIComponent(id)}/at`, body);
  const call = async () => {
    if (L === d) return at(await detailServerId(d));
    if (L.id) {
      try { return await at(L.id); } catch (err) {
        if (err.status !== 404) throw err;
        L.id = null;
      }
    }
    const r = await geLocal(d);
    if (!r.found) throw new Error(t('この地点では食は起こりません。'));
    L.id = r.id;
    return at(r.id);
  };
  try { return await call(); } catch (err) {
    if (err.status !== 404) throw err;
    await liveDetail(d.id);
    return call();
  }
}
/* "時刻を指定": the values of the server at a time in UTC (e.g. that of an image). */
async function showTimeAt() {
  const st = $('#atStatus'), text = $('#atTime').value.trim(), L = state.local;
  if (!state.series || !L) return;
  if (!text) { setStatus(st, t('時刻を入力してください（UTC）'), 'err'); return; }
  setStatus(st, t('計算中…'), 'busy');
  try {
    const r = await eventAt({ times: [text] });
    if (state.local !== L || !state.series) return;      // another event meanwhile
    const row = r.rows[0];
    setTime((msOf(row.time) - state.series.t0ms) / 1000, row);
    setStatus(st, (r.warnings || []).map((w) => '⚠ ' + w).join('\n'));
  } catch (err) { if (state.local === L) setStatus(st, err.message, 'err'); }
}
/* "観測画像と比べる": one line per image, "TIME, X, Y" or "TIME" (also separated by tabs or ";", or
   "TIME X Y" with spaces when X and Y are numbers); "#" starts a comment. */
function parseCompare(text) {
  const out = [];
  const isNum = (v) => /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/.test(v);
  text.split(/\r?\n/).forEach((raw, i) => {
    const line = raw.replace(/#.*/, '').trim();
    if (!line) return;
    let f = line.split(/\s*[,;\t]\s*/);
    while (f.length > 1 && f[f.length - 1] === '') f.pop();      // a comma at the end
    if (f.length === 1) {
      const w = line.split(/\s+/);
      if (w.length >= 3 && isNum(w[w.length - 1]) && isNum(w[w.length - 2])) f = [w.slice(0, -2).join(' '), w[w.length - 2], w[w.length - 1]];
    }
    if (f.length === 1) out.push({ time: f[0], xy: null });
    else if (f.length === 3 && isNum(f[1]) && isNum(f[2])) out.push({ time: f[0], xy: [+f[1], +f[2]] });
    else throw new Error(t('{n} 行目「{line}」を読めません（「時刻, X, Y」または「時刻」の形で書いてください）', { n: i + 1, line: raw.trim() }));
  });
  return out;
}
const MAX_AT_TIMES = 2000;
async function runCompare() {
  const st = $('#cmpStatus'), L = state.local;
  if (!state.series || !L) return;
  let items;
  try { items = parseCompare($('#cmpText').value); } catch (err) { setStatus(st, err.message, 'err'); return; }
  if (!items.length) { setStatus(st, t('時刻を 1 行に 1 つずつ入力してください（UTC）'), 'err'); return; }
  if (items.length > MAX_AT_TIMES) { setStatus(st, t('一度に計算できる時刻は {n} 個までです', { n: MAX_AT_TIMES }), 'err'); return; }
  const body = { times: items.map((x) => x.time) };
  if (items.some((x) => x.xy)) body.measured = items.map((x) => x.xy);
  const exp = parseFloat($('#cmpExp').value);
  if (isFinite(exp) && exp > 0) body.exposure_s = exp;
  setStatus(st, t('計算中…'), 'busy');
  try {
    const r = await eventAt(body);
    if (state.local !== L) return;
    state.cmp = { rows: r.rows, measured: !!body.measured, exposure: body.exposure_s || 0, body: L.body };
    renderCompare();
    setStatus(st, [t('{n} 個の時刻を計算しました。行をクリックすると、その時刻の見え方を表示します。', { n: r.rows.length }),
      ...(r.warnings || []).map((w) => '⚠ ' + w)].join('\n'));
  } catch (err) { if (state.local === L) setStatus(st, err.message, 'err'); }
}
function clearCompare() {
  state.cmp = null;
  $('#cmpTable').innerHTML = '';
  $('#cmpCsv').disabled = true;
  setStatus($('#cmpStatus'), '');
  setStatus($('#atStatus'), '');
}
function renderCompare() {
  const c = state.cmp, tbl = $('#cmpTable');
  tbl.innerHTML = '';
  $('#cmpCsv').disabled = !c;
  if (!c) return;
  const moon = c.body === 'moon';
  const head = [t('入力'), c.exposure ? t('UTC（露光の中央）') : 'UTC', t('太陽の P 角'), t('X（″）'), t('Y（″）'), t('太陽の縁からの距離（外側が +）')];
  if (moon) head.push(t('食分'));
  head.push(t('見えるか'));
  if (c.measured) head.push(t('O−C X（″）'), t('O−C Y（″）'), t('O−C（時間）'));
  const num = (v, d) => (v == null || !isFinite(v) ? '—' : (+v).toFixed(d));
  const rows = c.rows.map((r) => {
    const tr = el('tr', { class: 'click' },
      el('td', {}, r.input ?? ''), el('td', {}, fmtUtc(r.time)), el('td', {}, `${signed(r.p_angle, 2)}°`),
      el('td', {}, num(r.hpc_x, 2)), el('td', {}, num(r.hpc_y, 2)), limbCell(r, c.body),
      moon && el('td', {}, f4(r.magnitude)),
      el('td', {}, r.visible ? el('span', { class: 'pill ok' }, t('見える')) : el('span', { class: 'pill no' }, t('見えない'))),
      c.measured && el('td', {}, r.oc_x == null ? '—' : signed(r.oc_x, 2)),
      c.measured && el('td', {}, r.oc_y == null ? '—' : signed(r.oc_y, 2)),
      c.measured && el('td', {}, r.oc_dt == null ? '—' : t('{s}秒', { s: signed(r.oc_dt, 2) })));
    tr.addEventListener('click', () => {
      const s = state.series;
      if (!s) return;
      setTime((msOf(r.time) - s.t0ms) / 1000, r);
      $('#diskCanvas').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });
    return tr;
  });
  tbl.append(el('thead', {}, el('tr', {}, head.map((h) => el('th', {}, h)))), el('tbody', {}, rows));
  if (c.measured) {
    const m = c.rows.filter((r) => r.oc_x != null);
    if (m.length) {
      const stat = (k) => {
        const v = m.map((r) => r[k]).filter((x) => x != null && isFinite(x));
        const mean = v.reduce((a, b) => a + b, 0) / v.length;
        const sd = v.length > 1 ? Math.sqrt(v.reduce((a, b) => a + (b - mean) ** 2, 0) / (v.length - 1)) : null;
        return [mean, sd];
      };
      const [x, sx] = stat('oc_x'), [y, sy] = stat('oc_y'), [dt, sdt] = stat('oc_dt');
      const pm = (v, s, d) => signed(v, d) + (s != null ? ' ± ' + s.toFixed(d) : '');
      tbl.append(el('tfoot', {}, el('tr', {}, el('td', { colspan: head.length },
        t('O−C の平均 ± 標準偏差（{n} 枚）: X {x}″、Y {y}″、時間 {dt} 秒', { n: m.length, x: pm(x, sx, 2), y: pm(y, sy, 2), dt: pm(dt, sdt, 2) })))));
    }
  }
  tbl.prepend(el('caption', {}, c.measured
    ? t('O−C は画像で測った位置から計算した位置を引いた差です。O−C（時間）は、観測された位置が計算より運動の向きに何秒先に進んでいるかを表します。')
    : t('X・Y は太陽の北が上の太陽の中心からの角度（″、X は西、Y は北が正）です。')));
}
function downloadCompareCsv() {
  const c = state.cmp, L = state.local;
  if (!c || !c.rows.length) return;
  const keys = [...new Set(c.rows.flatMap((r) => Object.keys(r)))];
  const rows = [keys, ...c.rows.map((r) => keys.map((k) => r[k]))];
  saveFile(`eclipse_at_${(L ? L.max : c.rows[0].time).slice(0, 10)}.csv`, toCsv(rows), 'text/csv');
}

/* ------------------------------------------------------------------ */
/* charts (SVG)                                                        */
/* ------------------------------------------------------------------ */
const SVGNS = 'http://www.w3.org/2000/svg';
function svg(tag, attrs = {}) { const e = document.createElementNS(SVGNS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); return e; }
function niceStep(range, target = 6) {
  const raw = range / target, p = Math.pow(10, Math.floor(Math.log10(raw)));
  for (const m of [1, 2, 5, 10]) if (m * p >= raw) return m * p;
  return 10 * p;
}
function lineChart(box, cfg) {
  box.innerHTML = '';
  box.append(el('div', { class: 'ttl' }, cfg.title));
  const W = 900, H = 240, pl = 58, pr = 16, pt = 10, pb = 34;
  const s = state.series;
  const X = (t) => pl + (W - pl - pr) * t / s.dur;
  const [y0, y1] = cfg.yRange;
  const Y = (v) => pt + (H - pt - pb) * (1 - (v - y0) / (y1 - y0));
  const root = svg('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img' });
  // shading (not visible)
  if (cfg.shade) {
    let start = null;
    const n = s.dt_s.length;
    for (let i = 0; i <= n; i++) {
      const bad = i < n && cfg.shade(i);
      if (bad && start == null) start = i;
      if ((!bad || i === n) && start != null) {
        root.append(svg('rect', { x: X(s.dt_s[start]), y: pt, width: Math.max(1, X(s.dt_s[Math.min(i, n - 1)]) - X(s.dt_s[start])), height: H - pt - pb, fill: '#eef0f4' }));
        start = null;
      }
    }
  }
  const ys = niceStep(y1 - y0, 5);
  for (let v = Math.ceil(y0 / ys) * ys; v <= y1 + 1e-9; v += ys) {
    root.append(svg('line', { x1: pl, x2: W - pr, y1: Y(v), y2: Y(v), stroke: '#e3e7ee' }));
    const tx = svg('text', { x: pl - 6, y: Y(v) + 4, 'text-anchor': 'end', 'font-size': 11, fill: '#7a8396' });
    tx.textContent = cfg.yFmt ? cfg.yFmt(v) : +v.toFixed(3);
    root.append(tx);
  }
  // time ticks
  const steps = [60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600];
  const tstep = steps.find((x) => s.dur / x <= 9) || 21600;
  const firstMs = Math.ceil(s.t0ms / 1000 / tstep) * tstep * 1000;
  for (let ms = firstMs; ms <= s.t0ms + s.dur * 1000; ms += tstep * 1000) {
    const t = (ms - s.t0ms) / 1000;
    root.append(svg('line', { x1: X(t), x2: X(t), y1: H - pb, y2: H - pb + 4, stroke: '#9aa4b8' }));
    const tx = svg('text', { x: X(t), y: H - pb + 17, 'text-anchor': 'middle', 'font-size': 11, fill: '#4a5468' });
    tx.textContent = fmtHM(new Date(ms).toISOString());
    root.append(tx);
  }
  const tzt = svg('text', { x: W - pr, y: H - 3, 'text-anchor': 'end', 'font-size': 11, fill: '#7a8396' });
  tzt.textContent = t('時刻（{tz}）', { tz: tzLabel(new Date(s.t0ms).toISOString()) });
  root.append(tzt);
  root.append(svg('line', { x1: pl, x2: W - pr, y1: H - pb, y2: H - pb, stroke: '#9aa4b8' }));
  for (const h of cfg.hlines || []) {
    root.append(svg('line', { x1: pl, x2: W - pr, y1: Y(h.v), y2: Y(h.v), stroke: h.color, 'stroke-dasharray': '4 4' }));
    const tx = svg('text', { x: W - pr - 4, y: Y(h.v) - 4, 'text-anchor': 'end', 'font-size': 11, fill: h.color });
    tx.textContent = h.label; root.append(tx);
  }
  // contacts
  for (const c of state.local.contacts) {
    const tc = (msOf(c.time) - s.t0ms) / 1000;
    root.append(svg('line', { x1: X(tc), x2: X(tc), y1: pt, y2: H - pb, stroke: c.label === 'MAX' ? '#e8850c' : '#b8c0cf', 'stroke-dasharray': '3 3' }));
    const tx = svg('text', { x: X(tc) + 3, y: pt + 11, 'font-size': 10, fill: '#7a8396' });
    tx.textContent = c.label === 'MAX' ? t('最大') : c.label; root.append(tx);
  }
  for (const ser of cfg.series) {
    let dstr = '';
    s.dt_s.forEach((t, i) => {
      const v = ser.y[i];
      if (v == null || !isFinite(v)) return;
      dstr += (dstr ? 'L' : 'M') + X(t).toFixed(1) + ',' + Y(Math.max(y0, Math.min(y1, v))).toFixed(1);
    });
    root.append(svg('path', { d: dstr, fill: 'none', stroke: ser.color, 'stroke-width': 2.2 }));
  }
  // legend
  let lx = pl + 8;
  for (const ser of cfg.series) {
    root.append(svg('rect', { x: lx, y: pt + 18, width: 14, height: 3, fill: ser.color }));
    const tx = svg('text', { x: lx + 18, y: pt + 23, 'font-size': 11, fill: '#4a5468' });
    tx.textContent = ser.label; root.append(tx);
    lx += 30 + ser.label.length * (wideScript() ? 11 : 6.5);
  }
  const cur = svg('line', { x1: X(state.t), x2: X(state.t), y1: pt, y2: H - pb, stroke: '#d62828', 'stroke-width': 1.5, class: 'cursor' });
  root.append(cur);
  root.addEventListener('click', (ev) => {
    const r = root.getBoundingClientRect();
    const x = (ev.clientX - r.left) / r.width * W;
    setTime((x - pl) / (W - pl - pr) * s.dur);
    showTab('view');
  });
  root.style.cursor = 'pointer';
  box._X = X;
  box.append(root);
}
function renderCharts() {
  const s = state.series, L = state.local;
  const c1 = $('#chart1'), c2 = $('#chart2');
  if (!s || !L) { c1.innerHTML = `<p class="muted">${esc(t('計算中…'))}</p>`; c2.innerHTML = ''; return; }
  const kind = L.observer_kind;
  const notVis = (i) => kind !== 'geocenter' && s.vis[i] <= 0;
  if (L.body === 'moon') {
    const ymax = Math.max(1, ...s.magnitude) * 1.02;
    lineChart(c1, {
      title: t('食分と食面積率の変化'), yRange: [0, ymax], shade: notVis, yFmt: (v) => v.toFixed(1),
      series: [{ y: s.magnitude, color: '#e8850c', label: t('食分') }, { y: s.obscuration, color: '#2463c9', label: t('食面積率') }],
    });
  } else {
    const rs = s.rho_s[0], rb = s.rho_b[0];
    lineChart(c1, {
      title: t('{body}の中心と太陽の中心の距離（″）', { body: bodyJa(L.body) }), yRange: [0, Math.max(...s.sep_arcsec) * 1.05], shade: notVis, yFmt: (v) => v.toFixed(0),
      series: [{ y: s.sep_arcsec, color: '#2463c9', label: t('中心間距離') }],
      hlines: [{ v: rs + rb, color: '#e8850c', label: t('外接') }, { v: rs - rb, color: '#19875f', label: t('内接') }],
    });
  }
  if (kind === 'ground') {
    const lo = Math.min(-5, ...s.sun_alt), hi = Math.max(10, ...s.sun_alt);
    lineChart(c2, {
      title: t('太陽の高度（°）'), yRange: [Math.floor(lo / 5) * 5, Math.ceil(hi / 5) * 5], shade: notVis, yFmt: (v) => v.toFixed(0),
      series: [{ y: s.sun_alt, color: '#c9a227', label: t('太陽高度') }], hlines: [{ v: 0, color: '#19875f', label: t('地平線') }],
    });
  } else if (kind === 'space') {
    const lo = Math.min(-2, ...s.vis), hi = Math.max(2, ...s.vis);
    lineChart(c2, {
      title: t('地球の縁から太陽までの角度（°）— 0 未満は地球に隠される'), yRange: [lo, hi], shade: notVis, yFmt: (v) => v.toFixed(0),
      series: [{ y: s.vis, color: '#2463c9', label: t('地球の縁からの離角') }], hlines: [{ v: 0, color: '#c0392b', label: t('地球の縁') }],
    });
  } else c2.innerHTML = '';
}
function updateCursor() {
  for (const box of [$('#chart1'), $('#chart2')]) {
    const cur = box.querySelector('.cursor');
    if (cur && box._X) { const x = box._X(state.t); cur.setAttribute('x1', x); cur.setAttribute('x2', x); }
  }
}

/* ------------------------------------------------------------------ */
/* one-sheet diagram (経過図)                                          */
/* ------------------------------------------------------------------ */
/* Position angle of the Sun's rotation axis, degrees east of the celestial north (Meeus, ch. 25 and 29; about 0.01°). */
function solarP(ms) {
  const R = Math.PI / 180, jd = ms / 86400000 + 2440587.5, T = (jd - 2451545) / 36525;
  const L0 = 280.46646 + 36000.76983 * T + 0.0003032 * T * T;
  const M = (357.52911 + 35999.05029 * T - 0.0001537 * T * T) * R;
  const C = (1.914602 - 0.004817 * T - 0.000014 * T * T) * Math.sin(M) + (0.019993 - 0.000101 * T) * Math.sin(2 * M) + 0.000289 * Math.sin(3 * M);
  const om = (125.04 - 1934.136 * T) * R;
  const lam = (L0 + C - 0.00569 - 0.00478 * Math.sin(om)) * R;
  const eps = (23.439291 - 0.0130042 * T + 0.00256 * Math.cos(om)) * R;
  const K = (73.6667 + 1.3958333 * (jd - 2396758) / 36525) * R;
  return (Math.atan(-Math.cos(lam) * Math.tan(eps)) + Math.atan(-Math.cos(lam - K) * Math.tan(7.25 * R))) / R;
}
/* Rough width of a text in px, to squeeze a long one into its place. */
function textWidth(s, size) {
  let w = 0;
  for (const ch of String(s)) w += /[　-鿿＀-￯]/.test(ch) ? size : size * 0.58;
  return w;
}
function sheetText(root, x, y, s, { size = 12, fill = '#262b38', anchor = 'start', weight = null, w = 0, transform = null } = {}) {
  const e = svg('text', { x, y, 'font-size': size, fill, 'text-anchor': anchor });
  if (weight) e.setAttribute('font-weight', weight);
  if (transform) e.setAttribute('transform', transform);
  if (w && textWidth(s, size) * (weight ? 1.06 : 1) > w) { e.setAttribute('textLength', w); e.setAttribute('lengthAdjust', 'spacingAndGlyphs'); }
  e.textContent = s;
  root.append(e);
  return e;
}
function fmtStep(s) { return s < 60 ? t('{s}秒', { s }) : s < 3600 ? t('{m}分', { m: s / 60 }) : t('{h}時間', { h: s / 3600 }); }
const SHEET_FONT = 'system-ui, -apple-system, "Segoe UI", "Hiragino Sans", "Yu Gothic UI", Meiryo, "Noto Sans", sans-serif';
const SHEET_STYLE = {
  C1: { color: '#2463c9', dash: '2 4' }, MAX: { color: '#e8850c', dash: '9 5' }, C4: { color: '#19875f', dash: '12 4 2 4' },
  C2: { color: '#8a3fd1' }, C3: { color: '#8a3fd1' },
};

/* "太陽の半径が 1″ 大きいと: 第1接触 −11.9 秒、…" (dt_drs of the contacts), or '' */
function sensitivityLine(d) {
  const cs = d.contacts.filter((c) => c.label !== 'MAX' && c.dt_drs != null);
  if (!cs.length) return '';
  return t('太陽の半径が 1″ 大きいと: {list}', { list: cs.map((c) => `${contactName(c.label, d)[0]} ${t('{s}秒', { s: signed(c.dt_drs, 1) })}`).join(listSep()) });
}
function renderSheet() {
  const box = $('#sheet');
  const d = state.detail, L = state.local;
  if (!d || !L || !state.series) { box.innerHTML = `<p class="muted" style="padding:12px">${esc(t('計算中…'))}</p>`; return; }
  box.replaceChildren(sheetSvg(d, L, $('#sheetOrient').value));
}

/* The event on one sheet: the body's path across the Sun with its circles at the contacts,
   the contact times and positions, and the main values (after the NAOJ / Hinode prediction charts). */
function sheetSvg(d, L, orient) {
  const s = state.series;
  const moon = L.body === 'moon', kind = L.observer_kind;
  const cs = L.contacts;
  const cmax = cs.find((c) => c.label === 'MAX');
  const tc = (c) => (msOf(c.time) - s.t0ms) / 1000;
  const solar = orient === 'solar';
  const Pdeg = cmax.p_angle != null ? cmax.p_angle : solarP(msOf(cmax.time));
  // the P angle at a contact, at a point of the time series and at a time (0 with the celestial north up)
  const Pc = (c) => (solar ? (c.p_angle != null ? c.p_angle : Pdeg) : 0);
  const Pi = (i) => (solar ? (s.p_angle ? s.p_angle[i] : Pdeg) : 0);
  const Pt = (tt) => (solar ? (s.p_angle ? sample(s.p_angle, tt) : Pdeg) : 0);
  // (x, y): arcseconds west and north of the Sun's centre (solar west and north: helioprojective X, Y)
  const XY = (xi, eta, P) => {
    const r = P * Math.PI / 180, cr = Math.cos(r), sr = Math.sin(r);
    return [-(xi * cr - eta * sr), eta * cr + xi * sr];
  };
  // a square frame around the Sun and the body at the contacts, on a round grid
  const box = [-cmax.rho_s, cmax.rho_s, -cmax.rho_s, cmax.rho_s];
  for (const c of cs) {
    const [x, y] = XY(c.xi, c.eta, Pc(c));
    box[0] = Math.min(box[0], x - c.rho_b); box[1] = Math.max(box[1], x + c.rho_b);
    box[2] = Math.min(box[2], y - c.rho_b); box[3] = Math.max(box[3], y + c.rho_b);
  }
  const span = Math.max(box[1] - box[0], box[3] - box[2]), m = span * 0.03;
  const gstep = niceStep(span, 12);
  const nx = Math.ceil((box[1] + m) / gstep) - Math.floor((box[0] - m) / gstep), ny = Math.ceil((box[3] + m) / gstep) - Math.floor((box[2] - m) / gstep);
  const n = Math.max(nx, ny), ext = n * gstep;
  const x0 = (Math.floor((box[0] - m) / gstep) - Math.floor((n - nx) / 2)) * gstep, y0 = (Math.floor((box[2] - m) / gstep) - Math.floor((n - ny) / 2)) * gstep;

  const W = 1000, px = 82, py = 96, S = 600, rx = px + S + 34, rw = W - rx - 24;
  const X = (v) => px + (v - x0) / ext * S, Y = (v) => py + (y0 + ext - v) / ext * S, R = (r) => r / ext * S;
  const P = (c) => { const [x, y] = XY(c.xi, c.eta, Pc(c)); return [X(x), Y(y)]; };
  const cx = X(0), cy = Y(0);
  const sens = sensitivityLine(L);
  const rowH = 30, tableY = py + S + 58, H = tableY + 26 + rowH * cs.length + 76 + (sens ? 18 : 0);

  const root = svg('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, 'font-family': SHEET_FONT, role: 'img' });
  root.append(svg('rect', { x: 0, y: 0, width: W, height: H, fill: '#fff' }));

  // title
  sheetText(root, W / 2, 40, t('{date} の{type}', { date: fmtDate(d.max) + utDateNote(d.max), type: typeLabel(d) }), { size: 22, weight: 700, anchor: 'middle', w: W - 60 });
  sheetText(root, W / 2, 66, t('観測者: {obs}', { obs: observerText(L.observer || d.observer) }), { size: 13, fill: '#4a5468', anchor: 'middle', w: W - 60 });

  // grid and axes
  for (let i = 0; i <= n; i++) {
    const vx = x0 + i * gstep, vy = y0 + i * gstep;
    if (i > 0 && i < n) {
      root.append(svg('line', { x1: X(vx), x2: X(vx), y1: py, y2: py + S, stroke: Math.abs(vx) < 1e-9 ? '#d5dbe5' : '#eef0f4' }));
      root.append(svg('line', { x1: px, x2: px + S, y1: Y(vy), y2: Y(vy), stroke: Math.abs(vy) < 1e-9 ? '#d5dbe5' : '#eef0f4' }));
    }
    root.append(svg('line', { x1: X(vx), x2: X(vx), y1: py + S, y2: py + S + 5, stroke: '#7a8396' }));
    root.append(svg('line', { x1: px - 5, x2: px, y1: Y(vy), y2: Y(vy), stroke: '#7a8396' }));
    sheetText(root, X(vx), py + S + 19, String(Math.round(vx * 100) / 100), { size: 11, fill: '#4a5468', anchor: 'middle' });
    sheetText(root, px - 8, Y(vy) + 4, String(Math.round(vy * 100) / 100), { size: 11, fill: '#4a5468', anchor: 'end' });
  }
  sheetText(root, px + S / 2, py + S + 40, t('東 ← X（″）→ 西'), { size: 12, anchor: 'middle' });
  sheetText(root, 0, 0, orient === 'solar' ? t('太陽の南 ← Y（″）→ 太陽の北') : t('南 ← Y（″）→ 北'),
    { size: 12, anchor: 'middle', transform: `translate(${px - 52},${py + S / 2}) rotate(-90)` });

  const clip = svg('clipPath', { id: 'sheetClip' });
  clip.append(svg('rect', { x: px, y: py, width: S, height: S }));
  const defs = svg('defs');
  defs.append(clip);
  root.append(defs);
  const g = svg('g', { 'clip-path': 'url(#sheetClip)' });
  root.append(g);

  // the Sun
  const rs = R(cmax.rho_s);
  g.append(svg('circle', { cx, cy, r: rs, fill: '#fff4d6', stroke: '#e39b17', 'stroke-width': 1.8 }));
  g.append(svg('path', { d: `M${cx - 6},${cy}h12M${cx},${cy - 6}v12`, stroke: '#c98a14', 'stroke-width': 1.2 }));

  // the path of the body's centre; dashed where it cannot be seen
  const hidden = (i) => kind !== 'geocenter' && s.vis[i] <= 0;
  let anyHidden = false;
  const segs = [];
  s.dt_s.forEach((_, i) => {
    const [x, y] = XY(s.xi[i], s.eta[i], Pi(i));
    const pt = `${X(x).toFixed(1)},${Y(y).toFixed(1)}`;
    const h = hidden(i);
    anyHidden = anyHidden || h;
    const last = segs[segs.length - 1];
    if (last && last.h === h) last.pts.push(pt);
    else { if (last) last.pts.push(pt); segs.push({ h, pts: [pt] }); }
  });
  for (const sg of segs) {
    g.append(svg('path', { d: 'M' + sg.pts.join('L'), fill: 'none', stroke: sg.h ? '#9aa4b8' : '#262b38', 'stroke-width': 1.6, 'stroke-dasharray': sg.h ? '5 4' : 'none' }));
  }
  const at = (tt) => { const [x, y] = XY(sample(s.xi, tt), sample(s.eta, tt), Pt(tt)); return [X(x), Y(y)]; };
  // side of the path away from the Sun at time tt (unit vector on the screen)
  const outward = (tt) => {
    const dt = s.dur / 400, [x1, y1] = at(Math.max(0, tt - dt)), [x2, y2] = at(Math.min(s.dur, tt + dt)), [x, y] = at(tt);
    let nx = -(y2 - y1), ny = x2 - x1;
    const n = Math.hypot(nx, ny) || 1;
    nx /= n; ny /= n;
    if (nx * (x - cx) + ny * (y - cy) < 0) { nx = -nx; ny = -ny; }
    return [nx, ny];
  };
  const t1 = tc(cs[0]), t4 = tc(cs[cs.length - 1]);
  // name of the Sun across the path from the body at the maximum
  {
    const [nx, ny] = outward(tc(cmax)), lx = cx - nx * 0.5 * rs, ly = cy - ny * 0.5 * rs;
    sheetText(g, lx, ly, t('太陽'), { size: 18, fill: '#b5790f', anchor: 'middle', weight: 700 });
    sheetText(g, lx, ly + 18, t('視半径 {v}″', { v: cmax.rho_s.toFixed(2) }), { size: 12, fill: '#8a6a1f', anchor: 'middle' });
  }
  // arrow for the direction of motion, half-way between the maximum and the end
  {
    const ta = (tc(cmax) + t4) / 2;
    const [x1, y1] = at(ta - s.dur / 200), [x2, y2] = at(ta + s.dur / 200);
    const a = Math.atan2(y2 - y1, x2 - x1), [x, y] = at(ta), k = 9;
    const p = (b, r) => `${(x + r * Math.cos(b)).toFixed(1)},${(y + r * Math.sin(b)).toFixed(1)}`;
    g.append(svg('path', { d: `M${p(a, k)}L${p(a + 2.6, k)}L${p(a - 2.6, k)}Z`, fill: '#262b38' }));
  }
  // time marks
  const steps = [1, 2, 5, 10, 15, 20, 30, 60, 120, 300, 600, 900, 1200, 1800, 3600, 7200, 10800];
  const tstep = steps.find((x) => (t4 - t1) / x <= 10) || 10800;
  for (let ms = Math.ceil((s.t0ms + t1 * 1000) / (tstep * 1000)) * tstep * 1000; ms <= s.t0ms + t4 * 1000; ms += tstep * 1000) {
    const tt = (ms - s.t0ms) / 1000, [x, y] = at(tt), [nx, ny] = outward(tt);
    g.append(svg('path', { d: `M${x},${y - 5.5}L${x + 5},${y + 3.5}L${x - 5},${y + 3.5}Z`, fill: '#fff', stroke: '#4a5468', 'stroke-width': 1.2 }));
    const iso = new Date(ms).toISOString();
    sheetText(g, x - nx * 15, y - ny * 15 + 4, tstep >= 60 ? fmtHM(iso) : fmtTime(iso),
      { size: 10, fill: '#7a8396', anchor: -nx > 0.3 ? 'start' : -nx < -0.3 ? 'end' : 'middle' });
  }
  // the body at the first contact, the maximum and the last contact
  for (const c of [cs[0], cmax, cs[cs.length - 1]]) {
    const [x, y] = P(c), st = SHEET_STYLE[c.label];
    g.append(svg('circle', { cx: x, cy: y, r: Math.max(R(c.rho_b), 2), fill: 'none', stroke: st.color, 'stroke-width': 1.6, 'stroke-dasharray': st.dash }));
  }
  // contact points on the Sun's limb, and the deepest point at the maximum
  const point = (c) => {
    const sep = Math.hypot(c.xi, c.eta) || 1, ux = c.xi / sep, uy = c.eta / sep;
    let k;
    if (c.label === 'MAX') {      // the Moon's limb on the line of the centres, which gives the magnitude
      k = c.sep_arcsec - c.rho_b;
      if (!moon || Math.abs(k) > c.rho_s) return null;
    } else k = (c.label === 'C2' || c.label === 'C3') && c.rho_b_int > c.rho_s ? -c.rho_s : c.rho_s;
    return XY(k * ux, k * uy, Pc(c));
  };
  for (const c of cs) {
    const st = SHEET_STYLE[c.label] || SHEET_STYLE.C2, [x, y] = P(c), q = point(c);
    if (q) g.append(svg('circle', { cx: X(q[0]), cy: Y(q[1]), r: 3.5, fill: st.color }));
    g.append(svg('rect', { x: x - 4, y: y - 4, width: 8, height: 8, fill: '#fff', stroke: st.color, 'stroke-width': 1.8 }));
  }
  // times of the first contact, the maximum and the last contact, and of the others where there is room
  const main = (c) => c.label === 'C1' || c.label === 'MAX' || c.label === 'C4';
  const labelled = [];
  for (const c of [...cs.filter(main), ...cs.filter((c) => !main(c))]) {
    const st = SHEET_STYLE[c.label] || SHEET_STYLE.C2, [x, y] = P(c);
    if (!main(c) && labelled.some(([a, b]) => Math.hypot(a - x, b - y) < 40)) continue;
    labelled.push([x, y]);
    const [nx, ny] = outward(tc(c));
    sheetText(g, x + nx * 16, y + ny * 16 + 4, fmtTime(c.time, 1),
      { size: 11, weight: 700, fill: st.color, anchor: nx > 0.3 ? 'start' : nx < -0.3 ? 'end' : 'middle' });
  }
  root.append(svg('rect', { x: px, y: py, width: S, height: S, fill: 'none', stroke: '#7a8396' }));

  // legend
  let yy = py + 8;
  sheetText(root, rx, yy, t('凡例'), { size: 13, weight: 700 });
  yy += 22;
  const legend = (draw, label) => { draw(rx, yy - 4); sheetText(root, rx + 40, yy, label, { size: 12, w: rw - 40 }); yy += 22; };
  for (const c of [cs[0], cmax, cs[cs.length - 1]]) {
    const st = SHEET_STYLE[c.label];
    legend((x, y) => root.append(svg('line', { x1: x, x2: x + 30, y1: y, y2: y, stroke: st.color, 'stroke-width': 1.8, 'stroke-dasharray': st.dash })),
      contactName(c.label, L)[0]);
  }
  const inner = cs.filter((c) => c.label === 'C2' || c.label === 'C3');
  if (inner.length) {
    legend((x, y) => root.append(svg('rect', { x: x + 11, y: y - 4, width: 8, height: 8, fill: '#fff', stroke: SHEET_STYLE.C2.color, 'stroke-width': 1.8 })),
      [...new Set(inner.map((c) => contactName(c.label, L)[0]))].join(sep()));
  }
  legend((x, y) => root.append(svg('line', { x1: x, x2: x + 30, y1: y, y2: y, stroke: '#262b38', 'stroke-width': 1.6 })), t('{body}の中心の経路', { body: bodyJa(L.body) }));
  if (anyHidden) {
    legend((x, y) => root.append(svg('line', { x1: x, x2: x + 30, y1: y, y2: y, stroke: '#9aa4b8', 'stroke-width': 1.6, 'stroke-dasharray': '5 4' })),
      kind === 'ground' ? t('見えない区間（太陽が地平線の下）') : t('見えない区間（太陽が地球に隠される）'));
  }
  legend((x, y) => root.append(svg('path', { d: `M${x + 15},${y - 5.5}L${x + 20},${y + 3.5}L${x + 10},${y + 3.5}Z`, fill: '#fff', stroke: '#4a5468', 'stroke-width': 1.2 })),
    t('{step}ごとの位置', { step: fmtStep(tstep) }));
  legend((x, y) => root.append(svg('rect', { x: x + 11, y: y - 4, width: 8, height: 8, fill: '#fff', stroke: '#4a5468', 'stroke-width': 1.8 })), t('接触時の中心'));
  legend((x, y) => root.append(svg('circle', { cx: x + 15, cy: y, r: 3.5, fill: '#4a5468' })), moon ? t('接触点・最も深く欠ける点') : t('接触点'));

  // main values
  yy += 12;
  sheetText(root, rx, yy, t('主な値'), { size: 13, weight: 700 });
  yy += 8;
  const vals = [];
  if (moon) {
    vals.push([t('最大食分'), f4(L.magnitude)], [t('食面積率'), pct(L.obscuration, 2)]);
    if (L.type === 'total' || L.type === 'annular') vals.push([L.type === 'total' ? t('皆既の継続時間') : t('金環の継続時間'), fmtDur(L.central_duration_s, true)]);
    vals.push([t('食の継続時間'), fmtDur(L.duration_s)]);
  } else {
    vals.push([t('太陽中心との最小距離'), f1(L.min_sep_arcsec) + '″'], [t('経過時間'), fmtDur(L.duration_s)]);
    if (L.central_duration_s > 0) vals.push([t('内接している時間'), fmtDur(L.central_duration_s)]);
  }
  vals.push([t('{body}の視半径（最大時）', { body: bodyJa(L.body) }), `${cmax.rho_b.toFixed(2)}″`]);
  if (moon) vals.push([t('視直径比（月/太陽）'), f4(L.ratio)], [t('サロス番号'), String((d.kind === 'global' ? d.saros : L.saros) ?? '—')]);
  if (kind === 'ground') vals.push([t('最大時の太陽高度'), `${cmax.sun_alt.toFixed(1)}°` + paren(azName(cmax.sun_az))]);
  if (kind === 'space') vals.push([t('見える時間の割合'), pct(L.vis_fraction, 0)]);
  vals.push([t('太陽の P 角（自転軸、最大時）'), `${signed(Pdeg, 2)}°`]);
  for (const [k, v] of vals) {
    sheetText(root, rx, yy + 14, k, { size: 11, fill: '#7a8396', w: rw });
    sheetText(root, rx, yy + 32, v, { size: 15, weight: 700, w: rw });
    yy += 38;
  }

  // contact table (the UTC to the millisecond when another time zone is shown)
  const utc = tzId() === 'UTC';
  const cols = [
    { h: t('接触'), w: 148 },
    { h: t('時刻（{tz}）', { tz: tzLabel(d.max) }), w: utc ? 136 : 112 },
    !utc && { h: 'UTC', w: 108 },
    { h: t('中心 X, Y（″）'), w: 126 },
    { h: t('接触点 X, Y（″）'), w: 126 },
    { h: solar ? t('位置角（太陽の北から）') : t('位置角（天の北から）'), w: 84 },
    { h: moon ? t('食分') : t('太陽の縁からの距離'), w: 80 },
  ].filter(Boolean);
  if (kind === 'ground') cols.push({ h: t('太陽高度'), w: 86 });
  if (kind === 'space') cols.push({ h: t('地球の縁からの離角'), w: 100 });
  if (kind !== 'geocenter') cols.push({ h: t('観測'), w: 76 });
  const tw = cols.reduce((a, c) => a + c.w, 0);
  let tx = (W - tw) / 2;
  cols.forEach((c) => { c.x = tx; tx += c.w; });
  root.append(svg('rect', { x: cols[0].x, y: tableY, width: tw, height: 26, fill: '#f3f5f9' }));
  for (const c of cols) sheetText(root, c.x + 6, tableY + 17, c.h, { size: 11, weight: 700, fill: '#4a5468', w: c.w - 10 });
  const xy = (q) => (q ? `(${q[0].toFixed(1)}, ${q[1].toFixed(1)})` : '—');
  cs.forEach((c, i) => {
    const y = tableY + 26 + i * rowH;
    if (c.label === 'MAX') root.append(svg('rect', { x: cols[0].x, y, width: tw, height: rowH, fill: '#fff6e8' }));
    root.append(svg('line', { x1: cols[0].x, x2: cols[0].x + tw, y1: y + rowH, y2: y + rowH, stroke: '#e3e7ee' }));
    const [n, sub] = contactName(c.label, L);
    const st = SHEET_STYLE[c.label] || SHEET_STYLE.C2;
    root.append(svg('rect', { x: cols[0].x + 6, y: y + 6, width: 7, height: 7, fill: '#fff', stroke: st.color, 'stroke-width': 1.6 }));
    sheetText(root, cols[0].x + 19, y + 14, n, { size: 12, weight: 700, w: cols[0].w - 24 });
    if (sub) sheetText(root, cols[0].x + 19, y + 26, sub, { size: 10, fill: '#7a8396', w: cols[0].w - 24 });
    const h = hpcOf(c);
    const cells = [fmtTime(c.time, utc ? 3 : 1) + (dayDiff(c.time, d.max) ? ' ' + fmtDateShort(c.time, 1) : ''),
      !utc && fmtUtc(c.time).slice(11) + (c.time.slice(0, 10) !== L.max.slice(0, 10) ? ` (${c.time.slice(5, 10)})` : ''), xy(XY(c.xi, c.eta, Pc(c))), xy(point(c)),
      `${(solar ? h.paSolar : c.pa).toFixed(1)}°`, moon ? f4(c.magnitude) : `${signed(limbDist(c)[0], 2)}″`].filter((v) => v !== false);
    if (kind === 'ground') cells.push(`${c.sun_alt.toFixed(1)}°`);
    if (kind === 'space') cells.push(`${c.vis.toFixed(1)}°`);
    if (kind !== 'geocenter') cells.push(c.visible ? t('見える') : kind === 'ground' ? t('地平線下') : t('地球に隠れる'));
    const hiddenCell = kind !== 'geocenter' && !c.visible ? cells.length - 1 : -1;
    cells.forEach((v, j) => sheetText(root, cols[j + 1].x + 6, y + 19, v, { size: 12, w: cols[j + 1].w - 10, fill: j === hiddenCell ? '#c0392b' : '#262b38' }));
  });

  // conditions
  let fy = tableY + 26 + rowH * cs.length + 24;
  const p = L.params;
  const model = sunModelText(p) + sep() + bodyModelText(L.body, p);
  sheetText(root, cols[0].x, fy, [`${t('暦')} ${L.ephemeris}`, `ΔT ${t('{v} 秒', { v: L.delta_t_s.toFixed(2) })}`, model].join(sep()), { size: 11, fill: '#4a5468', w: tw });
  fy += 18;
  sheetText(root, cols[0].x, fy, (solar ? t('太陽の自転軸の北が上（P = {p}°）', { p: Pdeg.toFixed(2) }) : t('天の北が上・東が左（空に見えるとおりの向き）'))
    + sep() + t('X・Y は太陽の中心から測った角度（西・北が正）'), { size: 11, fill: '#4a5468', w: tw });
  fy += 18;
  if (sens) {
    sheetText(root, cols[0].x, fy, sens, { size: 11, fill: '#4a5468', w: tw });
    fy += 18;
  }
  const made = new Date().toISOString();
  sheetText(root, cols[0].x + tw, fy, `${document.title}${state.info ? ' v' + state.info.version : ''}${sep()}${t('作成 {time}', { time: `${fmtDT(made)} ${tzLabel(made)}` })}`,
    { size: 10, fill: '#9aa4b8', anchor: 'end', w: tw });
  return root;
}
function sheetFile(ext) { return `eclipse_sheet_${state.detail.max.slice(0, 10)}.${ext}`; }
function sheetSource() { const e = $('#sheet svg'); return e ? new XMLSerializer().serializeToString(e) : null; }
function downloadSheetSvg() { const src = sheetSource(); if (src) saveFile(sheetFile('svg'), src, 'image/svg+xml'); }
function downloadSheetPng() {
  const e = $('#sheet svg'), src = sheetSource();
  if (!src) return;
  const vb = e.viewBox.baseVal, k = 2;
  const img = new Image();
  img.onload = () => {
    const c = el('canvas', { width: vb.width * k, height: vb.height * k });
    c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
    c.toBlob((b) => saveFile(sheetFile('png'), b, 'image/png'));
  };
  img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(src);
}

/* ------------------------------------------------------------------ */
/* map                                                                 */
/* ------------------------------------------------------------------ */
let _worldGeo = null;
function baseLayers(m) {
  const osm = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 18, attribution: '&copy; OpenStreetMap contributors',
  });
  // Panes: basemap (vector, below everything) < raster overlays < lines/markers.
  if (!m.getPane('basemap')) {
    m.createPane('basemap').style.zIndex = 150;
    m.createPane('raster').style.zIndex = 350;
    m.getPane('raster').style.pointerEvents = 'none';
  }
  const simple = L.layerGroup();
  const ocean = L.rectangle([[-85.06, -540], [85.06, 540]], { pane: 'basemap', stroke: false, fillColor: '#b9dbe8', fillOpacity: 1, interactive: false });
  simple.addLayer(ocean);
  const addWorld = (geo) => {
    for (const dx of [-360, 0, 360]) {
      simple.addLayer(L.geoJSON(geo, {
        pane: 'basemap', interactive: false, coordsToLatLng: (c) => L.latLng(c[1], c[0] + dx),
        style: { color: '#9a9585', weight: 0.7, fillColor: '#f4f1e8', fillOpacity: 1 },
      }));
    }
  };
  if (_worldGeo) addWorld(_worldGeo);
  else fetch('/static/vendor/world_110m.geojson').then((r) => r.json()).then((g) => { _worldGeo = g; addWorld(g); }).catch(() => {});
  simple.addTo(m);
  const lc = { m, simple, osm };
  relabelLayerControl(lc);
  state.layerControls.push(lc);
  return { simple, osm };
}
/* (Re)create the switch between the base maps with names in the language of the page. */
function relabelLayerControl(lc) {
  if (lc.ctl) lc.ctl.remove();
  lc.ctl = L.control.layers({ [t('シンプル地図（オフライン）')]: lc.simple, [t('詳細地図（OpenStreetMap）')]: lc.osm }, null, { position: 'topright' }).addTo(lc.m);
}
function ensureMap() {
  if (state.map) return state.map;
  const m = L.map('map', { worldCopyJump: true, minZoom: 1, preferCanvas: true, zoomSnap: 0.5 }).setView([20, 140], 2);
  baseLayers(m);
  state.mapLayers = L.layerGroup().addTo(m);
  m.on('click', onMapClick);
  state.map = m;
  return m;
}
async function loadMap(force = false) {
  const d = state.detail;
  if (!d) return;
  const m = ensureMap();
  setTimeout(() => m.invalidateSize(), 30);
  const st = $('#mapStatus');
  if (state.mapData[d.id] && !force) { drawMap(d, state.mapData[d.id]); return; }
  if (state.mapData[d.id] === undefined || force || state.mapData[d.id] === null) {
    setStatus(st, t('地図データを計算中…（数秒かかります）'), 'busy');
    try {
      const md = await api(`/api/event/${await liveId(d.id)}/map`);
      state.mapData[d.id] = md;
      if (state.detail === d) { drawMap(d, md); setStatus(st, ''); }
    } catch (err) { setStatus(st, err.message, 'err'); }
  }
}
function magColor(v) {
  const stops = [[0, [255, 245, 200]], [0.2, [255, 220, 120]], [0.4, [253, 174, 70]], [0.6, [240, 110, 40]], [0.8, [200, 50, 60]], [0.95, [120, 20, 90]], [1.0, [40, 10, 60]]];
  if (v >= 1) return stops[stops.length - 1][1];
  for (let i = 1; i < stops.length; i++) {
    if (v <= stops[i][0]) {
      const [a, ca] = stops[i - 1], [b, cb] = stops[i];
      const t = (v - a) / (b - a);
      return ca.map((x, j) => Math.round(x + (cb[j] - x) * t));
    }
  }
  return stops[stops.length - 1][1];
}
const TRANSIT_CODES = {
  1: ['#2fa36b', '全経過が見える'], 2: ['#4f86d9', '日の出時にすでに通過中（終わりは見える）'], 3: ['#e39a2d', '日の入り時に通過中（始まりは見える）'],
  4: ['#9b6bd6', '途中の一部だけ見える'], 5: ['#d85fa0', '始まりと終わりは見えるが途中で夜になる'],
};
function gridOverlay(grid, colorFn) {
  const raw = atob(grid.values || grid.codes);
  const n = grid.nlat * grid.nlon;
  const vals = new Float32Array(n);
  if (grid.values) {
    for (let i = 0; i < n; i++) vals[i] = (raw.charCodeAt(2 * i) | (raw.charCodeAt(2 * i + 1) << 8)) / grid.scale;
  } else for (let i = 0; i < n; i++) vals[i] = raw.charCodeAt(i);
  const latMax = grid.lat0 + grid.dlat * (grid.nlat - 1);
  const merc = (lat) => Math.log(Math.tan(Math.PI / 4 + lat * Math.PI / 360));
  const y0 = merc(grid.lat0), y1 = merc(latMax);
  const Wp = 1440, Hp = Math.round(Wp * (y1 - y0) / (2 * Math.PI));
  const cv = document.createElement('canvas'); cv.width = Wp; cv.height = Hp;
  const g = cv.getContext('2d');
  const img = g.createImageData(Wp, Hp);
  const nearest = !grid.values;
  for (let py = 0; py < Hp; py++) {
    const y = y1 - (py + 0.5) / Hp * (y1 - y0);
    const lat = (2 * Math.atan(Math.exp(y)) - Math.PI / 2) * 180 / Math.PI;
    const fy = (lat - grid.lat0) / grid.dlat;
    const iy = Math.max(0, Math.min(grid.nlat - 2, Math.floor(fy))), ty = fy - iy;
    for (let px = 0; px < Wp; px++) {
      const lon = -180 + (px + 0.5) / Wp * 360;
      const fx = (lon - grid.lon0) / grid.dlon;
      const ix = Math.max(0, Math.min(grid.nlon - 2, Math.floor(fx))), tx = fx - ix;
      let v;
      if (nearest) v = vals[(iy + (ty > 0.5 ? 1 : 0)) * grid.nlon + ix + (tx > 0.5 ? 1 : 0)];
      else {
        const a = vals[iy * grid.nlon + ix], b = vals[iy * grid.nlon + ix + 1];
        const c = vals[(iy + 1) * grid.nlon + ix], d = vals[(iy + 1) * grid.nlon + ix + 1];
        v = (a * (1 - tx) + b * tx) * (1 - ty) + (c * (1 - tx) + d * tx) * ty;
      }
      const col = colorFn(v);
      if (!col) continue;
      const o = 4 * (py * Wp + px);
      img.data[o] = col[0]; img.data[o + 1] = col[1]; img.data[o + 2] = col[2]; img.data[o + 3] = col[3];
    }
  }
  g.putImageData(img, 0, 0);
  const url = cv.toDataURL();
  const group = L.layerGroup();
  for (const dx of [-360, 0, 360]) group.addLayer(L.imageOverlay(url, [[grid.lat0, -180 + dx], [latMax, 180 + dx]], { pane: 'raster', opacity: 1, interactive: false }));
  return group;
}
function hexRgb(h) { return h.match(/\w\w/g).map((x) => parseInt(x, 16)); }
function unwrapTrack(pts) {
  const out = [];
  for (const p of pts) {
    let lo = p[1];
    if (out.length) lo += 360 * Math.round((out[out.length - 1][1] - lo) / 360);
    out.push([p[0], lo]);
  }
  return out;
}
const shiftLL = (pts, dx) => pts.map((p) => [p[0], p[1] + dx]);
function addCopies(layers, pts, make) {
  for (const dx of [-360, 0, 360]) layers.addLayer(make(dx ? shiftLL(pts, dx) : pts));
}
function nearLon(pts, ref) {
  if (!pts.length) return pts;
  const dx = 360 * Math.round((ref - pts[0][1]) / 360);
  return dx ? shiftLL(pts, dx) : pts;
}
function drawMap(d, md) {
  const m = ensureMap();
  const layers = state.mapLayers;
  layers.clearLayers();
  const legend = $('#mapLegend');
  legend.innerHTML = '';
  const bounds = L.latLngBounds([]);
  if (!md.exists) {
    legend.append(el('span', {}, t('この現象は地上からは見られない（地球上に食が生じない）ため、食の地図はありません。')));
  } else if (md.map_kind === 'eclipse') {
    const grid = md.grid;
    layers.addLayer(gridOverlay(grid, (v) => (v <= 0.0005 ? null : [...magColor(v), 150])));
    for (const c of grid.contours) {
      for (const ln of c.lines) {
        addCopies(layers, ln, (pts) => L.polyline(pts, { color: c.level < 0.01 ? '#7a3b00' : '#8a4b10', weight: c.level < 0.01 ? 1.6 : 1, opacity: 0.8, dashArray: c.level < 0.01 ? null : '4 4' })
          .bindTooltip(c.level < 0.01 ? t('部分食が見える範囲の境界（太陽が地平線上）') : t('最大食分 {v}', { v: c.level }), { sticky: true, className: 'map-tip' }));
      }
    }
    if (md.penumbra_limits) {
      for (const side of ['N', 'S']) for (const seg of md.penumbra_limits[side] || []) {
        addCopies(layers, seg.map((p) => [p[0], p[1]]), (pts) => L.polyline(pts, { color: '#6b4fbb', weight: 1.5, dashArray: '6 4' }).bindTooltip(side === 'N' ? t('部分食の北限界線') : t('部分食の南限界線'), { sticky: true }));
      }
    }
    if (md.umbra_limits) {
      const N = md.umbra_limits.N || [], S = md.umbra_limits.S || [];
      if (N.length === 1 && S.length === 1) {
        const n0 = N[0].map((p) => [p[0], p[1]]);
        const s0 = nearLon(S[0].map((p) => [p[0], p[1]]), n0[0][1]);
        addCopies(layers, n0.concat(s0.slice().reverse()), (pts) => L.polygon(pts, { stroke: false, fillColor: '#111', fillOpacity: 0.35, interactive: false }));
      }
      const typ = md.event.type;
      const limit = typ === 'annular' ? { N: t('金環帯の北限界線'), S: t('金環帯の南限界線') }
        : typ === 'total' ? { N: t('皆既帯の北限界線'), S: t('皆既帯の南限界線') } : { N: t('中心食帯の北限界線'), S: t('中心食帯の南限界線') };
      for (const [side, arr] of [['N', N], ['S', S]]) for (const seg of arr) {
        const pts = seg.map((p) => [p[0], p[1]]);
        addCopies(layers, pts, (q) => L.polyline(q, { color: '#111', weight: 1.8 }).bindTooltip(limit[side], { sticky: true }));
      }
    }
    for (const seg of md.central_line || []) {
      const pts = seg.map((p) => [p[0], p[1]]);
      addCopies(layers, pts, (q) => L.polyline(q, { color: '#d62828', weight: 2 }).bindTooltip(t('中心線'), { sticky: true }));
      pts.forEach((p) => bounds.extend(p));
    }
    for (const o of md.umbra_outlines || []) {
      for (const seg of o.lines) addCopies(layers, seg, (q) => L.polyline(q, { color: '#000', weight: 1, opacity: 0.7 }).bindTooltip(t('{time} の本影', { time: `${fmtTime(o.time)} ${tzLabel(o.time)}` }), { sticky: true }));
    }
    for (const p of md.central_points || []) {
      addCopies(layers, [[p.lat, p.lon]], (q) => L.circleMarker(q[0], { radius: 3, color: '#d62828', weight: 1, fillOpacity: 0.6 })
        .bindTooltip(`${fmtDT(p.time)} ${tzLabel(p.time)}<br>` + esc(t('中心食の継続 {v}', { v: fmtDur(p.duration_s, true) })) + '<br>' +
          esc(t('太陽高度 {v}°', { v: p.sun_alt.toFixed(0) }))));
    }
    const ev = md.event;
    const ge = L.circleMarker([ev.ge_lat, ev.ge_lon], { radius: 7, color: '#fff', weight: 2, fillColor: '#d62828', fillOpacity: 1 });
    ge.bindTooltip(esc(t('最大食 {time}', { time: `${fmtDT(ev.max)} ${tzLabel(ev.max)}` })) + `<br>${fmtLatLon(ev.ge_lat, ev.ge_lon)}`);
    layers.addLayer(ge);
    if (!bounds.isValid()) {
      // fit to the area where the partial eclipse is visible
      const raw = atob(grid.values);
      for (let iy = 0; iy < grid.nlat; iy += 2) for (let ix = 0; ix < grid.nlon; ix += 2) {
        const i = iy * grid.nlon + ix;
        if ((raw.charCodeAt(2 * i) | (raw.charCodeAt(2 * i + 1) << 8)) > 5) bounds.extend([grid.lat0 + iy * grid.dlat, grid.lon0 + ix * grid.dlon]);
      }
    }
    legend.append(
      el('span', {}, t('最大食分'), el('span', { class: 'grad', style: `background:linear-gradient(90deg,${[0.05, 0.2, 0.4, 0.6, 0.8, 0.95, 1].map((v) => `rgb(${magColor(v).join(',')})`).join(',')})` }), '0 → 1'),
      el('span', {}, el('span', { class: 'ln', style: 'border-color:#d62828' }), t('中心線')),
      ev.type !== 'partial' && el('span', {}, el('span', { class: 'sw', style: 'background:rgba(17,17,17,.4)' }),
        ev.type === 'annular' ? t('金環帯（黒線は限界線・10分ごとの本影）') : t('皆既帯（黒線は限界線・10分ごとの本影）')),
      el('span', {}, el('span', { class: 'ln', style: 'border-color:#6b4fbb;border-top-style:dashed' }), t('部分食の限界線')),
      el('span', { class: 'muted' }, t('色は太陽が地平線上にある時間帯での最大食分。地図をクリックすると、その地点での見え方を計算します。')),
    );
  } else if (md.map_kind === 'transit') {
    layers.addLayer(gridOverlay(md.grid, (v) => (TRANSIT_CODES[v] ? [...hexRgb(TRANSIT_CODES[v][0]), 120] : null)));
    for (const sp of md.subsolar) {
      const lbl = { C1: t('第1接触'), MAX: t('最大'), C4: t('第4接触') }[sp.label];
      layers.addLayer(L.circleMarker([sp.lat, sp.lon], { radius: 6, color: '#fff', weight: 2, fillColor: '#f39b1d', fillOpacity: 1 })
        .bindTooltip(esc(t('{label}の時刻に太陽が真上にある地点', { label: lbl }))));
    }
    legend.append(...Object.values(TRANSIT_CODES).map(([c, l]) => el('span', {}, el('span', { class: 'sw', style: `background:${c}` }), t(l))));
    legend.append(el('span', { class: 'muted' }, t('地心の接触時刻（{start}〜{end} {tz}）にもとづく概略図。地図をクリックすると、その地点での正確な時刻を計算します。',
      { start: fmtTime(md.geocentric.c1), end: fmtTime(md.geocentric.c4), tz: tzLabel(md.geocentric.c1) })));
    bounds.extend([[-60, -180], [70, 180]]);
  }
  // observer
  const Lc = state.local;
  if (d.kind !== 'global' && d.observer_kind === 'ground') {
    const o = d.observer;
    layers.addLayer(L.marker([o.lat, o.lon]).bindTooltip(t('観測地: {name}', { name: esc(o.name) }), { permanent: false }));
    bounds.extend([o.lat, o.lon]);
  }
  if (d.kind !== 'global' && d.observer_kind === 'space' && Lc && Lc.timeseries) {
    const s = Lc.timeseries;
    const pts = unwrapTrack(s.sat_lat.map((la, i) => [la, s.sat_lon[i]]));
    addCopies(layers, pts, (q) => L.polyline(q, { color: '#0aa5c9', weight: 3, opacity: 0.9 }).bindTooltip(t('現象中の衛星直下点の軌跡'), { sticky: true }));
    for (const c of Lc.contacts) {
      const [n] = contactName(c.label, Lc);
      layers.addLayer(L.circleMarker([c.sat_lat, c.sat_lon], { radius: c.label === 'MAX' ? 6 : 4, color: '#055a6e', weight: 1, fillColor: '#0aa5c9', fillOpacity: 1 })
        .bindTooltip(`${esc(n)} ${fmtTime(c.time)}<br>` + esc(t('衛星高度 {v} km', { v: c.sat_alt_km.toFixed(0) }))));
    }
    pts.forEach((p) => bounds.extend(p));
    legend.append(el('span', {}, el('span', { class: 'ln', style: 'border-color:#0aa5c9' }), t('衛星直下点の軌跡')));
  }
  if (state.clickMarker) layers.addLayer(state.clickMarker);
  if (bounds.isValid()) m.fitBounds(bounds.pad(0.15), { maxZoom: 6 });
}
async function onMapClick(ev) {
  const d = state.detail;
  if (!d) return;
  const md = state.mapData[d.id];
  if (!md || !md.exists) return;
  const ll = ev.latlng.wrap();
  const base = state.rootId || d.id;
  const popup = L.popup().setLatLng(ev.latlng).setContent('<span class="spinner"></span>' + esc(t('この地点での見え方を計算中…'))).openOn(state.map);
  try {
    const r = await api('/api/local', { event_id: await liveId(base), lat: ll.lat, lon: ll.lng, elevation_m: 0, name: t('地図上の地点 {pos}', { pos: fmtLatLon(ll.lat, ll.lng) }) });
    if (!r.found) {
      popup.setContent(`${fmtLatLon(ll.lat, ll.lng)}<br>` + esc(d.body === 'moon' || d.kind === 'global' ? t('この地点では食は起こりません。') : t('この地点では見られません。')));
      return;
    }
    const moon = r.body === 'moon';
    const vis = r.vis_fraction >= 0.999 ? t('全経過が見える') : r.vis_fraction > 0 ? t('一部が見える（{p}%）', { p: Math.round(r.vis_fraction * 100) }) : t('太陽が地平線の下で見えない');
    const rows = [
      [t('種類'), typeLabel(r)], [t('始まり'), `${fmtTime(r.c1)}`], [t('最大'), `${fmtTime(r.max)}${moon ? paren(t('食分 {v}', { v: f3(r.magnitude) })) : ''}`], [t('終わり'), fmtTime(r.c4)],
      moon && r.central_duration_s > 0 ? [r.type === 'total' ? t('皆既') : t('金環'), fmtDur(r.central_duration_s, true)] : null,
      [t('太陽高度(最大時)'), `${r.contacts.find((c) => c.label === 'MAX').sun_alt.toFixed(0)}°`], [t('見え方'), vis],
    ].filter(Boolean);
    const btn = el('button', { class: 'popup-btn', type: 'button' }, t('この地点の詳細を表示'));
    btn.addEventListener('click', () => { state.map.closePopup(); openDetail(r.id, { push: true }); });
    const div = el('div', {}, el('b', {}, fmtLatLon(ll.lat, ll.lng)), el('div', { class: 'muted', style: 'font-size:11px' }, fmtDate(r.max) + sep() + tzLabel(r.max)),
      el('table', {}, rows.map(([a, b]) => el('tr', {}, el('td', { class: 'muted' }, a), el('td', {}, b)))), btn);
    popup.setContent(div);
  } catch (err) { popup.setContent(esc(err.message)); }
}

/* ------------------------------------------------------------------ */
/* data tab                                                            */
/* ------------------------------------------------------------------ */
function textSummary() {
  const d = state.detail, L = state.local;
  if (!d) return '';
  const lines = [];
  if (d.kind === 'global') {
    lines.push(`${fmtDate(d.max)} ${typeLabel(d)}` + paren(t('地球全体')));
    lines.push(`${t('最大食 {time}', { time: `${fmtDT(d.max, 1)} ${tzLabel(d.max)}` })}  ${fmtLatLon(d.ge_lat, d.ge_lon)}  ${t('太陽高度 {v}°', { v: d.sun_alt.toFixed(1) })}`);
    lines.push([t('食分 {v}', { v: f4(d.magnitude) }), `γ ${f4(d.gamma)}`, t('中心食の継続 {v}', { v: fmtDur(d.central_duration_s, true) }),
      t('幅 {v}', { v: d.path_width_km ? km(d.path_width_km.toFixed(1)) : '—' }), t('サロス {v}', { v: d.saros })].join('  '));
    lines.push(`P1 ${fmtDT(d.p1, 1)}  P4 ${fmtDT(d.p4, 1)}  ΔT ${d.delta_t_s.toFixed(2)} s`);
    if (L) lines.push('', t('最大食の地点での接触時刻:'));
  } else lines.push(`${fmtDate(d.max)} ${typeLabel(d)}  ${t('観測者: {obs}', { obs: observerText(d.observer) })}`);
  const src = d.kind === 'global' ? L : d;
  if (src) {
    for (const c of src.contacts) {
      const [n, sub] = contactName(c.label, src);
      const name = n + (sub ? paren(sub) : '');
      const h = hpcOf(c);
      let s = `${LANG === 'ja' || LANG === 'zh' ? name.padEnd(18, '　') : name.padEnd(36)} ${fmtDT(c.time, 1)} ${tzLabel(c.time)}  UTC ${fmtUtc(c.time)}  ` +
        `${t('位置角 {v}°', { v: c.pa.toFixed(1) })}  X ${h.x.toFixed(2)}″ Y ${h.y.toFixed(2)}″`;
      if (c.sun_alt != null) s += '  ' + t('高度 {alt}° 方位 {az}°', { alt: c.sun_alt.toFixed(1), az: c.sun_az.toFixed(1) });
      if (c.sat_lat != null) s += '  ' + t('衛星 {pos} {alt} km', { pos: fmtLatLon(c.sat_lat, c.sat_lon), alt: c.sat_alt_km.toFixed(0) });
      if (src.body === 'moon') s += '  ' + t('食分 {v}', { v: f4(c.magnitude) });
      lines.push(s);
    }
  }
  return lines.join('\n');
}
function renderData() {
  const d = state.detail;
  if (!d) return;
  const copy = JSON.parse(JSON.stringify(d));
  if (copy.timeseries) copy.timeseries = t('（{n} 点の時系列 — CSV でダウンロードできます）', { n: copy.timeseries.dt_s.length });
  $('#dataText').textContent = textSummary() + '\n\n' + JSON.stringify(copy, null, 2);
}
function downloadJson() {
  const d = state.detail;
  if (!d) return;
  const out = { tool: SAVE_TOOL, version: state.info.version, saved_at: new Date().toISOString(), event: d, local: state.local };
  out.event = noId(d);
  if (out.local) out.local = noId(out.local);
  saveFile(`eclipse_${d.max.slice(0, 10)}_${d.id}.json`, JSON.stringify(out, null, 2), 'application/json');
}
function downloadSeriesCsv() {
  const L = state.local;
  if (!L || !L.timeseries) return;
  const s = L.timeseries;
  const t0 = msOf(s.t0);
  const keys = Object.keys(s).filter((k) => Array.isArray(s[k]) && k !== 'dt_s');
  const rows = [['time_utc', ...keys]];
  s.dt_s.forEach((dt, i) => rows.push([new Date(t0 + dt * 1000).toISOString(), ...keys.map((k) => s[k][i])]));
  saveFile(`eclipse_timeseries_${L.max.slice(0, 10)}.csv`, toCsv(rows), 'text/csv');
}
async function copyText() {
  try { await navigator.clipboard.writeText(textSummary()); $('#copyText').textContent = t('コピーしました'); }
  catch (_) { $('#copyText').textContent = t('コピーできませんでした'); }
  setTimeout(() => { $('#copyText').textContent = t('結果をテキストでコピー'); }, 1800);
}

init();
