"""Checks of the Earth's shadow on a satellite (eclipsecalc/earth_shadow.py, the phenomenon
"earth" of the web API and of cli.py): the shadow agrees with the visibility of the eclipses and
transits (the Sun's centre hidden by the Earth and the absorbing atmosphere), no shadow is missed
between the samples, and the geostationary seasons are those around the equinoxes.

Run with  python tests/test_earth_shadow.py  (no internet needed).
"""
import json
import os
import subprocess
import sys

import numpy as np

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
sys.path.insert(0, ROOT)

from eclipsecalc import server  # noqa: E402
from eclipsecalc.constants import DAY_S  # noqa: E402
from eclipsecalc.context import get_context  # noqa: E402
from eclipsecalc.earth_shadow import ShadowSearch, atm_levels, check_levels  # noqa: E402
from eclipsecalc.local import LocalSearch, Params  # noqa: E402
from eclipsecalc.observers import ObserverError, build_observer  # noqa: E402
from eclipsecalc.timeutil import parse_utc  # noqa: E402

CTX = get_context()
# a planned 680 km dawn-dusk sun-synchronous orbit: in the Earth's shadow from May to August
PLAN = {'type': 'kepler', 'perigee_alt_km': 680, 'apogee_alt_km': 680, 'sso': True, 'ltan_h': 18.0,
        'argp_deg': 0, 'm_deg': 0, 'j2': True, 'name': 'plan', 'epoch': '2027-06-01T00:00:00'}


def _shadow(spec, start, end, **settings):
    obs = build_observer(CTX, spec, parse_time=lambda s: parse_utc(CTX, s))
    p = Params.from_dict(settings)
    sh = ShadowSearch(CTX, obs, p, parse_utc(CTX, start), parse_utc(CTX, end), atm_levels(p))
    return obs, p, sh, sh.run()


def _vis(obs, p, iso):
    """The visibility margin of the eclipses (deg, > 0: the Sun's centre is seen) at the UTC times."""
    jd = np.array([parse_utc(CTX, s) for s in iso])
    ls = LocalSearch(CTX, obs, 'moon', p, jd.min() - 0.1, jd.max() + 0.1)
    return ls.E.geom(jd - ls.jd0, need_vis=True)['vis']


def _shift(iso, s):
    return [CTX.ts.tt_jd(parse_utc(CTX, x) + s / DAY_S).utc_iso(places=3) for x in iso]


def test_agrees_with_visibility():
    # The entry and exit of each pass are roots of the visibility margin of the eclipses
    # (blocking height 140 km): the Sun's centre is seen 10 ms before the entry, hidden 10 ms after.
    obs, p, _, r = _shadow(PLAN, '2027-06-17', '2027-06-19', earth_atm_km=140)
    passes = [x for x in r['passes'] if x['start']]
    assert 28 <= len(passes) <= 31, len(passes)
    starts, ends = [x['start'] for x in passes], [x['end'] for x in passes]
    assert np.all(_vis(obs, p, _shift(starts, -0.01)) > 0) and np.all(_vis(obs, p, _shift(starts, 0.01)) < 0)
    assert np.all(_vis(obs, p, _shift(ends, -0.01)) < 0) and np.all(_vis(obs, p, _shift(ends, 0.01)) > 0)
    for x in passes:      # some of the Sun, its centre, all of it: nested, ~12 s apart at 680 km
        t = [parse_utc(CTX, x[k]) for k in ('partial_start', 'start', 'total_start', 'total_end', 'end', 'partial_end')]
        assert all(a < b for a, b in zip(t, t[1:])), x
        assert 8 < (t[1] - t[0]) * DAY_S < 40 and 8 < (t[5] - t[4]) * DAY_S < 40, x
        assert 20 * 60 < x['duration_s'] < 30 * 60 and x['depth_deg'] > 0
        assert x['sat_lat'] < -60          # the night side is over the south pole in June
    lv = {v['atm_km']: v for v in r['levels']}
    assert sorted(lv) == [0.0, 140.0, 250.0]
    assert lv[0.0]['max_s'] < lv[140.0]['max_s'] < lv[250.0]['max_s']
    assert lv[140.0]['n'] == len(passes)


def test_no_shadow_missed():
    # Sampled every 5 s, the visibility margin is negative exactly in the intervals found.
    obs, p, _, r = _shadow(PLAN, '2027-05-20', '2027-05-21')
    a, b = parse_utc(CTX, '2027-05-20'), parse_utc(CTX, '2027-05-21')
    jd = np.arange(a, b, 5 / DAY_S)
    ls = LocalSearch(CTX, obs, 'moon', p, a - 0.1, b + 0.1)
    hidden = ls.E.geom(jd - ls.jd0, need_vis=True)['vis'] < 0
    iv = [(parse_utc(CTX, x['start']), parse_utc(CTX, x['end'])) for x in r['passes'] if x['start']]
    inside = np.zeros(jd.size, bool)
    for s, e in iv:
        inside |= (jd > s) & (jd < e)
    assert np.array_equal(hidden, inside)
    assert len(iv) == int(np.sum(np.diff(hidden.astype(int)) == 1)) + int(hidden[0])


def test_geostationary_seasons():
    # A geostationary satellite is in the Earth's shadow once a night for ~45 days around each
    # equinox, at most ~70 min (72 min with the 250 km atmosphere).
    _, _, _, r = _shadow({'type': 'geo', 'lon': 140.7}, '2027-01-01', '2028-01-01')
    for lv in r['levels']:
        assert len(lv['seasons']) == 2, lv['seasons']
        spring, autumn = lv['seasons']
        assert spring['start'] < '2027-03-20' < spring['end'] and autumn['start'] < '2027-09-23' < autumn['end']
        for s in (spring, autumn):
            assert 43 <= s['days'] <= 48 and 44 <= s['n'] <= 49, s
            assert 69 * 60 < s['max_s'] < 73 * 60, s
    # one pass a night, with those in which only some of the Sun is hidden
    assert r['levels'][0]['n'] < len(r['passes']) < 100


def test_checks():
    p = Params.from_dict({'earth_atm_km': 60, 'shadow_atm_levels_km': '250, 0'})
    assert atm_levels(p) == [0.0, 60.0, 250.0]
    for bad in ('0, x', [-1], list(range(7)), [5000]):
        try:
            check_levels(bad)
            assert False, bad
        except ValueError:
            pass
    req = dict(phenomena=['earth'], start='2027-06-01', end='2027-06-02', settings={})
    for spec, code in (({'type': 'ground', 'lat': 35, 'lon': 139}, 400), ({'type': 'global'}, 400)):
        try:
            server.search(server.SearchRequest(observer=spec, **req))
            assert False, spec
        except server.HTTPException as exc:
            assert exc.status_code == code and '人工衛星' in exc.detail, exc.detail
    try:        # ~8,000 orbits at most: about 1.5 years in a low orbit
        server.search(server.SearchRequest(observer=PLAN, **dict(req, end='2029-01-01')))
        assert False
    except server.HTTPException as exc:
        assert '日以内' in exc.detail, exc.detail
    try:
        server.phase_sweep(server.SweepRequest(observer=PLAN, step_deg=30, **req))
        assert False
    except server.HTTPException as exc:
        assert '一括計算' in exc.detail, exc.detail
    try:
        Params.from_dict({'shadow_atm_levels_km': 'abc'})
        assert False
    except ObserverError:
        pass
    # with the eclipses: both in one answer
    r = server.search(server.SearchRequest(observer=dict(PLAN, epoch='2027-08-02T00:00:00'), phenomena=['moon', 'earth'],
                                           start='2027-08-02', end='2027-08-03', settings={}))
    assert r['events'] and r['earth_shadow']['passes']


def _cli(*args):
    p = subprocess.run([sys.executable, os.path.join(ROOT, 'cli.py'), '--lang', 'ja', '-q', *args],
                       capture_output=True, text=True, encoding='utf-8', cwd=ROOT)
    return p.returncode, p.stdout, p.stderr


def test_cli():
    orbit = ['--epoch', '2027-06-01T00:00:00', '--alt', '680', '--sso', '--ltan', '18:00']
    code, out, _ = _cli(*orbit, '--phenomena', 'earth', '--start', '2027-06-18', '--end', '2027-06-19', '--format', 'json',
                        '--atm', '140', '--atm-levels', '0,250')
    d = json.loads(out)
    assert code == 0 and d['ok'] and not d['events'], out[:300]
    sh = d['earth_shadow']
    assert sh['atm_km'] == 140 and [v['atm_km'] for v in sh['levels']] == [0, 140, 250]
    assert d['request']['settings']['shadow_atm_levels_km'] == [0, 250]
    code, out, _ = _cli(*orbit, '--phenomena', 'earth', '--start', '2027-06-18', '--end', '2027-06-19', '--format', 'csv')
    rows = out.splitlines()
    assert code == 0 and rows[0].startswith('atm_km,start_utc,end_utc,duration_s') and len(rows) == 1 + len(sh['passes'])
    code, out, _ = _cli(*orbit, '--phenomena', 'earth', '--start', '2027-06-18', '--end', '2027-06-19', '--detail')
    assert code == 0 and '周回ごとの地球の影' in out and '影の季節' in out, out
    for args in (('--phenomena', 'earth,moon', '--format', 'csv'), ('--phenomena', 'earth', '--sweep', '30'),
                 ('--phenomena', 'earth', '--atm-levels', '1,x')):
        code, _, err = _cli(*orbit, '--start', '2027-06-18', '--end', '2027-06-19', *args)
        assert code == 1 and 'エラー' in err, (args, err)
    code, _, err = _cli('--city', '東京', '--phenomena', 'earth', '--start', '2027-06-18', '--end', '2027-06-19')
    assert code == 1 and '人工衛星' in err, err


if __name__ == '__main__':
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
                print(f'PASS  {name}')
            except Exception as exc:     # an error fails this test only
                failed += 1
                print(f'FAIL  {name}: {exc!r}')
    sys.exit(1 if failed else 0)
