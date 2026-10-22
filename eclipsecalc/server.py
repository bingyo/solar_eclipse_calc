"""FastAPI web server: JSON API + static single-page UI."""
import asyncio
import itertools
import math
import os
import secrets
import signal
import threading
import time
import traceback
from collections import OrderedDict
from typing import Any, Optional
from urllib.parse import urlsplit

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, ccsds, i18n
from .constants import (DAY_S, MERCURY_RADIUS, MERCURY_RADIUS_NASA, MOON_K_EXTERNAL, MOON_K_INTERNAL,
                        MOON_RADIUS_MEAN, SUN_RADIUS_IAU2015, SUN_RADIUS_NASA, SUN_RADIUS_OBSERVED,
                        VENUS_RADIUS, VENUS_RADIUS_NASA, VENUS_RADIUS_VISIBLE, VENUS_RADIUS_XRAY, WGS84_A)
from .conjunctions import find_conjunctions
from .context import (DEFAULT_EPHEMERIS, DOWNLOADABLE, ROOT_DIR, available_ephemerides,
                      ensure_ephemeris, get_context)
from .earth_shadow import ShadowSearch, atm_levels
from .eclipse_map import solar_eclipse_map, transit_map
from .global_eclipse import search_global
from .i18n import span, tr
from .local import LocalSearch, Params
from .observers import (GeocenterObserver, GroundObserver, HorizonsObserver, NetworkError, ObserverError,
                        SGP4Observer, SSCWebObserver, TLEObserver, build_observer, fetch_tle_celestrak,
                        ssc_satellites)
from .presets import SATELLITES, cities
from .timeutil import iso_from_jd, parse_obs_time, parse_utc

STATIC_DIR = ROOT_DIR / 'static'
APP_MODE = os.environ.get('ECLIPSECALC_APP') == '1'  # started by the macOS app (run.py --app)

app = FastAPI(title='日食・太陽面通過 精密計算機', version=__version__)
app.mount('/static', StaticFiles(directory=str(STATIC_DIR)), name='static')


@app.middleware('http')
async def _language(request: Request, call_next):
    '''Errors and warnings in the language of the page (X-Lang header, see i18n.py).'''
    token = i18n.set_lang(request.headers.get('x-lang'))
    try:
        return await call_next(request)
    finally:
        i18n.reset_lang(token)


@app.middleware('http')
async def _revalidate_page(request: Request, call_next):
    '''The browser asks again for the page and its scripts each time (304 when unchanged),
    so that an updated calculator is not shown with the old screen from the cache.'''
    response = await call_next(request)
    path = request.url.path
    if path == '/' or path.startswith('/static/'):
        response.headers['Cache-Control'] = 'no-cache'
    return response


@app.middleware('http')
async def _same_origin(request: Request, call_next):
    '''Refuse POSTs sent by other web pages: a form on any site could otherwise make the
    calculator download an ephemeris or quit (the endpoints without a JSON body need no preflight).'''
    origin = request.headers.get('origin')
    if request.method == 'POST' and origin and urlsplit(origin).netloc != request.headers.get('host'):
        return JSONResponse({'detail': 'Forbidden'}, status_code=403)
    return await call_next(request)


_compute_lock = threading.Lock()
_download_lock = threading.Lock()
_ids = itertools.count(1)
_RUN = secrets.token_hex(3)     # ids of an earlier run (kept by an open page) are not found, instead of naming other events
_store = OrderedDict()
_STORE_MAX = 4000

TYPE_JA = {
    ('moon', 'total'): '皆既日食', ('moon', 'annular'): '金環日食',
    ('moon', 'hybrid'): '金環皆既日食', ('moon', 'partial'): '部分日食',
    ('mercury', 'transit'): '水星の太陽面通過', ('mercury', 'transit_grazing'): '水星の太陽面通過（外接のみ）',
    ('venus', 'transit'): '金星の太陽面通過', ('venus', 'transit_grazing'): '金星の太陽面通過（外接のみ）',
}


def _bad_input(exc):
    """An ObserverError as the answer: 502 if an online service could not be reached, else 400."""
    return HTTPException(502 if isinstance(exc, NetworkError) else 400, str(exc))


def _put(obj):
    eid = f'e{_RUN}-{next(_ids)}'
    _store[eid] = obj
    while len(_store) > _STORE_MAX:
        _store.popitem(last=False)
    return eid


def _get(eid):
    obj = _store.get(eid)
    if obj is None:
        raise HTTPException(404, tr('指定された現象が見つかりません（サーバー再起動後は再計算してください）'))
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


class RestoreRequest(BaseModel):
    request: dict
    event: dict


class LocalRequest(BaseModel):
    event_id: str
    lat: float
    lon: float
    elevation_m: float = 0.0
    name: str = ''


class AtRequest(BaseModel):
    '''Times of observations (UTC, see timeutil.parse_obs_time), optionally with the measured
    solar-north-up X / Y (arcsec) of the body at each and the exposure time (s).  Typed loosely
    so that wrong input gets the translated messages of parse_at_request() instead of a 422.'''
    times: Any = None
    measured: Any = None
    exposure_s: Any = None


class AtSearchRequest(AtRequest):
    '''The same for an observer and one body without a computed event (cli.py --at).'''
    phenomena: list[str]
    observer: dict
    settings: dict = {}


def _settings(settings):
    eph = settings.get('ephemeris') or DEFAULT_EPHEMERIS
    dt = settings.get('delta_t')
    dt = None if dt in (None, '', 'auto') else float(dt)
    ctx = get_context(eph, dt)
    params = Params.from_dict(settings)
    if not (params.sun_radius_km > 0 and params.moon_radius_ext_km > 0 and params.moon_radius_int_km > 0
            and params.mercury_radius_km > 0 and params.venus_radius_km > 0):
        raise ObserverError(tr('太陽・月・惑星の半径は正の数（km）で指定してください'))
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
        # observed: the visible limb / disk in front of the Sun (the defaults); iau: the IAU solid
        # surface (the IAU 2015 photosphere for the Sun); nasa: NASA/Espenak; xray: soft X-rays
        'radius_presets': {
            'sun': {'observed': SUN_RADIUS_OBSERVED, 'nasa': SUN_RADIUS_NASA, 'iau2015': SUN_RADIUS_IAU2015},
            'moon': {'nasa': [MOON_K_EXTERNAL * WGS84_A, MOON_K_INTERNAL * WGS84_A],
                     'mean': [MOON_RADIUS_MEAN, MOON_RADIUS_MEAN]},
            'mercury': {'observed': MERCURY_RADIUS, 'iau': MERCURY_RADIUS, 'nasa': MERCURY_RADIUS_NASA,
                        'xray': MERCURY_RADIUS},
            'venus': {'observed': VENUS_RADIUS_VISIBLE, 'iau': VENUS_RADIUS, 'nasa': VENUS_RADIUS_NASA,
                      'xray': VENUS_RADIUS_XRAY},
        },
    }


def _downloadable_ephemerides():
    have = set(available_ephemerides())
    return [{'name': n, 'description': d} for n, d in DOWNLOADABLE.items() if n not in have]


@app.post('/api/ephemeris/download')
def ephemeris_download(name: str):
    """Download a JPL ephemeris into data/ (so that DE440 can be added from the UI)."""
    if name not in DOWNLOADABLE:
        raise HTTPException(400, tr('{name} は自動ダウンロードに対応していません', name=name))
    with _download_lock:
        try:
            ensure_ephemeris(name, log=lambda m: print(m, flush=True))
        except Exception as exc:
            raise HTTPException(502, tr('JPL 暦をダウンロードできませんでした: {exc}', exc=exc))
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
        raise _bad_input(exc)
    except Exception as exc:
        raise HTTPException(502, tr('CelesTrak に接続できませんでした: {exc}', exc=exc))
    try:
        obs = TLEObserver(get_context(), l1, l2, name)
    except ObserverError as exc:        # e.g. a TLE whose perigee is inside the Earth (re-entered)
        raise _bad_input(exc)
    d = obs.describe()
    d['epoch'] = iso_from_jd(get_context(), obs.epoch_jd)
    return d


class CCSDSRequest(BaseModel):
    '''A CCSDS message to look into before computing (the page's 「内容を確認」).  Typed loosely so
    that wrong input gets the translated messages of ccsds.py instead of a 422.'''
    text: Any = None
    object: Any = None
    stations: Any = None
    settings: dict = {}


@app.post('/api/ccsds/inspect')
def ccsds_inspect(req: CCSDSRequest):
    '''What a CCSDS input holds (messages, objects, the TDM participants whose positions are needed)
    and, without a TDM, the observer it gives.'''
    with _compute_lock:
        try:
            ctx, _ = _settings(req.settings)
            out = ccsds.inspect(ctx, req.text, str(req.object or ''), req.stations)
        except ObserverError as exc:
            raise _bad_input(exc)
        except Exception as exc:
            traceback.print_exc()
            raise HTTPException(500, tr('計算中にエラーが発生しました: {exc}', exc=exc))
    return _clean(out)


@app.get('/api/sscweb/satellites')
def sscweb_satellites():
    try:
        return {'satellites': ssc_satellites()}
    except ObserverError as exc:
        raise HTTPException(502, str(exc))


BODIES = ('moon', 'mercury', 'venus')
# 'earth': the times at which the Earth hides the Sun from a spacecraft (earth_shadow.py)
PHENOMENA = BODIES + ('earth',)


def _parse_request(req):
    '''Common validation of search requests -> (ctx, params, jd_a, jd_b, phenomena).'''
    try:
        ctx, params = _settings(req.settings)
        jd_a = parse_utc(ctx, req.start)
        jd_b = parse_utc(ctx, req.end)
    except ObserverError as exc:
        raise _bad_input(exc)
    except Exception as exc:
        raise HTTPException(400, tr('入力値を解釈できません: {exc}', exc=exc))
    if jd_b <= jd_a:
        raise HTTPException(400, tr('終了日は開始日より後にしてください'))
    cov_a, cov_b = ctx.coverage_utc()
    if jd_a < ctx.jd_min or jd_b > ctx.jd_max:
        raise HTTPException(400, tr('期間が暦 {name} の範囲（{start}〜{end}）を超えています',
                                     name=ctx.ephemeris_name, start=cov_a, end=cov_b))
    phen = [p for p in dict.fromkeys(req.phenomena) if p in PHENOMENA]
    if not phen:
        raise HTTPException(400, tr('計算する現象を 1 つ以上選んでください'))
    return ctx, params, jd_a, jd_b, phen


@app.post('/api/search')
def search(req: SearchRequest):
    t0 = time.time()
    ctx, params, jd_a, jd_b, phen = _parse_request(req)
    spec = dict(req.observer)
    years = (jd_b - jd_a) / 365.25
    warnings = []
    shadow = None
    with _compute_lock:
        try:
            if spec.get('type') == 'global':
                observer = GeocenterObserver()
                obs_desc = {'kind': 'global', 'name': tr('地球全体')}
            else:
                observer = build_observer(ctx, spec, parse_time=lambda s: parse_utc(ctx, s))
                obs_desc = observer.describe()
                if obs_desc.get('epoch_jd_tt'):
                    obs_desc['epoch'] = iso_from_jd(ctx, obs_desc['epoch_jd_tt'])
            saved = _saved_request(req, spec, observer)
            if observer.kind == 'space' and years > 20:
                raise ObserverError(tr('人工衛星の観測者では期間を 20 年以内にしてください'))
            events = []
            if 'earth' in phen:
                sh = ShadowSearch(ctx, observer, params, jd_a, jd_b, atm_levels(params))
                shadow = sh.run()
                warnings += [w for w in sh.warnings if w not in warnings]
                epoch = getattr(observer, 'epoch_jd', None)       # an old TLE: judged at the far end
                far = jd_b if epoch is None or abs(jd_b - epoch) >= abs(jd_a - epoch) else jd_a
                warnings += [w for w in observer.warnings_for(ctx, far) if w not in warnings]
            for body in phen:
                if body == 'earth':
                    continue
                if spec.get('type') == 'global' and body == 'moon':
                    for ev in search_global(ctx, jd_a, jd_b, params):
                        eid = _put({'kind': 'global', 'event': ev, 'ctx': ctx, 'params': params,
                                    'request': saved})
                        s = dict(ev, id=eid)
                        events.append(s)
                    continue
                ls = LocalSearch(ctx, observer, body, params, jd_a, jd_b)
                found = ls.run()
                warnings += [w for w in ls.warnings if w not in warnings]
                for ev in found:
                    eid = _put({'kind': 'local', 'event': ev, 'search': ls,
                                'ctx': ctx, 'params': params, 'observer': observer,
                                'global_obs': spec.get('type') == 'global', 'request': saved})
                    s = _local_summary(ev, observer)
                    s['id'] = eid
                    if spec.get('type') == 'global':
                        s['observer_kind'] = 'geocenter'
                    s['warnings'] = observer.warnings_for(ctx, ev['jd_max'])
                    events.append(s)
            if isinstance(observer, SSCWebObserver):
                obs_desc = observer.describe()       # orbit size is known after the search
        except ObserverError as exc:
            raise _bad_input(exc)
        except HTTPException:
            raise
        except Exception as exc:
            traceback.print_exc()
            raise HTTPException(500, tr('計算中にエラーが発生しました: {exc}', exc=exc))
    events.sort(key=lambda e: e['jd_max'])
    # One summary warning per kind (e.g. the largest TLE age) instead of one per event.
    worst = max((e for e in events if e.get('warnings')),
                key=lambda e: abs(e['jd_max'] - getattr(observer, 'epoch_jd', e['jd_max'])),
                default=None)
    if worst is not None and shadow is None:     # the Earth's shadow judged them at the end of the period
        for w in worst['warnings']:
            if w not in warnings:
                warnings.append(w)
    dt_now = float(ctx.ts.tt_jd(0.5 * (jd_a + jd_b)).delta_t)
    out = {
        'events': events, 'observer': obs_desc, 'warnings': warnings,
        'elapsed_s': time.time() - t0, 'ephemeris': ctx.ephemeris_name,
        'delta_t_mid_s': dt_now, 'delta_t_override': ctx.delta_t_override,
        'params': params.as_dict(), 'start': req.start, 'end': req.end,
    }
    if shadow is not None:
        out['earth_shadow'] = shadow
    return _clean(out)


SWEEP_MAX_DAYS = 366.0 + 3 / 86400   # 1 year; the dates are TT, and leap seconds (2 in 1972) lengthen a UTC year
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

    Before launch the position of a satellite along its orbit is unknown, and
    months after the epoch of a TLE it is no longer known well either (drag,
    manoeuvres); this shows the range of possible outcomes for each
    eclipse/transit. Works for Keplerian elements and for TLEs.'''
    t0 = time.time()
    ctx, params, jd_a, jd_b, phen = _parse_request(req)
    spec = dict(req.observer)
    if spec.get('type') not in ('kepler', 'tle') and not (spec.get('type') == 'ccsds'
                                                         and ccsds.mean_elements_only(spec)):
        raise HTTPException(400, tr('位相を変えた一括計算は「軌道要素」・TLE・OMM で指定した衛星だけで使えます'))
    if 'earth' in phen:
        raise HTTPException(400, tr('地球の影は、位相を変えた一括計算では計算できません（影に入る時期と長さは軌道上の位置によらないため、'
                                    '一括計算をやめて計算してください）'))
    step = float(req.step_deg)
    if not 5.0 <= step <= 90.0:
        raise HTTPException(400, tr('位相の刻みは 5〜90° で指定してください'))
    if jd_b - jd_a > SWEEP_MAX_DAYS:
        raise HTTPException(400, tr('位相を変えた一括計算では期間を 1 年以内にしてください'))
    phases = [float(m) for m in np.arange(0.0, 360.0 - 1e-9, step)]
    conj = {b: find_conjunctions(ctx, b, jd_a - 2.0, jd_b + 2.0) for b in phen}
    groups = {}
    warnings = []
    with _compute_lock:
        try:
            # the observer as given (for a TLE: with its own mean anomaly)
            observer = build_observer(ctx, spec, parse_time=lambda s: parse_utc(ctx, s))
            obs_desc = observer.describe()
            obs_desc['epoch'] = iso_from_jd(ctx, obs_desc['epoch_jd_tt'])
            epoch = obs_desc['epoch_jd_tt']
            warnings += observer.warnings_for(ctx, jd_a if abs(jd_a - epoch) > abs(jd_b - epoch) else jd_b)
            for m in phases:
                observer = build_observer(ctx, dict(spec, m_deg=m), parse_time=lambda s: parse_utc(ctx, s))
                saved = _saved_request(req, dict(spec, m_deg=m), observer)
                for body in phen:
                    ls = LocalSearch(ctx, observer, body, params, jd_a, jd_b)
                    found = ls.run()
                    warnings += [w for w in ls.warnings if w not in warnings]
                    tc = conj[body]
                    for ev in found:
                        eid = _put({'kind': 'local', 'event': ev, 'search': ls, 'ctx': ctx,
                                    'params': params, 'observer': observer, 'global_obs': False,
                                    'request': saved})
                        s = _local_summary(ev, observer)
                        s['id'] = eid
                        s['m_deg'] = m
                        k = int(np.argmin(np.abs(tc - ev['jd_max']))) if len(tc) else -1
                        g = groups.setdefault((body, k), {
                            'body': body, 'jd_conj': float(tc[k]) if k >= 0 else ev['jd_max'],
                            'by_phase': {}})
                        g['by_phase'].setdefault(m, []).append(s)
        except ObserverError as exc:
            raise _bad_input(exc)
        except HTTPException:
            raise
        except Exception as exc:
            traceback.print_exc()
            raise HTTPException(500, tr('計算中にエラーが発生しました: {exc}', exc=exc))
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


def _saved_request(req, spec, observer):
    '''The request that computes an event again (a TLE fetched from CelesTrak is kept as fetched).'''
    if spec.get('type') == 'celestrak':
        spec = {'type': 'tle', 'line1': observer.line1, 'line2': observer.line2,
                'name': observer.name, 'norad': spec.get('norad')}
    return {'phenomena': list(req.phenomena), 'observer': dict(spec), 'start': req.start,
            'end': req.end, 'settings': dict(req.settings)}


RESTORE_MARGIN_D = 0.5


@app.post('/api/restore')
def restore(req: RestoreRequest):
    '''Compute one event of a saved result again.

    A saved result keeps the list as it was computed; the details of an event (contacts,
    timeseries, map) are computed again from the saved request around the saved time.'''
    r, ev = req.request, req.event
    if not isinstance(r.get('observer'), dict) or ev.get('jd_max') is None:
        raise HTTPException(400, tr('保存したファイルに計算条件または現象の時刻がありません'))
    body = ev.get('body') or 'moon'
    try:
        ctx, params = _settings(r.get('settings') or {})
        jd = float(ev['jd_max'])
        a = (ev.get('jd_c1') or jd) - RESTORE_MARGIN_D
        b = (ev.get('jd_c4') or jd) + RESTORE_MARGIN_D
        # keep the period of the original search (contacts cut at its ends stay cut)
        if r.get('start'):
            a = max(a, parse_utc(ctx, r['start']))
        if r.get('end'):
            b = min(b, parse_utc(ctx, r['end']))
    except ObserverError as exc:
        raise _bad_input(exc)
    except Exception as exc:
        raise HTTPException(400, tr('保存した計算条件を解釈できません: {exc}', exc=exc))
    spec = dict(r['observer'])
    if ev.get('m_deg') is not None:
        spec['m_deg'] = ev['m_deg']
    with _compute_lock:
        try:
            if spec.get('type') == 'global' and body == 'moon':
                found = search_global(ctx, a, b, params)
                obj = {'kind': 'global', 'ctx': ctx, 'params': params}
            else:
                observer = build_observer(ctx, spec, parse_time=lambda s: parse_utc(ctx, s))
                ls = LocalSearch(ctx, observer, body, params, a, b)
                found = ls.run()
                obj = {'kind': 'local', 'search': ls, 'ctx': ctx, 'params': params,
                       'observer': observer, 'global_obs': spec.get('type') == 'global'}
        except ObserverError as exc:
            raise _bad_input(exc)
        except Exception as exc:
            traceback.print_exc()
            raise HTTPException(500, tr('計算中にエラーが発生しました: {exc}', exc=exc))
    hit = [e for e in found if abs(e['jd_max'] - jd) < 0.1]
    if not hit:
        raise HTTPException(404, tr('この現象を保存した計算条件で再計算できませんでした'))
    obj['event'] = min(hit, key=lambda e: abs(e['jd_max'] - jd))
    obj['request'] = {'phenomena': [body], 'observer': spec, 'start': r.get('start'),
                      'end': r.get('end'), 'settings': r.get('settings') or {}}
    return event_detail(_put(obj))


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
        d['observer'] = {'kind': 'global', 'name': tr('地心（地球中心）')}
        d['observer_kind'] = 'geocenter'
    d['warnings'] = obj['observer'].warnings_for(obj['ctx'], ev['jd_max'])
    d['params'] = obj['params'].as_dict()
    d['ephemeris'] = obj['ctx'].ephemeris_name
    if obj.get('request'):
        d['request'] = obj['request']
    return d


@app.get('/api/event/{eid}')
def event_detail(eid: str):
    obj = _get(eid)
    if obj['kind'] == 'global':
        ev = dict(obj['event'], id=eid)
        if obj.get('request'):
            ev['request'] = obj['request']
        return _clean(ev)
    d = _local_detail(obj)
    d['id'] = eid
    return _clean(d)


@app.get('/api/event/{eid}/map')
def event_map(eid: str, grid: float = 1.0):
    obj = _get(eid)
    ctx, params = obj['ctx'], obj['params']
    grid = float(min(max(grid, 0.5), 3.0)) if math.isfinite(grid) else 1.0
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
            raise HTTPException(500, tr('地図データの計算に失敗しました: {exc}', exc=exc))
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
        raise _bad_input(exc)
    a = ev.get('jd_p1') or ev.get('jd_c1') or ev['jd_max'] - 0.3
    b = ev.get('jd_p4') or ev.get('jd_c4') or ev['jd_max'] + 0.3
    p2 = Params.from_dict(dict(params.as_dict(), include_invisible=True))
    with _compute_lock:
        ls = LocalSearch(ctx, observer, body, p2, a - 0.3, b + 0.3)
        found = ls.run()
    if not found:
        return {'found': False, 'observer': observer.describe()}
    e = min(found, key=lambda x: abs(x['jd_max'] - ev['jd_max']))
    parent = obj.get('request') or {}
    request = {'phenomena': [body], 'observer': {'type': 'ground', 'lat': req.lat, 'lon': req.lon,
                                                 'elevation_m': req.elevation_m, 'name': req.name},
               'start': iso_from_jd(ctx, a - 0.3), 'end': iso_from_jd(ctx, b + 0.3),
               'settings': dict(parent.get('settings') or {}, include_invisible=True)}
    eid = _put({'kind': 'local', 'event': e, 'search': ls, 'ctx': ctx, 'params': p2,
                'observer': observer, 'request': request})
    d = _local_detail(_store[eid])
    d['id'] = eid
    d['found'] = True
    return _clean(d)


# --- the geometry at given times (e.g. those of images of a solar telescope) ---------------------
AT_MAX_TIMES = 2000
AT_MAX_SPAN_D = 3.0
AT_MAX_EXPOSURE_S = 3600.0
AT_PAD_D = 60.0 / DAY_S          # orbit data around the times (the rates are differences over 1 s)
# The values of a row (details() of local.py and the rates of geometry_at()); the observer's extras
# are there for a ground observer (Sun's altitude / azimuth, the horizon's direction) or a spacecraft
# (sub-satellite point, the Earth's direction and size), all that the page needs to draw the frame.
AT_FIELDS = ('time', 'jd_tt', 'p_angle', 'hpc_x', 'hpc_y', 'hpc_vx', 'hpc_vy', 'pa', 'pa_solar', 'xi', 'eta',
             'sep_arcsec', 'sep_rate', 'limb_dist', 'limb_depth', 'rho_s', 'rho_b', 'rho_b_int', 'magnitude',
             'obscuration', 'vis')
AT_EXTRAS = ('sun_alt', 'sun_az', 'parallactic', 'v_angle', 'sat_lat', 'sat_lon', 'sat_alt_km', 'earth_sep_deg',
             'earth_pa', 'earth_radius_deg', 'earth_atm_deg', 'dist_earth_km')


def _number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def parse_at_request(ctx, req):
    '''The times of an AtRequest -> (TT JDs of the middles of the exposures, measured or None).'''
    times = [req.times] if isinstance(req.times, str) else req.times
    if not isinstance(times, list) or not times:
        raise HTTPException(400, tr('時刻を 1 つ以上指定してください'))
    if len(times) > AT_MAX_TIMES:
        raise HTTPException(400, tr('時刻は {n} 個までにしてください', n=AT_MAX_TIMES))
    try:
        exp = 0.0 if req.exposure_s is None else float(req.exposure_s)
    except (TypeError, ValueError):
        exp = float('nan')
    if not 0.0 <= exp <= AT_MAX_EXPOSURE_S:          # NaN too
        raise HTTPException(400, tr('露光時間は 0〜{max:g} 秒で指定してください', max=AT_MAX_EXPOSURE_S))
    try:
        jd = np.array([parse_obs_time(ctx, s) for s in times]) + 0.5 * exp / DAY_S
    except ValueError as exc:                        # names the time that cannot be read
        raise HTTPException(400, str(exc))
    out = np.flatnonzero((jd < ctx.jd_min) | (jd > ctx.jd_max))
    if out.size:
        a, b = ctx.coverage_utc()
        raise HTTPException(400, tr('時刻「{text}」は暦 {name} の範囲（{start}〜{end}）の外です',
                                     text=str(times[out[0]]).strip(), name=ctx.ephemeris_name, start=a, end=b))
    if jd.max() - jd.min() > AT_MAX_SPAN_D:
        raise HTTPException(400, tr('時刻はすべて {days:g} 日以内に収めてください（指定: {first}〜{last}）',
                                     days=AT_MAX_SPAN_D, first=iso_from_jd(ctx, jd.min()),
                                     last=iso_from_jd(ctx, jd.max())))
    m = req.measured
    if m is not None and not (isinstance(m, list) and len(m) == len(times) and all(
            p is None or (isinstance(p, (list, tuple)) and len(p) == 2 and all(map(_number, p))) for p in m)):
        raise HTTPException(400, tr('measured（測った位置）は時刻と同じ数の [X, Y]（″）または null の配列で指定してください'))
    return jd, m


def at_observer_checks(ctx, observer, jd):
    '''The checks of the observer at the times that need no orbit data from a service (the re-entry
    that SGP4 predicts for a TLE) -> warnings (an old TLE judged at the time farthest from its epoch).'''
    lo, hi = float(jd.min()), float(jd.max())
    if isinstance(observer, SGP4Observer):
        jd_d = observer.decay_jd(hi + 0.01)
        if jd_d is not None:
            raise ObserverError(tr('SGP4 ではこの衛星は {date} ごろに再突入するため、指定した時刻は計算できません',
                                   date=iso_from_jd(ctx, jd_d)[:10]))
    epoch = getattr(observer, 'epoch_jd', None)
    if epoch is None:
        return observer.warnings_for(ctx, hi)
    return observer.warnings_for(ctx, lo if abs(lo - epoch) > abs(hi - epoch) else hi)


def _prepare_at(ctx, observer, jd):
    '''Get the orbit data of the observer for the times (a tabulated orbit must cover them) -> warnings.'''
    warnings = at_observer_checks(ctx, observer, jd)
    lo, hi = float(jd.min()), float(jd.max())
    if isinstance(observer, HorizonsObserver):
        if observer.coverage is None:                # not used by a search yet: the step it needs
            observer.survey(lo - 0.05, hi + 0.05)
        warnings += observer.step_warnings()
    elif isinstance(observer, SSCWebObserver):
        cov = observer.coverage_jd()
        if cov and (lo - AT_PAD_D < cov[0] or hi + AT_PAD_D > cov[1]):
            raise ObserverError(tr('指定した時刻には NASA SSCWeb の軌道データがありません（提供期間 {period}）',
                                   period=span(iso_from_jd(ctx, cov[0])[:10], iso_from_jd(ctx, cov[1])[:10])))
    observer.prepare(ctx, [(lo - AT_PAD_D, hi + AT_PAD_D)])
    return warnings


def _at_rows(ls, jd, inputs, measured):
    '''The geometry at the TT JDs (LocalSearch.geometry_at) as rows; with a measured [X, Y] also the
    O-C (arcsec) and the time (s) by which the observed body is ahead along its motion.'''
    g = ls.geometry_at(np.asarray(jd, float) - ls.jd0)
    rows = []
    for k, text in enumerate(inputs):
        r = {'input': text}
        r.update({f: g[f][k] for f in AT_FIELDS + AT_EXTRAS if f in g})
        r['visible'] = bool(g['vis'][k] > 0)
        p = measured[k] if measured else None
        if p is not None:
            ox, oy = float(p[0]) - r['hpc_x'], float(p[1]) - r['hpc_y']
            vx, vy = r['hpc_vx'], r['hpc_vy']
            v2 = vx * vx + vy * vy
            r.update(measured_x=float(p[0]), measured_y=float(p[1]), oc_x=ox, oc_y=oy,
                     oc_dt=(ox * vx + oy * vy) / v2 if v2 > 0 else None)
        rows.append(r)
    return rows


def _no_global_at():
    return HTTPException(400, tr('時刻を指定した位置の計算は、地上の地点・人工衛星・探査機（または地心）から見た'
                                 '現象だけでできます（地球全体の日食ではできません）'))


@app.post('/api/event/{eid}/at')
def event_at(eid: str, req: AtRequest):
    '''The geometry of a stored event at given times (e.g. the exposure times of images): the
    solar-north-up X / Y (arcsec), the distance from the limb and the motion, and the O-C of
    measured positions.'''
    obj = _get(eid)
    if obj['kind'] != 'local':
        raise _no_global_at()
    ctx = obj['ctx']
    jd, measured = parse_at_request(ctx, req)
    with _compute_lock:
        try:
            warnings = _prepare_at(ctx, obj['observer'], jd)
            rows = _at_rows(obj['search'], jd, req.times, measured)
        except ObserverError as exc:
            raise _bad_input(exc)
        except Exception as exc:
            traceback.print_exc()
            raise HTTPException(500, tr('計算中にエラーが発生しました: {exc}', exc=exc))
    return _clean({'rows': rows, 'warnings': warnings})


def at_times(req: AtSearchRequest):
    '''The rows of /api/event/{eid}/at for an observer and one body without a computed event
    (cli.py --at), with the description of the observer and the settings.'''
    t0 = time.time()
    try:
        ctx, params = _settings(req.settings)
    except ObserverError as exc:
        raise _bad_input(exc)
    except Exception as exc:
        raise HTTPException(400, tr('入力値を解釈できません: {exc}', exc=exc))
    phen = list(dict.fromkeys(req.phenomena))
    if len(phen) != 1 or phen[0] not in ('moon', 'mercury', 'venus'):
        raise HTTPException(400, tr('時刻を指定した位置の計算では、現象（moon, mercury, venus）を 1 つだけ選んでください'))
    spec = dict(req.observer)
    if spec.get('type') == 'global':
        raise _no_global_at()
    jd, measured = parse_at_request(ctx, req)
    with _compute_lock:
        try:
            observer = build_observer(ctx, spec, parse_time=lambda s: parse_utc(ctx, s))
            ls = LocalSearch(ctx, observer, phen[0], params, float(jd.min()) - 0.5, float(jd.max()) + 0.5)
            warnings = _prepare_at(ctx, observer, jd)
            rows = _at_rows(ls, jd, req.times, measured)
            obs_desc = observer.describe()
            if obs_desc.get('epoch_jd_tt'):
                obs_desc['epoch'] = iso_from_jd(ctx, obs_desc['epoch_jd_tt'])
        except ObserverError as exc:
            raise _bad_input(exc)
        except Exception as exc:
            traceback.print_exc()
            raise HTTPException(500, tr('計算中にエラーが発生しました: {exc}', exc=exc))
    return _clean({
        'at': rows, 'body': phen[0], 'observer': obs_desc, 'warnings': warnings,
        'elapsed_s': time.time() - t0, 'ephemeris': ctx.ephemeris_name,
        'delta_t_mid_s': float(ctx.ts.tt_jd(0.5 * (jd.min() + jd.max())).delta_t),
        'delta_t_override': ctx.delta_t_override, 'params': params.as_dict(), 'exposure_s': req.exposure_s,
    })


@app.exception_handler(Exception)
async def _unhandled(request, exc):  # pragma: no cover
    traceback.print_exc()
    token = i18n.set_lang(request.headers.get('x-lang'))     # runs outside the _language middleware
    try:
        return JSONResponse({'detail': tr('サーバーエラー: {exc}', exc=exc)}, status_code=500)
    finally:
        i18n.reset_lang(token)
