"""FastAPI web server: JSON API + static single-page UI."""
import asyncio
import itertools
import os
import signal
import threading
import time
import traceback
from collections import OrderedDict

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .constants import (MERCURY_RADIUS, MERCURY_RADIUS_NASA, MOON_K_EXTERNAL, MOON_K_INTERNAL,
                        MOON_RADIUS_MEAN, SUN_RADIUS_IAU2015, SUN_RADIUS_NASA, VENUS_RADIUS,
                        VENUS_RADIUS_NASA, WGS84_A)
from .conjunctions import find_conjunctions
from .context import (DEFAULT_EPHEMERIS, DOWNLOADABLE, ROOT_DIR, available_ephemerides,
                      ensure_ephemeris, get_context)
from .eclipse_map import solar_eclipse_map, transit_map
from .global_eclipse import search_global
from .local import LocalSearch, Params
from .observers import (GeocenterObserver, GroundObserver, ObserverError, SSCWebObserver,
                        TLEObserver, build_observer, fetch_tle_celestrak, ssc_satellites)
from .presets import SATELLITES, cities
from .timeutil import iso_from_jd, parse_utc

STATIC_DIR = ROOT_DIR / 'static'
APP_MODE = os.environ.get('ECLIPSECALC_APP') == '1'  # started by the macOS app (run.py --app)

app = FastAPI(title='日食・太陽面通過 精密計算機', version=__version__)
app.mount('/static', StaticFiles(directory=str(STATIC_DIR)), name='static')

_compute_lock = threading.Lock()
_download_lock = threading.Lock()
_ids = itertools.count(1)
_store = OrderedDict()
_STORE_MAX = 4000

TYPE_JA = {
    ('moon', 'total'): '皆既日食', ('moon', 'annular'): '金環日食',
    ('moon', 'hybrid'): '金環皆既日食', ('moon', 'partial'): '部分日食',
    ('mercury', 'transit'): '水星の太陽面通過', ('mercury', 'transit_grazing'): '水星の太陽面通過（外接のみ）',
    ('venus', 'transit'): '金星の太陽面通過', ('venus', 'transit_grazing'): '金星の太陽面通過（外接のみ）',
}


def _put(obj):
    eid = f'e{next(_ids)}'
    _store[eid] = obj
    while len(_store) > _STORE_MAX:
        _store.popitem(last=False)
    return eid


def _get(eid):
    obj = _store.get(eid)
    if obj is None:
        raise HTTPException(404, '指定された現象が見つかりません（サーバー再起動後は再計算してください）')
    return obj


def _clean(v):
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items() if not k.startswith('_')}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if isinstance(v, np.ndarray):
        return _clean(v.tolist())
    if isinstance(v, (np.floating, float)):
        f = float(v)
        return f if np.isfinite(f) else None
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.bool_):
        return bool(v)
    return v


class SearchRequest(BaseModel):
    phenomena: list[str] = ['moon', 'mercury', 'venus']
    observer: dict
    start: str
    end: str
    settings: dict = {}


class SweepRequest(SearchRequest):
    step_deg: float = 10.0


class LocalRequest(BaseModel):
    event_id: str
    lat: float
    lon: float
    elevation_m: float = 0.0
    name: str = ''


def _settings(settings):
    eph = settings.get('ephemeris') or DEFAULT_EPHEMERIS
    dt = settings.get('delta_t')
    dt = None if dt in (None, '', 'auto') else float(dt)
    ctx = get_context(eph, dt)
    params = Params.from_dict(settings)
    return ctx, params


def _local_summary(ev, observer):
    s = {k: ev.get(k) for k in (
        'body', 'type', 'c1', 'max', 'c4', 'jd_c1', 'jd_max', 'jd_c4', 'magnitude', 'obscuration',
        'ratio', 'min_sep_arcsec', 'duration_s', 'central_duration_s', 'internal_durations_s',
        'vis_fraction', 'visible_at_max', 'visible_intervals', 'visible_max', 'saros',
        'body_diameter_arcsec', 'sun_diameter_arcsec', 'delta_t_s', 'c1_cut', 'c4_cut')}
    s['kind'] = 'local'
    s['observer_kind'] = observer.kind
    s['type_ja'] = TYPE_JA.get((ev['body'], ev['type']), ev['type'])
    cmax = next(c for c in ev['contacts'] if c['label'] == 'MAX')
    s['sun_alt_max'] = cmax.get('sun_alt')
    s['sun_az_max'] = cmax.get('sun_az')
    s['sat_lat_max'] = cmax.get('sat_lat')
    s['sat_lon_max'] = cmax.get('sat_lon')
    s['sat_alt_km_max'] = cmax.get('sat_alt_km')
    s['n_internal'] = len(ev['internal'])
    return s


@app.get('/')
def index():
    return FileResponse(STATIC_DIR / 'index.html')


@app.get('/api/info')
def info():
    ctx = get_context()
    a, b = ctx.coverage_utc()
    return {
        'version': __version__,
        'app_mode': APP_MODE,
        'quit_when_closed': QUIT_WHEN_CLOSED,
        'ephemerides': available_ephemerides(),
        'downloadable_ephemerides': _downloadable_ephemerides(),
        'default_ephemeris': DEFAULT_EPHEMERIS,
        'coverage': {'start': a, 'end': b},
        'cities': cities(),
        'satellites': SATELLITES,
        'defaults': Params().as_dict(),
        'radius_presets': {
            'sun': {'iau2015': SUN_RADIUS_IAU2015, 'nasa': SUN_RADIUS_NASA},
            'moon': {'nasa': [MOON_K_EXTERNAL * WGS84_A, MOON_K_INTERNAL * WGS84_A],
                     'mean': [MOON_RADIUS_MEAN, MOON_RADIUS_MEAN]},
            'mercury': {'iau': MERCURY_RADIUS, 'nasa': MERCURY_RADIUS_NASA},
            'venus': {'iau': VENUS_RADIUS, 'nasa': VENUS_RADIUS_NASA},
        },
    }


def _downloadable_ephemerides():
    have = set(available_ephemerides())
    return [{'name': n, 'description': d} for n, d in DOWNLOADABLE.items() if n not in have]


@app.post('/api/ephemeris/download')
def ephemeris_download(name: str):
    """Download a JPL ephemeris into data/ (so that DE440 can be added from the UI)."""
    if name not in DOWNLOADABLE:
        raise HTTPException(400, f'{name} は自動ダウンロードに対応していません')
    with _download_lock:
        try:
            ensure_ephemeris(name, log=lambda m: print(m, flush=True))
        except Exception as exc:
            raise HTTPException(502, f'JPL 暦をダウンロードできませんでした: {exc}')
    return {'ephemerides': available_ephemerides(), 'downloadable_ephemerides': _downloadable_ephemerides()}


# Set by run.py: stops uvicorn gracefully (os.kill with SIGINT would just terminate the process on Windows)
request_quit = None
_stopping = threading.Event()


def _quit():
    _stopping.set()
    if request_quit is not None:
        request_quit()
    else:
        os.kill(os.getpid(), signal.SIGINT)


@app.post('/api/shutdown')
def shutdown():
    """Quit the server ("終了" button of the macOS app, which has no console window to close)."""
    if not APP_MODE:
        raise HTTPException(404, 'Not Found')
    threading.Timer(0.3, _quit).start()
    return {'ok': True}


# Quit when the last page of the UI is closed.  run.py enables this when it opened the browser
# itself (the macOS app, start.bat, start.command).  Each open page keeps this event stream open,
# and the browser drops the connection as soon as the tab or the browser is closed.
QUIT_WHEN_CLOSED = os.environ.get('ECLIPSECALC_QUIT_WHEN_CLOSED') == '1'
PAGE_QUIT_GRACE_S = 10  # a reloaded page connects again well within this
_pages = {'open': 0, 'seen': False, 'changed': 0.0}
_pages_lock = threading.Lock()


@app.get('/api/page/stream')
async def page_stream(request: Request):
    async def events():
        with _pages_lock:
            _pages['open'] += 1
            _pages['seen'] = True
        try:
            yield 'retry: 2000\n\n'
            ticks = 0
            while not _stopping.is_set() and not await request.is_disconnected():
                await asyncio.sleep(1)
                ticks += 1
                if ticks % 15 == 0:
                    yield ': keep-alive\n\n'
        finally:
            with _pages_lock:
                _pages['open'] -= 1
                _pages['changed'] = time.monotonic()
    return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache'})


def _watch_pages():
    while True:
        time.sleep(1)
        with _pages_lock:
            idle = (_pages['seen'] and _pages['open'] == 0
                    and time.monotonic() - _pages['changed'] >= PAGE_QUIT_GRACE_S)
        if idle:
            print('ブラウザの画面がすべて閉じられたため終了します。', flush=True)
            _quit()
            return


if QUIT_WHEN_CLOSED:
    threading.Thread(target=_watch_pages, daemon=True).start()


@app.get('/api/ephemeris_coverage')
def ephemeris_coverage(name: str = DEFAULT_EPHEMERIS):
    ctx = get_context(name)
    a, b = ctx.coverage_utc()
    return {'start': a, 'end': b}


@app.get('/api/tle')
def tle(norad: int):
    try:
        name, l1, l2 = fetch_tle_celestrak(norad)
    except ObserverError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, f'CelesTrak に接続できませんでした: {exc}')
    obs = TLEObserver(get_context(), l1, l2, name)
    d = obs.describe()
    d['epoch'] = iso_from_jd(get_context(), obs.epoch_jd)
    return d


@app.get('/api/sscweb/satellites')
def sscweb_satellites():
    try:
        return {'satellites': ssc_satellites()}
    except ObserverError as exc:
        raise HTTPException(502, str(exc))


def _parse_request(req):
    '''Common validation of search requests -> (ctx, params, jd_a, jd_b, phenomena).'''
    try:
        ctx, params = _settings(req.settings)
        jd_a = parse_utc(ctx, req.start)
        jd_b = parse_utc(ctx, req.end)
    except ObserverError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(400, f'入力値を解釈できません: {exc}')
    if jd_b <= jd_a:
        raise HTTPException(400, '終了日は開始日より後にしてください')
    cov_a, cov_b = ctx.coverage_utc()
    if jd_a < ctx.jd_min or jd_b > ctx.jd_max:
        raise HTTPException(400, f'期間が暦 {ctx.ephemeris_name} の範囲（{cov_a}〜{cov_b}）を超えています')
    phen = [p for p in req.phenomena if p in ('moon', 'mercury', 'venus')]
    if not phen:
        raise HTTPException(400, '計算する現象を 1 つ以上選んでください')
    return ctx, params, jd_a, jd_b, phen


@app.post('/api/search')
def search(req: SearchRequest):
    t0 = time.time()
    ctx, params, jd_a, jd_b, phen = _parse_request(req)
    spec = dict(req.observer)
    years = (jd_b - jd_a) / 365.25
    warnings = []
    with _compute_lock:
        try:
            if spec.get('type') == 'global':
                observer = GeocenterObserver()
                obs_desc = {'kind': 'global', 'name': '地球全体'}
            else:
                observer = build_observer(ctx, spec, parse_time=lambda s: parse_utc(ctx, s))
                obs_desc = observer.describe()
                if obs_desc.get('epoch_jd_tt'):
                    obs_desc['epoch'] = iso_from_jd(ctx, obs_desc['epoch_jd_tt'])
            if observer.kind == 'space' and years > 20:
                raise ObserverError('人工衛星の観測者では期間を 20 年以内にしてください')
            events = []
            for body in phen:
                if spec.get('type') == 'global' and body == 'moon':
                    for ev in search_global(ctx, jd_a, jd_b, params):
                        eid = _put({'kind': 'global', 'event': ev, 'ctx': ctx, 'params': params})
                        s = dict(ev, id=eid)
                        events.append(s)
                    continue
                ls = LocalSearch(ctx, observer, body, params, jd_a, jd_b)
                found = ls.run()
                warnings += [w for w in ls.warnings if w not in warnings]
                for ev in found:
                    eid = _put({'kind': 'local', 'event': ev, 'search': ls,
                                'ctx': ctx, 'params': params, 'observer': observer,
                                'global_obs': spec.get('type') == 'global'})
                    s = _local_summary(ev, observer)
                    s['id'] = eid
                    if spec.get('type') == 'global':
                        s['observer_kind'] = 'geocenter'
                    s['warnings'] = observer.warnings_for(ctx, ev['jd_max'])
                    events.append(s)
            if isinstance(observer, SSCWebObserver):
                obs_desc = observer.describe()       # orbit size is known after the search
        except ObserverError as exc:
            raise HTTPException(400, str(exc))
        except HTTPException:
            raise
        except Exception as exc:
            traceback.print_exc()
            raise HTTPException(500, f'計算中にエラーが発生しました: {exc}')
    events.sort(key=lambda e: e['jd_max'])
    # One summary warning per kind (e.g. the largest TLE age) instead of one per event.
    worst = max((e for e in events if e.get('warnings')),
                key=lambda e: abs(e['jd_max'] - getattr(observer, 'epoch_jd', e['jd_max'])),
                default=None)
    if worst is not None:
        for w in worst['warnings']:
            if w not in warnings:
                warnings.append(w)
    dt_now = float(ctx.ts.tt_jd(0.5 * (jd_a + jd_b)).delta_t)
    return _clean({
        'events': events, 'observer': obs_desc, 'warnings': warnings,
        'elapsed_s': time.time() - t0, 'ephemeris': ctx.ephemeris_name,
        'delta_t_mid_s': dt_now, 'delta_t_override': ctx.delta_t_override,
        'params': params.as_dict(), 'start': req.start, 'end': req.end,
    })


SWEEP_MAX_DAYS = 366.0
CENTRAL_TYPES = ('total', 'annular', 'hybrid')


def _visible(e):
    return (e.get('vis_fraction') or 0) > 0


def _sweep_best(events):
    '''The most significant (visible, if any) event of one phase in one group.'''
    pool = [e for e in events if _visible(e)] or events
    if not pool:
        return None
    if pool[0]['body'] == 'moon':
        return max(pool, key=lambda e: e['magnitude'])
    return max(pool, key=lambda e: (e.get('vis_fraction') or 0, e['duration_s']))


@app.post('/api/phase_sweep')
def phase_sweep(req: SweepRequest):
    '''Search with the satellite placed at every mean anomaly (step_deg apart).

    Before launch the position of a satellite along its orbit is unknown;
    this shows the range of possible outcomes for each eclipse/transit.'''
    t0 = time.time()
    ctx, params, jd_a, jd_b, phen = _parse_request(req)
    spec = dict(req.observer)
    if spec.get('type') != 'kepler':
        raise HTTPException(400, '位相を変えた一括計算は「軌道要素」で指定した衛星だけで使えます')
    step = float(req.step_deg)
    if not 5.0 <= step <= 90.0:
        raise HTTPException(400, '位相の刻みは 5〜90° で指定してください')
    if jd_b - jd_a > SWEEP_MAX_DAYS:
        raise HTTPException(400, '位相を変えた一括計算では期間を 1 年以内にしてください')
    phases = [float(m) for m in np.arange(0.0, 360.0 - 1e-9, step)]
    conj = {b: find_conjunctions(ctx, b, jd_a - 2.0, jd_b + 2.0) for b in phen}
    groups = {}
    warnings = []
    obs_desc = None
    with _compute_lock:
        try:
            for m in phases:
                observer = build_observer(ctx, dict(spec, m_deg=m), parse_time=lambda s: parse_utc(ctx, s))
                if obs_desc is None:
                    obs_desc = observer.describe()
                    obs_desc['epoch'] = iso_from_jd(ctx, obs_desc['epoch_jd_tt'])
                for body in phen:
                    ls = LocalSearch(ctx, observer, body, params, jd_a, jd_b)
                    found = ls.run()
                    warnings += [w for w in ls.warnings if w not in warnings]
                    tc = conj[body]
                    for ev in found:
                        eid = _put({'kind': 'local', 'event': ev, 'search': ls, 'ctx': ctx,
                                    'params': params, 'observer': observer, 'global_obs': False})
                        s = _local_summary(ev, observer)
                        s['id'] = eid
                        s['m_deg'] = m
                        k = int(np.argmin(np.abs(tc - ev['jd_max']))) if len(tc) else -1
                        g = groups.setdefault((body, k), {
                            'body': body, 'jd_conj': float(tc[k]) if k >= 0 else ev['jd_max'],
                            'by_phase': {}})
                        g['by_phase'].setdefault(m, []).append(s)
        except ObserverError as exc:
            raise HTTPException(400, str(exc))
        except HTTPException:
            raise
        except Exception as exc:
            traceback.print_exc()
            raise HTTPException(500, f'計算中にエラーが発生しました: {exc}')
    out = []
    for g in sorted(groups.values(), key=lambda g: g['jd_conj']):
        rows = []
        for m in phases:
            evs = sorted(g['by_phase'].get(m, []), key=lambda e: e['jd_max'])
            rows.append({'m_deg': m, 'count': sum(1 for e in evs if _visible(e)), 'n_all': len(evs),
                         'best': _sweep_best(evs), 'events': evs})
        bests = [r['best'] for r in rows if r['best'] and _visible(r['best'])]
        mags = [b['magnitude'] for b in bests]
        times = sorted(b['max'] for b in bests)
        out.append({
            'body': g['body'], 'date': iso_from_jd(ctx, g['jd_conj']),
            'n_phases': len(phases), 'n_visible': len(bests),
            'count_min': min(r['count'] for r in rows), 'count_max': max(r['count'] for r in rows),
            'mag_min': min(mags) if mags else None, 'mag_max': max(mags) if mags else None,
            'central_phases': [b['m_deg'] for b in bests if b['type'] in CENTRAL_TYPES],
            'time_first': times[0] if times else None, 'time_last': times[-1] if times else None,
            'rows': rows,
        })
    return _clean({
        'groups': out, 'phases': phases, 'step_deg': step, 'observer': obs_desc,
        'warnings': warnings, 'elapsed_s': time.time() - t0, 'ephemeris': ctx.ephemeris_name,
        'delta_t_override': ctx.delta_t_override, 'params': params.as_dict(),
        'start': req.start, 'end': req.end,
    })


def _local_detail(obj):
    ev, ls = obj['event'], obj['search']
    with _compute_lock:
        ts = ls.timeseries(ev)
    d = _local_summary(ev, obj['observer'])
    d['contacts'] = ev['contacts']
    d['internal'] = [[iso_from_jd(obj['ctx'], ls.jd0 + a), iso_from_jd(obj['ctx'], ls.jd0 + b)]
                     for a, b in ev['internal']]
    d['timeseries'] = ts
    d['observer'] = obj['observer'].describe()
    if obj.get('global_obs'):
        d['observer'] = {'kind': 'global', 'name': '地心（地球中心）'}
        d['observer_kind'] = 'geocenter'
    d['warnings'] = obj['observer'].warnings_for(obj['ctx'], ev['jd_max'])
    d['params'] = obj['params'].as_dict()
    d['ephemeris'] = obj['ctx'].ephemeris_name
    return d


@app.get('/api/event/{eid}')
def event_detail(eid: str):
    obj = _get(eid)
    if obj['kind'] == 'global':
        ev = dict(obj['event'], id=eid)
        return _clean(ev)
    d = _local_detail(obj)
    d['id'] = eid
    return _clean(d)


@app.get('/api/event/{eid}/map')
def event_map(eid: str, grid: float = 1.0):
    obj = _get(eid)
    ctx, params = obj['ctx'], obj['params']
    grid = float(min(max(grid, 0.5), 3.0))
    ev = obj['event']
    with _compute_lock:
        try:
            if obj['kind'] == 'global':
                m = solar_eclipse_map(ctx, ev['jd_max'], params, grid, params.min_sun_alt_deg,
                                      params.refraction, event=ev)
                m['map_kind'] = 'eclipse'
            elif ev['body'] == 'moon':
                m = solar_eclipse_map(ctx, ev['jd_max'], params, grid, params.min_sun_alt_deg,
                                      params.refraction)
                m['map_kind'] = 'eclipse'
            else:
                geo = LocalSearch(ctx, GeocenterObserver(), ev['body'], params,
                                  ev['jd_max'] - 1, ev['jd_max'] + 1).run()
                if geo:
                    g = min(geo, key=lambda e: abs(e['jd_max'] - ev['jd_max']))
                    m = transit_map(ctx, g['jd_c1'], g['jd_c4'], g['jd_max'], grid,
                                    params.min_sun_alt_deg, params.refraction)
                    m['geocentric'] = {'c1': g['c1'], 'max': g['max'], 'c4': g['c4']}
                else:
                    m = {'exists': False}
                m['map_kind'] = 'transit'
        except Exception as exc:
            traceback.print_exc()
            raise HTTPException(500, f'地図データの計算に失敗しました: {exc}')
    return _clean(m)


@app.post('/api/local')
def local_at(req: LocalRequest):
    """Local circumstances at a clicked map location for a stored event."""
    obj = _get(req.event_id)
    ctx, params = obj['ctx'], obj['params']
    ev = obj['event']
    body = ev.get('body', 'moon')
    try:
        observer = GroundObserver(req.lat, req.lon, req.elevation_m, req.name)
    except ObserverError as exc:
        raise HTTPException(400, str(exc))
    a = ev.get('jd_p1') or ev.get('jd_c1') or ev['jd_max'] - 0.3
    b = ev.get('jd_p4') or ev.get('jd_c4') or ev['jd_max'] + 0.3
    p2 = Params.from_dict(dict(params.as_dict(), include_invisible=True))
    with _compute_lock:
        ls = LocalSearch(ctx, observer, body, p2, a - 0.3, b + 0.3)
        found = ls.run()
    if not found:
        return {'found': False, 'observer': observer.describe()}
    e = min(found, key=lambda x: abs(x['jd_max'] - ev['jd_max']))
    eid = _put({'kind': 'local', 'event': e, 'search': ls, 'ctx': ctx, 'params': p2,
                'observer': observer})
    d = _local_detail(_store[eid])
    d['id'] = eid
    d['found'] = True
    return _clean(d)


@app.exception_handler(Exception)
async def _unhandled(request, exc):  # pragma: no cover
    traceback.print_exc()
    return JSONResponse({'detail': f'サーバーエラー: {exc}'}, status_code=500)
