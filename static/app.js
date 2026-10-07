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
  detail: null, local: null, stack: [], rootId: null, saved: null,
  t: 0, playing: false, mapData: {}, map: null, mapLayers: null, pickMap: null,
};

/* ------------------------------------------------------------------ */
/* time & number formatting                                            */
/* ------------------------------------------------------------------ */
const TZ_OPTIONS = [
  ['Asia/Tokyo', '日本時間 (JST)'], ['UTC', '世界時 (UTC)'], ['local', 'このPCの時刻'],
  ['Asia/Seoul', '韓国'], ['Asia/Shanghai', '中国'], ['Asia/Singapore', 'シンガポール'],
  ['Australia/Sydney', 'シドニー'], ['Pacific/Honolulu', 'ハワイ'],
  ['America/Los_Angeles', '米国太平洋'], ['America/Chicago', '米国中部'], ['America/New_York', '米国東部'],
  ['Europe/London', '英国'], ['Europe/Paris', '中央ヨーロッパ'], ['Africa/Cairo', 'エジプト'],
];
const _fmtCache = new Map();
function tzId() { const v = $('#tz').value; return v === 'local' ? undefined : v; }
function dtf(opts) {
  const key = (tzId() || 'local') + JSON.stringify(opts);
  if (!_fmtCache.has(key)) _fmtCache.set(key, new Intl.DateTimeFormat('ja-JP', { timeZone: tzId(), hourCycle: 'h23', ...opts }));
  return _fmtCache.get(key);
}
function parts(date, opts) {
  const o = {};
  for (const p of dtf(opts).formatToParts(date)) o[p.type] = p.value;
  return o;
}
function toDate(iso) { return iso instanceof Date ? iso : new Date(iso); }
function fmtDate(iso) {
  if (!iso) return '—';
  const p = parts(toDate(iso), { year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short' });
  return `${p.year}年${+p.month}月${+p.day}日(${p.weekday})`;
}
function fmtDateShort(iso) {
  if (!iso) return '—';
  const p = parts(toDate(iso), { year: 'numeric', month: '2-digit', day: '2-digit' });
  return `${p.year}/${p.month}/${p.day}`;
}
function fmtTime(iso, digits = 0) {
  if (!iso) return '—';
  const d = toDate(iso);
  const p = parts(d, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  let s = `${p.hour}:${p.minute}:${p.second}`;
  if (digits > 0) {
    const ms = d.getUTCMilliseconds();
    s += '.' + String(Math.floor(ms / Math.pow(10, 3 - digits))).padStart(digits, '0');
  }
  return s;
}
function fmtHM(iso) {
  const p = parts(toDate(iso), { hour: '2-digit', minute: '2-digit' });
  return `${p.hour}:${p.minute}`;
}
function fmtDT(iso, digits = 0) { return iso ? `${fmtDateShort(iso)} ${fmtTime(iso, digits)}` : '—'; }
function tzLabel(iso) {
  if (!tzId()) return '';
  if (tzId() === 'UTC') return 'UTC';
  const p = parts(toDate(iso || Date.now()), { timeZoneName: 'short', hour: '2-digit' });
  return p.timeZoneName || '';
}
function dayDiff(isoA, isoB) {
  const a = parts(toDate(isoA), { year: 'numeric', month: '2-digit', day: '2-digit' });
  const b = parts(toDate(isoB), { year: 'numeric', month: '2-digit', day: '2-digit' });
  return `${a.year}${a.month}${a.day}` !== `${b.year}${b.month}${b.day}`;
}
function fmtDur(s, precise = false) {
  if (s == null || !isFinite(s)) return '—';
  if (s < 60) return `${s.toFixed(precise ? 1 : 0)}秒`;
  if (s < 3600) {
    const m = Math.floor(s / 60), r = s - 60 * m;
    return `${m}分${precise ? r.toFixed(1).padStart(4, '0') : String(Math.round(r)).padStart(2, '0')}秒`;
  }
  const h = Math.floor(s / 3600), m = Math.round((s - 3600 * h) / 60);
  return m === 60 ? `${h + 1}時間00分` : `${h}時間${String(m).padStart(2, '0')}分`;
}
const f1 = (v) => (v == null ? '—' : Number(v).toFixed(1));
const f3 = (v) => (v == null ? '—' : Number(v).toFixed(3));
const f4 = (v) => (v == null ? '—' : Number(v).toFixed(4));
const pct = (v, d = 1) => (v == null ? '—' : (100 * v).toFixed(d) + '%');
function fmtLat(v) { return v == null ? '—' : `${v >= 0 ? '北緯' : '南緯'} ${Math.abs(v).toFixed(2)}°`; }
function fmtLon(v) { return v == null ? '—' : `${v >= 0 ? '東経' : '西経'} ${Math.abs(v).toFixed(2)}°`; }
function fmtLatLon(la, lo) { return `${fmtLat(la)}・${fmtLon(lo)}`; }
function azName(a) {
  const n = ['北', '北北東', '北東', '東北東', '東', '東南東', '南東', '南南東', '南', '南南西', '南西', '西南西', '西', '西北西', '北西', '北北西'];
  return n[Math.round(((a % 360) + 360) % 360 / 22.5) % 16];
}

/* ------------------------------------------------------------------ */
/* API                                                                 */
/* ------------------------------------------------------------------ */
async function api(path, body) {
  const opt = body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {};
  const res = await fetch(path, opt);
  let data = null;
  try { data = await res.json(); } catch (_) { /* ignore */ }
  if (!res.ok) {
    const d = data && data.detail;
    throw new Error(typeof d === 'string' ? d : d ? d.map((x) => x.msg).join('\n') : `HTTP ${res.status}`);
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
  total: { label: '皆既日食', cls: 'total', color: '#262b38' },
  annular: { label: '金環日食', cls: 'annular', color: '#e8850c' },
  hybrid: { label: '金環皆既日食', cls: 'hybrid', color: '#8a3fd1' },
  partial: { label: '部分日食', cls: 'partial', color: '#8f9bb3' },
  mercury: { label: '水星の太陽面通過', cls: 'mercury', color: '#2b8fbf' },
  venus: { label: '金星の太陽面通過', cls: 'venus', color: '#c9a227' },
};
function catOf(e) { return e.body === 'moon' ? e.type : e.body; }
function badge(e) {
  const c = CAT[catOf(e)];
  let label = c.label;
  if (e.body !== 'moon' && e.type === 'transit_grazing') label += '(外接のみ)';
  if (e.kind === 'global' && e.noncentral) label += '(非中心)';
  return el('span', { class: 'badge ' + c.cls }, label);
}
function contactName(label, e) {
  const moon = e.body === 'moon';
  const tot = e.type === 'total' ? '皆既' : e.type === 'annular' ? '金環' : '中心食';
  const map = moon ? {
    C1: ['第1接触', '欠け始め'], C2: ['第2接触', `${tot}の始まり`], MAX: ['食の最大', ''],
    C3: ['第3接触', `${tot}の終わり`], C4: ['第4接触', '欠け終わり'],
  } : {
    C1: ['第1接触', '外接・入り始め'], C2: ['第2接触', '内接・入り終わり'], MAX: ['最大', '太陽中心に最も近づく'],
    C3: ['第3接触', '内接・出始め'], C4: ['第4接触', '外接・出終わり'],
  };
  return map[label] || [label, ''];
}
function bodyJa(b) { return { moon: '月', mercury: '水星', venus: '金星' }[b]; }

/* ------------------------------------------------------------------ */
/* initialisation                                                      */
/* ------------------------------------------------------------------ */
function isoDate(d) { return d.toISOString().slice(0, 10); }
function addYears(d, y) { const r = new Date(d); r.setUTCFullYear(r.getUTCFullYear() + y); return r; }

async function init() {
  const tz = $('#tz');
  for (const [v, l] of TZ_OPTIONS) tz.append(el('option', { value: v }, l));
  try { localStorage && localStorage.getItem('tz') && (tz.value = localStorage.getItem('tz')); } catch (_) { /* ignore */ }
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
    setStatus($('#status'), 'サーバーに接続できません: ' + err.message, 'err');
    return;
  }
  const info = state.info;
  if (info.app_mode) {
    $('#quitBtn').hidden = false;
    $('#quitBtn').addEventListener('click', quitApp);
  }
  if (info.quit_when_closed) watchPage();
  const cp = $('#cityPreset');
  info.cities.forEach((c, i) => cp.append(el('option', { value: i }, c.name)));
  cp.addEventListener('change', () => {
    const c = info.cities[cp.value];
    if (!c) return;
    $('#lat').value = c.lat; $('#lon').value = c.lon; $('#elev').value = c.elevation_m; $('#placeName').value = c.name;
    updatePickMarker();
  });
  const sp = $('#satPreset');
  info.satellites.forEach((s, i) => sp.append(el('option', { value: i }, s.label)));
  sp.addEventListener('change', () => { const s = info.satellites[sp.value]; if (s) applySatSpec(s.spec); });
  const eph = $('#ephem');
  fillEphemerides(info.ephemerides);
  eph.value = info.default_ephemeris;
  eph.addEventListener('change', updateCoverage);
  updateCoverage();
  renderEphemerisDownloads(info.downloadable_ephemerides || []);

  $$('#obsTabs button').forEach((b) => b.addEventListener('click', () => setObsTab(b.dataset.obs)));
  $$('#satModes button').forEach((b) => b.addEventListener('click', () => setSatMode(b.dataset.mode)));
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
  $('#sunR').addEventListener('change', () => { $('#sunRCustomWrap').hidden = $('#sunR').value !== 'custom'; });
  $('#moonR').addEventListener('change', () => { $('#moonRCustomWrap').hidden = $('#moonR').value !== 'custom'; });
  $('#form').addEventListener('submit', (ev) => { ev.preventDefault(); runSearch(); });
  $('#helpBtn').addEventListener('click', () => $('#helpDlg').showModal());
  $('#csvBtn').addEventListener('click', downloadListCsv);
  $('#saveBtn').addEventListener('click', saveResult);
  $$('[data-open]').forEach((b) => b.addEventListener('click', () => $('#openFile').click()));
  $('#openFile').addEventListener('change', (ev) => {
    const f = ev.target.files[0];
    ev.target.value = '';
    if (f) openSavedFile(f);
  });
  $('#closeDetail').addEventListener('click', closeDetail);
  $('#backBtn').addEventListener('click', goBack);
  $$('#detailTabs button').forEach((b) => b.addEventListener('click', () => showTab(b.dataset.tab)));
  $$('[data-example]').forEach((b) => b.addEventListener('click', () => runExample(b.dataset.example)));
  initViewer();
  $('#dlJson').addEventListener('click', downloadJson);
  $('#dlCsv').addEventListener('click', downloadSeriesCsv);
  $('#copyText').addEventListener('click', copyText);
}

// The calculator quits by itself when its last page is closed: each page keeps this event stream
// open, and the browser drops it when the tab or the browser is closed (see server.py)
function watchPage() {
  state.pageStream = new EventSource('/api/page/stream');
  state.pageStream.onerror = () => {
    if (state.stopped) return;
    fetch('/api/info').catch(() => showStopped('日食計算機は終了しています'));
  };
}
function showStopped(title) {
  state.stopped = true;
  if (state.pageStream) state.pageStream.close();
  const again = state.info && state.info.app_mode ? '「日食計算機」アプリを開いてください' : 'start.bat（Mac・Linux は start.command）を起動してください';
  document.body.innerHTML = '';
  document.body.append(el('div', { class: 'quit-msg' },
    el('h2', {}, title),
    el('p', {}, `このタブは閉じてかまいません。もう一度使うときは${again}。`)));
}
async function quitApp() {
  if (!confirm('日食計算機を終了しますか？')) return;
  state.stopped = true;
  try { await api('/api/shutdown', {}); } catch (_) { /* already stopped */ }
  showStopped('日食計算機を終了しました');
}

function fillEphemerides(names) {
  const eph = $('#ephem');
  const cur = eph.value;
  eph.innerHTML = '';
  names.forEach((n) => eph.append(el('option', { value: n }, n + (n === 'de440s.bsp' ? '（1849〜2150年）' : n === 'de440.bsp' ? '（1550〜2650年）' : ''))));
  if (names.includes(cur)) eph.value = cur;
}
function renderEphemerisDownloads(list) {
  const box = $('#ephemDlButtons');
  box.innerHTML = '';
  $('#ephemDl').hidden = !list.length;
  list.forEach((d) => box.append(el('button', {
    type: 'button', class: 'secondary small', onclick: (ev) => downloadEphemeris(d.name, ev.currentTarget),
  }, `${d.name} を追加（${d.description}）`)));
}
async function downloadEphemeris(name, btn) {
  const st = $('#ephemDlStatus');
  btn.disabled = true;
  setStatus(st, `JPL から ${name} をダウンロード中…（数分かかることがあります）`, 'busy');
  try {
    const d = await api('/api/ephemeris/download?name=' + encodeURIComponent(name), {});
    fillEphemerides(d.ephemerides);
    $('#ephem').value = name;
    updateCoverage();
    renderEphemerisDownloads(d.downloadable_ephemerides);
    $('#ephemDl').hidden = false;
    setStatus(st, `${name} を追加し、暦として選択しました。`);
  } catch (err) {
    btn.disabled = false;
    setStatus(st, err.message, 'err');
  }
}

async function updateCoverage() {
  try {
    const c = await api('/api/ephemeris_coverage?name=' + encodeURIComponent($('#ephem').value));
    $('#coverageHint').textContent = `選択中の暦で計算できる期間: ${c.start} 〜 ${c.end}`;
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
  box.innerHTML = '<span class="spinner"></span>NASA SSCWeb の衛星一覧を取得中…';
  try {
    const d = await api('/api/sscweb/satellites');
    state.sscSats = d.satellites;
    const dl = $('#sscList');
    dl.replaceChildren(...d.satellites.map((s) => el('option', { value: s.id, label: `${s.name}（${s.start.slice(0, 10)}〜${s.end.slice(0, 10)}）` })));
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
  if (!s) { box.innerHTML = `<span style="color:var(--err)">「${esc(id)}」は SSCWeb の衛星一覧にありません</span>`; return; }
  if (setName) $('#satName').value = s.name;
  box.innerHTML = `<b>${esc(s.name)}</b><br>軌道データ: ${s.start.slice(0, 10)} 〜 ${s.end.slice(0, 10)}（${s.resolution_s} 秒間隔）`;
}
function applySatSpec(spec) {
  $('#satName').value = spec.name || '';
  state.fetchedTle = null;
  $('#satInfo').hidden = true;
  if (spec.type === 'celestrak') { setSatMode('celestrak'); $('#norad').value = spec.norad; }
  else if (spec.type === 'geo') { setSatMode('geo'); $('#geoLon').value = spec.lon; }
  else if (spec.type === 'horizons') { setSatMode('horizons'); $('#hzId').value = spec.command; $('#hzStep').value = spec.step_min; }
  else if (spec.type === 'sscweb') { $('#sscId').value = spec.id; setSatMode('sscweb'); }
  else if (spec.type === 'tle') {
    setSatMode('tle');
    $('#tleText').value = [spec.name, spec.line1, spec.line2].filter(Boolean).join('\n');
  } else if (spec.type === 'kepler') {
    setSatMode('kepler');
    if (spec.epoch) $('#kEpoch').value = spec.epoch.replace(' ', 'T').replace('Z', '').slice(0, 19);
    $('#kJ2').checked = spec.j2 !== false;
    let peri = spec.perigee_alt_km, apo = spec.apogee_alt_km;
    if (spec.a_km) {
      const e = spec.e ?? 0;
      peri = spec.a_km * (1 - e) - R_EARTH; apo = spec.a_km * (1 + e) - R_EARTH;
      $('#kA').value = spec.a_km; $('#kE').value = e;
    }
    $('#kPeri').value = peri.toFixed(1); $('#kApo').value = apo.toFixed(1);
    $('#kSso').checked = !!spec.sso;
    if (spec.i_deg != null) $('#kInc').value = spec.i_deg;
    if (spec.ltan_h != null) { $('#kPlane').value = 'ltan'; $('#kLtan').value = fmtLtan(spec.ltan_h); }
    else { $('#kPlane').value = 'raan'; $('#kRaan').value = spec.raan_deg ?? 0; }
    $('#kArgp').value = spec.argp_deg ?? 0; $('#kM').value = spec.m_deg ?? 0;
    setKeplerInput(spec.a_km ? 'elements' : 'plan', false);
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
  if (!isFinite(h)) throw new Error('昇交点の地方時を入力してください（例: 18:00）');
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
    if (i == null) { note.textContent = 'この高度では太陽同期軌道になりません'; }
    else { $('#kInc').value = i.toFixed(3); note.textContent = '太陽同期になるよう自動で決めた値'; }
  } else note.textContent = '';
  const ltan = !elements && $('#kPlane').value === 'ltan';
  $('#kLtanWrap').hidden = !ltan;
  $('#kRaanWrap').hidden = ltan;
  const sweep = $('#kSweep').checked;
  $('#kM').disabled = sweep;
  $('#kSweepWrap').hidden = !sweep;
  const a = num('#kA', NaN), e = num('#kE', NaN);
  $('#kANote').textContent = a > 0 && e >= 0 && e < 1
    ? `高度 ${(a * (1 - e) - R_EARTH).toFixed(0)}〜${(a * (1 + e) - R_EARTH).toFixed(0)} km・周期 ${(2 * Math.PI * Math.sqrt(a ** 3 / 398600.4418) / 60).toFixed(1)} 分`
    : '';
}
async function fetchTle() {
  const box = $('#satInfo');
  box.hidden = false;
  box.innerHTML = '<span class="spinner"></span>CelesTrak から取得中…';
  try {
    const d = await api('/api/tle?norad=' + encodeURIComponent($('#norad').value));
    state.fetchedTle = { norad: +$('#norad').value, line1: d.line1, line2: d.line2, name: d.name };
    if (!$('#satName').value || $('#satPreset').value === '') $('#satName').value = d.name;
    box.innerHTML = `<b>${esc(d.name)}</b><br>元期: ${fmtDT(d.epoch)} ${tzLabel(d.epoch)}<br>周期 ${d.period_min.toFixed(1)} 分・近地点 ${d.perigee_km.toFixed(0)} km・遠地点 ${d.apogee_km.toFixed(0)} km` +
      `<br><small>TLE の予報精度は元期の前後数日が目安です。</small>`;
    $('#tleText').value = `${d.name}\n${d.line1}\n${d.line2}`;
  } catch (err) {
    box.innerHTML = `<span style="color:var(--err)">${esc(err.message)}</span>`;
  }
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
    baseLayers(m, true);
    state.pickMarker = L.marker([+$('#lat').value, +$('#lon').value]).addTo(m);
    m.on('click', (ev) => {
      const ll = ev.latlng.wrap();
      $('#lat').value = ll.lat.toFixed(4); $('#lon').value = ll.lng.toFixed(4);
      $('#placeName').value = '地図で選んだ地点'; $('#cityPreset').value = '';
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
      if (i1 < 0 || !lines[i1 + 1] || !lines[i1 + 1].startsWith('2 ')) throw new Error('TLE の 1 行目（"1 "で始まる）と 2 行目（"2 "で始まる）を貼り付けてください');
      observer = { type: 'tle', line1: lines[i1], line2: lines[i1 + 1], name: name || (i1 > 0 ? lines[i1 - 1] : '') };
    } else if (m === 'kepler') {
      const ep = $('#kEpoch').value;
      if (!ep) throw new Error('軌道要素の元期を入力してください');
      observer = {
        type: 'kepler', epoch: ep.length === 16 ? ep + ':00' : ep,
        argp_deg: num('#kArgp'), m_deg: num('#kM'), j2: $('#kJ2').checked, name,
      };
      if (state.kInput === 'elements') {
        const a = num('#kA', NaN), e = num('#kE', NaN);
        if (!(a > 0)) throw new Error('軌道長半径を入力してください');
        if (!(e >= 0 && e < 1)) throw new Error('軌道離心率は 0 以上 1 未満で入力してください');
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
      if (!id) throw new Error('NASA SSCWeb の衛星 ID を入力してください（例: hinode）');
      observer = { type: 'sscweb', id, name };
    } else {
      observer = { type: 'horizons', command: $('#hzId').value.trim(), step_min: num('#hzStep', 5), name };
    }
  }
  const rp = state.info.radius_presets;
  const sunSel = $('#sunR').value;
  const moonSel = $('#moonR').value;
  const plSel = $('#planetR').value;
  const settings = {
    ephemeris: $('#ephem').value,
    delta_t: $('#dtMode').value === 'manual' && $('#dtValue').value !== '' ? num('#dtValue') : null,
    sun_radius_km: sunSel === 'custom' ? num('#sunRCustom') : rp.sun[sunSel],
    moon_radius_ext_km: moonSel === 'custom' ? num('#moonRExt') : rp.moon[moonSel][0],
    moon_radius_int_km: moonSel === 'custom' ? num('#moonRInt') : rp.moon[moonSel][1],
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
  if (!req.phenomena.length) { setStatus(st, '計算する現象を 1 つ以上選んでください', 'err'); return; }
  const btn = $('#runBtn');
  btn.disabled = true;
  const msg = req.observer.type === 'horizons' ? 'JPL Horizons から軌道を取得して計算中…' :
    req.observer.type === 'sscweb' ? 'NASA SSCWeb から軌道を取得して計算中…' :
    req.observer.type === 'celestrak' ? 'CelesTrak から TLE を取得して計算中…' : '計算中…';
  setStatus(st, req.sweepStep ? `平均近点角を ${req.sweepStep}° ずつ変えて計算中…` : msg, 'busy');
  try {
    if (req.sweepStep) {
      const { sweepStep, ...body } = req;
      const r = await api('/api/phase_sweep', { ...body, step_deg: sweepStep });
      r.request = savedRequest(req, r);
      state.sweep = r;
      state.sweepGroup = 0;
      state.result = null;
      state.saved = null;
      setStatus(st, `完了（${r.elapsed_s.toFixed(1)} 秒）`);
      $('#welcome').hidden = true;
      $('#resultArea').hidden = false;
      closeDetail();
      renderSweep();
      return;
    }
    const r = await api('/api/search', req);
    r.request = savedRequest(req, r);
    state.result = r;
    state.sweep = null;
    state.saved = null;
    state.filters = new Set();
    setStatus(st, `完了（${r.elapsed_s.toFixed(1)} 秒）`);
    $('#welcome').hidden = true;
    $('#resultArea').hidden = false;
    closeDetail();
    renderResults();
    if (r.events.length === 1) openDetail(r.events[0].id, { root: true });
  } catch (err) {
    setStatus(st, err.message, 'err');
  } finally {
    btn.disabled = false;
  }
}

function runExample(name) {
  const now = new Date();
  $$('input[name=ph]').forEach((i) => { i.checked = true; });
  if (name === 'tokyo') {
    setObsTab('ground');
    const c = state.info.cities.find((x) => x.name === '東京');
    $('#lat').value = c.lat; $('#lon').value = c.lon; $('#elev').value = c.elevation_m; $('#placeName').value = c.name;
    $$('input[name=ph]').forEach((i) => { i.checked = i.value === 'moon'; });
    $('#start').value = isoDate(now); $('#end').value = isoDate(addYears(now, 30));
  } else if (name === 'global') {
    setObsTab('global');
    $('#start').value = '2001-01-01'; $('#end').value = '2101-01-01';
  } else if (name === 'iss') {
    setObsTab('space'); applySatSpec(state.info.satellites[0].spec);
    $$('input[name=ph]').forEach((i) => { i.checked = i.value === 'moon'; });
    $('#start').value = isoDate(now); $('#end').value = isoDate(addYears(now, 1));
  } else if (name === 'geo') {
    setObsTab('space'); applySatSpec(state.info.satellites[3].spec);
    $('#start').value = isoDate(now); $('#end').value = isoDate(addYears(now, 10));
  } else if (name === 'prelaunch') {
    setObsTab('space');
    applySatSpec({ type: 'kepler', name: '太陽同期 680km（計画）', perigee_alt_km: 680, apogee_alt_km: 680, sso: true, ltan_h: 18, argp_deg: 0, m_deg: 0 });
    $('#kEpoch').value = '2027-01-01T00:00:00';
    $('#kSweep').checked = true; $('#kSweepStep').value = '10'; updateKeplerForm();
    $$('input[name=ph]').forEach((i) => { i.checked = i.value === 'moon'; });
    $('#start').value = '2027-01-01'; $('#end').value = '2028-01-01';
  } else if (name === 'hinode') {
    setObsTab('space'); applySatSpec(state.info.satellites.find((s) => s.spec.type === 'sscweb').spec);
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
  if (o.kind === 'global') return '地球全体';
  if (o.kind === 'ground') return `${o.name}（${fmtLatLon(o.lat, o.lon)}・標高 ${Math.round(o.elevation_m)} m）`;
  if (o.kind === 'geocenter') return '地球中心';
  const model = { tle: 'TLE/SGP4', kepler: '軌道要素', fixed: '地球固定位置', horizons: 'JPL Horizons', sscweb: 'NASA SSCWeb' }[o.model] || '';
  let s = `${o.name}（${model}`;
  if (o.perigee_km != null) s += `・高度 ${o.perigee_km.toFixed(0)}〜${o.apogee_km.toFixed(0)} km`;
  if (o.model === 'fixed') s += `・${fmtLon(o.lon)}・高度 ${o.height_km.toFixed(0)} km`;
  if (o.model === 'kepler') {
    s += `・傾斜角 ${o.i_deg.toFixed(2)}°`;
    if (o.raan_deg != null && !o.sso) s += `・昇交点赤経 ${o.raan_deg.toFixed(2)}°`;
    if (o.ltan_h != null) s += `・昇交点の地方時 ${fmtLtan(o.ltan_h)}`;
    if (o.sso) s += '・太陽同期';
  }
  if (o.epoch) s += `・元期 ${fmtDT(o.epoch)}`;
  return s + '）';
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
    `<div><span class="big">${evs.length} 件</span> の現象が見つかりました` +
    (!global && r.params.include_invisible ? `（うち見えるもの ${visCount} 件）` : '') + '</div>' +
    savedNote() +
    `<div class="muted">観測者: ${esc(observerText(r.observer))}<br>期間: ${esc(r.start)} 〜 ${esc(r.end)}（UTC）・暦 ${esc(r.ephemeris)}・ΔT ${r.delta_t_override != null ? r.delta_t_override + ' 秒（手動）' : '約 ' + r.delta_t_mid_s.toFixed(1) + ' 秒（期間中央）'}・計算 ${r.elapsed_s.toFixed(1)} 秒</div>`;
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
    ? ['日付', '種類', '最大食の時刻', '食分', 'γ', '中心食の継続', '中心食帯の幅', '最大食の地点', 'サロス']
    : ['日付', '種類', '最大の時刻', '規模', '太陽が隠れる割合', '継続時間', r.observer.kind === 'ground' ? '最大時の太陽' : r.observer.kind === 'space' ? '最大時の衛星位置' : '—', '見えるか', 'サロス'];
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
  if (!tb.children.length) tb.append(el('tr', {}, el('td', { colspan: head.length, style: 'text-align:center;color:var(--muted);padding:20px' }, '該当する現象はありません。期間や観測者を変えてお試しください。')));
}

/* ------------------------------------------------------------------ */
/* phase sweep (satellite position along the orbit unknown)            */
/* ------------------------------------------------------------------ */
const BODY_JA = { moon: '日食', mercury: '水星の太陽面通過', venus: '金星の太陽面通過' };
function renderSweep() {
  const r = state.sweep;
  $('#listCard').hidden = true;
  $('#sweepArea').hidden = false;
  const n = r.phases.length;
  const counts = {};
  r.groups.forEach((g) => { counts[g.body] = (counts[g.body] || 0) + 1; });
  const what = Object.entries(counts).map(([b, c]) => `${BODY_JA[b]} ${c} 件`).join('・') || '現象なし';
  $('#summaryText').innerHTML =
    `<div><span class="big">${what}</span>（平均近点角を ${r.step_deg}° ずつ変えた ${n} 通りで計算）</div>` +
    savedNote() +
    `<div class="muted">観測者: ${esc(observerText(r.observer))}<br>期間: ${esc(r.start)} 〜 ${esc(r.end)}（UTC）・暦 ${esc(r.ephemeris)}・計算 ${r.elapsed_s.toFixed(1)} 秒<br>` +
    '衛星が軌道上のどこにいるかで結果が変わります。行をクリックすると、平均近点角ごとの結果が表示されます。</div>';
  $('#warnings').innerHTML = (r.warnings || []).map((w) => `<div>⚠ ${esc(w)}</div>`).join('');
  $('#filters').innerHTML = '';
  const tbl = $('#sweepTable');
  tbl.innerHTML = '';
  const head = ['日付', '現象', '見える位相', '見える回数', '最も深い食の食分', '皆既・金環になる位相', '最大の時刻の範囲'];
  tbl.append(el('thead', {}, el('tr', {}, head.map((h) => el('th', {}, h)))));
  const tb = el('tbody');
  r.groups.forEach((g, k) => {
    const tr = el('tr', { class: k === state.sweepGroup ? 'selected' : '' });
    tr.addEventListener('click', () => { state.sweepGroup = k; renderSweep(); });
    const isT = g.body !== 'moon';
    const central = g.central_phases.length;
    tr.append(
      el('td', {}, fmtDate(g.date)),
      el('td', {}, BODY_JA[g.body]),
      el('td', { class: 'num' }, `${g.n_visible} / ${g.n_phases}`),
      el('td', { class: 'num' }, g.count_min === g.count_max ? `${g.count_max} 回` : `${g.count_min}〜${g.count_max} 回`),
      el('td', { class: 'num' }, isT || g.mag_min == null ? '—' : `${f3(g.mag_min)} 〜 ${f3(g.mag_max)}`),
      el('td', { class: 'num' }, isT ? '—' : central ? `${central} / ${g.n_phases}（${Math.round(100 * central / g.n_phases)}%）` : 'なし'),
      el('td', { class: 'num' }, g.time_first ? sweepRange(g.time_first, g.time_last) : '—'),
    );
    if (!g.n_visible) tr.classList.add('dim');
    tb.append(tr);
  });
  if (!r.groups.length) tb.append(el('tr', {}, el('td', { colspan: head.length, style: 'text-align:center;color:var(--muted);padding:20px' }, 'この期間には、どの位相でも見られる現象がありません。')));
  tbl.append(tb);
  renderSweepGroup();
}
function renderSweepGroup() {
  const r = state.sweep;
  const g = r.groups[state.sweepGroup];
  $('#sweepDetail').hidden = !g;
  if (!g) return;
  const isT = g.body !== 'moon';
  $('#sweepTitle').textContent = `${fmtDate(g.date)} の${BODY_JA[g.body]}：平均近点角ごとの結果`;
  $('#sweepSub').textContent = isT
    ? '各位相で最も長く見える通過を表示しています。行をクリックすると詳細が開きます。'
    : '各位相で最も深い食を表示しています（1 周回ごとに複数回起きることがあります）。行をクリックすると詳細が開きます。';
  $('#sweepChart').replaceChildren(sweepChart(g));
  const tbl = $('#sweepPhaseTable');
  tbl.innerHTML = '';
  const multiDay = g.time_first && dayDiff(g.time_first, g.time_last);
  const head = ['平均近点角', '見える回数', '種類', '最大の時刻', isT ? '中心間距離' : '食分', '太陽が隠れる割合', isT ? '継続時間' : '中心食の継続', '見えるか'];
  tbl.append(el('thead', {}, el('tr', {}, head.map((h) => el('th', {}, h)))));
  const tb = el('tbody');
  for (const row of g.rows) {
    const e = row.best;
    const tr = el('tr', {});
    if (!e) {
      tr.append(el('td', { class: 'num' }, `${row.m_deg}°`), el('td', { class: 'num' }, '0 回'),
        el('td', { colspan: head.length - 2, class: 'muted' }, 'この位相では見られません'));
      tr.classList.add('dim');
    } else {
      tr.addEventListener('click', () => openDetail(e.id, { root: true }));
      tr.append(
        el('td', { class: 'num' }, `${row.m_deg}°`),
        el('td', { class: 'num' }, `${row.count} 回`),
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
  if (!dayDiff(a, b)) return `${fmtHM(a)} 〜 ${fmtHM(b)} ${tzLabel(a)}`;
  return `${fmtDateShort(a).slice(5)} ${fmtHM(a)} 〜 ${fmtDateShort(b).slice(5)} ${fmtHM(b)} ${tzLabel(a)}`;
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
  svg.setAttribute('aria-label', isT ? '平均近点角ごとの見える割合' : '平均近点角ごとの最大食分');
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
      title.textContent = `平均近点角 ${r.m_deg}°：${c.label}・${isT ? '見える割合 ' + pct(v) : '食分 ' + f3(v)}`;
      rect.append(title);
    }
    if (k % Math.max(1, Math.round(g.rows.length / 12)) === 0) add('text', { x: x + bw / 2, y: H - B + 16, 'text-anchor': 'middle', class: 'tick' }, `${r.m_deg}°`);
  });
  add('text', { x: L + (W - L - R) / 2, y: H - 4, 'text-anchor': 'middle', class: 'axis' }, '元期での平均近点角（衛星が軌道上のどこにいるか）');
  if (!isT) add('text', { x: W - R, y: y(1) - 5, 'text-anchor': 'end', class: 'tick' }, '食分 1.0');
  return svg;
}
function downloadSweepCsv() {
  const r = state.sweep;
  const rows = [['date_utc', 'phenomenon', 'mean_anomaly_deg', 'visible_count', 'type', 'max_utc', 'magnitude', 'obscuration', 'min_sep_arcsec', 'central_duration_s', 'duration_s', 'visible_fraction']];
  for (const g of r.groups) {
    for (const row of g.rows) {
      const e = row.best || {};
      rows.push([g.date.slice(0, 10), BODY_JA[g.body], row.m_deg, row.count, e.type ? CAT[catOf(e)].label : '', e.max ?? '', e.magnitude ?? '',
        e.obscuration ?? '', e.min_sep_arcsec ?? '', e.central_duration_s ?? '', e.duration_s ?? '', e.vis_fraction ?? '']);
    }
  }
  saveFile('phase_sweep.csv', toCsv(rows), 'text/csv');
}

function globalRow(e, isT) {
  if (isT) {
    return [el('td', {}, fmtDate(e.max)), el('td', {}, badge(e)), el('td', { class: 'num' }, `${fmtTime(e.max)} ${tzLabel(e.max)}`),
      el('td', { class: 'num' }, `中心間 ${f1(e.min_sep_arcsec)}″`), el('td', {}, '—'), el('td', { class: 'num' }, fmtDur(e.duration_s)),
      el('td', {}, '—'), el('td', { class: 'muted' }, '地球中心から見た値'), el('td', {}, '—')];
  }
  return [
    el('td', {}, fmtDate(e.max)), el('td', {}, badge(e)), el('td', { class: 'num' }, `${fmtTime(e.max)} ${tzLabel(e.max)}`),
    el('td', { class: 'num' }, f4(e.magnitude)), el('td', { class: 'num' }, (e.gamma >= 0 ? '+' : '') + f4(e.gamma)),
    el('td', { class: 'num' }, e.type === 'partial' ? '—' : fmtDur(e.central_duration_s, true)),
    el('td', { class: 'num' }, e.path_width_km ? `${e.path_width_km.toFixed(0)} km` : '—'),
    el('td', {}, fmtLatLon(e.ge_lat, e.ge_lon)), el('td', { class: 'num' }, e.saros ?? '—'),
  ];
}
function visCell(e, kind) {
  if (kind === 'geocenter') return el('td', { class: 'vis yes' }, '（地心）');
  const f = e.vis_fraction || 0;
  let txt, cls;
  if (f >= 0.999) { txt = '◎ 全経過'; cls = 'yes'; }
  else if (f > 0) {
    cls = 'part';
    if (kind === 'ground') {
      const startVis = e.visible_intervals[0][0] === e.c1;
      txt = `○ 一部 ${Math.round(f * 100)}%` + (startVis ? '（日の入り帯食）' : '（日の出帯食）');
      if (e.visible_intervals.length > 1) txt = `○ 一部 ${Math.round(f * 100)}%`;
    } else txt = `○ 一部 ${Math.round(f * 100)}%（地球に隠される）`;
  } else { txt = kind === 'ground' ? '× 地平線の下' : '× 地球に隠される'; cls = 'no'; }
  return el('td', { class: 'vis ' + cls }, txt);
}
function localRow(e, isT, kind) {
  const scale = isT ? `中心間 ${f1(e.min_sep_arcsec)}″` : `食分 ${f3(e.magnitude)}`;
  const obs = isT ? `${pct(e.obscuration, 3)}` : el('span', {}, el('span', { class: 'bar' }, el('i', { style: `width:${Math.min(100, e.obscuration * 100)}%` })), pct(e.obscuration));
  let dur = fmtDur(e.duration_s);
  if (!isT && e.central_duration_s > 0) dur = `${e.type === 'total' ? '皆既' : '金環'} ${fmtDur(e.central_duration_s, true)}／全体 ${fmtDur(e.duration_s)}`;
  let pos = '—';
  if (kind === 'ground' && e.sun_alt_max != null) pos = `高度 ${e.sun_alt_max.toFixed(0)}°・${azName(e.sun_az_max)}`;
  if (kind === 'space' && e.sat_lat_max != null) pos = `${fmtLatLon(e.sat_lat_max, e.sat_lon_max)}・${e.sat_alt_km_max.toFixed(0)} km`;
  return [
    el('td', {}, fmtDate(e.max)), el('td', {}, badge(e)), el('td', { class: 'num' }, `${fmtTime(e.max)} ${tzLabel(e.max)}`),
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
/* An event without the server's id (which means nothing once the server is restarted). */
function noId({ id, ...e }) { return e; }
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
    setStatus(st, `「${file.name}」は JSON ファイルとして読めません`, 'err');
    return;
  }
  try {
    if (data && data.event && data.event.max) openSavedEvent(data, file.name);
    else if (data && (Array.isArray(data.events) || Array.isArray(data.groups))) openSavedResult(data, file.name);
    else if (data && data.dry_run) throw new Error('このファイルは解釈の確認（--dry-run）の出力で、計算結果を含みません');
    else throw new Error(`「${file.name}」は日食計算機で保存した結果ではありません（「結果を保存」で保存した JSON、詳細の「JSON をダウンロード」、cli.py の --format json の出力を開けます）`);
  } catch (err) {
    setStatus(st, err.message, 'err');
  }
}
function openSavedResult(data, name) {
  const events = new Map();
  const tag = (e) => { e.id = `saved${events.size + 1}`; events.set(e.id, e); };
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
  const { tool, version, saved_at: savedAt, ok, request, ...r } = data;
  r.request = request;
  if (r.elapsed_s == null) r.elapsed_s = 0;
  state.saved = { request, events, name, savedAt, tool };
  if (request) {
    try { applyRequestToForm(request); } catch (err) { console.warn(err); }
  }
  closeDetail();
  $('#welcome').hidden = true;
  $('#resultArea').hidden = false;
  if (data.groups) {
    state.sweep = r; state.sweepGroup = 0; state.result = null;
    renderSweep();
  } else {
    state.result = r; state.sweep = null; state.filters = new Set();
    renderResults();
  }
  setStatus($('#status'), `「${name}」を開きました`);
}
/* One event saved from the detail view ("JSON をダウンロード"): shown as saved; the map and
   other places are computed from the request saved with it. */
function openSavedEvent(data, name) {
  const d = data.event;
  d.id = 'saved1';
  state.saved = { request: d.request || data.request, events: new Map([[d.id, d]]), name, savedAt: data.saved_at, tool: data.tool };
  if (state.saved.request) {
    try { applyRequestToForm(state.saved.request); } catch (err) { console.warn(err); }
  }
  state.result = null; state.sweep = null;
  $('#welcome').hidden = true;
  $('#resultArea').hidden = true;
  state.stack = []; state.rootId = d.id; state.selectedId = null;
  state.detail = d;
  state.local = d.kind === 'global' ? data.local || null : d;
  $('#detail').hidden = false;
  $('#backBtn').hidden = true;
  renderDetailAll(true);
  if (d.kind === 'global' && !state.local) loadGeLocal(d);
  setStatus($('#status'), `「${name}」を開きました（保存した現象）`);
}
function savedNote() {
  const s = state.saved;
  if (!s) return '';
  const when = s.savedAt ? `・${fmtDT(s.savedAt)} ${tzLabel(s.savedAt)} に保存` : '';
  const how = s.request ? '詳細は保存した計算条件で再計算します。' : 'このファイルには計算条件がないため、詳細は表示できません。';
  return `<div class="saved-note">保存した結果を表示しています（${esc(s.name)}${when}）。${how}</div>`;
}
/* The server's id of an event: an event of a saved result is computed again (once) from the
   saved request; the server forgets it when restarted, then it is computed again. */
async function liveDetail(id) {
  const s = state.saved && state.saved.events.get(id);
  if (!s) return api('/api/event/' + id);
  if (s.serverId) {
    try { return await api('/api/event/' + s.serverId); } catch (_) { s.serverId = null; }
  }
  if (!state.saved.request) throw new Error('このファイルには計算条件（request）がないため、詳細を計算できません');
  const event = Object.fromEntries(['body', 'kind', 'jd_max', 'jd_c1', 'jd_c4', 'm_deg'].filter((k) => s[k] != null).map((k) => [k, s[k]]));
  const d = await api('/api/restore', { request: state.saved.request, event });
  s.serverId = d.id;
  return d;
}
async function liveId(id) {
  const s = state.saved && state.saved.events.get(id);
  if (!s) return id;
  if (!s.serverId) await liveDetail(id);
  return s.serverId;
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
    $('#kSweep').checked = o.type === 'kepler' && req.step_deg != null;
    if (req.step_deg != null && $$('#kSweepStep option').some((x) => +x.value === +req.step_deg)) $('#kSweepStep').value = String(+req.step_deg);
    updateKeplerForm();
  }
  if (req.start) $('#start').value = req.start.slice(0, 10);
  if (req.end) $('#end').value = req.end.slice(0, 10);
  const s = { ...state.info.defaults, ...(req.settings || {}) };
  const eph = s.ephemeris && !s.ephemeris.endsWith('.bsp') ? s.ephemeris + '.bsp' : s.ephemeris;
  if (eph && $$('#ephem option').some((x) => x.value === eph)) { $('#ephem').value = eph; updateCoverage(); }
  const manual = s.delta_t != null && s.delta_t !== '' && s.delta_t !== 'auto';
  $('#dtMode').value = manual ? 'manual' : 'auto';
  $('#dtValue').value = manual ? s.delta_t : '';
  $('#dtValue').disabled = !manual;
  const rp = state.info.radius_presets;
  const near = (a, b) => Math.abs(a - b) < 1e-6;
  const sun = Object.keys(rp.sun).find((k) => near(rp.sun[k], s.sun_radius_km));
  $('#sunR').value = sun || 'custom';
  if (!sun) $('#sunRCustom').value = s.sun_radius_km;
  $('#sunRCustomWrap').hidden = !!sun;
  const moon = Object.keys(rp.moon).find((k) => near(rp.moon[k][0], s.moon_radius_ext_km) && near(rp.moon[k][1], s.moon_radius_int_km));
  $('#moonR').value = moon || 'custom';
  if (!moon) { $('#moonRExt').value = s.moon_radius_ext_km; $('#moonRInt').value = s.moon_radius_int_km; }
  $('#moonRCustomWrap').hidden = !!moon;
  $('#planetR').value = Object.keys(rp.mercury).find((k) => near(rp.mercury[k], s.mercury_radius_km)) || 'iau';
  $('#minAlt').value = s.min_sun_alt_deg;
  $('#atm').value = s.earth_atm_km;
  $('#refraction').checked = !!s.refraction;
  $('#includeInvisible').checked = !!s.include_invisible;
}

/* ------------------------------------------------------------------ */
/* detail                                                              */
/* ------------------------------------------------------------------ */
async function openDetail(id, { root = false, push = false } = {}) {
  const st = $('#status');
  const saved = state.saved && state.saved.events.get(id);
  setStatus(st, saved && !saved.serverId ? '保存した計算条件で詳細を計算中…' : '詳細を計算中…', 'busy');
  try {
    const d = await liveDetail(id);
    if (push && state.detail) state.stack.push(state.detail.id);
    if (root) { state.stack = []; state.rootId = d.id; }
    state.detail = d;
    state.selectedId = root ? id : state.selectedId;
    $$('#eventTable tbody tr').forEach((tr) => tr.classList.toggle('selected', tr.dataset.id === state.selectedId));
    state.local = d.kind === 'global' ? null : d;
    $('#detail').hidden = false;
    $('#backBtn').hidden = state.stack.length === 0;
    renderDetailAll(true);
    setStatus(st, '');
    if (d.kind === 'global') loadGeLocal(d);
    $('#detail').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (err) {
    setStatus(st, err.message, 'err');
  }
}
async function loadGeLocal(d) {
  try {
    const r = await api('/api/local', { event_id: await liveId(d.id), lat: d.ge_lat, lon: d.ge_lon, elevation_m: 0, name: '最大食の地点' });
    if (state.detail !== d || !r.found) return;
    state.local = r;
    renderOverview();
    setupViewer();
    renderCharts();
  } catch (err) { console.warn(err); }
}
function closeDetail() {
  $('#detail').hidden = true;
  state.detail = null; state.local = null; state.playing = false;
  state.selectedId = null;
  $$('#eventTable tbody tr').forEach((tr) => tr.classList.remove('selected'));
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
  if (tab === 'data') renderData();
}
function renderDetailAll(reset) {
  const d = state.detail;
  if (!d) return;
  const obs = d.kind === 'global' ? null : d.observer;
  const title = d.kind === 'global' ? `${fmtDate(d.max)} の${d.type_ja || CAT[catOf(d)].label}`
    : `${fmtDate(d.max)} の${d.type_ja}`;
  $('#detailTitle').textContent = title;
  $('#detailSub').textContent = d.kind === 'global' ? '地球全体での状況' : `観測者: ${observerText(obs)}`;
  renderOverview();
  setupViewer(reset);
  if (reset) {
    state.mapData[d.id] = state.mapData[d.id] || null;
    showTab(currentTab());
  } else {
    renderCharts();
    if (currentTab() === 'map') loadMap(true);
    if (currentTab() === 'data') renderData();
  }
}

function leadText(d) {
  const L = state.local;
  const moon = d.body === 'moon';
  if (d.kind === 'global') {
    if (d.body !== 'moon') return '';
    let s = `<b>${fmtDate(d.max)}</b> の${esc(d.type_ja)}です。`;
    s += `食が最も大きくなる「最大食」は <b>${fmtTime(d.max)} ${tzLabel(d.max)}</b> に <b>${fmtLatLon(d.ge_lat, d.ge_lon)}</b> で起こり、太陽高度は ${d.sun_alt.toFixed(0)}° です。`;
    if (d.type !== 'partial') {
      s += `中心食の継続時間は最大 <b>${fmtDur(d.central_duration_s, true)}</b>`;
      if (d.path_width_km) s += `、${d.type === 'annular' ? '金環' : '皆既'}帯の幅は約 <b>${d.path_width_km.toFixed(0)} km</b>`;
      s += 'です。';
    } else s += `最大食分は ${f3(d.magnitude)} の部分日食で、皆既・金環になる場所はありません。`;
    if (d.p1 && d.p4) s += `地球上のどこかで部分食が見られるのは ${fmtDT(d.p1)} 〜 ${fmtDT(d.p4)}（${tzLabel(d.p1)}）です。`;
    return s;
  }
  const kind = d.observer_kind;
  const who = kind === 'ground' ? `${esc(d.observer.name)}では` : kind === 'space' ? `${esc(d.observer.name)}から見ると` : '地球中心から見ると';
  const c = Object.fromEntries(d.contacts.map((x) => [x.label, x]));
  const c2 = d.contacts.find((x) => x.label === 'C2');
  const c3 = [...d.contacts].reverse().find((x) => x.label === 'C3');
  let s;
  if (moon) {
    s = `${who}、<b>${fmtDate(d.max)}</b> の <b>${fmtTime(d.c1)}</b> に太陽が欠け始め、<b>${fmtTime(d.max)}</b> に最大（食分 <b>${f3(d.magnitude)}</b>、太陽の面積の <b>${pct(d.obscuration)}</b> が隠れる）となり、<b>${fmtTime(d.c4)}</b> に終わります（${tzLabel(d.max)}）。`;
    if (d.type === 'total' || d.type === 'annular') {
      s += `${fmtTime(c2.time)} から ${fmtTime(c3.time)} までの <b>${fmtDur(d.central_duration_s, true)}</b> 間は${d.type === 'total' ? '<b>皆既日食</b>（太陽が完全に隠れる）' : '<b>金環日食</b>（太陽がリング状に見える）'}です。`;
      if (d.n_internal > 1) s += `（衛星の運動により ${d.n_internal} 回に分かれます）`;
    }
  } else {
    s = `${who}、<b>${fmtDate(d.max)}</b> の <b>${fmtTime(d.c1)}</b> に${bodyJa(d.body)}が太陽の縁にかかり始め、<b>${fmtTime(d.max)}</b> に太陽の中心に最も近づき（中心間 ${f1(d.min_sep_arcsec)}″）、<b>${fmtTime(d.c4)}</b> に太陽面から離れます（${tzLabel(d.max)}）。経過時間は ${fmtDur(d.duration_s)} です。`;
    if (d.type === 'transit_grazing') s += `${bodyJa(d.body)}が太陽の縁をかすめるだけで、全体が太陽面に入ることはありません。`;
  }
  if (kind === 'ground' || kind === 'space') {
    const f = d.vis_fraction;
    if (f >= 0.999) s += kind === 'ground' ? ` 全経過で太陽は地平線の上にあり、最大時の太陽高度は <b>${c.MAX.sun_alt.toFixed(0)}°</b>（${azName(c.MAX.sun_az)}の空）です。` : ' 全経過で太陽は地球に隠されません。';
    else if (f > 0) {
      const iv = d.visible_intervals.map(([a, b]) => `${fmtTime(a)}〜${fmtTime(b)}`).join('、');
      s += ` ただし見られるのは <b>${iv}</b> の間だけです（${kind === 'ground' ? '太陽が地平線の下にある時間を除く' : '衛星から見て太陽が地球に隠される時間を除く'}）。`;
      if (d.visible_max && moon) s += `見える範囲での最大食分は ${f3(d.visible_max.magnitude)} です。`;
    } else s += kind === 'ground' ? ' <b>この現象は太陽が地平線の下にあるため見られません。</b>' : ' <b>この間、太陽は地球に隠されていて見えません。</b>';
    if (kind === 'space' && c.MAX.sat_lat != null) s += ` 最大時の衛星は ${fmtLatLon(c.MAX.sat_lat, c.MAX.sat_lon)} の上空 ${c.MAX.sat_alt_km.toFixed(0)} km にいます。`;
  }
  if (kind === 'geocenter') s += ' これは地球中心から見た標準値で、地上の各地点では視差により数分ずれます。地図タブで地点をクリックすると、その場所での時刻を計算できます。';
  return s;
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
      metric(d.type === 'partial' ? '最大食分' : '食分（視直径比）', f4(d.magnitude), d.type === 'partial' ? '太陽の直径が隠れる割合' : '月と太陽の見かけの大きさの比'),
      metric('γ（ガンマ）', (d.gamma >= 0 ? '+' : '') + f4(d.gamma), '影の軸と地球中心の最接近距離'),
      metric('中心食の継続時間', d.type === 'partial' ? '—' : fmtDur(d.central_duration_s, true), '最大食の地点で'),
      metric('中心食帯の幅', d.path_width_km ? d.path_width_km.toFixed(1) + ' km' : '—', '最大食の地点で'),
      metric('サロス番号', d.saros ?? '—', '約18年周期の系列'),
    ));
    box.append(el('div', { class: 'section-title' }, '地球全体での経過'));
    const rows = [['部分食の始まり（P1）', d.p1], [d.type !== 'partial' ? '中心食の始まり' : null, d.c_begin], ['最大食', d.max],
      [d.type !== 'partial' ? '中心食の終わり' : null, d.c_end], ['部分食の終わり（P4）', d.p4]].filter((r) => r[0] && r[1]);
    box.append(el('div', { class: 'table-scroll' }, el('table', { class: 'contacts' },
      el('thead', {}, el('tr', {}, el('th', {}, '段階'), el('th', {}, `日時（${tzLabel(d.max)}）`), el('th', {}, 'UTC'))),
      el('tbody', {}, rows.map(([k, t]) => el('tr', { class: k === '最大食' ? 'max' : '' }, el('td', { class: 'lbl' }, k), el('td', {}, fmtDT(t, 1)), el('td', { class: 'muted' }, t.replace('T', ' ').replace('Z', '')))))
    )));
    const kv = el('dl', { class: 'kv' },
      el('dt', {}, '最大食の地点'), el('dd', {}, `${fmtLatLon(d.ge_lat, d.ge_lon)}（太陽高度 ${d.sun_alt.toFixed(1)}°）`),
      el('dt', {}, 'ΔT'), el('dd', {}, `${d.delta_t_s.toFixed(2)} 秒`));
    box.append(el('div', { class: 'section-title' }, 'その他'), kv);
    box.append(el('div', { class: 'section-title' }, '最大食の地点での見え方'));
    if (state.local) box.append(contactTable(state.local));
    else box.append(el('p', { class: 'muted' }, el('span', { class: 'spinner' }), '計算中…'));
    box.append(el('p', { class: 'note' }, '「地図」タブで地図上の好きな地点をクリックすると、その地点での見え方（時刻・食分）を計算できます。'));
    return;
  }
  box.append(el('div', { class: 'lead', html: leadText(d) }));
  if (d.warnings && d.warnings.length) box.append(el('div', { class: 'warnings' }, d.warnings.map((w) => el('div', {}, '⚠ ' + w))));
  const moon = d.body === 'moon';
  const cmax = d.contacts.find((c) => c.label === 'MAX');
  const ms = [];
  if (moon) {
    ms.push(metric('最大食分', f4(d.magnitude), '太陽の直径が隠れる割合'));
    ms.push(metric('食面積率', pct(d.obscuration, 2), '太陽の面積が隠れる割合'));
    if (d.type === 'total' || d.type === 'annular') ms.push(metric(d.type === 'total' ? '皆既の継続時間' : '金環の継続時間', fmtDur(d.central_duration_s, true), d.n_internal > 1 ? `最長区間（${d.n_internal}回）` : '第2〜第3接触'));
    ms.push(metric('食の継続時間', fmtDur(d.duration_s), '第1〜第4接触'));
    ms.push(metric('視直径比（月/太陽）', f4(d.ratio), d.ratio > 1 ? '月の方が大きい' : '月の方が小さい'));
  } else {
    ms.push(metric('太陽中心との最小距離', f1(d.min_sep_arcsec) + '″', `太陽の半径は ${f1(d.sun_diameter_arcsec / 2)}″`));
    ms.push(metric(`${bodyJa(d.body)}の視直径`, f1(d.body_diameter_arcsec) + '″', `太陽の約 1/${Math.round(d.sun_diameter_arcsec / d.body_diameter_arcsec)}`));
    ms.push(metric('経過時間', fmtDur(d.duration_s), '第1〜第4接触'));
    if (d.central_duration_s > 0) ms.push(metric('内接している時間', fmtDur(d.central_duration_s), '第2〜第3接触'));
  }
  if (d.observer_kind === 'ground') ms.push(metric('最大時の太陽高度', `${cmax.sun_alt.toFixed(1)}°`, `方位 ${cmax.sun_az.toFixed(0)}°（${azName(cmax.sun_az)}）`));
  if (d.observer_kind === 'space') ms.push(metric('見える時間の割合', pct(d.vis_fraction, 0), '太陽が地球に隠されない時間'));
  box.append(el('div', { class: 'metrics' }, ms));
  box.append(el('div', { class: 'section-title' }, '接触時刻と状況'));
  box.append(contactTable(d));
  const p = d.params;
  const kv = el('dl', { class: 'kv' },
    el('dt', {}, '暦'), el('dd', {}, d.ephemeris),
    el('dt', {}, 'ΔT（TT−UT）'), el('dd', {}, `${d.delta_t_s.toFixed(2)} 秒`),
    el('dt', {}, '太陽の視直径（最大時）'), el('dd', {}, `${f1(d.sun_diameter_arcsec)}″`),
    el('dt', {}, `${bodyJa(d.body)}の視直径（最大時）`), el('dd', {}, `${f1(d.body_diameter_arcsec)}″`),
    moon && el('dt', {}, 'サロス番号'), moon && el('dd', {}, d.saros),
    el('dt', {}, '計算モデル'), el('dd', {}, `太陽半径 ${p.sun_radius_km.toFixed(0)} km・` + (moon ? `月半径 ${p.moon_radius_ext_km.toFixed(2)} / ${p.moon_radius_int_km.toFixed(2)} km（外接/内接）` : `${bodyJa(d.body)}半径 ${(d.body === 'venus' ? p.venus_radius_km : p.mercury_radius_km).toFixed(1)} km`)),
  );
  box.append(el('div', { class: 'section-title' }, '計算条件'), kv);
  if (d.c1_cut || d.c4_cut) box.append(el('p', { class: 'note' }, '※ 計算期間の端にかかっているため、一部の接触時刻は期間の端の値です。'));
}

function contactTable(d) {
  const kind = d.observer_kind;
  const head = ['接触', `時刻（${tzLabel(d.max)}）`];
  if (kind === 'ground') head.push('太陽高度', '方位');
  if (kind === 'space') head.push('衛星直下点', '高度', '地球の縁からの太陽の離角');
  head.push('位置角 P');
  if (kind === 'ground') head.push('天頂角 V');
  head.push(d.body === 'moon' ? '食分' : '中心間距離', '観測');
  const rows = d.contacts.map((c) => {
    const [n, sub] = contactName(c.label, d);
    const cells = [el('td', { class: 'lbl' }, n, sub && el('span', { class: 'sub' }, sub)),
      el('td', {}, fmtTime(c.time, 1), dayDiff(c.time, d.max) ? el('span', { class: 'sub' }, fmtDateShort(c.time)) : null)];
    if (kind === 'ground') cells.push(el('td', {}, `${c.sun_alt.toFixed(1)}°`), el('td', {}, `${c.sun_az.toFixed(1)}°（${azName(c.sun_az)}）`));
    if (kind === 'space') cells.push(el('td', {}, fmtLatLon(c.sat_lat, c.sat_lon)), el('td', {}, `${c.sat_alt_km.toFixed(0)} km`), el('td', {}, `${c.vis.toFixed(1)}°`));
    cells.push(el('td', {}, `${c.pa.toFixed(1)}°`));
    if (kind === 'ground') cells.push(el('td', {}, `${c.v_angle.toFixed(1)}°`));
    cells.push(el('td', {}, d.body === 'moon' ? f4(c.magnitude) : `${f1(c.sep_arcsec)}″`));
    const visTxt = kind === 'geocenter' ? '—' : c.visible ? el('span', { class: 'pill ok' }, '見える') : el('span', { class: 'pill no' }, kind === 'ground' ? '地平線下' : '地球に隠れる');
    cells.push(el('td', {}, visTxt));
    return el('tr', { class: c.label === 'MAX' ? 'max' : '' }, cells);
  });
  return el('div', { class: 'table-scroll' }, el('table', { class: 'contacts' }, el('thead', {}, el('tr', {}, head.map((h) => el('th', {}, h)))), el('tbody', {}, rows)));
}

/* ------------------------------------------------------------------ */
/* disk viewer                                                         */
/* ------------------------------------------------------------------ */
const SPEEDS = [[1, '実時間'], [10, '10倍速'], [60, '60倍速（1秒=1分）'], [300, '300倍速（1秒=5分）'], [900, '900倍速（1秒=15分）'], [3600, '3600倍速（1秒=1時間）']];
function initViewer() {
  const sp = $('#speed');
  SPEEDS.forEach(([v, l]) => sp.append(el('option', { value: v }, l)));
  $('#playBtn').addEventListener('click', () => { state.playing ? pause() : play(); });
  $('#timeSlider').addEventListener('input', () => {
    const ts = state.series; if (!ts) return;
    state.t = ts.dur * (+$('#timeSlider').value / 1000);
    drawDisk(); updateCursor();
  });
  $('#orient').addEventListener('change', drawDisk);
  $('#showGrid').addEventListener('change', drawDisk);
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
  if (!L || !L.timeseries) { state.series = null; drawDisk(); return; }
  const ts = L.timeseries;
  const t0 = new Date(ts.t0).getTime();
  const s = { ...ts, t0ms: t0, dur: ts.dt_s[ts.dt_s.length - 1] };
  if (ts.parallactic) s.parallactic = unwrapDeg(ts.parallactic);
  if (ts.earth_pa) s.earth_pa = unwrapDeg(ts.earth_pa);
  state.series = s;
  const o = $('#orient');
  const kind = L.observer_kind;
  const prev = o.value;
  o.innerHTML = '';
  if (kind === 'ground') o.append(el('option', { value: 'zenith' }, '天頂が上（見たままの向き）'));
  o.append(el('option', { value: 'north' }, '天の北が上（天文図の向き）'));
  if (kind === 'space') o.append(el('option', { value: 'earth' }, '地球の方向が下'));
  if ([...o.options].some((x) => x.value === prev) && !reset) o.value = prev;
  // contact jump buttons
  const jb = $('#jumpBtns');
  jb.innerHTML = '';
  for (const c of L.contacts) {
    const [n] = contactName(c.label, L);
    const tt = (new Date(c.time).getTime() - t0) / 1000;
    jb.append(el('button', { type: 'button', onclick: () => setTime(tt) }, n));
  }
  // default speed: whole event in ~20 s
  const target = s.dur / 20;
  let best = SPEEDS[0][0];
  for (const [v] of SPEEDS) if (Math.abs(Math.log(v / target)) < Math.abs(Math.log(best / target))) best = v;
  $('#speed').value = best;
  const cmax = L.contacts.find((c) => c.label === 'MAX');
  state.t = (new Date(cmax.time).getTime() - t0) / 1000;
  $('#viewNote').textContent = kind === 'ground' ? '空を見上げたときの見え方です（大きさは太陽が基準）。地平線は緑で表示します。'
    : kind === 'space' ? '衛星から太陽方向を見た様子です。紺色の大きな円弧は地球の縁です。' : '地球中心から見た様子です。';
  syncSlider();
  if (currentTab() === 'view') { resizeCanvas(); drawDisk(); }
}
function setTime(t) {
  const s = state.series; if (!s) return;
  state.t = Math.max(0, Math.min(s.dur, t));
  syncSlider(); drawDisk(); updateCursor();
}
function syncSlider() { const s = state.series; if (s) $('#timeSlider').value = Math.round(1000 * state.t / s.dur); }
let _raf = null, _last = 0;
function play() {
  const s = state.series; if (!s) return;
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
function frame(t) {
  const s = state.series;
  const keys = ['xi', 'eta', 'rho_s', 'rho_b', 'rho_b_int', 'magnitude', 'obscuration', 'sep_arcsec', 'vis', 'sun_alt', 'sun_az', 'parallactic',
    'earth_pa', 'earth_sep_deg', 'earth_radius_deg', 'earth_atm_deg', 'sat_lat', 'sat_alt_km'];
  const f = {};
  for (const k of keys) f[k] = sample(s[k], t);
  if (s.sat_lon) { const i = Math.round(t / s.dur * (s.dt_s.length - 1)); f.sat_lon = s.sat_lon[i]; }
  return f;
}
function resizeCanvas() {
  const c = $('#diskCanvas');
  const r = c.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const w = Math.max(200, Math.round(r.width * dpr));
  if (c.width !== w) { c.width = w; c.height = w; }
}
function refAngle(f, mode) {
  if (mode === 'zenith' && f.parallactic != null) return f.parallactic;
  if (mode === 'earth' && f.earth_pa != null) return f.earth_pa - 180;
  return 0;
}
function mix(a, b, t) {
  const pa = a.match(/\w\w/g).map((h) => parseInt(h, 16));
  const pb = b.match(/\w\w/g).map((h) => parseInt(h, 16));
  return '#' + pa.map((v, i) => Math.round(v + (pb[i] - v) * t).toString(16).padStart(2, '0')).join('');
}
function drawDisk() {
  const c = $('#diskCanvas');
  const g = c.getContext('2d');
  const W = c.width, H = c.height, cx = W / 2, cy = H / 2;
  g.setTransform(1, 0, 0, 1, 0, 0);
  g.clearRect(0, 0, W, H);
  const s = state.series;
  const L = state.local;
  if (!s || !L) {
    g.fillStyle = '#0b0f19'; g.fillRect(0, 0, W, H);
    g.fillStyle = '#9aa4b8'; g.font = `${Math.round(W / 30)}px sans-serif`; g.textAlign = 'center';
    g.fillText(state.detail && state.detail.kind === 'global' ? '最大食の地点での見え方を計算中…' : '', cx, cy);
    $('#readout').innerHTML = '';
    return;
  }
  const mode = $('#orient').value || 'north';
  const f = frame(state.t);
  const kind = L.observer_kind;
  const k = (Math.min(W, H) * 0.30) / f.rho_s;
  const ref = refAngle(f, mode) * Math.PI / 180;
  const toScreen = (xi, eta, refA = ref) => [cx - k * (xi * Math.cos(refA) - eta * Math.sin(refA)), cy - k * (eta * Math.cos(refA) + xi * Math.sin(refA))];
  const totalPhase = L.body === 'moon' && f.rho_b_int > f.rho_s && f.sep_arcsec < f.rho_b_int - f.rho_s;

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
  for (let i = 0; i < n; i += 2) {
    const ra = (mode === 'zenith' && s.parallactic) ? s.parallactic[i] * Math.PI / 180 : (mode === 'earth' && s.earth_pa) ? (s.earth_pa[i] - 180) * Math.PI / 180 : 0;
    const [x, y] = toScreen(s.xi[i], s.eta[i], ra);
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
  // occulting body
  const [bx, by] = toScreen(f.xi, f.eta);
  const rb = f.rho_b * k;
  g.fillStyle = L.body === 'moon' ? (totalPhase ? '#05070c' : '#161a22') : '#050505';
  g.beginPath(); g.arc(bx, by, Math.max(rb, 1.5), 0, 2 * Math.PI); g.fill();
  if (L.body === 'moon') { g.strokeStyle = 'rgba(255,255,255,.08)'; g.lineWidth = 1; g.stroke(); }

  // Earth limb (spacecraft)
  if (kind === 'space' && f.earth_sep_deg != null && (f.earth_sep_deg - f.earth_atm_deg) * 3600 < f.rho_s * 4.5) {
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
  if (kind === 'ground' && f.sun_alt != null && f.sun_alt * 3600 < f.rho_s * 4.5) {
    const sigz = (f.parallactic * Math.PI / 180) - ref;
    g.save();
    g.translate(cx, cy); g.rotate(-sigz);
    const hy = f.sun_alt * 3600 * k;
    const gg = g.createLinearGradient(0, hy, 0, hy + W);
    gg.addColorStop(0, '#2f5d3a'); gg.addColorStop(1, '#14261a');
    g.fillStyle = gg; g.fillRect(-2 * W, hy, 4 * W, 4 * W);
    g.strokeStyle = '#9fd3a8'; g.lineWidth = Math.max(1, W / 400);
    g.beginPath(); g.moveTo(-2 * W, hy); g.lineTo(2 * W, hy); g.stroke();
    g.restore();
  }
  // orientation marks
  if ($('#showGrid').checked) {
    const fs = Math.round(W / 32);
    g.font = `bold ${fs}px sans-serif`; g.textAlign = 'center'; g.textBaseline = 'middle';
    const arrow = (paDeg, label, color) => {
      const sig = paDeg * Math.PI / 180 - ref;
      const r1 = rs * 1.22, r2 = rs * 1.45;
      const x1 = cx - r1 * Math.sin(sig), y1 = cy - r1 * Math.cos(sig);
      const x2 = cx - r2 * Math.sin(sig), y2 = cy - r2 * Math.cos(sig);
      g.strokeStyle = color; g.fillStyle = color; g.lineWidth = Math.max(1.5, W / 350);
      g.beginPath(); g.moveTo(x1, y1); g.lineTo(x2, y2); g.stroke();
      g.fillText(label, cx - (r2 + fs) * Math.sin(sig), cy - (r2 + fs) * Math.cos(sig));
    };
    arrow(0, '北', 'rgba(255,255,255,.75)');
    arrow(90, '東', 'rgba(255,255,255,.55)');
    if (kind === 'ground' && f.parallactic != null) arrow(f.parallactic, '天頂', 'rgba(160,255,190,.9)');
    if (kind === 'space' && f.earth_pa != null) arrow(f.earth_pa, '地球', 'rgba(140,190,255,.95)');
  }
  // scale bar text
  g.font = `${Math.round(W / 40)}px sans-serif`; g.textAlign = 'left'; g.textBaseline = 'alphabetic';
  g.fillStyle = 'rgba(255,255,255,.7)';
  g.fillText(`太陽の視直径 ${(2 * f.rho_s / 60).toFixed(2)}′`, W * 0.03, H * 0.965);
  updateReadout(f, totalPhase);
}
function phaseLabel(f, L) {
  if (L.body === 'moon') {
    if (f.magnitude <= 0) return '食なし';
    if (f.rho_b_int > f.rho_s && f.sep_arcsec < f.rho_b_int - f.rho_s) return '皆既中';
    if (f.rho_b_int < f.rho_s && f.sep_arcsec < f.rho_s - f.rho_b_int) return '金環中';
    return '部分食中';
  }
  if (f.sep_arcsec > f.rho_s + f.rho_b) return '通過前後';
  if (f.sep_arcsec < f.rho_s - f.rho_b) return '太陽面を通過中';
  return '太陽の縁にかかっている';
}
function updateReadout(f, total) {
  const s = state.series, L = state.local;
  const t = new Date(s.t0ms + state.t * 1000);
  const kind = L.observer_kind;
  const visible = kind === 'geocenter' || f.vis > 0;
  const rows = [];
  if (L.body === 'moon') {
    rows.push(['食分', f.magnitude > 0 ? f.magnitude.toFixed(4) : '0'], ['食面積率', pct(Math.max(0, f.obscuration), 2)]);
  } else rows.push(['太陽中心からの距離', `${f.sep_arcsec.toFixed(1)}″`]);
  if (kind === 'ground') rows.push(['太陽の高度・方位', `${f.sun_alt.toFixed(1)}°・${f.sun_az.toFixed(0)}°（${azName(f.sun_az)}）`]);
  if (kind === 'space') {
    rows.push(['衛星直下点', `${fmtLat(f.sat_lat)} ${fmtLon(f.sat_lon)}`], ['衛星の高度', `${f.sat_alt_km.toFixed(0)} km`]);
    rows.push(['地球の縁からの太陽', `${f.vis.toFixed(2)}°`]);
  }
  rows.push(['見えるか', kind === 'geocenter' ? '—' : visible ? '見える' : (kind === 'ground' ? '地平線の下' : '地球に隠れている')]);
  $('#readout').innerHTML = `<div class="t">${fmtTime(t.toISOString(), 1)} <small class="muted">${tzLabel(t)}</small></div>` +
    `<div class="muted" style="font-size:12px">${fmtDate(t.toISOString())}</div>` +
    `<span class="phase">${phaseLabel(f, L)}</span><table>${rows.map(([a, b]) => `<tr><td>${a}</td><td>${b}</td></tr>`).join('')}</table>`;
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
  root.append(svg('line', { x1: pl, x2: W - pr, y1: H - pb, y2: H - pb, stroke: '#9aa4b8' }));
  for (const h of cfg.hlines || []) {
    root.append(svg('line', { x1: pl, x2: W - pr, y1: Y(h.v), y2: Y(h.v), stroke: h.color, 'stroke-dasharray': '4 4' }));
    const tx = svg('text', { x: W - pr - 4, y: Y(h.v) - 4, 'text-anchor': 'end', 'font-size': 11, fill: h.color });
    tx.textContent = h.label; root.append(tx);
  }
  // contacts
  for (const c of state.local.contacts) {
    const t = (new Date(c.time).getTime() - s.t0ms) / 1000;
    root.append(svg('line', { x1: X(t), x2: X(t), y1: pt, y2: H - pb, stroke: c.label === 'MAX' ? '#e8850c' : '#b8c0cf', 'stroke-dasharray': '3 3' }));
    const tx = svg('text', { x: X(t) + 3, y: pt + 11, 'font-size': 10, fill: '#7a8396' });
    tx.textContent = c.label === 'MAX' ? '最大' : c.label; root.append(tx);
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
    lx += 30 + ser.label.length * 11;
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
  if (!s || !L) { c1.innerHTML = '<p class="muted">計算中…</p>'; c2.innerHTML = ''; return; }
  const kind = L.observer_kind;
  const notVis = (i) => kind !== 'geocenter' && s.vis[i] <= 0;
  if (L.body === 'moon') {
    const ymax = Math.max(1, ...s.magnitude) * 1.02;
    lineChart(c1, {
      title: '食分と食面積率の変化', yRange: [0, ymax], shade: notVis, yFmt: (v) => v.toFixed(1),
      series: [{ y: s.magnitude, color: '#e8850c', label: '食分' }, { y: s.obscuration, color: '#2463c9', label: '食面積率' }],
    });
  } else {
    const rs = s.rho_s[0], rb = s.rho_b[0];
    lineChart(c1, {
      title: `${bodyJa(L.body)}の中心と太陽の中心の距離（″）`, yRange: [0, Math.max(...s.sep_arcsec) * 1.05], shade: notVis, yFmt: (v) => v.toFixed(0),
      series: [{ y: s.sep_arcsec, color: '#2463c9', label: '中心間距離' }],
      hlines: [{ v: rs + rb, color: '#e8850c', label: '外接' }, { v: rs - rb, color: '#19875f', label: '内接' }],
    });
  }
  if (kind === 'ground') {
    const lo = Math.min(-5, ...s.sun_alt), hi = Math.max(10, ...s.sun_alt);
    lineChart(c2, {
      title: '太陽の高度（°）', yRange: [Math.floor(lo / 5) * 5, Math.ceil(hi / 5) * 5], shade: notVis, yFmt: (v) => v.toFixed(0),
      series: [{ y: s.sun_alt, color: '#c9a227', label: '太陽高度' }], hlines: [{ v: 0, color: '#19875f', label: '地平線' }],
    });
  } else if (kind === 'space') {
    const lo = Math.min(-2, ...s.vis), hi = Math.max(2, ...s.vis);
    lineChart(c2, {
      title: '地球の縁から太陽までの角度（°）— 0 未満は地球に隠される', yRange: [lo, hi], shade: notVis, yFmt: (v) => v.toFixed(0),
      series: [{ y: s.vis, color: '#2463c9', label: '地球の縁からの離角' }], hlines: [{ v: 0, color: '#c0392b', label: '地球の縁' }],
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
/* map                                                                 */
/* ------------------------------------------------------------------ */
let _worldGeo = null;
function baseLayers(m, small = false) {
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
  L.control.layers({ 'シンプル地図（オフライン）': simple, '詳細地図（OpenStreetMap）': osm }, null, { position: small ? 'topright' : 'topright' }).addTo(m);
  return { simple, osm };
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
    setStatus(st, '地図データを計算中…（数秒かかります）', 'busy');
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
    legend.append(el('span', {}, 'この現象は地上からは見られない（地球上に食が生じない）ため、食の地図はありません。'));
  } else if (md.map_kind === 'eclipse') {
    const grid = md.grid;
    layers.addLayer(gridOverlay(grid, (v) => (v <= 0.0005 ? null : [...magColor(v), 150])));
    for (const c of grid.contours) {
      for (const ln of c.lines) {
        addCopies(layers, ln, (pts) => L.polyline(pts, { color: c.level < 0.01 ? '#7a3b00' : '#8a4b10', weight: c.level < 0.01 ? 1.6 : 1, opacity: 0.8, dashArray: c.level < 0.01 ? null : '4 4' })
          .bindTooltip(c.level < 0.01 ? '部分食が見える範囲の境界（太陽が地平線上）' : `最大食分 ${c.level}`, { sticky: true, className: 'map-tip' }));
      }
    }
    if (md.penumbra_limits) {
      for (const side of ['N', 'S']) for (const seg of md.penumbra_limits[side] || []) {
        addCopies(layers, seg.map((p) => [p[0], p[1]]), (pts) => L.polyline(pts, { color: '#6b4fbb', weight: 1.5, dashArray: '6 4' }).bindTooltip(`部分食の${side === 'N' ? '北' : '南'}限界線`, { sticky: true }));
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
      for (const [side, arr] of [['N', N], ['S', S]]) for (const seg of arr) {
        const pts = seg.map((p) => [p[0], p[1]]);
        addCopies(layers, pts, (q) => L.polyline(q, { color: '#111', weight: 1.8 }).bindTooltip(`${typ === 'annular' ? '金環' : typ === 'total' ? '皆既' : '中心食'}帯の${side === 'N' ? '北' : '南'}限界線`, { sticky: true }));
      }
    }
    for (const seg of md.central_line || []) {
      const pts = seg.map((p) => [p[0], p[1]]);
      addCopies(layers, pts, (q) => L.polyline(q, { color: '#d62828', weight: 2 }).bindTooltip('中心線', { sticky: true }));
      pts.forEach((p) => bounds.extend(p));
    }
    for (const o of md.umbra_outlines || []) {
      for (const seg of o.lines) addCopies(layers, seg, (q) => L.polyline(q, { color: '#000', weight: 1, opacity: 0.7 }).bindTooltip(`${fmtTime(o.time)} ${tzLabel(o.time)} の本影`, { sticky: true }));
    }
    for (const p of md.central_points || []) {
      addCopies(layers, [[p.lat, p.lon]], (q) => L.circleMarker(q[0], { radius: 3, color: '#d62828', weight: 1, fillOpacity: 0.6 })
        .bindTooltip(`${fmtDT(p.time)} ${tzLabel(p.time)}<br>中心食の継続 ${fmtDur(p.duration_s, true)}<br>太陽高度 ${p.sun_alt.toFixed(0)}°`));
    }
    const ev = md.event;
    const ge = L.circleMarker([ev.ge_lat, ev.ge_lon], { radius: 7, color: '#fff', weight: 2, fillColor: '#d62828', fillOpacity: 1 });
    ge.bindTooltip(`最大食 ${fmtDT(ev.max)} ${tzLabel(ev.max)}<br>${fmtLatLon(ev.ge_lat, ev.ge_lon)}`);
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
      el('span', {}, '最大食分', el('span', { class: 'grad', style: `background:linear-gradient(90deg,${[0.05, 0.2, 0.4, 0.6, 0.8, 0.95, 1].map((v) => `rgb(${magColor(v).join(',')})`).join(',')})` }), '0 → 1'),
      el('span', {}, el('span', { class: 'ln', style: 'border-color:#d62828' }), '中心線'),
      ev.type !== 'partial' && el('span', {}, el('span', { class: 'sw', style: 'background:rgba(17,17,17,.4)' }), `${ev.type === 'annular' ? '金環' : '皆既'}帯（黒線は限界線・10分ごとの本影）`),
      el('span', {}, el('span', { class: 'ln', style: 'border-color:#6b4fbb;border-top-style:dashed' }), '部分食の限界線'),
      el('span', { class: 'muted' }, '色は太陽が地平線上にある時間帯での最大食分。地図をクリックすると、その地点での見え方を計算します。'),
    );
  } else if (md.map_kind === 'transit') {
    layers.addLayer(gridOverlay(md.grid, (v) => (TRANSIT_CODES[v] ? [...hexRgb(TRANSIT_CODES[v][0]), 120] : null)));
    for (const sp of md.subsolar) {
      const lbl = { C1: '第1接触', MAX: '最大', C4: '第4接触' }[sp.label];
      layers.addLayer(L.circleMarker([sp.lat, sp.lon], { radius: 6, color: '#fff', weight: 2, fillColor: '#f39b1d', fillOpacity: 1 }).bindTooltip(`${lbl}の時刻に太陽が真上にある地点`));
    }
    legend.append(...Object.values(TRANSIT_CODES).map(([c, l]) => el('span', {}, el('span', { class: 'sw', style: `background:${c}` }), l)));
    legend.append(el('span', { class: 'muted' }, `地心の接触時刻（${fmtTime(md.geocentric.c1)}〜${fmtTime(md.geocentric.c4)} ${tzLabel(md.geocentric.c1)}）にもとづく概略図。地図をクリックすると、その地点での正確な時刻を計算します。`));
    bounds.extend([[-60, -180], [70, 180]]);
  }
  // observer
  const Lc = state.local;
  if (d.kind !== 'global' && d.observer_kind === 'ground') {
    const o = d.observer;
    layers.addLayer(L.marker([o.lat, o.lon]).bindTooltip(`観測地: ${esc(o.name)}`, { permanent: false }));
    bounds.extend([o.lat, o.lon]);
  }
  if (d.kind !== 'global' && d.observer_kind === 'space' && Lc && Lc.timeseries) {
    const s = Lc.timeseries;
    const pts = unwrapTrack(s.sat_lat.map((la, i) => [la, s.sat_lon[i]]));
    addCopies(layers, pts, (q) => L.polyline(q, { color: '#0aa5c9', weight: 3, opacity: 0.9 }).bindTooltip('現象中の衛星直下点の軌跡', { sticky: true }));
    for (const c of Lc.contacts) {
      const [n] = contactName(c.label, Lc);
      layers.addLayer(L.circleMarker([c.sat_lat, c.sat_lon], { radius: c.label === 'MAX' ? 6 : 4, color: '#055a6e', weight: 1, fillColor: '#0aa5c9', fillOpacity: 1 })
        .bindTooltip(`${n} ${fmtTime(c.time)}<br>衛星高度 ${c.sat_alt_km.toFixed(0)} km`));
    }
    pts.forEach((p) => bounds.extend(p));
    legend.append(el('span', {}, el('span', { class: 'ln', style: 'border-color:#0aa5c9' }), '衛星直下点の軌跡'));
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
  const popup = L.popup().setLatLng(ev.latlng).setContent('<span class="spinner"></span>この地点での見え方を計算中…').openOn(state.map);
  try {
    const r = await api('/api/local', { event_id: await liveId(base), lat: ll.lat, lon: ll.lng, elevation_m: 0, name: `地図上の地点 ${fmtLatLon(ll.lat, ll.lng)}` });
    if (!r.found) { popup.setContent(`${fmtLatLon(ll.lat, ll.lng)}<br>この地点では${d.body === 'moon' || d.kind === 'global' ? '食は起こりません' : '見られません'}。`); return; }
    const moon = r.body === 'moon';
    const vis = r.vis_fraction >= 0.999 ? '全経過が見える' : r.vis_fraction > 0 ? `一部が見える（${Math.round(r.vis_fraction * 100)}%）` : '太陽が地平線の下で見えない';
    const rows = [
      ['種類', r.type_ja], ['始まり', `${fmtTime(r.c1)}`], ['最大', `${fmtTime(r.max)}${moon ? `（食分 ${f3(r.magnitude)}）` : ''}`], ['終わり', fmtTime(r.c4)],
      moon && r.central_duration_s > 0 ? [r.type === 'total' ? '皆既' : '金環', fmtDur(r.central_duration_s, true)] : null,
      ['太陽高度(最大時)', `${r.contacts.find((c) => c.label === 'MAX').sun_alt.toFixed(0)}°`], ['見え方', vis],
    ].filter(Boolean);
    const btn = el('button', { class: 'popup-btn', type: 'button' }, 'この地点の詳細を表示');
    btn.addEventListener('click', () => { state.map.closePopup(); openDetail(r.id, { push: true }); });
    const div = el('div', {}, el('b', {}, fmtLatLon(ll.lat, ll.lng)), el('div', { class: 'muted', style: 'font-size:11px' }, `${fmtDate(r.max)}・${tzLabel(r.max)}`),
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
    lines.push(`${fmtDate(d.max)} ${d.type_ja}（地球全体）`);
    lines.push(`最大食: ${fmtDT(d.max, 1)} ${tzLabel(d.max)}  ${fmtLatLon(d.ge_lat, d.ge_lon)}  太陽高度 ${d.sun_alt.toFixed(1)}°`);
    lines.push(`食分 ${f4(d.magnitude)}  γ ${f4(d.gamma)}  中心食継続 ${fmtDur(d.central_duration_s, true)}  幅 ${d.path_width_km ? d.path_width_km.toFixed(1) + ' km' : '—'}  サロス ${d.saros}`);
    lines.push(`P1 ${fmtDT(d.p1, 1)}  P4 ${fmtDT(d.p4, 1)}  ΔT ${d.delta_t_s.toFixed(2)} s`);
    if (L) lines.push('', '最大食の地点での接触時刻:');
  } else lines.push(`${fmtDate(d.max)} ${d.type_ja}  観測者: ${observerText(d.observer)}`);
  const src = d.kind === 'global' ? L : d;
  if (src) {
    for (const c of src.contacts) {
      const [n, sub] = contactName(c.label, src);
      let s = `${(n + (sub ? `（${sub}）` : '')).padEnd(18, '　')} ${fmtDT(c.time, 1)} ${tzLabel(c.time)}  P=${c.pa.toFixed(1)}°`;
      if (c.sun_alt != null) s += `  高度 ${c.sun_alt.toFixed(1)}° 方位 ${c.sun_az.toFixed(1)}°`;
      if (c.sat_lat != null) s += `  衛星 ${fmtLatLon(c.sat_lat, c.sat_lon)} ${c.sat_alt_km.toFixed(0)}km`;
      if (src.body === 'moon') s += `  食分 ${f4(c.magnitude)}`;
      lines.push(s);
    }
  }
  return lines.join('\n');
}
function renderData() {
  const d = state.detail;
  if (!d) return;
  const copy = JSON.parse(JSON.stringify(d));
  if (copy.timeseries) copy.timeseries = `（${copy.timeseries.dt_s.length} 点の時系列 — CSV でダウンロードできます）`;
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
  const t0 = new Date(s.t0).getTime();
  const keys = Object.keys(s).filter((k) => Array.isArray(s[k]) && k !== 'dt_s');
  const rows = [['time_utc', ...keys]];
  s.dt_s.forEach((dt, i) => rows.push([new Date(t0 + dt * 1000).toISOString(), ...keys.map((k) => s[k][i])]));
  saveFile(`eclipse_timeseries_${L.max.slice(0, 10)}.csv`, toCsv(rows), 'text/csv');
}
async function copyText() {
  try { await navigator.clipboard.writeText(textSummary()); $('#copyText').textContent = 'コピーしました'; }
  catch (_) { $('#copyText').textContent = 'コピーできませんでした'; }
  setTimeout(() => { $('#copyText').textContent = '結果をテキストでコピー'; }, 1800);
}

init();
